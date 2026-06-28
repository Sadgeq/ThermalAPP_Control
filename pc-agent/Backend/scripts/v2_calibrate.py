"""Step 1 + 2 — calibrate the load and find the reachable band (STANDALONE).

Direct hardware (no agent). Run from an Administrator terminal.

  --plateau : pick the number of stress processes so the CPU settles in a
      realistic, sub-throttle range (~78-82 C) at a moderate fan speed.

  --band    : with that load fixed, measure where temperature lands at MAX
      cooling (fans 100%) and LOW cooling (fans 30%) -> reachable band
      [T_floor, T_ceiling] and the process gain (C per % PWM) for tuning.

Examples:
    python v2_calibrate.py --plateau --procs 3 --fan 50 --secs 180
    python v2_calibrate.py --band --procs 3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v2_lib as v2  # noqa: E402


def _hold_and_measure(hw, pct, settle_s, window_s):
    print(f"[cal] fans -> {pct:.0f}% ; settling {settle_s:.0f}s ...")
    v2.set_all_fans(hw, pct)
    temps = []
    t_window_start = settle_s - window_s

    def on_tick(t_rel, row, dt):
        v2.set_all_fans(hw, pct)  # hold it (in case anything drifts)
        cpu = row["cpu_temp"]
        if t_rel >= t_window_start and cpu is not None:
            temps.append(float(cpu))
        if int(t_rel) % 20 == 0:
            print(f"[cal]   t={t_rel:4.0f}s  cpu={cpu}C  pwm~{row['fan0_pct']}")

    v2.control_loop(hw, settle_s, on_tick, period_s=2.0, safety=True)
    return v2.mean(temps), v2.stdev(temps)


def cmd_plateau(hw, args):
    print(f"[cal] PLATEAU: {args.procs} procs @ {args.fan:.0f}% fans, {args.secs:.0f}s")
    with v2.CpuStress(n_processes=args.procs) as st:
        print(f"[cal] stress across {st.n} processes")
        m, s = _hold_and_measure(hw, args.fan, args.secs,
                                 window_s=min(60.0, args.secs / 3))
    print("\n================ REZULTAT CALIBRARE ================")
    print(f"  plateau (medie ultim interval): {m:.1f} C  (+-{s:.1f})")
    if m > 85:
        print("  -> PREA CALD. Scade --procs cu 1 si reia.")
    elif m < 70:
        print("  -> PREA RECE. Creste --procs cu 1 si reia.")
    else:
        print("  -> OK. Tine acest --procs pentru toate scenariile.")
    print("====================================================")


def cmd_band(hw, args):
    print(f"[cal] BAND: {args.procs} procs ; fans 100% apoi 30%")
    with v2.CpuStress(n_processes=args.procs) as st:
        print(f"[cal] stress across {st.n} processes")
        floor, fs = _hold_and_measure(hw, 100.0, args.settle, window_s=30.0)
        ceil, cs = _hold_and_measure(hw, 30.0, args.settle, window_s=30.0)
    gain = (ceil - floor) / (100.0 - 30.0)
    mid = (floor + ceil) / 2.0
    print("\n================ BANDA REALIZABILA ================")
    print(f"  T_floor  (fani 100%) : {floor:.1f} C  (+-{fs:.1f})")
    print(f"  T_ceiling(fani  30%) : {ceil:.1f} C  (+-{cs:.1f})")
    print(f"  banda                : {floor:.1f} .. {ceil:.1f} C "
          f"(latime {ceil - floor:.1f} C)")
    print(f"  castig de proces     : {abs(gain):.3f} C / % PWM")
    print(f"  setpoint sugerat     : {mid:.0f} C (mijlocul benzii)")
    print(f"\n  -> v2_tune_gains.py:  --gain {abs(gain):.3f}")
    if ceil - floor < 8:
        print("  ATENTIE: banda < 8 C. Creste --procs (mai mult loc de reglat).")
    print("===================================================")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plateau", action="store_true")
    mode.add_argument("--band", action="store_true")
    p.add_argument("--procs", type=int, required=True)
    p.add_argument("--fan", type=float, default=50.0)
    p.add_argument("--secs", type=float, default=180.0)
    p.add_argument("--settle", type=float, default=150.0)
    args = p.parse_args()

    hw = v2.make_hw()
    try:
        if args.plateau:
            cmd_plateau(hw, args)
        else:
            cmd_band(hw, args)
    except RuntimeError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(2)
    finally:
        v2.release(hw)  # return fans to automatic BIOS control


if __name__ == "__main__":
    main()
