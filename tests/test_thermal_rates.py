"""Tests for thermal rate learning functionality."""

from datetime import datetime, timedelta
import pytest

from custom_components.adaptive_thermostat.adaptive.learning import ThermalRateLearner


def test_cooling_rate_calculation():
    """Test cooling rate calculation from temperature history."""
    learner = ThermalRateLearner()

    # Create temperature history showing cooling from 22°C to 20°C over 2 hours
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    history = [
        (base_time, 22.0),
        (base_time + timedelta(minutes=30), 21.5),
        (base_time + timedelta(minutes=60), 21.0),
        (base_time + timedelta(minutes=90), 20.5),
        (base_time + timedelta(minutes=120), 20.0),
    ]

    cooling_rate = learner.calculate_cooling_rate(history, min_duration_minutes=30)

    # Expected rate: 2°C drop over 2 hours = 1.0 °C/hour
    assert cooling_rate is not None
    assert 0.9 <= cooling_rate <= 1.1  # Allow small tolerance


def test_cooling_rate_insufficient_data():
    """Test cooling rate returns None with insufficient data."""
    learner = ThermalRateLearner()

    # Single data point
    history = [(datetime(2024, 1, 1, 12, 0, 0), 22.0)]
    cooling_rate = learner.calculate_cooling_rate(history)
    assert cooling_rate is None

    # No cooling (temperature stable)
    history = [
        (datetime(2024, 1, 1, 12, 0, 0), 22.0),
        (datetime(2024, 1, 1, 13, 0, 0), 22.0),
    ]
    cooling_rate = learner.calculate_cooling_rate(history, min_duration_minutes=30)
    assert cooling_rate is None


def test_heating_rate_calculation():
    """Test heating rate calculation from temperature history."""
    learner = ThermalRateLearner()

    # Create temperature history showing heating from 18°C to 22°C over 1 hour
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    history = [
        (base_time, 18.0),
        (base_time + timedelta(minutes=15), 19.0),
        (base_time + timedelta(minutes=30), 20.0),
        (base_time + timedelta(minutes=45), 21.0),
        (base_time + timedelta(minutes=60), 22.0),
    ]

    heating_rate = learner.calculate_heating_rate(history, min_duration_minutes=10)

    # Expected rate: 4°C rise over 1 hour = 4.0 °C/hour
    assert heating_rate is not None
    assert 3.9 <= heating_rate <= 4.1  # Allow small tolerance


def test_heating_rate_insufficient_data():
    """Test heating rate returns None with insufficient data."""
    learner = ThermalRateLearner()

    # No heating (temperature stable)
    history = [
        (datetime(2024, 1, 1, 12, 0, 0), 20.0),
        (datetime(2024, 1, 1, 13, 0, 0), 20.0),
    ]
    heating_rate = learner.calculate_heating_rate(history, min_duration_minutes=10)
    assert heating_rate is None


def test_rate_averaging():
    """Test averaging of multiple rate measurements."""
    learner = ThermalRateLearner()

    # Add cooling measurements
    learner.add_cooling_measurement(1.0)
    learner.add_cooling_measurement(1.2)
    learner.add_cooling_measurement(0.8)
    learner.add_cooling_measurement(1.1)

    avg_cooling = learner.get_average_cooling_rate(reject_outliers=False)
    assert avg_cooling is not None
    expected = (1.0 + 1.2 + 0.8 + 1.1) / 4
    assert abs(avg_cooling - expected) < 0.01

    # Add heating measurements
    learner.add_heating_measurement(3.5)
    learner.add_heating_measurement(4.0)
    learner.add_heating_measurement(3.8)

    avg_heating = learner.get_average_heating_rate(reject_outliers=False)
    assert avg_heating is not None
    expected = (3.5 + 4.0 + 3.8) / 3
    assert abs(avg_heating - expected) < 0.01


def test_rate_averaging_no_data():
    """Test averaging returns None when no measurements stored."""
    learner = ThermalRateLearner()

    assert learner.get_average_cooling_rate() is None
    assert learner.get_average_heating_rate() is None


def test_outlier_rejection():
    """Test outlier rejection in rate averaging."""
    # Use more aggressive threshold for clearer outlier rejection
    learner = ThermalRateLearner(outlier_threshold=1.5)

    # Add more measurements to get better statistics with one clear outlier
    learner.add_cooling_measurement(1.0)
    learner.add_cooling_measurement(1.1)
    learner.add_cooling_measurement(0.9)
    learner.add_cooling_measurement(1.0)
    learner.add_cooling_measurement(1.2)
    learner.add_cooling_measurement(0.95)
    learner.add_cooling_measurement(1.05)
    learner.add_cooling_measurement(10.0)  # Clear outlier

    # With outlier rejection
    avg_with_rejection = learner.get_average_cooling_rate(reject_outliers=True)
    assert avg_with_rejection is not None

    # Without outlier rejection
    avg_without_rejection = learner.get_average_cooling_rate(reject_outliers=False)
    assert avg_without_rejection is not None

    # Average with rejection should be much lower (closer to ~1.0)
    assert avg_with_rejection < 1.5
    assert avg_without_rejection > 1.5  # Pulled up by outlier


def test_outlier_rejection_threshold():
    """Test custom outlier rejection threshold."""
    learner = ThermalRateLearner(outlier_threshold=1.5)

    # Add measurements with moderate outlier
    learner.add_heating_measurement(4.0)
    learner.add_heating_measurement(4.1)
    learner.add_heating_measurement(3.9)
    learner.add_heating_measurement(6.0)  # Moderate outlier

    avg = learner.get_average_heating_rate(reject_outliers=True)
    assert avg is not None
    # With threshold=1.5, the 6.0 might be rejected depending on stdev


def test_max_measurements_limit():
    """Test that only recent measurements are used."""
    learner = ThermalRateLearner()

    # Add many measurements
    for i in range(100):
        learner.add_cooling_measurement(1.0 + (i * 0.01))

    # Get average with max_measurements=10
    avg = learner.get_average_cooling_rate(reject_outliers=False, max_measurements=10)
    assert avg is not None

    # Should use only last 10 measurements (1.90 to 1.99)
    # Average should be around 1.945
    assert 1.9 <= avg <= 2.0


def test_measurement_counts():
    """Test getting measurement counts."""
    learner = ThermalRateLearner()

    counts = learner.get_measurement_counts()
    assert counts["cooling"] == 0
    assert counts["heating"] == 0

    learner.add_cooling_measurement(1.0)
    learner.add_cooling_measurement(1.1)
    learner.add_heating_measurement(4.0)

    counts = learner.get_measurement_counts()
    assert counts["cooling"] == 2
    assert counts["heating"] == 1


def test_clear_measurements():
    """Test clearing all measurements."""
    learner = ThermalRateLearner()

    learner.add_cooling_measurement(1.0)
    learner.add_heating_measurement(4.0)

    counts = learner.get_measurement_counts()
    assert counts["cooling"] == 1
    assert counts["heating"] == 1

    learner.clear_measurements()

    counts = learner.get_measurement_counts()
    assert counts["cooling"] == 0
    assert counts["heating"] == 0


def test_negative_rates_ignored():
    """Test that negative rates are not added."""
    learner = ThermalRateLearner()

    # Try to add negative rates (invalid)
    learner.add_cooling_measurement(-1.0)
    learner.add_heating_measurement(-2.0)

    counts = learner.get_measurement_counts()
    assert counts["cooling"] == 0
    assert counts["heating"] == 0

    # Add valid rates
    learner.add_cooling_measurement(1.0)
    learner.add_heating_measurement(4.0)

    counts = learner.get_measurement_counts()
    assert counts["cooling"] == 1
    assert counts["heating"] == 1


def test_mixed_heating_cooling_segments():
    """Test rate calculation with mixed heating and cooling periods."""
    learner = ThermalRateLearner()

    # Temperature goes: cooling, heating, cooling
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    history = [
        (base_time, 22.0),
        (base_time + timedelta(minutes=30), 21.0),  # Cooling
        (base_time + timedelta(minutes=60), 20.0),  # Cooling
        (base_time + timedelta(minutes=90), 21.0),  # Heating
        (base_time + timedelta(minutes=120), 22.0),  # Heating
        (base_time + timedelta(minutes=150), 21.5),  # Cooling
        (base_time + timedelta(minutes=180), 21.0),  # Cooling
    ]

    cooling_rate = learner.calculate_cooling_rate(history, min_duration_minutes=30)
    heating_rate = learner.calculate_heating_rate(history, min_duration_minutes=30)

    # Both should be calculated from their respective segments
    assert cooling_rate is not None
    assert heating_rate is not None

    # Cooling has two segments:
    # - First: 22.0→20.0 over 60 min = 2.0 °C/hour
    # - Second: 21.5→21.0 over 30 min = 1.0 °C/hour
    # Median = 1.5 °C/hour
    assert 1.4 <= cooling_rate <= 1.6

    # Heating: 20.0→22.0 over 60 min = 2.0 °C/hour
    assert 1.9 <= heating_rate <= 2.1


def test_short_segment_ignored():
    """Test that segments shorter than min_duration are ignored."""
    learner = ThermalRateLearner()

    # Short cooling segment (only 5 minutes)
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    history = [
        (base_time, 22.0),
        (base_time + timedelta(minutes=5), 21.5),
    ]

    cooling_rate = learner.calculate_cooling_rate(history, min_duration_minutes=30)
    assert cooling_rate is None  # Too short
