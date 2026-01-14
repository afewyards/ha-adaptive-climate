"""Thermal rate learning and adaptive PID adjustments for Adaptive Thermostat."""

from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, NamedTuple, Optional, Set, Tuple, Any
import statistics
import logging
import json
import os

from ..const import (
    PID_LIMITS,
    MIN_CYCLES_FOR_LEARNING,
    MAX_CYCLE_HISTORY,
    MIN_ADJUSTMENT_INTERVAL,
    CONVERGENCE_THRESHOLDS,
    RULE_PRIORITY_OSCILLATION,
    RULE_PRIORITY_OVERSHOOT,
    RULE_PRIORITY_SLOW_RESPONSE,
    SEGMENT_NOISE_TOLERANCE,
    SEGMENT_RATE_MIN,
    SEGMENT_RATE_MAX,
    MIN_CONVERGENCE_CYCLES_FOR_KE,
)

_LOGGER = logging.getLogger(__name__)


class PIDRule(Enum):
    """PID adjustment rules with associated priority levels."""

    HIGH_OVERSHOOT = ("high_overshoot", RULE_PRIORITY_OVERSHOOT)
    MODERATE_OVERSHOOT = ("moderate_overshoot", RULE_PRIORITY_OVERSHOOT)
    SLOW_RESPONSE = ("slow_response", RULE_PRIORITY_SLOW_RESPONSE)
    UNDERSHOOT = ("undershoot", RULE_PRIORITY_SLOW_RESPONSE)
    MANY_OSCILLATIONS = ("many_oscillations", RULE_PRIORITY_OSCILLATION)
    SOME_OSCILLATIONS = ("some_oscillations", RULE_PRIORITY_OSCILLATION)
    SLOW_SETTLING = ("slow_settling", RULE_PRIORITY_SLOW_RESPONSE)

    def __init__(self, rule_name: str, priority: int):
        """Initialize rule with name and priority."""
        self.rule_name = rule_name
        self.priority = priority


class PIDRuleResult(NamedTuple):
    """Result of a single PID rule evaluation."""

    rule: PIDRule
    kp_factor: float  # Multiplier for Kp (1.0 = no change)
    ki_factor: float  # Multiplier for Ki (1.0 = no change)
    kd_factor: float  # Multiplier for Kd (1.0 = no change)
    reason: str       # Human-readable reason for adjustment


class ThermalRateLearner:
    """Learn thermal heating and cooling rates from observed temperature history."""

    def __init__(
        self,
        outlier_threshold: float = 2.0,
        noise_tolerance: float = SEGMENT_NOISE_TOLERANCE,
    ):
        """
        Initialize the ThermalRateLearner.

        Args:
            outlier_threshold: Number of standard deviations for outlier rejection
            noise_tolerance: Temperature changes below this threshold are ignored as noise (default 0.05C)
        """
        self.outlier_threshold = outlier_threshold
        self.noise_tolerance = noise_tolerance
        self._cooling_rates: List[float] = []
        self._heating_rates: List[float] = []

    def calculate_cooling_rate(
        self,
        temperature_history: List[Tuple[datetime, float]],
        min_duration_minutes: int = 30,
    ) -> Optional[float]:
        """
        Calculate cooling rate from temperature history when heating is off.

        Args:
            temperature_history: List of (timestamp, temperature) tuples
            min_duration_minutes: Minimum duration to consider for rate calculation

        Returns:
            Cooling rate in °C/hour, or None if insufficient data
        """
        if len(temperature_history) < 2:
            return None

        # Filter for cooling periods (temperature decreasing over time)
        cooling_segments = self._find_cooling_segments(
            temperature_history, min_duration_minutes
        )

        if not cooling_segments:
            return None

        # Calculate rate for each segment
        rates = []
        for segment in cooling_segments:
            if len(segment) < 2:
                continue

            start_time, start_temp = segment[0]
            end_time, end_temp = segment[-1]

            duration_hours = (end_time - start_time).total_seconds() / 3600
            if duration_hours <= 0:
                continue

            temp_drop = start_temp - end_temp
            if temp_drop <= 0:
                continue

            rate = temp_drop / duration_hours  # °C/hour (positive for cooling)
            rates.append(rate)

        if not rates:
            return None

        # Return median rate to reduce impact of outliers
        return statistics.median(rates)

    def calculate_heating_rate(
        self,
        temperature_history: List[Tuple[datetime, float]],
        min_duration_minutes: int = 10,
    ) -> Optional[float]:
        """
        Calculate heating rate from temperature history when heating is on.

        Args:
            temperature_history: List of (timestamp, temperature) tuples
            min_duration_minutes: Minimum duration to consider for rate calculation

        Returns:
            Heating rate in °C/hour, or None if insufficient data
        """
        if len(temperature_history) < 2:
            return None

        # Filter for heating periods (temperature increasing over time)
        heating_segments = self._find_heating_segments(
            temperature_history, min_duration_minutes
        )

        if not heating_segments:
            return None

        # Calculate rate for each segment
        rates = []
        for segment in heating_segments:
            if len(segment) < 2:
                continue

            start_time, start_temp = segment[0]
            end_time, end_temp = segment[-1]

            duration_hours = (end_time - start_time).total_seconds() / 3600
            if duration_hours <= 0:
                continue

            temp_rise = end_temp - start_temp
            if temp_rise <= 0:
                continue

            rate = temp_rise / duration_hours  # °C/hour (positive for heating)
            rates.append(rate)

        if not rates:
            return None

        # Return median rate to reduce impact of outliers
        return statistics.median(rates)

    def add_cooling_measurement(self, rate: float) -> None:
        """
        Add a cooling rate measurement.

        Args:
            rate: Cooling rate in °C/hour
        """
        if rate > 0:
            self._cooling_rates.append(rate)

    def add_heating_measurement(self, rate: float) -> None:
        """
        Add a heating rate measurement.

        Args:
            rate: Heating rate in °C/hour
        """
        if rate > 0:
            self._heating_rates.append(rate)

    def get_average_cooling_rate(
        self, reject_outliers: bool = True, max_measurements: int = 50
    ) -> Optional[float]:
        """
        Calculate average cooling rate from stored measurements.

        Args:
            reject_outliers: Whether to reject statistical outliers
            max_measurements: Maximum number of recent measurements to consider

        Returns:
            Average cooling rate in °C/hour, or None if insufficient data
        """
        if not self._cooling_rates:
            return None

        # Use only recent measurements
        recent_rates = self._cooling_rates[-max_measurements:]

        if reject_outliers and len(recent_rates) >= 3:
            recent_rates = self._reject_outliers(recent_rates)

        if not recent_rates:
            return None

        return statistics.mean(recent_rates)

    def get_average_heating_rate(
        self, reject_outliers: bool = True, max_measurements: int = 50
    ) -> Optional[float]:
        """
        Calculate average heating rate from stored measurements.

        Args:
            reject_outliers: Whether to reject statistical outliers
            max_measurements: Maximum number of recent measurements to consider

        Returns:
            Average heating rate in °C/hour, or None if insufficient data
        """
        if not self._heating_rates:
            return None

        # Use only recent measurements
        recent_rates = self._heating_rates[-max_measurements:]

        if reject_outliers and len(recent_rates) >= 3:
            recent_rates = self._reject_outliers(recent_rates)

        if not recent_rates:
            return None

        return statistics.mean(recent_rates)

    def _reject_outliers(self, values: List[float]) -> List[float]:
        """
        Reject statistical outliers using standard deviation threshold.

        Args:
            values: List of values to filter

        Returns:
            Filtered list with outliers removed
        """
        if len(values) < 3:
            return values

        mean = statistics.mean(values)
        stdev = statistics.stdev(values)

        if stdev == 0:
            return values

        # Keep values within threshold standard deviations of mean
        filtered = [
            v
            for v in values
            if abs(v - mean) <= self.outlier_threshold * stdev
        ]

        return filtered if filtered else values

    def _find_cooling_segments(
        self,
        temperature_history: List[Tuple[datetime, float]],
        min_duration_minutes: int,
    ) -> List[List[Tuple[datetime, float]]]:
        """
        Find continuous cooling segments in temperature history.

        Uses noise tolerance to ignore small reversals caused by sensor noise.
        Validates segments against rate bounds to reject physically impossible rates.

        Args:
            temperature_history: List of (timestamp, temperature) tuples
            min_duration_minutes: Minimum segment duration

        Returns:
            List of cooling segments (with physically impossible rates filtered out)
        """
        segments = []
        current_segment = []

        for i in range(len(temperature_history) - 1):
            current_time, current_temp = temperature_history[i]
            next_time, next_temp = temperature_history[i + 1]

            temp_change = current_temp - next_temp  # Positive = cooling

            # Check if temperature is decreasing (with noise tolerance)
            # A reversal below noise_tolerance is ignored and segment continues
            if temp_change > -self.noise_tolerance:  # Cooling or within noise band
                if not current_segment:
                    current_segment.append((current_time, current_temp))
                current_segment.append((next_time, next_temp))
            else:
                # Significant reversal (warming beyond tolerance) - end segment
                if current_segment:
                    segment = self._validate_segment(
                        current_segment, min_duration_minutes, is_cooling=True
                    )
                    if segment:
                        segments.append(segment)
                    current_segment = []

        # Check final segment
        if current_segment:
            segment = self._validate_segment(
                current_segment, min_duration_minutes, is_cooling=True
            )
            if segment:
                segments.append(segment)

        return segments

    def _find_heating_segments(
        self,
        temperature_history: List[Tuple[datetime, float]],
        min_duration_minutes: int,
    ) -> List[List[Tuple[datetime, float]]]:
        """
        Find continuous heating segments in temperature history.

        Uses noise tolerance to ignore small reversals caused by sensor noise.
        Validates segments against rate bounds to reject physically impossible rates.

        Args:
            temperature_history: List of (timestamp, temperature) tuples
            min_duration_minutes: Minimum segment duration

        Returns:
            List of heating segments (with physically impossible rates filtered out)
        """
        segments = []
        current_segment = []

        for i in range(len(temperature_history) - 1):
            current_time, current_temp = temperature_history[i]
            next_time, next_temp = temperature_history[i + 1]

            temp_change = next_temp - current_temp  # Positive = heating

            # Check if temperature is increasing (with noise tolerance)
            # A reversal below noise_tolerance is ignored and segment continues
            if temp_change > -self.noise_tolerance:  # Heating or within noise band
                if not current_segment:
                    current_segment.append((current_time, current_temp))
                current_segment.append((next_time, next_temp))
            else:
                # Significant reversal (cooling beyond tolerance) - end segment
                if current_segment:
                    segment = self._validate_segment(
                        current_segment, min_duration_minutes, is_cooling=False
                    )
                    if segment:
                        segments.append(segment)
                    current_segment = []

        # Check final segment
        if current_segment:
            segment = self._validate_segment(
                current_segment, min_duration_minutes, is_cooling=False
            )
            if segment:
                segments.append(segment)

        return segments

    def _validate_segment(
        self,
        segment: List[Tuple[datetime, float]],
        min_duration_minutes: int,
        is_cooling: bool,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """
        Validate a segment meets duration and rate bounds requirements.

        Args:
            segment: List of (timestamp, temperature) tuples
            min_duration_minutes: Minimum required segment duration
            is_cooling: True for cooling segments, False for heating

        Returns:
            The segment if valid, None if rejected
        """
        if len(segment) < 2:
            return None

        start_time, start_temp = segment[0]
        end_time, end_temp = segment[-1]

        # Check minimum duration
        duration_minutes = (end_time - start_time).total_seconds() / 60
        if duration_minutes < min_duration_minutes:
            return None

        # Calculate rate
        duration_hours = duration_minutes / 60
        if duration_hours <= 0:
            return None

        if is_cooling:
            temp_change = start_temp - end_temp
        else:
            temp_change = end_temp - start_temp

        # Reject segments where overall temperature didn't change in expected direction
        if temp_change <= 0:
            return None

        rate = temp_change / duration_hours  # C/hour

        # Check rate bounds
        if rate < SEGMENT_RATE_MIN or rate > SEGMENT_RATE_MAX:
            _LOGGER.debug(
                f"Rejected {'cooling' if is_cooling else 'heating'} segment: "
                f"rate {rate:.2f} C/hour outside bounds [{SEGMENT_RATE_MIN}, {SEGMENT_RATE_MAX}]"
            )
            return None

        return segment

    def get_measurement_counts(self) -> Dict[str, int]:
        """
        Get counts of stored measurements.

        Returns:
            Dictionary with cooling and heating measurement counts
        """
        return {
            "cooling": len(self._cooling_rates),
            "heating": len(self._heating_rates),
        }

    def clear_measurements(self) -> None:
        """Clear all stored measurements."""
        self._cooling_rates.clear()
        self._heating_rates.clear()


# Cycle analysis functions


class PhaseAwareOvershootTracker:
    """
    Track temperature phases (rise vs settling) for accurate overshoot detection.

    Overshoot should only be measured in the settling phase, which begins after
    the temperature first crosses the setpoint. This prevents false overshoot
    readings during the rise phase.
    """

    # Phase constants
    PHASE_RISE = "rise"
    PHASE_SETTLING = "settling"

    def __init__(self, setpoint: float, tolerance: float = 0.05):
        """
        Initialize the phase-aware overshoot tracker.

        Args:
            setpoint: Target temperature in degrees C
            tolerance: Small tolerance band for detecting setpoint crossing (default 0.05C)
        """
        self._setpoint = setpoint
        self._tolerance = tolerance
        self._phase = self.PHASE_RISE
        self._setpoint_crossed = False
        self._crossing_timestamp: Optional[datetime] = None
        self._max_settling_temp: Optional[float] = None
        self._settling_temps: List[Tuple[datetime, float]] = []

    @property
    def setpoint(self) -> float:
        """Get the current setpoint."""
        return self._setpoint

    @property
    def phase(self) -> str:
        """Get the current phase (rise or settling)."""
        return self._phase

    @property
    def setpoint_crossed(self) -> bool:
        """Check if the setpoint has been crossed."""
        return self._setpoint_crossed

    @property
    def crossing_timestamp(self) -> Optional[datetime]:
        """Get the timestamp when setpoint was first crossed."""
        return self._crossing_timestamp

    def reset(self, new_setpoint: Optional[float] = None) -> None:
        """
        Reset tracking state. Call when setpoint changes.

        Args:
            new_setpoint: Optional new setpoint value. If None, keeps current setpoint.
        """
        if new_setpoint is not None:
            self._setpoint = new_setpoint
        self._phase = self.PHASE_RISE
        self._setpoint_crossed = False
        self._crossing_timestamp = None
        self._max_settling_temp = None
        self._settling_temps.clear()
        _LOGGER.debug(f"Overshoot tracker reset, setpoint: {self._setpoint}°C")

    def update(self, timestamp: datetime, temperature: float) -> None:
        """
        Update tracker with a new temperature reading.

        Args:
            timestamp: Time of the reading
            temperature: Current temperature in degrees C
        """
        # Check for setpoint crossing (rise phase -> settling phase)
        if self._phase == self.PHASE_RISE:
            # Temperature has crossed or reached setpoint (with tolerance)
            if temperature >= self._setpoint - self._tolerance:
                self._phase = self.PHASE_SETTLING
                self._setpoint_crossed = True
                self._crossing_timestamp = timestamp
                _LOGGER.debug(
                    f"Setpoint crossed at {timestamp}, temp={temperature:.2f}°C, "
                    f"setpoint={self._setpoint:.2f}°C - entering settling phase"
                )

        # Track maximum temperature in settling phase
        if self._phase == self.PHASE_SETTLING:
            self._settling_temps.append((timestamp, temperature))
            if self._max_settling_temp is None or temperature > self._max_settling_temp:
                self._max_settling_temp = temperature

    def get_overshoot(self) -> Optional[float]:
        """
        Calculate overshoot based on settling phase data.

        Returns:
            Overshoot in degrees C (positive values only), or None if:
            - Setpoint was never crossed (still in rise phase)
            - No settling phase data available
        """
        # No overshoot if setpoint was never reached
        if not self._setpoint_crossed:
            _LOGGER.debug("No overshoot: setpoint was never crossed")
            return None

        if self._max_settling_temp is None:
            return None

        overshoot = self._max_settling_temp - self._setpoint
        return max(0.0, overshoot)

    def get_settling_temps(self) -> List[Tuple[datetime, float]]:
        """
        Get all temperature readings from the settling phase.

        Returns:
            List of (timestamp, temperature) tuples from settling phase
        """
        return list(self._settling_temps)


def calculate_overshoot(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    phase_aware: bool = True
) -> Optional[float]:
    """
    Calculate maximum overshoot beyond target temperature.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C
        phase_aware: If True, only calculate overshoot from settling phase
                    (after setpoint is first crossed). Default True.

    Returns:
        Overshoot in °C (positive values only), or None if:
        - No data provided
        - phase_aware=True and setpoint was never reached
    """
    if not temperature_history:
        return None

    if not phase_aware:
        # Legacy behavior: max temp minus setpoint
        max_temp = max(temp for _, temp in temperature_history)
        overshoot = max_temp - target_temp
        return max(0.0, overshoot)

    # Phase-aware calculation: only consider temps after setpoint crossing
    tracker = PhaseAwareOvershootTracker(target_temp)

    for timestamp, temp in temperature_history:
        tracker.update(timestamp, temp)

    return tracker.get_overshoot()


def calculate_undershoot(
    temperature_history: List[Tuple[datetime, float]], target_temp: float
) -> Optional[float]:
    """
    Calculate maximum undershoot below target temperature.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C

    Returns:
        Undershoot in °C (positive values only), or None if no data
    """
    if not temperature_history:
        return None

    min_temp = min(temp for _, temp in temperature_history)
    undershoot = target_temp - min_temp

    return max(0.0, undershoot)


def count_oscillations(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    threshold: float = 0.1,
) -> int:
    """
    Count number of oscillations around target temperature.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C
        threshold: Hysteresis threshold in °C to avoid counting noise

    Returns:
        Number of oscillations (crossings of target)
    """
    if len(temperature_history) < 2:
        return 0

    # Track state: None, 'above', or 'below'
    state = None
    crossings = 0

    for _, temp in temperature_history:
        # Determine if above or below target (with threshold)
        if temp > target_temp + threshold:
            new_state = "above"
        elif temp < target_temp - threshold:
            new_state = "below"
        else:
            # Within threshold band, maintain current state
            new_state = state

        # Check for state change (crossing)
        if state is not None and new_state != state and new_state is not None:
            crossings += 1

        if new_state is not None:
            state = new_state

    return crossings


def calculate_settling_time(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    tolerance: float = 0.2,
) -> Optional[float]:
    """
    Calculate time required for temperature to settle within tolerance band.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C
        tolerance: Tolerance band in °C (±)

    Returns:
        Settling time in minutes, or None if never settles
    """
    if len(temperature_history) < 2:
        return None

    start_time = temperature_history[0][0]
    settle_index = None

    # Find first entry into tolerance band that persists
    for i, (timestamp, temp) in enumerate(temperature_history):
        within_tolerance = abs(temp - target_temp) <= tolerance

        if within_tolerance:
            # Check if it stays within tolerance
            # Need at least 3 more samples or until the end
            remaining = temperature_history[i:]
            if len(remaining) >= 3:
                # Check if next samples stay within tolerance
                stays_settled = all(
                    abs(t - target_temp) <= tolerance for _, t in remaining[:3]
                )
                if stays_settled:
                    settle_index = i
                    break
            elif len(remaining) > 0:
                # At end of history, check if all remaining stay within tolerance
                stays_settled = all(
                    abs(t - target_temp) <= tolerance for _, t in remaining
                )
                if stays_settled:
                    settle_index = i
                    break

    if settle_index is None:
        return None

    settle_time = temperature_history[settle_index][0]
    settling_minutes = (settle_time - start_time).total_seconds() / 60

    return settling_minutes


# Adaptive learning


class CycleMetrics:
    """Container for heating cycle performance metrics."""

    def __init__(
        self,
        overshoot: Optional[float] = None,
        undershoot: Optional[float] = None,
        settling_time: Optional[float] = None,
        oscillations: int = 0,
        rise_time: Optional[float] = None,
    ):
        """
        Initialize cycle metrics.

        Args:
            overshoot: Maximum overshoot in °C
            undershoot: Maximum undershoot in °C
            settling_time: Settling time in minutes
            oscillations: Number of oscillations around target
            rise_time: Time to reach target from start in minutes
        """
        self.overshoot = overshoot
        self.undershoot = undershoot
        self.settling_time = settling_time
        self.oscillations = oscillations
        self.rise_time = rise_time


class AdaptiveLearner:
    """Adaptive PID tuning based on observed heating cycle performance."""

    def __init__(self, max_history: int = MAX_CYCLE_HISTORY):
        """
        Initialize the AdaptiveLearner.

        Args:
            max_history: Maximum number of cycles to retain in history (FIFO eviction)
        """
        self._cycle_history: List[CycleMetrics] = []
        self._max_history = max_history
        self._last_adjustment_time: Optional[datetime] = None
        # Convergence tracking for Ke learning activation
        self._consecutive_converged_cycles: int = 0
        self._pid_converged_for_ke: bool = False

    def add_cycle_metrics(self, metrics: CycleMetrics) -> None:
        """
        Add a cycle's performance metrics to history.

        Implements FIFO eviction when history exceeds max_history.

        Args:
            metrics: CycleMetrics object with performance data
        """
        self._cycle_history.append(metrics)

        # FIFO eviction: remove oldest entries when exceeding max history
        if len(self._cycle_history) > self._max_history:
            evicted_count = len(self._cycle_history) - self._max_history
            self._cycle_history = self._cycle_history[-self._max_history:]
            _LOGGER.debug(
                f"Cycle history exceeded max ({self._max_history}), "
                f"evicted {evicted_count} oldest entries"
            )

    def get_cycle_count(self) -> int:
        """
        Get number of stored cycle metrics.

        Returns:
            Number of cycles in history
        """
        return len(self._cycle_history)

    def _evaluate_rules(
        self,
        avg_overshoot: float,
        avg_undershoot: float,
        avg_oscillations: float,
        avg_rise_time: float,
        avg_settling_time: float,
    ) -> List[PIDRuleResult]:
        """
        Evaluate all PID tuning rules against current metrics.

        Args:
            avg_overshoot: Average overshoot in °C
            avg_undershoot: Average undershoot in °C
            avg_oscillations: Average number of oscillations
            avg_rise_time: Average rise time in minutes
            avg_settling_time: Average settling time in minutes

        Returns:
            List of applicable rule results (rules that would fire)
        """
        results: List[PIDRuleResult] = []

        # Rule 1: High overshoot (>0.5C)
        if avg_overshoot > 0.5:
            reduction = min(0.15, avg_overshoot * 0.2)  # Up to 15% reduction
            results.append(PIDRuleResult(
                rule=PIDRule.HIGH_OVERSHOOT,
                kp_factor=1.0 - reduction,
                ki_factor=0.9,
                kd_factor=1.0,
                reason=f"High overshoot ({avg_overshoot:.2f}°C)"
            ))
        # Rule 2: Moderate overshoot (>0.2C, only if high overshoot didn't fire)
        elif avg_overshoot > 0.2:
            results.append(PIDRuleResult(
                rule=PIDRule.MODERATE_OVERSHOOT,
                kp_factor=0.95,
                ki_factor=1.0,
                kd_factor=1.0,
                reason=f"Moderate overshoot ({avg_overshoot:.2f}°C)"
            ))

        # Rule 3: Slow response (rise time >60 min)
        if avg_rise_time > 60:
            results.append(PIDRuleResult(
                rule=PIDRule.SLOW_RESPONSE,
                kp_factor=1.10,
                ki_factor=1.0,
                kd_factor=1.0,
                reason=f"Slow rise time ({avg_rise_time:.1f} min)"
            ))

        # Rule 4: Undershoot (>0.3C)
        if avg_undershoot > 0.3:
            increase = min(0.20, avg_undershoot * 0.4)  # Up to 20% increase
            results.append(PIDRuleResult(
                rule=PIDRule.UNDERSHOOT,
                kp_factor=1.0,
                ki_factor=1.0 + increase,
                kd_factor=1.0,
                reason=f"Undershoot ({avg_undershoot:.2f}°C)"
            ))

        # Rule 5: Many oscillations (>3)
        if avg_oscillations > 3:
            results.append(PIDRuleResult(
                rule=PIDRule.MANY_OSCILLATIONS,
                kp_factor=0.90,
                ki_factor=1.0,
                kd_factor=1.20,
                reason=f"Many oscillations ({avg_oscillations:.1f})"
            ))
        # Rule 6: Some oscillations (>1, only if many didn't fire)
        elif avg_oscillations > 1:
            results.append(PIDRuleResult(
                rule=PIDRule.SOME_OSCILLATIONS,
                kp_factor=1.0,
                ki_factor=1.0,
                kd_factor=1.10,
                reason=f"Some oscillations ({avg_oscillations:.1f})"
            ))

        # Rule 7: Slow settling (>90 min)
        if avg_settling_time > 90:
            results.append(PIDRuleResult(
                rule=PIDRule.SLOW_SETTLING,
                kp_factor=1.0,
                ki_factor=1.0,
                kd_factor=1.15,
                reason=f"Slow settling ({avg_settling_time:.1f} min)"
            ))

        return results

    def _detect_conflicts(
        self,
        rule_results: List[PIDRuleResult]
    ) -> List[Tuple[PIDRuleResult, PIDRuleResult, str]]:
        """
        Detect conflicts between applicable rules.

        A conflict occurs when two rules adjust the same parameter in opposite directions
        (one increases, one decreases).

        Args:
            rule_results: List of applicable rule results

        Returns:
            List of (rule1, rule2, parameter_name) tuples for each conflict
        """
        conflicts: List[Tuple[PIDRuleResult, PIDRuleResult, str]] = []

        for i, r1 in enumerate(rule_results):
            for r2 in rule_results[i + 1:]:
                # Check Kp conflicts (one increases, one decreases)
                if (r1.kp_factor > 1.0 and r2.kp_factor < 1.0) or \
                   (r1.kp_factor < 1.0 and r2.kp_factor > 1.0):
                    conflicts.append((r1, r2, "kp"))

                # Check Ki conflicts
                if (r1.ki_factor > 1.0 and r2.ki_factor < 1.0) or \
                   (r1.ki_factor < 1.0 and r2.ki_factor > 1.0):
                    conflicts.append((r1, r2, "ki"))

                # Check Kd conflicts
                if (r1.kd_factor > 1.0 and r2.kd_factor < 1.0) or \
                   (r1.kd_factor < 1.0 and r2.kd_factor > 1.0):
                    conflicts.append((r1, r2, "kd"))

        return conflicts

    def _resolve_conflicts(
        self,
        rule_results: List[PIDRuleResult],
        conflicts: List[Tuple[PIDRuleResult, PIDRuleResult, str]]
    ) -> List[PIDRuleResult]:
        """
        Resolve conflicts by applying higher priority rules.

        For each conflict, the lower priority rule's adjustment for that parameter
        is neutralized (set to 1.0).

        Args:
            rule_results: List of applicable rule results
            conflicts: List of detected conflicts from _detect_conflicts()

        Returns:
            Modified list of rule results with conflicts resolved
        """
        # Track which rules have parameters suppressed
        suppressed: Dict[PIDRule, Set[str]] = {}

        for r1, r2, param in conflicts:
            # Determine winner (higher priority)
            winner = r1 if r1.rule.priority >= r2.rule.priority else r2
            loser = r2 if winner == r1 else r1

            _LOGGER.info(
                f"PID rule conflict on {param}: '{winner.rule.rule_name}' "
                f"(priority {winner.rule.priority}) takes precedence over "
                f"'{loser.rule.rule_name}' (priority {loser.rule.priority})"
            )

            # Mark loser's parameter as suppressed
            if loser.rule not in suppressed:
                suppressed[loser.rule] = set()
            suppressed[loser.rule].add(param)

        # Build resolved results
        resolved: List[PIDRuleResult] = []
        for result in rule_results:
            if result.rule in suppressed:
                # Create new result with suppressed parameters neutralized
                suppressed_params = suppressed[result.rule]
                resolved.append(PIDRuleResult(
                    rule=result.rule,
                    kp_factor=1.0 if "kp" in suppressed_params else result.kp_factor,
                    ki_factor=1.0 if "ki" in suppressed_params else result.ki_factor,
                    kd_factor=1.0 if "kd" in suppressed_params else result.kd_factor,
                    reason=result.reason + " (partially suppressed)"
                ))
            else:
                resolved.append(result)

        return resolved

    def _check_convergence(
        self,
        avg_overshoot: float,
        avg_oscillations: float,
        avg_settling_time: float,
        avg_rise_time: float,
    ) -> bool:
        """
        Check if the system has converged (is well-tuned).

        Convergence occurs when ALL performance metrics are within acceptable bounds
        defined by CONVERGENCE_THRESHOLDS.

        Args:
            avg_overshoot: Average overshoot in °C
            avg_oscillations: Average number of oscillations
            avg_settling_time: Average settling time in minutes
            avg_rise_time: Average rise time in minutes

        Returns:
            True if converged, False otherwise
        """
        is_converged = (
            avg_overshoot <= CONVERGENCE_THRESHOLDS["overshoot_max"] and
            avg_oscillations <= CONVERGENCE_THRESHOLDS["oscillations_max"] and
            avg_settling_time <= CONVERGENCE_THRESHOLDS["settling_time_max"] and
            avg_rise_time <= CONVERGENCE_THRESHOLDS["rise_time_max"]
        )

        if is_converged:
            _LOGGER.info(
                f"PID convergence detected - system tuned: "
                f"overshoot={avg_overshoot:.2f}°C, oscillations={avg_oscillations:.1f}, "
                f"settling={avg_settling_time:.1f}min, rise={avg_rise_time:.1f}min"
            )

        return is_converged

    def _check_rate_limit(
        self,
        min_interval_hours: int = MIN_ADJUSTMENT_INTERVAL,
    ) -> bool:
        """
        Check if enough time has passed since the last PID adjustment.

        Args:
            min_interval_hours: Minimum hours between adjustments

        Returns:
            True if rate limited (adjustment should be skipped), False if OK to adjust
        """
        if self._last_adjustment_time is None:
            return False

        time_since_last = datetime.now() - self._last_adjustment_time
        min_interval = timedelta(hours=min_interval_hours)

        if time_since_last < min_interval:
            hours_remaining = (min_interval - time_since_last).total_seconds() / 3600
            _LOGGER.info(
                f"PID adjustment rate limited: last adjustment was "
                f"{time_since_last.total_seconds() / 3600:.1f}h ago, "
                f"minimum interval is {min_interval_hours}h "
                f"({hours_remaining:.1f}h remaining)"
            )
            return True

        return False

    def calculate_pid_adjustment(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
        min_cycles: int = MIN_CYCLES_FOR_LEARNING,
        min_interval_hours: int = MIN_ADJUSTMENT_INTERVAL,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate PID adjustments based on observed cycle performance.

        Implements priority-based rule conflict resolution:
        - Priority 3 (highest): Oscillation rules (safety)
        - Priority 2: Overshoot rules (stability)
        - Priority 1 (lowest): Response time rules (performance)

        When rules conflict (e.g., overshoot says decrease Kp, slow response says increase),
        the higher priority rule wins.

        Also detects convergence (system is well-tuned) and skips adjustments.
        Rate limiting prevents adjustments too frequently.

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            min_cycles: Minimum cycles required before making recommendations
            min_interval_hours: Minimum hours between adjustments (rate limiting)

        Returns:
            Dictionary with recommended kp, ki, kd values, or None if insufficient data,
            system is converged, or rate limited
        """
        # Check rate limiting first
        if self._check_rate_limit(min_interval_hours):
            return None

        if len(self._cycle_history) < min_cycles:
            _LOGGER.debug(
                f"Insufficient cycles for learning: {len(self._cycle_history)} < {min_cycles}"
            )
            return None

        # Calculate average metrics from recent cycles
        recent_cycles = self._cycle_history[-min_cycles:]

        avg_overshoot = statistics.mean(
            [c.overshoot for c in recent_cycles if c.overshoot is not None]
        ) if any(c.overshoot is not None for c in recent_cycles) else 0.0

        avg_undershoot = statistics.mean(
            [c.undershoot for c in recent_cycles if c.undershoot is not None]
        ) if any(c.undershoot is not None for c in recent_cycles) else 0.0

        avg_settling_time = statistics.mean(
            [c.settling_time for c in recent_cycles if c.settling_time is not None]
        ) if any(c.settling_time is not None for c in recent_cycles) else 0.0

        avg_oscillations = statistics.mean([c.oscillations for c in recent_cycles])

        avg_rise_time = statistics.mean(
            [c.rise_time for c in recent_cycles if c.rise_time is not None]
        ) if any(c.rise_time is not None for c in recent_cycles) else 0.0

        # Check for convergence - skip adjustments if system is tuned
        if self._check_convergence(
            avg_overshoot, avg_oscillations, avg_settling_time, avg_rise_time
        ):
            _LOGGER.info("Skipping PID adjustment - system has converged")
            return None

        # Evaluate all applicable rules
        rule_results = self._evaluate_rules(
            avg_overshoot, avg_undershoot, avg_oscillations,
            avg_rise_time, avg_settling_time
        )

        if not rule_results:
            _LOGGER.debug("No PID rules triggered - metrics within acceptable ranges")
            return None

        # Detect and resolve conflicts
        conflicts = self._detect_conflicts(rule_results)
        if conflicts:
            _LOGGER.info(f"Detected {len(conflicts)} PID rule conflict(s)")
            rule_results = self._resolve_conflicts(rule_results, conflicts)

        # Apply resolved rules
        new_kp = current_kp
        new_ki = current_ki
        new_kd = current_kd

        for result in rule_results:
            if result.kp_factor != 1.0:
                _LOGGER.info(f"{result.reason}: Kp *= {result.kp_factor:.2f}")
            if result.ki_factor != 1.0:
                _LOGGER.info(f"{result.reason}: Ki *= {result.ki_factor:.2f}")
            if result.kd_factor != 1.0:
                _LOGGER.info(f"{result.reason}: Kd *= {result.kd_factor:.2f}")

            new_kp *= result.kp_factor
            new_ki *= result.ki_factor
            new_kd *= result.kd_factor

        # Enforce PID limits
        new_kp = max(PID_LIMITS["kp_min"], min(PID_LIMITS["kp_max"], new_kp))
        new_ki = max(PID_LIMITS["ki_min"], min(PID_LIMITS["ki_max"], new_ki))
        new_kd = max(PID_LIMITS["kd_min"], min(PID_LIMITS["kd_max"], new_kd))

        # Record adjustment time for rate limiting
        self._last_adjustment_time = datetime.now()

        return {
            "kp": new_kp,
            "ki": new_ki,
            "kd": new_kd,
        }

    def get_last_adjustment_time(self) -> Optional[datetime]:
        """
        Get the timestamp of the last PID adjustment.

        Returns:
            datetime of last adjustment, or None if no adjustment has been made
        """
        return self._last_adjustment_time

    def clear_history(self) -> None:
        """Clear all stored cycle metrics and reset last adjustment time."""
        self._cycle_history.clear()
        self._last_adjustment_time = None

    def update_convergence_tracking(self, metrics: CycleMetrics) -> bool:
        """Update convergence tracking based on latest cycle metrics.

        Tracks consecutive converged cycles to determine when PID is stable
        enough for Ke learning to begin.

        Args:
            metrics: The latest cycle metrics to evaluate

        Returns:
            True if PID is now converged for Ke learning, False otherwise
        """
        # Check if this cycle meets convergence thresholds
        overshoot = metrics.overshoot if metrics.overshoot is not None else 0.0
        oscillations = metrics.oscillations
        settling_time = metrics.settling_time if metrics.settling_time is not None else 0.0
        rise_time = metrics.rise_time if metrics.rise_time is not None else 0.0

        is_cycle_converged = (
            overshoot <= CONVERGENCE_THRESHOLDS["overshoot_max"] and
            oscillations <= CONVERGENCE_THRESHOLDS["oscillations_max"] and
            settling_time <= CONVERGENCE_THRESHOLDS["settling_time_max"] and
            rise_time <= CONVERGENCE_THRESHOLDS["rise_time_max"]
        )

        if is_cycle_converged:
            self._consecutive_converged_cycles += 1
            _LOGGER.debug(
                "Convergence tracking: cycle converged (%d consecutive)",
                self._consecutive_converged_cycles,
            )

            # Check if we've reached the threshold for Ke learning
            if (self._consecutive_converged_cycles >= MIN_CONVERGENCE_CYCLES_FOR_KE and
                    not self._pid_converged_for_ke):
                self._pid_converged_for_ke = True
                _LOGGER.info(
                    "PID converged for Ke learning after %d consecutive cycles",
                    self._consecutive_converged_cycles,
                )
        else:
            # Reset consecutive counter on non-converged cycle
            if self._consecutive_converged_cycles > 0:
                _LOGGER.debug(
                    "Convergence tracking: cycle not converged, resetting "
                    "counter (was %d)",
                    self._consecutive_converged_cycles,
                )
            self._consecutive_converged_cycles = 0
            self._pid_converged_for_ke = False

        return self._pid_converged_for_ke

    def is_pid_converged_for_ke(self) -> bool:
        """Check if PID has converged sufficiently for Ke learning.

        Returns:
            True if PID has had MIN_CONVERGENCE_CYCLES_FOR_KE consecutive
            converged cycles, False otherwise
        """
        return self._pid_converged_for_ke

    def get_consecutive_converged_cycles(self) -> int:
        """Get the number of consecutive converged cycles.

        Returns:
            Number of consecutive cycles meeting convergence thresholds
        """
        return self._consecutive_converged_cycles

    def reset_ke_convergence(self) -> None:
        """Reset Ke convergence tracking.

        Call this when PID values are changed or Ke learning needs to restart.
        """
        old_converged = self._pid_converged_for_ke
        old_count = self._consecutive_converged_cycles
        self._consecutive_converged_cycles = 0
        self._pid_converged_for_ke = False
        if old_converged or old_count > 0:
            _LOGGER.info(
                "Ke convergence reset (was: converged=%s, consecutive=%d)",
                old_converged,
                old_count,
            )


# PWM auto-tuning


def calculate_pwm_adjustment(
    cycle_times: List[float],
    current_pwm_period: float,
    min_pwm_period: float = 180.0,
    max_pwm_period: float = 1800.0,
    short_cycle_threshold: float = 10.0,
) -> Optional[float]:
    """
    Calculate PWM period adjustment based on observed cycling behavior.

    Detects short cycling and increases PWM period to reduce valve wear.
    Short cycling occurs when the heating switches on/off too frequently.

    Args:
        cycle_times: List of cycle times in minutes
        current_pwm_period: Current PWM period in seconds
        min_pwm_period: Minimum allowed PWM period in seconds (default 180s = 3 min)
        max_pwm_period: Maximum allowed PWM period in seconds (default 1800s = 30 min)
        short_cycle_threshold: Threshold for short cycling in minutes (default 10 min)

    Returns:
        Recommended PWM period in seconds, or None if insufficient data
    """
    if not cycle_times or len(cycle_times) < 3:
        return None

    # Calculate average cycle time
    avg_cycle_time = statistics.mean(cycle_times)

    # Check for short cycling
    if avg_cycle_time < short_cycle_threshold:
        # Increase PWM period to reduce cycling
        # Increase by 20% for each minute below threshold
        shortage = short_cycle_threshold - avg_cycle_time
        increase_factor = 1.0 + (shortage / short_cycle_threshold) * 0.2
        new_pwm_period = current_pwm_period * increase_factor

        # Enforce bounds
        new_pwm_period = max(min_pwm_period, min(max_pwm_period, new_pwm_period))

        _LOGGER.info(
            f"Short cycling detected (avg {avg_cycle_time:.1f} min): "
            f"increasing PWM period from {current_pwm_period:.0f}s to {new_pwm_period:.0f}s"
        )

        return new_pwm_period

    # If cycles are acceptable, keep current PWM period
    return current_pwm_period


class ValveCycleTracker:
    """Track valve cycles for wear monitoring."""

    def __init__(self):
        """Initialize the ValveCycleTracker."""
        self._cycle_count: int = 0
        self._last_state: Optional[bool] = None

    def update(self, valve_open: bool) -> None:
        """
        Update valve state and count cycles.

        A cycle is counted each time the valve transitions from closed to open.

        Args:
            valve_open: Current valve state (True = open, False = closed)
        """
        if self._last_state is not None and not self._last_state and valve_open:
            # Transition from closed to open
            self._cycle_count += 1

        self._last_state = valve_open

    def get_cycle_count(self) -> int:
        """
        Get total number of valve cycles.

        Returns:
            Total cycle count
        """
        return self._cycle_count

    def reset(self) -> None:
        """Reset cycle counter."""
        self._cycle_count = 0
        self._last_state = None


class LearningDataStore:
    """Persist learning data across Home Assistant restarts."""

    def __init__(self, storage_path: str):
        """
        Initialize the LearningDataStore.

        Args:
            storage_path: Path to storage directory (typically .storage/)
        """
        self.storage_path = storage_path
        self.storage_file = os.path.join(storage_path, "adaptive_thermostat_learning.json")

    def save(
        self,
        thermal_learner: Optional[ThermalRateLearner] = None,
        adaptive_learner: Optional[AdaptiveLearner] = None,
        valve_tracker: Optional[ValveCycleTracker] = None,
        ke_learner: Optional[Any] = None,  # KeLearner, imported dynamically to avoid circular import
    ) -> bool:
        """
        Save learning data to storage.

        Args:
            thermal_learner: ThermalRateLearner instance to save
            adaptive_learner: AdaptiveLearner instance to save
            valve_tracker: ValveCycleTracker instance to save
            ke_learner: KeLearner instance to save

        Returns:
            True if save successful, False otherwise
        """
        try:
            # Ensure storage directory exists
            os.makedirs(self.storage_path, exist_ok=True)

            data: Dict[str, Any] = {
                "version": 2,  # Incremented for Ke learner support
                "last_updated": datetime.now().isoformat(),
            }

            # Save ThermalRateLearner data
            if thermal_learner is not None:
                data["thermal_learner"] = {
                    "cooling_rates": thermal_learner._cooling_rates,
                    "heating_rates": thermal_learner._heating_rates,
                    "outlier_threshold": thermal_learner.outlier_threshold,
                }

            # Save AdaptiveLearner data
            if adaptive_learner is not None:
                cycle_history = []
                for cycle in adaptive_learner._cycle_history:
                    cycle_history.append({
                        "overshoot": cycle.overshoot,
                        "undershoot": cycle.undershoot,
                        "settling_time": cycle.settling_time,
                        "oscillations": cycle.oscillations,
                        "rise_time": cycle.rise_time,
                    })

                data["adaptive_learner"] = {
                    "cycle_history": cycle_history,
                    "last_adjustment_time": (
                        adaptive_learner._last_adjustment_time.isoformat()
                        if adaptive_learner._last_adjustment_time is not None
                        else None
                    ),
                    "max_history": adaptive_learner._max_history,
                    # Include convergence tracking state
                    "consecutive_converged_cycles": adaptive_learner._consecutive_converged_cycles,
                    "pid_converged_for_ke": adaptive_learner._pid_converged_for_ke,
                }

            # Save ValveCycleTracker data
            if valve_tracker is not None:
                data["valve_tracker"] = {
                    "cycle_count": valve_tracker._cycle_count,
                    "last_state": valve_tracker._last_state,
                }

            # Save KeLearner data
            if ke_learner is not None:
                data["ke_learner"] = ke_learner.to_dict()

            # Write to file atomically
            temp_file = f"{self.storage_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)

            # Atomic rename
            os.replace(temp_file, self.storage_file)

            _LOGGER.info(f"Learning data saved to {self.storage_file}")
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to save learning data: {e}")
            return False

    def load(self) -> Optional[Dict[str, Any]]:
        """
        Load learning data from storage.

        Returns:
            Dictionary with learning data, or None if file doesn't exist or is corrupt
        """
        if not os.path.exists(self.storage_file):
            _LOGGER.info(f"No existing learning data found at {self.storage_file}")
            return None

        try:
            with open(self.storage_file, "r") as f:
                data = json.load(f)

            # Validate version
            if "version" not in data:
                _LOGGER.warning("Learning data missing version field, treating as corrupt")
                return None

            _LOGGER.info(f"Learning data loaded from {self.storage_file}")
            return data

        except json.JSONDecodeError as e:
            _LOGGER.error(f"Corrupt learning data (invalid JSON): {e}")
            return None
        except Exception as e:
            _LOGGER.error(f"Failed to load learning data: {e}")
            return None

    def restore_thermal_learner(self, data: Dict[str, Any]) -> Optional[ThermalRateLearner]:
        """
        Restore ThermalRateLearner from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored ThermalRateLearner instance, or None if data missing
        """
        if "thermal_learner" not in data:
            return None

        try:
            thermal_data = data["thermal_learner"]
            learner = ThermalRateLearner(
                outlier_threshold=thermal_data.get("outlier_threshold", 2.0)
            )

            # Validate data types
            cooling_rates = thermal_data.get("cooling_rates", [])
            heating_rates = thermal_data.get("heating_rates", [])

            if not isinstance(cooling_rates, list):
                raise TypeError("cooling_rates must be a list")
            if not isinstance(heating_rates, list):
                raise TypeError("heating_rates must be a list")

            learner._cooling_rates = cooling_rates
            learner._heating_rates = heating_rates

            _LOGGER.info(
                f"Restored ThermalRateLearner: "
                f"{len(learner._cooling_rates)} cooling rates, "
                f"{len(learner._heating_rates)} heating rates"
            )
            return learner

        except Exception as e:
            _LOGGER.error(f"Failed to restore ThermalRateLearner: {e}")
            return None

    def restore_adaptive_learner(self, data: Dict[str, Any]) -> Optional[AdaptiveLearner]:
        """
        Restore AdaptiveLearner from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored AdaptiveLearner instance, or None if data missing
        """
        if "adaptive_learner" not in data:
            return None

        try:
            adaptive_data = data["adaptive_learner"]
            max_history = adaptive_data.get("max_history", MAX_CYCLE_HISTORY)
            learner = AdaptiveLearner(max_history=max_history)

            # Validate cycle history is a list
            cycle_history = adaptive_data.get("cycle_history", [])
            if not isinstance(cycle_history, list):
                raise TypeError("cycle_history must be a list")

            # Restore cycle history
            for cycle_data in cycle_history:
                if not isinstance(cycle_data, dict):
                    raise TypeError("cycle_data must be a dictionary")

                metrics = CycleMetrics(
                    overshoot=cycle_data.get("overshoot"),
                    undershoot=cycle_data.get("undershoot"),
                    settling_time=cycle_data.get("settling_time"),
                    oscillations=cycle_data.get("oscillations", 0),
                    rise_time=cycle_data.get("rise_time"),
                )
                learner.add_cycle_metrics(metrics)

            # Restore last adjustment time
            last_adj_time_str = adaptive_data.get("last_adjustment_time")
            if last_adj_time_str is not None:
                learner._last_adjustment_time = datetime.fromisoformat(last_adj_time_str)

            # Restore convergence tracking state (version 2+)
            learner._consecutive_converged_cycles = adaptive_data.get(
                "consecutive_converged_cycles", 0
            )
            learner._pid_converged_for_ke = adaptive_data.get(
                "pid_converged_for_ke", False
            )

            _LOGGER.info(
                f"Restored AdaptiveLearner: {learner.get_cycle_count()} cycles, "
                f"last adjustment: {learner._last_adjustment_time}, "
                f"converged_for_ke: {learner._pid_converged_for_ke}"
            )
            return learner

        except Exception as e:
            _LOGGER.error(f"Failed to restore AdaptiveLearner: {e}")
            return None

    def restore_valve_tracker(self, data: Dict[str, Any]) -> Optional[ValveCycleTracker]:
        """
        Restore ValveCycleTracker from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored ValveCycleTracker instance, or None if data missing
        """
        if "valve_tracker" not in data:
            return None

        try:
            valve_data = data["valve_tracker"]
            tracker = ValveCycleTracker()
            tracker._cycle_count = valve_data.get("cycle_count", 0)
            tracker._last_state = valve_data.get("last_state")

            _LOGGER.info(f"Restored ValveCycleTracker: {tracker._cycle_count} cycles")
            return tracker

        except Exception as e:
            _LOGGER.error(f"Failed to restore ValveCycleTracker: {e}")
            return None

    def restore_ke_learner(self, data: Dict[str, Any]) -> Optional[Any]:
        """
        Restore KeLearner from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored KeLearner instance, or None if data missing
        """
        if "ke_learner" not in data:
            return None

        try:
            # Import KeLearner here to avoid circular import
            from .ke_learning import KeLearner

            ke_data = data["ke_learner"]
            learner = KeLearner.from_dict(ke_data)

            _LOGGER.info(
                f"Restored KeLearner: ke={learner.current_ke:.2f}, "
                f"enabled={learner.enabled}, observations={learner.observation_count}"
            )
            return learner

        except Exception as e:
            _LOGGER.error(f"Failed to restore KeLearner: {e}")
            return None
