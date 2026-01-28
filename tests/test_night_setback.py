"""Tests for night setback module."""
import pytest
from datetime import datetime, time, timedelta
from unittest.mock import Mock
from custom_components.adaptive_thermostat.adaptive.night_setback import (
    NightSetback,
    NightSetbackManager
)
from custom_components.adaptive_thermostat.adaptive.thermal_rates import ThermalRateLearner


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


class TestNightSetbackLearnedRate:
    """Test night setback with learned heating rates."""

    def test_night_setback_learned_rate(self):
        """Test recovery timing with learned heating rate."""
        # Create thermal rate learner with learned rate
        learner = ThermalRateLearner()
        learner.add_heating_measurement(1.5)  # Learned 1.5°C/h
        learner.add_heating_measurement(1.6)
        learner.add_heating_measurement(1.4)

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=3.0,
            recovery_deadline="07:00",
            thermal_rate_learner=learner,
            heating_type="radiator"
        )

        # Current: 04:00, 3 hours until deadline
        # Temp deficit: 5°C (15°C current, 20°C target)
        # Learned rate: 1.5°C/h
        # Cold-soak margin: 1.3x for radiator
        # Estimated recovery: (5 / 1.5) * 1.3 = 4.33 hours
        # Should start recovery since 4.33h > 3h

        current = datetime(2024, 1, 15, 4, 0)
        base_setpoint = 20.0
        current_temp = 15.0

        should_recover = setback.should_start_recovery(current, current_temp, base_setpoint)
        assert should_recover is True

    def test_night_setback_fallback_heating_type(self):
        """Test fallback to heating type estimate when no learned rate."""
        # No thermal rate learner provided
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=3.0,
            recovery_deadline="07:00",
            thermal_rate_learner=None,
            heating_type="forced_air"
        )

        # Current: 05:00, 2 hours until deadline
        # Temp deficit: 4°C (16°C current, 20°C target)
        # Forced air estimate: 4.0°C/h
        # Cold-soak margin: 1.1x for forced_air
        # Estimated recovery: (4 / 4.0) * 1.1 = 1.1 hours
        # Should NOT start recovery since 1.1h < 2h

        current = datetime(2024, 1, 15, 5, 0)
        base_setpoint = 20.0
        current_temp = 16.0

        should_recover = setback.should_start_recovery(current, current_temp, base_setpoint)
        assert should_recover is False

    def test_night_setback_fallback_hierarchy(self):
        """Test complete fallback hierarchy: learned → type → default."""
        # Test 1: Learned rate (highest priority)
        learner_with_data = ThermalRateLearner()
        learner_with_data.add_heating_measurement(2.5)

        setback1 = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            thermal_rate_learner=learner_with_data,
            heating_type="floor_hydronic"
        )

        # Learned rate should be used (2.5°C/h), not floor_hydronic (0.5°C/h)
        rate1 = setback1._get_heating_rate()
        assert rate1 == 2.5

        # Test 2: Heating type estimate (second priority)
        learner_no_data = ThermalRateLearner()  # No measurements

        setback2 = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            thermal_rate_learner=learner_no_data,
            heating_type="convector"
        )

        # Should use convector estimate (2.0°C/h)
        rate2 = setback2._get_heating_rate()
        assert rate2 == 2.0

        # Test 3: Default rate (lowest priority)
        setback3 = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            thermal_rate_learner=None,
            heating_type=None
        )

        # Should use default (1.0°C/h)
        rate3 = setback3._get_heating_rate()
        assert rate3 == 1.0

    def test_night_setback_floor_hydronic_slow_recovery(self):
        """Test floor hydronic with slow learned rate and high margin."""
        learner = ThermalRateLearner()
        learner.add_heating_measurement(0.6)  # Slow learned rate
        learner.add_heating_measurement(0.5)
        learner.add_heating_measurement(0.7)

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=3.0,
            recovery_deadline="07:00",
            thermal_rate_learner=learner,
            heating_type="floor_hydronic"
        )

        # Current: 02:00, 5 hours until deadline
        # Temp deficit: 6°C (14°C current, 20°C target)
        # Learned rate: 0.6°C/h (median)
        # Cold-soak margin: 1.5x for floor_hydronic
        # Estimated recovery: (6 / 0.6) * 1.5 = 15 hours
        # Should start recovery since 15h > 5h

        current = datetime(2024, 1, 15, 2, 0)
        base_setpoint = 20.0
        current_temp = 14.0

        should_recover = setback.should_start_recovery(current, current_temp, base_setpoint)
        assert should_recover is True

    def test_night_setback_forced_air_fast_recovery(self):
        """Test forced air with fast learned rate and low margin."""
        learner = ThermalRateLearner()
        learner.add_heating_measurement(3.8)  # Fast learned rate
        learner.add_heating_measurement(4.2)
        learner.add_heating_measurement(4.0)

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            thermal_rate_learner=learner,
            heating_type="forced_air"
        )

        # Current: 05:30, 1.5 hours until deadline
        # Temp deficit: 3°C (17°C current, 20°C target)
        # Learned rate: 4.0°C/h (median)
        # Cold-soak margin: 1.1x for forced_air
        # Estimated recovery: (3 / 4.0) * 1.1 = 0.825 hours
        # Should NOT start recovery since 0.825h < 1.5h

        current = datetime(2024, 1, 15, 5, 30)
        base_setpoint = 20.0
        current_temp = 17.0

        should_recover = setback.should_start_recovery(current, current_temp, base_setpoint)
        assert should_recover is False

    def test_cold_soak_margins_by_heating_type(self):
        """Test cold-soak margins are correctly applied by heating type."""
        # Floor hydronic: 50% margin
        setback_floor = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="floor_hydronic"
        )
        assert setback_floor._get_cold_soak_margin() == 1.5

        # Radiator: 30% margin
        setback_radiator = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="radiator"
        )
        assert setback_radiator._get_cold_soak_margin() == 1.3

        # Convector: 20% margin
        setback_convector = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="convector"
        )
        assert setback_convector._get_cold_soak_margin() == 1.2

        # Forced air: 10% margin
        setback_forced = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="forced_air"
        )
        assert setback_forced._get_cold_soak_margin() == 1.1

        # Unknown: 25% margin (default)
        setback_unknown = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="unknown_type"
        )
        assert setback_unknown._get_cold_soak_margin() == 1.25

    def test_heating_type_rate_estimates(self):
        """Test heating type rate estimates are correct."""
        # Floor hydronic: 0.5°C/h
        setback_floor = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="floor_hydronic"
        )
        assert setback_floor._get_heating_rate() == 0.5

        # Radiator: 1.2°C/h
        setback_radiator = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="radiator"
        )
        assert setback_radiator._get_heating_rate() == 1.2

        # Convector: 2.0°C/h
        setback_convector = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="convector"
        )
        assert setback_convector._get_heating_rate() == 2.0

        # Forced air: 4.0°C/h
        setback_forced = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            heating_type="forced_air"
        )
        assert setback_forced._get_heating_rate() == 4.0

    def test_night_setback_no_recovery_deadline(self):
        """Test that without recovery deadline, learned rate is not used."""
        learner = ThermalRateLearner()
        learner.add_heating_measurement(2.0)

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline=None,  # No deadline
            thermal_rate_learner=learner,
            heating_type="radiator"
        )

        current = datetime(2024, 1, 15, 4, 0)
        base_setpoint = 20.0
        current_temp = 15.0

        # Should always return False when no recovery deadline
        should_recover = setback.should_start_recovery(current, current_temp, base_setpoint)
        assert should_recover is False


def test_night_setback_learned_rate_module_exists():
    """Marker test to verify night setback learned rate module exists."""
    from custom_components.adaptive_thermostat.adaptive.night_setback import NightSetback
    from custom_components.adaptive_thermostat.adaptive.thermal_rates import ThermalRateLearner

    # Verify new parameters exist
    learner = ThermalRateLearner()
    setback = NightSetback(
        start_time="22:00",
        end_time="06:00",
        setback_delta=2.0,
        thermal_rate_learner=learner,
        heating_type="radiator"
    )

    assert hasattr(setback, 'thermal_rate_learner')
    assert hasattr(setback, 'heating_type')
    assert hasattr(setback, '_get_heating_rate')
    assert hasattr(setback, '_get_cold_soak_margin')


class TestNightSetbackTimezone:
    """Test NightSetback with timezone-aware datetimes."""

    def test_local_time_used_not_utc(self):
        """Test that local time is used for period checks, not UTC.

        Scenario: Local time is 10:00 AM (past the 08:57 end time)
                  UTC time is 08:00 AM (before the 08:57 end time)
        Result: Night setback should NOT be active (local time is used correctly)
        """
        from zoneinfo import ZoneInfo

        setback = NightSetback(
            start_time="22:00",
            end_time="08:57",
            setback_delta=2.0
        )

        # Create timezone-aware datetime
        # Amsterdam timezone (UTC+1 or UTC+2 depending on DST)
        # Using winter time: UTC+1
        tz = ZoneInfo("Europe/Amsterdam")

        # Local time: 10:00 AM (past end time 08:57)
        # UTC time: 09:00 AM (also past 08:57, but that's not the point)
        # The bug would manifest if we were comparing UTC time against local config
        local_time = datetime(2024, 1, 15, 10, 0, tzinfo=tz)

        # Night setback should NOT be active (local time 10:00 > end time 08:57)
        assert setback.is_night_period(local_time) is False

    def test_utc_vs_local_edge_case(self):
        """Test edge case where UTC and local times span the end boundary.

        Scenario: Local time is 09:30 AM (past the 09:00 end time)
                  UTC time is 08:30 AM (before the 09:00 end time)
        Result: Night setback should NOT be active
        """
        from zoneinfo import ZoneInfo

        setback = NightSetback(
            start_time="23:00",
            end_time="09:00",
            setback_delta=3.0
        )

        # New York timezone (UTC-5)
        tz = ZoneInfo("America/New_York")

        # Local time: 09:30 AM EST (past end time 09:00)
        # UTC time would be: 14:30 (2:30 PM)
        local_time = datetime(2024, 1, 15, 9, 30, tzinfo=tz)

        # Night setback should NOT be active
        assert setback.is_night_period(local_time) is False

        # Local time: 08:45 AM EST (before end time 09:00)
        # UTC time would be: 13:45 (1:45 PM)
        local_time = datetime(2024, 1, 15, 8, 45, tzinfo=tz)

        # Night setback SHOULD be active
        assert setback.is_night_period(local_time) is True

    def test_timezone_aware_during_night(self):
        """Test timezone-aware datetime during night period."""
        from zoneinfo import ZoneInfo

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0
        )

        tz = ZoneInfo("Europe/Amsterdam")

        # Local time: 23:00 (11 PM) - clearly during night
        local_time = datetime(2024, 1, 15, 23, 0, tzinfo=tz)
        assert setback.is_night_period(local_time) is True

        # Local time: 02:00 (2 AM) - during night (crosses midnight)
        local_time = datetime(2024, 1, 15, 2, 0, tzinfo=tz)
        assert setback.is_night_period(local_time) is True

    def test_multiple_timezones(self):
        """Test that the same config works correctly across different timezones."""
        from zoneinfo import ZoneInfo

        setback = NightSetback(
            start_time="22:00",
            end_time="07:00",
            setback_delta=2.0
        )

        # Test with multiple timezones - all at local 10:00 AM
        timezones = [
            "Europe/Amsterdam",
            "America/New_York",
            "Asia/Tokyo",
            "Australia/Sydney",
        ]

        for tz_name in timezones:
            tz = ZoneInfo(tz_name)
            # Local time 10:00 AM - past end time in all zones
            local_time = datetime(2024, 1, 15, 10, 0, tzinfo=tz)
            assert setback.is_night_period(local_time) is False, f"Failed for {tz_name}"

            # Local time 23:00 (11 PM) - during night in all zones
            local_time = datetime(2024, 1, 15, 23, 0, tzinfo=tz)
            assert setback.is_night_period(local_time) is True, f"Failed for {tz_name}"
