"""Health sensors for Adaptive Thermostat.

This module contains sensors that track overall system health:
- SystemHealthSensor: Monitors health status across all heating zones
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..analytics.health import SystemHealthMonitor

_LOGGER = logging.getLogger(__name__)


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
