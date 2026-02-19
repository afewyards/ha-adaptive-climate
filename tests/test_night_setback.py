"""Tests for night setback module."""

import pytest
from datetime import datetime, time, timedelta
from unittest.mock import Mock
from custom_components.adaptive_climate.adaptive.night_setback import NightSetback, NightSetbackManager
from custom_components.adaptive_climate.adaptive.thermal_rates import ThermalRateLearner


class TestNightSetback:
    """Test NightSetback class."""

    def test_night_period_detection_basic(self):
        """Test basic night period detection with fixed times."""
        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0)

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
        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.5)

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
        setback = NightSetback(start_time="sunset", end_time="06:00", setback_delta=2.0)

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
        setback = NightSetback(start_time="sunset+30", end_time="06:00", setback_delta=2.0)

        sunset = datetime(2024, 1, 15, 18, 30)

        # 18:45 (15 minutes after sunset, but before sunset+30)
        current = datetime(2024, 1, 15, 18, 45)
        assert setback.is_night_period(current, sunset) is False

        # 19:05 (35 minutes after sunset, after sunset+30)
        current = datetime(2024, 1, 15, 19, 5)
        assert setback.is_night_period(current, sunset) is True

        # Sunset - 15 minutes
        setback = NightSetback(start_time="sunset-15", end_time="06:00", setback_delta=2.0)

        # 18:20 (10 minutes before sunset, but after sunset-15)
        current = datetime(2024, 1, 15, 18, 20)
        assert setback.is_night_period(current, sunset) is True

    def test_recovery_deadline_override(self):
        """Test recovery deadline forces setpoint restoration."""
        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0, recovery_deadline="06:00")

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
        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0)

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
        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0, recovery_deadline="06:00")

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
        setback = NightSetback(start_time="01:00", end_time="06:00", setback_delta=2.0)

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
            zone_id="bedroom", start_time="22:00", end_time="06:00", setback_delta=2.5, recovery_deadline="06:00"
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

        manager.configure_zone(zone_id="bedroom", start_time="22:00", end_time="06:00", setback_delta=2.0)

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

        manager.configure_zone(zone_id="bedroom", start_time="22:00", end_time="06:00", setback_delta=2.0)

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
        manager.configure_zone(zone_id="bedroom", start_time="22:00", end_time="06:00", setback_delta=2.0)

        # Living room: sunset - 23:00, 1.5°C setback
        manager.configure_zone(zone_id="living_room", start_time="sunset", end_time="23:00", setback_delta=1.5)

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

        manager.configure_zone(zone_id="living_room", start_time="sunset+30", end_time="23:00", setback_delta=1.5)

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
            heating_type="radiator",
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
            heating_type="forced_air",
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
            heating_type="floor_hydronic",
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
            heating_type="convector",
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
            heating_type=None,
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
            heating_type="floor_hydronic",
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
            heating_type="forced_air",
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
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="floor_hydronic"
        )
        assert setback_floor._get_cold_soak_margin() == 1.5

        # Radiator: 30% margin
        setback_radiator = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="radiator"
        )
        assert setback_radiator._get_cold_soak_margin() == 1.3

        # Convector: 20% margin
        setback_convector = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="convector"
        )
        assert setback_convector._get_cold_soak_margin() == 1.2

        # Forced air: 10% margin
        setback_forced = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="forced_air"
        )
        assert setback_forced._get_cold_soak_margin() == 1.1

        # Unknown: 25% margin (default)
        setback_unknown = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="unknown_type"
        )
        assert setback_unknown._get_cold_soak_margin() == 1.25

    def test_heating_type_rate_estimates(self):
        """Test heating type rate estimates are correct."""
        # Floor hydronic: 0.5°C/h
        setback_floor = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="floor_hydronic"
        )
        assert setback_floor._get_heating_rate() == 0.5

        # Radiator: 1.2°C/h
        setback_radiator = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="radiator"
        )
        assert setback_radiator._get_heating_rate() == 1.2

        # Convector: 2.0°C/h
        setback_convector = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="convector"
        )
        assert setback_convector._get_heating_rate() == 2.0

        # Forced air: 4.0°C/h
        setback_forced = NightSetback(
            start_time="22:00", end_time="06:00", setback_delta=2.0, heating_type="forced_air"
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
            heating_type="radiator",
        )

        current = datetime(2024, 1, 15, 4, 0)
        base_setpoint = 20.0
        current_temp = 15.0

        # Should always return False when no recovery deadline
        should_recover = setback.should_start_recovery(current, current_temp, base_setpoint)
        assert should_recover is False


def test_night_setback_learned_rate_module_exists():
    """Marker test to verify night setback learned rate module exists."""
    from custom_components.adaptive_climate.adaptive.night_setback import NightSetback
    from custom_components.adaptive_climate.adaptive.thermal_rates import ThermalRateLearner

    # Verify new parameters exist
    learner = ThermalRateLearner()
    setback = NightSetback(
        start_time="22:00", end_time="06:00", setback_delta=2.0, thermal_rate_learner=learner, heating_type="radiator"
    )

    assert hasattr(setback, "thermal_rate_learner")
    assert hasattr(setback, "heating_type")
    assert hasattr(setback, "_get_heating_rate")
    assert hasattr(setback, "_get_cold_soak_margin")


class TestNightSetbackTimezone:
    """Test NightSetback with timezone-aware datetimes."""

    def test_local_time_used_not_utc(self):
        """Test that local time is used for period checks, not UTC.

        Scenario: Local time is 10:00 AM (past the 08:57 end time)
                  UTC time is 08:00 AM (before the 08:57 end time)
        Result: Night setback should NOT be active (local time is used correctly)
        """
        from zoneinfo import ZoneInfo

        setback = NightSetback(start_time="22:00", end_time="08:57", setback_delta=2.0)

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

        setback = NightSetback(start_time="23:00", end_time="09:00", setback_delta=3.0)

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

        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0)

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

        setback = NightSetback(start_time="22:00", end_time="07:00", setback_delta=2.0)

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

    def test_get_adjusted_setpoint_timezone_aware(self):
        """Test get_adjusted_setpoint with timezone-aware datetime doesn't crash."""
        from datetime import timezone, timedelta as td

        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0, recovery_deadline="06:00")

        base_setpoint = 20.0

        # Create timezone-aware datetime (UTC+2)
        tz = timezone(td(hours=2))

        # Test during night, more than 2 hours before recovery deadline
        # 02:00 is 4 hours before 06:00 - should be in setback
        current = datetime(2024, 1, 15, 2, 0, tzinfo=tz)
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current)
        assert adjusted == 18.0  # Still in setback

        # Test during night, within 2 hours of recovery deadline
        # 04:30 is 1.5 hours before 06:00 - should restore setpoint
        current = datetime(2024, 1, 15, 4, 30, tzinfo=tz)
        adjusted = setback.get_adjusted_setpoint(base_setpoint, current)
        assert adjusted == 20.0  # Recovery mode

    def test_should_start_recovery_timezone_aware(self):
        """Test should_start_recovery with timezone-aware datetime doesn't crash."""
        from datetime import timezone, timedelta as td

        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0, recovery_deadline="06:00")

        base_setpoint = 20.0

        # Create timezone-aware datetime (UTC-5)
        tz = timezone(td(hours=-5))

        # Large deficit, close to deadline (need to start recovery)
        current = datetime(2024, 1, 15, 4, 0, tzinfo=tz)  # 2 hours before deadline
        current_temp = 16.0  # 4°C below setpoint
        assert setback.should_start_recovery(current, current_temp, base_setpoint) is True

        # Small deficit, plenty of time (no recovery needed)
        current = datetime(2024, 1, 15, 2, 0, tzinfo=tz)  # 4 hours before deadline
        current_temp = 19.0  # 1°C below setpoint
        assert setback.should_start_recovery(current, current_temp, base_setpoint) is False

    def test_recovery_deadline_crosses_midnight_timezone_aware(self):
        """Test recovery deadline calculations when deadline is next day."""
        from datetime import timezone, timedelta as td

        setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0, recovery_deadline="06:00")

        base_setpoint = 20.0

        # Create timezone-aware datetime (UTC+1)
        tz = timezone(td(hours=1))

        # Test at 23:00 (11 PM) - deadline is "tomorrow" at 06:00 (7 hours away)
        current = datetime(2024, 1, 15, 23, 0, tzinfo=tz)
        current_temp = 15.0  # 5°C deficit

        # With default heating rate (1.0°C/h) and default margin (1.25x):
        # estimated_recovery = (5 / 1.0) * 1.25 = 6.25 hours
        # time_until_deadline = 7 hours
        # Should NOT start recovery (6.25 < 7)
        should_recover = setback.should_start_recovery(current, current_temp, base_setpoint)
        assert should_recover is False


class TestNightSetbackManagerGraduatedDelta:
    """Test NightSetbackManager with graduated delta callback."""

    def test_manager_applies_min_of_configured_and_allowed(self):
        """Test manager applies min(configured_delta, allowed_delta)."""
        from unittest.mock import Mock
        from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
        from custom_components.adaptive_climate.adaptive.night_setback import NightSetback

        # Mock hass
        hass = Mock()
        hass.states.get.return_value = None
        hass.data = {}

        # Create night setback with configured delta of 3.0
        night_setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=3.0)

        # Callback returns allowed_delta of 0.5
        def get_allowed_delta():
            return 0.5

        # Create manager
        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            get_allowed_setback_delta=get_allowed_delta,
        )

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply min(3.0, 0.5) = 0.5
        assert in_night_period is True
        assert effective_target == 19.5  # 20.0 - 0.5
        assert info.get("night_setback_delta") == 0.5

    def test_manager_applies_full_when_allowed_none(self):
        """Test manager applies full configured_delta when allowed_delta is None."""
        from unittest.mock import Mock
        from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
        from custom_components.adaptive_climate.adaptive.night_setback import NightSetback

        # Mock hass
        hass = Mock()
        hass.states.get.return_value = None
        hass.data = {}

        # Create night setback with configured delta of 3.0
        night_setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=3.0)

        # Callback returns None (no restriction)
        def get_allowed_delta():
            return None

        # Create manager
        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            get_allowed_setback_delta=get_allowed_delta,
        )

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply full 3.0 when allowed is None
        assert in_night_period is True
        assert effective_target == 17.0  # 20.0 - 3.0
        assert info.get("night_setback_delta") == 3.0

    def test_manager_applies_full_when_allowed_exceeds_configured(self):
        """Test manager applies full configured when allowed > configured."""
        from unittest.mock import Mock
        from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
        from custom_components.adaptive_climate.adaptive.night_setback import NightSetback

        # Mock hass
        hass = Mock()
        hass.states.get.return_value = None
        hass.data = {}

        # Create night setback with configured delta of 2.0
        night_setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0)

        # Callback returns allowed_delta of 5.0 (exceeds configured)
        def get_allowed_delta():
            return 5.0

        # Create manager
        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            get_allowed_setback_delta=get_allowed_delta,
        )

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply min(2.0, 5.0) = 2.0
        assert in_night_period is True
        assert effective_target == 18.0  # 20.0 - 2.0
        assert info.get("night_setback_delta") == 2.0

    def test_manager_applies_zero_when_allowed_zero(self):
        """Test manager suppresses setback when allowed_delta is 0."""
        from unittest.mock import Mock
        from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
        from custom_components.adaptive_climate.adaptive.night_setback import NightSetback

        # Mock hass
        hass = Mock()
        hass.states.get.return_value = None
        hass.data = {}

        # Create night setback with configured delta of 3.0
        night_setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=3.0)

        # Callback returns allowed_delta of 0 (fully suppressed)
        def get_allowed_delta():
            return 0.0

        # Create manager
        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            get_allowed_setback_delta=get_allowed_delta,
        )

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply 0 (no setback)
        assert in_night_period is True
        assert effective_target == 20.0  # 20.0 - 0.0 (no change)
        assert info.get("night_setback_delta") == 0.0

    def test_manager_suppressed_reason_limited_when_reduced(self):
        """Test suppressed_reason is 'limited' when allowed < configured."""
        from unittest.mock import Mock
        from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
        from custom_components.adaptive_climate.adaptive.night_setback import NightSetback

        # Mock hass
        hass = Mock()
        hass.states.get.return_value = None
        hass.data = {}

        # Create night setback with configured delta of 3.0
        night_setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=3.0)

        # Callback returns allowed_delta of 1.0 (less than configured)
        def get_allowed_delta():
            return 1.0

        # Create manager
        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            get_allowed_setback_delta=get_allowed_delta,
        )

        # During night
        current = datetime(2024, 1, 15, 23, 0)
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should have suppressed_reason='limited' since allowed < configured
        assert in_night_period is True
        assert effective_target == 19.0  # 20.0 - 1.0
        assert info.get("suppressed_reason") == "limited"

    def test_manager_no_suppressed_reason_when_full(self):
        """Test no suppressed_reason when allowed >= configured or None."""
        from unittest.mock import Mock
        from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
        from custom_components.adaptive_climate.adaptive.night_setback import NightSetback

        # Mock hass
        hass = Mock()
        hass.states.get.return_value = None
        hass.data = {}

        # Test case 1: allowed >= configured
        night_setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0)

        def get_allowed_delta_high():
            return 5.0

        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            get_allowed_setback_delta=get_allowed_delta_high,
        )

        current = datetime(2024, 1, 15, 23, 0)
        _, _, info = manager.calculate_night_setback_adjustment(current)
        assert "suppressed_reason" not in info

        # Test case 2: allowed is None
        def get_allowed_delta_none():
            return None

        manager2 = NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            get_allowed_setback_delta=get_allowed_delta_none,
        )

        _, _, info2 = manager2.calculate_night_setback_adjustment(current)
        assert "suppressed_reason" not in info2


class TestPreheatTimingWithGraduatedDelta:
    """Test preheat timing calculations with graduated (limited) setback delta.

    When night setback is suppressed or limited due to learning gate, the effective
    delta is smaller than the configured delta. Preheat calculations must use the
    effective delta, not the configured delta, to avoid starting preheat too early.

    Key insight:
    - Configured setback delta: 3.0°C (what user configured)
    - Effective delta: target_temp - effective_target (actual temperature drop)
    - Preheat must calculate based on effective_delta to get correct timing
    """

    def test_preheat_duration_scales_with_effective_delta(self):
        """Test that preheat duration is based on effective delta, not configured delta.

        When learning limits night setback:
        - Configured delta: 3.0°C
        - Allowed delta: 0.5°C (limited by learning)
        - Effective delta: 0.5°C (actual temperature drop)
        - Preheat should be ~1/6 the duration of full 3.0°C delta
        """
        # This test will FAIL until NightSetbackCalculator uses effective_delta
        #
        # Expected behavior:
        # 1. configured_delta = 3.0°C
        # 2. allowed_delta = 0.5°C (from learning gate callback)
        # 3. effective_delta = min(3.0, 0.5) = 0.5°C
        # 4. Preheat calculates: time = (0.5 / heating_rate) * margin
        # 5. NOT: time = (3.0 / heating_rate) * margin
        #
        # Example with radiator (1.2°C/hour):
        # - Full delta (3.0°C): (3.0/1.2)*1.3 = 3.25 hours = 195 minutes
        # - Limited delta (0.5°C): (0.5/1.2)*1.3 = 0.54 hours = 32.5 minutes
        # - Ratio: 195 / 32.5 ≈ 6.0 ✓

        pytest.skip("Implementation not yet available - NightSetbackCalculator needs effective_delta")

    def test_preheat_duration_uses_effective_not_configured(self):
        """Test preheat explicitly uses effective delta when learning limits setback.

        Scenario:
        - Configured delta: 3.0°C
        - Allowed delta: 1.0°C (partial suppression)
        - Effective delta: 1.0°C
        - Expected preheat: ~1/3 the duration of full 3.0°C delta
        """
        # This test will FAIL until implementation provides effective_delta to preheat
        #
        # Expected behavior:
        # 1. Night setback configured with delta=3.0°C
        # 2. Learning gate allows only 1.0°C
        # 3. NightSetbackManager applies effective_delta=1.0°C
        # 4. NightSetbackCalculator passes effective_delta to preheat calculation
        # 5. Preheat start time based on recovering 1.0°C, not 3.0°C
        #
        # Verification:
        # preheat_start_time(1.0°C) > preheat_start_time(3.0°C)
        # (smaller delta = shorter preheat = starts later)

        pytest.skip("Implementation not yet available - effective_delta not passed to preheat")

    def test_preheat_disabled_when_effective_delta_zero(self):
        """Test preheat is disabled when effective delta is zero (fully suppressed).

        Scenario:
        - Configured delta: 3.0°C
        - Allowed delta: 0.0°C (fully suppressed by learning)
        - Effective delta: 0.0°C (no temperature drop)
        - Expected: preheat_start = None OR preheat_start = deadline (immediate)
        """
        # This test will FAIL until implementation checks effective_delta before preheat
        #
        # Expected behavior:
        # 1. Learning gate allows 0.0°C (fully suppressed)
        # 2. effective_delta = 0.0°C
        # 3. Preheat calculator sees delta=0.0
        # 4. Returns None or deadline (no lead time needed)
        # 5. Preheat is effectively disabled
        #
        # Rationale:
        # No temperature drop = no recovery needed = no preheat lead time

        pytest.skip("Implementation not yet available - zero delta handling in preheat")

    def test_preheat_full_duration_when_unlimited(self):
        """Test preheat uses full configured delta when allowed_delta is None or >= configured.

        Scenario:
        - Configured delta: 3.0°C
        - Allowed delta: None (no restriction from learning)
        - Effective delta: 3.0°C (full configured delta)
        - Expected: preheat based on full 3.0°C recovery
        """
        # This test will FAIL until implementation handles None allowed_delta correctly
        #
        # Expected behavior:
        # 1. Learning gate returns None (no restriction)
        # 2. effective_delta = configured_delta = 3.0°C
        # 3. Preheat calculates based on 3.0°C
        # 4. Preheat starts early enough to recover 3.0°C by deadline
        #
        # Also test when allowed_delta >= configured_delta:
        # - configured: 3.0°C, allowed: 5.0°C -> effective: 3.0°C

        pytest.skip("Implementation not yet available - None allowed_delta handling")

    def test_preheat_transition_from_suppressed_to_enabled(self):
        """Test preheat timing changes when learning transitions from limited to unlimited.

        Scenario:
        - Start: allowed_delta=0.0°C, effective_delta=0.0, no preheat
        - Transition: allowed_delta=3.0°C, effective_delta=3.0, preheat enabled
        - Expected: preheat start time recalculated based on new effective_delta
        """
        # This test will FAIL until implementation dynamically recalculates preheat
        #
        # Expected behavior:
        # 1. Initially: allowed=0.0 -> preheat disabled or at deadline
        # 2. Callback value changes to allowed=3.0
        # 3. Preheat start time recalculated based on current_temp and full delta
        # 4. System starts preheating if current_time >= new preheat_start
        #
        # Edge case: Transition happens close to deadline
        # - If insufficient time to preheat, should start immediately

        pytest.skip("Implementation not yet available - dynamic preheat recalculation")

    def test_preheat_with_small_allowed_delta_floor_hydronic(self):
        """Test preheat timing for floor hydronic with 0.5°C allowed delta.

        Floor hydronic has slow heating rate (0.5°C/hour default).
        Small delta (0.5°C) should result in ~1 hour preheat (vs ~6 hours for 3.0°C).

        Scenario:
        - Heating type: floor_hydronic
        - Configured delta: 3.0°C
        - Allowed delta: 0.5°C
        - Effective delta: 0.5°C
        - Heating rate: 0.5°C/hour (default)
        - Expected preheat: ~1.5 hours (with 1.5x margin)
        """
        # This test will FAIL until implementation uses effective_delta
        #
        # Expected calculation:
        # 1. effective_delta = 0.5°C
        # 2. heating_rate = 0.5°C/hour (floor_hydronic default)
        # 3. base_time = 0.5 / 0.5 = 1.0 hour
        # 4. margin = 1.5x (floor_hydronic cold-soak)
        # 5. total_time = 1.0 * 1.5 = 1.5 hours = 90 minutes
        # 6. preheat_start = deadline - 90 minutes
        #
        # Contrast with full 3.0°C:
        # - base_time = 3.0 / 0.5 = 6.0 hours
        # - total_time = 6.0 * 1.5 = 9.0 hours (capped to max_hours)

        pytest.skip("Implementation not yet available - effective_delta for floor_hydronic")

    def test_preheat_with_small_allowed_delta_forced_air(self):
        """Test preheat timing for forced air with 0.5°C allowed delta.

        Forced air has fast heating rate (4.0°C/hour default).
        Small delta (0.5°C) should result in ~8 minutes preheat (vs ~50 minutes for 3.0°C).

        Scenario:
        - Heating type: forced_air
        - Configured delta: 3.0°C
        - Allowed delta: 0.5°C
        - Effective delta: 0.5°C
        - Heating rate: 4.0°C/hour (default)
        - Expected preheat: ~8 minutes (with 1.1x margin)
        """
        # This test will FAIL until implementation uses effective_delta
        #
        # Expected calculation:
        # 1. effective_delta = 0.5°C
        # 2. heating_rate = 4.0°C/hour (forced_air default)
        # 3. base_time = 0.5 / 4.0 = 0.125 hours = 7.5 minutes
        # 4. margin = 1.1x (forced_air cold-soak)
        # 5. total_time = 7.5 * 1.1 = 8.25 minutes
        # 6. preheat_start = deadline - 8.25 minutes
        #
        # Contrast with full 3.0°C:
        # - base_time = 3.0 / 4.0 = 0.75 hours = 45 minutes
        # - total_time = 45 * 1.1 = 49.5 minutes

        pytest.skip("Implementation not yet available - effective_delta for forced_air")

    def test_preheat_graduated_delta_ratios(self):
        """Test that preheat duration ratios match effective delta ratios.

        Mathematical relationship:
        preheat_duration ∝ effective_delta

        If delta is reduced by 6x (3.0°C -> 0.5°C), preheat should reduce by ~6x.

        Scenario:
        - Test case 1: effective_delta = 3.0°C -> preheat = T
        - Test case 2: effective_delta = 1.5°C -> preheat = T/2
        - Test case 3: effective_delta = 0.5°C -> preheat = T/6
        """
        # This test will FAIL until implementation correctly scales preheat
        #
        # Expected behavior:
        # Given same heating rate and outdoor conditions:
        # duration(3.0) / duration(0.5) ≈ 3.0 / 0.5 = 6.0
        #
        # Example with radiator (1.2°C/hour, 1.3x margin):
        # - 3.0°C: (3.0/1.2)*60*1.3 = 195 minutes
        # - 1.5°C: (1.5/1.2)*60*1.3 = 97.5 minutes
        # - 0.5°C: (0.5/1.2)*60*1.3 = 32.5 minutes
        # - Ratios: 195/97.5=2.0, 195/32.5=6.0 ✓

        pytest.skip("Implementation not yet available - proportional scaling verification")

    def test_preheat_confidence_not_affected_by_suppression(self):
        """Test that preheat learner confidence is independent of suppression.

        Preheat learning should continue collecting observations regardless of whether
        night setback is suppressed. The suppression only affects when preheat starts,
        not the learning itself.

        Scenario:
        - Night setback limited (allowed_delta < configured_delta)
        - Preheat observations still collected during recovery periods
        - Confidence continues to build
        - When suppression lifts, preheat has good data
        """
        # This test will FAIL if implementation incorrectly blocks preheat learning
        #
        # Expected behavior:
        # 1. Suppression active (allowed < configured)
        # 2. Preheat disabled or limited for scheduling
        # 3. BUT observations still collected (natural warmup periods)
        # 4. Confidence continues to increase
        # 5. When suppression lifts, preheat can immediately use learned rates
        # 6. No restart of learning process
        #
        # Implementation note:
        # PreheatLearner.add_observation() should be called regardless of suppression
        # Only calculate_preheat_start() is affected by suppression

        pytest.skip("Implementation not yet available - preheat learning during suppression")

    def test_preheat_info_shows_effective_and_configured_delta(self):
        """Test that preheat state attributes show both effective and configured delta.

        For debugging and user understanding, both values should be visible.

        Scenario:
        - Configured delta: 3.0°C
        - Allowed delta: 1.0°C
        - Effective delta: 1.0°C
        - State attributes should show:
          - night_setback_delta_configured: 3.0
          - night_setback_delta_effective: 1.0
          - night_setback_delta_allowed: 1.0
          - suppressed_reason: "limited"
        """
        # This test will FAIL until state attributes include both deltas
        #
        # Expected state attributes:
        # {
        #   "night_setback_delta_configured": 3.0,
        #   "night_setback_delta_effective": 1.0,
        #   "night_setback_delta_allowed": 1.0,
        #   "suppressed_reason": "limited",
        #   "preheat_estimated_duration_min": 65,  # Based on 1.0°C, not 3.0°C
        #   "preheat_scheduled_start": "05:00",
        # }
        #
        # This helps users understand:
        # - What they configured (3.0°C)
        # - What learning allows (1.0°C)
        # - What is actually applied (1.0°C)

        pytest.skip("Implementation not yet available - state attributes for effective delta")


class TestNightSetbackManagerTransitions:
    """Test NightSetbackManager transition tracking via consume_transition()."""

    def _make_manager(self, night_setback=None):
        """Helper: create a NightSetbackManager with a configured night setback."""
        from unittest.mock import Mock
        from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
        from custom_components.adaptive_climate.adaptive.night_setback import NightSetback

        hass = Mock()
        hass.states.get.return_value = None
        hass.data = {}

        if night_setback is None:
            night_setback = NightSetback(start_time="22:00", end_time="06:00", setback_delta=2.0)

        return NightSetbackManager(
            hass=hass,
            entity_id="climate.test",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
        )

    def test_consume_transition_returns_started_on_entry(self):
        """consume_transition() returns 'started' when night setback first activates."""
        manager = self._make_manager()

        # Seed the previous state as NOT in night period (daytime)
        manager._night_setback_was_active = False

        # Call during night period → triggers "started" transition
        night_time = datetime(2024, 1, 15, 23, 0)
        manager.calculate_night_setback_adjustment(night_time)

        assert manager.consume_transition() == "started"

    def test_consume_transition_returns_ended_on_exit(self):
        """consume_transition() returns 'ended' when night setback deactivates."""
        manager = self._make_manager()

        # Seed the previous state as in night period
        manager._night_setback_was_active = True

        # Call during day period → triggers "ended" transition
        day_time = datetime(2024, 1, 15, 10, 0)
        manager.calculate_night_setback_adjustment(day_time)

        assert manager.consume_transition() == "ended"

    def test_consume_transition_returns_none_when_no_transition(self):
        """consume_transition() returns None when no state change occurred."""
        manager = self._make_manager()

        # Seed the previous state matching current (night→night = no transition)
        manager._night_setback_was_active = True

        night_time = datetime(2024, 1, 15, 23, 0)
        manager.calculate_night_setback_adjustment(night_time)

        assert manager.consume_transition() is None

    def test_consume_transition_clears_after_read(self):
        """consume_transition() clears the pending transition after returning it."""
        manager = self._make_manager()

        # Seed as daytime, trigger night entry
        manager._night_setback_was_active = False
        night_time = datetime(2024, 1, 15, 23, 0)
        manager.calculate_night_setback_adjustment(night_time)

        # First call returns the transition
        assert manager.consume_transition() == "started"
        # Second call returns None (cleared)
        assert manager.consume_transition() is None

    def test_consume_transition_none_on_fresh_manager(self):
        """consume_transition() returns None on a freshly created manager."""
        manager = self._make_manager()
        assert manager.consume_transition() is None
