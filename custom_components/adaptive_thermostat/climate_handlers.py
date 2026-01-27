"""Event handlers and state updates for adaptive thermostat.

This module contains all Home Assistant event handlers for sensor changes,
contact sensors, humidity detection, and thermal group leader tracking.
"""

import logging
import time

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.util import dt as dt_util

from . import const
from .managers.events import ContactPauseEvent, ContactResumeEvent

_LOGGER = logging.getLogger(__name__)


class ClimateHandlersMixin:
    """Mixin providing event handlers and state updates for AdaptiveThermostat.

    This mixin encapsulates all sensor state change handlers and the logic
    for updating internal state based on external sensor inputs.
    """

    async def _async_sensor_changed(self, event: Event[EventStateChangedData]):
        """Handle temperature changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._previous_temp_time = self._cur_temp_time
        self._cur_temp_time = time.monotonic()
        self._async_update_temp(new_state)
        self._trigger_source = 'sensor'
        _LOGGER.debug("%s: Received new temperature: %s", self.entity_id, self._current_temp)
        await self._async_control_heating(calc_pid=True, is_temp_sensor_update=True)
        self.async_write_ha_state()

    async def _async_ext_sensor_changed(self, event: Event[EventStateChangedData]):
        """Handle temperature changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._async_update_ext_temp(new_state)
        self._trigger_source = 'ext_sensor'
        _LOGGER.debug("%s: Received new external temperature: %s", self.entity_id, self._ext_temp)
        await self._async_control_heating(calc_pid=False, is_temp_sensor_update=False)

    async def _async_weather_entity_changed(self, event: Event[EventStateChangedData]):
        """Handle weather entity changes - extract temperature attribute as fallback."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._async_update_ext_temp_from_weather(new_state)
        self._trigger_source = 'weather_entity'
        _LOGGER.debug("%s: Received outdoor temperature from weather entity: %s", self.entity_id, self._ext_temp)
        await self._async_control_heating(calc_pid=False, is_temp_sensor_update=False)

    async def _async_wind_speed_sensor_changed(self, event: Event[EventStateChangedData]):
        """Handle wind speed changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._async_update_wind_speed(new_state)
        _LOGGER.debug("%s: Received new wind speed: %s m/s", self.entity_id, self._wind_speed)
        # Wind speed doesn't trigger immediate control loop - it will be used in next calc
        # No need to call _async_control_heating here

    async def _async_weather_entity_wind_changed(self, event: Event[EventStateChangedData]):
        """Handle weather entity changes - extract wind_speed attribute as fallback."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._async_update_wind_speed_from_weather(new_state)
        _LOGGER.debug("%s: Received wind speed from weather entity: %s m/s", self.entity_id, self._wind_speed)

    @callback
    def _async_switch_changed(self, event: Event[EventStateChangedData]):
        """Handle heater switch state changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        # Update zone demand for CentralController when valve state changes
        if self._zone_id:
            coordinator = self.hass.data.get(const.DOMAIN, {}).get("coordinator")
            if coordinator:
                coordinator.update_zone_demand(self._zone_id, self._is_device_active, self._hvac_mode.value if self._hvac_mode else None)

        self.async_write_ha_state()

    async def _async_contact_sensor_changed(self, event: Event[EventStateChangedData]):
        """Handle contact sensor (window/door) state changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        # Update all contact sensor states in the handler
        self._update_contact_sensor_states()

        entity_id = event.data["entity_id"]
        is_open = new_state.state == STATE_ON
        _LOGGER.debug(
            "%s: Contact sensor %s changed to %s",
            self.entity_id, entity_id, "open" if is_open else "closed"
        )

        # Emit contact pause/resume events
        if self._cycle_dispatcher:
            now = dt_util.utcnow()
            if is_open:
                # Track pause start time for this sensor
                self._contact_pause_times[entity_id] = now
                self._cycle_dispatcher.emit(
                    ContactPauseEvent(
                        hvac_mode=str(self._hvac_mode.value) if self._hvac_mode else "off",
                        timestamp=now,
                        entity_id=entity_id,
                    )
                )
                # Reset duty accumulator when contact opens
                if self._heater_controller is not None:
                    self._heater_controller.reset_duty_accumulator()
            else:
                # Calculate pause duration and emit resume event
                pause_start = self._contact_pause_times.pop(entity_id, None)
                pause_duration = (now - pause_start).total_seconds() if pause_start else 0.0
                self._cycle_dispatcher.emit(
                    ContactResumeEvent(
                        hvac_mode=str(self._hvac_mode.value) if self._hvac_mode else "off",
                        timestamp=now,
                        entity_id=entity_id,
                        pause_duration_seconds=pause_duration,
                    )
                )

        # Trigger control heating to potentially pause/resume
        await self._async_control_heating(calc_pid=False, is_temp_sensor_update=False)

    async def _async_humidity_sensor_changed(self, event: Event[EventStateChangedData]):
        """Handle humidity sensor state changes."""
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        if not self._humidity_detector:
            return

        try:
            humidity = float(new_state.state)
            now = dt_util.utcnow()
            self._humidity_detector.record_humidity(now, humidity)

            _LOGGER.debug(
                "%s: Humidity sensor changed to %.1f%% (state=%s)",
                self.entity_id, humidity, self._humidity_detector.get_state()
            )

            # Trigger control heating to potentially pause/resume
            await self._async_control_heating(calc_pid=False, is_temp_sensor_update=False)

        except (ValueError, TypeError) as e:
            _LOGGER.warning(
                "%s: Failed to parse humidity sensor value: %s (error: %s)",
                self.entity_id, new_state.state, e
            )

    def _update_contact_sensor_states(self):
        """Update contact sensor handler with current states from Home Assistant."""
        if not self._contact_sensor_handler:
            return

        contact_states = {}
        for sensor_id in self._contact_sensor_handler.contact_sensors:
            state = self.hass.states.get(sensor_id)
            if state:
                # Contact sensors: 'on' = open, 'off' = closed
                contact_states[sensor_id] = state.state == STATE_ON
            else:
                _LOGGER.warning(
                    "%s: Contact sensor %s not found",
                    self.entity_id, sensor_id
                )
        self._contact_sensor_handler.update_contact_states(contact_states)

    async def _async_leader_changed(self, event: Event[EventStateChangedData]):
        """Handle leader zone setpoint changes for follower zones.

        Follower zones in open_plan thermal groups automatically track
        their leader's target temperature.
        """
        new_state = event.data["new_state"]
        if new_state is None:
            return

        # Get coordinator and thermal group manager
        coordinator = self.hass.data.get(const.DOMAIN, {}).get("coordinator")
        if not coordinator:
            return

        thermal_group_manager = coordinator.thermal_group_manager
        if not thermal_group_manager:
            return

        # Check if leader's temperature attribute changed
        leader_temp = new_state.attributes.get("temperature")
        if leader_temp is None:
            return

        # Only sync if different from current target
        if self._target_temp == leader_temp:
            return

        _LOGGER.info(
            "%s: Syncing follower setpoint to leader: %.1f°C",
            self.entity_id, leader_temp
        )

        # Update target temperature
        await self._temperature_manager.async_set_temperature(leader_temp)

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            _LOGGER.debug("%s: Sensor %s is %s, skipping update",
                          self.entity_id, self._sensor_entity_id, state.state)
            return
        try:
            self._previous_temp = self._current_temp
            self._current_temp = float(state.state)
            self._last_sensor_update = time.monotonic()
        except ValueError as ex:
            _LOGGER.debug("%s: Unable to update from sensor %s: %s", self.entity_id,
                          self._sensor_entity_id, ex)

    @callback
    def _async_update_ext_temp(self, state):
        """Update thermostat with latest state from sensor."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            _LOGGER.debug("%s: External sensor %s is %s, skipping update",
                          self.entity_id, self._ext_sensor_entity_id, state.state)
            return
        try:
            self._ext_temp = float(state.state)
            self._last_ext_sensor_update = time.monotonic()
        except ValueError as ex:
            _LOGGER.debug("%s: Unable to update from sensor %s: %s", self.entity_id,
                          self._ext_sensor_entity_id, ex)

    @callback
    def _async_update_ext_temp_from_weather(self, state):
        """Update outdoor temp from weather entity's temperature attribute."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            _LOGGER.debug("%s: Weather entity %s is %s, skipping outdoor temp update",
                          self.entity_id, self._weather_entity_id, state.state)
            return

        temp = state.attributes.get("temperature")
        if temp is not None:
            try:
                self._ext_temp = float(temp)
                self._last_ext_sensor_update = time.monotonic()
            except (ValueError, TypeError) as ex:
                _LOGGER.debug("%s: Unable to get temperature from weather entity %s: %s",
                              self.entity_id, self._weather_entity_id, ex)

    @callback
    def _async_update_wind_speed(self, state):
        """Update wind speed from sensor."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            _LOGGER.debug("%s: Wind speed sensor %s is %s, treating as 0 m/s",
                          self.entity_id, self._wind_speed_sensor_entity_id, state.state)
            self._wind_speed = None
            return
        try:
            self._wind_speed = float(state.state)
        except ValueError as ex:
            _LOGGER.debug("%s: Unable to update from wind speed sensor %s: %s", self.entity_id,
                          self._wind_speed_sensor_entity_id, ex)
            self._wind_speed = None

    @callback
    def _async_update_wind_speed_from_weather(self, state):
        """Update wind speed from weather entity's wind_speed attribute."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            self._wind_speed = None
            return

        # Try wind_speed first, then native_wind_speed
        wind = state.attributes.get("wind_speed")
        if wind is None:
            wind = state.attributes.get("native_wind_speed")

        if wind is not None:
            try:
                self._wind_speed = float(wind)
            except (ValueError, TypeError):
                self._wind_speed = None
        else:
            self._wind_speed = None
