"""Tests for HeaterController manager."""

from datetime import datetime
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
from custom_components.adaptive_thermostat.managers.events import (
    CycleEventDispatcher,
    CycleEventType,
    CycleStartedEvent,
    SettlingStartedEvent,
    HeatingStartedEvent,
    HeatingEndedEvent,
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
        """Test that _cycle_active initializes to False."""
        assert hasattr(heater_controller, '_cycle_active')
        assert heater_controller._cycle_active is False

    @pytest.mark.asyncio
    async def test_session_starts_on_zero_to_positive(self, heater_controller, mock_thermostat):
        """Test that _has_demand becomes True when demand goes 0→>0.

        Note: _cycle_active only becomes True when the heater actually turns on.
        _has_demand tracks whether control_output > 0.
        """
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call to be async
        async def mock_async_call(*args, **kwargs):
            pass
        heater_controller._hass.services.async_call = mock_async_call

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Initial state: no demand, cycle not active
        assert heater_controller._cycle_active is False
        assert heater_controller._has_demand is False

        # Transition: 0 → 50 (should set _has_demand = True but not _cycle_active)
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

        # on_heating_started NOT called here - it's called in async_turn_on when device activates
        mock_cycle_tracker.on_heating_started.assert_not_called()
        # Verify demand tracking is active
        assert heater_controller._has_demand is True
        # _cycle_active only becomes True when heater actually turns on
        # (which requires proper mocking of the turn-on flow)

    @pytest.mark.asyncio
    async def test_session_ends_on_positive_to_zero(self, heater_controller, mock_thermostat):
        """Test that session tracking updates when output goes >0→0 (no direct cycle_tracker calls)."""
        # Setup mock cycle tracker (but HC should NOT call it)
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call to be async
        async def mock_async_call(*args, **kwargs):
            pass
        heater_controller._hass.services.async_call = mock_async_call

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Start with cycle active and demand active (simulate previous heating)
        heater_controller._cycle_active = True
        heater_controller._has_demand = True

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

        # Verify HC does NOT call cycle_tracker directly
        mock_cycle_tracker.on_heating_session_ended.assert_not_called()
        # Verify session is now inactive (internal tracking still works)
        assert heater_controller._cycle_active is False
        assert heater_controller._has_demand is False

    @pytest.mark.asyncio
    async def test_session_no_notify_during_pwm(self, heater_controller, mock_thermostat):
        """Test that no session end calls are made during PWM pulses (demand stays >0).

        Note: _has_demand tracks whether control_output > 0.
        _cycle_active only becomes True when heater actually turns on.
        This test verifies session end is not called while demand remains active during PWM cycling.
        """
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call to be async
        async def mock_async_call(*args, **kwargs):
            pass
        heater_controller._hass.services.async_call = mock_async_call

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Start session with first heating call (0 → 50)
        heater_controller._cycle_active = False
        heater_controller._has_demand = False
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

        # on_heating_started NOT called here - demand tracking only
        mock_cycle_tracker.on_heating_started.assert_not_called()
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

        # Verify no session end calls during PWM pulses (demand stays active)
        mock_cycle_tracker.on_heating_session_ended.assert_not_called()
        # Demand should still be active
        assert heater_controller._has_demand is True

    @pytest.mark.asyncio
    async def test_turn_on_no_direct_tracker_notify(self, heater_controller, mock_thermostat):
        """Test that async_turn_on does NOT call cycle_tracker directly (uses events instead)."""
        # Setup mock cycle tracker (but HC should NOT call it)
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call (make it an async coroutine)
        async def async_mock(*args, **kwargs):
            pass
        heater_controller._hass.services.async_call = async_mock
        heater_controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Call async_turn_on directly
        await heater_controller.async_turn_on(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify HC does NOT call cycle_tracker directly
        mock_cycle_tracker.on_heating_started.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_no_tracker_notify(self, heater_controller, mock_thermostat):
        """Test that async_turn_off does not call on_heating_session_ended (session tracking is in async_set_control_value)."""
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

        # Verify on_heating_session_ended was NOT called
        # Session tracking happens in async_set_control_value, not here
        mock_cycle_tracker.on_heating_session_ended.assert_not_called()

    @pytest.mark.asyncio
    async def test_100_percent_duty_starts_session(self, heater_controller, mock_thermostat):
        """Test that 100% duty cycle sets _has_demand active when output goes 0→100.

        Note: _cycle_active only becomes True when heater actually turns on.
        _has_demand tracks whether control_output > 0.
        """
        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call to be async
        async def mock_async_call(*args, **kwargs):
            pass
        heater_controller._hass.services.async_call = mock_async_call

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Initial state: session not active, control_output = 0
        assert heater_controller._cycle_active is False
        assert heater_controller._has_demand is False

        # Transition: 0 → 100 (max duty, should set _has_demand = True)
        await heater_controller.async_set_control_value(
            control_output=100.0,
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

        # on_heating_started NOT called here - demand tracking only
        mock_cycle_tracker.on_heating_started.assert_not_called()
        # Verify demand is now active
        assert heater_controller._has_demand is True

    @pytest.mark.asyncio
    async def test_0_percent_duty_ends_session(self, heater_controller, mock_thermostat):
        """Test that 0% duty cycle ends session internally (no direct cycle_tracker calls)."""
        # Setup mock cycle tracker (but HC should NOT call it)
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call to be async
        async def mock_async_call(*args, **kwargs):
            pass
        heater_controller._hass.services.async_call = mock_async_call

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Start with cycle active and demand active (simulate previous heating)
        heater_controller._cycle_active = True
        heater_controller._has_demand = True

        # Transition: >0 → 0 (0% duty, should end session)
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

        # Verify HC does NOT call cycle_tracker directly
        mock_cycle_tracker.on_heating_session_ended.assert_not_called()
        # Verify session is now inactive (internal tracking still works)
        assert heater_controller._cycle_active is False
        assert heater_controller._has_demand is False


class TestHeaterControllerPWMThreshold:
    """Tests for PWM minimum on-time threshold behavior."""

    @pytest.mark.asyncio
    async def test_pwm_skip_tiny_output(self, heater_controller, mock_thermostat):
        """Test that PWM skips activation when on-time < min_on_cycle_duration.

        With fixture config: pwm=600s, min_on=300s → threshold = 50%
        Output 25% → on-time = 150s < 300s → should NOT turn on
        """
        import time as time_module

        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call
        heater_controller._hass.services.async_call = MagicMock()
        # Device starts OFF
        heater_controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # time_changed = current time (simulates we just started a new PWM period)
        current_time = time_module.time()

        # Apply small control output (25%)
        # With pwm=600, this gives time_on = 600 * 25 / 100 = 150s
        # Since 150s < min_on=300s, it should skip activation
        await heater_controller.async_set_control_value(
            control_output=25.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=current_time,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify turn_on was NOT called (the heater should stay off)
        # Check that no service call was made with "turn_on"
        for call in heater_controller._hass.services.async_call.call_args_list:
            args, kwargs = call
            assert args[1] != "turn_on", "Device should not turn on with tiny output"

    @pytest.mark.asyncio
    async def test_pwm_activates_above_threshold(self, heater_controller, mock_thermostat):
        """Test that PWM activates normally when on-time >= min_on_cycle_duration.

        With fixture config: pwm=600s, min_on=300s → threshold = 50%
        Output 60% → on-time = 360s >= 300s → should turn on
        """
        import time as time_module

        # Setup mock cycle tracker
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock the service call
        heater_controller._hass.services.async_call = MagicMock()
        # Device starts OFF, then simulate time_off has passed
        heater_controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock required callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Simulate off-time has passed (force PWM to turn on)
        # time_changed was 10 minutes ago (600s), so time_off has definitely passed
        time_changed = time_module.time() - 600

        # Apply 60% output
        # With pwm=600, this gives time_on = 360s >= min_on=300s
        await heater_controller.async_set_control_value(
            control_output=60.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time_changed,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify turn_on WAS called
        turn_on_called = False
        for call in heater_controller._hass.services.async_call.call_args_list:
            args, kwargs = call
            if args[1] == "turn_on":
                turn_on_called = True
                break
        assert turn_on_called, "Device should turn on with output above threshold"


class TestHeaterControllerBackwardCompatibility:
    """Tests for backward compatibility without dispatcher or cycle tracker."""

    @pytest.mark.asyncio
    async def test_hc_no_direct_ctm_calls(self, heater_controller, mock_thermostat):
        """Test HC doesn't access self._thermostat._cycle_tracker directly."""
        # Setup mock cycle tracker on thermostat
        mock_cycle_tracker = MagicMock()
        mock_thermostat._cycle_tracker = mock_cycle_tracker

        # Mock service calls and state
        heater_controller._hass.services.async_call = MagicMock()
        heater_controller._hass.states.is_state = MagicMock(return_value=False)
        heater_controller._hass.states.get = MagicMock(return_value=MagicMock(state="0"))

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Call all methods that previously accessed _cycle_tracker
        # 1. async_turn_on
        await heater_controller.async_turn_on(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # 2. async_set_valve_value (off->on transition)
        await heater_controller.async_set_valve_value(50.0, MockHVACMode.HEAT)

        # 3. async_set_valve_value (on->off transition)
        heater_controller._hass.states.get = MagicMock(return_value=MagicMock(state="50"))
        heater_controller._last_heater_state = True
        await heater_controller.async_set_valve_value(0.0, MockHVACMode.HEAT)

        # 4. async_set_control_value (demand 0->positive)
        heater_controller._cycle_active = False
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

        # 5. async_set_control_value (demand positive->0)
        heater_controller._cycle_active = True
        heater_controller._has_demand = True
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

        # Verify cycle tracker was NEVER accessed
        mock_cycle_tracker.on_heating_started.assert_not_called()
        mock_cycle_tracker.on_cooling_started.assert_not_called()
        mock_cycle_tracker.on_heating_session_ended.assert_not_called()
        mock_cycle_tracker.on_cooling_session_ended.assert_not_called()

    @pytest.mark.asyncio
    async def test_hc_works_without_dispatcher(self, mock_hass, mock_thermostat):
        """Test HC functions correctly when dispatcher is None (backwards compatibility)."""
        # Create controller WITHOUT dispatcher
        controller = HeaterController(
            hass=mock_hass,
            thermostat=mock_thermostat,
            heater_entity_id=["switch.heater"],
            cooler_entity_id=None,
            demand_switch_entity_id=None,
            heater_polarity_invert=False,
            pwm=600,
            difference=100.0,
            min_on_cycle_duration=300.0,
            min_off_cycle_duration=300.0,
            dispatcher=None,  # Explicitly None
        )

        # Verify dispatcher is None
        assert controller._dispatcher is None

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=False)
        controller._hass.states.get = MagicMock(return_value=MagicMock(state="0"))

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Test all operations work without dispatcher
        # 1. async_turn_on should work
        await controller.async_turn_on(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # 2. async_turn_off should work
        controller._hass.states.is_state = MagicMock(return_value=True)
        controller._last_heater_state = True
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # 3. async_set_valve_value should work
        controller._hass.states.get = MagicMock(return_value=MagicMock(state="0"))
        await controller.async_set_valve_value(50.0, MockHVACMode.HEAT)

        # 4. async_set_control_value should work
        await controller.async_set_control_value(
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

        # Verify demand tracking still works internally
        assert controller._has_demand is True

        # All operations completed without errors - success!


class TestHeaterControllerEventEmission:
    """Tests for event emission in HeaterController."""

    @pytest.fixture
    def heater_controller_with_dispatcher(self, mock_hass, mock_thermostat):
        """Create a HeaterController with event dispatcher."""
        dispatcher = CycleEventDispatcher()
        controller = HeaterController(
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
            dispatcher=dispatcher,
        )
        return controller, dispatcher

    @pytest.fixture
    def heater_controller_valve(self, mock_hass, mock_thermostat):
        """Create a HeaterController for valve mode (pwm=0)."""
        dispatcher = CycleEventDispatcher()
        controller = HeaterController(
            hass=mock_hass,
            thermostat=mock_thermostat,
            heater_entity_id=["number.valve"],
            cooler_entity_id=None,
            demand_switch_entity_id=None,
            heater_polarity_invert=False,
            pwm=0,  # Valve mode
            difference=100.0,
            min_on_cycle_duration=0.0,
            min_off_cycle_duration=0.0,
            dispatcher=dispatcher,
        )
        return controller, dispatcher

    def test_hc_accepts_dispatcher(self, heater_controller_with_dispatcher):
        """Test HC.__init__ accepts optional dispatcher parameter."""
        controller, dispatcher = heater_controller_with_dispatcher
        assert hasattr(controller, '_dispatcher')
        assert controller._dispatcher is dispatcher

    def test_hc_tracks_cycle_active(self, heater_controller_with_dispatcher):
        """Test HC tracks _cycle_active bool for demand state."""
        controller, _ = heater_controller_with_dispatcher
        # Should be renamed from _cycle_active to _cycle_active
        assert hasattr(controller, '_cycle_active')
        assert controller._cycle_active is False

    @pytest.mark.asyncio
    async def test_hc_emits_cycle_started(self, heater_controller_with_dispatcher, mock_thermostat):
        """Test that CYCLE_STARTED is emitted when heater actually turns on."""
        controller, dispatcher = heater_controller_with_dispatcher

        # Setup event listener
        events = []
        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, lambda e: events.append(e))

        # Setup thermostat state
        mock_thermostat.hvac_mode = MockHVACMode.HEAT
        mock_thermostat.target_temperature = 21.0
        mock_thermostat._cur_temp = 19.5

        # Mock service calls to be async
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call

        # Mock device state as OFF (so turn_on will actually turn it on)
        controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set demand active (simulates async_set_control_value having been called with output > 0)
        controller._has_demand = True

        # Turn on heater (should emit CYCLE_STARTED)
        await controller.async_turn_on(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify CYCLE_STARTED event was emitted
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, CycleStartedEvent)
        assert event.hvac_mode == MockHVACMode.HEAT
        assert event.target_temp == 21.0
        assert event.current_temp == 19.5
        # Verify _cycle_active is now True
        assert controller._cycle_active is True

    @pytest.mark.asyncio
    async def test_hc_emits_settling_started_pwm(self, heater_controller_with_dispatcher):
        """Test demand >0→0 emits SETTLING_STARTED for PWM mode."""
        controller, dispatcher = heater_controller_with_dispatcher

        # Setup event listener
        events = []
        dispatcher.subscribe(CycleEventType.SETTLING_STARTED, lambda e: events.append(e))

        # Mock service calls to be async
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Start with cycle active and demand active
        controller._cycle_active = True
        controller._has_demand = True

        # Transition: >0 → 0 (should emit SETTLING_STARTED)
        await controller.async_set_control_value(
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

        # Verify event was emitted
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, SettlingStartedEvent)
        assert event.hvac_mode == MockHVACMode.HEAT

    @pytest.mark.asyncio
    async def test_hc_emits_settling_started_valve(self, heater_controller_valve, mock_thermostat):
        """Test valve demand <5% + temp tolerance emits SETTLING_STARTED."""
        controller, dispatcher = heater_controller_valve

        # Setup event listener
        events = []
        dispatcher.subscribe(CycleEventType.SETTLING_STARTED, lambda e: events.append(e))

        # Setup thermostat state - within tolerance
        mock_thermostat.target_temperature = 21.0
        mock_thermostat._cur_temp = 20.7  # Within 0.5°C

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.get = MagicMock(return_value=MagicMock(state="50"))

        # Start with cycle active and demand >5%
        controller._cycle_active = True

        # Transition: demand goes to 3% (<5%) AND temp within tolerance
        await controller.async_set_valve_value(3.0, MockHVACMode.HEAT)

        # Verify event was emitted
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, SettlingStartedEvent)
        assert event.hvac_mode == MockHVACMode.HEAT

    @pytest.mark.asyncio
    async def test_hc_emits_heating_started(self, heater_controller_with_dispatcher):
        """Test device ON emits HEATING_STARTED."""
        controller, dispatcher = heater_controller_with_dispatcher

        # Setup event listener
        events = []
        dispatcher.subscribe(CycleEventType.HEATING_STARTED, lambda e: events.append(e))

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Turn on device
        await controller.async_turn_on(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify event was emitted
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, HeatingStartedEvent)
        assert event.hvac_mode == MockHVACMode.HEAT

    @pytest.mark.asyncio
    async def test_hc_emits_heating_ended(self, heater_controller_with_dispatcher):
        """Test device OFF emits HEATING_ENDED."""
        controller, dispatcher = heater_controller_with_dispatcher

        # Setup event listener
        events = []
        dispatcher.subscribe(CycleEventType.HEATING_ENDED, lambda e: events.append(e))

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=True)

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state for cycle count
        controller._last_heater_state = True

        # Turn off device
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify event was emitted
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, HeatingEndedEvent)
        assert event.hvac_mode == MockHVACMode.HEAT

    @pytest.mark.asyncio
    async def test_hc_emits_heating_events_pwm(self, heater_controller_with_dispatcher):
        """Test PWM cycling emits HEATING_STARTED/ENDED each toggle."""
        controller, dispatcher = heater_controller_with_dispatcher

        # Setup event listeners
        started_events = []
        ended_events = []
        dispatcher.subscribe(CycleEventType.HEATING_STARTED, lambda e: started_events.append(e))
        dispatcher.subscribe(CycleEventType.HEATING_ENDED, lambda e: ended_events.append(e))

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        import time
        # Simulate PWM cycle: device OFF → ON
        controller._hass.states.is_state = MagicMock(return_value=False)
        time_changed = time.time() - 600  # Force turn on

        await controller.async_pwm_switch(
            control_output=50.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time_changed,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Should emit HEATING_STARTED when turning on
        assert len(started_events) == 1

        # Simulate PWM cycle: device ON → OFF
        controller._hass.states.is_state = MagicMock(return_value=True)
        controller._last_heater_state = True
        time_changed = time.time() - 600  # Force turn off

        await controller.async_pwm_switch(
            control_output=50.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time_changed,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Should emit HEATING_ENDED when turning off
        assert len(ended_events) == 1

    @pytest.mark.asyncio
    async def test_hc_emits_heating_events_valve(self, heater_controller_valve):
        """Test valve transitions emit events."""
        controller, dispatcher = heater_controller_valve

        # Setup event listeners
        started_events = []
        ended_events = []
        dispatcher.subscribe(CycleEventType.HEATING_STARTED, lambda e: started_events.append(e))
        dispatcher.subscribe(CycleEventType.HEATING_ENDED, lambda e: ended_events.append(e))

        # Mock service calls
        controller._hass.services.async_call = MagicMock()

        # Start with valve closed (0)
        controller._hass.states.get = MagicMock(return_value=MagicMock(state="0"))

        # Transition: 0 → 50 (should emit HEATING_STARTED)
        await controller.async_set_valve_value(50.0, MockHVACMode.HEAT)
        assert len(started_events) == 1

        # Transition: 50 → 0 (should emit HEATING_ENDED)
        controller._hass.states.get = MagicMock(return_value=MagicMock(state="50"))
        controller._last_heater_state = True
        await controller.async_set_valve_value(0.0, MockHVACMode.HEAT)
        assert len(ended_events) == 1
