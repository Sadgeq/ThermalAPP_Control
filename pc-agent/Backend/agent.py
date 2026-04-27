"""
ThermalControl PC Agent
=======================
Reads hardware sensors via LibreHardwareMonitor, applies fan curve profiles,
syncs telemetry to Supabase cloud, and exposes a local WebSocket + REST API
on port 8420 for the Tauri desktop UI.

Run:
    python agent.py              (normal)
    python agent.py --demo       (force demo mode, no hardware)

SECURITY FIXES:
  - Auth token forwarded to local server for endpoint protection
  - Command payload validation with type checking and clamping
  - Password cleared from memory after auth
  - Cloud insert rate limiting (done in cloud.py)
  - Blocking calls wrapped in executor for async safety
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent / ".env")

# Public Supabase project values. Hardcoded as defaults so a fresh install
# can pair without any .env at all — the user types a PIN, the agent stores
# its session in the OS keyring, and no plaintext secrets ever land on disk.
# These values are already public (mobile-app/lib/supabase.ts ships them).
_DEFAULT_SUPABASE_URL = "https://gwpqkvsvhobkkqctjduc.supabase.co"
_DEFAULT_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd3cHFrdnN2aG9ia2txY3RqZHVjIiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3NzIwMzk0NTQsImV4cCI6MjA4NzYxNTQ1NH0."
    "OcEe0CphKJ4Lu7jwPrwJ2SdOiWjRwn3Vtc8Nur4oB1I"
)

CONFIG = {
    "supabase_url": os.getenv("SUPABASE_URL") or _DEFAULT_SUPABASE_URL,
    "supabase_key": os.getenv("SUPABASE_ANON_KEY") or _DEFAULT_SUPABASE_ANON_KEY,
    "device_name": os.getenv("DEVICE_NAME", "My PC"),
    "polling_interval": max(1.0, min(60.0, float(os.getenv("POLLING_INTERVAL", "2")))),
    "heartbeat_interval": 30,
    "email": os.getenv("USER_EMAIL", ""),
    "password": os.getenv("USER_PASSWORD", ""),
    # OAuth tokens passed from the Tauri desktop (takes priority over email/password)
    "access_token": os.getenv("AUTH_ACCESS_TOKEN", ""),
    "refresh_token": os.getenv("AUTH_REFRESH_TOKEN", ""),
    "auth_user_id": os.getenv("AUTH_USER_ID", ""),
    "auth_user_email": os.getenv("AUTH_USER_EMAIL", ""),
    "cloud_enabled": True,
    "local_port": int(os.getenv("LOCAL_PORT", "8420")),
}

# Allow disabling cloud if no credentials
if not CONFIG["supabase_url"] or not CONFIG["supabase_key"]:
    CONFIG["cloud_enabled"] = False

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("Agent")


def _clamp(v, min_v, max_v) -> float:
    """Safely clamp a numeric value."""
    try:
        return max(min_v, min(max_v, float(v)))
    except (TypeError, ValueError):
        return min_v


# Map UI profile names to Lenovo Legion fan modes (1=Quiet, 2=Balanced,
# 3=Performance). Performance covers both Gaming and Turbo because the
# 82NL BIOS only exposes three modes.
_PROFILE_TO_MODE = {
    "Silent":   1,
    "Balanced": 2,
    "Gaming":   3,
    "Turbo":    3,
}


class FanControlAgent:
    """Main agent: hardware -> profiles -> cloud + local API."""

    def __init__(self, force_demo: bool = False):
        from hardware import HardwareMonitor
        from profiles import ProfileEngine

        self.hardware = HardwareMonitor(force_demo=force_demo)
        self.profile_engine = ProfileEngine()
        self.cloud = None
        self.device_id: str | None = None
        self.device_name: str = CONFIG["device_name"]
        self.running = False
        self.demo_mode = not self.hardware._lhm_available
        self._hardware_id = self._get_hardware_id()
        self._last_heartbeat = 0.0
        self._last_command_poll = 0.0  # FIX: track last command poll time
        self._last_sensor_data: dict = {}
        self.alert_thresholds: dict = {}
        self._cmd_listener = None  # Realtime listener for sub-second commands

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self):
        """Initialize cloud connection, load profiles, start loops."""
        logger.info("=" * 50)
        logger.info("ThermalControl Agent v3.2 starting...")
        logger.info(f"  Device: {self.device_name}")
        logger.info(f"  Mode: {'DEMO' if self.demo_mode else 'HARDWARE'}")
        logger.info(f"  Cloud: {'enabled' if CONFIG['cloud_enabled'] else 'OFFLINE'}")
        logger.info(f"  Local API: http://127.0.0.1:{CONFIG['local_port']}")
        logger.info("=" * 50)

        # Cloud init (optional)
        if CONFIG["cloud_enabled"]:
            try:
                from cloud import SupabaseClient
                self.cloud = SupabaseClient(
                    CONFIG["supabase_url"], CONFIG["supabase_key"]
                )

                # Auth resolution order:
                #   1) OAuth tokens passed from the Tauri desktop (existing path)
                #   2) Stored pairing credential in OS keyring → refresh_session
                #   3) Interactive PIN pairing (first run)
                #   4) Legacy .env email/password (fallback for older installs)
                if CONFIG["access_token"]:
                    self.cloud.set_session(
                        CONFIG["access_token"],
                        CONFIG["refresh_token"],
                        CONFIG["auth_user_id"],
                    )
                    logger.info(f"Authenticated via OAuth ({CONFIG['auth_user_email']})")
                    self.device_id = self.cloud.register_device(
                        hardware_id=self._hardware_id,
                        name=self.device_name,
                        os_info=self._get_os_info(),
                    )
                else:
                    try:
                        from pairing import authenticate_or_pair
                        sd = authenticate_or_pair(
                            cloud_client=self.cloud,
                            supabase_url=CONFIG["supabase_url"],
                            hardware_id=self._hardware_id,
                            device_name=self.device_name,
                            os_info=self._get_os_info(),
                            interactive=sys.stdin.isatty(),
                        )
                        self.device_id = sd.device_id
                        logger.info(f"Authenticated via pairing (device {sd.device_id[:8]}…)")
                    except Exception as pair_err:
                        # Legacy fallback: only if explicit creds are configured.
                        if CONFIG["email"] and CONFIG["password"]:
                            logger.info(f"Pairing unavailable ({pair_err}); falling back to email/password")
                            self.cloud.sign_in(CONFIG["email"], CONFIG["password"])
                            CONFIG["password"] = ""
                            self.device_id = self.cloud.register_device(
                                hardware_id=self._hardware_id,
                                name=self.device_name,
                                os_info=self._get_os_info(),
                            )
                        else:
                            raise

                logger.info(f"Device registered: {self.device_id[:16]}...")

                # Load profiles from cloud. If empty, try creating defaults —
                # but a failure there (e.g. RLS) should NOT take the whole
                # cloud session offline. We just fall back to local defaults
                # and keep syncing sensors / listening for commands.
                try:
                    profiles = self.cloud.get_profiles(self.device_id)
                except Exception as e:
                    logger.warning(f"Could not load cloud profiles: {e}")
                    profiles = []

                if not profiles:
                    try:
                        self._create_default_profiles_cloud()
                        profiles = self.cloud.get_profiles(self.device_id)
                    except Exception as e:
                        logger.warning(
                            f"Could not seed cloud profiles ({e}); using local defaults. "
                            f"Apply 0001_baseline_rls.sql in Supabase if RLS-related."
                        )
                        profiles = []

                if profiles:
                    self.profile_engine.load_profiles(profiles)
                    # Align the laptop's fan mode (and Y-key LED color) with
                    # whichever profile the cloud says is active. Otherwise
                    # the mobile UI shows e.g. Gaming while the BIOS is still
                    # in Quiet from a prior session.
                    active = self.profile_engine.active_profile
                    mode = _PROFILE_TO_MODE.get(active) if active else None
                    if mode is not None:
                        try:
                            self.hardware.set_fan_mode(mode)
                        except Exception as e:
                            logger.debug(f"Startup set_fan_mode({mode}) failed: {e}")

                # Load alert settings — non-fatal if RLS blocks.
                try:
                    settings = self.cloud.get_alert_settings(self.device_id)
                    for s in settings:
                        self.alert_thresholds[s["metric"]] = s
                except Exception as e:
                    logger.debug(f"Could not load alert settings: {e}")

                # Realtime command listener — sub-second mobile→agent latency.
                # The 30s safety-net poll in _monitoring_loop catches anything
                # missed during a reconnect.
                token = self.cloud.get_access_token()
                if token:
                    try:
                        from realtime_listener import CommandListener
                        self._cmd_listener = CommandListener(
                            url=CONFIG["supabase_url"],
                            anon_key=CONFIG["supabase_key"],
                            access_token=token,
                            device_id=self.device_id,
                            on_command=self._handle_command_sync,
                        )
                        self._cmd_listener.start()
                        logger.info("Realtime command listener started")
                    except Exception as e:
                        logger.warning(
                            f"Realtime listener unavailable, polling only: {e}"
                        )
                        self._cmd_listener = None
                else:
                    logger.info("No access token available; commands via polling only")

                logger.info(f"Loaded {len(profiles)} profiles")

            except Exception as e:
                logger.error(f"Cloud init failed: {e}")
                logger.info("Continuing in OFFLINE mode (local API only)")
                self.cloud = None
        else:
            logger.info("Cloud disabled, loading default profiles locally")

        # If no profiles loaded (offline or error), create defaults locally
        if not self.profile_engine.profiles:
            self._create_default_profiles_local()

        # Default alert thresholds if none from cloud
        if not self.alert_thresholds:
            self.alert_thresholds = {
                "cpu_temp": {
                    "metric": "cpu_temp", "threshold": 85.0,
                    "enabled": True, "cooldown_minutes": 5, "last_triggered": None,
                },
                "gpu_temp": {
                    "metric": "gpu_temp", "threshold": 85.0,
                    "enabled": True, "cooldown_minutes": 5, "last_triggered": None,
                },
            }

        self.running = True

        # The local API token is independent of the Supabase session. It's
        # a persistent per-machine secret stored in the OS keyring, shared
        # with the Tauri desktop frontend. Never use the rotating Supabase
        # JWT for this — it expires hourly and isn't shared cross-process.
        from pairing import get_or_create_local_api_token
        local_api_token = get_or_create_local_api_token()

        from local_server import start_server
        await asyncio.gather(
            start_server(self, CONFIG["local_port"], auth_token=local_api_token),
            self._monitoring_loop(),
        )

    async def stop(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self.running = False
        if self._cmd_listener:
            try:
                self._cmd_listener.stop()
            except Exception:
                pass
        if self.cloud and self.device_id:
            try:
                self.cloud.set_device_offline(self.device_id)
            except Exception:
                pass
        self.hardware.close()
        logger.info("Agent stopped.")

    # ------------------------------------------------------------------
    # Monitoring loop
    # ------------------------------------------------------------------
    async def _monitoring_loop(self):
        """Read sensors -> apply curve -> broadcast -> cloud sync."""
        await asyncio.sleep(1)
        logger.info("Monitoring loop started")

        loop = asyncio.get_event_loop()

        while self.running:
            try:
                # 1. Read sensors (potentially blocking I/O)
                data = await loop.run_in_executor(None, self.hardware.read_sensors)

                # 2. Apply active profile fan curve
                if self.profile_engine.active_profile and self.hardware.fan_count > 0:
                    curve_temp = data.get("cpu_temp") or data.get("gpu_temp")
                    if curve_temp is not None:
                        target = self.profile_engine.calculate_fan_speeds(
                            curve_temp, self.hardware.fan_count
                        )
                        for idx, speed in target.items():
                            self.hardware.set_fan_speed(idx, speed)
                        data["fan_speeds"] = self.hardware.read_fan_speeds()
                    data["active_profile"] = self.profile_engine.active_profile

                self._last_sensor_data = data

                # 3. Broadcast to local WebSocket clients (desktop UI)
                from local_server import broadcast_sensor_data
                await broadcast_sensor_data(data)

                # 4. Cloud sync (rate limiting done inside cloud.py)
                now = time.monotonic()
                if self.cloud and self.device_id:
                    try:
                        # FIX: Run blocking Supabase call in executor
                        await loop.run_in_executor(None, lambda: (
                            self.cloud.insert_sensor_reading(
                                device_id=self.device_id,
                                cpu_temp=data.get("cpu_temp") or 0,
                                cpu_load=data.get("cpu_load") or 0,
                                gpu_temp=data.get("gpu_temp"),
                                fan_speeds=data.get("fan_speeds", []),
                                ram_usage=data.get("ram_usage") or 0,
                            )
                        ))
                    except Exception as e:
                        logger.debug(f"Cloud insert failed: {e}")

                    # Heartbeat every N seconds
                    if now - self._last_heartbeat > CONFIG["heartbeat_interval"]:
                        try:
                            await loop.run_in_executor(
                                None, lambda: self.cloud.heartbeat(self.device_id)
                            )
                        except Exception:
                            pass
                        self._last_heartbeat = now

                    # Safety-net poll. Realtime listener handles the fast path;
                    # this only catches commands missed during a reconnect.
                    # Drops to 5s while the listener is disconnected.
                    poll_interval = (
                        30
                        if (self._cmd_listener and self._cmd_listener.connected)
                        else 5
                    )
                    if now - self._last_command_poll >= poll_interval:
                        try:
                            await loop.run_in_executor(
                                None, lambda: self.cloud.poll_commands(
                                    self.device_id, self._handle_command_sync
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Command poll error: {e}")
                        self._last_command_poll = now

                # 5. Check alerts
                await self._check_alerts(data)

            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)

            await asyncio.sleep(CONFIG["polling_interval"])

    # ------------------------------------------------------------------
    # Commands — FIX: Full payload validation
    # ------------------------------------------------------------------
    def _handle_command_sync(self, command: dict):
        """Handle incoming command (called from realtime callback, sync context)."""
        cmd_type = command.get("command_type", "")
        payload = command.get("payload", {})
        cmd_id = command.get("id")

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                logger.error(f"Invalid command payload JSON: {payload}")
                return

        if not isinstance(payload, dict):
            logger.error(f"Command payload is not a dict: {type(payload)}")
            return

        logger.info(f"Command: {cmd_type} -> {payload}")

        try:
            if cmd_type == "set_fan_speed":
                idx = int(payload.get("fan_index", 0))
                if idx < 0 or idx >= self.hardware.fan_count:
                    raise ValueError(f"Invalid fan_index: {idx}")
                speed = _clamp(payload.get("speed_percent", 50), 0, 100)
                self.hardware.set_fan_speed(idx, speed)
                self.profile_engine.set_active(None)

            elif cmd_type == "set_profile":
                name = str(payload.get("profile_name", "")).strip()[:32]
                if name not in self.profile_engine.profiles:
                    raise ValueError(f"Profile not found: {name}")
                self.profile_engine.set_active(name)

                # Flip the Lenovo Legion fan mode (this is what changes the
                # Y-key LED color and BIOS thermal policy). Quiet/Balanced/
                # Performance correspond to modes 1/2/3.
                mode = _PROFILE_TO_MODE.get(name)
                if mode is not None:
                    try:
                        self.hardware.set_fan_mode(mode)
                    except Exception as e:
                        logger.debug(f"set_fan_mode({mode}) failed: {e}")

                # Mirror to cloud so the mobile UI reflects the change.
                # Without this, the mobile re-renders from stale cloud state
                # and "reverts" to whichever profile had is_active=true.
                if self.cloud and self.device_id:
                    try:
                        self.cloud.set_profile_active(self.device_id, name)
                    except Exception as e:
                        logger.debug(f"Cloud profile sync failed: {e}")

            elif cmd_type == "set_alert_threshold":
                metric = str(payload.get("metric", ""))
                if metric not in ("cpu_temp", "gpu_temp"):
                    raise ValueError(f"Invalid metric: {metric}")
                threshold = _clamp(payload.get("threshold", 85), 30, 120)
                self.alert_thresholds[metric] = {
                    "metric": metric,
                    "threshold": threshold,
                    "enabled": True,
                    "cooldown_minutes": int(_clamp(payload.get("cooldown_minutes", 5), 1, 60)),
                    "last_triggered": None,
                }

            elif cmd_type == "set_all_fans":
                speed = _clamp(payload.get("speed_percent", 50), 0, 100)
                for i in range(self.hardware.fan_count):
                    self.hardware.set_fan_speed(i, speed)
                self.profile_engine.set_active(None)

            elif cmd_type == "set_fan_mode":
                try:
                    mode = int(payload.get("mode", 0))
                except (TypeError, ValueError):
                    raise ValueError("mode must be an integer 1, 2, or 3")
                if mode not in (1, 2, 3):
                    raise ValueError(f"Invalid fan mode: {mode}")
                if not self.hardware.set_fan_mode(mode):
                    raise RuntimeError(f"Hardware rejected fan mode {mode}")

            else:
                logger.warning(f"Unknown command type: {cmd_type}")
                return

            if self.cloud and cmd_id:
                self.cloud.update_command_status(cmd_id, "executed")

        except Exception as e:
            logger.error(f"Command failed: {e}", exc_info=True)
            if self.cloud and cmd_id:
                self.cloud.update_command_status(cmd_id, "failed")

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------
    async def _check_alerts(self, data: dict):
        """Fire alerts when thresholds exceeded (with cooldown)."""
        now = datetime.now(timezone.utc)

        for metric, settings in self.alert_thresholds.items():
            if not settings.get("enabled"):
                continue
            value = data.get(metric)
            if value is None:
                continue

            threshold = settings["threshold"]
            if value < threshold:
                continue

            # Cooldown check
            last = settings.get("last_triggered")
            cooldown = settings.get("cooldown_minutes", 5) * 60
            if last and (now - last).total_seconds() < cooldown:
                continue

            # Fire alert
            settings["last_triggered"] = now
            logger.warning(f"ALERT: {metric}={value:.1f}C (threshold {threshold}C)")

            from local_server import broadcast_sensor_data
            await broadcast_sensor_data({
                "type": "alert",
                "metric": metric,
                "value": value,
                "threshold": threshold,
            })

            if self.cloud and self.device_id:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, lambda: self.cloud.insert_alert(
                            self.device_id, metric, value, threshold
                        )
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Default profiles
    # ------------------------------------------------------------------
    _DEFAULT_PROFILES = [
        {"name": "Silent", "fan_curve": [
            {"temp": 30, "speed": 20}, {"temp": 50, "speed": 30},
            {"temp": 65, "speed": 45}, {"temp": 75, "speed": 60},
            {"temp": 85, "speed": 80}]},
        {"name": "Balanced", "fan_curve": [
            {"temp": 30, "speed": 30}, {"temp": 50, "speed": 45},
            {"temp": 65, "speed": 60}, {"temp": 75, "speed": 80},
            {"temp": 85, "speed": 100}]},
        {"name": "Gaming", "fan_curve": [
            {"temp": 30, "speed": 40}, {"temp": 50, "speed": 60},
            {"temp": 65, "speed": 80}, {"temp": 75, "speed": 95},
            {"temp": 85, "speed": 100}]},
        {"name": "Turbo", "fan_curve": [
            {"temp": 30, "speed": 60}, {"temp": 50, "speed": 80},
            {"temp": 65, "speed": 100}, {"temp": 75, "speed": 100},
            {"temp": 85, "speed": 100}]},
    ]

    def _create_default_profiles_cloud(self):
        for i, p in enumerate(self._DEFAULT_PROFILES):
            self.cloud.create_profile(
                device_id=self.device_id,
                name=p["name"],
                fan_curve=p["fan_curve"],
                is_active=(p["name"] == "Balanced"),
            )

    def _create_default_profiles_local(self):
        import uuid
        profiles = []
        for p in self._DEFAULT_PROFILES:
            profiles.append({
                "id": str(uuid.uuid4()),
                "name": p["name"],
                "fan_curve": p["fan_curve"],
                "is_active": (p["name"] == "Balanced"),
            })
        self.profile_engine.load_profiles(profiles)
        logger.info(f"Loaded {len(profiles)} default profiles (local)")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _get_hardware_id() -> str:
        import hashlib, platform, uuid as _uuid
        raw = f"{platform.node()}-{platform.machine()}-{_uuid.getnode()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _get_os_info() -> str:
        import platform
        return f"{platform.system()} {platform.release()} ({platform.version()})"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ThermalControl Agent")
    parser.add_argument("--demo", action="store_true", help="Force demo mode")
    parser.add_argument(
        "--print-local-api-token",
        action="store_true",
        help="Print the local API bearer token to stdout and exit. "
             "Used by the desktop frontend to authenticate against the "
             "local agent. Generates one on first call.",
    )
    parser.add_argument(
        "--reset-pairing",
        action="store_true",
        help="Wipe stored pairing credentials so the next run prompts for a new code.",
    )
    args = parser.parse_args()

    if args.print_local_api_token:
        from pairing import get_or_create_local_api_token
        sys.stdout.write(get_or_create_local_api_token() + "\n")
        sys.stdout.flush()
        return

    if args.reset_pairing:
        from pairing import clear_stored
        clear_stored(CONFIG["supabase_url"])
        logger.info("Pairing credentials cleared. Next run will prompt for a code.")
        return

    # Admin check on Windows
    if sys.platform == "win32":
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                logger.warning(
                    "Not running as Administrator - fan control may not work."
                )
        except Exception:
            pass

    agent = FanControlAgent(force_demo=args.demo)

    async def run():
        try:
            await agent.start()
        except KeyboardInterrupt:
            pass
        finally:
            await agent.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()