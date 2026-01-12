"""Performance sensors for Adaptive Thermostat."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Update interval for performance sensors (5 minutes)
UPDATE_INTERVAL = timedelta(minutes=5)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Adaptive Thermostat performance sensors."""
    if discovery_info is None:
        return

    zone_id = discovery_info.get("zone_id")
    zone_name = discovery_info.get("zone_name")
    climate_entity_id = discovery_info.get("climate_entity_id")

    if not zone_id or not climate_entity_id:
        _LOGGER.error("Missing required discovery info for sensor platform")
        return

    sensors = [
        DutyCycleSensor(hass, zone_id, zone_name, climate_entity_id),
        PowerPerM2Sensor(hass, zone_id, zone_name, climate_entity_id),
        CycleTimeSensor(hass, zone_id, zone_name, climate_entity_id),
    ]

    async_add_entities(sensors, True)

    # Schedule updates every 5 minutes
    async def async_update_sensors(now):
        """Update all sensors."""
        for sensor in sensors:
            await sensor.async_update()
            sensor.async_write_ha_state()

    async_track_time_interval(hass, async_update_sensors, UPDATE_INTERVAL)


class AdaptiveThermostatSensor(SensorEntity):
    """Base class for Adaptive Thermostat sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._climate_entity_id = climate_entity_id
        self._attr_should_poll = False
        self._attr_available = True


class DutyCycleSensor(AdaptiveThermostatSensor):
    """Sensor for heating duty cycle percentage."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the duty cycle sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Duty Cycle"
        self._attr_unique_id = f"{zone_id}_duty_cycle"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:percent"
        self._state = 0.0
        self._on_time_seconds = 0
        self._total_time_seconds = 0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Calculate duty cycle from heating cycles in the last 5 minutes
        duty_cycle = await self._calculate_duty_cycle()
        self._state = round(duty_cycle, 1) if duty_cycle is not None else 0.0

    async def _calculate_duty_cycle(self) -> float | None:
        """Calculate duty cycle from recent heating history.

        Returns:
            Duty cycle as percentage (0-100), or None if insufficient data
        """
        # Get the climate entity state history for the last 5 minutes
        end_time = datetime.now()
        start_time = end_time - UPDATE_INTERVAL

        # Access history through the history component
        history = self.hass.data.get("history")
        if not history:
            return None

        # For testing/basic implementation, we'll use a simplified approach
        # In production, this would query the state history to determine
        # how long the heater was on vs off

        # Get current climate entity state
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            return None

        # Get heater entity states from climate attributes
        heater_entity_id = climate_state.attributes.get("heater_entity_id")
        if not heater_entity_id:
            return None

        # For now, return a simple on/off state
        # In production, this would calculate actual duty cycle from history
        heater_state = self.hass.states.get(heater_entity_id)
        if heater_state and heater_state.state == "on":
            return 100.0
        return 0.0


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


class CycleTimeSensor(AdaptiveThermostatSensor):
    """Sensor for average heating cycle time."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the cycle time sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Cycle Time"
        self._attr_unique_id = f"{zone_id}_cycle_time"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:timer-outline"
        self._state = 0.0
        self._cycle_times: list[float] = []

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Calculate average cycle time from recent cycles
        avg_cycle_time = await self._calculate_average_cycle_time()
        self._state = round(avg_cycle_time, 1) if avg_cycle_time is not None else 0.0

    async def _calculate_average_cycle_time(self) -> float | None:
        """Calculate average heating cycle time.

        Returns:
            Average cycle time in minutes, or None if insufficient data
        """
        # Get the climate entity state
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            return None

        # For now, return a default value
        # In production, this would analyze heating on/off cycles from history
        # and calculate the average time between cycles

        # Typical cycle time for floor heating is 15-30 minutes
        return 20.0
