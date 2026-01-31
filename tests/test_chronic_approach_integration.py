"""Integration tests for ChronicApproachDetector with AdaptiveLearner."""
import pytest
from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_climate.const import HeatingType


class TestChronicApproachIntegration:
    """Test that ChronicApproachDetector integrates correctly with AdaptiveLearner."""

    def test_detector_initialized_with_learner(self):
        """Test that detector is initialized when learner is created."""
        learner = AdaptiveLearner(heating_type="radiator")
        assert learner.chronic_approach_detector is not None
        assert learner.chronic_approach_detector.cumulative_ki_multiplier == 1.0

    def test_detector_receives_cycles_from_learner(self):
        """Test that cycles added to learner are fed to detector."""
        learner = AdaptiveLearner(heating_type="radiator")

        # Add a chronic approach failure cycle (no rise_time, high undershoot)
        metrics = CycleMetrics(
            rise_time=None,
            undershoot=1.5,  # Above radiator threshold of 1.0
            overshoot=None,
            settling_time=30.0,
        )

        learner.add_cycle_metrics(metrics)

        # Detector should have tracked this
        assert learner.chronic_approach_detector._consecutive_failures == 1

    def test_detector_triggers_adjustment_after_pattern(self):
        """Test that detector triggers Ki adjustment after pattern detected."""
        learner = AdaptiveLearner(heating_type="radiator")
        current_ki = 0.1

        # Add 3 consecutive chronic approach failures (radiator requires 3)
        for _ in range(3):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=1.5,
                overshoot=None,
                settling_time=30.0,
            )
            learner.add_cycle_metrics(metrics)

        # Should trigger adjustment
        new_ki = learner.check_chronic_approach_adjustment(current_ki)
        assert new_ki is not None
        assert new_ki > current_ki

        # Radiator multiplier is 1.25, so new_ki should be ~0.125
        expected_ki = current_ki * 1.25
        assert abs(new_ki - expected_ki) < 0.001

    def test_detector_resets_on_successful_cycle(self):
        """Test that detector resets when cycle reaches setpoint."""
        learner = AdaptiveLearner(heating_type="radiator")
        current_ki = 0.1

        # Add 2 failures
        for _ in range(2):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=1.5,
                overshoot=None,
                settling_time=30.0,
            )
            learner.add_cycle_metrics(metrics)

        # Add a successful cycle (has rise_time)
        success_metrics = CycleMetrics(
            rise_time=15.0,
            undershoot=0.0,
            overshoot=0.2,
            settling_time=20.0,
        )
        learner.add_cycle_metrics(success_metrics)

        # Should NOT trigger adjustment (reset to 0)
        new_ki = learner.check_chronic_approach_adjustment(current_ki)
        assert new_ki is None
        assert learner.chronic_approach_detector._consecutive_failures == 0

    def test_detector_respects_cooldown(self):
        """Test that detector respects cooldown after adjustment."""
        learner = AdaptiveLearner(heating_type="forced_air")  # 2 cycles, 3h cooldown
        current_ki = 0.1

        # Add 2 consecutive failures to trigger
        for _ in range(2):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=0.6,  # Above forced_air threshold of 0.5
                overshoot=None,
                settling_time=10.0,
            )
            learner.add_cycle_metrics(metrics)

        # Apply first adjustment
        new_ki = learner.check_chronic_approach_adjustment(current_ki)
        assert new_ki is not None

        # Add more failures immediately
        for _ in range(2):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=0.6,
                overshoot=None,
                settling_time=10.0,
            )
            learner.add_cycle_metrics(metrics)

        # Should NOT trigger again (in cooldown)
        new_ki2 = learner.check_chronic_approach_adjustment(new_ki)
        assert new_ki2 is None

    def test_detector_with_none_heating_type(self):
        """Test that detector handles None heating_type gracefully."""
        learner = AdaptiveLearner(heating_type=None)

        # Should default to RADIATOR
        assert learner.chronic_approach_detector is not None
        assert learner.chronic_approach_detector._heating_type == HeatingType.RADIATOR

    def test_detector_with_string_heating_type(self):
        """Test that detector converts string heating_type to enum."""
        learner = AdaptiveLearner(heating_type="floor_hydronic")

        assert learner.chronic_approach_detector is not None
        assert learner.chronic_approach_detector._heating_type == HeatingType.FLOOR_HYDRONIC

    def test_detector_cumulative_multiplier_tracking(self):
        """Test that cumulative multiplier is tracked across adjustments."""
        learner = AdaptiveLearner(heating_type="radiator")
        current_ki = 0.1

        # First adjustment
        for _ in range(3):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=1.5,
                overshoot=None,
                settling_time=30.0,
            )
            learner.add_cycle_metrics(metrics)

        new_ki = learner.check_chronic_approach_adjustment(current_ki)
        assert learner.chronic_approach_detector.cumulative_ki_multiplier == 1.25

        # Wait for cooldown to expire (mock by directly resetting time)
        learner.chronic_approach_detector.last_adjustment_time = None

        # Second adjustment
        for _ in range(3):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=1.5,
                overshoot=None,
                settling_time=30.0,
            )
            learner.add_cycle_metrics(metrics)

        new_ki2 = learner.check_chronic_approach_adjustment(new_ki)
        assert new_ki2 is not None

        # Cumulative should be 1.25 * 1.25 = 1.5625
        expected_cumulative = 1.25 * 1.25
        assert abs(learner.chronic_approach_detector.cumulative_ki_multiplier - expected_cumulative) < 0.001

    def test_confidence_decreases_when_chronic_approach_fires(self):
        """Test that convergence confidence decreases when chronic approach adjustment fires."""
        learner = AdaptiveLearner(heating_type="radiator")
        current_ki = 0.1

        # Set initial confidence to 0.5
        learner._heating_convergence_confidence = 0.5
        initial_confidence = learner.get_convergence_confidence()
        assert initial_confidence == 0.5

        # Add 3 consecutive chronic approach failures (radiator requires 3)
        for _ in range(3):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=1.5,
                overshoot=None,
                settling_time=30.0,
            )
            learner.add_cycle_metrics(metrics)

        # Trigger adjustment - should decrease confidence
        new_ki = learner.check_chronic_approach_adjustment(current_ki)
        assert new_ki is not None

        # Check that confidence decreased
        new_confidence = learner.get_convergence_confidence()
        assert new_confidence < initial_confidence

        # Should decrease by CONFIDENCE_INCREASE_PER_GOOD_CYCLE * 0.5
        # CONFIDENCE_INCREASE_PER_GOOD_CYCLE is 0.1 (10% per good cycle)
        expected_decrease = 0.1 * 0.5  # 0.05
        expected_confidence = max(0.0, initial_confidence - expected_decrease)
        assert abs(new_confidence - expected_confidence) < 0.001

    def test_confidence_mode_specific_decrease(self):
        """Test that confidence decrease is mode-specific."""
        from homeassistant.components.climate import HVACMode

        learner = AdaptiveLearner(heating_type="radiator")
        current_ki = 0.1

        # Set different confidence levels for heating and cooling
        learner._heating_convergence_confidence = 0.6
        learner._cooling_convergence_confidence = 0.8

        # Add 3 consecutive chronic approach failures
        for _ in range(3):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=1.5,
                overshoot=None,
                settling_time=30.0,
            )
            learner.add_cycle_metrics(metrics)

        # Trigger adjustment for HEAT mode
        new_ki = learner.check_chronic_approach_adjustment(current_ki, mode=HVACMode.HEAT)
        assert new_ki is not None

        # Only heating confidence should have decreased
        heating_confidence = learner.get_convergence_confidence(mode=HVACMode.HEAT)
        cooling_confidence = learner.get_convergence_confidence(mode=HVACMode.COOL)

        assert heating_confidence < 0.6  # Decreased from 0.6
        assert cooling_confidence == 0.8  # Unchanged

    def test_confidence_cannot_go_negative(self):
        """Test that confidence is clamped to 0.0 minimum."""
        learner = AdaptiveLearner(heating_type="radiator")
        current_ki = 0.1

        # Set very low initial confidence
        learner._heating_convergence_confidence = 0.01
        initial_confidence = learner.get_convergence_confidence()
        assert initial_confidence == 0.01

        # Add 3 consecutive chronic approach failures
        for _ in range(3):
            metrics = CycleMetrics(
                rise_time=None,
                undershoot=1.5,
                overshoot=None,
                settling_time=30.0,
            )
            learner.add_cycle_metrics(metrics)

        # Trigger adjustment
        new_ki = learner.check_chronic_approach_adjustment(current_ki)
        assert new_ki is not None

        # Confidence should be clamped at 0.0
        new_confidence = learner.get_convergence_confidence()
        assert new_confidence >= 0.0
        assert new_confidence < initial_confidence
