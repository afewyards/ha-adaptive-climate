"""Auto mode switching manager for house-wide HVAC mode control."""
from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from ..const import (
    CONF_AUTO_MODE_THRESHOLD,
    CONF_FORECAST_HOURS,
    CONF_MIN_SWITCH_INTERVAL,
    CONF_SEASON_THRESHOLDS,
    CONF_SUMMER_ABOVE,
    CONF_WINTER_BELOW,
    DEFAULT_AUTO_MODE_THRESHOLD,
    DEFAULT_FORECAST_HOURS,
    DEFAULT_MIN_SWITCH_INTERVAL,
    DEFAULT_SUMMER_ABOVE,
    DEFAULT_WINTER_BELOW,
)

if TYPE_CHECKING:
    from ..coordinator import AdaptiveThermostatCoordinator

_LOGGER = logging.getLogger(__name__)


class AutoModeSwitchingManager:
    """Manages automatic house-wide heat/cool mode switching."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict,
        coordinator: AdaptiveThermostatCoordinator,
    ) -> None:
        """Initialize the auto mode switching manager."""
        self._hass = hass
        self._coordinator = coordinator

        # Configuration
        self._threshold = config.get(CONF_AUTO_MODE_THRESHOLD, DEFAULT_AUTO_MODE_THRESHOLD)
        self._min_switch_interval = config.get(CONF_MIN_SWITCH_INTERVAL, DEFAULT_MIN_SWITCH_INTERVAL)
        self._forecast_hours = config.get(CONF_FORECAST_HOURS, DEFAULT_FORECAST_HOURS)

        season_config = config.get(CONF_SEASON_THRESHOLDS, {})
        self._winter_below = season_config.get(CONF_WINTER_BELOW, DEFAULT_WINTER_BELOW)
        self._summer_above = season_config.get(CONF_SUMMER_ABOVE, DEFAULT_SUMMER_ABOVE)

        # State
        self._current_mode: str | None = None
        self._last_switch: float = 0.0  # monotonic timestamp

    @property
    def current_mode(self) -> str | None:
        """Return the current auto-switched mode."""
        return self._current_mode

    @property
    def last_switch_time(self) -> float:
        """Return monotonic timestamp of last switch."""
        return self._last_switch

    def get_median_setpoint(self) -> float | None:
        """Get median setpoint from all active zones.

        Returns:
            Median setpoint temperature, or None if no active zones.
        """
        setpoints = self._coordinator.get_active_zone_setpoints()
        if not setpoints:
            _LOGGER.debug("No active zones for median setpoint calculation")
            return None
        return statistics.median(setpoints)

    def get_season(self) -> str:
        """Get current season based on forecast median temperature.

        Returns:
            Season string: "winter", "summer", or "shoulder"
            - winter: forecast median < winter_below threshold
            - summer: forecast median > summer_above threshold
            - shoulder: in between (both modes allowed)
        """
        forecast_median = self._get_forecast_median()

        if forecast_median is None:
            # No forecast available, assume shoulder season (both modes allowed)
            _LOGGER.debug("No forecast available, assuming shoulder season")
            return "shoulder"

        if forecast_median < self._winter_below:
            return "winter"
        elif forecast_median > self._summer_above:
            return "summer"
        else:
            return "shoulder"

    def _get_forecast_median(self) -> float | None:
        """Get median temperature from weather forecast.

        Uses the weather entity configured in coordinator to get forecast
        and calculates median of high temperatures over the next 7 days.

        Returns:
            Median forecast temperature, or None if forecast unavailable.
        """
        weather_entity = self._coordinator.weather_entity
        if not weather_entity:
            _LOGGER.debug("No weather entity configured for forecast")
            return None

        state = self._hass.states.get(weather_entity)
        if state is None:
            _LOGGER.warning("Weather entity %s not found", weather_entity)
            return None

        forecast = state.attributes.get("forecast", [])
        if not forecast:
            _LOGGER.debug("No forecast data available from %s", weather_entity)
            return None

        # Get temps from forecast (up to 7 days/entries)
        temps = []
        for entry in forecast[:7]:
            # Try 'temperature' (daily high) first, then 'templow' fallback
            temp = entry.get("temperature")
            if temp is not None:
                temps.append(temp)

        if not temps:
            _LOGGER.debug("No temperature data in forecast")
            return None

        return statistics.median(temps)

    async def _check_forecast(self) -> str | None:
        """Check if forecast suggests proactive mode switch.

        Looks at forecast for the next N hours (configured by forecast_hours)
        and returns a mode if weather is trending past the threshold.

        Returns:
            HVACMode.HEAT if cold weather coming,
            HVACMode.COOL if hot weather coming,
            None if no proactive switch needed.
        """
        weather_entity = self._coordinator.weather_entity
        if not weather_entity:
            return None

        state = self._hass.states.get(weather_entity)
        if state is None:
            return None

        forecast = state.attributes.get("forecast", [])
        if not forecast:
            return None

        median_setpoint = self.get_median_setpoint()
        if median_setpoint is None:
            return None

        # Check forecast entries within forecast_hours window
        # Home Assistant forecasts may be hourly or daily, check timestamp if available
        for entry in forecast[:self._forecast_hours]:
            temp = entry.get("temperature")
            if temp is None:
                continue

            # Check if forecast temp crosses thresholds
            if temp > median_setpoint + self._threshold:
                _LOGGER.debug(
                    "Forecast shows hot weather (%.1f°C > %.1f°C + %.1f°C), suggesting COOL",
                    temp, median_setpoint, self._threshold
                )
                return HVACMode.COOL
            if temp < median_setpoint - self._threshold:
                _LOGGER.debug(
                    "Forecast shows cold weather (%.1f°C < %.1f°C - %.1f°C), suggesting HEAT",
                    temp, median_setpoint, self._threshold
                )
                return HVACMode.HEAT

        return None

    async def async_evaluate(self) -> str | None:
        """Evaluate and return new mode if switch needed, None otherwise."""
        # Placeholder - will be implemented in task 22
        raise NotImplementedError("Implemented in task 22")
