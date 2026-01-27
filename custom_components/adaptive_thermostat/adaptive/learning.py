"""Thermal rate learning and adaptive PID adjustments for Adaptive Thermostat."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, TYPE_CHECKING
import statistics
import logging

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode

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
    MAX_AUTO_APPLIES_PER_SEASON,
    MAX_AUTO_APPLIES_LIFETIME,
    MAX_CUMULATIVE_DRIFT_PCT,
    CLAMPED_OVERSHOOT_MULTIPLIER,
    DEFAULT_CLAMPED_OVERSHOOT_MULTIPLIER,
    HeatingType,
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

# Import and re-export PWM tuning utilities for backward compatibility
from .pwm_tuning import calculate_pwm_adjustment, ValveCycleTracker

# Import validation manager for safety checks
from .validation import ValidationManager

# Import confidence tracker for convergence tracking
from .confidence import ConfidenceTracker

# Import serialization utilities for state persistence
from .learner_serialization import (
    serialize_cycle,
    learner_to_dict,
    restore_learner_from_dict,
)

# Import auto-apply manager for safety gates and threshold management
from .auto_apply import AutoApplyManager, get_auto_apply_thresholds

# Import HVAC mode helpers
from ..helpers.hvac_mode import mode_to_str, get_hvac_heat_mode, get_hvac_cool_mode

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
        # Mode-specific cycle histories
        self._heating_cycle_history: List[CycleMetrics] = []
        self._cooling_cycle_history: List[CycleMetrics] = []
        self._max_history = max_history
        self._heating_type = heating_type
        self._convergence_thresholds = get_convergence_thresholds(heating_type)
        self._rule_thresholds = get_rule_thresholds(heating_type)
        self._last_adjustment_time: Optional[datetime] = None
        # Convergence tracking for Ke learning activation
        self._consecutive_converged_cycles: int = 0
        self._pid_converged_for_ke: bool = False
        self._duty_cycle_history: List[float] = []
        # Hybrid rate limiting: track cycles since last adjustment
        self._cycles_since_last_adjustment: int = 0
        # Rule state tracker with hysteresis to prevent oscillation
        self._rule_state_tracker = RuleStateTracker()

        # PID history for rollback and debugging
        self._pid_history: List[Dict[str, Any]] = []

        # Confidence tracker for convergence confidence and auto-apply counts
        self._confidence = ConfidenceTracker(self._convergence_thresholds)

        # Validation manager for safety checks and validation mode
        self._validation = ValidationManager()

        # Auto-apply manager for safety gates and threshold-based decisions
        self._auto_apply = AutoApplyManager(heating_type)

    @property
    def cycle_history(self) -> List[CycleMetrics]:
        """Return cycle history for external access (defaults to heating for backward compatibility)."""
        return self._heating_cycle_history

    @cycle_history.setter
    def cycle_history(self, value: List[CycleMetrics]) -> None:
        """Set cycle history (primarily for testing, defaults to heating for backward compatibility)."""
        self._heating_cycle_history = value

    # Backward-compatible aliases for private attributes (used by tests)
    @property
    def _cycle_history(self) -> List[CycleMetrics]:
        """Backward-compatible alias for _heating_cycle_history."""
        return self._heating_cycle_history

    @_cycle_history.setter
    def _cycle_history(self, value: List[CycleMetrics]) -> None:
        """Backward-compatible alias setter for _heating_cycle_history."""
        self._heating_cycle_history = value

    # Backward-compatible aliases for confidence tracker attributes (used by tests)
    @property
    def _heating_convergence_confidence(self) -> float:
        """Backward-compatible alias for confidence tracker's heating confidence."""
        return self._confidence._heating_convergence_confidence

    @_heating_convergence_confidence.setter
    def _heating_convergence_confidence(self, value: float) -> None:
        """Backward-compatible alias setter for confidence tracker's heating confidence."""
        self._confidence._heating_convergence_confidence = value

    @property
    def _cooling_convergence_confidence(self) -> float:
        """Backward-compatible alias for confidence tracker's cooling confidence."""
        return self._confidence._cooling_convergence_confidence

    @_cooling_convergence_confidence.setter
    def _cooling_convergence_confidence(self, value: float) -> None:
        """Backward-compatible alias setter for confidence tracker's cooling confidence."""
        self._confidence._cooling_convergence_confidence = value

    @property
    def _heating_auto_apply_count(self) -> int:
        """Backward-compatible alias for confidence tracker's heating auto-apply count."""
        return self._confidence._heating_auto_apply_count

    @_heating_auto_apply_count.setter
    def _heating_auto_apply_count(self, value: int) -> None:
        """Backward-compatible alias setter for confidence tracker's heating auto-apply count."""
        self._confidence._heating_auto_apply_count = value

    @property
    def _cooling_auto_apply_count(self) -> int:
        """Backward-compatible alias for confidence tracker's cooling auto-apply count."""
        return self._confidence._cooling_auto_apply_count

    @_cooling_auto_apply_count.setter
    def _cooling_auto_apply_count(self, value: int) -> None:
        """Backward-compatible alias setter for confidence tracker's cooling auto-apply count."""
        self._confidence._cooling_auto_apply_count = value

    @property
    def _auto_apply_count(self) -> int:
        """Backward-compatible alias for _heating_auto_apply_count."""
        return self._confidence._heating_auto_apply_count

    @_auto_apply_count.setter
    def _auto_apply_count(self, value: int) -> None:
        """Backward-compatible alias setter for _heating_auto_apply_count."""
        self._confidence._heating_auto_apply_count = value

    @property
    def _convergence_confidence(self) -> float:
        """Backward-compatible alias for _heating_convergence_confidence."""
        return self._confidence._heating_convergence_confidence

    @_convergence_confidence.setter
    def _convergence_confidence(self, value: float) -> None:
        """Backward-compatible alias setter for _heating_convergence_confidence."""
        self._confidence._heating_convergence_confidence = value

    # Backward-compatible aliases for validation manager attributes (used by tests)
    @property
    def _validation_mode(self) -> bool:
        """Backward-compatible alias for validation manager's validation mode."""
        return self._validation.is_in_validation_mode()

    @property
    def _validation_cycles(self) -> List[CycleMetrics]:
        """Backward-compatible alias for validation manager's validation cycles."""
        return self._validation._validation_cycles

    @property
    def _validation_baseline_overshoot(self) -> Optional[float]:
        """Backward-compatible alias for validation manager's baseline overshoot."""
        return self._validation._validation_baseline_overshoot

    @property
    def _last_seasonal_check(self) -> Optional[datetime]:
        """Backward-compatible alias for validation manager's last seasonal check."""
        return self._validation._last_seasonal_check

    @_last_seasonal_check.setter
    def _last_seasonal_check(self, value: Optional[datetime]) -> None:
        """Backward-compatible alias setter for validation manager's last seasonal check."""
        self._validation._last_seasonal_check = value

    @property
    def _last_seasonal_shift(self) -> Optional[datetime]:
        """Backward-compatible alias for validation manager's last seasonal shift."""
        return self._validation._last_seasonal_shift

    @_last_seasonal_shift.setter
    def _last_seasonal_shift(self, value: Optional[datetime]) -> None:
        """Backward-compatible alias setter for validation manager's last seasonal shift."""
        self._validation._last_seasonal_shift = value

    @property
    def _outdoor_temp_history(self) -> List[float]:
        """Backward-compatible alias for validation manager's outdoor temp history."""
        return self._validation._outdoor_temp_history

    @property
    def _physics_baseline_kp(self) -> Optional[float]:
        """Backward-compatible alias for validation manager's physics baseline Kp."""
        return self._validation._physics_baseline_kp

    @property
    def _physics_baseline_ki(self) -> Optional[float]:
        """Backward-compatible alias for validation manager's physics baseline Ki."""
        return self._validation._physics_baseline_ki

    @property
    def _physics_baseline_kd(self) -> Optional[float]:
        """Backward-compatible alias for validation manager's physics baseline Kd."""
        return self._validation._physics_baseline_kd

    def add_cycle_metrics(self, metrics: CycleMetrics, mode: "HVACMode" = None) -> None:
        """
        Add a cycle's performance metrics to history.

        Implements FIFO eviction when history exceeds max_history.
        Increments cycle counter for hybrid rate limiting.

        Args:
            metrics: CycleMetrics object with performance data
            mode: HVACMode (HEAT or COOL) to route cycle to correct history (defaults to HEAT)
        """
        if mode is None:
            mode = get_hvac_heat_mode()
        # Route to correct history based on mode
        if mode == get_hvac_cool_mode():
            cycle_history = self._cooling_cycle_history
        else:
            cycle_history = self._heating_cycle_history

        cycle_history.append(metrics)

        # Log detailed cycle metrics for debugging
        _LOGGER.debug(
            "Cycle recorded [%s mode, %d/%d]: overshoot=%.3f, undershoot=%.3f, "
            "settling_time=%.1f, oscillations=%d, rise_time=%.1f, "
            "inter_cycle_drift=%.3f, settling_mae=%.3f, "
            "integral@tolerance=%.2f, integral@setpoint=%.2f, decay=%.3f, "
            "disturbed=%s, clamped=%s",
            mode_to_str(mode),
            len(cycle_history),
            self._max_history,
            metrics.overshoot or 0.0,
            metrics.undershoot or 0.0,
            metrics.settling_time or 0.0,
            metrics.oscillations,
            metrics.rise_time or 0.0,
            metrics.inter_cycle_drift or 0.0,
            metrics.settling_mae or 0.0,
            metrics.integral_at_tolerance_entry or 0.0,
            metrics.integral_at_setpoint_cross or 0.0,
            metrics.decay_contribution or 0.0,
            metrics.is_disturbed,
            metrics.was_clamped,
        )

        # Increment cycle counter for hybrid rate limiting
        self._cycles_since_last_adjustment += 1

        # FIFO eviction: remove oldest entries when exceeding max history
        if len(cycle_history) > self._max_history:
            evicted_count = len(cycle_history) - self._max_history
            if mode == get_hvac_cool_mode():
                self._cooling_cycle_history = self._cooling_cycle_history[-self._max_history:]
            else:
                self._heating_cycle_history = self._heating_cycle_history[-self._max_history:]
            _LOGGER.debug(
                f"Cycle history ({mode_to_str(mode)} mode) exceeded max ({self._max_history}), "
                f"evicted {evicted_count} oldest entries"
            )

    def get_cycle_count(self, mode: "HVACMode" = None) -> int:
        """
        Get number of stored cycle metrics.

        Args:
            mode: HVACMode (HEAT or COOL) to check (defaults to HEAT)

        Returns:
            Number of cycles in history for specified mode
        """
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            return len(self._cooling_cycle_history)
        else:
            return len(self._heating_cycle_history)

    def _check_convergence(
        self,
        avg_overshoot: float,
        avg_oscillations: float,
        avg_settling_time: float,
        avg_rise_time: float,
        avg_inter_cycle_drift: float = 0.0,
        avg_settling_mae: float = 0.0,
        avg_undershoot: float = 0.0,
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
            avg_inter_cycle_drift: Average inter-cycle drift in °C (default 0.0)
            avg_settling_mae: Average settling MAE in °C (default 0.0)
            avg_undershoot: Average undershoot in °C (default 0.0)

        Returns:
            True if converged, False otherwise
        """
        is_converged = (
            avg_overshoot <= self._convergence_thresholds["overshoot_max"] and
            avg_oscillations <= self._convergence_thresholds["oscillations_max"] and
            avg_settling_time <= self._convergence_thresholds["settling_time_max"] and
            avg_rise_time <= self._convergence_thresholds["rise_time_max"] and
            abs(avg_inter_cycle_drift) <= self._convergence_thresholds.get("inter_cycle_drift_max", 0.3) and
            avg_settling_mae <= self._convergence_thresholds.get("settling_mae_max", 0.3) and
            avg_undershoot <= self._convergence_thresholds.get("undershoot_max", 0.2)
        )

        if is_converged:
            _LOGGER.info(
                f"PID convergence detected - system tuned: "
                f"overshoot={avg_overshoot:.2f}°C, oscillations={avg_oscillations:.1f}, "
                f"settling={avg_settling_time:.1f}min, rise={avg_rise_time:.1f}min, "
                f"inter_cycle_drift={avg_inter_cycle_drift:.2f}°C, settling_mae={avg_settling_mae:.2f}°C, "
                f"undershoot={avg_undershoot:.2f}°C"
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
        time_since_last = dt_util.utcnow() - self._last_adjustment_time
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
        check_auto_apply: bool = False,
        outdoor_temp: Optional[float] = None,
        mode: "HVACMode" = None,
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

        When check_auto_apply is True, additional safety gates are enforced:
        - Validation mode check (skip if in validation)
        - Lifetime and seasonal auto-apply limits
        - Cumulative drift from physics baseline limit
        - Seasonal shift blocking
        - Heating-type-specific confidence thresholds

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            min_cycles: Minimum cycles required before making recommendations
            min_interval_hours: Minimum hours between adjustments (hybrid time gate)
            min_adjustment_cycles: Minimum cycles between adjustments (hybrid cycle gate)
            pwm_seconds: PWM period in seconds (0 for valve mode, >0 for PWM mode)
            check_auto_apply: If True, enforce auto-apply safety gates and use
                heating-type-specific thresholds
            outdoor_temp: Current outdoor temperature for seasonal shift detection
            mode: HVACMode (HEAT or COOL) to use correct history (defaults to HEAT)

        Returns:
            Dictionary with recommended kp, ki, kd values, or None if insufficient data,
            system is converged, rate limited, or blocked by auto-apply safety gates
        """
        # Select the correct cycle history based on mode
        if mode is None:
            mode = get_hvac_heat_mode()
        cycle_history = self._cooling_cycle_history if mode == get_hvac_cool_mode() else self._heating_cycle_history
        auto_apply_count = self._cooling_auto_apply_count if mode == get_hvac_cool_mode() else self._heating_auto_apply_count
        convergence_confidence = self._cooling_convergence_confidence if mode == get_hvac_cool_mode() else self._heating_convergence_confidence

        # Auto-apply safety gates (when called for automatic PID application)
        if check_auto_apply:
            # Delegate to auto-apply manager for all safety gate checks
            gates_passed, min_interval_hours, min_adjustment_cycles, min_cycles = (
                self._auto_apply.check_auto_apply_safety_gates(
                    validation_manager=self._validation,
                    confidence_tracker=self._confidence,
                    current_kp=current_kp,
                    current_ki=current_ki,
                    current_kd=current_kd,
                    outdoor_temp=outdoor_temp,
                    pid_history=self._pid_history,
                    mode=mode,
                )
            )

            if not gates_passed:
                return None

            # Use the adjusted parameters from auto-apply manager
            # (already includes heating-type-specific thresholds and subsequent learning multiplier)

        # Check hybrid rate limiting first (both time AND cycles)
        if self._check_rate_limit(min_interval_hours, min_adjustment_cycles):
            return None

        if len(cycle_history) < min_cycles:
            _LOGGER.debug(
                f"Insufficient cycles for learning ({mode_to_str(mode)} mode): {len(cycle_history)} < {min_cycles}"
            )
            return None

        # Calculate average metrics from recent cycles
        # Filter out disturbed cycles for more accurate learning
        recent_cycles = cycle_history[-min_cycles * 2:]  # Get more cycles to account for filtering
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

        # Apply clamped overshoot multiplier to cycles that were clamped
        # Clamping hides true overshoot potential, so we amplify to compensate
        clamped_multiplier = CLAMPED_OVERSHOOT_MULTIPLIER.get(
            self._heating_type, DEFAULT_CLAMPED_OVERSHOOT_MULTIPLIER
        )
        clamped_cycle_count = 0
        overshoot_values = []
        for c in recent_cycles:
            if c.overshoot is not None:
                if getattr(c, 'was_clamped', False):
                    overshoot_values.append(c.overshoot * clamped_multiplier)
                    clamped_cycle_count += 1
                else:
                    overshoot_values.append(c.overshoot)

        if overshoot_values:
            avg_overshoot, overshoot_outliers = robust_average(overshoot_values)
            if overshoot_outliers:
                _LOGGER.debug(
                    f"Removed {len(overshoot_outliers)} overshoot outliers from {len(overshoot_values)} cycles"
                )
            if clamped_cycle_count > 0:
                _LOGGER.debug(
                    f"{clamped_cycle_count} of {len(overshoot_values)} recent cycles clamped, "
                    f"overshoot amplified by {clamped_multiplier}x ({self._heating_type})"
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

        # Extract decay metrics for rule evaluation
        decay_contribution_values = [
            c.decay_contribution for c in recent_cycles if c.decay_contribution is not None
        ]
        avg_decay_contribution = (
            statistics.mean(decay_contribution_values) if decay_contribution_values else None
        )

        integral_at_tolerance_values = [
            c.integral_at_tolerance_entry for c in recent_cycles if c.integral_at_tolerance_entry is not None
        ]
        avg_integral_at_tolerance = (
            statistics.mean(integral_at_tolerance_values) if integral_at_tolerance_values else None
        )

        # Extract inter_cycle_drift values from recent cycles
        drift_values = [
            c.inter_cycle_drift for c in recent_cycles
            if c.inter_cycle_drift is not None
        ]
        avg_inter_cycle_drift = sum(drift_values) / len(drift_values) if drift_values else 0.0

        # Extract settling_mae values
        settling_mae_values = [
            c.settling_mae for c in recent_cycles
            if c.settling_mae is not None
        ]
        avg_settling_mae = sum(settling_mae_values) / len(settling_mae_values) if settling_mae_values else 0.0

        # Log averaged metrics used for learning decisions
        _LOGGER.debug(
            "Learning evaluation using %d cycles: avg_overshoot=%.3f, avg_undershoot=%.3f, "
            "avg_oscillations=%.1f, avg_settling_time=%.1f, avg_rise_time=%.1f, "
            "avg_inter_cycle_drift=%.3f, avg_settling_mae=%.3f, avg_decay=%.3f",
            len(recent_cycles),
            avg_overshoot,
            avg_undershoot,
            avg_oscillations,
            avg_settling_time,
            avg_rise_time,
            avg_inter_cycle_drift,
            avg_settling_mae,
            avg_decay_contribution or 0.0,
        )

        # Check for convergence - skip adjustments if system is tuned
        if self._check_convergence(
            avg_overshoot, avg_oscillations, avg_settling_time, avg_rise_time,
            avg_inter_cycle_drift=avg_inter_cycle_drift,
            avg_settling_mae=avg_settling_mae,
            avg_undershoot=avg_undershoot,
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
            decay_contribution=avg_decay_contribution,
            integral_at_tolerance_entry=avg_integral_at_tolerance,
            avg_inter_cycle_drift=avg_inter_cycle_drift,
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
        learning_rate = self.get_learning_rate_multiplier(convergence_confidence)
        if learning_rate != 1.0:
            _LOGGER.info(
                f"Applying learning rate multiplier: {learning_rate:.2f}x "
                f"(confidence: {convergence_confidence:.2f}, mode: {mode_to_str(mode)})"
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
        self._last_adjustment_time = dt_util.utcnow()
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
        """Clear all stored cycle metrics, reset adjustment tracking, and exit validation mode.

        This resets:
        - Cycle history (both heating and cooling)
        - Last adjustment time and cycle counter
        - Convergence confidence (both heating and cooling)
        - Validation mode state and collected validation cycles
        """
        self._heating_cycle_history.clear()
        self._cooling_cycle_history.clear()
        self._last_adjustment_time = None
        self._cycles_since_last_adjustment = 0
        self._confidence.reset_confidence()  # Reset both modes
        self._validation.reset_validation_state()

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
            "timestamp": dt_util.utcnow(),
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

    def restore_pid_history(self, history: List[Dict[str, Any]]) -> None:
        """Restore PID history from state restoration.

        Parses ISO timestamp strings back to datetime objects and validates entries.
        Used during Home Assistant startup to restore history from previous session.

        Args:
            history: List of PID snapshot dictionaries with ISO timestamp strings
        """
        if not history:
            return

        restored = []
        for entry in history:
            try:
                # Parse ISO timestamp string back to datetime
                timestamp = entry.get("timestamp")
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp)
                elif not isinstance(timestamp, datetime):
                    _LOGGER.warning("Invalid timestamp in PID history entry: %s", entry)
                    continue

                restored.append({
                    "timestamp": timestamp,
                    "kp": float(entry.get("kp", 0)),
                    "ki": float(entry.get("ki", 0)),
                    "kd": float(entry.get("kd", 0)),
                    "reason": entry.get("reason", "restored"),
                    "metrics": entry.get("metrics"),
                })
            except (ValueError, TypeError) as e:
                _LOGGER.warning("Failed to restore PID history entry: %s - %s", entry, e)
                continue

        # Apply FIFO limit
        if len(restored) > PID_HISTORY_SIZE:
            restored = restored[-PID_HISTORY_SIZE:]

        self._pid_history = restored
        _LOGGER.info("Restored %d PID history entries", len(restored))

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
        self._validation.set_physics_baseline(kp, ki, kd)

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
        return self._validation.calculate_drift_from_baseline(current_kp, current_ki, current_kd)

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
        undershoot = metrics.undershoot if metrics.undershoot is not None else 0.0
        oscillations = metrics.oscillations
        settling_time = metrics.settling_time if metrics.settling_time is not None else 0.0
        rise_time = metrics.rise_time if metrics.rise_time is not None else 0.0

        is_cycle_converged = (
            overshoot <= self._convergence_thresholds["overshoot_max"] and
            undershoot <= self._convergence_thresholds.get("undershoot_max", 0.2) and
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


    def get_convergence_confidence(self, mode: "HVACMode" = None) -> float:
        """Get current convergence confidence level for specified mode.

        Args:
            mode: HVACMode (HEAT or COOL) to check (defaults to HEAT)

        Returns:
            Confidence in range [0.0, 1.0]
        """
        return self._confidence.get_convergence_confidence(mode)

    def get_auto_apply_count(self, mode: "HVACMode" = None) -> int:
        """Get number of times PID has been auto-applied for specified mode.

        Args:
            mode: HVACMode (HEAT or COOL) to check (defaults to HEAT)

        Returns:
            Total count of auto-applied PID adjustments for the specified mode.
        """
        return self._confidence.get_auto_apply_count(mode)

    def update_convergence_confidence(self, metrics: CycleMetrics, mode: "HVACMode" = None) -> None:
        """Update convergence confidence based on cycle performance.

        Confidence increases when cycles meet convergence criteria, and is used
        to scale learning rate adjustments.

        Args:
            metrics: CycleMetrics from the latest completed cycle
            mode: HVACMode (HEAT or COOL) to update (defaults to HEAT)
        """
        self._confidence.update_convergence_confidence(metrics, mode)

    def check_performance_degradation(self, baseline_window: int = 10, mode: "HVACMode" = None) -> bool:
        """Check if recent performance has degraded compared to baseline.

        Compares recent cycles to earlier baseline to detect if tuning has drifted.

        Args:
            baseline_window: Number of cycles to use for baseline comparison
            mode: HVACMode (HEAT or COOL) to check (defaults to HEAT)

        Returns:
            True if performance has degraded significantly, False otherwise
        """
        if mode is None:
            mode = get_hvac_heat_mode()
        cycle_history = self._cooling_cycle_history if mode == get_hvac_cool_mode() else self._heating_cycle_history
        return self._validation.check_performance_degradation(cycle_history, baseline_window)

    def check_seasonal_shift(self, outdoor_temp: Optional[float] = None) -> bool:
        """Check if outdoor temperature regime has shifted significantly.

        Detects seasonal changes (e.g., winter to spring) that may require
        re-tuning. Uses 10°C shift threshold.

        Args:
            outdoor_temp: Current outdoor temperature in °C

        Returns:
            True if significant shift detected, False otherwise
        """
        return self._validation.check_seasonal_shift(outdoor_temp)

    def apply_confidence_decay(self) -> None:
        """Apply daily confidence decay to account for drift over time.

        Reduces confidence by CONFIDENCE_DECAY_RATE_DAILY (2% per day).
        Call this once per day or on each cycle with appropriate scaling.
        Decays both heating and cooling mode confidence.
        """
        self._confidence.apply_confidence_decay()

    def get_learning_rate_multiplier(self, confidence: Optional[float] = None) -> float:
        """Get learning rate multiplier based on convergence confidence.

        Args:
            confidence: Optional confidence value to use. If None, uses heating mode confidence.

        Returns:
            Multiplier in range [0.5, 2.0]:
            - Low confidence (0.0): 2.0x faster learning
            - High confidence (1.0): 0.5x slower learning
        """
        return self._confidence.get_learning_rate_multiplier(confidence)

    def start_validation_mode(self, baseline_overshoot: float) -> None:
        """Start validation mode after auto-applying PID changes.

        Validation mode monitors the next VALIDATION_CYCLE_COUNT cycles
        to verify the auto-applied changes don't degrade performance.

        Args:
            baseline_overshoot: Average overshoot from cycles before auto-apply,
                               used as reference for degradation detection.
        """
        self._validation.start_validation_mode(baseline_overshoot)

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
        return self._validation.add_validation_cycle(metrics)

    def is_in_validation_mode(self) -> bool:
        """Check if currently in validation mode.

        Returns:
            True if validation mode is active, False otherwise.
        """
        return self._validation.is_in_validation_mode()

    def check_auto_apply_limits(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
    ) -> Optional[str]:
        """Check if auto-apply is allowed based on safety limits.

        Performs four safety checks before allowing auto-apply:
        1. Lifetime limit: Maximum total auto-applies (prevents runaway drift)
        2. Seasonal limit: Maximum auto-applies per 90-day season
        3. Drift limit: Maximum cumulative drift from physics baseline
        4. Seasonal shift block: Cooldown period after weather regime change

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain

        Returns:
            None if all checks pass (OK to auto-apply),
            Error message string if any check fails (blocked).
        """
        return self._validation.check_auto_apply_limits(
            current_kp, current_ki, current_kd,
            self._heating_auto_apply_count,
            self._cooling_auto_apply_count,
            self._pid_history
        )

    def record_seasonal_shift(self) -> None:
        """Record that a seasonal shift has occurred.

        Sets the last_seasonal_shift timestamp to now, which starts
        the SEASONAL_SHIFT_BLOCK_DAYS cooldown period for auto-apply.
        This prevents auto-applying PID changes during weather regime transitions
        when system behavior may be unstable.
        """
        self._validation.record_seasonal_shift()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize AdaptiveLearner state to a dictionary in v5 format with v4 backward compatibility.

        Delegates to learner_serialization module for actual serialization logic.

        Returns:
            Dictionary containing:
            - v5 mode-keyed structure (heating/cooling sub-dicts)
            - v4 backward-compatible top-level keys (cycle_history, auto_apply_count, etc.)
        """
        return learner_to_dict(
            heating_cycle_history=self._heating_cycle_history,
            cooling_cycle_history=self._cooling_cycle_history,
            heating_auto_apply_count=self._heating_auto_apply_count,
            cooling_auto_apply_count=self._cooling_auto_apply_count,
            heating_convergence_confidence=self._heating_convergence_confidence,
            cooling_convergence_confidence=self._cooling_convergence_confidence,
            pid_history=self._pid_history,
            last_adjustment_time=self._last_adjustment_time,
            consecutive_converged_cycles=self._consecutive_converged_cycles,
            pid_converged_for_ke=self._pid_converged_for_ke,
        )

    def restore_from_dict(self, data: Dict[str, Any]) -> None:
        """Restore AdaptiveLearner state from a dictionary.

        Performs in-place restoration by clearing existing state and
        repopulating from the provided data dictionary.

        Delegates to learner_serialization module for actual deserialization logic.

        Supports both v4 (flat) and v5 (mode-keyed) formats for backward compatibility.

        Args:
            data: Dictionary containing either:
                v4 format: cycle_history, auto_apply_count, etc. at top level
                v5 format: heating/cooling sub-dicts with mode-specific data
        """
        # Delegate to serialization module for parsing
        restored = restore_learner_from_dict(data)

        # Clear existing state
        self._heating_cycle_history.clear()
        self._cooling_cycle_history.clear()

        # Apply restored state to instance attributes
        self._heating_cycle_history = restored["heating_cycle_history"]
        self._cooling_cycle_history = restored["cooling_cycle_history"]
        self._heating_auto_apply_count = restored["heating_auto_apply_count"]
        self._cooling_auto_apply_count = restored["cooling_auto_apply_count"]
        self._heating_convergence_confidence = restored["heating_convergence_confidence"]
        self._cooling_convergence_confidence = restored["cooling_convergence_confidence"]
        self._pid_history = restored["pid_history"]
        self._last_adjustment_time = restored["last_adjustment_time"]
        self._consecutive_converged_cycles = restored["consecutive_converged_cycles"]
        self._pid_converged_for_ke = restored["pid_converged_for_ke"]
