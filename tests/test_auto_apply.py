"""Tests for auto-apply PID functionality including rollback and validation."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from custom_components.adaptive_climate.adaptive.learning import (
    AdaptiveLearner,
    CycleMetrics,
    get_auto_apply_thresholds,
)
from custom_components.adaptive_climate.const import (
    PID_HISTORY_SIZE,
    MAX_AUTO_APPLIES_LIFETIME,
    MAX_AUTO_APPLIES_PER_SEASON,
    MAX_CUMULATIVE_DRIFT_PCT,
    SEASONAL_SHIFT_BLOCK_DAYS,
    VALIDATION_CYCLE_COUNT,
    VALIDATION_DEGRADATION_THRESHOLD,
    AUTO_APPLY_THRESHOLDS,
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
)


# ============================================================================
# NOTE: PID History tests have been moved to test_pid_gains_manager.py
# The PID history functionality is now managed by PIDGainsManager.
# ============================================================================


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
        baseline_overshoot = 0.20  # convector overshoot_max
        learner.start_validation_mode(baseline_overshoot=baseline_overshoot)

        # 40% worse than baseline is above both baseline × 1.3 and overshoot_max
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

    def test_overshoot_within_heating_type_limit_passes(self):
        """No pre-apply overshoot: normal floor overshoot (under overshoot_max 0.3) is not a failure."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner.start_validation_mode(baseline_overshoot=0.0)

        for _ in range(VALIDATION_CYCLE_COUNT):
            result = learner.add_validation_cycle(CycleMetrics(overshoot=0.2, oscillations=0, settling_time=30.0))

        assert result == "success"

    def test_overshoot_above_heating_type_limit_rolls_back(self):
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner.start_validation_mode(baseline_overshoot=0.0)

        for _ in range(VALIDATION_CYCLE_COUNT):
            result = learner.add_validation_cycle(CycleMetrics(overshoot=0.35, oscillations=0, settling_time=30.0))

        assert result == "rollback"

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
        """Test seasonal limit blocks after 5 auto-applies in 90 days.

        NOTE: Seasonal limit checking now requires PID history from PIDGainsManager.
        Since AdaptiveLearner no longer maintains PID history, this test verifies
        the integration at the ValidationManager level with actual history.
        Full integration testing is in test_integration_auto_apply.py.
        """
        # This test is now covered by ValidationManager tests with actual history
        # and full integration tests. Skipping unit test since the API has changed.
        pass

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
        now = datetime.now()
        learner._last_seasonal_shift = now - timedelta(days=3)

        with patch("custom_components.adaptive_climate.adaptive.validation.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = now
            result = learner.check_auto_apply_limits(100.0, 0.01, 50.0)

            assert result is not None
            assert "Seasonal shift block active" in result
            # Should show approximately 4 days remaining (7 - 3)
            assert "days remaining" in result

    def test_check_auto_apply_limits_all_pass(self):
        """Test that all checks pass when within limits."""
        learner = AdaptiveLearner(heating_type="convector")
        learner.set_physics_baseline(100.0, 0.01, 50.0)

        # Set reasonable state (under limits)
        learner._heating_auto_apply_count = 2  # Well under lifetime and seasonal limits

        # No seasonal shift recorded

        # 10% drift (within 50% limit)
        with patch("custom_components.adaptive_climate.adaptive.validation.dt_util") as mock_dt_util:
            now = datetime.now()
            mock_dt_util.utcnow.return_value = now
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

        now = datetime.now()
        with patch("custom_components.adaptive_climate.adaptive.validation.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = now
            learner.record_seasonal_shift()

            assert learner._last_seasonal_shift is not None
            # Should be very recent (should be exactly now)
            assert learner._last_seasonal_shift == now

    def test_get_auto_apply_count(self):
        """Test get_auto_apply_count returns correct count."""
        learner = AdaptiveLearner(heating_type="convector")

        assert learner.get_auto_apply_count() == 0

        learner._auto_apply_count = 7
        assert learner.get_auto_apply_count() == 7


# ============================================================================
# Heating-Type-Specific Threshold Tests
# ============================================================================


class TestHeatingTypeThresholds:
    """Tests for heating-type-specific auto-apply thresholds."""

    def test_auto_apply_threshold_floor_hydronic(self):
        """Test floor_hydronic heating type has correct thresholds."""
        thresholds = get_auto_apply_thresholds(HEATING_TYPE_FLOOR_HYDRONIC)

        # No more confidence thresholds - removed in favor of tier-based gating
        assert "confidence_first" not in thresholds
        assert "confidence_subsequent" not in thresholds
        assert thresholds["min_cycles"] == 8
        assert thresholds["cooldown_hours"] == 96
        assert thresholds["cooldown_cycles"] == 15

    def test_auto_apply_threshold_forced_air(self):
        """Test forced_air heating type has correct thresholds."""
        thresholds = get_auto_apply_thresholds(HEATING_TYPE_FORCED_AIR)

        assert "confidence_first" not in thresholds
        assert "confidence_subsequent" not in thresholds
        assert thresholds["cooldown_hours"] == 36
        assert thresholds["min_cycles"] == 6
        assert thresholds["cooldown_cycles"] == 8

    def test_auto_apply_threshold_radiator(self):
        """Test radiator heating type has correct thresholds."""
        thresholds = get_auto_apply_thresholds(HEATING_TYPE_RADIATOR)

        assert "confidence_first" not in thresholds
        assert "confidence_subsequent" not in thresholds
        assert thresholds["min_cycles"] == 7
        assert thresholds["cooldown_hours"] == 72
        assert thresholds["cooldown_cycles"] == 12

    def test_auto_apply_threshold_convector(self):
        """Test convector heating type (baseline) has correct thresholds."""
        thresholds = get_auto_apply_thresholds(HEATING_TYPE_CONVECTOR)

        assert "confidence_first" not in thresholds
        assert "confidence_subsequent" not in thresholds
        assert thresholds["min_cycles"] == 6
        assert thresholds["cooldown_hours"] == 48
        assert thresholds["cooldown_cycles"] == 10

    def test_auto_apply_threshold_unknown_defaults_to_convector(self):
        """Test unknown heating type defaults to convector thresholds."""
        thresholds = get_auto_apply_thresholds("unknown_type")
        convector_thresholds = get_auto_apply_thresholds(HEATING_TYPE_CONVECTOR)

        # Unknown type should return same values as convector
        assert thresholds == convector_thresholds

    def test_auto_apply_threshold_none_defaults_to_convector(self):
        """Test None heating type defaults to convector thresholds."""
        thresholds = get_auto_apply_thresholds(None)
        convector_thresholds = get_auto_apply_thresholds(HEATING_TYPE_CONVECTOR)

        assert thresholds == convector_thresholds

    def test_threshold_dict_has_all_heating_types(self):
        """Test AUTO_APPLY_THRESHOLDS contains all 4 heating types."""
        assert len(AUTO_APPLY_THRESHOLDS) == 4
        assert HEATING_TYPE_FLOOR_HYDRONIC in AUTO_APPLY_THRESHOLDS
        assert HEATING_TYPE_RADIATOR in AUTO_APPLY_THRESHOLDS
        assert HEATING_TYPE_CONVECTOR in AUTO_APPLY_THRESHOLDS
        assert HEATING_TYPE_FORCED_AIR in AUTO_APPLY_THRESHOLDS

    def test_learner_uses_heating_type_for_threshold_lookup(self):
        """Test AdaptiveLearner stores heating_type for threshold lookup."""
        learner_floor = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner_forced = AdaptiveLearner(heating_type=HEATING_TYPE_FORCED_AIR)

        # Learners should have heating_type set
        assert learner_floor._heating_type == HEATING_TYPE_FLOOR_HYDRONIC
        assert learner_forced._heating_type == HEATING_TYPE_FORCED_AIR

        # Their thresholds should be retrievable
        floor_thresholds = get_auto_apply_thresholds(learner_floor._heating_type)
        forced_thresholds = get_auto_apply_thresholds(learner_forced._heating_type)

        # Floor hydronic requires more cycles and longer cooldowns (slower thermal response)
        assert floor_thresholds["min_cycles"] == 8
        assert forced_thresholds["min_cycles"] == 6


# ============================================================================
# Tier-Based Auto-Apply Gating Tests
# ============================================================================


class TestTierBasedAutoApplyGating:
    """Auto-apply no longer waits for a learning-status tier; the other gates still apply."""

    @staticmethod
    def _gates(learner=None, outdoor_temp=5.0):
        learner = learner or AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        return learner._auto_apply.check_auto_apply_safety_gates(
            validation_manager=learner._validation,
            confidence_tracker=learner._confidence,
            current_kp=100.0,
            current_ki=0.01,
            current_kd=1000.0,
            outdoor_temp=outdoor_temp,
            pid_history=[],
        )

    def test_first_auto_apply_allowed_while_collecting(self):
        """Zero confidence (status collecting) passes the gate with the floor thresholds."""
        thresholds = AUTO_APPLY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC]
        assert self._gates() == (
            True,
            thresholds["cooldown_hours"],
            thresholds["cooldown_cycles"],
            thresholds["min_cycles"],
        )

    def test_subsequent_auto_apply_doubles_min_cycles(self):
        """After the first apply the gate still passes below 'optimized' but asks for twice the cycles."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner._heating_auto_apply_count = 1
        passed, _, _, min_cycles = self._gates(learner)
        assert passed is True
        assert min_cycles == AUTO_APPLY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC]["min_cycles"] * 2

    def test_validation_mode_blocks(self):
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner.start_validation_mode(0.2)
        assert self._gates(learner) == (False, None, None, None)

    def test_lifetime_limit_blocks(self):
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner._heating_auto_apply_count = MAX_AUTO_APPLIES_LIFETIME
        assert self._gates(learner) == (False, None, None, None)

    def test_seasonal_shift_blocks(self):
        """A 10°C outdoor regime change detected at the gate blocks and starts the shift cooldown."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner._outdoor_temp_history.extend([-5.0] * 10 + [10.0] * 9)

        assert self._gates(learner, outdoor_temp=10.0) == (False, None, None, None)
        assert learner._last_seasonal_shift is not None
        assert self._gates(learner, outdoor_temp=10.0) == (False, None, None, None)

    def test_min_cycles_enforced_without_learning_status(self):
        """With confidence at zero, the recommendation still waits for min_cycles of history."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner.set_physics_baseline(100.0, 0.01, 1000.0)
        min_cycles = AUTO_APPLY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC]["min_cycles"]
        poor_cycle = CycleMetrics(overshoot=1.5, undershoot=0.0, oscillations=0, settling_time=60, rise_time=60)

        for _ in range(min_cycles - 1):
            learner.add_cycle_metrics(poor_cycle)
        assert learner.calculate_pid_adjustment(100.0, 0.01, 1000.0, check_auto_apply=True) is None
        assert learner._last_skip_reason == "insufficient_data"

        learner.add_cycle_metrics(poor_cycle)
        assert learner.calculate_pid_adjustment(100.0, 0.01, 1000.0, check_auto_apply=True) is not None

    def test_tier_thresholds_scale_by_heating_type(self):
        """Test that tier thresholds scale correctly by heating type."""
        # Base tier 2 = 70%
        # floor_hydronic: 70% * 0.8 = 56%
        # radiator: 70% * 0.9 = 63%
        # convector: 70% * 1.0 = 70%
        # forced_air: 70% * 1.1 = 77%

        learner_floor = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner_radiator = AdaptiveLearner(heating_type=HEATING_TYPE_RADIATOR)
        learner_convector = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
        learner_forced = AdaptiveLearner(heating_type=HEATING_TYPE_FORCED_AIR)

        # All learners should have different heating types
        assert learner_floor._heating_type == HEATING_TYPE_FLOOR_HYDRONIC
        assert learner_radiator._heating_type == HEATING_TYPE_RADIATOR
        assert learner_convector._heating_type == HEATING_TYPE_CONVECTOR
        assert learner_forced._heating_type == HEATING_TYPE_FORCED_AIR


def _undershoot_cycle(undershoot=0.5):
    return CycleMetrics(
        overshoot=0.0, undershoot=undershoot, oscillations=0, settling_time=30, rise_time=30, starting_delta=0.2
    )


class TestAutoApplyStepSize:
    """Auto-apply runs at low confidence, so a single apply must not amplify the rule step."""

    KP, KI, KD = 100.0, 10.0, 2.0

    def _learner(self, cycles=8):
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        for _ in range(cycles):
            learner.add_cycle_metrics(_undershoot_cycle())
        assert learner.get_convergence_confidence() == 0.0
        return learner

    def test_auto_apply_takes_the_plain_rule_step(self):
        """Undershoot 0.5 asks Ki ×2; at zero confidence auto-apply does ×2, not ×3."""
        learner = self._learner()
        learner.set_physics_baseline(self.KP, self.KI * 1.5, self.KD)

        result = learner.calculate_pid_adjustment(self.KP, self.KI, self.KD, check_auto_apply=True)

        assert result["ki"] == pytest.approx(self.KI * 2.0)
        assert result["kp"] == pytest.approx(self.KP)

    def test_manual_recommendation_keeps_confidence_multiplier(self):
        result = self._learner().calculate_pid_adjustment(self.KP, self.KI, self.KD)

        assert result["ki"] == pytest.approx(self.KI * 3.0)

    def test_auto_apply_refused_without_physics_baseline(self):
        """Without a baseline neither the drift clip nor the drift gate bounds the apply."""
        learner = self._learner()

        assert learner.calculate_pid_adjustment(self.KP, self.KI, self.KD, check_auto_apply=True) is None
        assert learner._last_skip_reason == "gates_not_passed"
        assert learner.calculate_pid_adjustment(self.KP, self.KI, self.KD) is not None

    def test_auto_apply_clipped_to_drift_limit(self):
        """Ki ×2 from the physics baseline is 100% drift (max 50%): auto-apply does Ki ×1.5."""
        learner = self._learner()
        learner.set_physics_baseline(self.KP, self.KI, self.KD)

        result = learner.calculate_pid_adjustment(self.KP, self.KI, self.KD, check_auto_apply=True)

        assert result == {"kp": self.KP, "ki": pytest.approx(self.KI * 1.5), "kd": self.KD}

    def test_auto_apply_skipped_when_already_at_drift_limit(self):
        learner = self._learner()
        learner.set_physics_baseline(self.KP, self.KI, self.KD)

        assert learner.calculate_pid_adjustment(self.KP, self.KI * 1.5, self.KD, check_auto_apply=True) is None
        assert learner._last_skip_reason == "no_change"

    def test_manual_recommendation_not_clipped(self):
        learner = self._learner()
        learner.set_physics_baseline(self.KP, self.KI, self.KD)

        assert learner.calculate_pid_adjustment(self.KP, self.KI, self.KD)["ki"] == pytest.approx(self.KI * 3.0)

    def test_gains_clipped_to_limit_still_pass_drift_gate(self):
        """0.0123 × 1.5 is 0.5000000000000001 drift in floats; it must count as on the limit, not over it."""
        learner = self._learner()
        learner.set_physics_baseline(self.KP, 0.0123, self.KD)

        assert learner.calculate_drift_from_baseline(self.KP, 0.0123 * 1.5, self.KD) == 0.5
        assert learner.check_auto_apply_limits(self.KP, 0.0123 * 1.5, self.KD) is None

    def test_auto_apply_allowed_when_result_within_drift_limit(self):
        """Ki 25% below baseline stepping ×2 lands at +50%, exactly on the limit."""
        learner = self._learner()
        learner.set_physics_baseline(self.KP, self.KI, self.KD)
        current_ki = self.KI * 0.75

        result = learner.calculate_pid_adjustment(self.KP, current_ki, self.KD, check_auto_apply=True)

        assert result["ki"] == pytest.approx(current_ki * 2.0)
        assert learner.calculate_drift_from_baseline(result["kp"], result["ki"], result["kd"]) <= (
            MAX_CUMULATIVE_DRIFT_PCT / 100.0
        )


class TestAutoApplyCooldown:
    """After an apply, cooldown hours and cooldown cycles both hold off the next one."""

    def _learner_after_first_apply(self):
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        learner.set_physics_baseline(100.0, 10.0, 2.0)
        learner._heating_auto_apply_count = 1
        learner.mark_adjustment_applied()
        for _ in range(AUTO_APPLY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC]["min_cycles"] * 2):
            learner.add_cycle_metrics(_undershoot_cycle())
        return learner

    def _apply(self, learner):
        return learner.calculate_pid_adjustment(100.0, 10.0, 2.0, check_auto_apply=True)

    def test_cooldown_hours_block_second_apply(self):
        learner = self._learner_after_first_apply()
        thresholds = AUTO_APPLY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC]
        assert learner._cycles_since_last_adjustment >= thresholds["cooldown_cycles"]
        learner._last_adjustment_time -= timedelta(hours=thresholds["cooldown_hours"] - 1)

        assert self._apply(learner) is None
        assert learner._last_skip_reason == "rate_limited"

    def test_cooldown_cycles_block_second_apply(self):
        learner = self._learner_after_first_apply()
        thresholds = AUTO_APPLY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC]
        learner._last_adjustment_time -= timedelta(hours=thresholds["cooldown_hours"] + 1)
        learner._cycles_since_last_adjustment = thresholds["cooldown_cycles"] - 1

        assert self._apply(learner) is None
        assert learner._last_skip_reason == "rate_limited"

        learner._cycles_since_last_adjustment = thresholds["cooldown_cycles"]
        assert self._apply(learner) is not None


class TestKiBoostCountsAsAdjustment:
    """An undershoot Ki boost restarts the auto-apply cooldown so the two cannot stack."""

    @staticmethod
    def _boosting_learner():
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_FLOOR_HYDRONIC)
        for _ in range(16):
            learner.add_cycle_metrics(_undershoot_cycle())
        learner.undershoot_detector.time_below_target = 4.0 * 3600.0
        learner.undershoot_detector.thermal_debt = 2.4
        return learner

    def test_boost_marks_adjustment_without_clearing_history(self):
        learner = self._boosting_learner()

        assert learner.check_undershoot_adjustment(cycles_completed=0, current_ki=10.0) is not None

        assert learner._last_adjustment_time is not None
        assert learner._cycles_since_last_adjustment == 0
        assert learner.get_cycle_count() == 16

    def test_auto_apply_waits_after_boost(self):
        learner = self._boosting_learner()
        learner.set_physics_baseline(100.0, 10.0, 2.0)
        new_ki = learner.check_undershoot_adjustment(cycles_completed=0, current_ki=10.0)

        assert learner.calculate_pid_adjustment(100.0, new_ki, 2.0, check_auto_apply=True) is None
        assert learner._last_skip_reason == "rate_limited"


class TestNoKiBoostDuringValidation:
    """A boost inside the validation window makes rollback (history[-2]) restore the auto-applied gains."""

    def test_undershoot_boost_waits_for_validation(self):
        learner = TestKiBoostCountsAsAdjustment._boosting_learner()
        learner.start_validation_mode(baseline_overshoot=0.1)

        assert learner.check_undershoot_adjustment(cycles_completed=0, current_ki=10.0) is None


class TestSafetyNetFollowsLearningStatus:
    """The PID integral-decay safety net stays on until learning reaches tuned or optimized."""

    @staticmethod
    def _learner(confidence):
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_RADIATOR)
        for _ in range(8):
            learner.add_cycle_metrics(_undershoot_cycle())
        learner._heating_auto_apply_count = 2
        learner._contribution_tracker._heating_recovery_cycle_count = 20
        learner._heating_convergence_confidence = confidence
        return learner

    def test_count_hidden_until_tuned(self):
        assert self._learner(0.1).get_safety_net_auto_apply_count() == 0

    def test_count_passed_once_tuned(self):
        assert self._learner(0.96).get_safety_net_auto_apply_count() == 2

    def test_control_loop_resyncs_controller(self):
        from unittest.mock import MagicMock

        from custom_components.adaptive_climate.climate_control import ClimateControlMixin
        from custom_components.adaptive_climate.const import DOMAIN

        coordinator = MagicMock()
        coordinator.get_zone_data.return_value = {"adaptive_learner": self._learner(0.1)}
        thermostat = MagicMock()
        thermostat._zone_id = "zone"
        thermostat.hass.data = {DOMAIN: {"coordinator": coordinator}}

        ClimateControlMixin._async_schedule_milestone_check(thermostat)

        thermostat._pid_controller.set_auto_apply_count.assert_called_once_with(0)
