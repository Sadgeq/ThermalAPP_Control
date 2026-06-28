"""One-command v2 validation (STANDALONE). Run as Administrator.

Given the calibrated stress level (--procs from v2_calibrate --plateau),
this does EVERYTHING automatically, chaining the numbers internally so
nothing has to be copied by hand:

  1. band    : floor (fans 100%) + ceiling (fans 30%) -> process gain
  2. model   : heat + cooldown -> tau (first-order fit)
  3. tune    : IMC gains from tau + gain
  4. run     : 3 scenarios (curve / PID@setpoint / PID setpoint-step)
  5. plot    : overlays + metrics (calls v2_plot.py)
  6. summary : data/v2_summary.txt with every number used

The operator only picks --procs (and optionally --setpoint). Everything
else is derived from measurements.

Example:
    python v2_all.py --procs 3
    python v2_all.py --procs 3 --setpoint 75
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from scipy.optimize import curve_fit  # noqa: E402

import v2_lib as v2  # noqa: E402
from profiles import (  # noqa: E402
    PidController, ProfileEngine,
    PID_DEFAULT_KP, PID_DEFAULT_KI, PID_DEFAULT_KD,
)

SCEN_COLS = ["t_s", "cpu_temp", "cpu_load", "gpu_temp",
             "fan0_rpm", "fan0_pct", "fan1_rpm", "fan1_pct", "target_temp"]
MODEL_COLS = ["t_s", "cpu_temp", "cpu_load", "gpu_temp", "fan0_pct", "phase"]
DEFAULT_CURVE = [
    {"temp": 40, "speed": 30}, {"temp": 55, "speed": 40},
    {"temp": 70, "speed": 55}, {"temp": 80, "speed": 75},
    {"temp": 90, "speed": 95}, {"temp": 95, "speed": 100},
]


def cooldown_between(hw, secs=60.0):
    print(f"[all] racire {secs:.0f}s intre rulari (fani 100%)...")
    v2.set_all_fans(hw, 100.0)
    v2.control_loop(hw, secs, lambda *_: None, period_s=2.0, safety=False)


def _hold(hw, pct, settle, window):
    v2.set_all_fans(hw, pct)
    temps = []
    last = {"t": None}

    def on_tick(t_rel, row, dt):
        v2.set_all_fans(hw, pct)
        if row["cpu_temp"] is not None:
            last["t"] = float(row["cpu_temp"])
            if t_rel >= settle - window:
                temps.append(float(row["cpu_temp"]))
        if int(t_rel) % 20 == 0:
            print(f"[band]  {pct:.0f}%  t={t_rel:4.0f}s  cpu={row['cpu_temp']}C")
    v2.control_loop(hw, settle, on_tick, period_s=2.0, safety=True)
    # If the phase stopped early (safety), the window may be empty — fall back
    # to the last reading so we never return NaN.
    if temps:
        return v2.mean(temps)
    return last["t"] if last["t"] is not None else float("nan")


def measure_band(hw, procs, settle):
    print(f"[all] === BAND ({procs} procs) ===")
    with v2.CpuStress(n_processes=procs):
        # Pre-warm so the first (100%) hold doesn't start from a cold CPU —
        # otherwise its average is contaminated by the warm-up transient and
        # can read HOTTER than the 30% hold (false "fans do nothing").
        print("[band] pre-incalzire 60s la 50% ...")
        _hold(hw, 50.0, 60.0, 10.0)
        floor = _hold(hw, 100.0, settle, 30.0)   # max cooling -> coolest
        ceil = _hold(hw, 30.0, settle, 30.0)     # low cooling -> warmest
    gain = (ceil - floor) / (100.0 - 30.0)       # >0 if fans actually cool
    print(f"[all] floor(100%)={floor:.1f}C ceiling(30%)={ceil:.1f}C gain={gain:.3f} C/%")
    return floor, ceil, gain


def _cooling(t, t_inf, t_start, tau):
    return t_inf + (t_start - t_inf) * np.exp(-t / tau)


def identify_model(hw, procs, heat_s, cool_s, skip_s, out):
    print(f"[all] === MODEL ({procs} procs) ===")
    v2.set_all_fans(hw, 100.0)
    rows = []

    def rec(phase, off=0.0):
        def on_tick(t_rel, row, dt):
            v2.set_all_fans(hw, 100.0)
            rows.append({"t_s": round(off + t_rel, 2), "cpu_temp": row["cpu_temp"],
                         "cpu_load": row["cpu_load"], "gpu_temp": row["gpu_temp"],
                         "fan0_pct": row["fan0_pct"], "phase": phase})
            if int(t_rel) % 20 == 0:
                print(f"[model] {phase:8s} t={t_rel:4.0f}s cpu={row['cpu_temp']}C")
        return on_tick

    with v2.CpuStress(n_processes=procs):
        v2.control_loop(hw, heat_s, rec("heat"), period_s=1.0, safety=True)
    t_end = rows[-1]["t_s"] if rows else heat_s
    v2.control_loop(hw, cool_s, rec("cooldown", t_end), period_s=1.0, safety=False)
    v2.write_csv(out, MODEL_COLS, rows)

    cd = [r for r in rows if r["phase"] == "cooldown" and r["cpu_temp"] is not None]
    t = np.array([r["t_s"] for r in cd], float)
    t = t - t[0]
    T = np.array([float(r["cpu_temp"]) for r in cd], float)
    m = t >= skip_s
    t, T = t[m] - t[m][0], T[m]
    try:
        popt, _ = curve_fit(_cooling, t, T, p0=[T[-1], T[0], 30.0], maxfev=10000)
        t_inf, t_start, tau = popt
        r2 = 1 - np.sum((T - _cooling(t, *popt)) ** 2) / np.sum((T - T.mean()) ** 2)
        if not (1.0 < tau < 1e4):
            raise ValueError(f"tau={tau:.1f} out of range")
    except Exception as e:
        tau, t_inf, t_start, r2 = 40.0, float(T[-1]), float(T[0]), 0.0
        print(f"[all] WARN: fit esuat ({e}); folosesc tau fallback=40s")
    print(f"[all] tau={tau:.1f}s K={abs(t_start-t_inf):.1f}C R2={r2:.3f}")
    return tau, abs(t_start - t_inf), float(r2)


def run_scenario(hw, mode, procs, duration, dt, out, *,
                 setpoint=None, t1=None, t2=None, switch=150.0,
                 kp=0, ki=0, kd=0):
    print(f"[all] === SCENARIU {mode} -> {out.name} ===")
    rows = []
    st = {"sp": (setpoint if mode == "pid" else t1 if mode == "pid-step" else None),
          "switched": False}
    pid = None
    if mode in ("pid", "pid-step"):
        pid = PidController(setpoint=st["sp"], kp=kp, ki=ki, kd=kd)

    def on_tick(t_rel, row, dt_):
        temp = row["cpu_temp"]
        if temp is None:
            return
        if mode == "pid-step" and not st["switched"] and t_rel >= switch:
            pid.setpoint = float(t2)
            pid.reset()
            st["sp"] = t2
            st["switched"] = True
            print(f"[run] >>> t={t_rel:.0f}s setpoint {t1}->{t2}C")
        pwm = pid.step(float(temp), dt_) if pid else ProfileEngine._interpolate(DEFAULT_CURVE, float(temp))
        v2.set_all_fans(hw, pwm)
        r = dict(row)
        r["t_s"] = round(t_rel, 2)
        r["target_temp"] = st["sp"] if st["sp"] is not None else ""
        rows.append(r)
        if int(t_rel) % 20 == 0:
            print(f"[run] t={t_rel:4.0f}s cpu={temp}C set={st['sp']} pwm={pwm:.0f}%")

    with v2.CpuStress(n_processes=procs):
        v2.control_loop(hw, duration, on_tick, period_s=dt, safety=True)
    v2.write_csv(out, SCEN_COLS, rows)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--procs", type=int, required=True,
                   help="calibrated stress procs (from v2_calibrate --plateau)")
    p.add_argument("--model-procs", type=int, default=None,
                   help="load for the model swing (default = same as --procs, "
                        "so it never throttles)")
    p.add_argument("--setpoint", type=float, default=None,
                   help="PID setpoint (default = middle of the band)")
    p.add_argument("--duration", type=float, default=300.0)
    p.add_argument("--dt", type=float, default=2.0)
    p.add_argument("--settle", type=float, default=120.0)
    p.add_argument("--heat", type=float, default=150.0)
    p.add_argument("--cool", type=float, default=180.0)
    p.add_argument("--cooldown", type=float, default=60.0,
                   help="cool-off seconds between runs (default 60)")
    p.add_argument("--lam", type=float, default=None, help="IMC lambda (default=tau)")
    p.add_argument("--outdir", type=Path, default=Path("data"))
    p.add_argument("--figdir", type=Path, default=Path("figures_v2"))
    args = p.parse_args()
    # Model uses the SAME calibrated load by default, so it never throttles.
    model_procs = args.model_procs if args.model_procs is not None else args.procs

    hw = v2.make_hw()
    try:
        # 1. band + gain (pre-warm inside, so 100% isn't measured from cold)
        floor, ceil, gain = measure_band(hw, args.procs, args.settle)
        if floor != floor or ceil != ceil:  # NaN guard (no usable readings)
            print("[all] WARN: banda necitita; folosesc valori implicite.")
            floor, ceil, gain = 75.0, 80.0, 0.0
        cooldown_between(hw, args.cooldown)
        lo, hi = min(floor, ceil), max(floor, ceil)
        # setpoint: middle of the band unless given; keep it in a safe range
        sp = args.setpoint if args.setpoint is not None else round((lo + hi) / 2)
        sp = max(45.0, min(85.0, sp))
        t1 = min(85.0, sp + 3)
        t2 = max(45.0, sp - 3)

        # 2. model (calibrated load, NOT a hotter one -> avoids throttle/abort)
        model_csv = args.outdir / "v2_model.csv"
        tau, K, r2 = identify_model(hw, model_procs, args.heat, args.cool, 3.0, model_csv)
        cooldown_between(hw, args.cooldown)

        # 3. tune: IMC from the model when fans have real authority; otherwise
        # fall back to the empirical gains (low fan authority -> IMC blows up).
        lam = args.lam if args.lam is not None else tau
        if gain < 0.05:
            print(f"[all] WARN: autoritate redusa a ventilatoarelor (gain={gain:.3f} "
                  f"C/%); folosesc gains empirice implicite in loc de IMC.")
            kp, ki, kd = PID_DEFAULT_KP, PID_DEFAULT_KI, PID_DEFAULT_KD
        else:
            kp = tau / (gain * lam)
            ki = kp / tau
            kd = kp * (tau / 20.0)
        print(f"[all] === GAINS Kp={kp:.2f} Ki={ki:.3f} Kd={kd:.3f} ===")

        # 4. scenarios
        curve_csv = args.outdir / "v2_curve.csv"
        pid_csv = args.outdir / "v2_pid_fix.csv"
        step_csv = args.outdir / "v2_pid_step.csv"
        run_scenario(hw, "curve", args.procs, args.duration, args.dt, curve_csv)
        cooldown_between(hw, args.cooldown)
        run_scenario(hw, "pid", args.procs, args.duration, args.dt, pid_csv,
                     setpoint=sp, kp=kp, ki=ki, kd=kd)
        cooldown_between(hw, args.cooldown)
        run_scenario(hw, "pid-step", args.procs, args.duration, args.dt, step_csv,
                     t1=t1, t2=t2, switch=args.duration / 2, kp=kp, ki=ki, kd=kd)
    except RuntimeError as e:
        print(f"\n{e}\nSarcina prea mare — reia cu --procs mai mic.", file=sys.stderr)
        sys.exit(2)
    finally:
        v2.release(hw)  # return fans to automatic BIOS control

    # 5. summary
    summary = (
        f"procs={args.procs} model_procs={model_procs}\n"
        f"band: floor={floor:.1f}C ceiling={ceil:.1f}C gain={gain:.3f} C/%\n"
        f"model: tau={tau:.1f}s K={K:.1f}C R2={r2:.3f}\n"
        f"gains(IMC,lam={lam:.0f}): Kp={kp:.2f} Ki={ki:.3f} Kd={kd:.2f}\n"
        f"setpoints: pid={sp}C  step={t1}->{t2}C\n"
    )
    (args.outdir / "v2_summary.txt").write_text(summary, encoding="utf-8")
    print("\n================ SUMAR ================\n" + summary)

    # 6. plot (reuse v2_plot.py)
    print("[all] generez graficele...")
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "v2_plot.py"),
                    "--curve", str(curve_csv),
                    "--pid", f"{pid_csv}:{sp}",
                    "--step", f"{step_csv}:{t2}",
                    "--outdir", str(args.figdir)], check=False)
    print(f"\n[all] GATA. Trimite folderele '{args.outdir}' si '{args.figdir}'.")


if __name__ == "__main__":
    main()
