"""Serialized binary actuation and explicit outcomes for HeaterController."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback

if TYPE_CHECKING:
    from .heater_controller import HeaterController


class ActuationResult(StrEnum):
    """A service result is command acceptance, not proof of physical heat."""

    CHANGED = "changed"
    UNCHANGED = "unchanged"
    MINIMUM_TIME = "minimum_time"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class HeaterActuationController:
    """One service writer, including safety requests arriving during awaited I/O."""

    def __init__(self, heater: HeaterController) -> None:
        self._heater = heater
        self._lock = asyncio.Lock()
        self._generation = 0
        self._commanded: dict[str, bool] = {}
        self._waiters: list[asyncio.Event] = []
        self._observed_at: dict[str, float] = {}

    def observe(self, entity: str, active: bool) -> None:
        """Reconcile external output changes instead of trusting a cached command."""
        if self._commanded.get(entity) != active:
            self._observed_at[entity] = time.monotonic()
        self._commanded[entity] = active

    def invalidate(self) -> None:
        """Synchronously revoke queued ON operations before awaiting a lock."""
        self._generation += 1
        for waiter in self._waiters:
            waiter.set()

    def _listen(self, entity: str, callback):
        """HA subscription boundary, kept injectable for deterministic tests."""
        from homeassistant.helpers.event import async_track_state_change_event

        return async_track_state_change_event(self._heater._hass, [entity], callback)

    async def _confirm(self, entity: str, electrical_on: bool) -> bool:
        """Await binary HA state with timeout; this is not valve/flow feedback."""
        expected = "on" if electrical_on else "off"

        def matches() -> bool:
            state = self._heater._hass.states.get(entity)
            return state is not None and state.state == expected

        if matches():
            return True
        event = asyncio.Event()
        self._waiters.append(event)

        @callback
        def state_changed(_: Any) -> None:
            event.set()

        cancel = self._listen(entity, state_changed)
        generation = self._generation
        try:
            async with asyncio.timeout(10):
                while not matches():
                    await event.wait()
                    event.clear()
                    if generation != self._generation:
                        return False
            return True
        except TimeoutError:
            return False
        finally:
            self._waiters.remove(event)
            cancel()

    async def _write(self, entities: list[str], on: bool) -> bool:
        heater = self._heater
        electrical_on = on != heater._heater_polarity_invert
        service = "turn_on" if electrical_on else "turn_off"
        successful = True
        failure: str | None = None
        generation = self._generation
        for entity in entities:
            result = await heater._async_call_heater_service(entity, "homeassistant", service, {"entity_id": entity})
            if result and (not on or generation == self._generation):
                result = await self._confirm(entity, electrical_on)
                if not result:
                    heater._service_caller._heater_control_failed = True
                    heater._service_caller._last_heater_error = "binary output state not confirmed"
            if result:
                self._commanded[entity] = on
            else:
                successful = False
                failure = heater.last_heater_error
        if not successful:
            heater._service_caller._heater_control_failed = True
            heater._service_caller._last_heater_error = failure
        return successful

    async def async_switch(
        self,
        on: bool,
        hvac_mode: Any,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        force: bool = False,
    ) -> ActuationResult:
        """Commit bookkeeping only after the complete command succeeds.

        OFF invalidation occurs before lock acquisition. A stale in-flight ON
        is reconciled OFF under the same lock before allowing another writer.
        """
        if not on:
            self.invalidate()
        generation = self._generation
        heater = self._heater
        entities = heater.get_entities(hvac_mode)
        if force:
            heater.abort_active_cycle()
            heater._bookkeeper.has_demand = False
        async with self._lock:
            if on and generation != self._generation:
                return ActuationResult.SUPERSEDED
            active = heater.is_active(hvac_mode) or any(self._commanded.get(e, False) for e in entities)
            if active == on:
                if on and not heater._bookkeeper.cycle_active and heater.has_demand:
                    heater._bookkeeper.cycle_active = True
                    heater._bookkeeper.reset_pid_clamp_state()
                    heater._bookkeeper.emit_cycle_started(hvac_mode)
                return ActuationResult.UNCHANGED
            minimum = heater._min_closed_time if on else heater.effective_min_open_time
            if not force and time.monotonic() - get_cycle_start_time() < minimum:
                return ActuationResult.MINIMUM_TIME
            if on:
                for entity in entities:
                    state = heater._hass.states.get(entity)
                    if state is None or state.state in ("unknown", "unavailable"):
                        return ActuationResult.UNAVAILABLE
            command_at = time.monotonic()
            if not await self._write(entities, on):
                # A partially successful ON must not leave untracked outputs ON.
                if on:
                    failure = heater.last_heater_error
                    await self._write(entities, False)
                    heater._service_caller._heater_control_failed = True
                    heater._service_caller._last_heater_error = failure
                return ActuationResult.FAILED
            if on and generation != self._generation:
                if not await self._write(entities, False):
                    return ActuationResult.FAILED
                return ActuationResult.SUPERSEDED
            confirmed_at = time.monotonic()
            now = max((self._observed_at.get(entity, confirmed_at) for entity in entities), default=confirmed_at)
            if not command_at <= now <= confirmed_at:
                now = confirmed_at
            set_last_heat_cycle_time(now)
            set_is_heating(on)
            bookkeeper = heater._bookkeeper
            pipeline = heater._heat_pipeline
            if on:
                if str(hvac_mode) == "cool":
                    bookkeeper.last_cooler_state = True
                else:
                    bookkeeper.last_heater_state = True
                if pipeline is not None:
                    pipeline.valve_opened(now)
                if not bookkeeper.cycle_active and bookkeeper.has_demand:
                    bookkeeper.cycle_active = True
                    bookkeeper.reset_pid_clamp_state()
                    bookkeeper.emit_cycle_started(hvac_mode, at=now)
                if heater._dispatcher:
                    if heater._pwm and heater._valve_actuation_time > 0:
                        heater._timers.schedule_heating_started(hvac_mode, opened_at=now)
                    else:
                        bookkeeper.emit_heating_started(hvac_mode, at=now)
            else:
                bookkeeper.increment_cycle_count(hvac_mode, is_now_off=True)
                heater._timers.cancel_valve_open()
                if pipeline is not None:
                    heater._last_committed_heat_snapshot = pipeline.committed_heat_remaining(now)
                    pipeline.valve_closed(now)
                if heater._dispatcher:
                    if heater._pwm and heater._valve_actuation_time > 0:
                        heater._timers.schedule_heating_ended(
                            hvac_mode, heater._last_committed_heat_snapshot, closed_at=now
                        )
                    else:
                        bookkeeper.emit_heating_ended(hvac_mode, heater._last_committed_heat_snapshot, at=now)
            return ActuationResult.CHANGED
