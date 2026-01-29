"""Auto mode switching manager for house-wide HVAC mode control."""
from __future__ import annotations

import logging
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
        """Get median setpoint from all active zones."""
        # Placeholder - will be implemented in task 20
        raise NotImplementedError("Implemented in task 20")

    def get_season(self) -> str:
        """Get current season based on forecast median temp."""
        # Placeholder - will be implemented in task 21
        raise NotImplementedError("Implemented in task 21")

    def _get_forecast_median(self) -> float | None:
        """Get median temperature from weather forecast."""
        # Placeholder - will be implemented in task 20
        raise NotImplementedError("Implemented in task 20")

    async def _check_forecast(self) -> str | None:
        """Check if forecast suggests proactive mode switch."""
        # Placeholder - will be implemented in task 21
        raise NotImplementedError("Implemented in task 21")

    async def async_evaluate(self) -> str | None:
        """Evaluate and return new mode if switch needed, None otherwise."""
        # Placeholder - will be implemented in task 22
        raise NotImplementedError("Implemented in task 22")
