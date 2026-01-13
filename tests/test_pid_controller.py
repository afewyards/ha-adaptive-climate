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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
