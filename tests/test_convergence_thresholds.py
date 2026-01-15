"""Tests for heating-type-specific convergence thresholds."""

from custom_components.adaptive_thermostat.const import (
    get_convergence_thresholds,
    DEFAULT_CONVERGENCE_THRESHOLDS,
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
)
from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics


class TestConvergenceThresholdsHelper:
    """Test the get_convergence_thresholds helper function."""

    def test_get_convergence_thresholds_floor_hydronic(self):
        """Test that floor_hydronic returns relaxed thresholds."""
        thresholds = get_convergence_thresholds(HEATING_TYPE_FLOOR_HYDRONIC)

        assert thresholds["overshoot_max"] == 0.3  # Relaxed from 0.2
        assert thresholds["oscillations_max"] == 1  # Same as default
        assert thresholds["settling_time_max"] == 120  # Relaxed from 60
        assert thresholds["rise_time_max"] == 90  # Relaxed from 45

    def test_get_convergence_thresholds_radiator(self):
        """Test that radiator returns moderately relaxed thresholds."""
        thresholds = get_convergence_thresholds(HEATING_TYPE_RADIATOR)

        assert thresholds["overshoot_max"] == 0.25  # Slightly relaxed from 0.2
        assert thresholds["oscillations_max"] == 1  # Same as default
        assert thresholds["settling_time_max"] == 90  # Relaxed from 60
        assert thresholds["rise_time_max"] == 60  # Relaxed from 45

    def test_get_convergence_thresholds_convector(self):
        """Test that convector returns baseline (default) thresholds."""
        thresholds = get_convergence_thresholds(HEATING_TYPE_CONVECTOR)

        assert thresholds["overshoot_max"] == 0.2  # Baseline
        assert thresholds["oscillations_max"] == 1  # Baseline
        assert thresholds["settling_time_max"] == 60  # Baseline
        assert thresholds["rise_time_max"] == 45  # Baseline

    def test_get_convergence_thresholds_forced_air(self):
        """Test that forced_air returns tightened thresholds."""
        thresholds = get_convergence_thresholds(HEATING_TYPE_FORCED_AIR)

        assert thresholds["overshoot_max"] == 0.15  # Tightened from 0.2
        assert thresholds["oscillations_max"] == 1  # Same as default
        assert thresholds["settling_time_max"] == 45  # Tightened from 60
        assert thresholds["rise_time_max"] == 30  # Tightened from 45

    def test_get_convergence_thresholds_unknown_type(self):
        """Test that unknown heating type returns default thresholds."""
        thresholds = get_convergence_thresholds("unknown_type")

        assert thresholds == DEFAULT_CONVERGENCE_THRESHOLDS

    def test_get_convergence_thresholds_none(self):
        """Test that None heating type returns default thresholds."""
        thresholds = get_convergence_thresholds(None)

        assert thresholds == DEFAULT_CONVERGENCE_THRESHOLDS


class TestAdaptiveLearnerHeatingTypeThresholds:
    """Test that AdaptiveLearner uses heating-type-specific thresholds."""

    def test_adaptive_learner_floor_hydronic_thresholds(self):
        """Test floor_hydronic learner uses relaxed convergence criteria."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)

        # Verify internal thresholds are set correctly
        assert learner._convergence_thresholds["overshoot_max"] == 0.3
        assert learner._convergence_thresholds["settling_time_max"] == 120
        assert learner._convergence_thresholds["rise_time_max"] == 90

        # Add 6 cycles with performance within floor_hydronic thresholds
        # but outside default thresholds (overshoot=0.25, settling=100, rise=70)
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.25,  # Within 0.3 floor_hydronic limit, exceeds 0.2 default
                undershoot=0.1,
                settling_time=100,  # Within 120 min floor_hydronic limit, exceeds 60 default
                oscillations=0,
                rise_time=70,  # Within 90 min floor_hydronic limit, exceeds 45 default
            )
            learner.add_cycle_metrics(metrics)

        # Should detect convergence with relaxed floor_hydronic thresholds
        adjustment = learner.calculate_pid_adjustment(current_kp=100, current_ki=1.2, current_kd=2.5)
        assert adjustment is None, "Floor hydronic should converge with relaxed thresholds"

    def test_adaptive_learner_forced_air_thresholds(self):
        """Test forced_air learner uses tightened convergence criteria."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FORCED_AIR)

        # Verify internal thresholds are set correctly
        assert learner._convergence_thresholds["overshoot_max"] == 0.15
        assert learner._convergence_thresholds["settling_time_max"] == 45
        assert learner._convergence_thresholds["rise_time_max"] == 30

        # Add 6 cycles with performance within default thresholds
        # but outside forced_air tightened thresholds (overshoot=0.18, settling=55, rise=40)
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.18,  # Exceeds 0.15 forced_air limit, within 0.2 default
                undershoot=0.1,
                settling_time=55,  # Exceeds 45 min forced_air limit, within 60 default
                oscillations=0,
                rise_time=40,  # Exceeds 30 min forced_air limit, within 45 default
            )
            learner.add_cycle_metrics(metrics)

        # With tightened forced_air thresholds, this performance should trigger adjustments
        # The system should detect that it's not converged because metrics exceed thresholds
        # Check _check_convergence directly to verify threshold logic
        from custom_components.adaptive_thermostat.adaptive.robust_stats import robust_average

        overshoot_values = [m.overshoot for m in learner._cycle_history if m.overshoot is not None]
        settling_values = [m.settling_time for m in learner._cycle_history if m.settling_time is not None]
        rise_values = [m.rise_time for m in learner._cycle_history if m.rise_time is not None]

        # robust_average returns (average, count_removed) tuple
        avg_overshoot, _ = robust_average(overshoot_values)
        avg_settling, _ = robust_average(settling_values)
        avg_rise, _ = robust_average(rise_values)

        # All averages should exceed forced_air thresholds
        assert avg_overshoot > 0.15, f"Overshoot {avg_overshoot} should exceed 0.15"
        assert avg_settling > 45, f"Settling {avg_settling} should exceed 45"
        assert avg_rise > 30, f"Rise time {avg_rise} should exceed 30"

        # _check_convergence should return False with forced_air thresholds
        is_converged = learner._check_convergence(avg_overshoot, 0, avg_settling, avg_rise)
        assert not is_converged, "Forced air should not be converged with metrics exceeding thresholds"

    def test_adaptive_learner_default_thresholds_when_no_heating_type(self):
        """Test learner uses default thresholds when heating_type not specified."""
        learner = AdaptiveLearner()  # No heating_type specified

        # Should use default thresholds
        assert learner._convergence_thresholds == DEFAULT_CONVERGENCE_THRESHOLDS

    def test_adaptive_learner_persistence_of_heating_type(self):
        """Test that heating_type is stored and can be persisted."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_RADIATOR)

        # Verify heating_type is stored
        assert learner._heating_type == HEATING_TYPE_RADIATOR
        assert learner._convergence_thresholds["overshoot_max"] == 0.25


# Marker test
def test_convergence_thresholds_module_exists():
    """Marker test to ensure convergence threshold helpers are importable."""
    from custom_components.adaptive_thermostat.const import get_convergence_thresholds
    assert get_convergence_thresholds is not None
