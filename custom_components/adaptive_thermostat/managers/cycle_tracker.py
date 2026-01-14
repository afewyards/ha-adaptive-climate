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
        """
        self._hass = hass
        self._zone_id = zone_id
        self._adaptive_learner = adaptive_learner
        self._get_target_temp = get_target_temp
        self._get_current_temp = get_current_temp
        self._get_hvac_mode = get_hvac_mode
        self._get_in_grace_period = get_in_grace_period

        # State tracking
        self._state: CycleState = CycleState.IDLE
        self._cycle_start_time: datetime | None = None
        self._cycle_target_temp: float | None = None
        self._temperature_history: list[tuple[datetime, float]] = []
        self._settling_timeout_handle = None

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

    def _schedule_settling_timeout(self) -> None:
        """Schedule timeout for settling detection."""
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
        self._settling_timeout_handle = self._hass.async_call_later(
            self._max_settling_time_minutes * 60, lambda _: self._hass.async_create_task(_settling_timeout())
        )

    async def update_temperature(self, timestamp: datetime, temperature: float) -> None:
        """Update temperature history and check for settling completion.

        Only collects temperature samples when in HEATING or SETTLING state.
        Checks for settling completion on each update during SETTLING state.

        Args:
            timestamp: Time of temperature reading
            temperature: Current temperature value
        """
        # Only collect during active cycle tracking
        if self._state not in (CycleState.HEATING, CycleState.SETTLING):
            return

        # Append temperature sample
        self._temperature_history.append((timestamp, temperature))

        # Check for settling completion during SETTLING state
        if self._state == CycleState.SETTLING:
            if self._is_settling_complete():
                self._logger.info("Settling complete, finalizing cycle")
                await self._finalize_cycle()

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

    async def _finalize_cycle(self) -> None:
        """Finalize cycle and record metrics.

        Note: Full implementation will be added in feature 2.3.
        For now, this just transitions to IDLE state.
        """
        # Cancel settling timeout if active
        if self._settling_timeout_handle is not None:
            self._settling_timeout_handle()
            self._settling_timeout_handle = None

        # Transition to IDLE
        self._state = CycleState.IDLE

        # Note: Cycle validation and metrics calculation will be added in feature 2.3
        self._logger.info("Cycle finalized (full metrics calculation pending feature 2.3)")
