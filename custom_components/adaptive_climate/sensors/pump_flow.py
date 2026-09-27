"""System-wide diagnostic sensor for pump flow control."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_ATTRIBUTE_KEYS = (
    "open_zones",
    "pending_zones",
    "raw_sum",
    "min_flow_applied",
    "decrease_pending_since",
    "binding_constraint",
    "last_write",
)


class PumpFlowTargetSensor(SensorEntity):
    """Reports the flow currently requested from the system pump."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the sensor.

        Args:
            hass: Home Assistant instance.
        """
        # Deferred for the same reason as WaterTempSupplySensor: keeps sensor.py importable in tests.
        from homeassistant.helpers.entity import EntityCategory

        self.hass = hass
        self._attr_name = "Pump Flow Target"
        self._attr_unique_id = "pump_flow_target"
        self._attr_icon = "mdi:pump"
        self._attr_should_poll = False
        self._attr_native_unit_of_measurement = UnitOfVolumeFlowRate.LITERS_PER_MINUTE
        self._attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_available = False
        self._state: float | None = None
        self._attributes: dict[str, Any] = {}

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this entity."""
        return self._attr_unique_id

    @property
    def name(self) -> str:
        """Return the name of this entity."""
        return self._attr_name

    @property
    def _controller(self) -> Any | None:
        from ..const import DOMAIN

        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator is None:
            return None
        return getattr(coordinator, "pump_flow_controller", None)

    @property
    def native_value(self) -> float | None:
        """Return the requested flow in L/min."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the diagnostic breakdown."""
        return self._attributes

    @property
    def available(self) -> bool:
        """Return whether the sensor currently has a usable reading."""
        return self._attr_available

    def _set_unavailable(self) -> None:
        self._state = None
        self._attributes = {}
        self._attr_available = False

    async def async_update(self) -> None:
        """Refresh state from the controller's diagnostics payload."""
        controller = self._controller
        if controller is None:
            self._set_unavailable()
            return
        try:
            diagnostics = controller.diagnostics()
        except Exception:  # broad: one bad sensor must not block the others
            _LOGGER.exception("Pump flow diagnostics failed")
            self._set_unavailable()
            return
        self._state = diagnostics.get("requested")
        self._attributes = {key: diagnostics.get(key) for key in _ATTRIBUTE_KEYS}
        self._attr_available = True
