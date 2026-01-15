"""Night setback module for adaptive thermostat.

Implements energy-saving night setback with configurable temperature delta,
sunset support, and recovery deadline overrides.
"""
from datetime import datetime, time
from typing import Optional, Dict, Any
import logging

_LOGGER = logging.getLogger(__name__)


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
        sunset_offset_minutes: int = 0,
        thermal_rate_learner: Optional['ThermalRateLearner'] = None,
        heating_type: Optional[str] = None,
    ):
        """Initialize night setback.

        Args:
            start_time: Start time as "HH:MM" or "sunset" or "sunset+2" (hours) or "sunset+30m"
            end_time: End time as "HH:MM"
            setback_delta: Temperature reduction during night (degrees)
            recovery_deadline: Optional override time "HH:MM" when temp must be restored
            sunset_offset_minutes: Minutes to add/subtract from sunset (e.g., +30, -15)
            thermal_rate_learner: Optional ThermalRateLearner for learned heating rate
            heating_type: Heating type for fallback estimates (floor_hydronic, radiator, convector, forced_air)
        """
        self.start_time_str = start_time
        self.end_time = self._parse_time(end_time)
        self.setback_delta = setback_delta
        self.recovery_deadline = self._parse_time(recovery_deadline) if recovery_deadline else None
        self.sunset_offset_minutes = sunset_offset_minutes
        self.thermal_rate_learner = thermal_rate_learner
        self.heating_type = heating_type

        # Parse start time (may be "sunset" or "HH:MM")
        if start_time.lower().startswith("sunset"):
            self.use_sunset = True
            # Parse sunset offset from string like "sunset+2" (hours) or "sunset+30m"
            if "+" in start_time:
                self.sunset_offset_minutes = self._parse_sunset_offset(start_time.split("+")[1])
            elif "-" in start_time:
                self.sunset_offset_minutes = -self._parse_sunset_offset(start_time.split("-")[1])
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

    def _parse_sunset_offset(self, offset_str: str) -> int:
        """Parse sunset offset string to minutes.

        Supports: +2 (hours if <=12), +2h (hours), +30m (minutes), +120 (minutes if >12)

        Args:
            offset_str: Offset string like "2", "2h", "30m", "120"

        Returns:
            Offset in minutes
        """
        offset_str = offset_str.strip()
        if offset_str.endswith('m'):
            return int(offset_str[:-1])
        elif offset_str.endswith('h'):
            return int(offset_str[:-1]) * 60
        else:
            # Default: interpret as hours for values <= 12, minutes otherwise
            value = int(offset_str)
            if value <= 12:
                return value * 60  # hours
            return value  # minutes (backward compat for sunset+30, sunset+120)

    def _get_heating_rate(self) -> float:
        """Get heating rate with fallback hierarchy.

        Fallback order:
        1. Learned rate from ThermalRateLearner
        2. Heating type estimate (floor=0.5, radiator=1.2, convector=2.0, forced_air=4.0)
        3. Default 1.0°C/h

        Returns:
            Heating rate in °C/hour
        """
        # Fallback 1: Try learned rate
        if self.thermal_rate_learner is not None:
            learned_rate = self.thermal_rate_learner.get_average_heating_rate()
            if learned_rate is not None:
                _LOGGER.debug(f"Using learned heating rate: {learned_rate:.2f}°C/h")
                return learned_rate

        # Fallback 2: Heating type estimates
        heating_type_rates = {
            "floor_hydronic": 0.5,  # Slow heating
            "radiator": 1.2,         # Moderate heating
            "convector": 2.0,        # Fast heating
            "forced_air": 4.0,       # Very fast heating
        }

        if self.heating_type in heating_type_rates:
            rate = heating_type_rates[self.heating_type]
            _LOGGER.debug(f"Using heating type estimate for {self.heating_type}: {rate:.2f}°C/h")
            return rate

        # Fallback 3: Default rate
        _LOGGER.debug("Using default heating rate: 1.0°C/h")
        return 1.0

    def _get_cold_soak_margin(self) -> float:
        """Get cold-soak margin multiplier based on heating type.

        Cold-soak margin accounts for extra time needed when building is cold.
        Higher thermal mass systems need more margin.

        Returns:
            Margin multiplier (e.g., 1.5 = 50% extra time)
        """
        # Cold-soak margins by heating type
        margins = {
            "floor_hydronic": 1.5,  # 50% margin - high thermal mass
            "radiator": 1.3,         # 30% margin - moderate thermal mass
            "convector": 1.2,        # 20% margin - low thermal mass
            "forced_air": 1.1,       # 10% margin - very low thermal mass
        }

        if self.heating_type in margins:
            return margins[self.heating_type]

        # Default margin for unknown heating types
        return 1.25  # 25% margin

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
        Uses learned heating rate with fallback to heating type estimates.

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

        # Get heating rate with fallback hierarchy
        heating_rate = self._get_heating_rate()

        # Get cold-soak margin
        cold_soak_margin = self._get_cold_soak_margin()

        # Estimate recovery time needed with cold-soak margin
        estimated_recovery_hours = (temp_deficit / heating_rate) * cold_soak_margin

        _LOGGER.debug(
            f"Night setback recovery calculation: "
            f"temp_deficit={temp_deficit:.2f}°C, "
            f"heating_rate={heating_rate:.2f}°C/h, "
            f"cold_soak_margin={cold_soak_margin:.2f}x, "
            f"estimated_recovery={estimated_recovery_hours:.2f}h, "
            f"time_until_deadline={time_until_deadline:.2f}h"
        )

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
