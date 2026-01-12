"""Tests for night setback module."""
import pytest
from datetime import datetime, time, timedelta
from custom_components.adaptive_thermostat.adaptive.night_setback import (
    NightSetback,
    NightSetbackManager
)


class TestNightSetback:
    """Test NightSetback class."""

    def test_night_period_detection_basic(self):
        """Test basic night period detection with fixed times."""
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0
        )

        # During night (23:00)
        current = datetime(2024, 1, 15, 23, 0)
        assert setback.is_night_period(current) is True

        # During night (02:00)
        current = datetime(2024, 1, 15, 2, 0)
        assert setback.is_night_period(current) is True

        # Not night (10:00)
        current = datetime(2024, 1, 15, 10, 0)
        assert setback.is_night_period(current) is False

        # Edge case: exactly at start time
        current = datetime(2024, 1, 15, 22, 0)
        assert setback.is_night_period(current) is True

        # Edge case: exactly at end time
        current = datetime(2024, 1, 15, 6, 0)
        assert setback.is_night_period(current) is False

    def test_setpoint_lowering_during_night(self):
        """Test setpoint is lowered by delta during night period."""
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.5
        )

        base_setpoint = 20.0

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current)
        assert adjusted == 17.5  # 20.0 - 2.5

        # During day
        current = datetime(2024, 1, 15, 10, 0)
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current)
        assert adjusted == 20.0  # No change

    def test_sunset_as_start_time(self):
        """Test using sunset as start time."""
        setback = NightSetback(
            start_time="sunset",
            end_time="06:00",
            setback_delta=2.0
        )

        # Sunset at 18:30
        sunset = datetime(2024, 1, 15, 18, 30)

        # 19:00 (after sunset)
        current = datetime(2024, 1, 15, 19, 0)
        assert setback.is_night_period(current, sunset) is True

        # 18:00 (before sunset)
        current = datetime(2024, 1, 15, 18, 0)
        assert setback.is_night_period(current, sunset) is False

        # 02:00 (night)
        current = datetime(2024, 1, 15, 2, 0)
        assert setback.is_night_period(current, sunset) is True

    def test_sunset_with_offset(self):
        """Test sunset with positive and negative offsets."""
        # Sunset + 30 minutes
        setback = NightSetback(
            start_time="sunset+30",
            end_time="06:00",
            setback_delta=2.0
        )

        sunset = datetime(2024, 1, 15, 18, 30)

        # 18:45 (15 minutes after sunset, but before sunset+30)
        current = datetime(2024, 1, 15, 18, 45)
        assert setback.is_night_period(current, sunset) is False

        # 19:05 (35 minutes after sunset, after sunset+30)
        current = datetime(2024, 1, 15, 19, 5)
        assert setback.is_night_period(current, sunset) is True

        # Sunset - 15 minutes
        setback = NightSetback(
            start_time="sunset-15",
            end_time="06:00",
            setback_delta=2.0
        )

        # 18:20 (10 minutes before sunset, but after sunset-15)
        current = datetime(2024, 1, 15, 18, 20)
        assert setback.is_night_period(current, sunset) is True

    def test_recovery_deadline_override(self):
        """Test recovery deadline forces setpoint restoration."""
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="06:00"
        )

        base_setpoint = 20.0

        # During night, but within 2 hours of recovery deadline
        # (04:30 is 1.5 hours before 06:00)
        current = datetime(2024, 1, 15, 4, 30)
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current)
        assert adjusted == 20.0  # Recovery mode

        # During night, more than 2 hours before recovery deadline
        # (02:00 is 4 hours before 06:00)
        current = datetime(2024, 1, 15, 2, 0)
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current)
        assert adjusted == 18.0  # Still in setback

    def test_force_recovery_parameter(self):
        """Test force_recovery parameter overrides night setback."""
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0
        )

        base_setpoint = 20.0
        current = datetime(2024, 1, 15, 23, 0)  # During night

        # Normal operation
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current)
        assert adjusted == 18.0

        # Force recovery
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current, force_recovery=True)
        assert adjusted == 20.0

    def test_should_start_recovery(self):
        """Test recovery start detection based on temperature deficit."""
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="06:00"
        )

        base_setpoint = 20.0

        # Large deficit, close to deadline (need to start recovery)
        current = datetime(2024, 1, 15, 4, 0)  # 2 hours before deadline
        current_temp = 16.0  # 4°C below setpoint
        assert setback.should_start_recovery(current, current_temp, base_setpoint) is True

        # Small deficit, plenty of time (no recovery needed)
        current = datetime(2024, 1, 15, 2, 0)  # 4 hours before deadline
        current_temp = 19.0  # 1°C below setpoint
        assert setback.should_start_recovery(current, current_temp, base_setpoint) is False

    def test_night_period_not_crossing_midnight(self):
        """Test night period that doesn't cross midnight."""
        setback = NightSetback(
            start_time="01:00",
            end_time="06:00",
            setback_delta=2.0
        )

        # During night (03:00)
        current = datetime(2024, 1, 15, 3, 0)
        assert setback.is_night_period(current) is True

        # Before night (00:30)
        current = datetime(2024, 1, 15, 0, 30)
        assert setback.is_night_period(current) is False

        # After night (23:00)
        current = datetime(2024, 1, 15, 23, 0)
        assert setback.is_night_period(current) is False


class TestNightSetbackManager:
    """Test NightSetbackManager class."""

    def test_configure_zone(self):
        """Test configuring night setback for a zone."""
        manager = NightSetbackManager()

        manager.configure_zone(
            zone_id="bedroom",
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.5,
            recovery_deadline="06:00"
        )

        config = manager.get_zone_config("bedroom")
        assert config is not None
        assert config["start_time"] == "22:00"
        assert config["end_time"] == "06:00"
        assert config["setback_delta"] == 2.5
        assert config["recovery_deadline"] == "06:00"
        assert config["use_sunset"] is False

    def test_get_adjusted_setpoint_for_zone(self):
        """Test getting adjusted setpoint for configured zone."""
        manager = NightSetbackManager()

        manager.configure_zone(
            zone_id="bedroom",
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0
        )

        base_setpoint = 20.0

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        adjusted = manager.get_adjusted_setpoint("bedroom", base_setpoint, current)
        assert adjusted == 18.0

        # During day
        current = datetime(2024, 1, 15, 10, 0)
        adjusted = manager.get_adjusted_setpoint("bedroom", base_setpoint, current)
        assert adjusted == 20.0

    def test_unconfigured_zone_returns_base_setpoint(self):
        """Test unconfigured zone returns base setpoint unchanged."""
        manager = NightSetbackManager()

        base_setpoint = 20.0
        current = datetime(2024, 1, 15, 23, 0)

        adjusted = manager.get_adjusted_setpoint("kitchen", base_setpoint, current)
        assert adjusted == 20.0  # No change

    def test_is_zone_in_setback(self):
        """Test checking if zone is in setback period."""
        manager = NightSetbackManager()

        manager.configure_zone(
            zone_id="bedroom",
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0
        )

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        assert manager.is_zone_in_setback("bedroom", current) is True

        # During day
        current = datetime(2024, 1, 15, 10, 0)
        assert manager.is_zone_in_setback("bedroom", current) is False

        # Unconfigured zone
        assert manager.is_zone_in_setback("kitchen", current) is False

    def test_multiple_zones_with_different_schedules(self):
        """Test multiple zones with different setback schedules."""
        manager = NightSetbackManager()

        # Bedroom: 22:00 - 06:00, 2.0°C setback
        manager.configure_zone(
            zone_id="bedroom",
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0
        )

        # Living room: sunset - 23:00, 1.5°C setback
        manager.configure_zone(
            zone_id="living_room",
            start_time="sunset",
            end_time="23:00",
            setback_delta=1.5
        )

        base_setpoint = 20.0
        current = datetime(2024, 1, 15, 22, 30)
        sunset = datetime(2024, 1, 15, 18, 30)

        # Bedroom is in setback
        adjusted_bedroom = manager.get_adjusted_setpoint("bedroom", base_setpoint, current)
        assert adjusted_bedroom == 18.0

        # Living room is in setback
        adjusted_living = manager.get_adjusted_setpoint("living_room", base_setpoint, current, sunset)
        assert adjusted_living == 18.5

    def test_sunset_configuration_in_manager(self):
        """Test sunset-based configuration in manager."""
        manager = NightSetbackManager()

        manager.configure_zone(
            zone_id="living_room",
            start_time="sunset+30",
            end_time="23:00",
            setback_delta=1.5
        )

        config = manager.get_zone_config("living_room")
        assert config["use_sunset"] is True
        assert config["sunset_offset_minutes"] == 30
        assert config["start_time"] == "sunset+30"
