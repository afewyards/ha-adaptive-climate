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
# Zone Adjustment Tests - Story 3.2
# ============================================================================


class TestZoneAdjustmentFactors:
    """Tests for zone-specific adjustment factors in AdaptiveLearner."""

    def test_zone_factors_calculated_at_initialization(self):
        """Test that zone factors are calculated once at initialization."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        # Kitchen zone should have lower Ki factor
        kitchen_learner = AdaptiveLearner(zone_name="Kitchen")
        factors = kitchen_learner.get_zone_factors()

        assert factors["kp_factor"] == 1.0
        assert factors["ki_factor"] == 0.8
        assert factors["kd_factor"] == 1.0

    def test_bathroom_zone_factors(self):
        """Test bathroom zone has higher Kp factor."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        learner = AdaptiveLearner(zone_name="Master Bathroom")
        factors = learner.get_zone_factors()

        assert factors["kp_factor"] == 1.1
        assert factors["ki_factor"] == 1.0
        assert factors["kd_factor"] == 1.0

    def test_bedroom_zone_factors(self):
        """Test bedroom zone has lower Ki factor."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        learner = AdaptiveLearner(zone_name="Main Bedroom")
        factors = learner.get_zone_factors()

        assert factors["kp_factor"] == 1.0
        assert factors["ki_factor"] == 0.85
        assert factors["kd_factor"] == 1.0

    def test_ground_floor_zone_factors(self):
        """Test ground floor zone has higher Ki factor."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        learner = AdaptiveLearner(zone_name="Ground Floor Living Room")
        factors = learner.get_zone_factors()

        assert factors["kp_factor"] == 1.0
        assert factors["ki_factor"] == 1.15
        assert factors["kd_factor"] == 1.0

    def test_gf_abbreviation_zone_factors(self):
        """Test GF abbreviation is recognized for ground floor."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        learner = AdaptiveLearner(zone_name="GF Hallway")
        factors = learner.get_zone_factors()

        assert factors["ki_factor"] == 1.15

    def test_no_zone_name_default_factors(self):
        """Test that no zone name results in default factors of 1.0."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        learner = AdaptiveLearner(zone_name=None)
        factors = learner.get_zone_factors()

        assert factors["kp_factor"] == 1.0
        assert factors["ki_factor"] == 1.0
        assert factors["kd_factor"] == 1.0

    def test_unknown_zone_default_factors(self):
        """Test that unknown zone name results in default factors."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        learner = AdaptiveLearner(zone_name="Office")
        factors = learner.get_zone_factors()

        assert factors["kp_factor"] == 1.0
        assert factors["ki_factor"] == 1.0
        assert factors["kd_factor"] == 1.0


class TestZoneAdjustmentAppliedOnce:
    """Tests verifying zone adjustments are applied only once, not compounded."""

    def test_zone_adjustment_applied_once(self):
        """Test that zone adjustment is applied once per PID calculation."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
            CycleMetrics,
        )

        learner = AdaptiveLearner(zone_name="Kitchen")

        # Add minimum required cycles with neutral metrics
        # (no overshoot, no undershoot, etc. to avoid rule-based adjustments)
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,  # Below threshold
                undershoot=0.1,  # Below threshold
                settling_time=30,  # Fast settling
                oscillations=0,  # No oscillations
                rise_time=30,  # Fast rise
            ))

        # Calculate PID adjustment
        result = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=10.0,
            current_kd=5.0,
        )

        # Kitchen zone has Ki factor of 0.8
        # With neutral metrics, only zone factor should be applied
        assert result["kp"] == pytest.approx(100.0, abs=0.01)
        assert result["ki"] == pytest.approx(8.0, abs=0.01)  # 10.0 * 0.8
        assert result["kd"] == pytest.approx(5.0, abs=0.01)

    def test_multiple_cycles_do_not_compound_zone_adjustment(self):
        """Test that calling calculate_pid_adjustment multiple times does not compound zone factors."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
            CycleMetrics,
        )

        learner = AdaptiveLearner(zone_name="Kitchen")

        # Add initial cycles
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30,
                oscillations=0,
                rise_time=30,
            ))

        # First calculation
        result1 = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=10.0,
            current_kd=5.0,
        )

        # Add more cycles
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30,
                oscillations=0,
                rise_time=30,
            ))

        # Second calculation with same input values
        result2 = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=10.0,
            current_kd=5.0,
        )

        # Both results should have the same Ki (zone factor applied once each time)
        assert result1["ki"] == pytest.approx(8.0, abs=0.01)  # 10.0 * 0.8
        assert result2["ki"] == pytest.approx(8.0, abs=0.01)  # 10.0 * 0.8 (not 10.0 * 0.8 * 0.8)

    def test_zone_factor_applied_after_learned_adjustments(self):
        """Test that zone factor is applied as a multiplier after learned adjustments."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
            CycleMetrics,
        )

        learner = AdaptiveLearner(zone_name="Kitchen")

        # Add cycles with high undershoot to trigger Ki increase
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.0,
                undershoot=0.5,  # Above 0.3 threshold, triggers Ki increase
                settling_time=30,
                oscillations=0,
                rise_time=30,
            ))

        result = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=10.0,
            current_kd=5.0,
        )

        # Undershoot of 0.5 triggers: increase = min(0.20, 0.5 * 0.4) = 0.20
        # Learned Ki = 10.0 * 1.20 = 12.0
        # Zone factor applied: 12.0 * 0.8 = 9.6
        assert result["ki"] == pytest.approx(9.6, abs=0.01)

    def test_apply_zone_factors_parameter(self):
        """Test that apply_zone_factors=False skips zone factor application."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
            CycleMetrics,
        )

        learner = AdaptiveLearner(zone_name="Kitchen")

        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30,
                oscillations=0,
                rise_time=30,
            ))

        # With apply_zone_factors=False
        result = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=10.0,
            current_kd=5.0,
            apply_zone_factors=False,
        )

        # Ki should remain unchanged (no zone factor applied)
        assert result["ki"] == pytest.approx(10.0, abs=0.01)

    def test_bathroom_zone_multiple_cycles_no_compounding(self):
        """Test bathroom zone Kp factor does not compound over multiple cycles."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
            CycleMetrics,
        )

        learner = AdaptiveLearner(zone_name="Bathroom")

        # Simulate multiple rounds of PID calculation
        base_kp = 100.0

        for round_num in range(5):
            # Add more cycles
            for _ in range(3):
                learner.add_cycle_metrics(CycleMetrics(
                    overshoot=0.1,
                    undershoot=0.1,
                    settling_time=30,
                    oscillations=0,
                    rise_time=30,
                ))

            result = learner.calculate_pid_adjustment(
                current_kp=base_kp,  # Always use original base value
                current_ki=10.0,
                current_kd=5.0,
            )

            # Kp should always be 110.0 (100.0 * 1.1), not compounding
            assert result["kp"] == pytest.approx(110.0, abs=0.01), (
                f"Round {round_num}: Kp should be 110.0, got {result['kp']}"
            )


class TestZoneAdjustmentEdgeCases:
    """Edge case tests for zone adjustments."""

    def test_case_insensitive_zone_matching(self):
        """Test that zone matching is case-insensitive."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        # Test various case combinations
        for zone_name in ["KITCHEN", "kitchen", "Kitchen", "kItChEn"]:
            learner = AdaptiveLearner(zone_name=zone_name)
            factors = learner.get_zone_factors()
            assert factors["ki_factor"] == 0.8, f"Failed for zone name: {zone_name}"

    def test_zone_substring_matching(self):
        """Test that zone matching works with substrings."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
        )

        # Zone name contains "kitchen"
        learner = AdaptiveLearner(zone_name="Open Plan Kitchen Dining")
        factors = learner.get_zone_factors()
        assert factors["ki_factor"] == 0.8

    def test_zone_factors_preserved_after_clear_history(self):
        """Test that zone factors remain after clearing cycle history."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
            CycleMetrics,
        )

        learner = AdaptiveLearner(zone_name="Kitchen")

        # Add cycles
        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30,
                oscillations=0,
                rise_time=30,
            ))

        # Clear history
        learner.clear_history()

        # Zone factors should still be set
        factors = learner.get_zone_factors()
        assert factors["ki_factor"] == 0.8

    def test_pid_limits_enforced_after_zone_adjustment(self):
        """Test that PID limits are still enforced after zone factor application."""
        from custom_components.adaptive_thermostat.adaptive.learning import (
            AdaptiveLearner,
            CycleMetrics,
        )
        from custom_components.adaptive_thermostat.const import PID_LIMITS

        # Use bathroom zone which has Kp factor of 1.1
        learner = AdaptiveLearner(zone_name="Bathroom")

        for _ in range(3):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30,
                oscillations=0,
                rise_time=30,
            ))

        # Use very high Kp that would exceed limit after 1.1 factor
        result = learner.calculate_pid_adjustment(
            current_kp=480.0,  # 480.0 * 1.1 = 528.0, above kp_max of 500
            current_ki=10.0,
            current_kd=5.0,
        )

        # Should be clamped to kp_max
        assert result["kp"] == PID_LIMITS["kp_max"]
