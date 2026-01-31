"""Adaptive learning module for Adaptive Thermostat."""

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
        "ThermalRateLearner",
        "PhaseAwareOvershootTracker",
        "CycleMetrics",
        "calculate_overshoot",
        "calculate_undershoot",
        "count_oscillations",
        "calculate_settling_time",
        # PID rule engine
        "PIDRule",
        "PIDRuleResult",
        "evaluate_pid_rules",
        "detect_rule_conflicts",
        "resolve_rule_conflicts",
        # Persistence
        "LearningDataStore",
        # PWM tuning
        "calculate_pwm_adjustment",
        "ValveCycleTracker",
    ]
except ImportError:
    # Handle case where module is imported in unusual way (e.g., test path manipulation)
    __all__ = []
