"""Predictive preheat learning for adaptive thermostat.

Learns heating rates based on temperature delta and outdoor conditions,
then estimates time-to-target for early start scheduling.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import statistics

from ..const import (
    HEATING_TYPE_PREHEAT_CONFIG,
    PREHEAT_OBSERVATION_WINDOW_DAYS,
    PREHEAT_MAX_OBSERVATIONS_PER_BIN,
    PREHEAT_MIN_OBSERVATIONS_FOR_LEARNING,
)


@dataclass
class HeatingObservation:
    """Single heating rate observation."""

    start_temp: float
    end_temp: float
    outdoor_temp: float
    duration_minutes: float
    rate: float  # C/hour
    timestamp: datetime


class PreheatLearner:
    """Learns heating rates and estimates time-to-target for preheat scheduling.

    Observations are binned by temperature delta and outdoor temperature to
    account for thermal mass effects and outdoor heat loss.

    Delta bins: 0-2C, 2-4C, 4-6C, 6+C
    Outdoor bins: cold (<5C), mild (5-12C), moderate (>12C)
    """

    def __init__(
        self,
        heating_type: str,
        max_hours: Optional[float] = None,
    ):
        """Initialize preheat learner.

        Args:
            heating_type: Heating system type (from HEATING_TYPE_*)
            max_hours: Maximum preheat hours (defaults to config value)
        """
        self.heating_type = heating_type
        config = HEATING_TYPE_PREHEAT_CONFIG[heating_type]

        self.max_hours = max_hours if max_hours is not None else config["max_hours"]
        self.cold_soak_margin = config["cold_soak_margin"]
        self.fallback_rate = config["fallback_rate"]

        # Observations keyed by (delta_bin, outdoor_bin)
        self._observations: Dict[Tuple[str, str], List[HeatingObservation]] = {}

    def get_delta_bin(self, delta: float) -> str:
        """Get temperature delta bin.

        Args:
            delta: Temperature delta in C

        Returns:
            Bin key: "0-2", "2-4", "4-6", or "6+"
        """
        if delta < 2.0:
            return "0-2"
        elif delta < 4.0:
            return "2-4"
        elif delta < 6.0:
            return "4-6"
        else:
            return "6+"

    def get_outdoor_bin(self, outdoor_temp: float) -> str:
        """Get outdoor temperature bin.

        Args:
            outdoor_temp: Outdoor temperature in C

        Returns:
            Bin key: "cold", "mild", or "moderate"
        """
        if outdoor_temp < 5.0:
            return "cold"
        elif outdoor_temp < 12.0:
            return "mild"
        else:
            return "moderate"

    def add_observation(
        self,
        start_temp: float,
        end_temp: float,
        outdoor_temp: float,
        duration_minutes: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Add heating rate observation.

        Args:
            start_temp: Starting temperature in C
            end_temp: Ending temperature in C
            outdoor_temp: Outdoor temperature in C
            duration_minutes: Heating duration in minutes
            timestamp: Observation timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        delta = end_temp - start_temp
        if delta <= 0 or duration_minutes <= 0:
            return  # Invalid observation

        # Calculate rate in C/hour
        rate = delta / (duration_minutes / 60.0)

        # Determine bins
        delta_bin = self.get_delta_bin(delta)
        outdoor_bin = self.get_outdoor_bin(outdoor_temp)
        bin_key = (delta_bin, outdoor_bin)

        # Create observation
        obs = HeatingObservation(
            start_temp=start_temp,
            end_temp=end_temp,
            outdoor_temp=outdoor_temp,
            duration_minutes=duration_minutes,
            rate=rate,
            timestamp=timestamp,
        )

        # Add to bin
        if bin_key not in self._observations:
            self._observations[bin_key] = []
        self._observations[bin_key].append(obs)

        # Limit observations per bin
        if len(self._observations[bin_key]) > PREHEAT_MAX_OBSERVATIONS_PER_BIN:
            # Remove oldest
            self._observations[bin_key].pop(0)

        # Expire old observations across all bins
        self._expire_old_observations(timestamp)

    def _expire_old_observations(self, current_time: datetime) -> None:
        """Remove observations older than 90 days.

        Args:
            current_time: Current timestamp
        """
        cutoff = current_time - timedelta(days=PREHEAT_OBSERVATION_WINDOW_DAYS)

        for bin_key in list(self._observations.keys()):
            # Filter out old observations
            self._observations[bin_key] = [
                obs for obs in self._observations[bin_key]
                if obs.timestamp >= cutoff
            ]

            # Remove empty bins
            if not self._observations[bin_key]:
                del self._observations[bin_key]

    def estimate_time_to_target(
        self,
        current_temp: float,
        target_temp: float,
        outdoor_temp: float,
    ) -> float:
        """Estimate time needed to reach target temperature.

        Args:
            current_temp: Current temperature in C
            target_temp: Target temperature in C
            outdoor_temp: Outdoor temperature in C

        Returns:
            Estimated time in minutes (0 if already at/above target)
        """
        delta = target_temp - current_temp
        if delta <= 0:
            return 0.0

        # Get bin
        delta_bin = self.get_delta_bin(delta)
        outdoor_bin = self.get_outdoor_bin(outdoor_temp)
        bin_key = (delta_bin, outdoor_bin)

        # Get observations for this bin
        observations = self._observations.get(bin_key, [])

        # Determine rate
        if len(observations) >= PREHEAT_MIN_OBSERVATIONS_FOR_LEARNING:
            # Use median of last 10 observations
            recent_rates = [obs.rate for obs in observations[-10:]]
            base_rate = statistics.median(recent_rates)
        else:
            # Use fallback rate
            base_rate = self.fallback_rate

        # Calculate margin (scales with delta)
        # margin = (1.0 + delta/10 * 0.3) * cold_soak_margin
        margin = (1.0 + delta / 10.0 * 0.3) * self.cold_soak_margin

        # Calculate time
        time_minutes = (delta / base_rate) * 60.0 * margin

        # Clamp to max_hours
        max_minutes = self.max_hours * 60.0
        return min(time_minutes, max_minutes)

    def get_confidence(self) -> float:
        """Get learning confidence based on observation count.

        Returns:
            Confidence from 0.0 (no data) to 1.0 (10+ observations)
        """
        total_observations = self.get_observation_count()
        if total_observations == 0:
            return 0.0
        return min(total_observations / 10.0, 1.0)

    def get_observation_count(self) -> int:
        """Get total observation count across all bins.

        Returns:
            Total number of observations
        """
        return sum(len(obs) for obs in self._observations.values())

    def get_learned_rate(
        self,
        delta: float,
        outdoor_temp: float,
    ) -> Optional[float]:
        """Get learned heating rate for specific conditions.

        Args:
            delta: Temperature delta in C
            outdoor_temp: Outdoor temperature in C

        Returns:
            Median learned rate in C/hour, or None if no data
        """
        delta_bin = self.get_delta_bin(delta)
        outdoor_bin = self.get_outdoor_bin(outdoor_temp)
        bin_key = (delta_bin, outdoor_bin)

        observations = self._observations.get(bin_key, [])
        if not observations:
            return None

        rates = [obs.rate for obs in observations]
        return statistics.median(rates)

    def to_dict(self) -> dict:
        """Serialize learner state to dict.

        Returns:
            Dictionary containing all learner state
        """
        observations_list = []
        for bin_key, obs_list in self._observations.items():
            for obs in obs_list:
                observations_list.append({
                    "bin_key": bin_key,
                    "start_temp": obs.start_temp,
                    "end_temp": obs.end_temp,
                    "outdoor_temp": obs.outdoor_temp,
                    "duration_minutes": obs.duration_minutes,
                    "rate": obs.rate,
                    "timestamp": obs.timestamp.isoformat(),
                })

        return {
            "heating_type": self.heating_type,
            "max_hours": self.max_hours,
            "observations": observations_list,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PreheatLearner":
        """Deserialize learner state from dict.

        Args:
            data: Dictionary from to_dict()

        Returns:
            Restored PreheatLearner instance
        """
        learner = cls(
            heating_type=data["heating_type"],
            max_hours=data["max_hours"],
        )

        # Restore observations
        for obs_data in data["observations"]:
            bin_key = tuple(obs_data["bin_key"])
            obs = HeatingObservation(
                start_temp=obs_data["start_temp"],
                end_temp=obs_data["end_temp"],
                outdoor_temp=obs_data["outdoor_temp"],
                duration_minutes=obs_data["duration_minutes"],
                rate=obs_data["rate"],
                timestamp=datetime.fromisoformat(obs_data["timestamp"]),
            )

            if bin_key not in learner._observations:
                learner._observations[bin_key] = []
            learner._observations[bin_key].append(obs)

        return learner
