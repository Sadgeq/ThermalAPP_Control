"""Step 4 — run a control scenario (STANDALONE: direct hardware + PidController).

No agent. Runs the closed loop in-process using the SAME PidController class
the agent uses (profiles.py), reading temp and writing PWM directly. Three
modes, all under a fixed stress load:

  --mode curve              : open-loop fan curve (no setpoint) — baseline
  --mode pid --setpoint 75  : hold a fixed setpoint
  --mode pid-step --t1 80 --t2 72 --switch 150
                            : setpoint step (the textbook closed-loop graph)

Gains default to profiles.py (PID_DEFAULT_K*); override with --kp/--ki/--kd
to test tuning without editing the file. Run as Administrator.

Examples:
    python v2_run.py --mode curve --procs 3 --output data/v2_curve.csv
    python v2_run.py --mode pid --setpoint 75 --procs 3 --output data/v2_pid75.csv
    python v2_run.py --mode pid-step --t1 80 --t2 72 --switch 150 --procs 3 \
        --output data/v2_pid_step.csv
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import v2_lib as v2  # noqa: E402
from profiles import (  # noqa: E402
    PidController, ProfileEngine,
    PID_DEFAULT_KP, PID_DEFAULT_KI, PID_DEFAULT_KD,
)

COLUMNS = ["t_s", "cpu_temp", "cpu_load", "gpu_temp",
           "fan0_rpm", "fan0_pct", "fan1_rpm", "fan1_pct", "target_temp"]

# Representative QUIET open-loop fan curve (temp C -> fan %): low fans until
# it gets hot, so the baseline sits warm and the PID's lower target stands out.
DEFAULT_CURVE = [
    {"temp": 40, "speed": 20}, {"temp": 60, "speed": 30},
    {"temp": 78, "speed": 42}, {"temp": 86, "speed": 58},
    {"temp": 92, "speed": 80}, {"temp": 97, "speed": 100},
]


def run(args):
    hw = v2.make_hw()
    rows = []
    state = {"setpoint": None, "switched": False}

    pid = None
    if args.mode in ("pid", "pid-step"):
        sp = args.setpoint if args.mode == "pid" else args.t1
        state["setpoint"] = sp
        pid = PidController(setpoint=sp, kp=args.kp, ki=args.ki, kd=args.kd)
        print(f"[run] PID setpoint={sp}C  Kp={args.kp} Ki={args.ki} Kd={args.kd}")
    else:
        print("[run] curve mode (open loop)")

    buf = []  # rolling median on the noisy temperature fed to the controller

    def on_tick(t_rel, row, dt):
        temp = row["cpu_temp"]
        if temp is None:
            return
        buf.append(float(temp))
        if len(buf) > 5:
            buf.pop(0)
        temp_ctrl = sorted(buf)[len(buf) // 2]

        # setpoint step (once)
        if args.mode == "pid-step" and not state["switched"] and t_rel >= args.switch:
            pid.setpoint = float(args.t2)
            pid.reset()
            state["setpoint"] = args.t2
            state["switched"] = True
            print(f"[run] >>> t={t_rel:.0f}s  setpoint {args.t1} -> {args.t2}C")

        if pid is not None:
            pwm = pid.step(measured=temp_ctrl, dt=dt)
        else:
            pwm = ProfileEngine._interpolate(DEFAULT_CURVE, temp_ctrl)
        v2.set_all_fans(hw, pwm)

        r = dict(row)
        r["t_s"] = round(t_rel, 2)
        r["target_temp"] = state["setpoint"] if state["setpoint"] is not None else ""
        rows.append(r)
        if int(t_rel) % 10 == 0:
            print(f"[run] t={t_rel:4.0f}s cpu={temp}C "
                  f"set={state['setpoint']} pwm={pwm:.0f}%")

    try:
        with v2.CpuStress(n_processes=args.procs) as st:
            print(f"[run] stress across {st.n} procs; {args.duration:.0f}s")
            v2.control_loop(hw, args.duration, on_tick,
                            period_s=args.dt, safety=True)
    except RuntimeError as e:
        print(f"\n{e}", file=sys.stderr)
        v2.write_csv(args.output, COLUMNS, rows)  # keep partial data
        sys.exit(2)
    finally:
        v2.release(hw)  # return fans to automatic BIOS control

    v2.write_csv(args.output, COLUMNS, rows)
    print(f"[run] done. wrote {args.output} ({len(rows)} rows)")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--mode", required=True, choices=["curve", "pid", "pid-step"])
    p.add_argument("--setpoint", type=float, help="setpoint for --mode pid")
    p.add_argument("--t1", type=float, help="initial setpoint for pid-step")
    p.add_argument("--t2", type=float, help="setpoint after step for pid-step")
    p.add_argument("--switch", type=float, default=150.0)
    p.add_argument("--procs", type=int, required=True)
    p.add_argument("--duration", type=float, default=300.0)
    p.add_argument("--dt", type=float, default=2.0, help="control period s (default 2)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--kp", type=float, default=PID_DEFAULT_KP)
    p.add_argument("--ki", type=float, default=PID_DEFAULT_KI)
    p.add_argument("--kd", type=float, default=PID_DEFAULT_KD)
    args = p.parse_args()

    if args.mode == "pid" and args.setpoint is None:
        sys.exit("--mode pid needs --setpoint")
    if args.mode == "pid-step" and (args.t1 is None or args.t2 is None):
        sys.exit("--mode pid-step needs --t1 and --t2")
    for v in (args.setpoint, args.t1, args.t2):
        if v is not None and not (40.0 <= v <= 95.0):
            sys.exit("setpoints must be in [40, 95] C")

    run(args)


if __name__ == "__main__":
    main()
