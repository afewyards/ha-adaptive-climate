"""Solar recovery module for adaptive thermostat.

Implements energy-saving solar recovery based on window orientation,
allowing the sun to warm zones instead of active heating when appropriate.
"""
from datetime import datetime, time, timedelta
from typing import Optional
from enum import Enum


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
        recovery_deadline: Optional[str] = None
    ):
        """Initialize solar recovery.

        Args:
            window_orientation: Window orientation ("north", "south", "east", "west", "none")
            base_recovery_time: Base recovery time as "HH:MM" (default 06:00)
            recovery_deadline: Hard deadline time "HH:MM" when temp must be restored
        """
        # Parse orientation
        orientation_str = window_orientation.lower()
        self.orientation = WindowOrientation(orientation_str)

        # Parse base recovery time
        self.base_recovery_time = self._parse_time(base_recovery_time)

        # Calculate adjusted recovery time based on orientation
        offset_minutes = self.ORIENTATION_OFFSETS[self.orientation]
        base_dt = datetime.combine(datetime.today(), self.base_recovery_time)
        adjusted_dt = base_dt + timedelta(minutes=offset_minutes)
        self.adjusted_recovery_time = adjusted_dt.time()

        # Parse recovery deadline
        self.recovery_deadline = self._parse_time(recovery_deadline) if recovery_deadline else None

    def _parse_time(self, time_str: str) -> time:
        """Parse time string in HH:MM format.

        Args:
            time_str: Time string like "06:00"

        Returns:
            time object
        """
        hour, minute = map(int, time_str.split(":"))
        return time(hour, minute)

    def get_recovery_time(self) -> time:
        """Get the adjusted recovery time for this zone.

        Returns:
            Recovery time adjusted for window orientation
        """
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
        1. Current time is before the adjusted recovery time
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

        # If we're past the adjusted recovery time, use active heating
        if current_time_only >= self.adjusted_recovery_time:
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
        recovery_deadline: Optional[str] = None
    ):
        """Configure solar recovery for a zone.

        Args:
            zone_id: Zone identifier
            window_orientation: Window orientation ("north", "south", "east", "west", "none")
            base_recovery_time: Base recovery time as "HH:MM" (default 06:00)
            recovery_deadline: Hard deadline time "HH:MM" when temp must be restored
        """
        self._zone_recoveries[zone_id] = SolarRecovery(
            window_orientation=window_orientation,
            base_recovery_time=base_recovery_time,
            recovery_deadline=recovery_deadline
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

    def get_zone_recovery_time(self, zone_id: str) -> Optional[time]:
        """Get the recovery time for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Recovery time or None if not configured
        """
        if zone_id not in self._zone_recoveries:
            return None

        recovery = self._zone_recoveries[zone_id]
        return recovery.get_recovery_time()

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
