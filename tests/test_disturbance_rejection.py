"""Tests for disturbance rejection in adaptive learning."""
import pytest
from datetime import datetime, timedelta
from custom_components.adaptive_thermostat.adaptive.disturbance_detector import DisturbanceDetector
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics


class TestDisturbanceDetector:
    """Test the DisturbanceDetector class."""

    def test_no_disturbances_normal_cycle(self):
        """Test that normal cycles without disturbances are not flagged."""
        detector = DisturbanceDetector()

        # Normal temperature rise during heating, then gentle settling
        start_time = datetime.now()
        temperature_history = [
            (start_time, 19.0),
            (start_time + timedelta(minutes=10), 19.5),
            (start_time + timedelta(minutes=20), 20.0),
            (start_time + timedelta(minutes=25), 20.1),  # Heater stops, minimal rise
            (start_time + timedelta(minutes=30), 20.1),
            (start_time + timedelta(minutes=40), 20.0),  # Slow cooling
        ]

        heater_active_periods = [
            (start_time, start_time + timedelta(minutes=23))
        ]

        disturbances = detector.detect_disturbances(
            temperature_history=temperature_history,
            heater_active_periods=heater_active_periods,
        )

        assert disturbances == []

    def test_solar_gain_detection(self):
        """Test detection of solar gain disturbance."""
        detector = DisturbanceDetector()

        # Scenario: heating stops, but temperature continues rising due to solar gain
        start_time = datetime.now()
        temperature_history = [
            (start_time, 19.0),
            (start_time + timedelta(minutes=10), 19.5),
            (start_time + timedelta(minutes=20), 20.0),  # Heater stops
            (start_time + timedelta(minutes=30), 20.3),  # Solar gain starts
            (start_time + timedelta(minutes=40), 20.6),  # Continued rise
            (start_time + timedelta(minutes=50), 20.9),  # Still rising
        ]

        heater_active_periods = [
            (start_time, start_time + timedelta(minutes=20))
        ]

        # Solar irradiance increasing
        solar_values = [
            (start_time, 100),
            (start_time + timedelta(minutes=10), 150),
            (start_time + timedelta(minutes=20), 200),
            (start_time + timedelta(minutes=30), 300),
            (start_time + timedelta(minutes=40), 400),
            (start_time + timedelta(minutes=50), 450),
        ]

        disturbances = detector.detect_disturbances(
            temperature_history=temperature_history,
            heater_active_periods=heater_active_periods,
            solar_values=solar_values,
        )

        assert "solar_gain" in disturbances

    def test_wind_loss_detection(self):
        """Test detection of wind-driven heat loss."""
        detector = DisturbanceDetector()

        # Scenario: heating stops, temp drops faster than expected due to wind
        # Need >1.0°C/hour drop rate
        start_time = datetime.now()
        temperature_history = [
            (start_time, 19.0),
            (start_time + timedelta(minutes=10), 19.5),
            (start_time + timedelta(minutes=20), 20.0),  # Heater stops
            (start_time + timedelta(minutes=30), 19.3),  # Rapid drop
            (start_time + timedelta(minutes=40), 18.5),  # Continued rapid drop (1.5°C in 20 min = 4.5°C/h)
        ]

        heater_active_periods = [
            (start_time, start_time + timedelta(minutes=20))
        ]

        # Outdoor temperature stable
        outdoor_temps = [
            (start_time, 5.0),
            (start_time + timedelta(minutes=20), 5.5),
            (start_time + timedelta(minutes=40), 5.2),
        ]

        # High wind speed
        wind_speeds = [
            (start_time, 8.0),
            (start_time + timedelta(minutes=20), 9.0),
            (start_time + timedelta(minutes=40), 8.5),
        ]

        disturbances = detector.detect_disturbances(
            temperature_history=temperature_history,
            heater_active_periods=heater_active_periods,
            outdoor_temps=outdoor_temps,
            wind_speeds=wind_speeds,
        )

        assert "wind_loss" in disturbances

    def test_outdoor_temp_swing_detection(self):
        """Test detection of large outdoor temperature swings."""
        detector = DisturbanceDetector()

        start_time = datetime.now()
        temperature_history = [
            (start_time, 19.0),
            (start_time + timedelta(minutes=20), 20.0),
        ]

        heater_active_periods = [
            (start_time, start_time + timedelta(minutes=15))
        ]

        # Large outdoor temperature swing (>5°C)
        outdoor_temps = [
            (start_time, 5.0),
            (start_time + timedelta(minutes=10), 8.0),
            (start_time + timedelta(minutes=20), 11.0),  # 6°C total swing
        ]

        disturbances = detector.detect_disturbances(
            temperature_history=temperature_history,
            heater_active_periods=heater_active_periods,
            outdoor_temps=outdoor_temps,
        )

        assert "outdoor_temp_swing" in disturbances

    def test_occupancy_detection(self):
        """Test detection of occupancy-driven temperature rise."""
        detector = DisturbanceDetector()

        # Scenario: heating stops, but temp rises due to people/cooking
        start_time = datetime.now()
        temperature_history = [
            (start_time, 19.0),
            (start_time + timedelta(minutes=10), 19.5),
            (start_time + timedelta(minutes=20), 20.0),  # Heater stops
            (start_time + timedelta(minutes=30), 20.2),  # Occupancy gain
            (start_time + timedelta(minutes=40), 20.4),
            (start_time + timedelta(minutes=50), 20.6),  # Still rising
        ]

        heater_active_periods = [
            (start_time, start_time + timedelta(minutes=20))
        ]

        disturbances = detector.detect_disturbances(
            temperature_history=temperature_history,
            heater_active_periods=heater_active_periods,
        )

        assert "occupancy" in disturbances

    def test_multiple_disturbances(self):
        """Test detection of multiple disturbances in one cycle."""
        detector = DisturbanceDetector()

        start_time = datetime.now()
        temperature_history = [
            (start_time, 19.0),
            (start_time + timedelta(minutes=15), 19.8),
            (start_time + timedelta(minutes=20), 20.0),
            (start_time + timedelta(minutes=30), 20.4),  # Rising despite heater off
            (start_time + timedelta(minutes=40), 20.8),
            (start_time + timedelta(minutes=50), 21.2),
        ]

        heater_active_periods = [
            (start_time, start_time + timedelta(minutes=20))
        ]

        # Both solar gain and outdoor temp swing
        solar_values = [
            (start_time, 100),
            (start_time + timedelta(minutes=50), 450),  # Large increase
        ]

        outdoor_temps = [
            (start_time, 5.0),
            (start_time + timedelta(minutes=50), 12.0),  # 7°C swing
        ]

        disturbances = detector.detect_disturbances(
            temperature_history=temperature_history,
            heater_active_periods=heater_active_periods,
            solar_values=solar_values,
            outdoor_temps=outdoor_temps,
        )

        # Should detect both disturbances
        assert len(disturbances) >= 2
        assert "solar_gain" in disturbances or "occupancy" in disturbances
        assert "outdoor_temp_swing" in disturbances


class TestCycleMetricsDisturbances:
    """Test CycleMetrics disturbance tracking."""

    def test_cycle_metrics_is_disturbed_property(self):
        """Test the is_disturbed property."""
        # No disturbances
        metrics = CycleMetrics(
            overshoot=0.5,
            undershoot=0.2,
            disturbances=[]
        )
        assert not metrics.is_disturbed

        # With disturbances
        metrics = CycleMetrics(
            overshoot=0.5,
            undershoot=0.2,
            disturbances=["solar_gain"]
        )
        assert metrics.is_disturbed

    def test_cycle_metrics_disturbances_default_empty(self):
        """Test that disturbances defaults to empty list."""
        metrics = CycleMetrics(overshoot=0.5)
        assert metrics.disturbances == []
        assert not metrics.is_disturbed


class TestDisturbanceFiltering:
    """Test disturbance filtering in adaptive learning."""

    def test_filtering_integration(self):
        """Test that disturbed cycles are filtered from learning.

        This is an integration test showing the pattern:
        1. CycleMetrics created with disturbances
        2. Filtered by is_disturbed property
        3. Only undisturbed cycles used for PID tuning
        """
        # Create mix of disturbed and undisturbed cycles
        cycles = [
            CycleMetrics(overshoot=0.3, disturbances=[]),  # Good
            CycleMetrics(overshoot=0.5, disturbances=[]),  # Good
            CycleMetrics(overshoot=1.5, disturbances=["solar_gain"]),  # Bad - solar
            CycleMetrics(overshoot=0.4, disturbances=[]),  # Good
            CycleMetrics(overshoot=2.0, disturbances=["wind_loss"]),  # Bad - wind
            CycleMetrics(overshoot=0.35, disturbances=[]),  # Good
        ]

        # Filter out disturbed cycles (pattern used in AdaptiveLearner)
        undisturbed_cycles = [c for c in cycles if not c.is_disturbed]

        # Should have 4 good cycles
        assert len(undisturbed_cycles) == 4

        # Calculate average overshoot from undisturbed cycles only
        avg_overshoot = sum(c.overshoot for c in undisturbed_cycles) / len(undisturbed_cycles)

        # Should be around 0.35-0.4 (not affected by 1.5 and 2.0 outliers)
        assert 0.30 <= avg_overshoot <= 0.45


def test_disturbance_rejection_module_exists():
    """Test that disturbance rejection module is importable."""
    from custom_components.adaptive_thermostat.adaptive import disturbance_detector
    assert hasattr(disturbance_detector, 'DisturbanceDetector')
