"""Energy sensors for Adaptive Thermostat.

This module contains sensors that track energy consumption and costs:
- PowerPerM2Sensor: Tracks power consumption per square meter
- HeatOutputSensor: Tracks heat output calculated from supply/return delta-T
- TotalPowerSensor: Tracks total heating power across all zones
- WeeklyCostSensor: Tracks weekly heating energy cost
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPower,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from ..const import (
    DOMAIN,
    DEFAULT_FALLBACK_FLOW_RATE,
)
from ..analytics.heat_output import HeatOutputCalculator
from .performance import AdaptiveThermostatSensor

_LOGGER = logging.getLogger(__name__)


class PowerPerM2Sensor(AdaptiveThermostatSensor):
    """Sensor for power consumption per square meter."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the power/m2 sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Power per m²"
        self._attr_unique_id = f"{zone_id}_power_m2"
        self._attr_native_unit_of_measurement = f"{UnitOfPower.WATT}/m²"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:lightning-bolt"
        self._state = 0.0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Calculate power per m2 from duty cycle and zone area
        power_m2 = await self._calculate_power_m2()
        self._state = round(power_m2, 1) if power_m2 is not None else 0.0

    async def _calculate_power_m2(self) -> float | None:
        """Calculate power consumption per square meter.

        Returns:
            Power in W/m², or None if insufficient data
        """
        # Get coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return None

        # Get zone data
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return None

        # Get zone area
        area_m2 = zone_data.get("area_m2")
        if not area_m2 or area_m2 <= 0:
            return None

        # Get duty cycle sensor
        duty_cycle_sensor_id = f"sensor.{self._zone_id}_duty_cycle"
        duty_cycle_state = self.hass.states.get(duty_cycle_sensor_id)
        duty_cycle = 0.0
        if duty_cycle_state and duty_cycle_state.state not in ("unknown", "unavailable"):
            try:
                duty_cycle = float(duty_cycle_state.state)
            except (ValueError, TypeError):
                duty_cycle = 0.0

        # Get heating power rating (assume 100 W/m² maximum for floor heating)
        max_power_w_m2 = zone_data.get("max_power_w_m2", 100.0)

        # Calculate actual power based on duty cycle
        power_m2 = (duty_cycle / 100.0) * max_power_w_m2

        return power_m2


class HeatOutputSensor(AdaptiveThermostatSensor):
    """Sensor for heat output calculated from supply/return delta-T.

    Uses the formula: Q = m x cp x delta-T
    Where:
    - Q = heat output (kW)
    - m = mass flow rate (kg/s)
    - cp = specific heat capacity of water (4.186 kJ/(kg.C))
    - delta-T = temperature difference (C)

    Requires supply temperature and return temperature sensors.
    Flow rate can be from a sensor or use a configured fallback value.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
        supply_temp_sensor: str | None = None,
        return_temp_sensor: str | None = None,
        flow_rate_sensor: str | None = None,
        fallback_flow_rate: float = DEFAULT_FALLBACK_FLOW_RATE,
    ) -> None:
        """Initialize the heat output sensor.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            zone_name: Human-readable zone name
            climate_entity_id: Entity ID of the climate entity
            supply_temp_sensor: Entity ID of supply temperature sensor
            return_temp_sensor: Entity ID of return temperature sensor
            flow_rate_sensor: Entity ID of flow rate sensor (optional)
            fallback_flow_rate: Fallback flow rate in L/min (default 0.5)
        """
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Heat Output"
        self._attr_unique_id = f"{zone_id}_heat_output"
        self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:radiator"
        self._state: float | None = None

        # Store sensor entity IDs
        self._supply_temp_sensor = supply_temp_sensor
        self._return_temp_sensor = return_temp_sensor
        self._flow_rate_sensor = flow_rate_sensor
        self._fallback_flow_rate = fallback_flow_rate

        # Create heat output calculator with fallback flow rate
        self._calculator = HeatOutputCalculator(
            fallback_flow_rate_lpm=fallback_flow_rate
        )

        # Cached values for attributes
        self._supply_temp: float | None = None
        self._return_temp: float | None = None
        self._flow_rate: float | None = None
        self._delta_t: float | None = None

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "supply_temp_sensor": self._supply_temp_sensor,
            "return_temp_sensor": self._return_temp_sensor,
            "flow_rate_sensor": self._flow_rate_sensor,
            "fallback_flow_rate_lpm": self._fallback_flow_rate,
            "supply_temp_c": self._supply_temp,
            "return_temp_c": self._return_temp,
            "flow_rate_lpm": self._flow_rate,
            "delta_t_c": self._delta_t,
        }

    async def async_update(self) -> None:
        """Update the sensor state."""
        heat_output = await self._calculate_heat_output()
        if heat_output is not None:
            self._state = round(heat_output, 3)
        else:
            self._state = None

    async def _calculate_heat_output(self) -> float | None:
        """Calculate heat output from supply/return temperatures.

        Returns:
            Heat output in kW, or None if insufficient data
        """
        # Get supply temperature
        supply_temp = self._get_sensor_value(self._supply_temp_sensor)
        if supply_temp is None:
            _LOGGER.debug(
                "%s: Supply temperature unavailable from %s",
                self._attr_unique_id,
                self._supply_temp_sensor,
            )
            return None

        # Get return temperature
        return_temp = self._get_sensor_value(self._return_temp_sensor)
        if return_temp is None:
            _LOGGER.debug(
                "%s: Return temperature unavailable from %s",
                self._attr_unique_id,
                self._return_temp_sensor,
            )
            return None

        # Get flow rate (optional - calculator will use fallback if None)
        flow_rate = self._get_sensor_value(self._flow_rate_sensor)

        # Cache values for attributes
        self._supply_temp = supply_temp
        self._return_temp = return_temp
        self._flow_rate = flow_rate or self._fallback_flow_rate
        self._delta_t = supply_temp - return_temp if supply_temp > return_temp else None

        # Calculate heat output using the calculator
        heat_output = self._calculator.calculate_with_fallback(
            supply_temp_c=supply_temp,
            return_temp_c=return_temp,
            measured_flow_rate_lpm=flow_rate,
        )

        if heat_output is not None:
            _LOGGER.debug(
                "%s: Heat output calculated: %.3f kW "
                "(supply=%.1f C, return=%.1f C, delta_t=%.1f C, flow=%.2f L/min)",
                self._attr_unique_id,
                heat_output,
                supply_temp,
                return_temp,
                supply_temp - return_temp,
                flow_rate or self._fallback_flow_rate,
            )

        return heat_output

    def _get_sensor_value(self, entity_id: str | None) -> float | None:
        """Get numeric value from a sensor entity.

        Args:
            entity_id: Entity ID to read value from

        Returns:
            Float value or None if unavailable
        """
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if not state:
            return None

        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None


class TotalPowerSensor(SensorEntity):
    """Sensor for total heating power across all zones."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the total power sensor."""
        self.hass = hass
        self._attr_name = "Heating Total Power"
        self._attr_unique_id = "heating_total_power"
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:lightning-bolt"
        self._attr_should_poll = False
        self._attr_available = True
        self._attr_entity_registry_visible_default = False
        self._value = 0.0
        self._zone_powers = {}

    @property
    def native_value(self) -> float | None:
        """Return the total power in Watts."""
        return round(self._value, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "zone_powers": self._zone_powers,
            "zone_count": len(self._zone_powers),
        }

    async def async_update(self) -> None:
        """Update the sensor by aggregating power from all zones."""
        total_power = 0.0
        zone_powers = {}

        # Get coordinator
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            self._value = 0.0
            self._zone_powers = {}
            return

        # Get all zones from coordinator
        all_zones = coordinator.get_all_zones()

        for zone_id in all_zones:
            zone_data = coordinator.get_zone_data(zone_id)
            if not zone_data:
                continue

            # Get area for this zone
            area_m2 = zone_data.get("area_m2", 0)
            if area_m2 <= 0:
                continue

            # Get power/m2 sensor value
            power_m2_sensor_id = f"sensor.{zone_id}_power_m2"
            power_m2_state = self.hass.states.get(power_m2_sensor_id)

            if power_m2_state and power_m2_state.state not in ("unknown", "unavailable"):
                try:
                    power_m2 = float(power_m2_state.state)
                    zone_power = power_m2 * area_m2
                    zone_powers[zone_id] = round(zone_power, 1)
                    total_power += zone_power
                except (ValueError, TypeError):
                    pass

        self._value = total_power
        self._zone_powers = zone_powers


class WeeklyCostSensor(SensorEntity, RestoreEntity):
    """Sensor for weekly heating energy cost.

    Tracks weekly energy consumption by storing the meter reading at the start
    of each week and calculating the delta. Persists state across HA restarts
    and handles meter reset/replacement scenarios.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        energy_meter_entity: str | None = None,
        energy_cost_entity: str | None = None,
    ) -> None:
        """Initialize the weekly cost sensor."""
        self.hass = hass
        self._energy_meter_entity = energy_meter_entity
        self._energy_cost_entity = energy_cost_entity
        self._attr_name = "Heating Weekly Cost"
        self._attr_unique_id = "heating_weekly_cost"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:currency-eur"
        self._attr_should_poll = False
        self._attr_available = True
        self._attr_entity_registry_visible_default = False
        self._value = 0.0
        self._weekly_energy_kwh = 0.0
        self._price_per_kwh: float | None = None
        self._currency = "EUR"
        # Week tracking state
        self._week_start_reading: float | None = None
        self._week_start_timestamp: datetime | None = None
        self._last_meter_reading: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Restore previous state
        old_state = await self.async_get_last_state()
        if old_state is not None:
            # Restore week_start_reading from attributes
            attrs = old_state.attributes
            week_start = attrs.get("week_start_reading")
            if week_start is not None:
                try:
                    self._week_start_reading = float(week_start)
                except (ValueError, TypeError):
                    pass

            week_start_ts = attrs.get("week_start_timestamp")
            if week_start_ts:
                try:
                    self._week_start_timestamp = datetime.fromisoformat(week_start_ts)
                except (ValueError, TypeError):
                    pass

            last_reading = attrs.get("last_meter_reading")
            if last_reading is not None:
                try:
                    self._last_meter_reading = float(last_reading)
                except (ValueError, TypeError):
                    pass

            # Restore weekly energy
            weekly_energy = attrs.get("weekly_energy_kwh")
            if weekly_energy is not None:
                try:
                    self._weekly_energy_kwh = float(weekly_energy)
                except (ValueError, TypeError):
                    pass

            # Restore price
            price = attrs.get("price_per_kwh")
            if price is not None:
                try:
                    self._price_per_kwh = float(price)
                except (ValueError, TypeError):
                    pass

            _LOGGER.debug(
                "Restored WeeklyCostSensor state: week_start_reading=%.2f, "
                "week_start_timestamp=%s, weekly_energy_kwh=%.2f",
                self._week_start_reading or 0.0,
                self._week_start_timestamp,
                self._weekly_energy_kwh,
            )

    def _check_week_boundary(self, current_reading: float) -> None:
        """Check if we've crossed into a new week and reset if needed.

        Resets on Sunday midnight (start of new week).
        """
        now = datetime.now()

        if self._week_start_timestamp is None:
            # First run or no previous data - initialize
            self._start_new_week(current_reading, now)
            return

        # Check if we're in a new week (ISO week-based)
        # Week number changed means new week
        current_week = now.isocalendar()[1]
        stored_week = self._week_start_timestamp.isocalendar()[1]
        current_year = now.isocalendar()[0]
        stored_year = self._week_start_timestamp.isocalendar()[0]

        if current_year != stored_year or current_week != stored_week:
            _LOGGER.info(
                "New week detected (week %d/%d -> %d/%d). "
                "Previous week energy: %.2f kWh, cost: %.2f",
                stored_year,
                stored_week,
                current_year,
                current_week,
                self._weekly_energy_kwh,
                self._value,
            )
            self._start_new_week(current_reading, now)

    def _start_new_week(self, current_reading: float, now: datetime) -> None:
        """Reset week tracking with new start reading."""
        self._week_start_reading = current_reading
        self._week_start_timestamp = now
        self._weekly_energy_kwh = 0.0
        _LOGGER.debug(
            "Started new week at %s with reading %.2f kWh",
            now.isoformat(),
            current_reading,
        )

    @property
    def native_value(self) -> float | None:
        """Return the weekly cost."""
        return round(self._value, 2)

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the currency unit."""
        return self._currency

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "weekly_energy_kwh": round(self._weekly_energy_kwh, 2),
            "price_per_kwh": self._price_per_kwh,
            "energy_meter_entity": self._energy_meter_entity,
            "cost_entity": self._energy_cost_entity,
            # Persistence attributes
            "week_start_reading": self._week_start_reading,
            "week_start_timestamp": (
                self._week_start_timestamp.isoformat()
                if self._week_start_timestamp
                else None
            ),
            "last_meter_reading": self._last_meter_reading,
        }

    async def async_update(self) -> None:
        """Update the sensor with weekly cost calculation."""
        if not self._energy_meter_entity:
            self._attr_available = False
            return

        # Get current energy price
        if self._energy_cost_entity:
            cost_state = self.hass.states.get(self._energy_cost_entity)
            if cost_state and cost_state.state not in ("unknown", "unavailable"):
                try:
                    self._price_per_kwh = float(cost_state.state)
                    # Try to get currency from unit_of_measurement
                    uom = cost_state.attributes.get("unit_of_measurement", "")
                    if "/" in uom:
                        self._currency = uom.split("/")[0]
                except (ValueError, TypeError):
                    pass

        # Get energy meter reading
        meter_state = self.hass.states.get(self._energy_meter_entity)
        if not meter_state or meter_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            return

        self._attr_available = True

        try:
            current_reading = float(meter_state.state)
            unit = meter_state.attributes.get("unit_of_measurement", "kWh").upper()

            # Convert to kWh
            from ..analytics.energy import UNIT_CONVERSIONS

            conversion = UNIT_CONVERSIONS.get(unit, 1.0)
            current_kwh = current_reading * conversion

            # Check for week boundary reset
            self._check_week_boundary(current_kwh)

            # Handle meter reset scenarios (current reading less than week start)
            if self._week_start_reading is not None:
                if current_kwh < self._week_start_reading:
                    # Meter reset detected - likely replacement or rollover
                    _LOGGER.warning(
                        "Meter reset detected: week_start=%.2f kWh, current=%.2f kWh. "
                        "Resetting week start to current reading.",
                        self._week_start_reading,
                        current_kwh,
                    )
                    self._week_start_reading = current_kwh
                    self._weekly_energy_kwh = 0.0
                else:
                    # Normal case: calculate delta
                    self._weekly_energy_kwh = current_kwh - self._week_start_reading
            else:
                # First reading - initialize
                self._week_start_reading = current_kwh
                self._week_start_timestamp = datetime.now()
                self._weekly_energy_kwh = 0.0

            self._last_meter_reading = current_kwh

            # Calculate cost if price is available
            if self._price_per_kwh is not None:
                self._value = self._weekly_energy_kwh * self._price_per_kwh
            else:
                self._value = 0.0

        except (ValueError, TypeError) as e:
            _LOGGER.error("Error calculating weekly cost: %s", e)
            self._value = 0.0
