"""
Supabase Cloud Client
=====================
Synchronous wrapper around supabase-py for:
  - Auth (sign in / sign up — SEPARATED, no auto-create on failed login)
  - Device registration + heartbeat
  - Sensor data insert (triggers Realtime to mobile app)
  - Command subscription via Realtime channels
  - Profile & alert CRUD

SECURITY FIXES:
  - sign_in no longer auto-creates accounts on login failure
  - Input validation on all query parameters
  - Retry logic with exponential backoff on transient failures
  - Token format validation in set_session
  - Rate limiting on sensor inserts to prevent DB flooding
"""

import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger("Cloud")

# Refresh JWTs when they have less than this many seconds left. Supabase
# access tokens live for ~1h; 300s gives us a comfortable margin to refresh
# before realtime channels start dropping with auth errors.
_JWT_REFRESH_THRESHOLD_S = 300


def _jwt_exp(token: str) -> Optional[int]:
    """Decode a JWT's `exp` claim without verifying the signature.

    Returns the expiry as a Unix timestamp, or None if the token is
    malformed. We only need the timestamp to decide whether to refresh —
    the supabase client validates signatures on its end.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        # JWT uses URL-safe base64 without padding; add padding back.
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None

# Validation patterns
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
HARDWARE_ID_PATTERN = re.compile(r"^[0-9a-f]{16,64}$", re.IGNORECASE)
PROFILE_NAME_PATTERN = re.compile(r"^[\w\s\-]{1,32}$")


def _validate_uuid(value: str, label: str = "ID") -> str:
    """Validate UUID format to prevent injection."""
    if not value or not UUID_PATTERN.match(value):
        raise ValueError(f"Invalid {label} format: {value}")
    return value


def _validate_hardware_id(value: str) -> str:
    """Validate hardware ID (hex hash)."""
    if not value or not HARDWARE_ID_PATTERN.match(value):
        raise ValueError(f"Invalid hardware_id format")
    return value


def _validate_profile_name(value: str) -> str:
    """Validate profile name — safe characters only."""
    if not value or not PROFILE_NAME_PATTERN.match(value.strip()):
        raise ValueError(f"Invalid profile name: {value}")
    return value.strip()[:32]


class SupabaseClient:
    """Synchronous Supabase client with retry, validation, and health tracking."""

    def __init__(self, url: str, key: str):
        # FIX: Validate Supabase URL format
        if not url or not url.startswith("https://"):
            raise ValueError(f"Invalid Supabase URL: must be https://")
        if not key or len(key) < 20:
            raise ValueError("Invalid Supabase key")

        # Stash these so the local API can expose them to validation
        # scripts (latency_test.py) — the test needs both URL and anon
        # key to talk to PostgREST directly with the user's JWT.
        self.url = url
        self.anon_key = key

        from supabase import create_client
        # PKCE flow is required for the loopback OAuth path used by the
        # interactive Google sign-in (pairing.sign_in_with_google). The JS
        # desktop side does the same — see desktop/src/lib/supabase.ts.
        # Email/password sign-in works regardless of flow_type.
        try:
            from supabase.client import ClientOptions
            self.client = create_client(url, key, ClientOptions(flow_type="pkce"))
        except Exception:
            # Older supabase-py versions might not expose ClientOptions or
            # flow_type. Fall back to default (implicit) flow — Google
            # sign-in will fail in that case but everything else still works.
            self.client = create_client(url, key)
        self.user_id: Optional[str] = None
        self._realtime_channel = None
        self._healthy = False
        self._last_insert_time = 0.0
        self._insert_interval = 2.0  # Minimum seconds between sensor inserts
        self._last_polled_command_time: Optional[str] = None  # FIX: polling state

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------
    def _retry(self, fn, max_retries: int = 3, backoff: float = 1.0):
        """Execute fn with exponential backoff retry on transient errors."""
        last_error = None
        for attempt in range(max_retries):
            try:
                return fn()
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                # Don't retry on auth errors or client errors
                if any(kw in err_str for kw in ["unauthorized", "forbidden", "not found", "invalid", "400", "401", "403", "404"]):
                    raise
                if attempt < max_retries - 1:
                    wait = backoff * (2 ** attempt)
                    logger.debug(f"Retry {attempt + 1}/{max_retries} in {wait}s: {e}")
                    time.sleep(wait)
        raise last_error

    # ------------------------------------------------------------------
    # Auth — FIX: Login and register are now SEPARATE methods
    # ------------------------------------------------------------------
    def sign_in(self, email: str, password: str):
        """Sign in with existing account. Raises on failure — does NOT auto-create."""
        if not email or not password:
            raise ValueError("Email and password are required")
        try:
            resp = self.client.auth.sign_in_with_password({
                "email": email,
                "password": password,
            })
            self.user_id = resp.user.id
            self._healthy = True
            logger.info(f"Signed in as {email}")
        except Exception as e:
            self._healthy = False
            # FIX: No longer auto-creates account — just raise the error
            raise ValueError(f"Login failed: {e}")

    def sign_up(self, email: str, password: str):
        """Create new account. Separate from sign_in."""
        if not email or not password:
            raise ValueError("Email and password are required")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        try:
            resp = self.client.auth.sign_up({
                "email": email,
                "password": password,
            })
            self.user_id = resp.user.id
            self._healthy = True
            logger.info(f"Created account for {email}")
        except Exception as e:
            self._healthy = False
            raise ValueError(f"Registration failed: {e}")

    def get_access_token(self) -> Optional[str]:
        """Return a live access JWT, refreshing if it's near expiry.

        Used by the realtime listener to authenticate the websocket so RLS
        sees auth.uid(). The realtime channel silently disconnects when its
        JWT expires, so we proactively refresh ~5 minutes before expiry —
        the listener calls this on every reconnect, and we want it to never
        hand out a stale token.

        Returns None on any client-side oddity rather than raising; the
        caller treats absence as "no auth, fall back to polling."
        """
        try:
            sess = self.client.auth.get_session()
            if sess is None:
                return None
            tok = getattr(sess, "access_token", None)
            if not tok:
                return None

            exp = _jwt_exp(tok)
            if exp is not None and exp - time.time() < _JWT_REFRESH_THRESHOLD_S:
                # Try to refresh in-place. supabase-py rotates the session
                # behind get_session(); we re-read after refresh.
                try:
                    self.client.auth.refresh_session()
                    sess = self.client.auth.get_session()
                    if sess is not None:
                        new_tok = getattr(sess, "access_token", None)
                        if new_tok:
                            return new_tok
                except Exception as e:
                    # Refresh failed (network blip, refresh token revoked,
                    # supabase-py version mismatch). Fall back to the still-
                    # valid-but-expiring token; if it really is expired, the
                    # realtime channel will fail and we'll retry next loop.
                    logger.warning(f"Token refresh failed, using existing token: {e}")
            return tok
        except Exception:
            return None

    def set_session(self, access_token: str, refresh_token: str, user_id: str = ""):
        """Set auth session from OAuth token (passed from the Tauri desktop)."""
        # FIX: Validate token format (JWT-like: three base64 segments)
        if not access_token or access_token.count('.') != 2:
            raise ValueError("Invalid access token format")
        if not refresh_token:
            raise ValueError("Refresh token is required")

        try:
            self.client.auth.set_session(access_token, refresh_token)
            if user_id:
                # Validate UUID format
                if UUID_PATTERN.match(user_id):
                    self.user_id = user_id
                else:
                    logger.warning(f"Invalid user_id format, fetching from API")
                    user = self.client.auth.get_user()
                    self.user_id = user.user.id
            else:
                user = self.client.auth.get_user()
                self.user_id = user.user.id
            self._healthy = True
            logger.info(f"Session set via OAuth (user: {self.user_id[:16]}...)")
        except Exception as e:
            self._healthy = False
            raise

    # ------------------------------------------------------------------
    # Device
    # ------------------------------------------------------------------
    def register_device(
        self, hardware_id: str, name: str, os_info: str,
        controller: str = "",
    ) -> str:
        """Register device or update existing. Returns device UUID.

        `controller` is the fan-control driver name advertised by the agent
        (e.g. 'lenovo-legion-wmi', 'lhm-pwm', 'demo'). Empty string is
        accepted for compatibility with the column being absent.
        """
        hardware_id = _validate_hardware_id(hardware_id)
        # Sanitize name (strip HTML/scripts)
        name = re.sub(r'[<>"\';&]', '', name)[:64]
        os_info = re.sub(r'[<>"\';&]', '', os_info)[:128]
        controller = re.sub(r'[<>"\';&]', '', controller)[:32]
        now = datetime.now(timezone.utc).isoformat()

        def _do():
            result = (
                self.client.table("devices")
                .select("id")
                .eq("hardware_id", hardware_id)
                .execute()
            )
            update_fields: dict = {
                "is_online": True,
                "last_seen": now,
                "name": name,
                "os_info": os_info,
            }
            insert_fields: dict = {
                "user_id": self.user_id,
                "hardware_id": hardware_id,
                "name": name,
                "os_info": os_info,
                "is_online": True,
                "last_seen": now,
            }
            # Only set the controller column when we have one. Defensive in
            # case the migration hasn't been applied yet — sending an
            # unknown column raises 'column does not exist' from PostgREST.
            if controller:
                update_fields["controller"] = controller
                insert_fields["controller"] = controller

            if result.data:
                device_id = result.data[0]["id"]
                self.client.table("devices").update(update_fields).eq("id", device_id).execute()
                return device_id

            result = self.client.table("devices").insert(insert_fields).execute()
            return result.data[0]["id"]

        return self._retry(_do)

    def heartbeat(self, device_id: str, controller: str = "", app_version: str = ""):
        """Update last_seen timestamp.

        `controller` is the agent's current fan-control driver name.
        `app_version` is the agent's semantic version string.
        Both are stamped on every heartbeat so support / telemetry sees
        the latest values without needing a re-pair after upgrades.
        """
        _validate_uuid(device_id, "device_id")
        update: dict = {
            "is_online": True,
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        if controller:
            update["controller"] = re.sub(r'[<>"\';&]', '', controller)[:32]
        if app_version:
            update["app_version"] = re.sub(r'[<>"\';&]', '', app_version)[:32]
        self._retry(lambda: (
            self.client.table("devices").update(update).eq("id", device_id).execute()
        ))

    def set_device_offline(self, device_id: str):
        """Mark device offline on shutdown."""
        try:
            _validate_uuid(device_id, "device_id")
            self.client.table("devices").update({
                "is_online": False,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }).eq("id", device_id).execute()
        except Exception as e:
            logger.debug(f"Failed to set offline: {e}")

    # ------------------------------------------------------------------
    # Sensor readings — FIX: Rate limited to prevent DB flooding
    # ------------------------------------------------------------------
    def insert_sensor_reading(
        self,
        device_id: str,
        cpu_temp: float,
        cpu_load: float,
        gpu_temp: Optional[float],
        fan_speeds: list[dict],
        ram_usage: float,
    ):
        """Insert one sensor row. Rate limited to prevent DB flooding."""
        _validate_uuid(device_id, "device_id")

        # FIX: Rate limit sensor inserts
        now = time.monotonic()
        if now - self._last_insert_time < self._insert_interval:
            return  # Skip this insert, too soon
        self._last_insert_time = now

        # Clamp values to sane ranges
        def _safe_round(v, min_v, max_v, decimals=1):
            if v is None:
                return None
            return round(max(min_v, min(max_v, float(v))), decimals)

        self.client.table("sensor_readings").insert({
            "device_id": device_id,
            "cpu_temp": _safe_round(cpu_temp, -40, 150),
            "cpu_load": _safe_round(cpu_load, 0, 100),
            "gpu_temp": _safe_round(gpu_temp, -40, 150) if gpu_temp is not None else None,
            "fan_speeds": json.dumps(fan_speeds) if isinstance(fan_speeds, list) else "[]",
            "ram_usage": _safe_round(ram_usage, 0, 100),
        }).execute()

    # ------------------------------------------------------------------
    # FIX: poll_commands replaces subscribe_commands
    # supabase-py sync client does NOT support Realtime channels — only the
    # async client does. Calling .channel().subscribe() on the sync client
    # raises: "This feature isn't available in the sync client."
    # Polling every 5s (called from agent._monitoring_loop) achieves the same.
    # ------------------------------------------------------------------
    def poll_commands(self, device_id: str, callback: Callable):
        """Poll for pending commands and invoke callback for each new one."""
        _validate_uuid(device_id, "device_id")
        try:
            query = (
                self.client.table("commands")
                .select("*")
                .eq("device_id", device_id)
                .eq("status", "pending")
                .order("created_at", desc=False)
            )
            if self._last_polled_command_time:
                query = query.gt("created_at", self._last_polled_command_time)

            result = query.execute()

            for record in (result.data or []):
                # Validate command type whitelist
                cmd_type = record.get("command_type", "")
                if cmd_type not in ("set_fan_speed", "set_profile", "set_alert_threshold", "set_all_fans", "set_fan_mode"):
                    logger.warning(f"Unknown command type from cloud: {cmd_type}")
                    self.update_command_status(record["id"], "failed")
                    continue

                # Parse payload if JSON string
                p = record.get("payload")
                if isinstance(p, str):
                    try:
                        record["payload"] = json.loads(p)
                    except (json.JSONDecodeError, TypeError):
                        record["payload"] = {}

                # Advance watermark so we don't re-process
                created = record.get("created_at", "")
                if created and (not self._last_polled_command_time or created > self._last_polled_command_time):
                    self._last_polled_command_time = created

                try:
                    callback(record)
                except Exception as e:
                    logger.error(f"Command callback error: {e}", exc_info=True)

        except Exception as e:
            logger.debug(f"Command poll failed: {e}")

    def update_command_status(self, command_id: str, status: str):
        """Mark command as executed or failed."""
        _validate_uuid(command_id, "command_id")
        if status not in ("executed", "failed"):
            raise ValueError(f"Invalid command status: {status}")
        self.client.table("commands").update({
            "status": status,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", command_id).execute()

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------
    def get_profiles(self, device_id: str) -> list[dict]:
        """Fetch all fan profiles for this device."""
        _validate_uuid(device_id, "device_id")
        result = (
            self.client.table("profiles")
            .select("*")
            .eq("device_id", device_id)
            .order("name")
            .execute()
        )
        for p in result.data:
            if isinstance(p.get("fan_curve"), str):
                try:
                    p["fan_curve"] = json.loads(p["fan_curve"])
                except (json.JSONDecodeError, TypeError):
                    p["fan_curve"] = []
        return result.data

    def create_profile(
        self, device_id: str, name: str,
        fan_curve: list[dict], is_active: bool = False,
        fan_mode: Optional[int] = None,
        target_temp: Optional[float] = None,
    ):
        """Insert a new profile.

        `target_temp` enables PID setpoint mode for this profile — see
        migration 0014_profiles_target_temp.sql. Profiles without it run
        in legacy fan_curve mode.
        """
        _validate_uuid(device_id, "device_id")
        name = _validate_profile_name(name)
        row: dict = {
            "device_id": device_id,
            "name": name,
            "fan_curve": json.dumps(fan_curve),
            "is_active": is_active,
        }
        if fan_mode is not None:
            if fan_mode not in (1, 2, 3):
                raise ValueError(f"fan_mode must be 1, 2, or 3 (got {fan_mode})")
            row["fan_mode"] = fan_mode
        if target_temp is not None:
            t = float(target_temp)
            if not (40.0 <= t <= 95.0):
                raise ValueError(f"target_temp must be in [40, 95]°C (got {t})")
            row["target_temp"] = t
        self.client.table("profiles").insert(row).execute()

    def update_profile_curve(self, profile_id: str, fan_curve: list[dict]):
        """Update a profile's fan curve."""
        _validate_uuid(profile_id, "profile_id")
        self.client.table("profiles").update({
            "fan_curve": json.dumps(fan_curve),
        }).eq("id", profile_id).execute()

    def update_profile_target_temp(
        self, profile_id: str, target_temp: float | None,
    ):
        """Update a profile's PID setpoint.

        None clears the column (profile reverts to fan_curve mode). A
        float in [40, 95] activates PID mode at that setpoint. The DB
        CHECK constraint from 0014_profiles_target_temp.sql enforces the
        range — we don't double-validate here.
        """
        _validate_uuid(profile_id, "profile_id")
        self.client.table("profiles").update({
            "target_temp": target_temp,
        }).eq("id", profile_id).execute()

    def set_profile_active(self, device_id: str, profile_name: str):
        """Atomically activate one profile (and deactivate all others) for a device.

        Calls the public.activate_profile RPC defined in
        supabase/migrations/0012_activate_profile_rpc.sql. The function
        does a single UPDATE that both flips is_active=true on the named
        profile and is_active=false on every other profile of the same
        device — atomic, so two clients toggling concurrently can't end
        up with no profile active or two profiles active.

        Falls back to the legacy two-step path on RPC failure (e.g. an
        older database that hasn't applied 0012 yet). Once everyone has
        the migration the fallback can be deleted.
        """
        _validate_uuid(device_id, "device_id")
        profile_name = _validate_profile_name(profile_name)

        try:
            self.client.rpc("activate_profile", {
                "p_device_id": device_id,
                "p_profile_name": profile_name,
            }).execute()
            return
        except Exception as e:
            # 'function does not exist' from PostgREST means the migration
            # hasn't been applied yet. Fall back to the racy two-step path
            # so older databases keep working until the operator catches up.
            err_str = str(e).lower()
            if "does not exist" not in err_str and "could not find" not in err_str:
                raise
            logger.warning(
                "activate_profile RPC unavailable, falling back to two-step UPDATE. "
                "Apply 0012_activate_profile_rpc.sql to fix."
            )

        # Legacy fallback. Same behaviour as before this migration landed.
        self.client.table("profiles").update({
            "is_active": False,
        }).eq("device_id", device_id).execute()
        self.client.table("profiles").update({
            "is_active": True,
        }).eq("device_id", device_id).eq("name", profile_name).execute()

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------
    def get_alert_settings(self, device_id: str) -> list[dict]:
        """Get alert threshold config."""
        _validate_uuid(device_id, "device_id")
        result = (
            self.client.table("alert_settings")
            .select("*")
            .eq("device_id", device_id)
            .execute()
        )
        return result.data

    def insert_alert(
        self, device_id: str, metric: str, value: float, threshold: float,
    ):
        """Record a fired alert."""
        _validate_uuid(device_id, "device_id")
        if metric not in ("cpu_temp", "gpu_temp"):
            raise ValueError(f"Invalid alert metric: {metric}")
        self.client.table("alerts").insert({
            "device_id": device_id,
            "metric": metric,
            "value": round(max(-40, min(150, float(value))), 1),
            "threshold": round(max(30, min(120, float(threshold))), 1),
        }).execute()

    def get_alert_log(self, device_id: str, limit: int = 50) -> list[dict]:
        """Fetch recent fired alerts."""
        _validate_uuid(device_id, "device_id")
        limit = max(1, min(200, int(limit)))  # Cap limit
        result = (
            self.client.table("alerts")
            .select("*")
            .eq("device_id", device_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data

    def get_sensor_history(self, device_id: str, limit: int = 180) -> list[dict]:
        """Fetch recent sensor readings for history chart."""
        _validate_uuid(device_id, "device_id")
        limit = max(1, min(500, int(limit)))
        result = (
            self.client.table("sensor_readings")
            .select("cpu_temp,gpu_temp,cpu_load,ram_usage,fan_speeds,created_at")
            .eq("device_id", device_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(reversed(result.data))