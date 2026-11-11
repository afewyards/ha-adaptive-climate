"""Thermal rate learning and adaptive PID adjustments for Adaptive Climate."""

from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util
from typing import Any, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode

from ..const import (
    MIN_CYCLES_FOR_LEARNING,
    MAX_CYCLE_HISTORY,
    MIN_ADJUSTMENT_INTERVAL,
    MIN_ADJUSTMENT_CYCLES,
    get_convergence_thresholds,
    get_rule_thresholds,
    HeatingType as HeatingTypeEnum,
)

# RuleStateTracker is used by __init__; others are backward-compat re-exports
from .pid_rules import RuleStateTracker
from .pid_rules import PIDRule, PIDRuleResult, evaluate_pid_rules, detect_rule_conflicts, resolve_rule_conflicts

# Backward-compat re-exports (tests import these from learning.py)
from .thermal_rates import ThermalRateLearner
from .cycle_analysis import (
    PhaseAwareOvershootTracker,
    CycleMetrics,
    calculate_overshoot,
    calculate_undershoot,
    count_oscillations,
)
from .pwm_tuning import calculate_pwm_adjustment, ValveCycleTracker

# Import validation manager for safety checks
from .validation import ValidationManager

# Import confidence tracker for convergence tracking
from .confidence import ConfidenceTracker

# Import serialization utilities for state persistence
from .learner_serialization import learner_to_dict

# Import auto-apply manager for safety gates and threshold management
from .auto_apply import AutoApplyManager, get_auto_apply_thresholds

# Import undershoot detector for persistent temperature deficit detection
from .undershoot_detector import UndershootDetector

from .cycle_weight import CycleWeightCalculator
from .confidence_contribution import ConfidenceContributionTracker

# Import heating rate learner for unified heating rate learning
from .heating_rate_learner import HeatingRateLearner

# Import HVAC mode helpers
from ..helpers.hvac_mode import mode_to_str, get_hvac_heat_mode, get_hvac_cool_mode

# Import computation helpers from extracted modules
from .learning_adjustments import (
    check_convergence as _check_convergence_fn,
    check_rate_limit as _check_rate_limit_fn,
    update_convergence_tracking as _update_convergence_tracking_fn,
)
from .learning_coordinator import (
    calculate_pid_adjustment as _calculate_pid_adjustment,
    update_convergence_confidence as _update_convergence_confidence,
    check_undershoot_adjustment as _check_undershoot_adjustment,
    apply_restored_state,
)
from .learning_health_monitor import HealthReport, LearningHealthMonitor

_LOGGER = logging.getLogger(__name__)

# Explicit re-exports — keeps Pyright happy and documents the public surface
__all__ = [
    "AdaptiveLearner",
    "CycleMetrics",
    "PIDRule",
    "PIDRuleResult",
    "PhaseAwareOvershootTracker",
    "ThermalRateLearner",
    "ValveCycleTracker",
    "calculate_overshoot",
    "calculate_pwm_adjustment",
    "calculate_undershoot",
    "count_oscillations",
    "detect_rule_conflicts",
    "evaluate_pid_rules",
    "get_auto_apply_thresholds",
    "resolve_rule_conflicts",
]


# Adaptive learning (CycleMetrics imported from cycle_analysis)


class AdaptiveLearner:
    """Adaptive PID tuning based on observed heating cycle performance."""

    def __init__(
        self,
        max_history: int = MAX_CYCLE_HISTORY,
        heating_type: str | None = None,
        chronic_approach_historic_scan: bool = False,
    ):
        """
        Initialize the AdaptiveLearner.

        Args:
            max_history: Maximum number of cycles to retain in history (FIFO eviction)
            heating_type: Heating system type (floor_hydronic, radiator, convector, forced_air)
                         Used to select appropriate convergence thresholds
            chronic_approach_historic_scan: If True, scan existing cycle history on init for
                                           chronic approach patterns
        """
        # Mode-specific cycle histories
        self._heating_cycle_history: list[CycleMetrics] = []
        self._cooling_cycle_history: list[CycleMetrics] = []
        self._max_history = max_history
        self._heating_type = heating_type
        self._convergence_thresholds = get_convergence_thresholds(heating_type)
        self._rule_thresholds = get_rule_thresholds(heating_type)
        self._last_adjustment_time: datetime | None = None
        # Convergence tracking for Ke learning activation
        self._consecutive_converged_cycles: int = 0
        self._pid_converged_for_ke: bool = False
        # Hybrid rate limiting: track cycles since last adjustment
        self._cycles_since_last_adjustment: int = 0
        self._last_skip_reason: str | None = None
        # Rule state tracker with hysteresis to prevent oscillation
        self._rule_state_tracker = RuleStateTracker()

        # Confidence tracker for convergence confidence and auto-apply counts
        self._confidence = ConfidenceTracker(self._convergence_thresholds)

        # Validation manager for safety checks and validation mode
        self._validation = ValidationManager()

        # Auto-apply manager for safety gates and threshold-based decisions
        self._auto_apply = AutoApplyManager(heating_type)

        # Undershoot detector for persistent temperature deficit detection
        if heating_type is None:
            undershoot_heating_type = HeatingTypeEnum.RADIATOR
        else:
            undershoot_heating_type = HeatingTypeEnum(heating_type)
        self._undershoot_detector = UndershootDetector(undershoot_heating_type)

        # Store historic scan flag (used by unified detector)
        self._chronic_approach_historic_scan = chronic_approach_historic_scan

        # Weighted learning components
        self._weight_calculator = CycleWeightCalculator(undershoot_heating_type)
        self._contribution_tracker = ConfidenceContributionTracker(undershoot_heating_type)

        # Heating rate learner for unified heating rate learning
        self._heating_rate_learner = HeatingRateLearner(heating_type or "radiator")

        # Optional reference to PIDGainsManager; wired from climate.py after init.
        self._pid_gains_manager: Any = None

    def set_pid_gains_manager(self, manager: Any) -> None:
        """Wire the PIDGainsManager so seasonal-limit safety gates use real history."""
        self._pid_gains_manager = manager

    @property
    def cycle_history(self) -> list[CycleMetrics]:
        """Return heating cycle history (backward compat for sensors/pid_tuning)."""
        return self._heating_cycle_history

    @cycle_history.setter
    def cycle_history(self, value: list[CycleMetrics]) -> None:
        """Set heating cycle history (primarily for testing)."""
        self._heating_cycle_history = value

    # Backward-compatible aliases for private attributes (used by tests)
    @property
    def _cycle_history(self) -> list[CycleMetrics]:
        """Backward-compatible alias for _heating_cycle_history."""
        return self._heating_cycle_history

    @_cycle_history.setter
    def _cycle_history(self, value: list[CycleMetrics]) -> None:
        self._heating_cycle_history = value

    @property
    def _heating_convergence_confidence(self) -> float:
        return self._confidence._heating_convergence_confidence

    @_heating_convergence_confidence.setter
    def _heating_convergence_confidence(self, value: float) -> None:
        self._confidence._heating_convergence_confidence = value

    @property
    def _cooling_convergence_confidence(self) -> float:
        return self._confidence._cooling_convergence_confidence

    @_cooling_convergence_confidence.setter
    def _cooling_convergence_confidence(self, value: float) -> None:
        self._confidence._cooling_convergence_confidence = value

    @property
    def _heating_auto_apply_count(self) -> int:
        return self._confidence._heating_auto_apply_count

    @_heating_auto_apply_count.setter
    def _heating_auto_apply_count(self, value: int) -> None:
        self._confidence._heating_auto_apply_count = value

    @property
    def _cooling_auto_apply_count(self) -> int:
        return self._confidence._cooling_auto_apply_count

    @_cooling_auto_apply_count.setter
    def _cooling_auto_apply_count(self, value: int) -> None:
        self._confidence._cooling_auto_apply_count = value

    @property
    def _auto_apply_count(self) -> int:
        """Backward-compatible alias for _heating_auto_apply_count."""
        return self._confidence._heating_auto_apply_count

    @_auto_apply_count.setter
    def _auto_apply_count(self, value: int) -> None:
        self._confidence._heating_auto_apply_count = value

    def increment_auto_apply_count(self, mode: HVACMode = None) -> int:
        """Increment and return the auto-apply counter for the specified mode."""
        self._confidence.increment_auto_apply_count(mode)
        return self._confidence.get_auto_apply_count(mode)

    @property
    def _convergence_confidence(self) -> float:
        """Backward-compatible alias for _heating_convergence_confidence."""
        return self._confidence._heating_convergence_confidence

    @_convergence_confidence.setter
    def _convergence_confidence(self, value: float) -> None:
        self._confidence._heating_convergence_confidence = value

    # Backward-compatible aliases for validation manager attributes (used by tests)
    @property
    def _validation_mode(self) -> bool:
        return self._validation.is_in_validation_mode()

    @property
    def _validation_cycles(self) -> list[CycleMetrics]:
        return self._validation._validation_cycles

    @property
    def _validation_baseline_overshoot(self) -> float | None:
        return self._validation._validation_baseline_overshoot

    @property
    def _last_seasonal_check(self) -> datetime | None:
        return self._validation._last_seasonal_check

    @_last_seasonal_check.setter
    def _last_seasonal_check(self, value: datetime | None) -> None:
        self._validation._last_seasonal_check = value

    @property
    def _last_seasonal_shift(self) -> datetime | None:
        return self._validation._last_seasonal_shift

    @_last_seasonal_shift.setter
    def _last_seasonal_shift(self, value: datetime | None) -> None:
        self._validation._last_seasonal_shift = value

    @property
    def _outdoor_temp_history(self) -> list[float]:
        return self._validation._outdoor_temp_history

    @property
    def _physics_baseline_kp(self) -> float | None:
        return self._validation._physics_baseline_kp

    @property
    def _physics_baseline_ki(self) -> float | None:
        return self._validation._physics_baseline_ki

    @property
    def _physics_baseline_kd(self) -> float | None:
        return self._validation._physics_baseline_kd

    def add_cycle_metrics(self, metrics: CycleMetrics, mode: HVACMode = None) -> None:
        """Add a cycle's performance metrics to history (FIFO eviction at max_history)."""
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            cycle_history = self._cooling_cycle_history
        else:
            cycle_history = self._heating_cycle_history

        cycle_history.append(metrics)

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

        self._cycles_since_last_adjustment += 1

        # Feed to undershoot detector for cycle-mode chronic approach detection
        cycle_duration_minutes = None
        if metrics.rise_time is not None or metrics.settling_time is not None:
            cycle_duration_minutes = (metrics.rise_time or 0.0) + (metrics.settling_time or 0.0)
        self._undershoot_detector.add_cycle(metrics, cycle_duration_minutes)

        # FIFO eviction
        if len(cycle_history) > self._max_history:
            evicted_count = len(cycle_history) - self._max_history
            if mode == get_hvac_cool_mode():
                del self._cooling_cycle_history[:evicted_count]
            else:
                del self._heating_cycle_history[:evicted_count]
            _LOGGER.debug(
                "Cycle history (%s mode) exceeded max (%d), evicted %d oldest entries",
                mode_to_str(mode),
                self._max_history,
                evicted_count,
            )

    def get_cycle_count(self, mode: HVACMode = None) -> int:
        """Get number of stored cycle metrics for the specified mode."""
        if mode is None:
            mode = get_hvac_heat_mode()
        return len(self._cooling_cycle_history) if mode == get_hvac_cool_mode() else len(self._heating_cycle_history)

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
        """Check if system has converged. Delegates to learning_adjustments.check_convergence."""
        return _check_convergence_fn(
            self._convergence_thresholds,
            avg_overshoot,
            avg_oscillations,
            avg_settling_time,
            avg_rise_time,
            avg_inter_cycle_drift,
            avg_settling_mae,
            avg_undershoot,
        )

    def _check_rate_limit(
        self,
        min_interval_hours: int = MIN_ADJUSTMENT_INTERVAL,
        min_cycles: int = MIN_ADJUSTMENT_CYCLES,
    ) -> bool:
        """Check hybrid rate limit. Delegates to learning_adjustments.check_rate_limit."""
        return _check_rate_limit_fn(
            self._last_adjustment_time, self._cycles_since_last_adjustment, min_interval_hours, min_cycles
        )

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
        outdoor_temp: float | None = None,
        mode: HVACMode = None,
    ) -> dict[str, float] | None:
        """Calculate PID adjustments. Full implementation in learning_coordinator.py."""
        return _calculate_pid_adjustment(
            self,
            current_kp,
            current_ki,
            current_kd,
            min_cycles,
            min_interval_hours,
            min_adjustment_cycles,
            pwm_seconds,
            check_auto_apply,
            outdoor_temp,
            mode,
        )

    def get_last_adjustment_time(self) -> datetime | None:
        """Get the timestamp of the last PID adjustment."""
        return self._last_adjustment_time

    def mark_adjustment_applied(self) -> None:
        """Start the rate-limit cooldown after new gains were committed."""
        self._last_adjustment_time = dt_util.utcnow()
        self._cycles_since_last_adjustment = 0

    def clear_history(self) -> None:
        """Clear cycle history, reset tracking, and exit validation mode."""
        self._heating_cycle_history.clear()
        self._cooling_cycle_history.clear()
        self._last_adjustment_time = None
        self._cycles_since_last_adjustment = 0
        self._confidence.reset_confidence()
        self._validation.reset_validation_state()
        self._undershoot_detector.reset_all()

        if self._heating_type is None:
            heating_type_enum = HeatingTypeEnum.RADIATOR
        elif isinstance(self._heating_type, str):
            heating_type_enum = HeatingTypeEnum(self._heating_type)
        else:
            heating_type_enum = self._heating_type
        self._contribution_tracker = ConfidenceContributionTracker(heating_type_enum)
        self._heating_rate_learner = HeatingRateLearner(self._heating_type or "radiator")

    def set_physics_baseline(self, kp: float, ki: float, kd: float) -> None:
        """Set the physics-based baseline PID values for drift calculation."""
        self._validation.set_physics_baseline(kp, ki, kd)

    def calculate_drift_from_baseline(self, current_kp: float, current_ki: float, current_kd: float) -> float:
        """Calculate max percentage drift from physics baseline (0.0 if no baseline)."""
        return self._validation.calculate_drift_from_baseline(current_kp, current_ki, current_kd)

    def update_convergence_tracking(self, metrics: CycleMetrics) -> bool:
        """Update convergence tracking for Ke learning activation. Delegates to learning_adjustments."""
        return _update_convergence_tracking_fn(self, metrics)

    def is_pid_converged_for_ke(self) -> bool:
        """Check if PID has converged sufficiently for Ke learning."""
        return self._pid_converged_for_ke

    def get_consecutive_converged_cycles(self) -> int:
        """Get the number of consecutive converged cycles."""
        return self._consecutive_converged_cycles

    def reset_ke_convergence(self) -> None:
        """Reset Ke convergence tracking (call when PID values change)."""
        old_converged = self._pid_converged_for_ke
        old_count = self._consecutive_converged_cycles
        self._consecutive_converged_cycles = 0
        self._pid_converged_for_ke = False
        if old_converged or old_count > 0:
            _LOGGER.info("Ke convergence reset (was: converged=%s, consecutive=%d)", old_converged, old_count)

    def get_convergence_confidence(self, mode: HVACMode = None) -> float:
        """Get current convergence confidence for specified mode."""
        return self._confidence.get_convergence_confidence(mode)

    def get_auto_apply_count(self, mode: HVACMode = None) -> int:
        """Get number of auto-applied PID adjustments for specified mode."""
        return self._confidence.get_auto_apply_count(mode)

    def update_convergence_confidence(self, metrics: CycleMetrics, mode: HVACMode = None) -> None:
        """Update convergence confidence based on cycle performance. Delegates to learning_coordinator."""
        _update_convergence_confidence(self, metrics, mode)

    def check_performance_degradation(self, baseline_window: int = 10, mode: HVACMode = None) -> bool:
        """Check if recent performance has degraded compared to baseline."""
        if mode is None:
            mode = get_hvac_heat_mode()
        cycle_history = self._cooling_cycle_history if mode == get_hvac_cool_mode() else self._heating_cycle_history
        return self._validation.check_performance_degradation(cycle_history, baseline_window)

    def check_seasonal_shift(self, outdoor_temp: float | None = None) -> bool:
        """Check if outdoor temperature regime has shifted significantly."""
        return self._validation.check_seasonal_shift(outdoor_temp)

    def apply_confidence_decay(self) -> None:
        """Apply daily confidence decay (2% per day) to both modes."""
        self._confidence.apply_confidence_decay()

    def get_learning_rate_multiplier(self, confidence: float | None = None) -> float:
        """Get learning rate multiplier in [0.5, 2.0] based on convergence confidence."""
        return self._confidence.get_learning_rate_multiplier(confidence)

    def get_heating_rate(self, delta: float, outdoor_temp: float) -> tuple[float, str]:
        """Get learned heating rate for given conditions."""
        return self._heating_rate_learner.get_heating_rate(delta, outdoor_temp)

    def start_validation_mode(self, baseline_overshoot: float) -> None:
        """Start validation mode after auto-applying PID changes."""
        self._validation.start_validation_mode(baseline_overshoot)

    def add_validation_cycle(self, metrics: CycleMetrics) -> str | None:
        """Add a cycle to validation tracking; returns None, 'success', or 'rollback'."""
        return self._validation.add_validation_cycle(metrics)

    def is_in_validation_mode(self) -> bool:
        """Check if currently in validation mode."""
        return self._validation.is_in_validation_mode()

    def check_auto_apply_limits(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
    ) -> str | None:
        """Check if auto-apply is allowed based on safety limits.

        Returns None if OK, or an error message string if blocked.
        """
        _pid_history = self._pid_gains_manager.get_history(None) if self._pid_gains_manager is not None else []
        return self._validation.check_auto_apply_limits(
            current_kp,
            current_ki,
            current_kd,
            self._heating_auto_apply_count,
            self._cooling_auto_apply_count,
            _pid_history,
        )

    def record_seasonal_shift(self) -> None:
        """Record that a seasonal shift has occurred (starts cooldown for auto-apply)."""
        self._validation.record_seasonal_shift()

    def update_undershoot_detector(
        self, temp: float, setpoint: float, dt_seconds: float, cold_tolerance: float
    ) -> None:
        """Update real-time undershoot detector with current temperature reading."""
        self._undershoot_detector.update_realtime(temp, setpoint, dt_seconds, cold_tolerance)

    def check_undershoot_adjustment(
        self,
        cycles_completed: int,
        current_ki: float,
        pid_history: list[dict] | None = None,
        mode: HVACMode = None,
    ) -> float | None:
        """Check if Ki needs boosting for persistent undershoot. Delegates to learning_coordinator."""
        return _check_undershoot_adjustment(self, cycles_completed, current_ki, pid_history, mode)

    def check_physics_rate_underperformance(
        self,
        tau: float | None = None,
        area_m2: float | None = None,
        max_power_w: float | None = None,
        supply_temperature: float | None = None,
    ) -> dict | None:
        """Check if learned heating rate is below physics-based expectations.

        Returns a dict with comparison data if underperforming, None otherwise.
        """
        result = self._heating_rate_learner.check_physics_underperformance(
            tau=tau, area_m2=area_m2, max_power_w=max_power_w, supply_temperature=supply_temperature
        )
        if result is None or not result.get("is_underperforming", False):
            return None

        _LOGGER.warning(
            "Physics-based rate check: learned=%.3f°C/h, expected=%.3f°C/h (%.0f%% of expected). "
            "System is underperforming - consider Ki boost of %.2fx",
            result["learned_rate"],
            result["expected_rate"],
            result["ratio"] * 100,
            result.get("suggested_ki_boost", 1.0),
        )
        return result

    @property
    def undershoot_detector(self) -> UndershootDetector:
        """Expose undershoot detector for serialization access."""
        return self._undershoot_detector

    @property
    def health(self) -> HealthReport:
        """Compute and return the current learning health report.

        Aggregates Ki multiplier, PID drift, and maintenance cap usage into a
        single 0–100 score.  Current PID gains are retrieved from the
        PIDGainsManager when available; drift is omitted (0 penalty) otherwise.

        Returns:
            HealthReport with score, status, and per-signal breakdown.
        """
        if self._heating_type is None:
            heating_type_enum = HeatingTypeEnum.RADIATOR
        elif isinstance(self._heating_type, str):
            heating_type_enum = HeatingTypeEnum(self._heating_type)
        else:
            heating_type_enum = self._heating_type

        monitor = LearningHealthMonitor(
            heating_type=heating_type_enum,
            undershoot_detector=self._undershoot_detector,
            validation_manager=self._validation,
            contribution_tracker=self._contribution_tracker,
        )

        # Pull current gains if PIDGainsManager is wired in
        kp: float | None = None
        ki: float | None = None
        kd: float | None = None
        if self._pid_gains_manager is not None:
            gains = self._pid_gains_manager.get_gains()
            kp = gains.kp
            ki = gains.ki
            kd = gains.kd

        return monitor.assess(current_kp=kp, current_ki=ki, current_kd=kd)

    def can_reach_learning_tier(self, tier: int, mode: HVACMode) -> bool:
        """Check if the system has enough recovery cycles to reach a learning tier."""
        return self._contribution_tracker.can_reach_tier(tier, mode)

    def to_dict(self) -> dict[str, Any]:
        """Serialize AdaptiveLearner state to a dict. Delegates to learner_serialization."""
        return learner_to_dict(
            heating_cycle_history=self._heating_cycle_history,
            cooling_cycle_history=self._cooling_cycle_history,
            heating_auto_apply_count=self._heating_auto_apply_count,
            cooling_auto_apply_count=self._cooling_auto_apply_count,
            heating_convergence_confidence=self._heating_convergence_confidence,
            cooling_convergence_confidence=self._cooling_convergence_confidence,
            last_adjustment_time=self._last_adjustment_time,
            cycles_since_last_adjustment=self._cycles_since_last_adjustment,
            consecutive_converged_cycles=self._consecutive_converged_cycles,
            pid_converged_for_ke=self._pid_converged_for_ke,
            undershoot_detector=self._undershoot_detector,
            contribution_tracker=self._contribution_tracker,
            heating_rate_learner=self._heating_rate_learner,
            last_seasonal_shift=self._validation._last_seasonal_shift,
        )

    def restore_from_dict(self, data: dict[str, Any]) -> None:
        """Restore AdaptiveLearner state from a dict. Delegates to learning_coordinator."""
        apply_restored_state(self, data)
