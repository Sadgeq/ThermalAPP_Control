"""
Profile Engine
==============
Fan-speed profiles with two modes:

  * Open-loop curve mode (legacy):
    Each profile stores a temperature → fan-percent curve. On each control
    tick we linearly interpolate the curve at the current temperature and
    apply the resulting PWM. Hysteresis avoids oscillation when temperature
    bounces around a curve breakpoint.

  * Closed-loop PID mode (new):
    A profile with `target_temp` set runs a discrete-time PID controller
    that drives the CPU temperature toward that setpoint. The controller's
    output is a fan PWM in [0, 100]. Used for the bachelor's thesis
    "reglare automată" requirement — the mobile app picks the setpoint,
    the agent closes the loop.

The two modes share the same dispatch surface (`calculate_fan_speeds`) so
agent.py doesn't have to know which one a profile uses.

FIXES:
  - Safe JSON parsing with error handling
  - Input validation on profile data
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("Profiles")

HYSTERESIS_DEGREES = 2.0

# PID gains, tuned manually for a Lenovo Legion 5 82NL on a sustained
# Cinebench load. Will be re-tuned during the empirical-validation phase
# (see Backend/scripts/fit_first_order.py + the thesis chapter on PID
# tuning); these are reasonable starting values.
#
# Convention: positive temperature error (measured > setpoint) drives PWM
# upward (fans spin faster to cool). So gains are *positive* — note that
# in the step function we compute `error = measured - setpoint`, not the
# more conventional `setpoint - measured`, to keep the gains intuitive.
PID_DEFAULT_KP = 4.0     # %/°C — proportional gain
PID_DEFAULT_KI = 0.4     # %/(°C·s) — integral gain
PID_DEFAULT_KD = 8.0     # %·s/°C — derivative gain
# Anti-windup: cap the integral so its KI-scaled contribution never exceeds
# ±60 PWM percent. The cap is computed *per instance* from that instance's
# KI in the constructor — making it a module-level constant breaks when a
# caller passes custom gains. Without the cap, a saturated system
# accumulates integral forever and overshoots wildly on recovery.
PID_INTEGRAL_PWM_BUDGET = 60.0


class PidController:
    """Discrete-time PID for "hold the CPU at this temperature."

    State:
        setpoint        target temperature in °C
        last_error      previous-tick error, for the derivative term
        integral        accumulated error * dt, clamped via anti-windup
        last_pwm        the last command we issued (for warm-start when the
                        controller is recreated on profile activation)

    Output:
        PWM percent in [0, 100], clamped at the saturation limits.
    """

    def __init__(
        self, setpoint: float,
        kp: float = PID_DEFAULT_KP,
        ki: float = PID_DEFAULT_KI,
        kd: float = PID_DEFAULT_KD,
        warm_start_pwm: float = 30.0,
    ):
        self.setpoint = float(setpoint)
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        # Anti-windup limit scaled to this instance's ki: integral * ki
        # can contribute at most ±PID_INTEGRAL_PWM_BUDGET PWM percent.
        # Falls back to a generous bound when ki is ~0 (purely PD).
        self.integral_limit: float = (
            PID_INTEGRAL_PWM_BUDGET / self.ki if self.ki > 1e-6 else 1e9
        )
        self.last_error: Optional[float] = None
        self.integral: float = 0.0
        self.last_pwm: float = float(warm_start_pwm)

    def reset(self) -> None:
        """Forget integral history. Called when the user changes setpoint
        or re-activates the profile, so the controller starts clean."""
        self.last_error = None
        self.integral = 0.0

    def step(self, measured: float, dt: float) -> float:
        """Run one control tick. Returns PWM percent in [0, 100].

        `measured` is the current CPU temperature in °C. `dt` is seconds
        since the last call (typically the agent's polling_interval, 2s).

        Anti-windup uses *conditional integration*: the integral is only
        updated when doing so wouldn't push the actuator deeper into a
        saturation it's already in. Without this, a long warm-up phase
        (CPU climbs from idle to setpoint while error is large negative
        and PWM is clamped at 0%) lets the integral accumulate strongly
        negative; then when temperature finally crosses the setpoint
        the regulator stays "stuck" at zero output for many seconds
        while the integral discharges. The classical fix from Åström &
        Hägglund (1995) is to skip the integration step entirely while
        the actuator is saturated in the same direction as the error.
        """
        if dt <= 0:
            # Defensive: agent loop guarantees dt > 0, but if something
            # ever passes 0 we bail to the last command instead of NaNing.
            return self.last_pwm

        error = measured - self.setpoint

        # Derivative on error (not on measurement) — simpler, but has
        # setpoint-kick on a step setpoint change. We mitigate by
        # calling reset() when the user changes setpoint, so the kick
        # only happens once per setpoint edit.
        if self.last_error is None:
            derivative = 0.0
        else:
            derivative = (error - self.last_error) / dt
        self.last_error = error

        # Conditional integration based on the *current* tick's would-be
        # output (a "trial" computation without updating the integral
        # first), not on the previous tick's result. This is robust to
        # the warm_start_pwm initial value: even on the very first step,
        # if the trial output is saturated and the error pushes deeper
        # into saturation, we skip the integration. Using last_pwm
        # instead would let the first step accumulate negative wind-up
        # because warm_start_pwm = 30 != 0.
        trial_raw = (
            self.kp * error
            + self.ki * self.integral
            + self.kd * derivative
        )
        trial_pwm = max(0.0, min(100.0, trial_raw))

        saturated_low  = trial_pwm <= 1e-3
        saturated_high = trial_pwm >= 100.0 - 1e-3
        skip_integration = (
            (saturated_low and error < 0.0)
            or (saturated_high and error > 0.0)
        )

        if not skip_integration:
            self.integral += error * dt
            # Safety clamp on |integral|. With conditional integration
            # this is rarely hit, but kept as a belt-and-suspenders
            # bound so an unexpected runaway can't produce a nonsensical
            # command.
            if self.integral > self.integral_limit:
                self.integral = self.integral_limit
            elif self.integral < -self.integral_limit:
                self.integral = -self.integral_limit

            # Recompute output with the freshly updated integral. When
            # we skipped the integration, trial_raw / trial_pwm are
            # already correct.
            raw = (
                self.kp * error
                + self.ki * self.integral
                + self.kd * derivative
            )
            pwm = max(0.0, min(100.0, raw))
        else:
            pwm = trial_pwm

        self.last_pwm = pwm
        return pwm


class ProfileEngine:

    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.active_profile: Optional[str] = None
        self._last_speed: float = 0.0
        # Per-active-profile PID. Created when a target_temp profile is
        # activated; reset/destroyed on profile change so each session
        # starts with a clean integral.
        self._pid: Optional[PidController] = None
        # Throttle PID INFO logs to roughly one per N control ticks so the
        # log is readable while still showing the controller in action.
        # Counter resets at activation.
        self._pid_log_tick: int = 0

    def load_profiles(self, profiles_data: list[dict]):
        self.profiles.clear()
        for p in profiles_data:
            try:
                name = str(p.get("name", "")).strip()
                if not name:
                    logger.warning(f"Skipping profile with empty name")
                    continue

                curve = p.get("fan_curve", [])
                if isinstance(curve, str):
                    # FIX: Safe JSON parsing with error handling
                    try:
                        curve = json.loads(curve)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"Invalid fan_curve JSON for profile '{name}', using empty curve")
                        curve = []

                # FIX: Validate curve structure
                if not isinstance(curve, list):
                    logger.warning(f"fan_curve for '{name}' is not a list, using empty curve")
                    curve = []

                validated_curve = []
                for point in curve:
                    if isinstance(point, dict) and "temp" in point and "speed" in point:
                        try:
                            validated_curve.append({
                                "temp": max(0, min(120, float(point["temp"]))),
                                "speed": max(0, min(100, float(point["speed"]))),
                            })
                        except (TypeError, ValueError):
                            continue
                    else:
                        logger.warning(f"Skipping invalid curve point in '{name}': {point}")

                # fan_mode is the Lenovo Legion BIOS mode (1/2/3) the agent
                # flips on activation. None means "leave BIOS alone."
                raw_mode = p.get("fan_mode")
                fan_mode: Optional[int] = None
                if raw_mode is not None:
                    try:
                        m = int(raw_mode)
                        if m in (1, 2, 3):
                            fan_mode = m
                    except (TypeError, ValueError):
                        logger.warning(f"Invalid fan_mode for '{name}': {raw_mode}")

                # target_temp is the PID setpoint in °C. None = curve mode
                # (legacy). Validated against the same [40, 95] band the
                # SQL CHECK constraint enforces — we re-validate here so a
                # corrupt cloud row can't crash the agent.
                raw_target = p.get("target_temp")
                target_temp: Optional[float] = None
                if raw_target is not None:
                    try:
                        t = float(raw_target)
                        if 40.0 <= t <= 95.0:
                            target_temp = t
                        else:
                            logger.warning(
                                f"target_temp {t} out of range [40, 95] for "
                                f"profile '{name}'; ignoring"
                            )
                    except (TypeError, ValueError):
                        logger.warning(f"Invalid target_temp for '{name}': {raw_target}")

                self.profiles[name] = {
                    "id": p.get("id", ""),
                    "name": name,
                    "fan_curve": sorted(validated_curve, key=lambda x: x["temp"]),
                    "fan_mode": fan_mode,
                    "target_temp": target_temp,
                    "is_active": bool(p.get("is_active", False)),
                }
            except Exception as e:
                logger.error(f"Error loading profile: {e}", exc_info=True)

        # Find which profile (if any) was marked active in the source data
        # and activate it through set_active() so per-profile state (PID
        # controller for target-mode profiles, etc.) is properly initialized.
        # Doing this in a second pass means the active profile must already
        # exist in self.profiles by the time we look it up.
        active_name = next(
            (str(p.get("name", "")).strip() for p in profiles_data
             if p.get("is_active") and p.get("name")),
            None,
        )
        if active_name and active_name in self.profiles:
            self.set_active(active_name)
        else:
            self.active_profile = None
            self._pid = None

        logger.info(
            f"Profiles: {list(self.profiles.keys())} | Active: {self.active_profile}"
        )

    def set_active(self, profile_name: Optional[str]):
        if profile_name and profile_name not in self.profiles:
            logger.warning(f"Cannot activate unknown profile: {profile_name}")
            return
        self.active_profile = profile_name
        self._last_speed = 0.0
        self._pid_log_tick = 0

        # (Re)create the PID controller iff the new active profile has a
        # setpoint. Switching FROM a PID profile TO a curve profile drops
        # the controller; switching between two PID profiles starts fresh
        # so integral history from the old setpoint doesn't leak.
        if profile_name:
            target = self.profiles[profile_name].get("target_temp")
            if target is not None:
                self._pid = PidController(
                    setpoint=float(target),
                    warm_start_pwm=self._last_speed or 30.0,
                )
                logger.info(
                    f"Profile activated: {profile_name} "
                    f"(PID mode, target={target:.1f}°C)"
                )
            else:
                self._pid = None
                logger.info(f"Profile activated: {profile_name} (curve mode)")
        else:
            self._pid = None
            logger.info("Manual mode (no profile)")

    def calculate_fan_speeds(
        self, current_temp: float, fan_count: int, dt: float = 2.0,
    ) -> dict:
        """Returns {fan_index: speed_percent} for all fans.

        Dispatches on the active profile's mode:
          * PID mode (target_temp set): runs one PID step. dt is the
            seconds since the last call (the agent's polling interval).
          * Curve mode (no target_temp): linear interpolation on the
            fan_curve with hysteresis to prevent oscillation around a
            breakpoint.

        Returns an empty dict when no profile is active or the active
        profile has neither a curve nor a setpoint.
        """
        if not self.active_profile or self.active_profile not in self.profiles:
            return {}

        prof = self.profiles[self.active_profile]

        # PID mode wins if a setpoint is configured. Skip the curve entirely.
        if self._pid is not None and prof.get("target_temp") is not None:
            target = self._pid.step(measured=current_temp, dt=dt)
            self._last_speed = target
            # Periodic INFO so the user can see PID actually running. At a
            # 2s polling interval, every 5 ticks ≈ 10s — quiet enough not
            # to drown the log, frequent enough to debug a setpoint chase.
            self._pid_log_tick += 1
            if self._pid_log_tick % 5 == 0:
                error = current_temp - self._pid.setpoint
                logger.info(
                    f"PID: temp={current_temp:.1f}°C target={self._pid.setpoint:.1f}°C "
                    f"err={error:+.1f} pwm={target:.1f}%"
                )
            return {i: round(target, 1) for i in range(fan_count)}

        # Curve mode (legacy path).
        curve = prof.get("fan_curve") or []
        if not curve:
            return {}

        raw_speed = self._interpolate(curve, current_temp)

        if raw_speed >= self._last_speed:
            target = raw_speed
        else:
            hyst_speed = self._interpolate(curve, current_temp + HYSTERESIS_DEGREES)
            target = max(raw_speed, min(hyst_speed, self._last_speed))

        self._last_speed = target
        return {i: round(target, 1) for i in range(fan_count)}

    @staticmethod
    def _interpolate(curve: list[dict], temp: float) -> float:
        if not curve:
            return 50.0
        if temp <= curve[0]["temp"]:
            return float(curve[0]["speed"])
        if temp >= curve[-1]["temp"]:
            return float(curve[-1]["speed"])

        for i in range(len(curve) - 1):
            t1, s1 = curve[i]["temp"], curve[i]["speed"]
            t2, s2 = curve[i + 1]["temp"], curve[i + 1]["speed"]
            if t1 <= temp <= t2:
                ratio = (temp - t1) / (t2 - t1) if t2 != t1 else 0
                return s1 + ratio * (s2 - s1)

        return float(curve[-1]["speed"])

    def get_profile_names(self) -> list[str]:
        return list(self.profiles.keys())

    def get_active_profile_data(self) -> Optional[dict]:
        if self.active_profile and self.active_profile in self.profiles:
            return self.profiles[self.active_profile]
        return None