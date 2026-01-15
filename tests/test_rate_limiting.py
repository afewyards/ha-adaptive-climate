"""Tests for hybrid rate limiting (time AND cycles)."""

import pytest
from datetime import datetime, timedelta
from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics


@pytest.fixture
def learner():
    """Create an AdaptiveLearner instance for testing."""
    return AdaptiveLearner(heating_type="convector")


@pytest.fixture
def problem_cycle():
    """Create a cycle metric with problems (triggers rules, doesn't converge)."""
    return CycleMetrics(
        overshoot=0.6,  # High overshoot - triggers rule but not converged
        undershoot=0.0,
        settling_time=30.0,
        oscillations=0,
        rise_time=20.0,
        disturbances=None,
        interruption_history=[],
    )


class TestHybridRateLimiting:
    """Test hybrid rate limiting with both time and cycle gates."""

    def test_rate_limiting_hybrid_both_gates(self, learner, problem_cycle):
        """Verify both time AND cycle gates must be satisfied."""
        # Add 6 cycles to meet minimum for learning
        for _ in range(6):
            learner.add_cycle_metrics(problem_cycle)

        # First adjustment - should succeed (no rate limiting)
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_cycles=6,
            min_interval_hours=8,
            min_adjustment_cycles=3,
        )
        assert adjustment is not None, "First adjustment should succeed"
        assert learner._cycles_since_last_adjustment == 0, "Cycle counter should be reset"

        # Add 2 more cycles (total 2 since last adjustment)
        learner.add_cycle_metrics(problem_cycle)
        learner.add_cycle_metrics(problem_cycle)

        # Try adjustment - should fail (cycle gate not satisfied: 2 < 3)
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_cycles=6,
            min_interval_hours=8,
            min_adjustment_cycles=3,
        )
        assert adjustment is None, "Should be blocked by cycle gate"

        # Add 1 more cycle (total 3 since last adjustment)
        learner.add_cycle_metrics(problem_cycle)

        # Try adjustment immediately - should fail (time gate not satisfied: 0h < 8h)
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_cycles=6,
            min_interval_hours=8,
            min_adjustment_cycles=3,
        )
        assert adjustment is None, "Should be blocked by time gate"

        # Simulate 8 hours passing
        learner._last_adjustment_time = datetime.now() - timedelta(hours=8)

        # Try adjustment - should succeed (both gates satisfied)
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_cycles=6,
            min_interval_hours=8,
            min_adjustment_cycles=3,
        )
        assert adjustment is not None, "Should succeed with both gates satisfied"
        assert learner._cycles_since_last_adjustment == 0, "Cycle counter should be reset again"

    def test_rate_limiting_faster_convergence(self, learner, problem_cycle):
        """Verify 8h hybrid is faster than 24h time-only."""
        # Add 6 cycles to meet minimum for learning
        for _ in range(6):
            learner.add_cycle_metrics(problem_cycle)

        # First adjustment
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0, current_ki=2.0, current_kd=1.2
        )
        assert adjustment is not None

        # Add 3 more cycles
        for _ in range(3):
            learner.add_cycle_metrics(problem_cycle)

        # Simulate 8 hours passing (new hybrid gate)
        learner._last_adjustment_time = datetime.now() - timedelta(hours=8)

        # Should succeed with 8h hybrid gate
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0, current_ki=2.0, current_kd=1.2
        )
        assert adjustment is not None, "8h hybrid should allow adjustment"

        # Reset for 24h test
        learner.clear_history()
        for _ in range(6):
            learner.add_cycle_metrics(problem_cycle)

        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0, current_ki=2.0, current_kd=1.2
        )
        assert adjustment is not None

        # Add 3 more cycles
        for _ in range(3):
            learner.add_cycle_metrics(problem_cycle)

        # Simulate only 8 hours passing (would fail with 24h gate)
        learner._last_adjustment_time = datetime.now() - timedelta(hours=8)

        # With old 24h gate, this would fail
        adjustment_old = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_interval_hours=24,  # Old 24h gate
        )
        assert adjustment_old is None, "24h gate should block adjustment"

    def test_cycle_counter_increments_on_add(self, learner, problem_cycle):
        """Verify cycle counter increments on each add_cycle_metrics call."""
        assert learner._cycles_since_last_adjustment == 0

        learner.add_cycle_metrics(problem_cycle)
        assert learner._cycles_since_last_adjustment == 1

        learner.add_cycle_metrics(problem_cycle)
        assert learner._cycles_since_last_adjustment == 2

        learner.add_cycle_metrics(problem_cycle)
        assert learner._cycles_since_last_adjustment == 3

    def test_cycle_counter_resets_on_adjustment(self, learner, problem_cycle):
        """Verify cycle counter resets when adjustment is applied."""
        # Add 6 cycles
        for _ in range(6):
            learner.add_cycle_metrics(problem_cycle)

        assert learner._cycles_since_last_adjustment == 6

        # Make adjustment
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0, current_ki=2.0, current_kd=1.2
        )
        assert adjustment is not None
        assert learner._cycles_since_last_adjustment == 0

    def test_cycle_counter_resets_on_clear_history(self, learner, problem_cycle):
        """Verify cycle counter resets when history is cleared."""
        # Add some cycles
        for _ in range(5):
            learner.add_cycle_metrics(problem_cycle)

        assert learner._cycles_since_last_adjustment == 5

        # Clear history
        learner.clear_history()
        assert learner._cycles_since_last_adjustment == 0
        assert len(learner._cycle_history) == 0

    def test_time_gate_blocks_when_insufficient(self, learner, problem_cycle):
        """Verify time gate blocks adjustment when time < min_interval_hours."""
        # Add 6 cycles and make first adjustment
        for _ in range(6):
            learner.add_cycle_metrics(problem_cycle)

        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0, current_ki=2.0, current_kd=1.2
        )
        assert adjustment is not None

        # Add 3 cycles (satisfies cycle gate)
        for _ in range(3):
            learner.add_cycle_metrics(problem_cycle)

        # Simulate only 4 hours passing (less than 8h minimum)
        learner._last_adjustment_time = datetime.now() - timedelta(hours=4)

        # Should be blocked by time gate
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_interval_hours=8,
            min_adjustment_cycles=3,
        )
        assert adjustment is None

    def test_cycle_gate_blocks_when_insufficient(self, learner, problem_cycle):
        """Verify cycle gate blocks adjustment when cycles < min_adjustment_cycles."""
        # Add 6 cycles and make first adjustment
        for _ in range(6):
            learner.add_cycle_metrics(problem_cycle)

        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0, current_ki=2.0, current_kd=1.2
        )
        assert adjustment is not None

        # Add only 2 cycles (less than 3 minimum)
        learner.add_cycle_metrics(problem_cycle)
        learner.add_cycle_metrics(problem_cycle)

        # Simulate 8 hours passing (satisfies time gate)
        learner._last_adjustment_time = datetime.now() - timedelta(hours=8)

        # Should be blocked by cycle gate
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_interval_hours=8,
            min_adjustment_cycles=3,
        )
        assert adjustment is None

    def test_first_adjustment_not_rate_limited(self, learner, problem_cycle):
        """Verify first adjustment is never rate limited."""
        # Add 6 cycles
        for _ in range(6):
            learner.add_cycle_metrics(problem_cycle)

        # First adjustment should succeed immediately
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_interval_hours=8,
            min_adjustment_cycles=3,
        )
        assert adjustment is not None
        assert learner._last_adjustment_time is not None

    def test_custom_gates_configuration(self, learner, problem_cycle):
        """Verify custom time and cycle gates can be configured."""
        # Add 10 cycles and make first adjustment
        for _ in range(10):
            learner.add_cycle_metrics(problem_cycle)

        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0, current_ki=2.0, current_kd=1.2
        )
        assert adjustment is not None

        # Add 4 cycles
        for _ in range(4):
            learner.add_cycle_metrics(problem_cycle)

        # Simulate 12 hours passing
        learner._last_adjustment_time = datetime.now() - timedelta(hours=12)

        # Should fail with custom gates (5 cycles, 15 hours)
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_interval_hours=15,  # Custom time gate
            min_adjustment_cycles=5,  # Custom cycle gate
        )
        assert adjustment is None  # 4 cycles < 5

        # Add 1 more cycle (5 total)
        learner.add_cycle_metrics(problem_cycle)

        # Still fails (12h < 15h)
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_interval_hours=15,
            min_adjustment_cycles=5,
        )
        assert adjustment is None

        # Simulate 15 hours total
        learner._last_adjustment_time = datetime.now() - timedelta(hours=15)

        # Should succeed now
        adjustment = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=2.0,
            current_kd=1.2,
            min_interval_hours=15,
            min_adjustment_cycles=5,
        )
        assert adjustment is not None


def test_rate_limiting_module_exists():
    """Marker test to verify rate limiting functionality exists."""
    learner = AdaptiveLearner()
    assert hasattr(learner, "_cycles_since_last_adjustment")
    assert hasattr(learner, "_check_rate_limit")
