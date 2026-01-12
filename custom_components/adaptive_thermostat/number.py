"""Number entities for Adaptive Thermostat."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import (
    DOMAIN,
    DEFAULT_LEARNING_WINDOW_DAYS,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Adaptive Thermostat number entities."""
    # Create system-level number entity for learning window
    entities = [
        LearningWindowNumber(hass),
    ]

    async_add_entities(entities, True)


class LearningWindowNumber(NumberEntity):
    """Number entity for adjusting the learning window in days."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the number entity."""
        self.hass = hass
        self._attr_name = "Adaptive Thermostat Learning Window"
        self._attr_unique_id = f"{DOMAIN}_learning_window"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 30
        self._attr_native_step = 1
        self._attr_native_value = DEFAULT_LEARNING_WINDOW_DAYS
        self._attr_native_unit_of_measurement = "days"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:calendar-clock"
        self._attr_should_poll = False

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        self._attr_native_value = int(value)

        # Store in hass.data for other components to access
        if DOMAIN not in self.hass.data:
            self.hass.data[DOMAIN] = {}
        self.hass.data[DOMAIN]["learning_window_days"] = int(value)

        self.async_write_ha_state()
        _LOGGER.debug("Learning window set to %d days", int(value))

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Initialize the value in hass.data
        if DOMAIN not in self.hass.data:
            self.hass.data[DOMAIN] = {}
        self.hass.data[DOMAIN]["learning_window_days"] = self._attr_native_value
