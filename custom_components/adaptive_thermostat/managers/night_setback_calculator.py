"""Night setback calculator for Adaptive Thermostat integration.

This module provides pure calculation logic for night setback functionality,
separated from the state management concerns handled by NightSetbackManager.
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant
    from homeassistant.util import dt as dt_util
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any
    dt_util = None

from ..const import DOMAIN
from ..adaptive.night_setback import NightSetback

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class NightSetbackCalculator:
    """Calculator for night setback temperature adjustments.

    Provides pure calculation logic for determining effective setpoint temperatures
    based on night setback configuration, sunrise/sunset times, weather conditions,
    and solar recovery logic. This class is stateless and handles only calculations.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        night_setback: Optional[NightSetback],
        night_setback_config: Optional[Dict[str, Any]],
        window_orientation: Optional[str],
        get_target_temp: Callable[[], Optional[float]],
        get_current_temp: Callable[[], Optional[float]],
        preheat_learner: Optional[Any] = None,
        preheat_enabled: bool = False,
    ):
        """Initialize the NightSetbackCalculator.

        Args:
            hass: Home Assistant instance
            entity_id: Entity ID of the thermostat (for logging)
            night_setback: NightSetback instance (for static end time mode)
            night_setback_config: Night setback configuration dict (for dynamic mode)
            window_orientation: Window orientation for solar calculations
            get_target_temp: Callback to get current target temperature
            get_current_temp: Callback to get current temperature
            preheat_learner: Optional PreheatLearner instance for time estimation
            preheat_enabled: Whether preheat functionality is enabled
        """
        self._hass = hass
        self._entity_id = entity_id
        self._night_setback = night_setback
        self._night_setback_config = night_setback_config
        self._window_orientation = window_orientation
        self._get_target_temp = get_target_temp
        self._get_current_temp = get_current_temp
        self._preheat_learner = preheat_learner
        self._preheat_enabled = preheat_enabled

    @property
    def is_configured(self) -> bool:
        """Return True if night setback is configured."""
        return self._night_setback is not None or self._night_setback_config is not None

    def get_sunset_time(self) -> Optional[datetime]:
        """Get sunset time from Home Assistant sun component (local time)."""
        sun_state = self._hass.states.get("sun.sun")
        if sun_state and sun_state.attributes.get("next_setting"):
            try:
                utc_sunset = datetime.fromisoformat(
                    sun_state.attributes["next_setting"].replace("Z", "+00:00")
                )
                if dt_util:
                    return dt_util.as_local(utc_sunset)
                return utc_sunset
            except (ValueError, TypeError):
                return None
        return None

    def get_sunrise_time(self) -> Optional[datetime]:
        """Get sunrise time from Home Assistant sun component (local time)."""
        sun_state = self._hass.states.get("sun.sun")
        if sun_state and sun_state.attributes.get("next_rising"):
            try:
                utc_sunrise = datetime.fromisoformat(
                    sun_state.attributes["next_rising"].replace("Z", "+00:00")
                )
                if dt_util:
                    return dt_util.as_local(utc_sunrise)
                return utc_sunrise
            except (ValueError, TypeError):
                return None
        return None

    def get_weather_condition(self) -> Optional[str]:
        """Get current weather condition from coordinator's weather entity."""
        coordinator = self._hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator and hasattr(coordinator, '_weather_entity'):
            weather_state = self._hass.states.get(coordinator._weather_entity)
            if weather_state:
                return weather_state.state
        # Try common weather entity names as fallback
        for entity_id in ["weather.home", "weather.knmi_home", "weather.forecast_home"]:
            weather_state = self._hass.states.get(entity_id)
            if weather_state:
                return weather_state.state
        return None

    def calculate_dynamic_night_end(self) -> Optional[dt_time]:
        """Calculate dynamic night setback end time based on sunrise, orientation, weather.

        Returns time object for when night setback should end, or None if cannot calculate.

        Logic:
        - Base: sunrise + 60 min (sun needs time to rise high enough to heat windows)
        - Orientation: adjusts based on when direct sun reaches windows
        - Weather: cloudy = need active heating sooner, clear = can wait for sun
        """
        sunrise = self.get_sunrise_time()
        if not sunrise:
            return None

        # Base: sunrise + 60 min (sun needs to rise high enough to provide heat)
        end_time = sunrise + timedelta(minutes=60)

        # Orientation offsets - when does direct sun actually hit these windows?
        # South: sun hits when higher in sky, can rely on solar gain longer
        # East: gets early morning sun, moderate delay
        # West: no morning sun at all, need active heating
        # North: minimal direct sun ever, need active heating
        orientation_offsets = {
            "south": +30,   # Wait longer - sun will heat this room well once high enough
            "east": +15,    # Gets morning sun fairly soon
            "west": -30,    # No morning sun - start heating earlier
            "north": -45,   # No direct sun - need heating earliest
        }

        if self._window_orientation:
            offset = orientation_offsets.get(self._window_orientation.lower(), 0)
            end_time = end_time + timedelta(minutes=offset)

        # Weather adjustment
        weather = self.get_weather_condition()
        if weather:
            weather_lower = weather.lower().replace("-", "").replace("_", "")
            if any(c in weather_lower for c in ["cloud", "rain", "snow", "fog", "hail", "storm"]):
                # Cloudy: no solar gain expected - end setback earlier to allow heating
                end_time = end_time - timedelta(minutes=30)
            elif any(c in weather_lower for c in ["sunny", "clear"]):
                # Clear: good solar gain - can delay heating longer
                end_time = end_time + timedelta(minutes=15)

        return end_time.time()

    def parse_sunset_offset(self, offset_str: str) -> int:
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

    def parse_night_start_time(self, start_str: str, current_time: datetime) -> dt_time:
        """Parse night setback start time string.

        Args:
            start_str: Start time as "HH:MM" or "sunset" or "sunset+2" (hours) or "sunset+30m"
            current_time: Current datetime for sunset lookup

        Returns:
            time object for the start time
        """
        if start_str.lower().startswith("sunset"):
            sunset = self.get_sunset_time()
            if sunset:
                offset = 0
                if "+" in start_str:
                    offset = self.parse_sunset_offset(start_str.split("+")[1])
                elif "-" in start_str:
                    offset = -self.parse_sunset_offset(start_str.split("-")[1])
                return (sunset + timedelta(minutes=offset)).time()
            else:
                return dt_time(21, 0)  # Fallback to 21:00
        else:
            hour, minute = map(int, start_str.split(":"))
            return dt_time(hour, minute)

    def is_in_night_time_period(
        self,
        current_time_only: dt_time,
        start_time: dt_time,
        end_time: dt_time
    ) -> bool:
        """Check if current time is within night period, handling midnight crossing.

        Args:
            current_time_only: time object for current time
            start_time: time object for period start
            end_time: time object for period end

        Returns:
            True if in night period
        """
        if start_time > end_time:
            # Period crosses midnight (e.g., 22:00 to 06:00)
            return current_time_only >= start_time or current_time_only < end_time
        else:
            # Normal period (e.g., 00:00 to 06:00)
            return start_time <= current_time_only < end_time

    def calculate_night_setback_adjustment(
        self,
        current_time: Optional[datetime] = None
    ) -> Tuple[float, bool, Dict[str, Any]]:
        """Calculate night setback adjustment for effective target temperature.

        Handles both static end time (NightSetback object) and dynamic end time
        (sunrise/orientation/weather-based) configurations.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            A tuple of (effective_target, in_night_period, night_setback_info) where:
            - effective_target: The adjusted target temperature
            - in_night_period: Whether we are currently in the night setback period
            - night_setback_info: Dict with additional info for state attributes
        """
        if current_time is None:
            current_time = dt_util.utcnow()

        target_temp = self._get_target_temp()
        current_temp = self._get_current_temp()
        effective_target = target_temp
        in_night_period = False
        info: Dict[str, Any] = {}

        if self._night_setback:
            # Static end time mode - use NightSetback object
            sunset_time = self.get_sunset_time() if self._night_setback.use_sunset else None
            in_night_period = self._night_setback.is_night_period(current_time, sunset_time)

            info["night_setback_delta"] = self._night_setback.setback_delta
            info["night_setback_end"] = self._night_setback.end_time.strftime("%H:%M")
            info["night_setback_end_dynamic"] = False

            if in_night_period:
                effective_target = target_temp - self._night_setback.setback_delta

        elif self._night_setback_config:
            # Dynamic end time mode - calculate based on sunrise, orientation, weather
            current_time_only = current_time.time()

            # Parse start time
            start_time = self.parse_night_start_time(
                self._night_setback_config['start'], current_time
            )

            # Calculate dynamic end time
            end_time = self.calculate_dynamic_night_end()
            if not end_time:
                # Fallback: use recovery_deadline or default 07:00
                deadline = self._night_setback_config.get('recovery_deadline')
                if deadline:
                    hour, minute = map(int, deadline.split(":"))
                    end_time = dt_time(hour, minute)
                else:
                    end_time = dt_time(7, 0)
            else:
                # If recovery_deadline is set and earlier than dynamic end, use it
                deadline_str = self._night_setback_config.get('recovery_deadline')
                if deadline_str:
                    hour, minute = map(int, deadline_str.split(":"))
                    deadline_time = dt_time(hour, minute)
                    if deadline_time < end_time:
                        end_time = deadline_time
                        _LOGGER.debug(
                            "%s: Using recovery_deadline %s (earlier than dynamic end time)",
                            self._entity_id, deadline_str
                        )

            # Check if in night period
            in_night_period = self.is_in_night_time_period(
                current_time_only, start_time, end_time
            )

            info["night_setback_delta"] = self._night_setback_config['delta']
            info["night_setback_end"] = end_time.strftime("%H:%M")
            info["night_setback_end_dynamic"] = True

            # Include weather for debugging
            weather = self.get_weather_condition()
            if weather:
                info["weather_condition"] = weather

            _LOGGER.debug(
                "%s: Night setback check: current=%s, start=%s, end=%s, in_night=%s, target=%s, delta=%s",
                self._entity_id, current_time_only, start_time, end_time, in_night_period,
                target_temp, self._night_setback_config['delta']
            )

            if in_night_period:
                effective_target = target_temp - self._night_setback_config['delta']
                _LOGGER.info("%s: Night setback active, effective_target=%s", self._entity_id, effective_target)

        info["night_setback_active"] = in_night_period

        return effective_target, in_night_period, info

    def calculate_preheat_start(
        self,
        deadline: datetime,
        current_temp: float,
        target_temp: float,
        outdoor_temp: float,
        humidity_paused: bool = False,
    ) -> Optional[datetime]:
        """Calculate when to start preheating before recovery deadline.

        Args:
            deadline: Recovery deadline datetime
            current_temp: Current temperature in C
            target_temp: Target temperature in C
            outdoor_temp: Outdoor temperature in C
            humidity_paused: Whether heating is paused due to humidity spike

        Returns:
            Datetime when preheat should start, or None if preheat is disabled/not configured
        """
        # Block preheat if humidity spike detected
        if humidity_paused:
            return None

        # Check if preheat is enabled and configured
        if not self._preheat_enabled:
            return None

        if not self._night_setback_config or "recovery_deadline" not in self._night_setback_config:
            return None

        if not self._preheat_learner:
            return None

        # If already at or above target, no preheat needed
        if current_temp >= target_temp:
            return deadline

        # Get estimated time from PreheatLearner
        estimated_minutes = self._preheat_learner.estimate_time_to_target(
            current_temp, target_temp, outdoor_temp
        )

        # Add 10% buffer (minimum 15 minutes)
        buffer_minutes = max(estimated_minutes * 0.1, 15.0)
        total_minutes = estimated_minutes + buffer_minutes

        # Clamp to max_preheat_hours
        max_minutes = self._preheat_learner.max_hours * 60.0
        total_minutes = min(total_minutes, max_minutes)

        # Calculate start time
        preheat_start = deadline - timedelta(minutes=total_minutes)

        return preheat_start

    def get_preheat_info(
        self,
        now: datetime,
        current_temp: float,
        target_temp: float,
        outdoor_temp: float,
        deadline: datetime,
        humidity_paused: bool = False,
    ) -> Dict[str, Any]:
        """Get preheat information for state attributes.

        Args:
            now: Current datetime
            current_temp: Current temperature in C
            target_temp: Target temperature in C
            outdoor_temp: Outdoor temperature in C
            deadline: Recovery deadline datetime
            humidity_paused: Whether heating is paused due to humidity spike

        Returns:
            Dict with scheduled_start, estimated_duration, and active status
        """
        info = {
            "scheduled_start": None,
            "estimated_duration": 0,
            "active": False,
        }

        if not self._preheat_enabled or not self._preheat_learner:
            return info

        # Calculate scheduled start
        scheduled_start = self.calculate_preheat_start(
            deadline, current_temp, target_temp, outdoor_temp, humidity_paused
        )

        if scheduled_start is None:
            return info

        # Get estimated duration
        estimated_minutes = self._preheat_learner.estimate_time_to_target(
            current_temp, target_temp, outdoor_temp
        )

        info["scheduled_start"] = scheduled_start
        info["estimated_duration"] = estimated_minutes
        info["active"] = now >= scheduled_start

        return info
