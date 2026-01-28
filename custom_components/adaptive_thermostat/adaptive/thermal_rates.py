"""Thermal rate learning for Adaptive Thermostat.

This module provides the ThermalRateLearner class for learning thermal heating
and cooling rates from observed temperature history.
"""

from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Deque
import statistics
import logging

from ..const import (
    SEGMENT_NOISE_TOLERANCE,
    SEGMENT_RATE_MIN,
    SEGMENT_RATE_MAX,
)

_LOGGER = logging.getLogger(__name__)


class ThermalRateLearner:
    """Learn thermal heating and cooling rates from observed temperature history."""

    def __init__(
        self,
        outlier_threshold: float = 2.0,
        noise_tolerance: float = SEGMENT_NOISE_TOLERANCE,
        max_measurements: int = 50,
    ):
        """
        Initialize the ThermalRateLearner.

        Args:
            outlier_threshold: Number of standard deviations for outlier rejection
            noise_tolerance: Temperature changes below this threshold are ignored as noise (default 0.05C)
            max_measurements: Maximum number of measurements to keep (FIFO eviction, default 50)
        """
        self.outlier_threshold = outlier_threshold
        self.noise_tolerance = noise_tolerance
        self.max_measurements = max_measurements
        self._cooling_rates: Deque[float] = deque(maxlen=max_measurements)
        self._heating_rates: Deque[float] = deque(maxlen=max_measurements)

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
            Cooling rate in C/hour, or None if insufficient data
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

            rate = temp_drop / duration_hours  # C/hour (positive for cooling)
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
            Heating rate in C/hour, or None if insufficient data
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

            rate = temp_rise / duration_hours  # C/hour (positive for heating)
            rates.append(rate)

        if not rates:
            return None

        # Return median rate to reduce impact of outliers
        return statistics.median(rates)

    def add_cooling_measurement(self, rate: float) -> None:
        """
        Add a cooling rate measurement.

        Args:
            rate: Cooling rate in C/hour
        """
        if rate > 0:
            self._cooling_rates.append(rate)

    def add_heating_measurement(self, rate: float) -> None:
        """
        Add a heating rate measurement.

        Args:
            rate: Heating rate in C/hour
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
            Average cooling rate in C/hour, or None if insufficient data
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
            Average heating rate in C/hour, or None if insufficient data
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
