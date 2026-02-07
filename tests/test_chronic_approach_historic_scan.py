"""Tests for chronic approach historic scan functionality."""

import pytest
from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics


class TestChronicApproachHistoricScan:
    """Test suite for chronic approach historic scan."""

    def test_historic_scan_disabled_by_default(self):
        """Test that historic scan is disabled by default.

        This simulates upgrading from a version without the detector,
        where cycle history exists but detector state doesn't.
        """
        learner = AdaptiveLearner(heating_type="radiator")

        # Add cycles
        for _ in range(3):
            cycle = CycleMetrics(
                rise_time=None,
                undershoot=0.4,
                overshoot=None,
                settling_time=30,
                oscillations=0,
            )
            learner.add_cycle_metrics(cycle)

        # Simulate old format without detector state
        state = learner.to_dict()
        # Remove detector state to simulate pre-detector version
        if "undershoot_detector" in state:
            del state["undershoot_detector"]

        # Create new learner without historic scan
        learner2 = AdaptiveLearner(heating_type="radiator")
        learner2.restore_from_dict(state)

        # Detector should be fresh (no consecutive failures)
        # because historic scan is disabled
        assert learner2._undershoot_detector._consecutive_failures == 0
        assert not learner2._undershoot_detector.should_adjust_ki(cycles_completed=3)

    def test_historic_scan_enabled(self):
        """Test that historic scan processes cycles when enabled.

        This simulates upgrading from a version without the detector,
        where cycle history exists but detector state doesn't.
        """
        learner = AdaptiveLearner(heating_type="radiator")

        # Add 6 consecutive chronic approach failures (need MIN_CYCLES_FOR_LEARNING + 3 for radiator threshold)
        for _ in range(6):
            cycle = CycleMetrics(
                rise_time=None,
                undershoot=0.4,  # Above 0.35 threshold
                overshoot=None,
                settling_time=30,
                oscillations=0,
            )
            learner.add_cycle_metrics(cycle)

        # Simulate old format without detector state
        state = learner.to_dict()
        if "undershoot_detector" in state:
            del state["undershoot_detector"]

        # Restore with historic scan enabled
        learner2 = AdaptiveLearner(heating_type="radiator", chronic_approach_historic_scan=True)
        learner2.restore_from_dict(state)

        # Should have detected pattern via historic scan
        assert learner2._undershoot_detector._consecutive_failures == 6
        assert learner2._undershoot_detector.should_adjust_ki(cycles_completed=6)

    def test_historic_scan_no_pattern_detected(self):
        """Test historic scan with cycles that don't match pattern."""
        learner = AdaptiveLearner(heating_type="radiator")

        # Add cycles with rise_time (not failures)
        for _ in range(3):
            cycle = CycleMetrics(
                rise_time=20,  # Has rise time = not a failure
                undershoot=0.1,
                overshoot=0.2,
                settling_time=30,
                oscillations=0,
            )
            learner.add_cycle_metrics(cycle)

        # Simulate old format without detector state
        state = learner.to_dict()
        if "undershoot_detector" in state:
            del state["undershoot_detector"]

        # Restore with historic scan enabled
        learner2 = AdaptiveLearner(heating_type="radiator", chronic_approach_historic_scan=True)
        learner2.restore_from_dict(state)

        # Should not have detected pattern (cycles have rise_time)
        assert learner2._undershoot_detector._consecutive_failures == 0
        assert not learner2._undershoot_detector.should_adjust_ki(cycles_completed=3)

    def test_historic_scan_with_empty_history(self):
        """Test historic scan with no cycle history."""
        learner = AdaptiveLearner(heating_type="radiator")

        # No cycles added
        state = learner.to_dict()
        if "undershoot_detector" in state:
            del state["undershoot_detector"]

        learner2 = AdaptiveLearner(heating_type="radiator", chronic_approach_historic_scan=True)
        learner2.restore_from_dict(state)

        # Should not crash and should not detect pattern
        assert learner2._undershoot_detector._consecutive_failures == 0
        assert not learner2._undershoot_detector.should_adjust_ki(cycles_completed=0)

    def test_historic_scan_respects_cooldown(self):
        """Test that historic scan respects cooldown from previous adjustment."""
        learner = AdaptiveLearner(heating_type="radiator", chronic_approach_historic_scan=True)

        # Add 3 consecutive failures
        for _ in range(3):
            cycle = CycleMetrics(
                rise_time=None,
                undershoot=0.4,
                overshoot=None,
                settling_time=30,
                oscillations=0,
            )
            learner.add_cycle_metrics(cycle)

        # Manually trigger adjustment (simulating previous adjustment)
        detector = learner._undershoot_detector
        detector.apply_adjustment()

        # Restore from dict (should trigger scan but be in cooldown)
        state = learner.to_dict()

        # Restore detector state with cooldown active
        learner2 = AdaptiveLearner(heating_type="radiator", chronic_approach_historic_scan=True)
        learner2.restore_from_dict(state)
        learner2._undershoot_detector.last_adjustment_time = detector.last_adjustment_time
        learner2._undershoot_detector.cumulative_ki_multiplier = detector.cumulative_ki_multiplier

        # Re-add cycles to detector after restoration
        for cycle in learner2._heating_cycle_history:
            learner2._undershoot_detector.add_cycle(cycle)

        # Should not recommend adjustment due to cooldown
        assert learner2._undershoot_detector._in_cooldown()
        assert not learner2._undershoot_detector.should_adjust_ki(cycles_completed=3)

    def test_historic_scan_respects_cap(self):
        """Test that historic scan respects cumulative Ki multiplier cap."""
        learner = AdaptiveLearner(heating_type="radiator", chronic_approach_historic_scan=True)

        # Set cumulative multiplier near cap
        learner._undershoot_detector.cumulative_ki_multiplier = 1.95  # Near 2.0 cap

        # Add 3 consecutive failures
        for _ in range(3):
            cycle = CycleMetrics(
                rise_time=None,
                undershoot=0.4,
                overshoot=None,
                settling_time=30,
                oscillations=0,
            )
            learner.add_cycle_metrics(cycle)

        # Restore from dict (should trigger scan)
        state = learner.to_dict()
        learner2 = AdaptiveLearner(heating_type="radiator", chronic_approach_historic_scan=True)
        learner2._undershoot_detector.cumulative_ki_multiplier = 1.95
        learner2.restore_from_dict(state)

        # Should not recommend adjustment due to cap
        assert not learner2._undershoot_detector.should_adjust_ki(cycles_completed=3)

    def test_historic_scan_multiple_heating_types(self):
        """Test historic scan with different heating types and their thresholds."""
        # Floor hydronic: 6 cycles minimum for learning, 4 consecutive failures threshold
        learner_floor = AdaptiveLearner(heating_type="floor_hydronic")
        for _ in range(6):
            cycle = CycleMetrics(
                rise_time=None,
                undershoot=0.5,
                overshoot=None,
                settling_time=60,
                oscillations=0,
            )
            learner_floor.add_cycle_metrics(cycle)

        state_floor = learner_floor.to_dict()
        if "undershoot_detector" in state_floor:
            del state_floor["undershoot_detector"]

        learner_floor2 = AdaptiveLearner(heating_type="floor_hydronic", chronic_approach_historic_scan=True)
        learner_floor2.restore_from_dict(state_floor)
        assert learner_floor2._undershoot_detector._consecutive_failures == 6
        assert learner_floor2._undershoot_detector.should_adjust_ki(cycles_completed=6)

        # Forced air: 6 cycles minimum for learning, 2 consecutive failures threshold
        learner_forced = AdaptiveLearner(heating_type="forced_air")
        for _ in range(6):
            cycle = CycleMetrics(
                rise_time=None,
                undershoot=0.3,
                overshoot=None,
                settling_time=15,
                oscillations=0,
            )
            learner_forced.add_cycle_metrics(cycle)

        state_forced = learner_forced.to_dict()
        if "undershoot_detector" in state_forced:
            del state_forced["undershoot_detector"]

        learner_forced2 = AdaptiveLearner(heating_type="forced_air", chronic_approach_historic_scan=True)
        learner_forced2.restore_from_dict(state_forced)
        assert learner_forced2._undershoot_detector._consecutive_failures == 6
        assert learner_forced2._undershoot_detector.should_adjust_ki(cycles_completed=6)
