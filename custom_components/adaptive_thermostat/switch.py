"""Demand switches for Adaptive Thermostat."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Default PID output threshold for demand detection (0-100%)
DEFAULT_DEMAND_THRESHOLD = 5.0


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Adaptive Thermostat demand switches."""
    if discovery_info is None:
        return

    zone_id = discovery_info.get("zone_id")
    zone_name = discovery_info.get("zone_name")
    climate_entity_id = discovery_info.get("climate_entity_id")
    demand_threshold = discovery_info.get("demand_threshold", DEFAULT_DEMAND_THRESHOLD)

    if not zone_id or not climate_entity_id:
        _LOGGER.error("Missing required discovery info for switch platform")
        return

    switches = [
        DemandSwitch(hass, zone_id, zone_name, climate_entity_id, demand_threshold),
    ]

    async_add_entities(switches, True)


class DemandSwitch(SwitchEntity):
    """Switch entity that indicates when a zone has heating/cooling demand.

    This switch automatically turns ON when the PID output is above a threshold,
    and OFF when the PID output is zero or the zone is satisfied.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
        demand_threshold: float = DEFAULT_DEMAND_THRESHOLD,
    ) -> None:
        """Initialize the demand switch."""
        self.hass = hass
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._climate_entity_id = climate_entity_id
        self._demand_threshold = demand_threshold
        self._attr_name = f"{zone_name} Demand"
        self._attr_unique_id = f"{zone_id}_demand"
        self._attr_icon = "mdi:radiator"
        self._attr_should_poll = False
        self._is_on = False

        # Track climate entity state changes
        async_track_state_change_event(
            hass, [climate_entity_id], self._async_climate_state_changed
        )

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on (zone has demand)."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Switch is available if climate entity exists
        climate_state = self.hass.states.get(self._climate_entity_id)
        return climate_state is not None

    @callback
    def _async_climate_state_changed(self, event) -> None:
        """Handle climate entity state changes."""
        self.async_schedule_update_ha_state(force_refresh=True)

    async def async_update(self) -> None:
        """Update the switch state based on PID output."""
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            self._is_on = False
            return

        # Check PID output from climate entity attributes
        pid_output = climate_state.attributes.get("pid_output")
        if pid_output is None:
            # Fallback: check if heater is currently on
            heater_entity_id = climate_state.attributes.get("heater_entity_id")
            if heater_entity_id:
                heater_state = self.hass.states.get(heater_entity_id)
                self._is_on = heater_state and heater_state.state == "on"
            else:
                self._is_on = False
            return

        # Turn ON when PID output > threshold (zone needs heating)
        # Turn OFF when PID output = 0 (zone is satisfied)
        self._is_on = float(pid_output) > self._demand_threshold

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch (not user-controllable, state follows PID)."""
        _LOGGER.warning(
            "Demand switch %s cannot be manually controlled. "
            "State is automatically determined by PID output.",
            self._attr_name
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch (not user-controllable, state follows PID)."""
        _LOGGER.warning(
            "Demand switch %s cannot be manually controlled. "
            "State is automatically determined by PID output.",
            self._attr_name
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            return {}

        pid_output = climate_state.attributes.get("pid_output")

        return {
            "climate_entity_id": self._climate_entity_id,
            "demand_threshold": self._demand_threshold,
            "pid_output": pid_output,
            "zone_id": self._zone_id,
        }
