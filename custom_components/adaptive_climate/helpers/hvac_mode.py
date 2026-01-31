"""HVAC mode helper utilities for Adaptive Thermostat.

Provides helper functions for working with HVACMode enums,
including lazy imports and string conversions.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode


def mode_to_str(mode):
    """Convert mode to string (handles both enum and string).

    Args:
        mode: HVACMode enum or string

    Returns:
        String representation of the mode
    """
    return mode.value if hasattr(mode, 'value') else str(mode)


def get_hvac_heat_mode():
    """Lazy import of HVACMode.HEAT for default parameter.

    Returns:
        HVACMode.HEAT enum value
    """
    from homeassistant.components.climate import HVACMode
    return HVACMode.HEAT


def get_hvac_cool_mode():
    """Lazy import of HVACMode.COOL for comparison.

    Returns:
        HVACMode.COOL enum value
    """
    from homeassistant.components.climate import HVACMode
    return HVACMode.COOL
