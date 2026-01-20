"""Heater controller manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, List, Optional

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant
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
    from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
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
    HomeAssistantError = Exception
    ServiceNotFound = Exception

from ..const import DOMAIN
from .events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    SettlingStartedEvent,
    HeatingStartedEvent,
    HeatingEndedEvent,
)

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


class HeaterController:
    """Controller for heater/cooler device operations.

    Manages the state and control of heater/cooler entities for the thermostat.
    This includes turning devices on/off, setting valve values, and PWM control.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        thermostat: AdaptiveThermostat,
        heater_entity_id: List[str] | None,
        cooler_entity_id: List[str] | None,
        demand_switch_entity_id: List[str] | None,
        heater_polarity_invert: bool,
        pwm: int,  # PWM duration in seconds
        difference: float,  # output_max - output_min
        min_on_cycle_duration: float,  # in seconds
        min_off_cycle_duration: float,  # in seconds
        dispatcher: Optional[CycleEventDispatcher] = None,
    ):
        """Initialize the HeaterController.

        Args:
            hass: Home Assistant instance
            thermostat: Reference to the parent thermostat entity
            heater_entity_id: List of heater entity IDs
            cooler_entity_id: List of cooler entity IDs
            demand_switch_entity_id: List of demand switch entity IDs
            heater_polarity_invert: Whether to invert heater polarity
            pwm: PWM duration in seconds
            difference: Output range (max - min)
            min_on_cycle_duration: Minimum on cycle duration in seconds
            min_off_cycle_duration: Minimum off cycle duration in seconds
            dispatcher: Optional event dispatcher for cycle events
        """
        self._hass = hass
        self._thermostat = thermostat
        self._heater_entity_id = heater_entity_id
        self._cooler_entity_id = cooler_entity_id
        self._demand_switch_entity_id = demand_switch_entity_id
        self._heater_polarity_invert = heater_polarity_invert
        self._pwm = pwm
        self._difference = difference
        self._min_on_cycle_duration = min_on_cycle_duration
        self._min_off_cycle_duration = min_off_cycle_duration
        self._dispatcher = dispatcher

        # State tracking (owned by thermostat, but accessed here)
        self._heater_control_failed = False
        self._last_heater_error: str | None = None

        # Cycle counting for actuator wear tracking
        self._heater_cycle_count: int = 0
        self._cooler_cycle_count: int = 0
        self._last_heater_state: bool = False
        self._last_cooler_state: bool = False

        # Cycle tracking for event emission
        self._cycle_active: bool = False

    def update_cycle_durations(
        self,
        min_on_cycle_duration: float,
        min_off_cycle_duration: float,
    ) -> None:
        """Update the minimum cycle durations.

        This is used when the PID mode changes, as different modes
        may have different minimum cycle requirements.

        Args:
            min_on_cycle_duration: Minimum on cycle duration in seconds
            min_off_cycle_duration: Minimum off cycle duration in seconds
        """
        self._min_on_cycle_duration = min_on_cycle_duration
        self._min_off_cycle_duration = min_off_cycle_duration

    @property
    def heater_control_failed(self) -> bool:
        """Return True if the last heater control operation failed."""
        return self._heater_control_failed

    @property
    def last_heater_error(self) -> str | None:
        """Return the last heater error message, if any."""
        return self._last_heater_error

    @property
    def heater_cycle_count(self) -> int:
        """Return the total number of heater on→off cycles."""
        return self._heater_cycle_count

    @property
    def cooler_cycle_count(self) -> int:
        """Return the total number of cooler on→off cycles."""
        return self._cooler_cycle_count

    def set_heater_cycle_count(self, count: int) -> None:
        """Set the heater cycle count (used during state restoration).

        Args:
            count: Cycle count to restore
        """
        self._heater_cycle_count = count

    def set_cooler_cycle_count(self, count: int) -> None:
        """Set the cooler cycle count (used during state restoration).

        Args:
            count: Cycle count to restore
        """
        self._cooler_cycle_count = count

    def _increment_cycle_count(self, hvac_mode: HVACMode, is_now_off: bool) -> None:
        """Increment cycle counter on on→off transition.

        Args:
            hvac_mode: Current HVAC mode
            is_now_off: Whether device just turned off
        """
        if not is_now_off:
            return

        # Increment heater or cooler based on mode
        if hvac_mode == HVACMode.COOL:
            if self._last_cooler_state and is_now_off:
                self._cooler_cycle_count += 1
                _LOGGER.debug(
                    "%s: Cooler cycle count incremented to %d",
                    self._thermostat.entity_id,
                    self._cooler_cycle_count
                )
            self._last_cooler_state = not is_now_off
        else:
            if self._last_heater_state and is_now_off:
                self._heater_cycle_count += 1
                _LOGGER.debug(
                    "%s: Heater cycle count incremented to %d",
                    self._thermostat.entity_id,
                    self._heater_cycle_count
                )
            self._last_heater_state = not is_now_off

    def get_entities(self, hvac_mode: HVACMode) -> List[str]:
        """Return the entities to be controlled based on HVAC MODE.

        Returns heater or cooler entities based on mode, plus any demand_switch
        entities which are controlled regardless of heat/cool mode.

        Args:
            hvac_mode: Current HVAC mode

        Returns:
            List of entity IDs to control
        """
        entities = []

        # Add heater or cooler based on mode
        if hvac_mode == HVACMode.COOL and self._cooler_entity_id is not None:
            entities.extend(self._cooler_entity_id)
        elif self._heater_entity_id is not None:
            entities.extend(self._heater_entity_id)

        # Add demand_switch entities (controlled in both heat and cool modes)
        if self._demand_switch_entity_id is not None:
            entities.extend(self._demand_switch_entity_id)

        return entities

    def is_active(self, hvac_mode: HVACMode) -> bool:
        """Check if the controlled device is currently active.

        For PWM devices, checks if any entity is in the expected ON state.
        For valve devices, checks if any entity has a value > 0.

        Args:
            hvac_mode: Current HVAC mode

        Returns:
            True if the device is active
        """
        entities = self.get_entities(hvac_mode)

        if self._pwm:
            # If the toggleable device is currently active
            expected = STATE_ON
            if self._heater_polarity_invert:
                expected = STATE_OFF
            return any([
                self._hass.states.is_state(entity, expected)
                for entity in entities
            ])
        else:
            # If the valve device is currently active
            is_active = False
            try:
                for entity in entities:
                    state = self._hass.states.get(entity).state
                    try:
                        value = float(state)
                        if value > 0:
                            is_active = True
                    except ValueError:
                        if state in ['on', 'open']:
                            is_active = True
                return is_active
            except AttributeError as ex:
                _LOGGER.debug(
                    "Entity state not available during device active check: %s",
                    ex
                )
                return False

    def _fire_heater_control_failed_event(
        self,
        entity_id: str,
        operation: str,
        error: str,
    ) -> None:
        """Fire an event when heater control fails.

        Args:
            entity_id: Entity that failed to control
            operation: Operation that failed (turn_on, turn_off, set_value)
            error: Error message
        """
        self._hass.bus.async_fire(
            f"{DOMAIN}_heater_control_failed",
            {
                "climate_entity_id": self._thermostat.entity_id,
                "heater_entity_id": entity_id,
                "operation": operation,
                "error": error,
            },
        )

    async def _async_call_heater_service(
        self,
        entity_id: str,
        domain: str,
        service: str,
        data: dict,
    ) -> bool:
        """Call a heater/cooler service with error handling.

        Args:
            entity_id: Entity ID being controlled
            domain: Service domain (homeassistant, light, valve, number, etc.)
            service: Service name (turn_on, turn_off, set_value, etc.)
            data: Service call data

        Returns:
            True if successful, False otherwise
        """
        thermostat_entity_id = self._thermostat.entity_id

        try:
            await self._hass.services.async_call(domain, service, data)
            # Clear failure state on success
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
            self._fire_heater_control_failed_event(entity_id, service, str(e))
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
            self._fire_heater_control_failed_event(entity_id, service, str(e))
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
            self._fire_heater_control_failed_event(entity_id, service, str(e))
            return False

    @staticmethod
    def _get_number_entity_domain(entity_id: str) -> str:
        """Get the domain for a number entity.

        Args:
            entity_id: Entity ID to check

        Returns:
            Either INPUT_NUMBER_DOMAIN or NUMBER_DOMAIN
        """
        return INPUT_NUMBER_DOMAIN if "input_number" in entity_id else NUMBER_DOMAIN

    async def async_turn_on(
        self,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
    ) -> None:
        """Turn heater toggleable device on.

        Args:
            hvac_mode: Current HVAC mode
            get_cycle_start_time: Callable that returns cycle start time
            set_is_heating: Callback to set heating state
            set_last_heat_cycle_time: Callback to set last heat cycle time
        """
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id

        is_device_active = self.is_active(hvac_mode)

        if is_device_active:
            # It's a state refresh call from control interval, just force switch ON
            _LOGGER.info(
                "%s: Refresh state ON %s",
                thermostat_entity_id,
                ", ".join(entities)
            )
        elif time.time() - get_cycle_start_time() >= self._min_off_cycle_duration:
            _LOGGER.info(
                "%s: Turning ON %s",
                thermostat_entity_id,
                ", ".join(entities)
            )
            set_last_heat_cycle_time(time.time())

            # Update state tracking for cycle counting (off→on transition)
            if hvac_mode == HVACMode.COOL:
                self._last_cooler_state = True
            else:
                self._last_heater_state = True

            set_is_heating(True)

            # Emit HEATING_STARTED event
            if self._dispatcher:
                self._dispatcher.emit(HeatingStartedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=datetime.now(),
                ))
        else:
            _LOGGER.info(
                "%s: Reject request turning ON %s: Cycle is too short",
                thermostat_entity_id,
                ", ".join(entities)
            )
            return

        for entity in entities:
            data = {ATTR_ENTITY_ID: entity}
            if self._heater_polarity_invert:
                service = SERVICE_TURN_OFF
            else:
                service = SERVICE_TURN_ON
            await self._async_call_heater_service(
                entity, HA_DOMAIN, service, data
            )

    async def async_turn_off(
        self,
        hvac_mode: HVACMode,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        force: bool = False,
    ) -> None:
        """Turn heater toggleable device off.

        Args:
            hvac_mode: Current HVAC mode
            get_cycle_start_time: Callable that returns cycle start time
            set_is_heating: Callback to set heating state
            set_last_heat_cycle_time: Callback to set last heat cycle time
            force: Force turn off regardless of cycle duration
        """
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id
        is_device_active = self.is_active(hvac_mode)

        if not is_device_active:
            # It's a state refresh call from control interval, just force switch OFF
            _LOGGER.info(
                "%s: Refresh state OFF %s",
                thermostat_entity_id,
                ", ".join(entities)
            )
        elif time.time() - get_cycle_start_time() >= self._min_on_cycle_duration or force:
            _LOGGER.info(
                "%s: Turning OFF %s",
                thermostat_entity_id,
                ", ".join(entities)
            )
            set_last_heat_cycle_time(time.time())

            # Increment cycle counter for wear tracking (on→off transition)
            self._increment_cycle_count(hvac_mode, is_now_off=True)

            # Emit HEATING_ENDED event
            if self._dispatcher:
                self._dispatcher.emit(HeatingEndedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=datetime.now(),
                ))

            # Reset heating state for zone linking
            set_is_heating(False)
        else:
            _LOGGER.info(
                "%s: Reject request turning OFF %s: Cycle is too short",
                thermostat_entity_id,
                ", ".join(entities)
            )
            return

        for entity in entities:
            data = {ATTR_ENTITY_ID: entity}
            if self._heater_polarity_invert:
                service = SERVICE_TURN_ON
            else:
                service = SERVICE_TURN_OFF
            await self._async_call_heater_service(
                entity, HA_DOMAIN, service, data
            )

    async def async_set_valve_value(
        self,
        value: float,
        hvac_mode: HVACMode,
    ) -> None:
        """Set the valve value for non-PWM devices.

        Args:
            value: Value to set (0-100)
            hvac_mode: Current HVAC mode
        """
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id

        # Track old active state for cycle tracker
        old_active = self.is_active(hvac_mode)

        _LOGGER.info(
            "%s: Change state of %s to %s",
            thermostat_entity_id,
            ", ".join(entities),
            value
        )

        for entity in entities:
            if entity[0:6] == 'light.':
                data = {ATTR_ENTITY_ID: entity, ATTR_BRIGHTNESS_PCT: value}
                await self._async_call_heater_service(
                    entity,
                    LIGHT_DOMAIN,
                    SERVICE_TURN_LIGHT_ON,
                    data,
                )
            elif entity[0:6] == 'valve.':
                data = {ATTR_ENTITY_ID: entity, ATTR_POSITION: value}
                await self._async_call_heater_service(
                    entity,
                    VALVE_DOMAIN,
                    SERVICE_SET_VALVE_POSITION,
                    data,
                )
            else:
                data = {ATTR_ENTITY_ID: entity, ATTR_VALUE: value}
                await self._async_call_heater_service(
                    entity,
                    self._get_number_entity_domain(entity),
                    SERVICE_SET_VALUE,
                    data,
                )

        # Track new active state after valve change for cycle counting
        new_active = value > 0

        # Check if we should emit SETTLING_STARTED for valve mode
        # Criteria: demand < 5% AND temp within 0.5°C of target
        if self._cycle_active and value < 5.0:
            target_temp = getattr(self._thermostat, 'target_temperature', 0.0)
            current_temp = getattr(self._thermostat, '_cur_temp', 0.0)
            if abs(current_temp - target_temp) <= 0.5:
                if self._dispatcher:
                    self._dispatcher.emit(SettlingStartedEvent(
                        hvac_mode=hvac_mode,
                        timestamp=datetime.now(),
                    ))

        # Detect heating started transition (was off, now on)
        if not old_active and new_active:
            # Update state tracking for cycle counting
            if hvac_mode == HVACMode.COOL:
                self._last_cooler_state = True
            else:
                self._last_heater_state = True

            # Emit HEATING_STARTED event
            if self._dispatcher:
                self._dispatcher.emit(HeatingStartedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=datetime.now(),
                ))
        # Detect heating stopped transition (was on, now off)
        elif old_active and not new_active:
            # Increment cycle counter for wear tracking (on→off transition)
            self._increment_cycle_count(hvac_mode, is_now_off=True)

            # Emit HEATING_ENDED event
            if self._dispatcher:
                self._dispatcher.emit(HeatingEndedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=datetime.now(),
                ))

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
        """Set output value for heater.

        Args:
            control_output: Current PID control output
            hvac_mode: Current HVAC mode
            get_cycle_start_time: Callable that returns cycle start time
            set_is_heating: Callback to set heating state
            set_last_heat_cycle_time: Callback to set last heat cycle time
            time_changed: Last time state changed
            set_time_changed: Callback to set time changed
            force_on: Force turn on flag
            force_off: Force turn off flag
            set_force_on: Callback to set force on flag
            set_force_off: Callback to set force off flag
        """
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id

        # Track demand state for cycle tracking
        old_cycle_active = self._cycle_active
        new_cycle_active = abs(control_output) > 0
        self._cycle_active = new_cycle_active

        # Emit CYCLE_STARTED when demand goes 0 → >0
        if not old_cycle_active and new_cycle_active:
            if self._dispatcher:
                target_temp = getattr(self._thermostat, 'target_temperature', 0.0)
                current_temp = getattr(self._thermostat, '_cur_temp', 0.0)
                self._dispatcher.emit(CycleStartedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=datetime.now(),
                    target_temp=target_temp,
                    current_temp=current_temp,
                ))

        # Emit SETTLING_STARTED when demand drops to 0 (PWM mode)
        if old_cycle_active and not new_cycle_active:
            if self._dispatcher and self._pwm > 0:
                self._dispatcher.emit(SettlingStartedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=datetime.now(),
                ))

        if self._pwm:
            if abs(control_output) == self._difference:
                if not self.is_active(hvac_mode):
                    _LOGGER.info(
                        "%s: Output is %s. Request turning ON %s",
                        thermostat_entity_id,
                        self._difference,
                        ", ".join(entities)
                    )
                    set_time_changed(time.time())
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
                if self.is_active(hvac_mode):
                    _LOGGER.info(
                        "%s: Output is 0. Request turning OFF %s",
                        thermostat_entity_id,
                        ", ".join(entities)
                    )
                    set_time_changed(time.time())
                await self.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
        else:
            await self.async_set_valve_value(abs(control_output), hvac_mode)

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
        """Turn off and on the heater proportionally to control_value.

        Args:
            control_output: Current PID control output
            hvac_mode: Current HVAC mode
            get_cycle_start_time: Callable that returns cycle start time
            set_is_heating: Callback to set heating state
            set_last_heat_cycle_time: Callback to set last heat cycle time
            time_changed: Last time state changed
            set_time_changed: Callback to set time changed
            force_on: Force turn on flag
            force_off: Force turn off flag
            set_force_on: Callback to set force on flag
            set_force_off: Callback to set force off flag
        """
        entities = self.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id

        time_passed = time.time() - time_changed
        # Compute time_on based on PWM duration and PID output
        time_on = self._pwm * abs(control_output) / self._difference
        time_off = self._pwm - time_on

        # If calculated on-time < min_on_cycle_duration, don't turn on at all
        # (turning on would force staying on for min_on, overshooting demand)
        if 0 < time_on < self._min_on_cycle_duration:
            _LOGGER.debug(
                "%s: Skipping PWM activation - calculated on-time %.0fs < min %.0fs",
                thermostat_entity_id,
                time_on,
                self._min_on_cycle_duration,
            )
            await self.async_turn_off(
                hvac_mode=hvac_mode,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
            )
            return

        if 0 < time_off < self._min_off_cycle_duration:
            # time_off is too short, increase time_on and time_off
            time_on *= self._min_off_cycle_duration / time_off
            time_off = self._min_off_cycle_duration

        is_device_active = self.is_active(hvac_mode)

        if is_device_active:
            if time_on <= time_passed or force_off:
                _LOGGER.info(
                    "%s: ON time passed. Request turning OFF %s",
                    thermostat_entity_id,
                    ", ".join(entities)
                )
                await self.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
                set_time_changed(time.time())
            else:
                _LOGGER.info(
                    "%s: Time until %s turns OFF: %s sec",
                    thermostat_entity_id,
                    ", ".join(entities),
                    int(time_on - time_passed)
                )
                await self.async_turn_on(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
        else:
            if time_off <= time_passed or force_on:
                _LOGGER.info(
                    "%s: OFF time passed. Request turning ON %s",
                    thermostat_entity_id,
                    ", ".join(entities)
                )
                await self.async_turn_on(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
                set_time_changed(time.time())
            else:
                _LOGGER.info(
                    "%s: Time until %s turns ON: %s sec",
                    thermostat_entity_id,
                    ", ".join(entities),
                    int(time_off - time_passed)
                )
                await self.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )

        set_force_on(False)
        set_force_off(False)
