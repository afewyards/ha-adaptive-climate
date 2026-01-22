"""Tests for PID controller."""
import pytest
import sys
from pathlib import Path

# Add parent directory to path to import pid_controller
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat"))

from pid_controller import PID


class TestPIDController:
    """Test PID controller output calculation."""

    def test_pid_output_calculation(self):
        """Test basic PID output calculation."""
        # Create PID controller with known parameters
        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100)

        # Set initial conditions
        input_val = 20.0
        set_point = 22.0
        input_time = 0.0

        # Calculate output
        output, changed = pid.calc(input_val, set_point, input_time=input_time, last_input_time=None)

        # Should produce output since error exists (setpoint - input = 2.0)
        assert changed is True
        assert 0 <= output <= 100  # Within bounds

        # Check error is correct
        assert pid.error == 2.0

        # Check proportional term (P-on-M: 0 on first call since no previous measurement)
        assert pid.proportional == 0.0

    def test_pid_output_limits(self):
        """Test PID output respects min/max limits."""
        # Create PID with tight limits
        pid = PID(kp=100, ki=10, kd=5, out_min=0, out_max=50)

        # Large error should clamp to max
        output, _ = pid.calc(input_val=10.0, set_point=30.0, input_time=0.0)
        assert output <= 50.0
        assert output >= 0.0

        # Create PID with negative scenario
        pid2 = PID(kp=100, ki=10, kd=5, out_min=-50, out_max=100)

        # Negative error should produce appropriate output
        output2, _ = pid2.calc(input_val=30.0, set_point=10.0, input_time=0.0)
        assert output2 >= -50.0
        assert output2 <= 100.0

    def test_integral_windup_prevention(self):
        """Test integral windup prevention with directional saturation.

        Tests that the back-calculation anti-windup:
        1. Blocks integration when error drives further saturation (same direction)
        2. Allows integration when error opposes saturation (wind-down)
        """
        # Create PID with low Kp so integral must build up to saturate
        pid = PID(kp=2, ki=10, kd=0, out_min=0, out_max=100)

        # First call establishes baseline
        pid.calc(input_val=10.0, set_point=20.0, input_time=0.0, last_input_time=None)

        # Build up integral over time to saturate output at 100%
        # With P-on-M and constant input, P=0, so output = integral only
        # error = 10, Ki = 10, dt = 100s = 100/3600 hours
        # Each iteration adds approximately 2.778 to integral
        # Need ~37 iterations to reach 100%
        for i in range(1, 40):
            last_time = 100.0 * (i - 1)
            current_time = 100.0 * i
            output, _ = pid.calc(input_val=10.0, set_point=20.0, input_time=current_time, last_input_time=last_time)

        # Should now be saturated at 100%
        assert output == 100.0
        assert pid.integral >= 100.0  # Clamped at 100%

        # Test case 1: error in same direction as saturation (positive error, saturated high)
        # Integral should NOT increase (anti-windup blocks further windup)
        integral_before_same_direction = pid.integral
        output2, _ = pid.calc(input_val=10.0, set_point=20.0, input_time=4000.0, last_input_time=3900.0)

        # Output still saturated
        assert output2 == 100.0

        # Integral should not have increased (saturated_high = True blocks integration)
        assert pid.integral == integral_before_same_direction

        # Test case 2: error opposes saturation (negative error, saturated high)
        # This simulates overshoot - integral SHOULD decrease (wind-down allowed)
        integral_before_opposite = pid.integral
        # Create negative error: input > setpoint
        output3, _ = pid.calc(input_val=22.0, set_point=20.0, input_time=4100.0, last_input_time=4000.0)

        # Output may drop below 100% as integral winds down and P term responds to measurement change
        # With P-on-M: P = -2 * (22 - 10) = -24, and integral decreases
        assert output3 < 100.0

        # Integral should decrease when error opposes saturation
        # (saturated_high = False because error < 0, so integration proceeds with negative error)
        assert pid.integral < integral_before_opposite

    def test_pid_mode_off(self):
        """Test PID behavior when mode is OFF."""
        # Create PID controller
        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100,
                  cold_tolerance=0.3, hot_tolerance=0.3)

        # Set mode to OFF
        pid.mode = 'OFF'

        # Below setpoint - cold tolerance: should turn on (max output)
        output, changed = pid.calc(input_val=19.5, set_point=20.0, input_time=0.0)
        assert changed is True
        assert output == 100.0

        # Above setpoint + hot tolerance: should turn off (min output)
        output, changed = pid.calc(input_val=20.5, set_point=20.0, input_time=1.0)
        assert changed is True
        assert output == 0.0

        # Within tolerance band: should not change
        output, changed = pid.calc(input_val=20.1, set_point=20.0, input_time=2.0)
        assert changed is False

    def test_external_temperature_compensation(self):
        """Test ke parameter for outdoor temperature compensation."""
        # Create PID with outdoor compensation
        pid = PID(kp=10, ki=0.5, kd=2, ke=0.8, out_min=0, out_max=100)

        # Calculate with external temperature
        output_with_ext, _ = pid.calc(
            input_val=20.0,
            set_point=22.0,
            input_time=0.0,
            ext_temp=5.0
        )

        # External term should be: ke * (setpoint - ext_temp) = 0.8 * (22 - 5) = 13.6
        assert pid.external == pytest.approx(13.6, rel=0.01)

        # Output should include external compensation
        assert output_with_ext > 0

    def test_set_pid_parameters(self):
        """Test updating PID parameters."""
        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100)

        # Update parameters
        pid.set_pid_param(kp=20, ki=1.0, kd=5.0, ke=0.5)

        # Verify they were updated
        assert pid._Kp == 20
        assert pid._Ki == 1.0
        assert pid._Kd == 5.0
        assert pid._Ke == 0.5

    def test_clear_samples(self):
        """Test clearing PID samples."""
        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100)

        # Run a calculation
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0)

        # Clear samples
        pid.clear_samples()

        # Verify samples are cleared
        assert pid._input is None
        assert pid._input_time is None
        assert pid._last_input is None
        assert pid._last_input_time is None

    def test_sampling_period_mode_respects_timing(self):
        """Test that sampling period mode skips calculations when called too frequently.

        This tests the fix for the timing bug where time() - self._input_time was
        incorrectly used instead of time() - self._last_input_time.
        """
        from unittest.mock import patch

        # Create PID with 60 second sampling period
        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100, sampling_period=60)

        # Mock time() to control the timing
        mock_time = 1000.0

        with patch('pid_controller.time', return_value=mock_time):
            # First calculation should always run (no last_input_time yet)
            output1, changed1 = pid.calc(input_val=20.0, set_point=22.0)
            assert changed1 is True
            assert pid._input_time == 1000.0
            # After first call, _last_input_time is set from previous _input_time (was None)
            assert pid._last_input_time is None

        # Second call at t=1030 (30s later) - still runs because _last_input_time
        # becomes set after this call (it gets the previous _input_time)
        mock_time = 1030.0
        with patch('pid_controller.time', return_value=mock_time):
            output2, changed2 = pid.calc(input_val=20.5, set_point=22.0)
            assert changed2 is True  # Runs because _last_input_time was still None
            assert pid._last_input_time == 1000.0  # Now set from previous _input_time
            assert pid._input_time == 1030.0

        # Third call at t=1040 (10s after second call) - should be SKIPPED
        # because 10s < 60s sampling period
        mock_time = 1040.0
        with patch('pid_controller.time', return_value=mock_time):
            output3, changed3 = pid.calc(input_val=21.0, set_point=22.0)
            assert changed3 is False  # Skipped - too soon
            assert output3 == output2  # Returns cached output
            # Timestamps should NOT be updated when skipped
            assert pid._last_input_time == 1000.0
            assert pid._input_time == 1030.0

        # Fourth call at t=1100 (70s after second call's _last_input_time)
        # Should run: 1100 - 1000 = 100s > 60s sampling period
        mock_time = 1100.0
        with patch('pid_controller.time', return_value=mock_time):
            output4, changed4 = pid.calc(input_val=21.5, set_point=22.0)
            assert changed4 is True
            # Error is now 0.5 (22 - 21.5)
            assert pid.error == 0.5
            assert pid._last_input_time == 1030.0  # Updated to previous _input_time
            assert pid._input_time == 1100.0

    def test_event_driven_mode_timestamp_validation(self, caplog):
        """Test that event-driven mode logs warning when timestamp not provided.

        When sampling_period=0, the controller operates in event-driven mode
        and expects timestamps to be provided via input_time parameter.
        """
        import logging

        # Create PID in event-driven mode (sampling_period=0)
        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100, sampling_period=0)

        # Call without providing input_time - should log warning
        with caplog.at_level(logging.WARNING):
            output, changed = pid.calc(input_val=20.0, set_point=22.0)

        # Should have logged a warning about missing timestamp
        assert "event-driven mode" in caplog.text
        assert "input_time" in caplog.text

        # Should still calculate output (using time() as fallback)
        assert changed is True
        assert pid.error == 2.0

        # Clear log for next test
        caplog.clear()

        # Call with input_time provided - should NOT log warning
        with caplog.at_level(logging.WARNING):
            output2, changed2 = pid.calc(
                input_val=21.0,
                set_point=22.0,
                input_time=100.0,
                last_input_time=0.0
            )

        # No warning should be logged when timestamp is provided
        assert "event-driven mode" not in caplog.text
        assert changed2 is True

        # Verify dt was calculated correctly
        assert pid.dt == 100.0  # input_time - last_input_time = 100 - 0

    def test_integral_preserved_on_setpoint_change(self):
        """Test that integral is preserved on setpoint change with P-on-M.

        With proportional-on-measurement mode, the integral term is NOT reset
        when the setpoint changes. This provides smoother transitions.
        """
        pid = PID(kp=10, ki=1.0, kd=0, out_min=0, out_max=100)

        # Build up integral with constant setpoint over multiple cycles
        # Use dt >= 10s to meet MIN_DT_FOR_DERIVATIVE threshold
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)
        pid.calc(input_val=20.5, set_point=22.0, input_time=15.0, last_input_time=0.0)
        pid.calc(input_val=21.0, set_point=22.0, input_time=30.0, last_input_time=15.0)

        # Integral should have accumulated (Ki=1.0, error reduces over time)
        integral_before = pid.integral
        assert integral_before != 0, "Integral should have accumulated"

        # Change setpoint WITHOUT providing ext_temp
        pid.calc(input_val=21.0, set_point=25.0, input_time=45.0, last_input_time=30.0)

        # Integral should be preserved and continue accumulating (P-on-M behavior)
        assert pid.integral > integral_before, "Integral should continue accumulating with larger error"

    def test_mode_switch_off_to_auto_clears_samples(self):
        """Test that switching from OFF to AUTO clears samples.

        When PID is in OFF mode, it uses simple bang-bang control. The timestamps
        and input values from OFF mode are stale and should not be used for
        derivative calculations when switching to AUTO mode.
        """
        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100)

        # Run some calculations in AUTO mode to populate samples
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)
        pid.calc(input_val=20.5, set_point=22.0, input_time=1.0, last_input_time=0.0)

        # Verify samples exist
        assert pid._input is not None
        assert pid._input_time is not None
        assert pid._last_input is not None

        # Switch to OFF mode
        pid.mode = 'OFF'

        # Samples should still exist (OFF mode doesn't clear)
        assert pid._input is not None

        # Run in OFF mode (this won't update samples meaningfully)
        pid.calc(input_val=19.0, set_point=22.0, input_time=100.0)

        # Switch back to AUTO - this should clear samples
        pid.mode = 'AUTO'

        # Samples should be cleared for fresh PID calculation
        assert pid._input is None
        assert pid._input_time is None
        assert pid._last_input is None
        assert pid._last_input_time is None

        # First calculation after mode switch should work correctly
        # With P-on-M, first call has P=0 since there's no previous measurement
        output1, changed1 = pid.calc(input_val=21.0, set_point=22.0, input_time=200.0)
        assert changed1 is True
        assert output1 == 0  # P=0 on first call with P-on-M (no previous measurement)

        # Second call should have P term responding to measurement change
        output2, changed2 = pid.calc(input_val=21.0, set_point=22.0, input_time=210.0, last_input_time=200.0)
        assert changed2 is True
        # Integral should start accumulating with positive error
        assert pid.integral > 0

    def test_nan_inf_input_validation(self):
        """Test that NaN and Inf inputs return cached output without corrupting state.

        Invalid sensor readings (NaN, Inf) should not corrupt the PID controller
        state. Instead, the cached output should be returned.
        """
        import math

        pid = PID(kp=10, ki=0.5, kd=2, out_min=0, out_max=100)

        # First valid calculation to establish baseline
        output1, changed1 = pid.calc(
            input_val=20.0, set_point=22.0,
            input_time=0.0, last_input_time=None
        )
        assert changed1 is True
        baseline_output = output1

        # NaN input_val should return cached output
        output2, changed2 = pid.calc(
            input_val=float('nan'), set_point=22.0,
            input_time=1.0, last_input_time=0.0
        )
        assert changed2 is False
        assert output2 == baseline_output

        # Inf input_val should return cached output
        output3, changed3 = pid.calc(
            input_val=float('inf'), set_point=22.0,
            input_time=2.0, last_input_time=1.0
        )
        assert changed3 is False
        assert output3 == baseline_output

        # Negative Inf input_val should return cached output
        output4, changed4 = pid.calc(
            input_val=float('-inf'), set_point=22.0,
            input_time=3.0, last_input_time=2.0
        )
        assert changed4 is False
        assert output4 == baseline_output

        # NaN set_point should return cached output
        output5, changed5 = pid.calc(
            input_val=20.0, set_point=float('nan'),
            input_time=4.0, last_input_time=3.0
        )
        assert changed5 is False
        assert output5 == baseline_output

        # Inf set_point should return cached output
        output6, changed6 = pid.calc(
            input_val=20.0, set_point=float('inf'),
            input_time=5.0, last_input_time=4.0
        )
        assert changed6 is False
        assert output6 == baseline_output

        # NaN ext_temp should return cached output
        output7, changed7 = pid.calc(
            input_val=20.0, set_point=22.0,
            input_time=6.0, last_input_time=5.0,
            ext_temp=float('nan')
        )
        assert changed7 is False
        assert output7 == baseline_output

        # Inf ext_temp should return cached output
        output8, changed8 = pid.calc(
            input_val=20.0, set_point=22.0,
            input_time=7.0, last_input_time=6.0,
            ext_temp=float('inf')
        )
        assert changed8 is False
        assert output8 == baseline_output

        # Valid calculation after invalid inputs should work correctly
        output9, changed9 = pid.calc(
            input_val=21.0, set_point=22.0,
            input_time=8.0, last_input_time=0.0  # Use last valid time
        )
        assert changed9 is True
        # State should still be valid and produce reasonable output
        assert 0 <= output9 <= 100


class TestPIDIntegralDimensionalFix:
    """Test PID integral and derivative dimensional correctness (v0.7.0 fix).

    Tests verify that Ki and Kd parameters use hourly time units, not seconds.
    Ki should be in %/(°C·hour) and Kd in %/(°C/hour).
    """

    def test_integral_accumulation_hourly_units(self):
        """Test that integral accumulates correctly with hourly units.

        With Ki = 1.2 %/(°C·hour) and 1°C error for 1 hour (3600 seconds),
        the integral should accumulate 1.2%.
        """
        # Use floor hydronic typical values from physics.py (after 100x increase)
        pid = PID(kp=0.3, ki=1.2, kd=2.5, out_min=0, out_max=100)

        # First calculation establishes baseline
        # With P-on-M, first call has P=0 (no previous measurement)
        output1, _ = pid.calc(input_val=19.0, set_point=20.0, input_time=0.0, last_input_time=None)
        assert output1 == 0  # P=0 on first call with P-on-M
        assert pid.integral == 0.0  # First call initializes to 0

        # Second calculation with dt >= 10s to meet MIN_DT_FOR_DERIVATIVE threshold
        # Integral starts accumulating
        output2, _ = pid.calc(input_val=19.0, set_point=20.0, input_time=15.0, last_input_time=0.0)
        # Integral accumulation over 15 seconds = 1.2 * 1.0 * (15/3600) = 0.005%
        assert pid.integral > 0, f"Expected integral > 0, got {pid.integral}"
        small_integral = pid.integral

        # Third calculation: maintain 1°C error for 1 hour (3600 seconds from time=15)
        # Expected additional accumulation: Ki * error * dt_hours = 1.2 * 1.0 * 1.0 = 1.2%
        output3, _ = pid.calc(input_val=19.0, set_point=20.0, input_time=3615.0, last_input_time=15.0)

        # Total integral = small_integral + 1.2 ≈ 1.2%
        assert abs(pid.integral - 1.2) < 0.01, f"Expected integral ~1.2, got {pid.integral}"

        # With P-on-M and constant input, P=0 (no measurement change)
        assert pid.proportional == 0.0

    def test_derivative_calculation_hourly_units(self):
        """Test that derivative calculates correctly with hourly rate units.

        With Kd = 2.5 %/(°C/hour) and 1°C change over 1 hour,
        the derivative should contribute -2.5% (negative of derivative of process variable).
        """
        # Disable derivative filter for this test (alpha=1.0 = no filtering)
        pid = PID(kp=0.3, ki=1.2, kd=2.5, out_min=-100, out_max=100, derivative_filter_alpha=1.0)

        # First reading at 20°C
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second reading 1 hour later at 21°C (heating by 1°C over 1 hour)
        # Rate of change = 1°C/hour
        # Derivative term = -Kd * rate = -2.5 * 1.0 = -2.5%
        output, _ = pid.calc(input_val=21.0, set_point=22.0, input_time=3600.0, last_input_time=0.0)

        # Check derivative term
        assert abs(pid.derivative - (-2.5)) < 0.01, f"Expected derivative ~-2.5, got {pid.derivative}"

    def test_floor_hydronic_realistic_scenario(self):
        """Test realistic floor hydronic scenario with 2-hour error accumulation.

        Simulates underfloor heating system starting cold and slowly heating up.
        Uses lower Kd to keep output in valid range for anti-windup testing.
        """
        # Floor hydronic: slow system with high thermal mass (lower Kd for this test)
        pid = PID(kp=0.3, ki=1.2, kd=0.8, out_min=0, out_max=100)

        # Start 3°C below setpoint (cold floor)
        # With P-on-M, first call has P=0 (no previous measurement)
        pid.calc(input_val=18.0, set_point=21.0, input_time=0.0, last_input_time=None)
        assert pid.proportional == 0.0  # P=0 on first call with P-on-M

        # After 30 minutes (1800 seconds), temp rises to 18.5°C slowly
        # Error = 2.5°C, dt = 0.5 hours, rate of change = 1°C/hour
        # P = 0.3 * 2.5 = 0.75, I = 1.5, D = -0.8 * 1.0 = -0.8
        # Total = 0.75 + 1.5 - 0.8 = 1.45% (positive, allows continued integration)
        # Integral accumulation = 1.2 * 2.5 * 0.5 = 1.5%
        pid.calc(input_val=18.5, set_point=21.0, input_time=1800.0, last_input_time=0.0)
        assert abs(pid.integral - 1.5) < 0.01
        assert pid._output > 0, "Output should be positive to allow continued integration"

        # After 1 hour total, temp at 19.0°C (continuing slow rise)
        # Error = 2.0°C, additional dt = 0.5 hours
        # Additional accumulation = 1.2 * 2.0 * 0.5 = 1.2%
        # Total integral = 1.5 + 1.2 = 2.7%
        pid.calc(input_val=19.0, set_point=21.0, input_time=3600.0, last_input_time=1800.0)
        assert abs(pid.integral - 2.7) < 0.01
        assert pid._output > 0, "Output should remain positive"

        # After 2 hours total, temp at 19.8°C (approaching setpoint)
        # Error = 1.2°C, additional dt = 1.0 hours
        # Additional accumulation = 1.2 * 1.2 * 1.0 = 1.44%
        # Total integral = 2.7 + 1.44 = 4.14%
        pid.calc(input_val=19.8, set_point=21.0, input_time=7200.0, last_input_time=3600.0)
        assert abs(pid.integral - 4.14) < 0.02

        # Integral contribution should be meaningful but not overwhelming
        # After 2 hours with average 2°C error, integral accumulated ~4%
        # This is reasonable for a slow floor heating system


class TestPIDDerivativeFilter:
    """Test derivative term filtering to reduce sensor noise amplification.

    Tests verify EMA filtering of derivative term works correctly across
    different alpha values and reduces noise sensitivity.
    """

    def test_derivative_filter_noise_reduction(self):
        """Test that derivative filter reduces sensor noise amplification.

        Inject ±0.1°C noise on temperature readings and verify filtered
        derivative is more stable than unfiltered.
        """
        # Create two PIDs: one with filtering (alpha=0.15), one without (alpha=1.0)
        pid_filtered = PID(kp=10, ki=1.0, kd=5.0, out_min=0, out_max=100,
                          derivative_filter_alpha=0.15)
        pid_unfiltered = PID(kp=10, ki=1.0, kd=5.0, out_min=0, out_max=100,
                            derivative_filter_alpha=1.0)

        # Start at 20.0°C, target 22.0°C
        pid_filtered.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)
        pid_unfiltered.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Temperature readings with injected noise: 20.1 (noise +0.1), 20.15 (noise -0.05), 20.3 (noise +0.1)
        # Real trend: heating from 20.0 -> 20.1 -> 20.2 -> 20.3
        # Noisy readings: 20.0 -> 20.1 -> 20.15 -> 20.3

        # Reading 1: 20.1°C (rise of 0.1°C in 60s)
        pid_filtered.calc(input_val=20.1, set_point=22.0, input_time=60.0, last_input_time=0.0)
        pid_unfiltered.calc(input_val=20.1, set_point=22.0, input_time=60.0, last_input_time=0.0)
        deriv_filtered_1 = pid_filtered.derivative
        deriv_unfiltered_1 = pid_unfiltered.derivative

        # Reading 2: 20.15°C (apparent rise of only 0.05°C in 60s due to noise)
        # This should show noise impact
        pid_filtered.calc(input_val=20.15, set_point=22.0, input_time=120.0, last_input_time=60.0)
        pid_unfiltered.calc(input_val=20.15, set_point=22.0, input_time=120.0, last_input_time=60.0)
        deriv_filtered_2 = pid_filtered.derivative
        deriv_unfiltered_2 = pid_unfiltered.derivative

        # Reading 3: 20.3°C (large apparent rise of 0.15°C in 60s due to noise)
        pid_filtered.calc(input_val=20.3, set_point=22.0, input_time=180.0, last_input_time=120.0)
        pid_unfiltered.calc(input_val=20.3, set_point=22.0, input_time=180.0, last_input_time=120.0)
        deriv_filtered_3 = pid_filtered.derivative
        deriv_unfiltered_3 = pid_unfiltered.derivative

        # Calculate variance of derivative values (measure of noise)
        # Filtered should have lower variance (more stable)
        import statistics
        variance_filtered = statistics.variance([deriv_filtered_1, deriv_filtered_2, deriv_filtered_3])
        variance_unfiltered = statistics.variance([deriv_unfiltered_1, deriv_unfiltered_2, deriv_unfiltered_3])

        # Filtered derivative should be more stable (lower variance)
        assert variance_filtered < variance_unfiltered, \
            f"Filtered variance {variance_filtered} should be < unfiltered {variance_unfiltered}"

    def test_derivative_filter_alpha_range(self):
        """Test derivative filter behavior across alpha range 0.0 to 1.0.

        Alpha = 0.0: maximum filtering (derivative becomes constant)
        Alpha = 1.0: no filtering (raw derivative)
        Alpha = 0.5: moderate filtering
        """
        # Test alpha = 0.0 (maximum filtering)
        pid_max_filter = PID(kp=10, ki=1.0, kd=5.0, out_min=0, out_max=100,
                            derivative_filter_alpha=0.0)

        # First calculation
        pid_max_filter.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)
        assert pid_max_filter.derivative == 0.0  # No previous derivative

        # Second calculation with temperature change
        pid_max_filter.calc(input_val=20.5, set_point=22.0, input_time=60.0, last_input_time=0.0)
        # With alpha=0.0: filtered = 0.0 * raw + 1.0 * 0.0 = 0.0
        # Derivative should remain 0 (maximum filtering suppresses all changes)
        assert pid_max_filter.derivative == 0.0

        # Test alpha = 1.0 (no filtering)
        pid_no_filter = PID(kp=10, ki=1.0, kd=5.0, out_min=0, out_max=100,
                           derivative_filter_alpha=1.0)

        pid_no_filter.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)
        pid_no_filter.calc(input_val=20.5, set_point=22.0, input_time=60.0, last_input_time=0.0)

        # Calculate expected raw derivative
        # Rate = 0.5°C / 60s = 0.5°C / (60/3600)h = 30°C/h
        # Derivative = -Kd * rate = -5.0 * 30 = -150%
        expected_derivative = -150.0
        assert abs(pid_no_filter.derivative - expected_derivative) < 0.1

        # Test alpha = 0.5 (moderate filtering)
        pid_moderate = PID(kp=10, ki=1.0, kd=5.0, out_min=0, out_max=100,
                          derivative_filter_alpha=0.5)

        pid_moderate.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)
        pid_moderate.calc(input_val=20.5, set_point=22.0, input_time=60.0, last_input_time=0.0)

        # With alpha=0.5: filtered = 0.5 * (-150) + 0.5 * 0.0 = -75
        assert abs(pid_moderate.derivative - (-75.0)) < 0.1

    def test_derivative_filter_disable(self):
        """Test that alpha=1.0 completely disables filter (raw derivative passthrough)."""
        # Alpha = 1.0 should give unfiltered derivative
        pid = PID(kp=10, ki=1.0, kd=5.0, out_min=0, out_max=100,
                 derivative_filter_alpha=1.0)

        # First calculation
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second calculation with 1°C change over 1 hour
        pid.calc(input_val=21.0, set_point=22.0, input_time=3600.0, last_input_time=0.0)

        # Raw derivative = -Kd * (1°C / 1 hour) = -5.0 * 1.0 = -5.0%
        # With alpha=1.0, filtered = 1.0 * raw + 0.0 * prev = raw
        assert abs(pid.derivative - (-5.0)) < 0.01

        # Third calculation with noise spike (0.2°C in 60 seconds)
        pid.calc(input_val=21.2, set_point=22.0, input_time=3660.0, last_input_time=3600.0)

        # Rate = 0.2°C / (60/3600)h = 12°C/h
        # Raw derivative = -5.0 * 12 = -60%
        # With alpha=1.0, should be unfiltered: -60%
        assert abs(pid.derivative - (-60.0)) < 0.1

    def test_derivative_filter_persistence_through_samples_clear(self):
        """Test that derivative filter resets when samples are cleared."""
        pid = PID(kp=10, ki=1.0, kd=5.0, out_min=0, out_max=100,
                 derivative_filter_alpha=0.15)

        # Build up filtered derivative
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)
        pid.calc(input_val=20.5, set_point=22.0, input_time=60.0, last_input_time=0.0)
        pid.calc(input_val=21.0, set_point=22.0, input_time=120.0, last_input_time=60.0)

        # Derivative filter should have some value
        assert pid._derivative_filtered != 0.0

        # Clear samples (e.g., when switching modes OFF -> AUTO)
        pid.clear_samples()

        # Filtered derivative should be reset to 0
        assert pid._derivative_filtered == 0.0


class TestPIDOutdoorTempLag:
    """Test outdoor temperature lag (EMA filter) functionality."""

    def test_outdoor_temp_ema_filter(self):
        """Test outdoor temperature EMA filter simulating sunny day scenario."""
        # Create PID with outdoor_temp_lag_tau = 4 hours
        pid = PID(kp=50, ki=1.0, kd=2.0, ke=0.005, out_min=0, out_max=100,
                  outdoor_temp_lag_tau=4.0)

        # Simulate sunny day: outdoor temp rises from 10°C to 20°C over 8 hours
        # Indoor temp stable at 20°C, setpoint = 21°C
        # Samples every 30 minutes
        indoor_temp = 20.0
        setpoint = 21.0

        # Initial outdoor temp = 10°C
        t = 0.0
        outdoor_temp = 10.0
        output, _ = pid.calc(input_val=indoor_temp, set_point=setpoint,
                            input_time=t, last_input_time=None, ext_temp=outdoor_temp)

        # First reading should initialize lagged temp to current outdoor temp
        assert pid.outdoor_temp_lagged == 10.0

        # Outdoor temp rises to 15°C after 2 hours
        t = 7200.0  # 2 hours in seconds
        outdoor_temp = 15.0
        output, _ = pid.calc(input_val=indoor_temp, set_point=setpoint,
                            input_time=t, last_input_time=0.0, ext_temp=outdoor_temp)

        # Lagged temp should be between 10 and 15 (filtered)
        # With tau=4h, dt=2h, alpha = 2/4 = 0.5
        # lagged = 0.5 * 15 + 0.5 * 10 = 12.5
        assert 10.0 < pid.outdoor_temp_lagged < 15.0
        assert abs(pid.outdoor_temp_lagged - 12.5) < 0.1

        # Outdoor temp rises to 17.5°C after another 1 hour (3 hours total)
        t = 10800.0  # 3 hours in seconds
        outdoor_temp = 17.5
        output, _ = pid.calc(input_val=indoor_temp, set_point=setpoint,
                            input_time=t, last_input_time=7200.0, ext_temp=outdoor_temp)

        # Lagged temp should be catching up
        # alpha = 1/4 = 0.25, lagged = 0.25 * 17.5 + 0.75 * 12.5 = 4.375 + 9.375 = 13.75
        assert 12.5 < pid.outdoor_temp_lagged < 17.5
        assert abs(pid.outdoor_temp_lagged - 13.75) < 0.1

        # After another hour, outdoor temp at 20°C (4 hours total)
        t = 14400.0  # 4 hours in seconds
        outdoor_temp = 20.0
        output, _ = pid.calc(input_val=indoor_temp, set_point=setpoint,
                            input_time=t, last_input_time=10800.0, ext_temp=outdoor_temp)

        # Lagged temp continues to approach outdoor temp
        # alpha = 0.25, lagged = 0.25 * 20 + 0.75 * 13.75 = 5.0 + 10.3125 = 15.3125
        assert 13.75 < pid.outdoor_temp_lagged < 20.0
        assert abs(pid.outdoor_temp_lagged - 15.3125) < 0.1

    def test_outdoor_temp_lag_initialization(self):
        """Test that outdoor temp lag initializes with first reading."""
        # Create PID with default tau
        pid = PID(kp=50, ki=1.0, kd=2.0, ke=0.005, out_min=0, out_max=100)

        # Initially, lagged temp should be None
        assert pid.outdoor_temp_lagged is None

        # First calc with outdoor temp
        indoor_temp = 20.0
        setpoint = 21.0
        outdoor_temp = 5.0
        t = 0.0

        output, _ = pid.calc(input_val=indoor_temp, set_point=setpoint,
                            input_time=t, last_input_time=None, ext_temp=outdoor_temp)

        # After first reading, lagged temp should equal outdoor temp (no warmup needed)
        assert pid.outdoor_temp_lagged == 5.0

        # Verify dext uses the lagged temp
        # dext = setpoint - outdoor_temp_lagged = 21 - 5 = 16
        assert pid._dext == 16.0

    def test_outdoor_temp_lag_reset_on_clear_samples(self):
        """Test that outdoor temp lag resets when samples are cleared."""
        # Create PID with outdoor temp lag
        pid = PID(kp=50, ki=1.0, kd=2.0, ke=0.005, out_min=0, out_max=100,
                  outdoor_temp_lag_tau=4.0)

        # Initialize with outdoor temp
        pid.calc(input_val=20.0, set_point=21.0, input_time=0.0,
                last_input_time=None, ext_temp=10.0)

        assert pid.outdoor_temp_lagged == 10.0

        # Clear samples (e.g., mode change OFF -> AUTO)
        pid.clear_samples()

        # Lagged temp should be reset to None
        assert pid.outdoor_temp_lagged is None

    def test_outdoor_temp_lag_state_persistence(self):
        """Test outdoor temp lag can be restored from saved state."""
        # Create PID
        pid = PID(kp=50, ki=1.0, kd=2.0, ke=0.005, out_min=0, out_max=100,
                  outdoor_temp_lag_tau=4.0)

        # Initialize with outdoor temp
        pid.calc(input_val=20.0, set_point=21.0, input_time=0.0,
                last_input_time=None, ext_temp=10.0)

        # Simulate several readings to update lagged temp
        for i in range(1, 5):
            t = i * 3600.0  # 1 hour intervals
            outdoor_temp = 10.0 + i * 2.0  # Rising temp
            pid.calc(input_val=20.0, set_point=21.0, input_time=t,
                    last_input_time=(i-1)*3600.0, ext_temp=outdoor_temp)

        # Save lagged temp value
        saved_lagged_temp = pid.outdoor_temp_lagged
        assert saved_lagged_temp is not None

        # Simulate restoration by creating new PID and setting lagged temp
        pid2 = PID(kp=50, ki=1.0, kd=2.0, ke=0.005, out_min=0, out_max=100,
                   outdoor_temp_lag_tau=4.0)

        # Restore the lagged temp (as would happen in state_restorer.py)
        pid2.outdoor_temp_lagged = saved_lagged_temp

        # Verify restoration
        assert pid2.outdoor_temp_lagged == saved_lagged_temp

        # Next calc should use restored value
        t = 5 * 3600.0
        outdoor_temp = 18.0
        pid2.calc(input_val=20.0, set_point=21.0, input_time=t,
                 last_input_time=4*3600.0, ext_temp=outdoor_temp)

        # Lagged temp should have updated from restored value
        assert pid2.outdoor_temp_lagged != saved_lagged_temp


class TestPIDBumplessTransfer:
    """Test bumpless transfer for OFF→AUTO mode changes."""

    def test_bumpless_transfer_maintains_output(self):
        """Test that bumpless transfer maintains output continuity when switching from OFF to AUTO."""
        # Create PID controller
        pid = PID(kp=10.0, ki=1.2, kd=2.5, ke=0.005, out_min=0, out_max=100)

        # Run in AUTO mode to establish an output value
        # With P-on-M, first call has P=0, so output starts at 0
        t = 0.0
        output1, _ = pid.calc(input_val=19.5, set_point=21.0, input_time=t,
                             last_input_time=None, ext_temp=10.0)
        # First call has P=0 with P-on-M

        # Continue running to build up integral term
        for i in range(1, 6):
            t = i * 60.0  # 1 minute intervals
            output_before, _ = pid.calc(input_val=19.8, set_point=21.0, input_time=t,
                                       last_input_time=t - 60.0, ext_temp=10.0)

        # Store the last output value - should have accumulated integral by now
        last_output = pid._output
        assert pid.integral > 0  # Integral should have accumulated

        # Switch to OFF mode
        pid.mode = 'OFF'

        # Verify transfer state was stored
        assert pid.has_transfer_state
        assert pid._last_output_before_off == last_output

        # Simulate some time passing in OFF mode
        t += 60.0
        pid.calc(input_val=20.5, set_point=21.0, input_time=t, last_input_time=t - 60.0)

        # Switch back to AUTO mode
        pid.mode = 'AUTO'

        # First calc after switching to AUTO should use bumpless transfer
        t += 60.0
        output_after, _ = pid.calc(input_val=20.0, set_point=21.0, input_time=t,
                                   last_input_time=t - 60.0, ext_temp=10.0)

        # Output should be close to the last output before OFF
        # Allow some tolerance since D term will be different
        assert abs(output_after - last_output) < 5.0, \
            f"Output changed too much: {last_output:.2f} -> {output_after:.2f}"

        # Transfer state should be cleared after use
        assert not pid.has_transfer_state

    def test_bumpless_transfer_skips_on_setpoint_change(self):
        """Test that bumpless transfer is skipped when setpoint changes significantly."""
        # Create PID controller
        pid = PID(kp=10.0, ki=1.2, kd=2.5, out_min=0, out_max=100)

        # Run in AUTO mode
        t = 0.0
        pid.calc(input_val=19.5, set_point=21.0, input_time=t, last_input_time=None)

        for i in range(1, 6):
            t = i * 60.0
            pid.calc(input_val=19.8, set_point=21.0, input_time=t, last_input_time=t - 60.0)

        last_output = pid._output
        last_integral = pid.integral

        # Switch to OFF mode
        pid.mode = 'OFF'
        assert pid.has_transfer_state

        # Switch back to AUTO with significantly different setpoint
        pid.mode = 'AUTO'
        t += 60.0
        pid.calc(input_val=20.0, set_point=24.0, input_time=t, last_input_time=t - 60.0)

        # Transfer should have been skipped due to setpoint change > 2°C
        assert not pid.has_transfer_state
        # With P-on-M, integral is NOT reset on setpoint change - it continues accumulating
        # The integral should be greater than before (larger error with new setpoint)
        assert pid.integral > last_integral, "Integral should continue accumulating with P-on-M"

    def test_bumpless_transfer_skips_on_large_error(self):
        """Test that bumpless transfer is skipped when error is too large."""
        # Create PID controller
        pid = PID(kp=10.0, ki=1.2, kd=2.5, out_min=0, out_max=100)

        # Run in AUTO mode
        t = 0.0
        pid.calc(input_val=19.5, set_point=21.0, input_time=t, last_input_time=None)

        for i in range(1, 6):
            t = i * 60.0
            pid.calc(input_val=20.0, set_point=21.0, input_time=t, last_input_time=t - 60.0)

        # Switch to OFF mode
        pid.mode = 'OFF'
        assert pid.has_transfer_state

        # Switch back to AUTO with large error (> 2°C)
        pid.mode = 'AUTO'
        t += 60.0
        pid.calc(input_val=17.0, set_point=21.0, input_time=t, last_input_time=t - 60.0)

        # Transfer should have been skipped due to large error
        assert not pid.has_transfer_state

    def test_bumpless_transfer_module_exists(self):
        """Marker test to verify bumpless transfer functionality exists."""
        pid = PID(kp=10.0, ki=1.2, kd=2.5, out_min=0, out_max=100)

        # Verify properties and methods exist
        assert hasattr(pid, 'has_transfer_state')
        assert hasattr(pid, 'prepare_bumpless_transfer')
        assert hasattr(pid, '_last_output_before_off')


class TestPIDKeIntegralClamping:
    """Test integral clamping with reduced Ke values (v0.7.0).

    After issue 1.3 reduced Ke values by 100x (from 0.1-1.3 to 0.001-0.013),
    verify that integral headroom is now >99% instead of being severely limited.
    """

    def test_integral_headroom_with_small_ke(self):
        """Verify integral headroom is >99% with reduced Ke values.

        After Ke reduction in v0.7.0, the external term should contribute <1% of output,
        leaving >99% headroom for the integral term to accumulate.

        Test scenario: G-rated house (worst insulation) at -10°C outdoor temp.
        """
        # G-rated house has Ke = 0.013 (after 100x reduction)
        # Worst case: indoor 20°C, outdoor -10°C, delta = 30°C
        # External term: E = Ke * dext = 0.013 * 30 = 0.39%
        # Expected headroom: 100% - 0.39% = 99.61%

        pid = PID(
            kp=0.3,
            ki=1.2,  # floor_hydronic Ki after v0.7.0 fix
            kd=2.5,
            ke=0.013,  # G-rated house (worst insulation)
            out_min=0,
            out_max=100
        )

        # First calculation to initialize
        time0 = 0.0
        output0, _ = pid.calc(20.0, 20.0, input_time=time0, ext_temp=-10.0)

        # External term should be: 0.013 * (20 - (-10)) = 0.013 * 30 = 0.39
        assert pid.external == pytest.approx(0.39, abs=0.01), "External term should be ~0.39%"

        # External term is only 0.39% of 100% range
        external_percentage = (pid.external / 100.0) * 100
        assert external_percentage < 1.0, "External term should be <1% of output range"

        # Integral headroom should be >99%
        integral_headroom = 100.0 - pid.external
        assert integral_headroom > 99.0, "Integral headroom should be >99%"

        # Verify integral clamping formula: integral <= out_max - external
        # Max integral value: 100 - 0.39 = 99.61
        max_integral = pid._out_max - pid.external
        assert max_integral == pytest.approx(99.61, abs=0.01), "Max integral should be ~99.61"

    def test_extreme_cold_no_underheating(self):
        """Verify integral can reach high values in extreme cold without clamping issues.

        Simulate extreme cold scenario where system needs 95% steady-state power.
        With old Ke values (pre-v0.7.0), external term would steal 7.6% of headroom,
        clamping integral at 92.4% and causing underheating.
        With new Ke values (post-v0.7.0), external term only uses 0.39%, allowing
        integral to reach 99.61% if needed.
        """
        # G-rated house in extreme cold: -20°C outdoor, 20°C indoor
        pid = PID(
            kp=0.3,
            ki=1.2,
            kd=2.5,
            ke=0.013,  # G-rated house
            out_min=0,
            out_max=100
        )

        # Initialize PID
        time0 = 0.0
        pid.calc(20.0, 20.0, input_time=time0, ext_temp=-20.0)

        # External term: E = 0.013 * (20 - (-20)) = 0.013 * 40 = 0.52%
        assert pid.external == pytest.approx(0.52, abs=0.01), "External term should be ~0.52%"

        # Simulate cold start: temperature drops to 18°C, needs to recover to 20°C
        # Let integral accumulate over several hours to reach high value
        current_temp = 18.0
        setpoint = 20.0
        time = 0.0

        # Run for 10 hours with constant 2°C error
        # Integral accumulation: Ki * error * time = 1.2 * 2.0 * 10 = 24% per 10 hours
        # Need ~80 hours to reach 95% integral (if system really needs that much)
        for hour in range(80):
            time = hour * 3600.0  # Convert hours to seconds
            last_time = time - 3600.0 if hour > 0 else None

            # Temperature slowly rises but still below setpoint
            # In reality, integral would drive heater to 100% and temp would rise faster
            # But for this test, we're checking if integral CAN accumulate to 95%
            current_temp = 18.0 + (hour * 0.02)  # Very slow rise for testing

            output, _ = pid.calc(current_temp, setpoint, input_time=time, last_input_time=last_time, ext_temp=-20.0)

        # After 80 hours with 2°C error: integral should be ~1.2 * 2.0 * 80 = 192%
        # Clamped to: max_integral = 100 - 0.52 = 99.48%
        # Verify integral can reach very high values (simulating 95% power need)
        assert pid.integral >= 95.0, "Integral should be able to reach 95%+ for extreme cold"
        assert pid.integral <= 99.5, "Integral should be clamped at ~99.5% (100 - 0.52)"

        # Old behavior (pre-v0.7.0) with Ke=1.3:
        # E = 1.3 * 40 = 52%, max_integral = 100 - 52 = 48%
        # This would cause severe underheating in extreme cold!

        # New behavior (v0.7.0) with Ke=0.013:
        # E = 0.013 * 40 = 0.52%, max_integral = 100 - 0.52 = 99.48%
        # System can reach 95%+ power when needed ✓

    def test_integral_clamping_formula_documentation(self):
        """Document and verify the integral clamping formula is correct.

        The clamping formula at line 364/374 of pid_controller/__init__.py:
        self._integral = max(min(self._integral, self._out_max - self._external),
                             self._out_min - self._external)

        This ensures the total output (P + I + D + E) respects out_min and out_max bounds.
        After Ke reduction, this formula now allows >99% integral headroom.
        """
        # Test with moderate Ke value (A-rated house)
        pid = PID(
            kp=0.3,
            ki=2.0,
            kd=2.0,
            ke=0.005,  # A-rated house
            out_min=0,
            out_max=100
        )

        # Initialize at 20°C indoor, 0°C outdoor
        time0 = 0.0
        pid.calc(20.0, 20.0, input_time=time0, ext_temp=0.0)

        # External term: E = 0.005 * 20 = 0.1%
        assert pid.external == pytest.approx(0.1, abs=0.01)

        # Integral bounds should be:
        # Lower: out_min - external = 0 - 0.1 = -0.1
        # Upper: out_max - external = 100 - 0.1 = 99.9
        integral_min = pid._out_min - pid.external
        integral_max = pid._out_max - pid.external

        assert integral_min == pytest.approx(-0.1, abs=0.01)
        assert integral_max == pytest.approx(99.9, abs=0.01)

        # Simulate integral accumulation with 1°C error over 40 hours
        # Integral: Ki * error * time = 2.0 * 1.0 * 40 = 80%
        current_temp = 19.0
        setpoint = 20.0
        time = 0.0

        for hour in range(40):
            time = hour * 3600.0
            last_time = time - 3600.0 if hour > 0 else None
            pid.calc(current_temp, setpoint, input_time=time, last_input_time=last_time, ext_temp=0.0)

        # Integral should accumulate to ~80%
        assert 78.0 <= pid.integral <= 82.0, "Integral should be ~80%"

        # Total output = P + I + D + E should be clamped to [0, 100]
        total = pid.proportional + pid.integral + pid.derivative + pid.external
        assert 0 <= total <= 100, "Total output should respect bounds"

        # This test confirms the clamping formula is working correctly with reduced Ke values

    def test_wind_compensation_calculation(self):
        """Test wind speed compensation in external term."""
        # Create PID with outdoor compensation and wind compensation
        pid = PID(kp=10, ki=1, kd=2, ke=0.005, ke_wind=0.02, out_min=0, out_max=100)

        # Scenario: Indoor 18°C, Setpoint 20°C, Outdoor 5°C, Wind 10 m/s
        input_val = 18.0
        set_point = 20.0
        ext_temp = 5.0
        wind_speed = 10.0

        # Calculate with wind
        output_with_wind, _ = pid.calc(
            input_val, set_point,
            input_time=100.0, last_input_time=0.0,
            ext_temp=ext_temp, wind_speed=wind_speed
        )

        # dext = 20 - 5 = 15
        # External term = Ke * dext + Ke_wind * wind_speed * dext
        #               = 0.005 * 15 + 0.02 * 10 * 15
        #               = 0.075 + 3.0 = 3.075
        expected_external = 0.005 * 15 + 0.02 * 10 * 15
        assert abs(pid.external - expected_external) < 0.1

        # Calculate without wind (wind_speed=0)
        pid2 = PID(kp=10, ki=1, kd=2, ke=0.005, ke_wind=0.02, out_min=0, out_max=100)
        output_no_wind, _ = pid2.calc(
            input_val, set_point,
            input_time=100.0, last_input_time=0.0,
            ext_temp=ext_temp, wind_speed=0.0
        )

        # External term without wind = Ke * dext = 0.005 * 15 = 0.075
        expected_external_no_wind = 0.005 * 15
        assert abs(pid2.external - expected_external_no_wind) < 0.1

        # Wind should increase external term significantly
        assert pid.external > pid2.external

    def test_wind_compensation_graceful_none(self):
        """Test wind compensation handles None wind_speed gracefully."""
        pid = PID(kp=10, ki=1, kd=2, ke=0.005, ke_wind=0.02, out_min=0, out_max=100)

        # Call without wind_speed (defaults to None)
        output, _ = pid.calc(
            18.0, 20.0,
            input_time=100.0, last_input_time=0.0,
            ext_temp=5.0
        )

        # Should treat as wind_speed=0
        # External = Ke * dext = 0.005 * 15 = 0.075
        expected_external = 0.005 * 15
        assert abs(pid.external - expected_external) < 0.1

    def test_wind_amplifies_cold_weather_effect(self):
        """Test that wind amplifies outdoor temperature effect in cold weather."""
        pid = PID(kp=10, ki=1, kd=2, ke=0.005, ke_wind=0.02, out_min=0, out_max=100)

        # Extreme cold: Indoor 18°C, Setpoint 20°C, Outdoor -10°C
        input_val = 18.0
        set_point = 20.0
        ext_temp = -10.0

        # No wind
        pid.calc(
            input_val, set_point,
            input_time=100.0, last_input_time=0.0,
            ext_temp=ext_temp, wind_speed=0.0
        )
        external_no_wind = pid.external

        # High wind (15 m/s)
        pid2 = PID(kp=10, ki=1, kd=2, ke=0.005, ke_wind=0.02, out_min=0, out_max=100)
        pid2.calc(
            input_val, set_point,
            input_time=100.0, last_input_time=0.0,
            ext_temp=ext_temp, wind_speed=15.0
        )
        external_with_wind = pid2.external

        # dext = 20 - (-10) = 30
        # Wind should significantly increase external term
        # No wind: 0.005 * 30 = 0.15
        # With wind: 0.005 * 30 + 0.02 * 15 * 30 = 0.15 + 9.0 = 9.15
        assert abs(external_no_wind - 0.15) < 0.1
        assert abs(external_with_wind - 9.15) < 0.1
        assert external_with_wind > external_no_wind * 50  # Wind increases by >50x


class TestWindCompensationPhysics:
    """Test calculate_ke_wind function from physics module."""

    def test_calculate_ke_wind_default(self):
        """Test default Ke_wind calculation."""
        from adaptive.physics import calculate_ke_wind

        # Default (moderate insulation)
        ke_wind = calculate_ke_wind()
        assert 0.015 <= ke_wind <= 0.025

    def test_calculate_ke_wind_excellent_insulation(self):
        """Test Ke_wind with excellent insulation."""
        from adaptive.physics import calculate_ke_wind

        # Excellent insulation should have lower wind impact
        ke_wind_excellent = calculate_ke_wind(energy_rating="A++++")
        ke_wind_poor = calculate_ke_wind(energy_rating="G")

        assert ke_wind_excellent < ke_wind_poor
        assert ke_wind_excellent < 0.015  # Should be well below baseline

    def test_calculate_ke_wind_window_exposure(self):
        """Test Ke_wind adjustment for high window exposure."""
        from adaptive.physics import calculate_ke_wind

        # Large window area with poor glazing
        ke_wind_high_windows = calculate_ke_wind(
            energy_rating="B",
            window_area_m2=20.0,
            floor_area_m2=50.0,  # 40% window ratio
            window_rating="single"
        )

        # Small window area with good glazing
        ke_wind_low_windows = calculate_ke_wind(
            energy_rating="B",
            window_area_m2=5.0,
            floor_area_m2=50.0,  # 10% window ratio
            window_rating="triple"
        )

        assert ke_wind_high_windows > ke_wind_low_windows


class TestPIDDerivativeTimingProtection:
    """Test PID derivative spike prevention via timing threshold."""

    def test_tiny_dt_freezes_integral_and_derivative(self):
        """Test that tiny dt (< 5s) freezes I and D to prevent spikes."""
        # Create PID controller with known parameters
        # Use larger out_min to avoid clamping issues in test
        pid = PID(kp=10, ki=1.0, kd=50, out_min=-50, out_max=100)

        # First call: establish baseline with normal dt (30s)
        output1, _ = pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call: normal dt (30s) - I and D should update
        output2, _ = pid.calc(input_val=20.1, set_point=22.0, input_time=30.0, last_input_time=0.0)
        integral_after_normal = pid.integral
        derivative_after_normal = pid.derivative

        # Verify I and D were updated (not zero)
        assert integral_after_normal > 0
        assert derivative_after_normal != 0

        # Third call: tiny dt (0.05s) - I and D should freeze
        output3, _ = pid.calc(input_val=20.2, set_point=22.0, input_time=30.05, last_input_time=30.0)

        # Integral and derivative should be frozen (unchanged)
        assert pid.integral == integral_after_normal
        assert pid.derivative == derivative_after_normal

    def test_boundary_conditions(self):
        """Test boundary conditions around 5s threshold."""
        # Use wider output range and smaller gains to avoid saturation
        pid = PID(kp=2, ki=0.1, kd=5, out_min=-50, out_max=100)

        # First call: establish baseline
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call: dt = 30s (normal update)
        output2, _ = pid.calc(input_val=20.1, set_point=22.0, input_time=30.0, last_input_time=0.0)
        baseline_integral = pid.integral
        baseline_derivative = pid.derivative

        # dt = 4.5s (below threshold) - should freeze
        pid.calc(input_val=20.2, set_point=22.0, input_time=34.5, last_input_time=30.0)
        assert pid.integral == baseline_integral  # Frozen
        assert pid.derivative == baseline_derivative  # Frozen

        # dt = 5.0s (exactly at threshold) - should update
        pid.calc(input_val=20.3, set_point=22.0, input_time=39.5, last_input_time=34.5)
        assert pid.integral > baseline_integral  # Updated

        # dt = 7.5s (above threshold) - should update
        # Use a larger dt to ensure integral accumulates
        new_baseline = pid.integral
        pid.calc(input_val=20.4, set_point=22.0, input_time=47.0, last_input_time=39.5)
        assert pid.integral > new_baseline  # Updated

    def test_rapid_calls_sequence(self):
        """Test rapid call sequence: sensor → external → periodic.

        Only the first (sensor) call with normal dt should update I&D.
        """
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100)

        # First call: establish baseline
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Sensor update: dt = 30s (normal) - should update I and D
        pid.calc(input_val=20.1, set_point=22.0, input_time=30.0, last_input_time=0.0)
        integral_after_sensor = pid.integral
        derivative_after_sensor = pid.derivative
        assert integral_after_sensor > 0
        assert derivative_after_sensor != 0

        # External sensor update: dt = 0.1s (tiny) - should freeze I and D
        pid.calc(input_val=20.1, set_point=22.0, input_time=30.1, last_input_time=30.0)
        assert pid.integral == integral_after_sensor
        assert pid.derivative == derivative_after_sensor

        # Periodic control loop: dt = 0.1s (tiny) - should freeze I and D
        pid.calc(input_val=20.1, set_point=22.0, input_time=30.2, last_input_time=30.1)
        assert pid.integral == integral_after_sensor
        assert pid.derivative == derivative_after_sensor

    def test_normal_operation_preserved(self):
        """Test that normal operation (dt ≥ 5s) is unchanged."""
        # Use wider output range and smaller gains to avoid saturation
        pid = PID(kp=5, ki=0.5, kd=10, out_min=-50, out_max=100)

        # First call
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call: dt = 15s - should update normally
        pid.calc(input_val=20.1, set_point=22.0, input_time=15.0, last_input_time=0.0)
        integral_1 = pid.integral

        # Third call: dt = 20s - should update normally
        pid.calc(input_val=20.2, set_point=22.0, input_time=35.0, last_input_time=15.0)
        integral_2 = pid.integral

        # Fourth call: dt = 60s - should update normally
        pid.calc(input_val=20.3, set_point=22.0, input_time=95.0, last_input_time=35.0)
        integral_3 = pid.integral

        # All should show integral accumulation
        assert integral_2 > integral_1
        assert integral_3 > integral_2

    def test_first_call_initializes_to_zero(self):
        """Test that first call (dt=0) initializes I and D to zero."""
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100)

        # First call with dt=0 (implicit from last_input_time=None)
        output, _ = pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # I and D should be zero on first call
        assert pid.integral == 0.0
        assert pid.derivative == 0.0
        assert pid.dt == 0.0

    def test_ema_filter_state_preserved_when_frozen(self):
        """Test that EMA filter state is preserved during freeze."""
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100,
                  derivative_filter_alpha=0.3)

        # First call
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call: dt = 30s - establish filtered derivative
        pid.calc(input_val=20.1, set_point=22.0, input_time=30.0, last_input_time=0.0)

        # Third call: dt = 30s - continue filtering
        pid.calc(input_val=20.3, set_point=22.0, input_time=60.0, last_input_time=30.0)
        derivative_before_freeze = pid.derivative
        filtered_before_freeze = pid._derivative_filtered

        # Fourth call: dt = 0.05s (tiny) - freeze D
        pid.calc(input_val=20.4, set_point=22.0, input_time=60.05, last_input_time=60.0)

        # Filtered derivative state should be preserved
        assert pid._derivative_filtered == filtered_before_freeze
        assert pid.derivative == derivative_before_freeze

        # Fifth call: dt = 30s - resume normal operation
        pid.calc(input_val=20.5, set_point=22.0, input_time=90.05, last_input_time=60.05)

        # Derivative should update based on preserved filter state
        assert pid.derivative != derivative_before_freeze

    def test_no_derivative_spike_from_noise(self):
        """Test that sensor noise with tiny dt doesn't cause derivative spikes."""
        pid = PID(kp=10, ki=1.0, kd=500, out_min=0, out_max=100)

        # First call
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call: dt = 30s - normal operation
        pid.calc(input_val=20.1, set_point=22.0, input_time=30.0, last_input_time=0.0)
        output_before = pid._output
        derivative_before = pid.derivative

        # Third call: dt = 0.049s with tiny temperature noise (0.002°C)
        # Without protection, this would cause: -(500 * 0.002) / (0.049/3600) = -146,938% spike!
        # With protection, derivative should freeze
        pid.calc(input_val=20.102, set_point=22.0, input_time=30.049, last_input_time=30.0)

        # Derivative should be frozen (unchanged), not spiking
        assert pid.derivative == derivative_before

        # Output should not have wild spike
        assert abs(pid._output - output_before) < 50  # Reasonable change

    def test_proportional_term_updates_even_when_id_frozen(self):
        """Test that proportional term updates even when I and D are frozen."""
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100)

        # First call
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call: dt = 30s
        pid.calc(input_val=20.1, set_point=22.0, input_time=30.0, last_input_time=0.0)
        integral_before = pid.integral
        derivative_before = pid.derivative

        # Third call: dt = 0.05s with temperature change - I&D freeze but P should update
        pid.calc(input_val=20.5, set_point=22.0, input_time=30.05, last_input_time=30.0)

        # I and D frozen
        assert pid.integral == integral_before
        assert pid.derivative == derivative_before

        # P-on-M: P should reflect measurement change (20.5 - 20.1 = 0.4)
        # P = -Kp * input_diff = -10 * 0.4 = -4.0
        assert pid.proportional == pytest.approx(-4.0)


class TestPIDAntiWindupWindDown:
    """Test back-calculation anti-windup allows integral wind-down when error opposes saturation."""

    def test_antiwindup_allows_winddown_from_high_saturation(self):
        """Test integral can wind down when saturated high but error negative."""
        # Create PID with low Kp (2) so integral must build up to reach saturation
        pid = PID(kp=2, ki=10, kd=0, out_min=-50, out_max=100)

        # First call: establish baseline
        pid.calc(input_val=10.0, set_point=20.0, input_time=0.0, last_input_time=None)

        # Build up integral over time to saturate output
        # With P-on-M and constant input, P=0, so output = integral only
        # error = 10.0, Ki = 10, dt = 100s = 100/3600 hours
        # Each iteration: delta_I = 10 * 10 * (100/3600) ≈ 2.778
        # Need ~37 iterations to build I to 100%
        for i in range(1, 40):
            last_time = 100.0 * (i - 1)
            current_time = 100.0 * i
            output, _ = pid.calc(input_val=10.0, set_point=20.0, input_time=current_time, last_input_time=last_time)

        # Should now be saturated at 100%
        assert output == 100.0, f"Expected saturation, got output={output}, I={pid.integral}, P={pid.proportional}"
        assert pid.integral >= 100.0, f"Integral should be clamped at 100, got {pid.integral}"
        initial_integral = pid.integral

        # Now create negative error (temperature overshoots setpoint)
        # error = 20.0 - 22.0 = -2.0
        # _last_output = 100 (saturated), error = -2.0 (negative)
        # saturated_high = (100 >= 100) and (-2.0 > 0) = True and False = False
        # So integration SHOULD proceed
        output_final, _ = pid.calc(input_val=22.0, set_point=20.0, input_time=4000.0, last_input_time=3900.0)

        # Integral should decrease (wind down) because error is negative
        # dt = 100s = 100/3600 hours, error = -2.0, Ki = 10
        # delta_I = 10 * (-2.0) * (100/3600) ≈ -0.556
        assert pid.integral < initial_integral, \
            f"Integral should wind down when error opposes saturation: I={pid.integral} vs initial={initial_integral}"

    def test_antiwindup_allows_winddown_from_low_saturation(self):
        """Test integral can wind down when saturated low but error positive."""
        # Create PID for cooling scenario
        pid = PID(kp=2, ki=10, kd=0, out_min=-50, out_max=100)

        # Establish baseline
        pid.calc(input_val=30.0, set_point=20.0, input_time=0.0, last_input_time=None)

        # Build up negative integral to saturate output low
        # With P-on-M and constant input, P=0, so output = integral only
        # error = 20.0 - 30.0 = -10.0
        # Need I to build down to -50 for saturation at -50%
        # Each iteration: delta_I = 10 * (-10) * (100/3600) ≈ -2.778
        # Need ~20 iterations to reach -50%
        for i in range(1, 25):
            last_time = 100.0 * (i - 1)
            current_time = 100.0 * i
            output, _ = pid.calc(input_val=30.0, set_point=20.0, input_time=current_time, last_input_time=last_time)

        # Should now be saturated at -50%
        assert output == -50.0, f"Expected low saturation, got {output}"
        assert pid.integral <= -50.0, f"Integral should be clamped at -50, got {pid.integral}"
        initial_integral = pid.integral

        # Now create positive error (temperature drops below setpoint)
        # error = 20.0 - 18.0 = 2.0
        # _last_output = -50 (saturated), error = 2.0 (positive)
        # saturated_low = (-50 <= -50) and (2.0 < 0) = True and False = False
        # So integration SHOULD proceed
        pid.calc(input_val=18.0, set_point=20.0, input_time=2500.0, last_input_time=2400.0)

        # Integral should increase because error is positive
        # dt = 100s = 100/3600 hours, error = 2.0, Ki = 10
        # delta_I = 10 * 2.0 * (100/3600) ≈ 0.556
        assert pid.integral > initial_integral, \
            f"Integral should wind up when error opposes low saturation: {pid.integral} <= {initial_integral}"

    def test_antiwindup_blocks_further_windup_at_saturation(self):
        """Test integral blocked when saturated AND error drives further saturation."""
        pid = PID(kp=2, ki=10, kd=0, out_min=-50, out_max=100)

        # Establish baseline
        pid.calc(input_val=10.0, set_point=20.0, input_time=0.0, last_input_time=None)

        # Build up integral to saturate
        # With P-on-M and constant input, P=0, so output = integral only
        # Need ~37 iterations to reach 100%
        for i in range(1, 40):
            last_time = 100.0 * (i - 1)
            current_time = 100.0 * i
            output, _ = pid.calc(input_val=10.0, set_point=20.0, input_time=current_time, last_input_time=last_time)

        # Saturate output high
        assert output == 100.0
        saturated_integral = pid.integral

        # Continue with positive error (same saturation direction)
        # error = 20.0 - 5.0 = 15.0 (still positive, drives saturation)
        # _last_output = 100, error = 15.0
        # saturated_high = (100 >= 100) and (15.0 > 0) = True
        # Integration should be BLOCKED
        pid.calc(input_val=5.0, set_point=20.0, input_time=4000.0, last_input_time=3900.0)

        # Integral should NOT increase beyond saturation point
        # New directional check should PREVENT integration entirely
        assert pid.integral == saturated_integral, \
            f"Integral should be blocked when error drives further saturation: {pid.integral} != {saturated_integral}"

    def test_antiwindup_wind_down_with_measurement_change(self):
        """Test anti-windup wind-down works with varying measurements."""
        pid = PID(kp=2, ki=10, kd=0, out_min=-50, out_max=100)

        # Establish baseline
        pid.calc(input_val=10.0, set_point=20.0, input_time=0.0, last_input_time=None)

        # Build up integral by varying measurement to generate P term
        # Lower measurement → larger error → build up integral
        for i in range(1, 35):
            last_time = 100.0 * (i - 1)
            current_time = 100.0 * i
            # Vary measurement slightly to generate P term
            measurement = 10.0 - 0.01 * (i % 2)  # Oscillate slightly
            output, _ = pid.calc(input_val=measurement, set_point=20.0, input_time=current_time, last_input_time=last_time)

        # Should be saturated at 100%
        assert output == 100.0 or pid.integral > 70.0, f"Expected high integral or saturation, got output={output}, I={pid.integral}"
        initial_integral = pid.integral

        # Create situation where measurement increases (overshoot)
        # P = -Kp * (new_measurement - last_measurement)
        # If new_measurement > last_measurement, P is negative
        pid.calc(input_val=22.0, set_point=20.0, input_time=3500.0, last_input_time=3400.0)

        # Should allow wind-down
        # error = 20.0 - 22.0 = -2.0 (negative)
        # _last_output >= 100, error < 0 → saturated_high = False → integration proceeds
        assert pid.integral < initial_integral, \
            f"Integral should wind down when error opposes saturation: {pid.integral} >= {initial_integral}"


class TestPIDFeedforward:
    """Test feedforward term for thermal coupling compensation."""

    def test_pid_feedforward_init(self):
        """Test feedforward defaults to 0 at initialization."""
        pid = PID(kp=10, ki=1.0, kd=2, out_min=0, out_max=100)

        # Feedforward should default to 0
        assert pid._feedforward == 0.0
        assert pid.feedforward == 0.0

    def test_pid_set_feedforward(self):
        """Test set_feedforward updates internal value."""
        pid = PID(kp=10, ki=1.0, kd=2, out_min=0, out_max=100)

        # Set feedforward to various values
        pid.set_feedforward(5.0)
        assert pid.feedforward == 5.0

        pid.set_feedforward(15.5)
        assert pid.feedforward == 15.5

        pid.set_feedforward(0.0)
        assert pid.feedforward == 0.0

        # Negative feedforward (edge case)
        pid.set_feedforward(-2.0)
        assert pid.feedforward == -2.0

    def test_pid_output_includes_feedforward(self):
        """Test output = P + I + D + E - F (feedforward subtracts from output)."""
        pid = PID(kp=10, ki=1.0, kd=0, ke=0, out_min=0, out_max=100)

        # First call to establish baseline
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call with dt >= MIN_DT_FOR_DERIVATIVE
        output_no_ff, _ = pid.calc(input_val=20.0, set_point=22.0, input_time=60.0, last_input_time=0.0)
        # Output is based on P + I + D + E (no feedforward yet)
        # error = 2.0, Ki = 1.0, dt = 60s = 60/3600 hours
        # P = 0 (P-on-M, no measurement change)
        # I = 1.0 * 2.0 * (60/3600) = 0.0333...
        # D = 0 (no change)
        # E = 0 (no ext_temp)
        # Output ≈ 0.0333...
        assert 0.03 < output_no_ff < 0.04

        # Now set feedforward to 10% - output should decrease by 10
        pid.set_feedforward(10.0)

        # Third call - feedforward should be subtracted
        output_with_ff, _ = pid.calc(input_val=20.0, set_point=22.0, input_time=120.0, last_input_time=60.0)
        # Additional I accumulation: 1.0 * 2.0 * (60/3600) = 0.0333...
        # Total I ≈ 0.0667...
        # Output = P + I + D + E - F = 0 + 0.0667 + 0 + 0 - 10 = -9.93
        # Clamped to out_min = 0
        assert output_with_ff == 0.0

        # Test with larger integral to see feedforward effect
        pid2 = PID(kp=10, ki=50.0, kd=0, ke=0, out_min=0, out_max=100)

        # Build up integral
        pid2.calc(input_val=18.0, set_point=20.0, input_time=0.0, last_input_time=None)
        pid2.calc(input_val=18.0, set_point=20.0, input_time=3600.0, last_input_time=0.0)
        # I = 50 * 2.0 * 1.0 = 100 (clamped to 100)
        output_before_ff = pid2._output
        assert output_before_ff == 100.0

        # Set feedforward to reduce output
        pid2.set_feedforward(30.0)
        output_after_ff, _ = pid2.calc(input_val=18.0, set_point=20.0, input_time=7200.0, last_input_time=3600.0)

        # Now: I clamped to (100 - 0 - 30) = 70 max
        # Output = P + I + D + E - F = 0 + 70 + 0 + 0 - 30 = 40
        # But integral was already 100, now clamped to 70
        # After clamp: Output = 0 + 70 + 0 + 0 - 30 = 40
        assert output_after_ff < output_before_ff
        assert output_after_ff == pytest.approx(40.0, abs=1.0)

    def test_pid_antiwindup_accounts_for_feedforward(self):
        """Test integral clamping: I_max = out_max - E - F."""
        pid = PID(kp=10, ki=100.0, kd=0, ke=0.005, out_min=0, out_max=100)

        # Initialize with outdoor temp - use same input as second call to get P=0
        pid.calc(input_val=15.0, set_point=20.0, input_time=0.0, last_input_time=None, ext_temp=5.0)
        # E = 0.005 * (20 - 5) = 0.075

        # Set feedforward
        pid.set_feedforward(25.0)

        # Build up integral over 1 hour with large error (5.0°C)
        # Using same input_val=15.0 as first call so P=0 (no measurement change)
        pid.calc(input_val=15.0, set_point=20.0, input_time=3600.0, last_input_time=0.0, ext_temp=5.0)

        # I should accumulate: Ki * error * dt_hours = 100 * 5.0 * 1.0 = 500
        # But clamped to: out_max - E - F = 100 - 0.075 - 25 = 74.925
        assert pid.integral == pytest.approx(74.925, abs=0.1)

        # Total output: P + I + D + E - F = 0 + 74.925 + 0 + 0.075 - 25 = 50
        # P = 0 because input_val didn't change (P-on-M)
        # Final clamp to [0, 100]
        assert pid._output == pytest.approx(50.0, abs=0.5)

    def test_feedforward_zero_default_behavior(self):
        """Test that zero feedforward doesn't change existing PID behavior."""
        pid_with_ff = PID(kp=10, ki=1.0, kd=2, ke=0.005, out_min=0, out_max=100)
        pid_without_ff = PID(kp=10, ki=1.0, kd=2, ke=0.005, out_min=0, out_max=100)

        # Run identical calculations
        for i in range(5):
            t = i * 60.0
            last_t = t - 60.0 if i > 0 else None
            out1, _ = pid_with_ff.calc(input_val=19.0 + i * 0.2, set_point=21.0,
                                        input_time=t, last_input_time=last_t, ext_temp=10.0)
            out2, _ = pid_without_ff.calc(input_val=19.0 + i * 0.2, set_point=21.0,
                                           input_time=t, last_input_time=last_t, ext_temp=10.0)
            # With feedforward = 0, behavior should be identical
            assert out1 == out2
            assert pid_with_ff.integral == pid_without_ff.integral
            assert pid_with_ff.external == pid_without_ff.external

    def test_feedforward_property_readonly(self):
        """Test feedforward property is readable."""
        pid = PID(kp=10, ki=1.0, kd=2, out_min=0, out_max=100)

        # Read via property
        assert pid.feedforward == 0.0

        # Set via method
        pid.set_feedforward(12.5)
        assert pid.feedforward == 12.5


class TestIntegralDecayConstants:
    """Test integral decay constants for heating types."""

    def test_integral_decay_constants_exist(self):
        """Verify HEATING_TYPE_INTEGRAL_DECAY dict exists with all heating types."""
        from const import (
            HEATING_TYPE_INTEGRAL_DECAY,
            HEATING_TYPE_FLOOR_HYDRONIC,
            HEATING_TYPE_RADIATOR,
            HEATING_TYPE_CONVECTOR,
            HEATING_TYPE_FORCED_AIR,
        )

        # Dict must exist and have all heating types
        assert isinstance(HEATING_TYPE_INTEGRAL_DECAY, dict)
        assert HEATING_TYPE_FLOOR_HYDRONIC in HEATING_TYPE_INTEGRAL_DECAY
        assert HEATING_TYPE_RADIATOR in HEATING_TYPE_INTEGRAL_DECAY
        assert HEATING_TYPE_CONVECTOR in HEATING_TYPE_INTEGRAL_DECAY
        assert HEATING_TYPE_FORCED_AIR in HEATING_TYPE_INTEGRAL_DECAY

        # All values should be >= 1.0 (multiplier)
        for heating_type, multiplier in HEATING_TYPE_INTEGRAL_DECAY.items():
            assert multiplier >= 1.0, f"{heating_type} should have multiplier >= 1.0"

        # Floor hydronic should have highest decay (slowest system)
        assert HEATING_TYPE_INTEGRAL_DECAY[HEATING_TYPE_FLOOR_HYDRONIC] >= 3.0

    def test_default_integral_decay_exists(self):
        """Verify DEFAULT_INTEGRAL_DECAY = 1.5 exists."""
        from const import DEFAULT_INTEGRAL_DECAY

        assert DEFAULT_INTEGRAL_DECAY == 1.5


class TestAsymmetricIntegralDecay:
    """Test asymmetric integral decay in PID calc() for overhang situations."""

    def test_integral_decay_applied_on_overhang(self):
        """With integral>0, error<0, decay multiplier applied."""
        # Setup: PID with integral_decay_multiplier=3.0, Ki=10
        # Use out_min=-100 to avoid clamping when integral goes negative
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100, integral_decay_multiplier=3.0)

        # Build up positive integral first (multiple calls to build up enough)
        base_time = 1000.0
        pid.calc(19.0, 20.0, input_time=base_time, last_input_time=base_time - 10)  # error=+1.0
        pid.calc(19.0, 20.0, input_time=base_time + 10, last_input_time=base_time)  # error=+1.0
        initial_integral = pid.integral
        assert initial_integral > 0, "Integral should be positive after heating"

        # Now create overhang: temp above setpoint (error < 0)
        # error = 20.0 - 20.5 = -0.5
        # Without decay multiplier: delta_i = Ki * error * dt_hours = 10 * (-0.5) * (10/3600) = -0.0139
        # With decay multiplier 3.0: delta_i = Ki * error * dt_hours * 3.0 = -0.0417
        pid.calc(20.5, 20.0, input_time=base_time + 20, last_input_time=base_time + 10)

        # Verify integral decreased more than it would without multiplier
        delta_i = pid.integral - initial_integral
        expected_without_decay = 10 * (-0.5) * (10 / 3600)  # -0.0139
        expected_with_decay = expected_without_decay * 3.0  # -0.0417

        assert delta_i < 0, "Integral should decrease on overhang"
        # Allow 1% tolerance for floating point
        assert abs(delta_i - expected_with_decay) < abs(expected_with_decay * 0.01), \
            f"Decay should be 3x faster: got {delta_i}, expected {expected_with_decay}"

    def test_integral_decay_not_applied_same_sign(self):
        """With integral>0, error>0, multiplier=1.0 (no extra decay)."""
        pid = PID(kp=0, ki=10, kd=0, out_min=0, out_max=100, integral_decay_multiplier=3.0)

        # Build up positive integral
        base_time = 1000.0
        pid.calc(19.5, 20.0, input_time=base_time, last_input_time=base_time - 10)  # error=+0.5
        initial_integral = pid.integral

        # Continue heating (error still positive) - no decay multiplier
        # error = 20.0 - 19.7 = +0.3
        pid.calc(19.7, 20.0, input_time=base_time + 10, last_input_time=base_time)

        delta_i = pid.integral - initial_integral
        expected_normal = 10 * 0.3 * (10 / 3600)  # Normal accumulation, no multiplier

        assert delta_i > 0, "Integral should increase when error is positive"
        assert abs(delta_i - expected_normal) < abs(expected_normal * 0.01), \
            f"Should not apply decay multiplier: got {delta_i}, expected {expected_normal}"

    def test_integral_decay_negative_overhang(self):
        """With integral<0, error>0, decay multiplier applied."""
        # This handles cooling scenarios where integral is negative
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100, integral_decay_multiplier=2.0)

        # Build up negative integral (cooling scenario)
        base_time = 1000.0
        pid.calc(20.5, 20.0, input_time=base_time, last_input_time=base_time - 10)  # error=-0.5
        initial_integral = pid.integral
        assert initial_integral < 0, "Integral should be negative after cooling"

        # Now undershoot: temp below setpoint (error > 0)
        # error = 20.0 - 19.5 = +0.5
        # With decay multiplier 2.0: integral should increase 2x faster
        pid.calc(19.5, 20.0, input_time=base_time + 10, last_input_time=base_time)

        delta_i = pid.integral - initial_integral
        expected_without_decay = 10 * 0.5 * (10 / 3600)  # +0.0139
        expected_with_decay = expected_without_decay * 2.0  # +0.0278

        assert delta_i > 0, "Integral should increase toward zero on cooling undershoot"
        assert abs(delta_i - expected_with_decay) < abs(expected_with_decay * 0.01), \
            f"Decay should be 2x faster: got {delta_i}, expected {expected_with_decay}"

    def test_integral_decay_default_value(self):
        """Without param, uses default 1.5 multiplier during overhang."""
        # Use out_min=-100 to avoid clamping when integral goes negative
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100)  # default multiplier=1.5

        # Build up positive integral (multiple calls to build up enough)
        base_time = 1000.0
        pid.calc(19.0, 20.0, input_time=base_time, last_input_time=base_time - 10)
        pid.calc(19.0, 20.0, input_time=base_time + 10, last_input_time=base_time)
        initial_integral = pid.integral

        # Create overhang
        pid.calc(20.5, 20.0, input_time=base_time + 20, last_input_time=base_time + 10)

        delta_i = pid.integral - initial_integral
        expected_with_decay = 10 * (-0.5) * (10 / 3600) * 1.5  # default 1.5x decay

        assert abs(delta_i - expected_with_decay) < abs(expected_with_decay * 0.01), \
            f"Should use default 1.5x decay: got {delta_i}, expected {expected_with_decay}"

    def test_integral_decay_zero_integral(self):
        """When integral is zero, no special decay behavior needed."""
        pid = PID(kp=0, ki=10, kd=0, out_min=0, out_max=100, integral_decay_multiplier=3.0)

        # First call - integral will be 0 (first call sets it to 0)
        base_time = 1000.0
        pid.calc(20.0, 20.0, input_time=base_time, last_input_time=None)
        assert pid.integral == 0.0

        # Second call with error - normal accumulation
        pid.calc(19.5, 20.0, input_time=base_time + 10, last_input_time=base_time)
        expected = 10 * 0.5 * (10 / 3600)  # Normal accumulation
        assert abs(pid.integral - expected) < 0.001


class TestIntegralDecayMultiplier:
    """Test integral_decay_multiplier parameter in PID controller."""

    def test_pid_init_accepts_integral_decay_multiplier(self):
        """PID(integral_decay_multiplier=3.0) stores value."""
        pid = PID(kp=10, ki=5, kd=2, out_min=0, out_max=100, integral_decay_multiplier=3.0)
        assert pid._integral_decay_multiplier == 3.0

    def test_pid_integral_decay_default_value(self):
        """PID without integral_decay_multiplier uses default 1.5."""
        pid = PID(kp=10, ki=5, kd=2, out_min=0, out_max=100)
        assert pid._integral_decay_multiplier == 1.5

    def test_pid_integral_decay_property_getter(self):
        """pid.integral_decay_multiplier returns stored value."""
        pid = PID(kp=10, ki=5, kd=2, out_min=0, out_max=100, integral_decay_multiplier=2.5)
        assert pid.integral_decay_multiplier == 2.5

    def test_pid_integral_decay_property_setter(self):
        """Setter enforces minimum 1.0."""
        pid = PID(kp=10, ki=5, kd=2, out_min=0, out_max=100)

        # Setting valid value works
        pid.integral_decay_multiplier = 4.0
        assert pid.integral_decay_multiplier == 4.0

        # Setting value below 1.0 clamps to 1.0
        pid.integral_decay_multiplier = 0.5
        assert pid.integral_decay_multiplier == 1.0

        # Setting exactly 1.0 works
        pid.integral_decay_multiplier = 1.0
        assert pid.integral_decay_multiplier == 1.0


class TestExponentialIntegralDecay:
    """Test exponential integral decay during overhang."""

    def test_exponential_decay_during_overhang(self):
        """Verify integral decays by exp(-dt/tau) during overhang."""
        import math
        tau = 0.18  # 7.5 min half-life (floor hydronic)
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100,
                  integral_decay_multiplier=1.0, integral_exp_decay_tau=tau)

        # Build up positive integral
        base_time = 1000.0
        pid.calc(19.0, 20.0, input_time=base_time, last_input_time=base_time - 10)
        pid.calc(19.0, 20.0, input_time=base_time + 100, last_input_time=base_time)
        pid.calc(19.0, 20.0, input_time=base_time + 200, last_input_time=base_time + 100)

        # Record integral before overhang
        integral_before = pid.integral
        assert integral_before > 0, "Need positive integral for overhang test"

        # Create overhang: temp above setpoint (error < 0)
        # dt = 300s = 300/3600 = 0.0833 hours
        dt_hours = 300.0 / 3600.0
        pid.calc(20.5, 20.0, input_time=base_time + 500, last_input_time=base_time + 200)

        # First the linear decay is applied, then exponential decay
        # Expected integral after linear decay: integral_before + Ki * error * dt * decay_mult
        linear_delta = 10 * (-0.5) * dt_hours * 1.0
        after_linear = integral_before + linear_delta
        # Expected integral after exponential decay: after_linear * exp(-dt/tau)
        expected_integral = after_linear * math.exp(-dt_hours / tau)

        assert abs(pid.integral - expected_integral) < 0.1, \
            f"Expected integral ~{expected_integral}, got {pid.integral}"

    def test_no_exponential_decay_when_not_overhang(self):
        """Integral unchanged by exp decay when error and integral same sign."""
        import math
        tau = 0.18
        pid = PID(kp=0, ki=10, kd=0, out_min=0, out_max=100,
                  integral_decay_multiplier=1.0, integral_exp_decay_tau=tau)

        # Build up positive integral
        base_time = 1000.0
        pid.calc(19.5, 20.0, input_time=base_time, last_input_time=base_time - 10)
        initial_integral = pid.integral

        # Continue heating (error still positive) - no exponential decay
        dt_hours = 10.0 / 3600.0
        pid.calc(19.7, 20.0, input_time=base_time + 10, last_input_time=base_time)

        # Only normal accumulation, no exponential decay
        expected_delta = 10 * 0.3 * dt_hours  # error = 20.0 - 19.7 = 0.3
        expected_integral = initial_integral + expected_delta

        assert abs(pid.integral - expected_integral) < 0.001, \
            f"Expected integral ~{expected_integral}, got {pid.integral}"

    def test_exponential_decay_disabled_when_tau_none(self):
        """With integral_exp_decay_tau=None, no exponential decay applied."""
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100,
                  integral_decay_multiplier=1.0, integral_exp_decay_tau=None)

        # Build up positive integral
        base_time = 1000.0
        pid.calc(19.0, 20.0, input_time=base_time, last_input_time=base_time - 10)
        pid.calc(19.0, 20.0, input_time=base_time + 100, last_input_time=base_time)
        integral_before = pid.integral

        # Create overhang
        dt_hours = 10.0 / 3600.0
        pid.calc(20.5, 20.0, input_time=base_time + 110, last_input_time=base_time + 100)

        # Only linear decay (with multiplier=1.0, so just normal Ki*error*dt)
        expected_delta = 10 * (-0.5) * dt_hours
        expected_integral = integral_before + expected_delta

        assert abs(pid.integral - expected_integral) < 0.01, \
            f"Without exp decay tau, should only have linear: expected ~{expected_integral}, got {pid.integral}"


class TestOutputClampingOnWrongSide:
    """Test output clamping when temperature is on wrong side of setpoint."""

    def test_output_clamped_when_above_setpoint(self):
        """error < 0, integral > 0 → output ≤ 0."""
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100)

        # Build up positive integral (heating history)
        base_time = 1000.0
        for i in range(50):
            t = base_time + i * 100
            pid.calc(18.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        # Integral should be positive (Ki=10, error=2.0, dt=100s=0.0278h → ~0.56 per iter)
        assert pid.integral > 20, f"Expected positive integral, got {pid.integral}"

        # Now temperature is above setpoint (error < 0)
        output, _ = pid.calc(20.5, 20.0, input_time=base_time + 5100, last_input_time=base_time + 5000)

        # Output should be clamped to 0 (no heating when above setpoint)
        assert output <= 0, f"Output should be ≤ 0 when above setpoint, got {output}"

    def test_output_clamped_when_below_setpoint_cooling(self):
        """error > 0, integral < 0 → output ≥ 0."""
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100)

        # Build up negative integral (cooling history)
        base_time = 1000.0
        for i in range(50):
            t = base_time + i * 100
            pid.calc(22.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        # Integral should be negative
        assert pid.integral < -20, f"Expected negative integral, got {pid.integral}"

        # Now temperature is below setpoint (error > 0)
        output, _ = pid.calc(19.5, 20.0, input_time=base_time + 5100, last_input_time=base_time + 5000)

        # Output should be clamped to 0 (no cooling when below setpoint)
        assert output >= 0, f"Output should be ≥ 0 when below setpoint during cooling, got {output}"

    def test_no_clamping_when_on_correct_side(self):
        """Heating: error > 0, integral > 0 → normal output."""
        pid = PID(kp=0, ki=10, kd=0, out_min=0, out_max=100)

        # Build up positive integral
        base_time = 1000.0
        for i in range(10):
            t = base_time + i * 100
            pid.calc(19.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        # Still below setpoint (error > 0) - no clamping
        output, _ = pid.calc(19.5, 20.0, input_time=base_time + 1100, last_input_time=base_time + 1000)

        # Output should be positive (heating demand)
        assert output > 0, f"Output should be > 0 when still below setpoint, got {output}"

    def test_clamping_with_exp_decay_integration(self):
        """Verify output clamping works together with exponential decay."""
        tau = 0.18
        pid = PID(kp=0, ki=10, kd=0, out_min=-100, out_max=100,
                  integral_decay_multiplier=1.0, integral_exp_decay_tau=tau)

        # Build up positive integral
        base_time = 1000.0
        for i in range(30):
            t = base_time + i * 100
            pid.calc(18.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        assert pid.integral > 10, f"Need positive integral, got {pid.integral}"

        # Now above setpoint - should clamp output to 0 AND apply exponential decay
        output, _ = pid.calc(20.3, 20.0, input_time=base_time + 3100, last_input_time=base_time + 3000)

        assert output <= 0, f"Output should be clamped ≤ 0, got {output}"
        # Integral should have decayed but still be positive
        assert pid.integral > 0, "Integral should still be positive after decay"


    def test_should_apply_decay_untuned_excessive_integral_in_tolerance(self):
        """Test should_apply_decay returns True when untuned + excessive integral + within tolerance."""
        # Floor hydronic: threshold=35%, cold_tolerance=0.5°C
        pid = PID(kp=0, ki=100, kd=0, out_min=0, out_max=100,
                  cold_tolerance=0.5, heating_type="floor_hydronic")

        # Build up integral to 40% (above 35% threshold)
        # Ki=100, error=2.0, dt=100s = 2.0 * 100 * (100/3600) = 5.555% per iteration
        # Need ~7 iterations to reach 40%
        base_time = 1000.0
        for i in range(8):
            t = base_time + i * 100
            pid.calc(18.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        # Verify integral is above threshold (35%)
        assert pid.integral > 35.0, f"Integral should be above 35%, got {pid.integral}"

        # Set temperature within cold_tolerance (0.5°C)
        # Setpoint=20.0, tolerance=0.5, so anything >= 19.5 is within tolerance
        pid.calc(19.6, 20.0, input_time=base_time + 2100, last_input_time=base_time + 2000)

        # Should apply decay: untuned (auto_apply_count=0) + excessive integral + within tolerance
        assert pid.should_apply_decay() is True

    def test_should_apply_decay_disabled_after_first_autoapply(self):
        """Test should_apply_decay returns False after first auto-apply (safety net disabled)."""
        pid = PID(kp=0, ki=100, kd=0, out_min=0, out_max=100,
                  cold_tolerance=0.5, heating_type="floor_hydronic")

        # Build up excessive integral
        base_time = 1000.0
        for i in range(8):
            t = base_time + i * 100
            pid.calc(18.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        # Within tolerance
        pid.calc(19.6, 20.0, input_time=base_time + 2100, last_input_time=base_time + 2000)

        # Simulate first auto-apply
        pid.set_auto_apply_count(1)

        # Safety net should be disabled
        assert pid.should_apply_decay() is False

    def test_should_apply_decay_integral_below_threshold(self):
        """Test should_apply_decay returns False when integral below threshold."""
        pid = PID(kp=100, ki=10, kd=0, out_min=0, out_max=100,
                  cold_tolerance=0.5, heating_type="floor_hydronic")

        # Build up small integral (below 35% threshold)
        base_time = 1000.0
        for i in range(5):
            t = base_time + i * 100
            pid.calc(18.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        # Verify integral is below threshold
        assert pid.integral < 35.0, f"Integral should be below 35%, got {pid.integral}"

        # Within tolerance
        pid.calc(19.6, 20.0, input_time=base_time + 600, last_input_time=base_time + 500)

        # Should NOT apply decay: integral not excessive
        assert pid.should_apply_decay() is False

    def test_should_apply_decay_outside_tolerance(self):
        """Test should_apply_decay returns False when error outside tolerance."""
        pid = PID(kp=0, ki=100, kd=0, out_min=0, out_max=100,
                  cold_tolerance=0.5, heating_type="floor_hydronic")

        # Build up excessive integral
        base_time = 1000.0
        for i in range(8):
            t = base_time + i * 100
            pid.calc(18.0, 20.0, input_time=t, last_input_time=t - 100 if i > 0 else None)

        # Verify integral is above threshold
        assert pid.integral > 35.0, f"Integral should be above 35%, got {pid.integral}"

        # Temperature outside cold_tolerance (0.5°C)
        # Setpoint=20.0, tolerance=0.5, so 19.4 is outside (error=0.6 > 0.5)
        pid.calc(19.4, 20.0, input_time=base_time + 2100, last_input_time=base_time + 2000)

        # Should NOT apply decay: outside tolerance zone
        assert pid.should_apply_decay() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
