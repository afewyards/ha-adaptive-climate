"""Solar gain learning and prediction for adaptive thermostat."""
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Dict, List, Optional, Tuple
import math


class WindowOrientation(Enum):
    """Window orientation for solar gain calculation."""
    NORTH = "north"
    NORTHEAST = "northeast"
    EAST = "east"
    SOUTHEAST = "southeast"
    SOUTH = "south"
    SOUTHWEST = "southwest"
    WEST = "west"
    NORTHWEST = "northwest"
    NONE = "none"


class Season(Enum):
    """Season for solar angle calculation."""
    WINTER = "winter"  # Dec 21 - Mar 20
    SPRING = "spring"  # Mar 21 - Jun 20
    SUMMER = "summer"  # Jun 21 - Sep 20
    FALL = "fall"      # Sep 21 - Dec 20


class CloudCoverage(Enum):
    """Cloud coverage levels."""
    CLEAR = "clear"           # 0-10% cloud coverage
    PARTLY_CLOUDY = "partly_cloudy"  # 10-50%
    CLOUDY = "cloudy"         # 50-90%
    OVERCAST = "overcast"     # 90-100%


@dataclass
class SolarGainMeasurement:
    """Single solar gain measurement."""
    timestamp: datetime
    temperature_rise_c: float  # Temperature rise attributed to solar gain
    hour_of_day: int
    season: Season
    orientation: WindowOrientation
    cloud_coverage: CloudCoverage


@dataclass
class SolarGainPattern:
    """Learned solar gain pattern for a specific time/season/orientation."""
    avg_gain_c_per_hour: float
    measurement_count: int
    hour_of_day: int
    season: Season
    orientation: WindowOrientation
    cloud_coverage: CloudCoverage


class SolarGainLearner:
    """Learns solar gain patterns from observed temperature changes."""

    def __init__(self, zone_id: str, orientation: WindowOrientation):
        """
        Initialize solar gain learner.

        Args:
            zone_id: Zone identifier
            orientation: Window orientation for this zone
        """
        self.zone_id = zone_id
        self.orientation = orientation
        self.measurements: List[SolarGainMeasurement] = []
        self.patterns: Dict[Tuple[int, Season, CloudCoverage], SolarGainPattern] = {}

    def add_measurement(
        self,
        timestamp: datetime,
        temperature_rise_c: float,
        cloud_coverage: CloudCoverage
    ) -> None:
        """
        Add a solar gain measurement.

        Args:
            timestamp: Time of measurement
            temperature_rise_c: Temperature rise attributed to solar gain
            cloud_coverage: Cloud coverage at measurement time
        """
        if temperature_rise_c <= 0:
            return  # Ignore non-positive gains

        season = self._get_season(timestamp)
        hour = timestamp.hour

        measurement = SolarGainMeasurement(
            timestamp=timestamp,
            temperature_rise_c=temperature_rise_c,
            hour_of_day=hour,
            season=season,
            orientation=self.orientation,
            cloud_coverage=cloud_coverage
        )

        self.measurements.append(measurement)
        self._update_patterns()

    def _get_season(self, dt: datetime) -> Season:
        """
        Determine season from datetime.

        Args:
            dt: Datetime to check

        Returns:
            Season enum value
        """
        month = dt.month
        day = dt.day

        # Winter: Dec 21 - Mar 20
        if month == 12 and day >= 21:
            return Season.WINTER
        if month in [1, 2]:
            return Season.WINTER
        if month == 3 and day < 21:
            return Season.WINTER

        # Spring: Mar 21 - Jun 20
        if month == 3 and day >= 21:
            return Season.SPRING
        if month in [4, 5]:
            return Season.SPRING
        if month == 6 and day < 21:
            return Season.SPRING

        # Summer: Jun 21 - Sep 20
        if month == 6 and day >= 21:
            return Season.SUMMER
        if month in [7, 8]:
            return Season.SUMMER
        if month == 9 and day < 21:
            return Season.SUMMER

        # Fall: Sep 21 - Dec 20
        return Season.FALL

    def _update_patterns(self) -> None:
        """Update learned patterns from all measurements."""
        # Group measurements by (hour, season, cloud_coverage)
        grouped: Dict[Tuple[int, Season, CloudCoverage], List[float]] = {}

        for m in self.measurements:
            key = (m.hour_of_day, m.season, m.cloud_coverage)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(m.temperature_rise_c)

        # Calculate average for each group
        self.patterns.clear()
        for key, gains in grouped.items():
            if len(gains) >= 2:  # Require at least 2 measurements
                hour, season, cloud = key
                avg_gain = sum(gains) / len(gains)

                pattern = SolarGainPattern(
                    avg_gain_c_per_hour=avg_gain,
                    measurement_count=len(gains),
                    hour_of_day=hour,
                    season=season,
                    orientation=self.orientation,
                    cloud_coverage=cloud
                )
                self.patterns[key] = pattern

    def predict_solar_gain(
        self,
        timestamp: datetime,
        cloud_coverage: CloudCoverage,
        fallback_gain_c_per_hour: float = 0.0
    ) -> float:
        """
        Predict solar gain for a specific time and conditions.

        Args:
            timestamp: Time to predict for
            cloud_coverage: Expected cloud coverage
            fallback_gain_c_per_hour: Fallback if no pattern learned

        Returns:
            Predicted solar gain in °C/hour
        """
        season = self._get_season(timestamp)
        hour = timestamp.hour

        # Try exact match first
        key = (hour, season, cloud_coverage)
        if key in self.patterns:
            return self.patterns[key].avg_gain_c_per_hour

        # Try same hour and season but any cloud coverage
        for (h, s, c), pattern in self.patterns.items():
            if h == hour and s == season:
                # Apply cloud coverage adjustment
                adjustment = self._get_cloud_adjustment(c, cloud_coverage)
                return pattern.avg_gain_c_per_hour * adjustment

        # Try same hour but any season/cloud
        for (h, s, c), pattern in self.patterns.items():
            if h == hour:
                # Apply seasonal and cloud adjustments
                seasonal_adj = self._get_seasonal_adjustment(s, season)
                cloud_adj = self._get_cloud_adjustment(c, cloud_coverage)
                return pattern.avg_gain_c_per_hour * seasonal_adj * cloud_adj

        # No learned pattern, use fallback
        return fallback_gain_c_per_hour

    def _get_cloud_adjustment(
        self,
        learned_cloud: CloudCoverage,
        actual_cloud: CloudCoverage
    ) -> float:
        """
        Calculate adjustment factor for different cloud coverage.

        Args:
            learned_cloud: Cloud coverage of learned pattern
            actual_cloud: Actual cloud coverage

        Returns:
            Adjustment factor (0.0-1.0)
        """
        # Cloud coverage reduction factors
        cloud_factors = {
            CloudCoverage.CLEAR: 1.0,
            CloudCoverage.PARTLY_CLOUDY: 0.7,
            CloudCoverage.CLOUDY: 0.4,
            CloudCoverage.OVERCAST: 0.1
        }

        learned_factor = cloud_factors[learned_cloud]
        actual_factor = cloud_factors[actual_cloud]

        # If learned is clear, scale down by actual factor
        if learned_factor > 0:
            return actual_factor / learned_factor
        return actual_factor

    def _get_seasonal_adjustment(
        self,
        learned_season: Season,
        actual_season: Season
    ) -> float:
        """
        Calculate adjustment factor for different seasons (sun angle).

        Args:
            learned_season: Season of learned pattern
            actual_season: Actual season

        Returns:
            Adjustment factor based on sun angle differences
        """
        if learned_season == actual_season:
            return 1.0

        # Solar intensity by season (relative to summer = 1.0)
        # Based on sun angle and day length
        seasonal_intensity = {
            Season.WINTER: 0.4,
            Season.SPRING: 0.8,
            Season.SUMMER: 1.0,
            Season.FALL: 0.7
        }

        # Further adjust by orientation
        orientation_seasonal_impact = {
            WindowOrientation.SOUTH: 1.0,  # Most affected by season
            WindowOrientation.SOUTHEAST: 0.85,  # Between south and east
            WindowOrientation.SOUTHWEST: 0.85,  # Between south and west
            WindowOrientation.EAST: 0.7,
            WindowOrientation.WEST: 0.7,
            WindowOrientation.NORTHEAST: 0.5,  # Between north and east
            WindowOrientation.NORTHWEST: 0.5,  # Between north and west
            WindowOrientation.NORTH: 0.3,  # Least affected
            WindowOrientation.NONE: 0.0
        }

        learned_intensity = seasonal_intensity[learned_season]
        actual_intensity = seasonal_intensity[actual_season]
        orientation_impact = orientation_seasonal_impact[self.orientation]

        # Calculate adjustment
        if learned_intensity > 0:
            base_adjustment = actual_intensity / learned_intensity
        else:
            base_adjustment = actual_intensity

        # Apply orientation impact (less sensitive orientations get smaller adjustments)
        adjustment = 1.0 + (base_adjustment - 1.0) * orientation_impact
        return adjustment

    def get_measurement_count(self) -> int:
        """Get total number of measurements."""
        return len(self.measurements)

    def get_pattern_count(self) -> int:
        """Get number of learned patterns."""
        return len(self.patterns)

    def clear_measurements(self) -> None:
        """Clear all measurements and patterns."""
        self.measurements.clear()
        self.patterns.clear()


class SolarGainManager:
    """Manages solar gain learning for multiple zones."""

    def __init__(self):
        """Initialize solar gain manager."""
        self.learners: Dict[str, SolarGainLearner] = {}

    def configure_zone(
        self,
        zone_id: str,
        orientation: WindowOrientation
    ) -> None:
        """
        Configure solar gain learning for a zone.

        Args:
            zone_id: Zone identifier
            orientation: Window orientation for this zone
        """
        self.learners[zone_id] = SolarGainLearner(zone_id, orientation)

    def add_measurement(
        self,
        zone_id: str,
        timestamp: datetime,
        temperature_rise_c: float,
        cloud_coverage: CloudCoverage
    ) -> None:
        """
        Add a measurement for a zone.

        Args:
            zone_id: Zone identifier
            timestamp: Time of measurement
            temperature_rise_c: Temperature rise attributed to solar gain
            cloud_coverage: Cloud coverage at measurement time
        """
        if zone_id in self.learners:
            self.learners[zone_id].add_measurement(
                timestamp,
                temperature_rise_c,
                cloud_coverage
            )

    def predict_solar_gain(
        self,
        zone_id: str,
        timestamp: datetime,
        cloud_coverage: CloudCoverage,
        fallback_gain_c_per_hour: float = 0.0
    ) -> float:
        """
        Predict solar gain for a zone.

        Args:
            zone_id: Zone identifier
            timestamp: Time to predict for
            cloud_coverage: Expected cloud coverage
            fallback_gain_c_per_hour: Fallback if no pattern learned

        Returns:
            Predicted solar gain in °C/hour
        """
        if zone_id in self.learners:
            return self.learners[zone_id].predict_solar_gain(
                timestamp,
                cloud_coverage,
                fallback_gain_c_per_hour
            )
        return fallback_gain_c_per_hour

    def get_learner(self, zone_id: str) -> Optional[SolarGainLearner]:
        """Get learner for a zone."""
        return self.learners.get(zone_id)
