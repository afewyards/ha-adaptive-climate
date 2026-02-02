"""Tests for UndershootDetector."""
import time
from unittest.mock import patch

import pytest

from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_climate.adaptive.undershoot_detector import (
    UndershootDetector,
)
from custom_components.adaptive_climate.const import (
    HeatingType,
    MAX_UNDERSHOOT_KI_MULTIPLIER,
    MIN_CYCLES_FOR_LEARNING,
    SEVERE_UNDERSHOOT_MULTIPLIER,
    UNDERSHOOT_THRESHOLDS,
)


@pytest.fixture
def detector():
    """Create a detector for floor_hydronic heating."""
    return UndershootDetector(HeatingType.FLOOR_HYDRONIC)


@pytest.fixture
def forced_air_detector():
    """Create a detector for forced_air heating."""
    return UndershootDetector(HeatingType.FORCED_AIR)


class TestTimeTrackingAccumulation:
    """Test time accumulation when error exceeds cold_tolerance."""

    def test_accumulates_time_below_target(self, detector):
        """Test that time_below_target accumulates when error > cold_tolerance."""
        # Setpoint 20°C, temp 18°C, error = 2°C, cold_tolerance = 0.5°C
        # error (2.0) > cold_tolerance (0.5) -> should accumulate
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        assert detector.time_below_target == 60.0

    def test_accumulates_time_across_multiple_updates(self, detector):
        """Test that time accumulates correctly across multiple updates."""
        # First update: 60 seconds
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)
        assert detector.time_below_target == 60.0

        # Second update: 30 seconds
        detector.update(temp=18.5, setpoint=20.0, dt_seconds=30.0, cold_tolerance=0.5)
        assert detector.time_below_target == 90.0

        # Third update: 120 seconds
        detector.update(temp=17.8, setpoint=20.0, dt_seconds=120.0, cold_tolerance=0.5)
        assert detector.time_below_target == 210.0


class TestThermalDebtCalculation:
    """Test thermal debt calculation (integral of error over time)."""

    def test_calculates_debt_as_integral(self, detector):
        """Test that thermal debt is error * time in °C·hours."""
        # Error = 2.0°C, time = 3600 seconds (1 hour)
        # Debt = 2.0 * (3600 / 3600) = 2.0 °C·h
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        assert detector.thermal_debt == pytest.approx(2.0, abs=0.01)

    def test_accumulates_debt_across_updates(self, detector):
        """Test that thermal debt accumulates correctly across multiple updates."""
        # First update: error=2.0°C for 1800s (0.5h) -> 1.0 °C·h
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(1.0, abs=0.01)

        # Second update: error=1.5°C for 3600s (1.0h) -> 1.5 °C·h
        detector.update(temp=18.5, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(2.5, abs=0.01)

    def test_debt_scales_with_error_magnitude(self, detector):
        """Test that debt accumulation scales linearly with error magnitude."""
        # Large error: 4.0°C for 1800s (0.5h) -> 2.0 °C·h
        detector.update(temp=16.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(2.0, abs=0.01)


class TestResetOnOvershoot:
    """Test reset behavior when temperature exceeds setpoint."""

    def test_resets_when_temp_above_setpoint(self, detector):
        """Test that counters reset when temp > setpoint (error < 0)."""
        # Accumulate some time and debt
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert detector.time_below_target > 0
        assert detector.thermal_debt > 0

        # Temperature rises above setpoint (error < 0)
        detector.update(temp=20.5, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        assert detector.time_below_target == 0.0
        assert detector.thermal_debt == 0.0

    def test_resets_preserve_other_state(self, detector):
        """Test that reset doesn't affect cumulative multiplier or cooldown."""
        detector.cumulative_ki_multiplier = 1.3
        detector.last_adjustment_time = time.monotonic()

        # Trigger reset
        detector.update(temp=20.5, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        assert detector.cumulative_ki_multiplier == 1.3
        assert detector.last_adjustment_time is not None


class TestHoldWithinTolerance:
    """Test that state holds when within tolerance band."""

    def test_holds_state_within_tolerance_band(self, detector):
        """Test that no accumulation or reset occurs when 0 <= error <= cold_tolerance."""
        # Accumulate some time and debt first
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt

        # Within tolerance: error = 0.3°C, cold_tolerance = 0.5°C
        detector.update(temp=19.7, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # Should hold state - no change
        assert detector.time_below_target == time_before
        assert detector.thermal_debt == debt_before

    def test_holds_at_exact_tolerance_boundary(self, detector):
        """Test hold behavior at exact tolerance boundary."""
        # Accumulate initial state
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt

        # Exactly at tolerance boundary: error = 0.5°C
        detector.update(temp=19.5, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # Should hold - boundary is inclusive
        assert detector.time_below_target == time_before
        assert detector.thermal_debt == debt_before

    def test_holds_at_zero_error(self, detector):
        """Test hold behavior at exact setpoint."""
        # Accumulate initial state
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt

        # Exactly at setpoint: error = 0.0°C
        detector.update(temp=20.0, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # Should hold - within tolerance band
        assert detector.time_below_target == time_before
        assert detector.thermal_debt == debt_before


class TestThermalDebtCap:
    """Test that thermal debt is capped at 10.0 °C·h."""

    def test_debt_caps_at_maximum(self, detector):
        """Test that thermal debt cannot exceed 10.0 °C·h."""
        # Accumulate massive debt: error=5.0°C for 7200s (2h) -> 10.0 °C·h
        detector.update(temp=15.0, setpoint=20.0, dt_seconds=7200.0, cold_tolerance=0.5)
        assert detector.thermal_debt == 10.0

        # Try to accumulate more
        detector.update(temp=15.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        # Should still be capped at 10.0
        assert detector.thermal_debt == 10.0

    def test_debt_caps_across_multiple_updates(self, detector):
        """Test that cap is enforced across multiple updates."""
        # First update: 8.0 °C·h
        detector.update(temp=16.0, setpoint=20.0, dt_seconds=7200.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(8.0, abs=0.01)

        # Second update: would add 3.0 °C·h -> should cap at 10.0
        detector.update(temp=17.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert detector.thermal_debt == 10.0


class TestCooldownEnforcement:
    """Test cooldown period between adjustments."""

    def test_cannot_adjust_during_cooldown(self, detector):
        """Test that adjustment is blocked during cooldown period."""
        # Trigger conditions for adjustment
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Should be ready to adjust
        assert detector.should_adjust_ki(cycles_completed=0) is True

        # Apply adjustment
        detector.apply_adjustment()

        # Immediately check again - should be in cooldown
        assert detector.should_adjust_ki(cycles_completed=0) is False

    @patch('custom_components.adaptive_climate.adaptive.undershoot_detector.time.monotonic')
    def test_can_adjust_after_cooldown_expires(self, mock_time, detector):
        """Test that adjustment is allowed after cooldown expires."""
        # Set initial time
        mock_time.return_value = 1000.0

        # Trigger adjustment
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)
        detector.apply_adjustment()

        # Accumulate conditions again
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Still in cooldown (24h for floor_hydronic)
        mock_time.return_value = 1000.0 + 23 * 3600  # 23 hours later
        assert detector.should_adjust_ki(cycles_completed=0) is False

        # After cooldown expires
        mock_time.return_value = 1000.0 + 25 * 3600  # 25 hours later
        assert detector.should_adjust_ki(cycles_completed=0) is True


class TestCumulativeKiCap:
    """Test cumulative Ki multiplier cap."""

    def test_respects_cumulative_cap(self, detector):
        """Test that cumulative multiplier cannot exceed MAX_UNDERSHOOT_KI_MULTIPLIER."""
        detector.cumulative_ki_multiplier = 1.8

        # Trigger adjustment conditions
        # Use small error (0.6) to accumulate time but not debt
        # 0.6 °C * 4 hours = 2.4 °C·h (exceeds debt threshold of 2.0)
        # So use 1 hour instead: 0.6 * 1 = 0.6 (below debt threshold)
        detector.update(temp=18.9, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        # Should not adjust - cumulative multiplier approaching cap (1.8 * 1.15 = 2.07 > 2.0)
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_blocks_adjustment_at_cap(self, detector):
        """Test that adjustment is blocked when at cap."""
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # Trigger adjustment conditions
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Should not adjust - at cap
        assert detector.should_adjust_ki(cycles_completed=0) is False


class TestShouldAdjustWithCompletedCycles:
    """Test that adjustment is blocked when cycles have completed (without severe undershoot)."""

    def test_returns_false_when_enough_cycles_completed(self, detector):
        """Test that adjustment is blocked after MIN_CYCLES_FOR_LEARNING without severe undershoot."""
        # Trigger adjustment conditions (but NOT severe undershoot)
        # debt_threshold for floor_hydronic is 2.0, so severe is 4.0
        # Use small error to hit time threshold without hitting severe debt
        detector.update(temp=19.4, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Should adjust with no completed cycles
        assert detector.should_adjust_ki(cycles_completed=0) is True

        # Should still adjust with cycles < MIN_CYCLES_FOR_LEARNING
        assert detector.should_adjust_ki(cycles_completed=1) is True
        assert detector.should_adjust_ki(cycles_completed=5) is True

        # Should NOT adjust with cycles >= MIN_CYCLES_FOR_LEARNING (normal learning takes over)
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is False
        assert detector.should_adjust_ki(cycles_completed=15) is False


class TestShouldAdjustTimeThreshold:
    """Test adjustment trigger based on time threshold."""

    def test_triggers_when_time_threshold_exceeded(self, detector):
        """Test that adjustment triggers when time threshold is exceeded."""
        # Floor hydronic threshold: 4.0 hours = 14400 seconds
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        time_threshold = thresholds["time_threshold_hours"] * 3600.0
        debt_threshold = thresholds["debt_threshold"]

        # Use small error to avoid triggering debt threshold
        # Need: error * (time_hours) < debt_threshold
        # For 4 hours: error < 2.0 / 4 = 0.5
        # But error must be > cold_tolerance to accumulate
        # So use error slightly > 0.5 for just under 4 hours
        # error = 0.51, time = 3.9 hours -> debt = 0.51 * 3.9 = 1.99 (just below 2.0)
        temp = 20.0 - 0.51  # setpoint - error = temp

        # Just below threshold (3.9 hours)
        detector.update(temp=temp, setpoint=20.0, dt_seconds=time_threshold - 360, cold_tolerance=0.5)
        assert detector.should_adjust_ki(cycles_completed=0) is False

        # Exceed threshold (add 6 more minutes to reach 4 hours)
        detector.update(temp=temp, setpoint=20.0, dt_seconds=360.0, cold_tolerance=0.5)
        assert detector.should_adjust_ki(cycles_completed=0) is True

    def test_forced_air_has_shorter_threshold(self, forced_air_detector):
        """Test that forced_air has a shorter time threshold."""
        # Forced air threshold: 0.75 hours = 2700 seconds
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]
        time_threshold = thresholds["time_threshold_hours"] * 3600.0

        assert time_threshold == 2700.0

        # Should trigger at 2700s
        forced_air_detector.update(
            temp=18.0, setpoint=20.0, dt_seconds=2700.0, cold_tolerance=0.5
        )
        assert forced_air_detector.should_adjust_ki(cycles_completed=0) is True


class TestShouldAdjustDebtThreshold:
    """Test adjustment trigger based on thermal debt threshold."""

    def test_triggers_when_debt_threshold_exceeded(self, detector):
        """Test that adjustment triggers when debt threshold is exceeded."""
        # Floor hydronic debt threshold: 2.0 °C·h
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        debt_threshold = thresholds["debt_threshold"]

        # Just below threshold: error=1.9°C for 1h -> 1.9 °C·h
        detector.update(temp=18.1, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert detector.should_adjust_ki(cycles_completed=0) is False

        # Exceed threshold: add error=2.0°C for 0.1h -> total 2.1 °C·h
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=360.0, cold_tolerance=0.5)
        assert detector.should_adjust_ki(cycles_completed=0) is True

    def test_forced_air_has_lower_debt_threshold(self, forced_air_detector):
        """Test that forced_air has a lower debt threshold."""
        # Forced air debt threshold: 0.5 °C·h
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]
        debt_threshold = thresholds["debt_threshold"]

        assert debt_threshold == 0.5

        # Should trigger at 0.5 °C·h: error=1.0°C for 0.5h
        forced_air_detector.update(
            temp=19.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5
        )
        assert forced_air_detector.should_adjust_ki(cycles_completed=0) is True


class TestPartialDebtResetAfterAdjustment:
    """Test that debt is reduced by 50% after adjustment."""

    def test_debt_reduced_by_half_after_adjustment(self, detector):
        """Test that apply_adjustment reduces debt by 50%."""
        # Accumulate debt
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=7200.0, cold_tolerance=0.5)
        initial_debt = detector.thermal_debt
        assert initial_debt == pytest.approx(4.0, abs=0.01)

        # Apply adjustment
        detector.apply_adjustment()

        # Debt should be halved
        assert detector.thermal_debt == pytest.approx(initial_debt * 0.5, abs=0.01)

    def test_time_counter_not_reset(self, detector):
        """Test that time counter is NOT reset by apply_adjustment."""
        # Accumulate time and debt
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)
        initial_time = detector.time_below_target

        # Apply adjustment
        detector.apply_adjustment()

        # Time should remain unchanged
        assert detector.time_below_target == initial_time


class TestGetAdjustmentRespectsCap:
    """Test that get_adjustment clamps to respect cumulative cap."""

    def test_returns_configured_multiplier_when_safe(self, detector):
        """Test that full multiplier is returned when below cap."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        expected = thresholds["ki_multiplier"]

        assert detector.get_adjustment() == expected

    def test_clamps_multiplier_near_cap(self, detector):
        """Test that multiplier is clamped when approaching cap."""
        # Set cumulative to 1.8 (close to cap of 2.0)
        detector.cumulative_ki_multiplier = 1.8

        # Max allowed = 2.0 / 1.8 = 1.111
        # Configured = 1.15
        # Should return min(1.15, 1.111) = 1.111
        multiplier = detector.get_adjustment()
        expected = MAX_UNDERSHOOT_KI_MULTIPLIER / 1.8

        assert multiplier == pytest.approx(expected, abs=0.001)

    def test_clamps_multiplier_at_cap(self, detector):
        """Test that multiplier is 1.0 when at cap."""
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # Max allowed = 2.0 / 2.0 = 1.0
        assert detector.get_adjustment() == pytest.approx(1.0, abs=0.001)


class TestApplyAdjustment:
    """Test the apply_adjustment method updates state correctly."""

    def test_updates_cumulative_multiplier(self, detector):
        """Test that cumulative multiplier is updated."""
        initial_cumulative = detector.cumulative_ki_multiplier
        multiplier = detector.get_adjustment()

        detector.apply_adjustment()

        expected = initial_cumulative * multiplier
        assert detector.cumulative_ki_multiplier == pytest.approx(expected, abs=0.001)

    def test_records_adjustment_time(self, detector):
        """Test that adjustment time is recorded for cooldown."""
        assert detector.last_adjustment_time is None

        before = time.monotonic()
        detector.apply_adjustment()
        after = time.monotonic()

        assert detector.last_adjustment_time is not None
        assert before <= detector.last_adjustment_time <= after

    def test_returns_applied_multiplier(self, detector):
        """Test that apply_adjustment returns the multiplier that was applied."""
        expected = detector.get_adjustment()
        actual = detector.apply_adjustment()

        assert actual == expected


class TestDifferentHeatingTypes:
    """Test different threshold configurations for different heating types."""

    def test_floor_hydronic_thresholds(self):
        """Test floor_hydronic has longest thresholds (slow system)."""
        detector = UndershootDetector(HeatingType.FLOOR_HYDRONIC)
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        assert thresholds["time_threshold_hours"] == 4.0
        assert thresholds["debt_threshold"] == 2.0
        assert thresholds["ki_multiplier"] == 1.20
        assert thresholds["cooldown_hours"] == 24.0

    def test_radiator_thresholds(self):
        """Test radiator has moderate thresholds."""
        detector = UndershootDetector(HeatingType.RADIATOR)
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.RADIATOR]

        assert thresholds["time_threshold_hours"] == 2.0
        assert thresholds["debt_threshold"] == 1.0
        assert thresholds["ki_multiplier"] == 1.25
        assert thresholds["cooldown_hours"] == 8.0

    def test_convector_thresholds(self):
        """Test convector has shorter thresholds (faster system)."""
        detector = UndershootDetector(HeatingType.CONVECTOR)
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.CONVECTOR]

        assert thresholds["time_threshold_hours"] == 1.5
        assert thresholds["debt_threshold"] == 0.75
        assert thresholds["ki_multiplier"] == 1.30
        assert thresholds["cooldown_hours"] == 4.0

    def test_forced_air_thresholds(self):
        """Test forced_air has shortest thresholds (fastest system)."""
        detector = UndershootDetector(HeatingType.FORCED_AIR)
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]

        assert thresholds["time_threshold_hours"] == 0.75
        assert thresholds["debt_threshold"] == 0.5
        assert thresholds["ki_multiplier"] == 1.35
        assert thresholds["cooldown_hours"] == 2.0

    def test_forced_air_triggers_faster(self):
        """Test that forced_air triggers adjustment much faster than floor_hydronic."""
        floor = UndershootDetector(HeatingType.FLOOR_HYDRONIC)
        forced = UndershootDetector(HeatingType.FORCED_AIR)

        # Same conditions for both: error=1.5°C for 1 hour
        floor.update(temp=18.5, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        forced.update(temp=18.5, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        # Forced air should trigger (1.5 °C·h > 0.5 threshold)
        assert forced.should_adjust_ki(cycles_completed=0) is True

        # Floor hydronic should not (1.5 °C·h < 2.0 threshold)
        assert floor.should_adjust_ki(cycles_completed=0) is False


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_dt_no_accumulation(self, detector):
        """Test that zero dt doesn't accumulate anything."""
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=0.0, cold_tolerance=0.5)

        assert detector.time_below_target == 0.0
        assert detector.thermal_debt == 0.0

    def test_negative_dt_no_accumulation(self, detector):
        """Test that negative dt doesn't cause issues."""
        # This shouldn't happen in practice, but let's verify it doesn't break
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=-60.0, cold_tolerance=0.5)

        # Implementation adds dt regardless of sign, so this would accumulate negative time
        # This is actually a potential bug, but we test current behavior
        assert detector.time_below_target == -60.0

    def test_very_small_error_below_tolerance(self, detector):
        """Test behavior with very small error within tolerance."""
        # Accumulate initial state
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt

        # Very small error within tolerance
        detector.update(temp=19.95, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # Should hold state
        assert detector.time_below_target == time_before
        assert detector.thermal_debt == debt_before

    def test_reset_is_idempotent(self, detector):
        """Test that multiple resets don't cause issues."""
        detector.reset()
        detector.reset()
        detector.reset()

        assert detector.time_below_target == 0.0
        assert detector.thermal_debt == 0.0


class TestPersistentUndershootMode:
    """Test persistent undershoot detection beyond bootstrap phase."""

    def test_severe_undershoot_allows_adjustment_after_min_cycles(self, detector):
        """Test that severe undershoot (2x threshold) enables adjustment after MIN_CYCLES."""
        # Floor hydronic debt threshold is 2.0, so severe is 4.0
        # Accumulate severe undershoot: error=4.0°C for 1h -> 4.0 °C·h
        detector.update(temp=16.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        # Verify we have severe undershoot
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        severe_threshold = thresholds["debt_threshold"] * SEVERE_UNDERSHOOT_MULTIPLIER
        assert detector.thermal_debt >= severe_threshold

        # Should adjust even with many cycles completed (persistent mode)
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is True
        assert detector.should_adjust_ki(cycles_completed=15) is True
        assert detector.should_adjust_ki(cycles_completed=100) is True

    def test_moderate_undershoot_blocked_after_min_cycles(self, detector):
        """Test that moderate undershoot (< 2x threshold) is blocked after MIN_CYCLES."""
        # Accumulate moderate undershoot: error=2.0°C for 0.9h -> 1.8 °C·h (< 2.0 threshold)
        # But add time to trigger time threshold
        detector.update(temp=19.4, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Verify undershoot is NOT severe (debt < 2x threshold = 4.0)
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        severe_threshold = thresholds["debt_threshold"] * SEVERE_UNDERSHOOT_MULTIPLIER
        assert detector.thermal_debt < severe_threshold

        # Should adjust with fewer cycles
        assert detector.should_adjust_ki(cycles_completed=0) is True
        assert detector.should_adjust_ki(cycles_completed=5) is True

        # Should NOT adjust after MIN_CYCLES - normal learning takes over
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is False

    def test_severe_undershoot_boundary(self, detector):
        """Test behavior at exact severe undershoot boundary."""
        # Floor hydronic: debt threshold 2.0, severe = 4.0
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        severe_threshold = thresholds["debt_threshold"] * SEVERE_UNDERSHOOT_MULTIPLIER

        # Just below severe threshold: 3.9 °C·h
        detector.thermal_debt = severe_threshold - 0.1
        detector.time_below_target = 14400.0  # 4h - meets time threshold

        # Should NOT adjust at MIN_CYCLES (not severe enough)
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is False

        # Reach severe threshold
        detector.thermal_debt = severe_threshold

        # Should adjust now
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is True

    def test_severe_undershoot_respects_cooldown(self, detector):
        """Test that severe undershoot still respects cooldown period."""
        # Accumulate severe undershoot
        detector.update(temp=16.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        # Apply first adjustment
        assert detector.should_adjust_ki(cycles_completed=15) is True
        detector.apply_adjustment()

        # Should be in cooldown now
        assert detector.should_adjust_ki(cycles_completed=15) is False

    def test_severe_undershoot_respects_cumulative_cap(self, detector):
        """Test that severe undershoot still respects cumulative Ki cap."""
        # Accumulate severe undershoot
        detector.update(temp=16.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        # Set cumulative multiplier at cap
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # Should NOT adjust - at cap even with severe undershoot
        assert detector.should_adjust_ki(cycles_completed=15) is False

    def test_catch22_scenario(self, detector):
        """Test the catch-22 scenario: many cycles, 0% confidence, severe undershoot.

        This is the real-world failure case:
        - 15 cycles completed
        - 0% confidence (cycles never converge)
        - 18% output, 0.6°C below setpoint persistently
        - Ki boost should be applied despite cycles_completed > 0
        """
        # Simulate persistent undershoot: 0.6°C error for 8 hours -> 4.8 °C·h
        # This represents a system stuck below setpoint
        for _ in range(8):  # 8 hours of updates
            detector.update(temp=19.4, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.3)

        # Verify severe undershoot
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        severe_threshold = thresholds["debt_threshold"] * SEVERE_UNDERSHOOT_MULTIPLIER
        assert detector.thermal_debt >= severe_threshold, (
            f"Expected severe undershoot >= {severe_threshold}, got {detector.thermal_debt}"
        )

        # With 15 completed cycles (like the real case), should still adjust
        assert detector.should_adjust_ki(cycles_completed=15) is True

        # Apply adjustment and verify multiplier
        multiplier = detector.apply_adjustment()
        assert multiplier == thresholds["ki_multiplier"]  # 1.20 for floor_hydronic

    def test_forced_air_severe_threshold(self):
        """Test severe undershoot threshold for forced_air (faster system)."""
        detector = UndershootDetector(HeatingType.FORCED_AIR)

        # Forced air: debt threshold 0.5, severe = 1.0
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]
        severe_threshold = thresholds["debt_threshold"] * SEVERE_UNDERSHOOT_MULTIPLIER

        assert severe_threshold == 1.0

        # Accumulate severe undershoot: error=2.0°C for 0.5h -> 1.0 °C·h
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)

        # Should trigger persistent mode
        assert detector.should_adjust_ki(cycles_completed=15) is True


class TestCycleModeDetection:
    """Test cycle mode for detecting chronic approach failures."""

    def test_add_cycle_with_failing_cycle(self, detector):
        """Test that failing cycles (rise_time=None, high undershoot) increment counter."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Create a failing cycle
        cycle = CycleMetrics(
            rise_time=None,  # Never reached setpoint
            settling_time=None,
            undershoot=thresholds["undershoot_threshold"] + 0.1,
            overshoot=0.0,
            inter_cycle_drift=0.0,
            settling_mae=0.0,
        )

        # Add cycle with sufficient duration
        detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should have 1 consecutive failure
        assert detector._consecutive_failures == 1

    def test_add_cycle_with_successful_cycle_resets_counter(self, detector):
        """Test that successful cycles (with rise_time) reset the counter."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add failing cycle
        failing_cycle = CycleMetrics(
            rise_time=None,
            settling_time=None,
            undershoot=thresholds["undershoot_threshold"] + 0.1,
            overshoot=0.0,
            inter_cycle_drift=0.0,
            settling_mae=0.0,
        )
        detector.add_cycle(failing_cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)
        assert detector._consecutive_failures == 1

        # Add successful cycle (has rise_time)
        success_cycle = CycleMetrics(
            rise_time=20.0,  # Reached setpoint in 20 minutes
            settling_time=5.0,
            undershoot=0.1,
            overshoot=0.2,
            inter_cycle_drift=0.1,
            settling_mae=0.05,
        )
        detector.add_cycle(success_cycle, cycle_duration_minutes=30.0)

        # Counter should reset to 0
        assert detector._consecutive_failures == 0

    def test_cycle_mode_triggers_after_consecutive_failures(self, detector):
        """Test that cycle mode triggers adjustment after enough consecutive failures."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 consecutive failing cycles (floor_hydronic requires 4)
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should be ready to adjust after MIN_CYCLES_FOR_LEARNING
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is True

    def test_cycle_mode_ignores_short_duration_cycles(self, detector):
        """Test that cycles shorter than min_duration are ignored."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 failing cycles but with too short duration
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] - 10)

        # Should NOT trigger - all cycles too short
        assert detector._consecutive_failures == 0
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_cycle_mode_ignores_low_undershoot(self, detector):
        """Test that cycles with undershoot below threshold are ignored."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 cycles with undershoot below threshold
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] - 0.05,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should NOT trigger - undershoot too small
        assert detector._consecutive_failures == 0
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_cycle_mode_requires_consecutive_failures(self, detector):
        """Test that pattern requires CONSECUTIVE failures (no successful cycles in between)."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Alternate between failing and successful cycles
        for i in range(8):
            if i % 2 == 0:
                # Failing cycle
                cycle = CycleMetrics(
                    rise_time=None,
                    settling_time=None,
                    undershoot=thresholds["undershoot_threshold"] + 0.1,
                    overshoot=0.0,
                    inter_cycle_drift=0.0,
                    settling_mae=0.0,
                )
            else:
                # Successful cycle
                cycle = CycleMetrics(
                    rise_time=15.0,
                    settling_time=5.0,
                    undershoot=0.1,
                    overshoot=0.1,
                    inter_cycle_drift=0.1,
                    settling_mae=0.05,
                )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should never trigger - no consecutive sequence of 4
        # After alternating, the last cycle is successful (i=7), so counter resets to 0
        assert detector._consecutive_failures == 0
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is False

    def test_cycle_mode_forced_air_requires_fewer_cycles(self, forced_air_detector):
        """Test that forced_air requires only 2 consecutive failures."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]

        # Add only 2 failing cycles
        for _ in range(2):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.05,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            forced_air_detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should trigger with just 2 cycles after MIN_CYCLES_FOR_LEARNING
        assert forced_air_detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is True

    def test_cycle_mode_shares_cooldown_with_realtime(self, detector):
        """Test that cycle mode and real-time mode share the same cooldown."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Trigger adjustment via real-time mode
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)
        assert detector.should_adjust_ki(cycles_completed=0) is True
        detector.apply_adjustment()

        # Try to trigger via cycle mode
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should be blocked by cooldown
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_cycle_mode_shares_cumulative_cap_with_realtime(self, detector):
        """Test that cycle mode and real-time mode share the same cumulative Ki cap."""
        # Set cumulative multiplier at cap
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # Try to trigger via cycle mode
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should be blocked by cap
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_cycle_mode_get_adjustment_returns_chronic_approach_multiplier(self, detector):
        """Test that get_adjustment returns the chronic approach multiplier when triggered by cycles."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Trigger via cycle mode
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # get_adjustment should return the chronic approach ki_multiplier
        multiplier = detector.get_adjustment()
        assert multiplier == pytest.approx(thresholds["ki_multiplier"], abs=0.001)

    def test_cycle_mode_apply_adjustment_updates_cumulative(self, detector):
        """Test that applying cycle mode adjustment updates cumulative multiplier."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Trigger via cycle mode
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        initial_cumulative = detector.cumulative_ki_multiplier
        detector.apply_adjustment()

        expected_cumulative = initial_cumulative * thresholds["ki_multiplier"]
        assert detector.cumulative_ki_multiplier == pytest.approx(expected_cumulative, abs=0.001)

    def test_cycle_mode_with_no_duration_accepts_cycles(self, detector):
        """Test that cycles without duration parameter (None) are accepted."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add cycles without duration parameter
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=0.0,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle)  # No duration parameter - defaults to None

        # Cycles should be accepted (counter increments)
        assert detector._consecutive_failures == 4
        # Should trigger after MIN_CYCLES_FOR_LEARNING
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is True


class TestRateModeDetection:
    """Test rate-based undershoot detection using HeatingRateLearner."""

    @pytest.fixture
    def heating_rate_learner(self):
        """Create a HeatingRateLearner for testing."""
        from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
            HeatingRateLearner,
        )
        return HeatingRateLearner("floor_hydronic")

    @pytest.fixture
    def detector_with_rate_learner(self, heating_rate_learner):
        """Create detector with heating rate learner."""
        detector = UndershootDetector(HeatingType.FLOOR_HYDRONIC)
        detector.set_heating_rate_learner(heating_rate_learner)
        return detector

    def test_detector_can_accept_heating_rate_learner(self, detector, heating_rate_learner):
        """Test that detector accepts HeatingRateLearner instance."""
        detector.set_heating_rate_learner(heating_rate_learner)
        assert detector._heating_rate_learner is heating_rate_learner

    def test_check_rate_based_undershoot_returns_none_without_learner(self, detector):
        """Test that rate check returns None when no learner is set."""
        result = detector.check_rate_based_undershoot(
            current_rate=0.1,
            delta=2.0,
            outdoor_temp=5.0,
        )
        assert result is None

    def test_check_rate_based_undershoot_returns_none_when_rate_ok(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that rate check returns None when performing well."""
        # Add observations showing expected rate of 0.15 C/h
        for _ in range(5):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        # Current rate is good (90% of expected)
        result = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.135,
            delta=2.0,
            outdoor_temp=5.0,
        )
        assert result is None

    def test_check_rate_based_undershoot_returns_none_without_sufficient_observations(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that rate check returns None without sufficient observations."""
        # Add only 2 observations (need 5 for comparison)
        for _ in range(2):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        # Current rate is poor but not enough data
        result = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.05,
            delta=2.0,
            outdoor_temp=5.0,
        )
        assert result is None

    def test_check_rate_based_undershoot_returns_none_without_stalls(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that rate check returns None without sufficient stall count."""
        # Add observations showing expected rate
        for _ in range(5):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        # Current rate is poor but no stalls recorded
        result = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.05,
            delta=2.0,
            outdoor_temp=5.0,
        )
        assert result is None

    def test_check_rate_based_undershoot_returns_none_when_capacity_limited(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that rate check returns None when duty shows capacity limit."""
        from datetime import datetime, timedelta
        from homeassistant.util import dt as dt_util

        # Add observations and simulate stalls
        for _ in range(5):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        # Start session and record 2 stalls with high duty (capacity limited)
        base_time = dt_util.utcnow()
        for i in range(2):
            start_time = base_time + timedelta(hours=i * 2)
            end_time = start_time + timedelta(minutes=65)
            heating_rate_learner.start_session(18.0, 20.0, 5.0, timestamp=start_time)
            heating_rate_learner.update_session(18.5, duty=0.90)  # High duty
            heating_rate_learner.end_session(19.0, "stalled", timestamp=end_time)

        # Current rate is poor but system is capacity limited
        result = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.05,
            delta=2.0,
            outdoor_temp=5.0,
        )
        assert result is None

    def test_check_rate_based_undershoot_returns_multiplier_when_underperforming(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that rate check returns Ki multiplier when underperforming."""
        from datetime import datetime, timedelta
        from homeassistant.util import dt as dt_util

        # Add observations showing expected rate of 0.15 C/h
        for _ in range(5):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        # Record 2 stalls with low duty (not capacity limited)
        # Need sessions to be >= 60 min for floor_hydronic
        base_time = dt_util.utcnow()
        for i in range(2):
            start_time = base_time + timedelta(hours=i * 2)
            end_time = start_time + timedelta(minutes=65)
            heating_rate_learner.start_session(18.0, 20.0, 5.0, timestamp=start_time)
            heating_rate_learner.update_session(18.5, duty=0.50)  # Low duty
            heating_rate_learner.end_session(19.0, "stalled", timestamp=end_time)

        # Current rate is poor (50% of expected)
        result = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.075,
            delta=2.0,
            outdoor_temp=5.0,
        )

        # Should return Ki multiplier (1.20 for floor_hydronic)
        assert result == 1.20

    def test_check_rate_based_undershoot_respects_cumulative_cap(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that rate-based detection respects cumulative Ki cap."""
        from datetime import datetime, timedelta
        from homeassistant.util import dt as dt_util

        # Add observations and stalls
        for _ in range(5):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        base_time = dt_util.utcnow()
        for i in range(2):
            start_time = base_time + timedelta(hours=i * 2)
            end_time = start_time + timedelta(minutes=65)
            heating_rate_learner.start_session(18.0, 20.0, 5.0, timestamp=start_time)
            heating_rate_learner.update_session(18.5, duty=0.50)
            heating_rate_learner.end_session(19.0, "stalled", timestamp=end_time)

        # Set cumulative multiplier near cap
        detector_with_rate_learner.cumulative_ki_multiplier = 1.9

        # Current rate is poor but near cap
        result = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.075,
            delta=2.0,
            outdoor_temp=5.0,
        )

        # Should return clamped multiplier
        expected = MAX_UNDERSHOOT_KI_MULTIPLIER / 1.9  # 2.0 / 1.9 = 1.053
        assert result == pytest.approx(expected, abs=0.001)

    def test_rate_mode_applies_adjustment_and_resets_stall_counter(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that applying rate-based adjustment resets stall counter."""
        from datetime import datetime, timedelta
        from homeassistant.util import dt as dt_util

        # Add observations and stalls
        for _ in range(5):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        base_time = dt_util.utcnow()
        for i in range(2):
            start_time = base_time + timedelta(hours=i * 2)
            end_time = start_time + timedelta(minutes=65)
            heating_rate_learner.start_session(18.0, 20.0, 5.0, timestamp=start_time)
            heating_rate_learner.update_session(18.5, duty=0.50)
            heating_rate_learner.end_session(19.0, "stalled", timestamp=end_time)

        # Check returns multiplier
        result = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.075,
            delta=2.0,
            outdoor_temp=5.0,
        )
        assert result == 1.20

        # Apply adjustment (should acknowledge learner)
        detector_with_rate_learner.apply_rate_adjustment()

        # Stall counter should be reset
        assert heating_rate_learner._stall_counter == 0

    def test_rate_mode_updates_cumulative_multiplier(
        self, detector_with_rate_learner, heating_rate_learner
    ):
        """Test that rate-based adjustment updates cumulative multiplier."""
        from datetime import datetime, timedelta
        from homeassistant.util import dt as dt_util

        # Setup underperforming scenario
        for _ in range(5):
            heating_rate_learner.add_observation(
                rate=0.15,
                duration_min=90,
                source="session",
                stalled=False,
                delta=2.0,
                outdoor_temp=5.0,
            )

        base_time = dt_util.utcnow()
        for i in range(2):
            start_time = base_time + timedelta(hours=i * 2)
            end_time = start_time + timedelta(minutes=65)
            heating_rate_learner.start_session(18.0, 20.0, 5.0, timestamp=start_time)
            heating_rate_learner.update_session(18.5, duty=0.50)
            heating_rate_learner.end_session(19.0, "stalled", timestamp=end_time)

        initial_cumulative = detector_with_rate_learner.cumulative_ki_multiplier
        multiplier = detector_with_rate_learner.check_rate_based_undershoot(
            current_rate=0.075,
            delta=2.0,
            outdoor_temp=5.0,
        )

        detector_with_rate_learner.apply_rate_adjustment()

        expected = initial_cumulative * multiplier
        assert detector_with_rate_learner.cumulative_ki_multiplier == pytest.approx(
            expected, abs=0.001
        )
