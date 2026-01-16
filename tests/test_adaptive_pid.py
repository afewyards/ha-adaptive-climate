"""Tests for adaptive PID adjustments."""

import pytest

from custom_components.adaptive_thermostat.adaptive.learning import (
    AdaptiveLearner,
    CycleMetrics,
)
from custom_components.adaptive_thermostat.const import MIN_CYCLES_FOR_LEARNING


def test_high_overshoot_reducing_kp():
    """Test that moderate overshoot increases Kd (v0.7.0: thermal lag addressed by derivative)."""
    learner = AdaptiveLearner()

    # Add cycles with moderate overshoot (0.6°C, triggers MODERATE_OVERSHOOT rule)
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.6,
            undershoot=0.0,
            settling_time=45.0,
            oscillations=0,
            rise_time=30.0,
        )
        learner.add_cycle_metrics(metrics)

    # Initial PID values (v0.7.0: kd_max reduced to 5.0)
    kp, ki, kd = 100.0, 20.0, 2.0

    # Calculate adjustments
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # v0.7.0: Moderate overshoot (0.2-1.0°C) increases Kd only (thermal lag)
    assert result["kp"] == kp  # Kp unchanged
    assert result["ki"] == ki  # Ki unchanged
    assert result["kd"] > kd   # Kd increased to dampen overshoot (2.0 * 1.20 * learning_rate)


def test_slow_response_increasing_kp():
    """Test that slow response increases Kp."""
    learner = AdaptiveLearner()

    # Add cycles with slow rise time (70 minutes)
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.1,
            undershoot=0.0,
            settling_time=80.0,
            oscillations=0,
            rise_time=70.0,  # Slow response
        )
        learner.add_cycle_metrics(metrics)

    # Initial PID values
    kp, ki, kd = 100.0, 20.0, 10.0

    # Calculate adjustments
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Slow rise time should increase Kp
    assert result["kp"] > kp


def test_oscillations_adjusting_kp_and_kd():
    """Test that oscillations reduce Kp and increase Kd."""
    learner = AdaptiveLearner()

    # Add cycles with many oscillations (4 oscillations)
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.3,
            undershoot=0.1,
            settling_time=60.0,
            oscillations=4,  # Many oscillations
            rise_time=40.0,
        )
        learner.add_cycle_metrics(metrics)

    # Initial PID values (v0.7.0: kd_max reduced to 5.0)
    kp, ki, kd = 100.0, 20.0, 2.0

    # Calculate adjustments
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Many oscillations should reduce Kp by 10%
    assert result["kp"] < kp
    # Many oscillations should increase Kd by 20%
    assert result["kd"] > kd


def test_pid_limits_enforcement():
    """Test that PID values are constrained within limits."""
    learner = AdaptiveLearner()

    # Add cycles with extreme metrics to push beyond limits
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=2.0,  # Very high overshoot
            undershoot=0.0,
            settling_time=45.0,
            oscillations=0,
            rise_time=30.0,
        )
        learner.add_cycle_metrics(metrics)

    # Start with values near minimum (v0.7.0: kd_max = 5.0)
    kp, ki, kd = 15.0, 5.0, 2.0

    # Calculate adjustments (high overshoot will try to reduce Kp/Ki and increase Kd)
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Kp should not go below minimum (10.0)
    assert result["kp"] >= 10.0
    # Ki should not go below minimum (0.0)
    assert result["ki"] >= 0.0
    # Kd should not go below minimum (0.0)
    assert result["kd"] >= 0.0

    # Test maximum limits (v0.7.0: Kd limits changed to 0.0-5.0)
    learner2 = AdaptiveLearner()

    # Add cycles with slow response to increase Kp
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.0,
            undershoot=0.5,  # High undershoot to increase Ki
            settling_time=100.0,  # Slow settling to increase Kd
            oscillations=0,
            rise_time=90.0,  # Very slow response to increase Kp
        )
        learner2.add_cycle_metrics(metrics)

    # Start with values near maximum (v0.7.0: kd_max = 5.0)
    kp, ki, kd = 480.0, 95.0, 4.5

    # Calculate adjustments
    result = learner2.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Kp should not exceed maximum (500.0)
    assert result["kp"] <= 500.0
    # Ki should not exceed maximum (1000.0) - v0.7.0 increased from 100.0
    assert result["ki"] <= 1000.0
    # Kd should not exceed maximum (5.0) - v0.7.0 reduced from 200.0
    assert result["kd"] <= 5.0


def test_minimum_cycles_requirement():
    """Test that insufficient cycles return None."""
    learner = AdaptiveLearner()

    # Add only MIN_CYCLES_FOR_LEARNING - 1 cycles (less than minimum)
    for _ in range(MIN_CYCLES_FOR_LEARNING - 1):
        metrics = CycleMetrics(
            overshoot=0.3,
            undershoot=0.1,
            settling_time=45.0,
            oscillations=1,
            rise_time=30.0,
        )
        learner.add_cycle_metrics(metrics)

    # Initial PID values
    kp, ki, kd = 100.0, 20.0, 10.0

    # Should return None due to insufficient cycles
    result = learner.calculate_pid_adjustment(kp, ki, kd)
    assert result is None

    # Add one more cycle to reach minimum
    metrics = CycleMetrics(
        overshoot=0.3,
        undershoot=0.1,
        settling_time=45.0,
        oscillations=1,
        rise_time=30.0,
    )
    learner.add_cycle_metrics(metrics)

    # Now should return a result
    result = learner.calculate_pid_adjustment(kp, ki, kd)
    assert result is not None
    assert "kp" in result
    assert "ki" in result
    assert "kd" in result


def test_undershoot_increases_ki():
    """Test that undershoot increases Ki."""
    learner = AdaptiveLearner()

    # Add cycles with undershoot (0.4°C)
    # Note: rise_time > 45 to avoid triggering convergence check
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.0,
            undershoot=0.4,  # Significant undershoot
            settling_time=50.0,
            oscillations=0,
            rise_time=50.0,  # Above convergence threshold
        )
        learner.add_cycle_metrics(metrics)

    # Initial PID values
    kp, ki, kd = 100.0, 20.0, 10.0

    # Calculate adjustments
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Undershoot should increase Ki
    assert result["ki"] > ki


def test_slow_settling_increases_kd():
    """Test that slow settling time increases Kd."""
    learner = AdaptiveLearner()

    # Add cycles with slow settling (100 minutes)
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.1,
            undershoot=0.0,
            settling_time=100.0,  # Very slow settling
            oscillations=0,
            rise_time=30.0,
        )
        learner.add_cycle_metrics(metrics)

    # Initial PID values (v0.7.0: kd_max = 5.0)
    kp, ki, kd = 100.0, 20.0, 2.0

    # Calculate adjustments
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Slow settling should increase Kd
    assert result["kd"] > kd


def test_cycle_count():
    """Test cycle counting functionality."""
    learner = AdaptiveLearner()

    assert learner.get_cycle_count() == 0

    # Add cycles
    for i in range(5):
        metrics = CycleMetrics(
            overshoot=0.1,
            undershoot=0.1,
            settling_time=45.0,
            oscillations=0,
            rise_time=30.0,
        )
        learner.add_cycle_metrics(metrics)
        assert learner.get_cycle_count() == i + 1

    # Clear history
    learner.clear_history()
    assert learner.get_cycle_count() == 0
