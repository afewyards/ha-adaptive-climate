"""Integration tests for weighted cycle learning infrastructure.

This module tests the weighted learning infrastructure that has been added:
- CycleWeightCalculator for determining cycle weights
- ConfidenceContributionTracker for managing contributions
- Cycle outcome classification
- Recovery vs maintenance cycle distinction

Note: Full integration into AdaptiveLearner.update_convergence_confidence()
is pending. These tests verify the underlying components are working correctly.
"""

from __future__ import annotations

import pytest
from typing import Optional

from custom_components.adaptive_climate.adaptive.cycle_weight import CycleWeightCalculator, CycleOutcome
from custom_components.adaptive_climate.adaptive.confidence_contribution import ConfidenceContributionTracker
from custom_components.adaptive_climate.const import HeatingType
from custom_components.adaptive_climate.helpers.hvac_mode import get_hvac_heat_mode


class TestCycleWeightCalculation:
    """Test cycle weight calculation logic."""

    def test_weight_calculator_maintenance_vs_recovery(self):
        """Verify weight calculator distinguishes maintenance from recovery cycles."""
        calculator = CycleWeightCalculator(HeatingType.FLOOR_HYDRONIC)

        # Floor hydronic threshold is 0.5°C
        # Maintenance cycle: below threshold
        weight_maintenance = calculator.calculate_weight(
            starting_delta=0.3,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
        )

        # Recovery cycle: above threshold
        weight_recovery = calculator.calculate_weight(
            starting_delta=0.7,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
        )

        # Recovery cycles should have higher weight
        assert weight_recovery > weight_maintenance, (
            f"Recovery weight ({weight_recovery:.2f}) should exceed "
            f"maintenance weight ({weight_maintenance:.2f})"
        )

    def test_weight_calculator_outcome_affects_weight(self):
        """Verify cycle outcome (overshoot/undershoot) reduces weight."""
        calculator = CycleWeightCalculator(HeatingType.CONVECTOR)

        # Clean cycle
        weight_clean = calculator.calculate_weight(
            starting_delta=0.6,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
        )

        # Overshoot cycle
        weight_overshoot = calculator.calculate_weight(
            starting_delta=0.6,
            is_stable=False,
            outcome=CycleOutcome.OVERSHOOT,
        )

        # Undershoot cycle
        weight_undershoot = calculator.calculate_weight(
            starting_delta=0.6,
            is_stable=False,
            outcome=CycleOutcome.UNDERSHOOT,
        )

        # Clean should have highest weight
        assert weight_clean > weight_overshoot, "Clean > Overshoot"
        assert weight_clean > weight_undershoot, "Clean > Undershoot"

    def test_heating_type_threshold_differences(self):
        """Verify different heating types have different recovery thresholds."""
        floor_calc = CycleWeightCalculator(HeatingType.FLOOR_HYDRONIC)
        radiator_calc = CycleWeightCalculator(HeatingType.RADIATOR)
        convector_calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        forced_calc = CycleWeightCalculator(HeatingType.FORCED_AIR)

        # At 0.4°C delta:
        # - Floor (0.5 threshold) should be maintenance
        # - Others should be recovery

        is_floor_recovery = floor_calc.is_recovery_cycle(0.4, is_stable=False)
        is_radiator_recovery = radiator_calc.is_recovery_cycle(0.4, is_stable=False)
        is_convector_recovery = convector_calc.is_recovery_cycle(0.4, is_stable=False)
        is_forced_recovery = forced_calc.is_recovery_cycle(0.4, is_stable=False)

        assert not is_floor_recovery, "Floor: 0.4°C should be maintenance"
        assert is_radiator_recovery, "Radiator: 0.4°C should be recovery"
        assert is_convector_recovery, "Convector: 0.4°C should be recovery"
        assert is_forced_recovery, "Forced air: 0.4°C should be recovery"

    def test_stable_system_recovery_threshold_difference(self):
        """Verify stable systems have different recovery threshold."""
        calculator = CycleWeightCalculator(HeatingType.RADIATOR)

        # Radiator collecting threshold: 0.3°C, stable threshold: 0.5°C
        # At 0.4°C delta: recovery when collecting, maintenance when stable
        is_recovery_collecting = calculator.is_recovery_cycle(0.4, is_stable=False)
        is_recovery_stable = calculator.is_recovery_cycle(0.4, is_stable=True)

        assert is_recovery_collecting, "0.4°C is recovery when collecting (threshold 0.3)"
        assert not is_recovery_stable, "0.4°C is maintenance when stable (threshold 0.5)"


class TestConfidenceContributionTracker:
    """Test confidence contribution tracking."""

    def test_maintenance_contribution_capped(self):
        """Verify maintenance contributions are capped."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        mode = get_hvac_heat_mode()

        # Floor hydronic cap is 25%
        cap = 0.25

        # Apply gains that would exceed cap
        for _ in range(50):
            tracker.apply_maintenance_gain(0.02, mode)

        # Should be capped at 25% + diminishing returns
        contribution = tracker.get_maintenance_contribution(mode)
        assert contribution < cap * 1.5, (
            f"Maintenance should be capped around {cap:.0%}, got {contribution:.1%}"
        )

    def test_recovery_cycle_counting(self):
        """Verify recovery cycles are counted correctly."""
        tracker = ConfidenceContributionTracker(HeatingType.RADIATOR)
        mode = get_hvac_heat_mode()

        # Add recovery cycles
        for _ in range(7):
            tracker.add_recovery_cycle(mode)

        count = tracker.get_recovery_cycle_count(mode)
        assert count == 7, f"Expected 7 recovery cycles, got {count}"

    def test_tier_unlocking(self):
        """Verify tier unlocking based on recovery cycles."""
        tracker = ConfidenceContributionTracker(HeatingType.FORCED_AIR)
        mode = get_hvac_heat_mode()

        # Forced air tier 1 needs 6 cycles
        assert not tracker.can_reach_tier(1, mode), "Should not reach tier 1 initially"

        # Add 6 recovery cycles
        for _ in range(6):
            tracker.add_recovery_cycle(mode)

        assert tracker.can_reach_tier(1, mode), "Should reach tier 1 after 6 cycles"

    def test_heating_rate_contribution_capped(self):
        """Verify heating rate contributions are hard capped."""
        tracker = ConfidenceContributionTracker(HeatingType.CONVECTOR)

        # Convector heating rate cap is 10%
        cap = 0.10

        # Apply gains that exceed cap
        for _ in range(20):
            tracker.apply_heating_rate_gain(0.02)

        contribution = tracker.get_heating_rate_contribution()
        assert contribution <= cap, (
            f"Heating rate should be hard capped at {cap:.0%}, got {contribution:.1%}"
        )


class TestCycleOutcomeClassification:
    """Test cycle outcome classification."""

    def test_outcome_enum_values(self):
        """Verify CycleOutcome enum has expected values."""
        assert CycleOutcome.CLEAN.value == "clean"
        assert CycleOutcome.OVERSHOOT.value == "overshoot"
        assert CycleOutcome.UNDERSHOOT.value == "undershoot"

    def test_weight_by_outcome(self):
        """Verify weights decrease with worsening outcome."""
        calculator = CycleWeightCalculator(HeatingType.RADIATOR)

        weights = {}
        for outcome in [CycleOutcome.CLEAN, CycleOutcome.OVERSHOOT, CycleOutcome.UNDERSHOOT]:
            weights[outcome] = calculator.calculate_weight(
                starting_delta=0.6,
                is_stable=False,
                outcome=outcome,
            )

        # Clean should have highest weight
        assert weights[CycleOutcome.CLEAN] >= weights[CycleOutcome.OVERSHOOT]
        assert weights[CycleOutcome.CLEAN] >= weights[CycleOutcome.UNDERSHOOT]


class TestWeightedLearningConstants:
    """Test weighted learning constants are properly defined."""

    def test_maintenance_cap_per_heating_type(self):
        """Verify maintenance cap is defined for all heating types."""
        from custom_components.adaptive_climate.const import MAINTENANCE_CONFIDENCE_CAP

        for heating_type in HeatingType:
            assert heating_type in MAINTENANCE_CONFIDENCE_CAP, (
                f"MAINTENANCE_CONFIDENCE_CAP missing for {heating_type}"
            )
            cap = MAINTENANCE_CONFIDENCE_CAP[heating_type]
            assert 0.0 < cap < 0.5, f"Cap for {heating_type} should be reasonable: {cap}"

    def test_recovery_thresholds_per_heating_type(self):
        """Verify recovery thresholds are defined for all heating types."""
        from custom_components.adaptive_climate.const import (
            RECOVERY_THRESHOLD_COLLECTING,
            RECOVERY_THRESHOLD_STABLE,
        )

        for heating_type in HeatingType:
            assert heating_type in RECOVERY_THRESHOLD_COLLECTING
            assert heating_type in RECOVERY_THRESHOLD_STABLE

            collecting = RECOVERY_THRESHOLD_COLLECTING[heating_type]
            stable = RECOVERY_THRESHOLD_STABLE[heating_type]

            # Both thresholds should be defined and positive
            assert collecting > 0, f"{heating_type}: collecting threshold should be > 0"
            assert stable > 0, f"{heating_type}: stable threshold should be > 0"

    def test_recovery_cycles_for_tiers(self):
        """Verify tier requirements are defined for all heating types."""
        from custom_components.adaptive_climate.const import (
            RECOVERY_CYCLES_FOR_TIER1,
            RECOVERY_CYCLES_FOR_TIER2,
        )

        for heating_type in HeatingType:
            assert heating_type in RECOVERY_CYCLES_FOR_TIER1
            assert heating_type in RECOVERY_CYCLES_FOR_TIER2

            tier1 = RECOVERY_CYCLES_FOR_TIER1[heating_type]
            tier2 = RECOVERY_CYCLES_FOR_TIER2[heating_type]

            # Tier 2 should require more cycles than tier 1
            assert tier2 > tier1, (
                f"{heating_type}: tier2 ({tier2}) should require more cycles than "
                f"tier1 ({tier1})"
            )


class TestAdaptiveLearnerIntegration:
    """Test AdaptiveLearner integration with weighted learning components."""

    def test_maintenance_cap_enforced_in_update_convergence_confidence(self):
        """Verify maintenance cycles hit cap when routed through update_convergence_confidence.

        This test validates that maintenance cycle confidence gains are properly
        routed through ConfidenceContributionTracker.apply_maintenance_gain().
        """
        from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics

        learner = AdaptiveLearner(heating_type="floor_hydronic")
        mode = get_hvac_heat_mode()

        # Floor hydronic maintenance cap is 25%
        maintenance_cap = 0.25

        # Simulate 50 maintenance cycles (starting_delta < 0.5C threshold)
        # Each cycle should be "good" (low overshoot, etc.)
        for i in range(50):
            metrics = CycleMetrics(
                overshoot=0.05,
                undershoot=0.0,
                settling_time=10.0,
                oscillations=0,
                rise_time=15.0,
                starting_delta=0.2,  # < 0.5 threshold = maintenance
            )
            learner.update_convergence_confidence(metrics, mode)

        # Confidence should be capped around maintenance_cap + diminishing returns
        # Not reach 100% despite 50 "good" cycles
        confidence = learner.get_convergence_confidence(mode)
        # Allow 1.6x maintenance cap for diminishing returns + floating point tolerance
        max_allowed = maintenance_cap * 1.6

        assert confidence < max_allowed, (
            f"Confidence after 50 maintenance cycles should be capped below {max_allowed:.0%}, "
            f"got {confidence:.1%}. Maintenance cap not being enforced."
        )

        # Also verify confidence is reasonably close to the cap (not near 100%)
        assert confidence < 0.50, (
            f"Confidence should be well below 50% with maintenance-only cycles, "
            f"got {confidence:.1%}. Cap may not be working correctly."
        )

    def test_recovery_cycles_not_capped(self):
        """Verify recovery cycles can exceed maintenance cap."""
        from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics

        learner = AdaptiveLearner(heating_type="floor_hydronic")
        mode = get_hvac_heat_mode()

        # Floor hydronic maintenance cap is 25%
        maintenance_cap = 0.25

        # Simulate 30 recovery cycles (starting_delta >= 0.5C threshold)
        for i in range(30):
            metrics = CycleMetrics(
                overshoot=0.05,
                undershoot=0.0,
                settling_time=10.0,
                oscillations=0,
                rise_time=15.0,
                starting_delta=1.0,  # >= 0.5 threshold = recovery
            )
            learner.update_convergence_confidence(metrics, mode)

        # Confidence should exceed maintenance cap since these are recovery cycles
        confidence = learner.get_convergence_confidence(mode)

        assert confidence > maintenance_cap, (
            f"Confidence after 30 recovery cycles should exceed maintenance cap of {maintenance_cap:.0%}, "
            f"got {confidence:.1%}. Recovery cycles being incorrectly capped."
        )


class TestLearningStatusTierGates:
    """Test tier gate blocking in learning status computation."""

    def test_stable_requires_recovery_cycles(self):
        """Verify 'stable' status requires enough recovery cycles, not just confidence."""
        from custom_components.adaptive_climate.managers.state_attributes import (
            _compute_learning_status,
        )

        # Floor hydronic needs 12 recovery cycles for tier 1 (stable)
        # Tier 1 threshold is 40% * 0.8 = 32%

        # Create tracker with zero recovery cycles
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        mode = get_hvac_heat_mode()

        # Scenario: High confidence but zero recovery cycles
        # Should return "collecting" not "stable"
        status = _compute_learning_status(
            cycle_count=20,  # Enough total cycles
            convergence_confidence=0.50,  # Above tier 1 threshold
            heating_type="floor_hydronic",
            is_paused=False,
            contribution_tracker=tracker,
            mode=mode,
        )

        assert status == "collecting", (
            f"Status should be 'collecting' without enough recovery cycles, got '{status}'. "
            f"Tier gate not blocking progression to 'stable'."
        )

    def test_stable_unlocks_with_enough_recovery_cycles(self):
        """Verify 'stable' status unlocks with enough recovery cycles."""
        from custom_components.adaptive_climate.managers.state_attributes import (
            _compute_learning_status,
        )

        # Floor hydronic needs 12 recovery cycles for tier 1 (stable)
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        mode = get_hvac_heat_mode()

        # Add 12 recovery cycles
        for _ in range(12):
            tracker.add_recovery_cycle(mode)

        # Should now return "stable"
        status = _compute_learning_status(
            cycle_count=20,
            convergence_confidence=0.50,  # Above tier 1, below tier 2
            heating_type="floor_hydronic",
            is_paused=False,
            contribution_tracker=tracker,
            mode=mode,
        )

        assert status == "stable", (
            f"Status should be 'stable' with 12 recovery cycles, got '{status}'."
        )

    def test_tuned_requires_more_recovery_cycles(self):
        """Verify 'tuned' status requires tier 2 recovery cycles."""
        from custom_components.adaptive_climate.managers.state_attributes import (
            _compute_learning_status,
        )

        # Floor hydronic needs 20 recovery cycles for tier 2 (tuned)
        # Tier 2 threshold is 70% * 0.8 = 56%

        # Create tracker with enough for tier 1 but not tier 2
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        mode = get_hvac_heat_mode()

        # Add 15 recovery cycles (enough for tier 1=12, not enough for tier 2=20)
        for _ in range(15):
            tracker.add_recovery_cycle(mode)

        # Scenario: High confidence, enough for tier 1 but not tier 2
        # Should return "stable" not "tuned"
        status = _compute_learning_status(
            cycle_count=20,
            convergence_confidence=0.70,  # Above tier 2 threshold
            heating_type="floor_hydronic",
            is_paused=False,
            contribution_tracker=tracker,
            mode=mode,
        )

        assert status == "stable", (
            f"Status should be 'stable' without enough recovery cycles for tier 2, got '{status}'. "
            f"Tier gate not blocking progression to 'tuned'."
        )

    def test_tuned_unlocks_with_enough_recovery_cycles(self):
        """Verify 'tuned' status unlocks with enough recovery cycles."""
        from custom_components.adaptive_climate.managers.state_attributes import (
            _compute_learning_status,
        )

        # Floor hydronic needs 20 recovery cycles for tier 2 (tuned)
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        mode = get_hvac_heat_mode()

        # Add 20 recovery cycles
        for _ in range(20):
            tracker.add_recovery_cycle(mode)

        # Should now return "tuned"
        status = _compute_learning_status(
            cycle_count=25,
            convergence_confidence=0.70,  # Above tier 2 threshold
            heating_type="floor_hydronic",
            is_paused=False,
            contribution_tracker=tracker,
            mode=mode,
        )

        assert status == "tuned", (
            f"Status should be 'tuned' with 20 recovery cycles, got '{status}'."
        )


class TestEndToEndWeightedLearning:
    """End-to-end tests for the complete weighted learning flow."""

    def test_zone_cannot_reach_tuned_on_maintenance_alone(self):
        """Verify a zone cannot reach 'tuned' status with only maintenance cycles.

        This test validates the complete integration of:
        1. Maintenance cap enforcement in update_convergence_confidence
        2. Tier gate blocking in _compute_learning_status

        Even with 100 maintenance cycles, the zone should stay at "collecting"
        or "stable" (if tier 1 gates somehow pass), never "tuned".
        """
        from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
        from custom_components.adaptive_climate.managers.state_attributes import (
            _compute_learning_status,
        )

        learner = AdaptiveLearner(heating_type="floor_hydronic")
        mode = get_hvac_heat_mode()

        # Simulate 100 maintenance-only cycles (starting_delta < 0.5C)
        for i in range(100):
            metrics = CycleMetrics(
                overshoot=0.05,
                undershoot=0.0,
                settling_time=10.0,
                oscillations=0,
                rise_time=15.0,
                starting_delta=0.2,  # < 0.5 threshold = maintenance
            )
            learner.update_convergence_confidence(metrics, mode)
            learner.add_cycle_metrics(metrics, mode)

        # Get learning status using the actual function with tracker
        confidence = learner.get_convergence_confidence(mode)
        cycle_count = learner.get_cycle_count(mode)
        contribution_tracker = learner._contribution_tracker

        status = _compute_learning_status(
            cycle_count=cycle_count,
            convergence_confidence=confidence,
            heating_type="floor_hydronic",
            is_paused=False,
            contribution_tracker=contribution_tracker,
            mode=mode,
        )

        # Should never reach "tuned" or "optimized" on maintenance alone
        # The tier gates require recovery cycles, which we have 0 of
        assert status in ("collecting", "stable"), (
            f"Zone should not reach 'tuned' or 'optimized' on maintenance cycles alone, "
            f"got status='{status}' with confidence={confidence:.1%}, "
            f"recovery_cycles={contribution_tracker.get_recovery_cycle_count(mode)}"
        )

        # Verify confidence is significantly lower than it would be without caps
        # Without caps: 100 cycles * 0.03 weighted_gain = 3.0 (capped at 1.0)
        # With caps: ~0.25 (cap) + 0.30 (91 cycles * 0.003 diminishing) = ~0.55
        # The key verification is that it's well below 100% and tier gates block "tuned"
        assert confidence < 0.70, (
            f"Confidence should be significantly limited by maintenance cap, "
            f"got {confidence:.1%}"
        )

        # Verify recovery cycle count is still 0
        assert contribution_tracker.get_recovery_cycle_count(mode) == 0, (
            f"Should have 0 recovery cycles from maintenance-only operation, "
            f"got {contribution_tracker.get_recovery_cycle_count(mode)}"
        )

    def test_zone_progresses_with_recovery_cycles(self):
        """Verify a zone can progress to 'tuned' with sufficient recovery cycles.

        This validates that:
        1. Recovery cycles increase confidence without cap
        2. Recovery cycles count toward tier gates
        3. Zone can reach 'tuned' when both confidence and recovery thresholds are met
        """
        from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
        from custom_components.adaptive_climate.managers.state_attributes import (
            _compute_learning_status,
        )

        learner = AdaptiveLearner(heating_type="floor_hydronic")
        mode = get_hvac_heat_mode()

        # Floor hydronic needs 20 recovery cycles for tier 2 (tuned)
        # Simulate 25 recovery cycles (starting_delta >= 0.5C)
        for _ in range(25):
            metrics = CycleMetrics(
                overshoot=0.05,
                undershoot=0.0,
                settling_time=10.0,
                oscillations=0,
                rise_time=15.0,
                starting_delta=1.0,  # >= 0.5 threshold = recovery
            )
            learner.update_convergence_confidence(metrics, mode)
            learner.add_cycle_metrics(metrics, mode)

        # Get learning status
        confidence = learner.get_convergence_confidence(mode)
        cycle_count = learner.get_cycle_count(mode)
        contribution_tracker = learner._contribution_tracker

        status = _compute_learning_status(
            cycle_count=cycle_count,
            convergence_confidence=confidence,
            heating_type="floor_hydronic",
            is_paused=False,
            contribution_tracker=contribution_tracker,
            mode=mode,
        )

        # Should reach "tuned" with 25 recovery cycles and high confidence
        # Tier 2 threshold for floor_hydronic is 70% * 0.8 = 56%
        # 25 cycles * ~0.1 per cycle = ~2.5 (capped at 1.0)
        assert status in ("tuned", "optimized"), (
            f"Zone should reach 'tuned' or 'optimized' with recovery cycles, "
            f"got status='{status}' with confidence={confidence:.1%}, "
            f"recovery_cycles={contribution_tracker.get_recovery_cycle_count(mode)}"
        )

        # Verify recovery cycle count
        assert contribution_tracker.get_recovery_cycle_count(mode) == 25, (
            f"Should have 25 recovery cycles, "
            f"got {contribution_tracker.get_recovery_cycle_count(mode)}"
        )


# Marker test for module existence
def test_integration_weighted_learning_module_exists():
    """Marker test to verify module can be imported."""
    from custom_components.adaptive_climate.adaptive.cycle_weight import (
        CycleWeightCalculator,
        CycleOutcome,
    )
    from custom_components.adaptive_climate.adaptive.confidence_contribution import (
        ConfidenceContributionTracker,
    )
    assert CycleWeightCalculator is not None
    assert CycleOutcome is not None
    assert ConfidenceContributionTracker is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
