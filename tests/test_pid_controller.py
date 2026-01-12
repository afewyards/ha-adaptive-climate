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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
