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
