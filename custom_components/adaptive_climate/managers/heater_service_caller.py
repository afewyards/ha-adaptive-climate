"""HA service call abstraction for HeaterController."""

from __future__ import annotations

import logging
import asyncio
from typing import TYPE_CHECKING, Any

try:
    from homeassistant.core import HomeAssistant, split_entity_id
    from homeassistant.components.number.const import DOMAIN as NUMBER_DOMAIN
    from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
    from homeassistant.exceptions import HomeAssistantError, ServiceNotFound

    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any
    NUMBER_DOMAIN = "number"
    INPUT_NUMBER_DOMAIN = "input_number"
    HomeAssistantError = Exception
    ServiceNotFound = Exception

    def split_entity_id(entity_id: str) -> tuple[str, str]:  # type: ignore[misc]
        parts = entity_id.split(".", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else (entity_id, "")


from ..const import EVENT_HEATER_CONTROL_FAILED

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


class HeaterServiceCaller:
    """Handles HA service calls for heater/cooler entities with error handling.

    Centralises all hass.services.async_call() invocations so error handling and
    failure-event firing live in one place.
    """

    def __init__(self, hass: HomeAssistant, thermostat: AdaptiveThermostat) -> None:
        """Initialise.

        Args:
            hass: Home Assistant instance.
            thermostat: Parent thermostat entity (used for entity_id in events/logs).
        """
        self._hass = hass
        self._thermostat = thermostat
        self._heater_control_failed: bool = False
        self._last_heater_error: str | None = None
        self.blocking = False

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def heater_control_failed(self) -> bool:
        """Return True if the last heater control operation failed."""
        return self._heater_control_failed

    @property
    def last_heater_error(self) -> str | None:
        """Return the last heater error message, if any."""
        return self._last_heater_error

    # ── Service call helpers ───────────────────────────────────────────────────

    def fire_control_failed_event(self, entity_id: str, operation: str, error: str) -> None:
        """Fire an HA bus event when heater control fails.

        Args:
            entity_id: The entity that failed to be controlled.
            operation: Service name that was attempted (turn_on / turn_off / set_value).
            error: Error message string.
        """
        self._hass.bus.async_fire(
            EVENT_HEATER_CONTROL_FAILED,
            {
                "climate_entity_id": self._thermostat.entity_id,
                "heater_entity_id": entity_id,
                "operation": operation,
                "error": error,
            },
        )

    async def async_call(self, entity_id: str, domain: str, service: str, data: dict) -> bool:
        """Call a heater/cooler service, handling all error types.

        Args:
            entity_id: Entity being controlled (used for logging/events).
            domain: HA service domain (homeassistant, light, valve, number, …).
            service: Service name (turn_on, turn_off, set_value, …).
            data: Service call data payload.

        Returns:
            True if the call succeeded, False otherwise.
        """
        thermostat_entity_id = self._thermostat.entity_id

        try:
            if self.blocking:
                async with asyncio.timeout(30):
                    await self._hass.services.async_call(domain, service, data, blocking=True)
            else:
                await self._hass.services.async_call(domain, service, data)
            self._heater_control_failed = False
            self._last_heater_error = None
            return True

        except ServiceNotFound as e:
            _LOGGER.error(
                "%s: Service '%s.%s' not found for %s: %s",
                thermostat_entity_id,
                domain,
                service,
                entity_id,
                e,
            )
            self._heater_control_failed = True
            self._last_heater_error = f"Service not found: {domain}.{service}"
            self.fire_control_failed_event(entity_id, service, str(e))
            return False

        except HomeAssistantError as e:
            _LOGGER.error(
                "%s: Home Assistant error calling %s.%s on %s: %s",
                thermostat_entity_id,
                domain,
                service,
                entity_id,
                e,
            )
            self._heater_control_failed = True
            self._last_heater_error = str(e)
            self.fire_control_failed_event(entity_id, service, str(e))
            return False

        except Exception as e:
            _LOGGER.error(
                "%s: Unexpected error calling %s.%s on %s: %s",
                thermostat_entity_id,
                domain,
                service,
                entity_id,
                e,
            )
            self._heater_control_failed = True
            self._last_heater_error = str(e)
            self.fire_control_failed_event(entity_id, service, str(e))
            return False

    @staticmethod
    def get_number_entity_domain(entity_id: str) -> str:
        """Return the HA domain for a number-like entity.

        Args:
            entity_id: Entity ID to inspect.

        Returns:
            ``INPUT_NUMBER_DOMAIN`` for input_number entities, ``NUMBER_DOMAIN`` otherwise.
        """
        domain, _ = split_entity_id(entity_id)
        return INPUT_NUMBER_DOMAIN if domain == "input_number" else NUMBER_DOMAIN
