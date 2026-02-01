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
        """With valve delay, on-time is actuator_time + max(heat_duration, min_on_cycle)."""
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

        # 50% of 900s = 450s, plus full actuator time of 120s = 570s
        assert time_on == 570.0

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
        assert time_on == 225.0 + 120.0  # 225s heat + 120s actuator

        # 75% duty
        time_on = controller.calculate_adjusted_on_time(75, 100)
        assert time_on == 675.0 + 120.0  # 675s heat + 120s actuator

        # 100% duty
        time_on = controller.calculate_adjusted_on_time(100, 100)
        assert time_on == 900.0 + 120.0  # 900s heat + 120s actuator

    def test_calculate_adjusted_on_time_respects_min_on_cycle(self, mock_thermostat):
        """Short duty cycles are extended to min_on_cycle_duration."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=300,  # 5 minutes minimum
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        # Very small duty (5% = 45s) should be extended to min_on_cycle
        time_on = controller.calculate_adjusted_on_time(5, 100)
        assert time_on == 300.0 + 120.0  # min_on_cycle + actuator

        # Medium duty (20% = 180s) should be extended to min_on_cycle
        time_on = controller.calculate_adjusted_on_time(20, 100)
        assert time_on == 300.0 + 120.0  # min_on_cycle + actuator

        # Large duty (50% = 450s) exceeds min, so use calculated
        time_on = controller.calculate_adjusted_on_time(50, 100)
        assert time_on == 450.0 + 120.0  # calculated heat + actuator

    def test_set_transport_delay_updates_internal_state(self, mock_thermostat):
        """set_transport_delay updates internal transport delay."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        controller.set_transport_delay(180.0)
        assert controller._transport_delay == 180.0

    def test_calculate_adjusted_on_time_includes_transport_delay(self, mock_thermostat):
        """Adjusted on-time includes transport delay + valve time + heat duration."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        # Set transport delay (manifold pipes cold)
        controller.set_transport_delay(180.0)

        time_on = controller.calculate_adjusted_on_time(
            control_output=50,
            difference=100,
        )

        # 50% of 900s = 450s heat
        # + 180s transport delay
        # + 120s valve actuation
        # = 750s total
        assert time_on == 750.0

    def test_calculate_adjusted_on_time_zero_transport_delay(self, mock_thermostat):
        """Zero transport delay is ignored in calculation."""
        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        # No transport delay set (manifold already warm)
        controller.set_transport_delay(0.0)

        time_on = controller.calculate_adjusted_on_time(
            control_output=50,
            difference=100,
        )

        # 50% of 900s = 450s heat + 120s valve = 570s
        assert time_on == 570.0


@pytest.mark.asyncio
class TestPWMControllerEarlyValveClose:
    """Tests for early valve close command timing."""

    async def test_early_valve_close_with_valve_actuation_time(self, mock_thermostat):
        """Close command sent early by half valve actuation time."""
        import time
        from homeassistant.components.climate import HVACMode

        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        # Mock heater controller
        heater_controller = MagicMock()
        heater_controller.is_active = MagicMock(return_value=True)
        heater_controller.get_entities = MagicMock(return_value=["switch.heater"])
        heater_controller.async_turn_off = AsyncMock()
        heater_controller.async_turn_on = AsyncMock()

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=time.monotonic())
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Control output 50% → 450s heat + 120s valve = 570s total on-time
        # Close command should be sent at 570s - 60s = 510s
        control_output = 50.0
        time_changed = time.monotonic()

        # Simulate time passing to exactly when close command should be sent
        # At 510 seconds, close command should fire
        await controller.async_pwm_switch(
            control_output=control_output,
            hvac_mode=HVACMode.HEAT,
            heater_controller=heater_controller,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time_changed - 510.0,  # 510 seconds have passed
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify close command was sent
        heater_controller.async_turn_off.assert_called_once()

    async def test_early_valve_close_no_actuation_time(self, mock_thermostat):
        """Without valve actuation time, close command at normal time."""
        import time
        from homeassistant.components.climate import HVACMode

        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=0.0,
        )

        # Mock heater controller
        heater_controller = MagicMock()
        heater_controller.is_active = MagicMock(return_value=True)
        heater_controller.get_entities = MagicMock(return_value=["switch.heater"])
        heater_controller.async_turn_off = AsyncMock()
        heater_controller.async_turn_on = AsyncMock()

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=time.monotonic())
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Control output 50% → 450s (no valve delay)
        # Close command should be sent at 450s (no early offset)
        control_output = 50.0
        time_changed = time.monotonic()

        # Simulate time passing to exactly 450 seconds
        await controller.async_pwm_switch(
            control_output=control_output,
            hvac_mode=HVACMode.HEAT,
            heater_controller=heater_controller,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time_changed - 450.0,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify close command was sent
        heater_controller.async_turn_off.assert_called_once()

    async def test_early_valve_close_still_heating_before_offset(self, mock_thermostat):
        """Before close command offset, heater remains on."""
        import time
        from homeassistant.components.climate import HVACMode

        controller = PWMController(
            thermostat=mock_thermostat,
            pwm_duration=900,
            difference=100,
            min_on_cycle_duration=0,
            min_off_cycle_duration=0,
            valve_actuation_time=120.0,
        )

        # Mock heater controller
        heater_controller = MagicMock()
        heater_controller.is_active = MagicMock(return_value=True)
        heater_controller.get_entities = MagicMock(return_value=["switch.heater"])
        heater_controller.async_turn_off = AsyncMock()
        heater_controller.async_turn_on = AsyncMock()

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=time.monotonic())
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Control output 50% → 450s heat + 120s valve = 570s total
        # Close command at 510s
        # At 500s (before close command), heater should stay on
        control_output = 50.0
        time_changed = time.monotonic()

        await controller.async_pwm_switch(
            control_output=control_output,
            hvac_mode=HVACMode.HEAT,
            heater_controller=heater_controller,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time_changed - 500.0,  # 500s < 510s close time
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify heater stayed on (turn_on called, not turn_off)
        heater_controller.async_turn_on.assert_called_once()
        heater_controller.async_turn_off.assert_not_called()
