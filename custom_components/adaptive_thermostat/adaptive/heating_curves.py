"""Heating curves for outdoor temperature compensation.

This module provides weather compensation functionality for PID controllers.
Weather compensation adjusts heating output based on outdoor temperature to
improve comfort and reduce energy consumption.
"""

from typing import Optional

from ..const import PID_LIMITS


def calculate_weather_compensation(
    indoor_setpoint: float,
    outdoor_temp: float,
    ke: float = 0.0
) -> float:
    """Calculate weather compensation adjustment.

    Weather compensation increases heating output when outdoor temperature
    is lower than the indoor setpoint, reducing the load on the PID controller.

    Note: Ke values restored in v0.7.1 (100x increase from v0.7.0's incorrect scaling).

    Args:
        indoor_setpoint: Target indoor temperature in °C
        outdoor_temp: Current outdoor temperature in °C
        ke: Weather compensation coefficient (typically 0.1-2.0, restored in v0.7.1)
            - 0.0: No weather compensation
            - 0.5: Mild compensation (well-insulated buildings)
            - 1.0: Moderate compensation (typical buildings)
            - 2.0: Strong compensation (poorly insulated buildings)

    Returns:
        Weather compensation adjustment to add to PID output

    Example:
        >>> calculate_weather_compensation(20.0, 0.0, ke=1.0)
        20.0
        >>> calculate_weather_compensation(20.0, 10.0, ke=1.0)
        10.0
        >>> calculate_weather_compensation(20.0, 20.0, ke=1.0)
        0.0
    """
    if ke == 0.0:
        return 0.0

    # Delta between setpoint and outdoor temp
    delta_temp = indoor_setpoint - outdoor_temp

    # Calculate compensation (positive when outdoor is colder)
    compensation = ke * delta_temp

    return compensation


def calculate_recommended_ke(
    insulation_quality: str = "good",
    heating_type: str = "radiator"
) -> float:
    """Calculate recommended ke (weather compensation coefficient).

    The ke value determines how strongly outdoor temperature affects heating output.
    Higher values mean stronger compensation.

    Note: Values restored in v0.7.1 (100x increase from v0.7.0's incorrect scaling).

    Args:
        insulation_quality: Building insulation quality
            - "excellent": A+++ energy rating, minimal heat loss
            - "good": A/B energy rating, well-insulated
            - "average": C/D energy rating, typical insulation
            - "poor": E/F energy rating, high heat loss
        heating_type: Type of heating system
            - "floor_hydronic": Underfloor heating (low temperature, slow response)
            - "radiator": Traditional radiators (medium temperature)
            - "convector": Convection heaters (fast response)
            - "forced_air": Forced air systems (very fast response)

    Returns:
        Recommended ke coefficient (0.1-2.0, restored in v0.7.1)
    """
    # Base ke values by insulation quality (restored 100x in v0.7.1)
    insulation_ke = {
        "excellent": 0.3,  # A+++ - minimal compensation needed
        "good": 0.5,       # A/B - mild compensation
        "average": 1.0,    # C/D - moderate compensation
        "poor": 1.5,       # E/F - strong compensation
    }

    # Adjustment factors by heating type
    heating_factors = {
        "floor_hydronic": 0.8,  # Lower ke for slow-response systems
        "radiator": 1.0,        # Standard ke
        "convector": 1.1,       # Slightly higher for faster systems
        "forced_air": 1.2,      # Higher ke for very fast systems
    }

    # Get base ke (default to good insulation)
    base_ke = insulation_ke.get(insulation_quality, 0.5)

    # Get heating type factor (default to radiator)
    factor = heating_factors.get(heating_type, 1.0)

    # Calculate recommended ke
    recommended_ke = base_ke * factor

    # Clamp to PID_LIMITS range
    return max(PID_LIMITS["ke_min"], min(PID_LIMITS["ke_max"], recommended_ke))


def apply_outdoor_compensation_to_pid_output(
    pid_output: float,
    indoor_setpoint: float,
    outdoor_temp: Optional[float],
    ke: float = 0.0,
    min_output: float = 0.0,
    max_output: float = 100.0
) -> float:
    """Apply outdoor temperature compensation to PID output.

    This function adds weather compensation to the PID output and clamps
    the result to the specified limits.

    Args:
        pid_output: Base PID controller output (0-100)
        indoor_setpoint: Target indoor temperature in °C
        outdoor_temp: Current outdoor temperature in °C (None if unavailable)
        ke: Weather compensation coefficient
        min_output: Minimum allowed output (default 0.0)
        max_output: Maximum allowed output (default 100.0)

    Returns:
        Compensated PID output, clamped to [min_output, max_output]

    Note:
        If outdoor_temp is None, no compensation is applied and the
        original pid_output is returned (clamped to limits).
    """
    # If no outdoor temp available, return original output (clamped)
    if outdoor_temp is None or ke == 0.0:
        return max(min_output, min(pid_output, max_output))

    # Calculate weather compensation
    compensation = calculate_weather_compensation(
        indoor_setpoint, outdoor_temp, ke
    )

    # Add compensation to PID output
    compensated_output = pid_output + compensation

    # Clamp to limits
    return max(min_output, min(compensated_output, max_output))
