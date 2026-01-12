"""Physics-based PID initialization for Adaptive Thermostat.

This module provides functions to calculate initial PID parameters based on
thermal properties of zones and heating system characteristics.
"""

from typing import Tuple, Optional


# Glazing U-values in W/(m²·K) - lower is better insulation
GLAZING_U_VALUES = {
    "single": 5.8,      # Single pane glass
    "double": 2.8,      # Standard double glazing
    "hr": 2.8,          # HR (same as double)
    "hr+": 1.8,         # HR+ improved
    "hr++": 1.1,        # HR++ high performance
    "hr+++": 0.6,       # Triple glazing
    "triple": 0.6,      # Alias for HR+++
}


def calculate_thermal_time_constant(
    volume_m3: Optional[float] = None,
    energy_rating: Optional[str] = None,
    window_area_m2: Optional[float] = None,
    floor_area_m2: Optional[float] = None,
    window_rating: str = "hr++",
) -> float:
    """Calculate thermal time constant (tau) in hours.

    The thermal time constant represents how quickly a zone responds to
    heating changes. It can be estimated from zone volume or energy rating,
    and adjusted for window heat loss.

    Args:
        volume_m3: Zone volume in cubic meters. If provided, tau is estimated
                   as volume_m3 / 50 (larger spaces respond more slowly).
        energy_rating: Building energy efficiency rating (A+++, A++, A+, A, B, C, D).
                       Higher ratings mean better insulation and slower cooling.
        window_area_m2: Total window/glass area in square meters.
        floor_area_m2: Zone floor area in square meters (needed for window ratio).
        window_rating: Glazing type (single, double, hr, hr+, hr++, hr+++, triple).
                       Higher ratings mean better insulation. Default: hr++.

    Returns:
        Thermal time constant in hours.

    Raises:
        ValueError: If neither volume_m3 nor energy_rating is provided.
    """
    if volume_m3 is not None:
        # Estimate tau based on zone volume
        # Larger spaces have higher thermal mass and respond more slowly
        tau_base = volume_m3 / 50.0
    elif energy_rating is not None:
        # Estimate tau based on energy efficiency rating
        # Better insulation = slower temperature changes = higher tau
        rating_map = {
            "A+++": 8.0,  # Excellent insulation, very slow response
            "A++": 6.0,   # Very good insulation
            "A+": 5.0,    # Good insulation
            "A": 4.0,     # Standard good insulation
            "B": 3.0,     # Moderate insulation
            "C": 2.5,     # Poor insulation
            "D": 2.0,     # Very poor insulation, fast response
        }
        tau_base = rating_map.get(energy_rating.upper(), 4.0)
    else:
        raise ValueError("Either volume_m3 or energy_rating must be provided")

    # Adjust tau for window heat loss if window parameters provided
    if window_area_m2 and floor_area_m2 and floor_area_m2 > 0:
        # Get U-value for glazing type (default to HR++ if unknown)
        u_value = GLAZING_U_VALUES.get(window_rating.lower(), 1.1)

        # Window ratio: what fraction of floor area is glass
        window_ratio = window_area_m2 / floor_area_m2

        # Heat loss factor normalized to HR++ (U=1.1) at 20% window ratio as baseline
        # Higher U-value (worse insulation) and more glass = more heat loss = lower tau
        heat_loss_factor = (u_value / 1.1) * (window_ratio / 0.2)

        # Reduce tau by up to 40% for high glass area / poor insulation
        # Factor of 0.15 means: at baseline (HR++, 20% windows), reduction is 15%
        tau_reduction = min(heat_loss_factor * 0.15, 0.4)
        tau_base *= (1 - tau_reduction)

    return tau_base


def calculate_initial_pid(
    thermal_time_constant: float,
    heating_type: str = "floor_hydronic",
) -> Tuple[float, float, float]:
    """Calculate initial PID parameters using modified Ziegler-Nichols method.

    The Ziegler-Nichols method provides baseline PID tuning based on system
    dynamics. We modify it based on heating system type for better initial values.

    Args:
        thermal_time_constant: System thermal time constant in hours (tau).
        heating_type: Type of heating system. Valid values:
            - "floor_hydronic": Floor heating with water (very conservative)
            - "radiator": Traditional radiators (moderately conservative)
            - "convector": Convection heaters (more aggressive)
            - "forced_air": Forced air heating (most aggressive)

    Returns:
        Tuple of (Kp, Ki, Kd) PID parameters.

    Notes:
        - Floor heating uses very conservative tuning (high tau, slow response)
        - Convectors use more aggressive tuning (faster response)
        - Modified Ziegler-Nichols: Kp = 0.6/tau, Ki = 2*Kp/tau, Kd = Kp*tau/8
    """
    # Heating type modifiers for PID aggressiveness
    # Lower modifier = more conservative = slower response
    heating_modifiers = {
        "floor_hydronic": 0.5,   # Very slow response, high thermal mass
        "radiator": 0.7,          # Moderate response
        "convector": 1.0,         # Standard response
        "forced_air": 1.3,        # Fast response, low thermal mass
    }

    modifier = heating_modifiers.get(heating_type, 0.7)

    # Modified Ziegler-Nichols tuning
    # Base formula: Kp = 0.6 / tau
    Kp = (0.6 / thermal_time_constant) * modifier

    # Ki should integrate slowly for heating systems
    # Base formula: Ki = 2 * Kp / tau
    Ki = (2 * Kp / thermal_time_constant) * modifier

    # Kd provides damping to reduce overshoot
    # Base formula: Kd = Kp * tau / 8
    Kd = (Kp * thermal_time_constant / 8.0) * modifier

    return (round(Kp, 3), round(Ki, 5), round(Kd, 3))


def calculate_initial_pwm_period(heating_type: str = "floor_hydronic") -> int:
    """Calculate initial PWM (Pulse Width Modulation) period in seconds.

    The PWM period determines how frequently the heating actuator cycles
    on/off. Longer periods reduce mechanical wear but slower response.

    Args:
        heating_type: Type of heating system. Valid values:
            - "floor_hydronic": Floor heating with water
            - "radiator": Traditional radiators
            - "convector": Convection heaters
            - "forced_air": Forced air heating

    Returns:
        PWM period in seconds.

    Notes:
        - Floor heating uses long periods to reduce valve cycling
        - Forced air can use shorter periods for faster response
    """
    # PWM period lookup table based on heating system characteristics
    # Longer periods = less wear, slower response
    # Shorter periods = faster response, more wear
    pwm_periods = {
        "floor_hydronic": 900,    # 15 minutes - minimize valve wear
        "radiator": 600,          # 10 minutes - moderate valve cycling
        "convector": 300,         # 5 minutes - faster response
        "forced_air": 180,        # 3 minutes - very fast response
    }

    return pwm_periods.get(heating_type, 600)  # Default to 10 minutes
