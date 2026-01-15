"""Cycle tracking manager for adaptive learning.

This module provides the CycleTrackerManager class which tracks heating cycles,
collects temperature data, and calculates metrics for adaptive PID tuning.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from ..adaptive.learning import AdaptiveLearner

_LOGGER = logging.getLogger(__name__)


class CycleState(Enum):
    """States for cycle tracking."""

    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"
    SETTLING = "settling"


class CycleTrackerManager:
    """Manages heating cycle tracking and metrics collection.

    This class tracks heating cycles through their lifecycle:
    1. IDLE -> HEATING when heating starts
    2. HEATING -> SETTLING when heating stops
    3. SETTLING -> IDLE when temperature stabilizes or timeout occurs

    During HEATING and SETTLING states, temperature samples are collected
    for cycle metrics calculation.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        adaptive_learner: AdaptiveLearner,
        get_target_temp: Callable[[], float | None],
        get_current_temp: Callable[[], float | None],
        get_hvac_mode: Callable[[], str],
        get_in_grace_period: Callable[[], bool],
        get_is_device_active: Callable[[], bool] | None = None,
    ) -> None:
        """Initialize the cycle tracker manager.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            adaptive_learner: Adaptive learning instance for storing cycle metrics
            get_target_temp: Callback to get current target temperature
            get_current_temp: Callback to get current temperature
            get_hvac_mode: Callback to get current HVAC mode
            get_in_grace_period: Callback to check if in learning grace period
            get_is_device_active: Callback to check if heater/cooler is currently active
        """
        self._hass = hass
        self._zone_id = zone_id
        self._adaptive_learner = adaptive_learner
        self._get_target_temp = get_target_temp
        self._get_current_temp = get_current_temp
        self._get_hvac_mode = get_hvac_mode
        self._get_in_grace_period = get_in_grace_period
        self._get_is_device_active = get_is_device_active

        # State tracking
        self._state: CycleState = CycleState.IDLE
        self._cycle_start_time: datetime | None = None
        self._cycle_target_temp: float | None = None
        self._temperature_history: list[tuple[datetime, float]] = []
        self._settling_timeout_handle = None
        self._was_interrupted: bool = False
        self._setpoint_changes: list[tuple[datetime, float, float]] = []

        # Constants
        self._max_settling_time_minutes = 120
        self._min_cycle_duration_minutes = 5

        # Logging
        self._logger = logging.getLogger(f"{__name__}.{zone_id}")
        self._logger.info("CycleTrackerManager initialized for zone %s", zone_id)

    @property
    def state(self) -> CycleState:
        """Return current cycle state."""
        return self._state

    @property
    def cycle_start_time(self) -> datetime | None:
        """Return cycle start time."""
        return self._cycle_start_time

    @property
    def temperature_history(self) -> list[tuple[datetime, float]]:
        """Return temperature history."""
        return self._temperature_history.copy()

    def on_heating_started(self, timestamp: datetime) -> None:
        """Handle heating start event.

        Transitions from IDLE -> HEATING, records cycle start time and target temperature,
        and clears temperature history to start fresh collection.

        Args:
            timestamp: Time when heating started
        """
        if self._state != CycleState.IDLE:
            self._logger.warning(
                "Heating started while in state %s, resetting cycle", self._state
            )

        # Transition to HEATING state
        self._state = CycleState.HEATING
        self._cycle_start_time = timestamp
        self._cycle_target_temp = self._get_target_temp()
        self._temperature_history.clear()

        current_temp = self._get_current_temp()
        self._logger.info(
            "Cycle started: target=%.2f°C, current=%.2f°C",
            self._cycle_target_temp or 0.0,
            current_temp or 0.0,
        )

    def on_heating_stopped(self, timestamp: datetime) -> None:
        """Handle heating stop event.

        Transitions from HEATING -> SETTLING and schedules settling timeout.

        Args:
            timestamp: Time when heating stopped
        """
        if self._state != CycleState.HEATING:
            self._logger.warning(
                "Heating stopped while in state %s, ignoring", self._state
            )
            return

        # Transition to SETTLING state
        self._state = CycleState.SETTLING

        # Schedule settling timeout (120 minutes)
        self._schedule_settling_timeout()

        self._logger.info(
            "Heating stopped, monitoring settling (timeout in %d minutes)",
            self._max_settling_time_minutes,
        )

    def on_cooling_started(self, timestamp: datetime) -> None:
        """Handle cooling start event.

        Transitions from IDLE -> COOLING, records cycle start time and target temperature,
        and clears temperature history to start fresh collection.

        Args:
            timestamp: Time when cooling started
        """
        if self._state != CycleState.IDLE:
            self._logger.warning(
                "Cooling started while in state %s, resetting cycle", self._state
            )

        # Transition to COOLING state
        self._state = CycleState.COOLING
        self._cycle_start_time = timestamp
        self._cycle_target_temp = self._get_target_temp()
        self._temperature_history.clear()

        current_temp = self._get_current_temp()
        self._logger.info(
            "Cooling cycle started: target=%.2f°C, current=%.2f°C",
            self._cycle_target_temp or 0.0,
            current_temp or 0.0,
        )

    def on_cooling_stopped(self, timestamp: datetime) -> None:
        """Handle cooling stop event.

        Transitions from COOLING -> SETTLING and schedules settling timeout.

        Args:
            timestamp: Time when cooling stopped
        """
        if self._state != CycleState.COOLING:
            self._logger.warning(
                "Cooling stopped while in state %s, ignoring", self._state
            )
            return

        # Transition to SETTLING state
        self._state = CycleState.SETTLING

        # Schedule settling timeout (120 minutes)
        self._schedule_settling_timeout()

        self._logger.info(
            "Cooling stopped, monitoring settling (timeout in %d minutes)",
            self._max_settling_time_minutes,
        )

    def _schedule_settling_timeout(self) -> None:
        """Schedule timeout for settling detection."""
        from homeassistant.helpers.event import async_call_later

        # Cancel existing timeout if any
        if self._settling_timeout_handle is not None:
            self._settling_timeout_handle()
            self._settling_timeout_handle = None

        # Schedule new timeout
        async def _settling_timeout() -> None:
            """Handle settling timeout."""
            self._logger.warning(
                "Settling timeout reached (%d minutes), finalizing cycle",
                self._max_settling_time_minutes,
            )
            # Note: _finalize_cycle() will be implemented in feature 2.3
            self._state = CycleState.IDLE
            self._settling_timeout_handle = None

        # Store the cancel handle
        self._settling_timeout_handle = async_call_later(
            self._hass, self._max_settling_time_minutes * 60, lambda _: self._hass.async_create_task(_settling_timeout())
        )

    async def update_temperature(self, timestamp: datetime, temperature: float) -> None:
        """Update temperature history and check for settling completion.

        Only collects temperature samples when in HEATING, COOLING, or SETTLING state.
        Checks for settling completion on each update during SETTLING state.

        Args:
            timestamp: Time of temperature reading
            temperature: Current temperature value
        """
        # Only collect during active cycle tracking
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Append temperature sample
        self._temperature_history.append((timestamp, temperature))

        # Check for settling completion during SETTLING state
        if self._state == CycleState.SETTLING:
            if self._is_settling_complete():
                self._logger.info("Settling complete, finalizing cycle")
                await self._finalize_cycle()

    def _reset_cycle_state(self) -> None:
        """Reset cycle state to IDLE and clear all cycle data.

        This helper method provides consistent cleanup of cycle state,
        ensuring all state variables are properly reset.
        """
        # Clear temperature history
        self._temperature_history.clear()

        # Reset cycle tracking variables
        self._cycle_start_time = None
        self._cycle_target_temp = None
        self._was_interrupted = False

        # Clear setpoint changes
        self._setpoint_changes.clear()

        # Set state to IDLE
        self._state = CycleState.IDLE

        # Cancel settling timeout if active
        if self._settling_timeout_handle is not None:
            self._settling_timeout_handle()
            self._settling_timeout_handle = None

    def _is_settling_complete(self) -> bool:
        """Check if temperature has settled after heating stopped.

        Settling is considered complete when:
        1. At least 10 samples (5 minutes at 30-second intervals) are collected
        2. Variance of last 10 samples < 0.01 (stable temperature)
        3. Current temperature is within 0.5°C of target

        Returns:
            True if settling is complete, False otherwise
        """
        # Need minimum 10 samples for settling detection
        if len(self._temperature_history) < 10:
            return False

        # Get last 10 temperature samples
        last_temps = [temp for _, temp in self._temperature_history[-10:]]

        # Calculate variance
        mean_temp = sum(last_temps) / len(last_temps)
        variance = sum((temp - mean_temp) ** 2 for temp in last_temps) / len(last_temps)

        # Check if variance is below threshold (stable)
        if variance >= 0.01:
            return False

        # Check if current temperature is within 0.5°C of target
        current_temp = last_temps[-1]
        target_temp = self._cycle_target_temp
        if target_temp is None:
            return False

        if abs(current_temp - target_temp) > 0.5:
            return False

        return True

    def _is_cycle_valid(self) -> tuple[bool, str]:
        """Check if the current cycle is valid for recording.

        A cycle is valid if:
        1. Duration >= minimum cycle duration (5 minutes)
        2. Not in learning grace period
        3. Learning is enabled (not in vacation mode)
        4. Sufficient temperature samples (>= 5)

        Returns:
            Tuple of (is_valid, reason_string)
        """
        # Check minimum duration
        if self._cycle_start_time is None:
            return False, "No cycle start time recorded"

        duration_minutes = (datetime.now() - self._cycle_start_time).total_seconds() / 60
        if duration_minutes < self._min_cycle_duration_minutes:
            return False, f"Cycle too short ({duration_minutes:.1f} min < {self._min_cycle_duration_minutes} min)"

        # Check learning grace period
        if self._get_in_grace_period():
            return False, "In learning grace period"

        # Check vacation mode (learning_enabled)
        # Note: This is checked via zone_data["learning_enabled"] which is set to False in vacation mode
        # For now, we'll skip this check as it requires coordination with climate entity
        # The get_in_grace_period callback handles the grace period, and vacation mode
        # should be checked at a higher level before calling cycle tracking methods

        # Check sufficient temperature samples
        if len(self._temperature_history) < 5:
            return False, f"Insufficient temperature samples ({len(self._temperature_history)} < 5)"

        return True, "Valid"

    def on_setpoint_changed(self, old_temp: float, new_temp: float) -> None:
        """Handle setpoint change event.

        If heater/cooler is active, continues tracking with the new setpoint and marks
        the cycle as interrupted. Otherwise, aborts the current cycle.

        Args:
            old_temp: Previous target temperature
            new_temp: New target temperature
        """
        # Only process if we're in an active cycle
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Check if heater/cooler is currently active
        if self._get_is_device_active is not None and self._get_is_device_active():
            # Continue tracking with new setpoint
            self._logger.info(
                "Setpoint changed while device active, continuing cycle tracking: %.2f°C -> %.2f°C",
                old_temp,
                new_temp,
            )
            self._cycle_target_temp = new_temp
            self._was_interrupted = True
            self._setpoint_changes.append((datetime.now(), old_temp, new_temp))
            return

        # Abort the cycle
        self._logger.info(
            "Cycle aborted due to setpoint change: %.2f°C -> %.2f°C",
            old_temp,
            new_temp,
        )

        self._reset_cycle_state()

    def on_contact_sensor_pause(self) -> None:
        """Handle contact sensor pause event.

        Aborts the current cycle if in HEATING, COOLING, or SETTLING state, as
        climate control has been paused due to window/door opening.
        """
        # Only abort if we're in an active cycle
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Abort the cycle
        self._logger.info("Cycle aborted due to contact sensor pause")

        self._reset_cycle_state()

    def on_mode_changed(self, old_mode: str, new_mode: str) -> None:
        """Handle HVAC mode change event.

        Aborts the current cycle if mode changes away from the active mode,
        as the climate control operation has been terminated.

        Args:
            old_mode: Previous HVAC mode
            new_mode: New HVAC mode
        """
        # Only abort if we're in an active cycle
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Determine if mode change requires aborting the cycle
        # Abort if:
        # - We were heating and mode changed to off or cool
        # - We were cooling and mode changed to off or heat
        should_abort = False
        if self._state == CycleState.HEATING and new_mode in ("off", "cool"):
            should_abort = True
        elif self._state == CycleState.COOLING and new_mode in ("off", "heat"):
            should_abort = True
        elif self._state == CycleState.SETTLING and new_mode == "off":
            should_abort = True

        if should_abort:
            # Abort the cycle
            self._logger.info(
                "Cycle aborted due to mode change: %s -> %s",
                old_mode,
                new_mode,
            )

            self._reset_cycle_state()

    async def _finalize_cycle(self) -> None:
        """Finalize cycle and record metrics.

        Validates the cycle, calculates metrics if valid, and records them
        with the adaptive learner. Transitions to IDLE state.
        """
        # Cancel settling timeout if active
        if self._settling_timeout_handle is not None:
            self._settling_timeout_handle()
            self._settling_timeout_handle = None

        # Validate cycle
        is_valid, reason = self._is_cycle_valid()
        if not is_valid:
            self._logger.info("Cycle not recorded: %s", reason)
            self._reset_cycle_state()
            return

        # Log interruption status if cycle was interrupted
        if self._was_interrupted:
            self._logger.info(
                "Cycle had %d setpoint changes during tracking",
                len(self._setpoint_changes),
            )

        # Import cycle analysis functions
        from ..adaptive.cycle_analysis import (
            CycleMetrics,
            calculate_overshoot,
            calculate_undershoot,
            calculate_settling_time,
            count_oscillations,
            calculate_rise_time,
        )

        # Get target temperature
        target_temp = self._cycle_target_temp
        if target_temp is None:
            self._logger.warning("No target temperature recorded, cannot calculate metrics")
            self._reset_cycle_state()
            return

        # Get start temperature (first reading in history)
        if len(self._temperature_history) < 1:
            self._logger.warning("No temperature history, cannot calculate metrics")
            self._reset_cycle_state()
            return

        start_temp = self._temperature_history[0][1]

        # Calculate all 5 metrics
        overshoot = calculate_overshoot(self._temperature_history, target_temp)
        undershoot = calculate_undershoot(self._temperature_history, target_temp)
        settling_time = calculate_settling_time(self._temperature_history, target_temp)
        oscillations = count_oscillations(self._temperature_history, target_temp)
        rise_time = calculate_rise_time(self._temperature_history, start_temp, target_temp)

        # Create CycleMetrics object
        metrics = CycleMetrics(
            overshoot=overshoot,
            undershoot=undershoot,
            settling_time=settling_time,
            oscillations=oscillations,
            rise_time=rise_time,
        )

        # Record metrics with adaptive learner
        self._adaptive_learner.add_cycle_metrics(metrics)
        self._adaptive_learner.update_convergence_tracking(metrics)

        # Log cycle completion with all metrics
        self._logger.info(
            "Cycle completed - overshoot=%.2f°C, undershoot=%.2f°C, "
            "settling_time=%.1f min, oscillations=%d, rise_time=%.1f min",
            overshoot or 0.0,
            undershoot or 0.0,
            settling_time or 0.0,
            oscillations,
            rise_time or 0.0,
        )

        # Reset cycle state (clears interruption flags and transitions to IDLE)
        self._reset_cycle_state()
