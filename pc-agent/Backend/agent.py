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

# Agent version. Stamped on heartbeats and surfaced in /api/status so support
# can answer 'which build is this user on?' from the cloud or the desktop UI.
# Bump on every release; the Tauri side and the mobile app each track their
# own versions in app.json / tauri.conf.json.
__version__ = "3.2.0"

CONFIG = {
    "supabase_url": os.getenv("SUPABASE_URL") or _DEFAULT_SUPABASE_URL,
    "supabase_key": os.getenv("SUPABASE_ANON_KEY") or _DEFAULT_SUPABASE_ANON_KEY,
    "device_name": os.getenv("DEVICE_NAME", "My PC"),
    "polling_interval": max(1.0, min(60.0, float(os.getenv("POLLING_INTERVAL", "2")))),
    "heartbeat_interval": 30,
    "cloud_enabled": True,
    "local_port": int(os.getenv("LOCAL_PORT", "8420")),
    # Auth handoff from a parent process (specifically the Tauri sidecar
    # spawn — see pc-agent/desktop/src-tauri/src/sidecar.rs). When set, the
    # agent skips the keyring-backed pairing flow entirely and uses these
    # tokens to bind itself to whichever Supabase user the desktop UI is
    # signed in as. We removed this path in an earlier session because
    # it was a footgun when arbitrary processes could set it; bringing it
    # back now that the sidecar is the only legitimate source.
    "tauri_access_token": os.getenv("TAURI_ACCESS_TOKEN", ""),
    "tauri_refresh_token": os.getenv("TAURI_REFRESH_TOKEN", ""),
}

# Allow disabling cloud if no credentials
if not CONFIG["supabase_url"] or not CONFIG["supabase_key"]:
    CONFIG["cloud_enabled"] = False

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# RotatingFileHandler caps total disk usage at maxBytes * (backupCount + 1).
# 5MB × 4 = 20MB, more than enough to survive any realistic crash window
# while staying small enough that nobody notices it on disk. Without
# rotation, a long-running agent (weeks) would fill GBs of log over time.
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            LOG_DIR / "agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("Agent")


def _clamp(v, min_v, max_v) -> float:
    """Safely clamp a numeric value."""
    try:
        return max(min_v, min(max_v, float(v)))
    except (TypeError, ValueError):
        return min_v


# Each profile carries its own fan_mode (1=Quiet, 2=Balanced, 3=Performance)
# in the database — see migration 0007_profiles_fan_mode.sql. Renaming a
# profile no longer breaks the BIOS-mode flip; the mode travels with the row.


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
        # Last BIOS fan-mode we observed. Used to detect external mode
        # changes (Fn+Q hotkey, Lenovo Vantage, another tool poking WMI)
        # so we can reflect them back into profile is_active state. None
        # means "not observed yet" — the first observation is treated as
        # a baseline, not a change.
        self._last_known_bios_mode: int | None = None
        self.alert_thresholds: dict = {}
        # Lock guarding alert_thresholds. Three writers can touch it
        # concurrently: realtime-listener thread (set_alert_threshold
        # commands), uvicorn thread pool (REST /api/alerts/threshold), and
        # the monitoring loop (writes _check_alerts() last_triggered after
        # a fire). Without the lock, dict mutation during _check_alerts'
        # iteration can raise RuntimeError and stop the monitoring loop.
        self._alert_thresholds_lock = threading.Lock()
        self._cmd_listener = None  # Realtime listener for sub-second commands

        # PID -> FanMode quantizer state. On hardware without per-fan PWM
        # control (Lenovo Legion EC, most consumer laptops), the PID's
        # continuous output is mapped to the 3-state BIOS FanMode. We keep
        # the last applied mode and the wall-clock time we applied it so
        # that hysteresis bands and a minimum dwell time prevent the
        # Y-key LED from flapping when PWM hovers on a band edge.
        self._pwm_quant_mode: int | None = None
        self._pwm_quant_last_apply: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self):
        """Initialize cloud connection, load profiles, start loops."""
        logger.info("=" * 50)
        logger.info(f"ThermalControl Agent v{__version__} starting...")
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

                # Auth resolution:
                #   1. Tauri sidecar handoff (TAURI_ACCESS_TOKEN env). When
                #      the bundled desktop UI launches the agent, the React
                #      app's Google sign-in produces tokens that Tauri's
                #      Rust shell passes to us via env. Skips the PIN dance
                #      entirely for desktop-first users.
                #   2. Stored pairing credential (OS keyring). Resumed
                #      automatically across reboots once paired.
                #   3. Interactive PIN pairing (first-run, no Tauri).
                # Whichever path runs, the agent ends up bound to one
                # Supabase user — the keyring-stored credential is the
                # source of truth for subsequent runs.
                tauri_access = CONFIG["tauri_access_token"]
                tauri_refresh = CONFIG["tauri_refresh_token"]
                if tauri_access and tauri_refresh:
                    self.cloud.set_session(tauri_access, tauri_refresh)
                    self.device_id = self.cloud.register_device(
                        hardware_id=self._hardware_id,
                        name=self.device_name,
                        os_info=self._get_os_info(),
                        controller=self.hardware.controller_name,
                    )
                    # Persist the credential to keyring so subsequent runs
                    # without env tokens (e.g. user starts agent manually
                    # for debugging) still resume cleanly.
                    try:
                        from pairing import StoredDevice, save_stored
                        save_stored(CONFIG["supabase_url"], StoredDevice(
                            device_id=self.device_id,
                            user_id=self.cloud.user_id or "",
                            refresh_token=tauri_refresh,
                            access_token="",
                        ))
                    except Exception as e:
                        logger.debug(f"Could not persist Tauri-handoff creds: {e}")
                    logger.info(f"Authenticated via Tauri sidecar handoff (device {self.device_id[:8]}…)")
                else:
                    from pairing import authenticate_or_pair
                    sd = authenticate_or_pair(
                        cloud_client=self.cloud,
                        supabase_url=CONFIG["supabase_url"],
                        hardware_id=self._hardware_id,
                        device_name=self.device_name,
                        os_info=self._get_os_info(),
                        interactive=sys.stdin.isatty(),
                        controller=self.hardware.controller_name,
                    )
                    self.device_id = sd.device_id
                    logger.info(f"Authenticated via interactive sign-in (device {sd.device_id[:8]}…)")
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
                    mode = (
                        self.profile_engine.profiles.get(active, {}).get("fan_mode")
                        if active else None
                    )
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
                #
                # We pass the cloud client's get_access_token method directly
                # rather than a static token string. The listener calls it
                # before every reconnect, and get_access_token refreshes the
                # underlying Supabase session if the JWT is near expiry — so
                # an agent that's been up for hours still subscribes with a
                # live token and doesn't silently fall back to polling.
                if self.cloud.get_access_token():
                    try:
                        from realtime_listener import CommandListener
                        self._cmd_listener = CommandListener(
                            url=CONFIG["supabase_url"],
                            anon_key=CONFIG["supabase_key"],
                            token_provider=self.cloud.get_access_token,
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

                # 1.5. Reconcile against external BIOS fan-mode changes.
                # If the user pressed Fn+Q or used Lenovo Vantage, the BIOS
                # mode flipped without going through us — and the active
                # profile's is_active flag in cloud is now stale. Detect the
                # change here and propagate: pick a profile whose fan_mode
                # matches the observed BIOS state and activate it. That
                # cascades through cloud → mobile + Profiles tab.
                await self._reconcile_external_fan_mode(loop)

                # 2. Apply active profile fan curve OR PID setpoint.
                # The profile engine dispatches based on whether the
                # active profile has a target_temp set (PID mode) or
                # only a fan_curve (legacy mode). dt is the polling
                # interval — needed by the PID for the I and D terms.
                #
                # Note `is not None` (not `or`): cpu_temp at idle can be 0
                # on some sensors, and `0 or gpu_temp` would skip a valid
                # CPU read. Treat any numeric value as valid.
                if self.profile_engine.active_profile and self.hardware.fan_count > 0:
                    cpu_t = data.get("cpu_temp")
                    gpu_t = data.get("gpu_temp")
                    control_temp = cpu_t if cpu_t is not None else gpu_t
                    if control_temp is not None:
                        target = self.profile_engine.calculate_fan_speeds(
                            float(control_temp),
                            self.hardware.fan_count,
                            dt=float(CONFIG["polling_interval"]),
                        )
                        # On hardware with continuous PWM (LHM PWM
                        # controllers detected) we drive each fan
                        # individually. On Lenovo Legion (no per-fan PWM)
                        # we quantize the PID output to FanMode 1/2/3
                        # with hysteresis and a minimum dwell time —
                        # otherwise the Y-key LED flaps and the BIOS
                        # rejects rapid mode switches.
                        if self.hardware.has_pwm_control:
                            for idx, speed in target.items():
                                self.hardware.set_fan_speed(idx, speed)
                        elif target:
                            self._apply_pwm_as_fan_mode(
                                pwm=target.get(0, 0.0),
                                is_pid_mode=(
                                    self.profile_engine.get_active_profile_data()
                                    or {}
                                ).get("target_temp") is not None,
                            )
                        data["fan_speeds"] = self.hardware.read_fan_speeds()
                    else:
                        # Throttled warning: if neither sensor produced a
                        # reading, the profile engine never runs and the
                        # PID can't act. Surfacing this once every ~30
                        # ticks (≈ 90s) tells the user why nothing moves.
                        self._no_temp_tick = getattr(self, "_no_temp_tick", 0) + 1
                        if self._no_temp_tick % 30 == 1:
                            logger.warning(
                                "No CPU/GPU temperature available; profile engine "
                                "skipped (cpu=%r gpu=%r)", cpu_t, gpu_t
                            )
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
                                None, lambda: self.cloud.heartbeat(
                                    self.device_id,
                                    controller=self.hardware.controller_name,
                                    app_version=__version__,
                                )
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
    # PID -> FanMode quantizer
    # ------------------------------------------------------------------
    # PWM bands with hysteresis. Outer thresholds force a transition;
    # inner thresholds let the current mode hold. Result: Quiet from
    # 0-29% PWM, Balanced from 30-69%, Performance from 70-100%, with a
    # ±5% deadband around each boundary so a PWM hovering on the edge
    # doesn't flap the Y-key LED.
    _PWM_QUIET_TO_BALANCED = 35.0   # 30 + 5 deadband
    _PWM_BALANCED_TO_QUIET = 25.0   # 30 - 5
    _PWM_BALANCED_TO_PERF  = 75.0   # 70 + 5
    _PWM_PERF_TO_BALANCED  = 65.0   # 70 - 5
    _PWM_MIN_DWELL_S = 5.0          # hold each mode at least 5s

    def _apply_pwm_as_fan_mode(self, pwm: float, is_pid_mode: bool) -> None:
        """Quantize a PID PWM percent into FanMode 1/2/3 and apply via WMI.

        Only meaningful in PID mode. In curve mode the active profile's
        fan_mode was already applied at activation time; we don't want
        to override it from a curve PWM.

        Transitions are gated by hysteresis on the destination side: to
        leave a band you must cross deeply into the next one. Skip-band
        jumps (1->3, 3->1) are allowed when PWM is in the far band — we
        don't want to crawl through Balanced for 5s when the CPU is
        already at TjMax and PID is pegged at 100%.
        """
        if not is_pid_mode:
            return

        prev = self._pwm_quant_mode
        if prev == 1:
            if pwm > self._PWM_BALANCED_TO_PERF:
                target_mode = 3
            elif pwm > self._PWM_QUIET_TO_BALANCED:
                target_mode = 2
            else:
                target_mode = 1
        elif prev == 3:
            if pwm < self._PWM_BALANCED_TO_QUIET:
                target_mode = 1
            elif pwm < self._PWM_PERF_TO_BALANCED:
                target_mode = 2
            else:
                target_mode = 3
        elif prev == 2:
            if pwm < self._PWM_BALANCED_TO_QUIET:
                target_mode = 1
            elif pwm > self._PWM_BALANCED_TO_PERF:
                target_mode = 3
            else:
                target_mode = 2
        else:
            # No prior state — pick by raw PWM bands with no hysteresis.
            if pwm < 30.0:
                target_mode = 1
            elif pwm < 70.0:
                target_mode = 2
            else:
                target_mode = 3

        if target_mode == prev:
            return

        now = time.monotonic()
        if prev is not None and now - self._pwm_quant_last_apply < self._PWM_MIN_DWELL_S:
            return

        try:
            ok = self.hardware.set_fan_mode(target_mode)
        except Exception as e:
            logger.debug(f"PID quantizer set_fan_mode({target_mode}) failed: {e}")
            return
        if ok:
            self._pwm_quant_mode = target_mode
            self._pwm_quant_last_apply = now
            logger.info(
                f"PID->FanMode: pwm={pwm:.1f}% -> mode {target_mode} "
                f"({'Quiet' if target_mode == 1 else 'Balanced' if target_mode == 2 else 'Performance'})"
            )

    # ------------------------------------------------------------------
    # External-mode reconciliation
    # ------------------------------------------------------------------
    async def _reconcile_external_fan_mode(self, loop) -> None:
        """Detect Fn+Q / Vantage / external BIOS mode changes and sync.

        The agent applies fan-mode flips when the user activates a profile
        through any of our UIs (mobile, desktop, local API). But on Lenovo
        Legion the user can also press Fn+Q to cycle through Quiet /
        Balanced / Performance directly, and other vendor utilities can
        poke the same WMI surface. Those changes don't go through us, so
        without this reconciliation the cloud's `profiles.is_active` flag
        and every UI that reads it (mobile, desktop Profiles tab) shows
        whatever was last set by the agent — stale.

        Strategy: every monitoring tick, read the current BIOS mode and
        compare against the active profile's fan_mode. If they differ,
        find a profile whose fan_mode matches the new BIOS state and
        activate it locally + in the cloud. The cloud update fans out to
        mobile via realtime within a couple of seconds.
        """
        try:
            current_mode = await loop.run_in_executor(None, self.hardware.get_fan_mode)
        except Exception as e:
            logger.debug(f"get_fan_mode failed during reconcile: {e}")
            return

        if current_mode is None:
            # No Legion controller, demo hardware, or transient WMI failure.
            # Either way, nothing to reconcile against — bail.
            return

        if current_mode == self._last_known_bios_mode:
            # Same as last loop — no event to process. (Internal flips also
            # land here, because we call set_fan_mode then observe the same
            # value next tick and update _last_known_bios_mode below.)
            return

        # If the PID->FanMode quantizer caused this transition, treat it
        # as internal: the agent set the mode itself on the previous tick.
        # Without this guard, the reconciler thinks the user pressed Fn+Q
        # every time PID crosses a quantizer band and re-activates a
        # different profile, which then changes the PID setpoint, which
        # the next PWM sample crosses a different band... a self-induced
        # oscillation between Quiet/Balanced/Performance profiles.
        if (
            self._pwm_quant_mode is not None
            and current_mode == self._pwm_quant_mode
        ):
            self._last_known_bios_mode = current_mode
            return

        # Mode changed since last observation. Was it our doing or external?
        active = self.profile_engine.active_profile
        active_profile_mode = (
            self.profile_engine.profiles.get(active, {}).get("fan_mode")
            if active else None
        )

        if current_mode != active_profile_mode:
            # External: BIOS mode doesn't match what the active profile
            # would imply. Find a profile to take over.
            match = next(
                (name for name, p in self.profile_engine.profiles.items()
                 if p.get("fan_mode") == current_mode),
                None
            )
            if match and match != active:
                logger.info(
                    f"External fan-mode change detected (mode {current_mode}); "
                    f"activating profile '{match}' (was '{active}')"
                )
                self.profile_engine.set_active(match)
                if self.cloud and self.device_id:
                    try:
                        await loop.run_in_executor(
                            None,
                            lambda: self.cloud.set_profile_active(self.device_id, match),
                        )
                    except Exception as e:
                        logger.debug(f"Cloud sync of external mode change failed: {e}")
            elif not match:
                logger.debug(
                    f"External fan-mode change to {current_mode}, but no profile "
                    f"has matching fan_mode — leaving active='{active}' alone"
                )

        self._last_known_bios_mode = current_mode

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

                # Re-fetch profiles from cloud before activation so any
                # edits made in the mobile UI (target_temp, fan_curve,
                # fan_mode) take effect immediately. Without this, the
                # in-memory profile cache from boot is stale and the user
                # sees "I changed PID target to 55 but agent still uses 70."
                if self.cloud and self.device_id:
                    try:
                        fresh = self.cloud.get_profiles(self.device_id)
                        if fresh:
                            self.profile_engine.load_profiles(fresh)
                    except Exception as e:
                        logger.debug(f"Profile refetch on set_profile failed: {e}")

                if name not in self.profile_engine.profiles:
                    raise ValueError(f"Profile not found: {name}")
                # load_profiles() already activated whichever profile the
                # cloud row says is_active. Only re-activate if the
                # requested name differs — avoids a redundant
                # "Profile activated:" log line and an extra PID reset
                # when the user is just nudging target_temp on the
                # already-active profile.
                if self.profile_engine.active_profile != name:
                    self.profile_engine.set_active(name)

                # Flip the Lenovo Legion fan mode (this is what changes the
                # Y-key LED color and BIOS thermal policy). Quiet/Balanced/
                # Performance correspond to modes 1/2/3. The mode comes
                # from the profile row itself; profiles without a fan_mode
                # leave the BIOS untouched.
                mode = self.profile_engine.profiles.get(name, {}).get("fan_mode")
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
                with self._alert_thresholds_lock:
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

        # Snapshot under the lock so concurrent set_alert_threshold writes
        # can't mutate the dict while we iterate. We mutate `last_triggered`
        # below; do that under the lock too.
        with self._alert_thresholds_lock:
            snapshot = list(self.alert_thresholds.items())

        for metric, settings in snapshot:
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

            # Fire alert. Update last_triggered through the lock so a write
            # to the same metric doesn't race the cooldown check above.
            with self._alert_thresholds_lock:
                if metric in self.alert_thresholds:
                    self.alert_thresholds[metric]["last_triggered"] = now
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
    # fan_mode: 1=Quiet, 2=Balanced, 3=Performance — flips the Legion BIOS
    # mode (and Y-key LED) when the profile activates. None means "leave
    # the BIOS mode alone" (used for user-created profiles by default).
    _DEFAULT_PROFILES = [
        {"name": "Silent", "fan_mode": 1, "fan_curve": [
            {"temp": 30, "speed": 20}, {"temp": 50, "speed": 30},
            {"temp": 65, "speed": 45}, {"temp": 75, "speed": 60},
            {"temp": 85, "speed": 80}]},
        {"name": "Balanced", "fan_mode": 2, "fan_curve": [
            {"temp": 30, "speed": 30}, {"temp": 50, "speed": 45},
            {"temp": 65, "speed": 60}, {"temp": 75, "speed": 80},
            {"temp": 85, "speed": 100}]},
        {"name": "Gaming", "fan_mode": 3, "fan_curve": [
            {"temp": 30, "speed": 40}, {"temp": 50, "speed": 60},
            {"temp": 65, "speed": 80}, {"temp": 75, "speed": 95},
            {"temp": 85, "speed": 100}]},
        {"name": "Turbo", "fan_mode": 3, "fan_curve": [
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
                fan_mode=p.get("fan_mode"),
            )

    def _create_default_profiles_local(self):
        import uuid
        profiles = []
        for p in self._DEFAULT_PROFILES:
            profiles.append({
                "id": str(uuid.uuid4()),
                "name": p["name"],
                "fan_curve": p["fan_curve"],
                "fan_mode": p.get("fan_mode"),
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

    # Single-instance lock: a second agent on the same machine would fight
    # the first for hardware reads, BIOS mode flips, and the same `devices`
    # row in Supabase (flapping last_seen, conflicting heartbeats). Use the
    # local API port as the lock — uvicorn will fail to bind it later, but
    # checking here gives the user an actionable error instead of a stack
    # trace from deep inside the event loop.
    import socket
    _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        _probe.bind(("127.0.0.1", CONFIG["local_port"]))
    except OSError as e:
        logger.error(
            f"Port {CONFIG['local_port']} is already in use — another agent "
            f"is probably running. Close it before starting a new one. ({e})"
        )
        sys.exit(1)
    finally:
        _probe.close()

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