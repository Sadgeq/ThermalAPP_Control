"""Fit first-order cooling on the cooldown phase of a load-step CSV.

Why separate from fit_first_order.py:

The heating phase (load applied, fans pegged at max) contains a fast
transient driven by silicon junction thermal mass (sub-second), then a
slow rise governed by the heatsink. When the system reaches TjMax, the
fit collapses onto the throttle ceiling and reports a non-physical τ.

The cooldown phase (load removed, fans still pegged at max) is cleaner:
no actuator nonlinearity, no throttle, no input changing. After the
first ~1-2 seconds (where the Si die dumps its heat into the heatsink),
the temperature decays as a clean first-order process governed by
heatsink → ambient dynamics. This is the τ that matters for PID tuning.

Model fit on cooldown phase, t >= t_skip after load drop:

    T(t) = T_amb + (T_start - T_amb) * exp(-(t - t_start) / tau)

Outputs:
  - K_cool (°C): the heatsink-to-ambient drop captured by the cooldown
  - tau_cool (s): heatsink thermal time constant
  - R²: goodness of fit on the slow portion
  - PNG overlay of measurements + fit + skipped region
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def cooling(t, T_amb, T_start, tau):
    return T_amb + (T_start - T_amb) * np.exp(-t / tau)


def fit(csv_path: Path, skip_s: float) -> dict:
    df = pd.read_csv(csv_path)
    cd = df[df["phase"] == "cooldown"].copy()
    if cd.empty:
        sys.exit(f"No 'cooldown' rows in {csv_path}.")

    # Re-zero time at the start of cooldown so τ is interpretable as
    # "seconds after load removal".
    t_raw = cd["t_s"].to_numpy(dtype=float)
    t0 = float(t_raw[0])
    t = t_raw - t0
    T = cd["cpu_temp"].to_numpy(dtype=float)

    mask = ~np.isnan(T) & ~np.isnan(t)
    t, T = t[mask], T[mask]

    # Skip the fast Si-junction transient. On Lenovo Legion 5 the first
    # ~1-2 seconds after a hard taskkill show a near-instantaneous drop
    # (10-30°C) from the die emptying into the heatsink. That's not the
    # plant dynamics we want for PID tuning.
    fit_mask = t >= skip_s
    if fit_mask.sum() < 10:
        sys.exit(
            f"Not enough cooldown samples after skipping {skip_s}s "
            f"({fit_mask.sum()})"
        )
    t_fit = t[fit_mask] - skip_s  # re-zero again so τ refers to fit start
    T_fit = T[fit_mask]

    T_start_guess = float(T_fit[0])
    T_amb_guess = float(np.median(T_fit[-max(5, len(T_fit) // 10):]))
    # τ guess: 63% drop point
    drop_target = T_amb_guess + 0.368 * (T_start_guess - T_amb_guess)
    crossings = np.where(T_fit <= drop_target)[0]
    tau_guess = float(t_fit[crossings[0]]) if len(crossings) else 30.0

    p0 = [T_amb_guess, T_start_guess, max(tau_guess, 1.0)]
    bounds = (
        [10.0, 0.0, 0.5],
        [80.0, 120.0, 600.0],
    )

    popt, _ = curve_fit(cooling, t_fit, T_fit, p0=p0, bounds=bounds, maxfev=5000)
    T_amb, T_start, tau = (float(x) for x in popt)
    fitted = cooling(t_fit, *popt)

    ss_res = float(np.sum((T_fit - fitted) ** 2))
    ss_tot = float(np.sum((T_fit - T_fit.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    K_cool = T_start - T_amb

    return {
        "K_cool_delta_c": round(K_cool, 2),
        "tau_cool_seconds": round(tau, 2),
        "T_start_celsius": round(T_start, 2),
        "T_amb_celsius": round(T_amb, 2),
        "r_squared": round(r_squared, 4),
        "n_samples_fit": int(len(t_fit)),
        "skip_seconds": skip_s,
        "_t_all": t, "_T_all": T,
        "_t_fit": t_fit + skip_s, "_T_fit": T_fit, "_fitted": fitted,
    }


def render(csv_path: Path, r: dict, figures_dir: Path) -> Path:
    figures_dir.mkdir(parents=True, exist_ok=True)
    out_png = figures_dir / f"{csv_path.stem}_cooldown_fit.png"

    fig, ax = plt.subplots(figsize=(9, 5))
    # All cooldown samples in light gray (including skipped die transient)
    ax.plot(r["_t_all"], r["_T_all"],
            "o", markersize=3, alpha=0.3, color="gray",
            label=f"All cooldown samples (n={len(r['_t_all'])})")
    # Fitted region in solid blue
    ax.plot(r["_t_fit"], r["_T_fit"],
            "o", markersize=3, alpha=0.8, color="C0",
            label=f"Fitted region (skip first {r['skip_seconds']}s)")
    ax.plot(r["_t_fit"], r["_fitted"],
            "-", linewidth=2, color="C3",
            label="First-order cooling fit")
    ax.axhline(r["T_amb_celsius"], color="gray", linestyle=":",
               alpha=0.6,
               label=f"T_amb = {r['T_amb_celsius']:.1f}°C")
    ax.axvline(r["skip_seconds"], color="orange", linestyle="--",
               alpha=0.5, label="End of Si-die transient")

    title = (
        f"Cooldown fit — {csv_path.stem}\n"
        f"K_cool = {r['K_cool_delta_c']:.2f}°C   "
        f"τ_cool = {r['tau_cool_seconds']:.1f}s   "
        f"R² = {r['r_squared']:.3f}"
    )
    ax.set_title(title)
    ax.set_xlabel("Time after load removal (s)")
    ax.set_ylabel("CPU temperature (°C)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return out_png


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("csv", type=Path)
    p.add_argument("--skip", type=float, default=2.0,
                   help="Seconds to skip at cooldown start to drop the "
                        "Si-junction fast transient (default 2.0)")
    p.add_argument("--figures-dir", type=Path,
                   default=Path(__file__).parent / "figures")
    args = p.parse_args()

    if not args.csv.exists():
        sys.exit(f"CSV not found: {args.csv}")

    r = fit(args.csv, args.skip)
    png = render(args.csv, r, args.figures_dir)

    summary = {k: v for k, v in r.items() if not k.startswith("_")}
    print(json.dumps(summary, indent=2))
    print(f"\nFigure: {png}")

    json_out = png.with_suffix(".json")
    json_out.write_text(json.dumps(summary, indent=2))
    print(f"Summary: {json_out}")


if __name__ == "__main__":
    main()
