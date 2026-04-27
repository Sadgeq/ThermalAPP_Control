"""
Profile Engine
==============
Fan speed profiles with temperature-based curves.
Linear interpolation + hysteresis to prevent oscillation.

FIXES:
  - Safe JSON parsing with error handling
  - Input validation on profile data
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("Profiles")

HYSTERESIS_DEGREES = 2.0


class ProfileEngine:

    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.active_profile: Optional[str] = None
        self._last_speed: float = 0.0

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

                self.profiles[name] = {
                    "id": p.get("id", ""),
                    "name": name,
                    "fan_curve": sorted(validated_curve, key=lambda x: x["temp"]),
                    "is_active": bool(p.get("is_active", False)),
                }
                if p.get("is_active"):
                    self.active_profile = name

            except Exception as e:
                logger.error(f"Error loading profile: {e}", exc_info=True)

        logger.info(
            f"Profiles: {list(self.profiles.keys())} | Active: {self.active_profile}"
        )

    def set_active(self, profile_name: Optional[str]):
        if profile_name and profile_name not in self.profiles:
            logger.warning(f"Cannot activate unknown profile: {profile_name}")
            return
        self.active_profile = profile_name
        self._last_speed = 0.0
        if profile_name:
            logger.info(f"Profile activated: {profile_name}")
        else:
            logger.info("Manual mode (no profile)")

    def calculate_fan_speeds(self, current_temp: float, fan_count: int) -> dict:
        """Returns {fan_index: speed_percent} for all fans.
        Uses hysteresis to prevent oscillation."""
        if not self.active_profile or self.active_profile not in self.profiles:
            return {}

        curve = self.profiles[self.active_profile]["fan_curve"]
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