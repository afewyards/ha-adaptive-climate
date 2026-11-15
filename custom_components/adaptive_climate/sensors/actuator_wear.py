"""Actuator wear tracking sensors for Adaptive Climate.

This module contains sensors that track actuator (contactor/valve) wear
based on on→off cycle counting, with maintenance alerts at configurable thresholds.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from ..const import (
    DOMAIN,
    ACTUATOR_MAINTENANCE_SOON_PCT,
    ACTUATOR_MAINTENANCE_DUE_PCT,
)

_LOGGER = logging.getLogger(__name__)

# Debounce window for rapid state changes (e.g. PWM cycling at high frequency)
_DEBOUNCE_SECONDS = 5.0


class ActuatorWearSensor(SensorEntity):
    """Sensor for tracking actuator wear based on cycle count.

    Tracks the number of on→off cycles for heater or cooler actuators
    and calculates wear percentage based on rated lifetime cycles.

    Provides maintenance alerts at 80% (maintenance_soon) and 90% (maintenance_due).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
        actuator_type: str,  # "heater" or "cooler"
        rated_cycles: int | None = None,
    ) -> None:
        """Initialize the actuator wear sensor.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            zone_name: Human-readable zone name
            climate_entity_id: Entity ID of the climate entity
            actuator_type: Type of actuator ("heater" or "cooler")
            rated_cycles: Expected lifetime cycles (None = no rated cycles configured)
        """
        self.hass = hass
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._climate_entity_id = climate_entity_id
        self._actuator_type = actuator_type
        self._rated_cycles = rated_cycles

        # Sensor attributes
        self._attr_name = f"{zone_name} {actuator_type.capitalize()} Wear"
        self._attr_unique_id = f"{zone_id}_{actuator_type}_wear"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:gauge"
        self._attr_should_poll = False
        self._attr_available = True
        self._attr_entity_registry_visible_default = False

        # State
        self._current_cycles: int = 0
        self._wear_percentage: float = 0.0
        self._maintenance_status: str = "ok"  # ok, maintenance_soon, maintenance_due
        self._state_listener_unsub = None
        # Debounce handle — coalesces rapid state-change events into a single update
        self._update_cancel: CALLBACK_TYPE | None = None

    @property
    def native_value(self) -> float | None:
        """Return the wear percentage."""
        if self._rated_cycles is None or self._rated_cycles == 0:
            return None
        return self._wear_percentage

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if self._rated_cycles is None:
            estimated_remaining = None
        else:
            estimated_remaining = max(0, self._rated_cycles - self._current_cycles)

        return {
            "total_cycles": self._current_cycles,
            "rated_cycles": self._rated_cycles,
            "estimated_remaining": estimated_remaining,
            "maintenance_status": self._maintenance_status,
            "actuator_type": self._actuator_type,
        }

    async def async_added_to_hass(self) -> None:
        """Set up state change tracking when added to hass."""
        await super().async_added_to_hass()

        # Initial state update
        await self._async_update_from_climate_state()

        # Listen to climate entity state changes (C4 fix - store unsub handle)
        self._state_listener_unsub = async_track_state_change_event(
            self.hass,
            [self._climate_entity_id],
            self._async_climate_state_changed,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when removed from hass (C4 fix)."""
        if self._state_listener_unsub:
            self._state_listener_unsub()
            self._state_listener_unsub = None
        if self._update_cancel:
            self._update_cancel()
            self._update_cancel = None

    @callback
    def _async_climate_state_changed(self, _event=None) -> None:
        """Handle climate entity state changes.

        Debounces rapid events (e.g. PWM switching) so only one update runs per
        coalesce window (_DEBOUNCE_SECONDS).
        """
        if self._update_cancel:
            self._update_cancel()

        async def _do_update(_now=None):
            self._update_cancel = None
            await self._async_update_from_climate_state()

        self._update_cancel = async_call_later(self.hass, _DEBOUNCE_SECONDS, _do_update)

    async def _async_update_from_climate_state(self) -> None:
        """Update sensor state from climate entity attributes."""
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            self._attr_available = False
            self.async_write_ha_state()
            return

        self._attr_available = True

        # Get cycle count from climate attributes
        # int when a demand switch is the single actuator (heater only), else {"heater": n, "cooler": n}
        cycle_count = climate_state.attributes.get("cycle_count", 0)
        if isinstance(cycle_count, dict):
            self._current_cycles = cycle_count.get(self._actuator_type, 0)
        else:
            self._current_cycles = cycle_count if self._actuator_type == "heater" else 0

        # Calculate wear percentage
        if self._rated_cycles and self._rated_cycles > 0:
            self._wear_percentage = min(100.0, (self._current_cycles / self._rated_cycles) * 100.0)

            # Determine maintenance status
            if self._wear_percentage >= ACTUATOR_MAINTENANCE_DUE_PCT:
                self._maintenance_status = "maintenance_due"
                self._attr_icon = "mdi:gauge-full"
            elif self._wear_percentage >= ACTUATOR_MAINTENANCE_SOON_PCT:
                self._maintenance_status = "maintenance_soon"
                self._attr_icon = "mdi:gauge"
            else:
                self._maintenance_status = "ok"
                self._attr_icon = "mdi:gauge-low"
        else:
            self._wear_percentage = 0.0
            self._maintenance_status = "no_rating"

        self.async_write_ha_state()

        # Fire maintenance alert events if needed
        if self._maintenance_status in ["maintenance_soon", "maintenance_due"]:
            self._fire_maintenance_alert_event()

    def _fire_maintenance_alert_event(self) -> None:
        """Fire a maintenance alert event."""
        self.hass.bus.async_fire(
            f"{DOMAIN}_actuator_maintenance_alert",
            {
                "climate_entity_id": self._climate_entity_id,
                "zone_name": self._zone_name,
                "actuator_type": self._actuator_type,
                "total_cycles": self._current_cycles,
                "rated_cycles": self._rated_cycles,
                "wear_percentage": self._wear_percentage,
                "maintenance_status": self._maintenance_status,
            },
        )
