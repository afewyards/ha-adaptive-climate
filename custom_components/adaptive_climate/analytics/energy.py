"""Energy consumption and cost tracking for adaptive thermostat."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


# Unit conversion constants to kWh
UNIT_CONVERSIONS = {
    "GJ": 277.778,  # 1 GJ = 277.778 kWh
    "KWH": 1.0,     # 1 kWh = 1 kWh
    "MWH": 1000.0,  # 1 MWh = 1000 kWh
    "WH": 0.001,    # 1 Wh = 0.001 kWh
}


class EnergyTracker:
    """
    Track energy consumption from a meter entity supporting any unit.

    Converts all units to kWh for internal calculations and supports
    cost tracking when a price entity is provided.
    """

    def __init__(
        self,
        meter_entity_id: str,
        unit: str = "GJ",
        price_entity_id: Optional[str] = None,
    ):
        """
        Initialize energy tracker.

        Args:
            meter_entity_id: Entity ID of the energy meter
            unit: Unit of measurement (GJ, kWh, MWh, Wh)
            price_entity_id: Optional entity ID for energy price per kWh
        """
        self.meter_entity_id = meter_entity_id
        self.unit = unit.upper()
        self.price_entity_id = price_entity_id

        if self.unit not in UNIT_CONVERSIONS:
            raise ValueError(
                f"Unsupported unit: {unit}. "
                f"Supported units: {', '.join(UNIT_CONVERSIONS.keys())}"
            )

        self._daily_readings: list[tuple[datetime, float]] = []
        self._weekly_readings: list[tuple[datetime, float]] = []

    def to_kwh(self, value: float) -> float:
        """
        Convert value from meter unit to kWh.

        Args:
            value: Value in meter's native unit

        Returns:
            Value converted to kWh
        """
        return value * UNIT_CONVERSIONS[self.unit]

    def calculate_cost(
        self,
        energy_kwh: float,
        price_per_kwh: Optional[float] = None,
    ) -> Optional[float]:
        """
        Calculate cost from energy consumption.

        Args:
            energy_kwh: Energy consumption in kWh
            price_per_kwh: Price per kWh (currency unit)

        Returns:
            Cost in currency units, or None if price not available
        """
        if price_per_kwh is None:
            return None

        return energy_kwh * price_per_kwh

    def add_reading(
        self,
        timestamp: datetime,
        meter_value: float,
        period: str = "daily",
    ) -> None:
        """
        Add a meter reading for aggregation.

        Args:
            timestamp: When the reading was taken
            meter_value: Raw meter value in native unit
            period: 'daily' or 'weekly'
        """
        if period == "daily":
            self._daily_readings.append((timestamp, meter_value))
        elif period == "weekly":
            self._weekly_readings.append((timestamp, meter_value))
        else:
            raise ValueError(f"Invalid period: {period}")

    def get_daily_consumption(self) -> Optional[float]:
        """
        Calculate daily energy consumption in kWh.

        Returns:
            Daily consumption in kWh, or None if insufficient data
        """
        if len(self._daily_readings) < 2:
            return None

        # Sort by timestamp
        readings = sorted(self._daily_readings, key=lambda x: x[0])

        # Calculate difference between latest and earliest reading
        latest_value = readings[-1][1]
        earliest_value = readings[0][1]

        consumption_native = latest_value - earliest_value

        return self.to_kwh(consumption_native)

    def get_weekly_consumption(self) -> Optional[float]:
        """
        Calculate weekly energy consumption in kWh.

        Returns:
            Weekly consumption in kWh, or None if insufficient data
        """
        if len(self._weekly_readings) < 2:
            return None

        # Sort by timestamp
        readings = sorted(self._weekly_readings, key=lambda x: x[0])

        # Calculate difference between latest and earliest reading
        latest_value = readings[-1][1]
        earliest_value = readings[0][1]

        consumption_native = latest_value - earliest_value

        return self.to_kwh(consumption_native)

    def get_daily_cost(self, price_per_kwh: Optional[float] = None) -> Optional[float]:
        """
        Calculate daily energy cost.

        Args:
            price_per_kwh: Price per kWh

        Returns:
            Daily cost, or None if consumption or price unavailable
        """
        consumption = self.get_daily_consumption()
        if consumption is None:
            return None

        return self.calculate_cost(consumption, price_per_kwh)

    def get_weekly_cost(self, price_per_kwh: Optional[float] = None) -> Optional[float]:
        """
        Calculate weekly energy cost.

        Args:
            price_per_kwh: Price per kWh

        Returns:
            Weekly cost, or None if consumption or price unavailable
        """
        consumption = self.get_weekly_consumption()
        if consumption is None:
            return None

        return self.calculate_cost(consumption, price_per_kwh)

    def clear_readings(self, period: str = "all") -> None:
        """
        Clear stored readings.

        Args:
            period: 'daily', 'weekly', or 'all'
        """
        if period in ("daily", "all"):
            self._daily_readings.clear()
        if period in ("weekly", "all"):
            self._weekly_readings.clear()


class EnergyEstimator:
    """
    Fallback energy estimator using duty cycle when no meter available.

    Estimates energy consumption based on heating system duty cycle,
    zone area, and configured power rating.
    """

    def __init__(
        self,
        zone_area_m2: float,
        max_power_w_m2: float = 100.0,
    ):
        """
        Initialize energy estimator.

        Args:
            zone_area_m2: Zone area in square meters
            max_power_w_m2: Maximum power rating in W/m² (default 100 for floor heating)
        """
        self.zone_area_m2 = zone_area_m2
        self.max_power_w_m2 = max_power_w_m2
        self.max_power_w = zone_area_m2 * max_power_w_m2

    def estimate_consumption(
        self,
        duty_cycle: float,
        period_hours: float,
    ) -> float:
        """
        Estimate energy consumption from duty cycle.

        Args:
            duty_cycle: Duty cycle as percentage (0-100)
            period_hours: Time period in hours

        Returns:
            Estimated consumption in kWh
        """
        if not 0 <= duty_cycle <= 100:
            raise ValueError(f"Invalid duty cycle: {duty_cycle}. Must be 0-100.")

        if period_hours <= 0:
            raise ValueError(f"Invalid period: {period_hours}. Must be > 0.")

        # Convert duty cycle to fraction
        duty_fraction = duty_cycle / 100.0

        # Calculate energy: Power (W) * duty fraction * time (h) / 1000 = kWh
        energy_kwh = (self.max_power_w * duty_fraction * period_hours) / 1000.0

        return energy_kwh

    def estimate_daily_consumption(self, duty_cycle: float) -> float:
        """
        Estimate daily energy consumption.

        Args:
            duty_cycle: Average duty cycle as percentage (0-100)

        Returns:
            Estimated daily consumption in kWh
        """
        return self.estimate_consumption(duty_cycle, 24.0)

    def estimate_weekly_consumption(self, duty_cycle: float) -> float:
        """
        Estimate weekly energy consumption.

        Args:
            duty_cycle: Average duty cycle as percentage (0-100)

        Returns:
            Estimated weekly consumption in kWh
        """
        return self.estimate_consumption(duty_cycle, 24.0 * 7.0)
