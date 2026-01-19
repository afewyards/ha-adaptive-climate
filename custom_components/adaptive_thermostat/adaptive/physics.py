"""Physics-based PID initialization for Adaptive Thermostat.

This module provides functions to calculate initial PID parameters based on
thermal properties of zones and heating system characteristics.
"""

from typing import Tuple, Optional, Dict, List


# Energy rating to insulation quality mapping
# Higher insulation = less impact from outdoor temperature = lower initial Ke
# Values restored to correct scale in v0.7.1 (100x from v0.7.0 incorrect scaling)
ENERGY_RATING_TO_INSULATION: Dict[str, float] = {
    "A++++": 0.1,   # Outstanding insulation - extremely minimal outdoor impact
    "A+++": 0.15,   # Excellent insulation - minimal outdoor impact
    "A++": 0.25,    # Very good insulation
    "A+": 0.35,     # Good insulation
    "A": 0.45,      # Standard good insulation
    "B": 0.55,      # Moderate insulation
    "C": 0.7,       # Poor insulation - significant outdoor impact
    "D": 0.85,      # Very poor insulation
    "E": 1.0,       # Minimal insulation
    "F": 1.15,      # Below minimum standards
    "G": 1.3,       # No effective insulation
}


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
    floor_construction: Optional[Dict] = None,
    area_m2: Optional[float] = None,
    heating_type: Optional[str] = None,
) -> float:
    """Calculate thermal time constant (tau) in hours.

    The thermal time constant represents how quickly a zone responds to
    heating changes. It can be estimated from zone volume or energy rating,
    and adjusted for window heat loss. For floor heating systems, it can
    be further adjusted based on floor construction thermal properties.

    Args:
        volume_m3: Zone volume in cubic meters. If provided, tau is estimated
                   as volume_m3 / 50 (larger spaces respond more slowly).
        energy_rating: Building energy efficiency rating (A+++, A++, A+, A, B, C, D).
                       Higher ratings mean better insulation and slower cooling.
        window_area_m2: Total window/glass area in square meters.
        floor_area_m2: Zone floor area in square meters (needed for window ratio).
        window_rating: Glazing type (single, double, hr, hr+, hr++, hr+++, triple).
                       Higher ratings mean better insulation. Default: hr++.
        floor_construction: Optional floor construction configuration dict with:
                           - 'layers': List of layer dicts
                           - 'pipe_spacing_mm': Pipe spacing in millimeters
                           When provided with heating_type='floor_hydronic', adjusts tau
                           based on floor thermal properties.
        area_m2: Zone area in square meters. Required if floor_construction provided.
        heating_type: Heating system type. Required if floor_construction provided.

    Returns:
        Thermal time constant in hours.

    Raises:
        ValueError: If neither volume_m3 nor energy_rating is provided.
        ValueError: If floor_construction provided but area_m2 is missing.
        ValueError: If floor_construction provided but heating_type is missing.
    """
    if volume_m3 is not None:
        # Estimate tau based on zone volume
        # Larger spaces have higher thermal mass and respond more slowly
        tau_base = volume_m3 / 50.0
    elif energy_rating is not None:
        # Estimate tau based on energy efficiency rating
        # Better insulation = slower temperature changes = higher tau
        rating_map = {
            "A++++": 10.0,  # Outstanding insulation, extremely slow response
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

    # Adjust tau for floor construction if provided (only for floor_hydronic)
    if floor_construction is not None:
        # Validate required parameters
        if area_m2 is None:
            raise ValueError("area_m2 is required when floor_construction is provided")
        if heating_type is None:
            raise ValueError("heating_type is required when floor_construction is provided")

        # Only apply floor construction modifier for floor hydronic heating
        if heating_type == 'floor_hydronic':
            # Extract layers and pipe spacing from floor_construction dict
            layers = floor_construction.get('layers')
            pipe_spacing_mm = floor_construction.get('pipe_spacing_mm', 150)

            # Calculate floor thermal properties
            floor_props = calculate_floor_thermal_properties(
                layers=layers,
                area_m2=area_m2,
                pipe_spacing_mm=pipe_spacing_mm
            )

            # Apply tau modifier
            tau_modifier = floor_props['tau_modifier']
            tau_base *= tau_modifier

    return tau_base


def calculate_power_scaling_factor(
    heating_type: str,
    area_m2: Optional[float],
    max_power_w: Optional[float],
    supply_temperature: Optional[float] = None,
) -> float:
    """Calculate power scaling factor for PID gains based on heater power and supply temp.

    The process gain (system response to heating input) depends on heater power
    and supply water temperature. Systems with lower power density (W/m²) or
    lower supply temperature respond more slowly and need higher PID gains.

    This function calculates a scaling factor that adjusts PID gains to account
    for heater power and supply temperature deviating from the baseline for each
    heating type.

    Args:
        heating_type: Type of heating system (floor_hydronic, radiator, etc.)
        area_m2: Zone floor area in square meters. Required if max_power_w provided.
        max_power_w: Total heater power in watts. If None, power scaling is 1.0.
        supply_temperature: Actual supply water temperature in °C. If None, temp
                           scaling is 1.0. Lower temps than reference → higher gains.

    Returns:
        Combined scaling factor (0.25 - 4.0). Values > 1.0 indicate undersized system
        or low supply temp (needs higher gains), < 1.0 indicates oversized system
        or high supply temp (needs lower gains).

    Formula:
        power_factor = baseline_power_density / actual_power_density
        temp_factor = reference_ΔT / actual_ΔT  (where ΔT = supply_temp - 20°C)
        combined = power_factor * temp_factor

    Example:
        - 50m² zone with floor_hydronic (baseline 20 W/m², reference 45°C)
        - 500W heater installed (10 W/m² actual), 35°C supply temp
        - power_factor = 20 / 10 = 2.0
        - temp_factor = (45-20) / (35-20) = 25 / 15 = 1.67
        - combined = 2.0 * 1.67 = 3.33 (higher gains for low-temp undersized system)
    """
    # Import here to avoid circular dependency
    from ..const import HEATING_TYPE_CHARACTERISTICS

    # Get characteristics for heating type
    heating_chars = HEATING_TYPE_CHARACTERISTICS.get(
        heating_type, HEATING_TYPE_CHARACTERISTICS["convector"]
    )

    # Calculate power scaling factor
    if max_power_w is None or area_m2 is None or area_m2 <= 0:
        power_factor = 1.0
    else:
        baseline_power_w_m2 = heating_chars.get("baseline_power_w_m2", 60)
        actual_power_w_m2 = max_power_w / area_m2
        # Power scaling: inverse relationship
        # Lower power density → higher gains needed (slower response)
        power_factor = baseline_power_w_m2 / actual_power_w_m2

    # Calculate supply temperature scaling factor
    if supply_temperature is not None:
        ref_supply = heating_chars.get("reference_supply_temp", 55.0)
        # Reference ΔT = reference supply temp - room temp (20°C)
        ref_delta_t = ref_supply - 20.0
        # Actual ΔT = actual supply temp - room temp, clamped to 5-60°C range
        actual_delta_t = max(5.0, min(60.0, supply_temperature - 20.0))
        # Lower supply temp → smaller ΔT → higher gains needed
        temp_factor = ref_delta_t / actual_delta_t
        # Clamp temp_factor to 0.5 - 2.0 range for safety
        temp_factor = max(0.5, min(2.0, temp_factor))
    else:
        temp_factor = 1.0

    # Combined scaling factor
    combined_factor = power_factor * temp_factor

    # Clamp combined to 0.25x - 4.0x range for safety
    # 0.25x = system 4x oversized / high temp (very fast, needs conservative gains)
    # 4.0x = system 4x undersized / low temp (very slow, needs aggressive gains)
    combined_factor = max(0.25, min(4.0, combined_factor))

    return combined_factor


def calculate_initial_pid(
    thermal_time_constant: float,
    heating_type: str = "floor_hydronic",
    area_m2: Optional[float] = None,
    max_power_w: Optional[float] = None,
    supply_temperature: Optional[float] = None,
) -> Tuple[float, float, float]:
    """Calculate initial PID parameters using hybrid multi-point empirical model.

    Implements improved physics-based initialization with better tau scaling:
    - Kp ∝ 1/(tau × √tau) - proportional gain reduces with thermal mass
    - Ki ∝ 1/tau - integral responds slower for high thermal mass systems
    - Kd ∝ tau - derivative damping increases with thermal mass

    This hybrid approach combines reference building profiles with continuous
    tau-based scaling for better adaptation to diverse building characteristics.

    Args:
        thermal_time_constant: System thermal time constant in hours (tau).
        heating_type: Type of heating system. Valid values:
            - "floor_hydronic": Floor heating with water (very slow, high mass)
            - "radiator": Traditional radiators (moderate response)
            - "convector": Convection heaters (faster response)
            - "forced_air": Forced air heating (fast response)
        area_m2: Zone floor area in square meters. Required for power scaling.
        max_power_w: Total heater power in watts. If provided with area_m2,
                     PID gains are scaled based on power density.
        supply_temperature: Supply water temperature in °C. If provided, PID gains
                           are scaled based on deviation from reference supply temp.
                           Lower temps than reference → higher gains needed.

    Returns:
        Tuple of (Kp, Ki, Kd) PID parameters.

    Notes:
        - Reference points calibrated from real-world systems across tau range
        - Continuous scaling allows interpolation between reference points
        - Power and supply temp scaling account for undersized/low-temp systems
        - v0.7.1: Hybrid model with improved tau scaling formulas
    """
    # Multi-point reference building profiles (v0.7.1)
    # Each profile represents empirical data from different building types
    # Reference profiles calibrated at specific tau values
    reference_profiles = {
        "floor_hydronic": [
            # (tau_hours, kp, ki, kd) - calibrated reference points
            (2.0, 0.45, 2.0, 1.4),     # Well-insulated floor heating, fast response
            (4.0, 0.30, 1.2, 2.5),     # Standard floor heating, moderate mass
            (6.0, 0.22, 0.8, 3.3),     # High thermal mass floor, slow response (reduced from 3.5 to fit kd_max=3.3)
            (8.0, 0.18, 0.6, 3.2),     # Very slow floor heating, high mass (reduced from 4.2 to fit kd_max=3.3)
        ],
        "radiator": [
            (1.5, 0.70, 3.0, 1.2),     # Fast radiator system
            (3.0, 0.50, 2.0, 2.0),     # Standard radiator
            (5.0, 0.36, 1.3, 2.8),     # Slow radiator, high mass building
        ],
        "convector": [
            (1.0, 1.10, 6.0, 0.7),     # Fast convector, low mass
            (2.5, 0.80, 4.0, 1.2),     # Standard convector
            (4.0, 0.60, 2.8, 1.8),     # Slow convector, higher mass
        ],
        "forced_air": [
            (0.5, 1.80, 12.0, 0.4),    # Very fast forced air, minimal mass
            (1.5, 1.20, 8.0, 0.8),     # Standard forced air
            (3.0, 0.85, 5.5, 1.3),     # Slow forced air, higher mass building
        ],
    }

    # Get reference profiles for heating type, default to radiator
    profiles = reference_profiles.get(heating_type, reference_profiles["radiator"])

    # Find bracketing reference points for interpolation
    tau = thermal_time_constant if thermal_time_constant > 0 else 2.0

    # Kp scaling: Kp ∝ 1/(tau × √tau)
    # Rationale:
    #   - Base: Kp ∝ 1/tau (Ziegler-Nichols for first-order systems)
    #   - Additional √tau factor accounts for thermal mass damping
    #   - Higher thermal mass needs proportionally lower Kp to prevent oscillation
    #   - Formula validated against reference profiles from real-world systems

    # If tau is below lowest reference point, use improved scaling from lowest point
    if tau <= profiles[0][0]:
        tau_ref, kp_ref, ki_ref, kd_ref = profiles[0]
        # Scale using improved formulas: Kp ∝ 1/(tau × √tau), Ki ∝ 1/tau, Kd ∝ tau
        tau_ratio = tau_ref / tau
        Kp = kp_ref * tau_ratio * (tau_ratio ** 0.5)  # Kp ∝ 1/(tau × √tau)
        Ki = ki_ref * tau_ratio                         # Ki ∝ 1/tau
        Kd = kd_ref / tau_ratio                         # Kd ∝ tau
    # If tau is above highest reference point, use improved scaling from highest point
    elif tau >= profiles[-1][0]:
        tau_ref, kp_ref, ki_ref, kd_ref = profiles[-1]
        tau_ratio = tau_ref / tau
        Kp = kp_ref * tau_ratio * (tau_ratio ** 0.5)  # Kp ∝ 1/(tau × √tau)
        Ki = ki_ref * tau_ratio                         # Ki ∝ 1/tau
        Kd = kd_ref / tau_ratio                         # Kd ∝ tau
    # Otherwise, interpolate between bracketing reference points
    else:
        # Find bracketing points
        lower_profile = profiles[0]
        upper_profile = profiles[-1]
        for i in range(len(profiles) - 1):
            if profiles[i][0] <= tau <= profiles[i + 1][0]:
                lower_profile = profiles[i]
                upper_profile = profiles[i + 1]
                break

        # Linear interpolation between reference points
        tau_lower, kp_lower, ki_lower, kd_lower = lower_profile
        tau_upper, kp_upper, ki_upper, kd_upper = upper_profile

        # Interpolation factor (0.0 at lower, 1.0 at upper)
        alpha = (tau - tau_lower) / (tau_upper - tau_lower)

        Kp = kp_lower + alpha * (kp_upper - kp_lower)
        Ki = ki_lower + alpha * (ki_upper - ki_lower)
        Kd = kd_lower + alpha * (kd_upper - kd_lower)

    # Apply power and supply temperature scaling if configured
    # Undersized systems or low supply temps need higher gains
    scaling_factor = calculate_power_scaling_factor(
        heating_type, area_m2, max_power_w, supply_temperature
    )
    Kp *= scaling_factor
    Ki *= scaling_factor
    # Note: Kd is NOT scaled - derivative term responds to rate of change,
    # not absolute heating capacity or supply temperature

    return (round(Kp, 4), round(Ki, 5), round(Kd, 2))


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


def calculate_initial_ke(
    energy_rating: Optional[str] = None,
    window_area_m2: Optional[float] = None,
    floor_area_m2: Optional[float] = None,
    window_rating: str = "hr++",
    heating_type: str = "floor_hydronic",
) -> float:
    """Calculate initial Ke (outdoor temperature compensation) parameter.

    Ke adjusts the PID output based on outdoor temperature difference from
    a reference point. A well-tuned Ke reduces integral wind-up during
    outdoor temperature changes.

    The initial value is estimated based on:
    - Building energy rating (insulation quality)
    - Window area and rating (heat loss through glazing)
    - Heating system type (response characteristics)

    Args:
        energy_rating: Building energy efficiency rating (A+++, A++, A+, A, B, C, D, E, F, G).
                       Higher ratings mean better insulation and lower Ke.
        window_area_m2: Total window/glass area in square meters.
        floor_area_m2: Zone floor area in square meters (needed for window ratio).
        window_rating: Glazing type (single, double, hr, hr+, hr++, hr+++, triple).
                       Poorer glazing increases outdoor temperature impact.
        heating_type: Type of heating system. Slower systems (floor_hydronic)
                      benefit more from outdoor compensation.

    Returns:
        Initial Ke value (typically 0.1 - 0.8 for well-insulated buildings,
        0.8 - 1.5 for poorly insulated buildings). Restored to correct scale in v0.7.1.
    """
    # Base Ke from energy rating
    if energy_rating:
        base_ke = ENERGY_RATING_TO_INSULATION.get(energy_rating.upper(), 0.45)
    else:
        # Default to moderate insulation if not specified
        base_ke = 0.45

    # Adjust for window heat loss
    if window_area_m2 and floor_area_m2 and floor_area_m2 > 0:
        # Get U-value for glazing type
        u_value = GLAZING_U_VALUES.get(window_rating.lower(), 1.1)

        # Window ratio: what fraction of floor area is glass
        window_ratio = window_area_m2 / floor_area_m2

        # Higher window ratio and worse glazing = more outdoor impact = higher Ke
        # Normalized to HR++ (U=1.1) at 20% window ratio as baseline
        window_factor = (u_value / 1.1) * (window_ratio / 0.2)

        # Adjust Ke up to +50% for high glass / poor insulation
        base_ke *= (1.0 + min(window_factor * 0.25, 0.5))

    # Adjust for heating system type
    # Slower systems benefit more from outdoor compensation
    # These are multiplicative factors (not absolute values), applied to base_ke
    # No scaling needed for v0.7.1 - these remain as dimensionless multipliers
    heating_type_factors = {
        "floor_hydronic": 1.2,   # Slow response - more benefit from Ke
        "radiator": 1.0,         # Baseline
        "convector": 0.8,        # Faster response - less Ke needed
        "forced_air": 0.6,       # Fast response - minimal Ke needed
    }
    type_factor = heating_type_factors.get(heating_type, 1.0)
    base_ke *= type_factor

    # Round to 4 decimal places (was 2, now more precision needed)
    return round(base_ke, 4)


def calculate_ke_wind(
    energy_rating: Optional[str] = None,
    window_area_m2: Optional[float] = None,
    floor_area_m2: Optional[float] = None,
    window_rating: str = "hr++",
) -> float:
    """Calculate wind speed compensation coefficient (Ke_wind per m/s).

    Wind speed increases convective heat loss from building surfaces,
    particularly affecting windows and poorly insulated walls. This
    coefficient scales the outdoor temperature compensation based on
    wind speed.

    The wind compensation is applied as: Ke_wind * wind_speed * dext
    This means wind amplifies the outdoor temperature effect.

    Args:
        energy_rating: Building energy efficiency rating. Better insulation
                      reduces wind penetration.
        window_area_m2: Total window/glass area in square meters.
        floor_area_m2: Zone floor area in square meters.
        window_rating: Glazing type. Poorer glazing more affected by wind.

    Returns:
        Ke_wind coefficient (per m/s), typically 0.01 - 0.03.
        Default 0.02 per m/s for moderate insulation.
    """
    # Base wind coefficient - moderate insulation
    base_ke_wind = 0.02

    # Adjust for building insulation quality
    if energy_rating:
        # Better insulation = less wind impact
        rating_factors = {
            "A++++": 0.5,  # Excellent air sealing
            "A+++": 0.6,
            "A++": 0.7,
            "A+": 0.8,
            "A": 0.9,
            "B": 1.0,    # Baseline
            "C": 1.2,
            "D": 1.4,
            "E": 1.6,
            "F": 1.8,
            "G": 2.0,    # Poor air sealing, significant wind penetration
        }
        base_ke_wind *= rating_factors.get(energy_rating.upper(), 1.0)

    # Adjust for window exposure
    if window_area_m2 and floor_area_m2 and floor_area_m2 > 0:
        u_value = GLAZING_U_VALUES.get(window_rating.lower(), 1.1)
        window_ratio = window_area_m2 / floor_area_m2

        # More windows and worse glazing = more wind impact
        window_factor = (u_value / 1.1) * (window_ratio / 0.2)
        base_ke_wind *= (1.0 + min(window_factor * 0.3, 0.5))

    # Round to 3 decimal places
    return round(base_ke_wind, 3)


def validate_floor_construction(floor_config: dict) -> list[str]:
    """Validate floor heating construction configuration.

    This function validates the floor construction configuration to ensure
    all parameters are within acceptable ranges and material specifications
    are complete.

    Args:
        floor_config: Dict containing:
            - 'layers': List of layer dicts (required)
            - 'pipe_spacing_mm': Pipe spacing in millimeters (required)

    Returns:
        List of validation error strings. Empty list if valid.

    Example:
        >>> config = {
        ...     'layers': [
        ...         {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
        ...         {'type': 'screed', 'material': 'cement', 'thickness_mm': 50}
        ...     ],
        ...     'pipe_spacing_mm': 150
        ... }
        >>> errors = validate_floor_construction(config)
        >>> len(errors)
        0
    """
    # Import here to avoid circular dependency
    from ..const import (
        TOP_FLOOR_MATERIALS,
        SCREED_MATERIALS,
        PIPE_SPACING_EFFICIENCY,
        FLOOR_THICKNESS_LIMITS,
    )

    errors = []

    # Check pipe_spacing_mm is valid
    pipe_spacing_mm = floor_config.get('pipe_spacing_mm')
    if pipe_spacing_mm is None:
        errors.append("pipe_spacing_mm is required")
    elif pipe_spacing_mm not in PIPE_SPACING_EFFICIENCY:
        valid_spacings = sorted(PIPE_SPACING_EFFICIENCY.keys())
        errors.append(
            f"pipe_spacing_mm must be one of {valid_spacings}, got {pipe_spacing_mm}"
        )

    # Check layers list exists and is not empty
    layers = floor_config.get('layers')
    if layers is None:
        errors.append("layers is required")
        return errors  # Can't proceed without layers

    if not isinstance(layers, list):
        errors.append("layers must be a list")
        return errors  # Can't proceed if not a list

    if len(layers) == 0:
        errors.append("layers list cannot be empty")
        return errors  # Can't proceed with empty list

    # Track layer order for validation
    top_floor_indices = []
    screed_indices = []

    # Validate each layer
    for i, layer in enumerate(layers):
        layer_type = layer.get('type')
        material_name = layer.get('material')
        thickness_mm = layer.get('thickness_mm')

        # Validate layer type
        if layer_type not in ['top_floor', 'screed']:
            errors.append(
                f"Layer {i}: type must be 'top_floor' or 'screed', got '{layer_type}'"
            )
            continue

        # Track layer indices for order validation
        if layer_type == 'top_floor':
            top_floor_indices.append(i)
        elif layer_type == 'screed':
            screed_indices.append(i)

        # Validate thickness
        if thickness_mm is None:
            errors.append(f"Layer {i} ({layer_type}): thickness_mm is required")
        elif not isinstance(thickness_mm, (int, float)) or thickness_mm <= 0:
            errors.append(
                f"Layer {i} ({layer_type}): thickness_mm must be a positive number, got {thickness_mm}"
            )
        else:
            # Check thickness range for layer type
            min_thickness, max_thickness = FLOOR_THICKNESS_LIMITS[layer_type]
            if thickness_mm < min_thickness or thickness_mm > max_thickness:
                errors.append(
                    f"Layer {i} ({layer_type}): thickness_mm must be between "
                    f"{min_thickness}-{max_thickness}mm, got {thickness_mm}mm"
                )

        # Validate material specification
        # Material can be specified either by name (lookup) or custom properties
        has_custom_properties = all(
            prop in layer for prop in ['conductivity', 'density', 'specific_heat']
        )

        if not has_custom_properties:
            # Must have valid material name for lookup
            if material_name is None:
                errors.append(
                    f"Layer {i} ({layer_type}): material name is required "
                    "(or provide custom conductivity, density, and specific_heat)"
                )
            else:
                # Check if material exists in lookup
                if layer_type == 'top_floor':
                    if material_name not in TOP_FLOOR_MATERIALS:
                        errors.append(
                            f"Layer {i} ({layer_type}): unknown material '{material_name}'"
                        )
                elif layer_type == 'screed':
                    if material_name not in SCREED_MATERIALS:
                        errors.append(
                            f"Layer {i} ({layer_type}): unknown material '{material_name}'"
                        )
        else:
            # Validate custom properties are valid numbers
            for prop in ['conductivity', 'density', 'specific_heat']:
                value = layer.get(prop)
                if not isinstance(value, (int, float)) or value <= 0:
                    errors.append(
                        f"Layer {i} ({layer_type}): {prop} must be a positive number, got {value}"
                    )

    # Validate layer order: all top_floor layers must precede all screed layers
    if top_floor_indices and screed_indices:
        max_top_floor_index = max(top_floor_indices)
        min_screed_index = min(screed_indices)
        if max_top_floor_index > min_screed_index:
            errors.append(
                "Layer order invalid: all 'top_floor' layers must precede all 'screed' layers"
            )

    return errors


def calculate_floor_thermal_properties(
    layers: List[Dict],
    area_m2: float,
    pipe_spacing_mm: int = 150,
) -> Dict[str, float]:
    """Calculate thermal properties for floor heating construction.

    This function calculates thermal mass, thermal resistance, and tau modifier
    for floor heating systems based on construction layers. The tau modifier is
    used to adjust the base thermal time constant based on floor construction.

    Args:
        layers: List of layer dicts, each containing:
            - 'type': 'top_floor' or 'screed'
            - 'material': Material name (looked up in TOP_FLOOR_MATERIALS or SCREED_MATERIALS)
            - 'thickness_mm': Layer thickness in millimeters
            Optional overrides (if material lookup should be skipped):
            - 'conductivity': Thermal conductivity in W/(m·K)
            - 'density': Density in kg/m³
            - 'specific_heat': Specific heat capacity in J/(kg·K)
        area_m2: Floor area in square meters.
        pipe_spacing_mm: Pipe spacing in millimeters (100, 150, 200, or 300).
                         Default: 150mm. Used to calculate heat distribution efficiency.

    Returns:
        Dict containing:
            - 'thermal_mass_kj_k': Total thermal mass in kJ/K
            - 'thermal_resistance': Total thermal resistance in (m²·K)/W
            - 'tau_modifier': Multiplier for base tau (accounts for thermal mass
                              relative to reference 50mm cement screed)

    Example:
        >>> layers = [
        ...     {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
        ...     {'type': 'screed', 'material': 'cement', 'thickness_mm': 50}
        ... ]
        >>> props = calculate_floor_thermal_properties(layers, area_m2=50.0)
        >>> props['thermal_mass_kj_k']  # Total thermal mass
        462.0
        >>> props['tau_modifier']  # Relative to reference screed
        1.1
    """
    # Import here to avoid circular dependency
    from ..const import TOP_FLOOR_MATERIALS, SCREED_MATERIALS, PIPE_SPACING_EFFICIENCY

    total_thermal_mass = 0.0  # J/K (will convert to kJ/K at end)
    total_thermal_resistance = 0.0  # (m²·K)/W

    for layer in layers:
        layer_type = layer.get('type')
        material_name = layer.get('material')
        thickness_mm = layer.get('thickness_mm')

        if thickness_mm is None or thickness_mm <= 0:
            raise ValueError(f"Layer must have valid thickness_mm > 0, got {thickness_mm}")

        # Convert thickness to meters
        thickness_m = thickness_mm / 1000.0

        # Get material properties (either from lookup or custom overrides)
        if 'conductivity' in layer and 'density' in layer and 'specific_heat' in layer:
            # Custom material properties provided
            conductivity = layer['conductivity']
            density = layer['density']
            specific_heat = layer['specific_heat']
        else:
            # Lookup material properties from const.py
            if layer_type == 'top_floor':
                material_props = TOP_FLOOR_MATERIALS.get(material_name)
            elif layer_type == 'screed':
                material_props = SCREED_MATERIALS.get(material_name)
            else:
                raise ValueError(f"Unknown layer type '{layer_type}', must be 'top_floor' or 'screed'")

            if material_props is None:
                raise ValueError(f"Unknown material '{material_name}' for layer type '{layer_type}'")

            conductivity = material_props['conductivity']
            density = material_props['density']
            specific_heat = material_props['specific_heat']

        # Calculate thermal mass for this layer: thickness × area × density × specific_heat
        # Result in J/K
        layer_mass = thickness_m * area_m2 * density * specific_heat
        total_thermal_mass += layer_mass

        # Calculate thermal resistance for this layer: thickness / conductivity
        # Result in (m²·K)/W
        layer_resistance = thickness_m / conductivity
        total_thermal_resistance += layer_resistance

    # Convert total thermal mass from J/K to kJ/K
    thermal_mass_kj_k = total_thermal_mass / 1000.0

    # Calculate reference mass (50mm cement screed)
    # Reference: 0.05m × area_m2 × 2000 kg/m³ × 1000 J/(kg·K) = ... J/K
    reference_thickness_m = 0.05
    reference_density = 2000  # kg/m³
    reference_specific_heat = 1000  # J/(kg·K)
    reference_mass = (
        reference_thickness_m * area_m2 * reference_density * reference_specific_heat
    )  # J/K

    # Calculate tau modifier (ratio of actual mass to reference mass)
    tau_modifier = total_thermal_mass / reference_mass

    # Apply pipe spacing efficiency factor
    # Lookup efficiency for given pipe spacing (default to 150mm if not found)
    spacing_efficiency = PIPE_SPACING_EFFICIENCY.get(pipe_spacing_mm, 0.87)
    effective_tau_modifier = tau_modifier / spacing_efficiency

    return {
        'thermal_mass_kj_k': round(thermal_mass_kj_k, 2),
        'thermal_resistance': round(total_thermal_resistance, 4),
        'tau_modifier': round(effective_tau_modifier, 2),
    }
