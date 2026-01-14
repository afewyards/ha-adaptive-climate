"""Adaptive learning module for Adaptive Thermostat."""

try:
    from .thermal_rates import ThermalRateLearner
    __all__ = ["ThermalRateLearner"]
except ImportError:
    # Handle case where module is imported in unusual way (e.g., test path manipulation)
    __all__ = []
