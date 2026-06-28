"""Shared helpers for the v2 validation — STANDALONE (direct hardware).

No agent, no cloud, no app, no pairing. Reads sensors and writes fan PWM
directly through HardwareMonitor (LibreHardwareMonitor), the same backend
the agent uses — exactly like desktop_step_response.py.

Requirements: only requirements-experiment.txt (pythonnet, psutil, numpy,
scipy, pandas, matplotlib). Run every v2 script from an **Administrator**
terminal — LibreHardwareMonitor needs admin to read sensors and write PWM.
"""

import multiprocessing
import sys
import time
from pathlib import Path

# hardware.py lives one level up (Backend/), profiles.py too.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force UTF-8 console output so Romanian text / ° never crashes a cp1252
# Windows console with UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Abort any run if CPU crosses this — keeps us clear of the hardware
# thermal-protection throttle (~95-100 C) that ruined the v1 runs.
SAFE_MAX_C = 90.0


# ---------------------------------------------------------------------------
# Direct hardware access (no agent)
# ---------------------------------------------------------------------------
def make_hw():
    """Construct a HardwareMonitor (loads LibreHardwareMonitor, ~3s, admin).

    Set THERM_DEMO=1 in the environment to use simulated hardware — used to
    dry-run the whole pipeline without a real machine (no admin needed).
    """
    import os
    demo = bool(os.environ.get("THERM_DEMO"))
    from hardware import HardwareMonitor
    hw = HardwareMonitor(force_demo=demo)
    print(f"[hw] controller={getattr(hw, 'controller_name', '?')} "
          f"fan_count={hw.fan_count}{' (DEMO)' if demo else ''}")
    if hw.fan_count == 0 and not demo:
        raise RuntimeError(
            "Niciun ventilator detectat de LibreHardwareMonitor. "
            "Rulezi ca Administrator? Placa expune PWM?"
        )
    return hw


def set_all_fans(hw, pct: float) -> None:
    pct = max(0.0, min(100.0, float(pct)))
    for i in range(hw.fan_count):
        hw.set_fan_speed(i, pct)


def release(hw) -> None:
    """Return all fans to automatic/BIOS control and close LHM.

    MUST be called at the end of every run — otherwise fans stay pinned at
    the last software PWM (they don't auto-revert on a desktop board until
    SetDefault or a reboot). hw.close() resets every fan to default first.
    """
    try:
        hw.close()
        print("[hw] fans returned to automatic (BIOS) control.")
    except Exception as e:
        print(f"[hw] fan release warning: {e} -- reboot to restore fans.",
              file=sys.stderr)


def flat_sensors(hw) -> dict:
    """read_sensors() collapsed to flat columns for CSV logging."""
    s = hw.read_sensors()
    fans = s.get("fan_speeds") or []
    f0 = fans[0] if fans else {}
    f1 = fans[1] if len(fans) > 1 else {}
    return {
        "cpu_temp": s.get("cpu_temp"),
        "cpu_load": s.get("cpu_load"),
        "gpu_temp": s.get("gpu_temp"),
        "fan0_rpm": f0.get("rpm"), "fan0_pct": f0.get("percent"),
        "fan1_rpm": f1.get("rpm"), "fan1_pct": f1.get("percent"),
    }


def _wait_until(deadline: float) -> None:
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            return
        time.sleep(min(rem, 0.05))


def control_loop(hw, duration_s: float, on_tick, period_s: float = 2.0,
                 safety: bool = True) -> None:
    """Every period_s for duration_s, read sensors and call
    on_tick(t_rel, row, dt). Aborts (RuntimeError) if cpu_temp > SAFE_MAX_C
    while safety is True. period_s defaults to 2 s (the agent's interval)."""
    t0 = time.monotonic()
    next_tick = t0
    last_t = None
    while True:
        now = time.monotonic()
        t_rel = now - t0
        if t_rel >= duration_s:
            return
        dt = period_s if last_t is None else (t_rel - last_t)
        last_t = t_rel
        try:
            row = flat_sensors(hw)
            cpu = row["cpu_temp"]
            if safety and cpu is not None and float(cpu) > SAFE_MAX_C:
                raise RuntimeError(
                    f"ABORT: cpu_temp {cpu}C > limita {SAFE_MAX_C}C la "
                    f"t={t_rel:.0f}s. Reduce sarcina (--procs)."
                )
            on_tick(t_rel, row, dt)
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[v2] read/control failed: {e}", file=sys.stderr)
        next_tick += period_s
        _wait_until(next_tick)


# ---------------------------------------------------------------------------
# CPU stress (inlined so we depend on nothing but the stdlib here)
# ---------------------------------------------------------------------------
def _busy_loop():
    while True:
        x = 0
        for _ in range(10_000_000):
            x += 1


class CpuStress:
    """Spawn N busy-loop processes for sustained CPU load."""

    def __init__(self, n_processes=None):
        if n_processes is None:
            n_processes = max(2, multiprocessing.cpu_count() // 4)
        self.n = int(n_processes)
        self._procs = []

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
# Tiny stats + CSV
# ---------------------------------------------------------------------------
def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def write_csv(path: Path, columns, rows) -> None:
    import csv
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
