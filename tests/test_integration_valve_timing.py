"""Integration tests for valve actuation timing."""

from __future__ import annotations

import pytest
from datetime import timedelta
from unittest.mock import Mock
from typing import Any

from custom_components.adaptive_climate.climate_init import async_setup_managers
from custom_components.adaptive_climate.const import (
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FLOOR_HYDRONIC,
    PIDGains,
)
from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager


class MockThermostat:
    """Mock thermostat for testing valve timing integration."""

    def __init__(self, valve_actuation_time: float = 0.0):
        """Initialize mock thermostat with valve_actuation_time."""
        # Default configuration
        self.hass = Mock()
        self.hass.data = {}
        self.entity_id = "climate.test_zone"

        # Entity configuration
        self._heater_entity_id = "switch.heater"
        self._cooler_entity_id = None
        self._demand_switch_entity_id = None
        self._heater_polarity_invert = False
        self._pwm = 900  # 15 minutes
        self._valve_actuation_time = valve_actuation_time  # NEW: valve timing
        self._difference = 100
        self._min_open_time = timedelta(seconds=300)
        self._min_closed_time = timedelta(seconds=300)

        # Temperature configuration
        self._target_temp = 20.0
        self._current_temp = 19.5
        self._ext_temp = 5.0
        self._away_temp = 16.0
        self._eco_temp = 18.0
        self._boost_temp = 22.0
        self._comfort_temp = 21.0
        self._home_temp = 20.0
        self._sleep_temp = 18.0
        self._activity_temp = 21.0
        self._boost_pid_off = False
        self._preset_sync_mode = None
        self.min_temp = 7.0
        self.max_temp = 35.0
        self._attr_preset_mode = None
        self._saved_target_temp = None

        # Heating type and physics
        self._heating_type = HEATING_TYPE_CONVECTOR
        self._area_m2 = 20.0
        self._ceiling_height = 2.5
        self._window_area_m2 = 4.0
        self._window_rating = None
        self._window_orientation = None
        self._floor_construction = None
        self._supply_temperature = None
        self._max_power_w = None
        self._thermal_time_constant = 3600
        self._loops = 1

        # PID parameters
        self._pid_controller = Mock()
        self._pid_controller.set_pid_param = Mock()
        self._pid_controller.reset_clamp_state = Mock()

        # Initialize gains manager
        initial_heating_gains = PIDGains(kp=100.0, ki=0.01, kd=5000.0, ke=0.0)
        self._gains_manager = PIDGainsManager(
            pid_controller=self._pid_controller,
            initial_heating_gains=initial_heating_gains,
            get_hvac_mode=lambda: self._hvac_mode,
        )

        # Control state
        self._hvac_mode = "heat"
        self._control_output = 0.0
        self._cold_tolerance = 0.3
        self._hot_tolerance = 0.3
        self._force_on = False
        self._force_off = False

        # Night setback configuration
        self._night_setback = None
        self._night_setback_config = None

        # Contact sensor and humidity detection
        self._contact_sensor_handler = None
        self._humidity_detector = None

        # Outdoor temperature
        self._ext_sensor_entity_id = None
        self._has_outdoor_temp_source = False

        # Coordinator and zone
        self._coordinator = None
        self._zone_id = None

        # Setpoint boost configuration
        self._setpoint_boost = True
        self._setpoint_boost_factor = None
        self._setpoint_debounce = 5

        # Manager instances (will be initialized by async_setup_managers)
        self._cycle_dispatcher = None
        self._heater_controller = None
        self._preheat_learner = None
        self._night_setback_controller = None
        self._temperature_manager = None
        self._ke_learner = None
        self._ke_controller = None
        self._pid_tuning_manager = None
        self._control_output_manager = None
        self._cycle_tracker = None
        self._setpoint_boost_manager = None

        # Event handlers for manifold transport delay
        self._on_heating_started_event = Mock()
        self._on_heating_ended_event = Mock()

    # PID gain properties
    @property
    def _kp(self) -> float:
        return self._gains_manager.get_gains().kp

    @property
    def _ki(self) -> float:
        return self._gains_manager.get_gains().ki

    @property
    def _kd(self) -> float:
        return self._gains_manager.get_gains().kd

    @property
    def _ke(self) -> float:
        return self._gains_manager.get_gains().ke

    # Callback setters
    def _set_target_temp(self, temp: float) -> None:
        self._target_temp = temp

    def _set_force_on(self, value: bool) -> None:
        self._force_on = value

    def _set_force_off(self, value: bool) -> None:
        self._force_off = value

    async def _async_set_pid_mode_internal(self, mode: str) -> None:
        pass

    async def _async_control_heating_internal(self, calc_pid: bool = True) -> None:
        pass

    async def _async_write_ha_state_internal(self) -> None:
        pass

    def _set_ke(self, value: float) -> None:
        pass

    def _set_previous_temp_time(self, value: Any) -> None:
        pass

    def _set_cur_temp_time(self, value: Any) -> None:
        pass

    def _set_control_output(self, value: float) -> None:
        self._control_output = value

    def _set_p(self, value: float) -> None:
        pass

    def _set_i(self, value: float) -> None:
        pass

    def _set_d(self, value: float) -> None:
        pass

    def _set_e(self, value: float) -> None:
        pass

    def _set_dt(self, value: float) -> None:
        pass


@pytest.mark.asyncio
async def test_valve_timing_passed_to_heater_controller():
    """Test that valve_actuation_time is passed to HeaterController."""
    # Arrange
    thermostat = MockThermostat(valve_actuation_time=120.0)  # 120 seconds

    # Act
    await async_setup_managers(thermostat)

    # Assert
    assert thermostat._heater_controller is not None
    assert thermostat._heater_controller._valve_actuation_time == 120.0


@pytest.mark.asyncio
async def test_valve_timing_passed_to_pwm_controller():
    """Test that valve_actuation_time is passed to PWMController."""
    # Arrange
    thermostat = MockThermostat(valve_actuation_time=120.0)

    # Act
    await async_setup_managers(thermostat)

    # Assert
    assert thermostat._heater_controller is not None
    pwm_controller = thermostat._heater_controller._pwm_controller
    assert pwm_controller is not None
    assert pwm_controller._valve_actuation_time == 120.0


@pytest.mark.asyncio
async def test_heat_pipeline_created_with_valve_time():
    """Test that HeatPipeline is created when valve_actuation_time > 0."""
    # Arrange
    thermostat = MockThermostat(valve_actuation_time=120.0)

    # Act
    await async_setup_managers(thermostat)

    # Assert
    assert thermostat._heater_controller is not None
    heat_pipeline = thermostat._heater_controller._heat_pipeline
    assert heat_pipeline is not None
    assert heat_pipeline.valve_time == 120.0


@pytest.mark.asyncio
async def test_heat_pipeline_not_created_when_valve_time_zero():
    """Test that HeatPipeline is None when valve_actuation_time is 0."""
    # Arrange
    thermostat = MockThermostat(valve_actuation_time=0.0)

    # Act
    await async_setup_managers(thermostat)

    # Assert
    assert thermostat._heater_controller is not None
    heat_pipeline = thermostat._heater_controller._heat_pipeline
    assert heat_pipeline is None


@pytest.mark.asyncio
async def test_valve_timing_defaults_to_zero():
    """Test that valve_actuation_time defaults to 0 when not specified."""
    # Arrange
    thermostat = MockThermostat()  # No valve_actuation_time specified

    # Act
    await async_setup_managers(thermostat)

    # Assert
    assert thermostat._heater_controller is not None
    assert thermostat._heater_controller._valve_actuation_time == 0.0


@pytest.mark.asyncio
async def test_end_to_end_valve_timing_flow():
    """Integration test: valve timing flows through entire system."""
    # Arrange
    valve_time = 180.0  # 3 minutes
    thermostat = MockThermostat(valve_actuation_time=valve_time)

    # Act
    await async_setup_managers(thermostat)

    # Assert - Check all components have the valve time configured
    # 1. HeaterController
    assert thermostat._heater_controller._valve_actuation_time == valve_time

    # 2. PWMController
    pwm_controller = thermostat._heater_controller._pwm_controller
    assert pwm_controller._valve_actuation_time == valve_time

    # 3. HeatPipeline is created and configured
    heat_pipeline = thermostat._heater_controller._heat_pipeline
    assert heat_pipeline is not None
    assert heat_pipeline.valve_time == valve_time
    assert heat_pipeline.transport_delay == 0.0  # No manifold delay in this test
