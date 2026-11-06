"""Home Assistant lifecycle boundary for pump-aware PWM groups."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any, TYPE_CHECKING

from homeassistant.components.climate import HVACMode
from homeassistant.core import callback, split_entity_id
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .heater_actuation import ActuationResult
from .pwm_load_balancing_manager import PWMLoadBalancingManager
from .pwm_pulse import Pulse, prepare_pulse

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat
    from ..adaptive.persistence import LearningDataStore

_LOGGER = logging.getLogger(__name__)


class HAZoneAdapter:
    """Translate a live, restored entity into the narrow scheduler protocol."""

    def __init__(self, entity: AdaptiveThermostat) -> None:
        self.entity = entity
        self.entity_id = entity.entity_id
        self.period = float(entity._pwm)
        self.outputs = tuple(entity._heater_controller.get_entities(HVACMode.HEAT))
        self.blocked = False
        self.restart_force_off = False

    def continuous_demand(self) -> bool:
        return self.output() >= self.entity._heater_controller._difference

    def ready(self) -> bool:
        entity = self.entity
        return entity.hvac_mode == HVACMode.OFF or bool(
            entity._active
            and entity._current_temp is not None
            and entity._target_temp is not None
            and entity._control_output is not None
        )

    def checkpoint_state(self, now: float, wall: float) -> dict[str, Any]:
        from .pwm_restart import controller_state

        return controller_state(self, now, wall)

    def eligible(self) -> bool:
        entity = self.entity
        return bool(
            entity._active
            and not self.blocked
            and not entity._force_off
            and entity.hvac_mode == HVACMode.HEAT
            and entity._current_temp is not None
            and entity._target_temp is not None
            and not entity._status_manager.is_paused("heat")
            and self.output() > 0
        )

    def available(self) -> bool:
        return self.entity.entity_id == self.entity_id and all(
            (state := self.entity.hass.states.get(output)) is not None and state.state in ("on", "off")
            for output in self.outputs
        )

    def active(self) -> bool:
        return self.entity._heater_controller.is_active(HVACMode.HEAT)

    def active_elapsed(self, now: float) -> float:
        return max(0.0, now - self.entity._get_cycle_start_time()) if self.active() else 0.0

    def output(self) -> float:
        stall = getattr(self.entity, "_sensor_stall", 0)
        updated = getattr(self.entity, "_last_sensor_update", 0)
        if stall and time.monotonic() - updated > stall:
            return float(self.entity._output_safety or 0.0)
        return float(self.entity._control_output or 0.0)

    def signature(self) -> tuple[object, ...]:
        manager = getattr(self.entity, "_control_output_manager", None)
        target = getattr(manager, "_last_effective_target", None)
        return (self.entity._target_temp if target is None else target, self.entity.hvac_mode)

    def prepare(self, now: float) -> Pulse:
        entity = self.entity
        heater = entity._heater_controller
        # Preserve legacy low-duty protection against firing above target.
        if (
            heater._pwm_controller._calculate_heat_duration(self.output(), heater._difference) < heater.min_open_time
            and entity._current_temp >= entity._target_temp
        ):
            heater.reset_duty_accumulator()
            return Pulse(0.0)
        return prepare_pulse(heater, self.output(), now)

    def minimum_remaining(self, on: bool, now: float) -> float:
        if not on and self.restart_force_off:
            return 0.0
        heater = self.entity._heater_controller
        minimum = heater._min_closed_time if on else heater.effective_min_open_time
        return max(0.0, minimum - (now - self.entity._get_cycle_start_time()))

    def minimum_on_seconds(self) -> float:
        return self.entity._heater_controller.effective_min_open_time

    def invalidate(self) -> None:
        self.entity._heater_controller._actuation.invalidate()

    def commit_credit(self, seconds: float) -> None:
        if seconds:
            heater = self.entity._heater_controller
            heater.set_duty_accumulator(max(0.0, heater.duty_accumulator_seconds - seconds))
            heater._last_accumulator_calc_time = None

    async def command(self, on: bool) -> ActuationResult:
        entity = self.entity
        heater = entity._heater_controller
        # Eligibility is re-read at execution, not inherited from a plan.
        if on and (not self.eligible() or not self.available()):
            return ActuationResult.UNAVAILABLE
        method = heater.async_turn_on if on else heater.async_turn_off
        extra = {"force": True} if not on and self.restart_force_off else {}
        result = await method(
            hvac_mode=HVACMode.HEAT,
            get_cycle_start_time=entity._get_cycle_start_time,
            set_is_heating=entity._set_is_heating,
            set_last_heat_cycle_time=entity._set_last_heat_cycle_time,
            **extra,
        )
        if not on and result in (ActuationResult.CHANGED, ActuationResult.UNCHANGED):
            self.restart_force_off = False
        if result == ActuationResult.CHANGED:
            entity._set_time_changed(time.monotonic())
        entity.async_write_ha_state()
        return result if isinstance(result, ActuationResult) else ActuationResult.FAILED

    def release(self) -> None:
        # Keep actual elapsed state for the legacy PWM time basis. No relay edge
        # is generated merely because output ownership changes.
        self.entity._time_changed = self.entity._get_cycle_start_time()
        self.entity.hass.async_create_task(self.entity._async_control_heating(calc_pid=False))


class PWMCoordinationRegistry:
    """Own groups and HA listeners, bound only after real entity readiness."""

    def __init__(self, hass: Any, config: dict[str, Any]) -> None:
        self.hass = hass
        self.groups: list[PWMLoadBalancingManager] = []
        self._store: LearningDataStore | None = None
        self._journals: dict[str, Any] = {}
        self._by_entity: dict[str, PWMLoadBalancingManager] = {}
        self._adapters: dict[str, HAZoneAdapter] = {}
        self._listeners: dict[str, Any] = {}
        for group in config["groups"]:
            manager = PWMLoadBalancingManager(
                group["name"],
                {z["entity"]: z["hydraulic_weight"] for z in group["zones"]},
                group["handover_overlap_seconds"],
                group["max_deferral_seconds"],
                group["overlap_strategy"],
                self._schedule_later,
                self._create_task,
                now=time.monotonic,
            )
            self.groups.append(manager)
            for zone in manager.members:
                self._by_entity[zone] = manager

    async def async_initialize(self, store: LearningDataStore) -> None:
        """Restore the group journals before zone entities bind."""
        from .pwm_restart import restore

        if self._store is not None:
            return

        self._store = store
        data = store.load_pwm_coordination()
        if not isinstance(data, dict):
            for group in self.groups:
                group.fail("restart_state_invalid", release=False)
            return
        self._journals = data
        for group in self.groups:

            async def save(manager: PWMLoadBalancingManager = group) -> None:
                from .pwm_restart import snapshot

                if manager.mode != "active":
                    return
                state = snapshot(manager, time.monotonic(), dt_util.utcnow().timestamp())
                previous = self._journals.get(manager.name, {})
                for entity_id, row in previous.get("zones", {}).items():
                    if entity_id not in state["zones"]:
                        state["zones"][entity_id] = row
                await store.async_save_pwm_coordination(manager.name, state)
                self._journals[manager.name] = state

            def ready(manager: PWMLoadBalancingManager = group) -> None:
                saved = self._journals.get(manager.name)
                if saved is not None:
                    try:
                        from .pwm_restart import number

                        period = number(saved["period"])
                        if period <= 0 or any(z.adapter.period != period for z in manager._zones.values()):
                            raise ValueError("PWM restart period changed")
                        manager._period = period
                        restore(manager, saved, time.monotonic(), dt_util.utcnow().timestamp())
                    except (KeyError, TypeError, ValueError, OverflowError):
                        _LOGGER.exception("Invalid PWM restart state for %s", manager.name)
                        manager.fail("restart_state_invalid", release=False)

            async def stop(manager: PWMLoadBalancingManager = group) -> None:
                await self._stop_failed_group(manager)

            group.on_checkpoint_failure = stop
            group.checkpoint = save
            group.on_register = ready

    async def _stop_failed_group(self, group: PWMLoadBalancingManager) -> None:
        for entity_id in group.members:
            adapter = self._adapters.get(entity_id)
            if adapter is not None and adapter.active():
                adapter.restart_force_off = True
                await adapter.command(False)

    async def async_save(self) -> None:
        """Flush observed balances before orderly shutdown/unload."""
        for group in self.groups:
            if group.mode == "active":
                async with group._lock:
                    await group.save_checkpoint()

    def _schedule_later(self, delay: float, action: Callable[[], None]) -> Callable[[], None]:
        """Keep timer work and scheduler state on the HA event loop."""

        @callback
        def wake(_: Any) -> None:
            action()

        return async_call_later(self.hass, delay, wake)

    def _create_task(self, coroutine: object) -> None:
        if isinstance(coroutine, Coroutine):
            self.hass.async_create_task(coroutine)

    def bind(self, entity: AdaptiveThermostat) -> None:
        group = self._by_entity.get(entity.entity_id)
        adapter = HAZoneAdapter(entity)
        self._adapters[entity.entity_id] = adapter
        if group is None:
            # A configured and an unconfigured zone must not share a relay.
            self._check_outputs()
            return
        if (
            adapter.period <= 0
            or len(adapter.outputs) != 1
            or entity._cooler_entity_id
            or entity._ac_mode
            or entity._heater_polarity_invert
            or any(split_entity_id(output)[0] not in {"switch", "input_boolean"} for output in adapter.outputs)
        ):
            group.fail(
                "unsupported zone: HEAT-only PWM with one non-inverted switch or input_boolean per zone required"
            )
            return
        heater = entity._heater_controller
        if heater.effective_min_open_time + heater._min_closed_time > adapter.period:
            group.fail("effective minimum ON plus OFF exceeds PWM period")
            return
        saved = self._journals.get(group.name)
        if saved is not None:
            from .pwm_restart import restore_controller

            try:
                restore_controller(
                    adapter, saved["zones"][entity.entity_id], time.monotonic(), dt_util.utcnow().timestamp()
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                _LOGGER.exception("Invalid PWM controller restart state for %s", entity.entity_id)
                group.fail("restart_state_invalid", release=False)
                return
        if adapter.active() and saved is None and not entity._get_cycle_start_time():
            group.fail("startup output ON with unknown elapsed time; restart group with outputs OFF")
            return
        entity._heater_controller.enable_safe_actuation()
        group.register(adapter)
        self._check_outputs()

        @callback
        def state_changed(event: Any) -> None:
            data = event.data
            output = data.get("entity_id")
            state = data.get("new_state")
            if state is not None and state.state in ("on", "off"):
                entity._heater_controller._actuation.observe(output, state.state == "on")
                old = data.get("old_state")
                if old is not None and old.state != state.state:
                    entity._set_last_heat_cycle_time(time.monotonic())
            group.observe(entity.entity_id)
            self._create_task(group.update(entity.entity_id, "output_state"))

        self._listeners[entity.entity_id] = async_track_state_change_event(self.hass, adapter.outputs, state_changed)
        if group.mode == "waiting_for_members":
            _LOGGER.warning(
                "PWM group %s waits for all configured entity IDs; independent PWM remains active", group.name
            )

    def _check_outputs(self) -> None:
        owners: dict[str, str] = {}
        for entity, adapter in self._adapters.items():
            for output in adapter.outputs:
                previous = owners.get(output)
                if previous is not None:
                    for member in (previous, entity):
                        group = self._by_entity.get(member)
                        if group is not None:
                            group.fail("shared heater output")
                owners[output] = entity

    def invalidate(self, entity_id: str, urgent: bool = False) -> None:
        adapter = self._adapters.get(entity_id)
        if adapter is not None:
            adapter.blocked = True
        group = self._by_entity.get(entity_id)
        if group is not None:
            group.invalidate(entity_id, urgent)

    async def apply(self, entity_id: str) -> bool:
        for bound_id, candidate in self._adapters.items():
            if candidate.entity.entity_id != bound_id:
                affected = self._by_entity.get(bound_id)
                if affected is not None and affected.mode != "fallback":
                    affected.fail("entity ID changed; reload coordination configuration")
        group = self._by_entity.get(entity_id)
        owned_at_entry = group is not None and group.mode == "active"
        adapter = self._adapters.get(entity_id)
        if group is not None and adapter is not None:
            if adapter.entity.hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
                group.fail("cooling or mixed mode")
                return False
            adapter.blocked = False
        if group is None:
            return False
        if group.reason in ("restart_state_invalid", "restart_storage_error", "restart_reconciliation_failed"):
            # Do not bypass a failed durable reservation through the legacy path.
            await self._stop_failed_group(group)
            return True
        result = await group.update(entity_id)
        # A failed invocation is still consumed here. release() schedules the
        # next legacy transaction; never retry ON through two paths in one call.
        return (
            result
            or owned_at_entry
            or group.reason in ("restart_state_invalid", "restart_storage_error", "restart_reconciliation_failed")
        )

    def owns(self, entity_id: str) -> bool:
        group = self._by_entity.get(entity_id)
        return group is not None and group.mode == "active"

    async def refresh(self, entity_id: str) -> None:
        group = self._by_entity.get(entity_id)
        if group is not None:
            await group.update(entity_id, "safety_or_mode")

    def remove(self, entity_id: str) -> None:
        listener = self._listeners.pop(entity_id, None)
        if listener is not None:
            listener()
        group = self._by_entity.get(entity_id)
        if group is not None:
            group.remove(entity_id)
        self._adapters.pop(entity_id, None)

    def close(self) -> None:
        for listener in self._listeners.values():
            listener()
        self._listeners.clear()
        for group in self.groups:
            group.fail("shutdown", release=False)

    def diagnostics(self, entity_id: str) -> dict[str, object] | None:
        group = self._by_entity.get(entity_id)
        return group.diagnostics(entity_id) if group is not None else None
