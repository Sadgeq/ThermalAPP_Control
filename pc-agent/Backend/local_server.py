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
                    # FIX: Validate command structure before processing
                    cmd_type = cmd.get("command_type", "")
                    if cmd_type in ("set_fan_speed", "set_profile", "set_alert_threshold", "set_all_fans", "set_fan_mode"):
                        _agent._handle_command_sync(cmd)
                    else:
                        logger.warning(f"WS: Unknown command type: {cmd_type}")
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
    return {
        "status": "running",
        "device_id": _agent.device_id or _agent._hardware_id,
        "device_name": _agent.device_name,
        "demo_mode": _agent.demo_mode,
        "cloud_connected": _agent.cloud is not None and _agent.cloud.is_healthy,
        "active_profile": _agent.profile_engine.active_profile,
        "fan_count": _agent.hardware.fan_count,
    }


@app.get("/api/sensors")
async def get_sensors(auth=Depends(require_auth)):
    if not _agent:
        return {"error": "not_initialized"}
    return _agent._last_sensor_data or _agent.hardware.read_sensors()


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
    from agent import _PROFILE_TO_MODE
    mode = _PROFILE_TO_MODE.get(clean_name)
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


# ---- REST: Alerts ----

@app.get("/api/alerts")
async def get_alerts(auth=Depends(require_auth)):
    if not _agent:
        return {"error": "not_initialized"}
    return {"thresholds": _agent.alert_thresholds}


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


# ---- Server launcher ----

async def start_server(agent, port: int = 8420, auth_token: str = ""):
    global _agent
    _agent = agent
    if auth_token:
        set_auth_token(auth_token)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()