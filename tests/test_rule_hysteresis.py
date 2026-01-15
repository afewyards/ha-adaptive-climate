"""Tests for PID rule hysteresis to prevent oscillation."""

import pytest
from custom_components.adaptive_thermostat.adaptive.pid_rules import (
    PIDRule,
    RuleStateTracker,
    evaluate_pid_rules,
)


class TestRuleStateTracker:
    """Test the RuleStateTracker class."""

    def test_initial_state_inactive(self):
        """All rules should start in inactive state."""
        tracker = RuleStateTracker()

        # All rules should be inactive initially
        for rule in PIDRule:
            assert not tracker.is_active(rule)

    def test_activation_threshold(self):
        """Rule should activate when metric exceeds activation threshold."""
        tracker = RuleStateTracker(hysteresis_band_pct=0.20)

        # Initially inactive, overshoot below threshold
        result = tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.15, 0.2)
        assert not result
        assert not tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Exceed activation threshold -> activate
        result = tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.25, 0.2)
        assert result
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

    def test_release_threshold(self):
        """Rule should deactivate only when metric drops below release threshold."""
        tracker = RuleStateTracker(hysteresis_band_pct=0.20)

        # Activate rule
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.25, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Drop slightly below activation but above release -> stay active
        # Release threshold = 0.2 * (1 - 0.2) = 0.16
        result = tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.18, 0.2)
        assert result  # Still active
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Drop below release threshold -> deactivate
        result = tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.15, 0.2)
        assert not result
        assert not tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

    def test_hysteresis_band_prevents_oscillation(self):
        """Hysteresis should prevent rapid on/off when metric hovers near threshold."""
        tracker = RuleStateTracker(hysteresis_band_pct=0.20)

        # Activation threshold = 0.2, Release threshold = 0.16
        # Test sequence of noisy measurements around threshold

        # Start inactive, metric below threshold
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.15, 0.2)
        assert not tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Jump above activation -> activate
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.22, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Drop into hysteresis band (0.16-0.2) -> stay active
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.19, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.17, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Rise back above activation while active -> stay active
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.21, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Back into hysteresis band -> stay active
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.18, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Only deactivate when dropping below release
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.15, 0.2)
        assert not tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

    def test_multiple_rules_independent(self):
        """Multiple rules should maintain independent state."""
        tracker = RuleStateTracker()

        # Activate overshoot rule
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.25, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Activate undershoot rule
        tracker.update_state(PIDRule.UNDERSHOOT, 0.4, 0.3)
        assert tracker.is_active(PIDRule.UNDERSHOOT)

        # Both should be active
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)
        assert tracker.is_active(PIDRule.UNDERSHOOT)

        # Deactivate overshoot, undershoot should remain active
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.15, 0.2)
        assert not tracker.is_active(PIDRule.MODERATE_OVERSHOOT)
        assert tracker.is_active(PIDRule.UNDERSHOOT)

    def test_reset_clears_all_states(self):
        """Reset should clear all rule states."""
        tracker = RuleStateTracker()

        # Activate multiple rules
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.25, 0.2)
        tracker.update_state(PIDRule.UNDERSHOOT, 0.4, 0.3)
        tracker.update_state(PIDRule.MANY_OSCILLATIONS, 4.0, 3.0)

        # Verify all active
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)
        assert tracker.is_active(PIDRule.UNDERSHOOT)
        assert tracker.is_active(PIDRule.MANY_OSCILLATIONS)

        # Reset
        tracker.reset()

        # All should be inactive
        assert not tracker.is_active(PIDRule.MODERATE_OVERSHOOT)
        assert not tracker.is_active(PIDRule.UNDERSHOOT)
        assert not tracker.is_active(PIDRule.MANY_OSCILLATIONS)

    def test_custom_hysteresis_band(self):
        """Test with custom hysteresis band percentage."""
        # 10% hysteresis band (tighter)
        tracker = RuleStateTracker(hysteresis_band_pct=0.10)

        # Activation = 0.2, Release = 0.2 * 0.9 = 0.18
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.25, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Drop to 0.19 (above release) -> stay active
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.19, 0.2)
        assert tracker.is_active(PIDRule.MODERATE_OVERSHOOT)

        # Drop to 0.17 (below release) -> deactivate
        tracker.update_state(PIDRule.MODERATE_OVERSHOOT, 0.17, 0.2)
        assert not tracker.is_active(PIDRule.MODERATE_OVERSHOOT)


class TestHysteresisIntegration:
    """Test hysteresis integration with evaluate_pid_rules."""

    def test_overshoot_rule_without_hysteresis(self):
        """Without tracker, rule fires immediately when threshold crossed."""
        # No state_tracker -> simple threshold
        results = evaluate_pid_rules(
            avg_overshoot=0.25,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=None,
        )

        # Rule should fire
        assert len(results) == 1
        assert results[0].rule == PIDRule.MODERATE_OVERSHOOT

    def test_overshoot_rule_with_hysteresis(self):
        """With tracker, rule uses hysteresis logic."""
        tracker = RuleStateTracker(hysteresis_band_pct=0.20)

        # First call: overshoot = 0.25 > 0.2 -> activate
        results = evaluate_pid_rules(
            avg_overshoot=0.25,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=tracker,
        )
        assert len(results) == 1
        assert results[0].rule == PIDRule.MODERATE_OVERSHOOT

        # Second call: drop to 0.18 (in hysteresis band) -> stay active
        results = evaluate_pid_rules(
            avg_overshoot=0.18,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=tracker,
        )
        assert len(results) == 1  # Still fires due to hysteresis
        assert results[0].rule == PIDRule.MODERATE_OVERSHOOT

        # Third call: drop to 0.15 (below release) -> deactivate
        results = evaluate_pid_rules(
            avg_overshoot=0.15,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=tracker,
        )
        assert len(results) == 0  # No longer fires

    def test_hysteresis_prevents_oscillation_scenario(self):
        """Simulate noisy metrics that would cause oscillation without hysteresis."""
        tracker = RuleStateTracker(hysteresis_band_pct=0.20)

        # Simulate 10 cycles with noisy overshoot measurements around 0.2°C threshold
        # Without hysteresis: would fire on/off repeatedly
        # With hysteresis: should maintain stable state
        overshoot_sequence = [0.15, 0.22, 0.19, 0.21, 0.18, 0.20, 0.17, 0.23, 0.19, 0.16]
        states = []

        for overshoot in overshoot_sequence:
            results = evaluate_pid_rules(
                avg_overshoot=overshoot,
                avg_undershoot=0.0,
                avg_oscillations=0.0,
                avg_rise_time=50.0,
                avg_settling_time=50.0,
                state_tracker=tracker,
            )
            states.append(len(results) > 0)

        # Expected states with hysteresis (0.2 activation, 0.16 release):
        # 0.15: inactive (below activation)
        # 0.22: active (above activation)
        # 0.19: active (in hysteresis band)
        # 0.21: active (above activation)
        # 0.18: active (in hysteresis band)
        # 0.20: active (at activation, but already active)
        # 0.17: active (in hysteresis band)
        # 0.23: active (above activation)
        # 0.19: active (in hysteresis band)
        # 0.16: inactive (at release threshold)
        expected = [False, True, True, True, True, True, True, True, True, False]
        assert states == expected

    def test_multiple_rules_with_hysteresis(self):
        """Test multiple rules maintaining independent hysteresis state."""
        tracker = RuleStateTracker()

        # Scenario: overshoot and undershoot both present but oscillating
        # Cycle 1: High overshoot, low undershoot
        results = evaluate_pid_rules(
            avg_overshoot=0.5,
            avg_undershoot=0.2,
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=tracker,
        )
        rule_names = [r.rule.rule_name for r in results]
        assert "moderate_overshoot" in rule_names
        assert "undershoot" not in rule_names  # Below 0.3 threshold

        # Cycle 2: Overshoot drops into hysteresis, undershoot rises
        results = evaluate_pid_rules(
            avg_overshoot=0.18,  # In hysteresis band (0.16-0.2)
            avg_undershoot=0.35,  # Above threshold
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=tracker,
        )
        rule_names = [r.rule.rule_name for r in results]
        assert "moderate_overshoot" in rule_names  # Still active via hysteresis
        assert "undershoot" in rule_names  # Now active

        # Cycle 3: Both drop slightly
        results = evaluate_pid_rules(
            avg_overshoot=0.17,  # Still in hysteresis
            avg_undershoot=0.28,  # In hysteresis (0.24-0.3)
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=tracker,
        )
        rule_names = [r.rule.rule_name for r in results]
        assert "moderate_overshoot" in rule_names  # Both remain active
        assert "undershoot" in rule_names

    def test_backward_compatibility(self):
        """Test that omitting state_tracker maintains original behavior."""
        # With tracker
        tracker = RuleStateTracker()
        results_with = evaluate_pid_rules(
            avg_overshoot=0.25,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=tracker,
        )

        # Without tracker (backward compatible)
        results_without = evaluate_pid_rules(
            avg_overshoot=0.25,
            avg_undershoot=0.0,
            avg_oscillations=0.0,
            avg_rise_time=50.0,
            avg_settling_time=50.0,
            state_tracker=None,
        )

        # Should get same results on first evaluation
        assert len(results_with) == len(results_without)
        assert results_with[0].rule == results_without[0].rule
