"""Sensor platform for Adaptive Thermostat.

This module serves as the entry point for the sensor platform,
importing and re-exporting sensor classes from submodules.
"""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.event import async_track_time_interval

from .const import DEFAULT_FALLBACK_FLOW_RATE
from .sensors.performance import (
    AdaptiveThermostatSensor,
    DutyCycleSensor,
    CycleTimeSensor,
    OvershootSensor,
    SettlingTimeSensor,
    OscillationsSensor,
    HeaterStateChange,
    DEFAULT_DUTY_CYCLE_WINDOW,
    DEFAULT_ROLLING_AVERAGE_SIZE,
)
from .sensors.energy import (
    PowerPerM2Sensor,
    HeatOutputSensor,
    TotalPowerSensor,
    WeeklyCostSensor,
)
from .sensors.health import SystemHealthSensor

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

    # Get supply/return temp sensor configuration from discovery_info
    supply_temp_sensor = discovery_info.get("supply_temp_sensor")
    return_temp_sensor = discovery_info.get("return_temp_sensor")
    flow_rate_sensor = discovery_info.get("flow_rate_sensor")
    fallback_flow_rate = discovery_info.get("fallback_flow_rate", DEFAULT_FALLBACK_FLOW_RATE)

    sensors = [
        DutyCycleSensor(hass, zone_id, zone_name, climate_entity_id),
        PowerPerM2Sensor(hass, zone_id, zone_name, climate_entity_id),
        CycleTimeSensor(hass, zone_id, zone_name, climate_entity_id),
        OvershootSensor(hass, zone_id, zone_name, climate_entity_id),
        SettlingTimeSensor(hass, zone_id, zone_name, climate_entity_id),
        OscillationsSensor(hass, zone_id, zone_name, climate_entity_id),
        HeatOutputSensor(
            hass,
            zone_id,
            zone_name,
            climate_entity_id,
            supply_temp_sensor,
            return_temp_sensor,
            flow_rate_sensor,
            fallback_flow_rate,
        ),
    ]

    async_add_entities(sensors, True)

    # Schedule updates every 5 minutes
    async def async_update_sensors(now):
        """Update all sensors."""
        for sensor in sensors:
            await sensor.async_update()
            sensor.async_write_ha_state()

    async_track_time_interval(hass, async_update_sensors, UPDATE_INTERVAL)


# Re-export all sensor classes for backward compatibility
__all__ = [
    # Performance sensors
    "AdaptiveThermostatSensor",
    "DutyCycleSensor",
    "CycleTimeSensor",
    "OvershootSensor",
    "SettlingTimeSensor",
    "OscillationsSensor",
    "HeaterStateChange",
    "DEFAULT_DUTY_CYCLE_WINDOW",
    "DEFAULT_ROLLING_AVERAGE_SIZE",
    # Energy sensors
    "PowerPerM2Sensor",
    "HeatOutputSensor",
    "TotalPowerSensor",
    "WeeklyCostSensor",
    # Health sensors
    "SystemHealthSensor",
]
