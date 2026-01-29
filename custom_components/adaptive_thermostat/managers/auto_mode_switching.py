"""Auto mode switching manager for house-wide HVAC mode control."""
from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING

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
        """Get current season based on forecast median temp."""
        # Placeholder - will be implemented in task 21
        raise NotImplementedError("Implemented in task 21")

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
        """Check if forecast suggests proactive mode switch."""
        # Placeholder - will be implemented in task 21
        raise NotImplementedError("Implemented in task 21")

    async def async_evaluate(self) -> str | None:
        """Evaluate and return new mode if switch needed, None otherwise."""
        # Placeholder - will be implemented in task 22
        raise NotImplementedError("Implemented in task 22")
