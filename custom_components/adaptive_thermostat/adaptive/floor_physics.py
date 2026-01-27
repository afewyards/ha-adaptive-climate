"""Floor heating physics calculations for Adaptive Thermostat.

This module provides functions to validate floor construction configurations
and calculate thermal properties for floor heating systems.
"""

from typing import Dict, List


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
