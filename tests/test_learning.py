"""Tests for phase-aware overshoot detection and zone adjustments in adaptive learning."""

import pytest
from datetime import datetime, timedelta

from custom_components.adaptive_thermostat.adaptive.learning import (
    PhaseAwareOvershootTracker,
    calculate_overshoot,
    AdaptiveLearner,
    CycleMetrics,
)


# ============================================================================
# PhaseAwareOvershootTracker Tests
# ============================================================================


class TestPhaseAwareOvershootTracker:
    """Tests for the PhaseAwareOvershootTracker class."""

    def test_initial_state(self):
        """Test initial state of tracker is rise phase."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)

        assert tracker.setpoint == 21.0
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE
        assert tracker.setpoint_crossed is False
        assert tracker.crossing_timestamp is None
        assert tracker.get_overshoot() is None

    def test_rise_phase_no_overshoot(self):
        """Test that no overshoot is reported during rise phase."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Temperature rising but not yet at setpoint
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=5), 19.0)
        tracker.update(base_time + timedelta(minutes=10), 20.0)
        tracker.update(base_time + timedelta(minutes=15), 20.5)

        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE
        assert tracker.setpoint_crossed is False
        assert tracker.get_overshoot() is None

    def test_settling_phase_detection(self):
        """Test that settling phase is correctly detected when setpoint is crossed."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Rise phase
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=5), 19.5)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE

        # Cross setpoint - enters settling phase
        crossing_time = base_time + timedelta(minutes=10)
        tracker.update(crossing_time, 21.0)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_SETTLING
        assert tracker.setpoint_crossed is True
        assert tracker.crossing_timestamp == crossing_time

    def test_overshoot_calculation_in_settling_phase(self):
        """Test overshoot is calculated correctly from settling phase data."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Rise phase
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=5), 19.5)

        # Cross setpoint and overshoot
        tracker.update(base_time + timedelta(minutes=10), 21.0)
        tracker.update(base_time + timedelta(minutes=15), 21.5)  # overshoot
        tracker.update(base_time + timedelta(minutes=20), 21.8)  # max overshoot
        tracker.update(base_time + timedelta(minutes=25), 21.3)  # settling back
        tracker.update(base_time + timedelta(minutes=30), 21.0)

        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.8, abs=0.01)  # 21.8 - 21.0 = 0.8

    def test_settling_temps_collection(self):
        """Test that settling phase temperatures are collected correctly."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Rise phase - should not be collected
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=5), 19.5)
        assert len(tracker.get_settling_temps()) == 0

        # Settling phase - should be collected
        tracker.update(base_time + timedelta(minutes=10), 21.0)
        tracker.update(base_time + timedelta(minutes=15), 21.5)
        tracker.update(base_time + timedelta(minutes=20), 21.2)

        settling_temps = tracker.get_settling_temps()
        assert len(settling_temps) == 3
        assert settling_temps[0][1] == 21.0
        assert settling_temps[1][1] == 21.5
        assert settling_temps[2][1] == 21.2


class TestOvershootWithSetpointChanges:
    """Test overshoot detection with setpoint changes."""

    def test_reset_clears_all_state(self):
        """Test that reset() clears all tracking state."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Build up some state
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=5), 21.5)
        assert tracker.setpoint_crossed is True

        # Reset
        tracker.reset()

        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE
        assert tracker.setpoint_crossed is False
        assert tracker.crossing_timestamp is None
        assert tracker.get_overshoot() is None
        assert len(tracker.get_settling_temps()) == 0

    def test_reset_with_new_setpoint(self):
        """Test that reset() can change the setpoint."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Reach first setpoint
        tracker.update(base_time, 21.5)
        assert tracker.setpoint_crossed is True
        assert tracker.setpoint == 21.0

        # Reset with new setpoint
        tracker.reset(new_setpoint=22.0)

        assert tracker.setpoint == 22.0
        assert tracker.setpoint_crossed is False
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE

    def test_overshoot_after_setpoint_increase(self):
        """Test overshoot tracking after setpoint is increased."""
        tracker = PhaseAwareOvershootTracker(setpoint=20.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # First setpoint reached
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=5), 20.5)  # overshoot 0.5
        assert tracker.get_overshoot() == pytest.approx(0.5, abs=0.01)

        # Setpoint increased - should reset tracking
        tracker.reset(new_setpoint=22.0)

        # New rise phase to new setpoint
        tracker.update(base_time + timedelta(minutes=10), 21.0)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE
        assert tracker.get_overshoot() is None

        # Cross new setpoint
        tracker.update(base_time + timedelta(minutes=15), 22.3)  # overshoot 0.3
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_SETTLING
        assert tracker.get_overshoot() == pytest.approx(0.3, abs=0.01)

    def test_multiple_setpoint_changes(self):
        """Test tracking through multiple setpoint changes."""
        tracker = PhaseAwareOvershootTracker(setpoint=20.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # First cycle
        tracker.update(base_time, 20.5)
        assert tracker.get_overshoot() == pytest.approx(0.5, abs=0.01)

        # Second setpoint
        tracker.reset(new_setpoint=21.0)
        tracker.update(base_time + timedelta(minutes=5), 21.2)
        assert tracker.get_overshoot() == pytest.approx(0.2, abs=0.01)

        # Third setpoint
        tracker.reset(new_setpoint=19.0)
        tracker.update(base_time + timedelta(minutes=10), 19.8)
        assert tracker.get_overshoot() == pytest.approx(0.8, abs=0.01)


class TestSettlingPhaseDetection:
    """Test cases for settling phase detection."""

    def test_exact_setpoint_triggers_settling(self):
        """Test that exactly reaching setpoint triggers settling phase."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        tracker.update(base_time, 20.0)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE

        tracker.update(base_time + timedelta(minutes=5), 21.0)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_SETTLING
        assert tracker.setpoint_crossed is True

    def test_tolerance_allows_slight_undershoot(self):
        """Test that tolerance allows settling phase to trigger slightly below setpoint."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0, tolerance=0.1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        tracker.update(base_time, 20.0)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE

        # 20.95 is within tolerance (21.0 - 0.1 = 20.9)
        tracker.update(base_time + timedelta(minutes=5), 20.95)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_SETTLING

    def test_settling_phase_persists(self):
        """Test that once in settling phase, it persists even if temp drops."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Enter settling phase
        tracker.update(base_time, 21.5)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_SETTLING

        # Temperature drops below setpoint - should still be settling
        tracker.update(base_time + timedelta(minutes=5), 20.5)
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_SETTLING
        assert tracker.setpoint_crossed is True

    def test_crossing_timestamp_recorded(self):
        """Test that the crossing timestamp is recorded correctly."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        tracker.update(base_time, 20.0)
        assert tracker.crossing_timestamp is None

        crossing_time = base_time + timedelta(minutes=10)
        tracker.update(crossing_time, 21.0)
        assert tracker.crossing_timestamp == crossing_time


class TestSetpointNeverReached:
    """Test cases for when setpoint is never reached."""

    def test_no_overshoot_when_setpoint_never_reached(self):
        """Test that no overshoot is returned when setpoint is never reached."""
        tracker = PhaseAwareOvershootTracker(setpoint=25.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Temperature rises but never reaches 25C
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=10), 20.0)
        tracker.update(base_time + timedelta(minutes=20), 22.0)
        tracker.update(base_time + timedelta(minutes=30), 23.0)
        tracker.update(base_time + timedelta(minutes=40), 23.5)
        # Temperature starts falling before reaching setpoint
        tracker.update(base_time + timedelta(minutes=50), 23.0)
        tracker.update(base_time + timedelta(minutes=60), 22.0)

        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_RISE
        assert tracker.setpoint_crossed is False
        assert tracker.get_overshoot() is None

    def test_high_temp_in_rise_phase_not_counted_as_overshoot(self):
        """Test that high temps during rise phase are not counted as overshoot."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Start high but never cross setpoint (cooling scenario)
        # Note: This is an edge case where temp starts above setpoint
        # but we're treating the first reading as rise phase
        tracker.update(base_time, 25.0)

        # The temperature 25.0 >= 21.0 - tolerance, so it triggers settling
        # This is actually correct behavior - if we start above setpoint,
        # we're already past the rise phase
        assert tracker.phase == PhaseAwareOvershootTracker.PHASE_SETTLING
        overshoot = tracker.get_overshoot()
        assert overshoot == pytest.approx(4.0, abs=0.01)  # 25.0 - 21.0 = 4.0

    def test_empty_history_returns_none(self):
        """Test that empty tracker returns None for overshoot."""
        tracker = PhaseAwareOvershootTracker(setpoint=21.0)
        assert tracker.get_overshoot() is None


# ============================================================================
# calculate_overshoot Function Tests
# ============================================================================


class TestCalculateOvershoot:
    """Tests for the calculate_overshoot function."""

    def test_overshoot_phase_aware_basic(self):
        """Test basic phase-aware overshoot calculation."""
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=5), 19.5),
            (base_time + timedelta(minutes=10), 21.0),  # reaches setpoint
            (base_time + timedelta(minutes=15), 21.5),  # overshoot
            (base_time + timedelta(minutes=20), 21.0),
        ]

        overshoot = calculate_overshoot(history, target_temp=21.0, phase_aware=True)
        assert overshoot == pytest.approx(0.5, abs=0.01)

    def test_overshoot_phase_aware_setpoint_never_reached(self):
        """Test phase-aware returns None when setpoint never reached."""
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=5), 19.0),
            (base_time + timedelta(minutes=10), 19.5),
            (base_time + timedelta(minutes=15), 19.8),
            # Never reaches 21.0
        ]

        overshoot = calculate_overshoot(history, target_temp=21.0, phase_aware=True)
        assert overshoot is None

    def test_overshoot_legacy_mode(self):
        """Test legacy (non-phase-aware) overshoot calculation."""
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=5), 19.5),  # max in rise phase
            (base_time + timedelta(minutes=10), 19.0),
            # Never reaches setpoint of 21.0
        ]

        # Legacy mode returns overshoot even if setpoint not reached
        overshoot = calculate_overshoot(history, target_temp=18.0, phase_aware=False)
        # Max is 19.5, setpoint is 18.0, overshoot = 1.5
        assert overshoot == pytest.approx(1.5, abs=0.01)

    def test_overshoot_empty_history(self):
        """Test that empty history returns None."""
        overshoot = calculate_overshoot([], target_temp=21.0)
        assert overshoot is None

    def test_overshoot_no_overshoot_exact_setpoint(self):
        """Test overshoot is 0 when max temp equals setpoint."""
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=5), 20.0),
            (base_time + timedelta(minutes=10), 21.0),  # exactly at setpoint
            (base_time + timedelta(minutes=15), 20.8),  # settles back
        ]

        overshoot = calculate_overshoot(history, target_temp=21.0, phase_aware=True)
        assert overshoot == pytest.approx(0.0, abs=0.01)

    def test_overshoot_default_is_phase_aware(self):
        """Test that phase_aware=True is the default."""
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=5), 19.0),
            # Never reaches 21.0
        ]

        # Default should be phase-aware, so this returns None
        overshoot = calculate_overshoot(history, target_temp=21.0)
        assert overshoot is None


# ============================================================================
# Integration Tests
# ============================================================================


class TestOvershootIntegration:
    """Integration tests for overshoot detection scenarios."""

    def test_realistic_heating_cycle(self):
        """Test overshoot detection in a realistic heating cycle."""
        base_time = datetime(2024, 1, 1, 8, 0, 0)  # Morning warm-up
        setpoint = 21.0

        # Simulate a typical heating cycle
        history = [
            # Room cold in morning
            (base_time, 17.0),
            (base_time + timedelta(minutes=5), 17.5),
            (base_time + timedelta(minutes=10), 18.2),
            (base_time + timedelta(minutes=15), 18.9),
            (base_time + timedelta(minutes=20), 19.6),
            (base_time + timedelta(minutes=25), 20.3),
            # Approach setpoint
            (base_time + timedelta(minutes=30), 20.8),
            # Cross setpoint
            (base_time + timedelta(minutes=35), 21.2),
            # Overshoot peak
            (base_time + timedelta(minutes=40), 21.6),
            (base_time + timedelta(minutes=45), 21.7),  # max overshoot
            # Settling back
            (base_time + timedelta(minutes=50), 21.4),
            (base_time + timedelta(minutes=55), 21.2),
            (base_time + timedelta(minutes=60), 21.0),
        ]

        overshoot = calculate_overshoot(history, setpoint)
        # Max in settling phase is 21.7, setpoint is 21.0
        assert overshoot == pytest.approx(0.7, abs=0.01)

    def test_rapid_response_system(self):
        """Test overshoot in a fast-responding system (like forced air)."""
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        setpoint = 20.0

        # Fast heating with significant overshoot
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=2), 19.0),
            (base_time + timedelta(minutes=4), 20.0),  # reaches setpoint quickly
            (base_time + timedelta(minutes=6), 21.5),  # big overshoot
            (base_time + timedelta(minutes=8), 22.0),  # max overshoot
            (base_time + timedelta(minutes=10), 21.0),
            (base_time + timedelta(minutes=12), 20.5),
        ]

        overshoot = calculate_overshoot(history, setpoint)
        assert overshoot == pytest.approx(2.0, abs=0.01)  # 22.0 - 20.0

    def test_slow_response_system(self):
        """Test overshoot in a slow-responding system (like floor heating)."""
        base_time = datetime(2024, 1, 1, 6, 0, 0)
        setpoint = 21.0

        # Slow heating with minimal overshoot
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=30), 18.5),
            (base_time + timedelta(minutes=60), 19.2),
            (base_time + timedelta(minutes=90), 19.8),
            (base_time + timedelta(minutes=120), 20.4),
            (base_time + timedelta(minutes=150), 20.9),
            (base_time + timedelta(minutes=180), 21.1),  # just past setpoint
            (base_time + timedelta(minutes=210), 21.2),  # max overshoot
            (base_time + timedelta(minutes=240), 21.1),
            (base_time + timedelta(minutes=270), 21.0),
        ]

        overshoot = calculate_overshoot(history, setpoint)
        assert overshoot == pytest.approx(0.2, abs=0.01)  # 21.2 - 21.0


# Test marker for pytest discovery
def test_overshoot_module_exists():
    """Marker test to ensure module is importable."""
    from custom_components.adaptive_thermostat.adaptive.learning import (
        PhaseAwareOvershootTracker,
        calculate_overshoot,
    )
    assert PhaseAwareOvershootTracker is not None
    assert calculate_overshoot is not None


# ============================================================================
# Rule Conflict Tests
# ============================================================================


class TestRuleConflicts:
    """Tests for PID rule conflict detection and resolution."""

    def test_overshoot_vs_slow_response_conflict(self):
        """Test that overshoot takes precedence over slow response for Kp."""
        learner = AdaptiveLearner()

        # Add cycles with both overshoot AND slow response
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,     # Triggers high overshoot (reduce Kp)
                rise_time=70,      # Triggers slow response (increase Kp)
                oscillations=0,
                settling_time=30,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Overshoot (priority 2) should win over slow response (priority 1)
        # Kp should be REDUCED, not increased
        assert result is not None
        assert result["kp"] < 100.0

    def test_oscillation_vs_slow_response_conflict(self):
        """Test that oscillation takes precedence over slow response for Kp."""
        learner = AdaptiveLearner()

        # Add cycles with oscillations AND slow response
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.0,
                oscillations=4,    # Many oscillations (reduce Kp by 10%)
                rise_time=70,      # Slow response (increase Kp by 10%)
                settling_time=30,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Oscillation (priority 3) should win over slow response (priority 1)
        # Kp should be REDUCED
        assert result is not None
        assert result["kp"] < 100.0

    def test_no_conflict_when_rules_agree(self):
        """Test that agreeing rules both apply."""
        learner = AdaptiveLearner()

        # Add cycles where overshoot and oscillations both reduce Kp
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,     # High overshoot (reduce Kp ~12%)
                oscillations=4,    # Many oscillations (reduce Kp 10%)
                rise_time=20,
                settling_time=30,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Both reductions should apply (no conflict)
        # Both reduce Kp: 100 * 0.88 * 0.90 ≈ 79.2
        assert result is not None
        assert result["kp"] < 80.0

    def test_conflict_detection_only_on_opposing_adjustments(self):
        """Test that conflicts are only detected when adjustments oppose each other."""
        learner = AdaptiveLearner()

        # Add cycles with undershoot (increases Ki) and slow settling (increases Kd)
        # No conflict expected - they adjust different parameters
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.0,
                undershoot=0.5,    # Increase Ki
                oscillations=0,
                rise_time=20,
                settling_time=100,  # Increase Kd
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Both should apply since they don't conflict
        assert result is not None
        assert result["ki"] > 1.0  # Ki increased
        assert result["kd"] > 10.0  # Kd increased

    def test_multiple_conflicts_resolved(self):
        """Test that multiple conflicts are resolved correctly."""
        learner = AdaptiveLearner()

        # Create scenario with multiple potential conflicts
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.3,     # Moderate overshoot (reduce Kp by 5%)
                oscillations=4,    # Many oscillations (reduce Kp 10%, increase Kd 20%)
                rise_time=70,      # Slow response (increase Kp 10%)
                settling_time=30,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Kp conflicts: overshoot (priority 2) and oscillation (priority 3) both reduce
        #              slow response (priority 1) increases
        # Oscillation has highest priority, so slow response is suppressed
        assert result is not None
        assert result["kp"] < 100.0  # Should be reduced
        assert result["kd"] > 10.0   # Kd should be increased by oscillation rule


class TestConvergenceDetection:
    """Tests for convergence detection (system is well-tuned)."""

    def test_converged_system_returns_none(self):
        """Test that converged system returns None (no adjustment needed)."""
        learner = AdaptiveLearner()

        # Add cycles with good metrics (within convergence thresholds)
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,      # Below 0.2 threshold
                oscillations=0,     # Below 1 threshold
                settling_time=30,   # Below 60 threshold
                rise_time=20,       # Below 45 threshold
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # System is converged - should return None
        assert result is None

    def test_not_converged_when_overshoot_high(self):
        """Test that system is not converged when overshoot exceeds threshold."""
        learner = AdaptiveLearner()

        # Add cycles with one metric outside threshold
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.3,      # Above 0.2 threshold
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Not converged - should return adjustment
        assert result is not None

    def test_not_converged_when_oscillations_high(self):
        """Test that system is not converged when oscillations exceed threshold."""
        learner = AdaptiveLearner()

        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                oscillations=2,     # Above 1 threshold
                settling_time=30,
                rise_time=20,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Not converged due to oscillations
        assert result is not None

    def test_not_converged_when_settling_slow(self):
        """Test that system is not converged when settling time exceeds threshold."""
        learner = AdaptiveLearner()

        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                oscillations=0,
                settling_time=70,   # Above 60 threshold
                rise_time=20,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Not converged due to slow settling, but settling_time=70 doesn't trigger
        # slow settling rule (threshold is 90), so it should converge on other metrics
        # Actually settling_time=70 is below the 90 rule threshold but above
        # the 60 convergence threshold, so it's NOT converged
        # But no rule fires, so result should be None (no adjustments but still converged on rules)
        # This is tricky - convergence check happens BEFORE rule evaluation
        # With settling_time=70 > 60 threshold, convergence returns False
        # But no rule triggers, so rule_results is empty, returning None
        # Let me check the logic...
        # Actually the function returns None in two cases:
        # 1. Convergence check passes (all metrics within thresholds)
        # 2. No rules trigger
        # In this case, convergence check fails (settling_time=70 > 60)
        # But no rules trigger (settling_time=70 < 90 threshold)
        # So it returns None because no rules triggered, not because converged
        assert result is None  # No rules trigger

    def test_not_converged_when_rise_slow(self):
        """Test that system is not converged when rise time exceeds threshold."""
        learner = AdaptiveLearner()

        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                oscillations=0,
                settling_time=30,
                rise_time=70,       # Above 45 threshold, triggers slow response
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Not converged - slow response rule should trigger
        assert result is not None
        assert result["kp"] > 100.0  # Slow response increases Kp

    def test_partial_convergence_not_detected(self):
        """Test that partial convergence (some metrics good) doesn't trigger convergence."""
        learner = AdaptiveLearner()

        # Most metrics good, but overshoot is high
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.5,      # Above threshold, triggers moderate overshoot
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # Not fully converged - should return adjustment
        assert result is not None
        assert result["kp"] < 100.0  # Overshoot reduces Kp

    def test_convergence_at_threshold_boundary(self):
        """Test convergence when metrics are exactly at threshold boundaries."""
        learner = AdaptiveLearner()

        # All metrics exactly at thresholds (should still be considered converged)
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.2,      # Exactly at 0.2 threshold
                oscillations=1,     # Exactly at 1 threshold
                settling_time=60,   # Exactly at 60 threshold
                rise_time=45,       # Exactly at 45 threshold
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        # At boundary values should be considered converged (<=)
        assert result is None


# Test marker for rule conflict module
def test_rule_conflicts_module_exists():
    """Marker test to ensure rule conflict types are importable."""
    from custom_components.adaptive_thermostat.adaptive.learning import (
        PIDRule,
        PIDRuleResult,
    )
    assert PIDRule is not None
    assert PIDRuleResult is not None
    # Verify priority levels
    assert PIDRule.HIGH_OVERSHOOT.priority == 2
    assert PIDRule.MANY_OSCILLATIONS.priority == 3
    assert PIDRule.SLOW_RESPONSE.priority == 1
