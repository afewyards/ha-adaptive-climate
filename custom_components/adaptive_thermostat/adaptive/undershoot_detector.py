"""Undershoot detector for persistent temperature deficit conditions.

Detects when the heating system is too weak to complete heating cycles,
resulting in persistent undershoot. Tracks time below target and thermal
debt accumulation, recommending Ki increases to improve integral response.
"""
import time
from typing import Optional

from ..const import (
    HeatingType,
    MAX_UNDERSHOOT_KI_MULTIPLIER,
    UNDERSHOOT_THRESHOLDS,
)


class UndershootDetector:
    """Detects persistent undershoot and recommends Ki adjustments.

    When a system is too weak to reach the setpoint, it accumulates thermal debt
    (the integral of temperature error over time). This detector tracks both the
    duration below target and the magnitude of the debt, triggering Ki adjustments
    when thresholds are exceeded.

    Attributes:
        time_below_target: Seconds spent below (setpoint - cold_tolerance).
        thermal_debt: Accumulated temperature debt in °C·hours.
        cumulative_ki_multiplier: Total Ki increases from this detector (starts at 1.0).
        last_adjustment_time: Monotonic time of last adjustment for cooldown enforcement.
        heating_type: Heating system type for threshold lookup.
    """

    def __init__(self, heating_type: HeatingType) -> None:
        """Initialize the undershoot detector.

        Args:
            heating_type: Heating system type for threshold configuration.
        """
        self.heating_type = heating_type
        self.time_below_target: float = 0.0
        self.thermal_debt: float = 0.0
        self.cumulative_ki_multiplier: float = 1.0
        self.last_adjustment_time: Optional[float] = None

    def update(
        self,
        temp: float,
        setpoint: float,
        dt_seconds: float,
        cold_tolerance: float,
    ) -> None:
        """Update detector state with current temperature reading.

        Accumulates time and thermal debt when temperature is below the acceptable
        range (setpoint - cold_tolerance). Resets counters when temperature rises
        above setpoint. Holds state when within tolerance band.

        Args:
            temp: Current temperature in °C.
            setpoint: Target temperature in °C.
            dt_seconds: Time elapsed since last update in seconds.
            cold_tolerance: Acceptable temperature deficit in °C.
        """
        error = setpoint - temp

        if error > cold_tolerance:
            # Below acceptable range - accumulate time and debt
            self.time_below_target += dt_seconds
            # Convert to °C·hours for debt accumulation
            self.thermal_debt += error * (dt_seconds / 3600.0)
            # Cap thermal debt to prevent runaway
            self.thermal_debt = min(self.thermal_debt, 10.0)
        elif error < 0:
            # Above setpoint - full reset
            self.reset()
        # else: within tolerance band (0 <= error <= cold_tolerance) - hold state

    def should_adjust_ki(self, cycles_completed: int) -> bool:
        """Check if Ki adjustment should be triggered.

        Adjustment is triggered when:
        1. No complete cycles yet (normal learning hasn't started)
        2. Not in cooldown period
        3. Cumulative multiplier below safety cap
        4. Either time or debt threshold exceeded

        Args:
            cycles_completed: Number of complete heating cycles observed.

        Returns:
            True if Ki adjustment should be applied.
        """
        # Normal learning handles adjustments after first cycle completes
        if cycles_completed > 0:
            return False

        # Enforce cooldown between adjustments
        if self._in_cooldown():
            return False

        # Respect cumulative safety cap
        if self.cumulative_ki_multiplier >= MAX_UNDERSHOOT_KI_MULTIPLIER:
            return False

        # Check thresholds
        thresholds = UNDERSHOOT_THRESHOLDS[self.heating_type]
        time_threshold_seconds = thresholds["time_threshold_hours"] * 3600.0
        debt_threshold = thresholds["debt_threshold"]

        return (
            self.time_below_target >= time_threshold_seconds
            or self.thermal_debt >= debt_threshold
        )

    def get_adjustment(self) -> float:
        """Get the Ki multiplier for this heating type.

        Returns the configured ki_multiplier, clamped to respect the cumulative
        safety cap (MAX_UNDERSHOOT_KI_MULTIPLIER).

        Returns:
            Ki multiplier to apply (e.g., 1.15 for 15% increase).
        """
        thresholds = UNDERSHOOT_THRESHOLDS[self.heating_type]
        multiplier = thresholds["ki_multiplier"]

        # Clamp to respect cumulative cap
        max_allowed = MAX_UNDERSHOOT_KI_MULTIPLIER / self.cumulative_ki_multiplier
        return min(multiplier, max_allowed)

    def apply_adjustment(self) -> float:
        """Apply the adjustment and update internal state.

        Updates cumulative multiplier, records adjustment time, and performs
        partial debt reset (50% reduction) to allow continued monitoring without
        immediate re-triggering.

        Returns:
            The multiplier that was applied.
        """
        multiplier = self.get_adjustment()

        # Update cumulative multiplier
        self.cumulative_ki_multiplier *= multiplier

        # Record adjustment time for cooldown enforcement
        self.last_adjustment_time = time.monotonic()

        # Partial debt reset - continue monitoring but reduce debt by 50%
        self.thermal_debt *= 0.5

        return multiplier

    def _in_cooldown(self) -> bool:
        """Check if detector is in cooldown period.

        Cooldown prevents rapid-fire adjustments by enforcing a minimum time
        interval between Ki increases.

        Returns:
            True if in cooldown period, False otherwise.
        """
        if self.last_adjustment_time is None:
            return False

        thresholds = UNDERSHOOT_THRESHOLDS[self.heating_type]
        cooldown_seconds = thresholds["cooldown_hours"] * 3600.0
        elapsed = time.monotonic() - self.last_adjustment_time

        return elapsed < cooldown_seconds

    def reset(self) -> None:
        """Perform full reset of time and debt counters.

        Called when temperature rises above setpoint, indicating the system
        has successfully reached target and undershoot condition has cleared.
        """
        self.time_below_target = 0.0
        self.thermal_debt = 0.0
