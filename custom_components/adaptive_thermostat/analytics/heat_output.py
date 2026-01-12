"""Heat output calculation from supply/return delta-T for adaptive thermostat."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


# Water properties
SPECIFIC_HEAT_WATER = 4.186  # kJ/(kg·°C)
DENSITY_WATER = 1.0  # kg/L at typical heating system temperatures


def calculate_heat_output_kw(
    supply_temp_c: float,
    return_temp_c: float,
    flow_rate_lpm: float,
) -> float:
    """
    Calculate heat output from supply/return temperatures and flow rate.

    Uses the formula: Q = m × cp × ΔT
    Where:
    - Q = heat output (kW)
    - m = mass flow rate (kg/s)
    - cp = specific heat capacity of water (4.186 kJ/(kg·°C))
    - ΔT = temperature difference (°C)

    Args:
        supply_temp_c: Supply water temperature in °C
        return_temp_c: Return water temperature in °C
        flow_rate_lpm: Flow rate in liters per minute

    Returns:
        Heat output in kW

    Raises:
        ValueError: If supply temp is not higher than return temp
        ValueError: If flow rate is negative
    """
    if supply_temp_c <= return_temp_c:
        raise ValueError(
            f"Supply temperature ({supply_temp_c}°C) must be higher than "
            f"return temperature ({return_temp_c}°C)"
        )

    if flow_rate_lpm < 0:
        raise ValueError(f"Flow rate must be non-negative, got {flow_rate_lpm}")

    # Calculate temperature difference
    delta_t = supply_temp_c - return_temp_c

    # Convert flow rate from L/min to kg/s
    # L/min × (1 kg/L) × (1 min/60 s) = kg/s
    mass_flow_rate_kg_s = flow_rate_lpm * DENSITY_WATER / 60.0

    # Calculate heat output: Q = m × cp × ΔT
    # kW = (kg/s) × (kJ/(kg·°C)) × °C
    heat_output_kw = mass_flow_rate_kg_s * SPECIFIC_HEAT_WATER * delta_t

    return heat_output_kw


def calculate_flow_rate(
    volume_start: float,
    volume_end: float,
    time_start: datetime,
    time_end: datetime,
) -> float:
    """
    Calculate flow rate from volume meter changes over time.

    Args:
        volume_start: Starting volume reading in liters
        volume_end: Ending volume reading in liters
        time_start: Start timestamp
        time_end: End timestamp

    Returns:
        Flow rate in liters per minute

    Raises:
        ValueError: If volume decreases (end < start)
        ValueError: If time period is zero or negative
    """
    if volume_end < volume_start:
        raise ValueError(
            f"Volume cannot decrease: {volume_start}L → {volume_end}L"
        )

    # Calculate time difference in seconds
    time_delta = time_end - time_start
    time_seconds = time_delta.total_seconds()

    if time_seconds <= 0:
        raise ValueError(
            f"Time period must be positive, got {time_seconds} seconds"
        )

    # Calculate volume difference
    volume_delta = volume_end - volume_start

    # Convert to liters per minute
    # L / s × (60 s/min) = L/min
    flow_rate_lpm = (volume_delta / time_seconds) * 60.0

    return flow_rate_lpm


class HeatOutputCalculator:
    """
    Calculate heat output with support for fallback flow rate.

    Supports both measured flow rate (from volume meter) and
    configured fallback flow rate.
    """

    def __init__(
        self,
        fallback_flow_rate_lpm: Optional[float] = None,
    ):
        """
        Initialize heat output calculator.

        Args:
            fallback_flow_rate_lpm: Fallback flow rate in L/min when meter unavailable
        """
        self.fallback_flow_rate_lpm = fallback_flow_rate_lpm

    def calculate_with_fallback(
        self,
        supply_temp_c: float,
        return_temp_c: float,
        measured_flow_rate_lpm: Optional[float] = None,
    ) -> Optional[float]:
        """
        Calculate heat output using measured or fallback flow rate.

        Args:
            supply_temp_c: Supply water temperature in °C
            return_temp_c: Return water temperature in °C
            measured_flow_rate_lpm: Measured flow rate in L/min (optional)

        Returns:
            Heat output in kW, or None if no flow rate available
        """
        # Use measured flow rate if available, otherwise use fallback
        flow_rate = measured_flow_rate_lpm or self.fallback_flow_rate_lpm

        if flow_rate is None:
            return None

        try:
            return calculate_heat_output_kw(
                supply_temp_c,
                return_temp_c,
                flow_rate,
            )
        except ValueError:
            # Return None if calculation fails (e.g., invalid temps)
            return None
