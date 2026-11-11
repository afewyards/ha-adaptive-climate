"""Heater controller manager for Adaptive Climate integration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant, split_entity_id
    from homeassistant.core import DOMAIN as HA_DOMAIN
    from homeassistant.const import (
        ATTR_ENTITY_ID,
        SERVICE_TURN_OFF,
        SERVICE_TURN_ON,
        STATE_ON,
        STATE_OFF,
    )
    from homeassistant.components.number.const import (
        ATTR_VALUE,
        SERVICE_SET_VALUE,
        DOMAIN as NUMBER_DOMAIN,
    )
    from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
    from homeassistant.components.light import (
        DOMAIN as LIGHT_DOMAIN,
        SERVICE_TURN_ON as SERVICE_TURN_LIGHT_ON,
        ATTR_BRIGHTNESS_PCT,
    )
    from homeassistant.components.valve import (
        DOMAIN as VALVE_DOMAIN,
        SERVICE_SET_VALVE_POSITION,
        ATTR_POSITION,
    )
    from homeassistant.components.climate import HVACMode

    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any
    HVACMode = Any
    HA_DOMAIN = "homeassistant"
    ATTR_ENTITY_ID = "entity_id"
    SERVICE_TURN_OFF = "turn_off"
    SERVICE_TURN_ON = "turn_on"
    STATE_ON = "on"
    STATE_OFF = "off"
    ATTR_VALUE = "value"
    SERVICE_SET_VALUE = "set_value"
    NUMBER_DOMAIN = "number"
    INPUT_NUMBER_DOMAIN = "input_number"
    LIGHT_DOMAIN = "light"
    SERVICE_TURN_LIGHT_ON = "turn_on"
    ATTR_BRIGHTNESS_PCT = "brightness_pct"
    VALVE_DOMAIN = "valve"
    SERVICE_SET_VALVE_POSITION = "set_valve_position"
    ATTR_POSITION = "position"

    def split_entity_id(entity_id: str) -> tuple[str, str]:  # type: ignore[misc]
        parts = entity_id.split(".", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else (entity_id, "")


from ..const import COOLING_TYPE_CHARACTERISTICS, MIN_OUTPUT_THRESHOLD, get_convergence_thresholds
from .events import CycleEventDispatcher
from .heater_cycle_bookkeeper import HeaterCycleBookkeeper
from .heater_service_caller import HeaterServiceCaller
from .heater_timers import HeaterTimerManager
from .pwm_controller import PWMController
from .heat_pipeline import HeatPipeline
from .heater_actuation import ActuationResult, HeaterActuationController

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)

COMMAND_VERIFY_DELAY = 20.0
COMMAND_MAX_RETRIES = 2


class HeaterController:
    """Controller for heater/cooler device operations.

    Manages the state and control of heater/cooler entities for the thermostat.
    This includes turning devices on/off, setting valve values, and PWM control.

    Delegates to three helpers:
    - HeaterServiceCaller  – HA service invocation with error handling
    - HeaterCycleBookkeeper – cycle state tracking and event emission
    - HeaterTimerManager   – valve-actuation and settling-debounce timers
    """

    def __init__(
        self,
        hass: HomeAssistant,
        thermostat: AdaptiveThermostat,
        heater_entity_id: list[str] | None,
        cooler_entity_id: list[str] | None,
        demand_switch_entity_id: list[str] | None,
        heater_polarity_invert: bool,
        pwm: int,  # PWM duration in seconds
        difference: float,  # output_max - output_min
        min_open_time: float,  # in seconds
        min_closed_time: float,  # in seconds
        dispatcher: CycleEventDispatcher | None = None,
        cooling_type: str | None = None,
        get_was_clamped: Callable[[], bool] | None = None,
        reset_clamp_state: Callable[[], None] | None = None,
        valve_actuation_time: float = 0.0,
        heating_type: str | None = None,
    ):
        """Initialise the HeaterController."""
        # Derive min_open_time from cooling characteristics when none is explicitly configured.
        # Compressor-based cooling systems (forced_air, mini_split) require a minimum
        # on-time to protect the compressor from rapid short-cycling.
        if min_open_time == 0 and cooling_type is not None:
            cooling_chars = COOLING_TYPE_CHARACTERISTICS.get(cooling_type, {})
            derived = cooling_chars.get("min_cycle", 0)
            if derived > 0:
                min_open_time = float(derived)

        self._hass = hass
        self._thermostat = thermostat
        self._heater_entity_id = heater_entity_id
        self._cooler_entity_id = cooler_entity_id
        self._demand_switch_entity_id = demand_switch_entity_id
        self._heater_polarity_invert = heater_polarity_invert
        self._pwm = pwm
        self._difference = difference
        self._min_open_time = min_open_time
        self._min_closed_time = min_closed_time
        self._dispatcher = dispatcher
        self._cooling_type = cooling_type
        self._valve_actuation_time = valve_actuation_time
        self._heating_type = heating_type
        self._transport_delay: float = 0.0  # Updated dynamically via set_transport_delay

        # Committed heat snapshot taken at each valve-close (for overshoot split)
        self._last_committed_heat_snapshot: float = 0.0

        # ── Helper instances ───────────────────────────────────────────────────
        self._service_caller = HeaterServiceCaller(hass, thermostat)
        self._bookkeeper = HeaterCycleBookkeeper(
            thermostat=thermostat,
            dispatcher=dispatcher,
            get_was_clamped=get_was_clamped,
            reset_clamp_state=reset_clamp_state,
        )
        self._timers = HeaterTimerManager(
            hass=hass,
            thermostat_entity_id=thermostat.entity_id,
            pwm=pwm,
            valve_actuation_time=valve_actuation_time,
            bookkeeper=self._bookkeeper,
        )

        self._actuation = HeaterActuationController(self)
        self._safe_actuation_enabled = False

        # Heat pipeline for committed heat tracking.
        # Created for all PWM systems — transport_delay starts at 0 and is updated
        # dynamically via set_transport_delay() when the coordinator learns the manifold delay.
        self._heat_pipeline: HeatPipeline | None = (
            HeatPipeline(
                transport_delay=0.0,
                valve_time=valve_actuation_time,
                tau=HeatPipeline.tau_for_heating_type(heating_type),
            )
            if pwm
            else None
        )

        # PWM controller for duty accumulation and PWM switching
        self._pwm_controller = (
            PWMController(
                thermostat=thermostat,
                pwm_duration=pwm,
                difference=difference,
                min_open_time=min_open_time,
                min_closed_time=min_closed_time,
                valve_actuation_time=valve_actuation_time,
                heat_pipeline=self._heat_pipeline,
            )
            if pwm
            else None
        )

    # ── Backward-compatible delegation: bookkeeper state ──────────────────────

    @property
    def _cycle_active(self) -> bool:
        return self._bookkeeper.cycle_active

    @_cycle_active.setter
    def _cycle_active(self, value: bool) -> None:
        self._bookkeeper.cycle_active = value

    @property
    def _has_demand(self) -> bool:
        return self._bookkeeper.has_demand

    @_has_demand.setter
    def _has_demand(self, value: bool) -> None:
        self._bookkeeper.has_demand = value

    @property
    def _last_heater_state(self) -> bool:
        return self._bookkeeper.last_heater_state

    @_last_heater_state.setter
    def _last_heater_state(self, value: bool) -> None:
        self._bookkeeper.last_heater_state = value

    @property
    def _last_cooler_state(self) -> bool:
        return self._bookkeeper.last_cooler_state

    @_last_cooler_state.setter
    def _last_cooler_state(self, value: bool) -> None:
        self._bookkeeper.last_cooler_state = value

    @property
    def _heater_cycle_count(self) -> int:
        return self._bookkeeper.heater_cycle_count

    @property
    def _cooler_cycle_count(self) -> int:
        return self._bookkeeper.cooler_cycle_count

    def _get_pid_was_clamped(self) -> bool:
        return self._bookkeeper.get_pid_was_clamped()

    def _reset_pid_clamp_state(self) -> None:
        self._bookkeeper.reset_pid_clamp_state()

    # ── Backward-compatible delegation: timer handles ─────────────────────────

    @property
    def _demand_zero_timer(self) -> Any | None:
        return self._timers._demand_zero_timer

    @_demand_zero_timer.setter
    def _demand_zero_timer(self, value: Any | None) -> None:
        self._timers._demand_zero_timer = value

    @property
    def _low_output_timer(self) -> Any | None:
        return self._timers._low_output_timer

    @_low_output_timer.setter
    def _low_output_timer(self, value: Any | None) -> None:
        self._timers._low_output_timer = value

    def _emit_settling_started_debounced(self, hvac_mode: HVACMode, was_clamped: bool) -> None:
        """Kept for test compatibility — delegates to HeaterTimerManager._on_demand_zero."""
        self._timers._on_demand_zero(hvac_mode, was_clamped)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def heater_control_failed(self) -> bool:
        """Return True if the last heater control operation failed."""
        return self._service_caller.heater_control_failed

    @property
    def last_heater_error(self) -> str | None:
        """Return the last heater error message, if any."""
        return self._service_caller.last_heater_error

    @property
    def heater_cycle_count(self) -> int:
        """Return the total number of heater on→off cycles."""
        return self._bookkeeper.heater_cycle_count

    @property
    def cooler_cycle_count(self) -> int:
        """Return the total number of cooler on→off cycles."""
        return self._bookkeeper.cooler_cycle_count

    @property
    def cycle_active(self) -> bool:
        """Whether a cycle is currently active."""
        return self._bookkeeper.cycle_active

    @property
    def has_demand(self) -> bool:
        """Whether there is current demand (control_output > 0)."""
        return self._bookkeeper.has_demand

    def set_heater_cycle_count(self, count: int) -> None:
        """Set heater cycle count (used during state restoration)."""
        self._bookkeeper.set_heater_cycle_count(count)

    def set_cooler_cycle_count(self, count: int) -> None:
        """Set cooler cycle count (used during state restoration)."""
        self._bookkeeper.set_cooler_cycle_count(count)

    def cancel_pending_timers(self) -> None:
        """Cancel all pending timers (call on entity removal/shutdown)."""
        self._timers.cancel_all()

    def abort_active_cycle(self) -> None:
        """Abort the current cycle without emitting SETTLING_STARTED.

        Used when operating conditions change fundamentally (e.g., night setback).
        """
        self._timers.cancel_all()
        self._bookkeeper.abort()

    async def _async_call_heater_service(self, entity_id: str, domain: str, service: str, data: dict) -> bool:
        """Delegate to HeaterServiceCaller with error handling."""
        return await self._service_caller.async_call(entity_id, domain, service, data)

    # ── PWM controller pass-throughs ──────────────────────────────────────────

    @property
    def _max_accumulator(self) -> float:
        if self._pwm_controller:
            return self._pwm_controller._max_accumulator
        return 2.0 * self._min_open_time

    @property
    def _duty_accumulator_seconds(self) -> float:
        if self._pwm_controller:
            return self._pwm_controller._duty_accumulator_seconds
        return 0.0

    @_duty_accumulator_seconds.setter
    def _duty_accumulator_seconds(self, value: float) -> None:
        if self._pwm_controller:
            self._pwm_controller._duty_accumulator_seconds = value

    @property
    def _last_accumulator_calc_time(self) -> float | None:
        if self._pwm_controller:
            return self._pwm_controller._last_accumulator_calc_time
        return None

    @_last_accumulator_calc_time.setter
    def _last_accumulator_calc_time(self, value: float | None) -> None:
        if self._pwm_controller:
            self._pwm_controller._last_accumulator_calc_time = value

    @property
    def duty_accumulator_seconds(self) -> float:
        """Return the current duty accumulator value in seconds."""
        if self._pwm_controller:
            return self._pwm_controller.duty_accumulator_seconds
        return 0.0

    @property
    def min_open_time(self) -> float:
        """Return the minimum open time in seconds."""
        if self._pwm_controller:
            return self._pwm_controller.min_open_time
        return self._min_open_time

    @property
    def effective_min_open_time(self) -> float:
        """Return effective minimum open time including valve and transport delays.

        For floor hydronic systems with motorized valves and manifold transport delay,
        the valve must remain open long enough for:
        1. Valve to fully open (valve_actuation_time)
        2. Hot water to reach the zone (transport_delay)
        3. Actual heat delivery (min_open_time)

        Returns:
            Total minimum cycle time in seconds
        """
        return self._min_open_time + self._valve_actuation_time + self._transport_delay

    @property
    def cooling_type(self) -> str | None:
        """Return the cooling system type for compressor protection."""
        return self._cooling_type

    @property
    def committed_heat_at_last_turnoff(self) -> float:
        """Seconds of committed heat captured at the last valve-close command.

        Snapshotted by ``async_turn_off`` before calling ``pipeline.valve_closed()``.
        Used by CycleMetricsRecorder to split overshoot into controllable vs committed.
        """
        return self._last_committed_heat_snapshot

    def set_duty_accumulator(self, seconds: float) -> None:
        """Set duty accumulator (used during state restoration)."""
        if self._pwm_controller:
            self._pwm_controller.set_duty_accumulator(seconds)

    def reset_duty_accumulator(self) -> None:
        """Reset duty accumulator to zero."""
        if self._pwm_controller:
            self._pwm_controller.reset_duty_accumulator()

    def update_open_closed_times(self, min_open_time: float, min_closed_time: float) -> None:
        """Update minimum cycle durations when PID mode changes."""
        self._min_open_time = min_open_time
        self._min_closed_time = min_closed_time
        if self._pwm_controller:
            self._pwm_controller.update_open_closed_times(min_open_time, min_closed_time)

    def set_transport_delay(self, delay_seconds: float) -> None:
        """Set the manifold transport delay, propagating to PWMController and HeatPipeline."""
        self._transport_delay = delay_seconds
        if self._pwm_controller:
            self._pwm_controller.set_transport_delay(delay_seconds)
        if self._heat_pipeline is not None:
            self._heat_pipeline.transport_delay = delay_seconds

    # ── Entity helpers ─────────────────────────────────────────────────────────

    def get_entities(self, hvac_mode: HVACMode) -> list[str]:
        """Return entities to control based on HVAC mode (heater/cooler + demand switches)."""
        entities = []

        if hvac_mode == HVACMode.COOL and self._cooler_entity_id is not None:
            entities.extend(self._cooler_entity_id)
        elif self._heater_entity_id is not None:
            entities.extend(self._heater_entity_id)

        if self._demand_switch_entity_id is not None:
            entities.extend(self._demand_switch_entity_id)

        return entities

    def is_active(self, hvac_mode: HVACMode) -> bool:
        """Return True if the controlled device is currently active."""
        entities = self.get_entities(hvac_mode)

        if self._pwm:
            expected = STATE_ON
            if self._heater_polarity_invert:
                expected = STATE_OFF
            return any([self._hass.states.is_state(entity, expected) for entity in entities])
        else:
            is_active = False
            try:
                for entity in entities:
                    state = self._hass.states.get(entity).state
                    try:
                        value = float(state)
                        if value > 0:
                            is_active = True
                    except ValueError:
                        if state in ["on", "open"]:
                            is_active = True
                return is_active
            except AttributeError as ex:
                _LOGGER.debug("Entity state not available during device active check: %s", ex)
                return False

    # ── Core control methods ───────────────────────────────────────────────────

    def enable_safe_actuation(self) -> None:
        """Enable transactional commands for an explicitly coordinated PWM zone."""
        self._safe_actuation_enabled = True
        self._service_caller.blocking = True

    async def async_turn_on(
        self,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
    ) -> ActuationResult | None:
        """Turn on through the serialized, result-bearing binary command path."""
        if not self._safe_actuation_enabled:
            return await self._async_legacy_turn_on(
                hvac_mode, get_cycle_start_time, set_is_heating, set_last_heat_cycle_time
            )
        return await self._actuation.async_switch(
            True, hvac_mode, get_cycle_start_time, set_is_heating, set_last_heat_cycle_time
        )

    async def async_turn_off(
        self,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        force: bool = False,
    ) -> ActuationResult | None:
        """Invalidate pending ON, then serialize OFF and its bookkeeping."""
        if not self._safe_actuation_enabled:
            return await self._async_legacy_turn_off(
                hvac_mode, get_cycle_start_time, set_is_heating, set_last_heat_cycle_time, force
            )
        return await self._actuation.async_switch(
            False, hvac_mode, get_cycle_start_time, set_is_heating, set_last_heat_cycle_time, force
        )

    async def _async_legacy_turn_on(
        self,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
    ) -> None:
        """Turn heater toggleable device on."""
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id
        is_device_active = self.is_active(hvac_mode)

        if is_device_active:
            # State refresh call — device already on
            _LOGGER.debug("%s: Refresh state ON %s", thermostat_entity_id, ", ".join(entities))
            # Handle restart case: device already on but cycle not tracked
            if not self._bookkeeper.cycle_active and self._bookkeeper.has_demand:
                self._bookkeeper.cycle_active = True
                self._bookkeeper.reset_pid_clamp_state()
                self._bookkeeper.emit_cycle_started(hvac_mode)
            return
        elif time.monotonic() - get_cycle_start_time() >= self._min_closed_time:
            _LOGGER.info("%s: Turning ON %s", thermostat_entity_id, ", ".join(entities))
            set_last_heat_cycle_time(time.monotonic())

            # Update state tracking for cycle counting (off→on transition)
            if hvac_mode == HVACMode.COOL:
                self._bookkeeper.last_cooler_state = True
            else:
                self._bookkeeper.last_heater_state = True

            set_is_heating(True)

            # Emit CYCLE_STARTED on first heater turn-on in this demand period
            if not self._bookkeeper.cycle_active and self._bookkeeper.has_demand:
                self._bookkeeper.cycle_active = True
                self._bookkeeper.reset_pid_clamp_state()
                self._bookkeeper.emit_cycle_started(hvac_mode)

            if self._dispatcher:
                if self._pwm and self._valve_actuation_time > 0:
                    self._timers.schedule_heating_started(hvac_mode)
                else:
                    self._bookkeeper.emit_heating_started(hvac_mode)
        else:
            _LOGGER.info(
                "%s: Reject request turning ON %s: Cycle is too short",
                thermostat_entity_id,
                ", ".join(entities),
            )
            return

        await self._async_send_and_verify(hvac_mode, entities, SERVICE_TURN_ON)

        # Pipeline: record valve-open timestamp so committed heat starts rising
        if self._heat_pipeline is not None:
            self._heat_pipeline.valve_opened(time.monotonic())

    async def _async_legacy_turn_off(
        self,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        force: bool = False,
    ) -> None:
        """Turn heater toggleable device off. Enforces min-on-time for compressor protection."""
        if force:
            self._timers.cancel_all()

        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id
        is_device_active = self.is_active(hvac_mode)

        if not is_device_active:
            # State refresh call — device already off
            _LOGGER.debug("%s: Refresh state OFF %s", thermostat_entity_id, ", ".join(entities))
            return
        elif time.monotonic() - get_cycle_start_time() >= self.effective_min_open_time or force:
            _LOGGER.info("%s: Turning OFF %s", thermostat_entity_id, ", ".join(entities))
            set_last_heat_cycle_time(time.monotonic())

            self._bookkeeper.increment_cycle_count(hvac_mode, is_now_off=True)
            self._timers.cancel_valve_open()

            if self._heat_pipeline is not None:
                _now = time.monotonic()
                self._last_committed_heat_snapshot = self._heat_pipeline.committed_heat_remaining(_now)
                self._heat_pipeline.valve_closed(_now)

            if self._dispatcher:
                if self._pwm and self._valve_actuation_time > 0:
                    self._timers.schedule_heating_ended(hvac_mode, self._last_committed_heat_snapshot)
                else:
                    self._bookkeeper.emit_heating_ended(hvac_mode, self._last_committed_heat_snapshot)

            set_is_heating(False)
        else:
            elapsed = time.monotonic() - get_cycle_start_time()
            _LOGGER.info(
                "%s: Reject turning OFF %s: Cycle too short (%.0fs < %.0fs effective min)",
                thermostat_entity_id,
                ", ".join(entities),
                elapsed,
                self.effective_min_open_time,
            )
            return

        await self._async_send_and_verify(hvac_mode, entities, SERVICE_TURN_OFF)

    async def _async_send_and_verify(
        self, hvac_mode: HVACMode, entities: list[str], service: str, attempt: int = 1
    ) -> None:
        """Send turn_on/turn_off (``service`` = desired device state) and re-check the switch later."""
        self._timers.cancel_command_verify()
        sent = service
        if self._heater_polarity_invert:
            sent = SERVICE_TURN_OFF if service == SERVICE_TURN_ON else SERVICE_TURN_ON
        for entity in entities:
            await self._async_call_heater_service(entity, HA_DOMAIN, sent, {ATTR_ENTITY_ID: entity})

        async def verify() -> None:
            wanted_active = service == SERVICE_TURN_ON
            if self.is_active(hvac_mode) == wanted_active:
                _LOGGER.debug("%s: %s confirmed", self._thermostat.entity_id, service)
                return
            actual = "on" if self.is_active(hvac_mode) else "off"
            if attempt <= COMMAND_MAX_RETRIES:
                _LOGGER.warning(
                    "%s: %s still %s %.0fs after %s, retrying (%d/%d)",
                    self._thermostat.entity_id,
                    ", ".join(entities),
                    actual,
                    COMMAND_VERIFY_DELAY,
                    service,
                    attempt,
                    COMMAND_MAX_RETRIES,
                )
                await self._async_send_and_verify(hvac_mode, entities, service, attempt + 1)
            else:
                _LOGGER.error(
                    "%s: %s still %s after %d retries of %s, giving up",
                    self._thermostat.entity_id,
                    ", ".join(entities),
                    actual,
                    COMMAND_MAX_RETRIES,
                    service,
                )

        self._timers.schedule_command_verify(verify, COMMAND_VERIFY_DELAY)

    async def async_set_valve_value(self, value: float, hvac_mode: HVACMode) -> None:
        """Set valve value for non-PWM devices (0-100)."""
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id

        old_active = self.is_active(hvac_mode)
        self._bookkeeper.has_demand = value > 0

        _LOGGER.info("%s: Change state of %s to %s", thermostat_entity_id, ", ".join(entities), value)

        for entity in entities:
            domain, _ = split_entity_id(entity)
            if domain == "light":
                data = {ATTR_ENTITY_ID: entity, ATTR_BRIGHTNESS_PCT: value}
                await self._async_call_heater_service(entity, LIGHT_DOMAIN, SERVICE_TURN_LIGHT_ON, data)
            elif domain == "valve":
                data = {ATTR_ENTITY_ID: entity, ATTR_POSITION: value}
                await self._async_call_heater_service(entity, VALVE_DOMAIN, SERVICE_SET_VALVE_POSITION, data)
            else:
                number_domain = INPUT_NUMBER_DOMAIN if domain == "input_number" else NUMBER_DOMAIN
                data = {ATTR_ENTITY_ID: entity, ATTR_VALUE: value}
                await self._async_call_heater_service(entity, number_domain, SERVICE_SET_VALUE, data)

        new_active = value > 0

        # Emit SETTLING_STARTED for valve mode: demand < 5% AND within 0.5°C of target
        if self._bookkeeper.cycle_active and value < 5.0:
            target_temp = getattr(self._thermostat, "target_temperature", 0.0)
            current_temp = getattr(self._thermostat, "_current_temp", 0.0)
            if abs(current_temp - target_temp) <= 0.5:
                self._bookkeeper.emit_settling_started(hvac_mode, self._bookkeeper.get_pid_was_clamped())
                self._bookkeeper.cycle_active = False

        if not old_active and new_active:
            if hvac_mode == HVACMode.COOL:
                self._bookkeeper.last_cooler_state = True
            else:
                self._bookkeeper.last_heater_state = True

        if new_active and not self._bookkeeper.cycle_active and self._bookkeeper.has_demand:
            self._bookkeeper.cycle_active = True
            self._bookkeeper.reset_pid_clamp_state()
            self._bookkeeper.emit_cycle_started(hvac_mode)
            self._bookkeeper.emit_heating_started(hvac_mode)
        elif old_active and not new_active:
            self._bookkeeper.increment_cycle_count(hvac_mode, is_now_off=True)
            self._bookkeeper.emit_heating_ended(hvac_mode)
            self._bookkeeper.cycle_active = False

    async def async_set_control_value(
        self,
        control_output: float,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        time_changed: float,
        set_time_changed: Callable[[float], None],
        force_on: bool,
        force_off: bool,
        set_force_on: Callable[[bool], None],
        set_force_off: Callable[[bool], None],
    ) -> None:
        """Dispatch PID control_output to the appropriate heater/valve action."""
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id

        # Cache is_active() result to avoid redundant HA state queries
        device_is_active = self.is_active(hvac_mode)

        # Track demand state for cycle tracking
        old_has_demand = self._bookkeeper.has_demand
        new_has_demand = abs(control_output) > 0
        self._bookkeeper.has_demand = new_has_demand

        # Session end (PWM): one 2×PWM countdown from the first output at 0 or below MIN_OUTPUT_THRESHOLD.
        # Flips between 0 and low output don't restart it; only real demand away from target cancels it.
        countdown_active = self._timers.demand_zero_active or self._timers.low_output_active

        # Demand dropped to 0 → debounce SETTLING_STARTED
        if old_has_demand and not new_has_demand and self._bookkeeper.cycle_active:
            if self._pwm and self._dispatcher:
                if not countdown_active:
                    self._timers.schedule_demand_zero_debounce(hvac_mode, self._bookkeeper.get_pid_was_clamped())
            else:
                if self._dispatcher:
                    self._bookkeeper.emit_settling_started(hvac_mode, self._bookkeeper.get_pid_was_clamped())
                self._bookkeeper.cycle_active = False

        # Low-output maintenance timeout (maintenance cycles that never reach 0)
        if self._pwm and self._bookkeeper.cycle_active and self._dispatcher:
            if 0 < abs(control_output) < MIN_OUTPUT_THRESHOLD:
                if not countdown_active:
                    self._timers.schedule_low_output_timeout(hvac_mode, self._bookkeeper.get_pid_was_clamped())
            elif abs(control_output) >= MIN_OUTPUT_THRESHOLD and countdown_active and not self._near_target():
                self._timers.cancel_demand_zero()
                self._timers.cancel_low_output()

        from .pwm_coordination import PWMCoordinationRegistry

        registry = getattr(self._thermostat, "_pwm_coordination", None)
        if isinstance(registry, PWMCoordinationRegistry) and await registry.apply(thermostat_entity_id):
            if registry.owns(thermostat_entity_id):
                set_force_on(False)
                set_force_off(False)
            return

        if self._pwm:
            if abs(control_output) == self._difference:
                if not device_is_active:
                    _LOGGER.info(
                        "%s: Output is %s. Request turning ON %s",
                        thermostat_entity_id,
                        self._difference,
                        ", ".join(entities),
                    )
                    set_time_changed(time.monotonic())
                await self.async_turn_on(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
            elif abs(control_output) > 0:
                await self.async_pwm_switch(
                    control_output=control_output,
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                    time_changed=time_changed,
                    set_time_changed=set_time_changed,
                    force_on=force_on,
                    force_off=force_off,
                    set_force_on=set_force_on,
                    set_force_off=set_force_off,
                )
            else:
                if device_is_active:
                    _LOGGER.info("%s: Output is 0. Request turning OFF %s", thermostat_entity_id, ", ".join(entities))
                    set_time_changed(time.monotonic())
                await self.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
        else:
            await self.async_set_valve_value(abs(control_output), hvac_mode)

    def _near_target(self) -> bool:
        """Room within the learner's acceptable deviation: PWM pulses here are maintenance, not new demand."""
        target = getattr(self._thermostat, "target_temperature", None)
        current = getattr(self._thermostat, "_current_temp", None)
        if target is None or current is None:
            return False
        return abs(current - target) <= get_convergence_thresholds(self._heating_type)["undershoot_max"]

    async def async_pwm_switch(
        self,
        control_output: float,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        time_changed: float,
        set_time_changed: Callable[[float], None],
        force_on: bool,
        force_off: bool,
        set_force_on: Callable[[bool], None],
        set_force_off: Callable[[bool], None],
    ) -> None:
        """Proportional PWM switching — delegates to PWMController."""
        if self._pwm_controller:
            await self._pwm_controller.async_pwm_switch(
                control_output=control_output,
                hvac_mode=hvac_mode,
                heater_controller=self,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=time_changed,
                set_time_changed=set_time_changed,
                force_on=force_on,
                force_off=force_off,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )
