"""Comfort sensors for Adaptive Thermostat.

This module contains sensors that track comfort metrics:
- TimeAtTargetSensor: Tracks percentage of time temperature is within target band
- ComfortScoreSensor: Composite comfort score (0-100)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from .performance import AdaptiveThermostatSensor

_LOGGER = logging.getLogger(__name__)

# Temperature tolerance band for "at target" (± this value)
DEFAULT_TARGET_TOLERANCE = 0.5  # °C

# Measurement window for time-at-target calculation
DEFAULT_COMFORT_WINDOW = timedelta(hours=1)

# Maximum samples to keep (1 per minute for 2 hours)
MAX_COMFORT_SAMPLES = 120


@dataclass
class TemperatureSample:
    """A temperature sample with timestamp."""

    timestamp: datetime
    temperature: float
    setpoint: float


class TimeAtTargetSensor(AdaptiveThermostatSensor):
    """Sensor for percentage of time at target temperature.

    Tracks temperature samples and calculates what percentage of time
    the actual temperature was within the tolerance band of the setpoint.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
        tolerance: float = DEFAULT_TARGET_TOLERANCE,
        measurement_window: timedelta | None = None,
    ) -> None:
        """Initialize the time at target sensor.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            zone_name: Human-readable zone name
            climate_entity_id: Entity ID of the climate entity
            tolerance: Temperature tolerance band (±°C)
            measurement_window: Time window for calculation
        """
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Time at Target"
        self._attr_unique_id = f"{zone_id}_time_at_target"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:bullseye-arrow"
        self._state: float = 0.0

        self._tolerance = tolerance
        self._measurement_window = measurement_window or DEFAULT_COMFORT_WINDOW
        self._samples: deque[TemperatureSample] = deque(maxlen=MAX_COMFORT_SAMPLES)

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "tolerance_c": self._tolerance,
            "measurement_window_minutes": self._measurement_window.total_seconds() / 60,
            "samples_tracked": len(self._samples),
        }

    def record_sample(self, temperature: float, setpoint: float) -> None:
        """Record a temperature sample.

        Args:
            temperature: Current temperature
            setpoint: Current setpoint
        """
        self._samples.append(
            TemperatureSample(
                timestamp=dt_util.utcnow(),
                temperature=temperature,
                setpoint=setpoint,
            )
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        # First, try to record a new sample from climate entity
        await self._record_current_sample()

        # Then calculate time at target
        time_at_target = self._calculate_time_at_target()
        self._state = round(time_at_target, 1)

    async def _record_current_sample(self) -> None:
        """Record current temperature and setpoint from climate entity."""
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            return

        # Get current temperature
        current_temp = climate_state.attributes.get("current_temperature")
        if current_temp is None:
            return

        # Get setpoint
        setpoint = climate_state.attributes.get("temperature")
        if setpoint is None:
            return

        try:
            self.record_sample(float(current_temp), float(setpoint))
        except (ValueError, TypeError):
            pass

    def _calculate_time_at_target(self) -> float:
        """Calculate percentage of time within target band.

        Returns:
            Percentage (0-100) of time at target
        """
        now = dt_util.utcnow()
        window_start = now - self._measurement_window

        # Filter samples within window
        samples_in_window = [
            s for s in self._samples if s.timestamp >= window_start
        ]

        if not samples_in_window:
            return 0.0

        # Count samples within tolerance
        at_target = sum(
            1
            for s in samples_in_window
            if abs(s.temperature - s.setpoint) <= self._tolerance
        )

        return (at_target / len(samples_in_window)) * 100.0


class ComfortScoreSensor(AdaptiveThermostatSensor):
    """Composite comfort score sensor (0-100).

    Combines multiple comfort metrics into a single score:
    - Time at target: 60% weight
    - Low deviation: 25% weight
    - Low oscillations: 15% weight
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the comfort score sensor.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            zone_name: Human-readable zone name
            climate_entity_id: Entity ID of the climate entity
        """
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Comfort Score"
        self._attr_unique_id = f"{zone_id}_comfort_score"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:emoticon-happy-outline"
        self._state: float = 0.0

        # Component scores for attributes
        self._time_at_target_score: float = 0.0
        self._deviation_score: float = 0.0
        self._oscillation_score: float = 0.0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "time_at_target_score": round(self._time_at_target_score, 1),
            "deviation_score": round(self._deviation_score, 1),
            "oscillation_score": round(self._oscillation_score, 1),
        }

    async def async_update(self) -> None:
        """Update the sensor state."""
        comfort_score = await self._calculate_comfort_score()
        self._state = round(comfort_score, 0)

    async def _calculate_comfort_score(self) -> float:
        """Calculate composite comfort score.

        Returns:
            Comfort score 0-100
        """
        # Get time at target sensor value
        time_at_target = await self._get_time_at_target()
        self._time_at_target_score = time_at_target

        # Get deviation score (inverse of average deviation)
        deviation = await self._get_average_deviation()
        # Convert deviation to score: 0°C deviation = 100, 2°C deviation = 0
        self._deviation_score = max(0.0, 100.0 - (deviation * 50.0))

        # Get oscillation score (inverse of oscillation count)
        oscillations = await self._get_oscillations()
        # Convert oscillations to score: 0 oscillations = 100, 10+ = 0
        self._oscillation_score = max(0.0, 100.0 - (oscillations * 10.0))

        # Weighted combination
        score = (
            self._time_at_target_score * 0.60
            + self._deviation_score * 0.25
            + self._oscillation_score * 0.15
        )

        return min(100.0, max(0.0, score))

    async def _get_time_at_target(self) -> float:
        """Get time at target percentage from corresponding sensor.

        Returns:
            Time at target percentage (0-100)
        """
        sensor_id = f"sensor.{self._zone_id}_time_at_target"
        state = self.hass.states.get(sensor_id)

        if state and state.state not in ("unknown", "unavailable"):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass

        return 0.0

    async def _get_average_deviation(self) -> float:
        """Calculate average temperature deviation from setpoint.

        Returns:
            Average deviation in °C
        """
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            return 0.0

        current_temp = climate_state.attributes.get("current_temperature")
        setpoint = climate_state.attributes.get("temperature")

        if current_temp is None or setpoint is None:
            return 0.0

        try:
            return abs(float(current_temp) - float(setpoint))
        except (ValueError, TypeError):
            return 0.0

    async def _get_oscillations(self) -> float:
        """Get oscillation count from corresponding sensor.

        Returns:
            Oscillation count
        """
        sensor_id = f"sensor.{self._zone_id}_oscillations"
        state = self.hass.states.get(sensor_id)

        if state and state.state not in ("unknown", "unavailable"):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass

        return 0.0
