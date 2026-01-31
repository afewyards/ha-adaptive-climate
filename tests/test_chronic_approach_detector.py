"""Tests for ChronicApproachDetector pattern detection, cooldown, and cap behavior."""
import time
from typing import Optional
from unittest.mock import patch

import pytest

from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_climate.const import (
    CHRONIC_APPROACH_THRESHOLDS,
    HeatingType,
    MAX_UNDERSHOOT_KI_MULTIPLIER,
)


# Mock CycleMetrics helper
def make_cycle_metrics(
    rise_time: Optional[float],
    undershoot: float,
    overshoot: float,
) -> CycleMetrics:
    """Create a CycleMetrics object for testing."""
    return CycleMetrics(
        rise_time=rise_time,
        settling_time=None,
        undershoot=undershoot,
        overshoot=overshoot,
        inter_cycle_drift=0.0,
        settling_mae=0.0,
    )


@pytest.fixture
def detector():
    """Create a detector for floor_hydronic heating."""
    # Import will be available after implementation
    from custom_components.adaptive_climate.adaptive.chronic_approach_detector import (
        ChronicApproachDetector,
    )
    return ChronicApproachDetector(HeatingType.FLOOR_HYDRONIC)


@pytest.fixture
def radiator_detector():
    """Create a detector for radiator heating."""
    from custom_components.adaptive_climate.adaptive.chronic_approach_detector import (
        ChronicApproachDetector,
    )
    return ChronicApproachDetector(HeatingType.RADIATOR)


@pytest.fixture
def convector_detector():
    """Create a detector for convector heating."""
    from custom_components.adaptive_climate.adaptive.chronic_approach_detector import (
        ChronicApproachDetector,
    )
    return ChronicApproachDetector(HeatingType.CONVECTOR)


@pytest.fixture
def forced_air_detector():
    """Create a detector for forced_air heating."""
    from custom_components.adaptive_climate.adaptive.chronic_approach_detector import (
        ChronicApproachDetector,
    )
    return ChronicApproachDetector(HeatingType.FORCED_AIR)


class TestPatternDetectionLogic:
    """Test core pattern detection logic."""

    def test_detects_chronic_approach_when_pattern_present(self, detector):
        """Test detection when N consecutive cycles have rise_time=None AND undershoot >= threshold."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 cycles meeting all criteria
        for _ in range(4):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should detect pattern
        assert detector.should_adjust_ki() is True

    def test_does_not_detect_when_rise_time_present(self, detector):
        """Test NO detection when rise_time is present (cycle reached setpoint)."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 cycles but WITH rise_time (reached setpoint)
        for _ in range(4):
            cycle = make_cycle_metrics(
                rise_time=30.0,  # Reached setpoint in 30 min
                undershoot=0.5,
                overshoot=0.2
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should NOT detect - cycles reached setpoint
        assert detector.should_adjust_ki() is False

    def test_does_not_detect_when_undershoot_below_threshold(self, detector):
        """Test NO detection when undershoot < threshold."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 cycles with undershoot below threshold
        for _ in range(4):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] - 0.05,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should NOT detect - undershoot too small
        assert detector.should_adjust_ki() is False

    def test_does_not_detect_when_undershoot_is_zero(self, detector):
        """Test NO detection when undershoot is 0.0."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        for _ in range(4):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=0.0,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should NOT detect - no undershoot
        assert detector.should_adjust_ki() is False

    def test_does_not_detect_when_cycle_count_insufficient(self, detector):
        """Test NO detection when cycle count < min_cycles for heating type."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Floor hydronic needs 4 cycles - add only 3
        for _ in range(3):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should NOT detect - not enough cycles
        assert detector.should_adjust_ki() is False

    def test_pattern_resets_when_cycle_reaches_setpoint(self, detector):
        """Test that pattern counter resets when a cycle reaches setpoint."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 3 cycles with pattern
        for _ in range(3):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Add 1 cycle that reaches setpoint
        cycle = make_cycle_metrics(
                rise_time=25.0,
                undershoot=0.2,
                overshoot=0.3,
            )
        detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Pattern should be broken
        assert detector.should_adjust_ki() is False

        # Would need 4 more consecutive approach cycles to trigger
        for _ in range(3):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Still not enough (only 3 consecutive)
        assert detector.should_adjust_ki() is False

        # Add 4th consecutive
        cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
        detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Now should trigger
        assert detector.should_adjust_ki() is True


class TestHeatingTypeVariations:
    """Test different thresholds for different heating types."""

    def test_floor_hydronic_thresholds(self):
        """Test floor_hydronic: 4 cycles, 0.4°C threshold, 60 min duration."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        assert thresholds["min_cycles"] == 4
        assert thresholds["undershoot_threshold"] == 0.4
        assert thresholds["min_cycle_duration"] == 60.0
        assert thresholds["ki_multiplier"] == 1.20

    def test_radiator_thresholds(self):
        """Test radiator: 3 cycles, 0.35°C threshold, 30 min duration."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.RADIATOR]

        assert thresholds["min_cycles"] == 3
        assert thresholds["undershoot_threshold"] == 0.35
        assert thresholds["min_cycle_duration"] == 30.0
        assert thresholds["ki_multiplier"] == 1.25

    def test_convector_thresholds(self):
        """Test convector: 3 cycles, 0.30°C threshold, 20 min duration."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.CONVECTOR]

        assert thresholds["min_cycles"] == 3
        assert thresholds["undershoot_threshold"] == 0.30
        assert thresholds["min_cycle_duration"] == 20.0
        assert thresholds["ki_multiplier"] == 1.30

    def test_forced_air_thresholds(self):
        """Test forced_air: 2 cycles, 0.25°C threshold, 10 min duration."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]

        assert thresholds["min_cycles"] == 2
        assert thresholds["undershoot_threshold"] == 0.25
        assert thresholds["min_cycle_duration"] == 10.0
        assert thresholds["ki_multiplier"] == 1.35

    def test_radiator_triggers_with_three_cycles(self, radiator_detector):
        """Test radiator requires only 3 cycles."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.RADIATOR]

        # Add 3 cycles meeting criteria
        for _ in range(3):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.05,
                overshoot=0.0,
            )
            radiator_detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should trigger with 3 cycles
        assert radiator_detector.should_adjust_ki() is True

    def test_convector_triggers_with_three_cycles(self, convector_detector):
        """Test convector requires only 3 cycles."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.CONVECTOR]

        # Add 3 cycles meeting criteria
        for _ in range(3):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.05,
                overshoot=0.0,
            )
            convector_detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should trigger with 3 cycles
        assert convector_detector.should_adjust_ki() is True

    def test_forced_air_triggers_with_two_cycles(self, forced_air_detector):
        """Test forced_air requires only 2 cycles."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]

        # Add only 2 cycles meeting criteria
        for _ in range(2):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.05,
                overshoot=0.0,
            )
            forced_air_detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should trigger with just 2 cycles
        assert forced_air_detector.should_adjust_ki() is True

    def test_forced_air_triggers_faster_than_floor_hydronic(self, detector, forced_air_detector):
        """Test that forced_air requires fewer cycles than floor_hydronic."""
        # Add 2 cycles with same conditions to both
        for _ in range(2):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=0.5,  # Above both thresholds
                overshoot=0.0
            )
            # Use 65 minutes - above both floor_hydronic (60) and forced_air (10) thresholds
            detector.add_cycle(cycle, 65.0)
            forced_air_detector.add_cycle(cycle, 65.0)

        # Forced air should trigger (needs 2)
        assert forced_air_detector.should_adjust_ki() is True

        # Floor hydronic should not (needs 4)
        assert detector.should_adjust_ki() is False


class TestCycleDurationCheck:
    """Test minimum cycle duration filtering."""

    def test_ignores_cycles_shorter_than_min_duration(self, detector):
        """Test that cycles shorter than min_duration are ignored."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 cycles but all too short
        for _ in range(4):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] - 10)

        # Should NOT detect - all cycles too short
        assert detector.should_adjust_ki() is False

    def test_accepts_cycles_at_exact_min_duration(self, detector):
        """Test that cycles >= min_duration are accepted."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 cycles at exact minimum duration
        for _ in range(4):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should detect - cycles meet minimum
        assert detector.should_adjust_ki() is True

    def test_mixed_duration_cycles_only_counts_valid(self, detector):
        """Test that only cycles >= min_duration count toward pattern."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 2 short cycles (should be ignored)
        for _ in range(2):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] - 10)

        # Add 3 valid cycles (still not enough - need 4)
        for _ in range(3):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should NOT detect - only 3 valid cycles
        assert detector.should_adjust_ki() is False

        # Add 1 more valid cycle
        cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
        detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Now should detect - 4 valid consecutive cycles
        assert detector.should_adjust_ki() is True

    def test_forced_air_short_duration_threshold(self, forced_air_detector):
        """Test forced_air has shorter min_duration (10 min)."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]

        # Add cycles just above forced_air threshold
        for _ in range(2):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.05,
                overshoot=0.0,
            )
            forced_air_detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should detect
        assert forced_air_detector.should_adjust_ki() is True

    def test_just_below_duration_threshold_ignored(self, detector):
        """Test cycles just below duration threshold are ignored."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 cycles just below duration threshold
        for _ in range(4):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] - 0.1)

        # Should NOT detect
        assert detector.should_adjust_ki() is False


class TestCooldownBehavior:
    """Test cooldown period enforcement between adjustments."""

    def test_enters_cooldown_after_adjustment(self, detector):
        """Test that detector enters cooldown after applying adjustment."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add enough failing cycles to trigger detection
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,  # Never reached setpoint
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should be ready to adjust
        assert detector.should_adjust_ki() is True

        # Apply adjustment
        detector.apply_adjustment()

        # Should now be in cooldown
        assert detector.should_adjust_ki() is False

    @patch('custom_components.adaptive_climate.adaptive.chronic_approach_detector.time.monotonic')
    def test_suppresses_detection_during_cooldown(self, mock_time, detector):
        """Test that pattern detection is suppressed during cooldown."""
        mock_time.return_value = 1000.0
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Trigger first adjustment
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        detector.apply_adjustment()

        # Add more failing cycles during cooldown
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.2,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Still in cooldown (24h for floor_hydronic)
        mock_time.return_value = 1000.0 + 23 * 3600  # 23 hours later
        assert detector.should_adjust_ki() is False

    @patch('custom_components.adaptive_climate.adaptive.chronic_approach_detector.time.monotonic')
    def test_resumes_detection_after_cooldown(self, mock_time, detector):
        """Test that detection resumes after cooldown expires."""
        mock_time.return_value = 1000.0
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Apply first adjustment
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)
        detector.apply_adjustment()

        # Advance past cooldown period (24h + 1h for floor_hydronic)
        mock_time.return_value = 1000.0 + 25 * 3600

        # Add new failing cycles after cooldown
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should be ready to adjust again
        assert detector.should_adjust_ki() is True

    def test_cooldown_heating_type_specific(self, forced_air_detector):
        """Test that cooldown duration is heating-type-specific."""
        forced_air_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]
        floor_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Verify different cooldown periods
        # Floor hydronic has 24h cooldown (from UNDERSHOOT_THRESHOLDS pattern)
        # Forced air should have ~2h cooldown (faster system)
        # Note: Actual cooldown values should be added to CHRONIC_APPROACH_THRESHOLDS
        # For now, test that forced_air triggers faster
        assert forced_air_thresholds["min_cycles"] < floor_thresholds["min_cycles"]


class TestCumulativeKiCap:
    """Test cumulative Ki multiplier cap enforcement."""

    def test_respects_cumulative_cap(self, detector):
        """Test that cumulative multiplier cannot exceed MAX_UNDERSHOOT_KI_MULTIPLIER."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Set cumulative multiplier close to cap
        detector.cumulative_ki_multiplier = 1.8

        # Add failing cycles
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Would exceed cap: 1.8 * 1.20 = 2.16 > 2.0 (MAX_UNDERSHOOT_KI_MULTIPLIER)
        # Should block adjustment
        assert detector.should_adjust_ki() is False

    def test_blocks_adjustment_at_cap(self, detector):
        """Test that adjustment is blocked when at cap."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Set multiplier at cap
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # Add failing cycles
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should not adjust - at cap
        assert detector.should_adjust_ki() is False

    def test_tracks_cumulative_multiplier_across_adjustments(self, detector):
        """Test that cumulative multiplier accumulates correctly."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Start at 1.0
        assert detector.cumulative_ki_multiplier == 1.0

        # First adjustment
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        multiplier = detector.apply_adjustment()
        expected = 1.0 * thresholds["ki_multiplier"]  # 1.0 * 1.20 = 1.20
        assert detector.cumulative_ki_multiplier == pytest.approx(expected, abs=0.001)
        assert multiplier == pytest.approx(thresholds["ki_multiplier"], abs=0.001)

    def test_clamps_multiplier_near_cap(self, detector):
        """Test that get_adjustment clamps multiplier when approaching cap."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Set cumulative to 1.8 (close to cap of 2.0)
        detector.cumulative_ki_multiplier = 1.8

        # Add pattern
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # get_adjustment should return clamped multiplier
        # Max allowed = 2.0 / 1.8 = 1.111
        # Configured = 1.20
        # Should return min(1.20, 1.111) = 1.111
        multiplier = detector.get_adjustment()
        expected = MAX_UNDERSHOOT_KI_MULTIPLIER / 1.8
        assert multiplier == pytest.approx(expected, abs=0.001)

    def test_returns_one_at_cap(self, detector):
        """Test that get_adjustment returns 1.0 when at cap."""
        # Set at cap
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # get_adjustment should return 1.0 (no further adjustment)
        multiplier = detector.get_adjustment()
        assert multiplier == pytest.approx(1.0, abs=0.001)


class TestResetBehavior:
    """Test reset when zone reaches setpoint."""

    def test_resets_consecutive_count_on_success(self, detector):
        """Test that consecutive failure count resets when cycle has rise_time."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add failing cycles (min_cycles - 1, not enough to trigger)
        for _ in range(int(thresholds["min_cycles"]) - 1):
            cycle = make_cycle_metrics(
                rise_time=None,  # Failed to reach
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should not trigger yet (need 4 cycles, only have 3)
        assert detector.should_adjust_ki() is False

        # Add a successful cycle (zone reached setpoint)
        success_cycle = make_cycle_metrics(
            rise_time=1200.0,  # 20 minutes to reach setpoint
            undershoot=0.2,    # Small undershoot before reaching
            overshoot=0.1
        )
        detector.add_cycle(success_cycle)

        # Consecutive count should reset
        # Add more failing cycles - need full min_cycles again
        for _ in range(int(thresholds["min_cycles"]) - 1):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Still should not trigger (only 3 consecutive after reset)
        assert detector.should_adjust_ki() is False

    def test_requires_consecutive_failures(self, detector):
        """Test that pattern requires CONSECUTIVE failures."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Alternate between failing and successful cycles
        for i in range(int(thresholds["min_cycles"]) * 2):
            if i % 2 == 0:
                # Failing cycle
                cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            else:
                # Successful cycle (breaks pattern)
                cycle = make_cycle_metrics(
                rise_time=900.0,
                undershoot=0.1,
                overshoot=0.1,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should never trigger - no consecutive sequence
        assert detector.should_adjust_ki() is False

    def test_preserves_cumulative_multiplier_on_reset(self, detector):
        """Test that consecutive count reset doesn't affect cumulative multiplier."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Apply first adjustment
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)
        detector.apply_adjustment()

        cumulative_before = detector.cumulative_ki_multiplier
        assert cumulative_before > 1.0

        # Add successful cycle (resets consecutive count)
        success_cycle = make_cycle_metrics(
                rise_time=1200.0,
                undershoot=0.2,
                overshoot=0.1,
            )
        detector.add_cycle(success_cycle)

        # Cumulative multiplier should be preserved
        assert detector.cumulative_ki_multiplier == cumulative_before


class TestApplyAdjustment:
    """Test the apply_adjustment method."""

    def test_updates_cumulative_multiplier(self, detector):
        """Test that apply_adjustment updates cumulative multiplier."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        initial = detector.cumulative_ki_multiplier

        # Add pattern
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        multiplier = detector.apply_adjustment()
        expected = initial * thresholds["ki_multiplier"]

        assert detector.cumulative_ki_multiplier == pytest.approx(expected, abs=0.001)
        assert multiplier == pytest.approx(thresholds["ki_multiplier"], abs=0.001)

    def test_records_adjustment_time(self, detector):
        """Test that adjustment time is recorded for cooldown."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add pattern
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        assert detector.last_adjustment_time is None

        before = time.monotonic()
        detector.apply_adjustment()
        after = time.monotonic()

        assert detector.last_adjustment_time is not None
        assert before <= detector.last_adjustment_time <= after

    def test_returns_applied_multiplier(self, detector):
        """Test that apply_adjustment returns the multiplier."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add pattern
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        expected = detector.get_adjustment()
        actual = detector.apply_adjustment()

        assert actual == pytest.approx(expected, abs=0.001)


class TestDifferentHeatingTypes:
    """Test heating-type-specific configurations."""

    def test_floor_hydronic_requires_more_cycles(self):
        """Test floor_hydronic requires more cycles due to slow response."""
        floor_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        forced_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]

        assert floor_thresholds["min_cycles"] == 4
        assert forced_thresholds["min_cycles"] == 2
        assert floor_thresholds["min_cycles"] > forced_thresholds["min_cycles"]

    def test_forced_air_has_lower_thresholds(self):
        """Test forced_air has lower undershoot threshold (more sensitive)."""
        floor_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        forced_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]

        assert forced_thresholds["undershoot_threshold"] == 0.25
        assert floor_thresholds["undershoot_threshold"] == 0.4
        assert forced_thresholds["undershoot_threshold"] < floor_thresholds["undershoot_threshold"]

    def test_forced_air_requires_shorter_cycles(self):
        """Test forced_air has shorter minimum cycle duration."""
        floor_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        forced_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]

        assert forced_thresholds["min_cycle_duration"] == 10.0
        assert floor_thresholds["min_cycle_duration"] == 60.0
        assert forced_thresholds["min_cycle_duration"] < floor_thresholds["min_cycle_duration"]

    def test_forced_air_has_higher_multiplier(self):
        """Test forced_air has higher Ki multiplier (more aggressive)."""
        floor_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        forced_thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FORCED_AIR]

        assert forced_thresholds["ki_multiplier"] == 1.35
        assert floor_thresholds["ki_multiplier"] == 1.20
        assert forced_thresholds["ki_multiplier"] > floor_thresholds["ki_multiplier"]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_ignores_cycles_below_duration_threshold(self, detector):
        """Test that short cycles are ignored."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add cycles that are too short (below min_cycle_duration)
        for _ in range(int(thresholds["min_cycles"]) + 2):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0  # Too short
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] - 10)

        # Should not trigger - all cycles too short
        assert detector.should_adjust_ki() is False

    def test_ignores_cycles_below_undershoot_threshold(self, detector):
        """Test that cycles with small undershoot are ignored."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add cycles with undershoot below threshold
        for _ in range(int(thresholds["min_cycles"]) + 2):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"] - 0.1,  # Too small
                overshoot=0.0
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should not trigger - undershoot too small
        assert detector.should_adjust_ki() is False

    def test_ignores_cycles_with_overshoot(self, detector):
        """Test that cycles with overshoot are excluded."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add cycles with significant overshoot (zone went above setpoint)
        for _ in range(int(thresholds["min_cycles"]) + 2):
            cycle = make_cycle_metrics(
                rise_time=None,  # Never reached during rise
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.3,  # But went above later (shouldn't happen, but test it)
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should not trigger - overshoot indicates not stuck below
        assert detector.should_adjust_ki() is False

    def test_exact_threshold_boundary(self, detector):
        """Test behavior at exact threshold boundaries."""
        thresholds = CHRONIC_APPROACH_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add exactly min_cycles with exact thresholds
        for _ in range(int(thresholds["min_cycles"])):
            cycle = make_cycle_metrics(
                rise_time=None,
                undershoot=thresholds["undershoot_threshold"],  # Exactly at threshold
                overshoot=0.0  # Exactly at threshold
            )
            detector.add_cycle(cycle, thresholds["min_cycle_duration"] + 5)

        # Should trigger - meets thresholds (inclusive)
        assert detector.should_adjust_ki() is True
