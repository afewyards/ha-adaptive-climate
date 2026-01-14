"""Performance sensors for Adaptive Thermostat.

This module contains sensors that track heating system performance metrics:
- DutyCycleSensor: Tracks heater on/off time as percentage
- CycleTimeSensor: Tracks average heating cycle duration
- OvershootSensor: Tracks temperature overshoot from adaptive learning
- SettlingTimeSensor: Tracks time to reach stable temperature
- OscillationsSensor: Tracks temperature oscillation count
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTime,
    UnitOfTemperature,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.event import async_track_state_change_event

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Default measurement window for duty cycle calculation (1 hour)
DEFAULT_DUTY_CYCLE_WINDOW = timedelta(hours=1)

# Default number of cycles to average for CycleTimeSensor
DEFAULT_ROLLING_AVERAGE_SIZE = 10


@dataclass
class HeaterStateChange:
    """Record of a heater state change."""

    timestamp: datetime
    is_on: bool


class AdaptiveThermostatSensor(SensorEntity):
    """Base class for Adaptive Thermostat sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._climate_entity_id = climate_entity_id
        self._attr_should_poll = False
        self._attr_available = True
        self._attr_entity_registry_visible_default = False


class DutyCycleSensor(AdaptiveThermostatSensor):
    """Sensor for heating duty cycle percentage.

    Calculates the actual duty cycle by tracking heater on/off state changes
    over a configurable measurement window (default 1 hour).

    The duty cycle is calculated as: (on_time / total_time) * 100

    Alternatively, can use the PID controller's control_output as the duty cycle
    when no heater state tracking is available.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
        measurement_window: timedelta | None = None,
    ) -> None:
        """Initialize the duty cycle sensor.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            zone_name: Human-readable zone name
            climate_entity_id: Entity ID of the climate entity
            measurement_window: Time window for duty cycle calculation (default 1 hour)
        """
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Duty Cycle"
        self._attr_unique_id = f"{zone_id}_duty_cycle"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:percent"
        self._state: float = 0.0

        # Measurement window for duty cycle calculation
        self._measurement_window = measurement_window or DEFAULT_DUTY_CYCLE_WINDOW

        # State change tracking - use deque with maxlen to limit memory
        # Store enough changes to cover the measurement window
        # Assuming max 1 change per second, 1 hour = 3600 changes max
        self._state_changes: deque[HeaterStateChange] = deque(maxlen=7200)

        # Track current heater state
        self._heater_entity_id: str | None = None
        self._current_heater_state: bool = False
        self._state_listener_unsub: Any = None

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "measurement_window_minutes": self._measurement_window.total_seconds() / 60,
            "state_changes_tracked": len(self._state_changes),
            "heater_entity_id": self._heater_entity_id,
        }

    async def async_added_to_hass(self) -> None:
        """Set up state change tracking when added to hass."""
        await super().async_added_to_hass()

        # Get heater entity ID from climate entity
        climate_state = self.hass.states.get(self._climate_entity_id)
        if climate_state:
            # Try to get heater entity from attributes
            heater_ids = climate_state.attributes.get("heater_entity_id")
            if heater_ids:
                # heater_entity_id can be a list or single entity
                if isinstance(heater_ids, list) and heater_ids:
                    self._heater_entity_id = heater_ids[0]
                elif isinstance(heater_ids, str):
                    self._heater_entity_id = heater_ids

        # If we found a heater entity, track its state changes
        if self._heater_entity_id:
            # Record initial state
            heater_state = self.hass.states.get(self._heater_entity_id)
            if heater_state:
                self._current_heater_state = heater_state.state == STATE_ON
                self._record_state_change(self._current_heater_state)

            # Set up state change listener
            self._state_listener_unsub = async_track_state_change_event(
                self.hass,
                [self._heater_entity_id],
                self._async_heater_state_changed,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when removed from hass."""
        if self._state_listener_unsub:
            self._state_listener_unsub()
            self._state_listener_unsub = None

    @callback
    def _async_heater_state_changed(self, event: Event) -> None:
        """Handle heater state changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_on = new_state.state == STATE_ON

        # Only record if state actually changed
        if is_on != self._current_heater_state:
            self._current_heater_state = is_on
            self._record_state_change(is_on)
            _LOGGER.debug(
                "%s: Heater state changed to %s",
                self._attr_unique_id,
                "ON" if is_on else "OFF",
            )

    def _record_state_change(self, is_on: bool) -> None:
        """Record a state change with timestamp.

        Args:
            is_on: Whether the heater is now on
        """
        self._state_changes.append(
            HeaterStateChange(timestamp=datetime.now(), is_on=is_on)
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        duty_cycle = self._calculate_duty_cycle()
        self._state = round(duty_cycle, 1)

    def _calculate_duty_cycle(self) -> float:
        """Calculate duty cycle from tracked state changes.

        Returns:
            Duty cycle as percentage (0-100)
        """
        now = datetime.now()
        window_start = now - self._measurement_window

        # If no state changes tracked, try to use control_output from climate entity
        if not self._state_changes:
            return self._get_control_output_duty_cycle()

        # Prune old state changes outside measurement window
        # Keep one state before window_start to know initial state
        self._prune_old_state_changes(window_start)

        # If still no data after pruning, use control_output
        if not self._state_changes:
            return self._get_control_output_duty_cycle()

        # Calculate on_time within the measurement window
        on_time_seconds = self._calculate_on_time(window_start, now)
        total_seconds = self._measurement_window.total_seconds()

        # Calculate duty cycle percentage
        if total_seconds <= 0:
            return 0.0

        duty_cycle = (on_time_seconds / total_seconds) * 100.0

        _LOGGER.debug(
            "%s: Duty cycle calculated: %.1f%% (on_time=%.1fs, total=%.1fs)",
            self._attr_unique_id,
            duty_cycle,
            on_time_seconds,
            total_seconds,
        )

        return duty_cycle

    def _prune_old_state_changes(self, window_start: datetime) -> None:
        """Remove state changes older than window_start, keeping the most recent one before it.

        Args:
            window_start: Start of the measurement window
        """
        # Find state changes within the window
        changes_in_window = [
            sc for sc in self._state_changes if sc.timestamp >= window_start
        ]

        # Find the most recent state change before window_start
        changes_before_window = [
            sc for sc in self._state_changes if sc.timestamp < window_start
        ]

        if changes_before_window:
            # Keep only the most recent one before window
            last_before = max(changes_before_window, key=lambda sc: sc.timestamp)
            new_changes = [last_before] + changes_in_window
        else:
            new_changes = changes_in_window

        # Replace with pruned list
        self._state_changes.clear()
        for change in new_changes:
            self._state_changes.append(change)

    def _calculate_on_time(self, window_start: datetime, window_end: datetime) -> float:
        """Calculate total on-time within the measurement window.

        Args:
            window_start: Start of the measurement window
            window_end: End of the measurement window (now)

        Returns:
            Total on-time in seconds
        """
        if not self._state_changes:
            return 0.0

        # Sort changes by timestamp
        sorted_changes = sorted(self._state_changes, key=lambda sc: sc.timestamp)

        on_time = 0.0
        current_state = False
        last_timestamp = window_start

        for change in sorted_changes:
            # Clamp timestamp to window bounds
            change_time = max(change.timestamp, window_start)
            change_time = min(change_time, window_end)

            # If we were ON during this period, add the time
            if current_state and change_time > last_timestamp:
                on_time += (change_time - last_timestamp).total_seconds()

            # Update state
            current_state = change.is_on
            last_timestamp = change_time

        # Handle the final period from last change to window_end
        if current_state and window_end > last_timestamp:
            on_time += (window_end - last_timestamp).total_seconds()

        return on_time

    def _get_control_output_duty_cycle(self) -> float:
        """Get duty cycle from climate entity's control_output attribute.

        This is used as a fallback when no heater state tracking is available,
        or as the primary source when heater entity is not configured.

        Returns:
            Duty cycle as percentage (0-100) based on control_output
        """
        climate_state = self.hass.states.get(self._climate_entity_id)
        if not climate_state:
            return 0.0

        # Get control_output from climate entity attributes
        control_output = climate_state.attributes.get("control_output")
        if control_output is not None:
            try:
                # control_output is typically 0-100
                output = float(control_output)
                # Clamp to valid range
                return max(0.0, min(100.0, output))
            except (ValueError, TypeError):
                pass

        # Fallback: check if heater is currently on
        if self._heater_entity_id:
            heater_state = self.hass.states.get(self._heater_entity_id)
            if heater_state and heater_state.state == STATE_ON:
                return 100.0

        return 0.0


class CycleTimeSensor(AdaptiveThermostatSensor):
    """Sensor for average heating cycle time.

    Tracks heater state transitions (on->off->on) and calculates
    the cycle time as the duration between consecutive ON states.
    Maintains a rolling average of recent cycle times.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
        rolling_average_size: int = DEFAULT_ROLLING_AVERAGE_SIZE,
    ) -> None:
        """Initialize the cycle time sensor.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            zone_name: Human-readable zone name
            climate_entity_id: Entity ID of the climate entity
            rolling_average_size: Number of cycles to average (default 10)
        """
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Cycle Time"
        self._attr_unique_id = f"{zone_id}_cycle_time"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:timer-outline"
        self._state: float | None = None

        # Rolling average configuration
        self._rolling_average_size = rolling_average_size

        # State tracking
        self._heater_entity_id: str | None = None
        self._current_heater_state: bool = False
        self._state_listener_unsub: Any = None
        self._last_on_timestamp: datetime | None = None
        self._cycle_times: deque[float] = deque(maxlen=rolling_average_size)

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        last_cycle_time = self._cycle_times[-1] if self._cycle_times else None
        return {
            "cycle_count": len(self._cycle_times),
            "rolling_average_size": self._rolling_average_size,
            "heater_entity_id": self._heater_entity_id,
            "last_cycle_time_minutes": round(last_cycle_time, 1) if last_cycle_time else None,
        }

    async def async_added_to_hass(self) -> None:
        """Set up state change tracking when added to hass."""
        await super().async_added_to_hass()

        # Get heater entity ID from climate entity
        climate_state = self.hass.states.get(self._climate_entity_id)
        if climate_state:
            # Try to get heater entity from attributes
            heater_ids = climate_state.attributes.get("heater_entity_id")
            if heater_ids:
                # heater_entity_id can be a list or single entity
                if isinstance(heater_ids, list) and heater_ids:
                    self._heater_entity_id = heater_ids[0]
                elif isinstance(heater_ids, str):
                    self._heater_entity_id = heater_ids

        # If we found a heater entity, track its state changes
        if self._heater_entity_id:
            # Record initial state
            heater_state = self.hass.states.get(self._heater_entity_id)
            if heater_state:
                self._current_heater_state = heater_state.state == STATE_ON
                # If heater is currently ON, record this as the start of a cycle
                if self._current_heater_state:
                    self._last_on_timestamp = datetime.now()

            # Set up state change listener
            self._state_listener_unsub = async_track_state_change_event(
                self.hass,
                [self._heater_entity_id],
                self._async_heater_state_changed,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when removed from hass."""
        if self._state_listener_unsub:
            self._state_listener_unsub()
            self._state_listener_unsub = None

    @callback
    def _async_heater_state_changed(self, event: Event) -> None:
        """Handle heater state changes and detect cycles."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_on = new_state.state == STATE_ON
        now = datetime.now()

        # Only process if state actually changed
        if is_on == self._current_heater_state:
            return

        old_state = self._current_heater_state
        self._current_heater_state = is_on

        # Detect ON transition (cycle start)
        if is_on and not old_state:
            # Heater just turned ON
            if self._last_on_timestamp is not None:
                # Calculate cycle time as duration since last ON
                cycle_duration = now - self._last_on_timestamp
                cycle_minutes = cycle_duration.total_seconds() / 60.0

                # Record cycle time (filter out unreasonably short cycles < 1 min)
                if cycle_minutes >= 1.0:
                    self._cycle_times.append(cycle_minutes)
                    _LOGGER.debug(
                        "%s: Recorded cycle time: %.1f minutes (total cycles: %d)",
                        self._attr_unique_id,
                        cycle_minutes,
                        len(self._cycle_times),
                    )

            # Update last ON timestamp
            self._last_on_timestamp = now
            _LOGGER.debug(
                "%s: Heater turned ON at %s",
                self._attr_unique_id,
                now.isoformat(),
            )

        elif not is_on and old_state:
            # Heater just turned OFF
            _LOGGER.debug(
                "%s: Heater turned OFF at %s",
                self._attr_unique_id,
                now.isoformat(),
            )

    async def async_update(self) -> None:
        """Update the sensor state."""
        avg_cycle_time = self._calculate_average_cycle_time()
        if avg_cycle_time is not None:
            self._state = round(avg_cycle_time, 1)
        else:
            self._state = None

    def _calculate_average_cycle_time(self) -> float | None:
        """Calculate average heating cycle time.

        Returns:
            Average cycle time in minutes, or None if no complete cycles yet.
        """
        if not self._cycle_times:
            return None

        return sum(self._cycle_times) / len(self._cycle_times)


class OvershootSensor(AdaptiveThermostatSensor):
    """Sensor for temperature overshoot from adaptive learning."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the overshoot sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Overshoot"
        self._attr_unique_id = f"{zone_id}_overshoot"
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:thermometer-alert"
        self._state = 0.0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Get overshoot from adaptive learner
        overshoot = await self._get_overshoot()
        self._state = round(overshoot, 2) if overshoot is not None else 0.0

    async def _get_overshoot(self) -> float | None:
        """Get overshoot value from adaptive learner.

        Returns:
            Overshoot in degrees C, or None if no data available
        """
        # Get coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return None

        # Get zone data
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return None

        # Get adaptive learner from zone data
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return None

        # Get cycle history
        if not hasattr(adaptive_learner, "cycle_history") or not adaptive_learner.cycle_history:
            return 0.0

        # Calculate average overshoot from recent cycles
        overshoots = [
            cycle.overshoot
            for cycle in adaptive_learner.cycle_history
            if cycle.overshoot is not None
        ]

        if not overshoots:
            return 0.0

        return sum(overshoots) / len(overshoots)


class SettlingTimeSensor(AdaptiveThermostatSensor):
    """Sensor for settling time from adaptive learning."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the settling time sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Settling Time"
        self._attr_unique_id = f"{zone_id}_settling_time"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:timer-sand"
        self._state = 0.0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Get settling time from adaptive learner
        settling_time = await self._get_settling_time()
        self._state = round(settling_time, 1) if settling_time is not None else 0.0

    async def _get_settling_time(self) -> float | None:
        """Get settling time value from adaptive learner.

        Returns:
            Settling time in minutes, or None if no data available
        """
        # Get coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return None

        # Get zone data
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return None

        # Get adaptive learner from zone data
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return None

        # Get cycle history
        if not hasattr(adaptive_learner, "cycle_history") or not adaptive_learner.cycle_history:
            return 0.0

        # Calculate average settling time from recent cycles
        settling_times = [
            cycle.settling_time
            for cycle in adaptive_learner.cycle_history
            if cycle.settling_time is not None
        ]

        if not settling_times:
            return 0.0

        return sum(settling_times) / len(settling_times)


class OscillationsSensor(AdaptiveThermostatSensor):
    """Sensor for oscillation count from adaptive learning."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        zone_name: str,
        climate_entity_id: str,
    ) -> None:
        """Initialize the oscillations sensor."""
        super().__init__(hass, zone_id, zone_name, climate_entity_id)
        self._attr_name = f"{zone_name} Oscillations"
        self._attr_unique_id = f"{zone_id}_oscillations"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:sine-wave"
        self._state = 0

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        return self._state

    async def async_update(self) -> None:
        """Update the sensor state."""
        # Get oscillations from adaptive learner
        oscillations = await self._get_oscillations()
        self._state = int(oscillations) if oscillations is not None else 0

    async def _get_oscillations(self) -> int | None:
        """Get oscillations count from adaptive learner.

        Returns:
            Average oscillation count, or None if no data available
        """
        # Get coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return None

        # Get zone data
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return None

        # Get adaptive learner from zone data
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return None

        # Get cycle history
        if not hasattr(adaptive_learner, "cycle_history") or not adaptive_learner.cycle_history:
            return 0

        # Calculate average oscillations from recent cycles
        oscillations = [
            cycle.oscillations
            for cycle in adaptive_learner.cycle_history
            if cycle.oscillations is not None
        ]

        if not oscillations:
            return 0

        return int(sum(oscillations) / len(oscillations))
