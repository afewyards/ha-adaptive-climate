"""Tests for auto-apply PID functionality including history, rollback, and validation."""

import pytest
from datetime import datetime, timedelta

from custom_components.adaptive_thermostat.adaptive.learning import (
    AdaptiveLearner,
    CycleMetrics,
)
from custom_components.adaptive_thermostat.const import (
    PID_HISTORY_SIZE,
    MAX_AUTO_APPLIES_LIFETIME,
    MAX_AUTO_APPLIES_PER_SEASON,
    MAX_CUMULATIVE_DRIFT_PCT,
    SEASONAL_SHIFT_BLOCK_DAYS,
    VALIDATION_CYCLE_COUNT,
    VALIDATION_DEGRADATION_THRESHOLD,
)


# ============================================================================
# PID History Tests
# ============================================================================


class TestPIDHistory:
    """Tests for PID history recording and retrieval."""

    def test_record_pid_snapshot_basic(self):
        """Test recording PID snapshots and verifying list length."""
        learner = AdaptiveLearner(heating_type="convector")

        # Record 3 snapshots
        learner.record_pid_snapshot(100.0, 0.01, 50.0, reason="test1")
        learner.record_pid_snapshot(90.0, 0.012, 45.0, reason="test2")
        learner.record_pid_snapshot(95.0, 0.011, 48.0, reason="test3")

        history = learner.get_pid_history()
        assert len(history) == 3

        # Verify entries are in order
        assert history[0]["kp"] == 100.0
        assert history[0]["reason"] == "test1"
        assert history[1]["kp"] == 90.0
        assert history[2]["kp"] == 95.0

    def test_record_pid_snapshot_fifo_eviction(self):
        """Test FIFO eviction when exceeding PID_HISTORY_SIZE (10)."""
        learner = AdaptiveLearner(heating_type="convector")

        # Record 11 snapshots (exceeding PID_HISTORY_SIZE=10)
        for i in range(11):
            learner.record_pid_snapshot(
                100.0 + i, 0.01 + i * 0.001, 50.0 + i, reason=f"entry_{i}"
            )

        history = learner.get_pid_history()
        assert len(history) == PID_HISTORY_SIZE  # Should be 10

        # First entry should be evicted, oldest remaining is entry_1
        assert history[0]["reason"] == "entry_1"
        assert history[-1]["reason"] == "entry_10"

    def test_record_pid_snapshot_with_metrics(self):
        """Test recording snapshot with optional metrics."""
        learner = AdaptiveLearner(heating_type="convector")

        metrics = {"baseline_overshoot": 0.15, "confidence": 0.75}
        learner.record_pid_snapshot(100.0, 0.01, 50.0, reason="auto_apply", metrics=metrics)

        history = learner.get_pid_history()
        assert len(history) == 1
        assert history[0]["metrics"] == metrics

    def test_get_previous_pid_success(self):
        """Test get_previous_pid returns second-to-last entry."""
        learner = AdaptiveLearner(heating_type="convector")

        # Record 2 snapshots
        learner.record_pid_snapshot(100.0, 0.01, 50.0, reason="before_auto_apply")
        learner.record_pid_snapshot(90.0, 0.012, 45.0, reason="auto_apply")

        previous = learner.get_previous_pid()
        assert previous is not None
        assert previous["kp"] == 100.0
        assert previous["ki"] == 0.01
        assert previous["kd"] == 50.0
        assert previous["reason"] == "before_auto_apply"
        assert "timestamp" in previous

    def test_get_previous_pid_insufficient_history(self):
        """Test get_previous_pid returns None with < 2 entries."""
        learner = AdaptiveLearner(heating_type="convector")

        # No entries
        assert learner.get_previous_pid() is None

        # Only 1 entry
        learner.record_pid_snapshot(100.0, 0.01, 50.0, reason="single")
        assert learner.get_previous_pid() is None

    def test_get_pid_history_returns_copy(self):
        """Test that get_pid_history returns a copy to prevent mutation."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.record_pid_snapshot(100.0, 0.01, 50.0, reason="test")

        history1 = learner.get_pid_history()
        history2 = learner.get_pid_history()

        # Should be equal but not the same object
        assert history1 == history2
        assert history1 is not history2

        # Modifying the returned list should not affect internal state
        history1.clear()
        assert len(learner.get_pid_history()) == 1


# ============================================================================
# Physics Baseline and Drift Tests
# ============================================================================


class TestPhysicsBaselineAndDrift:
    """Tests for physics baseline setting and drift calculation."""

    def test_set_physics_baseline(self):
        """Test setting physics baseline values."""
        learner = AdaptiveLearner(heating_type="convector")

        learner.set_physics_baseline(100.0, 0.01, 50.0)

        # Baseline is set - now calculate drift with same values should return 0
        drift = learner.calculate_drift_from_baseline(100.0, 0.01, 50.0)
        assert drift == pytest.approx(0.0, abs=0.01)

    def test_calculate_drift_from_baseline(self):
        """Test drift calculation with 20% Kp change."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.set_physics_baseline(100.0, 0.01, 50.0)

        # 20% Kp drift (100 -> 120)
        drift = learner.calculate_drift_from_baseline(120.0, 0.01, 50.0)
        assert drift == pytest.approx(0.2, abs=0.01)  # 20% drift

    def test_calculate_drift_from_baseline_ki(self):
        """Test drift calculation with Ki change."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.set_physics_baseline(100.0, 0.01, 50.0)

        # 50% Ki drift (0.01 -> 0.015)
        drift = learner.calculate_drift_from_baseline(100.0, 0.015, 50.0)
        assert drift == pytest.approx(0.5, abs=0.01)  # 50% drift

    def test_calculate_drift_from_baseline_returns_max(self):
        """Test that drift returns max across all parameters."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.set_physics_baseline(100.0, 0.01, 50.0)

        # Kp: 10% drift, Ki: 30% drift, Kd: 20% drift
        # Should return max = 30%
        drift = learner.calculate_drift_from_baseline(110.0, 0.013, 60.0)
        assert drift == pytest.approx(0.30, abs=0.01)  # Ki drift is highest

    def test_calculate_drift_no_baseline(self):
        """Test calculate_drift returns 0.0 when no baseline set."""
        learner = AdaptiveLearner(heating_type="convector")

        # No baseline set
        drift = learner.calculate_drift_from_baseline(120.0, 0.012, 60.0)
        assert drift == 0.0

    def test_calculate_drift_zero_ki_baseline(self):
        """Test drift calculation handles zero Ki baseline gracefully."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.set_physics_baseline(100.0, 0.0, 50.0)  # Zero Ki

        # Should not cause division by zero
        drift = learner.calculate_drift_from_baseline(110.0, 0.01, 55.0)
        # Only Kp (10%) and Kd (10%) contribute
        assert drift == pytest.approx(0.10, abs=0.01)


# ============================================================================
# Validation Mode Tests
# ============================================================================


class TestValidationMode:
    """Tests for validation mode functionality."""

    def test_start_validation_mode(self):
        """Test starting validation mode sets correct state."""
        learner = AdaptiveLearner(heating_type="convector")

        assert learner.is_in_validation_mode() is False

        learner.start_validation_mode(baseline_overshoot=0.15)

        assert learner.is_in_validation_mode() is True
        assert learner._validation_baseline_overshoot == 0.15
        assert learner._validation_cycles == []

    def test_add_validation_cycle_collecting(self):
        """Test add_validation_cycle returns None while collecting cycles."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.start_validation_mode(baseline_overshoot=0.15)

        # Add 3 of 5 cycles (VALIDATION_CYCLE_COUNT = 5)
        for i in range(3):
            metrics = CycleMetrics(
                overshoot=0.1 + i * 0.01,
                oscillations=1,
                settling_time=30.0,
                rise_time=10.0,
                interruption_history=[],
            )
            result = learner.add_validation_cycle(metrics)
            assert result is None  # Still collecting

        assert learner.is_in_validation_mode() is True

    def test_add_validation_cycle_success(self):
        """Test add_validation_cycle returns 'success' when performance maintained."""
        learner = AdaptiveLearner(heating_type="convector")
        baseline_overshoot = 0.15
        learner.start_validation_mode(baseline_overshoot=baseline_overshoot)

        # Add 5 cycles with same/better overshoot (no degradation)
        for i in range(VALIDATION_CYCLE_COUNT):
            metrics = CycleMetrics(
                overshoot=0.15,  # Same as baseline
                oscillations=1,
                settling_time=30.0,
                rise_time=10.0,
                interruption_history=[],
            )
            result = learner.add_validation_cycle(metrics)

        assert result == "success"
        assert learner.is_in_validation_mode() is False

    def test_add_validation_cycle_degradation_triggers_rollback(self):
        """Test add_validation_cycle returns 'rollback' on >30% degradation."""
        learner = AdaptiveLearner(heating_type="convector")
        baseline_overshoot = 0.10  # 0.1°C baseline
        learner.start_validation_mode(baseline_overshoot=baseline_overshoot)

        # Add 5 cycles with 40% worse overshoot (degradation > 30% threshold)
        # 0.10 baseline + 40% = 0.14°C validation overshoot
        degraded_overshoot = baseline_overshoot * 1.4

        for i in range(VALIDATION_CYCLE_COUNT):
            metrics = CycleMetrics(
                overshoot=degraded_overshoot,
                oscillations=1,
                settling_time=30.0,
                rise_time=10.0,
                interruption_history=[],
            )
            result = learner.add_validation_cycle(metrics)

        assert result == "rollback"
        assert learner.is_in_validation_mode() is False

    def test_validation_mode_reset_on_clear_history(self):
        """Test that clear_history resets validation mode."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.start_validation_mode(baseline_overshoot=0.15)

        # Add some validation cycles
        metrics = CycleMetrics(
            overshoot=0.15,
            oscillations=1,
            settling_time=30.0,
            rise_time=10.0,
            interruption_history=[],
        )
        learner.add_validation_cycle(metrics)

        assert learner.is_in_validation_mode() is True

        # Clear history
        learner.clear_history()

        assert learner.is_in_validation_mode() is False
        assert learner._validation_cycles == []

    def test_add_validation_cycle_not_in_validation_mode(self):
        """Test add_validation_cycle returns None when not in validation mode."""
        learner = AdaptiveLearner(heating_type="convector")

        metrics = CycleMetrics(
            overshoot=0.15,
            oscillations=1,
            settling_time=30.0,
            rise_time=10.0,
            interruption_history=[],
        )

        result = learner.add_validation_cycle(metrics)
        assert result is None


# ============================================================================
# Auto-Apply Limits Tests
# ============================================================================


class TestAutoApplyLimits:
    """Tests for auto-apply safety limits checking."""

    def test_check_auto_apply_limits_lifetime(self):
        """Test lifetime limit blocks auto-apply at 20."""
        learner = AdaptiveLearner(heating_type="convector")
        learner._auto_apply_count = MAX_AUTO_APPLIES_LIFETIME  # 20

        result = learner.check_auto_apply_limits(100.0, 0.01, 50.0)

        assert result is not None
        assert "Lifetime limit reached" in result
        assert str(MAX_AUTO_APPLIES_LIFETIME) in result

    def test_check_auto_apply_limits_seasonal(self):
        """Test seasonal limit blocks after 5 auto-applies in 90 days."""
        learner = AdaptiveLearner(heating_type="convector")

        # Add 5 auto_apply entries within 90 days
        now = datetime.now()
        for i in range(5):
            learner._pid_history.append({
                "timestamp": now - timedelta(days=i * 10),  # Spread over 50 days
                "kp": 100.0,
                "ki": 0.01,
                "kd": 50.0,
                "reason": "auto_apply",
                "metrics": None,
            })

        result = learner.check_auto_apply_limits(100.0, 0.01, 50.0)

        assert result is not None
        assert "Seasonal limit reached" in result
        assert "90 days" in result

    def test_check_auto_apply_limits_drift(self):
        """Test drift limit blocks when >50% drift from baseline."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.set_physics_baseline(100.0, 0.01, 50.0)

        # 60% drift in Kp (100 -> 160)
        result = learner.check_auto_apply_limits(160.0, 0.01, 50.0)

        assert result is not None
        assert "Cumulative drift limit exceeded" in result
        assert "60.0%" in result

    def test_check_auto_apply_limits_seasonal_shift(self):
        """Test seasonal shift block after weather regime change."""
        learner = AdaptiveLearner(heating_type="convector")

        # Record seasonal shift 3 days ago
        learner._last_seasonal_shift = datetime.now() - timedelta(days=3)

        result = learner.check_auto_apply_limits(100.0, 0.01, 50.0)

        assert result is not None
        assert "Seasonal shift block active" in result
        # Should show approximately 4 days remaining (7 - 3)
        assert "days remaining" in result

    def test_check_auto_apply_limits_all_pass(self):
        """Test that all checks pass when within limits."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.set_physics_baseline(100.0, 0.01, 50.0)

        # Set reasonable state
        learner._auto_apply_count = 5  # Well under lifetime limit

        # Add 2 auto_apply entries (under seasonal limit)
        now = datetime.now()
        for i in range(2):
            learner._pid_history.append({
                "timestamp": now - timedelta(days=i * 10),
                "kp": 100.0,
                "ki": 0.01,
                "kd": 50.0,
                "reason": "auto_apply",
                "metrics": None,
            })

        # No seasonal shift recorded

        # 10% drift (within 50% limit)
        result = learner.check_auto_apply_limits(110.0, 0.01, 50.0)

        assert result is None  # All checks passed


# ============================================================================
# Seasonal Shift Recording Tests
# ============================================================================


class TestSeasonalShiftRecording:
    """Tests for seasonal shift recording and auto-apply count getter."""

    def test_record_seasonal_shift(self):
        """Test recording seasonal shift sets timestamp."""
        learner = AdaptiveLearner(heating_type="convector")

        assert learner._last_seasonal_shift is None

        learner.record_seasonal_shift()

        assert learner._last_seasonal_shift is not None
        # Should be very recent
        assert (datetime.now() - learner._last_seasonal_shift).total_seconds() < 1

    def test_get_auto_apply_count(self):
        """Test get_auto_apply_count returns correct count."""
        learner = AdaptiveLearner(heating_type="convector")

        assert learner.get_auto_apply_count() == 0

        learner._auto_apply_count = 7
        assert learner.get_auto_apply_count() == 7
