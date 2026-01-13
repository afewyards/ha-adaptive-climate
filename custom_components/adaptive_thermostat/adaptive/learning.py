"""Thermal rate learning and adaptive PID adjustments for Adaptive Thermostat."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import statistics
import logging
import json
import os

from ..const import PID_LIMITS, MIN_CYCLES_FOR_LEARNING

_LOGGER = logging.getLogger(__name__)


class ThermalRateLearner:
    """Learn thermal heating and cooling rates from observed temperature history."""

    def __init__(self, outlier_threshold: float = 2.0):
        """
        Initialize the ThermalRateLearner.

        Args:
            outlier_threshold: Number of standard deviations for outlier rejection
        """
        self.outlier_threshold = outlier_threshold
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

        Args:
            temperature_history: List of (timestamp, temperature) tuples
            min_duration_minutes: Minimum segment duration

        Returns:
            List of cooling segments
        """
        segments = []
        current_segment = []

        for i in range(len(temperature_history) - 1):
            current_time, current_temp = temperature_history[i]
            next_time, next_temp = temperature_history[i + 1]

            # Check if temperature is decreasing
            if next_temp < current_temp:
                if not current_segment:
                    current_segment.append((current_time, current_temp))
                current_segment.append((next_time, next_temp))
            else:
                # End of cooling segment
                if current_segment:
                    start_time = current_segment[0][0]
                    end_time = current_segment[-1][0]
                    duration = (end_time - start_time).total_seconds() / 60

                    if duration >= min_duration_minutes:
                        segments.append(current_segment)

                    current_segment = []

        # Check final segment
        if current_segment:
            start_time = current_segment[0][0]
            end_time = current_segment[-1][0]
            duration = (end_time - start_time).total_seconds() / 60

            if duration >= min_duration_minutes:
                segments.append(current_segment)

        return segments

    def _find_heating_segments(
        self,
        temperature_history: List[Tuple[datetime, float]],
        min_duration_minutes: int,
    ) -> List[List[Tuple[datetime, float]]]:
        """
        Find continuous heating segments in temperature history.

        Args:
            temperature_history: List of (timestamp, temperature) tuples
            min_duration_minutes: Minimum segment duration

        Returns:
            List of heating segments
        """
        segments = []
        current_segment = []

        for i in range(len(temperature_history) - 1):
            current_time, current_temp = temperature_history[i]
            next_time, next_temp = temperature_history[i + 1]

            # Check if temperature is increasing
            if next_temp > current_temp:
                if not current_segment:
                    current_segment.append((current_time, current_temp))
                current_segment.append((next_time, next_temp))
            else:
                # End of heating segment
                if current_segment:
                    start_time = current_segment[0][0]
                    end_time = current_segment[-1][0]
                    duration = (end_time - start_time).total_seconds() / 60

                    if duration >= min_duration_minutes:
                        segments.append(current_segment)

                    current_segment = []

        # Check final segment
        if current_segment:
            start_time = current_segment[0][0]
            end_time = current_segment[-1][0]
            duration = (end_time - start_time).total_seconds() / 60

            if duration >= min_duration_minutes:
                segments.append(current_segment)

        return segments

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

    def __init__(self, zone_name: Optional[str] = None):
        """
        Initialize the AdaptiveLearner.

        Args:
            zone_name: Optional zone name for zone-specific adjustments
        """
        self.zone_name = zone_name
        self._cycle_history: List[CycleMetrics] = []

        # Calculate zone adjustment factors once at initialization
        # These are stored separately and applied as final multipliers
        self._zone_kp_factor, self._zone_ki_factor, self._zone_kd_factor = (
            self._calculate_zone_factors()
        )

    def _calculate_zone_factors(self) -> Tuple[float, float, float]:
        """
        Calculate zone-specific adjustment factors based on zone name.

        These factors are calculated once at initialization and applied
        as multipliers on the final PID values to avoid compounding
        adjustments across multiple cycles.

        Zone-specific adjustments:
        - Kitchen: Lower Ki (oven/door disturbances)
        - Bathroom: Higher Kp (skylight heat loss)
        - Bedroom: Lower Ki (night ventilation)
        - Ground Floor: Higher Ki (exterior doors)

        Returns:
            Tuple of (kp_factor, ki_factor, kd_factor)
        """
        kp_factor = 1.0
        ki_factor = 1.0
        kd_factor = 1.0

        if not self.zone_name:
            return kp_factor, ki_factor, kd_factor

        zone_lower = self.zone_name.lower()

        if "kitchen" in zone_lower:
            # Kitchen: lower Ki due to oven/door disturbances
            ki_factor = 0.8
            _LOGGER.info(
                f"Zone '{self.zone_name}': Ki factor set to 0.8 for disturbance handling"
            )

        elif "bathroom" in zone_lower:
            # Bathroom: higher Kp for skylight heat loss
            kp_factor = 1.1
            _LOGGER.info(
                f"Zone '{self.zone_name}': Kp factor set to 1.1 for heat loss compensation"
            )

        elif "bedroom" in zone_lower:
            # Bedroom: lower Ki for night ventilation
            ki_factor = 0.85
            _LOGGER.info(
                f"Zone '{self.zone_name}': Ki factor set to 0.85 for ventilation"
            )

        elif "ground" in zone_lower or "gf" in zone_lower:
            # Ground floor: higher Ki for exterior door disturbances
            ki_factor = 1.15
            _LOGGER.info(
                f"Zone '{self.zone_name}': Ki factor set to 1.15 for door disturbances"
            )

        return kp_factor, ki_factor, kd_factor

    def get_zone_factors(self) -> Dict[str, float]:
        """
        Get the zone-specific adjustment factors.

        Returns:
            Dictionary with kp_factor, ki_factor, kd_factor
        """
        return {
            "kp_factor": self._zone_kp_factor,
            "ki_factor": self._zone_ki_factor,
            "kd_factor": self._zone_kd_factor,
        }

    def add_cycle_metrics(self, metrics: CycleMetrics) -> None:
        """
        Add a cycle's performance metrics to history.

        Args:
            metrics: CycleMetrics object with performance data
        """
        self._cycle_history.append(metrics)

    def get_cycle_count(self) -> int:
        """
        Get number of stored cycle metrics.

        Returns:
            Number of cycles in history
        """
        return len(self._cycle_history)

    def calculate_pid_adjustment(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
        min_cycles: int = MIN_CYCLES_FOR_LEARNING,
        apply_zone_factors: bool = True,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate PID adjustments based on observed cycle performance.

        Implements rule-based adaptive tuning:
        - High overshoot (>0.5C): Reduce Kp by up to 15%, reduce Ki
        - Moderate overshoot (>0.2C): Reduce Kp by 5%
        - Slow response (>60 min): Increase Kp by 10%
        - Undershoot (>0.3C): Increase Ki by up to 20%
        - Many oscillations (>3): Reduce Kp by 10%, increase Kd by 20%
        - Some oscillations (>1): Increase Kd by 10%
        - Slow settling (>90 min): Increase Kd by 15%

        Zone-specific adjustments are applied as final multipliers (not compounded
        per-cycle). The zone factors are calculated once at initialization and
        stored in _zone_kp_factor, _zone_ki_factor, _zone_kd_factor.

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            min_cycles: Minimum cycles required before making recommendations
            apply_zone_factors: Whether to apply zone-specific factors (default True)

        Returns:
            Dictionary with recommended kp, ki, kd values, or None if insufficient data
        """
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

        # Start with current values
        new_kp = current_kp
        new_ki = current_ki
        new_kd = current_kd

        # Apply rules based on performance metrics

        # High overshoot: reduce Kp and Ki
        if avg_overshoot > 0.5:
            reduction = min(0.15, avg_overshoot * 0.2)  # Up to 15% reduction
            new_kp *= (1.0 - reduction)
            new_ki *= 0.9  # Reduce integral gain by 10%
            _LOGGER.info(f"High overshoot ({avg_overshoot:.2f}C): reducing Kp by {reduction*100:.1f}%")

        # Moderate overshoot: reduce Kp slightly
        elif avg_overshoot > 0.2:
            new_kp *= 0.95
            _LOGGER.info(f"Moderate overshoot ({avg_overshoot:.2f}C): reducing Kp by 5%")

        # Slow response: increase Kp
        if avg_rise_time > 60:
            new_kp *= 1.10
            _LOGGER.info(f"Slow rise time ({avg_rise_time:.1f} min): increasing Kp by 10%")

        # Undershoot: increase Ki
        if avg_undershoot > 0.3:
            increase = min(0.20, avg_undershoot * 0.4)  # Up to 20% increase
            new_ki *= (1.0 + increase)
            _LOGGER.info(f"Undershoot ({avg_undershoot:.2f}C): increasing Ki by {increase*100:.1f}%")

        # Many oscillations: reduce Kp, increase Kd
        if avg_oscillations > 3:
            new_kp *= 0.90
            new_kd *= 1.20
            _LOGGER.info(f"Many oscillations ({avg_oscillations:.1f}): reducing Kp by 10%, increasing Kd by 20%")

        # Some oscillations: increase Kd
        elif avg_oscillations > 1:
            new_kd *= 1.10
            _LOGGER.info(f"Some oscillations ({avg_oscillations:.1f}): increasing Kd by 10%")

        # Slow settling: increase Kd
        if avg_settling_time > 90:
            new_kd *= 1.15
            _LOGGER.info(f"Slow settling ({avg_settling_time:.1f} min): increasing Kd by 15%")

        # Apply zone-specific adjustment factors as final multipliers
        # These factors are calculated once at initialization, not compounded per-cycle
        if apply_zone_factors:
            new_kp *= self._zone_kp_factor
            new_ki *= self._zone_ki_factor
            new_kd *= self._zone_kd_factor

        # Enforce PID limits
        new_kp = max(PID_LIMITS["kp_min"], min(PID_LIMITS["kp_max"], new_kp))
        new_ki = max(PID_LIMITS["ki_min"], min(PID_LIMITS["ki_max"], new_ki))
        new_kd = max(PID_LIMITS["kd_min"], min(PID_LIMITS["kd_max"], new_kd))

        return {
            "kp": new_kp,
            "ki": new_ki,
            "kd": new_kd,
        }

    def clear_history(self) -> None:
        """Clear all stored cycle metrics."""
        self._cycle_history.clear()


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
    ) -> bool:
        """
        Save learning data to storage.

        Args:
            thermal_learner: ThermalRateLearner instance to save
            adaptive_learner: AdaptiveLearner instance to save
            valve_tracker: ValveCycleTracker instance to save

        Returns:
            True if save successful, False otherwise
        """
        try:
            # Ensure storage directory exists
            os.makedirs(self.storage_path, exist_ok=True)

            data: Dict[str, Any] = {
                "version": 1,
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
                    "zone_name": adaptive_learner.zone_name,
                    "cycle_history": cycle_history,
                }

            # Save ValveCycleTracker data
            if valve_tracker is not None:
                data["valve_tracker"] = {
                    "cycle_count": valve_tracker._cycle_count,
                    "last_state": valve_tracker._last_state,
                }

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
            learner = AdaptiveLearner(zone_name=adaptive_data.get("zone_name"))

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

            _LOGGER.info(
                f"Restored AdaptiveLearner for zone '{learner.zone_name}': "
                f"{learner.get_cycle_count()} cycles"
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
