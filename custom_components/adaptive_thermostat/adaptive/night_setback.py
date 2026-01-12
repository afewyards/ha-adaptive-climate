"""Night setback module for adaptive thermostat.

Implements energy-saving night setback with configurable temperature delta,
sunset support, and recovery deadline overrides.
"""
from datetime import datetime, time
from typing import Optional, Dict, Any


class NightSetback:
    """Manages night setback for a zone.

    Night setback lowers the temperature setpoint during night hours to save energy,
    while ensuring the temperature recovers by a specified deadline.
    """

    def __init__(
        self,
        start_time: str,
        end_time: str,
        setback_delta: float,
        recovery_deadline: Optional[str] = None,
        sunset_offset_minutes: int = 0
    ):
        """Initialize night setback.

        Args:
            start_time: Start time as "HH:MM" or "sunset" or "sunset+30" (minutes offset)
            end_time: End time as "HH:MM"
            setback_delta: Temperature reduction during night (degrees)
            recovery_deadline: Optional override time "HH:MM" when temp must be restored
            sunset_offset_minutes: Minutes to add/subtract from sunset (e.g., +30, -15)
        """
        self.start_time_str = start_time
        self.end_time = self._parse_time(end_time)
        self.setback_delta = setback_delta
        self.recovery_deadline = self._parse_time(recovery_deadline) if recovery_deadline else None
        self.sunset_offset_minutes = sunset_offset_minutes

        # Parse start time (may be "sunset" or "HH:MM")
        if start_time.lower().startswith("sunset"):
            self.use_sunset = True
            # Parse sunset offset from string like "sunset+30" or "sunset-15"
            if "+" in start_time:
                self.sunset_offset_minutes = int(start_time.split("+")[1])
            elif "-" in start_time:
                self.sunset_offset_minutes = -int(start_time.split("-")[1])
        else:
            self.use_sunset = False
            self.start_time = self._parse_time(start_time)

    def _parse_time(self, time_str: str) -> time:
        """Parse time string in HH:MM format.

        Args:
            time_str: Time string like "22:00"

        Returns:
            time object
        """
        hour, minute = map(int, time_str.split(":"))
        return time(hour, minute)

    def is_night_period(
        self,
        current_time: datetime,
        sunset_time: Optional[datetime] = None
    ) -> bool:
        """Check if current time is within night period.

        Args:
            current_time: Current datetime
            sunset_time: Sunset datetime (required if start_time is "sunset")

        Returns:
            True if within night period
        """
        current_time_only = current_time.time()

        # Determine start time
        if self.use_sunset:
            if sunset_time is None:
                raise ValueError("sunset_time required when start_time is 'sunset'")
            start = sunset_time.time()
            # Apply offset
            if self.sunset_offset_minutes != 0:
                from datetime import timedelta
                sunset_with_offset = sunset_time + timedelta(minutes=self.sunset_offset_minutes)
                start = sunset_with_offset.time()
        else:
            start = self.start_time

        # Handle period crossing midnight
        if start > self.end_time:
            # Period crosses midnight (e.g., 22:00 to 06:00)
            return current_time_only >= start or current_time_only < self.end_time
        else:
            # Normal period (e.g., 00:00 to 06:00)
            return start <= current_time_only < self.end_time

    def get_adjusted_setpoint(
        self,
        base_setpoint: float,
        current_time: datetime,
        sunset_time: Optional[datetime] = None,
        force_recovery: bool = False
    ) -> float:
        """Get adjusted setpoint with night setback applied.

        Args:
            base_setpoint: Normal temperature setpoint
            current_time: Current datetime
            sunset_time: Sunset datetime (required if start_time is "sunset")
            force_recovery: Force recovery mode (ignore night period)

        Returns:
            Adjusted setpoint (lowered during night, normal otherwise)
        """
        # Check if recovery deadline is approaching
        if self.recovery_deadline and not force_recovery:
            current_time_only = current_time.time()
            # If we're within 2 hours of recovery deadline, restore setpoint
            from datetime import timedelta
            recovery_threshold = datetime.combine(current_time.date(), self.recovery_deadline)
            two_hours_before = recovery_threshold - timedelta(hours=2)

            if current_time >= two_hours_before:
                return base_setpoint

        if force_recovery:
            return base_setpoint

        # Apply night setback if in night period
        if self.is_night_period(current_time, sunset_time):
            return base_setpoint - self.setback_delta

        return base_setpoint

    def should_start_recovery(
        self,
        current_time: datetime,
        current_temp: float,
        base_setpoint: float
    ) -> bool:
        """Check if recovery heating should start.

        Recovery starts early if needed to reach setpoint by deadline.

        Args:
            current_time: Current datetime
            current_temp: Current temperature
            base_setpoint: Normal temperature setpoint

        Returns:
            True if recovery should start
        """
        if not self.recovery_deadline:
            return False

        current_time_only = current_time.time()

        # Calculate time until deadline
        recovery_dt = datetime.combine(current_time.date(), self.recovery_deadline)
        if recovery_dt < current_time:
            # Deadline is tomorrow
            from datetime import timedelta
            recovery_dt = recovery_dt + timedelta(days=1)

        time_until_deadline = (recovery_dt - current_time).total_seconds() / 3600  # hours

        # Calculate temperature deficit
        temp_deficit = base_setpoint - current_temp

        # Estimate recovery time needed (assuming ~2°C/hour heating rate)
        # This is a simplified estimate; real implementation would use learned heating rate
        estimated_recovery_hours = temp_deficit / 2.0

        # Start recovery if we don't have enough time
        return estimated_recovery_hours >= time_until_deadline


class NightSetbackManager:
    """Manages night setback for multiple zones."""

    def __init__(self):
        """Initialize night setback manager."""
        self._zone_setbacks: Dict[str, NightSetback] = {}

    def configure_zone(
        self,
        zone_id: str,
        start_time: str,
        end_time: str,
        setback_delta: float,
        recovery_deadline: Optional[str] = None
    ):
        """Configure night setback for a zone.

        Args:
            zone_id: Zone identifier
            start_time: Start time as "HH:MM" or "sunset" or "sunset+30"
            end_time: End time as "HH:MM"
            setback_delta: Temperature reduction during night (degrees)
            recovery_deadline: Optional override time "HH:MM" when temp must be restored
        """
        self._zone_setbacks[zone_id] = NightSetback(
            start_time=start_time,
            end_time=end_time,
            setback_delta=setback_delta,
            recovery_deadline=recovery_deadline
        )

    def get_adjusted_setpoint(
        self,
        zone_id: str,
        base_setpoint: float,
        current_time: datetime,
        sunset_time: Optional[datetime] = None
    ) -> float:
        """Get adjusted setpoint for a zone.

        Args:
            zone_id: Zone identifier
            base_setpoint: Normal temperature setpoint
            current_time: Current datetime
            sunset_time: Sunset datetime (required if zone uses sunset)

        Returns:
            Adjusted setpoint with night setback applied
        """
        if zone_id not in self._zone_setbacks:
            return base_setpoint

        setback = self._zone_setbacks[zone_id]
        return setback.get_adjusted_setpoint(base_setpoint, current_time, sunset_time)

    def is_zone_in_setback(
        self,
        zone_id: str,
        current_time: datetime,
        sunset_time: Optional[datetime] = None
    ) -> bool:
        """Check if a zone is currently in night setback.

        Args:
            zone_id: Zone identifier
            current_time: Current datetime
            sunset_time: Sunset datetime (required if zone uses sunset)

        Returns:
            True if zone is in night setback period
        """
        if zone_id not in self._zone_setbacks:
            return False

        setback = self._zone_setbacks[zone_id]
        return setback.is_night_period(current_time, sunset_time)

    def get_zone_config(self, zone_id: str) -> Optional[Dict[str, Any]]:
        """Get night setback configuration for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Configuration dictionary or None if not configured
        """
        if zone_id not in self._zone_setbacks:
            return None

        setback = self._zone_setbacks[zone_id]
        return {
            "start_time": setback.start_time_str,
            "end_time": setback.end_time.strftime("%H:%M"),
            "setback_delta": setback.setback_delta,
            "recovery_deadline": setback.recovery_deadline.strftime("%H:%M") if setback.recovery_deadline else None,
            "use_sunset": setback.use_sunset,
            "sunset_offset_minutes": setback.sunset_offset_minutes
        }
