"""Ke (outdoor temperature compensation) adaptive learning for Adaptive Thermostat.

This module provides automatic tuning of the Ke parameter based on observed
correlations between outdoor temperature and required heating effort (PID output).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import logging
import statistics

from homeassistant.util import dt as dt_util

from ..const import (
    KE_MIN_OBSERVATIONS,
    KE_MIN_TEMP_RANGE,
    KE_ADJUSTMENT_INTERVAL,
    KE_ADJUSTMENT_STEP,
    KE_CORRELATION_THRESHOLD,
    KE_MAX_OBSERVATIONS,
    PID_LIMITS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class KeObservation:
    """A single observation for Ke learning.

    Records the outdoor temperature and steady-state PID output when the
    system is maintaining target temperature.
    """

    timestamp: datetime
    outdoor_temp: float  # Outdoor temperature in Celsius
    pid_output: float    # Steady-state PID control output (0-100%)
    indoor_temp: float   # Indoor temperature when observation was taken
    target_temp: float   # Target temperature at time of observation

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to dictionary for persistence."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "outdoor_temp": self.outdoor_temp,
            "pid_output": self.pid_output,
            "indoor_temp": self.indoor_temp,
            "target_temp": self.target_temp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KeObservation":
        """Create observation from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            outdoor_temp=data["outdoor_temp"],
            pid_output=data["pid_output"],
            indoor_temp=data["indoor_temp"],
            target_temp=data["target_temp"],
        )


class KeLearner:
    """Learn optimal Ke parameter from outdoor temperature correlations.

    Ke (external temperature compensation) adjusts the PID output based on
    outdoor temperature. A well-tuned Ke reduces the work the I-term must do
    to compensate for outdoor temperature changes.

    The learner observes the correlation between outdoor temperature and
    steady-state PID output. If there's a strong correlation and Ke is not
    yet compensating adequately, Ke is increased. If there's no correlation
    (Ke is overcompensating), Ke is decreased.

    Requirements for Ke learning:
    1. PID must be converged (stable performance for MIN_CONVERGENCE_CYCLES_FOR_KE cycles)
    2. Sufficient observations (KE_MIN_OBSERVATIONS)
    3. Sufficient outdoor temperature variation (KE_MIN_TEMP_RANGE)
    4. Rate limiting (KE_ADJUSTMENT_INTERVAL hours between adjustments)
    """

    def __init__(
        self,
        initial_ke: float = 0.0,
        max_observations: int = KE_MAX_OBSERVATIONS,
    ):
        """Initialize the KeLearner.

        Args:
            initial_ke: Starting Ke value (typically from physics-based calculation)
            max_observations: Maximum observations to retain (FIFO eviction)
        """
        self._current_ke = initial_ke
        self._observations: List[KeObservation] = []
        self._max_observations = max_observations
        self._enabled = False  # Disabled until PID converges
        self._last_adjustment_time: Optional[datetime] = None

    @property
    def enabled(self) -> bool:
        """Check if Ke learning is currently enabled."""
        return self._enabled

    @property
    def current_ke(self) -> float:
        """Get the current Ke value."""
        return self._current_ke

    @property
    def observation_count(self) -> int:
        """Get the number of stored observations."""
        return len(self._observations)

    def enable(self) -> None:
        """Enable Ke learning (called when PID converges)."""
        if not self._enabled:
            _LOGGER.info("Ke learning enabled - PID has converged")
            self._enabled = True

    def disable(self) -> None:
        """Disable Ke learning (called when PID diverges or is reset)."""
        if self._enabled:
            _LOGGER.info("Ke learning disabled - PID no longer converged")
            self._enabled = False

    def add_observation(
        self,
        outdoor_temp: float,
        pid_output: float,
        indoor_temp: float,
        target_temp: float,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """Add a new observation for Ke learning.

        Args:
            outdoor_temp: Current outdoor temperature in Celsius
            pid_output: Current PID control output (0-100%)
            indoor_temp: Current indoor temperature in Celsius
            target_temp: Current target temperature in Celsius
            timestamp: Observation timestamp (defaults to now)

        Returns:
            True if observation was added, False if learning is disabled
        """
        if not self._enabled:
            _LOGGER.debug("Ke observation rejected: learning disabled")
            return False

        if timestamp is None:
            timestamp = dt_util.utcnow()

        observation = KeObservation(
            timestamp=timestamp,
            outdoor_temp=outdoor_temp,
            pid_output=pid_output,
            indoor_temp=indoor_temp,
            target_temp=target_temp,
        )

        self._observations.append(observation)

        # FIFO eviction when exceeding max observations
        if len(self._observations) > self._max_observations:
            evicted = len(self._observations) - self._max_observations
            self._observations = self._observations[-self._max_observations:]
            _LOGGER.debug(
                "Ke observations exceeded max (%d), evicted %d oldest",
                self._max_observations,
                evicted,
            )

        _LOGGER.debug(
            "Ke observation added: outdoor=%.1f, pid_output=%.1f, "
            "indoor=%.1f, target=%.1f (total: %d)",
            outdoor_temp,
            pid_output,
            indoor_temp,
            target_temp,
            len(self._observations),
        )
        return True

    def _calculate_pearson_correlation(
        self,
        x_values: List[float],
        y_values: List[float],
    ) -> Optional[float]:
        """Calculate Pearson correlation coefficient between two variables.

        Uses the sample correlation formula: r = sum((x-mx)(y-my)) / (n-1) / (sx * sy)
        where sx, sy are sample standard deviations.

        Args:
            x_values: First variable values
            y_values: Second variable values

        Returns:
            Correlation coefficient (-1 to 1), or None if calculation fails
        """
        if len(x_values) != len(y_values) or len(x_values) < 2:
            return None

        n = len(x_values)
        mean_x = statistics.mean(x_values)
        mean_y = statistics.mean(y_values)

        # Calculate sample standard deviations
        std_x = statistics.stdev(x_values) if n > 1 else 0
        std_y = statistics.stdev(y_values) if n > 1 else 0

        # Avoid division by zero
        if std_x == 0 or std_y == 0:
            return None

        # Calculate covariance using sample formula (n-1 denominator)
        covariance = sum(
            (x - mean_x) * (y - mean_y) for x, y in zip(x_values, y_values)
        ) / (n - 1)

        correlation = covariance / (std_x * std_y)
        return correlation

    def _check_rate_limit(self) -> bool:
        """Check if enough time has passed since the last Ke adjustment.

        Returns:
            True if rate limited (should skip adjustment), False if OK to adjust
        """
        if self._last_adjustment_time is None:
            return False

        time_since_last = dt_util.utcnow() - self._last_adjustment_time
        min_interval = timedelta(hours=KE_ADJUSTMENT_INTERVAL)

        if time_since_last < min_interval:
            hours_remaining = (min_interval - time_since_last).total_seconds() / 3600
            _LOGGER.debug(
                "Ke adjustment rate limited: %.1f hours remaining",
                hours_remaining,
            )
            return True

        return False

    def calculate_ke_adjustment(self) -> Optional[float]:
        """Calculate recommended Ke adjustment based on observations.

        Analyzes the correlation between outdoor temperature and PID output.
        A strong negative correlation (lower outdoor temp -> higher PID output)
        suggests Ke should be increased to proactively compensate.

        Returns:
            New recommended Ke value, or None if:
            - Learning is disabled
            - Insufficient observations
            - Insufficient temperature range
            - Rate limited
            - No significant correlation found
        """
        if not self._enabled:
            _LOGGER.debug("Ke adjustment skipped: learning disabled")
            return None

        if len(self._observations) < KE_MIN_OBSERVATIONS:
            _LOGGER.debug(
                "Ke adjustment skipped: insufficient observations (%d < %d)",
                len(self._observations),
                KE_MIN_OBSERVATIONS,
            )
            return None

        if self._check_rate_limit():
            return None

        # Extract observation data
        outdoor_temps = [obs.outdoor_temp for obs in self._observations]
        pid_outputs = [obs.pid_output for obs in self._observations]

        # Check temperature range
        temp_range = max(outdoor_temps) - min(outdoor_temps)
        if temp_range < KE_MIN_TEMP_RANGE:
            _LOGGER.debug(
                "Ke adjustment skipped: insufficient temperature range "
                "(%.1f < %.1f)",
                temp_range,
                KE_MIN_TEMP_RANGE,
            )
            return None

        # Calculate correlation
        correlation = self._calculate_pearson_correlation(outdoor_temps, pid_outputs)
        if correlation is None:
            _LOGGER.debug("Ke adjustment skipped: correlation calculation failed")
            return None

        _LOGGER.info(
            "Ke correlation analysis: r=%.3f (outdoor temp vs PID output), "
            "temp range=%.1f, observations=%d",
            correlation,
            temp_range,
            len(self._observations),
        )

        # Determine adjustment direction based on correlation
        # Negative correlation = lower outdoor temp needs higher PID output
        # This is expected behavior; if correlation is still strongly negative,
        # Ke needs to increase to compensate more
        # If correlation is weakly negative or positive, Ke may be overcompensating

        new_ke = self._current_ke

        if correlation < -KE_CORRELATION_THRESHOLD:
            # Strong negative correlation: increase Ke
            new_ke = self._current_ke + KE_ADJUSTMENT_STEP
            _LOGGER.info(
                "Ke increase recommended: correlation %.3f indicates "
                "insufficient outdoor compensation. Ke: %.2f -> %.2f",
                correlation,
                self._current_ke,
                new_ke,
            )
        elif correlation > KE_CORRELATION_THRESHOLD:
            # Strong positive correlation: decrease Ke (overcompensating)
            new_ke = self._current_ke - KE_ADJUSTMENT_STEP
            _LOGGER.info(
                "Ke decrease recommended: correlation %.3f indicates "
                "overcompensation. Ke: %.2f -> %.2f",
                correlation,
                self._current_ke,
                new_ke,
            )
        else:
            # Weak correlation: Ke is well-tuned
            _LOGGER.info(
                "Ke well-tuned: correlation %.3f within threshold (%.2f)",
                correlation,
                KE_CORRELATION_THRESHOLD,
            )
            return None

        # Enforce Ke limits
        new_ke = max(PID_LIMITS["ke_min"], min(PID_LIMITS["ke_max"], new_ke))

        # Skip if no effective change (v0.7.0: threshold scaled to match new Ke scale)
        if abs(new_ke - self._current_ke) < 0.0001:
            _LOGGER.debug("Ke adjustment skipped: at limit")
            return None

        return new_ke

    def apply_ke_adjustment(self, new_ke: float) -> None:
        """Apply a Ke adjustment and update internal state.

        Args:
            new_ke: The new Ke value to apply
        """
        old_ke = self._current_ke
        self._current_ke = new_ke
        self._last_adjustment_time = dt_util.utcnow()

        # Clear old observations to start fresh with new Ke
        self._observations.clear()

        _LOGGER.info(
            "Ke adjustment applied: %.2f -> %.2f (observations cleared)",
            old_ke,
            new_ke,
        )

    def get_last_adjustment_time(self) -> Optional[datetime]:
        """Get the timestamp of the last Ke adjustment."""
        return self._last_adjustment_time

    def get_observations_summary(self) -> Dict[str, Any]:
        """Get a summary of current observations for diagnostics.

        Returns:
            Dictionary with observation statistics
        """
        if not self._observations:
            return {
                "count": 0,
                "outdoor_temp_range": None,
                "pid_output_range": None,
                "correlation": None,
            }

        outdoor_temps = [obs.outdoor_temp for obs in self._observations]
        pid_outputs = [obs.pid_output for obs in self._observations]

        correlation = None
        if len(self._observations) >= 2:
            correlation = self._calculate_pearson_correlation(outdoor_temps, pid_outputs)

        return {
            "count": len(self._observations),
            "outdoor_temp_min": min(outdoor_temps),
            "outdoor_temp_max": max(outdoor_temps),
            "outdoor_temp_range": max(outdoor_temps) - min(outdoor_temps),
            "pid_output_min": min(pid_outputs),
            "pid_output_max": max(pid_outputs),
            "correlation": round(correlation, 3) if correlation is not None else None,
        }

    def clear_observations(self) -> None:
        """Clear all stored observations."""
        count = len(self._observations)
        self._observations.clear()
        _LOGGER.info("Ke observations cleared (%d removed)", count)

    def to_dict(self) -> Dict[str, Any]:
        """Convert learner state to dictionary for persistence.

        Returns:
            Dictionary with all learner state
        """
        return {
            "current_ke": self._current_ke,
            "enabled": self._enabled,
            "max_observations": self._max_observations,
            "last_adjustment_time": (
                self._last_adjustment_time.isoformat()
                if self._last_adjustment_time
                else None
            ),
            "observations": [obs.to_dict() for obs in self._observations],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KeLearner":
        """Restore learner state from dictionary.

        Args:
            data: Dictionary with learner state from to_dict()

        Returns:
            Restored KeLearner instance
        """
        learner = cls(
            initial_ke=data.get("current_ke", 0.0),
            max_observations=data.get("max_observations", KE_MAX_OBSERVATIONS),
        )

        learner._enabled = data.get("enabled", False)

        if data.get("last_adjustment_time"):
            learner._last_adjustment_time = datetime.fromisoformat(
                data["last_adjustment_time"]
            )

        # Restore observations
        for obs_data in data.get("observations", []):
            try:
                observation = KeObservation.from_dict(obs_data)
                learner._observations.append(observation)
            except (KeyError, ValueError) as e:
                _LOGGER.warning("Failed to restore Ke observation: %s", e)

        _LOGGER.info(
            "KeLearner restored: ke=%.2f, enabled=%s, observations=%d",
            learner._current_ke,
            learner._enabled,
            len(learner._observations),
        )

        return learner
