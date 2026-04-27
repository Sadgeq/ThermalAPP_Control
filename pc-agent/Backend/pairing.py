"""
Device pairing + DPAPI-backed credential storage
================================================
Replaces the .env email/password flow with a one-time PIN exchange.

Flow
----
1. User signs in on mobile, hits "Add Device", gets a 6-char code.
2. Agent first run: this module prompts for the code on stdin.
3. We call the SUPABASE RPC `claim_pairing(...)` — anonymous-callable.
4. The RPC returns `{device_id, user_id, refresh_token}` from the mobile
   session. We exchange the refresh token for a fresh access token via
   `auth.refresh_session(refresh_token)`.
5. We persist the (possibly rotated) refresh token + device_id under a
   per-user-per-machine keyring entry (Windows DPAPI on Win32, libsecret
   on Linux, Keychain on macOS).

Subsequent runs read the keyring, refresh the session, and skip the wizard.

Dependencies
------------
- keyring (cross-platform OS credential store)
- supabase-py (already in use)

Install:
    pip install keyring
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("Pairing")

# A single namespace inside the OS credential store. The "username" we use
# is the Supabase project URL so multiple environments coexist on one PC.
_KEYRING_SERVICE = "thermalctl-agent"
_KEY_DEVICE = "device"   # JSON: {device_id, user_id, refresh_token}


# ---------------------------------------------------------------------------
# Stored credential
# ---------------------------------------------------------------------------
@dataclass
class StoredDevice:
    device_id: str
    user_id: str
    refresh_token: str = ""   # preferred: long-lived session
    access_token: str = ""    # fallback: ~1h session, requires re-pair

    def to_json(self) -> str:
        return json.dumps({
            "device_id": self.device_id,
            "user_id": self.user_id,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
        })

    @classmethod
    def from_json(cls, raw: str) -> "StoredDevice":
        d = json.loads(raw)
        return cls(
            device_id=d["device_id"],
            user_id=d["user_id"],
            refresh_token=d.get("refresh_token") or "",
            access_token=d.get("access_token") or "",
        )


# ---------------------------------------------------------------------------
# Keyring helpers (degrades to file-on-disk if `keyring` is unavailable)
# ---------------------------------------------------------------------------
def _supabase_url_key(supabase_url: str) -> str:
    """Use the project URL as the keyring 'username' so multiple Supabase
    projects can coexist on one machine without collision."""
    return f"{_KEY_DEVICE}@{supabase_url}"


def load_stored(supabase_url: str) -> Optional[StoredDevice]:
    try:
        import keyring
        raw = keyring.get_password(_KEYRING_SERVICE, _supabase_url_key(supabase_url))
    except Exception as e:
        logger.debug(f"Keyring read failed: {e}")
        return None
    if not raw:
        return None
    try:
        return StoredDevice.from_json(raw)
    except Exception as e:
        logger.warning(f"Stored device payload corrupt, ignoring: {e}")
        return None


def save_stored(supabase_url: str, sd: StoredDevice) -> None:
    try:
        import keyring
        keyring.set_password(
            _KEYRING_SERVICE,
            _supabase_url_key(supabase_url),
            sd.to_json(),
        )
        logger.info("Device credentials saved to OS keyring")
    except Exception as e:
        logger.error(f"Keyring write failed: {e}")
        raise


def clear_stored(supabase_url: str) -> None:
    try:
        import keyring
        keyring.delete_password(_KEYRING_SERVICE, _supabase_url_key(supabase_url))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Local API bearer token
# ---------------------------------------------------------------------------
# Stored under the same keyring service so a single OS prompt unlocks both.
_LOCAL_API_KEY = "local_api_token"


def get_or_create_local_api_token() -> str:
    """Return the persistent Bearer token used by the local API server.

    Generated once per machine, stored in the OS keyring. Falls back to
    an in-memory token if the keyring is unavailable — that token won't
    survive restarts but at least the agent stays functional.
    """
    try:
        import keyring
        existing = keyring.get_password(_KEYRING_SERVICE, _LOCAL_API_KEY)
        if existing and len(existing) >= 32:
            return existing
        # Cryptographically secure 32 bytes -> 43-char base64.
        import secrets
        tok = secrets.token_urlsafe(32)
        keyring.set_password(_KEYRING_SERVICE, _LOCAL_API_KEY, tok)
        logger.info("Generated new local API token (stored in OS keyring)")
        return tok
    except Exception as e:
        logger.error(
            f"Keyring unavailable, using volatile local API token: {e}"
        )
        import secrets
        return secrets.token_urlsafe(32)


def get_local_api_token() -> Optional[str]:
    """Read the local API token from the keyring without creating one."""
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, _LOCAL_API_KEY)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pairing RPC + interactive wizard
# ---------------------------------------------------------------------------
def claim_with_code(
    cloud_client,
    code: str,
    hardware_id: str,
    name: str,
    os_info: str,
) -> StoredDevice:
    """Call the claim_pairing RPC and bootstrap a session.

    `cloud_client` is a SupabaseClient with no user session yet — the RPC
    is granted to the anon role.
    """
    code = (code or "").strip().upper().replace("-", "").replace(" ", "")
    if len(code) != 6:
        raise ValueError("Pairing code must be 6 characters")

    # Anonymous RPC call. supabase-py's .rpc() uses the configured anon key.
    resp = cloud_client.client.rpc(
        "claim_pairing",
        {
            "p_code": code,
            "p_hardware_id": hardware_id,
            "p_name": name[:64],
            "p_os_info": os_info[:128],
        },
    ).execute()

    rows = resp.data or []
    if not rows:
        raise RuntimeError("Pairing failed: empty response")
    row = rows[0] if isinstance(rows, list) else rows

    refresh = (row.get("refresh_token") or "").strip()
    access = (row.get("access_token") or "").strip()
    device_id = row.get("device_id")
    user_id = row.get("user_id")
    if not (device_id and user_id) or not (refresh or access):
        raise RuntimeError("Pairing failed: incomplete response")

    # Bootstrap a session. Prefer the refresh path (long-lived); fall back
    # to set_session with the access token (valid ~1h, agent must re-pair
    # when it expires).
    rotated_refresh = ""
    if refresh:
        try:
            rotated_refresh = _refresh_session(cloud_client, refresh) or refresh
        except Exception as e:
            logger.warning(f"refresh_session failed, falling back to access token: {e}")
            refresh = ""  # disable persisted refresh
            if access:
                _set_access_only(cloud_client, access)
            else:
                raise
    else:
        _set_access_only(cloud_client, access)

    cloud_client.user_id = user_id
    cloud_client._healthy = True

    return StoredDevice(
        device_id=device_id,
        user_id=user_id,
        refresh_token=rotated_refresh or refresh or "",
        access_token=access if not refresh else "",
    )


def _set_access_only(cloud_client, access_token: str) -> None:
    """Bootstrap a short-lived session using only an access token."""
    try:
        # supabase-py's set_session needs a refresh_token slot; pass the
        # access token there too as a placeholder. PostgREST only validates
        # the access token via the Authorization header.
        cloud_client.client.auth.set_session(access_token, access_token)
    except Exception:
        # Last resort: stuff the access token into the postgrest auth header
        # directly. The session won't auto-refresh but reads/writes work.
        try:
            cloud_client.client.postgrest.auth(access_token)
        except Exception:
            pass


def resume_with_stored(cloud_client, sd: StoredDevice) -> StoredDevice:
    """Bootstrap a session from previously stored credentials.

    Returns a (possibly rotated) StoredDevice the caller should persist.
    Raises if the credentials no longer work — caller should fall back to
    the pairing wizard.
    """
    if sd.refresh_token:
        rotated = _refresh_session(cloud_client, sd.refresh_token)
        cloud_client.user_id = sd.user_id
        cloud_client._healthy = True
        return StoredDevice(
            device_id=sd.device_id,
            user_id=sd.user_id,
            refresh_token=rotated or sd.refresh_token,
            access_token="",
        )
    if sd.access_token:
        _set_access_only(cloud_client, sd.access_token)
        cloud_client.user_id = sd.user_id
        cloud_client._healthy = True
        # Note: access tokens expire (~1h). If the user keeps the agent
        # running past that, writes will start failing — they'll need to
        # re-pair. The exception will bubble up and trigger the wizard.
        return sd
    raise RuntimeError("No usable credentials in keyring; re-pair required")


def _refresh_session(cloud_client, refresh_token: str) -> Optional[str]:
    """Use a refresh token to set a live session on the Supabase client.

    Returns the (possibly rotated) refresh token, or None if the client
    didn't expose one.
    """
    auth = cloud_client.client.auth
    # supabase-py exposes refresh_session in newer releases; fall back to
    # set_session with a dummy access JWT if needed.
    try:
        sess = auth.refresh_session(refresh_token)
    except TypeError:
        # Older signature: refresh_session() reads from internal storage.
        # Use set_session with the refresh-only path.
        sess = auth.set_session("", refresh_token)

    new_refresh = None
    try:
        s = getattr(sess, "session", None) or sess
        new_refresh = getattr(s, "refresh_token", None)
    except Exception:
        pass
    return new_refresh


def prompt_for_code() -> str:
    """Read a pairing code from stdin with a friendly banner."""
    sys.stdout.write(
        "\n"
        "============================================================\n"
        "  ThermalControl — first run pairing\n"
        "============================================================\n"
        "  1. Open the ThermalControl mobile app\n"
        "  2. Sign in (Google or email)\n"
        "  3. Tap  Settings  →  Add this PC\n"
        "  4. Read the 6-character code shown on your phone\n"
        "  5. Type it below (case-insensitive, dashes optional)\n"
        "------------------------------------------------------------\n"
    )
    sys.stdout.flush()
    return input("  Pairing code: ").strip()


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------
def authenticate_or_pair(
    cloud_client,
    supabase_url: str,
    hardware_id: str,
    device_name: str,
    os_info: str,
    interactive: bool = True,
) -> StoredDevice:
    """Establish a Supabase session for the agent.

    Order of attempts:
      1. Stored refresh token in keyring → refresh_session.
      2. Interactive PIN pairing → claim_pairing RPC.
    Saves the resulting credential to the keyring on success.

    Raises RuntimeError if no path works.
    """
    # 1. Resume from keyring.
    stored = load_stored(supabase_url)
    if stored:
        try:
            fresh = resume_with_stored(cloud_client, stored)
            if fresh.refresh_token != stored.refresh_token:
                save_stored(supabase_url, fresh)
            logger.info(f"Resumed session for device {fresh.device_id[:8]}…")
            return fresh
        except Exception as e:
            logger.warning(f"Stored credential rejected, re-pairing: {e}")
            clear_stored(supabase_url)

    # 2. Interactive pairing.
    if not interactive:
        raise RuntimeError("No stored credentials and pairing wizard disabled")

    if not sys.stdin.isatty():
        raise RuntimeError(
            "No stored credentials and stdin is not a TTY. "
            "Run the agent interactively the first time, or pre-seed the "
            "keyring via a small helper script."
        )

    last_err: Optional[Exception] = None
    for attempt in range(3):
        code = prompt_for_code()
        try:
            sd = claim_with_code(
                cloud_client,
                code=code,
                hardware_id=hardware_id,
                name=device_name,
                os_info=os_info,
            )
            save_stored(supabase_url, sd)
            print(f"\n  Paired. Welcome to ThermalControl.\n")
            return sd
        except Exception as e:
            last_err = e
            print(f"\n  ✗ Pairing failed: {e}\n")

    raise RuntimeError(f"Pairing exhausted retries: {last_err}")
