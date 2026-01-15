"""Tests for PID rule engine in adaptive thermostat tuning."""

import pytest

from custom_components.adaptive_thermostat.adaptive.pid_rules import (
    evaluate_pid_rules,
    PIDRule,
)
from custom_components.adaptive_thermostat.adaptive.learning import (
    AdaptiveLearner,
    CycleMetrics,
)
from custom_components.adaptive_thermostat.const import PID_LIMITS


class TestUndershootRuleAggressive:
    """Tests for the aggressive undershoot rule (20% → 100% Ki increase)."""

    def test_undershoot_rule_aggressive_increase(self):
        """Test undershoot rule with 50% undershoot allows aggressive Ki increase."""
        # Test the rule evaluation directly
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.5,  # 0.5°C undershoot
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        # Should trigger undershoot rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.UNDERSHOOT

        # With 0.5°C undershoot: increase = min(1.0, 0.5 * 2.0) = 1.0
        # ki_factor = 1.0 + 1.0 = 2.0 (100% increase, doubling)
        assert results[0].ki_factor == 2.0
        assert results[0].kp_factor == 1.0  # No change to Kp
        assert results[0].kd_factor == 1.0  # No change to Kd

        # Verify reason message includes percentage
        assert "100% Ki" in results[0].reason or "+100% Ki" in results[0].reason

    def test_undershoot_rule_moderate_increase(self):
        """Test undershoot rule with moderate undershoot gives proportional increase."""
        # Test with 0.35°C undershoot
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.35,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        assert len(results) == 1
        assert results[0].rule == PIDRule.UNDERSHOOT

        # With 0.35°C undershoot: increase = min(1.0, 0.35 * 2.0) = 0.7
        # ki_factor = 1.0 + 0.7 = 1.7 (70% increase)
        expected_factor = 1.0 + min(1.0, 0.35 * 2.0)
        assert abs(results[0].ki_factor - expected_factor) < 0.001
        assert "70% Ki" in results[0].reason or "+70% Ki" in results[0].reason

    def test_undershoot_rule_convergence_speed(self):
        """Test that new aggressive rule converges faster than old 20% limit."""
        learner = AdaptiveLearner()

        # Add 6 cycles with significant undershoot (0.6°C)
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.0,
                undershoot=0.6,  # Significant undershoot
                oscillations=0.0,
                rise_time=50.0,
                settling_time=60.0,
            ))

        # Starting Ki = 2.0
        result = learner.calculate_pid_adjustment(100.0, 2.0, 10.0)

        assert result is not None

        # With 0.6°C undershoot: increase = min(1.0, 0.6 * 2.0) = 1.0 (capped)
        # ki_factor = 2.0 (100% increase)
        # New Ki should be approximately 2.0 * 2.0 = 4.0
        #
        # Old formula would have been:
        # increase = min(0.20, 0.6 * 0.4) = 0.20
        # ki_factor = 1.2 (20% increase)
        # Old Ki would be 2.0 * 1.2 = 2.4
        #
        # So new rule gives ~67% faster convergence (4.0 vs 2.4)

        # Allow some tolerance for learning rate scaling
        assert result["ki"] >= 3.5, f"Expected Ki >= 3.5, got {result['ki']}"
        assert result["ki"] > 2.5, "New aggressive rule should increase Ki more than old 20% limit"

    def test_undershoot_rule_safety_limits(self):
        """Test that undershoot rule respects ki_max safety limit."""
        learner = AdaptiveLearner()

        # Add cycles with extreme undershoot
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.0,
                undershoot=2.0,  # Extreme undershoot
                oscillations=0.0,
                rise_time=50.0,
                settling_time=60.0,
            ))

        # Start with Ki already near maximum
        ki_near_max = PID_LIMITS["ki_max"] - 50.0
        result = learner.calculate_pid_adjustment(100.0, ki_near_max, 10.0)

        assert result is not None

        # Even with extreme undershoot wanting 200% increase,
        # result should be clamped to ki_max
        assert result["ki"] <= PID_LIMITS["ki_max"]
        assert result["ki"] == PID_LIMITS["ki_max"], "Ki should be clamped to maximum"

    def test_undershoot_rule_gradient_based(self):
        """Test that larger undershoot gets proportionally larger correction."""
        # Small undershoot (0.35°C)
        results_small = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.35,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        # Large undershoot (0.7°C)
        results_large = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.7,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        assert len(results_small) == 1
        assert len(results_large) == 1

        # 0.35°C: increase = min(1.0, 0.35 * 2.0) = 0.7 → factor = 1.7
        # 0.7°C: increase = min(1.0, 0.7 * 2.0) = 1.0 → factor = 2.0
        assert results_large[0].ki_factor > results_small[0].ki_factor
        assert abs(results_small[0].ki_factor - 1.7) < 0.001
        assert abs(results_large[0].ki_factor - 2.0) < 0.001

    def test_undershoot_rule_no_trigger_below_threshold(self):
        """Test that undershoot rule doesn't trigger below 0.3°C threshold."""
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.25,  # Below 0.3°C threshold
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        # Should not trigger any rules
        assert len(results) == 0


class TestOvershootRuleKdIncrease:
    """Tests for overshoot rule changes to increase Kd instead of reducing Kp."""

    def test_overshoot_moderate_increases_kd(self):
        """Test moderate overshoot (0.5°C) increases Kd by 20% without touching Kp."""
        results = evaluate_pid_rules(
            avg_overshoot=0.5,  # Moderate overshoot (0.2-1.0°C range)
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        # Should trigger moderate overshoot rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.MODERATE_OVERSHOOT

        # For moderate overshoot: only increase Kd, don't touch Kp or Ki
        assert results[0].kp_factor == 1.0, "Kp should not change for moderate overshoot"
        assert results[0].ki_factor == 1.0, "Ki should not change for moderate overshoot"
        assert results[0].kd_factor == 1.20, "Kd should increase by 20%"

        # Verify reason message explains Kd increase
        assert "increase kd" in results[0].reason.lower()

    def test_overshoot_extreme_increases_both(self):
        """Test extreme overshoot (1.5°C) increases Kd AND reduces Kp."""
        results = evaluate_pid_rules(
            avg_overshoot=1.5,  # Extreme overshoot (>1.0°C)
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        # Should trigger high overshoot rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.HIGH_OVERSHOOT

        # For extreme overshoot: increase Kd by 20%, reduce Kp by 10%, reduce Ki by 10%
        assert results[0].kp_factor == 0.90, "Kp should reduce by 10% for extreme overshoot"
        assert results[0].ki_factor == 0.9, "Ki should reduce by 10% for extreme overshoot"
        assert results[0].kd_factor == 1.20, "Kd should increase by 20%"

        # Verify reason message explains both Kd increase and Kp reduction
        assert "increase Kd" in results[0].reason.lower() or "kd" in results[0].reason.lower()
        assert "reduce Kp" in results[0].reason.lower() or "kp" in results[0].reason.lower()

    def test_overshoot_threshold_boundary(self):
        """Test boundary between moderate and extreme overshoot at 1.0°C."""
        # Just below 1.0°C - should be moderate
        results_moderate = evaluate_pid_rules(
            avg_overshoot=0.99,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        # Just above 1.0°C - should be extreme
        results_extreme = evaluate_pid_rules(
            avg_overshoot=1.01,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        assert len(results_moderate) == 1
        assert len(results_extreme) == 1

        # Moderate: Kp unchanged
        assert results_moderate[0].rule == PIDRule.MODERATE_OVERSHOOT
        assert results_moderate[0].kp_factor == 1.0

        # Extreme: Kp reduced
        assert results_extreme[0].rule == PIDRule.HIGH_OVERSHOOT
        assert results_extreme[0].kp_factor == 0.90

        # Both should increase Kd
        assert results_moderate[0].kd_factor == 1.20
        assert results_extreme[0].kd_factor == 1.20

    def test_overshoot_integration_old_vs_new(self):
        """Integration test comparing old vs new overshoot handling behavior."""
        learner = AdaptiveLearner()

        # Add 6 cycles with moderate overshoot (0.5°C)
        for _ in range(6):
            learner.add_cycle_metrics(CycleMetrics(
                overshoot=0.5,  # Moderate overshoot
                undershoot=0.0,
                oscillations=0.0,
                rise_time=40.0,
                settling_time=50.0,
            ))

        # Starting PID: Kp=100, Ki=2.0, Kd=2.0 (valid within PID_LIMITS kd_max=5.0)
        result = learner.calculate_pid_adjustment(100.0, 2.0, 2.0)

        assert result is not None

        # New behavior:
        # - Kp should stay at ~100.0 (no reduction for moderate overshoot)
        # - Kd should increase significantly (20% increase * 2.0 learning rate = 40% increase)
        #   From 2.0 → ~2.8
        #
        # Old behavior would have reduced Kp to 95.0 and left Kd unchanged

        # Allow some tolerance for learning rate scaling
        assert result["kp"] >= 95.0, "Kp should not be reduced significantly for moderate overshoot"
        assert result["kd"] >= 2.3, "Kd should increase to handle overshoot"

        # The new approach focuses on Kd for moderate overshoot
        kd_increase_pct = ((result["kd"] - 2.0) / 2.0) * 100
        assert kd_increase_pct > 10.0, f"Expected significant Kd increase, got {kd_increase_pct:.1f}%"

    def test_overshoot_no_trigger_below_threshold(self):
        """Test that overshoot rule doesn't trigger below 0.2°C threshold."""
        results = evaluate_pid_rules(
            avg_overshoot=0.15,  # Below 0.2°C threshold
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
        )

        # Should not trigger any rules
        assert len(results) == 0
