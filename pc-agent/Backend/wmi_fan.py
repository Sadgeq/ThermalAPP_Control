"""
WMI fan controller for Legion laptops that expose LENOVO_GAMEZONE_DATA.

This is the *only* path to functional fan RPM reads + fan-mode writes on
Legion 5 15IMH6 (82NL). The legacy EC-register path (0xC8/0xC9 for RPM,
0xC3/0xCD for duty) returns garbage on this model — those cells aren't
wired to the physical fan controller.

Confirmed capabilities on 82NL BIOS:
    - Read fan RPM:         GetFan1Speed, GetFan2Speed      ✓
    - Read max RPM:         GetFanMaxSpeed                  ✓
    - Read current mode:    GetSmartFanMode                 ✓
    - Set mode:             SetSmartFanMode(1|2|3)          ✓
    - Custom curve / arbitrary RPM:                         ✗ (not exposed)

Modes: 1 = Quiet, 2 = Balanced, 3 = Performance. Effects manifest under
load — at idle all modes produce similar RPM.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("WmiFan")

# COM is Single-Threaded Apartment. The agent's monitoring loop dispatches
# sensor reads via loop.run_in_executor(None, ...) which uses a thread pool;
# each worker thread must initialize COM and hold its own WMI connection.
# Cross-thread use of a COM proxy created on another thread either stalls
# (waiting for marshalling) or silently fails.
_tls = threading.local()

VALID_MODES = (1, 2, 3)
MODE_NAMES = {1: "Quiet", 2: "Balanced", 3: "Performance"}


def _unwrap(v):
    """WMI methods often return (x,) single-tuples. Return bare value."""
    if isinstance(v, tuple) and len(v) == 1:
        return v[0]
    return v


def _get_gz():
    """Return LENOVO_GAMEZONE_DATA instance for the current thread.
    Initializes COM + WMI lazily per thread. Returns None if unavailable."""
    cached = getattr(_tls, "gz", _SENTINEL)
    if cached is not _SENTINEL:
        return cached  # may be None (already-known-missing)
    gz = _init_for_current_thread()
    _tls.gz = gz
    return gz


_SENTINEL = object()


def _init_for_current_thread():
    try:
        import pythoncom
    except ImportError:
        logger.warning("pythoncom not installed (pip install pywin32)")
        return None
    try:
        pythoncom.CoInitialize()
    except Exception:
        # Already initialized on this thread — fine
        pass
    try:
        import wmi
    except ImportError:
        logger.warning("wmi package not installed")
        return None
    try:
        c = wmi.WMI(namespace="root\\WMI")
        instances = c.LENOVO_GAMEZONE_DATA()
        if not instances:
            return None
        return instances[0]
    except Exception as e:
        logger.debug(f"WMI init (thread={threading.current_thread().name}): {e}")
        return None


class WmiFanController:
    """Thread-safe WMI fan controller. Each thread gets its own COM-initialized
    WMI connection via thread-local storage. Safe to call from the agent's
    executor-dispatched sensor-read path."""

    def __init__(self):
        self._available = False

    def initialize(self) -> bool:
        # Probe on the init-caller's thread. Subsequent reads from other
        # threads will lazily re-init on their own threads.
        gz = _get_gz()
        if gz is None:
            logger.info("LENOVO_GAMEZONE_DATA not present or WMI init failed")
            return False
        self._available = True
        logger.info("WMI fan controller ready (LENOVO_GAMEZONE_DATA)")
        return True

    @property
    def available(self) -> bool:
        return self._available

    # --- Reads -----------------------------------------------------------

    def read_fan_rpm(self, fan_idx: int) -> int | None:
        if not self._available:
            return None
        gz = _get_gz()
        if gz is None:
            return None
        try:
            if fan_idx == 0:
                return int(_unwrap(gz.GetFan1Speed()))
            if fan_idx == 1:
                return int(_unwrap(gz.GetFan2Speed()))
            return None
        except Exception as e:
            logger.debug(f"WMI read fan{fan_idx} failed: {e}")
            return None

    def read_max_rpm(self) -> int | None:
        if not self._available:
            return None
        gz = _get_gz()
        if gz is None:
            return None
        try:
            return int(_unwrap(gz.GetFanMaxSpeed()))
        except Exception:
            return None

    def read_fan_count(self) -> int | None:
        if not self._available:
            return None
        gz = _get_gz()
        if gz is None:
            return None
        try:
            return int(_unwrap(gz.GetFanCount()))
        except Exception:
            return None

    def get_mode(self) -> int | None:
        if not self._available:
            return None
        gz = _get_gz()
        if gz is None:
            return None
        try:
            return int(_unwrap(gz.GetSmartFanMode()))
        except Exception as e:
            logger.debug(f"GetSmartFanMode failed: {e}")
            return None

    # --- Writes ----------------------------------------------------------

    def set_mode(self, mode: int) -> bool:
        """Set fan-control policy. mode ∈ {1, 2, 3}. Returns True on
        success verified by readback."""
        if not self._available:
            return False
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid mode {mode}. Valid: {VALID_MODES} "
                f"({', '.join(f'{k}={v}' for k, v in MODE_NAMES.items())})"
            )
        gz = _get_gz()
        if gz is None:
            return False
        try:
            gz.SetSmartFanMode(mode)
        except Exception as e:
            logger.error(f"SetSmartFanMode({mode}) failed: {e}")
            return False
        readback = self.get_mode()
        if readback != mode:
            logger.error(
                f"SetSmartFanMode({mode}) — BIOS coerced to {readback}"
            )
            return False
        logger.info(f"SmartFanMode set to {mode} ({MODE_NAMES.get(mode)})")
        return True
