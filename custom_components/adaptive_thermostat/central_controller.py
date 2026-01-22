"""Central Controller for managing main heater/cooler based on zone demand."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound

if TYPE_CHECKING:
    try:
        from .coordinator import AdaptiveThermostatCoordinator
    except ImportError:
        from coordinator import AdaptiveThermostatCoordinator

_LOGGER = logging.getLogger(__name__)

# Error handling constants for CentralController
MAX_SERVICE_CALL_RETRIES = 3
BASE_RETRY_DELAY_SECONDS = 1.0
CONSECUTIVE_FAILURE_WARNING_THRESHOLD = 3
# Debounce delay before turning off switches (prevents flickering during restarts)
TURN_OFF_DEBOUNCE_SECONDS = 10


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
        coordinator: "AdaptiveThermostatCoordinator",
        main_heater_switch: list[str] | None = None,
        main_cooler_switch: list[str] | None = None,
        startup_delay_seconds: int = 0,
    ) -> None:
        """Initialize the central controller.

        Args:
            hass: Home Assistant instance
            coordinator: AdaptiveThermostatCoordinator instance
            main_heater_switch: List of entity IDs for main heater switches
                (e.g., ["switch.boiler"] or ["switch.boiler", "switch.pump"])
            main_cooler_switch: List of entity IDs for main cooler switches
                (e.g., ["switch.chiller"] or ["switch.chiller", "switch.fan"])
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

        # Track turn-off debounce state
        self._heater_turnoff_task: asyncio.Task | None = None
        self._cooler_turnoff_task: asyncio.Task | None = None

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

    def _get_shared_switches(self) -> set[str]:
        """Get switches that are in both heater and cooler lists.

        Returns:
            Set of entity IDs that are shared between modes
        """
        if not self.main_heater_switch or not self.main_cooler_switch:
            return set()
        return set(self.main_heater_switch) & set(self.main_cooler_switch)

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
                # Cancel any pending turn-off (demand came back)
                self._cancel_heater_turnoff_unlocked()
                # Zone(s) need heating - start if not already waiting and not all switches are on
                if not self._heater_waiting_for_startup and not await self._are_all_switches_on(self.main_heater_switch):
                    # Start heater with delay (some switches are off)
                    await self._start_heater_with_delay_unlocked()
            else:
                # No demand - cancel startup and schedule debounced turn-off
                await self._cancel_heater_startup_unlocked()
                self._schedule_heater_turnoff_unlocked()

    async def _update_cooler(self, has_demand: bool) -> None:
        """Update cooler state based on demand.

        Args:
            has_demand: Whether any zone has cooling demand
        """
        async with self._startup_lock:
            if has_demand:
                # Cancel any pending turn-off (demand came back)
                self._cancel_cooler_turnoff_unlocked()
                # Zone(s) need cooling
                if not self._cooler_waiting_for_startup and not await self._are_all_switches_on(self.main_cooler_switch):
                    # Start cooler with delay (some switches are off)
                    await self._start_cooler_with_delay_unlocked()
            else:
                # No demand - cancel startup and schedule debounced turn-off
                await self._cancel_cooler_startup_unlocked()
                self._schedule_cooler_turnoff_unlocked()

    async def _start_heater_with_delay_unlocked(self) -> None:
        """Start heater after startup delay.

        Note: Must be called while holding _startup_lock.
        """
        # Cancel any existing startup task
        await self._cancel_heater_startup_unlocked()

        if self.startup_delay_seconds == 0:
            # No delay - turn on immediately
            await self._turn_on_switches(self.main_heater_switch)
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
            await self._turn_on_switches(self.main_cooler_switch)
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
                    await self._turn_on_switches(self.main_heater_switch)
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
                    await self._turn_on_switches(self.main_cooler_switch)
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

    def _schedule_heater_turnoff_unlocked(self) -> None:
        """Schedule a debounced heater turn-off.

        Note: Must be called while holding _startup_lock.
        """
        # Don't schedule if already scheduled
        if self._heater_turnoff_task and not self._heater_turnoff_task.done():
            return

        _LOGGER.debug(
            "Scheduling heater turn-off in %d seconds", TURN_OFF_DEBOUNCE_SECONDS
        )
        self._heater_turnoff_task = asyncio.create_task(self._delayed_heater_turnoff())

    def _schedule_cooler_turnoff_unlocked(self) -> None:
        """Schedule a debounced cooler turn-off.

        Note: Must be called while holding _startup_lock.
        """
        # Don't schedule if already scheduled
        if self._cooler_turnoff_task and not self._cooler_turnoff_task.done():
            return

        _LOGGER.debug(
            "Scheduling cooler turn-off in %d seconds", TURN_OFF_DEBOUNCE_SECONDS
        )
        self._cooler_turnoff_task = asyncio.create_task(self._delayed_cooler_turnoff())

    def _cancel_heater_turnoff_unlocked(self) -> None:
        """Cancel pending heater turn-off.

        Note: Must be called while holding _startup_lock.
        """
        if self._heater_turnoff_task and not self._heater_turnoff_task.done():
            self._heater_turnoff_task.cancel()
            _LOGGER.debug("Cancelled pending heater turn-off (demand returned)")
        self._heater_turnoff_task = None

    def _cancel_cooler_turnoff_unlocked(self) -> None:
        """Cancel pending cooler turn-off.

        Note: Must be called while holding _startup_lock.
        """
        if self._cooler_turnoff_task and not self._cooler_turnoff_task.done():
            self._cooler_turnoff_task.cancel()
            _LOGGER.debug("Cancelled pending cooler turn-off (demand returned)")
        self._cooler_turnoff_task = None

    async def _delayed_heater_turnoff(self) -> None:
        """Delayed heater turn-off task."""
        try:
            await asyncio.sleep(TURN_OFF_DEBOUNCE_SECONDS)

            # Check if we still have no demand (under lock to prevent races)
            async with self._startup_lock:
                demand = self.coordinator.get_aggregate_demand()
                if not demand["heating"]:
                    # Turn off heater switches, but skip shared ones if cooling active
                    await self._turn_off_switches_smart(
                        self.main_heater_switch,
                        other_mode_has_demand=demand["cooling"],
                    )
                    _LOGGER.info(
                        "Heater turned off after %d second debounce",
                        TURN_OFF_DEBOUNCE_SECONDS,
                    )
                else:
                    _LOGGER.debug("Heater turn-off cancelled - demand returned")
        except asyncio.CancelledError:
            _LOGGER.debug("Heater turn-off cancelled")
        finally:
            self._heater_turnoff_task = None

    async def _delayed_cooler_turnoff(self) -> None:
        """Delayed cooler turn-off task."""
        try:
            await asyncio.sleep(TURN_OFF_DEBOUNCE_SECONDS)

            # Check if we still have no demand (under lock to prevent races)
            async with self._startup_lock:
                demand = self.coordinator.get_aggregate_demand()
                if not demand["cooling"]:
                    # Turn off cooler switches, but skip shared ones if heating active
                    await self._turn_off_switches_smart(
                        self.main_cooler_switch,
                        other_mode_has_demand=demand["heating"],
                    )
                    _LOGGER.info(
                        "Cooler turned off after %d second debounce",
                        TURN_OFF_DEBOUNCE_SECONDS,
                    )
                else:
                    _LOGGER.debug("Cooler turn-off cancelled - demand returned")
        except asyncio.CancelledError:
            _LOGGER.debug("Cooler turn-off cancelled")
        finally:
            self._cooler_turnoff_task = None

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

    async def _is_any_switch_on(self, entity_ids: list[str]) -> bool:
        """Check if any switch in the list is currently on.

        Args:
            entity_ids: List of entity IDs to check

        Returns:
            True if any switch is on, False if all are off
        """
        for entity_id in entity_ids:
            if await self._is_switch_on(entity_id):
                return True
        return False

    async def _are_all_switches_on(self, entity_ids: list[str]) -> bool:
        """Check if all switches in the list are currently on.

        Args:
            entity_ids: List of entity IDs to check

        Returns:
            True if all switches are on, False if any is off
        """
        for entity_id in entity_ids:
            if not await self._is_switch_on(entity_id):
                return False
        return True

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

    async def _turn_on_switches(self, entity_ids: list[str]) -> bool:
        """Turn on all switches in the list.

        Args:
            entity_ids: List of entity IDs to turn on

        Returns:
            True if all switches were turned on successfully, False if any failed
        """
        success = True
        for entity_id in entity_ids:
            if not await self._turn_on_switch(entity_id):
                success = False
        return success

    async def _turn_off_switches(self, entity_ids: list[str]) -> bool:
        """Turn off all switches in the list.

        Args:
            entity_ids: List of entity IDs to turn off

        Returns:
            True if all switches were turned off successfully, False if any failed
        """
        success = True
        for entity_id in entity_ids:
            if not await self._turn_off_switch(entity_id):
                success = False
        return success

    async def _turn_off_switches_smart(
        self,
        entity_ids: list[str],
        other_mode_has_demand: bool,
    ) -> bool:
        """Turn off switches, skipping shared switches if other mode needs them.

        Args:
            entity_ids: List of switches to turn off
            other_mode_has_demand: Whether the other mode (heat/cool) has demand

        Returns:
            True if all operations succeeded
        """
        shared = self._get_shared_switches()
        success = True

        for entity_id in entity_ids:
            # Skip shared switches if other mode has demand
            if entity_id in shared and other_mode_has_demand:
                _LOGGER.debug(
                    "Skipping turn-off of shared switch %s (other mode has demand)",
                    entity_id,
                )
                continue

            # Turn off this switch (already has state check inside _turn_off_switch)
            if not await self._turn_off_switch(entity_id):
                success = False

        return success

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

    async def async_cleanup(self) -> None:
        """Cancel all pending tasks on unload.

        This method should be called when the integration is being unloaded
        to prevent memory leaks from orphaned asyncio tasks.
        """
        tasks_to_cancel = []
        async with self._startup_lock:
            for task in [
                self._heater_startup_task,
                self._cooler_startup_task,
                self._heater_turnoff_task,
                self._cooler_turnoff_task,
            ]:
                if task and not task.done():
                    task.cancel()
                    tasks_to_cancel.append(task)

        for task in tasks_to_cancel:
            try:
                await task
            except asyncio.CancelledError:
                pass

        _LOGGER.debug(
            "CentralController cleanup: cancelled %d tasks", len(tasks_to_cancel)
        )
