"""Tests for PWMController with valve actuation time support."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from custom_components.adaptive_climate.managers.pwm_controller import PWMController


@pytest.fixture
def mock_thermostat():
    """Create a mock thermostat."""
    thermostat = MagicMock()
    thermostat.entity_id = "climate.test"
    return thermostat


class TestPWMControllerValveActuation:
    """Tests for PWM controller with valve actuation time."""

    def test_pwm_controller_accepts_valve_actuation_time(self, mock_thermostat):
        """PWMController accepts valve_actuation_time parameter."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )
        assert controller is not None

    def test_pwm_controller_defaults_zero_valve_time(self, mock_thermostat):
        """PWMController defaults to zero valve actuation time."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
        )
        assert controller._valve_actuation_time == 0.0

    def test_calculate_adjusted_on_time_no_valve_delay(self, mock_thermostat):
        """With zero valve time, on-time matches base duty cycle."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=0.0,
        )

        time_on = controller.calculate_adjusted_on_time(
            control_output=50,
            difference=100,
        )

        # 50% of 900s = 450s
        assert time_on == 450.0

    def test_calculate_adjusted_on_time_with_valve_delay(self, mock_thermostat):
        """With valve delay, on-time is extended by half valve time."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        time_on = controller.calculate_adjusted_on_time(
            control_output=50,
            difference=100,
        )

        # 50% of 900s = 450s, plus half of 120s = 510s
        assert time_on == 510.0

    def test_calculate_adjusted_on_time_zero_output(self, mock_thermostat):
        """Zero control output produces zero on-time."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        time_on = controller.calculate_adjusted_on_time(
            control_output=0,
            difference=100,
        )

        assert time_on == 0.0

    def test_calculate_adjusted_on_time_zero_difference(self, mock_thermostat):
        """Zero difference produces zero on-time."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        time_on = controller.calculate_adjusted_on_time(
            control_output=50,
            difference=0,
        )

        assert time_on == 0.0

    def test_get_close_command_offset_returns_half_valve_time(self, mock_thermostat):
        """Close command offset is half the valve actuation time."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        offset = controller.get_close_command_offset()
        assert offset == 60.0

    def test_get_close_command_offset_zero_when_no_valve_time(self, mock_thermostat):
        """Close command offset is zero when no valve actuation time."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=0.0,
        )

        offset = controller.get_close_command_offset()
        assert offset == 0.0

    def test_calculate_adjusted_on_time_various_duty_cycles(self, mock_thermostat):
        """Test adjusted on-time calculation for various duty cycles."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        # 25% duty
        time_on = controller.calculate_adjusted_on_time(25, 100)
        assert time_on == 225.0 + 60.0  # 225s + 60s half-valve

        # 75% duty
        time_on = controller.calculate_adjusted_on_time(75, 100)
        assert time_on == 675.0 + 60.0  # 675s + 60s half-valve

        # 100% duty
        time_on = controller.calculate_adjusted_on_time(100, 100)
        assert time_on == 900.0 + 60.0  # 900s + 60s half-valve
