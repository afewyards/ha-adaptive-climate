"""Tests for UndershootDetector."""

from datetime import datetime, timedelta, timezone
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
    UNDERSHOOT_TBT_DECAY_TAU,
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
        """Test that time accumulates correctly across multiple updates.

        With exponential decay (H15), exact values are slightly less than naive sum
        because each update decays the accumulated total first.  The tau for
        floor_hydronic is 4 h (14400 s), so short dt values produce tiny losses —
        we use a 1 s absolute tolerance.
        """
        import math

        tau = UNDERSHOOT_TBT_DECAY_TAU[HeatingType.FLOOR_HYDRONIC]

        # First update: from 0, decay has no effect
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)
        assert detector.time_below_target == pytest.approx(60.0, abs=0.01)

        # Second update: 60 decays by exp(-30/tau) then 30 added
        expected_2 = 60.0 * math.exp(-30.0 / tau) + 30.0  # ≈ 89.9 s
        detector.update(temp=18.5, setpoint=20.0, dt_seconds=30.0, cold_tolerance=0.5)
        assert detector.time_below_target == pytest.approx(expected_2, abs=1.0)

        # Third update: expected_2 decays by exp(-120/tau) then 120 added
        expected_3 = expected_2 * math.exp(-120.0 / tau) + 120.0  # ≈ 209.1 s
        detector.update(temp=17.8, setpoint=20.0, dt_seconds=120.0, cold_tolerance=0.5)
        assert detector.time_below_target == pytest.approx(expected_3, abs=1.0)


class TestThermalDebtCalculation:
    """Test thermal debt calculation (integral of error over time)."""

    def test_calculates_debt_as_integral(self, detector):
        """Test that thermal debt is error * time in °C·hours."""
        # Error = 2.0°C, time = 3600 seconds (1 hour)
        # Debt = 2.0 * (3600 / 3600) = 2.0 °C·h
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        assert detector.thermal_debt == pytest.approx(2.0, abs=0.01)

    def test_accumulates_debt_across_updates(self, detector):
        """Test that thermal debt accumulates correctly across multiple updates.

        The previous debt decays by exp(-dt/tau) before the new error is added,
        so the total is less than a naive sum of individual contributions.
        """
        import math

        tau = UNDERSHOOT_TBT_DECAY_TAU[HeatingType.FLOOR_HYDRONIC]

        # First update from 0: decay(0)=0, + 2.0 * (1800/3600) = 1.0 °C·h
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(1.0, abs=0.01)

        # Second update: 1.0 decays by exp(-3600/14400), then 1.5 °C·h added
        expected = 1.0 * math.exp(-3600.0 / tau) + 1.5  # ≈ 2.279 °C·h
        detector.update(temp=18.5, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(expected, abs=0.01)

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
        detector.last_adjustment_time = datetime.now(timezone.utc)

        # Trigger reset
        detector.update(temp=20.5, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        assert detector.cumulative_ki_multiplier == 1.3
        assert detector.last_adjustment_time is not None


class TestWithinToleranceDecay:
    """Test that state decays (not holds) when within the tolerance band (H15).

    Previous behaviour: 0 <= error <= cold_tolerance → state unchanged.
    New behaviour (H15): exponential decay applied on every call; within-tolerance
    still does NOT accumulate new error but does allow stale debt to dissipate.
    """

    def test_within_tolerance_decays_time_and_debt(self, detector):
        """Verify that accumulated time/debt decays when temp is within tolerance."""
        # Accumulate some time and debt first (error > tolerance)
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt
        assert time_before > 0
        assert debt_before > 0

        # Within tolerance: error = 0.3°C < cold_tolerance=0.5°C
        detector.update(temp=19.7, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # State should have DECREASED (decayed), not stayed the same or grown
        assert detector.time_below_target < time_before
        assert detector.thermal_debt < debt_before
        # But not fully reset (that only happens above setpoint)
        assert detector.time_below_target > 0
        assert detector.thermal_debt > 0

    def test_at_exact_tolerance_boundary_decays(self, detector):
        """Verify decay at exact tolerance boundary (error == cold_tolerance)."""
        # Accumulate initial state
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt

        # Exactly at tolerance boundary: error = 0.5°C == cold_tolerance
        detector.update(temp=19.5, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # Boundary is inclusive (no accumulation), but decay still applies
        assert detector.time_below_target < time_before
        assert detector.thermal_debt < debt_before
        assert detector.time_below_target > 0

    def test_at_exact_setpoint_decays_not_resets(self, detector):
        """Verify that error == 0.0 decays (not full reset — that requires error < 0)."""
        # Accumulate initial state
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt

        # Exactly at setpoint: error = 0.0°C — within tolerance band, decay only
        detector.update(temp=20.0, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # Should decay (not hold, not full reset)
        assert detector.time_below_target < time_before
        assert detector.thermal_debt < debt_before
        assert detector.time_below_target > 0  # not fully reset by decay

    def test_sustained_tolerance_decays_to_near_zero(self, detector):
        """Long stay within tolerance should drain accumulated debt to near zero."""
        import math

        # Accumulate substantial debt
        detector.update(temp=16.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        initial_time = detector.time_below_target
        initial_debt = detector.thermal_debt

        # 3 tau worth of within-tolerance time (one large dt_seconds)
        tau = UNDERSHOOT_TBT_DECAY_TAU[HeatingType.FLOOR_HYDRONIC]
        long_dt = 3 * tau  # 3 × 4h = 12h
        detector.update(temp=19.8, setpoint=20.0, dt_seconds=long_dt, cold_tolerance=0.5)

        # After 3 tau, should be < 5% of original (exp(-3) ≈ 0.05)
        assert detector.time_below_target < initial_time * 0.06
        assert detector.thermal_debt < initial_debt * 0.06


class TestThermalDebtCap:
    """Test that thermal debt is capped at the heating-type-specific maximum (M16).

    Cap formula: 2 × SEVERE_UNDERSHOOT_MULTIPLIER × debt_threshold
    floor_hydronic: 2 × 2.0 × 2.0 = 8.0 °C·h  (was hard-coded 10.0)
    forced_air:     2 × 2.0 × 0.5 = 2.0 °C·h
    """

    def test_debt_caps_at_type_specific_maximum_floor(self, detector):
        """Test that thermal debt cannot exceed the floor_hydronic-specific cap (8.0 °C·h)."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        expected_cap = SEVERE_UNDERSHOOT_MULTIPLIER * 2.0 * thresholds["debt_threshold"]  # 8.0

        # Accumulate massive debt: error=5.0°C for 7200s (2h) -> would be 10.0 °C·h without cap
        detector.update(temp=15.0, setpoint=20.0, dt_seconds=7200.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(expected_cap, abs=0.01)

        # Try to accumulate more — should stay at cap
        detector.update(temp=15.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert detector.thermal_debt <= expected_cap + 0.01

    def test_debt_caps_at_type_specific_maximum_forced_air(self, forced_air_detector):
        """Test that forced_air cap is smaller than floor_hydronic cap."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]
        expected_cap = SEVERE_UNDERSHOOT_MULTIPLIER * 2.0 * thresholds["debt_threshold"]  # 2.0

        # Accumulate massive debt: error=5.0°C for 3600s -> would be 5.0 °C·h without cap
        forced_air_detector.update(temp=15.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert forced_air_detector.thermal_debt == pytest.approx(expected_cap, abs=0.01)

    def test_floor_cap_larger_than_forced_air_cap(self):
        """Verify that thermal mass scales the cap correctly."""
        floor_threshold = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]["debt_threshold"]
        fa_threshold = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]["debt_threshold"]
        floor_cap = SEVERE_UNDERSHOOT_MULTIPLIER * 2.0 * floor_threshold
        fa_cap = SEVERE_UNDERSHOOT_MULTIPLIER * 2.0 * fa_threshold
        assert floor_cap > fa_cap

    def test_debt_caps_across_multiple_updates(self, detector):
        """Test that type-specific cap is enforced across multiple updates."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        expected_cap = SEVERE_UNDERSHOOT_MULTIPLIER * 2.0 * thresholds["debt_threshold"]  # 8.0

        # First update: error=4.0°C for 7200s (2h) -> exactly at cap (8.0 °C·h)
        detector.update(temp=16.0, setpoint=20.0, dt_seconds=7200.0, cold_tolerance=0.5)
        assert detector.thermal_debt == pytest.approx(expected_cap, abs=0.01)

        # Second update: would add more but cap enforced (and decay first)
        detector.update(temp=17.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        assert detector.thermal_debt <= expected_cap + 0.01


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

    @patch("custom_components.adaptive_climate.adaptive.undershoot_detector.dt_util.utcnow")
    def test_can_adjust_after_cooldown_expires(self, mock_utcnow, detector):
        """Test that adjustment is allowed after cooldown expires."""
        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Trigger adjustment at base_time
        mock_utcnow.return_value = base_time
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)
        detector.apply_adjustment()

        # Accumulate conditions again
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Still in cooldown (24h for floor_hydronic)
        mock_utcnow.return_value = base_time + timedelta(hours=23)
        assert detector.should_adjust_ki(cycles_completed=0) is False

        # After cooldown expires
        mock_utcnow.return_value = base_time + timedelta(hours=25)
        assert detector.should_adjust_ki(cycles_completed=0) is True


class TestCumulativeKiCap:
    """Test cumulative Ki multiplier cap (fallback path without physics baseline)."""

    def test_respects_cumulative_cap(self, detector):
        """Test that cumulative multiplier cannot exceed MAX_UNDERSHOOT_KI_MULTIPLIER."""
        detector.cumulative_ki_multiplier = 2.8

        # Trigger adjustment conditions
        # Use small error (0.6) to accumulate time but not debt
        detector.update(temp=18.9, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)

        # Should not adjust - cumulative multiplier approaching cap (2.8 * 1.15 = 3.22 > 3.0)
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_blocks_adjustment_at_cap(self, detector):
        """Test that adjustment is blocked when at cap."""
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # Trigger adjustment conditions
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Should not adjust - at cap
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_noop_gate_blocks_near_cap(self, detector):
        """H14: effective multiplier ≤ 1.001 → should_adjust_ki returns False (no-op gate).

        When cumulative is just below 3.0 (floating-point), get_adjustment() returns
        a value barely above 1.0.  Applying that would record cooldown and reset state
        with zero actual Ki benefit.  The gate prevents this.
        """
        # Set cumulative to just under cap so effective multiplier ≈ 1.0
        # cumulative = 2.9999 → max_allowed = 3.0 / 2.9999 ≈ 1.0000333
        detector.cumulative_ki_multiplier = 2.9999

        # Build up enough undershoot to otherwise trigger
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # H14 gate: effective_multiplier ≈ 1.0003 ≤ 1.001 → blocked
        assert detector.should_adjust_ki(cycles_completed=0) is False

    def test_negative_cumulative_self_heals_on_apply(self, detector):
        """C08: a corrupt persisted negative cumulative is self-healed by apply_adjustment.

        Without the clamp, cumulative * ki_multiplier could produce a negative
        cumulative (if max_allowed was negative) and permanently break the >= cap gate.
        The H14 gate blocks *should_adjust_ki* when cumulative is negative, but
        apply_adjustment can still be called directly (e.g. from tests or legacy code),
        so it must self-heal regardless.
        """
        # Simulate a corrupt restore (negative cumulative)
        detector.cumulative_ki_multiplier = -0.5

        # Call apply_adjustment directly (bypassing should_adjust_ki gate)
        # C08 clamp: multiplier = max(1.0, get_adjustment()) — avoids negative product
        detector.apply_adjustment()

        # Cumulative must be ≥ 1.0 after apply (self-healed)
        assert detector.cumulative_ki_multiplier >= 1.0
        # And must not exceed cap
        assert detector.cumulative_ki_multiplier <= MAX_UNDERSHOOT_KI_MULTIPLIER

    def test_overshooting_cumulative_clamped_on_apply(self, detector):
        """C08: cumulative is clamped to MAX_UNDERSHOOT_KI_MULTIPLIER even when product overshoots."""
        # Set cumulative slightly below cap
        detector.cumulative_ki_multiplier = 2.9

        # Build undershoot — effective multiplier = min(1.20, 3.0/2.9) ≈ 1.034
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)
        # Note: H14 gate passes because 1.034 > 1.001

        detector.apply_adjustment()

        # Result clamped at MAX regardless of product
        assert detector.cumulative_ki_multiplier <= MAX_UNDERSHOOT_KI_MULTIPLIER


class TestPhysicsBasedKiCap:
    """Test physics-based Ki cap (actual Ki ratio vs physics baseline)."""

    def test_physics_cap_blocks_when_ratio_at_max(self, detector):
        """Test that physics-based cap blocks adjustment when Ki is 3x physics baseline."""
        # Accumulate undershoot conditions
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # physics_baseline_ki=0.01, current_ki=0.03 -> ratio=3.0 >= MAX (3.0)
        assert (
            detector.should_adjust_ki(
                cycles_completed=0,
                current_ki=0.03,
                physics_baseline_ki=0.01,
            )
            is False
        )

    def test_physics_cap_allows_when_ratio_below_max(self, detector):
        """Test that physics-based cap allows adjustment when Ki is below 3x physics baseline."""
        # Accumulate undershoot conditions
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # physics_baseline_ki=0.01, current_ki=0.02 -> ratio=2.0 < MAX (3.0)
        assert (
            detector.should_adjust_ki(
                cycles_completed=0,
                current_ki=0.02,
                physics_baseline_ki=0.01,
            )
            is True
        )

    def test_physics_cap_ignores_stale_cumulative_multiplier(self, detector):
        """Test that physics cap works correctly even with wrong cumulative_ki_multiplier."""
        # Accumulate undershoot conditions
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Simulate the bug: cumulative_ki_multiplier reset to 1.0 on restart,
        # but actual Ki is 4.5x the physics baseline (should be blocked)
        detector.cumulative_ki_multiplier = 1.0  # Wrong value (e.g. after restart)

        # Without physics params: would wrongly allow boost (cumulative=1.0 < 3.0)
        assert detector.should_adjust_ki(cycles_completed=0) is True  # Fallback path allows it

        # With physics params: correctly blocks the boost (4.5x >= 3.0 cap)
        assert (
            detector.should_adjust_ki(
                cycles_completed=0,
                current_ki=0.045,  # 4.5x actual
                physics_baseline_ki=0.01,
            )
            is False
        )

    def test_get_adjustment_uses_physics_cap(self, detector):
        """Test that get_adjustment uses physics-based cap when params are provided."""
        # current_ki=0.028, baseline=0.01 -> actual_ratio=2.8
        # max_allowed = 3.0 / 2.8 = 1.071
        # configured multiplier = 1.20 (floor_hydronic)
        # result = min(1.20, 1.071) = 1.071
        result = detector.get_adjustment(current_ki=0.028, physics_baseline_ki=0.01)
        expected = MAX_UNDERSHOOT_KI_MULTIPLIER / 2.8
        assert result == pytest.approx(expected, abs=0.001)

    def test_get_adjustment_full_multiplier_when_below_cap(self, detector):
        """Test that full multiplier is returned when ratio is well below cap."""
        # current_ki=0.01, baseline=0.01 -> ratio=1.0 (at baseline, cap not reached)
        # max_allowed = 3.0 / 1.0 = 3.0
        # result = min(1.20, 3.0) = 1.20
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        result = detector.get_adjustment(current_ki=0.01, physics_baseline_ki=0.01)
        assert result == pytest.approx(thresholds["ki_multiplier"], abs=0.001)

    def test_apply_adjustment_uses_physics_cap(self, detector):
        """Test that apply_adjustment uses physics-based cap and returns clamped multiplier."""
        # current_ki=0.028, baseline=0.01 -> ratio=2.8
        # clamped multiplier = min(1.20, 3.0/2.8) = 1.071
        expected_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER / 2.8
        result = detector.apply_adjustment(current_ki=0.028, physics_baseline_ki=0.01)
        assert result == pytest.approx(expected_multiplier, abs=0.001)

    def test_physics_cap_with_zero_baseline_falls_back_to_cumulative(self, detector):
        """Test that zero physics_baseline_ki falls back to cumulative tracking."""
        # Accumulate undershoot conditions
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=14400.0, cold_tolerance=0.5)

        # Zero baseline should use cumulative fallback (cumulative=1.0 < 3.0 -> allows)
        assert (
            detector.should_adjust_ki(
                cycles_completed=0,
                current_ki=0.05,
                physics_baseline_ki=0.0,  # Zero baseline -> fallback
            )
            is True
        )


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
        """Test that adjustment triggers when time or debt threshold is exceeded.

        With exponential decay (H15), the two-step "just below then just over" approach
        no longer works because the second step's decay reduces the running total.
        Instead we use two large steps that cleanly stay below then exceed the thresholds.
        """
        temp = 20.0 - 0.51  # error = 0.51°C (just above cold_tolerance=0.5)

        # First large step: 10000s below target
        # time=10000 < 14400 threshold; debt=0.51*(10000/3600)≈1.42 < 2.0 threshold → False
        detector.update(temp=temp, setpoint=20.0, dt_seconds=10000.0, cold_tolerance=0.5)
        assert detector.should_adjust_ki(cycles_completed=0) is False

        # Second large step: another 10000s → accumulated time and debt exceed thresholds
        # time ≈ 10000*exp(-10000/14400)+10000 ≈ 14997 > 14400 threshold
        # debt ≈ 1.42*exp(-10000/14400)+1.42 ≈ 2.13 > 2.0 threshold
        detector.update(temp=temp, setpoint=20.0, dt_seconds=10000.0, cold_tolerance=0.5)
        assert detector.should_adjust_ki(cycles_completed=0) is True

    def test_forced_air_has_shorter_threshold(self, forced_air_detector):
        """Test that forced_air has a shorter time threshold."""
        # Forced air threshold: 0.75 hours = 2700 seconds
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FORCED_AIR]
        time_threshold = thresholds["time_threshold_hours"] * 3600.0

        assert time_threshold == 2700.0

        # Should trigger at 2700s
        forced_air_detector.update(temp=18.0, setpoint=20.0, dt_seconds=2700.0, cold_tolerance=0.5)
        assert forced_air_detector.should_adjust_ki(cycles_completed=0) is True


class TestShouldAdjustDebtThreshold:
    """Test adjustment trigger based on thermal debt threshold."""

    def test_triggers_when_debt_threshold_exceeded(self, detector):
        """Test that adjustment triggers when debt threshold is exceeded."""
        # Floor hydronic debt threshold: 2.0 °C·h
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
        forced_air_detector.update(temp=19.0, setpoint=20.0, dt_seconds=1800.0, cold_tolerance=0.5)
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
        # Set cumulative to 2.7 (close to cap of 3.0)
        detector.cumulative_ki_multiplier = 2.7

        # Max allowed = 3.0 / 2.7 = 1.111
        # Configured = 1.15
        # Should return min(1.15, 1.111) = 1.111
        multiplier = detector.get_adjustment()
        expected = MAX_UNDERSHOOT_KI_MULTIPLIER / 2.7

        assert multiplier == pytest.approx(expected, abs=0.001)

    def test_clamps_multiplier_at_cap(self, detector):
        """Test that multiplier is 1.0 when at cap."""
        detector.cumulative_ki_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER

        # Max allowed = 3.0 / 3.0 = 1.0
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
        """Test that adjustment time is recorded as wall-clock datetime for cooldown."""
        assert detector.last_adjustment_time is None

        before = datetime.now(timezone.utc)
        detector.apply_adjustment()
        after = datetime.now(timezone.utc)

        assert detector.last_adjustment_time is not None
        assert isinstance(detector.last_adjustment_time, datetime)
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
        _detector = UndershootDetector(HeatingType.FLOOR_HYDRONIC)  # Verify instantiation
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        assert thresholds["time_threshold_hours"] == 4.0
        assert thresholds["debt_threshold"] == 2.0
        assert thresholds["ki_multiplier"] == 1.20
        assert thresholds["cooldown_hours"] == 24.0

    def test_radiator_thresholds(self):
        """Test radiator has moderate thresholds."""
        _detector = UndershootDetector(HeatingType.RADIATOR)  # Verify instantiation
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.RADIATOR]

        assert thresholds["time_threshold_hours"] == 2.0
        assert thresholds["debt_threshold"] == 1.0
        assert thresholds["ki_multiplier"] == 1.25
        assert thresholds["cooldown_hours"] == 8.0

    def test_convector_thresholds(self):
        """Test convector has shorter thresholds (faster system)."""
        _detector = UndershootDetector(HeatingType.CONVECTOR)  # Verify instantiation
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.CONVECTOR]

        assert thresholds["time_threshold_hours"] == 1.5
        assert thresholds["debt_threshold"] == 0.75
        assert thresholds["ki_multiplier"] == 1.30
        assert thresholds["cooldown_hours"] == 4.0

    def test_forced_air_thresholds(self):
        """Test forced_air has shortest thresholds (fastest system)."""
        _detector = UndershootDetector(HeatingType.FORCED_AIR)  # Verify instantiation
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
        """Test behavior with very small error within tolerance (H15: decay applies, not hold)."""
        # Accumulate initial state
        detector.update(temp=18.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.5)
        time_before = detector.time_below_target
        debt_before = detector.thermal_debt

        # Very small error within tolerance (error=0.05°C < cold_tolerance=0.5°C)
        detector.update(temp=19.95, setpoint=20.0, dt_seconds=60.0, cold_tolerance=0.5)

        # H15: decay applies on every call, so values should DECREASE, not hold
        assert detector.time_below_target < time_before
        assert detector.thermal_debt < debt_before
        # But not fully reset (that only happens when temp > setpoint)
        assert detector.time_below_target > 0
        assert detector.thermal_debt > 0

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
        """Test the catch-22 scenario: many cycles, persistent severe undershoot.

        This is the real-world failure case:
        - 15 cycles completed (realtime normally yields to cycle mode)
        - Severe undershoot (debt >= 2× threshold) overrides the cycle-count gate
        - Ki boost should be applied despite cycles_completed >= MIN_CYCLES_FOR_LEARNING

        With H15 decay, reaching the severe threshold (4.0 °C·h for floor_hydronic) with
        hourly updates requires a significant temperature error.  A 3°C deficit (e.g. a
        cold room that never warms) converges to ~4.75 °C·h in the absence of the cap.
        """
        # 3°C deficit (temp=17°C, setpoint=20°C) for 8 hourly updates
        # At dt=3600, tau=14400: debt_ss = 3.0 / (1 - exp(-1/4)) ≈ 3.0 / 0.632 ≈ 4.75 °C·h
        for _ in range(8):
            detector.update(temp=17.0, setpoint=20.0, dt_seconds=3600.0, cold_tolerance=0.3)

        # Verify severe undershoot threshold is reached (>= 2× debt_threshold = 4.0 °C·h)
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]
        severe_threshold = thresholds["debt_threshold"] * SEVERE_UNDERSHOOT_MULTIPLIER
        assert detector.thermal_debt >= severe_threshold, (
            f"Expected severe undershoot >= {severe_threshold}, got {detector.thermal_debt}"
        )

        # With 15 completed cycles + severe undershoot → realtime gate is bypassed → True
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
            overshoot=None,
            inter_cycle_drift=0.0,
            settling_mae=0.0,
        )

        # Add cycle with sufficient duration
        detector.add_cycle(cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)

        # Should have 1 consecutive failure
        assert detector._consecutive_failures == 1

    def test_add_cycle_with_clean_successful_cycle_resets_counter(self, detector):
        """Test that clean successful cycles (rise_time set, undershoot below threshold) reset counter."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add failing cycle
        failing_cycle = CycleMetrics(
            rise_time=None,
            settling_time=None,
            undershoot=thresholds["undershoot_threshold"] + 0.1,
            overshoot=None,
            inter_cycle_drift=0.0,
            settling_mae=0.0,
        )
        detector.add_cycle(failing_cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)
        assert detector._consecutive_failures == 1

        # Add clean successful cycle (has rise_time AND low undershoot)
        success_cycle = CycleMetrics(
            rise_time=20.0,  # Reached setpoint in 20 minutes
            settling_time=5.0,
            undershoot=0.1,  # Well below threshold (0.4 for floor_hydronic)
            overshoot=0.2,
            inter_cycle_drift=0.1,
            settling_mae=0.05,
        )
        detector.add_cycle(success_cycle, cycle_duration_minutes=30.0)

        # Counter should reset to 0 (clean success)
        assert detector._consecutive_failures == 0

    def test_add_cycle_barely_reaching_setpoint_does_not_reset(self, detector):
        """Test M15: a cycle that barely crosses setpoint but has large undershoot does NOT reset.

        The system reached the setpoint momentarily but still has a big thermal gap,
        which still indicates chronic heating weakness.
        """
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Establish some consecutive failures
        for _ in range(2):
            failing_cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=None,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(failing_cycle, cycle_duration_minutes=thresholds["min_cycle_duration"] + 5)
        assert detector._consecutive_failures == 2

        # Cycle that *did* reach setpoint (rise_time set) but has significant undershoot
        barely_success = CycleMetrics(
            rise_time=55.0,  # Technically reached setpoint
            settling_time=None,
            undershoot=thresholds["undershoot_threshold"] + 0.05,  # Above threshold!
            overshoot=0.0,
            inter_cycle_drift=0.0,
            settling_mae=0.0,
        )
        detector.add_cycle(barely_success, cycle_duration_minutes=60.0)

        # Counter should NOT reset — undershoot still above threshold despite rise_time set
        assert detector._consecutive_failures == 2

    def test_cycle_mode_triggers_after_consecutive_failures(self, detector):
        """Test that cycle mode triggers adjustment after enough consecutive failures."""
        thresholds = UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]

        # Add 4 consecutive failing cycles (floor_hydronic requires 4)
        for _ in range(4):
            cycle = CycleMetrics(
                rise_time=None,
                settling_time=None,
                undershoot=thresholds["undershoot_threshold"] + 0.1,
                overshoot=None,
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
                overshoot=None,
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
                overshoot=None,
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
                    overshoot=None,
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
                overshoot=None,
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
                overshoot=None,
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
                overshoot=None,
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
                overshoot=None,
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
                overshoot=None,
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
                overshoot=None,
                inter_cycle_drift=0.0,
                settling_mae=0.0,
            )
            detector.add_cycle(cycle)  # No duration parameter - defaults to None

        # Cycles should be accepted (counter increments)
        assert detector._consecutive_failures == 4
        # Should trigger after MIN_CYCLES_FOR_LEARNING
        assert detector.should_adjust_ki(cycles_completed=MIN_CYCLES_FOR_LEARNING) is True
