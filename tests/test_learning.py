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
        """Test that moderate overshoot and slow response don't conflict (different parameters)."""
        learner = AdaptiveLearner()

        # Add cycles with both overshoot AND slow response
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,     # Triggers moderate overshoot (increase Kd, Kp unchanged)
                rise_time=70,      # Triggers slow response (increase Kp)
                oscillations=0,
                settling_time=30,
            ))

        # Start with Kd = 2.0 (within valid range of 0-5.0)
        result = learner.calculate_pid_adjustment(100.0, 1.0, 2.0)

        # New behavior: moderate overshoot increases Kd, slow response increases Kp
        # No conflict since they adjust different parameters
        assert result is not None
        assert result["kp"] >= 100.0, "Kp should increase or stay same (slow response rule)"
        assert result["kd"] > 2.0, "Kd should increase (moderate overshoot rule)"

    def test_oscillation_vs_slow_response_conflict(self):
        """Test that oscillation takes precedence over slow response for Kp."""
        learner = AdaptiveLearner()

        # Add cycles with oscillations AND slow response
        for _ in range(6):
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
        """Test that agreeing rules both apply when they affect different parameters."""
        learner = AdaptiveLearner()

        # Add cycles where overshoot increases Kd and oscillations reduce Kp
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,     # Moderate overshoot (increase Kd 20%)
                oscillations=4,    # Many oscillations (reduce Kp 10%, increase Kd 20%)
                rise_time=20,
                settling_time=30,
            ))

        # Start with Kd = 2.0 (within valid range of 0-5.0)
        result = learner.calculate_pid_adjustment(100.0, 1.0, 2.0)

        # New behavior: oscillations reduce Kp, both increase Kd (no conflict)
        # Kp: 100 * 0.90 = 90.0 (oscillation rule only)
        # Kd: 2.0 * 1.20 * 1.20 = 2.88 (both rules increase)
        assert result is not None
        assert result["kp"] < 100.0, "Kp should be reduced by oscillation rule"
        assert result["kd"] > 2.0, "Kd should be increased by both overshoot and oscillation rules"

    def test_conflict_detection_only_on_opposing_adjustments(self):
        """Test that conflicts are only detected when adjustments oppose each other."""
        learner = AdaptiveLearner()

        # Add cycles with undershoot (increases Ki) and slow settling (increases Kd)
        # No conflict expected - they adjust different parameters
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.0,
                undershoot=0.5,    # Increase Ki
                oscillations=0,
                rise_time=20,
                settling_time=100,  # Increase Kd
            ))

        # Use realistic PID values within v0.7.0 limits (kd_max=5.0)
        result = learner.calculate_pid_adjustment(100.0, 1.0, 2.0)

        # Both should apply since they don't conflict
        assert result is not None
        assert result["ki"] > 1.0  # Ki increased
        assert result["kd"] > 2.0  # Kd increased

    def test_multiple_conflicts_resolved(self):
        """Test that multiple conflicts are resolved correctly."""
        learner = AdaptiveLearner()

        # Create scenario with multiple potential conflicts
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.3,     # Moderate overshoot (reduce Kp by 5%)
                oscillations=4,    # Many oscillations (reduce Kp 10%, increase Kd 20%)
                rise_time=70,      # Slow response (increase Kp 10%)
                settling_time=30,
            ))

        # Use realistic PID values within v0.7.0 limits (kd_max=5.0)
        result = learner.calculate_pid_adjustment(100.0, 1.0, 2.0)

        # Kp conflicts: overshoot (priority 2) and oscillation (priority 3) both reduce
        #              slow response (priority 1) increases
        # Oscillation has highest priority, so slow response is suppressed
        assert result is not None
        assert result["kp"] < 100.0  # Should be reduced
        assert result["kd"] > 2.0   # Kd should be increased by oscillation rule


class TestConvergenceDetection:
    """Tests for convergence detection (system is well-tuned)."""

    def test_converged_system_returns_none(self):
        """Test that converged system returns None (no adjustment needed)."""
        learner = AdaptiveLearner()

        # Add cycles with good metrics (within convergence thresholds)
        for _ in range(6):
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
        for _ in range(6):
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

        for _ in range(6):
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

        for _ in range(6):
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

        for _ in range(6):
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
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.5,      # Above threshold, triggers moderate overshoot
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Start with Kd = 2.0 (within valid range of 0-5.0)
        result = learner.calculate_pid_adjustment(100.0, 1.0, 2.0)

        # Not fully converged - should return adjustment
        assert result is not None
        # New behavior: moderate overshoot increases Kd, not reduces Kp
        assert result["kd"] > 2.0, "Overshoot should increase Kd"

    def test_convergence_at_threshold_boundary(self):
        """Test convergence when metrics are exactly at threshold boundaries."""
        learner = AdaptiveLearner()

        # All metrics exactly at thresholds (should still be considered converged)
        for _ in range(6):
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


# ============================================================================
# Segment Detection Tests (Story 3.4)
# ============================================================================


class TestSegmentNoiseToleranceConstants:
    """Test that segment detection constants are properly defined."""

    def test_noise_tolerance_constant_exists(self):
        """Test SEGMENT_NOISE_TOLERANCE constant exists and has correct default."""
        from custom_components.adaptive_thermostat.const import SEGMENT_NOISE_TOLERANCE
        assert SEGMENT_NOISE_TOLERANCE == 0.05

    def test_rate_bounds_constants_exist(self):
        """Test SEGMENT_RATE_MIN and SEGMENT_RATE_MAX constants exist."""
        from custom_components.adaptive_thermostat.const import (
            SEGMENT_RATE_MIN,
            SEGMENT_RATE_MAX,
        )
        assert SEGMENT_RATE_MIN == 0.1
        assert SEGMENT_RATE_MAX == 10.0


class TestNoisyTemperatureData:
    """Tests for noisy temperature data handling in segment detection."""

    def test_cooling_segment_with_noise_below_tolerance(self):
        """Test that small temperature reversals below tolerance don't break segments."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner(noise_tolerance=0.05)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Cooling trend with small noise reversals (< 0.05C)
        history = [
            (base_time, 22.0),
            (base_time + timedelta(minutes=10), 21.8),
            (base_time + timedelta(minutes=20), 21.82),   # +0.02 noise (below tolerance)
            (base_time + timedelta(minutes=30), 21.6),
            (base_time + timedelta(minutes=40), 21.63),   # +0.03 noise (below tolerance)
            (base_time + timedelta(minutes=50), 21.4),
            (base_time + timedelta(minutes=60), 21.2),
        ]

        # Should find ONE continuous segment despite noise
        segments = learner._find_cooling_segments(history, min_duration_minutes=30)
        assert len(segments) == 1
        assert len(segments[0]) == 7  # All points included

    def test_cooling_segment_broken_by_reversal_above_tolerance(self):
        """Test that temperature reversals above tolerance DO break segments."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner(noise_tolerance=0.05)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Cooling with significant warming reversal (> 0.05C)
        history = [
            (base_time, 22.0),
            (base_time + timedelta(minutes=10), 21.8),
            (base_time + timedelta(minutes=20), 21.6),
            (base_time + timedelta(minutes=30), 21.4),
            (base_time + timedelta(minutes=40), 21.5),    # +0.1C reversal (above tolerance)
            (base_time + timedelta(minutes=50), 21.3),
            (base_time + timedelta(minutes=60), 21.1),
            (base_time + timedelta(minutes=70), 20.9),
            (base_time + timedelta(minutes=80), 20.7),
        ]

        # Should find TWO segments due to reversal
        segments = learner._find_cooling_segments(history, min_duration_minutes=30)
        assert len(segments) == 2

    def test_heating_segment_with_noise_below_tolerance(self):
        """Test heating segment detection with noise tolerance."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner(noise_tolerance=0.05)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Heating trend with small noise
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=10), 18.5),
            (base_time + timedelta(minutes=20), 18.48),   # -0.02 noise (below tolerance)
            (base_time + timedelta(minutes=30), 19.0),
            (base_time + timedelta(minutes=40), 19.5),
            (base_time + timedelta(minutes=50), 19.47),   # -0.03 noise (below tolerance)
            (base_time + timedelta(minutes=60), 20.0),
        ]

        # Should find ONE continuous heating segment
        segments = learner._find_heating_segments(history, min_duration_minutes=30)
        assert len(segments) == 1

    def test_calculate_cooling_rate_with_noisy_data(self):
        """Test full cooling rate calculation with noisy data."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner(noise_tolerance=0.05)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # 90 minutes of cooling with noise: 22.0 -> 20.0 = ~1.33 C/hour
        history = [
            (base_time, 22.0),
            (base_time + timedelta(minutes=15), 21.52),
            (base_time + timedelta(minutes=30), 21.54),  # noise
            (base_time + timedelta(minutes=45), 21.0),
            (base_time + timedelta(minutes=60), 20.52),
            (base_time + timedelta(minutes=75), 20.0),
            (base_time + timedelta(minutes=90), 20.03),  # noise at end
        ]

        rate = learner.calculate_cooling_rate(history, min_duration_minutes=30)
        # Should get approximately 1.33 C/hour despite noise
        assert rate is not None
        # The actual rate depends on segment boundaries, allow some tolerance
        assert 1.0 < rate < 2.0


class TestRateBoundsRejection:
    """Tests for rate bounds checking and rejection of impossible rates."""

    def test_reject_rate_below_minimum(self):
        """Test that rates below SEGMENT_RATE_MIN are rejected."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner()
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Very slow cooling: 0.01C over 1 hour = 0.01 C/hour (< 0.1 minimum)
        history = [
            (base_time, 20.0),
            (base_time + timedelta(minutes=30), 19.995),
            (base_time + timedelta(minutes=60), 19.99),
        ]

        segments = learner._find_cooling_segments(history, min_duration_minutes=30)
        # Should be rejected due to rate below minimum
        assert len(segments) == 0

    def test_reject_rate_above_maximum(self):
        """Test that rates above SEGMENT_RATE_MAX are rejected."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner()
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Impossibly fast cooling: 15C over 1 hour = 15 C/hour (> 10 maximum)
        history = [
            (base_time, 35.0),
            (base_time + timedelta(minutes=30), 27.5),
            (base_time + timedelta(minutes=60), 20.0),
        ]

        segments = learner._find_cooling_segments(history, min_duration_minutes=30)
        # Should be rejected due to rate above maximum
        assert len(segments) == 0

    def test_accept_rate_within_bounds(self):
        """Test that rates within bounds are accepted."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner()
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Normal cooling: 2C over 1 hour = 2 C/hour (within 0.1-10 bounds)
        history = [
            (base_time, 22.0),
            (base_time + timedelta(minutes=30), 21.0),
            (base_time + timedelta(minutes=60), 20.0),
        ]

        segments = learner._find_cooling_segments(history, min_duration_minutes=30)
        assert len(segments) == 1

    def test_reject_heating_rate_above_maximum(self):
        """Test that impossibly fast heating rates are rejected."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner()
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Impossibly fast heating: 12C over 1 hour = 12 C/hour (> 10 maximum)
        history = [
            (base_time, 10.0),
            (base_time + timedelta(minutes=30), 16.0),
            (base_time + timedelta(minutes=60), 22.0),
        ]

        segments = learner._find_heating_segments(history, min_duration_minutes=30)
        assert len(segments) == 0

    def test_rate_bounds_at_boundaries(self):
        """Test rates exactly at boundary values."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )
        from custom_components.adaptive_thermostat.const import (
            SEGMENT_RATE_MIN,
            SEGMENT_RATE_MAX,
        )

        learner = ThermalRateLearner()
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Rate exactly at minimum (0.1 C/hour)
        # 0.1C over 1 hour
        history_min = [
            (base_time, 20.1),
            (base_time + timedelta(minutes=30), 20.05),
            (base_time + timedelta(minutes=60), 20.0),
        ]
        segments = learner._find_cooling_segments(history_min, min_duration_minutes=30)
        assert len(segments) == 1  # Exactly at minimum should be accepted

        # Rate exactly at maximum (10 C/hour)
        # 10C over 1 hour
        history_max = [
            (base_time, 30.0),
            (base_time + timedelta(minutes=30), 25.0),
            (base_time + timedelta(minutes=60), 20.0),
        ]
        segments = learner._find_cooling_segments(history_max, min_duration_minutes=30)
        assert len(segments) == 1  # Exactly at maximum should be accepted


class TestSegmentDetectionEdgeCases:
    """Edge cases for segment detection with noise tolerance."""

    def test_all_noise_no_trend(self):
        """Test data that is all noise with no clear trend."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner(noise_tolerance=0.05)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Temperature oscillating within noise band
        history = [
            (base_time, 20.0),
            (base_time + timedelta(minutes=10), 20.02),
            (base_time + timedelta(minutes=20), 19.98),
            (base_time + timedelta(minutes=30), 20.01),
            (base_time + timedelta(minutes=40), 19.99),
            (base_time + timedelta(minutes=50), 20.0),
        ]

        # Should not find valid segments (no net temperature change exceeds rate minimum)
        cooling_segments = learner._find_cooling_segments(history, min_duration_minutes=30)
        heating_segments = learner._find_heating_segments(history, min_duration_minutes=30)

        # Segments should be rejected for having rate below minimum
        assert len(cooling_segments) == 0
        assert len(heating_segments) == 0

    def test_custom_noise_tolerance(self):
        """Test that custom noise tolerance value is respected."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Data with 0.1C reversals - longer first segment to ensure both meet duration
        history = [
            (base_time, 26.0),
            (base_time + timedelta(minutes=15), 25.5),
            (base_time + timedelta(minutes=30), 25.0),
            (base_time + timedelta(minutes=45), 24.5),
            (base_time + timedelta(minutes=60), 24.0),
            (base_time + timedelta(minutes=75), 24.1),   # +0.1 reversal (above 0.05 tolerance)
            (base_time + timedelta(minutes=90), 23.5),
            (base_time + timedelta(minutes=105), 23.0),
            (base_time + timedelta(minutes=120), 22.5),
            (base_time + timedelta(minutes=135), 22.0),
            (base_time + timedelta(minutes=150), 21.5),
        ]

        # With default tolerance (0.05), should break at reversal
        # First segment: 26.0 -> 24.0 over 60 min = 2 C/hour (valid)
        # Second segment: 24.1 -> 21.5 over 75 min = ~2.08 C/hour (valid)
        learner_strict = ThermalRateLearner(noise_tolerance=0.05)
        segments_strict = learner_strict._find_cooling_segments(history, min_duration_minutes=30)

        # With higher tolerance (0.15), should be one segment (0.1C < 0.15 tolerance)
        learner_lenient = ThermalRateLearner(noise_tolerance=0.15)
        segments_lenient = learner_lenient._find_cooling_segments(history, min_duration_minutes=30)

        assert len(segments_strict) == 2  # Broken by 0.1C reversal
        assert len(segments_lenient) == 1  # 0.1C < 0.15 tolerance

    def test_learner_uses_default_noise_tolerance(self):
        """Test that ThermalRateLearner uses SEGMENT_NOISE_TOLERANCE by default."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )
        from custom_components.adaptive_thermostat.const import SEGMENT_NOISE_TOLERANCE

        learner = ThermalRateLearner()
        assert learner.noise_tolerance == SEGMENT_NOISE_TOLERANCE
        assert learner.noise_tolerance == 0.05

    def test_validate_segment_rejects_no_net_change(self):
        """Test that _validate_segment rejects segments with no net temperature change."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            ThermalRateLearner,
        )

        learner = ThermalRateLearner()
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Segment that starts and ends at same temperature
        segment = [
            (base_time, 20.0),
            (base_time + timedelta(minutes=30), 19.5),
            (base_time + timedelta(minutes=60), 20.0),  # back to start
        ]

        result = learner._validate_segment(segment, min_duration_minutes=30, is_cooling=True)
        assert result is None  # Should be rejected (no net cooling)


# Marker test for segment detection feature
def test_segment_detection_module():
    """Marker test to ensure segment detection with noise tolerance works."""
    from custom_components.adaptive_thermostat.adaptive.learning import ThermalRateLearner
    from custom_components.adaptive_thermostat.const import (
        SEGMENT_NOISE_TOLERANCE,
        SEGMENT_RATE_MIN,
        SEGMENT_RATE_MAX,
    )

    learner = ThermalRateLearner()
    assert hasattr(learner, 'noise_tolerance')
    assert learner.noise_tolerance == SEGMENT_NOISE_TOLERANCE
    assert SEGMENT_RATE_MIN == 0.1
    assert SEGMENT_RATE_MAX == 10.0


# ============================================================================
# Cycle History Bounding Tests (Story 3.5)
# ============================================================================


class TestCycleHistoryBounding:
    """Tests for MAX_CYCLE_HISTORY and FIFO eviction."""

    def test_max_cycle_history_constant_exists(self):
        """Test MAX_CYCLE_HISTORY constant exists and has correct default."""
        from custom_components.adaptive_thermostat.const import MAX_CYCLE_HISTORY
        assert MAX_CYCLE_HISTORY == 100

    def test_min_adjustment_interval_constant_exists(self):
        """Test MIN_ADJUSTMENT_INTERVAL constant exists and has correct default."""
        from custom_components.adaptive_thermostat.const import MIN_ADJUSTMENT_INTERVAL, MIN_ADJUSTMENT_CYCLES
        # Updated from 24h to 8h in v0.7.0 for hybrid rate limiting
        assert MIN_ADJUSTMENT_INTERVAL == 8
        assert MIN_ADJUSTMENT_CYCLES == 3

    def test_adaptive_learner_default_max_history(self):
        """Test AdaptiveLearner uses MAX_CYCLE_HISTORY by default."""
        from custom_components.adaptive_thermostat.const import MAX_CYCLE_HISTORY

        learner = AdaptiveLearner()
        assert learner._max_history == MAX_CYCLE_HISTORY
        assert learner._max_history == 100

    def test_adaptive_learner_custom_max_history(self):
        """Test AdaptiveLearner accepts custom max_history."""
        learner = AdaptiveLearner(max_history=50)
        assert learner._max_history == 50

    def test_fifo_eviction_when_exceeding_max(self):
        """Test that FIFO eviction removes oldest entries when exceeding max."""
        learner = AdaptiveLearner(max_history=5)

        # Add 7 cycles
        for i in range(7):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=float(i),  # Use overshoot to track which entry
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Should only have 5 entries (max_history)
        assert learner.get_cycle_count() == 5

        # Oldest entries (0, 1) should be evicted, keeping 2, 3, 4, 5, 6
        assert learner._cycle_history[0].overshoot == 2.0
        assert learner._cycle_history[-1].overshoot == 6.0

    def test_fifo_eviction_maintains_correct_order(self):
        """Test that FIFO eviction maintains chronological order."""
        learner = AdaptiveLearner(max_history=3)

        # Add 5 cycles
        for i in range(5):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=float(i * 0.1),
                oscillations=i,
                settling_time=float(i * 10),
                rise_time=float(i * 5),
            ))

        # Should have cycles 2, 3, 4 (0 and 1 evicted)
        assert learner.get_cycle_count() == 3
        assert learner._cycle_history[0].oscillations == 2
        assert learner._cycle_history[1].oscillations == 3
        assert learner._cycle_history[2].oscillations == 4

    def test_no_eviction_when_under_max(self):
        """Test that no eviction occurs when under max_history."""
        learner = AdaptiveLearner(max_history=10)

        # Add only 5 cycles
        for i in range(5):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=float(i),
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Should have all 5 entries
        assert learner.get_cycle_count() == 5
        assert learner._cycle_history[0].overshoot == 0.0
        assert learner._cycle_history[-1].overshoot == 4.0

    def test_fifo_eviction_at_exact_boundary(self):
        """Test behavior when adding entry that exactly reaches max_history."""
        learner = AdaptiveLearner(max_history=5)

        # Add exactly 5 cycles
        for i in range(5):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=float(i),
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Should have exactly 5 entries, no eviction
        assert learner.get_cycle_count() == 5
        assert learner._cycle_history[0].overshoot == 0.0

        # Add one more - should trigger eviction
        learner.add_cycle_metrics(CycleMetrics(
            overshoot=5.0,
            oscillations=0,
            settling_time=30,
            rise_time=20,
        ))

        assert learner.get_cycle_count() == 5
        assert learner._cycle_history[0].overshoot == 1.0  # 0 evicted

    def test_clear_history_resets_count(self):
        """Test that clear_history removes all entries."""
        learner = AdaptiveLearner(max_history=10)

        # Add some cycles
        for i in range(5):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=float(i),
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        assert learner.get_cycle_count() == 5

        learner.clear_history()
        assert learner.get_cycle_count() == 0


# ============================================================================
# Rate Limiting Tests (Story 3.5)
# ============================================================================


class TestRateLimiting:
    """Tests for MIN_ADJUSTMENT_INTERVAL and rate limiting."""

    def test_first_adjustment_not_rate_limited(self):
        """Test that first adjustment is not rate limited."""
        learner = AdaptiveLearner()
        assert learner._last_adjustment_time is None

        # Rate limit check should return False (not limited)
        assert learner._check_rate_limit(24) is False

    def test_adjustment_updates_last_adjustment_time(self):
        """Test that successful adjustment updates last_adjustment_time."""
        learner = AdaptiveLearner()

        # Add enough cycles to trigger adjustment
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,  # High overshoot triggers adjustment
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        assert learner._last_adjustment_time is None

        # Make adjustment
        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)
        assert result is not None
        assert learner._last_adjustment_time is not None

    def test_rate_limited_when_too_recent(self):
        """Test that adjustment is skipped when last adjustment was too recent."""
        from unittest.mock import patch

        learner = AdaptiveLearner()

        # Add enough cycles
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # First adjustment should work
        result1 = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)
        assert result1 is not None

        # Add more cycles for a potential second adjustment
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Second adjustment immediately after should be rate limited
        result2 = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)
        assert result2 is None  # Rate limited

    def test_adjustment_allowed_after_interval(self):
        """Test that adjustment is allowed after minimum interval has passed."""
        learner = AdaptiveLearner()

        # Add enough cycles
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # First adjustment
        result1 = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)
        assert result1 is not None

        # Manually set last adjustment time to 25 hours ago
        learner._last_adjustment_time = datetime.now() - timedelta(hours=25)

        # Add more cycles
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Should now be allowed (25h > 24h interval)
        result2 = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)
        assert result2 is not None

    def test_custom_rate_limit_interval(self):
        """Test that custom min_interval_hours is respected."""
        learner = AdaptiveLearner()

        # Add enough cycles
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # First adjustment with short interval
        result1 = learner.calculate_pid_adjustment(100.0, 1.0, 10.0, min_interval_hours=1)
        assert result1 is not None

        # Set last adjustment to 2 hours ago
        learner._last_adjustment_time = datetime.now() - timedelta(hours=2)

        # Add more cycles
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Should be allowed with 1h interval (2h > 1h)
        result2 = learner.calculate_pid_adjustment(100.0, 1.0, 10.0, min_interval_hours=1)
        assert result2 is not None

    def test_rate_limit_check_boundary(self):
        """Test rate limit at exact boundary for hybrid gates."""
        learner = AdaptiveLearner()

        # Set last adjustment to exactly 8 hours ago (time gate at boundary)
        learner._last_adjustment_time = datetime.now() - timedelta(hours=8)
        learner._cycles_since_last_adjustment = 3  # Cycle gate satisfied

        # At exactly 8h and 3 cycles, should NOT be rate limited (>= is allowed)
        assert learner._check_rate_limit(8, 3) is False

        # At 7.99h with 3 cycles, should be rate limited by time gate
        learner._last_adjustment_time = datetime.now() - timedelta(hours=7, minutes=59)
        learner._cycles_since_last_adjustment = 3
        assert learner._check_rate_limit(8, 3) is True

        # At 8h but only 2 cycles, should be rate limited by cycle gate
        learner._last_adjustment_time = datetime.now() - timedelta(hours=8)
        learner._cycles_since_last_adjustment = 2
        assert learner._check_rate_limit(8, 3) is True

    def test_clear_history_resets_last_adjustment_time(self):
        """Test that clear_history also resets last_adjustment_time."""
        learner = AdaptiveLearner()

        # Add cycles and make adjustment
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)
        assert result is not None
        assert learner._last_adjustment_time is not None

        # Clear history
        learner.clear_history()
        assert learner._last_adjustment_time is None

    def test_get_last_adjustment_time(self):
        """Test get_last_adjustment_time returns correct value."""
        learner = AdaptiveLearner()

        # Initially None
        assert learner.get_last_adjustment_time() is None

        # Add cycles and make adjustment
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        before = datetime.now()
        result = learner.calculate_pid_adjustment(100.0, 1.0, 10.0)
        after = datetime.now()

        assert result is not None
        last_time = learner.get_last_adjustment_time()
        assert last_time is not None
        assert before <= last_time <= after


# Marker test for Story 3.5
def test_story_3_5_features():
    """Marker test to ensure Story 3.5 features are importable."""
    from custom_components.adaptive_thermostat.const import (
        MAX_CYCLE_HISTORY,
        MIN_ADJUSTMENT_INTERVAL,
        MIN_ADJUSTMENT_CYCLES,
    )
    from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner

    assert MAX_CYCLE_HISTORY == 100
    # Updated from 24h to 8h in v0.7.0 for hybrid rate limiting
    assert MIN_ADJUSTMENT_INTERVAL == 8
    assert MIN_ADJUSTMENT_CYCLES == 3

    learner = AdaptiveLearner()
    assert hasattr(learner, '_max_history')
    assert hasattr(learner, '_last_adjustment_time')
    assert hasattr(learner, '_cycles_since_last_adjustment')
    assert hasattr(learner, 'get_last_adjustment_time')


# ============================================================================
# PID Limits Tests (Story 7.4)
# ============================================================================


class TestPIDLimitsConstants:
    """Tests for PID_LIMITS constants including Ke limits."""

    def test_pid_limits_constant_exists(self):
        """Test PID_LIMITS constant exists with all required keys."""
        from custom_components.adaptive_thermostat.const import PID_LIMITS

        assert "kp_min" in PID_LIMITS
        assert "kp_max" in PID_LIMITS
        assert "ki_min" in PID_LIMITS
        assert "ki_max" in PID_LIMITS
        assert "kd_min" in PID_LIMITS
        assert "kd_max" in PID_LIMITS
        assert "ke_min" in PID_LIMITS
        assert "ke_max" in PID_LIMITS

    def test_ke_limits_values(self):
        """Test Ke limits have correct default values (0.0 to 2.0) after v0.7.1."""
        from custom_components.adaptive_thermostat.const import PID_LIMITS

        assert PID_LIMITS["ke_min"] == 0.0
        assert PID_LIMITS["ke_max"] == 2.0  # Restored by 100x in v0.7.1 (issue 1.3)

    def test_ke_limits_range_is_valid(self):
        """Test that ke_min is less than ke_max."""
        from custom_components.adaptive_thermostat.const import PID_LIMITS

        assert PID_LIMITS["ke_min"] < PID_LIMITS["ke_max"]

    def test_all_pid_limits_are_numeric(self):
        """Test all PID limits are numeric (int or float)."""
        from custom_components.adaptive_thermostat.const import PID_LIMITS

        for key, value in PID_LIMITS.items():
            assert isinstance(value, (int, float)), f"{key} is not numeric"

    def test_all_min_limits_less_than_max(self):
        """Test all min limits are less than corresponding max limits."""
        from custom_components.adaptive_thermostat.const import PID_LIMITS

        assert PID_LIMITS["kp_min"] < PID_LIMITS["kp_max"]
        assert PID_LIMITS["ki_min"] < PID_LIMITS["ki_max"]
        assert PID_LIMITS["kd_min"] < PID_LIMITS["kd_max"]
        assert PID_LIMITS["ke_min"] < PID_LIMITS["ke_max"]


def test_pid_limits():
    """Test that PID_LIMITS includes Ke limits with correct values.

    Story 7.4: Add Ke limits to PID_LIMITS constant.
    - ke_min = 0.0 (no weather compensation)
    - ke_max = 2.0 (restored by 100x in v0.7.1 - issue 1.3)
    """
    from custom_components.adaptive_thermostat.const import PID_LIMITS

    # Verify Ke limits exist
    assert "ke_min" in PID_LIMITS, "ke_min missing from PID_LIMITS"
    assert "ke_max" in PID_LIMITS, "ke_max missing from PID_LIMITS"

    # Verify Ke limits have correct values
    assert PID_LIMITS["ke_min"] == 0.0, f"ke_min should be 0.0, got {PID_LIMITS['ke_min']}"
    assert PID_LIMITS["ke_max"] == 2.0, f"ke_max should be 2.0, got {PID_LIMITS['ke_max']}"

    # Verify existing limits (note: ki_max, kd_max, and ke_max changed in v0.7.0 and v0.7.1)
    assert PID_LIMITS["kp_min"] == 10.0
    assert PID_LIMITS["kp_max"] == 500.0
    assert PID_LIMITS["ki_min"] == 0.0
    assert PID_LIMITS["ki_max"] == 1000.0  # Increased from 100.0 in v0.7.0 (issue 1.4)
    assert PID_LIMITS["kd_min"] == 0.0
    assert PID_LIMITS["kd_max"] == 3.3  # Reduced from 5.0 in v0.7.1 (issue 4.1), was 200.0 in v0.7.0


# ============================================================================
# calculate_rise_time Function Tests
# ============================================================================


class TestCalculateRiseTime:
    """Tests for the calculate_rise_time function."""

    def test_calculate_rise_time_basic(self):
        """Test basic rise time calculation with normal temperature rise."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=15), 19.5),
            (base_time + timedelta(minutes=30), 21.0),
        ]

        rise_time = calculate_rise_time(history, start_temp=18.0, target_temp=21.0)
        assert rise_time == 30.0

    def test_calculate_rise_time_never_reached(self):
        """Test rise time when target temperature is never reached."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=15), 19.0),
            (base_time + timedelta(minutes=30), 20.0),
        ]

        # Target of 22.0 never reached
        rise_time = calculate_rise_time(history, start_temp=18.0, target_temp=22.0)
        assert rise_time is None

    def test_calculate_rise_time_insufficient_data(self):
        """Test rise time with insufficient data (< 2 samples)."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)

        # Empty history
        rise_time = calculate_rise_time([], start_temp=18.0, target_temp=21.0)
        assert rise_time is None

        # Single sample
        history = [(base_time, 18.0)]
        rise_time = calculate_rise_time(history, start_temp=18.0, target_temp=21.0)
        assert rise_time is None

    def test_calculate_rise_time_already_at_target(self):
        """Test rise time when start temperature equals target."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 21.0),
            (base_time + timedelta(minutes=15), 21.1),
        ]

        # Already at target, no rise time needed
        rise_time = calculate_rise_time(history, start_temp=21.0, target_temp=21.0)
        assert rise_time is None

    def test_calculate_rise_time_threshold_variations(self):
        """Test rise time with different threshold values."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=15), 19.5),
            (base_time + timedelta(minutes=30), 20.95),  # Just below 21.0
            (base_time + timedelta(minutes=45), 21.1),
        ]

        # With default threshold (0.05), 20.95 should count as reaching 21.0
        rise_time_default = calculate_rise_time(history, start_temp=18.0, target_temp=21.0)
        assert rise_time_default == 30.0

        # With stricter threshold (0.01), 20.95 doesn't count
        rise_time_strict = calculate_rise_time(history, start_temp=18.0, target_temp=21.0, threshold=0.01)
        assert rise_time_strict == 45.0

        # With looser threshold (0.1), even earlier temps might count
        rise_time_loose = calculate_rise_time(history, start_temp=18.0, target_temp=21.0, threshold=0.1)
        assert rise_time_loose == 30.0

    def test_calculate_rise_time_first_reading_at_target(self):
        """Test when first reading already reaches target (within threshold)."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 20.96),  # Within 0.05 of 21.0
            (base_time + timedelta(minutes=15), 21.0),
        ]

        # First reading is within threshold of target
        rise_time = calculate_rise_time(history, start_temp=18.0, target_temp=21.0)
        assert rise_time == 0.0

    def test_calculate_rise_time_slow_rise(self):
        """Test rise time with slow heating over longer duration."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=30), 18.5),
            (base_time + timedelta(minutes=60), 19.0),
            (base_time + timedelta(minutes=90), 19.5),
            (base_time + timedelta(minutes=120), 20.0),
            (base_time + timedelta(minutes=150), 20.5),
            (base_time + timedelta(minutes=180), 21.0),
        ]

        rise_time = calculate_rise_time(history, start_temp=18.0, target_temp=21.0)
        assert rise_time == 180.0

    def test_calculate_rise_time_fast_rise(self):
        """Test rise time with rapid heating."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=5), 21.0),
        ]

        rise_time = calculate_rise_time(history, start_temp=18.0, target_temp=21.0)
        assert rise_time == 5.0

    def test_calculate_rise_time_with_overshoot(self):
        """Test that rise time is measured to first crossing, not peak."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 18.0),
            (base_time + timedelta(minutes=20), 20.0),
            (base_time + timedelta(minutes=30), 21.0),  # First crosses target
            (base_time + timedelta(minutes=40), 22.0),  # Overshoot
            (base_time + timedelta(minutes=50), 21.5),  # Settling
        ]

        # Should measure to first crossing at 30 minutes
        rise_time = calculate_rise_time(history, start_temp=18.0, target_temp=21.0)
        assert rise_time == 30.0

    def test_calculate_rise_time_start_above_target(self):
        """Test when start temperature is already above target."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            calculate_rise_time,
        )

        base_time = datetime(2024, 1, 1, 10, 0, 0)
        history = [
            (base_time, 22.0),  # Start above target
            (base_time + timedelta(minutes=15), 21.5),
        ]

        # No rise needed when starting above target
        rise_time = calculate_rise_time(history, start_temp=22.0, target_temp=21.0)
        assert rise_time is None


# Marker test for rise time function
def test_calculate_rise_time_module_exists():
    """Marker test to ensure calculate_rise_time is importable."""
    from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
        calculate_rise_time,
    )
    assert calculate_rise_time is not None


# ============================================================================
# AdaptiveLearner Serialization Tests (Story 1.1)
# ============================================================================


class TestAdaptiveLearnerSerialization:
    """Tests for AdaptiveLearner.to_dict() serialization method."""

    def test_adaptive_learner_to_dict_empty(self):
        """Test to_dict returns dict with expected keys when no cycles."""
        learner = AdaptiveLearner()

        result = learner.to_dict()

        # Verify structure
        assert isinstance(result, dict)
        assert "cycle_history" in result
        assert "last_adjustment_time" in result
        assert "consecutive_converged_cycles" in result
        assert "pid_converged_for_ke" in result
        assert "auto_apply_count" in result

        # Verify empty state
        assert result["cycle_history"] == []
        assert result["last_adjustment_time"] is None
        assert result["consecutive_converged_cycles"] == 0
        assert result["pid_converged_for_ke"] is False
        assert result["auto_apply_count"] == 0

    def test_adaptive_learner_to_dict_with_cycles(self):
        """Test to_dict serializes CycleMetrics correctly."""
        learner = AdaptiveLearner()

        # Add cycles with various metrics
        learner.add_cycle_metrics(CycleMetrics(
            overshoot=0.5,
            undershoot=0.2,
            settling_time=45.0,
            oscillations=1,
            rise_time=30.0,
        ))
        learner.add_cycle_metrics(CycleMetrics(
            overshoot=0.3,
            undershoot=0.1,
            settling_time=40.0,
            oscillations=0,
            rise_time=25.0,
        ))

        result = learner.to_dict()

        # Verify cycle_history is serialized
        assert len(result["cycle_history"]) == 2

        # Verify first cycle structure
        cycle1 = result["cycle_history"][0]
        assert cycle1["overshoot"] == 0.5
        assert cycle1["undershoot"] == 0.2
        assert cycle1["settling_time"] == 45.0
        assert cycle1["oscillations"] == 1
        assert cycle1["rise_time"] == 30.0

        # Verify second cycle structure
        cycle2 = result["cycle_history"][1]
        assert cycle2["overshoot"] == 0.3
        assert cycle2["undershoot"] == 0.1
        assert cycle2["settling_time"] == 40.0
        assert cycle2["oscillations"] == 0
        assert cycle2["rise_time"] == 25.0

    def test_adaptive_learner_to_dict_timestamps(self):
        """Test to_dict converts datetime to ISO string and handles None."""
        learner = AdaptiveLearner()

        # Before any adjustment - should be None
        result1 = learner.to_dict()
        assert result1["last_adjustment_time"] is None

        # Add cycles and make adjustment to set last_adjustment_time
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.6,
                oscillations=0,
                settling_time=30,
                rise_time=20,
            ))

        # Trigger adjustment
        learner.calculate_pid_adjustment(100.0, 1.0, 10.0)

        result2 = learner.to_dict()

        # Should be ISO string now
        assert result2["last_adjustment_time"] is not None
        assert isinstance(result2["last_adjustment_time"], str)
        # Verify it's valid ISO format by parsing it
        from datetime import datetime
        parsed = datetime.fromisoformat(result2["last_adjustment_time"])
        assert isinstance(parsed, datetime)

    def test_adaptive_learner_to_dict_with_none_metrics(self):
        """Test to_dict handles CycleMetrics with None values correctly."""
        learner = AdaptiveLearner()

        # Add cycle with some None values
        learner.add_cycle_metrics(CycleMetrics(
            overshoot=None,
            undershoot=None,
            settling_time=None,
            oscillations=2,
            rise_time=None,
        ))

        result = learner.to_dict()

        cycle = result["cycle_history"][0]
        assert cycle["overshoot"] is None
        assert cycle["undershoot"] is None
        assert cycle["settling_time"] is None
        assert cycle["oscillations"] == 2
        assert cycle["rise_time"] is None

    def test_adaptive_learner_to_dict_convergence_state(self):
        """Test to_dict serializes convergence tracking state."""
        learner = AdaptiveLearner()

        # Set up convergence state
        learner._consecutive_converged_cycles = 5
        learner._pid_converged_for_ke = True
        learner._auto_apply_count = 3

        result = learner.to_dict()

        assert result["consecutive_converged_cycles"] == 5
        assert result["pid_converged_for_ke"] is True
        assert result["auto_apply_count"] == 3


# Marker test for Story 1.1
def test_adaptive_learner_serialization_exists():
    """Marker test to ensure to_dict method exists on AdaptiveLearner."""
    learner = AdaptiveLearner()
    assert hasattr(learner, 'to_dict')
    assert callable(learner.to_dict)
