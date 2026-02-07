"""PWM (Pulse Width Modulation) controller for Adaptive Climate integration.

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
    When PID output is too small to sustain min_open_time, the controller
    accumulates "duty credit" over time until enough accumulates to fire a minimum pulse.
    """

    def __init__(
        self,
        thermostat: AdaptiveThermostat,
        pwm_duration: int,
        difference: float,
        min_open_time: float,
        min_closed_time: float,
        valve_actuation_time: float = 0.0,
    ):
        """Initialize the PWMController.

        Args:
            thermostat: Reference to the parent thermostat entity
            pwm_duration: PWM period in seconds
            difference: Output range (max - min)
            min_open_time: Minimum open time in seconds
            min_closed_time: Minimum closed time in seconds
            valve_actuation_time: Valve actuation time in seconds (default: 0.0)
        """
        self._thermostat = thermostat
        self._pwm = pwm_duration
        self._difference = difference
        self._min_open_time = min_open_time
        self._min_closed_time = min_closed_time
        self._valve_actuation_time = valve_actuation_time
        self._transport_delay: float = 0.0

        # Duty accumulator for sub-threshold outputs
        self._duty_accumulator_seconds: float = 0.0
        self._last_accumulator_calc_time: float | None = None

    def update_open_closed_times(
        self,
        min_open_time: float,
        min_closed_time: float,
    ) -> None:
        """Update the minimum open/closed times.

        This is used when the PID mode changes, as different modes
        may have different minimum cycle requirements.

        Args:
            min_open_time: Minimum open time in seconds
            min_closed_time: Minimum closed time in seconds
        """
        self._min_open_time = min_open_time
        self._min_closed_time = min_closed_time

    def set_transport_delay(self, delay_seconds: float) -> None:
        """Set the manifold transport delay.

        Args:
            delay_seconds: Transport delay in seconds (0 if manifold warm)
        """
        self._transport_delay = delay_seconds

    @property
    def _max_accumulator(self) -> float:
        """Return maximum accumulator value (2x min_open_time)."""
        return 2.0 * self._min_open_time

    @property
    def duty_accumulator_seconds(self) -> float:
        """Return the current duty accumulator value in seconds."""
        return self._duty_accumulator_seconds

    @property
    def min_open_time(self) -> float:
        """Return the minimum open time in seconds."""
        return self._min_open_time

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

    def _calculate_heat_duration(
        self,
        control_output: float,
        difference: float,
    ) -> float:
        """Calculate raw heat delivery duration from PID output.

        This is the heat delivery time without any valve actuation or minimum
        cycle adjustments. Used for accumulation logic.

        Args:
            control_output: Current PID control output
            difference: Output range (max - min)

        Returns:
            Raw heat duration in seconds
        """
        if difference == 0 or control_output == 0:
            return 0.0

        duty = control_output / difference
        return self._pwm * duty

    def calculate_adjusted_on_time(
        self,
        control_output: float,
        difference: float,
    ) -> float:
        """Calculate valve-on duration accounting for actuation and transport delays.

        For valves with actuation time and manifold transport delay, heat doesn't
        arrive until pipes fill and valve opens. The total on-time is:
        - transport_delay: time for hot water to reach zone (0 if warm)
        - actuator_time: time for valve to fully open
        - heat_duration: actual heat delivery time (≥ min_open_time)

        Args:
            control_output: Current PID control output
            difference: Output range (max - min)

        Returns:
            Adjusted on-time in seconds
        """
        heat_duration = self._calculate_heat_duration(control_output, difference)
        if heat_duration == 0:
            return 0.0

        # Total on-time = transport delay + valve open time + max(heat_duration, min_open_time)
        # This ensures heat arrives and valve is fully open before heat delivery begins
        return (
            self._transport_delay
            + self._valve_actuation_time
            + max(
                heat_duration,
                self._min_open_time,
            )
        )

    def get_close_command_offset(self) -> float:
        """Get offset in seconds to send close command early.

        Returns half the valve actuation time, since the valve will continue
        delivering heat while it closes.

        Returns:
            Offset in seconds
        """
        return self._valve_actuation_time / 2

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
        # Use calculate_adjusted_on_time to account for valve actuation delay
        time_on = self.calculate_adjusted_on_time(
            control_output=abs(control_output),
            difference=self._difference,
        )
        time_off = self._pwm - time_on

        # For accumulation check, use raw heat duration (not adjusted on-time)
        heat_duration = self._calculate_heat_duration(
            control_output=abs(control_output),
            difference=self._difference,
        )

        # If calculated heat duration < min_open_time, accumulate duty
        if 0 < heat_duration < self._min_open_time:
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
            if self._duty_accumulator_seconds >= self._min_open_time:
                # Safety check: don't fire if heating would be counterproductive
                # (This can happen after restart when PID integral keeps output positive
                # even though temperature is already above setpoint)
                current_temp = getattr(self._thermostat, "_current_temp", None)
                target_temp = getattr(self._thermostat, "_target_temp", None)
                if isinstance(current_temp, (int, float)) and isinstance(target_temp, (int, float)):
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
                    self._min_open_time,
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
                self._duty_accumulator_seconds -= self._min_open_time

                set_force_on(False)
                set_force_off(False)
                return

            # Below threshold - accumulate duty scaled by actual elapsed time
            # heat_duration is for the full PWM period; scale by actual interval
            current_time = time.monotonic()
            if self._last_accumulator_calc_time is not None:
                actual_dt = current_time - self._last_accumulator_calc_time
                # duty_to_add = actual_dt * (heat_duration / pwm) = actual_dt * duty_fraction
                duty_to_add = actual_dt * heat_duration / self._pwm
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
                self._min_open_time,
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

        if 0 < time_off < self._min_closed_time:
            # time_off is too short, increase time_on and time_off
            time_on *= self._min_closed_time / time_off
            time_off = self._min_closed_time

        is_device_active = heater_controller.is_active(hvac_mode)

        # Calculate when to send the close command (earlier by half valve actuation time)
        close_command_offset = self.get_close_command_offset()
        time_to_close = time_on - close_command_offset

        if is_device_active:
            if time_to_close <= time_passed or force_off:
                _LOGGER.info("%s: ON time passed. Request turning OFF %s", thermostat_entity_id, ", ".join(entities))
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
                    int(time_to_close - time_passed),
                )
                await heater_controller.async_turn_on(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )
        else:
            if time_off <= time_passed or force_on:
                _LOGGER.info("%s: OFF time passed. Request turning ON %s", thermostat_entity_id, ", ".join(entities))
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
                    int(time_off - time_passed),
                )
                await heater_controller.async_turn_off(
                    hvac_mode=hvac_mode,
                    get_cycle_start_time=get_cycle_start_time,
                    set_is_heating=set_is_heating,
                    set_last_heat_cycle_time=set_last_heat_cycle_time,
                )

        set_force_on(False)
        set_force_off(False)
