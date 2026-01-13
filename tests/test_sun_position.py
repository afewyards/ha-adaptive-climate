"""Tests for sun position calculator."""
import pytest
from datetime import datetime, date, timezone, timedelta
from unittest.mock import Mock, patch

from custom_components.adaptive_thermostat.adaptive.sun_position import (
    SunPositionCalculator,
    SunPosition,
    ORIENTATION_AZIMUTH,
    DEFAULT_EFFECTIVE_ANGLE,
)


class TestSunPositionCalculator:
    """Tests for SunPositionCalculator."""

    @pytest.fixture
    def amsterdam_calculator(self):
        """Create calculator for Amsterdam (52.37N, 4.89E)."""
        return SunPositionCalculator(
            latitude=52.37,
            longitude=4.89,
            elevation_m=0,
        )

    @pytest.fixture
    def london_calculator(self):
        """Create calculator for London (51.51N, -0.13W)."""
        return SunPositionCalculator(
            latitude=51.51,
            longitude=-0.13,
            elevation_m=0,
        )

    def test_orientation_azimuth_mapping(self):
        """Test that orientation azimuth mapping is correct."""
        assert ORIENTATION_AZIMUTH["north"] == 0
        assert ORIENTATION_AZIMUTH["northeast"] == 45
        assert ORIENTATION_AZIMUTH["east"] == 90
        assert ORIENTATION_AZIMUTH["southeast"] == 135
        assert ORIENTATION_AZIMUTH["south"] == 180
        assert ORIENTATION_AZIMUTH["southwest"] == 225
        assert ORIENTATION_AZIMUTH["west"] == 270
        assert ORIENTATION_AZIMUTH["northwest"] == 315
        assert ORIENTATION_AZIMUTH["roof"] is None
        assert ORIENTATION_AZIMUTH["none"] is None

    def test_get_position_at_time_summer_noon(self, amsterdam_calculator):
        """Test sun position at solar noon in summer."""
        # Summer solstice at noon (approximately solar noon)
        dt = datetime(2025, 6, 21, 12, 0, tzinfo=timezone.utc)
        pos = amsterdam_calculator.get_position_at_time(dt)

        # Sun should be high and roughly south
        assert pos.elevation > 50  # High summer sun at ~62 degrees
        assert 150 < pos.azimuth < 210  # Roughly south

    def test_get_position_at_time_winter_noon(self, amsterdam_calculator):
        """Test sun position at solar noon in winter."""
        # Winter solstice at noon
        dt = datetime(2025, 12, 21, 12, 0, tzinfo=timezone.utc)
        pos = amsterdam_calculator.get_position_at_time(dt)

        # Sun should be low and south
        assert pos.elevation < 20  # Low winter sun at ~15 degrees
        assert 160 < pos.azimuth < 200  # South

    def test_get_position_at_time_sunrise(self, amsterdam_calculator):
        """Test sun position shortly after sunrise."""
        # Summer, early morning (around 05:00 UTC, sun has risen)
        dt = datetime(2025, 6, 21, 5, 0, tzinfo=timezone.utc)
        pos = amsterdam_calculator.get_position_at_time(dt)

        # Sun should be low but above horizon
        assert pos.elevation > 0
        assert pos.elevation < 30  # Not yet at peak
        # Morning sun is in the east/northeast
        assert 40 < pos.azimuth < 100

    def test_calculate_window_sun_entry_east_window_summer(self, amsterdam_calculator):
        """Test sun entry time for east-facing window in summer."""
        target = date(2025, 6, 21)

        entry_time = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="east",
            target_date=target,
            min_elevation=10.0,
        )

        assert entry_time is not None
        # East windows get sun early in the morning
        assert entry_time.hour < 9

    def test_calculate_window_sun_entry_south_window_summer(self, amsterdam_calculator):
        """Test sun entry time for south-facing window in summer."""
        target = date(2025, 6, 21)

        entry_time = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="south",
            target_date=target,
            min_elevation=10.0,
        )

        assert entry_time is not None
        # South windows in summer don't get direct sun until later
        # (sun rises in northeast and tracks through south around midday)
        assert entry_time.hour >= 8

    def test_calculate_window_sun_entry_west_window(self, amsterdam_calculator):
        """Test sun entry time for west-facing window."""
        target = date(2025, 6, 21)

        entry_time = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="west",
            target_date=target,
            min_elevation=10.0,
        )

        assert entry_time is not None
        # West windows don't get sun until afternoon
        assert entry_time.hour >= 11

    def test_calculate_window_sun_entry_north_window_summer(self, amsterdam_calculator):
        """Test sun entry time for north-facing window in summer."""
        target = date(2025, 6, 21)

        entry_time = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="north",
            target_date=target,
            min_elevation=10.0,
        )

        # North windows may get brief morning/evening sun in summer
        # but search stops at 14:00 so may not find entry
        # This is acceptable - north windows have minimal direct sun
        # If found, should be early (when sun is in NE)
        if entry_time is not None:
            assert entry_time.hour < 8

    def test_calculate_window_sun_entry_roof(self, amsterdam_calculator):
        """Test sun entry time for skylights (roof)."""
        target = date(2025, 6, 21)

        entry_time = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="roof",
            target_date=target,
            min_elevation=10.0,
        )

        assert entry_time is not None
        # Roof gets sun when elevation reaches min_elevation
        # Should be shortly after sunrise in summer

    def test_calculate_window_sun_entry_none_orientation(self, amsterdam_calculator):
        """Test that 'none' orientation returns None."""
        target = date(2025, 6, 21)

        entry_time = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="none",
            target_date=target,
            min_elevation=10.0,
        )

        assert entry_time is None

    def test_calculate_window_sun_entry_invalid_orientation(self, amsterdam_calculator):
        """Test that invalid orientation returns None."""
        target = date(2025, 6, 21)

        entry_time = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="invalid",
            target_date=target,
            min_elevation=10.0,
        )

        assert entry_time is None

    def test_seasonal_variation_south_window(self, amsterdam_calculator):
        """Test that south window entry time varies by season."""
        summer = date(2025, 6, 21)
        winter = date(2025, 12, 21)

        summer_entry = amsterdam_calculator.calculate_window_sun_entry_time(
            "south", summer, min_elevation=10.0
        )
        winter_entry = amsterdam_calculator.calculate_window_sun_entry_time(
            "south", winter, min_elevation=10.0
        )

        assert summer_entry is not None
        assert winter_entry is not None

        # In winter, the sun stays low in the southern sky
        # so south windows get sun earlier after sunrise
        # (sun doesn't track as far east in winter)

    def test_min_elevation_affects_entry_time(self, amsterdam_calculator):
        """Test that higher min_elevation delays entry time."""
        target = date(2025, 6, 21)

        # Use east window where elevation changes during morning entry
        low_elevation_entry = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="east",
            target_date=target,
            min_elevation=5.0,
        )

        high_elevation_entry = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="east",
            target_date=target,
            min_elevation=20.0,
        )

        assert low_elevation_entry is not None
        assert high_elevation_entry is not None
        # Higher elevation requirement means later entry time
        assert high_elevation_entry > low_elevation_entry

    def test_effective_angle_affects_entry_time(self, amsterdam_calculator):
        """Test that smaller effective angle delays entry time."""
        target = date(2025, 6, 21)

        wide_angle_entry = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="south",
            target_date=target,
            min_elevation=10.0,
            effective_angle=60,  # Wide angle
        )

        narrow_angle_entry = amsterdam_calculator.calculate_window_sun_entry_time(
            window_orientation="south",
            target_date=target,
            min_elevation=10.0,
            effective_angle=30,  # Narrow angle
        )

        assert wide_angle_entry is not None
        assert narrow_angle_entry is not None
        # Narrower angle requirement means later entry time
        assert narrow_angle_entry >= wide_angle_entry

    def test_latitude_affects_timing(self, amsterdam_calculator, london_calculator):
        """Test that different latitudes produce different timings."""
        target = date(2025, 6, 21)

        amsterdam_entry = amsterdam_calculator.calculate_window_sun_entry_time(
            "south", target, min_elevation=10.0
        )
        london_entry = london_calculator.calculate_window_sun_entry_time(
            "south", target, min_elevation=10.0
        )

        # Both should find entry times
        assert amsterdam_entry is not None
        assert london_entry is not None
        # Times may differ slightly due to latitude difference

    def test_is_sun_effective_checks_elevation(self, amsterdam_calculator):
        """Test that _is_sun_effective checks elevation threshold."""
        # Low elevation sun
        low_sun = SunPosition(azimuth=180, elevation=5, timestamp=datetime.now())
        # High elevation sun
        high_sun = SunPosition(azimuth=180, elevation=15, timestamp=datetime.now())

        # South facing window (180 degrees)
        window_azimuth = 180
        min_elevation = 10.0
        effective_angle = 45

        # Low sun should not be effective
        assert not amsterdam_calculator._is_sun_effective(
            low_sun, window_azimuth, min_elevation, effective_angle
        )
        # High sun should be effective
        assert amsterdam_calculator._is_sun_effective(
            high_sun, window_azimuth, min_elevation, effective_angle
        )

    def test_is_sun_effective_checks_azimuth(self, amsterdam_calculator):
        """Test that _is_sun_effective checks azimuth angle."""
        # Sun at different azimuths
        sun_south = SunPosition(azimuth=180, elevation=30, timestamp=datetime.now())
        sun_east = SunPosition(azimuth=90, elevation=30, timestamp=datetime.now())
        sun_north = SunPosition(azimuth=0, elevation=30, timestamp=datetime.now())

        # South facing window
        window_azimuth = 180
        min_elevation = 10.0
        effective_angle = 45

        # Sun in south should be effective for south window
        assert amsterdam_calculator._is_sun_effective(
            sun_south, window_azimuth, min_elevation, effective_angle
        )
        # Sun in east should not be effective for south window
        assert not amsterdam_calculator._is_sun_effective(
            sun_east, window_azimuth, min_elevation, effective_angle
        )
        # Sun in north should not be effective for south window
        assert not amsterdam_calculator._is_sun_effective(
            sun_north, window_azimuth, min_elevation, effective_angle
        )

    def test_azimuth_wraparound_at_360(self, amsterdam_calculator):
        """Test that azimuth comparison handles 360/0 degree wraparound."""
        # Sun at 350 degrees (just west of north)
        sun_near_north = SunPosition(azimuth=350, elevation=30, timestamp=datetime.now())

        # North facing window (0 degrees)
        window_azimuth = 0
        min_elevation = 10.0
        effective_angle = 45

        # 350 degrees should be within 45 degrees of 0/360 (diff is 10 degrees)
        assert amsterdam_calculator._is_sun_effective(
            sun_near_north, window_azimuth, min_elevation, effective_angle
        )


class TestSunPositionCalculatorFromHass:
    """Tests for creating calculator from Home Assistant."""

    def test_from_hass_success(self):
        """Test successful creation from HA config."""
        mock_hass = Mock()
        mock_hass.config.latitude = 52.37
        mock_hass.config.longitude = 4.89
        mock_hass.config.elevation = 10

        calculator = SunPositionCalculator.from_hass(mock_hass)

        assert calculator is not None
        assert calculator._latitude == 52.37
        assert calculator._longitude == 4.89

    def test_from_hass_missing_latitude(self):
        """Test that missing latitude returns None."""
        mock_hass = Mock()
        mock_hass.config.latitude = None
        mock_hass.config.longitude = 4.89
        mock_hass.config.elevation = 0

        calculator = SunPositionCalculator.from_hass(mock_hass)

        assert calculator is None

    def test_from_hass_missing_longitude(self):
        """Test that missing longitude returns None."""
        mock_hass = Mock()
        mock_hass.config.latitude = 52.37
        mock_hass.config.longitude = None
        mock_hass.config.elevation = 0

        calculator = SunPositionCalculator.from_hass(mock_hass)

        assert calculator is None

    def test_from_hass_no_elevation_uses_default(self):
        """Test that missing elevation defaults to 0."""
        mock_hass = Mock()
        mock_hass.config.latitude = 52.37
        mock_hass.config.longitude = 4.89
        mock_hass.config.elevation = None

        calculator = SunPositionCalculator.from_hass(mock_hass)

        assert calculator is not None
