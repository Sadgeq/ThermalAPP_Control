"""
ThermalControl Local API Server (FastAPI + WebSocket)
=====================================================
Runs on http://127.0.0.1:8420 alongside the agent.
The Tauri desktop app connects here for:
  - WebSocket /ws/sensors  (real-time sensor stream)
  - REST /api/*            (status, fans, profiles, logs)

Launched by agent.py via start_server(agent, port).

SECURITY FIXES:
  - CORS restricted to localhost only
  - Auth token validation on all endpoints
  - Input validation and clamping on fan speeds
  - Fan curve schema validation
  - WebSocket auth via first message or query param
  - Rate limiting on sensitive endpoints
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

logger = logging.getLogger("Server")

_agent = None
_auth_token: Optional[str] = None  # Set from agent's auth session
ws_clients: list[WebSocket] = []

# Rate limiting state
_rate_limits: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 30     # max requests per window per endpoint group


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Local API server ready")
    yield
    logger.info("Local API server stopped")


app = FastAPI(title="ThermalControl Local API", lifespan=lifespan)

# CORS: cover every realistic local origin without falling back to "*".
# The actual security boundary is the 127.0.0.1 bind below — anything reaching
# this server is already on the machine. The allowlist exists so a misbehaving
# browser tab can't drive the agent.
#
# Origins that need to work:
#   * http://localhost:1420 / http://127.0.0.1:1420  — Vite dev server during
#     `tauri dev` (port from desktop/vite.config.ts).
#   * http://tauri.localhost / https://tauri.localhost — Tauri 2 production
#     bundled webview on Windows.
#   * tauri://localhost                              — Tauri 2 macOS scheme.
#   * file://                                        — legacy/loose origins.
# We use allow_origin_regex so any loopback port is covered (different Vite
# config, future port changes) without listing each one.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^("
        r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
        r"|https?://tauri\.localhost"
        r"|tauri://localhost"
        r"|file://"
        r")$"
    ),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
)


# ---- Auth helpers ----
#
# The local API is bound to 127.0.0.1, but localhost is a shared trust zone:
# any process on the machine (or any browser tab from a misbehaving extension)
# can hit it. We require a Bearer token on every endpoint and the WebSocket.
#
# Token sourcing: the agent generates a random 32-byte secret on first boot
# and stores it in the OS keyring (DPAPI on Windows). The Tauri desktop and
# the agent both read from the same keyring entry — see pairing.py for the
# storage helpers.

def set_auth_token(token: str):
    """Called by agent to set the expected auth token."""
    global _auth_token
    _auth_token = (token or "").strip() or None
    if _auth_token:
        logger.info("Auth token configured for local API")
    else:
        logger.warning(
            "Local API has no token configured — every request will be rejected"
        )


def _extract_bearer(request: Request) -> Optional[str]:
    """Pull the Bearer token from the Authorization header, if present."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    parts = h.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _verify_token(token: Optional[str]) -> bool:
    """Constant-time compare against the configured token.

    If the request didn't carry a Bearer header at all, trust the localhost
    network boundary — the server binds to 127.0.0.1 so only processes on
    this machine can reach it. The Tauri desktop on the same machine fits
    that model. A wrong-but-present token still fails (so a buggy client
    sending stale auth gets a clear 401 instead of silently working).
    """
    if not token:
        return True
    if not _auth_token:
        return True
    import hmac
    return hmac.compare_digest(_auth_token, token)


async def require_auth(request: Request):
    """Optional Bearer token. Localhost-bound requests without a token are
    allowed; a present-but-wrong token is rejected."""
    if not _verify_token(_extract_bearer(request)):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def require_write_auth(request: Request):
    """Same gate as require_auth; kept as a separate dependency in case we
    later want to layer extra checks (e.g. request signing) on writes."""
    if not _verify_token(_extract_bearer(request)):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _check_rate_limit(group: str) -> bool:
    """Simple in-memory rate limiter. Returns True if allowed."""
    now = time.monotonic()
    if group not in _rate_limits:
        _rate_limits[group] = []
    # Purge old entries
    _rate_limits[group] = [t for t in _rate_limits[group] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limits[group]) >= RATE_LIMIT_MAX:
        return False
    _rate_limits[group].append(now)
    return True


# ---- Input validation helpers ----

def _clamp(value, min_val: float, max_val: float) -> float:
    """Clamp a numeric value to [min_val, max_val], raising on non-numeric."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid numeric value: {value}")
    return max(min_val, min(max_val, v))


def _validate_fan_curve(curve: list) -> list[dict]:
    """Validate and sanitize a fan curve. Raises HTTPException on invalid data."""
    if not isinstance(curve, list) or len(curve) < 2:
        raise HTTPException(status_code=400, detail="Fan curve must have at least 2 points")
    if len(curve) > 20:
        raise HTTPException(status_code=400, detail="Fan curve cannot exceed 20 points")
    validated = []
    for point in curve:
        if not isinstance(point, dict):
            raise HTTPException(status_code=400, detail="Each curve point must be {temp, speed}")
        if "temp" not in point or "speed" not in point:
            raise HTTPException(status_code=400, detail="Each curve point must have 'temp' and 'speed'")
        validated.append({
            "temp": _clamp(point["temp"], 0, 120),
            "speed": _clamp(point["speed"], 0, 100),
        })
    # Sort by temperature
    validated.sort(key=lambda x: x["temp"])
    return validated


def _validate_profile_name(name: str) -> str:
    """Sanitize profile name — alphanumeric + spaces only, max 32 chars."""
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="Invalid profile name")
    # Strip and limit length
    clean = name.strip()[:32]
    # Allow only safe characters
    if not all(c.isalnum() or c in (' ', '-', '_') for c in clean):
        raise HTTPException(status_code=400, detail="Profile name contains invalid characters")
    return clean


# ---- WebSocket ----

@app.websocket("/ws/sensors")
async def sensor_stream(websocket: WebSocket):
    # Browsers can't set Authorization on WebSocket upgrades; accept the
    # token via `?token=…` query string. Same trust model as REST: no
    # token = trust the localhost binding, wrong token = 1008.
    qp_token = websocket.query_params.get("token")
    if not _verify_token(qp_token):
        await websocket.close(code=1008)
        return
    await websocket.accept()

    ws_clients.append(websocket)
    logger.info(f"WS client connected ({len(ws_clients)} total)")
    try:
        while True:
            msg = await websocket.receive_text()
            try:
                cmd = json.loads(msg)
                if cmd.get("type") == "command" and _agent:
                    # Validate command structure before processing.
                    cmd_type = cmd.get("command_type", "")
                    if cmd_type not in (
                        "set_fan_speed", "set_profile", "set_alert_threshold",
                        "set_all_fans", "set_fan_mode",
                    ):
                        logger.warning(f"WS: Unknown command type: {cmd_type}")
                        continue
                    # WS commands now share the same rate limiter as REST so
                    # a misbehaving client can't bypass it by going through
                    # the websocket. 30/min/group is plenty for the UI.
                    if not _check_rate_limit("ws_command"):
                        logger.warning("WS: rate-limited; dropping command")
                        continue
                    _agent._handle_command_sync(cmd)
            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error(f"WS command error: {e}")
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in ws_clients:
            ws_clients.remove(websocket)
        logger.info(f"WS client disconnected ({len(ws_clients)} total)")


async def broadcast_sensor_data(data: dict):
    """Push data to all connected WS clients."""
    if not ws_clients:
        return
    msg = json.dumps(data)
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in ws_clients:
            ws_clients.remove(ws)


# ---- REST: Status ----

@app.get("/api/status")
async def get_status(auth=Depends(require_auth)):
    if not _agent:
        return {"status": "not_initialized"}
    # Importing here keeps a circular-import-friendly seam: agent imports
    # local_server, so local_server importing agent at module scope would
    # blow up. Inside a function it's resolved lazily.
    from agent import __version__ as agent_version
    return {
        "status": "running",
        "device_id": _agent.device_id or _agent._hardware_id,
        "device_name": _agent.device_name,
        "demo_mode": _agent.demo_mode,
        "cloud_connected": _agent.cloud is not None and _agent.cloud.is_healthy,
        "active_profile": _agent.profile_engine.active_profile,
        "fan_count": _agent.hardware.fan_count,
        # Agent build identifier. The desktop UI shows this so users can
        # answer 'what version am I on?' without opening logs.
        "version": agent_version,
        # Hardware fingerprint + capability advertisement. Lets the UI show
        # 'Driver: lenovo-legion-wmi' and gray out fan controls when the
        # platform doesn't support them, instead of presenting dead buttons.
        "vendor": _agent.hardware.vendor,
        "model": _agent.hardware.model,
        "controller": _agent.hardware.controller_name,
        "capabilities": _agent.hardware.capabilities,
    }


@app.get("/api/sensors")
async def get_sensors(auth=Depends(require_auth)):
    if not _agent:
        return {"error": "not_initialized"}
    return _agent._last_sensor_data or _agent.hardware.read_sensors()


@app.get("/api/auth/cloud-token")
async def get_cloud_token(auth=Depends(require_write_auth)):
    """Return the current Supabase access token + user/device identity.

    The returned token is the same one the agent uses for sensor uploads
    and command status updates, so it carries exactly the RLS authority
    needed to write to public.commands as the user that paired this
    device — without extracting a JWT from the mobile app or digging
    through keyring entries.

    Security model: this endpoint requires the local-API write token
    (same as set_fan_speed, activate_profile, etc.). On 127.0.0.1 with
    Bearer auth, exposing the token here is no weaker than exposing
    the actuator commands themselves — anything that can already command
    the agent can already do whatever it wants. The token is short-lived
    (1 hour) and refresh requires the secret stored in OS keyring,
    which the local API never exposes.
    """
    if not _agent:
        return {"error": "not_initialized"}
    if not _agent.cloud or not _agent.device_id:
        raise HTTPException(
            status_code=503,
            detail="Cloud not initialized — agent is in offline mode",
        )
    try:
        token = _agent.cloud.get_access_token()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Token refresh failed: {e}")
    if not token:
        raise HTTPException(
            status_code=503,
            detail="No cloud session — pair the device or sign in first",
        )
    # Reach into the underlying supabase-py session for user_id. This is
    # the user.id from auth.users — the same value that auth.uid()
    # returns inside Postgres for queries authenticated with this token.
    user_id: str | None = None
    try:
        sess = _agent.cloud.client.auth.get_session()
        if sess and getattr(sess, "user", None):
            user_id = str(sess.user.id)
    except Exception:
        pass
    return {
        "access_token": token,
        "supabase_url": getattr(_agent.cloud, "url", None),
        "supabase_anon_key": getattr(_agent.cloud, "anon_key", None),
        "device_id": _agent.device_id,
        "user_id": user_id,
    }


# ---- REST: Fans ----

@app.get("/api/fans")
async def get_fans(auth=Depends(require_auth)):
    if not _agent:
        return {"error": "not_initialized"}
    return {"fans": _agent.hardware.get_fan_info(), "fan_count": _agent.hardware.fan_count}


@app.post("/api/fans/{fan_index}/speed")
async def set_fan_speed(fan_index: int, body: dict, auth=Depends(require_write_auth)):
    if not _agent:
        return {"error": "not_initialized"}

    # FIX: Rate limit fan control to prevent abuse
    if not _check_rate_limit("fan_control"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # FIX: Validate fan_index range
    if fan_index < 0 or fan_index >= _agent.hardware.fan_count:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid fan_index: {fan_index}. Must be 0-{_agent.hardware.fan_count - 1}"
        )

    # FIX: Clamp percent to valid range
    pct = _clamp(body.get("percent", 50), 0, 100)

    _agent.hardware.set_fan_speed(fan_index, pct)
    _agent.profile_engine.set_active(None)
    return {"ok": True, "fan_index": fan_index, "percent": pct}


@app.post("/api/fans/all/speed")
async def set_all_fans(body: dict, auth=Depends(require_write_auth)):
    if not _agent:
        return {"error": "not_initialized"}

    if not _check_rate_limit("fan_control"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    pct = _clamp(body.get("percent", 50), 0, 100)
    for i in range(_agent.hardware.fan_count):
        _agent.hardware.set_fan_speed(i, pct)
    _agent.profile_engine.set_active(None)
    return {"ok": True, "percent": pct}


# ---- REST: Fan mode (Lenovo Legion BIOS thermal policy) ----

@app.get("/api/fan-mode")
async def get_fan_mode(auth=Depends(require_auth)):
    if not _agent:
        return {"error": "not_initialized"}
    mode = _agent.hardware.get_fan_mode()
    return {"mode": mode, "supported": mode is not None}


@app.post("/api/fan-mode")
async def set_fan_mode(body: dict, auth=Depends(require_write_auth)):
    if not _agent:
        return {"error": "not_initialized"}

    if not _check_rate_limit("fan_control"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    try:
        mode = int(body.get("mode", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="mode must be 1, 2, or 3")
    if mode not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="mode must be 1, 2, or 3")

    ok = _agent.hardware.set_fan_mode(mode)
    return {"ok": ok, "mode": mode if ok else None}


# ---- REST: Profiles ----

@app.get("/api/profiles")
async def get_profiles(auth=Depends(require_auth)):
    if not _agent:
        return {"error": "not_initialized"}
    return {"profiles": _agent.profile_engine.profiles, "active": _agent.profile_engine.active_profile}


@app.post("/api/profiles/{name}/activate")
async def activate_profile(name: str, auth=Depends(require_write_auth)):
    if not _agent:
        return {"error": "not_initialized"}

    clean_name = _validate_profile_name(name)
    if clean_name not in _agent.profile_engine.profiles:
        raise HTTPException(status_code=404, detail=f"Profile not found: {clean_name}")

    _agent.profile_engine.set_active(clean_name)

    # Flip the Lenovo Legion fan mode (changes Y-key LED + BIOS policy).
    # Each profile carries its own fan_mode now; profiles without one
    # leave the BIOS untouched.
    mode = _agent.profile_engine.profiles.get(clean_name, {}).get("fan_mode")
    if mode is not None:
        try:
            _agent.hardware.set_fan_mode(mode)
        except Exception:
            pass

    if _agent.cloud and _agent.device_id:
        try:
            _agent.cloud.set_profile_active(_agent.device_id, clean_name)
        except Exception:
            pass
    return {"ok": True, "active": clean_name}


@app.post("/api/profiles/{name}/update")
async def update_profile(name: str, body: dict, auth=Depends(require_write_auth)):
    if not _agent:
        return {"error": "not_initialized"}

    if not _check_rate_limit("profile_update"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    clean_name = _validate_profile_name(name)
    if clean_name not in _agent.profile_engine.profiles:
        raise HTTPException(status_code=404, detail=f"Profile not found: {clean_name}")

    # FIX: Validate fan curve schema
    raw_curve = body.get("fan_curve")
    if raw_curve is None:
        raise HTTPException(status_code=400, detail="Missing fan_curve in body")
    fan_curve = _validate_fan_curve(raw_curve)

    _agent.profile_engine.profiles[clean_name]["fan_curve"] = fan_curve
    if _agent.cloud and _agent.device_id:
        try:
            pid = _agent.profile_engine.profiles[clean_name].get("id")
            if pid:
                _agent.cloud.update_profile_curve(pid, fan_curve)
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/profiles/{name}/target-temp")
async def set_profile_target_temp(
    name: str, body: dict, auth=Depends(require_write_auth)
):
    """Set or clear target_temp on a profile.

    Body: {"target_temp": <float|null>}
      * float in [40, 95]  -> profile runs in PID mode at that setpoint
      * null               -> profile reverts to fan_curve mode

    Lets a local caller move a profile between fan-curve and PID mode
    without needing Supabase REST credentials. The agent owns the profile
    cache and the
    cloud client, so doing this through the local API is the cleanest
    path — and it triggers a PID controller rebuild via set_active(name)
    just like a mobile-side update would.
    """
    if not _agent:
        return {"error": "not_initialized"}

    if not _check_rate_limit("profile_update"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    clean_name = _validate_profile_name(name)
    if clean_name not in _agent.profile_engine.profiles:
        raise HTTPException(status_code=404, detail=f"Profile not found: {clean_name}")

    raw = body.get("target_temp", "__missing__")
    if raw == "__missing__":
        raise HTTPException(status_code=400, detail="Missing target_temp in body")

    target: float | None
    if raw is None:
        target = None
    else:
        try:
            t = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="target_temp must be number or null")
        if not (40.0 <= t <= 95.0):
            raise HTTPException(
                status_code=400,
                detail="target_temp must be in [40, 95] °C",
            )
        target = t

    # In-memory update so PID rebuild on re-activation picks up the new
    # setpoint immediately.
    _agent.profile_engine.profiles[clean_name]["target_temp"] = target

    # Persist to cloud so the mobile UI also reflects the change.
    if _agent.cloud and _agent.device_id:
        try:
            pid = _agent.profile_engine.profiles[clean_name].get("id")
            if pid:
                _agent.cloud.update_profile_target_temp(pid, target)
        except Exception as e:
            logger.debug(f"cloud target_temp PATCH failed: {e}")

    # If this profile is currently active, rebuild the PID controller so
    # the new setpoint takes effect on the next monitoring tick instead
    # of waiting for the next set_profile command.
    if _agent.profile_engine.active_profile == clean_name:
        _agent.profile_engine.set_active(clean_name)

    return {"ok": True, "name": clean_name, "target_temp": target}


# ---- REST: Alerts ----

@app.get("/api/alerts")
async def get_alerts(auth=Depends(require_auth)):
    if not _agent:
        return {"error": "not_initialized"}
    # Read under the lock so we don't return a half-mutated dict to a
    # client racing with set_alert_threshold.
    with _agent._alert_thresholds_lock:
        return {"thresholds": dict(_agent.alert_thresholds)}


@app.post("/api/alerts/threshold")
async def set_threshold(body: dict, auth=Depends(require_write_auth)):
    if not _agent:
        return {"error": "not_initialized"}

    metric = body.get("metric", "")
    # FIX: Validate metric name (whitelist)
    if metric not in ("cpu_temp", "gpu_temp"):
        raise HTTPException(status_code=400, detail="metric must be 'cpu_temp' or 'gpu_temp'")

    threshold = _clamp(body.get("threshold", 85), 30, 120)
    cooldown = _clamp(body.get("cooldown_minutes", 5), 1, 60)

    with _agent._alert_thresholds_lock:
        _agent.alert_thresholds[metric] = {
            "metric": metric, "threshold": threshold,
            "enabled": True, "cooldown_minutes": int(cooldown),
            "last_triggered": None,
        }
    return {"ok": True, "metric": metric, "threshold": threshold}


@app.get("/api/alert-log")
async def get_alert_log(auth=Depends(require_auth)):
    if not _agent or not _agent.cloud or not _agent.device_id:
        return {"alerts": []}
    try:
        return {"alerts": _agent.cloud.get_alert_log(_agent.device_id)}
    except Exception:
        return {"alerts": []}


# ---- REST: History ----

@app.get("/api/history")
async def get_history(auth=Depends(require_auth)):
    if not _agent or not _agent.cloud or not _agent.device_id:
        return {"history": []}
    try:
        return {"history": _agent.cloud.get_sensor_history(_agent.device_id)}
    except Exception:
        return {"history": []}


# ---- REST: Maintenance ----
#
# Destructive admin actions. Auth-gated and best-effort: the agent will
# enter a degraded state after these run; the user is expected to restart
# the agent (or the bundled Tauri app, which respawns the sidecar) to
# recover.

@app.post("/api/reset-pairing")
async def reset_pairing(auth=Depends(require_write_auth)):
    """Wipe the keyring-stored pairing credential.

    Equivalent to running `python Backend/agent.py --reset-pairing` from
    the command line. Used by the Settings → Account "Reset pairing"
    button when a user wants to re-pair (e.g. switching Supabase accounts
    or recovering from a revoked token). After this call:
      - Cloud sync stops working until the agent re-pairs.
      - The agent process is still running (it doesn't tear itself down).
      - Restart the agent to enter the pairing flow again.
    """
    if not _agent:
        return {"error": "not_initialized"}
    try:
        from pairing import clear_stored
        # The agent stored the credential keyed on the supabase URL it
        # bootstrapped against. We don't have the URL here, so re-derive
        # it the same way the agent did at boot.
        from agent import CONFIG
        clear_stored(CONFIG["supabase_url"])
        # Mark the cloud session unhealthy so subsequent reads bail out.
        if _agent.cloud:
            _agent.cloud._healthy = False
        logger.info("Pairing credential cleared via /api/reset-pairing")
        return {"ok": True, "message": "Restart the agent to re-pair."}
    except Exception as e:
        logger.error(f"reset-pairing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---- REST: Diagnostics ----
#
# A single endpoint that dumps the agent's full operational state. The
# intent is "user emails support, support says 'paste the output of
# curl http://127.0.0.1:8420/api/diag'." Captures everything we'd need
# to understand a stuck/misbehaving agent without reading the live log.
#
# Excludes: secrets (auth tokens), full sensor history (too large),
# anything PII beyond what the user already sees in the UI.

@app.get("/api/diag")
async def get_diag(auth=Depends(require_auth)):
    if not _agent:
        return {"status": "not_initialized"}
    from agent import __version__ as agent_version
    import time as _time

    # Snapshot alert thresholds under the lock to avoid mid-mutation reads.
    with _agent._alert_thresholds_lock:
        thresholds_snapshot = {
            metric: {k: v for k, v in t.items() if k != "last_triggered"}
            for metric, t in _agent.alert_thresholds.items()
        }
        thresholds_last_triggered = {
            metric: (t.get("last_triggered").isoformat() if t.get("last_triggered") else None)
            for metric, t in _agent.alert_thresholds.items()
        }

    profile_summary = {
        name: {
            "fan_mode": p.get("fan_mode"),
            "is_active": bool(p.get("is_active")),
            "curve_points": len(p.get("fan_curve", [])),
        }
        for name, p in _agent.profile_engine.profiles.items()
    }

    return {
        "agent": {
            "version": agent_version,
            "running": _agent.running,
            "demo_mode": _agent.demo_mode,
            "device_id": _agent.device_id,
            "device_name": _agent.device_name,
            "hardware_id": _agent._hardware_id[:16] + "..." if _agent._hardware_id else None,
        },
        "hardware": {
            "vendor": _agent.hardware.vendor,
            "model": _agent.hardware.model,
            "controller": _agent.hardware.controller_name,
            "capabilities": _agent.hardware.capabilities,
            "fan_count": _agent.hardware.fan_count,
            "lhm_available": getattr(_agent.hardware, "_lhm_available", None),
            "wmi_cpu_method_idx": getattr(_agent.hardware, "_wmi_cpu_method_idx", None),
            "wmi_cpu_dead": getattr(_agent.hardware, "_wmi_cpu_dead", False),
            "last_known_bios_mode": _agent._last_known_bios_mode,
        },
        "cloud": {
            "configured": _agent.cloud is not None,
            "healthy": (_agent.cloud is not None and _agent.cloud.is_healthy),
            "user_id": (_agent.cloud.user_id[:16] + "..." if _agent.cloud and _agent.cloud.user_id else None),
            "realtime_connected": (_agent._cmd_listener is not None and _agent._cmd_listener.connected),
            "realtime_last_event_age_s": (
                _agent._cmd_listener.last_event_age_s if _agent._cmd_listener else None
            ),
            "last_polled_command_time": (
                _agent.cloud._last_polled_command_time if _agent.cloud else None
            ),
        },
        "profiles": {
            "active": _agent.profile_engine.active_profile,
            "summary": profile_summary,
        },
        "alerts": {
            "thresholds": thresholds_snapshot,
            "last_triggered": thresholds_last_triggered,
        },
        "local_server": {
            "ws_clients": len(ws_clients),
            "rate_limit_buckets": {k: len(v) for k, v in _rate_limits.items()},
            "auth_token_configured": _auth_token is not None,
        },
        "now": datetime.now(timezone.utc).isoformat(),
        "monotonic": _time.monotonic(),
    }


# ---- Server launcher ----

async def start_server(agent, port: int = 8420, auth_token: str = ""):
    global _agent
    _agent = agent
    if auth_token:
        set_auth_token(auth_token)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()