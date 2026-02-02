"""Unified heating rate learning from cycle and session data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class HeatingRateObservation:
    """Single heating rate observation."""

    rate: float  # degrees C per hour
    duration_min: float  # session/cycle duration in minutes
    source: str  # "cycle" or "session"
    stalled: bool  # True if ended without reaching setpoint
    timestamp: datetime


@dataclass
class RecoverySession:
    """Tracks an active recovery session spanning multiple cycles."""

    start_temp: float
    start_time: datetime
    target_setpoint: float
    outdoor_temp: float  # snapshot at session start
    cycles_in_session: int = 0
    cycle_duties: list[float] = field(default_factory=list)
    last_progress_cycle: int = 0
    last_temp: float | None = None  # for progress detection


# Bin boundaries
DELTA_BINS = [(0, 2), (2, 4), (4, 6), (6, float("inf"))]
DELTA_BIN_NAMES = ["delta_0_2", "delta_2_4", "delta_4_6", "delta_6_plus"]
OUTDOOR_BINS = [(-float("inf"), 5), (5, 15), (15, float("inf"))]
OUTDOOR_BIN_NAMES = ["cold", "mild", "moderate"]

# Minimum session duration by heating type (minutes)
MIN_SESSION_DURATION: dict[str, int] = {
    "floor_hydronic": 60,
    "radiator": 30,
    "convector": 15,
    "forced_air": 10,
}


class HeatingRateLearner:
    """Unified heating rate learning from cycle and session data."""

    MAX_OBSERVATIONS_PER_BIN = 20
    MIN_OBSERVATIONS_FOR_RATE = 3
    PROGRESS_THRESHOLD = 0.1  # Minimum temp rise to count as progress
    STALL_CYCLES = 3  # Cycles without progress to declare stall

    # Fallback rates by heating type (degrees C per hour)
    FALLBACK_RATES: dict[str, float] = {
        "floor_hydronic": 0.15,
        "radiator": 0.3,
        "convector": 0.6,
        "forced_air": 1.0,
    }

    def __init__(self, heating_type: str) -> None:
        """Initialize learner.

        Args:
            heating_type: HeatingType enum value (e.g., "floor_hydronic")
        """
        self._heating_type = heating_type
        self._bins: dict[str, list[HeatingRateObservation]] = {}
        self._active_session: RecoverySession | None = None
        self._stall_counter: int = 0
        self._last_stall_outdoor: float | None = None
        self._last_stall_setpoint: float | None = None

        # Initialize all 12 bins
        for delta_name in DELTA_BIN_NAMES:
            for outdoor_name in OUTDOOR_BIN_NAMES:
                key = f"{delta_name}_{outdoor_name}"
                self._bins[key] = []

    def _get_bin_key(self, delta: float, outdoor_temp: float) -> str:
        """Get bin key for given delta and outdoor temp."""
        # Find delta bin
        delta_name = DELTA_BIN_NAMES[-1]  # default to largest
        for i, (low, high) in enumerate(DELTA_BINS):
            if low <= delta < high:
                delta_name = DELTA_BIN_NAMES[i]
                break

        # Find outdoor bin
        outdoor_name = OUTDOOR_BIN_NAMES[-1]  # default to warmest
        for i, (low, high) in enumerate(OUTDOOR_BINS):
            if low <= outdoor_temp < high:
                outdoor_name = OUTDOOR_BIN_NAMES[i]
                break

        return f"{delta_name}_{outdoor_name}"

    def add_observation(
        self,
        rate: float,
        duration_min: float,
        source: str,
        stalled: bool,
        delta: float,
        outdoor_temp: float,
        timestamp: datetime | None = None,
    ) -> None:
        """Add a heating rate observation to the appropriate bin.

        Args:
            rate: Heating rate in degrees C per hour
            duration_min: Duration of session/cycle in minutes
            source: "session" or "cycle"
            stalled: True if session ended without reaching setpoint
            delta: Temperature delta (setpoint - start_temp)
            outdoor_temp: Outdoor temperature at session start
            timestamp: Observation timestamp (defaults to now)
        """
        from homeassistant.util import dt as dt_util

        if timestamp is None:
            timestamp = dt_util.utcnow()

        obs = HeatingRateObservation(
            rate=rate,
            duration_min=duration_min,
            source=source,
            stalled=stalled,
            timestamp=timestamp,
        )

        bin_key = self._get_bin_key(delta, outdoor_temp)
        self._bins[bin_key].append(obs)

        # Cap at max observations, keep newest
        if len(self._bins[bin_key]) > self.MAX_OBSERVATIONS_PER_BIN:
            self._bins[bin_key] = self._bins[bin_key][-self.MAX_OBSERVATIONS_PER_BIN :]

    def get_observation_count(self) -> int:
        """Get total observation count across all bins."""
        return sum(len(obs_list) for obs_list in self._bins.values())

    def get_heating_rate(
        self, delta: float, outdoor_temp: float
    ) -> tuple[float, str]:
        """Get heating rate for given conditions.

        Args:
            delta: Temperature delta (setpoint - current_temp)
            outdoor_temp: Current outdoor temperature

        Returns:
            Tuple of (rate in degrees C/hour, source string)
            source: "learned_session", "learned_cycle", or "fallback"
        """
        bin_key = self._get_bin_key(delta, outdoor_temp)
        observations = self._bins[bin_key]

        # Try session observations first (≥3 required)
        session_obs = [o for o in observations if o.source == "session"]
        if len(session_obs) >= self.MIN_OBSERVATIONS_FOR_RATE:
            avg_rate = sum(o.rate for o in session_obs) / len(session_obs)
            return (avg_rate, "learned_session")

        # Try cycle observations (≥3 required)
        cycle_obs = [o for o in observations if o.source == "cycle"]
        if len(cycle_obs) >= self.MIN_OBSERVATIONS_FOR_RATE:
            avg_rate = sum(o.rate for o in cycle_obs) / len(cycle_obs)
            return (avg_rate, "learned_cycle")

        # Fallback to heating type default
        fallback = self.FALLBACK_RATES.get(self._heating_type, 0.3)
        return (fallback, "fallback")

    def start_session(
        self,
        temp: float,
        setpoint: float,
        outdoor_temp: float,
        timestamp: datetime | None = None,
    ) -> None:
        """Start tracking a recovery session.

        Args:
            temp: Current temperature
            setpoint: Target setpoint
            outdoor_temp: Current outdoor temperature
            timestamp: Session start time (defaults to now)
        """
        from homeassistant.util import dt as dt_util

        if timestamp is None:
            timestamp = dt_util.utcnow()

        self._active_session = RecoverySession(
            start_temp=temp,
            start_time=timestamp,
            target_setpoint=setpoint,
            outdoor_temp=outdoor_temp,
        )
        self._active_session.last_temp = temp

    def end_session(
        self,
        end_temp: float,
        reason: str,
        timestamp: datetime | None = None,
    ) -> HeatingRateObservation | None:
        """End the current recovery session.

        Args:
            end_temp: Final temperature at session end
            reason: End reason ("reached_setpoint", "stalled", "override")
            timestamp: Session end time (defaults to now)

        Returns:
            HeatingRateObservation if session was valid and banked, None if discarded
        """
        from homeassistant.util import dt as dt_util

        if self._active_session is None:
            return None

        if timestamp is None:
            timestamp = dt_util.utcnow()

        session = self._active_session
        self._active_session = None

        # Discard if override interrupted
        if reason == "override":
            return None

        # Calculate duration
        duration_min = (timestamp - session.start_time).total_seconds() / 60.0

        # Discard if too short
        min_duration = MIN_SESSION_DURATION.get(self._heating_type, 30)
        if duration_min < min_duration:
            return None

        # Calculate rate
        temp_rise = end_temp - session.start_temp
        duration_hours = duration_min / 60.0
        rate = temp_rise / duration_hours if duration_hours > 0 else 0.0

        # Determine if stalled
        stalled = reason == "stalled"

        # Bank observation
        delta = session.target_setpoint - session.start_temp
        self.add_observation(
            rate=rate,
            duration_min=duration_min,
            source="session",
            stalled=stalled,
            delta=delta,
            outdoor_temp=session.outdoor_temp,
            timestamp=timestamp,
        )

        return self._bins[self._get_bin_key(delta, session.outdoor_temp)][-1]

    def update_session(self, temp: float, duty: float) -> None:
        """Update session with cycle completion data."""
        if self._active_session is None:
            return

        session = self._active_session
        session.cycles_in_session += 1
        session.cycle_duties.append(duty)

        # Check for progress
        if session.last_temp is not None:
            temp_rise = temp - session.last_temp
            if temp_rise >= self.PROGRESS_THRESHOLD:
                session.last_progress_cycle = session.cycles_in_session

        session.last_temp = temp

    def is_stalled(self) -> bool:
        """Check if current session is stalled (no progress for 3 cycles)."""
        if self._active_session is None:
            return False

        session = self._active_session
        cycles_since_progress = session.cycles_in_session - session.last_progress_cycle
        return cycles_since_progress >= self.STALL_CYCLES

    def get_avg_session_duty(self) -> float | None:
        """Get average duty for current session."""
        if self._active_session is None or not self._active_session.cycle_duties:
            return None
        return sum(self._active_session.cycle_duties) / len(self._active_session.cycle_duties)
