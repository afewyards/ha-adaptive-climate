"""Unified undershoot detector for persistent temperature deficit conditions.

Detects when the heating system is too weak to reach the setpoint using two modes:

1. Real-time mode: Tracks time below target and thermal debt accumulation during
   normal operation. Triggers Ki boost when system is persistently below setpoint.

2. Cycle mode: Detects chronic approach failures - consecutive cycles where the
   system never reaches setpoint during rise phase. Indicates insufficient
   integral gain.

Both modes share cumulative multiplier tracking and cooldown enforcement to
prevent runaway integral gain.
"""
import logging
import time
from datetime import datetime
from typing import Optional

_LOGGER = logging.getLogger(__name__)

from ..const import (
    HeatingType,
    MAX_UNDERSHOOT_KI_MULTIPLIER,
    MIN_CYCLES_FOR_LEARNING,
    SEVERE_UNDERSHOOT_MULTIPLIER,
    UNDERSHOOT_THRESHOLDS,
)
from .cycle_analysis import CycleMetrics


class UndershootDetector:
    """Unified detector for persistent undershoot using real-time and cycle modes.

    Real-time mode tracks thermal debt accumulation (integral of error over time)
    and time below target. Useful during bootstrap or severe undershoot scenarios.

    Cycle mode detects chronic approach failures - consecutive cycles failing to
    reach setpoint during rise phase. Indicates persistent heating capacity issues.

    Shared state:
        cumulative_ki_multiplier: Total Ki increases from both modes (starts at 1.0).
        last_adjustment_time: Monotonic time of last adjustment for cooldown.

    Real-time state:
        time_below_target: Seconds spent below (setpoint - cold_tolerance).
        thermal_debt: Accumulated temperature debt in °C·hours.

    Cycle state:
        consecutive_failures: Count of consecutive approach failures.
    """

    def __init__(self, heating_type: HeatingType) -> None:
        """Initialize the unified undershoot detector.

        Args:
            heating_type: Heating system type for threshold configuration.
        """
        self.heating_type = heating_type
        self._thresholds = UNDERSHOOT_THRESHOLDS[heating_type]

        # Shared state
        self.cumulative_ki_multiplier: float = 1.0
        self.last_adjustment_time: Optional[float] = None

        # Real-time mode state
        self._time_below_target: float = 0.0
        self._thermal_debt: float = 0.0

        # Cycle mode state
        self._consecutive_failures: int = 0

    @property
    def time_below_target(self) -> float:
        """Get time below target (for backward compatibility)."""
        return self._time_below_target

    @time_below_target.setter
    def time_below_target(self, value: float) -> None:
        """Set time below target (for backward compatibility)."""
        self._time_below_target = value

    @property
    def thermal_debt(self) -> float:
        """Get thermal debt (for backward compatibility)."""
        return self._thermal_debt

    @thermal_debt.setter
    def thermal_debt(self, value: float) -> None:
        """Set thermal debt (for backward compatibility)."""
        self._thermal_debt = value

    def update_realtime(
        self,
        temp: float,
        setpoint: float,
        dt_seconds: float,
        cold_tolerance: float,
    ) -> None:
        """Update real-time detector state with current temperature reading.

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
            self._time_below_target += dt_seconds
            # Convert to °C·hours for debt accumulation
            self._thermal_debt += error * (dt_seconds / 3600.0)
            # Cap thermal debt to prevent runaway
            self._thermal_debt = min(self._thermal_debt, 10.0)
        elif error < 0:
            # Above setpoint - full reset
            self.reset_realtime()
        # else: within tolerance band (0 <= error <= cold_tolerance) - hold state

    def update(
        self,
        temp: float,
        setpoint: float,
        dt_seconds: float,
        cold_tolerance: float,
    ) -> None:
        """Update detector state (legacy compatibility wrapper for update_realtime).

        Args:
            temp: Current temperature in °C.
            setpoint: Target temperature in °C.
            dt_seconds: Time elapsed since last update in seconds.
            cold_tolerance: Acceptable temperature deficit in °C.
        """
        self.update_realtime(temp, setpoint, dt_seconds, cold_tolerance)

    def add_cycle(
        self,
        cycle: CycleMetrics,
        cycle_duration_minutes: Optional[float] = None,
    ) -> None:
        """Add a cycle for chronic approach failure detection.

        A cycle "fails" when:
        - rise_time is None (never reached setpoint during rise)
        - undershoot >= threshold (significant gap remains)
        - duration >= min_duration (avoid short transient cycles)

        Consecutive failures trigger Ki boost. Counter resets on any successful cycle.

        Args:
            cycle: The cycle metrics to analyze.
            cycle_duration_minutes: Duration of the cycle in minutes (optional).
        """
        is_failure = self._is_chronic_approach_failure(cycle, cycle_duration_minutes)

        if is_failure:
            # Increment consecutive failure count
            self._consecutive_failures += 1
            _LOGGER.debug(
                "Chronic approach failure detected: consecutive=%d, undershoot=%.2f°C",
                self._consecutive_failures,
                cycle.undershoot or 0.0,
            )
        else:
            # Reset on any successful cycle (has rise_time)
            if cycle.rise_time is not None and self._consecutive_failures > 0:
                _LOGGER.debug(
                    "Cycle reached setpoint, resetting consecutive failures counter"
                )
                self._consecutive_failures = 0

    def _is_chronic_approach_failure(
        self,
        cycle: CycleMetrics,
        cycle_duration_minutes: Optional[float] = None,
    ) -> bool:
        """Check if cycle meets chronic approach failure criteria.

        Args:
            cycle: The cycle metrics to check.
            cycle_duration_minutes: Duration of the cycle in minutes.

        Returns:
            True if cycle represents chronic approach failure.
        """
        # Must not have reached setpoint during rise
        if cycle.rise_time is not None:
            return False

        # Must have significant undershoot
        undershoot_threshold = self._thresholds["undershoot_threshold"]
        if cycle.undershoot is None or cycle.undershoot < undershoot_threshold:
            return False

        # Must not have overshoot (can't be stuck below if you went above)
        if cycle.overshoot is not None and cycle.overshoot > 0.0:
            return False

        # Must be a substantial cycle (not a short transient)
        if cycle_duration_minutes is not None:
            min_duration = self._thresholds["min_cycle_duration"]
            if cycle_duration_minutes < min_duration:
                return False

        return True

    def should_adjust_ki(
        self,
        cycles_completed: int,
        last_history_adjustment_utc: Optional[datetime] = None,
    ) -> bool:
        """Check if Ki adjustment should be triggered by either mode.

        Shared gates (checked first):
        1. Not in cooldown period
        2. Cumulative multiplier below safety cap

        Real-time mode triggers when:
        1. Either no complete cycles yet (bootstrap), OR severe undershoot detected
           (thermal_debt >= 2x threshold) after MIN_CYCLES_FOR_LEARNING
        2. Either time or debt threshold exceeded

        Cycle mode triggers when:
        1. Have MIN_CYCLES_FOR_LEARNING complete cycles
        2. Consecutive failures >= min_consecutive_cycles threshold

        Args:
            cycles_completed: Number of complete heating cycles observed.
            last_history_adjustment_utc: Timestamp of last Ki adjustment from PID history.

        Returns:
            True if Ki adjustment should be applied.
        """
        # Shared gate: Enforce cooldown between adjustments
        if self._in_cooldown(last_history_adjustment_utc):
            return False

        # Shared gate: Respect cumulative safety cap
        if self.cumulative_ki_multiplier >= MAX_UNDERSHOOT_KI_MULTIPLIER:
            return False

        # Check real-time mode
        realtime_triggered = self._check_realtime_mode(cycles_completed)

        # Check cycle mode
        cycle_triggered = self._check_cycle_mode(cycles_completed)

        return realtime_triggered or cycle_triggered

    def _check_realtime_mode(self, cycles_completed: int) -> bool:
        """Check if real-time mode should trigger.

        Args:
            cycles_completed: Number of complete heating cycles observed.

        Returns:
            True if real-time mode thresholds met.
        """
        debt_threshold = self._thresholds["debt_threshold"]

        # Check for severe undershoot (2x threshold) - allows persistent mode
        severe_undershoot = self._thermal_debt >= debt_threshold * SEVERE_UNDERSHOOT_MULTIPLIER

        # Let normal learning handle if it has enough cycles AND undershoot is not severe
        # Stay active if: no cycles yet OR severe undershoot after min learning cycles
        if cycles_completed >= MIN_CYCLES_FOR_LEARNING and not severe_undershoot:
            return False

        # Check thresholds
        time_threshold_seconds = self._thresholds["time_threshold_hours"] * 3600.0

        return (
            self._time_below_target >= time_threshold_seconds
            or self._thermal_debt >= debt_threshold
        )

    def _check_cycle_mode(self, cycles_completed: int) -> bool:
        """Check if cycle mode should trigger.

        Args:
            cycles_completed: Number of complete heating cycles observed.

        Returns:
            True if cycle mode thresholds met.
        """
        # Need minimum cycles for learning before cycle mode activates
        if cycles_completed < MIN_CYCLES_FOR_LEARNING:
            return False

        # Check if we have enough consecutive failures
        min_consecutive = self._thresholds["min_consecutive_cycles"]
        return self._consecutive_failures >= min_consecutive

    def get_adjustment(self) -> float:
        """Get the Ki multiplier for this heating type.

        Returns the configured ki_multiplier, clamped to respect the cumulative
        safety cap (MAX_UNDERSHOOT_KI_MULTIPLIER).

        Returns:
            Ki multiplier to apply (e.g., 1.20 for 20% increase).
        """
        multiplier = self._thresholds["ki_multiplier"]

        # Clamp to respect cumulative cap
        max_allowed = MAX_UNDERSHOOT_KI_MULTIPLIER / self.cumulative_ki_multiplier
        return min(multiplier, max_allowed)

    def apply_adjustment(self) -> float:
        """Apply the adjustment and update internal state for both modes.

        Updates cumulative multiplier, records adjustment time, and resets
        both real-time state (with partial debt reset) and cycle state.

        Returns:
            The multiplier that was applied.
        """
        multiplier = self.get_adjustment()

        # Update shared cumulative multiplier
        self.cumulative_ki_multiplier *= multiplier

        # Record adjustment time for cooldown enforcement
        self.last_adjustment_time = time.monotonic()

        # Reset both modes
        # Real-time: Partial debt reset - continue monitoring but reduce debt by 50%
        self._thermal_debt *= 0.5
        # Note: time_below_target is NOT reset to allow continued accumulation

        # Cycle mode: Reset consecutive failures counter
        self._consecutive_failures = 0

        _LOGGER.info(
            "Applied undershoot Ki adjustment: %.3fx (cumulative: %.3fx)",
            multiplier,
            self.cumulative_ki_multiplier,
        )

        return multiplier

    def _in_cooldown(
        self,
        last_history_adjustment_utc: Optional[datetime] = None,
    ) -> bool:
        """Check if detector is in cooldown period.

        Cooldown prevents rapid-fire adjustments by enforcing a minimum time
        interval between Ki increases. Checks both monotonic time (within-session)
        and history datetime (cross-restart).

        Args:
            last_history_adjustment_utc: Timestamp of last Ki adjustment from PID history.

        Returns:
            True if in cooldown period, False otherwise.
        """
        cooldown_seconds = self._thresholds["cooldown_hours"] * 3600.0

        # Check monotonic (within-session)
        if self.last_adjustment_time is not None:
            elapsed = time.monotonic() - self.last_adjustment_time
            if elapsed < cooldown_seconds:
                return True

        # Check history datetime (cross-restart)
        if last_history_adjustment_utc is not None:
            from datetime import timezone
            elapsed = (datetime.now(timezone.utc) - last_history_adjustment_utc).total_seconds()
            if elapsed < cooldown_seconds:
                remaining_hours = (cooldown_seconds - elapsed) / 3600.0
                _LOGGER.debug(
                    "Undershoot Ki boost blocked by history cooldown: "
                    "last boost was %.1fh ago, %.1fh remaining",
                    elapsed / 3600.0,
                    remaining_hours,
                )
                return True

        return False

    def reset_realtime(self) -> None:
        """Perform full reset of real-time mode counters.

        Called when temperature rises above setpoint, indicating the system
        has successfully reached target and undershoot condition has cleared.
        """
        self._time_below_target = 0.0
        self._thermal_debt = 0.0

    def reset(self) -> None:
        """Perform full reset of real-time counters (legacy compatibility).

        Called when temperature rises above setpoint, indicating the system
        has successfully reached target and undershoot condition has cleared.
        """
        self.reset_realtime()
