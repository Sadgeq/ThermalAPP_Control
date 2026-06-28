"""Step 3 — identify the thermal model (cooldown method, STANDALONE).

Direct hardware (no agent). Run from an Administrator terminal.

Holds fans at 100% (constant max cooling), heats the CPU with a sustained
load to steady state, then removes the load and records the cooldown. The
cooldown decays as a clean first-order process:

    T(t) = T_inf + (T_0 - T_inf) * exp(-(t - t0) / tau)

Outputs tau, K, R^2 and writes a CSV (phase column: heat/cooldown).

Example:
    python v2_model_id.py --procs 6 --output data/v2_model.csv
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from scipy.optimize import curve_fit  # noqa: E402

import v2_lib as v2  # noqa: E402

COLUMNS = ["t_s", "cpu_temp", "cpu_load", "gpu_temp", "fan0_pct", "phase"]


def _cooling(t, t_inf, t_start, tau):
    return t_inf + (t_start - t_inf) * np.exp(-t / tau)


def run(procs, heat_s, cool_s, output, skip_s):
    hw = v2.make_hw()
    rows = []

    def rec(phase, t_off=0.0):
        def on_tick(t_rel, row, dt):
            v2.set_all_fans(hw, 100.0)  # keep cooling pinned
            rows.append({
                "t_s": round(t_off + t_rel, 2),
                "cpu_temp": row["cpu_temp"], "cpu_load": row["cpu_load"],
                "gpu_temp": row["gpu_temp"], "fan0_pct": row["fan0_pct"],
                "phase": phase,
            })
            if int(t_rel) % 15 == 0:
                print(f"[mid] {phase:8s} t={t_rel:4.0f}s cpu={row['cpu_temp']}C")
        return on_tick

    try:
        print("[mid] fans -> 100% (max, constant)")
        v2.set_all_fans(hw, 100.0)
        # HEAT: load on, fans pinned, climb to steady state.
        with v2.CpuStress(n_processes=procs) as st:
            print(f"[mid] HEAT: {st.n} procs, {heat_s:.0f}s")
            v2.control_loop(hw, heat_s, rec("heat"), period_s=1.0, safety=True)
        t_heat_end = rows[-1]["t_s"] if rows else heat_s
        # COOLDOWN: load off, fans still pinned.
        print(f"[mid] COOLDOWN: load removed, {cool_s:.0f}s")
        v2.control_loop(hw, cool_s, rec("cooldown", t_off=t_heat_end),
                        period_s=1.0, safety=False)
    finally:
        v2.release(hw)  # return fans to automatic BIOS control

    v2.write_csv(output, COLUMNS, rows)
    print(f"[mid] wrote {output} ({len(rows)} rows)")

    # Fit the cooldown phase.
    cd = [r for r in rows if r["phase"] == "cooldown" and r["cpu_temp"] is not None]
    if len(cd) < 10:
        sys.exit("[mid] not enough cooldown samples to fit.")
    t_raw = np.array([r["t_s"] for r in cd], dtype=float)
    t = t_raw - t_raw[0]
    T = np.array([float(r["cpu_temp"]) for r in cd], dtype=float)
    m = t >= skip_s
    t, T = t[m] - t[m][0], T[m]

    popt, _ = curve_fit(_cooling, t, T, p0=[T[-1], T[0], 30.0], maxfev=10000)
    t_inf, t_start, tau = popt
    resid = T - _cooling(t, *popt)
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((T - T.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    K = abs(t_start - t_inf)

    print("\n================ MODEL TERMIC (cooldown) ================")
    print(f"  tau (constanta de timp) : {tau:.2f} s")
    print(f"  K (caderea de temp)     : {K:.2f} C")
    print(f"  T_0 / T_inf             : {t_start:.2f} / {t_inf:.2f} C")
    print(f"  R^2                     : {r2:.4f}")
    print(f"  n (esantioane fit)      : {len(t)}")
    print(f"\n  -> v2_tune_gains.py:  --tau {tau:.1f}")
    if r2 < 0.9:
        print("  ATENTIE: R^2 < 0.90. Mareste --heat sau --skip.")
    print("========================================================")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--procs", type=int, default=6)
    p.add_argument("--heat", type=float, default=150.0)
    p.add_argument("--cool", type=float, default=180.0)
    p.add_argument("--skip", type=float, default=3.0)
    p.add_argument("--output", type=Path, default=Path("data/v2_model.csv"))
    args = p.parse_args()
    try:
        run(args.procs, args.heat, args.cool, args.output, args.skip)
    except RuntimeError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
