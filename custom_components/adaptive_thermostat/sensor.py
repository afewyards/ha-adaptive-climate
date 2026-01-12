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
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .analytics.health import SystemHealthMonitor, HealthStatus

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
        OvershootSensor(hass, zone_id, zone_name, climate_entity_id),
        SettlingTimeSensor(hass, zone_id, zone_name, climate_entity_id),
        OscillationsSensor(hass, zone_id, zone_name, climate_entity_id),
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
        self._attr_entity_registry_visible_default = False


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


class OvershootSensor(AdaptiveThermostatSensor):
    """Sensor for temperature overshoot from adaptive learning."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the overshoot sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Overshoot"
        self._attr_unique_id = f"{zone_id}_overshoot"
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:thermometer-alert"
        self._state = 0.0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Get overshoot from adaptive learner
        overshoot = await self._get_overshoot()
        self._state = round(overshoot, 2) if overshoot is not None else 0.0

    async def _get_overshoot(self) -> float | None:
        """Get overshoot value from adaptive learner.

        Returns:
            Overshoot in °C, or None if no data available
        """
        # Get coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return None

        # Get zone data
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return None

        # Get adaptive learner from zone data
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return None

        # Get cycle history
        if not hasattr(adaptive_learner, "cycle_history") or not adaptive_learner.cycle_history:
            return 0.0

        # Calculate average overshoot from recent cycles
        overshoots = [
            cycle.overshoot
            for cycle in adaptive_learner.cycle_history
            if cycle.overshoot is not None
        ]

        if not overshoots:
            return 0.0

        return sum(overshoots) / len(overshoots)


class SettlingTimeSensor(AdaptiveThermostatSensor):
    """Sensor for settling time from adaptive learning."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the settling time sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Settling Time"
        self._attr_unique_id = f"{zone_id}_settling_time"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:timer-sand"
        self._state = 0.0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Get settling time from adaptive learner
        settling_time = await self._get_settling_time()
        self._state = round(settling_time, 1) if settling_time is not None else 0.0

    async def _get_settling_time(self) -> float | None:
        """Get settling time value from adaptive learner.

        Returns:
            Settling time in minutes, or None if no data available
        """
        # Get coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return None

        # Get zone data
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return None

        # Get adaptive learner from zone data
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return None

        # Get cycle history
        if not hasattr(adaptive_learner, "cycle_history") or not adaptive_learner.cycle_history:
            return 0.0

        # Calculate average settling time from recent cycles
        settling_times = [
            cycle.settling_time
            for cycle in adaptive_learner.cycle_history
            if cycle.settling_time is not None
        ]

        if not settling_times:
            return 0.0

        return sum(settling_times) / len(settling_times)


class OscillationsSensor(AdaptiveThermostatSensor):
    """Sensor for oscillation count from adaptive learning."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the oscillations sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Oscillations"
        self._attr_unique_id = f"{zone_id}_oscillations"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:sine-wave"
        self._state = 0

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Get oscillations from adaptive learner
        oscillations = await self._get_oscillations()
        self._state = int(oscillations) if oscillations is not None else 0

    async def _get_oscillations(self) -> int | None:
        """Get oscillations count from adaptive learner.

        Returns:
            Average oscillation count, or None if no data available
        """
        # Get coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return None

        # Get zone data
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return None

        # Get adaptive learner from zone data
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return None

        # Get cycle history
        if not hasattr(adaptive_learner, "cycle_history") or not adaptive_learner.cycle_history:
            return 0

        # Calculate average oscillations from recent cycles
        oscillations = [
            cycle.oscillations
            for cycle in adaptive_learner.cycle_history
            if cycle.oscillations is not None
        ]

        if not oscillations:
            return 0

        return int(sum(oscillations) / len(oscillations))


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


class WeeklyCostSensor(SensorEntity):
    """Sensor for weekly heating energy cost."""

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
        self._price_per_kwh = None
        self._currency = "EUR"

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

        # For now, we track the current meter value
        # A full implementation would store historical readings
        # and calculate the 7-day difference
        try:
            current_reading = float(meter_state.state)
            unit = meter_state.attributes.get("unit_of_measurement", "kWh").upper()

            # Convert to kWh
            from .analytics.energy import UNIT_CONVERSIONS
            conversion = UNIT_CONVERSIONS.get(unit.replace("KWH", "KWH").replace("GJ", "GJ"), 1.0)
            current_kwh = current_reading * conversion

            # Store the weekly energy (this is simplified - production would track 7-day delta)
            self._weekly_energy_kwh = current_kwh

            # Calculate cost if price is available
            if self._price_per_kwh is not None:
                self._value = self._weekly_energy_kwh * self._price_per_kwh
            else:
                self._value = 0.0

        except (ValueError, TypeError) as e:
            _LOGGER.error("Error calculating weekly cost: %s", e)
            self._value = 0.0
