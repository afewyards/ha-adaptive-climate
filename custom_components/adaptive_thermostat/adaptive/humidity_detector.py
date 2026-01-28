"""Humidity spike detection for adaptive thermostat.

Detects rapid humidity increases (e.g., from showers) and pauses heating
to prevent unnecessary heating during temporary humidity spikes.
"""

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Literal, Optional

_LOGGER = logging.getLogger(__name__)


class HumidityDetector:
    """Detects humidity spikes and manages pause state.

    State machine:
        NORMAL -> PAUSED (on spike) -> STABILIZING (on drop) -> NORMAL (after delay)

    Triggers:
        - Humidity rises >spike_threshold% in detection_window seconds
        - Humidity exceeds absolute_max%

    Exit conditions:
        - PAUSED -> STABILIZING: humidity <70% AND >10% drop from peak
        - STABILIZING -> NORMAL: stabilization_delay seconds elapsed
    """

    def __init__(
        self,
        spike_threshold: float = 15.0,
        absolute_max: float = 80.0,
        detection_window: int = 300,
        stabilization_delay: int = 300,
        max_pause_duration: int = 3600,
        exit_humidity_threshold: float = 70.0,
        exit_humidity_drop: float = 5.0,
    ):
        """Initialize humidity detector.

        Args:
            spike_threshold: Humidity rise % that triggers pause (default: 15%)
            absolute_max: Absolute humidity % that triggers pause (default: 80%)
            detection_window: Time window in seconds for spike detection (default: 300s/5min)
            stabilization_delay: Time in seconds before resuming from stabilizing (default: 300s/5min)
            max_pause_duration: Maximum time in seconds to stay paused (default: 3600s/60min)
            exit_humidity_threshold: Humidity % below which exit can occur (default: 70%)
            exit_humidity_drop: Drop % from peak required for exit (default: 5%)
        """
        self._spike_threshold = spike_threshold
        self._absolute_max = absolute_max
        self._detection_window = detection_window
        self._stabilization_delay = stabilization_delay
        self._max_pause_duration = max_pause_duration
        self._exit_humidity_threshold = exit_humidity_threshold
        self._exit_humidity_drop = exit_humidity_drop

        # State tracking
        self._state: Literal["normal", "paused", "stabilizing"] = "normal"
        self._humidity_history: deque[tuple[datetime, float]] = deque()
        self._peak_humidity: Optional[float] = None
        self._stabilization_start: Optional[datetime] = None
        self._pause_start: Optional[datetime] = None
        self._last_timestamp: Optional[datetime] = None

    def record_humidity(self, ts: datetime, humidity: float) -> None:
        """Record humidity reading and update state.

        Args:
            ts: Timestamp of reading
            humidity: Humidity percentage (0-100)
        """
        # Track latest timestamp
        self._last_timestamp = ts

        # Add to ring buffer
        self._humidity_history.append((ts, humidity))

        # Evict old entries outside detection window
        cutoff_time = ts - timedelta(seconds=self._detection_window)
        while self._humidity_history and self._humidity_history[0][0] < cutoff_time:
            self._humidity_history.popleft()

        # Update state machine
        self._update_state(ts, humidity)

    def _update_state(self, ts: datetime, current_humidity: float) -> None:
        """Update state machine based on current conditions.

        Args:
            ts: Current timestamp
            current_humidity: Current humidity reading
        """
        if self._state == "normal":
            self._check_triggers(current_humidity)

        elif self._state == "paused":
            # Check max pause duration - force resume after 60 min
            if self._pause_start is not None:
                elapsed = (ts - self._pause_start).total_seconds()
                if elapsed >= self._max_pause_duration:
                    _LOGGER.warning(
                        "Max pause duration (%d seconds) reached. Forcing resume to normal state.",
                        self._max_pause_duration
                    )
                    self._state = "normal"
                    self._peak_humidity = None
                    self._pause_start = None
                    return

            # Track peak humidity
            if self._peak_humidity is None or current_humidity > self._peak_humidity:
                self._peak_humidity = current_humidity

            # Check for new spike (re-triggers paused)
            self._check_triggers(current_humidity)

            # Check exit condition: below threshold AND sufficient drop from peak
            if current_humidity < self._exit_humidity_threshold and self._peak_humidity is not None:
                drop_from_peak = self._peak_humidity - current_humidity
                if drop_from_peak > self._exit_humidity_drop:
                    self._state = "stabilizing"
                    self._stabilization_start = ts
                    self._pause_start = None

        elif self._state == "stabilizing":
            # Check for new spike (returns to paused)
            if self._check_triggers(current_humidity):
                self._stabilization_start = None
                return

            # Check if stabilization period complete
            if self._stabilization_start is not None:
                elapsed = (ts - self._stabilization_start).total_seconds()
                if elapsed >= self._stabilization_delay:
                    self._state = "normal"
                    self._peak_humidity = None
                    self._stabilization_start = None

    def _check_triggers(self, current_humidity: float) -> bool:
        """Check if humidity conditions trigger pause state.

        Args:
            current_humidity: Current humidity reading

        Returns:
            True if triggered, False otherwise
        """
        # Absolute threshold trigger
        if current_humidity > self._absolute_max:
            self._state = "paused"
            # Only update peak if it's a new high (don't reset on re-trigger)
            if self._peak_humidity is None or current_humidity > self._peak_humidity:
                self._peak_humidity = current_humidity
            self._stabilization_start = None
            # Track pause start time (or reset if already paused)
            if self._pause_start is None:
                self._pause_start = self._last_timestamp
            return True

        # Rate of change trigger
        if len(self._humidity_history) >= 2:
            oldest_humidity = self._humidity_history[0][1]
            humidity_rise = current_humidity - oldest_humidity

            if humidity_rise > self._spike_threshold:
                self._state = "paused"
                # Only update peak if it's a new high (don't reset on re-trigger)
                if self._peak_humidity is None or current_humidity > self._peak_humidity:
                    self._peak_humidity = current_humidity
                self._stabilization_start = None
                # Track pause start time (or reset if already paused)
                if self._pause_start is None:
                    self._pause_start = self._last_timestamp
                return True

        return False

    def get_state(self) -> str:
        """Get current state.

        Returns:
            Current state: "normal", "paused", or "stabilizing"
        """
        return self._state

    def should_pause(self) -> bool:
        """Check if heating should be paused.

        Returns:
            True if state is "paused" or "stabilizing", False if "normal"
        """
        return self._state in ("paused", "stabilizing")

    def get_time_until_resume(self) -> Optional[int]:
        """Get time remaining until resume from stabilizing state.

        Returns:
            Seconds remaining if stabilizing, None otherwise
        """
        if self._state != "stabilizing" or self._stabilization_start is None:
            return None

        if self._last_timestamp is None:
            return self._stabilization_delay

        elapsed = (self._last_timestamp - self._stabilization_start).total_seconds()
        remaining = max(0, int(self._stabilization_delay - elapsed))

        return remaining
