"""State setter and heater delegation methods for adaptive thermostat.

This module contains callback methods used by managers to read/write thermostat
state, properties that expose device capabilities, and thin delegation wrappers
for HeaterController operations.
"""

from __future__ import annotations

import logging

from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from .const import PIDChangeReason
from .managers.events import SetpointChangedEvent
from .managers.pwm_coordination import PWMCoordinationRegistry

_LOGGER = logging.getLogger(__name__)


class ClimateStateSettersMixin:
    """Mixin providing state setter callbacks and heater delegation for AdaptiveThermostat.

    Groups three categories:
    - Simple state setters: tiny callback methods wired to HeaterController /
      ControlOutputManager so managers can mutate thermostat state.
    - Protocol interface: methods required by KeManagerState and similar Protocols.
    - Heater delegation: thin wrappers that forward actuator calls to HeaterController
      while keeping climate.py free of low-level I/O.
    """

    # ------------------------------------------------------------------
    # Temperature range properties
    # ------------------------------------------------------------------

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        if self._min_temp:
            return self._min_temp
        return super().min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._max_temp:
            return self._max_temp
        return super().max_temp

    # ------------------------------------------------------------------
    # Setter callbacks for HeaterController
    # ------------------------------------------------------------------

    def _set_is_heating(self, value: bool) -> None:
        """Set the heating state flag."""
        self._is_heating = value

    def _set_last_heat_cycle_time(self, value: float) -> None:
        """Set the last heat cycle time."""
        self._last_heat_cycle_time = value

    def _set_time_changed(self, value: float) -> None:
        """Set the time changed value."""
        self._time_changed = value

    def _set_force_on(self, value: bool) -> None:
        """Set the force on flag."""
        self._force_on = value

    def _set_force_off(self, value: bool) -> None:
        """Set the force off flag."""
        self._force_off = value

    # ------------------------------------------------------------------
    # Setter callbacks for ControlOutputManager
    # ------------------------------------------------------------------

    def _set_control_output(self, value: float) -> None:
        """Set the control output value."""
        self._control_output = value

    def _set_p(self, value: float) -> None:
        """Set the proportional component value."""
        self._p = value

    def _set_i(self, value: float) -> None:
        """Set the integral component value."""
        self._i = value

    def _set_d(self, value: float) -> None:
        """Set the derivative component value."""
        self._d = value

    def _set_e(self, value: float) -> None:
        """Set the external component value."""
        self._e = value

    def _set_dt(self, value: float) -> None:
        """Set the delta time value."""
        self._dt = value

    def _set_previous_temp_time(self, value: float) -> None:
        """Set the previous temperature time."""
        self._previous_temp_time = value

    def _set_cur_temp_time(self, value: float) -> None:
        """Set the current temperature time."""
        self._cur_temp_time = value

    # ------------------------------------------------------------------
    # Protocol interface (KeManagerState)
    # ------------------------------------------------------------------

    def is_pid_converged_for_ke(self) -> bool:
        """Check if PID has converged sufficiently for Ke learning.

        Satisfies the KeManagerState.is_pid_converged_for_ke() Protocol method.
        Returns True if the adaptive learner reports PID convergence
        (stable performance for required number of consecutive cycles).
        """
        coordinator = self._coordinator
        if not coordinator or not self._zone_id:
            return False
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return False
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return False
        return adaptive_learner.is_pid_converged_for_ke()

    # ------------------------------------------------------------------
    # Async state write callback (for managers)
    # ------------------------------------------------------------------

    async def _async_write_ha_state_internal(self) -> None:
        """Write HA state (internal callback for managers)."""
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Setter callback for TemperatureManager
    # ------------------------------------------------------------------

    def _set_target_temp(self, value: float) -> None:
        """Set the target temperature."""
        old_temp = self._target_temp
        self._target_temp = value

        # H04: Mirror user setpoint (pre-setback) into zone_data so the coordinator's
        # get_active_zone_setpoints returns the daytime target, not the night-setback-
        # reduced effective target.
        if self._zone_id and self._coordinator is not None:
            zone_data = self._coordinator.get_zone_data(self._zone_id)
            if zone_data is not None:
                zone_data["user_target_temp"] = value

        # Emit setpoint changed event
        if old_temp is not None and old_temp != value:
            if isinstance(getattr(self, "_pwm_coordination", None), PWMCoordinationRegistry):
                self._pwm_coordination.invalidate(self.entity_id, urgent=True)
            # Reset duty accumulator if setpoint changes by more than 0.5°C
            if abs(value - old_temp) > 0.5 and self._heater_controller is not None:
                self._heater_controller.reset_duty_accumulator()

            if hasattr(self, "_cycle_dispatcher") and self._cycle_dispatcher:
                self._cycle_dispatcher.emit(
                    SetpointChangedEvent(
                        hvac_mode=str(self._hvac_mode.value) if self._hvac_mode else "off",
                        timestamp=dt_util.utcnow(),
                        old_target=old_temp,
                        new_target=value,
                    )
                )

            # Notify setpoint boost manager
            if hasattr(self, "_setpoint_boost_manager") and self._setpoint_boost_manager:
                self._setpoint_boost_manager.on_setpoint_change(old_temp, value)

    # ------------------------------------------------------------------
    # Internal callbacks for TemperatureManager
    # ------------------------------------------------------------------

    async def _async_set_pid_mode_internal(self, mode: str) -> None:
        """Internal callback to set PID mode from TemperatureManager."""
        await self.async_set_pid_mode(mode=mode)

    async def _async_control_heating_internal(self, calc_pid: bool) -> None:
        """Internal callback to trigger heating control from TemperatureManager."""
        await self._async_control_heating(calc_pid=calc_pid)

    # ------------------------------------------------------------------
    # Capability properties
    # ------------------------------------------------------------------

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    @property
    def heater_or_cooler_entity(self):
        """Return the entities to be controlled based on HVAC MODE.

        Returns heater or cooler entities based on mode, plus any demand_switch
        entities which are controlled regardless of heat/cool mode.

        Delegates to HeaterController for the actual list.
        """
        return self._heater_controller.get_entities(self.hvac_mode)

    # ------------------------------------------------------------------
    # Heater control delegation
    # ------------------------------------------------------------------

    def _fire_heater_control_failed_event(
        self,
        entity_id: str,
        operation: str,
        error: str,
    ) -> None:
        """Fire an event when heater control fails.

        Delegates to HeaterController for the actual event firing.

        Args:
            entity_id: Entity that failed to control
            operation: Operation that failed (turn_on, turn_off, set_value)
            error: Error message
        """
        self._heater_controller._fire_heater_control_failed_event(entity_id, operation, error)

    async def _async_call_heater_service(
        self,
        entity_id: str,
        domain: str,
        service: str,
        data: dict,
    ) -> bool:
        """Call a heater/cooler service with error handling.

        Delegates to HeaterController for the actual service call.

        Args:
            entity_id: Entity ID being controlled
            domain: Service domain (homeassistant, light, valve, number, etc.)
            service: Service name (turn_on, turn_off, set_value, etc.)
            data: Service call data

        Returns:
            True if successful, False otherwise
        """
        result = await self._heater_controller._async_call_heater_service(entity_id, domain, service, data)
        # Sync failure state from controller
        self._heater_control_failed = self._heater_controller.heater_control_failed
        self._last_heater_error = self._heater_controller.last_heater_error
        return result

    @property
    def _effective_min_on_seconds(self) -> int:
        """Minimum open time including manifold transport delay."""
        base = self._min_open_time.seconds
        if self._transport_delay_minutes and self._transport_delay_minutes > 0:
            # C02: _transport_delay_minutes is in minutes; convert to seconds here.
            base += int(self._transport_delay_minutes * 60)
        return base

    async def _async_heater_turn_off(self, force=False, _effective_mode: HVACMode | None = None):
        """Turn heater toggleable device off.

        Delegates to HeaterController for the actual turn off operation.

        Args:
            force: Force turn off regardless of minimum cycle duration.
            _effective_mode: Override the HVAC mode used for device selection.  Pass the
                *pre-change* mode when calling during a mode transition so the correct
                heater/cooler entity is targeted (C01 fix).
        """
        if isinstance(getattr(self, "_pwm_coordination", None), PWMCoordinationRegistry):
            self._pwm_coordination.invalidate(self.entity_id)
        # Reset transport delay when heating stops
        if self._transport_delay_minutes is not None:
            self._pid_controller.reset_dead_time()
            self._transport_delay_minutes = None
            _LOGGER.debug("%s: Reset transport delay on heating stop", self.entity_id)

        # Update open/closed times in case PID mode changed
        self._heater_controller.update_open_closed_times(
            self._effective_min_on_seconds,
            self._min_closed_time.seconds,
        )
        await self._heater_controller.async_turn_off(
            hvac_mode=_effective_mode if _effective_mode is not None else self.hvac_mode,
            get_cycle_start_time=self._get_cycle_start_time,
            set_is_heating=self._set_is_heating,
            set_last_heat_cycle_time=self._set_last_heat_cycle_time,
            force=force,
        )
        if isinstance(getattr(self, "_pwm_coordination", None), PWMCoordinationRegistry):
            await self._pwm_coordination.refresh(self.entity_id)

    async def _async_set_valve_value(self, value: float):
        """Set valve value for non-PWM devices.

        Delegates to HeaterController for the actual valve control.
        """
        await self._heater_controller.async_set_valve_value(value, self.hvac_mode)

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode.
        This method must be run in the event loop and returns a coroutine.
        """
        await self._temperature_manager.async_set_preset_mode(preset_mode)
        # Sync internal state for backward compatibility
        self._attr_preset_mode = self._temperature_manager.preset_mode
        self._saved_target_temp = self._temperature_manager.saved_target_temp

    # ------------------------------------------------------------------
    # Pause counters (reported via sensor.py)
    # ------------------------------------------------------------------

    @property
    def humidity_pause_count(self) -> int:
        """Number of humidity pauses this reporting period."""
        return self._humidity_pause_count

    @property
    def contact_pause_count(self) -> int:
        """Number of contact sensor pauses this reporting period."""
        return self._contact_pause_count

    def reset_pause_counters(self) -> None:
        """Reset weekly pause counters (called after report generation)."""
        self._humidity_pause_count = 0
        self._contact_pause_count = 0
