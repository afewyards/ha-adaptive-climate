"""Performance sensors for Adaptive Thermostat."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    DEFAULT_FALLBACK_FLOW_RATE,
)
from .analytics.health import SystemHealthMonitor
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


class SystemHealthSensor(SensorEntity):
    """Sensor for overall system health status."""

    def __init__(
        self,
        hass: HomeAssistant,
        exception_zones: list[str] | None = None,
    ) -> None:
        """Initialize the system health sensor."""
        self.hass = hass
        self._attr_name = "Heating System Health"
        self._attr_unique_id = "heating_system_health"
        self._attr_icon = "mdi:heart-pulse"
        self._attr_should_poll = False
        self._attr_available = True
        self._attr_entity_registry_visible_default = False
        self._state = "healthy"
        self._health_monitor = SystemHealthMonitor(exception_zones or [])
        self._zone_issues = {}
        self._summary = "All zones healthy"
        self._total_issues = 0
        self._critical_count = 0
        self._warning_count = 0

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "summary": self._summary,
            "total_issues": self._total_issues,
            "critical_count": self._critical_count,
            "warning_count": self._warning_count,
            "zone_issues": {
                zone: [
                    {
                        "severity": issue.severity.value,
                        "type": issue.issue_type,
                        "message": issue.message,
                    }
                    for issue in issues
                ]
                for zone, issues in self._zone_issues.items()
            },
        }

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Collect health data from all zones
        zones_data = await self._collect_zones_data()

        # Check health of all zones
        health_result = self._health_monitor.check_all_zones(zones_data)

        # Update state
        self._state = health_result["status"].value
        self._zone_issues = health_result["zone_issues"]
        self._summary = health_result["summary"]
        self._total_issues = health_result["total_issues"]
        self._critical_count = health_result["critical_count"]
        self._warning_count = health_result["warning_count"]

    async def _collect_zones_data(self) -> dict[str, dict[str, Any]]:
        """Collect health-relevant data from all zones.

        Returns:
            Dictionary mapping zone IDs to their health data
        """
        zones_data = {}

        # Get coordinator
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return zones_data

        # Get all zones from coordinator
        all_zones = coordinator.get_all_zones()

        for zone_id in all_zones:
            # Get cycle time sensor
            cycle_time_sensor_id = f"sensor.{zone_id}_cycle_time"
            cycle_time_state = self.hass.states.get(cycle_time_sensor_id)
            cycle_time_min = None
            if cycle_time_state and cycle_time_state.state not in ("unknown", "unavailable"):
                try:
                    cycle_time_min = float(cycle_time_state.state)
                except (ValueError, TypeError):
                    pass

            # Get power/m2 sensor
            power_m2_sensor_id = f"sensor.{zone_id}_power_m2"
            power_m2_state = self.hass.states.get(power_m2_sensor_id)
            power_w_m2 = None
            if power_m2_state and power_m2_state.state not in ("unknown", "unavailable"):
                try:
                    power_w_m2 = float(power_m2_state.state)
                except (ValueError, TypeError):
                    pass

            # Get temperature sensor availability from zone data
            zone_data = coordinator.get_zone_data(zone_id)
            climate_entity_id = zone_data.get("climate_entity_id") if zone_data else None
            sensor_available = True
            if climate_entity_id:
                climate_state = self.hass.states.get(climate_entity_id)
                if not climate_state or climate_state.state in ("unknown", "unavailable"):
                    sensor_available = False

            zones_data[zone_id] = {
                "cycle_time_min": cycle_time_min,
                "power_w_m2": power_w_m2,
                "sensor_available": sensor_available,
            }

        return zones_data
