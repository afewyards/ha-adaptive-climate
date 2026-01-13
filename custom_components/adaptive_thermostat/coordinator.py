"""Data Update Coordinator for Adaptive Thermostat."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

try:
    from .const import DOMAIN
except ImportError:
    from const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Error handling constants for CentralController
MAX_SERVICE_CALL_RETRIES = 3
BASE_RETRY_DELAY_SECONDS = 1.0
CONSECUTIVE_FAILURE_WARNING_THRESHOLD = 3


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
        self._central_controller: "CentralController | None" = None

    def set_central_controller(self, controller: "CentralController") -> None:
        """Set the central controller reference for push-based updates."""
        self._central_controller = controller

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
            old_demand = self._demand_states[zone_id]
            self._demand_states[zone_id] = has_demand

            # Only trigger central controller if demand actually changed
            if old_demand != has_demand:
                _LOGGER.info("Demand changed for zone %s: %s -> %s", zone_id, old_demand, has_demand)
                if self._central_controller:
                    _LOGGER.info("Triggering CentralController update")
                    self.hass.async_create_task(self._central_controller.update())

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

        # Lock to protect startup state from race conditions during concurrent updates
        self._startup_lock = asyncio.Lock()

        # Track consecutive failures per switch for health monitoring
        self._consecutive_failures: dict[str, int] = {}

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
        async with self._startup_lock:
            if has_demand:
                # Zone(s) need heating
                if not self._heater_waiting_for_startup and not await self._is_switch_on(self.main_heater_switch):
                    # Start heater with delay
                    await self._start_heater_with_delay_unlocked()
            else:
                # No demand - turn off immediately
                await self._cancel_heater_startup_unlocked()
                await self._turn_off_switch(self.main_heater_switch)

    async def _update_cooler(self, has_demand: bool) -> None:
        """Update cooler state based on demand.

        Args:
            has_demand: Whether any zone has cooling demand
        """
        async with self._startup_lock:
            if has_demand:
                # Zone(s) need cooling
                if not self._cooler_waiting_for_startup and not await self._is_switch_on(self.main_cooler_switch):
                    # Start cooler with delay
                    await self._start_cooler_with_delay_unlocked()
            else:
                # No demand - turn off immediately
                await self._cancel_cooler_startup_unlocked()
                await self._turn_off_switch(self.main_cooler_switch)

    async def _start_heater_with_delay_unlocked(self) -> None:
        """Start heater after startup delay.

        Note: Must be called while holding _startup_lock.
        """
        # Cancel any existing startup task
        await self._cancel_heater_startup_unlocked()

        if self.startup_delay_seconds == 0:
            # No delay - turn on immediately
            await self._turn_on_switch(self.main_heater_switch)
        else:
            # Schedule delayed startup
            self._heater_waiting_for_startup = True
            _LOGGER.debug("Heater startup scheduled in %d seconds", self.startup_delay_seconds)
            self._heater_startup_task = asyncio.create_task(self._delayed_heater_startup())

    async def _start_cooler_with_delay_unlocked(self) -> None:
        """Start cooler after startup delay.

        Note: Must be called while holding _startup_lock.
        """
        # Cancel any existing startup task
        await self._cancel_cooler_startup_unlocked()

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

            # Check if we still have demand (under lock to prevent races)
            async with self._startup_lock:
                demand = self.coordinator.get_aggregate_demand()
                if demand["heating"]:
                    await self._turn_on_switch(self.main_heater_switch)
                    _LOGGER.info("Heater started after %d second delay", self.startup_delay_seconds)
                else:
                    _LOGGER.debug("Heater startup cancelled - no demand after delay")
        except asyncio.CancelledError:
            _LOGGER.debug("Heater startup cancelled")
        finally:
            async with self._startup_lock:
                self._heater_waiting_for_startup = False
                self._heater_startup_task = None

    async def _delayed_cooler_startup(self) -> None:
        """Delayed cooler startup task."""
        try:
            await asyncio.sleep(self.startup_delay_seconds)

            # Check if we still have demand (under lock to prevent races)
            async with self._startup_lock:
                demand = self.coordinator.get_aggregate_demand()
                if demand["cooling"]:
                    await self._turn_on_switch(self.main_cooler_switch)
                    _LOGGER.info("Cooler started after %d second delay", self.startup_delay_seconds)
                else:
                    _LOGGER.debug("Cooler startup cancelled - no demand after delay")
        except asyncio.CancelledError:
            _LOGGER.debug("Cooler startup cancelled")
        finally:
            async with self._startup_lock:
                self._cooler_waiting_for_startup = False
                self._cooler_startup_task = None

    async def _cancel_heater_startup_unlocked(self) -> None:
        """Cancel pending heater startup.

        Note: Must be called while holding _startup_lock.
        """
        if self._heater_startup_task and not self._heater_startup_task.done():
            self._heater_startup_task.cancel()
            # Release the lock while waiting for cancellation to complete
            # to avoid deadlock with the task's finally block
            task = self._heater_startup_task
            self._startup_lock.release()
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                await self._startup_lock.acquire()
        self._heater_waiting_for_startup = False
        self._heater_startup_task = None

    async def _cancel_cooler_startup_unlocked(self) -> None:
        """Cancel pending cooler startup.

        Note: Must be called while holding _startup_lock.
        """
        if self._cooler_startup_task and not self._cooler_startup_task.done():
            self._cooler_startup_task.cancel()
            # Release the lock while waiting for cancellation to complete
            # to avoid deadlock with the task's finally block
            task = self._cooler_startup_task
            self._startup_lock.release()
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                await self._startup_lock.acquire()
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

    async def _turn_on_switch(self, entity_id: str) -> bool:
        """Turn on a switch with retry logic.

        Args:
            entity_id: Entity ID of the switch

        Returns:
            True if switch was turned on successfully, False otherwise
        """
        return await self._call_switch_service(entity_id, "turn_on")

    async def _turn_off_switch(self, entity_id: str) -> bool:
        """Turn off a switch with retry logic.

        Args:
            entity_id: Entity ID of the switch

        Returns:
            True if switch was turned off successfully, False otherwise
        """
        # Only turn off if currently on
        if await self._is_switch_on(entity_id):
            return await self._call_switch_service(entity_id, "turn_off")
        return True

    async def _call_switch_service(
        self,
        entity_id: str,
        service: str,
    ) -> bool:
        """Call a switch service with retry logic and error handling.

        Implements exponential backoff retry logic for transient failures.
        Tracks consecutive failures and emits warnings when threshold is reached.

        Args:
            entity_id: Entity ID of the switch
            service: Service to call ("turn_on" or "turn_off")

        Returns:
            True if service call succeeded, False otherwise
        """
        last_exception: Exception | None = None

        for attempt in range(MAX_SERVICE_CALL_RETRIES):
            try:
                await self.hass.services.async_call(
                    "switch",
                    service,
                    {"entity_id": entity_id},
                    blocking=True,
                )
                _LOGGER.debug("Successfully called %s on switch: %s", service, entity_id)

                # Reset consecutive failure counter on success
                self._consecutive_failures[entity_id] = 0
                return True

            except ServiceNotFound as e:
                # Service doesn't exist - no point retrying
                _LOGGER.error(
                    "Service 'switch.%s' not found for %s: %s",
                    service,
                    entity_id,
                    e,
                )
                self._record_failure(entity_id)
                return False

            except HomeAssistantError as e:
                # Home Assistant error - may be transient, retry with backoff
                last_exception = e
                _LOGGER.warning(
                    "Home Assistant error calling %s on %s (attempt %d/%d): %s",
                    service,
                    entity_id,
                    attempt + 1,
                    MAX_SERVICE_CALL_RETRIES,
                    e,
                )

            except Exception as e:
                # Unexpected error - log and retry
                last_exception = e
                _LOGGER.warning(
                    "Unexpected error calling %s on %s (attempt %d/%d): %s",
                    service,
                    entity_id,
                    attempt + 1,
                    MAX_SERVICE_CALL_RETRIES,
                    e,
                )

            # Wait before retrying with exponential backoff
            if attempt < MAX_SERVICE_CALL_RETRIES - 1:
                delay = BASE_RETRY_DELAY_SECONDS * (2 ** attempt)
                _LOGGER.debug(
                    "Retrying %s on %s in %.1f seconds",
                    service,
                    entity_id,
                    delay,
                )
                await asyncio.sleep(delay)

        # All retries exhausted
        _LOGGER.error(
            "Failed to call %s on %s after %d attempts: %s",
            service,
            entity_id,
            MAX_SERVICE_CALL_RETRIES,
            last_exception,
        )
        self._record_failure(entity_id)
        return False

    def _record_failure(self, entity_id: str) -> None:
        """Record a failure for an entity and emit warning if threshold reached.

        Args:
            entity_id: Entity ID that failed
        """
        self._consecutive_failures[entity_id] = (
            self._consecutive_failures.get(entity_id, 0) + 1
        )
        failure_count = self._consecutive_failures[entity_id]

        if failure_count >= CONSECUTIVE_FAILURE_WARNING_THRESHOLD:
            _LOGGER.warning(
                "Switch %s has failed %d consecutive times. "
                "Check entity availability and Home Assistant logs.",
                entity_id,
                failure_count,
            )

    def get_consecutive_failures(self, entity_id: str) -> int:
        """Get the number of consecutive failures for an entity.

        Args:
            entity_id: Entity ID to check

        Returns:
            Number of consecutive failures (0 if none)
        """
        return self._consecutive_failures.get(entity_id, 0)

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


class ModeSync:
    """Mode synchronization across zones.

    Features:
    - Sync HEAT mode to all zones when one switches to HEAT
    - Sync COOL mode to all zones when one switches to COOL
    - Keep OFF mode independent per zone
    - Support disabling sync per zone
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AdaptiveThermostatCoordinator,
    ) -> None:
        """Initialize mode synchronization.

        Args:
            hass: Home Assistant instance
            coordinator: AdaptiveThermostatCoordinator instance
        """
        self.hass = hass
        self.coordinator = coordinator
        self._zone_modes: dict[str, str] = {}
        self._sync_disabled_zones: set[str] = set()
        self._sync_in_progress: bool = False

        _LOGGER.debug("ModeSync initialized")

    def disable_sync_for_zone(self, zone_id: str) -> None:
        """Disable mode synchronization for a specific zone.

        Args:
            zone_id: Zone identifier to disable sync for
        """
        self._sync_disabled_zones.add(zone_id)
        _LOGGER.debug("Mode sync disabled for zone: %s", zone_id)

    def enable_sync_for_zone(self, zone_id: str) -> None:
        """Enable mode synchronization for a specific zone.

        Args:
            zone_id: Zone identifier to enable sync for
        """
        self._sync_disabled_zones.discard(zone_id)
        _LOGGER.debug("Mode sync enabled for zone: %s", zone_id)

    def is_sync_disabled(self, zone_id: str) -> bool:
        """Check if sync is disabled for a zone.

        Args:
            zone_id: Zone identifier to check

        Returns:
            True if sync is disabled for this zone
        """
        return zone_id in self._sync_disabled_zones

    async def on_mode_change(
        self,
        zone_id: str,
        old_mode: str,
        new_mode: str,
        climate_entity_id: str,
    ) -> None:
        """Handle mode change for a zone.

        When a zone changes to HEAT or COOL mode, synchronize all other zones
        (except those with sync disabled) to the same mode.
        OFF mode is kept independent per zone.

        Args:
            zone_id: Zone that changed mode
            old_mode: Previous mode (heat, cool, off, etc.)
            new_mode: New mode (heat, cool, off, etc.)
            climate_entity_id: Entity ID of the climate entity that changed
        """
        # Update stored mode
        self._zone_modes[zone_id] = new_mode.lower()

        # Only sync HEAT and COOL modes (not OFF)
        if new_mode.lower() not in ("heat", "cool"):
            _LOGGER.debug(
                "Zone %s changed to %s mode - no sync needed (not heat/cool)",
                zone_id,
                new_mode,
            )
            return

        # Prevent feedback loop: skip if already syncing
        if self._sync_in_progress:
            _LOGGER.debug(
                "Zone %s mode change to %s - skipping sync (already in progress)",
                zone_id,
                new_mode,
            )
            return

        _LOGGER.info(
            "Zone %s changed to %s mode - synchronizing other zones",
            zone_id,
            new_mode,
        )

        # Set flag before syncing to prevent feedback loop
        self._sync_in_progress = True
        try:
            # Get all zones from coordinator
            all_zones = self.coordinator.get_all_zones()

            # Sync mode to all other zones (except sync-disabled ones)
            for other_zone_id, zone_data in all_zones.items():
                # Skip the originating zone
                if other_zone_id == zone_id:
                    continue

                # Skip if sync is disabled for this zone
                if self.is_sync_disabled(other_zone_id):
                    _LOGGER.debug(
                        "Skipping zone %s - sync disabled",
                        other_zone_id,
                    )
                    continue

                # Get climate entity ID for this zone
                other_climate_entity_id = zone_data.get("climate_entity_id")
                if not other_climate_entity_id:
                    _LOGGER.warning(
                        "No climate entity ID found for zone %s",
                        other_zone_id,
                    )
                    continue

                # Set the mode for this zone
                await self._set_zone_mode(
                    other_zone_id,
                    other_climate_entity_id,
                    new_mode.lower(),
                )
        finally:
            # Always clear flag after sync completes (or on error)
            self._sync_in_progress = False

    async def _set_zone_mode(
        self,
        zone_id: str,
        climate_entity_id: str,
        mode: str,
    ) -> None:
        """Set mode for a specific zone.

        Args:
            zone_id: Zone identifier
            climate_entity_id: Climate entity ID
            mode: Mode to set (heat, cool, off, etc.)
        """
        try:
            # Get current state
            state = self.hass.states.get(climate_entity_id)
            if state is None:
                _LOGGER.warning(
                    "Climate entity %s not found for zone %s",
                    climate_entity_id,
                    zone_id,
                )
                return

            current_mode = state.state

            # Only change if different
            if current_mode == mode:
                _LOGGER.debug(
                    "Zone %s already in %s mode",
                    zone_id,
                    mode,
                )
                return

            # Set the mode
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {
                    "entity_id": climate_entity_id,
                    "hvac_mode": mode,
                },
                blocking=True,
            )

            # Update stored mode
            self._zone_modes[zone_id] = mode

            _LOGGER.info(
                "Synced zone %s (%s) to %s mode",
                zone_id,
                climate_entity_id,
                mode,
            )
        except Exception as e:
            _LOGGER.error(
                "Error setting mode for zone %s: %s",
                zone_id,
                e,
            )

    def get_zone_mode(self, zone_id: str) -> str | None:
        """Get the current mode for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Current mode or None if not tracked
        """
        return self._zone_modes.get(zone_id)

    def is_sync_in_progress(self) -> bool:
        """Check if a sync operation is currently in progress.

        Returns:
            True if sync is in progress
        """
        return self._sync_in_progress


class ZoneLinker:
    """Zone linking for thermally connected zones.

    Features:
    - Configure linked zones per zone
    - Delay linked zone heating when primary heats
    - Track delay remaining time
    - Expire delay after configured minutes
    - Support bidirectional linking
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AdaptiveThermostatCoordinator,
    ) -> None:
        """Initialize zone linker.

        Args:
            hass: Home Assistant instance
            coordinator: AdaptiveThermostatCoordinator instance
        """
        self.hass = hass
        self.coordinator = coordinator
        # Map of zone_id -> list of linked zone_ids
        self._zone_links: dict[str, list[str]] = {}
        # Map of zone_id -> {start_time: datetime, delay_minutes: int}
        self._active_delays: dict[str, dict[str, Any]] = {}

        _LOGGER.debug("ZoneLinker initialized")

    def configure_linked_zones(self, zone_id: str, linked_zones: list[str]) -> None:
        """Configure which zones are thermally linked to a zone.

        Args:
            zone_id: Zone identifier
            linked_zones: List of zone IDs that are thermally linked to this zone
        """
        self._zone_links[zone_id] = linked_zones.copy()
        _LOGGER.debug("Configured linked zones for %s: %s", zone_id, linked_zones)

    def get_linked_zones(self, zone_id: str) -> list[str]:
        """Get the list of zones linked to a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            List of linked zone IDs
        """
        return self._zone_links.get(zone_id, []).copy()

    async def on_zone_heating_started(
        self,
        zone_id: str,
        delay_minutes: int = 30,
    ) -> None:
        """Handle when a zone starts heating - delay linked zones.

        When a zone starts heating, thermally connected zones may receive
        heat transfer and should delay their own heating.

        Args:
            zone_id: Zone that started heating
            delay_minutes: How long to delay linked zones (default 30 minutes)
        """
        linked_zones = self.get_linked_zones(zone_id)
        if not linked_zones:
            _LOGGER.debug("Zone %s has no linked zones", zone_id)
            return

        _LOGGER.info(
            "Zone %s started heating - delaying linked zones %s for %d minutes",
            zone_id,
            linked_zones,
            delay_minutes,
        )

        start_time = datetime.now()

        # Apply delay to all linked zones
        for linked_zone_id in linked_zones:
            self._active_delays[linked_zone_id] = {
                "start_time": start_time,
                "delay_minutes": delay_minutes,
                "source_zone": zone_id,
            }
            _LOGGER.debug(
                "Applied %d minute delay to zone %s (heat from %s)",
                delay_minutes,
                linked_zone_id,
                zone_id,
            )

    def is_zone_delayed(self, zone_id: str) -> bool:
        """Check if a zone is currently delayed due to linked zone heating.

        Args:
            zone_id: Zone identifier to check

        Returns:
            True if zone heating should be delayed
        """
        if zone_id not in self._active_delays:
            return False

        delay_info = self._active_delays[zone_id]
        start_time = delay_info["start_time"]
        delay_minutes = delay_info["delay_minutes"]

        # Calculate elapsed time
        elapsed = datetime.now() - start_time
        elapsed_minutes = elapsed.total_seconds() / 60

        if elapsed_minutes >= delay_minutes:
            # Delay has expired
            _LOGGER.debug(
                "Delay for zone %s has expired (%.1f >= %d minutes)",
                zone_id,
                elapsed_minutes,
                delay_minutes,
            )
            del self._active_delays[zone_id]
            return False

        _LOGGER.debug(
            "Zone %s is delayed: %.1f / %d minutes remaining",
            zone_id,
            delay_minutes - elapsed_minutes,
            delay_minutes,
        )
        return True

    def get_delay_remaining_minutes(self, zone_id: str) -> float | None:
        """Get the remaining delay time for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Remaining delay in minutes, or None if no delay active
        """
        if zone_id not in self._active_delays:
            return None

        delay_info = self._active_delays[zone_id]
        start_time = delay_info["start_time"]
        delay_minutes = delay_info["delay_minutes"]

        # Calculate remaining time
        elapsed = datetime.now() - start_time
        elapsed_minutes = elapsed.total_seconds() / 60
        remaining = delay_minutes - elapsed_minutes

        if remaining <= 0:
            # Delay expired
            del self._active_delays[zone_id]
            return None

        return remaining

    def clear_delay(self, zone_id: str) -> None:
        """Clear any active delay for a zone.

        Args:
            zone_id: Zone identifier
        """
        if zone_id in self._active_delays:
            _LOGGER.debug("Clearing delay for zone %s", zone_id)
            del self._active_delays[zone_id]

    def get_active_delays(self) -> dict[str, dict[str, Any]]:
        """Get all active delays.

        Returns:
            Dictionary of zone_id -> delay_info
        """
        return self._active_delays.copy()
