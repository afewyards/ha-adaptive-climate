"""Tests for cycle analysis functions."""

from datetime import datetime, timedelta

import pytest

from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
    calculate_settling_time,
    CycleMetrics,
)


class TestCalculateSettlingTime:
    """Test calculate_settling_time function."""

    def test_settling_time_with_reference_time(self):
        """Test settling time calculated from reference_time not first sample.

        When device_off_time is passed as reference_time, settling time should be
        calculated from device_off_time, not from the first temperature sample.
        This is important because temperature history may include heating phase,
        but settling time should only measure time after heating stops.
        """
        target_temp = 21.0
        tolerance = 0.2

        # Simulate heating cycle with temperature history starting during heating
        base_time = datetime(2024, 1, 1, 10, 0)
        device_off_time = base_time + timedelta(minutes=20)  # Heater stops at 20 min

        temperature_history = [
            (base_time, 19.0),  # Start of history (during heating)
            (base_time + timedelta(minutes=5), 19.5),
            (base_time + timedelta(minutes=10), 20.0),
            (base_time + timedelta(minutes=15), 20.5),
            (base_time + timedelta(minutes=20), 21.0),  # Device turns off here
            (base_time + timedelta(minutes=25), 21.3),  # Overshoot (outside tolerance)
            (base_time + timedelta(minutes=30), 21.25),  # Still overshooting (outside tolerance)
            (base_time + timedelta(minutes=35), 21.1),  # Settles within tolerance
            (base_time + timedelta(minutes=40), 21.0),
            (base_time + timedelta(minutes=45), 21.0),
        ]

        # Calculate settling time from device_off_time
        settling_time = calculate_settling_time(
            temperature_history,
            target_temp,
            tolerance=tolerance,
            reference_time=device_off_time
        )

        # Settling time should be from device_off_time (20 min) to settle point (35 min)
        # That's 15 minutes, NOT from start of history (which would be 35 minutes)
        assert settling_time is not None
        assert settling_time == pytest.approx(15.0, abs=0.1)

    def test_settling_time_without_reference_time(self):
        """Test backward compatibility when reference_time=None (uses first sample).

        When reference_time is not provided, settling time should be calculated
        from the first sample in temperature_history (legacy behavior).
        """
        target_temp = 21.0
        tolerance = 0.2

        base_time = datetime(2024, 1, 1, 10, 0)

        temperature_history = [
            (base_time, 19.0),  # Start - reference point when reference_time=None
            (base_time + timedelta(minutes=5), 19.5),
            (base_time + timedelta(minutes=10), 20.0),
            (base_time + timedelta(minutes=15), 20.5),
            (base_time + timedelta(minutes=20), 21.0),
            (base_time + timedelta(minutes=25), 21.3),  # Overshoot
            (base_time + timedelta(minutes=30), 21.25),  # Still overshooting
            (base_time + timedelta(minutes=35), 21.1),  # Settles within tolerance
            (base_time + timedelta(minutes=40), 21.0),
            (base_time + timedelta(minutes=45), 21.0),
        ]

        # Calculate settling time without reference_time (legacy behavior)
        settling_time = calculate_settling_time(
            temperature_history,
            target_temp,
            tolerance=tolerance,
            reference_time=None
        )

        # Settling time should be from first sample (0 min) to settle point (35 min)
        assert settling_time is not None
        assert settling_time == pytest.approx(35.0, abs=0.1)

    def test_settling_time_reference_before_history_start(self):
        """Test reference_time before first sample uses first sample timestamp."""
        target_temp = 21.0
        tolerance = 0.2

        base_time = datetime(2024, 1, 1, 10, 0)
        # Reference time is 10 minutes BEFORE first sample
        reference_time = base_time - timedelta(minutes=10)

        temperature_history = [
            (base_time, 20.5),  # First sample (outside tolerance)
            (base_time + timedelta(minutes=5), 20.7),  # Still outside
            (base_time + timedelta(minutes=10), 20.85),  # Settles
            (base_time + timedelta(minutes=15), 21.0),
        ]

        settling_time = calculate_settling_time(
            temperature_history,
            target_temp,
            tolerance=tolerance,
            reference_time=reference_time
        )

        # Should calculate from first sample (base_time), not reference_time
        # Settling at 10 minutes from base_time
        assert settling_time is not None
        assert settling_time == pytest.approx(10.0, abs=0.1)

    def test_settling_time_reference_after_settling(self):
        """Test reference_time after temperature already settled returns 0 or small value."""
        target_temp = 21.0
        tolerance = 0.2

        base_time = datetime(2024, 1, 1, 10, 0)

        temperature_history = [
            (base_time, 20.8),
            (base_time + timedelta(minutes=5), 21.0),  # Settles at 5 min
            (base_time + timedelta(minutes=10), 21.0),
            (base_time + timedelta(minutes=15), 21.1),
            (base_time + timedelta(minutes=20), 21.0),
        ]

        # Reference time is at 10 minutes, but temp settled at 5 minutes
        reference_time = base_time + timedelta(minutes=10)

        settling_time = calculate_settling_time(
            temperature_history,
            target_temp,
            tolerance=tolerance,
            reference_time=reference_time
        )

        # Temperature was already settled at reference_time, so settling_time
        # should be 0 or the time to the first point after reference that confirms settling
        assert settling_time is not None
        # The first sample at/after reference_time that stays settled is at 10 min
        # and it stays settled through 15 and 20 min, so settling is immediate (0)
        assert settling_time == pytest.approx(0.0, abs=0.1)

    def test_settling_time_never_settles_with_reference_time(self):
        """Test that None is returned when temperature never settles after reference_time."""
        target_temp = 21.0
        tolerance = 0.2

        base_time = datetime(2024, 1, 1, 10, 0)
        reference_time = base_time + timedelta(minutes=10)

        temperature_history = [
            (base_time, 19.0),
            (base_time + timedelta(minutes=5), 19.5),
            (base_time + timedelta(minutes=10), 20.0),  # Reference time here
            (base_time + timedelta(minutes=15), 20.5),
            (base_time + timedelta(minutes=20), 20.7),  # Never reaches target
        ]

        settling_time = calculate_settling_time(
            temperature_history,
            target_temp,
            tolerance=tolerance,
            reference_time=reference_time
        )

        assert settling_time is None

    def test_settling_time_reference_in_middle_of_history(self):
        """Test reference_time in middle of temperature history."""
        target_temp = 21.0
        tolerance = 0.2

        base_time = datetime(2024, 1, 1, 10, 0)
        reference_time = base_time + timedelta(minutes=15)

        temperature_history = [
            (base_time, 19.0),  # Pre-reference samples
            (base_time + timedelta(minutes=5), 19.5),
            (base_time + timedelta(minutes=10), 20.0),
            (base_time + timedelta(minutes=15), 20.5),  # Reference time here
            (base_time + timedelta(minutes=20), 21.0),
            (base_time + timedelta(minutes=25), 21.3),  # Overshoot (outside tolerance)
            (base_time + timedelta(minutes=30), 21.1),  # Settles
            (base_time + timedelta(minutes=35), 21.0),
            (base_time + timedelta(minutes=40), 21.0),
        ]

        settling_time = calculate_settling_time(
            temperature_history,
            target_temp,
            tolerance=tolerance,
            reference_time=reference_time
        )

        # Should measure from reference_time (15 min) to settling (30 min) = 15 min
        assert settling_time is not None
        assert settling_time == pytest.approx(15.0, abs=0.1)


class TestCycleMetrics:
    """Test CycleMetrics dataclass."""

    def test_cycle_metrics_decay_fields(self):
        """Test CycleMetrics stores decay-related fields correctly.

        CycleMetrics should accept and store three new optional decay fields:
        - integral_at_tolerance_entry: Integral value when temp enters tolerance band
        - integral_at_setpoint_cross: Integral value when temp crosses setpoint
        - decay_contribution: Calculated integral contribution from decay period
        """
        # Create CycleMetrics with decay fields
        metrics = CycleMetrics(
            overshoot=0.5,
            undershoot=0.2,
            settling_time=15.0,
            oscillations=2,
            rise_time=10.0,
            integral_at_tolerance_entry=1.5,
            integral_at_setpoint_cross=2.3,
            decay_contribution=0.8,
        )

        # Verify all decay fields are stored correctly
        assert metrics.integral_at_tolerance_entry == 1.5
        assert metrics.integral_at_setpoint_cross == 2.3
        assert metrics.decay_contribution == 0.8

        # Verify other fields still work
        assert metrics.overshoot == 0.5
        assert metrics.undershoot == 0.2
        assert metrics.settling_time == 15.0
        assert metrics.oscillations == 2
        assert metrics.rise_time == 10.0

    def test_cycle_metrics_decay_fields_optional(self):
        """Test that decay fields are optional and default to None."""
        # Create CycleMetrics without decay fields
        metrics = CycleMetrics(
            overshoot=0.3,
            settling_time=12.0,
        )

        # Verify decay fields default to None
        assert metrics.integral_at_tolerance_entry is None
        assert metrics.integral_at_setpoint_cross is None
        assert metrics.decay_contribution is None

        # Verify other fields still work
        assert metrics.overshoot == 0.3
        assert metrics.settling_time == 12.0

    def test_cycle_metrics_decay_fields_partial(self):
        """Test that decay fields can be set individually."""
        # Set only some decay fields
        metrics = CycleMetrics(
            overshoot=0.4,
            integral_at_tolerance_entry=1.2,
            decay_contribution=0.5,
        )

        # Verify specified fields are set
        assert metrics.integral_at_tolerance_entry == 1.2
        assert metrics.decay_contribution == 0.5

        # Verify unspecified decay field defaults to None
        assert metrics.integral_at_setpoint_cross is None

        # Verify other fields work
        assert metrics.overshoot == 0.4
