"""Integration tests for stale dt bugfix.

Tests that verify the integral accumulation rate is correct when calc_output()
is triggered by external sensors (not the temperature sensor).

The bug: When an external sensor (like a door contact sensor) triggered calc_output(),
the PID used the stale sensor-based timestamp interval instead of the actual elapsed
time since the last calculation. This caused the integral to accumulate at incorrect
rates (e.g., 3%/min instead of 0.01%/min).
"""
import pytest
from unittest.mock import Mock, patch

from custom_components.adaptive_thermostat.managers.control_output import (
    ControlOutputManager,
)
from custom_components.adaptive_thermostat.pid_controller import PID
from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_CONVECTOR,
)


class MockHVACMode:
    """Mock HVACMode for testing."""
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"


class MockThermostatState:
    """Mock implementation of ThermostatState Protocol for testing."""

    def __init__(self):
        """Initialize the mock thermostat state."""
        self.entity_id = "climate.test_zone"
        self._zone_id = None
        self._cur_temp = 20.9
        self._target_temp = 21.0
        self._outdoor_temp = 5.0
        self._wind_speed = 0.0
        self._hvac_mode = MockHVACMode.HEAT
        self._prev_temp_time = 1000.0
        self._cur_temp_time = 1060.0
        self._output_precision = 1
        self._control_output = 0.0
        self._kp = 100.0
        self._ki = 0.1
        self._kd = 50.0
        self._ke = 0.0
        self._coordinator = None

    def _calculate_night_setback_adjustment(self):
        """Mock night setback calculation."""
        return (self._target_temp, None, None)


class TestIntegralAccumulationRate:
    """Tests for verifying correct integral accumulation rate."""

    def setup_method(self):
        """Set up test fixtures with real PID controller."""
        # Create mock thermostat state
        self.thermostat_state = MockThermostatState()

        # Create real PID controller with known settings
        # Ki = 0.1 means 0.1% per (°C·hour)
        # With error = 0.1°C and dt = 60s = 1/60 hour:
        # integral_per_calc = Ki * error * dt_hours = 0.1 * 0.1 * (1/60) = 0.000167%
        self.pid_controller = PID(
            kp=100.0,
            ki=0.1,  # 0.1% per (°C·hour)
            kd=50.0,
            ke=0,
            out_min=0,
            out_max=100,
            sampling_period=0,  # Event-driven mode
            cold_tolerance=0.3,
            hot_tolerance=0.3,
        )
        self.pid_controller.set_feedforward = Mock()

        # Create mock heater controller
        self.heater_controller = Mock()

        # Create mock coordinator (no coupling)
        self.coordinator = Mock()
        self.coordinator.thermal_group_manager = None
        self.thermostat_state._coordinator = self.coordinator

        # Create setter callbacks
        self.set_callbacks = {
            "set_previous_temp_time": Mock(),
            "set_cur_temp_time": Mock(),
            "set_control_output": Mock(),
            "set_p": Mock(),
            "set_i": Mock(),
            "set_d": Mock(),
            "set_e": Mock(),
            "set_dt": Mock(),
        }

        # Create control output manager
        self.manager = ControlOutputManager(
            thermostat_state=self.thermostat_state,
            pid_controller=self.pid_controller,
            heater_controller=self.heater_controller,
            **self.set_callbacks
        )

    @pytest.mark.asyncio
    async def test_integral_accumulation_rate_with_non_sensor_triggers(self):
        """TEST: Simulate scenario from bug: trigger calc every 60s via external sensor.

        Verify integral grows at correct rate (~0.01%/min) not inflated rate (3%/min).

        Scenario:
        - Error = 0.1°C (current = 20.9, setpoint = 21.0)
        - Ki = 0.1 (%/(°C·hour))
        - 10 calculations at 60s intervals

        Expected integral accumulation per calc:
        - integral_delta = Ki * error * dt_hours = 0.1 * 0.1 * (60/3600) = 0.000167%

        After 10 calcs (600s = 10 min):
        - Expected integral ≈ 0.00167% (tiny, due to small error and Ki)
        - Bug behavior: would be much larger due to using sensor dt instead of actual dt

        For this test, we verify integral stays < 0.1% (not > 3% as in the bug case).
        """
        base_time = 1000.0

        # First calc to initialize - this sets _last_pid_calc_time
        with patch("time.monotonic", return_value=base_time):
            await self.manager.calc_output(is_temp_sensor_update=False)

        # Record initial integral (should be 0 after first calc with dt=0)
        initial_integral = self.pid_controller.integral
        assert initial_integral == pytest.approx(0.0, abs=0.01), \
            f"Initial integral should be 0, got {initial_integral}"

        # Perform 10 calculations at 60s intervals via external sensor triggers
        for i in range(10):
            calc_time = base_time + (i + 1) * 60.0  # 60s intervals
            with patch("time.monotonic", return_value=calc_time):
                await self.manager.calc_output(is_temp_sensor_update=False)

        # Get final integral
        final_integral = self.pid_controller.integral

        # Calculate expected integral:
        # For 10 calcs at 60s each with error=0.1 and Ki=0.1:
        # integral = Ki * error * total_dt_hours = 0.1 * 0.1 * (600/3600) = 0.00167%
        # But note: first calc has dt=0, so only 9 intervals contribute
        # Expected ≈ 0.1 * 0.1 * (9 * 60 / 3600) = 0.0015%
        expected_integral = 0.1 * 0.1 * (9 * 60.0 / 3600.0)

        # Allow 50% tolerance for rounding and implementation details
        assert final_integral < 0.1, \
            f"Integral grew too fast: {final_integral}% (expected ~{expected_integral}%, limit 0.1%)"

        # Also verify it's in the right ballpark (not zero)
        assert final_integral > 0.0001, \
            f"Integral too low: {final_integral}% (expected ~{expected_integral}%)"

    @pytest.mark.asyncio
    async def test_integral_stable_when_at_setpoint(self):
        """TEST: Verify integral doesn't drift when error=0 across mixed trigger types.

        When temperature equals setpoint (error=0), the integral should not change
        regardless of trigger frequency or type.
        """
        # Set current temp = setpoint (error = 0)
        self.thermostat_state._cur_temp = 21.0  # equals setpoint

        base_time = 1000.0

        # First calc to initialize
        with patch("time.monotonic", return_value=base_time):
            await self.manager.calc_output(is_temp_sensor_update=True)

        initial_integral = self.pid_controller.integral

        # Mix of sensor updates and external triggers
        trigger_sequence = [
            (60.0, True),    # sensor update at +60s
            (90.0, False),   # external trigger at +90s
            (120.0, True),   # sensor update at +120s
            (130.0, False),  # external trigger at +130s
            (140.0, False),  # external trigger at +140s
            (180.0, True),   # sensor update at +180s
            (200.0, False),  # external trigger at +200s
        ]

        for offset, is_sensor in trigger_sequence:
            with patch("time.monotonic", return_value=base_time + offset):
                await self.manager.calc_output(is_temp_sensor_update=is_sensor)

        final_integral = self.pid_controller.integral

        # With error=0, integral should not change
        # Note: There might be very small floating point differences
        assert final_integral == pytest.approx(initial_integral, abs=0.001), \
            f"Integral drifted from {initial_integral} to {final_integral} with zero error"

    @pytest.mark.asyncio
    async def test_integral_rate_independent_of_trigger_frequency(self):
        """TEST: Integral accumulation over 10 minutes should be the same
        regardless of trigger frequency.

        Two scenarios:
        1. 10 triggers at 60s intervals (slow triggers)
        2. 60 triggers at 10s intervals (fast triggers)

        Both should result in approximately the same integral value.
        """
        base_time = 1000.0
        test_duration = 600.0  # 10 minutes
        error = 0.1  # 0.1°C error

        # Set temperature for both scenarios
        self.thermostat_state._cur_temp = 20.9  # 0.1°C below setpoint

        # Scenario 1: Slow triggers (60s intervals)
        thermostat_state1 = MockThermostatState()
        thermostat_state1._cur_temp = 20.9
        pid1 = PID(
            kp=100.0, ki=0.1, kd=50.0, ke=0,
            out_min=0, out_max=100, sampling_period=0,
        )
        pid1.set_feedforward = Mock()
        manager1 = ControlOutputManager(
            thermostat_state=thermostat_state1,
            pid_controller=pid1,
            heater_controller=self.heater_controller,
            **self.set_callbacks
        )

        with patch("time.monotonic", return_value=base_time):
            await manager1.calc_output(is_temp_sensor_update=False)

        for i in range(10):
            with patch("time.monotonic", return_value=base_time + (i + 1) * 60.0):
                await manager1.calc_output(is_temp_sensor_update=False)

        integral_slow = manager1._pid_controller.integral

        # Scenario 2: Fast triggers (10s intervals)
        thermostat_state2 = MockThermostatState()
        thermostat_state2._cur_temp = 20.9
        pid2 = PID(
            kp=100.0, ki=0.1, kd=50.0, ke=0,
            out_min=0, out_max=100, sampling_period=0,
        )
        pid2.set_feedforward = Mock()
        manager2 = ControlOutputManager(
            thermostat_state=thermostat_state2,
            pid_controller=pid2,
            heater_controller=self.heater_controller,
            **self.set_callbacks
        )

        with patch("time.monotonic", return_value=base_time):
            await manager2.calc_output(is_temp_sensor_update=False)

        for i in range(60):
            with patch("time.monotonic", return_value=base_time + (i + 1) * 10.0):
                await manager2.calc_output(is_temp_sensor_update=False)

        integral_fast = manager2._pid_controller.integral

        # Both should have similar integral (within 20% tolerance)
        # The key is that total dt is the same (600s - 60s for first calc = 540s effective)
        # Slow: 9 intervals * 60s = 540s
        # Fast: 59 intervals * 10s = 590s (slightly more due to 60 triggers starting at 10s)
        # Actually: slow uses 60s intervals for positions 1-10, so 10*60=600s total time,
        # with 9 contributing intervals of 60s each = 540s
        # fast uses 10s intervals for positions 1-60, so 60*10=600s total time,
        # with 59 contributing intervals = 590s

        # Allow 15% difference to account for different number of intervals
        relative_diff = abs(integral_slow - integral_fast) / max(integral_slow, integral_fast, 0.0001)
        assert relative_diff < 0.15, \
            f"Integral differs too much: slow={integral_slow}, fast={integral_fast}, diff={relative_diff:.1%}"

    @pytest.mark.asyncio
    async def test_external_trigger_uses_correct_dt(self):
        """TEST: Verify external trigger uses actual elapsed time, not sensor interval.

        Scenario:
        - Sensor updates every 60s (previous_time=1000, cur_time=1060)
        - But we trigger externally 30s after last calc
        - The dt should be 30s, not 60s (sensor interval)
        """
        base_time = 1000.0

        # First calc at t=1000
        with patch("time.monotonic", return_value=base_time):
            await self.manager.calc_output(is_temp_sensor_update=True)

        # External trigger at t=1030 (30s after last calc)
        # Note: sensor timestamps still show 1000->1060 interval, but actual dt should be 30s
        with patch("time.monotonic", return_value=base_time + 30.0):
            await self.manager.calc_output(is_temp_sensor_update=False)

        # Check the dt that was set (this is what the PID controller calculated)
        dt_value = self.pid_controller.dt

        # dt should be 30s (actual elapsed), not 60s (sensor interval)
        assert dt_value == pytest.approx(30.0, abs=1.0), \
            f"dt should be 30s (actual elapsed), got {dt_value}s"
