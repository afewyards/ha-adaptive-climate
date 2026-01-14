"""Cycle tracking manager for adaptive learning.

This module provides the CycleTrackerManager class which tracks heating cycles,
collects temperature data, and calculates metrics for adaptive PID tuning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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


@dataclass
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
