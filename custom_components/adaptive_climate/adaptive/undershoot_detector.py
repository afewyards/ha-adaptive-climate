"""Unified undershoot detector for persistent temperature deficit conditions.

Detects when the heating system is too weak to reach the setpoint using two modes:

1. Real-time mode: Tracks time below target and thermal debt accumulation during
   normal operation. Triggers Ki boost when system is persistently below setpoint.

2. Cycle mode: Detects chronic approach failures - consecutive cycles where the
   system never reaches setpoint during rise phase. Indicates insufficient
   integral gain.

Both modes share cumulative multiplier tracking and cooldown enforcement to
prevent runaway integral gain. Neither counts time or cycles with the output
saturated: more Ki cannot help when the output is already at its maximum.
"""

from __future__ import annotations

import logging
from datetime import datetime
from math import exp

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

from ..const import (
    HeatingType,
    MAX_UNDERSHOOT_KI_MULTIPLIER,
    MIN_CYCLES_FOR_LEARNING,
    SEVERE_UNDERSHOOT_MULTIPLIER,
    UNDERSHOOT_SATURATED_CYCLE_SHARE,
    UNDERSHOOT_THRESHOLDS,
    UNDERSHOOT_TBT_DECAY_TAU,
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
        last_adjustment_time: Wall-clock datetime of last adjustment for cooldown (persists across restarts).

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
        self.last_adjustment_time: datetime | None = None

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

    @property
    def consecutive_undershoot_cycles(self) -> int:
        """Get consecutive undershoot cycle count."""
        return self._consecutive_failures

    def update_realtime(
        self,
        temp: float,
        setpoint: float,
        dt_seconds: float,
        cold_tolerance: float,
        saturated: bool = False,
    ) -> None:
        """Update real-time detector state with current temperature reading.

        Accumulates time and thermal debt when temperature is below the acceptable
        range (setpoint - cold_tolerance) and the output is not saturated. Resets
        counters when temperature rises above setpoint. Holds state when within
        tolerance band.

        Args:
            temp: Current temperature in °C.
            setpoint: Target temperature in °C (the effective target the PID controls to).
            dt_seconds: Time elapsed since last update in seconds.
            cold_tolerance: Acceptable temperature deficit in °C.
            saturated: Output is at or near its maximum; decay and reset still run.
        """
        error = setpoint - temp

        # Exponential decay on every call — stale accumulation fades when system is stable.
        # This prevents a one-off cold night from triggering a Ki boost days later.
        # tau is thermal-mass-scaled: floor 4h, radiator 2h, convector 1h, forced_air 30min.
        tau = UNDERSHOOT_TBT_DECAY_TAU[self.heating_type]
        decay = exp(-dt_seconds / tau)
        self._time_below_target *= decay
        self._thermal_debt *= decay

        if error > cold_tolerance and not saturated:
            # Below acceptable range - accumulate time and debt
            self._time_below_target += dt_seconds
            # Convert to °C·hours for debt accumulation
            self._thermal_debt += error * (dt_seconds / 3600.0)
            # Cap thermal debt per heating type: 2 × SEVERE multiplier × threshold
            # (e.g. floor_hydronic: 2 × 2.0 × 2.0 = 8.0 °C·h; forced_air: 2 × 2.0 × 0.5 = 2.0 °C·h)
            debt_cap = SEVERE_UNDERSHOOT_MULTIPLIER * 2.0 * self._thresholds["debt_threshold"]
            self._thermal_debt = min(self._thermal_debt, debt_cap)
        elif error < 0:
            # Above setpoint - full reset (decay already applied above, explicit reset clears remainder)
            self.reset_realtime()
        # else: within tolerance band (0 <= error <= cold_tolerance) - decay only, no accumulation

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
        cycle_duration_minutes: float | None = None,
    ) -> None:
        """Add a cycle for chronic approach failure detection.

        A cycle "fails" when:
        - rise_time is None and overshoot is None (never reached setpoint)
        - undershoot >= threshold (significant gap remains)
        - output saturated for less than half the samples (more Ki could help)
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
            # Reset only on a *clean* successful cycle: reached setpoint AND acceptable undershoot.
            # A cycle that barely crossed the setpoint but with large undershoot still indicates
            # chronic heating weakness — don't clear the failure streak.
            undershoot_threshold = self._thresholds["undershoot_threshold"]
            clean_success = cycle.rise_time is not None and (
                cycle.undershoot is None or cycle.undershoot < undershoot_threshold
            )
            if clean_success and self._consecutive_failures > 0:
                _LOGGER.debug(
                    "Cycle reached setpoint cleanly (undershoot=%.2f°C < %.2f°C threshold), "
                    "resetting consecutive failures counter",
                    cycle.undershoot or 0.0,
                    undershoot_threshold,
                )
                self._consecutive_failures = 0

    def _is_chronic_approach_failure(
        self,
        cycle: CycleMetrics,
        cycle_duration_minutes: float | None = None,
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

        # Crossed target at all (can't be stuck below if you reached it)
        if cycle.overshoot is not None:
            return False

        # More Ki cannot help an output that was already at its maximum
        if (cycle.output_saturated or 0.0) >= UNDERSHOOT_SATURATED_CYCLE_SHARE:
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
        last_history_adjustment_utc: datetime | None = None,
        current_ki: float | None = None,
        physics_baseline_ki: float | None = None,
    ) -> bool:
        """Check if Ki adjustment should be triggered by either mode.

        Shared gates (checked first):
        1. Not in cooldown period
        2. Actual Ki ratio below safety cap (physics-based)

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
            current_ki: Current Ki gain value (for physics-based cap check).
            physics_baseline_ki: Physics-baseline Ki value (for physics-based cap check).

        Returns:
            True if Ki adjustment should be applied.
        """
        # Shared gate: Enforce cooldown between adjustments
        if self._in_cooldown(last_history_adjustment_utc):
            return False

        # Shared gate: Respect cumulative safety cap
        # Always check cumulative multiplier (tracks total boosts across restarts)
        if self.cumulative_ki_multiplier >= MAX_UNDERSHOOT_KI_MULTIPLIER:
            return False

        # Additional check: physics-based cap when baseline is available
        if current_ki is not None and physics_baseline_ki is not None and physics_baseline_ki > 0:
            actual_ratio = current_ki / physics_baseline_ki
            if actual_ratio >= MAX_UNDERSHOOT_KI_MULTIPLIER:
                return False

        # H14: Guard against no-op adjustments at/near cap.
        # When cumulative is just under 3.0 (floating-point), get_adjustment() can return
        # ~1.0 → apply_adjustment records cooldown and resets state with zero actual Ki change.
        # Treat any effective multiplier ≤ 1.001 as "already at cap".
        _NO_OP_EPS = 0.001
        effective_multiplier = self.get_adjustment(current_ki, physics_baseline_ki)
        if effective_multiplier <= 1.0 + _NO_OP_EPS:
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

        return self._time_below_target >= time_threshold_seconds or self._thermal_debt >= debt_threshold

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

    def get_adjustment(
        self,
        current_ki: float | None = None,
        physics_baseline_ki: float | None = None,
    ) -> float:
        """Get the Ki multiplier for this heating type.

        Returns the configured ki_multiplier, clamped so neither the cumulative
        multiplier nor, when current_ki and physics_baseline_ki are provided, the
        actual Ki ratio vs the physics baseline passes the safety cap.

        Args:
            current_ki: Current Ki gain value (for physics-based cap calculation).
            physics_baseline_ki: Physics-baseline Ki value (for physics-based cap calculation).

        Returns:
            Ki multiplier to apply (e.g., 1.20 for 20% increase).
        """
        multiplier = self._thresholds["ki_multiplier"]

        max_allowed = MAX_UNDERSHOOT_KI_MULTIPLIER / self.cumulative_ki_multiplier
        if current_ki is not None and physics_baseline_ki is not None and physics_baseline_ki > 0:
            actual_ratio = current_ki / physics_baseline_ki
            max_allowed = min(max_allowed, MAX_UNDERSHOOT_KI_MULTIPLIER / actual_ratio)
        return min(multiplier, max_allowed)

    def apply_adjustment(
        self,
        current_ki: float | None = None,
        physics_baseline_ki: float | None = None,
    ) -> float:
        """Apply the adjustment and update internal state for both modes.

        Updates cumulative multiplier, records adjustment time, and resets
        both real-time state (with partial debt reset) and cycle state.

        Args:
            current_ki: Current Ki gain value (for physics-based cap calculation).
            physics_baseline_ki: Physics-baseline Ki value (for physics-based cap calculation).

        Returns:
            The multiplier that was applied.
        """
        # C08: Clamp multiplier ≥ 1.0 — get_adjustment() can return ≤ 0 when cumulative
        # is corrupt (e.g. bad restore produces negative max_allowed).  Applying a negative
        # multiplier would flip cumulative negative and permanently break the cap gate.
        multiplier = max(1.0, self.get_adjustment(current_ki, physics_baseline_ki))

        # Update shared cumulative multiplier, clamped to [1.0, cap] for safety
        self.cumulative_ki_multiplier = min(
            MAX_UNDERSHOOT_KI_MULTIPLIER,
            max(1.0, self.cumulative_ki_multiplier * multiplier),
        )

        # Record adjustment time for cooldown enforcement (wall-clock, survives restarts)
        self.last_adjustment_time = dt_util.utcnow()

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
        last_history_adjustment_utc: datetime | None = None,
    ) -> bool:
        """Check if detector is in cooldown period.

        Cooldown prevents rapid-fire adjustments by enforcing a minimum time
        interval between Ki increases. Uses wall-clock datetime for both in-session
        and cross-restart checks.

        Args:
            last_history_adjustment_utc: Timestamp of last Ki adjustment from PID history
                (fallback for backward compat when last_adjustment_time is None).

        Returns:
            True if in cooldown period, False otherwise.
        """
        cooldown_seconds = self._thresholds["cooldown_hours"] * 3600.0
        now = dt_util.utcnow()

        # Check in-session / persisted wall-clock timestamp (survives restarts via C09)
        if self.last_adjustment_time is not None:
            elapsed = (now - self.last_adjustment_time).total_seconds()
            if elapsed < cooldown_seconds:
                return True

        # Fallback: check history datetime for backward compat (pre-C09 restores)
        if last_history_adjustment_utc is not None:
            elapsed = (now - last_history_adjustment_utc).total_seconds()
            if elapsed < cooldown_seconds:
                remaining_hours = (cooldown_seconds - elapsed) / 3600.0
                _LOGGER.debug(
                    "Undershoot Ki boost blocked by history cooldown: last boost was %.1fh ago, %.1fh remaining",
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

    def reset_all(self) -> None:
        """Perform complete reset of all undershoot detector state.

        Called when learning is cleared to ensure fresh start. Resets:
        - Real-time counters (time_below_target, thermal_debt)
        - Cycle mode counters (consecutive_failures)
        - Shared state (cumulative_ki_multiplier, last_adjustment_time)
        """
        # Real-time mode
        self._time_below_target = 0.0
        self._thermal_debt = 0.0
        # Cycle mode
        self._consecutive_failures = 0
        # Shared state
        self.cumulative_ki_multiplier = 1.0
        self.last_adjustment_time = None
