"""Pre-flight diagnostic for the desktop thermal experiment.

Runs in ~30 seconds. Verifies everything the real experiment depends on:

  * Python version is compatible
  * Process is running with admin privileges (LHM requires this)
  * LibreHardwareMonitor DLL loads and reports the expected chips
  * CPU temperature is readable
  * At least one fan with PWM Control is detected
  * Software PWM write actually changes fan RPM (proves BIOS isn't
    silently ignoring us)

Run this FIRST. If it reports "all green", desktop_step_response.py
will work without surprises. If it flags an issue, fix that before
running the full experiment.

Self-contained — only depends on hardware.py + LHM DLLs in lib/.
"""

import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def is_admin() -> bool:
    """True if process has Windows admin privileges."""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def color(text: str, ok: bool) -> str:
    """Simple PASS/FAIL marker without ANSI codes (Windows cmd compat)."""
    return f"[{'PASS' if ok else 'FAIL'}] {text}"


def main():
    print("=" * 70)
    print("PREFLIGHT - desktop thermal experiment readiness check")
    print("=" * 70)

    all_ok = True

    # --- 1. Python version ---
    py_ok = sys.version_info >= (3, 9)
    print(color(
        f"Python {sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro} (need >= 3.9)", py_ok
    ))
    if not py_ok:
        all_ok = False

    # --- 2. Admin ---
    admin = is_admin()
    print(color(f"Running as Administrator: {admin}", admin))
    if not admin:
        print("       LibreHardwareMonitor needs admin to access sensors.")
        print("       Re-open Powershell with 'Run as Administrator'.")
        all_ok = False

    # --- 3. Python deps importable ---
    try:
        import psutil  # noqa: F401
        import clr  # noqa: F401  (pythonnet)
        print(color("pythonnet + psutil importable", True))
    except ImportError as e:
        print(color(f"missing Python dep: {e}", False))
        print("       Run: pip install -r requirements-experiment.txt")
        all_ok = False
        return _finish(all_ok)

    # --- 4. HardwareMonitor loads ---
    try:
        from hardware import HardwareMonitor
        print("[ ... ] starting HardwareMonitor (loads .NET DLL, ~3s)...")
        hw = HardwareMonitor()
    except Exception as e:
        print(color(f"HardwareMonitor failed to init: {e}", False))
        return _finish(False)
    print(color(f"HardwareMonitor loaded: vendor={hw.vendor!r}, "
                f"model={hw.model!r}", True))

    # --- 5. Controller type ---
    ctrl = hw.controller_name
    is_desktop = ctrl == "lhm-pwm"
    print(color(f"Controller detected: '{ctrl}' "
                f"({'continuous PWM' if is_desktop else 'discrete or unsupported'})",
                is_desktop))
    if not is_desktop:
        if ctrl == "lenovo-legion-wmi":
            print("       Lenovo Legion detected - use step_response_load.py "
                  "instead.")
        elif ctrl == "sensors-only":
            print("       LHM didn't find a PWM controller. Possible causes:")
            print("       - Not running as admin")
            print("       - BIOS hides the Super-I/O chip")
            print("       - Unsupported motherboard")
        all_ok = False

    # --- 6. CPU temperature readable ---
    try:
        sensors = hw.read_sensors()
        cpu_temp = sensors.get("cpu_temp")
        if cpu_temp is None or cpu_temp < 10 or cpu_temp > 110:
            print(color(f"CPU temp unreadable: {cpu_temp}", False))
            all_ok = False
        else:
            print(color(f"CPU temperature: {cpu_temp} C", True))
    except Exception as e:
        print(color(f"sensor read failed: {e}", False))
        all_ok = False
        return _finish(all_ok)

    # --- 7. Fans detected ---
    fans = sensors.get("fan_speeds", []) or []
    has_fans = len(fans) > 0
    print(color(f"Fans detected by LHM: {len(fans)}", has_fans))
    for i, f in enumerate(fans):
        rpm = f.get("rpm", "n/a")
        pct = f.get("percent", "n/a")
        print(f"       fan{i}: rpm={rpm}, pct={pct}")
    if not has_fans:
        all_ok = False
        return _finish(all_ok)

    # --- 8. PWM controllers ---
    has_pwm = hw.has_pwm_control
    n_ctrl = len(hw._fan_controllers)
    print(color(f"PWM controllers writable: {n_ctrl}", has_pwm))
    if not has_pwm:
        print("       No fans accept software PWM. BIOS Q-Fan settings "
              "likely need PWM Manual mode.")
        all_ok = False
        return _finish(all_ok)

    # --- 9. Live PWM write test ---
    # Pick the fastest-spinning fan as the CPU fan candidate.
    rpms = [(i, f.get("rpm") or 0) for i, f in enumerate(fans)]
    rpms.sort(key=lambda x: -x[1])
    cpu_fan_idx = rpms[0][0]
    idle_rpm = rpms[0][1]
    print(f"[ ... ] testing PWM write on fan{cpu_fan_idx} "
          f"(idle RPM = {idle_rpm})...")

    if cpu_fan_idx >= n_ctrl:
        print(color(f"fan{cpu_fan_idx} has no PWM controller available", False))
        all_ok = False
        return _finish(all_ok)

    # Try writing 30% PWM, then 70%, see if RPM changes.
    try:
        hw.set_fan_speed(cpu_fan_idx, 30.0)
        time.sleep(5)
        s_low = hw.read_sensors()
        rpm_low = (s_low.get("fan_speeds") or [{}])[cpu_fan_idx].get("rpm") or 0

        hw.set_fan_speed(cpu_fan_idx, 70.0)
        time.sleep(5)
        s_high = hw.read_sensors()
        rpm_high = (s_high.get("fan_speeds") or [{}])[cpu_fan_idx].get("rpm") or 0

        # Restore to BIOS auto
        try:
            hw._fan_controllers[cpu_fan_idx].Control.SetDefault()
        except Exception:
            pass

        diff = rpm_high - rpm_low
        print(f"       PWM 30% -> {rpm_low} RPM; PWM 70% -> {rpm_high} RPM "
              f"(delta = {diff})")
        # On a clean board, going from 30% to 70% should change RPM by
        # at least a few hundred. If diff < 100, BIOS is ignoring us.
        pwm_works = diff > 150
        print(color(f"Software PWM write changes fan RPM (BIOS not "
                    f"overriding): {pwm_works}", pwm_works))
        if not pwm_works:
            print("       BIOS is intercepting software PWM writes.")
            print("       Check BIOS: CPU Fan Mode = PWM, Profile = Manual.")
            all_ok = False
    except Exception as e:
        print(color(f"PWM write test failed: {e}", False))
        all_ok = False

    return _finish(all_ok)


def _finish(all_ok: bool):
    print("\n" + "=" * 70)
    if all_ok:
        print("RESULT: ALL CHECKS PASSED")
        print("Ready to run: python Backend\\scripts\\desktop_step_response.py")
    else:
        print("RESULT: PREFLIGHT FAILED")
        print("Fix the [FAIL] items above before running the real experiment.")
    print("=" * 70)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
