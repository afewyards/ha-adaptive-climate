"""Ke-First learning: Learn outdoor temperature compensation before PID tuning.

This module implements a "Ke-First" approach where the outdoor temperature
compensation parameter (Ke) is learned before PID tuning begins. This ensures
that PID gains are tuned with the correct external disturbance compensation
already in place, leading to better overall control performance.

The learning process:
1. Detect steady-state periods (stable duty cycle for 60+ minutes)
2. Collect temperature drop rates when heater is off
3. Calculate Ke from correlation: dT_indoor/dt vs (T_indoor - T_outdoor)
4. Use linear regression with R² > 0.7 threshold for convergence
5. Block PID tuning until Ke has converged
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
import logging
import statistics

from ..const import (
    KE_MIN_OBSERVATIONS,
    KE_MIN_TEMP_RANGE,
    PID_LIMITS,
)

_LOGGER = logging.getLogger(__name__)

# Constants for Ke-First learning
KE_FIRST_MIN_CYCLES = 10  # Minimum steady-state cycles for Ke learning
KE_FIRST_MIN_CYCLES_STRICT = 15  # Preferred minimum for better accuracy
KE_FIRST_R_SQUARED_THRESHOLD = 0.7  # R² threshold for convergence
KE_FIRST_STEADY_STATE_DURATION_MIN = 60  # Minutes of stable duty cycle
KE_FIRST_DUTY_CYCLE_TOLERANCE = 0.05  # ±5% duty cycle variation
KE_FIRST_MIN_TEMP_DROP_RATE = 0.01  # Minimum °C/hour temperature drop to record


@dataclass
class SteadyStateCycle:
    """A single steady-state observation for Ke-First learning.

    Records temperature drop rate when heater is off during a period when
    the system has been in steady state (maintaining target temperature).
    """

    timestamp: datetime
    outdoor_temp: float  # Average outdoor temperature during cycle (°C)
    indoor_temp_start: float  # Indoor temp at start of off period (°C)
    indoor_temp_end: float  # Indoor temp at end of off period (°C)
    duration_hours: float  # Duration of off period (hours)
    temp_drop_rate: float  # Temperature drop rate (°C/hour)
    temp_difference: float  # Indoor - outdoor temperature difference (°C)
    duty_cycle: float  # Average duty cycle during steady state (0-1)

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to dictionary for persistence."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "outdoor_temp": self.outdoor_temp,
            "indoor_temp_start": self.indoor_temp_start,
            "indoor_temp_end": self.indoor_temp_end,
            "duration_hours": self.duration_hours,
            "temp_drop_rate": self.temp_drop_rate,
            "temp_difference": self.temp_difference,
            "duty_cycle": self.duty_cycle,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SteadyStateCycle":
        """Create observation from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            outdoor_temp=data["outdoor_temp"],
            indoor_temp_start=data["indoor_temp_start"],
            indoor_temp_end=data["indoor_temp_end"],
            duration_hours=data["duration_hours"],
            temp_drop_rate=data["temp_drop_rate"],
            temp_difference=data["temp_difference"],
            duty_cycle=data["duty_cycle"],
        )


class KeFirstLearner:
    """Learn Ke parameter before PID tuning using temperature drop correlations.

    The Ke-First approach recognizes that outdoor temperature compensation (Ke)
    is a fundamental parameter that should be learned before tuning PID gains.
    This prevents the integral term from compensating for outdoor temperature
    changes, which would lead to suboptimal PID gains.

    Learning method:
    - Detect steady-state periods (duty cycle stable ±5% for 60+ minutes)
    - Track temperature drop when heater is off
    - Calculate correlation: dT/dt vs (T_indoor - T_outdoor)
    - Use linear regression: Ke = -slope of regression line
    - Require R² > 0.7 for convergence (strong correlation)

    Blocks PID tuning until:
    - Minimum 10-15 steady-state cycles collected
    - Sufficient outdoor temperature range (>5°C)
    - Strong correlation (R² > 0.7)
    """

    def __init__(
        self,
        initial_ke: float = 0.0,
        min_cycles: int = KE_FIRST_MIN_CYCLES,
        min_cycles_strict: int = KE_FIRST_MIN_CYCLES_STRICT,
    ):
        """Initialize the KeFirstLearner.

        Args:
            initial_ke: Starting Ke value (typically from physics-based calculation)
            min_cycles: Minimum cycles required for Ke calculation
            min_cycles_strict: Preferred minimum for better accuracy
        """
        self._initial_ke = initial_ke
        self._current_ke = initial_ke
        self._min_cycles = min_cycles
        self._min_cycles_strict = min_cycles_strict
        self._cycles: List[SteadyStateCycle] = []
        self._converged = False
        self._r_squared: Optional[float] = None
        self._convergence_time: Optional[datetime] = None

        # Steady-state detection state
        self._steady_state_start: Optional[datetime] = None
        self._steady_duty_cycle: Optional[float] = None
        self._off_period_start: Optional[datetime] = None
        self._off_period_start_temp: Optional[float] = None
        self._off_period_outdoor_temp: Optional[float] = None

    @property
    def converged(self) -> bool:
        """Check if Ke learning has converged."""
        return self._converged

    @property
    def current_ke(self) -> float:
        """Get the current learned Ke value."""
        return self._current_ke

    @property
    def r_squared(self) -> Optional[float]:
        """Get the R² value from linear regression."""
        return self._r_squared

    @property
    def cycle_count(self) -> int:
        """Get the number of collected steady-state cycles."""
        return len(self._cycles)

    @property
    def convergence_progress(self) -> float:
        """Get convergence progress as a percentage (0-100)."""
        if self._converged:
            return 100.0

        # Progress is based on:
        # - Cycle collection (70% weight)
        # - Temperature range (15% weight)
        # - R² value (15% weight)

        cycle_progress = min(100.0, (len(self._cycles) / self._min_cycles_strict) * 100)

        temp_range = self._get_temperature_range()
        temp_progress = min(100.0, (temp_range / KE_MIN_TEMP_RANGE) * 100) if temp_range else 0.0

        r_squared_progress = (self._r_squared / KE_FIRST_R_SQUARED_THRESHOLD * 100) if self._r_squared else 0.0

        total_progress = (
            cycle_progress * 0.70 +
            temp_progress * 0.15 +
            r_squared_progress * 0.15
        )

        return min(100.0, total_progress)

    def detect_steady_state(
        self,
        current_duty_cycle: float,
        current_time: Optional[datetime] = None,
    ) -> bool:
        """Detect if system is in steady state (stable duty cycle).

        Steady state is defined as:
        - Duty cycle stable within ±5% for 60+ minutes

        Args:
            current_duty_cycle: Current heater duty cycle (0-1)
            current_time: Current timestamp (defaults to now)

        Returns:
            True if in steady state, False otherwise
        """
        if current_time is None:
            current_time = datetime.now()

        # Initialize tracking
        if self._steady_state_start is None or self._steady_duty_cycle is None:
            self._steady_state_start = current_time
            self._steady_duty_cycle = current_duty_cycle
            return False

        # Check if duty cycle is still stable
        duty_cycle_change = abs(current_duty_cycle - self._steady_duty_cycle)
        if duty_cycle_change > KE_FIRST_DUTY_CYCLE_TOLERANCE:
            # Duty cycle changed too much - reset
            self._steady_state_start = current_time
            self._steady_duty_cycle = current_duty_cycle
            return False

        # Check if we've been stable long enough
        duration = (current_time - self._steady_state_start).total_seconds() / 60  # minutes
        return duration >= KE_FIRST_STEADY_STATE_DURATION_MIN

    def on_heater_off(
        self,
        indoor_temp: float,
        outdoor_temp: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Called when heater turns off during steady state.

        Args:
            indoor_temp: Current indoor temperature
            outdoor_temp: Current outdoor temperature
            timestamp: Event timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        self._off_period_start = timestamp
        self._off_period_start_temp = indoor_temp
        self._off_period_outdoor_temp = outdoor_temp

    def on_heater_on(
        self,
        indoor_temp: float,
        outdoor_temp: float,
        duty_cycle: float,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """Called when heater turns on, ending an off period.

        If we were tracking an off period during steady state, calculate and
        record the temperature drop rate.

        Args:
            indoor_temp: Current indoor temperature
            outdoor_temp: Current outdoor temperature
            duty_cycle: Average duty cycle during steady state period
            timestamp: Event timestamp (defaults to now)

        Returns:
            True if a cycle was recorded, False otherwise
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Check if we were tracking an off period
        if self._off_period_start is None or self._off_period_start_temp is None:
            return False

        # Calculate duration and temperature drop
        duration_hours = (timestamp - self._off_period_start).total_seconds() / 3600
        if duration_hours < 0.1:  # Less than 6 minutes - too short
            self._reset_off_period_tracking()
            return False

        temp_drop = self._off_period_start_temp - indoor_temp
        temp_drop_rate = temp_drop / duration_hours  # °C/hour

        # Only record significant temperature drops
        if temp_drop_rate < KE_FIRST_MIN_TEMP_DROP_RATE:
            self._reset_off_period_tracking()
            return False

        # Calculate temperature difference (average of start and end)
        avg_indoor_temp = (self._off_period_start_temp + indoor_temp) / 2
        avg_outdoor_temp = (self._off_period_outdoor_temp + outdoor_temp) / 2
        temp_difference = avg_indoor_temp - avg_outdoor_temp

        # Create and store cycle
        cycle = SteadyStateCycle(
            timestamp=self._off_period_start,
            outdoor_temp=avg_outdoor_temp,
            indoor_temp_start=self._off_period_start_temp,
            indoor_temp_end=indoor_temp,
            duration_hours=duration_hours,
            temp_drop_rate=temp_drop_rate,
            temp_difference=temp_difference,
            duty_cycle=duty_cycle,
        )

        self._cycles.append(cycle)
        _LOGGER.info(
            "Ke-First cycle recorded: outdoor=%.1f°C, temp_diff=%.1f°C, "
            "drop_rate=%.2f°C/h, duration=%.1fh (total: %d cycles)",
            avg_outdoor_temp,
            temp_difference,
            temp_drop_rate,
            duration_hours,
            len(self._cycles),
        )

        self._reset_off_period_tracking()

        # Check for convergence after adding cycle
        if not self._converged:
            self._check_convergence()

        return True

    def _reset_off_period_tracking(self) -> None:
        """Reset off period tracking state."""
        self._off_period_start = None
        self._off_period_start_temp = None
        self._off_period_outdoor_temp = None

    def _reset_steady_state_tracking(self) -> None:
        """Reset steady state tracking."""
        self._steady_state_start = None
        self._steady_duty_cycle = None

    def _get_temperature_range(self) -> float:
        """Get outdoor temperature range from collected cycles."""
        if not self._cycles:
            return 0.0

        outdoor_temps = [c.outdoor_temp for c in self._cycles]
        return max(outdoor_temps) - min(outdoor_temps)

    def _calculate_linear_regression(
        self,
        x_values: List[float],
        y_values: List[float],
    ) -> Tuple[float, float, float]:
        """Calculate linear regression: y = slope * x + intercept.

        Also calculates R² (coefficient of determination) to measure fit quality.

        Args:
            x_values: Independent variable (temperature difference)
            y_values: Dependent variable (temperature drop rate)

        Returns:
            Tuple of (slope, intercept, r_squared)
        """
        if len(x_values) != len(y_values) or len(x_values) < 2:
            raise ValueError("Need at least 2 points for linear regression")

        n = len(x_values)
        mean_x = statistics.mean(x_values)
        mean_y = statistics.mean(y_values)

        # Calculate slope using least squares
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_values, y_values))
        denominator = sum((x - mean_x) ** 2 for x in x_values)

        if denominator == 0:
            raise ValueError("Cannot calculate slope: zero variance in x")

        slope = numerator / denominator
        intercept = mean_y - slope * mean_x

        # Calculate R² (coefficient of determination)
        y_pred = [slope * x + intercept for x in x_values]
        ss_res = sum((y - y_p) ** 2 for y, y_p in zip(y_values, y_pred))
        ss_tot = sum((y - mean_y) ** 2 for y in y_values)

        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        return slope, intercept, r_squared

    def _check_convergence(self) -> None:
        """Check if Ke learning has converged and update state."""
        if self._converged:
            return

        # Need minimum cycles
        if len(self._cycles) < self._min_cycles:
            _LOGGER.debug(
                "Ke-First: Not enough cycles (%d < %d)",
                len(self._cycles),
                self._min_cycles,
            )
            return

        # Need sufficient temperature range
        temp_range = self._get_temperature_range()
        if temp_range < KE_MIN_TEMP_RANGE:
            _LOGGER.debug(
                "Ke-First: Insufficient temperature range (%.1f < %.1f°C)",
                temp_range,
                KE_MIN_TEMP_RANGE,
            )
            return

        # Calculate Ke from linear regression
        try:
            ke, r_squared = self._calculate_ke_from_regression()
        except (ValueError, ZeroDivisionError) as e:
            _LOGGER.warning("Ke-First: Regression failed: %s", e)
            return

        self._r_squared = r_squared

        # Check R² threshold
        if r_squared < KE_FIRST_R_SQUARED_THRESHOLD:
            _LOGGER.info(
                "Ke-First: R²=%.3f below threshold %.2f (cycles=%d, temp_range=%.1f°C)",
                r_squared,
                KE_FIRST_R_SQUARED_THRESHOLD,
                len(self._cycles),
                temp_range,
            )
            return

        # Converged!
        self._current_ke = ke
        self._converged = True
        self._convergence_time = datetime.now()

        _LOGGER.info(
            "Ke-First CONVERGED: Ke=%.4f, R²=%.3f, cycles=%d, temp_range=%.1f°C",
            ke,
            r_squared,
            len(self._cycles),
            temp_range,
        )

    def _calculate_ke_from_regression(self) -> Tuple[float, float]:
        """Calculate Ke parameter from linear regression on steady-state cycles.

        The relationship is:
        - Temperature drop rate (dT/dt) vs. temperature difference (T_in - T_out)
        - Higher temp difference -> Higher drop rate (positive correlation)
        - Ke represents the proportional heat loss coefficient
        - Therefore: Ke = slope (direct relationship)

        Returns:
            Tuple of (ke, r_squared)
        """
        # Extract data
        temp_differences = [c.temp_difference for c in self._cycles]
        drop_rates = [c.temp_drop_rate for c in self._cycles]

        # Perform linear regression
        slope, intercept, r_squared = self._calculate_linear_regression(
            temp_differences, drop_rates
        )

        # Ke is the slope: drop_rate = Ke * temp_diff + intercept
        # Larger temp difference requires more heating to compensate for faster heat loss
        # Therefore: Ke = slope (direct relationship)
        ke = slope

        # Clamp to valid range
        ke = max(PID_LIMITS["ke_min"], min(PID_LIMITS["ke_max"], ke))

        _LOGGER.debug(
            "Ke regression: slope=%.4f, intercept=%.4f, R²=%.3f, Ke=%.4f",
            slope,
            intercept,
            r_squared,
            ke,
        )

        return ke, r_squared

    def calculate_ke(self) -> Optional[Tuple[float, float]]:
        """Calculate Ke parameter if sufficient data available.

        Returns:
            Tuple of (ke, r_squared) if calculable, None otherwise
        """
        if len(self._cycles) < self._min_cycles:
            return None

        temp_range = self._get_temperature_range()
        if temp_range < KE_MIN_TEMP_RANGE:
            return None

        try:
            return self._calculate_ke_from_regression()
        except (ValueError, ZeroDivisionError) as e:
            _LOGGER.warning("Ke calculation failed: %s", e)
            return None

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of Ke-First learning status.

        Returns:
            Dictionary with learning statistics
        """
        temp_range = self._get_temperature_range()

        return {
            "converged": self._converged,
            "current_ke": self._current_ke,
            "r_squared": self._r_squared,
            "cycle_count": len(self._cycles),
            "min_cycles": self._min_cycles,
            "min_cycles_strict": self._min_cycles_strict,
            "temperature_range": temp_range,
            "min_temperature_range": KE_MIN_TEMP_RANGE,
            "convergence_progress": self.convergence_progress,
            "convergence_time": (
                self._convergence_time.isoformat() if self._convergence_time else None
            ),
        }

    def reset(self) -> None:
        """Reset learning state (clear all cycles and convergence)."""
        self._cycles.clear()
        self._converged = False
        self._r_squared = None
        self._convergence_time = None
        self._current_ke = self._initial_ke
        self._reset_off_period_tracking()
        self._reset_steady_state_tracking()
        _LOGGER.info("Ke-First learning reset")

    def to_dict(self) -> Dict[str, Any]:
        """Convert learner state to dictionary for persistence.

        Returns:
            Dictionary with all learner state
        """
        return {
            "initial_ke": self._initial_ke,
            "current_ke": self._current_ke,
            "min_cycles": self._min_cycles,
            "min_cycles_strict": self._min_cycles_strict,
            "converged": self._converged,
            "r_squared": self._r_squared,
            "convergence_time": (
                self._convergence_time.isoformat() if self._convergence_time else None
            ),
            "cycles": [c.to_dict() for c in self._cycles],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KeFirstLearner":
        """Restore learner state from dictionary.

        Args:
            data: Dictionary with learner state from to_dict()

        Returns:
            Restored KeFirstLearner instance
        """
        learner = cls(
            initial_ke=data.get("initial_ke", 0.0),
            min_cycles=data.get("min_cycles", KE_FIRST_MIN_CYCLES),
            min_cycles_strict=data.get("min_cycles_strict", KE_FIRST_MIN_CYCLES_STRICT),
        )

        learner._current_ke = data.get("current_ke", learner._initial_ke)
        learner._converged = data.get("converged", False)
        learner._r_squared = data.get("r_squared")

        if data.get("convergence_time"):
            learner._convergence_time = datetime.fromisoformat(data["convergence_time"])

        # Restore cycles
        for cycle_data in data.get("cycles", []):
            try:
                cycle = SteadyStateCycle.from_dict(cycle_data)
                learner._cycles.append(cycle)
            except (KeyError, ValueError) as e:
                _LOGGER.warning("Failed to restore Ke-First cycle: %s", e)

        _LOGGER.info(
            "KeFirstLearner restored: ke=%.4f, converged=%s, cycles=%d, R²=%s",
            learner._current_ke,
            learner._converged,
            len(learner._cycles),
            learner._r_squared,
        )

        return learner
