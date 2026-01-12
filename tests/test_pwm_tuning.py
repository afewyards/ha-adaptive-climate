"""Tests for PWM auto-tuning functionality."""

import pytest
from custom_components.adaptive_thermostat.adaptive.learning import (
    calculate_pwm_adjustment,
    ValveCycleTracker,
)


def test_short_cycling_detection():
    """Test that short cycling is detected and PWM period is increased."""
    # Average cycle time of 5 minutes (short cycling)
    cycle_times = [4.5, 5.0, 5.5, 4.8, 5.2]
    current_pwm = 300.0  # 5 minutes

    result = calculate_pwm_adjustment(
        cycle_times=cycle_times,
        current_pwm_period=current_pwm,
        short_cycle_threshold=10.0,
    )

    # Should increase PWM period
    assert result is not None
    assert result > current_pwm

    # With avg 5 min cycles and 10 min threshold:
    # shortage = 10 - 5 = 5
    # increase_factor = 1.0 + (5/10) * 0.2 = 1.1
    # expected = 300 * 1.1 = 330
    assert result == pytest.approx(330.0, abs=1.0)


def test_acceptable_cycling():
    """Test that acceptable cycle times don't change PWM period."""
    # Average cycle time of 15 minutes (acceptable)
    cycle_times = [14.0, 15.0, 16.0, 15.5, 14.5]
    current_pwm = 300.0  # 5 minutes

    result = calculate_pwm_adjustment(
        cycle_times=cycle_times,
        current_pwm_period=current_pwm,
        short_cycle_threshold=10.0,
    )

    # Should keep current PWM period
    assert result is not None
    assert result == current_pwm


def test_pwm_bounds_enforcement():
    """Test that PWM period stays within min/max bounds."""
    # Very short cycle times
    cycle_times = [1.0, 1.5, 1.2]

    # Test minimum bound
    result = calculate_pwm_adjustment(
        cycle_times=cycle_times,
        current_pwm_period=150.0,
        min_pwm_period=180.0,
        max_pwm_period=1800.0,
        short_cycle_threshold=10.0,
    )

    assert result is not None
    assert result >= 180.0

    # Test maximum bound
    result = calculate_pwm_adjustment(
        cycle_times=cycle_times,
        current_pwm_period=1700.0,
        min_pwm_period=180.0,
        max_pwm_period=1800.0,
        short_cycle_threshold=10.0,
    )

    assert result is not None
    assert result <= 1800.0


def test_insufficient_data():
    """Test that None is returned with insufficient cycle data."""
    # Less than 3 cycles
    cycle_times = [5.0, 6.0]

    result = calculate_pwm_adjustment(
        cycle_times=cycle_times,
        current_pwm_period=300.0,
    )

    assert result is None

    # Empty list
    result = calculate_pwm_adjustment(
        cycle_times=[],
        current_pwm_period=300.0,
    )

    assert result is None


def test_valve_cycle_counting():
    """Test that valve cycles are counted correctly."""
    tracker = ValveCycleTracker()

    # Initial state
    assert tracker.get_cycle_count() == 0

    # First state update (closed)
    tracker.update(False)
    assert tracker.get_cycle_count() == 0

    # Open valve (first cycle)
    tracker.update(True)
    assert tracker.get_cycle_count() == 1

    # Stay open (no new cycle)
    tracker.update(True)
    assert tracker.get_cycle_count() == 1

    # Close valve
    tracker.update(False)
    assert tracker.get_cycle_count() == 1

    # Open valve (second cycle)
    tracker.update(True)
    assert tracker.get_cycle_count() == 2

    # Reset
    tracker.reset()
    assert tracker.get_cycle_count() == 0


def test_valve_cycle_open_first():
    """Test valve cycle counting when starting with open state."""
    tracker = ValveCycleTracker()

    # Start with open valve
    tracker.update(True)
    assert tracker.get_cycle_count() == 0  # No cycle yet

    # Close valve
    tracker.update(False)
    assert tracker.get_cycle_count() == 0

    # Open valve (first cycle)
    tracker.update(True)
    assert tracker.get_cycle_count() == 1


def test_severe_short_cycling():
    """Test PWM adjustment with severe short cycling."""
    # Very short cycle times (2 minutes average)
    cycle_times = [1.5, 2.0, 2.5, 2.0, 1.8]
    current_pwm = 300.0  # 5 minutes

    result = calculate_pwm_adjustment(
        cycle_times=cycle_times,
        current_pwm_period=current_pwm,
        short_cycle_threshold=10.0,
    )

    assert result is not None
    assert result > current_pwm

    # With avg 2 min cycles and 10 min threshold:
    # shortage = 10 - 2 = 8
    # increase_factor = 1.0 + (8/10) * 0.2 = 1.16
    # expected = 300 * 1.16 = 348
    assert result == pytest.approx(348.0, abs=1.0)
