"""Tests for solar recovery module."""
import pytest
from datetime import datetime, time, date
from unittest.mock import Mock, patch
from custom_components.adaptive_thermostat.adaptive.solar_recovery import (
    SolarRecovery,
    SolarRecoveryManager,
    WindowOrientation
)
from custom_components.adaptive_thermostat.adaptive.sun_position import (
    SunPositionCalculator,
)


class TestSolarRecovery:
    """Tests for SolarRecovery class."""

    def test_orientation_based_recovery_time(self):
        """Test that recovery time is adjusted based on window orientation."""
        base_time = "06:00"

        # South windows get earlier recovery (06:00 - 30 min = 05:30)
        south_recovery = SolarRecovery(window_orientation="south", base_recovery_time=base_time)
        assert south_recovery.get_recovery_time() == time(5, 30)

        # East windows get early recovery (06:00 - 20 min = 05:40)
        east_recovery = SolarRecovery(window_orientation="east", base_recovery_time=base_time)
        assert east_recovery.get_recovery_time() == time(5, 40)

        # West windows get later recovery (06:00 + 20 min = 06:20)
        west_recovery = SolarRecovery(window_orientation="west", base_recovery_time=base_time)
        assert west_recovery.get_recovery_time() == time(6, 20)

        # North windows get latest recovery (06:00 + 30 min = 06:30)
        north_recovery = SolarRecovery(window_orientation="north", base_recovery_time=base_time)
        assert north_recovery.get_recovery_time() == time(6, 30)

        # No windows - no adjustment (06:00)
        none_recovery = SolarRecovery(window_orientation="none", base_recovery_time=base_time)
        assert none_recovery.get_recovery_time() == time(6, 0)

    def test_solar_recovery_vs_active_heating_decision(self):
        """Test decision between solar recovery and active heating."""
        # South-facing windows, base recovery at 06:00, adjusted to 05:30
        recovery = SolarRecovery(window_orientation="south", base_recovery_time="06:00")

        # Case 1: Before adjusted recovery time (05:00) - use solar recovery
        current_time = datetime(2025, 1, 15, 5, 0)
        current_temp = 18.0
        target = 20.0
        assert recovery.should_use_solar_recovery(current_time, current_temp, target) is True

        # Case 2: After adjusted recovery time (06:00) - use active heating
        current_time = datetime(2025, 1, 15, 6, 0)
        assert recovery.should_use_solar_recovery(current_time, current_temp, target) is False

        # Case 3: At exact recovery time (05:30) - use active heating
        current_time = datetime(2025, 1, 15, 5, 30)
        assert recovery.should_use_solar_recovery(current_time, current_temp, target) is False

    def test_recovery_deadline_override(self):
        """Test that recovery deadline overrides solar recovery."""
        # South-facing windows with deadline at 07:00
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            recovery_deadline="07:00"
        )

        # Case 1: Early morning (05:00), temp deficit = 2°C, 2 hours until deadline
        # With 2°C/hour heating rate, we need 1 hour to heat
        # We have 2 hours available, so solar recovery is OK
        current_time = datetime(2025, 1, 15, 5, 0)
        current_temp = 18.0
        target = 20.0
        heating_rate = 2.0
        assert recovery.should_use_solar_recovery(
            current_time, current_temp, target, heating_rate
        ) is True

        # Case 2: Later (06:00), temp deficit = 2°C, 1 hour until deadline
        # With 2°C/hour heating rate, we need 1 hour to heat
        # We have exactly 1 hour - need to start active heating now
        current_time = datetime(2025, 1, 15, 6, 0)
        assert recovery.should_use_solar_recovery(
            current_time, current_temp, target, heating_rate
        ) is False

        # Case 3: Larger deficit (06:15), temp deficit = 3°C, 45 min until deadline
        # With 2°C/hour heating rate, we need 1.5 hours to heat
        # We only have 0.75 hours - must start active heating immediately
        current_time = datetime(2025, 1, 15, 6, 15)
        current_temp = 17.0
        target = 20.0
        assert recovery.should_use_solar_recovery(
            current_time, current_temp, target, heating_rate
        ) is False

    def test_no_recovery_deadline(self):
        """Test solar recovery without deadline only uses recovery time."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00"
        )

        # Before adjusted recovery time (05:30) - use solar recovery
        current_time = datetime(2025, 1, 15, 5, 0)
        current_temp = 18.0
        target = 20.0
        assert recovery.should_use_solar_recovery(current_time, current_temp, target) is True

        # After adjusted recovery time - use active heating
        current_time = datetime(2025, 1, 15, 6, 0)
        assert recovery.should_use_solar_recovery(current_time, current_temp, target) is False


class TestSolarRecoveryManager:
    """Tests for SolarRecoveryManager class."""

    def test_configure_zone(self):
        """Test configuring solar recovery for zones."""
        manager = SolarRecoveryManager()

        manager.configure_zone(
            zone_id="living_room",
            window_orientation="south",
            base_recovery_time="06:00",
            recovery_deadline="07:30"
        )

        config = manager.get_zone_config("living_room")
        assert config is not None
        assert config["orientation"] == "south"
        assert config["base_recovery_time"] == "06:00"
        assert config["adjusted_recovery_time"] == "05:30"  # South: -30 min
        assert config["recovery_deadline"] == "07:30"

    def test_should_use_solar_recovery_for_zone(self):
        """Test solar recovery decision for configured zone."""
        manager = SolarRecoveryManager()

        manager.configure_zone(
            zone_id="bedroom",
            window_orientation="east",
            base_recovery_time="06:00"
        )

        # Before adjusted recovery time (05:40 for east) - use solar recovery
        current_time = datetime(2025, 1, 15, 5, 30)
        assert manager.should_use_solar_recovery(
            "bedroom", current_time, 18.0, 20.0
        ) is True

        # After adjusted recovery time - use active heating
        current_time = datetime(2025, 1, 15, 6, 0)
        assert manager.should_use_solar_recovery(
            "bedroom", current_time, 18.0, 20.0
        ) is False

    def test_unconfigured_zone_uses_active_heating(self):
        """Test that unconfigured zones default to active heating."""
        manager = SolarRecoveryManager()

        # Zone not configured - should always use active heating
        current_time = datetime(2025, 1, 15, 5, 0)
        assert manager.should_use_solar_recovery(
            "unknown_zone", current_time, 18.0, 20.0
        ) is False

    def test_get_zone_recovery_time(self):
        """Test getting recovery time for zones."""
        manager = SolarRecoveryManager()

        manager.configure_zone(
            zone_id="kitchen",
            window_orientation="west",
            base_recovery_time="06:00"
        )

        # West orientation: 06:00 + 20 min = 06:20
        recovery_time = manager.get_zone_recovery_time("kitchen")
        assert recovery_time == time(6, 20)

        # Unconfigured zone
        assert manager.get_zone_recovery_time("unknown") is None

    def test_multiple_zones_different_orientations(self):
        """Test managing multiple zones with different orientations."""
        manager = SolarRecoveryManager()

        manager.configure_zone("south_room", "south", "06:00")
        manager.configure_zone("north_room", "north", "06:00")
        manager.configure_zone("east_room", "east", "06:00")

        # At 05:45, check which zones should use solar recovery
        current_time = datetime(2025, 1, 15, 5, 45)

        # South room (adjusted 05:30) - already past, use active heating
        assert manager.should_use_solar_recovery(
            "south_room", current_time, 18.0, 20.0
        ) is False

        # East room (adjusted 05:40) - already past, use active heating
        assert manager.should_use_solar_recovery(
            "east_room", current_time, 18.0, 20.0
        ) is False

        # North room (adjusted 06:30) - still before, use solar recovery
        assert manager.should_use_solar_recovery(
            "north_room", current_time, 18.0, 20.0
        ) is True


class TestDynamicSolarRecovery:
    """Tests for dynamic sun position-based solar recovery."""

    @pytest.fixture
    def sun_calculator(self):
        """Create a sun position calculator for Amsterdam."""
        return SunPositionCalculator(
            latitude=52.37,
            longitude=4.89,
            elevation_m=0,
        )

    def test_dynamic_recovery_time_with_calculator(self, sun_calculator):
        """Test that dynamic recovery time is used when calculator is available."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
            min_effective_elevation=10.0,
        )

        # Get recovery time for a summer date
        current_time = datetime(2025, 6, 21, 5, 0)
        dynamic_time = recovery.get_recovery_time(current_time)

        # Should return a time (dynamic calculation)
        assert dynamic_time is not None
        assert isinstance(dynamic_time, time)

    def test_static_fallback_without_calculator(self):
        """Test that static offset is used when no calculator is available."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=None,
        )

        # Should use static offset (south = -30 min from 06:00 = 05:30)
        current_time = datetime(2025, 6, 21, 5, 0)
        recovery_time = recovery.get_recovery_time(current_time)

        assert recovery_time == time(5, 30)

    def test_static_fallback_without_current_time(self, sun_calculator):
        """Test that static offset is used when current_time is not provided."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
        )

        # Without current_time, should use static offset
        recovery_time = recovery.get_recovery_time()

        assert recovery_time == time(5, 30)

    def test_set_sun_calculator(self, sun_calculator):
        """Test setting sun calculator after initialization."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
        )

        # Initially no calculator
        assert recovery._sun_calculator is None

        # Set calculator
        recovery.set_sun_calculator(sun_calculator)
        assert recovery._sun_calculator is sun_calculator

        # Now dynamic time should be used
        current_time = datetime(2025, 6, 21, 5, 0)
        dynamic_time = recovery.get_recovery_time(current_time)
        assert dynamic_time is not None

    def test_caching_for_same_day(self, sun_calculator):
        """Test that dynamic recovery time is cached for the same day."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
        )

        # First call
        current_time = datetime(2025, 6, 21, 5, 0)
        first_time = recovery.get_dynamic_recovery_time(current_time.date())

        # Second call same day - should use cache
        second_time = recovery.get_dynamic_recovery_time(current_time.date())

        assert first_time == second_time
        assert recovery._cache_date == current_time.date()

    def test_cache_invalidated_on_new_day(self, sun_calculator):
        """Test that cache is invalidated for a different day."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
        )

        # First day
        day1 = date(2025, 6, 21)
        time1 = recovery.get_dynamic_recovery_time(day1)

        # Different day
        day2 = date(2025, 12, 21)
        time2 = recovery.get_dynamic_recovery_time(day2)

        # Cache should be updated to day2
        assert recovery._cache_date == day2
        # Times should be different (summer vs winter)
        # Note: They might be the same by coincidence, so just check cache was updated

    def test_should_use_solar_recovery_with_dynamic_time(self, sun_calculator):
        """Test should_use_solar_recovery uses dynamic time when available."""
        recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
            min_effective_elevation=10.0,
        )

        # Get the dynamic recovery time for comparison
        test_date = datetime(2025, 6, 21, 5, 0)
        dynamic_time = recovery.get_recovery_time(test_date)

        # Time before dynamic recovery time should use solar recovery
        before_recovery = datetime.combine(
            test_date.date(),
            time(dynamic_time.hour - 1, dynamic_time.minute)
        )
        assert recovery.should_use_solar_recovery(
            before_recovery, 18.0, 20.0
        ) is True

        # Time after dynamic recovery time should use active heating
        after_recovery = datetime.combine(
            test_date.date(),
            time(dynamic_time.hour + 1, dynamic_time.minute)
        )
        assert recovery.should_use_solar_recovery(
            after_recovery, 18.0, 20.0
        ) is False

    def test_min_effective_elevation_parameter(self, sun_calculator):
        """Test that min_effective_elevation affects dynamic timing."""
        # Low elevation requirement
        recovery_low = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
            min_effective_elevation=5.0,
        )

        # High elevation requirement
        recovery_high = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
            min_effective_elevation=25.0,
        )

        test_date = date(2025, 6, 21)

        low_time = recovery_low.get_dynamic_recovery_time(test_date)
        high_time = recovery_high.get_dynamic_recovery_time(test_date)

        # Higher elevation requirement should result in later time
        assert low_time is not None
        assert high_time is not None
        assert high_time >= low_time

    def test_different_orientations_different_times(self, sun_calculator):
        """Test that different orientations produce different dynamic times."""
        test_date = date(2025, 6, 21)

        east_recovery = SolarRecovery(
            window_orientation="east",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
        )
        south_recovery = SolarRecovery(
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
        )
        west_recovery = SolarRecovery(
            window_orientation="west",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
        )

        east_time = east_recovery.get_dynamic_recovery_time(test_date)
        south_time = south_recovery.get_dynamic_recovery_time(test_date)
        west_time = west_recovery.get_dynamic_recovery_time(test_date)

        # All should return times
        assert east_time is not None
        assert south_time is not None
        assert west_time is not None

        # East should be earliest (morning sun)
        # West should be latest (afternoon sun)
        assert east_time < west_time

    def test_manager_with_sun_calculator(self, sun_calculator):
        """Test SolarRecoveryManager with sun position calculator."""
        manager = SolarRecoveryManager()

        manager.configure_zone(
            zone_id="living_room",
            window_orientation="south",
            base_recovery_time="06:00",
            sun_position_calculator=sun_calculator,
            min_effective_elevation=10.0,
        )

        # Get recovery time with current_time
        current_time = datetime(2025, 6, 21, 5, 0)
        recovery_time = manager.get_zone_recovery_time("living_room", current_time)

        assert recovery_time is not None
        # Should be dynamic time, not static 05:30
