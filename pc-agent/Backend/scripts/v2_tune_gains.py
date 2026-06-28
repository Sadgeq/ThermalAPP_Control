"""Step 3 — compute PID gains from the identified model (IMC tuning).

For a first-order plant  G(s) = Kp_plant / (tau*s + 1)  the Internal
Model Control (lambda) rule gives a PI controller with NO overshoot:

    Ti = tau
    Kc = tau / (Kp_plant * lambda)

where:
  * Kp_plant = process gain = |Delta_temp / Delta_fan%|  (°C per % PWM),
    measured by  v2_calibrate.py --band  (the "castig de proces" line);
  * lambda   = desired closed-loop time constant. Larger lambda = slower
    but smoother (cleaner graph, no overshoot). Default lambda = tau is a
    safe, well-damped starting point. Use lambda = tau/2 for faster.

A small derivative term (Td = tau/20) is added so the controller stays a
true PID, but it is kept small on purpose: a first-order thermal plant
needs almost no derivative, and a large Kd just amplifies the 1 °C sensor
quantization into PWM jitter.

The error convention in profiles.py is  error = measured - setpoint  with
POSITIVE gains (hotter -> more PWM -> more cooling), so the gains printed
here are positive and drop straight into PID_DEFAULT_K*.

Example:
    python v2_tune_gains.py --tau 38 --gain 0.21
    python v2_tune_gains.py --tau 38 --gain 0.21 --lam 19   # faster
"""

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--tau", type=float, required=True,
                   help="Thermal time constant (s) from v2_model_id.py")
    p.add_argument("--gain", type=float, required=True,
                   help="Process gain |dT/dFan%%| (°C per %%) from --band")
    p.add_argument("--lam", type=float, default=None,
                   help="Closed-loop time constant (s); default = tau "
                        "(conservative, no overshoot)")
    args = p.parse_args()

    tau = args.tau
    g = abs(args.gain)
    lam = args.lam if args.lam is not None else tau
    if g <= 0:
        raise SystemExit("--gain must be > 0")

    kc = tau / (g * lam)          # %/°C
    ti = tau                      # s
    ki = kc / ti                  # %/(°C·s)
    td = tau / 20.0               # s
    kd = kc * td                  # %·s/°C

    print("\n================ ACORDARE PID (IMC) ================")
    print(f"  intrari: tau={tau:.1f}s  Kp_plant={g:.3f} °C/%  lambda={lam:.1f}s")
    print(f"  Ti = tau           = {ti:.1f} s")
    print(f"  Td = tau/20        = {td:.2f} s")
    print(f"  Kc (Kp)            = {kc:.2f} %/°C")
    print(f"  Ki = Kc/Ti         = {ki:.3f} %/(°C·s)")
    print(f"  Kd = Kc*Td         = {kd:.2f} %·s/°C")
    print("\n  -> Inlocuieste in pc-agent/Backend/profiles.py si reporneste agentul:")
    print(f"     PID_DEFAULT_KP = {kc:.2f}")
    print(f"     PID_DEFAULT_KI = {ki:.3f}")
    print(f"     PID_DEFAULT_KD = {kd:.2f}")
    print("\n  Iterare daca e nevoie (dupa o rulare scurta de proba):")
    print("    - raspuns prea lent  -> scade lambda (ex. tau/2), reruleaza")
    print("    - oscilatii/overshoot-> creste lambda (ex. 1.5*tau), reruleaza")
    print("====================================================")


if __name__ == "__main__":
    main()
