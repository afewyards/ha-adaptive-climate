"""Solar recovery module for adaptive thermostat.

Implements energy-saving solar recovery based on window orientation,
allowing the sun to warm zones instead of active heating when appropriate.

Supports dynamic sun position-based timing when a SunPositionCalculator
is provided, falling back to static orientation offsets otherwise.
"""
from datetime import datetime, time, timedelta, date
from typing import Optional, TYPE_CHECKING
from enum import Enum
import logging

if TYPE_CHECKING:
    from .sun_position import SunPositionCalculator

_LOGGER = logging.getLogger(__name__)


class WindowOrientation(Enum):
    """Window orientation enum."""
    NORTH = "north"
    NORTHEAST = "northeast"
    EAST = "east"
    SOUTHEAST = "southeast"
    SOUTH = "south"
    SOUTHWEST = "southwest"
    WEST = "west"
    NORTHWEST = "northwest"
    ROOF = "roof"  # Skylights
    NONE = "none"  # No windows or unknown


class SolarRecovery:
    """Manages solar recovery for a zone based on window orientation.

    Solar recovery delays active heating in the morning to let the sun warm
    the zone naturally, saving energy. The recovery timing depends on window
    orientation:
    - South windows: Earlier recovery allowed (direct morning/afternoon sun)
    - East windows: Early recovery (morning sun)
    - West windows: Later recovery (afternoon sun only)
    - North windows: Latest recovery (minimal direct sun)
    """

    # Recovery time adjustments based on orientation (minutes from base time)
    ORIENTATION_OFFSETS = {
        WindowOrientation.SOUTH: -30,      # 30 min earlier - best sun exposure
        WindowOrientation.SOUTHEAST: -25,  # 25 min earlier - morning + midday sun
        WindowOrientation.SOUTHWEST: -5,   # 5 min earlier - midday + afternoon sun
        WindowOrientation.EAST: -20,       # 20 min earlier - morning sun
        WindowOrientation.WEST: +20,       # 20 min later - afternoon sun only
        WindowOrientation.NORTHEAST: +5,   # 5 min later - some morning sun
        WindowOrientation.NORTHWEST: +25,  # 25 min later - some afternoon sun
        WindowOrientation.NORTH: +30,      # 30 min later - minimal sun
        WindowOrientation.ROOF: -30,       # 30 min earlier - skylights get good midday sun
        WindowOrientation.NONE: 0,         # No adjustment - no windows
    }

    def __init__(
        self,
        window_orientation: str,
        base_recovery_time: str = "06:00",
        recovery_deadline: Optional[str] = None,
        sun_position_calculator: Optional["SunPositionCalculator"] = None,
        min_effective_elevation: float = 10.0,
    ):
        """Initialize solar recovery.

        Args:
            window_orientation: Window orientation ("north", "south", "east", "west", "none")
            base_recovery_time: Base recovery time as "HH:MM" (default 06:00)
            recovery_deadline: Hard deadline time "HH:MM" when temp must be restored
            sun_position_calculator: Optional calculator for dynamic sun-based timing
            min_effective_elevation: Minimum sun elevation (degrees) for effective solar gain
        """
        # Parse orientation
        orientation_str = window_orientation.lower()
        self.orientation = WindowOrientation(orientation_str)

        # Parse base recovery time
        self.base_recovery_time = self._parse_time(base_recovery_time)

        # Calculate adjusted recovery time based on orientation (static fallback)
        offset_minutes = self.ORIENTATION_OFFSETS[self.orientation]
        base_dt = datetime.combine(datetime.today(), self.base_recovery_time)
        adjusted_dt = base_dt + timedelta(minutes=offset_minutes)
        self.adjusted_recovery_time = adjusted_dt.time()

        # Parse recovery deadline
        self.recovery_deadline = self._parse_time(recovery_deadline) if recovery_deadline else None

        # Dynamic sun position support
        self._sun_calculator = sun_position_calculator
        self._min_effective_elevation = min_effective_elevation
        self._cached_dynamic_time: Optional[time] = None
        self._cache_date: Optional[date] = None

    def _parse_time(self, time_str: str) -> time:
        """Parse time string in HH:MM format.

        Args:
            time_str: Time string like "06:00"

        Returns:
            time object
        """
        hour, minute = map(int, time_str.split(":"))
        return time(hour, minute)

    def set_sun_calculator(self, calculator: Optional["SunPositionCalculator"]) -> None:
        """Set or update the sun position calculator.

        Args:
            calculator: Sun position calculator instance or None
        """
        self._sun_calculator = calculator
        # Clear cache when calculator changes
        self._cached_dynamic_time = None
        self._cache_date = None

    def get_dynamic_recovery_time(self, target_date: date) -> Optional[time]:
        """Calculate recovery time based on actual sun position.

        Uses sun position calculator to find when sunlight first effectively
        illuminates the window, rather than using static offsets.

        Args:
            target_date: Date to calculate for

        Returns:
            Dynamic recovery time, or None if calculation unavailable
        """
        if not self._sun_calculator:
            return None

        # Use cached result if same day
        if self._cache_date == target_date and self._cached_dynamic_time is not None:
            return self._cached_dynamic_time

        try:
            entry_time = self._sun_calculator.calculate_window_sun_entry_time(
                window_orientation=self.orientation.value,
                target_date=target_date,
                min_elevation=self._min_effective_elevation,
            )

            if entry_time:
                # Cache result
                self._cache_date = target_date
                self._cached_dynamic_time = entry_time.time()
                _LOGGER.debug(
                    "Dynamic recovery time for %s window on %s: %s",
                    self.orientation.value,
                    target_date,
                    self._cached_dynamic_time,
                )
                return self._cached_dynamic_time
        except Exception as err:
            _LOGGER.warning("Failed to calculate dynamic recovery time: %s", err)

        return None

    def get_recovery_time(self, current_time: Optional[datetime] = None) -> time:
        """Get recovery time, preferring dynamic calculation when available.

        Args:
            current_time: Current datetime (used for dynamic calculation)

        Returns:
            Recovery time (dynamic if available and current_time provided,
            otherwise static orientation-based time)
        """
        if current_time and self._sun_calculator:
            dynamic_time = self.get_dynamic_recovery_time(current_time.date())
            if dynamic_time:
                return dynamic_time

        # Fallback to static calculation
        return self.adjusted_recovery_time

    def should_use_solar_recovery(
        self,
        current_time: datetime,
        current_temp: float,
        target_setpoint: float,
        heating_rate_c_per_hour: float = 2.0
    ) -> bool:
        """Determine if solar recovery should be used instead of active heating.

        Solar recovery is used when:
        1. Current time is before the recovery time (dynamic or static)
        2. We have enough time for sun to warm the zone before deadline
        3. Recovery deadline (if set) is not approaching

        Args:
            current_time: Current datetime
            current_temp: Current temperature in °C
            target_setpoint: Target temperature in °C
            heating_rate_c_per_hour: Expected heating rate (default 2.0 °C/hour)

        Returns:
            True if solar recovery should be used (delay active heating)
            False if active heating should start now
        """
        current_time_only = current_time.time()

        # Check if recovery deadline is approaching
        if self.recovery_deadline:
            # Calculate time until deadline
            deadline_dt = datetime.combine(current_time.date(), self.recovery_deadline)
            if deadline_dt < current_time:
                # Deadline is tomorrow
                deadline_dt = deadline_dt + timedelta(days=1)

            hours_until_deadline = (deadline_dt - current_time).total_seconds() / 3600

            # Calculate temperature deficit and recovery time needed
            temp_deficit = target_setpoint - current_temp
            recovery_hours_needed = max(0, temp_deficit / heating_rate_c_per_hour)

            # If deadline is approaching and we don't have enough time, use active heating
            if recovery_hours_needed >= hours_until_deadline:
                return False

        # Get recovery time (dynamic if available, else static)
        recovery_time = self.get_recovery_time(current_time)

        # If we're past the recovery time, use active heating
        if current_time_only >= recovery_time:
            return False

        # Otherwise, let the sun warm the zone
        return True


class SolarRecoveryManager:
    """Manages solar recovery for multiple zones."""

    def __init__(self):
        """Initialize solar recovery manager."""
        self._zone_recoveries = {}

    def configure_zone(
        self,
        zone_id: str,
        window_orientation: str,
        base_recovery_time: str = "06:00",
        recovery_deadline: Optional[str] = None,
        sun_position_calculator: Optional["SunPositionCalculator"] = None,
        min_effective_elevation: float = 10.0,
    ):
        """Configure solar recovery for a zone.

        Args:
            zone_id: Zone identifier
            window_orientation: Window orientation ("north", "south", "east", "west", "none")
            base_recovery_time: Base recovery time as "HH:MM" (default 06:00)
            recovery_deadline: Hard deadline time "HH:MM" when temp must be restored
            sun_position_calculator: Optional calculator for dynamic sun-based timing
            min_effective_elevation: Minimum sun elevation (degrees) for effective solar gain
        """
        self._zone_recoveries[zone_id] = SolarRecovery(
            window_orientation=window_orientation,
            base_recovery_time=base_recovery_time,
            recovery_deadline=recovery_deadline,
            sun_position_calculator=sun_position_calculator,
            min_effective_elevation=min_effective_elevation,
        )

    def should_use_solar_recovery(
        self,
        zone_id: str,
        current_time: datetime,
        current_temp: float,
        target_setpoint: float,
        heating_rate_c_per_hour: float = 2.0
    ) -> bool:
        """Check if solar recovery should be used for a zone.

        Args:
            zone_id: Zone identifier
            current_time: Current datetime
            current_temp: Current temperature in °C
            target_setpoint: Target temperature in °C
            heating_rate_c_per_hour: Expected heating rate (default 2.0 °C/hour)

        Returns:
            True if solar recovery should be used, False if active heating needed
        """
        if zone_id not in self._zone_recoveries:
            # No solar recovery configured - use active heating
            return False

        recovery = self._zone_recoveries[zone_id]
        return recovery.should_use_solar_recovery(
            current_time,
            current_temp,
            target_setpoint,
            heating_rate_c_per_hour
        )

    def get_zone_recovery_time(
        self,
        zone_id: str,
        current_time: Optional[datetime] = None,
    ) -> Optional[time]:
        """Get the recovery time for a zone.

        Args:
            zone_id: Zone identifier
            current_time: Current datetime for dynamic calculation

        Returns:
            Recovery time or None if not configured
        """
        if zone_id not in self._zone_recoveries:
            return None

        recovery = self._zone_recoveries[zone_id]
        return recovery.get_recovery_time(current_time)

    def get_zone_config(self, zone_id: str) -> Optional[dict]:
        """Get solar recovery configuration for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Configuration dictionary or None if not configured
        """
        if zone_id not in self._zone_recoveries:
            return None

        recovery = self._zone_recoveries[zone_id]
        return {
            "orientation": recovery.orientation.value,
            "base_recovery_time": recovery.base_recovery_time.strftime("%H:%M"),
            "adjusted_recovery_time": recovery.adjusted_recovery_time.strftime("%H:%M"),
            "recovery_deadline": recovery.recovery_deadline.strftime("%H:%M") if recovery.recovery_deadline else None,
        }
