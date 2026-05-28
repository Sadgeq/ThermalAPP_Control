"""Shared helpers for the empirical-validation scripts.

Used by step_response.py, thermal_test.py, latency_test.py, and the plotting
tools. Kept deliberately small: a thin HTTP client over the local API, a
CSV logger, and a CPU stress generator. Scripts compose these with their
own measurement loops.

Local API contract (binds to 127.0.0.1:8420):
  GET  /api/sensors            -> {cpu_temp, gpu_temp, fan_speeds:[{rpm, percent}], ...}
  GET  /api/fan-mode           -> {mode: 1|2|3|null, supported: bool}
  POST /api/fan-mode           -> {ok, mode}; body {"mode": 1|2|3}
  POST /api/profiles/X/activate-> {ok, active}
  GET  /api/status             -> {active_profile, fan_count, ...}

Localhost is a trusted network boundary for the agent — these scripts hit
the API without a Bearer token, which the agent allows on 127.0.0.1.
"""

import csv
import multiprocessing
import time
from pathlib import Path
from typing import Any, Optional

import requests

API_BASE = "http://127.0.0.1:8420"
HTTP_TIMEOUT = 4.0  # seconds; sensor reads should be fast


# ---------------------------------------------------------------------------
# Local API client
# ---------------------------------------------------------------------------
def get_sensors() -> dict[str, Any]:
    """Return the latest sensor snapshot from the running agent.

    The agent caches the most recent monitoring-loop reading and serves it
    here, so there's no extra hardware probe per call — safe to poll at
    1 Hz from a script.
    """
    r = requests.get(f"{API_BASE}/api/sensors", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_fan_mode() -> Optional[int]:
    r = requests.get(f"{API_BASE}/api/fan-mode", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json().get("mode")


def set_fan_mode(mode: int) -> bool:
    """Set BIOS fan mode 1=Quiet, 2=Balanced, 3=Performance."""
    r = requests.post(
        f"{API_BASE}/api/fan-mode", json={"mode": int(mode)}, timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()
    return bool(r.json().get("ok"))


def activate_profile(name: str) -> bool:
    r = requests.post(
        f"{API_BASE}/api/profiles/{name}/activate", timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()
    return bool(r.json().get("ok"))


def set_profile_target_temp(name: str, target: Optional[float]) -> dict:
    """Set or clear a profile's PID setpoint via the local API.

    None clears target_temp (curve mode). A float in [40, 95] enables
    PID mode at that setpoint. The agent updates both its in-memory
    cache and the cloud DB, then re-creates the PID controller if this
    profile is active.
    """
    body: dict = {"target_temp": None if target is None else float(target)}
    r = requests.post(
        f"{API_BASE}/api/profiles/{name}/target-temp",
        json=body, timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def get_status() -> dict[str, Any]:
    r = requests.get(f"{API_BASE}/api/status", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Stress generator
# ---------------------------------------------------------------------------
def _busy_loop():
    while True:
        x = 0
        for _ in range(10_000_000):
            x += 1


class CpuStress:
    """Spawn N busy-loop processes for sustained CPU load.

    Default is a QUARTER of logical CPUs (clamped to >=2). Why: on a
    Lenovo Legion 5 with i5-10500H, even half the cores under sustained
    Python busy-loop saturates the cooling envelope — curve mode
    plateaus at ~88°C and PID setpoints below that become unreachable
    (the actuator runs at FanMode 3 forever and can't bring temp down).
    A quarter of cores gives ~70-78°C steady state, where typical PID
    setpoints in the 70-85°C range are inside the reachable envelope
    and PID actually has something to do.
    """

    def __init__(self, n_processes: Optional[int] = None):
        if n_processes is None:
            n_processes = max(2, multiprocessing.cpu_count() // 4)
        self.n = int(n_processes)
        self._procs: list[multiprocessing.Process] = []

    def start(self):
        for _ in range(self.n):
            p = multiprocessing.Process(target=_busy_loop, daemon=True)
            p.start()
            self._procs.append(p)

    def stop(self):
        for p in self._procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in self._procs:
            try:
                p.join(timeout=2.0)
            except Exception:
                pass
        self._procs.clear()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


# ---------------------------------------------------------------------------
# CSV logger
# ---------------------------------------------------------------------------
class CsvLogger:
    """Append rows to a CSV file with an explicit header.

    Time column is wall-clock-relative-to-start ('t_s') so plots from
    different runs align on the same x-axis without epoch arithmetic.
    """

    def __init__(self, path: Path, columns: list[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.columns = columns
        self._fh = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=columns)
        self._writer.writeheader()

    def write(self, row: dict[str, Any]) -> None:
        clean = {k: row.get(k, "") for k in self.columns}
        self._writer.writerow(clean)
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------
def extract_metrics(sensors: dict, t_s: float, **extra) -> dict:
    """Collapse a sensors payload into a flat row for CSV logging.

    Picks fan 0 for the per-fan columns; thermal experiments rarely care
    about fan-vs-fan deltas on this hardware (both fans track each other
    closely on Legion). plot_results.py reads these columns directly.
    """
    fans = sensors.get("fan_speeds") or []
    fan0 = fans[0] if fans else {}
    fan1 = fans[1] if len(fans) > 1 else {}
    return {
        "t_s": round(t_s, 2),
        "cpu_temp": sensors.get("cpu_temp"),
        "cpu_load": sensors.get("cpu_load"),
        "gpu_temp": sensors.get("gpu_temp"),
        "fan0_rpm": fan0.get("rpm"),
        "fan0_pct": fan0.get("percent"),
        "fan1_rpm": fan1.get("rpm"),
        "fan1_pct": fan1.get("percent"),
        **extra,
    }


def wait_until(deadline: float) -> None:
    """Sleep in short chunks until monotonic >= deadline.

    Avoids both the long-sleep / Ctrl-C latency tradeoff and the busy-wait
    CPU cost. 50ms granularity is fine for 1 Hz sampling.
    """
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            return
        time.sleep(min(rem, 0.05))
