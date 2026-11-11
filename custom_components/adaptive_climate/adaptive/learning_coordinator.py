"""LearningCoordinator — orchestrates AdaptiveLearner, ValidationManager, ConfidenceTracker.

Contains the heavier method implementations extracted from AdaptiveLearner to
keep learning.py under the 800-line limit. AdaptiveLearner retains thin
delegates for backward compatibility; new code should prefer LearningCoordinator.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
import logging

from homeassistant.util import dt as dt_util

from ..const import (
    PID_LIMITS,
    MIN_CYCLES_FOR_LEARNING,
    MIN_ADJUSTMENT_INTERVAL,
    MIN_ADJUSTMENT_CYCLES,
    MAX_UNDERSHOOT_KI_MULTIPLIER,
    CONFIDENCE_INCREASE_PER_GOOD_CYCLE,
    CONVERGENCE_CONFIDENCE_HIGH,
    PIDChangeReason,
)
from .learning_adjustments import (
    get_last_adjustment_time_from_history,
    get_physics_baseline_ki_from_history,
    check_convergence,
    check_rate_limit,
    compute_cycle_averages,
    apply_rules_to_gains,
)
from .cycle_weight import CycleOutcome
from .pid_rules import evaluate_pid_rules, detect_rule_conflicts, resolve_rule_conflicts
from ..helpers.hvac_mode import mode_to_str, get_hvac_heat_mode, get_hvac_cool_mode

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode

    from .learning import AdaptiveLearner
    from .cycle_analysis import CycleMetrics
    from .learning_state import LearningState

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# calculate_pid_adjustment — full implementation (called from AdaptiveLearner delegate)
# ---------------------------------------------------------------------------


def calculate_pid_adjustment(
    learner: AdaptiveLearner,
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
    """Calculate PID adjustments based on observed cycle performance.

    Implements priority-based rule conflict resolution, convergence detection,
    and hybrid (time + cycle) rate limiting. See AdaptiveLearner.calculate_pid_adjustment
    docstring for full parameter documentation.
    """
    if mode is None:
        mode = get_hvac_heat_mode()
    cycle_history = learner._cooling_cycle_history if mode == get_hvac_cool_mode() else learner._heating_cycle_history
    convergence_confidence = (
        learner._cooling_convergence_confidence
        if mode == get_hvac_cool_mode()
        else learner._heating_convergence_confidence
    )

    learner._last_skip_reason = None

    # Auto-apply safety gates
    if check_auto_apply:
        _pid_history = learner._pid_gains_manager.get_history(mode) if learner._pid_gains_manager is not None else []
        gates_passed, min_interval_hours, min_adjustment_cycles, min_cycles = (
            learner._auto_apply.check_auto_apply_safety_gates(
                validation_manager=learner._validation,
                confidence_tracker=learner._confidence,
                current_kp=current_kp,
                current_ki=current_ki,
                current_kd=current_kd,
                outdoor_temp=outdoor_temp,
                pid_history=_pid_history,
                mode=mode,
                contribution_tracker=learner._contribution_tracker,
            )
        )
        if not gates_passed:
            learner._last_skip_reason = "gates_not_passed"
            return None

    # Hybrid rate limiting
    if check_rate_limit(
        learner._last_adjustment_time,
        learner._cycles_since_last_adjustment,
        min_interval_hours,
        min_adjustment_cycles,
    ):
        learner._last_skip_reason = "rate_limited"
        return None

    if len(cycle_history) < min_cycles:
        _LOGGER.debug(
            "Insufficient cycles for learning (%s mode): %d < %d",
            mode_to_str(mode),
            len(cycle_history),
            min_cycles,
        )
        learner._last_skip_reason = "insufficient_data"
        return None

    # Compute averaged metrics (with outlier rejection and disturbed-cycle filtering)
    averages = compute_cycle_averages(cycle_history, min_cycles, learner._heating_type)
    if averages is None:
        learner._last_skip_reason = "insufficient_usable_data"
        return None

    # Check convergence — skip adjustments if system is well-tuned
    if check_convergence(
        learner._convergence_thresholds,
        averages["avg_overshoot"],
        averages["avg_oscillations"],
        averages["avg_settling_time"],
        averages["avg_rise_time"],
        avg_inter_cycle_drift=averages["avg_inter_cycle_drift"],
        avg_settling_mae=averages["avg_settling_mae"],
        avg_undershoot=averages["avg_undershoot"],
    ):
        _LOGGER.info("Skipping PID adjustment - system has converged")
        learner._last_skip_reason = "converged"
        return None

    # Evaluate applicable rules with hysteresis tracking
    rule_results = evaluate_pid_rules(
        averages["avg_overshoot"],
        averages["avg_undershoot"],
        averages["avg_oscillations"],
        averages["avg_rise_time"],
        averages["avg_settling_time"],
        recent_rise_times=averages["rise_time_values"],
        recent_outdoor_temps=averages["outdoor_temp_values"],
        state_tracker=learner._rule_state_tracker,
        rule_thresholds=learner._rule_thresholds,
        decay_contribution=averages["avg_decay_contribution"],
        integral_at_tolerance_entry=averages["avg_integral_at_tolerance"],
        avg_inter_cycle_drift=averages["avg_inter_cycle_drift"],
    )

    if not rule_results:
        _LOGGER.debug("No PID rules triggered - metrics within acceptable ranges")
        learner._last_skip_reason = "no_rules_triggered"
        return None

    # Filter oscillation rules in PWM mode (PWM cycles are expected behaviour)
    if pwm_seconds > 0:
        from ..const import RULE_PRIORITY_OSCILLATION

        original_count = len(rule_results)
        rule_results = [r for r in rule_results if r.rule.priority != RULE_PRIORITY_OSCILLATION]
        if len(rule_results) < original_count:
            _LOGGER.debug(
                "PWM mode (period=%ss): filtered %d oscillation rule(s).",
                pwm_seconds,
                original_count - len(rule_results),
            )
        if not rule_results:
            _LOGGER.debug("All rules filtered in PWM mode - no adjustments needed")
            learner._last_skip_reason = "no_rules_triggered"
            return None

    # Detect and resolve conflicts
    conflicts = detect_rule_conflicts(rule_results)
    if conflicts:
        _LOGGER.info("Detected %d PID rule conflict(s)", len(conflicts))
        rule_results = resolve_rule_conflicts(rule_results, conflicts)

    # Apply rules with learning rate scaling
    learning_rate = learner.get_learning_rate_multiplier(convergence_confidence)
    if learning_rate != 1.0:
        _LOGGER.info(
            "Applying learning rate multiplier: %.2fx (confidence: %.2f, mode: %s)",
            learning_rate,
            convergence_confidence,
            mode_to_str(mode),
        )

    new_kp, new_ki, new_kd = apply_rules_to_gains(rule_results, learning_rate, current_kp, current_ki, current_kd)

    # Enforce PID limits
    new_kp = max(PID_LIMITS["kp_min"], min(PID_LIMITS["kp_max"], new_kp))
    new_ki = max(PID_LIMITS["ki_min"], min(PID_LIMITS["ki_max"], new_ki))
    new_kd = max(PID_LIMITS["kd_min"], min(PID_LIMITS["kd_max"], new_kd))

    return {"kp": new_kp, "ki": new_ki, "kd": new_kd}


# ---------------------------------------------------------------------------
# update_convergence_confidence — full implementation
# ---------------------------------------------------------------------------


def update_convergence_confidence(learner: AdaptiveLearner, metrics: CycleMetrics, mode: HVACMode = None) -> None:
    """Update convergence confidence based on cycle performance with weighted learning.

    Confidence increases on good cycles and decreases on poor ones. Uses weighted
    cycle learning (recovery vs maintenance) with per-heating-type caps.
    """
    if mode is None:
        mode = get_hvac_heat_mode()

    if mode == get_hvac_cool_mode():
        current_confidence = learner._confidence._cooling_convergence_confidence
    else:
        current_confidence = learner._confidence._heating_convergence_confidence

    thresholds = learner._convergence_thresholds
    tier1_base = 0.4
    is_stable = current_confidence >= (tier1_base * 0.8)
    is_recovery = metrics.starting_delta is not None and learner._weight_calculator.is_recovery_cycle(
        metrics.starting_delta, is_stable
    )

    rise_time_ok = (
        metrics.rise_time is not None
        if is_recovery
        else (metrics.rise_time is None or metrics.rise_time <= thresholds["rise_time_max"])
    )

    is_good_cycle = (
        (metrics.overshoot is None or metrics.overshoot <= thresholds["overshoot_max"])
        and (metrics.undershoot is None or metrics.undershoot <= thresholds.get("undershoot_max", 0.2))
        and metrics.oscillations <= thresholds["oscillations_max"]
        and (metrics.settling_time is None or metrics.settling_time <= thresholds["settling_time_max"])
        and rise_time_ok
    )

    # Determine cycle outcome
    if is_good_cycle:
        outcome = CycleOutcome.CLEAN
    elif metrics.overshoot is not None and metrics.overshoot > thresholds["overshoot_max"]:
        outcome = CycleOutcome.OVERSHOOT
    elif (metrics.undershoot is not None and metrics.undershoot > thresholds.get("undershoot_max", 0.2)) or (
        is_recovery and metrics.rise_time is None
    ):
        outcome = CycleOutcome.UNDERSHOOT
    else:
        outcome = CycleOutcome.CLEAN

    # Calculate cycle weight
    weight = 1.0
    if metrics.starting_delta is not None:
        is_stable_for_weight = current_confidence >= (tier1_base * 0.8)
        weight = learner._weight_calculator.calculate_weight(
            starting_delta=metrics.starting_delta,
            is_stable=is_stable_for_weight,
            outcome=outcome,
            effective_duty=None,
            outdoor_temp=metrics.outdoor_temp_avg,
            is_night_setback_recovery=False,
        )
        if learner._weight_calculator.is_recovery_cycle(metrics.starting_delta, is_stable_for_weight):
            learner._contribution_tracker.add_recovery_cycle(mode)

    if is_good_cycle:
        weighted_gain = CONFIDENCE_INCREASE_PER_GOOD_CYCLE * weight

        # Apply maintenance cap if cycle type is known
        is_recovery_known: bool | None = None
        is_maintenance = False
        if metrics.starting_delta is not None:
            is_stable_check = current_confidence >= (tier1_base * 0.8)
            is_recovery_known = learner._weight_calculator.is_recovery_cycle(metrics.starting_delta, is_stable_check)
            is_maintenance = not is_recovery_known

        if is_maintenance:
            actual_gain = learner._contribution_tracker.apply_maintenance_gain(weighted_gain, mode)
        else:
            actual_gain = weighted_gain

        if metrics.rise_time is not None:
            learner._contribution_tracker.apply_heating_rate_gain(actual_gain)

        current_confidence = min(CONVERGENCE_CONFIDENCE_HIGH, current_confidence + actual_gain)
        _LOGGER.debug(
            "Convergence confidence (%s mode) increased to %.2f "
            "(weight=%.2f, actual_gain=%.3f, is_recovery=%s, outcome=%s: "
            "overshoot=%.2f°C, oscillations=%d, settling=%.1fmin, starting_delta=%.2f°C)",
            mode_to_str(mode),
            current_confidence,
            weight,
            actual_gain,
            is_recovery_known,
            outcome.value,
            metrics.overshoot or 0.0,
            metrics.oscillations,
            metrics.settling_time or 0.0,
            metrics.starting_delta or 0.0,
        )
    else:
        current_confidence = max(0.0, current_confidence - CONFIDENCE_INCREASE_PER_GOOD_CYCLE * 0.5)
        _LOGGER.debug(
            "Convergence confidence (%s mode) decreased to %.2f (poor cycle, outcome=%s)",
            mode_to_str(mode),
            current_confidence,
            outcome.value,
        )

    if mode == get_hvac_cool_mode():
        learner._confidence._cooling_convergence_confidence = current_confidence
        learner._confidence._cooling_cycle_count += 1
    else:
        learner._confidence._heating_convergence_confidence = current_confidence
        learner._confidence._heating_cycle_count += 1


# ---------------------------------------------------------------------------
# check_undershoot_adjustment — full implementation
# ---------------------------------------------------------------------------


def check_undershoot_adjustment(
    learner: AdaptiveLearner,
    cycles_completed: int,
    current_ki: float,
    pid_history: list[dict] | None = None,
    mode: HVACMode = None,
) -> float | None:
    """Check if Ki needs boosting for persistent undershoot (real-time + cycle modes).

    Returns the new Ki if an adjustment was applied, or None otherwise.
    """
    if mode == get_hvac_cool_mode():
        return None

    last_boost_utc = None
    physics_baseline_ki: float | None = None
    if pid_history:
        undershoot_utc = get_last_adjustment_time_from_history(pid_history, PIDChangeReason.UNDERSHOOT_BOOST.value)
        chronic_utc = get_last_adjustment_time_from_history(pid_history, "chronic_approach_ki_boost")
        if undershoot_utc and chronic_utc:
            last_boost_utc = max(undershoot_utc, chronic_utc)
        else:
            last_boost_utc = undershoot_utc or chronic_utc
        physics_baseline_ki = get_physics_baseline_ki_from_history(pid_history)

    if not learner._undershoot_detector.should_adjust_ki(
        cycles_completed, last_boost_utc, current_ki, physics_baseline_ki
    ):
        return None

    multiplier = learner._undershoot_detector.apply_adjustment(current_ki, physics_baseline_ki)
    new_ki = current_ki * multiplier

    _LOGGER.info(
        "Undershoot detected: increasing Ki from %.4f to %.4f (%.1f%% increase, "
        "time_below=%.1fh, thermal_debt=%.2f°C·h, consecutive_failures=%d, cumulative=%.2fx)",
        current_ki,
        new_ki,
        (multiplier - 1.0) * 100,
        learner._undershoot_detector.time_below_target / 3600.0,
        learner._undershoot_detector.thermal_debt,
        learner._undershoot_detector._consecutive_failures,
        learner._undershoot_detector.cumulative_ki_multiplier,
    )

    # Decrease confidence — persistent undershoot signals poor tuning
    if mode is None:
        mode = get_hvac_heat_mode()

    if mode == get_hvac_cool_mode():
        current_confidence = learner._confidence._cooling_convergence_confidence
    else:
        current_confidence = learner._confidence._heating_convergence_confidence

    new_confidence = max(0.0, current_confidence - CONFIDENCE_INCREASE_PER_GOOD_CYCLE * 0.5)

    if mode == get_hvac_cool_mode():
        learner._confidence._cooling_convergence_confidence = new_confidence
    else:
        learner._confidence._heating_convergence_confidence = new_confidence

    _LOGGER.debug(
        "Convergence confidence (%s mode) decreased to %.2f due to undershoot detection",
        mode_to_str(mode),
        new_confidence,
    )

    return new_ki


# ---------------------------------------------------------------------------
# apply_restored_state — state assignment helper for restore_from_dict
# ---------------------------------------------------------------------------


def apply_restored_state(learner: AdaptiveLearner, data: dict[str, Any]) -> None:
    """Restore all AdaptiveLearner state from a serialized dict.

    Calls restore_learner_from_dict for parsing/migration, then applies
    the resulting values to the learner's attributes in-place.

    Args:
        learner: AdaptiveLearner instance to restore into.
        data: Raw serialized dict (any supported version).
    """
    from .learner_serialization import restore_learner_from_dict
    from .confidence_contribution import ConfidenceContributionTracker
    from .heating_rate_learner import HeatingRateLearner

    restored = restore_learner_from_dict(data)

    # Core state
    learner._heating_cycle_history.clear()
    learner._cooling_cycle_history.clear()
    learner._heating_cycle_history = restored["heating_cycle_history"]
    learner._cooling_cycle_history = restored["cooling_cycle_history"]
    learner._heating_auto_apply_count = restored["heating_auto_apply_count"]
    learner._cooling_auto_apply_count = restored["cooling_auto_apply_count"]
    learner._heating_convergence_confidence = restored["heating_convergence_confidence"]
    learner._cooling_convergence_confidence = restored["cooling_convergence_confidence"]
    learner._last_adjustment_time = restored["last_adjustment_time"]
    learner._consecutive_converged_cycles = restored["consecutive_converged_cycles"]
    learner._cycles_since_last_adjustment = restored.get("cycles_since_last_adjustment", 0)
    learner._pid_converged_for_ke = restored["pid_converged_for_ke"]

    # C04: Restore cycle counts from history length
    learner._confidence._heating_cycle_count = len(learner._heating_cycle_history)
    learner._confidence._cooling_cycle_count = len(learner._cooling_cycle_history)

    # Undershoot detector state
    undershoot_state = restored.get("undershoot_detector_state", {})
    if undershoot_state:
        learner._undershoot_detector.time_below_target = undershoot_state.get("time_below_target", 0.0)
        learner._undershoot_detector.thermal_debt = undershoot_state.get("thermal_debt", 0.0)
        learner._undershoot_detector._consecutive_failures = undershoot_state.get("consecutive_failures", 0)
        raw_cumulative = float(undershoot_state.get("cumulative_ki_multiplier", 1.0))
        learner._undershoot_detector.cumulative_ki_multiplier = min(
            MAX_UNDERSHOOT_KI_MULTIPLIER, max(1.0, raw_cumulative)
        )
        last_adj_raw = undershoot_state.get("last_adjustment_time")
        if isinstance(last_adj_raw, str):
            try:
                adj_dt = datetime.fromisoformat(last_adj_raw)
                if adj_dt.tzinfo is None:
                    adj_dt = adj_dt.replace(tzinfo=timezone.utc)
                if adj_dt <= dt_util.utcnow():
                    learner._undershoot_detector.last_adjustment_time = adj_dt
            except (ValueError, TypeError):
                _LOGGER.info("Could not parse undershoot last_adjustment_time: %s — cooldown reset", last_adj_raw)

    # Contribution tracker state
    contribution_state = restored.get("contribution_tracker_state", {})
    if contribution_state:
        learner._contribution_tracker = ConfidenceContributionTracker.from_dict(
            contribution_state, learner._heating_type
        )

    # Heating rate learner state
    heating_rate_learner_state = restored.get("heating_rate_learner_state", {})
    if heating_rate_learner_state:
        learner._heating_rate_learner = HeatingRateLearner.from_dict(heating_rate_learner_state, learner._heating_type)
    else:
        learner._heating_rate_learner = HeatingRateLearner(learner._heating_type)

    # Seasonal shift timestamp (v11) — restore auto-apply cooldown across restarts
    learner._validation._last_seasonal_shift = restored.get("last_seasonal_shift")

    # Historic scan (if enabled at construction)
    if learner._chronic_approach_historic_scan:
        from .learning_adjustments import perform_historic_scan

        perform_historic_scan(learner._heating_cycle_history, learner._undershoot_detector)


# ---------------------------------------------------------------------------
# LearningCoordinator class — simplified API for orchestrating learning
# ---------------------------------------------------------------------------


class LearningCoordinator:
    """Orchestrates AdaptiveLearner, ValidationManager, and ConfidenceTracker.

    Provides a higher-level interface for climate.py to eventually migrate to,
    while AdaptiveLearner retains backward-compatible delegates for existing callers.
    """

    def __init__(self, learner: AdaptiveLearner) -> None:
        """Initialise with a configured AdaptiveLearner.

        Args:
            learner: The AdaptiveLearner instance to orchestrate.
        """
        self._learner = learner

    def calculate_pid_adjustment(
        self, current_kp: float, current_ki: float, current_kd: float, **kwargs: Any
    ) -> dict[str, float] | None:
        """Delegate to the free-function implementation."""
        return calculate_pid_adjustment(self._learner, current_kp, current_ki, current_kd, **kwargs)

    def update_convergence_confidence(self, metrics: CycleMetrics, mode: HVACMode = None) -> None:
        """Delegate to the free-function implementation."""
        update_convergence_confidence(self._learner, metrics, mode)

    def check_undershoot_adjustment(
        self, cycles_completed: int, current_ki: float, pid_history: list[dict] | None = None, mode: HVACMode = None
    ) -> float | None:
        """Delegate to the free-function implementation."""
        return check_undershoot_adjustment(self._learner, cycles_completed, current_ki, pid_history, mode)

    def get_state(self, mode: HVACMode = None) -> LearningState:
        """Build and return a LearningState snapshot for the current learner state."""
        from .learning_state import get_learning_state

        return get_learning_state(self._learner, mode)

    @property
    def learner(self) -> AdaptiveLearner:
        """Return the wrapped AdaptiveLearner instance."""
        return self._learner
