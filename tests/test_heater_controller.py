"""Tests for HeaterController manager."""

from unittest.mock import MagicMock

import pytest


class MockHVACMode:
    """Mock HVACMode for testing."""
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"


# Import and patch the module
import custom_components.adaptive_thermostat.managers.heater_controller as heater_controller_module
heater_controller_module.HVACMode = MockHVACMode

from custom_components.adaptive_thermostat.managers.heater_controller import (
    HeaterController,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.states = MagicMock()
    hass.bus = MagicMock()
    return hass


@pytest.fixture
def mock_thermostat():
    """Create a mock thermostat entity."""
    thermostat = MagicMock()
    thermostat.entity_id = "climate.test_thermostat"
    return thermostat


@pytest.fixture
def heater_controller(mock_hass, mock_thermostat):
    """Create a HeaterController instance."""
    return HeaterController(
        hass=mock_hass,
        thermostat=mock_thermostat,
        heater_entity_id=["switch.heater"],
        cooler_entity_id=None,
        demand_switch_entity_id=None,
        heater_polarity_invert=False,
        pwm=600,  # 10 minutes
        difference=100.0,
        min_on_cycle_duration=300.0,  # 5 minutes
        min_off_cycle_duration=300.0,  # 5 minutes
    )


class TestHeaterControllerSessionTracking:
    """Tests for session tracking state in HeaterController."""

    def test_heater_controller_session_init(self, heater_controller):
        """Test that _heating_session_active initializes to False."""
        assert hasattr(heater_controller, '_heating_session_active')
        assert heater_controller._heating_session_active is False

    @pytest.mark.asyncio
    async def test_session_starts_on_zero_to_positive(self, heater_controller, mock_thermostat):
        """Test that on_heating_started is called when output goes 0→>0."""
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call
        heater_controller._hass.services.async_call = MagicMock()

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Initial state: session not active, control_output = 0
        assert heater_controller._heating_session_active is False

        # Transition: 0 → 50 (should start session)
        await heater_controller.async_set_control_value(
            control_output=50.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=0.0,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify on_heating_started was called
        mock_cycle_tracker.on_heating_started.assert_called_once()
        # Verify session is now active
        assert heater_controller._heating_session_active is True

    @pytest.mark.asyncio
    async def test_session_ends_on_positive_to_zero(self, heater_controller, mock_thermostat):
        """Test that on_heating_stopped is called when output goes >0→0."""
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call
        heater_controller._hass.services.async_call = MagicMock()

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Start with session active (simulate previous heating)
        heater_controller._heating_session_active = True

        # Transition: >0 → 0 (should end session)
        await heater_controller.async_set_control_value(
            control_output=0.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=0.0,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify on_heating_stopped was called
        mock_cycle_tracker.on_heating_stopped.assert_called_once()
        # Verify session is now inactive
        assert heater_controller._heating_session_active is False

    @pytest.mark.asyncio
    async def test_session_no_notify_during_pwm(self, heater_controller, mock_thermostat):
        """Test that no cycle tracker calls are made during PWM pulses (output stays >0)."""
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call and is_active to simulate PWM on/off cycling
        heater_controller._hass.services.async_call = MagicMock()

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Start session with first heating call (0 → 50)
        heater_controller._heating_session_active = False
        await heater_controller.async_set_control_value(
            control_output=50.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=0.0,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify on_heating_started was called once
        assert mock_cycle_tracker.on_heating_started.call_count == 1
        mock_cycle_tracker.reset_mock()

        # Simulate multiple PWM cycles while output stays 50 (>0)
        # During PWM cycling, the heater turns on/off but control_output stays 50
        for _ in range(3):
            await heater_controller.async_set_control_value(
                control_output=50.0,  # Output stays >0
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=0.0,
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        # Verify no additional cycle tracker calls during PWM pulses
        mock_cycle_tracker.on_heating_started.assert_not_called()
        mock_cycle_tracker.on_heating_stopped.assert_not_called()
        # Session should still be active
        assert heater_controller._heating_session_active is True

    @pytest.mark.asyncio
    async def test_turn_on_no_tracker_notify(self, heater_controller, mock_thermostat):
        """Test that async_turn_on does not call on_heating_started (session tracking is in async_set_control_value)."""
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call
        heater_controller._hass.services.async_call = MagicMock()
        heater_controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Call async_turn_on directly (not through async_set_control_value)
        await heater_controller.async_turn_on(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify on_heating_started was NOT called
        # Session tracking happens in async_set_control_value, not here
        mock_cycle_tracker.on_heating_started.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_no_tracker_notify(self, heater_controller, mock_thermostat):
        """Test that async_turn_off does not call on_heating_stopped (session tracking is in async_set_control_value)."""
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call
        heater_controller._hass.services.async_call = MagicMock()
        heater_controller._hass.states.is_state = MagicMock(return_value=True)

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set heater as previously on for cycle count test
        heater_controller._last_heater_state = True

        # Call async_turn_off directly (not through async_set_control_value)
        await heater_controller.async_turn_off(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify on_heating_stopped was NOT called
        # Session tracking happens in async_set_control_value, not here
        mock_cycle_tracker.on_heating_stopped.assert_not_called()
