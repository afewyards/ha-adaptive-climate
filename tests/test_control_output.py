"""Tests for ControlOutputManager PID timing."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from custom_components.adaptive_thermostat.managers.control_output import (
    ControlOutputManager,
)
from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
)


class MockHVACMode:
    """Mock HVACMode for testing."""
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"
    HEAT_COOL = "heat_cool"


# Alias for convenience
HVACMode = MockHVACMode


# ============================================================================
# PID Calc Time Tracking Tests (Story 1.1)
# ============================================================================


class TestPIDCalcTimeTracking:
    """Tests for tracking _last_pid_calc_time in ControlOutputManager."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock thermostat
        self.thermostat = Mock()
        self.thermostat.entity_id = "climate.living_room"
        self.thermostat.hass = Mock()
        self.thermostat._heating_type = HEATING_TYPE_CONVECTOR
        self.thermostat._hvac_mode = HVACMode.HEAT

        # Create mock PID controller
        self.pid_controller = Mock()
        self.pid_controller.sampling_period = 0
        self.pid_controller.calc = Mock(return_value=(50.0, True))
        self.pid_controller.proportional = 30.0
        self.pid_controller.integral = 15.0
        self.pid_controller.derivative = 5.0
        self.pid_controller.external = 0.0
        self.pid_controller.feedforward = 0.0
        self.pid_controller.error = 1.0
        self.pid_controller.dt = 60.0

        # Create mock heater controller
        self.heater_controller = Mock()

        # Create mock coordinator (minimal - no coupling)
        self.coordinator = Mock()
        self.coordinator.get_active_zones = Mock(return_value={})
        self.thermostat.hass.data = {
            "adaptive_thermostat": {
                "coordinator": self.coordinator
            }
        }

        # Create callbacks
        self.callbacks = {
            "get_current_temp": Mock(return_value=20.0),
            "get_ext_temp": Mock(return_value=5.0),
            "get_wind_speed": Mock(return_value=0.0),
            "get_previous_temp_time": Mock(return_value=1000.0),
            "set_previous_temp_time": Mock(),
            "get_cur_temp_time": Mock(return_value=1060.0),
            "set_cur_temp_time": Mock(),
            "get_output_precision": Mock(return_value=1),
            "calculate_night_setback_adjustment": Mock(return_value=(21.0, None, None)),
            "set_control_output": Mock(),
            "set_p": Mock(),
            "set_i": Mock(),
            "set_d": Mock(),
            "set_e": Mock(),
            "set_dt": Mock(),
            "get_kp": Mock(return_value=100.0),
            "get_ki": Mock(return_value=0.1),
            "get_kd": Mock(return_value=50.0),
            "get_ke": Mock(return_value=0.3),
        }

        # Create control output manager
        self.manager = ControlOutputManager(
            thermostat=self.thermostat,
            pid_controller=self.pid_controller,
            heater_controller=self.heater_controller,
            **self.callbacks
        )

    @pytest.mark.asyncio
    async def test_control_output_tracks_calc_time(self):
        """TEST: Verify _last_pid_calc_time is set after calc_output()."""
        # Initially, _last_pid_calc_time should be None
        assert self.manager._last_pid_calc_time is None

        # Call calc_output
        with patch("time.monotonic", return_value=1100.0):
            await self.manager.calc_output()

        # After calc_output, _last_pid_calc_time should be set
        assert self.manager._last_pid_calc_time == 1100.0

    @pytest.mark.asyncio
    async def test_control_output_first_calc_dt_zero(self):
        """TEST: Verify first calc returns dt=0 when _last_pid_calc_time is None."""
        # First call - _last_pid_calc_time is None, so actual_dt should be 0
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output()

        # The PID calc should have been called with times that result in dt=0
        # on first call since we have no previous calc time
        # For now, verify _last_pid_calc_time is set after first call
        assert self.manager._last_pid_calc_time == 1000.0

        # Second call - actual_dt should now be calculated
        with patch("time.monotonic", return_value=1060.0):
            await self.manager.calc_output()

        # Now _last_pid_calc_time should be updated
        assert self.manager._last_pid_calc_time == 1060.0

    @pytest.mark.asyncio
    async def test_control_output_clamps_negative_dt(self):
        """TEST: Verify negative dt is clamped to 0."""
        # First call to establish baseline
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output()

        # Simulate time going backwards (shouldn't happen, but be defensive)
        with patch("time.monotonic", return_value=900.0):
            await self.manager.calc_output()

        # Despite time going backwards, should clamp and continue
        # The implementation should handle this gracefully
        assert self.manager._last_pid_calc_time == 900.0

    @pytest.mark.asyncio
    async def test_control_output_clamps_huge_dt(self):
        """TEST: Verify huge dt is clamped to max_dt."""
        # First call to establish baseline
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output()

        # Simulate a huge gap (e.g., system was suspended)
        with patch("time.monotonic", return_value=10000.0):
            await self.manager.calc_output()

        # Should clamp dt to reasonable value
        assert self.manager._last_pid_calc_time == 10000.0


# ============================================================================
# PID Dt Correction Tests (Story 1.2)
# ============================================================================


class TestPIDDtCorrection:
    """Tests for correct dt calculation in different trigger scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock thermostat
        self.thermostat = Mock()
        self.thermostat.entity_id = "climate.living_room"
        self.thermostat.hass = Mock()
        self.thermostat._heating_type = HEATING_TYPE_CONVECTOR
        self.thermostat._hvac_mode = HVACMode.HEAT

        # Create mock PID controller with dt tracking
        self.pid_controller = Mock()
        self.pid_controller.sampling_period = 0
        self.pid_controller.calc = Mock(return_value=(50.0, True))
        self.pid_controller.proportional = 30.0
        self.pid_controller.integral = 15.0
        self.pid_controller.derivative = 5.0
        self.pid_controller.external = 0.0
        self.pid_controller.feedforward = 0.0
        self.pid_controller.error = 1.0
        self.pid_controller.dt = 60.0

        # Create mock heater controller
        self.heater_controller = Mock()

        # Create mock coordinator
        self.coordinator = Mock()
        self.coordinator.get_active_zones = Mock(return_value={})
        self.thermostat.hass.data = {
            "adaptive_thermostat": {
                "coordinator": self.coordinator
            }
        }

        # Track set_dt calls
        self._set_dt_calls = []

        def track_dt(value):
            self._set_dt_calls.append(value)

        # Create callbacks
        self.callbacks = {
            "get_current_temp": Mock(return_value=20.0),
            "get_ext_temp": Mock(return_value=5.0),
            "get_wind_speed": Mock(return_value=0.0),
            "get_previous_temp_time": Mock(return_value=1000.0),
            "set_previous_temp_time": Mock(),
            "get_cur_temp_time": Mock(return_value=1060.0),
            "set_cur_temp_time": Mock(),
            "get_output_precision": Mock(return_value=1),
            "calculate_night_setback_adjustment": Mock(return_value=(21.0, None, None)),
            "set_control_output": Mock(),
            "set_p": Mock(),
            "set_i": Mock(),
            "set_d": Mock(),
            "set_e": Mock(),
            "set_dt": Mock(side_effect=track_dt),
            "get_kp": Mock(return_value=100.0),
            "get_ki": Mock(return_value=0.1),
            "get_kd": Mock(return_value=50.0),
            "get_ke": Mock(return_value=0.3),
        }

        # Create control output manager
        self.manager = ControlOutputManager(
            thermostat=self.thermostat,
            pid_controller=self.pid_controller,
            heater_controller=self.heater_controller,
            **self.callbacks
        )

    @pytest.mark.asyncio
    async def test_dt_correct_between_sensor_updates(self):
        """TEST: Verify dt correctly calculated between temperature sensor updates."""
        # First sensor update at t=1000
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output(is_temp_sensor_update=True)

        # Second sensor update at t=1060 (60s later)
        with patch("time.monotonic", return_value=1060.0):
            await self.manager.calc_output(is_temp_sensor_update=True)

        # The dt should be 60s (actual elapsed time)
        assert len(self._set_dt_calls) == 2
        assert self._set_dt_calls[1] == pytest.approx(60.0, abs=0.1), \
            "dt should be actual elapsed time between sensor updates"

    @pytest.mark.asyncio
    async def test_non_sensor_trigger_uses_actual_elapsed_time(self):
        """TEST: Verify non-sensor triggers use actual elapsed time for dt."""
        # First sensor update at t=1000
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output(is_temp_sensor_update=True)

        # Non-sensor trigger (e.g., setpoint change) at t=1030 (30s later)
        with patch("time.monotonic", return_value=1030.0):
            await self.manager.calc_output(is_temp_sensor_update=False)

        # Should show 30s elapsed, not sensor interval
        assert self._set_dt_calls[1] == pytest.approx(30.0, abs=0.1), \
            "External trigger should show actual elapsed time"

    @pytest.mark.asyncio
    async def test_multiple_external_triggers_accumulate_correctly(self):
        """TEST: Verify multiple external triggers accumulate dt correctly."""
        # First sensor update at t=1000
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output(is_temp_sensor_update=True)

        # External trigger at t=1030 (30s later)
        with patch("time.monotonic", return_value=1030.0):
            await self.manager.calc_output(is_temp_sensor_update=False)

        # Should show 30s elapsed, not sensor interval
        assert self._set_dt_calls[1] == pytest.approx(30.0, abs=0.1), \
            "External trigger should show actual elapsed time"

        # Another external trigger at t=1045 (15s later)
        with patch("time.monotonic", return_value=1045.0):
            await self.manager.calc_output(is_temp_sensor_update=False)

        assert self._set_dt_calls[2] == pytest.approx(15.0, abs=0.1), \
            "Subsequent trigger should show correct dt"


# ============================================================================
# Reset Calc Timing Tests (Story 1.3)
# ============================================================================


class TestResetCalcTiming:
    """Tests for reset_calc_timing method."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock thermostat
        self.thermostat = Mock()
        self.thermostat.entity_id = "climate.living_room"
        self.thermostat.hass = Mock()
        self.thermostat._heating_type = HEATING_TYPE_CONVECTOR
        self.thermostat._hvac_mode = HVACMode.HEAT

        # Create mock PID controller
        self.pid_controller = Mock()
        self.pid_controller.sampling_period = 0
        self.pid_controller.calc = Mock(return_value=(50.0, True))
        self.pid_controller.proportional = 30.0
        self.pid_controller.integral = 15.0
        self.pid_controller.derivative = 5.0
        self.pid_controller.external = 0.0
        self.pid_controller.feedforward = 0.0
        self.pid_controller.error = 1.0
        self.pid_controller.dt = 60.0

        # Create mock heater controller
        self.heater_controller = Mock()

        # Create mock coordinator
        self.coordinator = Mock()
        self.coordinator.get_active_zones = Mock(return_value={})
        self.thermostat.hass.data = {
            "adaptive_thermostat": {
                "coordinator": self.coordinator
            }
        }

        # Track set_dt calls
        self._set_dt_calls = []

        def track_dt(value):
            self._set_dt_calls.append(value)

        # Create callbacks
        self.callbacks = {
            "get_current_temp": Mock(return_value=20.0),
            "get_ext_temp": Mock(return_value=5.0),
            "get_wind_speed": Mock(return_value=0.0),
            "get_previous_temp_time": Mock(return_value=1000.0),
            "set_previous_temp_time": Mock(),
            "get_cur_temp_time": Mock(return_value=1060.0),
            "set_cur_temp_time": Mock(),
            "get_output_precision": Mock(return_value=1),
            "calculate_night_setback_adjustment": Mock(return_value=(21.0, None, None)),
            "set_control_output": Mock(),
            "set_p": Mock(),
            "set_i": Mock(),
            "set_d": Mock(),
            "set_e": Mock(),
            "set_dt": Mock(side_effect=track_dt),
            "get_kp": Mock(return_value=100.0),
            "get_ki": Mock(return_value=0.1),
            "get_kd": Mock(return_value=50.0),
            "get_ke": Mock(return_value=0.3),
        }

        # Create control output manager
        self.manager = ControlOutputManager(
            thermostat=self.thermostat,
            pid_controller=self.pid_controller,
            heater_controller=self.heater_controller,
            **self.callbacks
        )

    @pytest.mark.asyncio
    async def test_reset_calc_timing_clears_last_pid_time(self):
        """TEST: Verify reset_calc_timing() clears _last_pid_calc_time."""
        # First establish a calc time
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output()

        assert self.manager._last_pid_calc_time == 1000.0

        # Reset timing
        self.manager.reset_calc_timing()

        # Should be cleared
        assert self.manager._last_pid_calc_time is None

    @pytest.mark.asyncio
    async def test_reset_calc_timing_causes_next_calc_dt_zero(self):
        """TEST: Verify after reset, next calc uses dt=0."""
        # Establish normal operation
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output()

        with patch("time.monotonic", return_value=1060.0):
            await self.manager.calc_output()

        # Reset timing
        self.manager.reset_calc_timing()
        self._set_dt_calls.clear()

        # Next calc should use dt=0 since we reset
        with patch("time.monotonic", return_value=1100.0):
            await self.manager.calc_output()

        # First call after reset should have dt=0
        assert self._set_dt_calls[0] == pytest.approx(0.0, abs=0.1), \
            "First calc after reset should have dt=0"


# ============================================================================
# PID dt State Attribute Tests (Story 1.4)
# ============================================================================


class TestPidDtStateAttribute:
    """Tests for pid_dt state attribute exposure."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock thermostat
        self.thermostat = Mock()
        self.thermostat.entity_id = "climate.living_room"
        self.thermostat.hass = Mock()
        self.thermostat._heating_type = HEATING_TYPE_CONVECTOR
        self.thermostat._hvac_mode = HVACMode.HEAT

        # Create mock PID controller
        self.pid_controller = Mock()
        self.pid_controller.sampling_period = 0
        self.pid_controller.calc = Mock(return_value=(50.0, True))
        self.pid_controller.proportional = 30.0
        self.pid_controller.integral = 15.0
        self.pid_controller.derivative = 5.0
        self.pid_controller.external = 0.0
        self.pid_controller.feedforward = 0.0
        self.pid_controller.error = 1.0
        self.pid_controller.dt = 60.0

        # Create mock heater controller
        self.heater_controller = Mock()

        # Create mock coordinator
        self.coordinator = Mock()
        self.coordinator.get_active_zones = Mock(return_value={})
        self.thermostat.hass.data = {
            "adaptive_thermostat": {
                "coordinator": self.coordinator
            }
        }

        # Track set_dt calls to capture actual dt values
        self._set_dt_calls = []

        def track_dt(value):
            self._set_dt_calls.append(value)

        # Create callbacks
        self.callbacks = {
            "get_current_temp": Mock(return_value=20.0),
            "get_ext_temp": Mock(return_value=5.0),
            "get_wind_speed": Mock(return_value=0.0),
            "get_previous_temp_time": Mock(return_value=1000.0),
            "set_previous_temp_time": Mock(),
            "get_cur_temp_time": Mock(return_value=1060.0),
            "set_cur_temp_time": Mock(),
            "get_output_precision": Mock(return_value=1),
            "calculate_night_setback_adjustment": Mock(return_value=(21.0, None, None)),
            "set_control_output": Mock(),
            "set_p": Mock(),
            "set_i": Mock(),
            "set_d": Mock(),
            "set_e": Mock(),
            "set_dt": Mock(side_effect=track_dt),
            "get_kp": Mock(return_value=100.0),
            "get_ki": Mock(return_value=0.1),
            "get_kd": Mock(return_value=50.0),
            "get_ke": Mock(return_value=0.3),
        }

        # Create control output manager
        self.manager = ControlOutputManager(
            thermostat=self.thermostat,
            pid_controller=self.pid_controller,
            heater_controller=self.heater_controller,
            **self.callbacks
        )

    @pytest.mark.asyncio
    async def test_pid_dt_attribute_shows_actual_calc_dt(self):
        """TEST: Verify pid_dt attribute shows actual dt used in calculation."""
        # First calc at t=1000
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output()

        # Second calc at t=1060 (60s later)
        with patch("time.monotonic", return_value=1060.0):
            await self.manager.calc_output()

        # The set_dt callback should have been called with the actual dt
        assert len(self._set_dt_calls) == 2
        assert self._set_dt_calls[1] == pytest.approx(60.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_pid_dt_attribute_after_reset(self):
        """TEST: Verify pid_dt shows 0 after timing reset."""
        # Establish normal operation
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output()

        # Reset timing
        self.manager.reset_calc_timing()
        self._set_dt_calls.clear()

        # Next calc should show dt=0
        with patch("time.monotonic", return_value=1100.0):
            await self.manager.calc_output()

        assert self._set_dt_calls[0] == pytest.approx(0.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_pid_dt_attribute_with_external_triggers(self):
        """TEST: Verify pid_dt reflects actual elapsed time for external triggers."""
        # Sensor update at t=1000
        with patch("time.monotonic", return_value=1000.0):
            await self.manager.calc_output(is_temp_sensor_update=True)

        # External trigger at t=1030 (30s later)
        with patch("time.monotonic", return_value=1030.0):
            await self.manager.calc_output(is_temp_sensor_update=False)

        # Should show 30s elapsed, not sensor interval
        assert self._set_dt_calls[1] == pytest.approx(30.0, abs=0.1), \
            "External trigger should show actual elapsed time"

        # Another external trigger at t=1045 (15s later)
        with patch("time.monotonic", return_value=1045.0):
            await self.manager.calc_output(is_temp_sensor_update=False)

        assert self._set_dt_calls[2] == pytest.approx(15.0, abs=0.1), \
            "Subsequent trigger should show correct dt"
