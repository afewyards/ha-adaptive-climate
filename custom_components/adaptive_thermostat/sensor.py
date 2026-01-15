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
from .sensors.comfort import (
    TimeAtTargetSensor,
    ComfortScoreSensor,
)
from .sensors.actuator_wear import ActuatorWearSensor

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

    # Get actuator wear tracking configuration
    heater_rated_cycles = discovery_info.get("heater_rated_cycles")
    cooler_rated_cycles = discovery_info.get("cooler_rated_cycles")

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
        # Comfort sensors
        TimeAtTargetSensor(hass, zone_id, zone_name, climate_entity_id),
        ComfortScoreSensor(hass, zone_id, zone_name, climate_entity_id),
    ]

    # Add actuator wear sensors if rated cycles are configured
    if heater_rated_cycles:
        sensors.append(
            ActuatorWearSensor(
                hass, zone_id, zone_name, climate_entity_id,
                actuator_type="heater",
                rated_cycles=heater_rated_cycles,
            )
        )
    if cooler_rated_cycles:
        sensors.append(
            ActuatorWearSensor(
                hass, zone_id, zone_name, climate_entity_id,
                actuator_type="cooler",
                rated_cycles=cooler_rated_cycles,
            )
        )

    # Create system-wide sensors on first zone setup
    from .const import DOMAIN
    if not hass.data[DOMAIN].get("system_sensors_created"):
        _LOGGER.info("Creating system-wide sensors (TotalPowerSensor, WeeklyCostSensor)")

        # Get energy configuration from domain data
        energy_meter = hass.data[DOMAIN].get("energy_meter_entity")
        energy_cost = hass.data[DOMAIN].get("energy_cost_entity")

        # Create TotalPowerSensor
        total_power_sensor = TotalPowerSensor(hass)
        sensors.append(total_power_sensor)

        # Create WeeklyCostSensor if energy meter configured
        if energy_meter:
            weekly_cost_sensor = WeeklyCostSensor(
                hass,
                energy_meter_entity=energy_meter,
                energy_cost_entity=energy_cost,
            )
            sensors.append(weekly_cost_sensor)
            _LOGGER.info(
                "WeeklyCostSensor created with meter=%s, cost=%s",
                energy_meter,
                energy_cost,
            )
        else:
            _LOGGER.warning(
                "No energy_meter_entity configured - WeeklyCostSensor will not be created"
            )

        # Mark as created
        hass.data[DOMAIN]["system_sensors_created"] = True

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
    # Comfort sensors
    "TimeAtTargetSensor",
    "ComfortScoreSensor",
    # Actuator wear sensors
    "ActuatorWearSensor",
]
