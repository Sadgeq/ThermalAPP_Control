"""Desktop thermal-model identification with continuous PWM control.

Designed for desktop motherboards (ASUS PRIME Z790, MSI, Gigabyte, etc.)
that expose a continuous PWM duty cycle through a Super-I/O chip
(NCT6798D, NCT6796D, IT8688E, etc.) read/written by LibreHardwareMonitor.

Unlike the laptop step-response script, this:

  * LOCKS the CPU fan at a fixed PWM duty for the entire run, so the
    cooling capacity is genuinely constant (no BIOS curve interference).
  * Steps the CPU LOAD (not the fan), so the input is a clean
    rectangular pulse.
  * Records BOTH heating and cooldown phases.
  * Fits a first-order model to each phase independently and reports
    them side-by-side for cross-validation.

Self-contained: does NOT require the agent to be running. Talks to
hardware directly via the HardwareMonitor class from hardware.py.

Pre-flight checklist:

  1. Open Windows Powershell AS ADMINISTRATOR
     (LibreHardwareMonitor needs admin to read sensors and write PWM)
  2. cd to fan-control-system/pc-agent/
  3. pip install -r requirements-dev.txt
  4. python Backend/scripts/desktop_step_response.py

Expected outcome on a desktop with proper cooler:

  * R^2 > 0.95 on BOTH heating and cooldown fits
  * tau_heat ~= tau_cool within 20%
  * tau in the 30-120s range depending on cooler thermal mass
  * No throttle (CPU stays well below TjMax)

Outputs (relative to script directory):

  * data/desktop_step_<timestamp>.csv      - raw 1Hz measurements
  * figures/desktop_step_<timestamp>.png   - both phases + fits overlay
  * figures/desktop_step_<timestamp>.json  - K, tau, R^2 for each phase
"""

import argparse
import json
import multiprocessing
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Defer the HardwareMonitor import to inside run() — on Windows,
# multiprocessing.Process(spawn) re-imports this module in each child
# process. If HardwareMonitor (which loads LibreHardwareMonitor.dll via
# pythonnet) imports at module top-level, every busy-loop child pays a
# 5-7s .NET init cost AND may conflict with the parent's CLR. Keeping
# the import lazy is required for correctness on Windows.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


COLUMNS = [
    "t_s", "phase", "cpu_temp", "cpu_load",
    "fan0_rpm", "fan0_pct", "fan1_rpm", "fan1_pct",
    "pwm_target", "n_stress",
]


def _busy_loop():
    """Pure-Python busy loop. One per process pins one logical core."""
    x = 0.0
    while True:
        x = (x + 1.0) * 1.0000001


class CpuStress:
    """Spawn N busy-loop processes; clean up on context exit."""

    def __init__(self, n_processes: int):
        self.n = int(n_processes)
        self._procs = []

    def __enter__(self):
        for _ in range(self.n):
            p = multiprocessing.Process(target=_busy_loop, daemon=True)
            p.start()
            self._procs.append(p)
        return self

    def __exit__(self, *exc):
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
        return False


def wait_until(deadline: float):
    """Sleep until monotonic deadline, robust to small overruns."""
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def extract_metrics(sensors: dict, t_s: float, phase: str,
                    pwm_target: float, n_stress: int) -> dict:
    fans = sensors.get("fan_speeds", []) or []
    f0 = fans[0] if len(fans) > 0 else {}
    f1 = fans[1] if len(fans) > 1 else {}
    cpu_load_list = sensors.get("cpu_load_per_core", []) or []
    cpu_load = sensors.get("cpu_load")
    if cpu_load is None and cpu_load_list:
        cpu_load = sum(cpu_load_list) / len(cpu_load_list)
    return {
        "t_s": round(t_s, 2),
        "phase": phase,
        "cpu_temp": sensors.get("cpu_temp"),
        "cpu_load": round(cpu_load, 2) if cpu_load is not None else None,
        "fan0_rpm": f0.get("rpm"),
        "fan0_pct": f0.get("percent"),
        "fan1_rpm": f1.get("rpm"),
        "fan1_pct": f1.get("percent"),
        "pwm_target": pwm_target,
        "n_stress": n_stress,
    }


def fit_first_order(t: np.ndarray, T: np.ndarray, rising: bool) -> dict:
    """Fit T(t) = T_inf + (T_0 - T_inf) * exp(-t/tau).

    rising=True for heating (T_0 < T_inf), False for cooldown.
    Returns dict with tau, K=|T_inf-T_0|, R^2, and reconstructed curve.
    """
    def model(t, T_inf, T_0, tau):
        return T_inf + (T_0 - T_inf) * np.exp(-t / tau)

    T_0_g = float(T[0])
    T_inf_g = float(np.median(T[int(0.8 * len(T)):]))
    delta = abs(T_inf_g - T_0_g)
    # tau initial guess at 63% crossing
    if delta > 0.5:
        target = T_0_g + 0.632 * (T_inf_g - T_0_g)
        cross = np.where(
            (T - target) * (T_0_g - T_inf_g) <= 0
        )[0]
        tau_g = float(t[cross[0]]) if len(cross) else 30.0
    else:
        tau_g = 30.0

    p0 = [T_inf_g, T_0_g, max(tau_g, 1.0)]
    bounds = ([10, 0, 0.5], [120, 120, 600])

    popt, _ = curve_fit(model, t, T, p0=p0, bounds=bounds, maxfev=10000)
    T_inf, T_0, tau = (float(x) for x in popt)
    fitted = model(t, *popt)
    ss_res = float(np.sum((T - fitted) ** 2))
    ss_tot = float(np.sum((T - T.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    K = T_inf - T_0 if rising else T_0 - T_inf

    return {
        "K_celsius": round(K, 2),
        "tau_seconds": round(tau, 2),
        "T_0_celsius": round(T_0, 2),
        "T_inf_celsius": round(T_inf, 2),
        "r_squared": round(r2, 4),
        "n_samples": int(len(t)),
        "rmse_celsius": round(float(np.sqrt(ss_res / len(t))), 3),
        "_t": t, "_T": T, "_fitted": fitted,
    }


def render(df: pd.DataFrame, heat_fit: dict, cool_fit: dict,
           pwm_target: float, n_stress: int, out_png: Path):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # --- Heating ---
    ax = axes[0]
    ax.plot(heat_fit["_t"], heat_fit["_T"], "o", ms=3, alpha=0.6,
            label="Measured")
    ax.plot(heat_fit["_t"], heat_fit["_fitted"], "-", lw=2, color="C3",
            label="First-order fit")
    ax.axhline(heat_fit["T_inf_celsius"], color="gray", linestyle=":",
               alpha=0.6,
               label=f"T_inf = {heat_fit['T_inf_celsius']:.1f}C")
    ax.set_title(
        f"Heating ({n_stress} stress procs, PWM {pwm_target:.0f}%)\n"
        f"K = {heat_fit['K_celsius']:.2f}C   "
        f"tau = {heat_fit['tau_seconds']:.1f}s   "
        f"R^2 = {heat_fit['r_squared']:.4f}"
    )
    ax.set_xlabel("Time after load applied (s)")
    ax.set_ylabel("CPU temperature (C)")
    ax.grid(alpha=0.3)
    ax.legend()

    # --- Cooldown ---
    ax = axes[1]
    ax.plot(cool_fit["_t"], cool_fit["_T"], "o", ms=3, alpha=0.6,
            color="C0", label="Measured")
    ax.plot(cool_fit["_t"], cool_fit["_fitted"], "-", lw=2, color="C3",
            label="First-order fit")
    ax.axhline(cool_fit["T_inf_celsius"], color="gray", linestyle=":",
               alpha=0.6,
               label=f"T_amb = {cool_fit['T_inf_celsius']:.1f}C")
    ax.set_title(
        f"Cooldown (load removed, PWM {pwm_target:.0f}% held)\n"
        f"K = {cool_fit['K_celsius']:.2f}C   "
        f"tau = {cool_fit['tau_seconds']:.1f}s   "
        f"R^2 = {cool_fit['r_squared']:.4f}"
    )
    ax.set_xlabel("Time after load removed (s)")
    ax.set_ylabel("CPU temperature (C)")
    ax.grid(alpha=0.3)
    ax.legend()

    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)


def cross_validate(heat_fit: dict, cool_fit: dict) -> dict:
    tau_h = heat_fit["tau_seconds"]
    tau_c = cool_fit["tau_seconds"]
    K_h = abs(heat_fit["K_celsius"])
    K_c = abs(cool_fit["K_celsius"])
    return {
        "tau_heating": tau_h,
        "tau_cooldown": tau_c,
        "tau_mean": round((tau_h + tau_c) / 2, 2),
        "tau_disagreement_pct": round(
            100 * abs(tau_h - tau_c) / max(tau_h, tau_c), 1),
        "K_heating": K_h,
        "K_cooldown": K_c,
        "K_mean": round((K_h + K_c) / 2, 2),
        "K_disagreement_pct": round(
            100 * abs(K_h - K_c) / max(K_h, K_c), 1) if max(K_h, K_c) > 0 else 0,
        "verdict": (
            "GOOD: heating and cooldown agree within 20%"
            if (max(tau_h, tau_c) / min(tau_h, tau_c) <= 1.20)
            else "WARNING: heating/cooldown disagree >20% - fan may not "
                 "have stayed at locked PWM, or one fit is over-extrapolated"
        ),
    }


def lock_fan_and_verify(hw, fan_index: int,
                        pwm_pct: float, settle_s: float = 8.0) -> bool:
    """Lock fan PWM and verify by reading RPM stability for settle_s.

    Returns True if RPM stabilizes (stdev < 5% of mean) at the requested
    duty. False if the EC overrides our setting.
    """
    print(f"[lock] requesting fan{fan_index} PWM = {pwm_pct:.0f}%")
    hw.set_fan_speed(fan_index, pwm_pct)
    time.sleep(settle_s)

    samples = []
    for _ in range(5):
        s = hw.read_sensors()
        fans = s.get("fan_speeds", []) or []
        if fan_index < len(fans):
            rpm = fans[fan_index].get("rpm")
            if rpm:
                samples.append(rpm)
        time.sleep(1.0)

    if len(samples) < 3:
        print("[lock] WARNING: could not read fan RPM consistently")
        return False
    mean = sum(samples) / len(samples)
    stdev = (sum((x - mean) ** 2 for x in samples) / len(samples)) ** 0.5
    cv = stdev / mean if mean > 0 else 1.0
    print(f"[lock] fan{fan_index} RPM samples: {samples}, "
          f"mean={mean:.0f}, cv={cv*100:.1f}%")
    if cv > 0.05:
        print(f"[lock] WARNING: fan RPM unstable (cv={cv*100:.1f}% > 5%); "
              f"BIOS/EC may be overriding software PWM")
        return False
    print(f"[lock] OK: fan{fan_index} stable at {mean:.0f} RPM")
    return True


def pick_cpu_fan(hw) -> int:
    """Identify the fan most likely to be the CPU fan (highest RPM at idle).

    Most desktop boards label the CPU fan as fan0 in LHM enumeration, but
    not guaranteed. We pick the fastest spinning fan at idle as a heuristic
    that's correct on virtually all setups.
    """
    s = hw.read_sensors()
    fans = s.get("fan_speeds", []) or []
    if not fans:
        print("[detect] ERROR: no fans detected by LibreHardwareMonitor")
        return -1
    print(f"[detect] {len(fans)} fan(s) found:")
    for i, f in enumerate(fans):
        print(f"  fan{i}: {f.get('rpm', 'n/a')} RPM, {f.get('percent', 'n/a')}%")
    # Pick fastest as CPU fan
    rpms = [(i, f.get("rpm") or 0) for i, f in enumerate(fans)]
    rpms.sort(key=lambda x: -x[1])
    chosen = rpms[0][0]
    print(f"[detect] picking fan{chosen} as CPU fan (highest idle RPM)")
    return chosen


def run(args):
    print("=" * 70)
    print("Desktop thermal-model identification (continuous PWM)")
    print("=" * 70)

    # Lazy import so multiprocessing child processes don't re-init LHM.
    print("[init] starting HardwareMonitor (loads LibreHardwareMonitor)...")
    from hardware import HardwareMonitor
    hw = HardwareMonitor()
    print(f"[init] controller_name = {hw.controller_name}")
    print(f"[init] has_pwm_control = {hw.has_pwm_control}")
    print(f"[init] vendor = {hw.vendor!r}, model = {hw.model!r}")

    if hw.controller_name == "lenovo-legion-wmi":
        print("\n[init] WARNING: This script is intended for desktops with "
              "continuous PWM control. Detected hardware is Lenovo Legion "
              "(WMI FanMode only). Use step_response_load.py instead.\n")
        if not args.force:
            sys.exit(1)

    if not hw.has_pwm_control:
        print("\n[init] ERROR: LibreHardwareMonitor reports no PWM controllers.")
        print("       Make sure the script runs as Administrator.")
        print("       (LHM needs admin to access Super-I/O registers.)")
        sys.exit(1)

    # Pick which fan to lock
    fan_index = args.fan_index if args.fan_index >= 0 else pick_cpu_fan(hw)
    if fan_index < 0:
        sys.exit("No CPU fan detected")

    # Lock fan PWM and verify
    if not lock_fan_and_verify(hw, fan_index, args.pwm_lock,
                                settle_s=args.settle_s):
        if not args.force:
            print("[init] Aborting because fan PWM did not lock cleanly.")
            print("       Re-run with --force to proceed anyway, or check "
                  "BIOS settings (disable 'Smart Fan', set CPU fan to "
                  "'Manual' or 'PWM' mode).")
            sys.exit(1)

    # Ensure fan returns to BIOS auto control no matter how we exit
    # (normal completion, KeyboardInterrupt, exception during stress).
    def _restore_fan():
        try:
            hw._fan_controllers[fan_index].Control.SetDefault()
            print("[cleanup] fan returned to BIOS control")
        except Exception as e:
            print(f"[cleanup] WARNING: could not restore fan to auto: {e}")

    try:
        return _run_experiment(args, hw, fan_index)
    finally:
        _restore_fan()


def _run_experiment(args, hw, fan_index: int):
    # Pre-soak: idle baseline with fan locked
    print(f"\n[soak] {args.baseline}s idle baseline...")
    period = 1.0 / args.hz
    out_data = []
    t0 = time.monotonic()
    next_tick = t0
    while True:
        now = time.monotonic()
        t_rel = now - t0 - args.baseline
        if now - t0 >= args.baseline:
            break
        try:
            row = extract_metrics(hw.read_sensors(), t_rel, "soak",
                                   args.pwm_lock, 0)
            out_data.append(row)
        except Exception as e:
            print(f"[soak] sensor read failed: {e}", file=sys.stderr)
        next_tick += period
        wait_until(next_tick)

    # Heating phase
    print(f"\n[heat] applying {args.stress_procs} stress procs, "
          f"logging {args.heating}s...")
    with CpuStress(args.stress_procs):
        step_t = time.monotonic()
        next_tick = step_t
        while True:
            now = time.monotonic()
            t_rel = now - step_t
            if t_rel >= args.heating:
                break
            try:
                row = extract_metrics(hw.read_sensors(), t_rel, "response",
                                       args.pwm_lock, args.stress_procs)
                out_data.append(row)
                if int(t_rel) % 30 == 0 and t_rel >= 1.0:
                    cpu = row["cpu_temp"]
                    rpm = row["fan0_rpm"]
                    if abs(t_rel - int(t_rel)) < period:
                        print(f"  t={t_rel:5.0f}s  cpu={cpu}C  fan_rpm={rpm}")
            except Exception as e:
                print(f"[heat] sensor read failed: {e}", file=sys.stderr)
            next_tick += period
            wait_until(next_tick)

    # Cooldown phase (stress already terminated by context exit)
    print(f"\n[cool] logging {args.cooldown}s cooldown (fan still at "
          f"PWM {args.pwm_lock}%)...")
    cool_t = time.monotonic()
    next_tick = cool_t
    while True:
        now = time.monotonic()
        t_rel = now - cool_t
        if t_rel >= args.cooldown:
            break
        try:
            row = extract_metrics(hw.read_sensors(), t_rel, "cooldown",
                                   args.pwm_lock, 0)
            out_data.append(row)
            if int(t_rel) % 30 == 0 and t_rel >= 1.0:
                cpu = row["cpu_temp"]
                rpm = row["fan0_rpm"]
                if abs(t_rel - int(t_rel)) < period:
                    print(f"  t={t_rel:5.0f}s  cpu={cpu}C  fan_rpm={rpm}")
        except Exception as e:
            print(f"[cool] sensor read failed: {e}", file=sys.stderr)
        next_tick += period
        wait_until(next_tick)

    # Write CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_dir = Path(__file__).parent / "data"
    fig_dir = Path(__file__).parent / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    csv_out = data_dir / f"desktop_step_{timestamp}.csv"
    df = pd.DataFrame(out_data, columns=COLUMNS)
    df.to_csv(csv_out, index=False)
    print(f"[done] CSV: {csv_out}")

    # Post-run sanity check: did the fan actually stay locked? If the BIOS
    # quietly modulates the fan despite our PWM write, the model fits are
    # contaminated. The lock_fan_and_verify check at the start can miss
    # this if BIOS only intervenes under thermal pressure.
    active = df[df.phase.isin(["response", "cooldown"])].copy()
    fan_rpms = active.fan0_rpm.dropna().astype(float).values
    if len(fan_rpms) >= 10:
        rpm_mean = float(fan_rpms.mean())
        rpm_std = float(fan_rpms.std())
        rpm_cv = rpm_std / rpm_mean if rpm_mean > 0 else 1.0
        print(f"\n[fan-check] fan0 RPM during experiment: "
              f"mean={rpm_mean:.0f}, std={rpm_std:.0f}, cv={rpm_cv*100:.1f}%")
        if rpm_cv > 0.10:
            print("[fan-check] WARNING: fan RPM varied >10% during the run.")
            print("            BIOS likely interfered with the locked PWM.")
            print("            The fit results may be biased; check BIOS Q-Fan "
                  "settings before trusting these numbers.")
        else:
            print("[fan-check] OK: fan stayed within 10% - clean experiment.")
    else:
        print("[fan-check] insufficient fan samples to validate stability.")

    # Fit heating phase
    resp = df[df.phase == "response"].copy()
    cool = df[df.phase == "cooldown"].copy()
    # Skip first SKIP seconds of cooldown for Si-die transient
    cool_skip = args.cool_skip
    cool = cool[cool.t_s >= cool_skip].copy()
    if len(cool):
        cool["t_s"] = cool["t_s"] - cool_skip

    print("\n" + "=" * 70)
    print("MODEL FIT RESULTS")
    print("=" * 70)

    heat_fit = fit_first_order(resp.t_s.values, resp.cpu_temp.values, rising=True)
    print("\n[fit] Heating:")
    for k in ["K_celsius", "tau_seconds", "T_0_celsius", "T_inf_celsius",
              "r_squared", "rmse_celsius", "n_samples"]:
        print(f"  {k:20s} = {heat_fit[k]}")

    cool_fit = fit_first_order(cool.t_s.values, cool.cpu_temp.values, rising=False)
    print("\n[fit] Cooldown:")
    for k in ["K_celsius", "tau_seconds", "T_0_celsius", "T_inf_celsius",
              "r_squared", "rmse_celsius", "n_samples"]:
        print(f"  {k:20s} = {cool_fit[k]}")

    xval = cross_validate(heat_fit, cool_fit)
    print("\n[xval] Cross-validation heating vs cooldown:")
    for k, v in xval.items():
        print(f"  {k:25s} = {v}")

    # Render figure
    png_out = fig_dir / f"desktop_step_{timestamp}.png"
    render(df[df.phase != "soak"], heat_fit, cool_fit,
           args.pwm_lock, args.stress_procs, png_out)
    print(f"\n[done] Figure: {png_out}")

    # Persist JSON summary
    summary = {
        "timestamp": timestamp,
        "hardware": {
            "vendor": hw.vendor,
            "model": hw.model,
            "controller": hw.controller_name,
        },
        "config": {
            "pwm_lock_pct": args.pwm_lock,
            "stress_procs": args.stress_procs,
            "baseline_s": args.baseline,
            "heating_s": args.heating,
            "cooldown_s": args.cooldown,
            "cool_skip_s": args.cool_skip,
            "sample_hz": args.hz,
            "fan_index_locked": fan_index,
        },
        "heating_fit": {k: v for k, v in heat_fit.items() if not k.startswith("_")},
        "cooldown_fit": {k: v for k, v in cool_fit.items() if not k.startswith("_")},
        "cross_validation": xval,
        "csv_path": str(csv_out),
        "figure_path": str(png_out),
    }
    json_out = fig_dir / f"desktop_step_{timestamp}.json"
    json_out.write_text(json.dumps(summary, indent=2))
    print(f"[done] JSON summary: {json_out}")
    print("\n" + "=" * 70)
    print("VERDICT:", xval["verdict"])
    print("=" * 70)
    print("\nSend back to Marius:")
    print(f"  - {csv_out}")
    print(f"  - {png_out}")
    print(f"  - {json_out}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--pwm-lock", type=float, default=50.0,
                   help="Fan PWM duty cycle to lock during the experiment "
                        "(percent, default 50). Choose so the fan stays "
                        "audible-but-tolerable.")
    # Auto-scale default stress procs by CPU thread count. Busy-loop is
    # GIL-bound, so one process = one logical core saturated. Targeting
    # ~1/3 of logical cores tends to land in the 75-85C sweet spot on
    # desktop coolers with fan at 50% PWM:
    #   8 threads  -> 4 procs  (older quad-core w/ HT)
    #   12 threads -> 4 procs  (Ryzen 5)
    #   16 threads -> 5 procs  (Ryzen 7 / older i7)
    #   20 threads -> 6 procs  (Intel 13th-gen i5 with E-cores)
    #   24 threads -> 8 procs  (i7-13700K)
    #   32 threads -> 10 procs (i9-13900K, Ryzen 9 7950X)
    n_threads = multiprocessing.cpu_count()
    default_stress = max(4, n_threads // 3)
    p.add_argument("--stress-procs", type=int, default=default_stress,
                   help=f"Number of busy-loop processes (default scales "
                        f"with CPU thread count; on this machine = "
                        f"{default_stress}). Tune to land around 75-85C "
                        f"steady state without throttling. Increase if "
                        f"it's too cold, decrease if it throttles.")
    p.add_argument("--baseline", type=float, default=60.0,
                   help="Idle baseline duration (s, default 60)")
    p.add_argument("--heating", type=float, default=240.0,
                   help="Heating phase duration (s, default 240). "
                        "Should be >= 5*tau to reach steady state.")
    p.add_argument("--cooldown", type=float, default=240.0,
                   help="Cooldown phase duration (s, default 240).")
    p.add_argument("--cool-skip", type=float, default=2.0,
                   help="Seconds to skip at cooldown start (default 2). "
                        "Drops the Si-die fast transient.")
    p.add_argument("--hz", type=float, default=1.0,
                   help="Sample rate (default 1 Hz).")
    p.add_argument("--settle-s", type=float, default=8.0,
                   help="Seconds to wait after locking fan PWM (default 8).")
    p.add_argument("--fan-index", type=int, default=-1,
                   help="Override fan index to lock (default: auto-detect "
                        "CPU fan as the fastest-spinning fan at idle).")
    p.add_argument("--force", action="store_true",
                   help="Continue even if fan PWM lock verification fails "
                        "or hardware appears to be a Lenovo Legion.")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    # Required for multiprocessing.Process on Windows when frozen.
    # Harmless when running as a script.
    multiprocessing.freeze_support()
    try:
        main()
    except KeyboardInterrupt:
        print("\n[interrupt] Ctrl+C received - finalizing cleanly...")
        # The try/finally inside run() handles fan restoration.
        sys.exit(130)
