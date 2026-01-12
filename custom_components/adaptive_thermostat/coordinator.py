"""Data Update Coordinator for Adaptive Thermostat."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

try:
    from .const import DOMAIN
except ImportError:
    from const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class AdaptiveThermostatCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from thermostats and coordinating cross-zone state."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self._zones: dict[str, dict[str, Any]] = {}
        self._demand_states: dict[str, bool] = {}

    def register_zone(self, zone_id: str, zone_data: dict[str, Any]) -> None:
        """Register a zone with the coordinator.

        Args:
            zone_id: Unique identifier for the zone
            zone_data: Dictionary containing zone information
        """
        self._zones[zone_id] = zone_data
        self._demand_states[zone_id] = False
        _LOGGER.debug("Registered zone: %s", zone_id)

    def update_zone_demand(self, zone_id: str, has_demand: bool) -> None:
        """Update the demand state for a zone.

        Args:
            zone_id: Unique identifier for the zone
            has_demand: Whether the zone currently has heating/cooling demand
        """
        if zone_id in self._demand_states:
            self._demand_states[zone_id] = has_demand
            _LOGGER.debug("Updated demand for zone %s: %s", zone_id, has_demand)

    def get_aggregate_demand(self) -> dict[str, bool]:
        """Get aggregated demand across all zones.

        Returns:
            Dictionary with 'heating' and 'cooling' boolean values indicating
            if any zone has demand for that mode.
        """
        has_heating_demand = any(self._demand_states.values())
        # For now, we only track heating demand. Cooling will be added later.
        return {
            "heating": has_heating_demand,
            "cooling": False,
        }

    def get_all_zones(self) -> dict[str, dict[str, Any]]:
        """Get all registered zones.

        Returns:
            Dictionary of all registered zones with their data.
        """
        return self._zones.copy()

    def get_zone_data(self, zone_id: str) -> dict[str, Any] | None:
        """Get data for a specific zone.

        Args:
            zone_id: Unique identifier for the zone

        Returns:
            Zone data dictionary or None if zone not found.
        """
        return self._zones.get(zone_id)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from all zones.

        This method is called automatically by the coordinator at the
        configured update interval.

        Returns:
            Dictionary containing current state of all zones.
        """
        # For now, we just return the current state
        # In the future, this could fetch additional data or perform calculations
        return {
            "zones": self._zones,
            "demand": self._demand_states,
            "aggregate_demand": self.get_aggregate_demand(),
        }


class CentralController:
    """Central controller for managing main heater/cooler based on zone demand.

    Features:
    - Aggregates demand from all zones
    - Controls main_heater_switch and main_cooler_switch
    - Implements startup delay before activating heat source
    - Immediate off when no demand
    - Independent control of heater and cooler
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AdaptiveThermostatCoordinator,
        main_heater_switch: str | None = None,
        main_cooler_switch: str | None = None,
        startup_delay_seconds: int = 0,
    ) -> None:
        """Initialize the central controller.

        Args:
            hass: Home Assistant instance
            coordinator: AdaptiveThermostatCoordinator instance
            main_heater_switch: Entity ID of main heater switch (e.g., "switch.boiler")
            main_cooler_switch: Entity ID of main cooler switch (e.g., "switch.chiller")
            startup_delay_seconds: Delay in seconds before activating heat source
        """
        self.hass = hass
        self.coordinator = coordinator
        self.main_heater_switch = main_heater_switch
        self.main_cooler_switch = main_cooler_switch
        self.startup_delay_seconds = startup_delay_seconds

        # Track startup delay state
        self._heater_startup_task: asyncio.Task | None = None
        self._cooler_startup_task: asyncio.Task | None = None
        self._heater_waiting_for_startup = False
        self._cooler_waiting_for_startup = False

        _LOGGER.debug(
            "CentralController initialized: heater=%s, cooler=%s, delay=%ds",
            main_heater_switch,
            main_cooler_switch,
            startup_delay_seconds,
        )

    async def update(self) -> None:
        """Update central controller state based on zone demand.

        This method should be called periodically (e.g., every 30 seconds)
        or when zone demand changes.
        """
        demand = self.coordinator.get_aggregate_demand()

        # Update heater
        if self.main_heater_switch:
            await self._update_heater(demand["heating"])

        # Update cooler
        if self.main_cooler_switch:
            await self._update_cooler(demand["cooling"])

    async def _update_heater(self, has_demand: bool) -> None:
        """Update heater state based on demand.

        Args:
            has_demand: Whether any zone has heating demand
        """
        if has_demand:
            # Zone(s) need heating
            if not self._heater_waiting_for_startup and not await self._is_switch_on(self.main_heater_switch):
                # Start heater with delay
                await self._start_heater_with_delay()
        else:
            # No demand - turn off immediately
            await self._cancel_heater_startup()
            await self._turn_off_switch(self.main_heater_switch)

    async def _update_cooler(self, has_demand: bool) -> None:
        """Update cooler state based on demand.

        Args:
            has_demand: Whether any zone has cooling demand
        """
        if has_demand:
            # Zone(s) need cooling
            if not self._cooler_waiting_for_startup and not await self._is_switch_on(self.main_cooler_switch):
                # Start cooler with delay
                await self._start_cooler_with_delay()
        else:
            # No demand - turn off immediately
            await self._cancel_cooler_startup()
            await self._turn_off_switch(self.main_cooler_switch)

    async def _start_heater_with_delay(self) -> None:
        """Start heater after startup delay."""
        # Cancel any existing startup task
        await self._cancel_heater_startup()

        if self.startup_delay_seconds == 0:
            # No delay - turn on immediately
            await self._turn_on_switch(self.main_heater_switch)
        else:
            # Schedule delayed startup
            self._heater_waiting_for_startup = True
            _LOGGER.debug("Heater startup scheduled in %d seconds", self.startup_delay_seconds)
            self._heater_startup_task = asyncio.create_task(self._delayed_heater_startup())

    async def _start_cooler_with_delay(self) -> None:
        """Start cooler after startup delay."""
        # Cancel any existing startup task
        await self._cancel_cooler_startup()

        if self.startup_delay_seconds == 0:
            # No delay - turn on immediately
            await self._turn_on_switch(self.main_cooler_switch)
        else:
            # Schedule delayed startup
            self._cooler_waiting_for_startup = True
            _LOGGER.debug("Cooler startup scheduled in %d seconds", self.startup_delay_seconds)
            self._cooler_startup_task = asyncio.create_task(self._delayed_cooler_startup())

    async def _delayed_heater_startup(self) -> None:
        """Delayed heater startup task."""
        try:
            await asyncio.sleep(self.startup_delay_seconds)

            # Check if we still have demand
            demand = self.coordinator.get_aggregate_demand()
            if demand["heating"]:
                await self._turn_on_switch(self.main_heater_switch)
                _LOGGER.info("Heater started after %d second delay", self.startup_delay_seconds)
            else:
                _LOGGER.debug("Heater startup cancelled - no demand after delay")
        except asyncio.CancelledError:
            _LOGGER.debug("Heater startup cancelled")
        finally:
            self._heater_waiting_for_startup = False
            self._heater_startup_task = None

    async def _delayed_cooler_startup(self) -> None:
        """Delayed cooler startup task."""
        try:
            await asyncio.sleep(self.startup_delay_seconds)

            # Check if we still have demand
            demand = self.coordinator.get_aggregate_demand()
            if demand["cooling"]:
                await self._turn_on_switch(self.main_cooler_switch)
                _LOGGER.info("Cooler started after %d second delay", self.startup_delay_seconds)
            else:
                _LOGGER.debug("Cooler startup cancelled - no demand after delay")
        except asyncio.CancelledError:
            _LOGGER.debug("Cooler startup cancelled")
        finally:
            self._cooler_waiting_for_startup = False
            self._cooler_startup_task = None

    async def _cancel_heater_startup(self) -> None:
        """Cancel pending heater startup."""
        if self._heater_startup_task and not self._heater_startup_task.done():
            self._heater_startup_task.cancel()
            try:
                await self._heater_startup_task
            except asyncio.CancelledError:
                pass
        self._heater_waiting_for_startup = False
        self._heater_startup_task = None

    async def _cancel_cooler_startup(self) -> None:
        """Cancel pending cooler startup."""
        if self._cooler_startup_task and not self._cooler_startup_task.done():
            self._cooler_startup_task.cancel()
            try:
                await self._cooler_startup_task
            except asyncio.CancelledError:
                pass
        self._cooler_waiting_for_startup = False
        self._cooler_startup_task = None

    async def _is_switch_on(self, entity_id: str) -> bool:
        """Check if a switch is currently on.

        Args:
            entity_id: Entity ID of the switch

        Returns:
            True if switch is on, False otherwise
        """
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Switch %s not found", entity_id)
            return False
        return state.state == "on"

    async def _turn_on_switch(self, entity_id: str) -> None:
        """Turn on a switch.

        Args:
            entity_id: Entity ID of the switch
        """
        await self.hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id},
            blocking=True,
        )
        _LOGGER.debug("Turned on switch: %s", entity_id)

    async def _turn_off_switch(self, entity_id: str) -> None:
        """Turn off a switch.

        Args:
            entity_id: Entity ID of the switch
        """
        # Only turn off if currently on
        if await self._is_switch_on(entity_id):
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )
            _LOGGER.debug("Turned off switch: %s", entity_id)

    def is_heater_waiting_for_startup(self) -> bool:
        """Check if heater is waiting for startup delay.

        Returns:
            True if heater startup is pending
        """
        return self._heater_waiting_for_startup

    def is_cooler_waiting_for_startup(self) -> bool:
        """Check if cooler is waiting for startup delay.

        Returns:
            True if cooler startup is pending
        """
        return self._cooler_waiting_for_startup
