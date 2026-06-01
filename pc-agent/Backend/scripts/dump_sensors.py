"""Dump every sensor LibreHardwareMonitor enumerates, with current values.

10 seconds, no stress, just lists what LHM sees. Used to diagnose
why CPU temp doesn't track load on a specific machine (typically
WinRing0 / MSR driver not loaded -> only motherboard socket temp
visible, which doesn't change with load).

Run as Administrator from pc-agent/:
    python Backend/scripts/dump_sensors.py
"""

import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def main():
    print("=" * 78)
    print("LHM Sensor Dump")
    print("=" * 78)

    try:
        import ctypes
        admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        admin = False
    print(f"Running as Administrator: {admin}")
    if not admin:
        print("WARNING: not admin. LHM cannot read CPU MSRs without admin.")

    from hardware import HardwareMonitor
    print("Loading HardwareMonitor (LHM .NET DLL, ~3s)...")
    hw = HardwareMonitor()
    print(f"vendor='{hw.vendor}', model='{hw.model}', "
          f"controller='{hw.controller_name}'")
    print()

    # Force fresh sensor reads, just like _lhm_read does each tick.
    from LibreHardwareMonitor.Hardware import SensorType
    type_names = {
        SensorType.Voltage: "Voltage", SensorType.Clock: "Clock",
        SensorType.Temperature: "Temperature", SensorType.Load: "Load",
        SensorType.Fan: "Fan", SensorType.Flow: "Flow",
        SensorType.Control: "Control", SensorType.Level: "Level",
        SensorType.Factor: "Factor", SensorType.Power: "Power",
        SensorType.Data: "Data", SensorType.SmallData: "SmallData",
        SensorType.Throughput: "Throughput",
        SensorType.Frequency: "Frequency",
    }

    # Pass 1: do TWO updates 0.5s apart to expose stale-value sensors.
    print("Reading sensors (two passes, 0.5s apart, to detect stale ones)...")
    print()
    pass1, pass2 = {}, {}
    for label, target in [("first", pass1), ("second", pass2)]:
        for hw_item in hw._computer.Hardware:
            try:
                hw_item.Update()
                for sub in hw_item.SubHardware:
                    try:
                        sub.Update()
                    except Exception:
                        pass
            except Exception:
                continue
        for hw_item in hw._computer.Hardware:
            for s in hw_item.Sensors:
                key = (str(hw_item.Name), "", str(s.Name), int(s.SensorType))
                target[key] = (
                    float(s.Value) if s.Value is not None else None
                )
            for sub in hw_item.SubHardware:
                for s in sub.Sensors:
                    key = (str(hw_item.Name), str(sub.Name), str(s.Name),
                           int(s.SensorType))
                    target[key] = (
                        float(s.Value) if s.Value is not None else None
                    )
        time.sleep(0.5)

    # Pretty-print, grouped by hardware
    print(f"{'Hardware':<35} {'Sub':<22} {'Sensor':<35} "
          f"{'Type':<13} {'v1':>10} {'v2':>10} {'live':>6}")
    print("-" * 138)
    cpu_temp_sensors = []
    last_hw = None
    for key in sorted(pass1.keys()):
        hw_name, sub_name, sensor_name, stype = key
        if hw_name != last_hw:
            if last_hw is not None:
                print()
            last_hw = hw_name
        v1 = pass1.get(key)
        v2 = pass2.get(key)
        type_name = type_names.get(stype, f"Type{stype}")
        v1s = f"{v1:.2f}" if v1 is not None else "None"
        v2s = f"{v2:.2f}" if v2 is not None else "None"
        live = "yes" if (v1 is not None and v2 is not None and v1 != v2) else (
            "stuck" if (v1 is not None and v1 == v2) else "null"
        )
        print(f"{hw_name[:34]:<35} {sub_name[:21]:<22} "
              f"{sensor_name[:34]:<35} {type_name:<13} "
              f"{v1s:>10} {v2s:>10} {live:>6}")
        if type_name == "Temperature":
            n_lower = sensor_name.lower()
            if ("cpu" in n_lower or "core" in n_lower or "package" in n_lower
                    or "tctl" in n_lower or "tdie" in n_lower):
                cpu_temp_sensors.append((hw_name, sub_name, sensor_name,
                                          v1, live))

    print()
    print("=" * 78)
    print("DIAGNOSIS")
    print("=" * 78)
    print()
    print(f"Found {len(cpu_temp_sensors)} CPU-related temperature sensor(s):")
    for hw_name, sub_name, sname, val, live in cpu_temp_sensors:
        marker = "  >>" if "package" in sname.lower() else "    "
        print(f"{marker} [{hw_name}] {sub_name}/{sname}: "
              f"{val}C (live={live})")

    has_package = any("package" in s[2].lower() for s in cpu_temp_sensors)
    has_core = any("core" in s[2].lower() and "max" not in s[2].lower()
                   for s in cpu_temp_sensors)
    has_die = any(s[1] != "" or "package" in s[2].lower() or
                  "core" in s[2].lower() for s in cpu_temp_sensors)

    print()
    if has_package or has_core:
        print("OK: LHM CAN read CPU package/core sensors. Experiment should work.")
        print("If main script still shows stuck temp, that's a hardware.py bug.")
    else:
        print("PROBLEM: NO CPU package/core temperature sensors found.")
        print("Only motherboard socket-pin sensors are available, which DO")
        print("NOT track actual CPU die temperature under load.")
        print()
        print("Root cause: LHM cannot access CPU MSRs (Model-Specific")
        print("Registers). The WinRing0 kernel driver is either:")
        print("  - Not installed (need admin)")
        print("  - Blocked by Windows Security / Smart App Control")
        print("  - Blocked by antivirus")
        print()
        print("Fix options (try in order, MOST LIKELY FIRST):")
        print()
        print("  1. *** MOST LIKELY ON WIN11 ***")
        print("     Disable Memory Integrity (also called HVCI / Core")
        print("     Isolation). It blocks the WinRing0 kernel driver LHM")
        print("     needs for CPU MSRs. Enabled by default on Win11 since")
        print("     mid-2024.")
        print("       Settings -> Privacy and Security -> Windows Security")
        print("       -> Device Security -> Core Isolation Details")
        print("       -> Memory Integrity -> turn OFF -> REBOOT")
        print()
        print("  2. Try running LibreHardwareMonitor.exe (in")
        print("     pc-agent/Backend/lib/) directly first. Accept any")
        print("     UAC / driver install prompt. If LHM.exe SHOWS your")
        print("     CPU package/core temps, the driver is now loaded;")
        print("     close it and re-run this script.")
        print()
        print("  3. Disable Windows Smart App Control:")
        print("     Settings -> Privacy and Security -> Windows Security")
        print("     -> App and browser control -> Smart App Control -> Off")
        print()
        print("  4. Add the lib/ folder to Windows Defender exclusions.")


if __name__ == "__main__":
    main()
