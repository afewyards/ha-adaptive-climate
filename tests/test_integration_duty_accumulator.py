"""Integration tests for duty accumulator end-to-end behavior.

This module tests the complete duty accumulator flow, simulating realistic
scenarios where sub-threshold outputs are accumulated and fired as minimum
pulses, including restart continuity and event emission.
"""

from __future__ import annotations

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


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
    thermostat.target_temperature = 21.0
    thermostat._cur_temp = 19.0
    return thermostat


@pytest.fixture
def dispatcher():
    """Create a CycleEventDispatcher for testing."""
    return CycleEventDispatcher()


@pytest.fixture
def heater_controller_with_dispatcher(mock_hass, mock_thermostat, dispatcher):
    """Create a HeaterController instance with event dispatcher."""
    return HeaterController(
        hass=mock_hass,
        thermostat=mock_thermostat,
        heater_entity_id=["switch.heater"],
        cooler_entity_id=None,
        demand_switch_entity_id=None,
        heater_polarity_invert=False,
        pwm=600,  # 10 minutes PWM period
        difference=100.0,
        min_on_cycle_duration=75.0,  # 75s = 12.5% of 600s PWM period
        min_off_cycle_duration=75.0,
        dispatcher=dispatcher,
    )


class TestAccumulatorFiresAfterMultiplePeriods:
    """Test accumulator fires after multiple PWM periods of sub-threshold output."""

    @pytest.mark.asyncio
    async def test_accumulator_fires_after_multiple_periods(
        self, heater_controller_with_dispatcher, dispatcher
    ):
        """Test 10% output fires minimum pulse after ~1.25 PWM periods.

        Configuration:
        - PWM period: 600s (10 min)
        - min_on_cycle_duration: 75s (12.5% threshold)
        - 10% output: time_on = 60s per period (< 75s threshold)

        Expected behavior:
        - Period 1: accumulate 60s (total: 60s, < 75s, stay off)
        - Period 2: check 60s >= 75s? NO, accumulate 60s (total: 120s)
          Then check 120s >= 75s? YES, fire pulse, subtract 75s (total: 45s)

        Note: Due to PWM timing logic, the accumulator check happens at the start
        of each call to async_pwm_switch().
        """
        controller = heater_controller_with_dispatcher

        # Track service calls
        service_calls = []

        async def track_service_call(domain, service, data):
            service_calls.append((domain, service, data))
        controller._hass.services.async_call = track_service_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        # Track events
        emitted_events = []
        dispatcher.subscribe(CycleEventType.HEATING_STARTED, lambda e: emitted_events.append(e))

        # Mock callbacks
        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Initial state
        assert controller._duty_accumulator_seconds == 0.0

        # Set baseline calc time (600s ago to simulate elapsed time)
        controller._last_accumulator_calc_time = 0.0

        # Simulate PWM period 1: 10% output (time_on = 60s < 75s threshold)
        # With 600s elapsed and 10% duty, accumulates: 600 * 0.1 = 60s
        with patch('time.monotonic', return_value=600.0):  # Enough time passed for PWM
            await controller.async_pwm_switch(
                control_output=10.0,  # 10% of 100 = 10, time_on = 600 * 10 / 100 = 60s
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

        # After period 1: accumulator = 60s (< 75s threshold, heater stays off)
        assert controller._duty_accumulator_seconds == 60.0

        # Heater should still be off (turn_off called, not turn_on)
        assert all(call[1] == "turn_off" for call in service_calls)
        service_calls.clear()

        # Simulate PWM period 2: another 10% output
        with patch('time.monotonic', return_value=1200.0):
            await controller.async_pwm_switch(
                control_output=10.0,
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=600.0,  # Previous time_changed
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        # After check at start of period 2: 60s >= 75s? NO
        # Then accumulate: 60 + 60 = 120s
        # Then check: 120s >= 75s? YES - would fire on NEXT call
        # But since check happens at START, accumulator = 60 + 60 = 120s
        assert controller._duty_accumulator_seconds == 120.0

        # Period 3: Now 120s >= 75s at check, so it fires and subtracts 75s
        service_calls.clear()
        with patch('time.monotonic', return_value=1800.0):
            await controller.async_pwm_switch(
                control_output=10.0,
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=1200.0,
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        # Check 120s >= 75s? YES - fire pulse, subtract 75s -> 45s
        assert controller._duty_accumulator_seconds == 45.0

        # Heater should have turned ON
        assert any(call[1] == "turn_on" for call in service_calls)

        # set_time_changed should have been called
        set_time_changed.assert_called()


class TestAccumulatorRestartContinuity:
    """Test accumulator resumes after simulated restart."""

    @pytest.mark.asyncio
    async def test_accumulator_restart_continuity(
        self, mock_hass, mock_thermostat, dispatcher
    ):
        """Test that accumulator value persists across restart simulation.

        Simulates:
        1. Controller accumulates duty
        2. Value is saved (via state attributes)
        3. New controller is created with restored value
        4. Accumulator continues from saved state
        """
        # Phase 1: Create first controller and accumulate duty
        controller1 = HeaterController(
            hass=mock_hass,
            thermostat=mock_thermostat,
            heater_entity_id=["switch.heater"],
            cooler_entity_id=None,
            demand_switch_entity_id=None,
            heater_polarity_invert=False,
            pwm=600,
            difference=100.0,
            min_on_cycle_duration=75.0,
            min_off_cycle_duration=75.0,
            dispatcher=dispatcher,
        )

        async def mock_async_call(*args, **kwargs):
            pass
        controller1._hass.services.async_call = mock_async_call
        controller1._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Set baseline calc time
        controller1._last_accumulator_calc_time = 0.0

        # Accumulate some duty (10% output for 600s = 60s)
        with patch('time.monotonic', return_value=600.0):
            await controller1.async_pwm_switch(
                control_output=10.0,
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

        # Save the accumulator value (simulates state persistence)
        saved_accumulator = controller1.duty_accumulator_seconds
        assert saved_accumulator == 60.0

        # Phase 2: Simulate restart - create new controller
        controller2 = HeaterController(
            hass=mock_hass,
            thermostat=mock_thermostat,
            heater_entity_id=["switch.heater"],
            cooler_entity_id=None,
            demand_switch_entity_id=None,
            heater_polarity_invert=False,
            pwm=600,
            difference=100.0,
            min_on_cycle_duration=75.0,
            min_off_cycle_duration=75.0,
            dispatcher=dispatcher,
        )

        # Restore accumulator value (simulates StateRestorer._restore_pid_values)
        controller2.set_duty_accumulator(saved_accumulator)
        assert controller2.duty_accumulator_seconds == 60.0

        # Phase 3: Continue operation - accumulator should continue from 60s
        controller2._hass.services.async_call = mock_async_call
        controller2._hass.states.is_state = MagicMock(return_value=False)

        # Set baseline calc time for time-scaled accumulation (600s ago)
        controller2._last_accumulator_calc_time = 600.0

        with patch('time.monotonic', return_value=1200.0):
            await controller2.async_pwm_switch(
                control_output=10.0,  # 10% for 600s = 60s more
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=600.0,
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        # Accumulator should have continued: 60 + 60 = 120s
        assert controller2.duty_accumulator_seconds == 120.0


class TestAccumulatorWithChangingOutput:
    """Test accumulator with varying output levels."""

    @pytest.mark.asyncio
    async def test_accumulator_with_changing_output(
        self, heater_controller_with_dispatcher
    ):
        """Test varying output correctly accumulates duty.

        Simulates realistic scenario where PID output varies:
        - Period 1: 5% output (30s) -> accumulator = 30s
        - Period 2: 8% output (48s) -> accumulator = 78s >= 75s -> fire, left = 3s
        - Period 3: 12% output (72s) -> time_on < 75s, accumulator = 75s >= 75s -> fire
        - Period 4: 15% output (90s) -> time_on >= 75s, normal firing, reset to 0
        """
        controller = heater_controller_with_dispatcher

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

        # Set baseline calc time
        controller._last_accumulator_calc_time = 0.0

        # Period 1: 5% output for 600s = 30s accumulated
        with patch('time.monotonic', return_value=600.0):
            await controller.async_pwm_switch(
                control_output=5.0,
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

        assert controller._duty_accumulator_seconds == 30.0

        # Period 2: 8% output (time_on = 48s < 75s)
        # Check: 30s >= 75s? NO, accumulate 48s -> 78s
        with patch('time.monotonic', return_value=1200.0):
            await controller.async_pwm_switch(
                control_output=8.0,
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=600.0,
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        # Accumulated: 30 + 48 = 78s
        assert controller._duty_accumulator_seconds == 78.0

        # Period 3: 12% output (time_on = 72s < 75s)
        # Check at start: 78s >= 75s? YES -> fire, subtract 75s -> 3s
        with patch('time.monotonic', return_value=1800.0):
            await controller.async_pwm_switch(
                control_output=12.0,
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=1200.0,
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        # Fired: 78 - 75 = 3s remaining
        assert controller._duty_accumulator_seconds == 3.0

        # Period 4: 15% output (time_on = 90s >= 75s threshold)
        # Since time_on >= threshold, this is normal firing, accumulator resets
        # But device is OFF, so it needs time_off elapsed to turn on
        controller._hass.states.is_state = MagicMock(return_value=False)
        with patch('time.monotonic', return_value=2400.0):
            await controller.async_pwm_switch(
                control_output=15.0,
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=1800.0,  # 600s passed
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        # time_on = 90s >= 75s threshold, so normal duty path resets accumulator
        assert controller._duty_accumulator_seconds == 0.0


class TestAccumulatorCycleTrackingCorrect:
    """Test that accumulator pulses emit correct HeatingStarted/HeatingEnded events."""

    @pytest.mark.asyncio
    async def test_accumulator_cycle_tracking_correct(
        self, heater_controller_with_dispatcher, dispatcher
    ):
        """Test that fired pulses emit HeatingStarted/HeatingEnded events.

        When accumulator fires a minimum pulse:
        1. _has_demand should be set to True
        2. async_turn_on should be called, which emits HeatingStartedEvent
        3. Later when heater turns off, HeatingEndedEvent should be emitted

        This test verifies the event emission chain is correct.
        """
        controller = heater_controller_with_dispatcher

        # Track events
        heating_started_events = []
        heating_ended_events = []
        cycle_started_events = []

        dispatcher.subscribe(
            CycleEventType.HEATING_STARTED,
            lambda e: heating_started_events.append(e)
        )
        dispatcher.subscribe(
            CycleEventType.HEATING_ENDED,
            lambda e: heating_ended_events.append(e)
        )
        dispatcher.subscribe(
            CycleEventType.CYCLE_STARTED,
            lambda e: cycle_started_events.append(e)
        )

        # Track service calls
        service_calls = []

        async def track_service_call(domain, service, data):
            service_calls.append((domain, service, data))
        controller._hass.services.async_call = track_service_call

        # Device starts OFF
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator above threshold to trigger immediate pulse
        controller._duty_accumulator_seconds = 80.0  # >= 75s threshold

        # Call with sub-threshold output to trigger accumulator fire
        with patch('time.monotonic', return_value=600.0):
            await controller.async_pwm_switch(
                control_output=10.0,  # 60s < 75s
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

        # Verify turn_on was called (accumulator fired)
        assert any(call[1] == "turn_on" for call in service_calls)

        # Verify _has_demand was set
        assert controller._has_demand is True

        # Verify HeatingStartedEvent was emitted
        assert len(heating_started_events) == 1
        assert heating_started_events[0].hvac_mode == MockHVACMode.HEAT

        # Verify CycleStartedEvent was emitted (first demand period)
        assert len(cycle_started_events) == 1
        assert cycle_started_events[0].hvac_mode == MockHVACMode.HEAT

        # Now simulate heater turning off
        service_calls.clear()
        controller._hass.states.is_state = MagicMock(return_value=True)  # Device is ON

        # Simulate off time passed
        with patch('time.monotonic', return_value=700.0):  # 100s later
            await controller.async_turn_off(
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=MagicMock(return_value=600.0),
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
            )

        # Verify turn_off was called
        assert any(call[1] == "turn_off" for call in service_calls)

        # Verify HeatingEndedEvent was emitted
        assert len(heating_ended_events) == 1
        assert heating_ended_events[0].hvac_mode == MockHVACMode.HEAT


class TestAccumulatorResetBehavior:
    """Test that accumulator resets correctly in various scenarios."""

    @pytest.mark.asyncio
    async def test_accumulator_reset_on_zero_output(
        self, heater_controller_with_dispatcher
    ):
        """Test accumulator resets when control_output goes to zero."""
        controller = heater_controller_with_dispatcher

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

        # Accumulate some duty
        controller._duty_accumulator_seconds = 50.0

        # Call with zero output
        with patch('time.monotonic', return_value=600.0):
            await controller.async_pwm_switch(
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

        # Accumulator should be reset
        assert controller._duty_accumulator_seconds == 0.0

    def test_accumulator_reset_method(self, heater_controller_with_dispatcher):
        """Test reset_duty_accumulator() method works correctly."""
        controller = heater_controller_with_dispatcher

        # Set some accumulated value
        controller._duty_accumulator_seconds = 200.0

        # Reset
        controller.reset_duty_accumulator()

        # Should be zero
        assert controller._duty_accumulator_seconds == 0.0

    def test_accumulator_capped_on_restoration(self, heater_controller_with_dispatcher):
        """Test set_duty_accumulator caps value at max."""
        controller = heater_controller_with_dispatcher

        # max_accumulator = 2 * 75 = 150s
        assert controller._max_accumulator == 150.0

        # Try to set above max
        controller.set_duty_accumulator(300.0)

        # Should be capped at max
        assert controller._duty_accumulator_seconds == 150.0


class TestAccumulatorEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_accumulator_exactly_at_threshold(
        self, heater_controller_with_dispatcher
    ):
        """Test accumulator fires when exactly at threshold."""
        controller = heater_controller_with_dispatcher

        service_calls = []

        async def track_service_call(domain, service, data):
            service_calls.append((domain, service, data))
        controller._hass.services.async_call = track_service_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Set accumulator exactly at threshold
        controller._duty_accumulator_seconds = 75.0

        with patch('time.monotonic', return_value=600.0):
            await controller.async_pwm_switch(
                control_output=10.0,
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

        # Should have fired: 75 - 75 = 0
        assert controller._duty_accumulator_seconds == 0.0
        assert any(call[1] == "turn_on" for call in service_calls)

    @pytest.mark.asyncio
    async def test_accumulator_negative_output_resets(
        self, heater_controller_with_dispatcher
    ):
        """Test negative output resets accumulator."""
        controller = heater_controller_with_dispatcher

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

        controller._duty_accumulator_seconds = 50.0

        with patch('time.monotonic', return_value=600.0):
            await controller.async_pwm_switch(
                control_output=-5.0,  # Negative output
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

        assert controller._duty_accumulator_seconds == 0.0

    @pytest.mark.asyncio
    async def test_multiple_fires_in_sequence(
        self, heater_controller_with_dispatcher
    ):
        """Test accumulator fires multiple times with sustained low output."""
        controller = heater_controller_with_dispatcher

        fire_count = 0

        async def track_service_call(domain, service, data):
            nonlocal fire_count
            if service == "turn_on":
                fire_count += 1
        controller._hass.services.async_call = track_service_call
        controller._hass.states.is_state = MagicMock(return_value=False)

        get_cycle_start_time = MagicMock(return_value=0.0)
        set_is_heating = MagicMock()
        set_last_heat_cycle_time = MagicMock()
        set_time_changed = MagicMock()
        set_force_on = MagicMock()
        set_force_off = MagicMock()

        # Pre-set accumulator high enough for 2 fires
        # 160s = fires at 160, leaves 85s, then 85 + 60 = 145s, fires again
        controller._duty_accumulator_seconds = 160.0

        # First call: 160s >= 75s -> fire, 160 - 75 = 85s
        with patch('time.monotonic', return_value=600.0):
            await controller.async_pwm_switch(
                control_output=10.0,  # 60s
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

        assert controller._duty_accumulator_seconds == 85.0
        assert fire_count == 1

        # Second call: 85s >= 75s -> fire, 85 - 75 = 10s
        with patch('time.monotonic', return_value=1200.0):
            await controller.async_pwm_switch(
                control_output=10.0,
                hvac_mode=MockHVACMode.HEAT,
                get_cycle_start_time=get_cycle_start_time,
                set_is_heating=set_is_heating,
                set_last_heat_cycle_time=set_last_heat_cycle_time,
                time_changed=600.0,
                set_time_changed=set_time_changed,
                force_on=False,
                force_off=False,
                set_force_on=set_force_on,
                set_force_off=set_force_off,
            )

        assert controller._duty_accumulator_seconds == 10.0
        assert fire_count == 2


# Module existence marker
def test_integration_duty_accumulator_module_exists():
    """Marker test to verify module can be imported."""
    from custom_components.adaptive_thermostat.managers.heater_controller import (
        HeaterController,
    )
    assert HeaterController is not None
