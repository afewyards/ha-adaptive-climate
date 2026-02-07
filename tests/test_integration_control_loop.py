"""Integration tests for PID control loop.

This module tests the complete control loop from PID calculation through
cycle tracking to learning updates, using real component instances.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from custom_components.adaptive_climate.managers.events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    SettlingStartedEvent,
    HeatingStartedEvent,
    HeatingEndedEvent,
)
from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_climate.const import HeatingType


class TestFullPIDFeedbackLoop:
    """Test A1: Full PID feedback loop with real components."""

    def test_multi_cycle_heating_scenario(self, make_thermostat, time_travel):
        """Simulate a multi-cycle heating scenario with PID, cycle tracking, and learning.

        This test demonstrates the complete feedback loop:
        1. PID calculates output based on temperature error
        2. Heater turns on (simulated)
        3. Temperature rises gradually
        4. Cycle tracker monitors the heating cycle
        5. Cycle completes and metrics are calculated
        6. Learner records the cycle observation
        """
        # Create a thermostat with default radiator heating type
        t = make_thermostat(heating_type=HeatingType.RADIATOR)

        # Initial state: Room at 19.0°C, target 21.0°C
        t.current_temp = 19.0
        t.target_temp = 21.0

        # Scenario: First heating cycle
        # =============================

        # 1. Calculate initial PID output - should be positive (need heating)
        # Note: P-on-M returns 0 on first call (no previous measurement)
        # Need to call twice to get meaningful output
        initial_error = t.target_temp - t.current_temp  # 2.0°C
        t.pid.calc(
            input_val=t.current_temp,
            set_point=t.target_temp,
            input_time=time_travel.monotonic(),
            ext_temp=None,
        )
        # Advance time slightly for second call
        time_travel.advance(seconds=30)
        output, calculated = t.pid.calc(
            input_val=t.current_temp,
            set_point=t.target_temp,
            input_time=time_travel.monotonic(),
            ext_temp=None,
        )
        assert calculated is True
        assert output >= 0, "PID should output non-negative value when temp below target"

        # 2. Notify cycle tracker that heating started
        start_time = time_travel.now()
        t.dispatcher.emit(
            CycleStartedEvent(
                hvac_mode="heat",
                timestamp=start_time,
                target_temp=t.target_temp,
                current_temp=t.current_temp,
            )
        )
        t.dispatcher.emit(
            HeatingStartedEvent(
                hvac_mode="heat",
                timestamp=start_time,
            )
        )

        # Mark restoration complete so cycle tracker will accept temperature updates
        t.cycle_tracker.set_restoration_complete()

        # Record initial cycle count
        initial_cycle_count = t.learner.get_cycle_count()

        # 3. Simulate heating session with temperature rising over 20 minutes
        # Temperature rises from 19.0 to 21.5°C (slight overshoot), then settles to 21.0°C
        heating_duration_minutes = 20
        temp_schedule = [
            (0, 19.0),
            (2, 19.2),
            (4, 19.5),
            (6, 19.8),
            (8, 20.1),
            (10, 20.4),
            (12, 20.7),
            (14, 21.0),
            (16, 21.2),
            (18, 21.4),
            (20, 21.5),  # Peak overshoot
        ]

        for minute_offset, temp in temp_schedule:
            time_travel.advance(minutes=minute_offset if minute_offset == 0 else 2)
            t.current_temp = temp

            # Update cycle tracker with temperature sample
            import asyncio

            asyncio.run(t.cycle_tracker.update_temperature(time_travel.now(), temp))

            # Calculate PID output - should decrease as we approach target
            output, calculated = t.pid.calc(
                input_val=t.current_temp,
                set_point=t.target_temp,
                input_time=time_travel.monotonic(),
            )
            if calculated and temp < t.target_temp:
                assert output >= 0, f"PID should output non-negative when below target (temp={temp})"

        # 4. Heater turns off, settling phase begins
        heating_end_time = time_travel.now()
        t.dispatcher.emit(
            HeatingEndedEvent(
                hvac_mode="heat",
                timestamp=heating_end_time,
            )
        )
        t.dispatcher.emit(
            SettlingStartedEvent(
                hvac_mode="heat",
                timestamp=heating_end_time,
                was_clamped=False,
            )
        )

        # 5. Temperature settles back to target over next 10 samples
        settling_temps = [21.4, 21.3, 21.2, 21.1, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0]
        for temp in settling_temps:
            time_travel.advance(seconds=30)
            t.current_temp = temp
            asyncio.run(t.cycle_tracker.update_temperature(time_travel.now(), temp))

        # 6. Wait for settling to complete (cycle tracker should detect stability)
        # At this point, the cycle should be finalized and metrics recorded

        # 7. Verify that learner received the cycle observation
        final_cycle_count = t.learner.get_cycle_count()
        assert final_cycle_count == initial_cycle_count + 1, (
            "Learner should have recorded one cycle after settling completed"
        )

        # 8. Verify cycle metrics were recorded
        # Check that the learner's cycle history contains the cycle
        assert len(t.learner.cycle_history) > 0, "Cycle history should not be empty"
        last_cycle = t.learner.cycle_history[-1]
        assert isinstance(last_cycle, CycleMetrics)
        # Verify overshoot was captured (peak 21.5 vs target 21.0)
        assert last_cycle.overshoot is not None
        assert last_cycle.overshoot > 0, "Cycle should have recorded overshoot"


class TestSetpointChangeResponse:
    """Test A3: Setpoint change response with integral boost/decay."""

    def test_setpoint_increase_applies_boost(self, make_thermostat, time_travel, mock_hass):
        """Test that setpoint increase triggers integral boost.

        Scenario:
        1. PID is running with steady integral
        2. User increases setpoint by 2°C
        3. SetpointBoostManager should boost integral
        4. Integral should be higher after boost
        """
        from custom_components.adaptive_climate.managers.setpoint_boost import SetpointBoostManager

        # Create thermostat
        t = make_thermostat(heating_type=HeatingType.RADIATOR)

        # Set up initial state with some accumulated integral
        t.current_temp = 20.0
        t.target_temp = 21.0
        t.pid.integral = 10.0  # Some existing integral

        # Create SetpointBoostManager
        boost_manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=t.pid,
            is_night_period_cb=lambda: False,  # Not in night setback
            enabled=True,
        )

        # Record initial integral
        initial_integral = t.pid.integral

        # Simulate setpoint increase by 2°C
        old_setpoint = t.target_temp
        new_setpoint = t.target_temp + 2.0
        t.target_temp = new_setpoint

        # Notify boost manager of setpoint change
        boost_manager.on_setpoint_change(old_setpoint, new_setpoint)

        # Advance time past debounce window to trigger boost
        import asyncio

        time_travel.advance(seconds=6)
        # Execute pending callbacks manually since we're not in real async context
        # The boost_manager schedules a callback, we need to trigger it manually
        # For this test, we'll call _apply_boost directly
        asyncio.run(boost_manager._apply_boost(time_travel.now()))

        # Verify integral increased
        final_integral = t.pid.integral
        assert final_integral > initial_integral, (
            f"Integral should increase after setpoint boost (was {initial_integral}, now {final_integral})"
        )

    def test_setpoint_decrease_applies_decay(self, make_thermostat, time_travel, mock_hass):
        """Test that setpoint decrease triggers integral decay.

        Scenario:
        1. PID is running with substantial integral
        2. User decreases setpoint by 2°C
        3. SetpointBoostManager should decay integral
        4. Integral should be lower after decay
        """
        from custom_components.adaptive_climate.managers.setpoint_boost import SetpointBoostManager

        # Create thermostat
        t = make_thermostat(heating_type=HeatingType.RADIATOR)

        # Set up initial state with substantial accumulated integral
        t.current_temp = 20.0
        t.target_temp = 22.0
        t.pid.integral = 30.0  # Large integral (heating hard)

        # Create SetpointBoostManager
        boost_manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=t.pid,
            is_night_period_cb=lambda: False,
            enabled=True,
        )

        # Record initial integral
        initial_integral = t.pid.integral

        # Simulate setpoint decrease by 2°C
        old_setpoint = t.target_temp
        new_setpoint = t.target_temp - 2.0
        t.target_temp = new_setpoint

        # Notify boost manager of setpoint change
        boost_manager.on_setpoint_change(old_setpoint, new_setpoint)

        # Advance time past debounce window to trigger decay
        import asyncio

        time_travel.advance(seconds=6)
        asyncio.run(boost_manager._apply_boost(time_travel.now()))

        # Verify integral decreased
        final_integral = t.pid.integral
        assert final_integral < initial_integral, (
            f"Integral should decrease after setpoint decay (was {initial_integral}, now {final_integral})"
        )


class TestCycleMetricsPropagation:
    """Test A2 (simplified): Cycle metrics propagation without transport delay.

    Note: Full transport delay testing requires HeaterController setup which is
    excluded from make_thermostat. This simplified test verifies that cycle
    metrics flow correctly through the system.
    """

    def test_cycle_metrics_basic_propagation(self, make_thermostat, time_travel):
        """Test that basic cycle metrics are calculated and recorded.

        This verifies:
        1. Cycle tracker monitors temperature samples
        2. Metrics recorder calculates overshoot, rise time, etc.
        3. Metrics are passed to learner
        """
        import asyncio
        from custom_components.adaptive_climate.managers.events import (
            CycleStartedEvent,
            SettlingStartedEvent,
            HeatingStartedEvent,
            HeatingEndedEvent,
        )

        # Create thermostat
        t = make_thermostat(heating_type=HeatingType.RADIATOR)

        # Set initial conditions
        t.current_temp = 19.0
        t.target_temp = 21.0

        # Mark restoration complete
        t.cycle_tracker.set_restoration_complete()

        # Record initial cycle count
        initial_count = t.learner.get_cycle_count()

        # Start heating cycle
        start_time = time_travel.now()
        t.dispatcher.emit(
            CycleStartedEvent(
                hvac_mode="heat",
                timestamp=start_time,
                target_temp=t.target_temp,
                current_temp=t.current_temp,
            )
        )
        t.dispatcher.emit(
            HeatingStartedEvent(
                hvac_mode="heat",
                timestamp=start_time,
            )
        )

        # Simulate heating with clean rise to target (no overshoot)
        temp_schedule = [
            (0, 19.0),
            (2, 19.3),
            (4, 19.6),
            (6, 19.9),
            (8, 20.2),
            (10, 20.5),
            (12, 20.8),
            (14, 21.0),
        ]

        for minute_offset, temp in temp_schedule:
            if minute_offset > 0:
                time_travel.advance(minutes=2)
            t.current_temp = temp
            asyncio.run(t.cycle_tracker.update_temperature(time_travel.now(), temp))

        # Stop heating
        time_travel.advance(minutes=2)
        heating_end_time = time_travel.now()
        t.dispatcher.emit(
            HeatingEndedEvent(
                hvac_mode="heat",
                timestamp=heating_end_time,
            )
        )
        t.dispatcher.emit(
            SettlingStartedEvent(
                hvac_mode="heat",
                timestamp=heating_end_time,
                was_clamped=False,
            )
        )

        # Settle at target (10 samples for settling detection)
        settling_temps = [21.0] * 10
        for temp in settling_temps:
            time_travel.advance(seconds=30)
            asyncio.run(t.cycle_tracker.update_temperature(time_travel.now(), temp))

        # Verify cycle was recorded
        final_count = t.learner.get_cycle_count()
        assert final_count == initial_count + 1, "One cycle should be recorded after settling"

        # Verify metrics structure
        assert len(t.learner.cycle_history) > 0
        cycle = t.learner.cycle_history[-1]
        assert isinstance(cycle, CycleMetrics)

        # Basic metrics should be present
        # Rise time should be measured (time to reach target from start)
        assert cycle.rise_time is not None, "Rise time should be measured"
        assert cycle.rise_time > 0, "Rise time should be positive"

        # Overshoot should be zero or very small (clean rise)
        if cycle.overshoot is not None:
            assert cycle.overshoot <= 0.3, "Clean rise should have minimal overshoot"
