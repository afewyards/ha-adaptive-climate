"""PWM (Pulse Width Modulation) controller for Adaptive Thermostat integration.

Manages duty accumulation for sub-threshold PID outputs and PWM switching logic.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.components.climate import HVACMode
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HVACMode = Any

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


class PWMController:
    """Controller for PWM (Pulse Width Modulation) operations.

    Handles duty accumulation for sub-threshold PID outputs and PWM switching.
    When PID output is too small to sustain min_on_cycle_duration, the controller
    accumulates "duty credit" over time until enough accumulates to fire a minimum pulse.
    """

    def __init__(
        self,
        thermostat: AdaptiveThermostat,
        pwm_duration: int,
        difference: float,
        min_on_cycle_duration: float,
        min_off_cycle_duration: float,
    ):
        """Initialize the PWMController.

        Args:
            thermostat: Reference to the parent thermostat entity
            pwm_duration: PWM period in seconds
            difference: Output range (max - min)
            min_on_cycle_duration: Minimum on cycle duration in seconds
            min_off_cycle_duration: Minimum off cycle duration in seconds
        """
        self._thermostat = thermostat
        self._pwm = pwm_duration
        self._difference = difference
        self._min_on_cycle_duration = min_on_cycle_duration
        self._min_off_cycle_duration = min_off_cycle_duration

        # Duty accumulator for sub-threshold outputs
        self._duty_accumulator_seconds: float = 0.0
        self._last_accumulator_calc_time: float | None = None

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
    def _max_accumulator(self) -> float:
        """Return maximum accumulator value (2x min_on_cycle_duration)."""
        return 2.0 * self._min_on_cycle_duration

    @property
    def duty_accumulator_seconds(self) -> float:
        """Return the current duty accumulator value in seconds."""
        return self._duty_accumulator_seconds

    @property
    def min_on_cycle_duration(self) -> float:
        """Return the minimum on cycle duration in seconds."""
        return self._min_on_cycle_duration

    def set_duty_accumulator(self, seconds: float) -> None:
        """Set the duty accumulator value (used during state restoration).

        Args:
            seconds: Accumulator value in seconds
        """
        self._duty_accumulator_seconds = min(seconds, self._max_accumulator)

    def reset_duty_accumulator(self) -> None:
        """Reset duty accumulator to zero.

        Called when:
        - Setpoint changes significantly (>0.5°C)
        - HVAC mode changes to OFF
        - Contact sensor opens (window/door)
        """
        self._duty_accumulator_seconds = 0.0
        self._last_accumulator_calc_time = None

    async def async_pwm_switch(
        self,
        control_output: float,
        hvac_mode: HVACMode,
        heater_controller,  # Import cycle prevention - pass controller
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
            heater_controller: HeaterController instance for device operations
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
        entities = heater_controller.get_entities(hvac_mode)
        thermostat_entity_id = self._thermostat.entity_id

        time_passed = time.monotonic() - time_changed

        # Handle zero/negative output - reset accumulator and turn off
        if control_output <= 0:
            self._duty_accumulator_seconds = 0.0
            self._last_accumulator_calc_time = None
            await heater_controller.async_turn_off(
                hvac_mode=hvac_mode,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
            )
            set_force_on(False)
            set_force_off(False)
            return

        # Compute time_on based on PWM duration and PID output
        time_on = self._pwm * abs(control_output) / self._difference
        time_off = self._pwm - time_on

        # If calculated on-time < min_on_cycle_duration, accumulate duty
        if 0 < time_on < self._min_on_cycle_duration:
            # If heater is already ON (e.g., during minimum pulse), don't accumulate
            # but DO try to turn off (respects min_cycle protection internally)
            if heater_controller.is_active(hvac_mode):
                _LOGGER.debug(
                    "%s: Sub-threshold output but heater already ON - attempting turn off",
                    thermostat_entity_id,
                )
                # Reset accumulator to prevent immediate re-firing after turn-off
                self._duty_accumulator_seconds = 0.0
                self._last_accumulator_calc_time = None
                await heater_controller.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
                set_force_on(False)
                set_force_off(False)
                return

            # Check if accumulator has already reached threshold to fire minimum pulse
            if self._duty_accumulator_seconds >= self._min_on_cycle_duration:
                # Safety check: don't fire if heating would be counterproductive
                # (This can happen after restart when PID integral keeps output positive
                # even though temperature is already above setpoint)
                current_temp = getattr(self._thermostat, '_current_temp', None)
                target_temp = getattr(self._thermostat, '_target_temp', None)
                if (isinstance(current_temp, (int, float)) and
                    isinstance(target_temp, (int, float))):
                    if hvac_mode == HVACMode.HEAT and current_temp >= target_temp:
                        _LOGGER.info(
                            "%s: Accumulator threshold reached but skipping pulse - "
                            "temp %.2f°C already at/above target %.2f°C. Resetting accumulator.",
                            thermostat_entity_id,
                            current_temp,
                            target_temp,
                        )
                        self._duty_accumulator_seconds = 0.0
                        self._last_accumulator_calc_time = None
                        await heater_controller.async_turn_off(
                            hvac_mode=hvac_mode,
                            get_cycle_start_time=get_cycle_start_time,
                            set_is_heating=set_is_heating,
                            set_last_heat_cycle_time=set_last_heat_cycle_time,
                        )
                        set_force_on(False)
                        set_force_off(False)
                        return
                    elif hvac_mode == HVACMode.COOL and current_temp <= target_temp:
                        _LOGGER.info(
                            "%s: Accumulator threshold reached but skipping pulse - "
                            "temp %.2f°C already at/below target %.2f°C. Resetting accumulator.",
                            thermostat_entity_id,
                            current_temp,
                            target_temp,
                        )
                        self._duty_accumulator_seconds = 0.0
                        self._last_accumulator_calc_time = None
                        await heater_controller.async_turn_off(
                            hvac_mode=hvac_mode,
                            get_cycle_start_time=get_cycle_start_time,
                            set_is_heating=set_is_heating,
                            set_last_heat_cycle_time=set_last_heat_cycle_time,
                        )
                        set_force_on(False)
                        set_force_off(False)
                        return

                _LOGGER.info(
                    "%s: Accumulator threshold reached (%.0fs >= %.0fs). Firing minimum pulse.",
                    thermostat_entity_id,
                    self._duty_accumulator_seconds,
                    self._min_on_cycle_duration,
                )
                # Set demand state before turning on
                heater_controller._has_demand = True

                # Fire minimum pulse
                await heater_controller.async_turn_on(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )

                # Update time tracking
                set_time_changed(time.monotonic())

                # Subtract minimum pulse duration from accumulator
                self._duty_accumulator_seconds -= self._min_on_cycle_duration

                set_force_on(False)
                set_force_off(False)
                return

            # Below threshold - accumulate duty scaled by actual elapsed time
            # time_on is for the full PWM period; scale by actual interval
            current_time = time.monotonic()
            if self._last_accumulator_calc_time is not None:
                actual_dt = current_time - self._last_accumulator_calc_time
                # duty_to_add = actual_dt * (time_on / pwm) = actual_dt * duty_fraction
                duty_to_add = actual_dt * time_on / self._pwm
            else:
                # First calculation - don't accumulate, just set baseline
                duty_to_add = 0.0
            self._last_accumulator_calc_time = current_time

            self._duty_accumulator_seconds = min(
                self._duty_accumulator_seconds + duty_to_add,
                self._max_accumulator,
            )
            _LOGGER.debug(
                "%s: Sub-threshold output - accumulated %.1fs (total: %.0fs / %.0fs)",
                thermostat_entity_id,
                duty_to_add,
                self._duty_accumulator_seconds,
                self._min_on_cycle_duration,
            )
            await heater_controller.async_turn_off(
                hvac_mode=hvac_mode,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
            )
            set_force_on(False)
            set_force_off(False)
            return

        # Normal duty threshold met - reset accumulator
        self._duty_accumulator_seconds = 0.0
        self._last_accumulator_calc_time = None

        if 0 < time_off < self._min_off_cycle_duration:
            # time_off is too short, increase time_on and time_off
            time_on *= self._min_off_cycle_duration / time_off
            time_off = self._min_off_cycle_duration

        is_device_active = heater_controller.is_active(hvac_mode)

        if is_device_active:
            if time_on <= time_passed or force_off:
                _LOGGER.info(
                    "%s: ON time passed. Request turning OFF %s",
                    thermostat_entity_id,
                    ", ".join(entities)
                )
                await heater_controller.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
                set_time_changed(time.monotonic())
            else:
                _LOGGER.info(
                    "%s: Time until %s turns OFF: %s sec",
                    thermostat_entity_id,
                    ", ".join(entities),
                    int(time_on - time_passed)
                )
                await heater_controller.async_turn_on(
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
                await heater_controller.async_turn_on(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
                set_time_changed(time.monotonic())
            else:
                _LOGGER.info(
                    "%s: Time until %s turns ON: %s sec",
                    thermostat_entity_id,
                    ", ".join(entities),
                    int(time_off - time_passed)
                )
                await heater_controller.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )

        set_force_on(False)
        set_force_off(False)
