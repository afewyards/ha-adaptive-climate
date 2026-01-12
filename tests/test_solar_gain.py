"""Tests for solar gain learning and prediction."""
import pytest
from datetime import datetime
from custom_components.adaptive_thermostat.solar.solar_gain import (
    SolarGainLearner,
    SolarGainManager,
    WindowOrientation,
    Season,
    CloudCoverage
)


class TestSolarGainPatternLearning:
    """Test solar gain pattern learning."""

    def test_learn_solar_gain_pattern(self):
        """Test learning solar gain patterns from measurements."""
        learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)

        # Add measurements for hour 12, winter, clear sky
        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),  # Winter, noon
            1.5,  # 1.5°C/hour gain
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),  # Winter, noon
            1.3,  # 1.3°C/hour gain
            CloudCoverage.CLEAR
        )

        # Should learn pattern: average of 1.5 and 1.3 = 1.4°C/hour
        assert learner.get_measurement_count() == 2
        assert learner.get_pattern_count() == 1

        # Predict for same conditions
        predicted = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.CLEAR
        )
        assert predicted == pytest.approx(1.4, abs=0.01)

    def test_multiple_patterns_by_hour(self):
        """Test learning different patterns for different hours."""
        learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)

        # Morning pattern (9 AM)
        learner.add_measurement(
            datetime(2024, 1, 15, 9, 0),
            0.8,
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 20, 9, 0),
            0.9,
            CloudCoverage.CLEAR
        )

        # Noon pattern (12 PM)
        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),
            1.5,
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),
            1.3,
            CloudCoverage.CLEAR
        )

        assert learner.get_pattern_count() == 2

        # Verify different predictions for different hours
        morning_gain = learner.predict_solar_gain(
            datetime(2024, 1, 25, 9, 0),
            CloudCoverage.CLEAR
        )
        noon_gain = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.CLEAR
        )

        assert morning_gain == pytest.approx(0.85, abs=0.01)  # (0.8 + 0.9) / 2
        assert noon_gain == pytest.approx(1.4, abs=0.01)      # (1.5 + 1.3) / 2

    def test_negative_gain_ignored(self):
        """Test that negative or zero gains are ignored."""
        learner = SolarGainLearner("bedroom", WindowOrientation.NORTH)

        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),
            -0.5,  # Negative gain (cooling)
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 15, 13, 0),
            0.0,  # Zero gain
            CloudCoverage.CLEAR
        )

        assert learner.get_measurement_count() == 0
        assert learner.get_pattern_count() == 0

    def test_insufficient_measurements_no_pattern(self):
        """Test that patterns require at least 2 measurements."""
        learner = SolarGainLearner("kitchen", WindowOrientation.EAST)

        # Add only 1 measurement
        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),
            1.2,
            CloudCoverage.CLEAR
        )

        assert learner.get_measurement_count() == 1
        assert learner.get_pattern_count() == 0  # No pattern yet


class TestSeasonalAdjustment:
    """Test seasonal adjustment for sun angle changes."""

    def test_season_detection(self):
        """Test correct season detection from dates."""
        learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)

        # Test winter detection
        assert learner._get_season(datetime(2024, 1, 15, 12, 0)) == Season.WINTER
        assert learner._get_season(datetime(2024, 12, 25, 12, 0)) == Season.WINTER
        assert learner._get_season(datetime(2024, 3, 15, 12, 0)) == Season.WINTER

        # Test spring detection
        assert learner._get_season(datetime(2024, 3, 25, 12, 0)) == Season.SPRING
        assert learner._get_season(datetime(2024, 5, 15, 12, 0)) == Season.SPRING

        # Test summer detection
        assert learner._get_season(datetime(2024, 6, 25, 12, 0)) == Season.SUMMER
        assert learner._get_season(datetime(2024, 8, 15, 12, 0)) == Season.SUMMER

        # Test fall detection
        assert learner._get_season(datetime(2024, 9, 25, 12, 0)) == Season.FALL
        assert learner._get_season(datetime(2024, 11, 15, 12, 0)) == Season.FALL

    def test_seasonal_adjustment_winter_to_summer(self):
        """Test seasonal adjustment from winter pattern to summer prediction."""
        learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)

        # Learn pattern in winter
        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),  # Winter
            1.0,
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),  # Winter
            1.0,
            CloudCoverage.CLEAR
        )

        # Predict in summer (higher sun angle, more gain)
        summer_gain = learner.predict_solar_gain(
            datetime(2024, 7, 15, 12, 0),  # Summer
            CloudCoverage.CLEAR
        )

        # Summer should have more gain than winter (sun angle higher)
        # Winter intensity: 0.4, Summer intensity: 1.0
        # Adjustment: 1.0 / 0.4 = 2.5
        # Expected: 1.0 * 2.5 = 2.5°C/hour
        assert summer_gain > 1.0
        assert summer_gain == pytest.approx(2.5, abs=0.1)

    def test_seasonal_adjustment_orientation_impact(self):
        """Test that orientation affects seasonal adjustment magnitude."""
        # South-facing window (most affected by season)
        south_learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)
        south_learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),  # Winter
            1.0,
            CloudCoverage.CLEAR
        )
        south_learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),  # Winter
            1.0,
            CloudCoverage.CLEAR
        )

        # North-facing window (least affected by season)
        north_learner = SolarGainLearner("bedroom", WindowOrientation.NORTH)
        north_learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),  # Winter
            0.3,
            CloudCoverage.CLEAR
        )
        north_learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),  # Winter
            0.3,
            CloudCoverage.CLEAR
        )

        # Predict in summer for both
        south_summer = south_learner.predict_solar_gain(
            datetime(2024, 7, 15, 12, 0),
            CloudCoverage.CLEAR
        )
        north_summer = north_learner.predict_solar_gain(
            datetime(2024, 7, 15, 12, 0),
            CloudCoverage.CLEAR
        )

        # South should have larger seasonal change than north
        south_change_ratio = south_summer / 1.0
        north_change_ratio = north_summer / 0.3

        assert south_change_ratio > north_change_ratio


class TestCloudForecastIntegration:
    """Test cloud forecast integration."""

    def test_cloud_coverage_adjustment(self):
        """Test adjustment for different cloud coverage levels."""
        learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)

        # Learn pattern under clear skies
        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),
            2.0,  # 2.0°C/hour under clear skies
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),
            2.0,
            CloudCoverage.CLEAR
        )

        # Predict under different cloud conditions
        clear_gain = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.CLEAR
        )
        partly_cloudy_gain = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.PARTLY_CLOUDY
        )
        cloudy_gain = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.CLOUDY
        )
        overcast_gain = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.OVERCAST
        )

        # Verify decreasing gain with increasing cloud coverage
        assert clear_gain == pytest.approx(2.0, abs=0.01)
        assert partly_cloudy_gain == pytest.approx(1.4, abs=0.1)  # 70% of clear
        assert cloudy_gain == pytest.approx(0.8, abs=0.1)         # 40% of clear
        assert overcast_gain == pytest.approx(0.2, abs=0.1)       # 10% of clear

        assert clear_gain > partly_cloudy_gain > cloudy_gain > overcast_gain

    def test_learn_patterns_for_different_cloud_conditions(self):
        """Test learning separate patterns for different cloud conditions."""
        learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)

        # Learn pattern for clear skies
        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),
            2.0,
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),
            2.0,
            CloudCoverage.CLEAR
        )

        # Learn pattern for cloudy skies
        learner.add_measurement(
            datetime(2024, 1, 16, 12, 0),
            0.8,
            CloudCoverage.CLOUDY
        )
        learner.add_measurement(
            datetime(2024, 1, 21, 12, 0),
            0.8,
            CloudCoverage.CLOUDY
        )

        assert learner.get_pattern_count() == 2

        # Predict with exact match (no adjustment needed)
        clear_prediction = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.CLEAR
        )
        cloudy_prediction = learner.predict_solar_gain(
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.CLOUDY
        )

        assert clear_prediction == pytest.approx(2.0, abs=0.01)
        assert cloudy_prediction == pytest.approx(0.8, abs=0.01)

    def test_fallback_when_no_pattern_learned(self):
        """Test fallback value when no pattern learned."""
        learner = SolarGainLearner("bedroom", WindowOrientation.NORTH)

        # No measurements added

        # Predict with fallback
        prediction = learner.predict_solar_gain(
            datetime(2024, 1, 15, 12, 0),
            CloudCoverage.CLEAR,
            fallback_gain_c_per_hour=0.5
        )

        assert prediction == 0.5

    def test_clear_measurements(self):
        """Test clearing measurements and patterns."""
        learner = SolarGainLearner("living_room", WindowOrientation.SOUTH)

        learner.add_measurement(
            datetime(2024, 1, 15, 12, 0),
            1.5,
            CloudCoverage.CLEAR
        )
        learner.add_measurement(
            datetime(2024, 1, 20, 12, 0),
            1.3,
            CloudCoverage.CLEAR
        )

        assert learner.get_measurement_count() == 2
        assert learner.get_pattern_count() == 1

        learner.clear_measurements()

        assert learner.get_measurement_count() == 0
        assert learner.get_pattern_count() == 0


class TestSolarGainManager:
    """Test solar gain manager for multiple zones."""

    def test_configure_zone(self):
        """Test configuring zones with different orientations."""
        manager = SolarGainManager()

        manager.configure_zone("living_room", WindowOrientation.SOUTH)
        manager.configure_zone("bedroom", WindowOrientation.NORTH)

        assert manager.get_learner("living_room") is not None
        assert manager.get_learner("bedroom") is not None
        assert manager.get_learner("kitchen") is None

    def test_add_measurement_to_zone(self):
        """Test adding measurements to specific zones."""
        manager = SolarGainManager()

        manager.configure_zone("living_room", WindowOrientation.SOUTH)

        manager.add_measurement(
            "living_room",
            datetime(2024, 1, 15, 12, 0),
            1.5,
            CloudCoverage.CLEAR
        )
        manager.add_measurement(
            "living_room",
            datetime(2024, 1, 20, 12, 0),
            1.3,
            CloudCoverage.CLEAR
        )

        learner = manager.get_learner("living_room")
        assert learner.get_measurement_count() == 2

    def test_predict_for_zone(self):
        """Test predicting solar gain for a specific zone."""
        manager = SolarGainManager()

        manager.configure_zone("living_room", WindowOrientation.SOUTH)

        manager.add_measurement(
            "living_room",
            datetime(2024, 1, 15, 12, 0),
            1.5,
            CloudCoverage.CLEAR
        )
        manager.add_measurement(
            "living_room",
            datetime(2024, 1, 20, 12, 0),
            1.3,
            CloudCoverage.CLEAR
        )

        prediction = manager.predict_solar_gain(
            "living_room",
            datetime(2024, 1, 25, 12, 0),
            CloudCoverage.CLEAR
        )

        assert prediction == pytest.approx(1.4, abs=0.01)

    def test_unconfigured_zone_uses_fallback(self):
        """Test that unconfigured zones use fallback value."""
        manager = SolarGainManager()

        # Don't configure any zones

        prediction = manager.predict_solar_gain(
            "kitchen",
            datetime(2024, 1, 15, 12, 0),
            CloudCoverage.CLEAR,
            fallback_gain_c_per_hour=0.3
        )

        assert prediction == 0.3
