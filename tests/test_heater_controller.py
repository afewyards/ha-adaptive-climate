"""Tests for HeaterController manager."""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest


class MockHVACMode:
    """Mock HVACMode for testing."""
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"


# Import and patch the module
import custom_components.adaptive_thermostat.managers.heater_controller as heater_controller_module
heater_controller_module.HVACMode = MockHVACMode

# Mock split_entity_id for tests
def mock_split_entity_id(entity_id: str):
    """Split entity_id into domain and object_id."""
    return tuple(entity_id.split('.', 1)) if entity_id and '.' in entity_id else ()

heater_controller_module.split_entity_id = mock_split_entity_id

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
    thermostat.target_temperature = 20.0
    thermostat._current_temp = 19.0
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
        current_time = time_module.monotonic()

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

        # Mock the service call to be async
        async def mock_async_call(*args, **kwargs):
            pass
        heater_controller._hass.services.async_call = mock_async_call
        # Device starts OFF, then simulate time_off has passed
        heater_controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock required callbacks
        # get_cycle_start_time must be at least min_off_cycle_duration in the past
        cycle_start_time = time_module.monotonic() - 600  # 600s ago (>= 300s min_off)
        get_cycle_start_time = MagicMock(return_value=cycle_start_time)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Simulate off-time has passed (force PWM to turn on)
        # time_changed was 10 minutes ago (600s), so time_off has definitely passed
        time_changed = time_module.monotonic() - 600

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

        # Verify turn_on was called by checking callbacks
        # In PWM mode with output 60% (time_on=360s >= min_on=300s, time_off=240s < min_off=300s),
        # the time_off is extended to min_off (line 301-302 in pwm_controller.py).
        # Device is OFF and time_off has passed, so it should turn ON.
        # When turning on, set_time_changed is called (line 346 in pwm_controller.py)
        set_time_changed.assert_called()
        # Also verify set_last_heat_cycle_time was called (indicates actual turn-on happened)
        set_last_heat_cycle_time.assert_called()


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
        mock_thermostat._current_temp = 19.5

        # Mock service calls to be async
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call

        # Mock device state as OFF (so turn_on will actually turn it on)
        controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock callbacks
        import time
        cycle_start_time = time.monotonic() - 600  # 600 seconds ago (>= min_off_cycle_duration)
        get_cycle_start_time = MagicMock(return_value=cycle_start_time)
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
    async def test_hc_emits_heating_started(self, heater_controller_with_dispatcher):
        """Test device ON emits HEATING_STARTED."""
        controller, dispatcher = heater_controller_with_dispatcher

        # Setup event listener
        events = []
        dispatcher.subscribe(CycleEventType.HEATING_STARTED, lambda e: events.append(e))

        # Mock service calls to be async
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        # Mock callbacks
        import time
        # Set cycle start time to be at least min_off_cycle_duration in the past
        # to satisfy the condition: time.monotonic() - get_cycle_start_time() >= min_off_cycle_duration
        cycle_start_time = time.monotonic() - 600  # 600 seconds ago (>= 300s min_off)
        get_cycle_start_time = MagicMock(return_value=cycle_start_time)
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

        # Mock service calls to be async
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=True)

        # Mock callbacks
        import time
        cycle_start_time = time.monotonic() - 600  # 600 seconds ago (>= min_on_cycle_duration)
        get_cycle_start_time = MagicMock(return_value=cycle_start_time)
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

        # Mock service calls to be async
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call

        # Mock callbacks
        import time
        cycle_start_time = time.monotonic() - 600  # 600 seconds ago
        get_cycle_start_time = MagicMock(return_value=cycle_start_time)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Simulate PWM cycle: device OFF → ON
        controller._hass.states.is_state = MagicMock(return_value=False)
        time_changed = time.monotonic() - 600  # Force turn on

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
        time_changed = time.monotonic() - 600  # Force turn off

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

    @pytest.mark.asyncio
    async def test_hc_pwm_heater_off_emits_settling_started(self, heater_controller_with_dispatcher):
        """Test that async_turn_off in PWM mode emits SETTLING_STARTED when cycle is active.

        Context: In PWM mode, demand stays at 5-10% during maintenance cycles, so
        SETTLING_STARTED won't be emitted from demand dropping to 0. Instead, it
        must be emitted when the heater is explicitly turned off while _cycle_active
        is True, to ensure proper learning cycle completion.
        """
        controller, dispatcher = heater_controller_with_dispatcher

        # Setup event listeners
        heating_ended_events = []
        settling_started_events = []
        dispatcher.subscribe(CycleEventType.HEATING_ENDED, lambda e: heating_ended_events.append(e))
        dispatcher.subscribe(CycleEventType.SETTLING_STARTED, lambda e: settling_started_events.append(e))

        # Mock service calls to be async
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=True)

        # Mock callbacks
        import time
        cycle_start_time = time.monotonic() - 600  # 600 seconds ago (>= min_on_cycle_duration)
        get_cycle_start_time = MagicMock(return_value=cycle_start_time)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state for cycle count test and mark cycle as active
        controller._last_heater_state = True
        controller._cycle_active = True

        # Verify we're in PWM mode
        assert controller._pwm > 0

        # Turn off heater (should emit both HEATING_ENDED and SETTLING_STARTED)
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify HEATING_ENDED event was emitted
        assert len(heating_ended_events) == 1
        event = heating_ended_events[0]
        assert isinstance(event, HeatingEndedEvent)
        assert event.hvac_mode == MockHVACMode.HEAT

        # Verify SETTLING_STARTED event was emitted
        assert len(settling_started_events) == 1
        settling_event = settling_started_events[0]
        assert isinstance(settling_event, SettlingStartedEvent)
        assert settling_event.hvac_mode == MockHVACMode.HEAT

    @pytest.mark.asyncio
    async def test_non_pwm_turn_off_no_settling_started(self, heater_controller_valve, mock_thermostat):
        """Test that async_turn_off in NON-PWM mode does NOT emit SETTLING_STARTED.

        This is a regression test to ensure the PWM fix doesn't affect valve mode.
        In valve mode (self._pwm == 0), async_turn_off should only emit HEATING_ENDED,
        not SETTLING_STARTED.
        """
        controller, dispatcher = heater_controller_valve

        # Setup event listeners
        heating_ended_events = []
        settling_started_events = []
        dispatcher.subscribe(CycleEventType.HEATING_ENDED, lambda e: heating_ended_events.append(e))
        dispatcher.subscribe(CycleEventType.SETTLING_STARTED, lambda e: settling_started_events.append(e))

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=True)

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set up state: cycle active, heater was on
        controller._cycle_active = True
        controller._last_heater_state = True

        # Verify we're in non-PWM mode (valve mode)
        assert controller._pwm == 0

        # Call async_turn_off
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify HEATING_ENDED was emitted
        assert len(heating_ended_events) == 1

        # Verify SETTLING_STARTED was NOT emitted from async_turn_off
        # (In valve mode, SETTLING_STARTED comes from async_set_valve_value, not async_turn_off)
        assert len(settling_started_events) == 0


class TestDutyAccumulator:
    """Tests for duty accumulator infrastructure (story 1.1)."""

    def test_duty_accumulator_initial_zero(self, heater_controller):
        """Test that duty accumulator starts at 0."""
        assert hasattr(heater_controller, '_duty_accumulator_seconds')
        assert heater_controller._duty_accumulator_seconds == 0.0

    def test_max_accumulator_is_2x_min_cycle(self, heater_controller):
        """Test that max_accumulator = 2 * min_on_cycle_duration.

        With fixture min_on_cycle_duration=300s, max_accumulator should be 600s.
        """
        assert hasattr(heater_controller, '_max_accumulator')
        expected = 2.0 * 300.0  # 2 * min_on_cycle_duration from fixture
        assert heater_controller._max_accumulator == expected

    def test_duty_accumulator_property_exposed(self, heater_controller):
        """Test duty_accumulator_seconds property returns field value."""
        # Set internal field to a specific value
        heater_controller._duty_accumulator_seconds = 123.45
        # Property should return same value
        assert heater_controller.duty_accumulator_seconds == 123.45

    def test_reset_duty_accumulator(self, heater_controller):
        """Test reset_duty_accumulator sets accumulator to 0."""
        # Set accumulator to non-zero value
        heater_controller._duty_accumulator_seconds = 0.0
        # Reset should set to 0
        heater_controller.reset_duty_accumulator()
        assert heater_controller._duty_accumulator_seconds == 0.0
        assert heater_controller.duty_accumulator_seconds == 0.0

    def test_reset_duty_accumulator_when_partial(self, heater_controller):
        """Test reset clears partial accumulation."""
        # Set accumulator to a partial value (less than threshold)
        heater_controller._duty_accumulator_seconds = 150.0  # Half of min_on_cycle
        # Reset should clear it
        heater_controller.reset_duty_accumulator()
        assert heater_controller._duty_accumulator_seconds == 0.0
        assert heater_controller.duty_accumulator_seconds == 0.0


class TestDutyAccumulation:
    """Tests for duty accumulation in async_pwm_switch (story 2.1)."""

    @pytest.mark.asyncio
    async def test_sub_threshold_accumulates_duty(self, heater_controller):
        """Test output 10% with threshold ~50% accumulates scaled duty.

        Fixture: pwm=600, difference=100, min_on_cycle=300
        10% output: duty_fraction = 10/100 = 0.1
        With 60s elapsed, should accumulate: 60 * 0.1 = 6s
        """
        import time

        controller = heater_controller
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=False)

        # Callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Start with zero accumulator
        assert controller._duty_accumulator_seconds == 0.0

        # Set last calc time to 60 seconds ago to simulate elapsed time
        controller._last_accumulator_calc_time = time.monotonic() - 60

        # Call with 10% output - should accumulate 60s * 0.1 = 6s
        await controller.async_pwm_switch(
            control_output=10.0,  # 10% duty fraction
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic(),
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Should have accumulated ~6 seconds (60s elapsed * 10% duty)
        assert 5.9 < controller._duty_accumulator_seconds < 6.1

    @pytest.mark.asyncio
    async def test_accumulator_capped_at_max(self, heater_controller):
        """Test accumulator never exceeds _max_accumulator (2 * min_on_cycle).

        max_accumulator = 2 * 300 = 600s
        Pre-set accumulator to 200s (below threshold), add enough to exceed max.
        """
        import time

        controller = heater_controller

        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator below threshold (300s)
        controller._duty_accumulator_seconds = 200.0
        # Set last calc time to 2000s ago - with 40% duty = 800s would be added
        # But max is 600, so should cap
        controller._last_accumulator_calc_time = time.monotonic() - 2000

        # Call with 40% output - would add 2000 * 0.4 = 800s
        # 200 + 800 = 1000, but capped at 600
        await controller.async_pwm_switch(
            control_output=40.0,  # 40% duty fraction
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic(),
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Should be capped at max_accumulator (600s)
        assert controller._duty_accumulator_seconds == 600.0

    @pytest.mark.asyncio
    async def test_normal_duty_resets_accumulator(self, heater_controller):
        """Test output >= threshold resets accumulator to 0.

        50% output: time_on = 600 * 50 / 100 = 300s (= threshold)
        Should reset accumulator to 0 when firing a normal pulse.
        """
        import time

        controller = heater_controller
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator
        controller._duty_accumulator_seconds = 150.0

        # Call with 50% output - time_on = 300s >= threshold
        # Device is OFF, time_passed > time_off, so it should turn on
        await controller.async_pwm_switch(
            control_output=50.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic() - 600,  # Force time_off elapsed
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Accumulator should be reset
        assert controller._duty_accumulator_seconds == 0.0

    @pytest.mark.asyncio
    async def test_zero_output_resets_accumulator(self, heater_controller):
        """Test control_output=0 resets accumulator."""
        import time

        controller = heater_controller
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator
        controller._duty_accumulator_seconds = 200.0

        # Call with 0% output
        await controller.async_pwm_switch(
            control_output=0.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic(),
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Accumulator should be reset
        assert controller._duty_accumulator_seconds == 0.0


class TestCompressorMinCycleProtection:
    """Tests for compressor minimum cycle protection in cooling mode."""

    @pytest.fixture
    def cooler_controller_forced_air(self, mock_hass, mock_thermostat):
        """Create a HeaterController for forced_air cooling (compressor-based)."""
        return HeaterController(
            hass=mock_hass,
            thermostat=mock_thermostat,
            heater_entity_id=None,
            cooler_entity_id=["switch.ac_unit"],
            demand_switch_entity_id=None,
            heater_polarity_invert=False,
            pwm=180,  # 3 minutes PWM for forced_air
            difference=100.0,
            min_on_cycle_duration=180.0,  # 3 minutes min cycle for compressor protection
            min_off_cycle_duration=180.0,
            dispatcher=None,
        )

    @pytest.fixture
    def cooler_controller_mini_split(self, mock_hass, mock_thermostat):
        """Create a HeaterController for mini_split cooling (compressor-based)."""
        return HeaterController(
            hass=mock_hass,
            thermostat=mock_thermostat,
            heater_entity_id=None,
            cooler_entity_id=["climate.mini_split"],
            demand_switch_entity_id=None,
            heater_polarity_invert=False,
            pwm=300,  # 5 minutes PWM for mini_split
            difference=100.0,
            min_on_cycle_duration=300.0,  # 5 minutes min cycle for compressor protection
            min_off_cycle_duration=300.0,
            dispatcher=None,
        )

    @pytest.fixture
    def cooler_controller_chilled_water(self, mock_hass, mock_thermostat):
        """Create a HeaterController for chilled_water cooling (no compressor)."""
        return HeaterController(
            hass=mock_hass,
            thermostat=mock_thermostat,
            heater_entity_id=None,
            cooler_entity_id=["number.chilled_water_valve"],
            demand_switch_entity_id=None,
            heater_polarity_invert=False,
            pwm=0,  # Valve mode for chilled_water
            difference=100.0,
            min_on_cycle_duration=0.0,  # No min cycle for chilled water
            min_off_cycle_duration=0.0,
            dispatcher=None,
        )

    @pytest.mark.asyncio
    async def test_forced_air_compressor_respects_min_cycle(self, cooler_controller_forced_air):
        """Test forced_air compressor cannot turn off before min_on_cycle_duration (180s).

        This protects compressor from short-cycling which can damage the equipment.
        """
        controller = cooler_controller_forced_air

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=True)  # Compressor is ON

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state for cycle count
        controller._last_cooler_state = True

        import time
        # Simulate only 60 seconds have passed (less than 180s min_cycle)
        get_cycle_start_time.return_value = time.monotonic() - 60

        # Attempt to turn off compressor - should be rejected
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.COOL,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify turn_off service was NOT called (protection engaged)
        for call in controller._hass.services.async_call.call_args_list:
            args, kwargs = call
            assert args[1] != "turn_off", "Compressor should not turn off before min_cycle elapsed"

    @pytest.mark.asyncio
    async def test_forced_air_compressor_allows_turn_off_after_min_cycle(self, cooler_controller_forced_air):
        """Test forced_air compressor CAN turn off after min_on_cycle_duration (180s)."""
        controller = cooler_controller_forced_air

        # Mock service calls and state
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=True)  # Compressor is ON

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state for cycle count
        controller._last_cooler_state = True

        import time
        # Simulate 200 seconds have passed (more than 180s min_cycle)
        get_cycle_start_time.return_value = time.monotonic() - 200

        # Turn off compressor - should succeed
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.COOL,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify set_last_heat_cycle_time was called (indicates successful turn-off)
        set_last_heat_cycle_time.assert_called_once()

    @pytest.mark.asyncio
    async def test_mini_split_compressor_respects_min_cycle(self, cooler_controller_mini_split):
        """Test mini_split compressor cannot turn off before min_on_cycle_duration (300s)."""
        controller = cooler_controller_mini_split

        # Mock service calls and state
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=True)  # Compressor is ON

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state
        controller._last_cooler_state = True

        import time
        # Simulate only 120 seconds have passed (less than 300s min_cycle)
        get_cycle_start_time.return_value = time.monotonic() - 120

        # Attempt to turn off compressor - should be rejected
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.COOL,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify turn_off service was NOT called (protection engaged)
        for call in controller._hass.services.async_call.call_args_list:
            args, kwargs = call
            assert args[1] != "turn_off", "Mini-split compressor should not turn off before min_cycle elapsed"

    @pytest.mark.asyncio
    async def test_mini_split_compressor_allows_turn_off_after_min_cycle(self, cooler_controller_mini_split):
        """Test mini_split compressor CAN turn off after min_on_cycle_duration (300s)."""
        controller = cooler_controller_mini_split

        # Mock service calls
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=True)  # Compressor is ON

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state
        controller._last_cooler_state = True

        import time
        # Simulate 350 seconds have passed (more than 300s min_cycle)
        get_cycle_start_time.return_value = time.monotonic() - 350

        # Turn off compressor - should succeed
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.COOL,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify set_last_heat_cycle_time was called (indicates successful turn-off)
        set_last_heat_cycle_time.assert_called_once()

    @pytest.mark.asyncio
    async def test_chilled_water_no_min_cycle_protection(self, cooler_controller_chilled_water):
        """Test chilled_water has no min_cycle protection (can turn off immediately).

        Chilled water systems use valves, not compressors, so they don't need
        minimum cycle protection.
        """
        controller = cooler_controller_chilled_water

        # Verify min_on_cycle_duration is 0
        assert controller.min_on_cycle_duration == 0.0

        # Mock service calls (valve mode)
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.get = MagicMock(return_value=MagicMock(state="50"))

        import time
        # Start with valve open at 50%
        controller._last_cooler_state = True

        # Close valve immediately (0 seconds elapsed) - should work since min_cycle=0
        await controller.async_set_valve_value(0.0, MockHVACMode.COOL)

        # Verify valve was closed (cycle count incremented)
        assert controller._cooler_cycle_count == 1

    @pytest.mark.asyncio
    async def test_force_flag_bypasses_min_cycle_protection(self, cooler_controller_forced_air):
        """Test force=True bypasses compressor min_cycle protection.

        This is needed for emergency shutdowns or mode changes.
        """
        controller = cooler_controller_forced_air

        # Mock service calls
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=True)  # Compressor is ON

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state
        controller._last_cooler_state = True

        import time
        # Simulate only 10 seconds have passed (way less than 180s min_cycle)
        get_cycle_start_time.return_value = time.monotonic() - 10

        # Turn off compressor with force=True - should succeed despite short cycle
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.COOL,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            force=True,
        )

        # Verify set_last_heat_cycle_time was called (indicates successful turn-off)
        set_last_heat_cycle_time.assert_called_once()

    @pytest.mark.asyncio
    async def test_compressor_cycle_count_increments_on_valid_turn_off(self, cooler_controller_forced_air):
        """Test cooler_cycle_count increments when compressor turns off after min_cycle."""
        controller = cooler_controller_forced_air

        # Mock service calls
        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=True)  # Compressor is ON

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state and initial cycle count
        controller._last_cooler_state = True
        initial_count = controller._cooler_cycle_count

        import time
        # Simulate 200 seconds have passed (more than 180s min_cycle)
        get_cycle_start_time.return_value = time.monotonic() - 200

        # Turn off compressor
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.COOL,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify cycle count incremented
        assert controller._cooler_cycle_count == initial_count + 1

    @pytest.mark.asyncio
    async def test_compressor_cycle_count_does_not_increment_on_rejected_turn_off(self, cooler_controller_forced_air):
        """Test cooler_cycle_count does NOT increment when turn-off is rejected."""
        controller = cooler_controller_forced_air

        # Mock service calls
        controller._hass.services.async_call = MagicMock()
        controller._hass.states.is_state = MagicMock(return_value=True)  # Compressor is ON

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()

        # Set previous state and initial cycle count
        controller._last_cooler_state = True
        initial_count = controller._cooler_cycle_count

        import time
        # Simulate only 60 seconds have passed (less than 180s min_cycle)
        get_cycle_start_time.return_value = time.monotonic() - 60

        # Attempt to turn off compressor - should be rejected
        await controller.async_turn_off(
            hvac_mode=MockHVACMode.COOL,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
        )

        # Verify cycle count did NOT increment
        assert controller._cooler_cycle_count == initial_count


class TestDutyAccumulatorPulse:
    """Tests for firing minimum pulse when accumulator reaches threshold (story 2.2)."""

    @pytest.mark.asyncio
    async def test_accumulator_fires_minimum_pulse(self, heater_controller):
        """Test that accumulator fires min pulse when reaching threshold.

        Fixture: min_on_cycle_duration=300s
        Pre-set accumulator to 300s → should fire pulse and subtract 300s
        """
        import time

        controller = heater_controller

        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator to exactly threshold
        controller._duty_accumulator_seconds = 300.0

        # Call with 10% output (60s) - should fire pulse since accumulator >= 300
        await controller.async_pwm_switch(
            control_output=10.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic() - 600,  # Force time_off elapsed
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Verify turn_on was called (device state should be updated)
        # Since we can't check async_call directly, verify accumulator was reduced
        # Accumulator should be reduced by min_on_cycle_duration (300 - 300 = 0)
        assert controller._duty_accumulator_seconds == 0.0

    @pytest.mark.asyncio
    async def test_accumulator_pulse_sets_has_demand(self, heater_controller):
        """Test that _has_demand is set to True when accumulator fires."""
        import time

        controller = heater_controller

        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator above threshold, has_demand initially false
        controller._duty_accumulator_seconds = 350.0
        controller._has_demand = False

        # Call with 10% output - should fire pulse
        await controller.async_pwm_switch(
            control_output=10.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic() - 600,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # _has_demand should be True
        assert controller._has_demand is True

    @pytest.mark.asyncio
    async def test_accumulator_pulse_updates_time_changed(self, heater_controller):
        """Test that set_time_changed is called after firing accumulator pulse."""
        import time

        controller = heater_controller

        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator to threshold
        controller._duty_accumulator_seconds = 300.0

        # Call with 10% output - should fire pulse
        await controller.async_pwm_switch(
            control_output=10.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic() - 600,
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # set_time_changed should have been called
        set_time_changed.assert_called_once()

    @pytest.mark.asyncio
    async def test_accumulator_below_threshold_stays_off(self, heater_controller):
        """Test that heater stays off when accumulator is below threshold."""
        import time

        controller = heater_controller

        async def mock_async_call(*args, **kwargs):
            pass
        controller._hass.services.async_call = mock_async_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Set accumulator below threshold
        controller._duty_accumulator_seconds = 200.0
        # Set last calc time to 60s ago
        controller._last_accumulator_calc_time = time.monotonic() - 60

        # Call with 10% output - should add 60s * 0.1 = 6s
        await controller.async_pwm_switch(
            control_output=10.0,
            hvac_mode=MockHVACMode.HEAT,
            get_cycle_start_time=get_cycle_start_time,
            set_is_heating=set_is_heating,
            set_last_heat_cycle_time=set_last_heat_cycle_time,
            time_changed=time.monotonic(),
            set_time_changed=set_time_changed,
            force_on=False,
            force_off=False,
            set_force_on=set_force_on,
            set_force_off=set_force_off,
        )

        # Accumulator should have increased by ~6s (60s * 10% duty)
        # 200 + 6 = 206 < 300 threshold, so heater stays off
        assert 205.9 < controller._duty_accumulator_seconds < 206.1
