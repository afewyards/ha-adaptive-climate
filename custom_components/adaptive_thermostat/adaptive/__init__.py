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
    __all__ = [
        "ThermalRateLearner",
        "PhaseAwareOvershootTracker",
        "CycleMetrics",
        "calculate_overshoot",
        "calculate_undershoot",
        "count_oscillations",
        "calculate_settling_time",
    ]
except ImportError:
    # Handle case where module is imported in unusual way (e.g., test path manipulation)
    __all__ = []
