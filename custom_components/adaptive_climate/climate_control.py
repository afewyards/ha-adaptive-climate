"""Control loop logic for adaptive thermostat.

This module contains the main heating control loop and PID output calculation.
"""

from __future__ import annotations

import logging
import time

from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from .const import DOMAIN, UNDERSHOOT_SATURATION_FRACTION, PIDChangeReason
from .managers.events import TemperatureUpdateEvent
from .managers.pid_tuning import calculate_physics_gains

_LOGGER = logging.getLogger(__name__)


class ClimateControlMixin:
    """Mixin providing control loop functionality for AdaptiveThermostat.

    This mixin encapsulates the core heating/cooling control logic including
    PID calculation, pause management, and zone demand coordination.
    """

    @property
    def _output_saturated(self) -> bool:
        """Heating output at or above 95% of its maximum: more Ki cannot raise it."""
        return (
            self._hvac_mode == HVACMode.HEAT
            and self._control_output >= UNDERSHOOT_SATURATION_FRACTION * self._pid_controller.out_max
        )

    async def _async_control_heating(
        self, time_func: object = None, calc_pid: object = False, is_temp_sensor_update: bool = False
    ) -> object:
        """Run PID controller, optional autotune for faster integration"""
        async with self._temp_lock:
            if not self._active and None not in (self._current_temp, self._target_temp):
                self._active = True
                _LOGGER.info(
                    "%s: Obtained temperature %s with set point %s. Activating SmartThermostat.",
                    self.entity_id,
                    self._current_temp,
                    self._target_temp,
                )

            if not self._active or self._hvac_mode == HVACMode.OFF:
                if self._force_off_state and self._hvac_mode == HVACMode.OFF and self._is_device_active:
                    _LOGGER.debug(
                        "%s: %s is active while HVAC mode is %s. Turning it OFF.",
                        self.entity_id,
                        ", ".join([entity for entity in self.heater_or_cooler_entity]),
                        self._hvac_mode,
                    )
                    if self._pwm:
                        await self._async_heater_turn_off(force=True)
                    else:
                        self._control_output = self._output_min
                        await self._async_set_valve_value(self._control_output)
                # Update zone demand to False when OFF/inactive
                if self._zone_id:
                    coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
                    if coordinator:
                        coordinator.update_zone_demand(
                            self._zone_id, False, self._hvac_mode.value if self._hvac_mode else None
                        )
                self.async_write_ha_state()
                return

            # Cache coordinator lookup for hot path optimization
            coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator") if self._zone_id else None

            # Unified pause check (contact sensors, humidity detection, etc.)
            hvac_mode_str = self._hvac_mode.value if self._hvac_mode else None
            if self._status_manager.is_paused(hvac_mode_str):
                _LOGGER.info("%s: Heating paused", self.entity_id)

                # Detect not-paused → paused transition for contact sensor pause.
                # The duty accumulator is reset here (after contact_delay) rather than
                # immediately on sensor open, so accumulated duty from before the door
                # opened is not discarded during the delay window.
                contact_paused_now = bool(
                    self._contact_sensor_handler and self._contact_sensor_handler.should_take_action()
                )
                if contact_paused_now and not self._contact_was_paused:
                    if self._heater_controller is not None:
                        self._heater_controller.reset_duty_accumulator()
                        _LOGGER.debug(
                            "%s: Contact sensor pause began — duty accumulator reset",
                            self.entity_id,
                        )
                self._contact_was_paused = contact_paused_now

                # Discard any active heating rate session on override
                zone_data = coordinator.get_zone_data(self._zone_id) if coordinator else None
                adaptive_learner = zone_data.get("adaptive_learner") if zone_data else None
                if (
                    adaptive_learner
                    and hasattr(adaptive_learner, "_heating_rate_learner")
                    and adaptive_learner._heating_rate_learner._active_session is not None
                ):
                    adaptive_learner._heating_rate_learner.end_session(
                        end_temp=self._current_temp,
                        reason="override",
                    )
                    _LOGGER.debug(
                        "%s: Discarded heating rate session due to override",
                        self.entity_id,
                    )

                # Decay integral for humidity pauses (~10%/min to prevent stale buildup)
                if self._humidity_detector and self._humidity_detector.should_pause():
                    current_time = time.monotonic()
                    elapsed = current_time - self._last_control_time
                    decay_factor = 0.9 ** (elapsed / 60)  # 10% decay per minute
                    if self._gains_manager is not None:
                        self._gains_manager.decay_integral(decay_factor, PIDChangeReason.HUMIDITY_DECAY)
                    else:
                        self._pid_controller.decay_integral(decay_factor)
                    # M06: advance the baseline so successive paused calls each see
                    # only one call-interval of elapsed time, not an ever-growing sum.
                    self._last_control_time = current_time

                # Turn off heating
                if self._pwm:
                    await self._async_heater_turn_off(force=True)
                else:
                    self._control_output = self._output_min
                    await self._async_set_valve_value(self._control_output)

                # Update zone demand to False when paused
                if coordinator:
                    coordinator.update_zone_demand(
                        self._zone_id, False, self._hvac_mode.value if self._hvac_mode else None
                    )

                self.async_write_ha_state()
                return

            # Not paused — clear the contact-pause transition tracker so the next
            # pause (after a re-open) is correctly detected as a fresh transition.
            self._contact_was_paused = False

            if self._sensor_stall != 0 and time.monotonic() - self._last_sensor_update > self._sensor_stall:
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

                # Update undershoot detector and check for Ki adjustment.
                # Skipped while the water temperature is ramping: a moving supply
                # temperature produces exactly the undershoot signature this
                # detector looks for, and the resulting Ki boosts take weeks to unwind.
                if (
                    self._hvac_mode == HVACMode.HEAT
                    and not self.water_temp_learning_gate_active
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

                            # Judge against the target the PID controls to (setback, contact and cooling clamps)
                            effective_target, _, _ = self._calculate_night_setback_adjustment()
                            adaptive_learner.update_undershoot_detector(
                                self._current_temp,
                                effective_target,
                                dt_seconds,
                                self._cold_tolerance,
                                saturated=self._output_saturated,
                            )

                            # Check if Ki adjustment is needed (unified detector handles both real-time and cycle modes)
                            cycles_completed = adaptive_learner.get_cycle_count()
                            current_ki = self._pid_controller.ki
                            pid_history = self._gains_manager.get_history()
                            try:
                                physics = calculate_physics_gains(self)
                            except ValueError:
                                _LOGGER.debug("%s: no physics Ki, cumulative Ki cap only", self.entity_id)
                                physics = None
                            new_ki = adaptive_learner.check_undershoot_adjustment(
                                cycles_completed,
                                current_ki,
                                pid_history,
                                mode=self._hvac_mode,
                                physics_ki=physics[2] if physics else None,
                            )

                            if new_ki is not None:
                                # Scale integral to prevent output spike
                                old_ki = self._pid_controller.ki
                                if old_ki > 0:
                                    scale_factor = old_ki / new_ki
                                    if self._gains_manager is not None:
                                        self._gains_manager.scale_integral(
                                            scale_factor, PIDChangeReason.UNDERSHOOT_BOOST
                                        )
                                    else:
                                        self._pid_controller.scale_integral(scale_factor)
                                    _LOGGER.info(
                                        "%s: Scaled integral by %.3f to prevent output spike (Ki: %.5f -> %.5f)",
                                        self.entity_id,
                                        scale_factor,
                                        old_ki,
                                        new_ki,
                                    )

                                # Update Ki via PIDGainsManager with comprehensive metrics
                                undershoot_amount = effective_target - self._current_temp
                                self._gains_manager.set_gains(
                                    PIDChangeReason.UNDERSHOOT_BOOST,
                                    ki=new_ki,
                                    metrics={
                                        "undershoot_amount": undershoot_amount,
                                        "time_below_target_hours": (
                                            adaptive_learner.undershoot_detector.time_below_target / 3600.0
                                        ),
                                        "thermal_debt": adaptive_learner.undershoot_detector.thermal_debt,
                                        "consecutive_failures": (
                                            adaptive_learner.undershoot_detector._consecutive_failures
                                        ),
                                    },
                                )

                                # Trigger state save
                                self.async_write_ha_state()

                                _LOGGER.info(
                                    "%s: Undershoot detector (unified) adjusted Ki: %.5f -> %.5f",
                                    self.entity_id,
                                    old_ki,
                                    new_ki,
                                )

                            # Check night setback state for heating rate session management
                            in_night_setback = False
                            if self._night_setback_controller:
                                _, in_night_period, _ = (
                                    self._night_setback_controller.calculate_night_setback_adjustment()
                                )
                                in_night_setback = in_night_period

                            # Check if we should start a heating rate session
                            heating_rate_learner = adaptive_learner._heating_rate_learner

                            # If in night setback and there's an active session, end it as override (discard)
                            if in_night_setback and heating_rate_learner._active_session is not None:
                                heating_rate_learner.end_session(
                                    end_temp=self._current_temp,
                                    reason="override",
                                )
                                _LOGGER.debug(
                                    "%s: Discarded heating rate session due to night setback",
                                    self.entity_id,
                                )

                            if (
                                heating_rate_learner._active_session is None
                                and not self._status_manager.is_paused(hvac_mode_str)
                                and not in_night_setback
                                and self._ext_temp is not None
                            ):
                                # Determine recovery threshold by heating type
                                from .const import HeatingType

                                threshold = 0.3 if self._heating_type == HeatingType.FLOOR_HYDRONIC else 0.5

                                # Start session if temp is below threshold
                                if self._current_temp < self._target_temp - threshold:
                                    heating_rate_learner.start_session(
                                        temp=self._current_temp,
                                        setpoint=self._target_temp,
                                        outdoor_temp=self._ext_temp,
                                    )
                                    _LOGGER.debug(
                                        "%s: Started heating rate session (delta=%.1f°C, outdoor=%.1f°C)",
                                        self.entity_id,
                                        self._target_temp - self._current_temp,
                                        self._ext_temp,
                                    )

                # Record temperature for cycle tracking
                if self._cycle_tracker and self._current_temp is not None:
                    await self._cycle_tracker.update_temperature(dt_util.utcnow(), self._current_temp)

                # Abort active cycle when night setback starts — conditions changed
                # fundamentally, so the in-progress cycle is not valid learning data.
                if self._night_setback_controller:
                    ns_transition = self._night_setback_controller.consume_transition()
                    if ns_transition == "started":
                        _LOGGER.info("%s: Night setback started - aborting active cycle", self.entity_id)
                        if self._heater_controller:
                            self._heater_controller.abort_active_cycle()
                        if self._cycle_tracker:
                            self._cycle_tracker.abort_cycle("night_setback_started")
            await self.set_control_value()

            # Record Ke observation if at steady state
            self._maybe_record_ke_observation()

            # Update control time for humidity integral decay calculation
            self._last_control_time = time.monotonic()

            # Fire milestone notification check once per control cycle (not on attribute reads)
            self._async_schedule_milestone_check()

            self.async_write_ha_state()

    def _async_schedule_milestone_check(self) -> None:
        """Schedule a learning milestone check as a background task.

        Called once per control cycle (not on attribute reads) to avoid flooding
        the event-loop.  The milestone tracker only fires a notification when the
        learning tier actually changes, so duplicate calls within a cycle are
        harmless but still wasteful on every attribute read.
        """
        if not self._zone_id:
            return

        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return

        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return

        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return

        # Integral-decay safety net follows learning status, so it comes back when confidence drops
        self._pid_controller.set_auto_apply_count(adaptive_learner.get_safety_net_auto_apply_count())

        milestone_tracker = zone_data.get("milestone_tracker")
        if not milestone_tracker:
            return

        # Compute current learning status (same logic as _add_learning_object)
        from .managers.state_attributes import _compute_learning_status
        from .managers.pause_detector import PauseDetector

        cycle_count = adaptive_learner.get_cycle_count()
        convergence_confidence = adaptive_learner.get_convergence_confidence()
        heating_type = getattr(self, "_heating_type", None)
        is_paused = PauseDetector.from_entity(self).is_learning_paused()
        contribution_tracker = getattr(adaptive_learner, "_contribution_tracker", None)

        learning_status = _compute_learning_status(
            cycle_count,
            convergence_confidence,
            heating_type,
            is_paused,
            contribution_tracker=contribution_tracker,
        )
        confidence_pct = round(convergence_confidence * 100)

        self.hass.async_create_background_task(
            milestone_tracker.async_check_milestone(learning_status, confidence_pct),
            "adaptive_climate_milestone_check",
        )

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

        # Update open/closed times in case PID mode changed
        self._heater_controller.update_open_closed_times(
            self._min_open_time.seconds,
            self._min_closed_time.seconds,
        )

        # Query transport delay from coordinator and set on heater controller
        # This must happen BEFORE async_set_control_value for PWM calculation
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator") if self._zone_id else None
        if coordinator:
            # H05: update zone demand FIRST so get_transport_delay_for_zone sees this
            # zone as active.  Previously, demand was updated AFTER set_control_value()
            # returned, so the first cold-manifold cycle always got delay=0.
            hvac_mode_str = self._hvac_mode.value if self._hvac_mode else None
            coordinator.update_zone_demand(self._zone_id, self._is_device_active, hvac_mode_str)
            # C03: manifold registry is keyed by entity_id (e.g. "climate.living_room"),
            # NOT by zone slug.  Using self._zone_id (slug) previously caused a silent
            # lookup miss so the heater controller never received the transport delay.
            transport_delay_minutes = coordinator.get_transport_delay_for_zone(self.entity_id)
            # Heater controller expects seconds; coordinator returns minutes.
            self._heater_controller.set_transport_delay(transport_delay_minutes * 60)

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

        # Update open/closed times in case PID mode changed
        self._heater_controller.update_open_closed_times(
            self._min_open_time.seconds,
            self._min_closed_time.seconds,
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
