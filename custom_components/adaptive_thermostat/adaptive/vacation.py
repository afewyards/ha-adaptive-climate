"""Vacation mode handling for Adaptive Thermostat."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.const import SERVICE_SET_TEMPERATURE

if TYPE_CHECKING:
    from ..coordinator import AdaptiveThermostatCoordinator

_LOGGER = logging.getLogger(__name__)

DEFAULT_VACATION_TEMP = 12.0


class VacationMode:
    """Manage vacation mode across all zones.

    When vacation mode is enabled:
    - All zones are set to a frost protection temperature
    - Adaptive learning is paused to prevent bad data
    - Original setpoints are stored for restoration

    When vacation mode is disabled:
    - Original setpoints are restored
    - Learning resumes
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: "AdaptiveThermostatCoordinator",
    ) -> None:
        """Initialize vacation mode handler.

        Args:
            hass: Home Assistant instance
            coordinator: The thermostat coordinator
        """
        self.hass = hass
        self.coordinator = coordinator
        self._enabled = False
        self._target_temp = DEFAULT_VACATION_TEMP
        self._original_setpoints: dict[str, float] = {}
        self._learning_was_enabled: dict[str, bool] = {}

    @property
    def enabled(self) -> bool:
        """Return whether vacation mode is enabled."""
        return self._enabled

    @property
    def target_temp(self) -> float:
        """Return the vacation target temperature."""
        return self._target_temp

    async def async_enable(self, target_temp: float = DEFAULT_VACATION_TEMP) -> None:
        """Enable vacation mode.

        Args:
            target_temp: The frost protection temperature to set (default 12°C)
        """
        if self._enabled:
            _LOGGER.warning("Vacation mode is already enabled")
            return

        self._target_temp = target_temp
        self._original_setpoints = {}
        self._learning_was_enabled = {}

        # Get all zones from coordinator
        all_zones = self.coordinator.get_all_zones()

        for zone_id, zone_data in all_zones.items():
            climate_entity_id = zone_data.get("climate_entity_id")
            if not climate_entity_id:
                continue

            # Get current state
            state = self.hass.states.get(climate_entity_id)
            if state:
                # Store original setpoint
                current_temp = state.attributes.get("temperature")
                if current_temp is not None:
                    self._original_setpoints[zone_id] = float(current_temp)

                # Store learning state if available
                learning_enabled = zone_data.get("learning_enabled", True)
                self._learning_was_enabled[zone_id] = learning_enabled

            # Set to vacation temperature
            try:
                await self.hass.services.async_call(
                    "climate",
                    SERVICE_SET_TEMPERATURE,
                    {
                        "entity_id": climate_entity_id,
                        "temperature": target_temp,
                    },
                    blocking=True,
                )
                _LOGGER.info(
                    "Set zone %s to vacation temperature %.1f°C",
                    zone_id,
                    target_temp,
                )
            except Exception as e:
                _LOGGER.error(
                    "Failed to set vacation temperature for zone %s: %s",
                    zone_id,
                    e,
                )

            # Pause learning for this zone
            zone_data["learning_enabled"] = False

        self._enabled = True
        _LOGGER.info(
            "Vacation mode enabled with target temperature %.1f°C for %d zones",
            target_temp,
            len(all_zones),
        )

    async def async_disable(self) -> None:
        """Disable vacation mode and restore original setpoints."""
        if not self._enabled:
            _LOGGER.warning("Vacation mode is not enabled")
            return

        # Get all zones from coordinator
        all_zones = self.coordinator.get_all_zones()

        for zone_id, zone_data in all_zones.items():
            climate_entity_id = zone_data.get("climate_entity_id")
            if not climate_entity_id:
                continue

            # Restore original setpoint if we have it
            original_temp = self._original_setpoints.get(zone_id)
            if original_temp is not None:
                try:
                    await self.hass.services.async_call(
                        "climate",
                        SERVICE_SET_TEMPERATURE,
                        {
                            "entity_id": climate_entity_id,
                            "temperature": original_temp,
                        },
                        blocking=True,
                    )
                    _LOGGER.info(
                        "Restored zone %s to original temperature %.1f°C",
                        zone_id,
                        original_temp,
                    )
                except Exception as e:
                    _LOGGER.error(
                        "Failed to restore temperature for zone %s: %s",
                        zone_id,
                        e,
                    )

            # Restore learning state
            was_enabled = self._learning_was_enabled.get(zone_id, True)
            zone_data["learning_enabled"] = was_enabled

        self._enabled = False
        self._original_setpoints = {}
        self._learning_was_enabled = {}
        _LOGGER.info("Vacation mode disabled, original setpoints restored")

    def get_status(self) -> dict[str, Any]:
        """Get current vacation mode status.

        Returns:
            Dictionary with vacation mode status details
        """
        return {
            "enabled": self._enabled,
            "target_temp": self._target_temp,
            "zones_affected": len(self._original_setpoints),
            "original_setpoints": self._original_setpoints.copy(),
        }
