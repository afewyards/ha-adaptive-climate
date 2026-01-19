"""Thermal rate learning and adaptive PID adjustments for Adaptive Thermostat."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import statistics
import logging

from ..const import (
    PID_LIMITS,
    MIN_CYCLES_FOR_LEARNING,
    MAX_CYCLE_HISTORY,
    MIN_ADJUSTMENT_INTERVAL,
    MIN_ADJUSTMENT_CYCLES,
    CONVERGENCE_THRESHOLDS,
    MIN_CONVERGENCE_CYCLES_FOR_KE,
    CONVERGENCE_CONFIDENCE_HIGH,
    CONFIDENCE_DECAY_RATE_DAILY,
    CONFIDENCE_INCREASE_PER_GOOD_CYCLE,
    PID_HISTORY_SIZE,
    VALIDATION_CYCLE_COUNT,
    VALIDATION_DEGRADATION_THRESHOLD,
    get_convergence_thresholds,
    get_rule_thresholds,
)

# Import PID rule engine components
from .pid_rules import (
    PIDRule,
    PIDRuleResult,
    RuleStateTracker,
    evaluate_pid_rules,
    detect_rule_conflicts,
    resolve_rule_conflicts,
)

# Import robust statistics for outlier rejection
from .robust_stats import robust_average

# Import and re-export ThermalRateLearner for backward compatibility
from .thermal_rates import ThermalRateLearner

# Import and re-export cycle analysis components for backward compatibility
from .cycle_analysis import (
    PhaseAwareOvershootTracker,
    CycleMetrics,
    calculate_overshoot,
    calculate_undershoot,
    count_oscillations,
    calculate_settling_time,
)

# Import and re-export LearningDataStore for backward compatibility
from .persistence import LearningDataStore

# Import and re-export PWM tuning utilities for backward compatibility
from .pwm_tuning import calculate_pwm_adjustment, ValveCycleTracker

_LOGGER = logging.getLogger(__name__)


# Adaptive learning (CycleMetrics imported from cycle_analysis)


class AdaptiveLearner:
    """Adaptive PID tuning based on observed heating cycle performance."""

    def __init__(self, max_history: int = MAX_CYCLE_HISTORY, heating_type: Optional[str] = None):
        """
        Initialize the AdaptiveLearner.

        Args:
            max_history: Maximum number of cycles to retain in history (FIFO eviction)
            heating_type: Heating system type (floor_hydronic, radiator, convector, forced_air)
                         Used to select appropriate convergence thresholds
        """
        self._cycle_history: List[CycleMetrics] = []
        self._max_history = max_history
        self._heating_type = heating_type
        self._convergence_thresholds = get_convergence_thresholds(heating_type)
        self._rule_thresholds = get_rule_thresholds(heating_type)
        self._last_adjustment_time: Optional[datetime] = None
        # Convergence tracking for Ke learning activation
        self._consecutive_converged_cycles: int = 0
        self._pid_converged_for_ke: bool = False
        # Adaptive convergence confidence tracking
        self._convergence_confidence: float = 0.0
        self._last_seasonal_check: Optional[datetime] = None
        self._outdoor_temp_history: List[float] = []
        self._duty_cycle_history: List[float] = []
        # Hybrid rate limiting: track cycles since last adjustment
        self._cycles_since_last_adjustment: int = 0
        # Rule state tracker with hysteresis to prevent oscillation
        self._rule_state_tracker = RuleStateTracker()

        # Auto-apply tracking state
        self._auto_apply_count: int = 0
        self._last_seasonal_shift: Optional[datetime] = None
        self._pid_history: List[Dict[str, Any]] = []

        # Physics baseline for drift calculation
        self._physics_baseline_kp: Optional[float] = None
        self._physics_baseline_ki: Optional[float] = None
        self._physics_baseline_kd: Optional[float] = None

        # Validation mode state
        self._validation_mode: bool = False
        self._validation_baseline_overshoot: Optional[float] = None
        self._validation_cycles: List[CycleMetrics] = []

    @property
    def cycle_history(self) -> List[CycleMetrics]:
        """Return cycle history for external access."""
        return self._cycle_history

    @cycle_history.setter
    def cycle_history(self, value: List[CycleMetrics]) -> None:
        """Set cycle history (primarily for testing)."""
        self._cycle_history = value

    def add_cycle_metrics(self, metrics: CycleMetrics) -> None:
        """
        Add a cycle's performance metrics to history.

        Implements FIFO eviction when history exceeds max_history.
        Increments cycle counter for hybrid rate limiting.

        Args:
            metrics: CycleMetrics object with performance data
        """
        self._cycle_history.append(metrics)

        # Increment cycle counter for hybrid rate limiting
        self._cycles_since_last_adjustment += 1

        # FIFO eviction: remove oldest entries when exceeding max history
        if len(self._cycle_history) > self._max_history:
            evicted_count = len(self._cycle_history) - self._max_history
            self._cycle_history = self._cycle_history[-self._max_history:]
            _LOGGER.debug(
                f"Cycle history exceeded max ({self._max_history}), "
                f"evicted {evicted_count} oldest entries"
            )

    def get_cycle_count(self) -> int:
        """
        Get number of stored cycle metrics.

        Returns:
            Number of cycles in history
        """
        return len(self._cycle_history)

    def _check_convergence(
        self,
        avg_overshoot: float,
        avg_oscillations: float,
        avg_settling_time: float,
        avg_rise_time: float,
    ) -> bool:
        """
        Check if the system has converged (is well-tuned).

        Convergence occurs when ALL performance metrics are within acceptable bounds
        defined by heating-type-specific thresholds (or default thresholds if heating type unknown).

        Args:
            avg_overshoot: Average overshoot in °C
            avg_oscillations: Average number of oscillations
            avg_settling_time: Average settling time in minutes
            avg_rise_time: Average rise time in minutes

        Returns:
            True if converged, False otherwise
        """
        is_converged = (
            avg_overshoot <= self._convergence_thresholds["overshoot_max"] and
            avg_oscillations <= self._convergence_thresholds["oscillations_max"] and
            avg_settling_time <= self._convergence_thresholds["settling_time_max"] and
            avg_rise_time <= self._convergence_thresholds["rise_time_max"]
        )

        if is_converged:
            _LOGGER.info(
                f"PID convergence detected - system tuned: "
                f"overshoot={avg_overshoot:.2f}°C, oscillations={avg_oscillations:.1f}, "
                f"settling={avg_settling_time:.1f}min, rise={avg_rise_time:.1f}min"
            )

        return is_converged

    def _check_rate_limit(
        self,
        min_interval_hours: int = MIN_ADJUSTMENT_INTERVAL,
        min_cycles: int = MIN_ADJUSTMENT_CYCLES,
    ) -> bool:
        """
        Check if enough time AND cycles have passed since the last PID adjustment.

        Implements hybrid rate limiting with two gates (both must be satisfied):
        1. Time gate: minimum hours between adjustments
        2. Cycle gate: minimum cycles between adjustments

        Args:
            min_interval_hours: Minimum hours between adjustments
            min_cycles: Minimum cycles between adjustments

        Returns:
            True if rate limited (adjustment should be skipped), False if OK to adjust
        """
        # First adjustment - no rate limiting
        if self._last_adjustment_time is None:
            return False

        # Check time gate
        time_since_last = datetime.now() - self._last_adjustment_time
        min_interval = timedelta(hours=min_interval_hours)
        time_gate_satisfied = time_since_last >= min_interval

        # Check cycle gate
        cycle_gate_satisfied = self._cycles_since_last_adjustment >= min_cycles

        # Both gates must be satisfied
        if not time_gate_satisfied:
            hours_remaining = (min_interval - time_since_last).total_seconds() / 3600
            _LOGGER.info(
                f"PID adjustment rate limited (time gate): last adjustment was "
                f"{time_since_last.total_seconds() / 3600:.1f}h ago, "
                f"minimum interval is {min_interval_hours}h "
                f"({hours_remaining:.1f}h remaining)"
            )
            return True

        if not cycle_gate_satisfied:
            cycles_remaining = min_cycles - self._cycles_since_last_adjustment
            _LOGGER.info(
                f"PID adjustment rate limited (cycle gate): "
                f"{self._cycles_since_last_adjustment} cycles since last adjustment, "
                f"minimum is {min_cycles} cycles "
                f"({cycles_remaining} cycles remaining)"
            )
            return True

        return False

    def calculate_pid_adjustment(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
        min_cycles: int = MIN_CYCLES_FOR_LEARNING,
        min_interval_hours: int = MIN_ADJUSTMENT_INTERVAL,
        min_adjustment_cycles: int = MIN_ADJUSTMENT_CYCLES,
        pwm_seconds: float = 0,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate PID adjustments based on observed cycle performance.

        Implements priority-based rule conflict resolution:
        - Priority 3 (highest): Oscillation rules (safety)
        - Priority 2: Overshoot rules (stability)
        - Priority 1 (lowest): Response time rules (performance)

        When rules conflict (e.g., overshoot says decrease Kp, slow response says increase),
        the higher priority rule wins.

        Also detects convergence (system is well-tuned) and skips adjustments.
        Rate limiting prevents adjustments too frequently using hybrid gates
        (both time AND cycles must be satisfied).

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            min_cycles: Minimum cycles required before making recommendations
            min_interval_hours: Minimum hours between adjustments (hybrid time gate)
            min_adjustment_cycles: Minimum cycles between adjustments (hybrid cycle gate)
            pwm_seconds: PWM period in seconds (0 for valve mode, >0 for PWM mode)

        Returns:
            Dictionary with recommended kp, ki, kd values, or None if insufficient data,
            system is converged, or rate limited
        """
        # Check hybrid rate limiting first (both time AND cycles)
        if self._check_rate_limit(min_interval_hours, min_adjustment_cycles):
            return None

        if len(self._cycle_history) < min_cycles:
            _LOGGER.debug(
                f"Insufficient cycles for learning: {len(self._cycle_history)} < {min_cycles}"
            )
            return None

        # Calculate average metrics from recent cycles
        # Filter out disturbed cycles for more accurate learning
        recent_cycles = self._cycle_history[-min_cycles * 2:]  # Get more cycles to account for filtering
        undisturbed_cycles = [c for c in recent_cycles if not c.is_disturbed]

        # If too many cycles were filtered out, we don't have enough data
        if len(undisturbed_cycles) < min_cycles:
            _LOGGER.debug(
                f"Insufficient undisturbed cycles for learning: "
                f"{len(undisturbed_cycles)} undisturbed out of {len(recent_cycles)} total "
                f"(need {min_cycles})"
            )
            return None

        # Use only undisturbed cycles for learning
        recent_cycles = undisturbed_cycles[-min_cycles:]

        # Use robust averaging with outlier detection (v0.7.0+)
        # MAD-based outlier rejection with max 30% removal, min 4 valid cycles

        overshoot_values = [c.overshoot for c in recent_cycles if c.overshoot is not None]
        if overshoot_values:
            avg_overshoot, overshoot_outliers = robust_average(overshoot_values)
            if overshoot_outliers:
                _LOGGER.debug(
                    f"Removed {len(overshoot_outliers)} overshoot outliers from {len(overshoot_values)} cycles"
                )
        else:
            avg_overshoot = 0.0

        undershoot_values = [c.undershoot for c in recent_cycles if c.undershoot is not None]
        if undershoot_values:
            avg_undershoot, undershoot_outliers = robust_average(undershoot_values)
            if undershoot_outliers:
                _LOGGER.debug(
                    f"Removed {len(undershoot_outliers)} undershoot outliers from {len(undershoot_values)} cycles"
                )
        else:
            avg_undershoot = 0.0

        settling_time_values = [c.settling_time for c in recent_cycles if c.settling_time is not None]
        if settling_time_values:
            avg_settling_time, settling_outliers = robust_average(settling_time_values)
            if settling_outliers:
                _LOGGER.debug(
                    f"Removed {len(settling_outliers)} settling_time outliers from {len(settling_time_values)} cycles"
                )
        else:
            avg_settling_time = 0.0

        oscillation_values = [c.oscillations for c in recent_cycles]
        avg_oscillations, oscillation_outliers = robust_average(oscillation_values)
        if oscillation_outliers:
            _LOGGER.debug(
                f"Removed {len(oscillation_outliers)} oscillation outliers from {len(oscillation_values)} cycles"
            )

        rise_time_values = [c.rise_time for c in recent_cycles if c.rise_time is not None]
        if rise_time_values:
            avg_rise_time, rise_outliers = robust_average(rise_time_values)
            if rise_outliers:
                _LOGGER.debug(
                    f"Removed {len(rise_outliers)} rise_time outliers from {len(rise_time_values)} cycles"
                )
        else:
            avg_rise_time = 0.0

        # Extract outdoor temperature averages for correlation analysis
        outdoor_temp_values = [c.outdoor_temp_avg for c in recent_cycles if c.outdoor_temp_avg is not None]

        # Check for convergence - skip adjustments if system is tuned
        if self._check_convergence(
            avg_overshoot, avg_oscillations, avg_settling_time, avg_rise_time
        ):
            _LOGGER.info("Skipping PID adjustment - system has converged")
            return None

        # Evaluate all applicable rules with hysteresis tracking
        rule_results = evaluate_pid_rules(
            avg_overshoot, avg_undershoot, avg_oscillations,
            avg_rise_time, avg_settling_time,
            recent_rise_times=rise_time_values,
            recent_outdoor_temps=outdoor_temp_values,
            state_tracker=self._rule_state_tracker,
            rule_thresholds=self._rule_thresholds,
        )

        if not rule_results:
            _LOGGER.debug("No PID rules triggered - metrics within acceptable ranges")
            return None

        # Filter out oscillation rules in PWM mode
        # PWM cycling is expected behavior and should not trigger oscillation rules
        if pwm_seconds > 0:
            from ..const import RULE_PRIORITY_OSCILLATION
            original_count = len(rule_results)
            rule_results = [r for r in rule_results if r.rule.priority != RULE_PRIORITY_OSCILLATION]
            if len(rule_results) < original_count:
                _LOGGER.debug(
                    f"PWM mode active (period={pwm_seconds}s): filtered out "
                    f"{original_count - len(rule_results)} oscillation rule(s). "
                    "PWM cycles are expected behavior."
                )
            if not rule_results:
                _LOGGER.debug("All rules filtered out in PWM mode - no adjustments needed")
                return None

        # Detect and resolve conflicts
        conflicts = detect_rule_conflicts(rule_results)
        if conflicts:
            _LOGGER.info(f"Detected {len(conflicts)} PID rule conflict(s)")
            rule_results = resolve_rule_conflicts(rule_results, conflicts)

        # Get learning rate multiplier based on convergence confidence
        learning_rate = self.get_learning_rate_multiplier()
        if learning_rate != 1.0:
            _LOGGER.info(
                f"Applying learning rate multiplier: {learning_rate:.2f}x "
                f"(confidence: {self._convergence_confidence:.2f})"
            )

        # Apply resolved rules with learning rate scaling
        new_kp = current_kp
        new_ki = current_ki
        new_kd = current_kd

        for result in rule_results:
            # Scale adjustment factors by learning rate
            # For factors < 1.0 (reductions), scale towards 1.0
            # For factors > 1.0 (increases), scale away from 1.0
            scaled_kp_factor = 1.0 + (result.kp_factor - 1.0) * learning_rate
            scaled_ki_factor = 1.0 + (result.ki_factor - 1.0) * learning_rate
            scaled_kd_factor = 1.0 + (result.kd_factor - 1.0) * learning_rate

            if result.kp_factor != 1.0:
                _LOGGER.info(
                    f"{result.reason}: Kp *= {result.kp_factor:.2f} "
                    f"(scaled: {scaled_kp_factor:.2f})"
                )
            if result.ki_factor != 1.0:
                _LOGGER.info(
                    f"{result.reason}: Ki *= {result.ki_factor:.2f} "
                    f"(scaled: {scaled_ki_factor:.2f})"
                )
            if result.kd_factor != 1.0:
                _LOGGER.info(
                    f"{result.reason}: Kd *= {result.kd_factor:.2f} "
                    f"(scaled: {scaled_kd_factor:.2f})"
                )

            new_kp *= scaled_kp_factor
            new_ki *= scaled_ki_factor
            new_kd *= scaled_kd_factor

        # Enforce PID limits
        new_kp = max(PID_LIMITS["kp_min"], min(PID_LIMITS["kp_max"], new_kp))
        new_ki = max(PID_LIMITS["ki_min"], min(PID_LIMITS["ki_max"], new_ki))
        new_kd = max(PID_LIMITS["kd_min"], min(PID_LIMITS["kd_max"], new_kd))

        # Record adjustment time and reset cycle counter for hybrid rate limiting
        self._last_adjustment_time = datetime.now()
        self._cycles_since_last_adjustment = 0

        return {
            "kp": new_kp,
            "ki": new_ki,
            "kd": new_kd,
        }

    def get_last_adjustment_time(self) -> Optional[datetime]:
        """
        Get the timestamp of the last PID adjustment.

        Returns:
            datetime of last adjustment, or None if no adjustment has been made
        """
        return self._last_adjustment_time

    def clear_history(self) -> None:
        """Clear all stored cycle metrics and reset last adjustment time and cycle counter."""
        self._cycle_history.clear()
        self._last_adjustment_time = None
        self._cycles_since_last_adjustment = 0

    def record_pid_snapshot(
        self,
        kp: float,
        ki: float,
        kd: float,
        reason: str,
        metrics: Optional[Dict[str, float]] = None,
    ) -> None:
        """Record a PID configuration snapshot for history tracking.

        Maintains a FIFO history of PID configurations for rollback and debugging.

        Args:
            kp: Proportional gain value
            ki: Integral gain value
            kd: Derivative gain value
            reason: Why this snapshot was recorded (auto_apply, manual, physics_reset, rollback)
            metrics: Optional performance metrics at time of snapshot
        """
        snapshot = {
            "timestamp": datetime.now(),
            "kp": kp,
            "ki": ki,
            "kd": kd,
            "reason": reason,
            "metrics": metrics,
        }
        self._pid_history.append(snapshot)

        # FIFO eviction: keep only the last PID_HISTORY_SIZE entries
        if len(self._pid_history) > PID_HISTORY_SIZE:
            self._pid_history = self._pid_history[-PID_HISTORY_SIZE:]

        _LOGGER.debug(
            "Recorded PID snapshot: Kp=%.2f, Ki=%.4f, Kd=%.2f, reason=%s",
            kp,
            ki,
            kd,
            reason,
        )

    def get_previous_pid(self) -> Optional[Dict[str, float]]:
        """Get the previous PID configuration for rollback.

        Returns the second-to-last PID configuration from history,
        which represents the configuration before the most recent change.
        Used for rollback when auto-applied changes cause degradation.

        Returns:
            Dict with kp, ki, kd, timestamp, reason if history has >= 2 entries,
            None otherwise.
        """
        if len(self._pid_history) < 2:
            _LOGGER.debug(
                "Cannot get previous PID: insufficient history (%d entries)",
                len(self._pid_history),
            )
            return None

        prev = self._pid_history[-2]
        return {
            "kp": prev["kp"],
            "ki": prev["ki"],
            "kd": prev["kd"],
            "timestamp": prev["timestamp"],
            "reason": prev["reason"],
        }

    def get_pid_history(self) -> List[Dict[str, Any]]:
        """Get full PID history for debugging.

        Returns a copy of the PID history list to prevent external mutation.

        Returns:
            List of PID snapshot dictionaries, each containing:
            - timestamp: When the snapshot was recorded
            - kp, ki, kd: PID gain values
            - reason: Why this snapshot was recorded
            - metrics: Optional performance metrics at time of snapshot
        """
        return self._pid_history.copy()

    def set_physics_baseline(self, kp: float, ki: float, kd: float) -> None:
        """Set the physics-based baseline PID values for drift calculation.

        The baseline represents the initial physics-calculated PID values.
        Used to calculate how far the adaptive tuning has drifted from
        the original physics-based estimates.

        Args:
            kp: Physics-based proportional gain
            ki: Physics-based integral gain
            kd: Physics-based derivative gain
        """
        self._physics_baseline_kp = kp
        self._physics_baseline_ki = ki
        self._physics_baseline_kd = kd
        _LOGGER.info(
            "Physics baseline set: Kp=%.2f, Ki=%.4f, Kd=%.2f",
            kp,
            ki,
            kd,
        )

    def calculate_drift_from_baseline(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
    ) -> float:
        """Calculate how far current PID values have drifted from physics baseline.

        Returns the maximum percentage drift across all three PID parameters.
        Used to enforce safety limits on cumulative adaptive changes.

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain

        Returns:
            Maximum drift as a decimal (e.g., 0.5 = 50% drift).
            Returns 0.0 if no baseline is set.
        """
        if self._physics_baseline_kp is None:
            _LOGGER.debug("No physics baseline set, drift calculation returns 0.0")
            return 0.0

        # Calculate percentage drift for each parameter
        # Use absolute difference divided by baseline value
        kp_drift = abs(current_kp - self._physics_baseline_kp) / self._physics_baseline_kp
        ki_drift = (
            abs(current_ki - self._physics_baseline_ki) / self._physics_baseline_ki
            if self._physics_baseline_ki > 0
            else 0.0
        )
        kd_drift = (
            abs(current_kd - self._physics_baseline_kd) / self._physics_baseline_kd
            if self._physics_baseline_kd > 0
            else 0.0
        )

        max_drift = max(kp_drift, ki_drift, kd_drift)
        _LOGGER.debug(
            "PID drift from baseline: Kp=%.1f%%, Ki=%.1f%%, Kd=%.1f%%, max=%.1f%%",
            kp_drift * 100,
            ki_drift * 100,
            kd_drift * 100,
            max_drift * 100,
        )
        return max_drift

    def update_convergence_tracking(self, metrics: CycleMetrics) -> bool:
        """Update convergence tracking based on latest cycle metrics.

        Tracks consecutive converged cycles to determine when PID is stable
        enough for Ke learning to begin.

        Args:
            metrics: The latest cycle metrics to evaluate

        Returns:
            True if PID is now converged for Ke learning, False otherwise
        """
        # Check if this cycle meets convergence thresholds
        overshoot = metrics.overshoot if metrics.overshoot is not None else 0.0
        oscillations = metrics.oscillations
        settling_time = metrics.settling_time if metrics.settling_time is not None else 0.0
        rise_time = metrics.rise_time if metrics.rise_time is not None else 0.0

        is_cycle_converged = (
            overshoot <= self._convergence_thresholds["overshoot_max"] and
            oscillations <= self._convergence_thresholds["oscillations_max"] and
            settling_time <= self._convergence_thresholds["settling_time_max"] and
            rise_time <= self._convergence_thresholds["rise_time_max"]
        )

        if is_cycle_converged:
            self._consecutive_converged_cycles += 1
            _LOGGER.debug(
                "Convergence tracking: cycle converged (%d consecutive)",
                self._consecutive_converged_cycles,
            )

            # Check if we've reached the threshold for Ke learning
            if (self._consecutive_converged_cycles >= MIN_CONVERGENCE_CYCLES_FOR_KE and
                    not self._pid_converged_for_ke):
                self._pid_converged_for_ke = True
                _LOGGER.info(
                    "PID converged for Ke learning after %d consecutive cycles",
                    self._consecutive_converged_cycles,
                )
        else:
            # Reset consecutive counter on non-converged cycle
            if self._consecutive_converged_cycles > 0:
                _LOGGER.debug(
                    "Convergence tracking: cycle not converged, resetting "
                    "counter (was %d)",
                    self._consecutive_converged_cycles,
                )
            self._consecutive_converged_cycles = 0
            self._pid_converged_for_ke = False

        return self._pid_converged_for_ke

    def is_pid_converged_for_ke(self) -> bool:
        """Check if PID has converged sufficiently for Ke learning.

        Returns:
            True if PID has had MIN_CONVERGENCE_CYCLES_FOR_KE consecutive
            converged cycles, False otherwise
        """
        return self._pid_converged_for_ke

    def get_consecutive_converged_cycles(self) -> int:
        """Get the number of consecutive converged cycles.

        Returns:
            Number of consecutive cycles meeting convergence thresholds
        """
        return self._consecutive_converged_cycles

    def reset_ke_convergence(self) -> None:
        """Reset Ke convergence tracking.

        Call this when PID values are changed or Ke learning needs to restart.
        """
        old_converged = self._pid_converged_for_ke
        old_count = self._consecutive_converged_cycles
        self._consecutive_converged_cycles = 0
        self._pid_converged_for_ke = False
        if old_converged or old_count > 0:
            _LOGGER.info(
                "Ke convergence reset (was: converged=%s, consecutive=%d)",
                old_converged,
                old_count,
            )

    def update_convergence_confidence(self, metrics: CycleMetrics) -> None:
        """Update convergence confidence based on cycle performance.

        Confidence increases when cycles meet convergence criteria, and is used
        to scale learning rate adjustments.

        Args:
            metrics: CycleMetrics from the latest completed cycle
        """
        # Check if this cycle meets convergence criteria
        is_good_cycle = (
            (metrics.overshoot is None or metrics.overshoot <= self._convergence_thresholds["overshoot_max"]) and
            metrics.oscillations <= self._convergence_thresholds["oscillations_max"] and
            (metrics.settling_time is None or metrics.settling_time <= self._convergence_thresholds["settling_time_max"]) and
            (metrics.rise_time is None or metrics.rise_time <= self._convergence_thresholds["rise_time_max"])
        )

        if is_good_cycle:
            # Increase confidence, capped at maximum
            self._convergence_confidence = min(
                CONVERGENCE_CONFIDENCE_HIGH,
                self._convergence_confidence + CONFIDENCE_INCREASE_PER_GOOD_CYCLE
            )
            _LOGGER.debug(
                f"Convergence confidence increased to {self._convergence_confidence:.2f} "
                f"(good cycle: overshoot={metrics.overshoot:.2f}°C, "
                f"oscillations={metrics.oscillations}, "
                f"settling={metrics.settling_time:.1f}min)"
            )
        else:
            # Poor cycle - reduce confidence slightly
            self._convergence_confidence = max(
                0.0,
                self._convergence_confidence - CONFIDENCE_INCREASE_PER_GOOD_CYCLE * 0.5
            )
            _LOGGER.debug(
                f"Convergence confidence decreased to {self._convergence_confidence:.2f} "
                f"(poor cycle detected)"
            )

    def check_performance_degradation(self, baseline_window: int = 10) -> bool:
        """Check if recent performance has degraded compared to baseline.

        Compares recent cycles to earlier baseline to detect if tuning has drifted.

        Args:
            baseline_window: Number of cycles to use for baseline comparison

        Returns:
            True if performance has degraded significantly, False otherwise
        """
        if len(self._cycle_history) < baseline_window * 2:
            return False  # Not enough data

        # Get baseline (older cycles) and recent cycles
        baseline_cycles = self._cycle_history[:baseline_window]
        recent_cycles = self._cycle_history[-baseline_window:]

        # Calculate average overshoot for both windows
        baseline_overshoot = statistics.mean(
            [c.overshoot for c in baseline_cycles if c.overshoot is not None]
        ) if any(c.overshoot is not None for c in baseline_cycles) else 0.0

        recent_overshoot = statistics.mean(
            [c.overshoot for c in recent_cycles if c.overshoot is not None]
        ) if any(c.overshoot is not None for c in recent_cycles) else 0.0

        # Check if recent performance is significantly worse (>50% increase in overshoot)
        if recent_overshoot > baseline_overshoot * 1.5 and recent_overshoot > 0.3:
            _LOGGER.warning(
                f"Performance degradation detected: overshoot increased from "
                f"{baseline_overshoot:.2f}°C to {recent_overshoot:.2f}°C"
            )
            return True

        return False

    def check_seasonal_shift(self, outdoor_temp: Optional[float] = None) -> bool:
        """Check if outdoor temperature regime has shifted significantly.

        Detects seasonal changes (e.g., winter to spring) that may require
        re-tuning. Uses 10°C shift threshold.

        Args:
            outdoor_temp: Current outdoor temperature in °C

        Returns:
            True if significant shift detected, False otherwise
        """
        if outdoor_temp is None:
            return False

        # Add to history
        self._outdoor_temp_history.append(outdoor_temp)

        # Keep last 30 readings (roughly 15 hours at 30-min intervals)
        if len(self._outdoor_temp_history) > 30:
            self._outdoor_temp_history = self._outdoor_temp_history[-30:]

        # Need at least 10 readings to detect shift
        if len(self._outdoor_temp_history) < 10:
            return False

        # Check daily (avoid checking too frequently)
        now = datetime.now()
        if self._last_seasonal_check is not None:
            if now - self._last_seasonal_check < timedelta(days=1):
                return False

        self._last_seasonal_check = now

        # Calculate average of old vs new readings
        old_avg = statistics.mean(self._outdoor_temp_history[:10])
        new_avg = statistics.mean(self._outdoor_temp_history[-10:])

        # Detect 10°C shift
        if abs(new_avg - old_avg) >= 10.0:
            _LOGGER.warning(
                f"Seasonal shift detected: outdoor temp changed from "
                f"{old_avg:.1f}°C to {new_avg:.1f}°C"
            )
            return True

        return False

    def apply_confidence_decay(self) -> None:
        """Apply daily confidence decay to account for drift over time.

        Reduces confidence by CONFIDENCE_DECAY_RATE_DAILY (2% per day).
        Call this once per day or on each cycle with appropriate scaling.
        """
        # Decay confidence
        self._convergence_confidence = max(
            0.0,
            self._convergence_confidence * (1.0 - CONFIDENCE_DECAY_RATE_DAILY)
        )

    def get_learning_rate_multiplier(self) -> float:
        """Get learning rate multiplier based on convergence confidence.

        Returns:
            Multiplier in range [0.5, 2.0]:
            - Low confidence (0.0): 2.0x faster learning
            - High confidence (1.0): 0.5x slower learning
        """
        # Low confidence = faster learning (larger adjustments)
        # High confidence = slower learning (smaller adjustments)
        # Linear interpolation from 2.0 (confidence=0) to 0.5 (confidence=1)
        multiplier = 2.0 - (self._convergence_confidence * 1.5)
        return max(0.5, min(2.0, multiplier))

    def get_convergence_confidence(self) -> float:
        """Get current convergence confidence level.

        Returns:
            Confidence in range [0.0, 1.0]
        """
        return self._convergence_confidence

    def start_validation_mode(self, baseline_overshoot: float) -> None:
        """Start validation mode after auto-applying PID changes.

        Validation mode monitors the next VALIDATION_CYCLE_COUNT cycles
        to verify the auto-applied changes don't degrade performance.

        Args:
            baseline_overshoot: Average overshoot from cycles before auto-apply,
                               used as reference for degradation detection.
        """
        self._validation_mode = True
        self._validation_baseline_overshoot = baseline_overshoot
        self._validation_cycles = []
        _LOGGER.info(
            "Validation mode started: monitoring next %d cycles "
            "(baseline overshoot: %.2f°C)",
            VALIDATION_CYCLE_COUNT,
            baseline_overshoot,
        )

    def add_validation_cycle(self, metrics: CycleMetrics) -> Optional[str]:
        """Add a cycle to validation tracking and check for completion.

        Collects cycles during validation mode and evaluates performance
        once VALIDATION_CYCLE_COUNT cycles have been recorded.

        Args:
            metrics: CycleMetrics from the completed cycle

        Returns:
            None if still collecting cycles,
            'success' if validation passed (performance maintained or improved),
            'rollback' if validation failed (significant degradation detected).
        """
        if not self._validation_mode:
            return None

        self._validation_cycles.append(metrics)
        _LOGGER.debug(
            "Validation cycle %d/%d added (overshoot: %.2f°C)",
            len(self._validation_cycles),
            VALIDATION_CYCLE_COUNT,
            metrics.overshoot if metrics.overshoot is not None else 0.0,
        )

        # Still collecting cycles
        if len(self._validation_cycles) < VALIDATION_CYCLE_COUNT:
            return None

        # Validation complete - calculate average overshoot
        overshoot_values = [
            c.overshoot for c in self._validation_cycles if c.overshoot is not None
        ]

        if not overshoot_values:
            _LOGGER.warning(
                "Validation complete but no overshoot data - assuming success"
            )
            self._validation_mode = False
            return "success"

        avg_overshoot = statistics.mean(overshoot_values)
        baseline = self._validation_baseline_overshoot or 0.1  # Avoid division by zero

        # Calculate degradation as percentage increase from baseline
        # Use max(baseline, 0.1) to handle very small baseline values
        degradation_pct = (avg_overshoot - baseline) / max(baseline, 0.1)

        if degradation_pct > VALIDATION_DEGRADATION_THRESHOLD:
            _LOGGER.warning(
                "Validation FAILED: overshoot degraded %.1f%% "
                "(baseline: %.2f°C, validation avg: %.2f°C, threshold: %.0f%%)",
                degradation_pct * 100,
                baseline,
                avg_overshoot,
                VALIDATION_DEGRADATION_THRESHOLD * 100,
            )
            self._validation_mode = False
            return "rollback"

        _LOGGER.info(
            "Validation SUCCESS: overshoot change %.1f%% within threshold "
            "(baseline: %.2f°C, validation avg: %.2f°C)",
            degradation_pct * 100,
            baseline,
            avg_overshoot,
        )
        self._validation_mode = False
        return "success"

    def is_in_validation_mode(self) -> bool:
        """Check if currently in validation mode.

        Returns:
            True if validation mode is active, False otherwise.
        """
        return self._validation_mode
