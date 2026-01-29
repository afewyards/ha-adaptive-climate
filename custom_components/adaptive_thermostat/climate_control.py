"""Control loop logic for adaptive thermostat.

This module contains the main heating control loop and PID output calculation.
"""

import logging
import time

from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from .managers.events import TemperatureUpdateEvent

_LOGGER = logging.getLogger(__name__)


class ClimateControlMixin:
    """Mixin providing control loop functionality for AdaptiveThermostat.

    This mixin encapsulates the core heating/cooling control logic including
    PID calculation, pause management, and zone demand coordination.
    """

    async def _async_control_heating(
            self, time_func: object = None, calc_pid: object = False, is_temp_sensor_update: bool = False) -> object:
        """Run PID controller, optional autotune for faster integration"""
        async with self._temp_lock:
            if not self._active and None not in (self._current_temp, self._target_temp):
                self._active = True
                _LOGGER.info("%s: Obtained temperature %s with set point %s. Activating Smart"
                             "Thermostat.", self.entity_id, self._current_temp, self._target_temp)

            if not self._active or self._hvac_mode == HVACMode.OFF:
                if self._force_off_state and self._hvac_mode == HVACMode.OFF and \
                        self._is_device_active:
                    _LOGGER.debug("%s: %s is active while HVAC mode is %s. Turning it OFF.",
                                  self.entity_id, ", ".join([entity for entity in self.heater_or_cooler_entity]), self._hvac_mode)
                    if self._pwm:
                        await self._async_heater_turn_off(force=True)
                    else:
                        self._control_output = self._output_min
                        await self._async_set_valve_value(self._control_output)
                # Update zone demand to False when OFF/inactive
                if self._zone_id:
                    coordinator = self.hass.data.get("adaptive_thermostat", {}).get("coordinator")
                    if coordinator:
                        coordinator.update_zone_demand(self._zone_id, False, self._hvac_mode.value if self._hvac_mode else None)
                self.async_write_ha_state()
                return

            # Cache coordinator lookup for hot path optimization
            coordinator = self.hass.data.get("adaptive_thermostat", {}).get("coordinator") if self._zone_id else None

            # Unified pause check (contact sensors, humidity detection, etc.)
            if self._status_manager.is_paused():
                _LOGGER.info("%s: Heating paused", self.entity_id)

                # Decay integral for humidity pauses (~10%/min to prevent stale buildup)
                if self._humidity_detector and self._humidity_detector.should_pause():
                    elapsed = time.monotonic() - self._last_control_time
                    decay_factor = 0.9 ** (elapsed / 60)  # 10% decay per minute
                    self._pid_controller.decay_integral(decay_factor)

                # Turn off heating
                if self._pwm:
                    await self._async_heater_turn_off(force=True)
                else:
                    self._control_output = self._output_min
                    await self._async_set_valve_value(self._control_output)

                # Update zone demand to False when paused
                if coordinator:
                    coordinator.update_zone_demand(self._zone_id, False, self._hvac_mode.value if self._hvac_mode else None)

                self.async_write_ha_state()
                return

            if self._sensor_stall != 0 and time.monotonic() - self._last_sensor_update > \
                    self._sensor_stall:
                # sensor not updated for too long, considered as stall, set to safety level
                self._control_output = self._output_safety
            else:
                # Always recalculate PID to ensure output reflects current conditions
                await self.calc_output(is_temp_sensor_update)

                # Dispatch TemperatureUpdateEvent after PID calculation
                if self._cycle_dispatcher and self._current_temp is not None and self._target_temp is not None:
                    self._cycle_dispatcher.emit(
                        TemperatureUpdateEvent(
                            timestamp=dt_util.utcnow(),
                            temperature=self._current_temp,
                            setpoint=self._target_temp,
                            pid_integral=self._pid_controller.integral,
                            pid_error=self._pid_controller.error,
                        )
                    )

                # Update undershoot detector and check for Ki adjustment
                if (
                    self._hvac_mode == HVACMode.HEAT
                    and coordinator
                    and self._zone_id
                    and self._current_temp is not None
                    and self._target_temp is not None
                ):
                    zone_data = coordinator.get_zone_data(self._zone_id)
                    if zone_data:
                        adaptive_learner = zone_data.get("adaptive_learner")
                        if adaptive_learner:
                            # Calculate dt since last control update
                            current_time = time.monotonic()
                            dt_seconds = current_time - self._last_control_time if self._last_control_time > 0 else 0.0

                            # Update undershoot detector
                            adaptive_learner.update_undershoot_detector(
                                self._current_temp,
                                self._target_temp,
                                dt_seconds,
                                self._cold_tolerance,
                            )

                            # Check if Ki adjustment is needed
                            cycles_completed = adaptive_learner.get_cycle_count()
                            new_ki = adaptive_learner.check_undershoot_adjustment(cycles_completed)

                            if new_ki is not None:
                                # Scale integral to prevent output spike
                                old_ki = self._pid_controller.ki
                                if old_ki > 0:
                                    scale_factor = old_ki / new_ki
                                    self._pid_controller.scale_integral(scale_factor)
                                    _LOGGER.info(
                                        "%s: Scaled integral by %.3f to prevent output spike (Ki: %.5f -> %.5f)",
                                        self.entity_id,
                                        scale_factor,
                                        old_ki,
                                        new_ki,
                                    )

                                # Update PID controller Ki
                                self._pid_controller.ki = new_ki
                                self._ki = new_ki

                                # Trigger state save
                                self.async_write_ha_state()

                                _LOGGER.info(
                                    "%s: Undershoot detector adjusted Ki: %.5f -> %.5f",
                                    self.entity_id,
                                    old_ki,
                                    new_ki,
                                )

                # Record temperature for cycle tracking
                if self._cycle_tracker and self._current_temp is not None:
                    await self._cycle_tracker.update_temperature(dt_util.utcnow(), self._current_temp)
            await self.set_control_value()

            # Update zone demand for CentralController (based on actual device state, not PID output)
            if coordinator:
                coordinator.update_zone_demand(self._zone_id, self._is_device_active, self._hvac_mode.value if self._hvac_mode else None)

            # Record Ke observation if at steady state
            self._maybe_record_ke_observation()

            # Update control time for humidity integral decay calculation
            self._last_control_time = time.monotonic()

            self.async_write_ha_state()

    @property
    def _is_device_active(self) -> bool:
        """Check if the toggleable/valve device is currently active.

        Delegates to HeaterController for the actual check.

        Returns:
            True if device is active, False if no heater controller or device is inactive.
        """
        if self._heater_controller is None:
            return False
        return self._heater_controller.is_active(self.hvac_mode)

    def _get_cycle_start_time(self) -> float:
        """Get the time when the current heating/cooling cycle started.

        Returns our tracked time if available. If not yet tracked (startup),
        returns 0 to allow immediate action since we have no reliable data
        about when the cycle actually started (HA's last_changed reflects
        restart time, not actual device state change time).
        """
        if self._last_heat_cycle_time is not None:
            return self._last_heat_cycle_time

        # No tracked time yet - allow immediate action on first cycle after startup
        return 0

    async def calc_output(self, is_temp_sensor_update: bool = False):
        """Calculate PID control output.

        Delegates to ControlOutputManager for the actual calculation.

        Args:
            is_temp_sensor_update: True if called from temperature sensor update
        """
        await self._control_output_manager.calc_output(is_temp_sensor_update)

    async def set_control_value(self):
        """Set output value for heater.

        Delegates to HeaterController for the actual control operation.
        """
        if self._heater_controller is None:
            _LOGGER.warning(
                "%s: HeaterController not initialized, cannot set control value",
                self.entity_id,
            )
            return

        # Update cycle durations in case PID mode changed
        self._heater_controller.update_cycle_durations(
            self._effective_min_on_seconds,
            self._min_off_cycle_duration.seconds,
        )
        await self._heater_controller.async_set_control_value(
            control_output=self._control_output,
            hvac_mode=self.hvac_mode,
            get_cycle_start_time=self._get_cycle_start_time,
            set_is_heating=self._set_is_heating,
            set_last_heat_cycle_time=self._set_last_heat_cycle_time,
            time_changed=self._time_changed,
            set_time_changed=self._set_time_changed,
            force_on=self._force_on,
            force_off=self._force_off,
            set_force_on=self._set_force_on,
            set_force_off=self._set_force_off,
        )

    async def pwm_switch(self):
        """Turn off and on the heater proportionally to control_value.

        Delegates to HeaterController for the PWM switching operation.
        """
        if self._heater_controller is None:
            _LOGGER.warning(
                "%s: HeaterController not initialized, cannot perform PWM switch",
                self.entity_id,
            )
            return

        # Update cycle durations in case PID mode changed
        self._heater_controller.update_cycle_durations(
            self._effective_min_on_seconds,
            self._min_off_cycle_duration.seconds,
        )
        await self._heater_controller.async_pwm_switch(
            control_output=self._control_output,
            hvac_mode=self.hvac_mode,
            get_cycle_start_time=self._get_cycle_start_time,
            set_is_heating=self._set_is_heating,
            set_last_heat_cycle_time=self._set_last_heat_cycle_time,
            time_changed=self._time_changed,
            set_time_changed=self._set_time_changed,
            force_on=self._force_on,
            force_off=self._force_off,
            set_force_on=self._set_force_on,
            set_force_off=self._set_force_off,
        )
