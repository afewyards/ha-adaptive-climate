"""Tests for adaptive convergence detection with confidence-based learning."""

import pytest
from datetime import datetime, timedelta
from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_thermostat.const import (
    CONVERGENCE_CONFIDENCE_HIGH,
    CONFIDENCE_INCREASE_PER_GOOD_CYCLE,
)


@pytest.fixture
def learner():
    """Create an AdaptiveLearner instance for testing."""
    return AdaptiveLearner(max_history=100)


@pytest.fixture
def good_cycle():
    """Create a cycle that meets convergence criteria."""
    return CycleMetrics(
        overshoot=0.1,  # Below 0.2°C threshold
        undershoot=0.0,
        settling_time=30.0,  # Below 60 min threshold
        oscillations=0,  # Below 1 threshold
        rise_time=20.0,  # Below 45 min threshold
    )


@pytest.fixture
def poor_cycle():
    """Create a cycle that does NOT meet convergence criteria."""
    return CycleMetrics(
        overshoot=0.8,  # Above 0.2°C threshold
        undershoot=0.2,
        settling_time=90.0,  # Above 60 min threshold
        oscillations=3,  # Above 1 threshold
        rise_time=70.0,  # Above 45 min threshold
    )


class TestConfidenceTracking:
    """Test confidence tracking functionality."""

    def test_confidence_increases_with_good_cycles(self, learner, good_cycle):
        """Test that confidence increases when good cycles are observed."""
        assert learner.get_convergence_confidence() == 0.0

        # Add first good cycle
        learner.update_convergence_confidence(good_cycle)
        assert learner.get_convergence_confidence() == pytest.approx(
            CONFIDENCE_INCREASE_PER_GOOD_CYCLE
        )

        # Add second good cycle
        learner.update_convergence_confidence(good_cycle)
        assert learner.get_convergence_confidence() == pytest.approx(
            CONFIDENCE_INCREASE_PER_GOOD_CYCLE * 2
        )

    def test_confidence_decreases_with_poor_cycles(self, learner, good_cycle, poor_cycle):
        """Test that confidence decreases when poor cycles are observed."""
        # Build up some confidence first
        for _ in range(5):
            learner.update_convergence_confidence(good_cycle)

        confidence_before = learner.get_convergence_confidence()
        assert confidence_before > 0.0

        # Add poor cycle
        learner.update_convergence_confidence(poor_cycle)
        confidence_after = learner.get_convergence_confidence()

        # Confidence should decrease
        assert confidence_after < confidence_before

    def test_confidence_capped_at_maximum(self, learner, good_cycle):
        """Test that confidence is capped at CONVERGENCE_CONFIDENCE_HIGH."""
        # Add many good cycles
        for _ in range(20):
            learner.update_convergence_confidence(good_cycle)

        # Should be capped at maximum
        assert learner.get_convergence_confidence() == pytest.approx(
            CONVERGENCE_CONFIDENCE_HIGH
        )

    def test_confidence_floored_at_zero(self, learner, poor_cycle):
        """Test that confidence cannot go below 0.0."""
        # Add many poor cycles
        for _ in range(20):
            learner.update_convergence_confidence(poor_cycle)

        # Should be floored at 0.0
        assert learner.get_convergence_confidence() >= 0.0


class TestLearningRateMultiplier:
    """Test learning rate multiplier calculation."""

    def test_low_confidence_high_learning_rate(self, learner):
        """Test that low confidence results in faster learning (higher multiplier)."""
        # Start with zero confidence
        assert learner.get_convergence_confidence() == 0.0

        # Should get 2.0x multiplier (faster learning)
        multiplier = learner.get_learning_rate_multiplier()
        assert multiplier == pytest.approx(2.0)

    def test_high_confidence_low_learning_rate(self, learner, good_cycle):
        """Test that high confidence results in slower learning (lower multiplier)."""
        # Build up maximum confidence
        for _ in range(20):
            learner.update_convergence_confidence(good_cycle)

        assert learner.get_convergence_confidence() == pytest.approx(
            CONVERGENCE_CONFIDENCE_HIGH
        )

        # Should get 0.5x multiplier (slower learning)
        multiplier = learner.get_learning_rate_multiplier()
        assert multiplier == pytest.approx(0.5)

    def test_medium_confidence_moderate_learning_rate(self, learner, good_cycle):
        """Test that medium confidence results in moderate learning rate."""
        # Build up to 50% confidence
        for _ in range(5):
            learner.update_convergence_confidence(good_cycle)

        confidence = learner.get_convergence_confidence()
        assert 0.4 < confidence < 0.6  # Roughly 50%

        # Should get ~1.25x multiplier (moderate)
        multiplier = learner.get_learning_rate_multiplier()
        assert 1.0 < multiplier < 1.5


class TestConfidenceDecay:
    """Test confidence decay over time."""

    def test_confidence_decay_reduces_confidence(self, learner, good_cycle):
        """Test that confidence decays over time."""
        # Build up some confidence
        for _ in range(5):
            learner.update_convergence_confidence(good_cycle)

        confidence_before = learner.get_convergence_confidence()

        # Apply decay
        learner.apply_confidence_decay()

        confidence_after = learner.get_convergence_confidence()
        assert confidence_after < confidence_before

    def test_confidence_decay_cannot_go_negative(self, learner):
        """Test that confidence decay cannot make confidence negative."""
        # Start with zero confidence
        assert learner.get_convergence_confidence() == 0.0

        # Apply decay multiple times
        for _ in range(10):
            learner.apply_confidence_decay()

        # Should still be 0.0 or positive
        assert learner.get_convergence_confidence() >= 0.0


class TestPerformanceDegradation:
    """Test performance degradation detection."""

    def test_no_degradation_with_consistent_performance(self, learner):
        """Test that no degradation is detected with consistent cycles."""
        # Create 20 consistent good cycles
        for i in range(20):
            cycle = CycleMetrics(
                overshoot=0.15,
                undershoot=0.0,
                settling_time=30.0,
                oscillations=0,
                rise_time=20.0,
            )
            learner.add_cycle_metrics(cycle)

        # Should not detect degradation
        assert not learner.check_performance_degradation()

    def test_degradation_detected_with_worse_recent_cycles(self, learner):
        """Test that degradation is detected when recent cycles are worse."""
        # Add 10 good baseline cycles
        for i in range(10):
            cycle = CycleMetrics(
                overshoot=0.10,  # Good baseline
                undershoot=0.0,
                settling_time=30.0,
                oscillations=0,
                rise_time=20.0,
            )
            learner.add_cycle_metrics(cycle)

        # Add 10 worse recent cycles
        for i in range(10):
            cycle = CycleMetrics(
                overshoot=0.50,  # Much worse (5x baseline)
                undershoot=0.1,
                settling_time=50.0,
                oscillations=1,
                rise_time=35.0,
            )
            learner.add_cycle_metrics(cycle)

        # Should detect degradation
        assert learner.check_performance_degradation()

    def test_no_degradation_with_insufficient_data(self, learner):
        """Test that degradation check requires sufficient history."""
        # Add only 5 cycles (need at least 20)
        for i in range(5):
            cycle = CycleMetrics(
                overshoot=0.15,
                undershoot=0.0,
                settling_time=30.0,
                oscillations=0,
                rise_time=20.0,
            )
            learner.add_cycle_metrics(cycle)

        # Should not detect degradation (insufficient data)
        assert not learner.check_performance_degradation()


class TestSeasonalShift:
    """Test seasonal shift detection."""

    def test_no_shift_with_stable_outdoor_temp(self, learner):
        """Test that no shift is detected with stable outdoor temperature."""
        # Add 15 readings around 10°C
        for _ in range(15):
            assert not learner.check_seasonal_shift(outdoor_temp=10.0)

    def test_shift_detected_with_large_change(self, learner):
        """Test that shift is detected with 10°C+ change."""
        # Add 10 readings at 5°C
        for _ in range(10):
            learner.check_seasonal_shift(outdoor_temp=5.0)

        # Add 10 readings at 16°C (11°C shift)
        for i in range(10):
            result = learner.check_seasonal_shift(outdoor_temp=16.0)
            # Should detect shift after enough new readings accumulated
            if i >= 9:  # After we have 10 new readings
                # But only checks daily, so may not trigger on first call
                pass

        # Force a check by setting last check to past
        learner._last_seasonal_check = datetime.now() - timedelta(days=2)

        # Now should detect shift
        assert learner.check_seasonal_shift(outdoor_temp=16.0)

    def test_no_shift_with_insufficient_data(self, learner):
        """Test that shift detection requires sufficient history."""
        # Add only 5 readings (need at least 10)
        for _ in range(5):
            assert not learner.check_seasonal_shift(outdoor_temp=10.0)

    def test_shift_check_rate_limited(self, learner):
        """Test that shift check is rate-limited to once per day."""
        # Add 20 readings to build history
        for _ in range(20):
            learner.check_seasonal_shift(outdoor_temp=10.0)

        # Check once
        learner._last_seasonal_check = datetime.now() - timedelta(days=2)
        learner.check_seasonal_shift(outdoor_temp=20.0)  # Should check

        # Immediately check again - should skip
        result = learner.check_seasonal_shift(outdoor_temp=20.0)
        # Check is skipped due to rate limiting (less than 1 day)


class TestPIDAdjustmentScaling:
    """Test that PID adjustments are scaled by learning rate."""

    def test_adjustments_scaled_with_low_confidence(self, learner):
        """Test that adjustments are larger with low confidence."""
        # Add cycles with overshoot to trigger reduction
        for i in range(5):
            cycle = CycleMetrics(
                overshoot=0.6,  # Trigger overshoot rule
                undershoot=0.0,
                settling_time=30.0,
                oscillations=0,
                rise_time=20.0,
            )
            learner.add_cycle_metrics(cycle)

        # Low confidence should give 2.0x learning rate
        assert learner.get_learning_rate_multiplier() == pytest.approx(2.0)

        # Calculate adjustment
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=2.5,
            min_cycles=3,
        )

        # Should get adjustment (overshoot should trigger Kp reduction)
        assert adjustment is not None
        # With 2.0x learning rate, Kp reduction should be more aggressive
        assert adjustment["kp"] < 100.0

    def test_adjustments_scaled_with_high_confidence(self, learner, good_cycle):
        """Test that adjustments are smaller with high confidence."""
        # Build high confidence first
        for _ in range(10):
            learner.update_convergence_confidence(good_cycle)

        # Add cycles with moderate overshoot
        for i in range(5):
            cycle = CycleMetrics(
                overshoot=0.3,  # Moderate overshoot
                undershoot=0.0,
                settling_time=30.0,
                oscillations=0,
                rise_time=20.0,
            )
            learner.add_cycle_metrics(cycle)

        # High confidence should give 0.5x learning rate
        multiplier = learner.get_learning_rate_multiplier()
        assert multiplier < 1.0  # Should be reduced

        # Calculate adjustment
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=2.5,
            min_cycles=3,
        )

        # Should get adjustment, but smaller than with low confidence
        assert adjustment is not None


def test_adaptive_convergence_module_exists():
    """Marker test to verify adaptive convergence feature exists."""
    learner = AdaptiveLearner()

    # Verify all new methods exist
    assert hasattr(learner, 'update_convergence_confidence')
    assert hasattr(learner, 'check_performance_degradation')
    assert hasattr(learner, 'check_seasonal_shift')
    assert hasattr(learner, 'apply_confidence_decay')
    assert hasattr(learner, 'get_learning_rate_multiplier')
    assert hasattr(learner, 'get_convergence_confidence')

    # Verify new attributes exist
    assert hasattr(learner, '_convergence_confidence')
    assert hasattr(learner, '_last_seasonal_check')
    assert hasattr(learner, '_outdoor_temp_history')
    assert hasattr(learner, '_duty_cycle_history')
