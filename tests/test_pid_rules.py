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
from custom_components.adaptive_thermostat.const import (
    PID_LIMITS,
    get_rule_thresholds,
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_FORCED_AIR,
)


class TestUndershootRuleDecayAware:
    """Tests for decay-aware UNDERSHOOT rule that scales Ki increase inversely to decay_ratio."""

    def test_undershoot_rule_no_decay_full_increase(self):
        """Test UNDERSHOOT rule with no decay (decay_ratio=0) gives full Ki increase 20%."""
        # With 0.4°C undershoot and decay_ratio=0.0, should give full increase
        # increase = min(1.0, 0.4 * 2.0) = 0.8
        # decay_ratio = 0 / 50 = 0.0
        # scaled_increase = 0.8 * (1 - 0.0) = 0.8
        # ki_factor = 1.0 + 0.8 = 1.8
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.4,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
            decay_contribution=0.0,
            integral_at_tolerance_entry=50.0,
        )

        assert len(results) == 1
        assert results[0].rule == PIDRule.UNDERSHOOT

        # With decay_ratio=0.0, get full increase of 80%
        expected_factor = 1.8
        assert abs(results[0].ki_factor - expected_factor) < 0.001
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.0

    def test_undershoot_rule_high_decay_no_increase(self):
        """Test UNDERSHOOT rule with full decay (decay_ratio=1.0) gives no Ki increase."""
        # With 0.4°C undershoot but decay_ratio=1.0, no increase
        # increase = min(1.0, 0.4 * 2.0) = 0.8
        # decay_ratio = 50 / 50 = 1.0
        # scaled_increase = 0.8 * (1 - 1.0) = 0.0
        # ki_factor = 1.0 + 0.0 = 1.0 (no change)
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.4,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
            decay_contribution=50.0,
            integral_at_tolerance_entry=50.0,
        )

        assert len(results) == 1
        assert results[0].rule == PIDRule.UNDERSHOOT

        # With decay_ratio=1.0, get no increase
        assert results[0].ki_factor == 1.0
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.0

    def test_undershoot_rule_partial_decay_scaled_increase(self):
        """Test UNDERSHOOT rule with partial decay (decay_ratio=0.5) gives scaled Ki increase."""
        # With 0.4°C undershoot and decay_ratio=0.5, should give half increase
        # increase = min(1.0, 0.4 * 2.0) = 0.8
        # decay_ratio = 25 / 50 = 0.5
        # scaled_increase = 0.8 * (1 - 0.5) = 0.4
        # ki_factor = 1.0 + 0.4 = 1.4
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.4,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
            decay_contribution=25.0,
            integral_at_tolerance_entry=50.0,
        )

        assert len(results) == 1
        assert results[0].rule == PIDRule.UNDERSHOOT

        # With decay_ratio=0.5, get half increase (40% instead of 80%)
        expected_factor = 1.4
        assert abs(results[0].ki_factor - expected_factor) < 0.001
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.0


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


class TestSlowResponseDiagnostics:
    """Tests for slow response rule with outdoor temperature correlation diagnostics."""

    def test_pearson_correlation_calculation(self):
        """Test Pearson correlation helper function."""
        from custom_components.adaptive_thermostat.adaptive.pid_rules import calculate_pearson_correlation

        # Perfect positive correlation
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        r = calculate_pearson_correlation(x, y)
        assert r is not None
        assert abs(r - 1.0) < 0.01, f"Expected r≈1.0 for perfect positive correlation, got {r}"

        # Perfect negative correlation
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [10.0, 8.0, 6.0, 4.0, 2.0]
        r = calculate_pearson_correlation(x, y)
        assert r is not None
        assert abs(r - (-1.0)) < 0.01, f"Expected r≈-1.0 for perfect negative correlation, got {r}"

        # No correlation
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [3.0, 3.0, 3.0, 3.0, 3.0]  # Constant
        r = calculate_pearson_correlation(x, y)
        assert r is None, "Expected None when std dev is zero"

        # Insufficient data
        r = calculate_pearson_correlation([1.0], [2.0])
        assert r is None, "Expected None with insufficient data"

    def test_slow_response_low_ki_diagnosis(self):
        """Test slow response rule diagnoses LOW_KI when cold weather correlation exists."""
        # Simulate scenario: slower rise times when colder outside
        # Rise times: [70, 75, 80, 85, 90] minutes
        # Outdoor temps: [5, 0, -5, -10, -15] °C
        # Strong negative correlation (r ≈ -0.99)

        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=80.0,  # >60 min triggers slow response
            avg_settling_time=30.0,
            recent_rise_times=[70.0, 75.0, 80.0, 85.0, 90.0],
            recent_outdoor_temps=[5.0, 0.0, -5.0, -10.0, -15.0],
        )

        # Should trigger slow response rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_RESPONSE

        # Should diagnose LOW_KI and increase Ki instead of Kp
        assert results[0].kp_factor == 1.0, "Kp should not change for LOW_KI diagnosis"
        assert results[0].ki_factor == 1.30, "Ki should increase by 30% for LOW_KI diagnosis"
        assert results[0].kd_factor == 1.0, "Kd should not change"

        # Reason should mention cold correlation and Ki increase
        reason_lower = results[0].reason.lower()
        assert "correlation" in reason_lower
        assert "ki" in reason_lower

    def test_slow_response_low_kp_diagnosis(self):
        """Test slow response rule diagnoses LOW_KP when no cold weather correlation."""
        # Simulate scenario: slow rise times but no outdoor correlation
        # Rise times: [70, 75, 80, 85, 90] minutes
        # Outdoor temps: [10, 12, 11, 13, 12] °C (warm, minimal variation)
        # No significant correlation

        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=80.0,  # >60 min triggers slow response
            avg_settling_time=30.0,
            recent_rise_times=[70.0, 75.0, 80.0, 85.0, 90.0],
            recent_outdoor_temps=[10.0, 12.0, 11.0, 13.0, 12.0],
        )

        # Should trigger slow response rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_RESPONSE

        # Should diagnose LOW_KP and increase Kp (default behavior)
        assert results[0].kp_factor == 1.10, "Kp should increase by 10% for LOW_KP diagnosis"
        assert results[0].ki_factor == 1.0, "Ki should not change for LOW_KP diagnosis"
        assert results[0].kd_factor == 1.0, "Kd should not change"

        # Reason should mention no cold correlation and Kp increase
        reason_lower = results[0].reason.lower()
        assert "correlation" in reason_lower
        assert "kp" in reason_lower

    def test_slow_response_fallback_without_outdoor_data(self):
        """Test slow response rule falls back to default behavior without outdoor data."""
        # No outdoor data provided
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=80.0,  # >60 min triggers slow response
            avg_settling_time=30.0,
            # No outdoor data
        )

        # Should trigger slow response rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_RESPONSE

        # Should fall back to default behavior (increase Kp)
        assert results[0].kp_factor == 1.10, "Should fall back to Kp increase"
        assert results[0].ki_factor == 1.0
        assert results[0].kd_factor == 1.0

    def test_slow_response_insufficient_outdoor_temp_range(self):
        """Test slow response rule requires sufficient outdoor temp variation."""
        # Outdoor temps have <3°C variation (insufficient for correlation)
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=80.0,
            avg_settling_time=30.0,
            recent_rise_times=[70.0, 75.0, 80.0, 85.0, 90.0],
            recent_outdoor_temps=[10.0, 11.0, 10.5, 11.5, 10.2],  # Range = 1.5°C < 3.0°C
        )

        # Should trigger slow response rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_RESPONSE

        # Should fall back to default behavior due to insufficient outdoor variation
        assert results[0].kp_factor == 1.10, "Should fall back to Kp increase when outdoor range too small"
        assert results[0].ki_factor == 1.0
        assert results[0].kd_factor == 1.0

    def test_slow_response_insufficient_data_points(self):
        """Test slow response rule requires at least 3 data points for correlation."""
        # Only 2 data points (insufficient)
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=80.0,
            avg_settling_time=30.0,
            recent_rise_times=[70.0, 90.0],
            recent_outdoor_temps=[5.0, -15.0],
        )

        # Should trigger slow response rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_RESPONSE

        # Should fall back to default behavior due to insufficient data
        assert results[0].kp_factor == 1.10, "Should fall back to Kp increase with <3 data points"
        assert results[0].ki_factor == 1.0
        assert results[0].kd_factor == 1.0

    def test_slow_response_weak_correlation_uses_kp(self):
        """Test slow response with weak correlation uses Kp (edge case)."""
        # Positive correlation (faster when colder - physically impossible)
        # Should trigger Kp increase since correlation is not strong negative

        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=80.0,
            avg_settling_time=30.0,
            recent_rise_times=[90.0, 85.0, 80.0, 75.0, 70.0],  # Decreasing
            recent_outdoor_temps=[5.0, 0.0, -5.0, -10.0, -15.0],  # Also decreasing -> positive correlation
        )

        # Should trigger slow response rule
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_RESPONSE

        # Positive correlation (not < -0.6) should trigger Kp increase
        assert results[0].kp_factor == 1.10, "Positive correlation should use Kp default"
        assert results[0].ki_factor == 1.0
        assert results[0].kd_factor == 1.0


class TestGetRuleThresholds:
    """Tests for get_rule_thresholds() function."""

    def test_get_rule_thresholds_floor_hydronic(self):
        """Test get_rule_thresholds() returns correct thresholds for floor_hydronic."""
        # Floor hydronic has:
        # - overshoot_max: 0.3
        # - rise_time_max: 90
        # Expected thresholds:
        # - slow_response = 90 * 1.33 = 120.0
        # - moderate_overshoot = max(0.3 * 1.0, 0.15) = 0.3
        # - high_overshoot = max(0.3 * 5.0, 1.0) = 1.5

        thresholds = get_rule_thresholds(HEATING_TYPE_FLOOR_HYDRONIC)

        assert thresholds["slow_response"] == 120.0
        assert thresholds["moderate_overshoot"] == 0.3
        assert thresholds["high_overshoot"] == 1.5

    def test_get_rule_thresholds_forced_air(self):
        """Test get_rule_thresholds() returns correct thresholds for forced_air."""
        # Forced air has:
        # - overshoot_max: 0.15
        # - rise_time_max: 30
        # Expected thresholds:
        # - slow_response = 30 * 1.33 = 40.0
        # - moderate_overshoot = max(0.15 * 1.0, 0.15) = 0.15 (floored)
        # - high_overshoot = max(0.15 * 5.0, 1.0) = 1.0 (floored)

        thresholds = get_rule_thresholds(HEATING_TYPE_FORCED_AIR)

        assert thresholds["slow_response"] == 40.0
        assert thresholds["moderate_overshoot"] == 0.15
        assert thresholds["high_overshoot"] == 1.0

    def test_get_rule_thresholds_default(self):
        """Test get_rule_thresholds() returns correct defaults for None."""
        # Default (convector-like) has:
        # - overshoot_max: 0.2
        # - rise_time_max: 45
        # Expected thresholds:
        # - slow_response = 45 * 1.33 = 60.0
        # - moderate_overshoot = max(0.2 * 1.0, 0.15) = 0.2
        # - high_overshoot = max(0.2 * 5.0, 1.0) = 1.0 (floored)

        thresholds = get_rule_thresholds(None)

        assert thresholds["slow_response"] == 60.0
        assert thresholds["moderate_overshoot"] == 0.2
        assert thresholds["high_overshoot"] == 1.0


class TestSlowSettlingDecayAware:
    """Tests for slow settling rule with decay-aware Ki adjustment (Case 3 and Case 5)."""

    def test_slow_settling_low_decay_increases_ki(self):
        """Test slow settling with low decay (decay_ratio < 0.2) increases Ki by 10%."""
        # Case 3: Sluggish system with low decay -> increase Ki
        # settling_time > max_settling (100 > 90) AND decay_ratio < 0.2 (0.1)
        # Should increase Ki by 10%
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=100.0,  # Above threshold (90 min default)
            decay_contribution=5.0,
            integral_at_tolerance_entry=50.0,  # decay_ratio = 5.0 / 50.0 = 0.1 < 0.2
        )

        # Should trigger slow settling rule with Ki increase
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_SETTLING

        # With low decay_ratio (0.1), Ki should increase by 10%
        assert results[0].ki_factor == 1.1
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.15  # Normal Kd increase for slow settling

        # Reason should mention Ki increase
        assert "+10% Ki" in results[0].reason or "10% Ki" in results[0].reason

    def test_slow_settling_high_decay_no_ki_change(self):
        """Test slow settling with high decay (decay_ratio > 0.5) does not increase Ki."""
        # Case 5: High decay with good result -> no Ki change
        # settling_time > max_settling (100 > 90) AND decay_ratio > 0.5 (0.6)
        # Should NOT increase Ki (Case 5 suppresses Ki adjustment)
        # Note: overshoot must be >= 0.1 to trigger Case 5 instead of Case 4
        results = evaluate_pid_rules(
            avg_overshoot=0.15,  # Overshoot >= 0.1 to avoid Case 4
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=100.0,  # Above threshold (90 min default)
            decay_contribution=30.0,
            integral_at_tolerance_entry=50.0,  # decay_ratio = 30.0 / 50.0 = 0.6 > 0.5
        )

        # Should trigger slow settling rule but without Ki increase
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_SETTLING

        # With high decay_ratio (0.6), Ki should NOT increase
        assert results[0].ki_factor == 1.0
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.15  # Normal Kd increase for slow settling

        # Reason should indicate no Ki change due to decay
        reason_lower = results[0].reason.lower()
        assert "decay" in reason_lower or "kd only" in reason_lower

    def test_slow_settling_moderate_decay_no_ki_change(self):
        """Test slow settling with moderate decay (0.2 <= decay_ratio <= 0.5) does not increase Ki."""
        # Middle case: decay_ratio in [0.2, 0.5] -> no Ki adjustment (default behavior)
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=100.0,  # Above threshold (90 min default)
            decay_contribution=15.0,
            integral_at_tolerance_entry=50.0,  # decay_ratio = 15.0 / 50.0 = 0.3 (in [0.2, 0.5])
        )

        # Should trigger slow settling rule but without Ki increase
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_SETTLING

        # With moderate decay_ratio, Ki should NOT increase (default behavior)
        assert results[0].ki_factor == 1.0
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.15  # Normal Kd increase for slow settling

    def test_slow_settling_no_decay_data_defaults_to_kd_only(self):
        """Test slow settling without decay data uses default behavior (Kd only)."""
        # When decay_contribution/integral_at_tolerance_entry not provided,
        # should fall back to default behavior (Kd only)
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=100.0,  # Above threshold (90 min default)
            # No decay_contribution or integral_at_tolerance_entry
        )

        # Should trigger slow settling rule with default behavior
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_SETTLING

        # Without decay data, should use default behavior (Kd only)
        assert results[0].ki_factor == 1.0
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.15

    def test_high_decay_slow_settling_reduces_ki(self):
        """Test slow settling with high decay (decay_ratio>0.5) and good overshoot reduces Ki by 3%."""
        # Case 4: High decay (integral decayed significantly) + good overshoot + slow settling
        # Rationale: If integral decayed significantly but settling is still slow AND overshoot
        # is good, then the integral might be slightly too aggressive during settling.
        # A gentle 3% Ki reduction helps without overcorrecting.
        #
        # Requires:
        # - avg_overshoot < 0.1 (good overshoot control)
        # - avg_settling_time > max_settling (slow settling)
        # - decay_ratio > 0.5 (high decay during settling)
        results = evaluate_pid_rules(
            avg_overshoot=0.05,  # Good overshoot control (<0.1)
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=100.0,  # Above threshold (90 min default)
            decay_contribution=30.0,
            integral_at_tolerance_entry=50.0,  # decay_ratio = 30.0 / 50.0 = 0.6 > 0.5
        )

        # Should trigger slow settling rule with gentle Ki reduction
        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_SETTLING

        # With high decay_ratio (0.6) and good overshoot (<0.1), Ki should gently reduce by 3%
        assert results[0].ki_factor == 0.97
        assert results[0].kp_factor == 1.0
        assert results[0].kd_factor == 1.15  # Normal Kd increase for slow settling

        # Reason should mention gentle Ki reduction
        reason_lower = results[0].reason.lower()
        assert "ki" in reason_lower or "3%" in reason_lower or "gentle" in reason_lower


class TestHeatingTypeSpecificThresholds:
    """Tests for heating-type-specific threshold behavior in evaluate_pid_rules()."""

    def test_floor_hydronic_70min_no_slow_response(self):
        """Test 70min rise does NOT trigger SLOW_RESPONSE for floor_hydronic."""
        # Floor hydronic threshold: 90 * 1.33 = 120 min
        # 70 min < 120 min -> no slow response rule
        thresholds = get_rule_thresholds(HEATING_TYPE_FLOOR_HYDRONIC)

        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=70.0,  # Below threshold for floor_hydronic
            avg_settling_time=30.0,
            rule_thresholds=thresholds,
        )

        # Should not trigger any rules
        assert len(results) == 0, "70 min rise time should not trigger SLOW_RESPONSE for floor_hydronic"

    def test_forced_air_45min_triggers_slow_response(self):
        """Test 45min rise DOES trigger SLOW_RESPONSE for forced_air."""
        # Forced air threshold: 30 * 1.33 = 40 min
        # 45 min > 40 min -> slow response rule triggered
        # Note: slow_settling threshold for forced_air is now 15 min (from max_settling_time)
        # so we use avg_settling_time=10.0 to avoid triggering SLOW_SETTLING rule
        thresholds = get_rule_thresholds(HEATING_TYPE_FORCED_AIR)

        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=45.0,  # Above threshold for forced_air
            avg_settling_time=10.0,  # Below slow_settling threshold (15 min for forced_air)
            rule_thresholds=thresholds,
        )

        # Should trigger slow response rule
        assert len(results) == 1, "45 min rise time should trigger SLOW_RESPONSE for forced_air"
        assert results[0].rule == PIDRule.SLOW_RESPONSE
        assert results[0].kp_factor == 1.10, "Should increase Kp by 10%"

    def test_floor_hydronic_high_overshoot_threshold(self):
        """Test 1.2C overshoot does NOT trigger HIGH_OVERSHOOT for floor_hydronic."""
        # Floor hydronic threshold: max(0.3 * 5.0, 1.0) = 1.5°C
        # 1.2°C < 1.5°C -> only moderate overshoot, not high
        thresholds = get_rule_thresholds(HEATING_TYPE_FLOOR_HYDRONIC)

        results = evaluate_pid_rules(
            avg_overshoot=1.2,  # Below HIGH_OVERSHOOT threshold for floor_hydronic
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
            rule_thresholds=thresholds,
        )

        # Should trigger moderate overshoot only (not high)
        assert len(results) == 1, "1.2°C overshoot should trigger MODERATE_OVERSHOOT for floor_hydronic"
        assert results[0].rule == PIDRule.MODERATE_OVERSHOOT, "Should be MODERATE_OVERSHOOT, not HIGH_OVERSHOOT"
        assert results[0].kp_factor == 1.0, "Kp should not change for moderate overshoot"
        assert results[0].kd_factor == 1.20, "Kd should increase by 20%"

    def test_backward_compatible_defaults(self):
        """Test evaluate_pid_rules(rule_thresholds=None) uses convector defaults."""
        # When rule_thresholds=None, function should use convector-like defaults:
        # - slow_response = 60.0
        # - moderate_overshoot = 0.2
        # - high_overshoot = 1.0

        # Test slow response with default threshold (60 min)
        results = evaluate_pid_rules(
            avg_overshoot=0.0,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=65.0,  # Above 60 min default
            avg_settling_time=30.0,
            rule_thresholds=None,  # Use defaults
        )

        assert len(results) == 1
        assert results[0].rule == PIDRule.SLOW_RESPONSE

        # Test moderate overshoot with default threshold (0.2°C)
        results = evaluate_pid_rules(
            avg_overshoot=0.25,  # Above 0.2°C default
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
            rule_thresholds=None,  # Use defaults
        )

        assert len(results) == 1
        assert results[0].rule == PIDRule.MODERATE_OVERSHOOT

        # Test high overshoot with default threshold (1.0°C)
        results = evaluate_pid_rules(
            avg_overshoot=1.1,  # Above 1.0°C default
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=30.0,
            avg_settling_time=30.0,
            rule_thresholds=None,  # Use defaults
        )

        assert len(results) == 1
        assert results[0].rule == PIDRule.HIGH_OVERSHOOT
