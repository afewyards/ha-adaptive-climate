"""Control output manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Optional, Tuple

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode
    from ..climate import AdaptiveThermostat
    from ..pid_controller import PIDController
    from .heater_controller import HeaterController

_LOGGER = logging.getLogger(__name__)


class ControlOutputManager:
    """Manager for PID control output calculation and heater control delegation.

    This class encapsulates the PID output calculation logic and provides
    a clean interface for setting control values on heaters/coolers.
    """

    def __init__(
        self,
        thermostat: AdaptiveThermostat,
        pid_controller: PIDController,
        heater_controller: HeaterController | None,
        *,
        get_current_temp: Callable[[], float | None],
        get_ext_temp: Callable[[], float | None],
        get_wind_speed: Callable[[], float | None],
        get_previous_temp_time: Callable[[], float | None],
        set_previous_temp_time: Callable[[float], None],
        get_cur_temp_time: Callable[[], float | None],
        set_cur_temp_time: Callable[[float], None],
        get_output_precision: Callable[[], int | None],
        calculate_night_setback_adjustment: Callable[[], Tuple[float, Any, Any]],
        set_control_output: Callable[[float], None],
        set_p: Callable[[float], None],
        set_i: Callable[[float], None],
        set_d: Callable[[float], None],
        set_e: Callable[[float], None],
        set_dt: Callable[[float], None],
        get_kp: Callable[[], float | None],
        get_ki: Callable[[], float | None],
        get_kd: Callable[[], float | None],
        get_ke: Callable[[], float | None],
    ):
        """Initialize the ControlOutputManager.

        Args:
            thermostat: Reference to the parent thermostat entity
            pid_controller: PID controller instance
            heater_controller: Heater controller instance (may be None initially)
            get_current_temp: Callback to get current temperature
            get_ext_temp: Callback to get external temperature
            get_wind_speed: Callback to get wind speed
            get_previous_temp_time: Callback to get previous temperature time
            set_previous_temp_time: Callback to set previous temperature time
            get_cur_temp_time: Callback to get current temperature time
            set_cur_temp_time: Callback to set current temperature time
            get_output_precision: Callback to get output precision
            calculate_night_setback_adjustment: Callback to calculate night setback
            set_control_output: Callback to set control output value
            set_p: Callback to set proportional component
            set_i: Callback to set integral component
            set_d: Callback to set derivative component
            set_e: Callback to set external component
            set_dt: Callback to set delta time
            get_kp: Callback to get Kp value
            get_ki: Callback to get Ki value
            get_kd: Callback to get Kd value
            get_ke: Callback to get Ke value
        """
        self._thermostat = thermostat
        self._pid_controller = pid_controller
        self._heater_controller = heater_controller

        # Getters/setters for thermostat state
        self._get_current_temp = get_current_temp
        self._get_ext_temp = get_ext_temp
        self._get_wind_speed = get_wind_speed
        self._get_previous_temp_time = get_previous_temp_time
        self._set_previous_temp_time = set_previous_temp_time
        self._get_cur_temp_time = get_cur_temp_time
        self._set_cur_temp_time = set_cur_temp_time
        self._get_output_precision = get_output_precision
        self._calculate_night_setback_adjustment = calculate_night_setback_adjustment
        self._set_control_output = set_control_output
        self._set_p = set_p
        self._set_i = set_i
        self._set_d = set_d
        self._set_e = set_e
        self._set_dt = set_dt
        self._get_kp = get_kp
        self._get_ki = get_ki
        self._get_kd = get_kd
        self._get_ke = get_ke

        # Store last calculated output for access
        self._last_control_output: float = 0

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
        entity_id = self._thermostat.entity_id

        # Ensure timing values are set
        previous_temp_time = self._get_previous_temp_time()
        cur_temp_time = self._get_cur_temp_time()

        if previous_temp_time is None:
            previous_temp_time = time.time()
            self._set_previous_temp_time(previous_temp_time)
        if cur_temp_time is None:
            cur_temp_time = time.time()
            self._set_cur_temp_time(cur_temp_time)

        # Only update cur_temp_time if this is a real temperature sensor update
        # This prevents artificial tiny dt values from external sensor, contact sensor, or periodic calls
        if is_temp_sensor_update:
            cur_temp_time = time.time()
            self._set_cur_temp_time(cur_temp_time)

        if previous_temp_time > cur_temp_time:
            previous_temp_time = cur_temp_time
            self._set_previous_temp_time(previous_temp_time)

        # Apply night setback adjustment if configured
        effective_target, _, _ = self._calculate_night_setback_adjustment()

        # Get current values
        current_temp = self._get_current_temp()
        ext_temp = self._get_ext_temp()
        wind_speed = self._get_wind_speed()
        output_precision = self._get_output_precision()

        # Calculate thermal coupling feedforward compensation
        feedforward = self._calculate_coupling_compensation()
        self._pid_controller.set_feedforward(feedforward)

        # Calculate PID output
        if self._pid_controller.sampling_period == 0:
            control_output, update = self._pid_controller.calc(
                current_temp,
                effective_target,
                cur_temp_time,
                previous_temp_time,
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

        # Get error and dt for logging
        error = self._pid_controller.error
        dt = self._pid_controller.dt
        self._set_dt(dt)

        if update:
            kp = self._get_kp() or 0
            ki = self._get_ki() or 0
            kd = self._get_kd() or 0
            ke = self._get_ke() or 0
            _LOGGER.debug(
                "%s: New PID control output: %s (error = %.2f, dt = %.2f, "
                "p=%.2f, i=%.2f, d=%.2f, e=%.2f) [Kp=%.4f, Ki=%.4f, Kd=%.2f, Ke=%.2f]",
                entity_id,
                str(control_output),
                error,
                dt,
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
                self._thermostat.entity_id,
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
                self._thermostat.entity_id,
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

    def _calculate_coupling_compensation(self) -> float:
        """Calculate thermal coupling compensation from active heating neighbors.

        Sums the predicted temperature rise from all actively heating zones
        based on learned coupling coefficients, then converts to power reduction.

        Also stores the compensation values (degC and power) for attribute exposure.

        Returns:
            Compensation in power % (0-100). Positive values reduce output.
        """
        from ..adaptive.thermal_coupling import graduated_confidence
        from ..const import MAX_COUPLING_COMPENSATION, DOMAIN

        # Disable in cooling mode (v1 - heating only)
        if self._thermostat._hvac_mode == "cool":
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Get coordinator
        coordinator = self._thermostat.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Get active zones (neighbors currently heating)
        active_zones = coordinator.get_active_zones(hvac_mode="heat")
        if not active_zones:
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Get coupling learner
        coupling_learner = coordinator.thermal_coupling_learner
        if not coupling_learner:
            self._last_coupling_compensation_degc = 0.0
            self._last_coupling_compensation_power = 0.0
            return 0.0

        # Get current zone temps
        zone_temps = coordinator.get_zone_temps()
        this_zone = self._thermostat.entity_id

        # Sum compensation from all active neighbors
        total_compensation_degc = 0.0

        for neighbor_id, neighbor_data in active_zones.items():
            # Skip self
            if neighbor_id == this_zone:
                continue

            # Get learned coefficient for this neighbor -> us
            coef = coupling_learner.get_coefficient(neighbor_id, this_zone)
            if not coef:
                continue

            # Apply graduated confidence scaling
            confidence_scale = graduated_confidence(coef.confidence)
            if confidence_scale == 0.0:
                continue

            # Calculate neighbor's temperature rise since heating started
            heating_start_temp = neighbor_data.get("heating_start_temp")
            if heating_start_temp is None:
                continue

            current_temp = zone_temps.get(neighbor_id)
            if current_temp is None:
                continue

            temp_rise = current_temp - heating_start_temp

            # Negative rise = neighbor cooling, no heating effect
            if temp_rise <= 0:
                continue

            # Calculate predicted rise in this zone: coefficient * confidence * neighbor_rise
            predicted_rise = coef.coefficient * confidence_scale * temp_rise
            total_compensation_degc += predicted_rise

        # Cap at max compensation for this heating type
        heating_type = getattr(self._thermostat, "_heating_type", "convector")
        max_compensation = MAX_COUPLING_COMPENSATION.get(heating_type, 1.5)
        total_compensation_degc = min(total_compensation_degc, max_compensation)

        # Convert to power using Kp
        kp = self._get_kp() or 100.0
        compensation_power = total_compensation_degc * kp

        # Store values for attribute exposure
        self._last_coupling_compensation_degc = total_compensation_degc
        self._last_coupling_compensation_power = compensation_power

        return compensation_power
