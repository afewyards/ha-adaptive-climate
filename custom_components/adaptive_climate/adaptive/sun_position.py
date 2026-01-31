"""Sun position calculations for dynamic solar recovery timing."""
from dataclasses import dataclass
from datetime import datetime, timedelta, time, date
from typing import Optional, TYPE_CHECKING
import logging

from astral import Observer
from astral.sun import azimuth, elevation, sunrise

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Window orientation to azimuth mapping (degrees clockwise from north)
ORIENTATION_AZIMUTH = {
    "north": 0,
    "northeast": 45,
    "east": 90,
    "southeast": 135,
    "south": 180,
    "southwest": 225,
    "west": 270,
    "northwest": 315,
    "roof": None,  # Skylights - use elevation only
    "none": None,
}

# Effective sun angle range for windows (degrees from facing direction)
DEFAULT_EFFECTIVE_ANGLE = 45  # Sun within +/- 45 degrees of window normal


@dataclass
class SunPosition:
    """Sun position at a point in time."""

    azimuth: float  # 0-360 degrees clockwise from north
    elevation: float  # degrees above horizon (negative = below)
    timestamp: datetime


class SunPositionCalculator:
    """Calculates sun position for dynamic solar recovery timing.

    Uses the astral library (bundled with Home Assistant) to calculate
    when the sun's azimuth aligns with a window's orientation, enabling
    dynamic solar recovery timing that adapts to season and location.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        elevation_m: float = 0,
    ):
        """Initialize with location.

        Args:
            latitude: Location latitude (-90 to 90)
            longitude: Location longitude (-180 to 180)
            elevation_m: Elevation above sea level in meters
        """
        self._observer = Observer(
            latitude=latitude,
            longitude=longitude,
            elevation=elevation_m,
        )
        self._latitude = latitude
        self._longitude = longitude

    @classmethod
    def from_hass(cls, hass: "HomeAssistant") -> Optional["SunPositionCalculator"]:
        """Create calculator from Home Assistant config.

        Args:
            hass: Home Assistant instance

        Returns:
            SunPositionCalculator or None if location unavailable
        """
        try:
            lat = hass.config.latitude
            lon = hass.config.longitude
            elev = hass.config.elevation or 0

            if lat is None or lon is None:
                _LOGGER.warning(
                    "Home location not configured in Home Assistant, "
                    "dynamic solar recovery unavailable"
                )
                return None

            return cls(latitude=lat, longitude=lon, elevation_m=elev)
        except Exception as err:
            _LOGGER.error("Failed to create SunPositionCalculator: %s", err)
            return None

    def get_position_at_time(self, dt: datetime) -> SunPosition:
        """Get sun position at a specific time.

        Args:
            dt: Datetime to calculate position for

        Returns:
            SunPosition with azimuth and elevation
        """
        az = azimuth(self._observer, dt)
        el = elevation(self._observer, dt)
        return SunPosition(azimuth=az, elevation=el, timestamp=dt)

    def calculate_window_sun_entry_time(
        self,
        window_orientation: str,
        target_date: date,
        min_elevation: float = 10.0,
        effective_angle: float = DEFAULT_EFFECTIVE_ANGLE,
    ) -> Optional[datetime]:
        """Calculate when sun first effectively illuminates a window.

        Finds the time after sunrise when:
        1. Sun azimuth is within effective_angle of window facing direction
        2. Sun elevation is above min_elevation

        Args:
            window_orientation: Window facing direction (e.g., "south", "east")
            target_date: Date to calculate for
            min_elevation: Minimum sun elevation for effective heating (degrees)
            effective_angle: Degrees from window normal for effective sun

        Returns:
            Datetime when sun enters effective range, or None if not possible
        """
        orientation = window_orientation.lower()

        # Handle special cases
        if orientation == "roof":
            # Skylights: return time when elevation reaches min_elevation
            return self._find_elevation_time(target_date, min_elevation)

        if orientation == "none" or orientation not in ORIENTATION_AZIMUTH:
            return None

        window_azimuth = ORIENTATION_AZIMUTH[orientation]
        if window_azimuth is None:
            return None

        # Get sunrise for this date
        try:
            sunrise_dt = sunrise(self._observer, target_date)
        except Exception as err:
            _LOGGER.debug("Could not calculate sunrise for %s: %s", target_date, err)
            return None

        # Search from sunrise, stepping 5 minutes at a time
        current_time = sunrise_dt
        # Stop searching at 14:00 local time (afternoon sun won't be "entry" time)
        end_time = datetime.combine(target_date, time(14, 0))
        if sunrise_dt.tzinfo:
            end_time = end_time.replace(tzinfo=sunrise_dt.tzinfo)
        step = timedelta(minutes=5)

        while current_time < end_time:
            pos = self.get_position_at_time(current_time)

            # Check if sun is in effective range for this window
            if self._is_sun_effective(
                pos, window_azimuth, min_elevation, effective_angle
            ):
                return current_time

            current_time += step

        # Sun never enters effective range (e.g., north window in winter)
        return None

    def _is_sun_effective(
        self,
        pos: SunPosition,
        window_azimuth: float,
        min_elevation: float,
        effective_angle: float,
    ) -> bool:
        """Check if sun position is effective for a window.

        Args:
            pos: Current sun position
            window_azimuth: Window facing direction (degrees)
            min_elevation: Minimum elevation threshold
            effective_angle: Max angle from window normal

        Returns:
            True if sun is effective for this window
        """
        # Check elevation first (quick filter)
        if pos.elevation < min_elevation:
            return False

        # Calculate angular difference (handle wrap-around at 360 degrees)
        diff = abs(pos.azimuth - window_azimuth)
        if diff > 180:
            diff = 360 - diff

        return diff <= effective_angle

    def _find_elevation_time(
        self,
        target_date: date,
        target_elevation: float,
    ) -> Optional[datetime]:
        """Find time when sun reaches target elevation (for skylights).

        Args:
            target_date: Date to calculate for
            target_elevation: Target elevation in degrees

        Returns:
            Datetime when elevation is reached, or None
        """
        try:
            sunrise_dt = sunrise(self._observer, target_date)
        except Exception:
            return None

        current_time = sunrise_dt
        step = timedelta(minutes=5)
        noon = datetime.combine(target_date, time(12, 0))
        if sunrise_dt.tzinfo:
            noon = noon.replace(tzinfo=sunrise_dt.tzinfo)

        while current_time < noon:
            pos = self.get_position_at_time(current_time)
            if pos.elevation >= target_elevation:
                return current_time
            current_time += step

        return None
