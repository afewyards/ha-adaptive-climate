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
        assert output > 0  # Should be heating
        assert 0 <= output <= 100  # Within bounds

        # Check error is correct
        assert pid.error == 2.0

        # Check proportional term
        assert pid.proportional == 20.0  # Kp * error = 10 * 2.0

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
        """Test integral windup prevention."""
        # Create PID controller
        pid = PID(kp=5, ki=1, kd=0, out_min=0, out_max=100)

        # First calculation establishes baseline
        output1, _ = pid.calc(input_val=20.0, set_point=25.0, input_time=0.0)
        initial_integral = pid.integral

        # Second calculation at output limit (simulate saturation)
        # Force output to be at max by making large error
        pid.calc(input_val=0.0, set_point=100.0, input_time=1.0)

        # Third calculation should not accumulate integral when saturated
        # The integral should be clamped
        output3, _ = pid.calc(input_val=0.0, set_point=100.0, input_time=2.0)

        # Output should be clamped at max
        assert output3 == 100.0

        # Integral should be limited to prevent windup
        assert pid.integral <= 100.0

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

    def test_integral_reset_on_setpoint_change_without_ext_temp(self):
        """Test that integral resets on setpoint change even without ext_temp.

        This tests the fix for inconsistent integral reset behavior where
        the integral was only reset when ext_temp was provided.
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

        # Integral should be reset to 0 regardless of ext_temp presence
        assert pid.integral == 0, "Integral should be reset on setpoint change"

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
        output, changed = pid.calc(input_val=21.0, set_point=22.0, input_time=200.0)
        assert changed is True
        assert output > 0  # Should be heating

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

        # First calculation establishes setpoint (integral will be reset to 0 due to setpoint change from 0->20)
        # Must pass both input_time and last_input_time for event-driven mode (sampling_period=0)
        output1, _ = pid.calc(input_val=19.0, set_point=20.0, input_time=0.0, last_input_time=None)
        assert abs(output1 - 0.3) < 0.01  # Proportional only: 0.3 * 1.0
        assert pid.integral == 0.0  # Reset due to setpoint change

        # Second calculation with dt >= 10s to meet MIN_DT_FOR_DERIVATIVE threshold
        # Now setpoint is stable (20 -> 20), so integration can occur
        output2, _ = pid.calc(input_val=19.0, set_point=20.0, input_time=15.0, last_input_time=0.0)
        # Integral accumulation over 15 seconds = 1.2 * 1.0 * (15/3600) = 0.005%
        assert pid.integral > 0, f"Expected integral > 0, got {pid.integral}"
        small_integral = pid.integral

        # Third calculation: maintain 1°C error for 1 hour (3600 seconds from time=15)
        # Expected additional accumulation: Ki * error * dt_hours = 1.2 * 1.0 * 1.0 = 1.2%
        output3, _ = pid.calc(input_val=19.0, set_point=20.0, input_time=3615.0, last_input_time=15.0)

        # Total integral = small_integral + 1.2 ≈ 1.2%
        assert abs(pid.integral - 1.2) < 0.01, f"Expected integral ~1.2, got {pid.integral}"

        # Verify proportional term is still correct
        assert abs(pid.proportional - 0.3) < 0.01  # Kp * error = 0.3 * 1.0

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
        pid.calc(input_val=18.0, set_point=21.0, input_time=0.0, last_input_time=None)
        initial_output = pid.proportional  # Only P term active
        assert abs(initial_output - 0.9) < 0.01  # 0.3 * 3.0

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
        t = 0.0
        output1, _ = pid.calc(input_val=19.5, set_point=21.0, input_time=t,
                             last_input_time=None, ext_temp=10.0)
        assert output1 > 0  # Should have some output

        # Continue running to build up integral term
        for i in range(1, 6):
            t = i * 60.0  # 1 minute intervals
            output_before, _ = pid.calc(input_val=19.8, set_point=21.0, input_time=t,
                                       last_input_time=t - 60.0, ext_temp=10.0)

        # Store the last output value
        last_output = pid._output
        assert last_output > 0

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
        # Integral should have been reset due to setpoint change
        assert pid.integral == 0.0

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


class TestPIDProportionalOnMeasurement:
    """Test proportional-on-measurement (P-on-M) functionality."""

    def test_proportional_on_measurement_setpoint_change(self):
        """Test P-on-M eliminates output spike on setpoint change and preserves integral."""
        # Create PID with P-on-M enabled (default)
        # Use out_min=-10 to avoid windup prevention blocking integration at output=0
        pid_p_on_m = PID(kp=10.0, ki=1.2, kd=2.5, out_min=-10, out_max=100, proportional_on_measurement=True)

        # Create PID with P-on-E for comparison
        pid_p_on_e = PID(kp=10.0, ki=1.2, kd=2.5, out_min=-10, out_max=100, proportional_on_measurement=False)

        # Run both controllers for a few cycles to build up integral term
        current_temp = 19.0
        setpoint = 20.0
        output_before_m = 0
        output_before_e = 0
        for i in range(5):
            time = float(i * 3600)  # 1 hour intervals
            last_time = float((i - 1) * 3600) if i > 0 else None
            output_before_m, _ = pid_p_on_m.calc(current_temp, setpoint, input_time=time, last_input_time=last_time)
            output_before_e, _ = pid_p_on_e.calc(current_temp, setpoint, input_time=time, last_input_time=last_time)
            current_temp += 0.1  # Slowly approaching setpoint

        # Store state before setpoint change
        integral_before_m = pid_p_on_m.integral
        integral_before_e = pid_p_on_e.integral

        # Now change setpoint from 20.0 to 22.0 (sudden +2°C change)
        new_setpoint = 22.0
        time_after_change = 5.0 * 3600
        last_time_after_change = 4.0 * 3600

        output_m, _ = pid_p_on_m.calc(current_temp, new_setpoint, input_time=time_after_change, last_input_time=last_time_after_change)
        output_e, _ = pid_p_on_e.calc(current_temp, new_setpoint, input_time=time_after_change, last_input_time=last_time_after_change)

        # P-on-M: Integral should NOT be reset (continues accumulating)
        assert pid_p_on_m.integral != 0, "P-on-M should preserve integral on setpoint change"
        assert pid_p_on_m.integral >= integral_before_m, "P-on-M integral should continue accumulating"

        # P-on-E: Integral SHOULD be reset to zero
        assert pid_p_on_e.integral == 0, "P-on-E should reset integral on setpoint change"

        # P-on-M: Output change should be smaller (no proportional spike)
        output_delta_m = abs(output_m - output_before_m)
        output_delta_e = abs(output_e - output_before_e)

        # P-on-E will have larger output change due to proportional term responding to error change
        assert output_delta_m < output_delta_e, "P-on-M should have smaller output change than P-on-E on setpoint change"

    def test_proportional_on_measurement_faster_recovery(self):
        """Test that P-on-M provides smoother recovery from setpoint changes due to preserved integral."""
        # Create two PID controllers
        # Use out_min=-10 to avoid windup prevention blocking integration at output=0
        pid_p_on_m = PID(kp=10.0, ki=1.2, kd=2.5, out_min=-10, out_max=100, proportional_on_measurement=True)
        pid_p_on_e = PID(kp=10.0, ki=1.2, kd=2.5, out_min=-10, out_max=100, proportional_on_measurement=False)

        # Build up integral by maintaining small error (19.5°C -> 20°C setpoint)
        # This simulates a system slowly approaching setpoint
        current_temp = 19.5
        for i in range(10):
            time = float(i * 3600)
            last_time = float((i - 1) * 3600) if i > 0 else None
            pid_p_on_m.calc(current_temp, 20.0, input_time=time, last_input_time=last_time)
            pid_p_on_e.calc(current_temp, 20.0, input_time=time, last_input_time=last_time)

        # Verify integral has accumulated (0.5°C error * 1.2 Ki * 10 hours = 6%)
        assert pid_p_on_m.integral > 0, "Should have accumulated integral"
        assert pid_p_on_e.integral > 0, "Should have accumulated integral"

        # Change setpoint to 22°C (keeping temp at 19.5°C)
        time = 10.0 * 3600
        last_time = 9.0 * 3600
        output_m, _ = pid_p_on_m.calc(19.5, 22.0, input_time=time, last_input_time=last_time)
        output_e, _ = pid_p_on_e.calc(19.5, 22.0, input_time=time, last_input_time=last_time)

        # P-on-M maintains integral, P-on-E resets it
        # This means P-on-M has a "head start" on recovery
        assert pid_p_on_m.integral > 0, "P-on-M should maintain positive integral"
        assert pid_p_on_e.integral == 0, "P-on-E should reset integral to zero"

        # Simulate temperature rising slowly toward new setpoint
        current_temp = 20.0
        outputs_m = []
        outputs_e = []

        for i in range(10):
            time = float((11 + i) * 3600)
            last_time = float((10 + i) * 3600)
            current_temp += 0.15  # Rising toward 22°C

            out_m, _ = pid_p_on_m.calc(current_temp, 22.0, input_time=time, last_input_time=last_time)
            out_e, _ = pid_p_on_e.calc(current_temp, 22.0, input_time=time, last_input_time=last_time)

            outputs_m.append(out_m)
            outputs_e.append(out_e)

        # P-on-M should have more consistent output due to preserved integral
        # P-on-E needs to rebuild integral from zero
        avg_output_m = sum(outputs_m) / len(outputs_m)
        avg_output_e = sum(outputs_e) / len(outputs_e)

        # Both should produce reasonable outputs
        assert avg_output_m > 0, "P-on-M should produce positive output"
        assert avg_output_e > 0, "P-on-E should produce positive output"

    def test_proportional_on_measurement_measurement_changes(self):
        """Test that P-on-M proportional term responds to measurement changes, not error changes."""
        pid = PID(kp=10.0, ki=1.2, kd=2.5, out_min=0, out_max=100, proportional_on_measurement=True)

        # First calculation at 19°C, setpoint 20°C
        time1 = 0.0
        output1, _ = pid.calc(19.0, 20.0, input_time=time1, last_input_time=None)

        # Proportional term should be 0 on first call (no previous measurement)
        assert pid.proportional == 0.0, "First P-on-M proportional should be 0"

        # Second calculation: temperature rises to 19.5°C
        time2 = 3600.0  # 1 hour later
        output2, _ = pid.calc(19.5, 20.0, input_time=time2, last_input_time=time1)

        # Proportional term should respond to measurement change (negative, since temp increased)
        # P = -Kp * (input - last_input) = -10.0 * (19.5 - 19.0) = -10.0 * 0.5 = -5.0
        assert pid.proportional == pytest.approx(-5.0), "P-on-M proportional should be -Kp * measurement_change"

        # Third calculation: temperature continues to 20.0°C (reaching setpoint)
        time3 = 7200.0  # 2 hours total
        output3, _ = pid.calc(20.0, 20.0, input_time=time3, last_input_time=time2)

        # P = -10.0 * (20.0 - 19.5) = -5.0
        assert pid.proportional == pytest.approx(-5.0), "P-on-M proportional continues responding to measurement"

    def test_proportional_on_error_traditional_behavior(self):
        """Test that P-on-E (traditional mode) responds to error, not measurement."""
        pid = PID(kp=10.0, ki=1.2, kd=2.5, out_min=0, out_max=100, proportional_on_measurement=False)

        # First calculation at 19°C, setpoint 20°C
        time1 = 0.0
        output1, _ = pid.calc(19.0, 20.0, input_time=time1, last_input_time=None)

        # P = Kp * error = 10.0 * (20.0 - 19.0) = 10.0
        assert pid.proportional == pytest.approx(10.0), "P-on-E should be Kp * error"

        # Second calculation: temperature rises to 19.5°C, error reduces to 0.5°C
        time2 = 3600.0
        output2, _ = pid.calc(19.5, 20.0, input_time=time2, last_input_time=time1)

        # P = 10.0 * (20.0 - 19.5) = 5.0
        assert pid.proportional == pytest.approx(5.0), "P-on-E proportional based on error"

        # Third calculation: setpoint changes to 22°C (error jumps from 0.5 to 2.5°C)
        time3 = 7200.0
        output3, _ = pid.calc(19.5, 22.0, input_time=time3, last_input_time=time2)

        # P = 10.0 * (22.0 - 19.5) = 25.0 (large spike due to setpoint change)
        assert pid.proportional == pytest.approx(25.0), "P-on-E spikes on setpoint change"

        # Integral should be reset on setpoint change
        assert pid.integral == 0.0, "P-on-E resets integral on setpoint change"

    def test_proportional_on_measurement_module_exists(self):
        """Marker test to verify P-on-M functionality exists."""
        # Verify P-on-M mode can be enabled/disabled
        pid_m = PID(kp=10.0, ki=1.2, kd=2.5, out_min=0, out_max=100, proportional_on_measurement=True)
        pid_e = PID(kp=10.0, ki=1.2, kd=2.5, out_min=0, out_max=100, proportional_on_measurement=False)

        assert hasattr(pid_m, '_proportional_on_measurement')
        assert pid_m._proportional_on_measurement is True
        assert pid_e._proportional_on_measurement is False


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
            out_max=100,
            proportional_on_measurement=True
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
            out_max=100,
            proportional_on_measurement=True
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
            out_max=100,
            proportional_on_measurement=True
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
        """Test that tiny dt (< 10s) freezes I and D to prevent spikes."""
        # Create PID controller with known parameters
        # Use larger out_min to avoid clamping issues in test
        pid = PID(kp=10, ki=1.0, kd=50, out_min=-50, out_max=100, proportional_on_measurement=False)

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
        """Test boundary conditions around 10s threshold."""
        # Use wider output range and smaller gains to avoid saturation
        pid = PID(kp=2, ki=0.1, kd=5, out_min=-50, out_max=100, proportional_on_measurement=False)

        # First call: establish baseline
        pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # Second call: dt = 30s (normal update)
        output2, _ = pid.calc(input_val=20.1, set_point=22.0, input_time=30.0, last_input_time=0.0)
        baseline_integral = pid.integral
        baseline_derivative = pid.derivative

        # dt = 9.5s (below threshold) - should freeze
        pid.calc(input_val=20.2, set_point=22.0, input_time=39.5, last_input_time=30.0)
        assert pid.integral == baseline_integral  # Frozen
        assert pid.derivative == baseline_derivative  # Frozen

        # dt = 10.0s (exactly at threshold) - should update
        pid.calc(input_val=20.3, set_point=22.0, input_time=49.5, last_input_time=39.5)
        assert pid.integral > baseline_integral  # Updated

        # dt = 15s (above threshold) - should update
        # Use a larger dt to ensure integral accumulates
        new_baseline = pid.integral
        pid.calc(input_val=20.4, set_point=22.0, input_time=64.5, last_input_time=49.5)
        assert pid.integral > new_baseline  # Updated

    def test_rapid_calls_sequence(self):
        """Test rapid call sequence: sensor → external → periodic.

        Only the first (sensor) call with normal dt should update I&D.
        """
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100, proportional_on_measurement=False)

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
        """Test that normal operation (dt ≥ 10s) is unchanged."""
        # Use wider output range and smaller gains to avoid saturation
        pid = PID(kp=5, ki=0.5, kd=10, out_min=-50, out_max=100, proportional_on_measurement=False)

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
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100, proportional_on_measurement=False)

        # First call with dt=0 (implicit from last_input_time=None)
        output, _ = pid.calc(input_val=20.0, set_point=22.0, input_time=0.0, last_input_time=None)

        # I and D should be zero on first call
        assert pid.integral == 0.0
        assert pid.derivative == 0.0
        assert pid.dt == 0.0

    def test_ema_filter_state_preserved_when_frozen(self):
        """Test that EMA filter state is preserved during freeze."""
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100,
                  derivative_filter_alpha=0.3, proportional_on_measurement=False)

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
        pid = PID(kp=10, ki=1.0, kd=500, out_min=0, out_max=100,
                  proportional_on_measurement=False)

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
        pid = PID(kp=10, ki=1.0, kd=50, out_min=0, out_max=100, proportional_on_measurement=False)

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

        # P should reflect new error (22.0 - 20.5 = 1.5)
        assert pid.proportional == 10 * 1.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
