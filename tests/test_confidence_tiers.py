"""Tests for 3-tier confidence system in learning status computation."""

import pytest
from custom_components.adaptive_climate.managers.state_attributes import (
    _compute_learning_status,
)
from custom_components.adaptive_climate.const import (
    HeatingType,
    MIN_CYCLES_FOR_LEARNING,
    CONFIDENCE_TIER_1,
    CONFIDENCE_TIER_2,
    CONFIDENCE_TIER_3,
    HEATING_TYPE_CONFIDENCE_SCALE,
)


# ============================================================================
# Confidence Tier Threshold Calculation Tests
# ============================================================================


class TestConfidenceTierScaling:
    """Test heating-type scaling of confidence tier thresholds."""

    def test_floor_hydronic_scaled_thresholds(self):
        """Floor hydronic should hit stable at 32% (40 * 0.8)."""
        heating_type = HeatingType.FLOOR_HYDRONIC
        scale = HEATING_TYPE_CONFIDENCE_SCALE[heating_type]

        # Calculate scaled thresholds
        scaled_tier_1 = CONFIDENCE_TIER_1 * scale / 100.0  # 32% -> 0.32
        scaled_tier_2 = CONFIDENCE_TIER_2 * scale / 100.0  # 56% -> 0.56

        # Below tier 1 = collecting
        status = _compute_learning_status(10, 0.31, heating_type, is_paused=False)
        assert status == "collecting"

        # At tier 1 = stable
        status = _compute_learning_status(10, 0.32, heating_type, is_paused=False)
        assert status == "stable"

        # Between tier 1 and tier 2 = stable
        status = _compute_learning_status(10, 0.40, heating_type, is_paused=False)
        assert status == "stable"

        # At tier 2 = tuned
        status = _compute_learning_status(10, 0.56, heating_type, is_paused=False)
        assert status == "tuned"

        # Between tier 2 and tier 3 = tuned
        status = _compute_learning_status(10, 0.80, heating_type, is_paused=False)
        assert status == "tuned"

        # At tier 3 (95%) = optimized (tier 3 is NOT scaled)
        status = _compute_learning_status(10, 0.95, heating_type, is_paused=False)
        assert status == "optimized"

    def test_radiator_scaled_thresholds(self):
        """Radiator should hit stable at 36% (40 * 0.9)."""
        heating_type = HeatingType.RADIATOR
        scale = HEATING_TYPE_CONFIDENCE_SCALE[heating_type]

        scaled_tier_1 = CONFIDENCE_TIER_1 * scale / 100.0  # 36% -> 0.36
        scaled_tier_2 = CONFIDENCE_TIER_2 * scale / 100.0  # 63% -> 0.63

        # Below tier 1 = collecting
        status = _compute_learning_status(10, 0.35, heating_type, is_paused=False)
        assert status == "collecting"

        # At tier 1 = stable
        status = _compute_learning_status(10, 0.36, heating_type, is_paused=False)
        assert status == "stable"

        # At tier 2 = tuned
        status = _compute_learning_status(10, 0.63, heating_type, is_paused=False)
        assert status == "tuned"

        # At tier 3 = optimized
        status = _compute_learning_status(10, 0.95, heating_type, is_paused=False)
        assert status == "optimized"

    def test_convector_baseline_thresholds(self):
        """Convector is baseline - stable at 40%, tuned at 70%."""
        heating_type = HeatingType.CONVECTOR

        # Baseline scaling factor is 1.0
        scaled_tier_1 = CONFIDENCE_TIER_1 * 1.0 / 100.0  # 40% -> 0.40
        scaled_tier_2 = CONFIDENCE_TIER_2 * 1.0 / 100.0  # 70% -> 0.70

        # Below tier 1 = collecting
        status = _compute_learning_status(10, 0.39, heating_type, is_paused=False)
        assert status == "collecting"

        # At tier 1 = stable
        status = _compute_learning_status(10, 0.40, heating_type, is_paused=False)
        assert status == "stable"

        # At tier 2 = tuned
        status = _compute_learning_status(10, 0.70, heating_type, is_paused=False)
        assert status == "tuned"

        # At tier 3 = optimized
        status = _compute_learning_status(10, 0.95, heating_type, is_paused=False)
        assert status == "optimized"

    def test_forced_air_higher_thresholds(self):
        """Forced air should hit stable at 44% (40 * 1.1)."""
        heating_type = HeatingType.FORCED_AIR
        scale = HEATING_TYPE_CONFIDENCE_SCALE[heating_type]

        scaled_tier_1 = CONFIDENCE_TIER_1 * scale / 100.0  # 44% -> 0.44
        scaled_tier_2 = CONFIDENCE_TIER_2 * scale / 100.0  # 77% -> 0.77

        # Below tier 1 = collecting
        status = _compute_learning_status(10, 0.43, heating_type, is_paused=False)
        assert status == "collecting"

        # At tier 1 = stable
        status = _compute_learning_status(10, 0.44, heating_type, is_paused=False)
        assert status == "stable"

        # At tier 2 = tuned
        status = _compute_learning_status(10, 0.77, heating_type, is_paused=False)
        assert status == "tuned"

        # At tier 3 = optimized
        status = _compute_learning_status(10, 0.95, heating_type, is_paused=False)
        assert status == "optimized"


# ============================================================================
# Learning Status State Transitions
# ============================================================================


class TestLearningStatusStates:
    """Test all learning status state transitions."""

    def test_idle_when_paused(self):
        """Any pause condition should return idle regardless of confidence."""
        heating_type = HeatingType.CONVECTOR

        # Even with high confidence and many cycles, pause = idle
        status = _compute_learning_status(20, 0.95, heating_type, is_paused=True)
        assert status == "idle"

        status = _compute_learning_status(20, 0.70, heating_type, is_paused=True)
        assert status == "idle"

        status = _compute_learning_status(20, 0.40, heating_type, is_paused=True)
        assert status == "idle"

    def test_collecting_insufficient_cycles(self):
        """Collecting when cycles < MIN_CYCLES_FOR_LEARNING."""
        heating_type = HeatingType.CONVECTOR

        # Even with high confidence, insufficient cycles = collecting
        status = _compute_learning_status(MIN_CYCLES_FOR_LEARNING - 1, 0.95, heating_type, is_paused=False)
        assert status == "collecting"

    def test_collecting_low_confidence(self):
        """Collecting when confidence below scaled tier 1."""
        heating_type = HeatingType.CONVECTOR

        # Enough cycles but confidence too low
        status = _compute_learning_status(10, 0.35, heating_type, is_paused=False)
        assert status == "collecting"

    def test_stable_at_tier_1_boundary(self):
        """Stable exactly at scaled tier 1 boundary."""
        heating_type = HeatingType.CONVECTOR

        # Exactly at 40% confidence
        status = _compute_learning_status(10, 0.40, heating_type, is_paused=False)
        assert status == "stable"

    def test_stable_between_tier_1_and_tier_2(self):
        """Stable when confidence between tier 1 and tier 2."""
        heating_type = HeatingType.CONVECTOR

        # 50% is between 40% (tier 1) and 70% (tier 2)
        status = _compute_learning_status(10, 0.50, heating_type, is_paused=False)
        assert status == "stable"

        # 69% is just below tier 2
        status = _compute_learning_status(10, 0.69, heating_type, is_paused=False)
        assert status == "stable"

    def test_tuned_at_tier_2_boundary(self):
        """Tuned exactly at scaled tier 2 boundary."""
        heating_type = HeatingType.CONVECTOR

        # Exactly at 70% confidence
        status = _compute_learning_status(10, 0.70, heating_type, is_paused=False)
        assert status == "tuned"

    def test_tuned_between_tier_2_and_tier_3(self):
        """Tuned when confidence between tier 2 and tier 3."""
        heating_type = HeatingType.CONVECTOR

        # 80% is between 70% (tier 2) and 95% (tier 3)
        status = _compute_learning_status(10, 0.80, heating_type, is_paused=False)
        assert status == "tuned"

        # 94% is just below tier 3
        status = _compute_learning_status(10, 0.94, heating_type, is_paused=False)
        assert status == "tuned"

    def test_optimized_at_tier_3_boundary(self):
        """Optimized exactly at tier 3 boundary (95%)."""
        heating_type = HeatingType.CONVECTOR

        # Exactly at 95% confidence
        status = _compute_learning_status(10, 0.95, heating_type, is_paused=False)
        assert status == "optimized"

    def test_optimized_above_tier_3(self):
        """Optimized when confidence above tier 3."""
        heating_type = HeatingType.CONVECTOR

        # Above 95%
        status = _compute_learning_status(10, 0.98, heating_type, is_paused=False)
        assert status == "optimized"


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_confidence(self):
        """Zero confidence should be collecting."""
        heating_type = HeatingType.CONVECTOR

        status = _compute_learning_status(10, 0.0, heating_type, is_paused=False)
        assert status == "collecting"

    def test_perfect_confidence(self):
        """Perfect confidence (1.0) should be optimized."""
        heating_type = HeatingType.CONVECTOR

        status = _compute_learning_status(10, 1.0, heating_type, is_paused=False)
        assert status == "optimized"

    def test_unknown_heating_type_fallback(self):
        """Unknown heating type should use convector baseline."""
        # Use a string that's not a valid HeatingType
        heating_type = "unknown_type"

        # Should use convector thresholds (40%, 70%, 95%)
        status = _compute_learning_status(10, 0.40, heating_type, is_paused=False)
        assert status == "stable"

        status = _compute_learning_status(10, 0.70, heating_type, is_paused=False)
        assert status == "tuned"

    def test_tier_3_not_scaled(self):
        """Tier 3 (optimized) should always be 95% regardless of heating type."""
        # All heating types should require 95% for optimized
        for heating_type in HeatingType:
            status = _compute_learning_status(10, 0.94, heating_type, is_paused=False)
            assert status != "optimized", f"{heating_type} should not be optimized at 94%"

            status = _compute_learning_status(10, 0.95, heating_type, is_paused=False)
            assert status == "optimized", f"{heating_type} should be optimized at 95%"


# ============================================================================
# Integration Tests
# ============================================================================


class TestConfidenceProgressionScenarios:
    """Test realistic confidence progression scenarios."""

    def test_floor_hydronic_progression(self):
        """Simulate confidence progression for floor hydronic system."""
        heating_type = HeatingType.FLOOR_HYDRONIC
        cycles = 10  # Enough cycles

        # Start: 20% confidence -> collecting
        status = _compute_learning_status(cycles, 0.20, heating_type, is_paused=False)
        assert status == "collecting"

        # Reach tier 1: 32% -> stable
        status = _compute_learning_status(cycles, 0.32, heating_type, is_paused=False)
        assert status == "stable"

        # Progress: 45% -> still stable
        status = _compute_learning_status(cycles, 0.45, heating_type, is_paused=False)
        assert status == "stable"

        # Reach tier 2: 56% -> tuned
        status = _compute_learning_status(cycles, 0.56, heating_type, is_paused=False)
        assert status == "tuned"

        # Progress: 80% -> still tuned
        status = _compute_learning_status(cycles, 0.80, heating_type, is_paused=False)
        assert status == "tuned"

        # Reach tier 3: 95% -> optimized
        status = _compute_learning_status(cycles, 0.95, heating_type, is_paused=False)
        assert status == "optimized"

    def test_forced_air_faster_progression(self):
        """Forced air should require higher confidence for each tier."""
        heating_type = HeatingType.FORCED_AIR
        cycles = 10

        # 40% would be stable for convector, but collecting for forced air
        status = _compute_learning_status(cycles, 0.40, heating_type, is_paused=False)
        assert status == "collecting"

        # 44% -> stable
        status = _compute_learning_status(cycles, 0.44, heating_type, is_paused=False)
        assert status == "stable"

        # 70% would be tuned for convector, but stable for forced air
        status = _compute_learning_status(cycles, 0.70, heating_type, is_paused=False)
        assert status == "stable"

        # 77% -> tuned
        status = _compute_learning_status(cycles, 0.77, heating_type, is_paused=False)
        assert status == "tuned"

        # 95% -> optimized (same for all)
        status = _compute_learning_status(cycles, 0.95, heating_type, is_paused=False)
        assert status == "optimized"
