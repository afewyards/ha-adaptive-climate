"""Thermal rate learning and adaptive PID adjustments for Adaptive Thermostat."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import statistics
import logging

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
