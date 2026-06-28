"""Step 5 — plot the v2 runs and compute the comparison metrics.

Produces three clean figures and a metrics CSV:
  * v2_temp_overlay.png  — CPU temp vs time, with setpoint reference lines
  * v2_fan_overlay.png   — fan PWM % vs time (shows active modulation,
                           NOT saturation)
  * v2_metrics.csv       — RMS error, overshoot, mean PWM/RPM per run

A light median filter (window 5) is applied for display only; raw data is
untouched. Pass any subset of --curve/--pid/--step.

Example:
    python v2_plot.py --curve data/v2_curve.csv \\
        --pid data/v2_pid75.csv:75 --step data/v2_pid_step.csv:72 \\
        --outdir figures_v2
"""

import argparse
import csv
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    def col(name):
        out = []
        for r in rows:
            v = r.get(name, "")
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(None)
        return out
    return {"t": col("t_s"), "temp": col("cpu_temp"),
            "pwm": col("fan0_pct"), "rpm": col("fan0_rpm"),
            "target": col("target_temp")}


def median_filter(xs, w=5):
    half = w // 2
    out = []
    for i in range(len(xs)):
        seg = [x for x in xs[max(0, i - half):i + half + 1] if x is not None]
        out.append(sorted(seg)[len(seg) // 2] if seg else None)
    return out


def metrics(d, setpoint):
    """RMS error vs setpoint + overshoot + means, over the steady tail."""
    t, temp, pwm, rpm = d["t"], d["temp"], d["pwm"], d["rpm"]
    pairs = [(temp[i], pwm[i], rpm[i]) for i in range(len(t))
             if temp[i] is not None]
    if not pairs:
        return {}
    temps = [p[0] for p in pairs]
    n = len(temps)
    tail = temps[int(n * 0.4):]          # ignore initial transient
    out = {"n": n, "mean_temp": round(sum(temps) / n, 1),
           "max_temp": round(max(temps), 1),
           "mean_pwm": round(sum(p[1] for p in pairs if p[1] is not None)
                             / max(1, sum(1 for p in pairs if p[1] is not None)), 0),
           "mean_rpm": round(sum(p[2] for p in pairs if p[2] is not None)
                             / max(1, sum(1 for p in pairs if p[2] is not None)), 0)}
    if setpoint is not None and tail:
        err = [x - setpoint for x in tail]
        out["setpoint"] = setpoint
        out["rms_err"] = round((sum(e * e for e in err) / len(err)) ** 0.5, 2)
        out["overshoot"] = round(max(0.0, max(tail) - setpoint), 1)
        out["ss_err"] = round(sum(err) / len(err), 2)
    return out


def parse_spec(spec):
    """'path' or 'path:setpoint' -> (Path, setpoint|None).

    Splits on the LAST colon only if what follows is a number, so
    Windows 'C:\\data\\x.csv' (no setpoint) and 'C:\\data\\x.csv:75'
    (with setpoint) both parse correctly.
    """
    head, sep, tail = spec.rpartition(":")
    if sep:
        try:
            return Path(head), float(tail)
        except ValueError:
            pass
    return Path(spec), None


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--curve", help="curve-mode CSV (path)")
    p.add_argument("--pid", help="PID@fixed CSV (path:setpoint)")
    p.add_argument("--step", help="PID-step CSV (path:final_setpoint)")
    p.add_argument("--outdir", type=Path, default=Path("figures_v2"))
    p.add_argument("--smooth", type=int, default=5,
                   help="median filter window for display (0=off)")
    args = p.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    series = []  # (label, data, setpoint, color)
    palette = {"curve": "tab:blue", "pid": "tab:orange", "step": "tab:green"}
    if args.curve:
        path, _ = parse_spec(args.curve)
        series.append(("Curbă (buclă deschisă)", load_csv(path), None, palette["curve"]))
    if args.pid:
        path, sp = parse_spec(args.pid)
        series.append((f"PID @{sp:.0f}°C" if sp else "PID", load_csv(path), sp, palette["pid"]))
    if args.step:
        path, sp = parse_spec(args.step)
        series.append(("PID cu treaptă de setpoint", load_csv(path), sp, palette["step"]))
    if not series:
        sys.exit("Nimic de plotat. Da cel putin --curve/--pid/--step.")

    sm = (lambda xs: median_filter(xs, args.smooth)) if args.smooth else (lambda xs: xs)

    # --- temperature overlay ---
    plt.figure(figsize=(11, 5.5))
    setpoints = set()
    for label, d, sp, color in series:
        plt.plot(d["t"], sm(d["temp"]), label=label, color=color, linewidth=1.3)
        if sp:
            setpoints.add(sp)
    for sp in setpoints:
        plt.axhline(sp, ls=":", color="gray", linewidth=0.9)
    plt.xlabel("Timp (s)"); plt.ylabel("Temperatură CPU (°C)")
    plt.title("Temperatura CPU vs. timp — comparație scenarii (sarcină moderată)")
    plt.legend(); plt.grid(alpha=0.3)
    f1 = args.outdir / "v2_temp_overlay.png"
    plt.tight_layout(); plt.savefig(f1, dpi=130); plt.close()

    # --- fan PWM overlay ---
    plt.figure(figsize=(11, 5.5))
    for label, d, sp, color in series:
        plt.plot(d["t"], sm(d["pwm"]), label=label, color=color, linewidth=1.3)
    plt.xlabel("Timp (s)"); plt.ylabel("Turație ventilator (% PWM)")
    plt.title("Comanda PWM vs. timp — modulare activă (fără saturație)")
    plt.ylim(0, 105); plt.legend(); plt.grid(alpha=0.3)
    f2 = args.outdir / "v2_fan_overlay.png"
    plt.tight_layout(); plt.savefig(f2, dpi=130); plt.close()

    # --- metrics ---
    rows = []
    for label, d, sp, color in series:
        m = metrics(d, sp)
        m["scenariu"] = label
        rows.append(m)
    cols = ["scenariu", "setpoint", "rms_err", "overshoot", "ss_err",
            "mean_temp", "max_temp", "mean_pwm", "mean_rpm", "n"]
    fcsv = args.outdir / "v2_metrics.csv"
    with open(fcsv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    print(f"[plot] scris:\n  {f1}\n  {f2}\n  {fcsv}\n")
    print("================ METRICI ================")
    for r in rows:
        print(f"  {r.get('scenariu')}: "
              f"RMS={r.get('rms_err','-')}°C  overshoot={r.get('overshoot','-')}°C  "
              f"ss_err={r.get('ss_err','-')}°C  PWM_med={r.get('mean_pwm','-')}%")
    print("=========================================")


if __name__ == "__main__":
    main()
