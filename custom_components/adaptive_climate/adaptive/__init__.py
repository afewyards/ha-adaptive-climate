"""Adaptive learning module for Adaptive Climate."""

from __future__ import annotations

try:
    from .thermal_rates import ThermalRateLearner
    from .cycle_analysis import (
        PhaseAwareOvershootTracker,
        CycleMetrics,
        calculate_overshoot,
        calculate_undershoot,
        count_oscillations,
        calculate_settling_time,
    )
    from .pid_rules import (
        PIDRule,
        PIDRuleResult,
        evaluate_pid_rules,
        detect_rule_conflicts,
        resolve_rule_conflicts,
    )
    from .persistence import LearningDataStore
    from .pwm_tuning import calculate_pwm_adjustment, ValveCycleTracker

    __all__ = [
        "CycleMetrics",
        "LearningDataStore",
        "PIDRule",
        "PIDRuleResult",
        "PhaseAwareOvershootTracker",
        "ThermalRateLearner",
        "ValveCycleTracker",
        "calculate_overshoot",
        "calculate_pwm_adjustment",
        "calculate_settling_time",
        "calculate_undershoot",
        "count_oscillations",
        "detect_rule_conflicts",
        "evaluate_pid_rules",
        "resolve_rule_conflicts",
    ]
except ImportError:
    # Handle case where module is imported in unusual way (e.g., test path manipulation)
    __all__ = []
