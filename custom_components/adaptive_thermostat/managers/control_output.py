"""Control output manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode
    from ..protocols import ThermostatState
    from ..pid_controller import PIDController
    from .heater_controller import HeaterController

_LOGGER = logging.getLogger(__name__)

# Maximum reasonable time delta between PID calculations (1 hour)
# Values larger than this indicate a system sleep/resume or clock jump
# and should be clamped to avoid integral windup or erratic behavior
MAX_REASONABLE_DT = 3600


class ControlOutputManager:
    """Manager for PID control output calculation and heater control delegation.

    This class encapsulates the PID output calculation logic and provides
    a clean interface for setting control values on heaters/coolers.
    """

    def __init__(
        self,
        thermostat_state: ThermostatState,
        pid_controller: PIDController,
        heater_controller: HeaterController | None,
        *,
        set_previous_temp_time: Callable[[float], None],
        set_cur_temp_time: Callable[[float], None],
        set_control_output: Callable[[float], None],
        set_p: Callable[[float], None],
        set_i: Callable[[float], None],
        set_d: Callable[[float], None],
        set_e: Callable[[float], None],
        set_dt: Callable[[float], None],
    ):
        """Initialize the ControlOutputManager.

        Args:
            thermostat_state: Reference to the thermostat state (implements ThermostatState Protocol)
            pid_controller: PID controller instance
            heater_controller: Heater controller instance (may be None initially)
            set_previous_temp_time: Callback to set previous temperature time
            set_cur_temp_time: Callback to set current temperature time
            set_control_output: Callback to set control output value
            set_p: Callback to set proportional component
            set_i: Callback to set integral component
            set_d: Callback to set derivative component
            set_e: Callback to set external component
            set_dt: Callback to set delta time
        """
        self._thermostat_state = thermostat_state
        self._pid_controller = pid_controller
        self._heater_controller = heater_controller

        # Setters for thermostat state (getters accessed via protocol)
        self._set_previous_temp_time = set_previous_temp_time
        self._set_cur_temp_time = set_cur_temp_time
        self._set_control_output = set_control_output
        self._set_p = set_p
        self._set_i = set_i
        self._set_d = set_d
        self._set_e = set_e
        self._set_dt = set_dt

        # Store last calculated output for access
        self._last_control_output: float = 0

        # Track when calc_output() was last called for accurate dt calculation
        # This fixes the stale dt bug where external sensor triggers used sensor-based timing
        self._last_pid_calc_time: float | None = None

        # Store coupling compensation values for attribute exposure
        self._last_coupling_compensation_degc: float = 0.0
        self._last_coupling_compensation_power: float = 0.0

    @property
    def coupling_compensation_degc(self) -> float:
        """Get the last calculated coupling compensation in degrees C."""
        return self._last_coupling_compensation_degc

    @property
    def coupling_compensation_power(self) -> float:
        """Get the last calculated coupling compensation in power %."""
        return self._last_coupling_compensation_power

    def update_heater_controller(self, heater_controller: HeaterController) -> None:
        """Update the heater controller reference.

        This is called after the heater controller is initialized.

        Args:
            heater_controller: The heater controller instance
        """
        self._heater_controller = heater_controller

    def reset_calc_timing(self) -> None:
        """Reset the PID calculation timing.

        This clears the _last_pid_calc_time so the next calc_output() call
        will treat itself as the first calculation (dt=0). This should be
        called when HVAC mode is turned OFF to avoid accumulating stale
        time deltas across off periods.
        """
        self._last_pid_calc_time = None

    async def calc_output(self, is_temp_sensor_update: bool = False) -> float:
        """Calculate PID control output.

        This method:
        1. Ensures temperature timing is properly set
        2. Applies night setback adjustment to get effective target
        3. Calls PID controller to calculate output
        4. Updates thermostat state with P, I, D, E components
        5. Logs the new output when changed

        Args:
            is_temp_sensor_update: True if called from temperature sensor update,
                                   False for external sensor, contact sensor, or periodic calls

        Returns:
            The calculated control output value
        """
        update = False
        entity_id = self._thermostat_state.entity_id
        current_time = time.monotonic()

        # Track actual elapsed time since last PID calculation
        # This fixes the stale dt bug where external triggers used sensor-based timing
        if self._last_pid_calc_time is not None:
            actual_dt = current_time - self._last_pid_calc_time

            # Sanity clamp for unreasonable dt values
            if actual_dt < 0:
                # Clock jumped backward - clamp to 0 to avoid negative integral contribution
                actual_dt = 0
            elif actual_dt > MAX_REASONABLE_DT:
                # Time jump too large (system sleep/resume, etc.) - clamp to 0
                _LOGGER.warning(
                    "%s: dt clamped from %.1fs to 0 (exceeds MAX_REASONABLE_DT=%ds)",
                    entity_id,
                    actual_dt,
                    MAX_REASONABLE_DT,
                )
                actual_dt = 0
        else:
            actual_dt = 0  # First calculation, no prior reference

        # Update last calc time for next iteration
        self._last_pid_calc_time = current_time

        # Ensure timing values are set
        previous_temp_time = self._thermostat_state._prev_temp_time
        cur_temp_time = self._thermostat_state._cur_temp_time

        if previous_temp_time is None:
            previous_temp_time = time.monotonic()
            self._set_previous_temp_time(previous_temp_time)
        if cur_temp_time is None:
            cur_temp_time = time.monotonic()
            self._set_cur_temp_time(cur_temp_time)

        # Only update cur_temp_time if this is a real temperature sensor update
        # This prevents artificial tiny dt values from external sensor, contact sensor, or periodic calls
        if is_temp_sensor_update:
            cur_temp_time = time.monotonic()
            self._set_cur_temp_time(cur_temp_time)

        if previous_temp_time > cur_temp_time:
            previous_temp_time = cur_temp_time
            self._set_previous_temp_time(previous_temp_time)

        # Calculate sensor_dt for comparison with actual_dt
        # This is the time between sensor updates (may be stale for external triggers)
        sensor_dt = cur_temp_time - previous_temp_time

        # Apply night setback adjustment if configured
        effective_target, _, _ = self._thermostat_state._calculate_night_setback_adjustment()

        # Get current values
        current_temp = self._thermostat_state._cur_temp
        ext_temp = self._thermostat_state._outdoor_temp
        wind_speed = self._thermostat_state._wind_speed
        output_precision = self._thermostat_state._output_precision

        # Calculate thermal coupling feedforward compensation
        feedforward = self._calculate_coupling_compensation()
        self._pid_controller.set_feedforward(feedforward)

        # Record heat output for thermal groups transfer history
        self._record_heat_output_for_thermal_groups()

        # Calculate PID output
        # For event-driven mode (sampling_period == 0), pass corrected timestamps
        # to ensure PID receives the actual elapsed time between calculations,
        # not the sensor-based interval which may be stale for external triggers
        if self._pid_controller.sampling_period == 0:
            # Calculate effective timestamps based on actual_dt
            # effective_input_time = current_time (when we're calculating now)
            # effective_previous_time = current_time - actual_dt (when last calc happened)
            effective_input_time = current_time
            effective_previous_time = current_time - actual_dt

            control_output, update = self._pid_controller.calc(
                current_temp,
                effective_target,
                effective_input_time,
                effective_previous_time,
                ext_temp,
                wind_speed,
            )
        else:
            control_output, update = self._pid_controller.calc(
                current_temp,
                effective_target,
                ext_temp=ext_temp,
                wind_speed=wind_speed,
            )

        # Update component values
        p = round(self._pid_controller.proportional, 1)
        i = round(self._pid_controller.integral, 1)
        d = round(self._pid_controller.derivative, 1)
        e = round(self._pid_controller.external, 1)

        self._set_p(p)
        self._set_i(i)
        self._set_d(d)
        self._set_e(e)

        # Round control output
        control_output = round(control_output, output_precision)
        if not output_precision:
            control_output = int(control_output)

        self._set_control_output(control_output)
        self._last_control_output = control_output

        # Get error for logging; use actual_dt (not PID's dt) for state attribute
        # This ensures the pid_dt attribute reflects actual calc interval, not sensor interval
        error = self._pid_controller.error
        self._set_dt(actual_dt)

        # Warn when actual_dt differs significantly from sensor_dt (ratio > 10x)
        # This indicates the thermostat is being triggered by non-temperature events
        if actual_dt > 0 and sensor_dt > 0:
            dt_ratio = actual_dt / sensor_dt if actual_dt > sensor_dt else sensor_dt / actual_dt
            if dt_ratio > 10:
                _LOGGER.warning(
                    "%s: Large dt discrepancy - actual_dt=%.2f, sensor_dt=%.2f (ratio %.1fx). "
                    "External trigger timing differs significantly from sensor updates.",
                    entity_id,
                    actual_dt,
                    sensor_dt,
                    dt_ratio,
                )

        if update:
            kp = self._thermostat_state._kp or 0
            ki = self._thermostat_state._ki or 0
            kd = self._thermostat_state._kd or 0
            ke = self._thermostat_state._ke or 0
            _LOGGER.debug(
                "%s: New PID control output: %s (error = %.2f, actual_dt = %.2f, sensor_dt = %.2f, "
                "p=%.2f, i=%.2f, d=%.2f, e=%.2f) [Kp=%.4f, Ki=%.4f, Kd=%.2f, Ke=%.2f]",
                entity_id,
                str(control_output),
                error,
                actual_dt,
                sensor_dt,
                p,
                i,
                d,
                e,
                kp,
                ki,
                kd,
                ke,
            )

        return control_output

    async def set_control_value(
        self,
        control_output: float,
        hvac_mode: HVACMode,
        *,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        time_changed: float,
        set_time_changed: Callable[[float], None],
        force_on: bool,
        force_off: bool,
        set_force_on: Callable[[bool], None],
        set_force_off: Callable[[bool], None],
        min_on_cycle_duration_seconds: float,
        min_off_cycle_duration_seconds: float,
    ) -> None:
        """Set output value for heater by delegating to HeaterController.

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
            min_on_cycle_duration_seconds: Minimum on cycle duration in seconds
            min_off_cycle_duration_seconds: Minimum off cycle duration in seconds
        """
        if self._heater_controller is None:
            _LOGGER.warning(
                "%s: HeaterController not initialized, cannot set control value",
                self._thermostat_state.entity_id,
            )
            return

        # Update cycle durations in case PID mode changed
        self._heater_controller.update_cycle_durations(
            min_on_cycle_duration_seconds,
            min_off_cycle_duration_seconds,
        )

        await self._heater_controller.async_set_control_value(
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

    async def pwm_switch(
        self,
        control_output: float,
        hvac_mode: HVACMode,
        *,
        get_cycle_start_time: Callable[[], float],
        set_is_heating: Callable[[bool], None],
        set_last_heat_cycle_time: Callable[[float], None],
        time_changed: float,
        set_time_changed: Callable[[float], None],
        force_on: bool,
        force_off: bool,
        set_force_on: Callable[[bool], None],
        set_force_off: Callable[[bool], None],
        min_on_cycle_duration_seconds: float,
        min_off_cycle_duration_seconds: float,
    ) -> None:
        """Turn off and on the heater proportionally to control_value.

        Delegates to HeaterController for PWM switching logic.

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
            min_on_cycle_duration_seconds: Minimum on cycle duration in seconds
            min_off_cycle_duration_seconds: Minimum off cycle duration in seconds
        """
        if self._heater_controller is None:
            _LOGGER.warning(
                "%s: HeaterController not initialized, cannot perform PWM switch",
                self._thermostat_state.entity_id,
            )
            return

        # Update cycle durations in case PID mode changed
        self._heater_controller.update_cycle_durations(
            min_on_cycle_duration_seconds,
            min_off_cycle_duration_seconds,
        )

        await self._heater_controller.async_pwm_switch(
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

    def _record_heat_output_for_thermal_groups(self) -> None:
        """Record current heat output for thermal groups transfer history."""
        from ..const import DOMAIN

        # Get coordinator
        coordinator = self._thermostat_state._coordinator
        if not coordinator:
            return

        # Get thermal group manager
        thermal_group_manager = coordinator.thermal_group_manager
        if not thermal_group_manager:
            return

        # Get zone ID
        zone_id = self._thermostat_state._zone_id
        if not zone_id:
            return

        # Only record when actively heating
        if self._thermostat_state._hvac_mode != "heat":
            return

        # Get current control output (heat output percentage)
        control_output = self._thermostat_state._control_output

        # Record heat output
        thermal_group_manager.record_heat_output(zone_id, control_output)

    def _calculate_coupling_compensation(self) -> float:
        """Calculate thermal coupling compensation using thermal groups.

        Uses static thermal group configuration for cross-group heat transfer
        feedforward based on delayed history and transfer factors.

        Also stores the compensation values (degC and power) for attribute exposure.

        Returns:
            Compensation in power % (0-100). Positive values reduce output.
        """
        from ..const import DOMAIN

        # Disable in cooling mode
        if self._thermostat_state._hvac_mode == "cool":
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Get coordinator
        coordinator = self._thermostat_state._coordinator
        if not coordinator:
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Get thermal group manager
        thermal_group_manager = coordinator.thermal_group_manager
        if not thermal_group_manager:
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Get zone ID
        zone_id = self._thermostat_state._zone_id
        if not zone_id:
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Calculate feedforward from thermal groups
        feedforward_power = thermal_group_manager.calculate_feedforward(zone_id)

        # Store values for attribute exposure (no degC conversion for static config)
        self._last_coupling_compensation_degc = 0.0
        self._last_coupling_compensation_power = feedforward_power

        return feedforward_power
