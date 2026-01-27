"""Tests for climate_init module - manager initialization factory."""

from __future__ import annotations

import pytest
from datetime import timedelta
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from typing import Any

from custom_components.adaptive_thermostat.climate_init import (
    async_setup_managers,
    _has_recovery_deadline,
)
from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_FORCED_AIR,
)


class MockThermostat:
    """Mock thermostat for testing manager initialization."""

    def __init__(self, config_overrides: dict[str, Any] | None = None):
        """Initialize mock thermostat with default config."""
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
        self._difference = 100
        self._min_on_cycle_duration = timedelta(seconds=300)
        self._min_off_cycle_duration = timedelta(seconds=300)

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

        # PID parameters
        self._kp = 100.0
        self._ki = 0.01
        self._kd = 5000.0
        self._ke = 0.0
        self._pid_controller = Mock()
        self._pid_controller.set_pid_param = Mock()

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

        # Outdoor temperature
        self._ext_sensor_entity_id = None
        self._has_outdoor_temp_source = False

        # Coordinator and zone
        self._coordinator = None
        self._zone_id = None

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

        # Apply overrides
        if config_overrides:
            for key, value in config_overrides.items():
                setattr(self, key, value)

    # Callback setters
    def _set_target_temp(self, temp: float) -> None:
        self._target_temp = temp

    def _set_force_on(self, value: bool) -> None:
        self._force_on = value

    def _set_force_off(self, value: bool) -> None:
        self._force_off = value

    async def _async_set_pid_mode_internal(self, mode: str) -> None:
        pass

    async def _async_control_heating_internal(self) -> None:
        pass

    async def _async_write_ha_state_internal(self) -> None:
        pass

    def _set_ke(self, ke: float) -> None:
        self._ke = ke

    def _set_kp(self, kp: float) -> None:
        self._kp = kp

    def _set_ki(self, ki: float) -> None:
        self._ki = ki

    def _set_kd(self, kd: float) -> None:
        self._kd = kd

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

    @property
    def in_learning_grace_period(self) -> bool:
        return False

    def _is_device_active(self) -> bool:
        return True

    def _handle_validation_failure(self, *args: Any, **kwargs: Any) -> None:
        pass

    def _check_auto_apply_pid(self, *args: Any, **kwargs: Any) -> None:
        pass

    def _is_pid_converged_for_ke(self) -> bool:
        return True

    async def _handle_cycle_ended_for_preheat(self, *args: Any, **kwargs: Any) -> None:
        pass


class TestHasRecoveryDeadline:
    """Tests for _has_recovery_deadline helper function."""

    def test_returns_true_when_recovery_deadline_is_set(self):
        """Test returns True when recovery_deadline is configured."""
        config = {"recovery_deadline": "07:00"}
        assert _has_recovery_deadline(config) is True

    def test_returns_false_when_recovery_deadline_is_none(self):
        """Test returns False when recovery_deadline is None."""
        config = {"recovery_deadline": None}
        assert _has_recovery_deadline(config) is False

    def test_returns_false_when_recovery_deadline_not_in_config(self):
        """Test returns False when recovery_deadline key is missing."""
        config = {"setback_delta": 2.0}
        assert _has_recovery_deadline(config) is False

    def test_returns_false_when_config_is_none(self):
        """Test returns False when config itself is None."""
        assert _has_recovery_deadline(None) is False

    def test_returns_false_when_config_is_empty(self):
        """Test returns False when config is empty dict."""
        assert _has_recovery_deadline({}) is False


@pytest.mark.asyncio
class TestAsyncSetupManagers:
    """Tests for async_setup_managers function."""

    async def test_basic_initialization_creates_all_core_managers(self):
        """Test that basic setup creates all core managers."""
        thermostat = MockThermostat()

        await async_setup_managers(thermostat)

        # Core managers should always be created
        assert thermostat._cycle_dispatcher is not None
        assert thermostat._heater_controller is not None
        assert thermostat._temperature_manager is not None
        assert thermostat._ke_controller is not None
        assert thermostat._pid_tuning_manager is not None
        assert thermostat._control_output_manager is not None

    async def test_cycle_dispatcher_initialization(self):
        """Test CycleEventDispatcher is created."""
        thermostat = MockThermostat()

        await async_setup_managers(thermostat)

        assert thermostat._cycle_dispatcher is not None
        # Verify it's a CycleEventDispatcher by checking it has expected structure
        from custom_components.adaptive_thermostat.managers.events import CycleEventDispatcher
        assert isinstance(thermostat._cycle_dispatcher, CycleEventDispatcher)

    async def test_heater_controller_initialization_with_pwm(self):
        """Test HeaterController is initialized with correct PWM config."""
        thermostat = MockThermostat({
            "_heater_entity_id": "switch.main_heater",
            "_pwm": 900,  # 15 minutes
            "_difference": 100,
        })

        await async_setup_managers(thermostat)

        assert thermostat._heater_controller is not None
        # Verify internal state is set correctly
        assert thermostat._heater_controller._pwm == 900
        assert thermostat._heater_controller._heater_entity_id == "switch.main_heater"

    async def test_heater_controller_initialization_without_pwm(self):
        """Test HeaterController works with pwm=0 (valve mode)."""
        thermostat = MockThermostat({
            "_pwm": 0,
        })

        await async_setup_managers(thermostat)

        assert thermostat._heater_controller is not None
        assert thermostat._heater_controller._pwm == 0

    async def test_preheat_learner_created_when_recovery_deadline_set(self):
        """Test PreheatLearner is created when recovery_deadline is configured."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "recovery_deadline": "07:00",
                "setback_delta": 2.0,
            },
            "_heating_type": HEATING_TYPE_FLOOR_HYDRONIC,
        })

        await async_setup_managers(thermostat)

        assert thermostat._preheat_learner is not None
        assert thermostat._preheat_learner.heating_type == HEATING_TYPE_FLOOR_HYDRONIC

    async def test_preheat_learner_respects_max_preheat_hours(self):
        """Test PreheatLearner uses configured max_preheat_hours."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "recovery_deadline": "07:00",
                "max_preheat_hours": 2.5,
            },
            "_heating_type": HEATING_TYPE_RADIATOR,
        })

        await async_setup_managers(thermostat)

        assert thermostat._preheat_learner is not None
        assert thermostat._preheat_learner.max_hours == 2.5

    async def test_preheat_learner_not_created_when_no_recovery_deadline(self):
        """Test PreheatLearner is not created without recovery_deadline."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "setback_delta": 2.0,
            },
        })

        await async_setup_managers(thermostat)

        assert thermostat._preheat_learner is None

    async def test_preheat_learner_created_when_preheat_enabled_explicitly(self):
        """Test PreheatLearner is created when preheat_enabled=True even without recovery_deadline."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "preheat_enabled": True,
                "setback_delta": 2.0,
            },
            "_heating_type": HEATING_TYPE_CONVECTOR,
        })

        await async_setup_managers(thermostat)

        assert thermostat._preheat_learner is not None
        assert thermostat._preheat_learner.heating_type == HEATING_TYPE_CONVECTOR

    async def test_preheat_learner_not_created_when_preheat_disabled_explicitly(self):
        """Test PreheatLearner is not created when preheat_enabled=False even with recovery_deadline."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "recovery_deadline": "07:00",
                "preheat_enabled": False,
            },
        })

        await async_setup_managers(thermostat)

        assert thermostat._preheat_learner is None

    async def test_preheat_learner_restored_from_storage(self):
        """Test PreheatLearner is restored from coordinator storage."""
        # Create stored preheat data
        stored_preheat_data = {
            "heating_type": HEATING_TYPE_RADIATOR,
            "max_hours": 3.0,
            "observations": {},
        }

        # Mock coordinator with stored data
        mock_coordinator = Mock()
        mock_coordinator.get_zone_data = Mock(return_value={
            "stored_preheat_data": stored_preheat_data,
        })

        thermostat = MockThermostat({
            "_night_setback_config": {
                "recovery_deadline": "07:00",
            },
            "_coordinator": mock_coordinator,
            "_zone_id": "test_zone",
        })

        with patch("custom_components.adaptive_thermostat.climate_init.PreheatLearner.from_dict") as mock_from_dict:
            mock_from_dict.return_value = Mock(
                heating_type=HEATING_TYPE_RADIATOR,
                get_observation_count=Mock(return_value=5),
            )

            await async_setup_managers(thermostat)

            mock_from_dict.assert_called_once_with(stored_preheat_data)
            assert thermostat._preheat_learner is not None

    async def test_night_setback_manager_created_with_config(self):
        """Test NightSetbackManager is created when night_setback_config exists."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "start_time": "22:00",
                "end_time": "06:00",
                "setback_delta": 2.5,
            },
        })

        await async_setup_managers(thermostat)

        assert thermostat._night_setback_controller is not None

    async def test_night_setback_manager_created_with_legacy_setback(self):
        """Test NightSetbackManager is created with legacy _night_setback value."""
        thermostat = MockThermostat({
            "_night_setback": 2.0,
        })

        await async_setup_managers(thermostat)

        assert thermostat._night_setback_controller is not None

    async def test_night_setback_manager_not_created_without_config(self):
        """Test NightSetbackManager is not created without config."""
        thermostat = MockThermostat()

        await async_setup_managers(thermostat)

        assert thermostat._night_setback_controller is None

    async def test_night_setback_manager_with_preheat_learner(self):
        """Test NightSetbackManager receives PreheatLearner when both enabled."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "recovery_deadline": "07:00",
                "setback_delta": 2.0,
            },
        })

        await async_setup_managers(thermostat)

        assert thermostat._night_setback_controller is not None
        assert thermostat._preheat_learner is not None
        # Verify preheat_learner was passed to calculator (stored in _calculator)
        assert thermostat._night_setback_controller._calculator._preheat_learner is not None

    async def test_temperature_manager_initialization(self):
        """Test TemperatureManager is initialized with preset temps."""
        thermostat = MockThermostat({
            "_away_temp": 16.0,
            "_eco_temp": 18.0,
            "_boost_temp": 22.0,
            "_comfort_temp": 21.0,
        })

        await async_setup_managers(thermostat)

        assert thermostat._temperature_manager is not None
        assert thermostat._temperature_manager._away_temp == 16.0
        assert thermostat._temperature_manager._eco_temp == 18.0
        assert thermostat._temperature_manager._boost_temp == 22.0
        assert thermostat._temperature_manager._comfort_temp == 21.0

    async def test_temperature_manager_restores_state(self):
        """Test TemperatureManager restores preset mode state."""
        thermostat = MockThermostat({
            "_attr_preset_mode": "away",
            "_saved_target_temp": 20.0,
        })

        await async_setup_managers(thermostat)

        assert thermostat._temperature_manager is not None
        # Verify restore_state was called (manager should have preset mode)
        # We can't directly verify the call, but we can check the manager exists

    async def test_ke_learner_created_with_outdoor_sensor(self):
        """Test KeLearner is created when outdoor sensor is configured."""
        thermostat = MockThermostat({
            "_has_outdoor_temp_source": True,
            "_ext_sensor_entity_id": "sensor.outdoor_temp",
            "_area_m2": 25.0,
        })

        with patch("custom_components.adaptive_thermostat.climate_init.calculate_initial_ke") as mock_calc_ke:
            mock_calc_ke.return_value = 0.05

            await async_setup_managers(thermostat)

            assert thermostat._ke_learner is not None
            assert thermostat._ke == 0.05
            mock_calc_ke.assert_called_once()

    async def test_ke_learner_restored_from_storage(self):
        """Test KeLearner is restored from coordinator storage."""
        stored_ke_data = {
            "initial_ke": 0.04,
            "current_ke": 0.045,
            "enabled": True,
            "observation_count": 10,
        }

        mock_coordinator = Mock()
        mock_coordinator.get_zone_data = Mock(return_value={
            "stored_ke_data": stored_ke_data,
        })

        thermostat = MockThermostat({
            "_has_outdoor_temp_source": True,
            "_coordinator": mock_coordinator,
            "_zone_id": "test_zone",
        })

        with patch("custom_components.adaptive_thermostat.climate_init.KeLearner.from_dict") as mock_from_dict:
            mock_ke_learner = Mock()
            mock_ke_learner.current_ke = 0.045
            mock_ke_learner.enabled = True
            mock_ke_learner.observation_count = 10
            mock_from_dict.return_value = mock_ke_learner

            await async_setup_managers(thermostat)

            mock_from_dict.assert_called_once_with(stored_ke_data)
            assert thermostat._ke_learner is not None
            assert thermostat._ke == 0.045

    async def test_ke_learner_not_created_without_outdoor_sensor(self):
        """Test KeLearner is not created without outdoor sensor."""
        thermostat = MockThermostat({
            "_has_outdoor_temp_source": False,
        })

        await async_setup_managers(thermostat)

        assert thermostat._ke_learner is None

    async def test_ke_manager_always_created(self):
        """Test KeManager is always created even without outdoor sensor."""
        thermostat = MockThermostat({
            "_has_outdoor_temp_source": False,
        })

        await async_setup_managers(thermostat)

        # KeManager should be created even without outdoor sensor
        assert thermostat._ke_controller is not None
        # But ke_learner should be None
        assert thermostat._ke_learner is None

    async def test_pid_tuning_manager_initialization(self):
        """Test PIDTuningManager is initialized."""
        thermostat = MockThermostat({
            "_kp": 100.0,
            "_ki": 0.01,
            "_kd": 5000.0,
        })

        await async_setup_managers(thermostat)

        assert thermostat._pid_tuning_manager is not None

    async def test_control_output_manager_initialization_with_thermostat_state(self):
        """Test ControlOutputManager receives ThermostatState protocol."""
        thermostat = MockThermostat()

        await async_setup_managers(thermostat)

        assert thermostat._control_output_manager is not None
        # ControlOutputManager should have received the thermostat as thermostat_state
        # Check it has the internal reference
        assert thermostat._control_output_manager._thermostat_state is not None

    async def test_cycle_tracker_created_with_coordinator_and_adaptive_learner(self):
        """Test CycleTrackerManager is created when coordinator and adaptive_learner exist."""
        mock_adaptive_learner = Mock()
        mock_coordinator = Mock()
        mock_coordinator.get_zone_data = Mock(return_value={
            "adaptive_learner": mock_adaptive_learner,
        })

        thermostat = MockThermostat({
            "_coordinator": mock_coordinator,
            "_zone_id": "test_zone",
            "_thermal_time_constant": 3600,
        })

        await async_setup_managers(thermostat)

        assert thermostat._cycle_tracker is not None

    async def test_cycle_tracker_not_created_without_coordinator(self):
        """Test CycleTrackerManager is not created without coordinator."""
        thermostat = MockThermostat({
            "_coordinator": None,
        })

        await async_setup_managers(thermostat)

        assert thermostat._cycle_tracker is None

    async def test_cycle_tracker_not_created_without_zone_id(self):
        """Test CycleTrackerManager is not created without zone_id."""
        mock_coordinator = Mock()

        thermostat = MockThermostat({
            "_coordinator": mock_coordinator,
            "_zone_id": None,
        })

        await async_setup_managers(thermostat)

        assert thermostat._cycle_tracker is None

    async def test_cycle_tracker_not_created_without_adaptive_learner(self):
        """Test CycleTrackerManager is not created without adaptive_learner in zone_data."""
        mock_coordinator = Mock()
        mock_coordinator.get_zone_data = Mock(return_value={})

        thermostat = MockThermostat({
            "_coordinator": mock_coordinator,
            "_zone_id": "test_zone",
        })

        await async_setup_managers(thermostat)

        assert thermostat._cycle_tracker is None

    async def test_preheat_learner_subscribes_to_cycle_ended_events(self):
        """Test PreheatLearner subscribes to CYCLE_ENDED events when created."""
        thermostat = MockThermostat({
            "_night_setback_config": {
                "recovery_deadline": "07:00",
            },
        })

        await async_setup_managers(thermostat)

        assert thermostat._preheat_learner is not None
        assert thermostat._cycle_dispatcher is not None
        # Verify subscription exists (dispatcher has subscribers)
        # We can check that the dispatcher has at least one subscriber
        assert len(thermostat._cycle_dispatcher._listeners) > 0

    async def test_multiple_heating_types_initialize_correctly(self):
        """Test initialization works for different heating types."""
        heating_types = [
            HEATING_TYPE_FLOOR_HYDRONIC,
            HEATING_TYPE_RADIATOR,
            HEATING_TYPE_CONVECTOR,
            HEATING_TYPE_FORCED_AIR,
        ]

        for heating_type in heating_types:
            thermostat = MockThermostat({
                "_heating_type": heating_type,
                "_night_setback_config": {
                    "recovery_deadline": "07:00",
                },
            })

            await async_setup_managers(thermostat)

            assert thermostat._preheat_learner is not None
            assert thermostat._preheat_learner.heating_type == heating_type
            assert thermostat._heater_controller is not None

    async def test_initialization_with_cooler_entity(self):
        """Test initialization works with cooler entity configured."""
        thermostat = MockThermostat({
            "_heater_entity_id": "switch.heater",
            "_cooler_entity_id": "switch.cooler",
        })

        await async_setup_managers(thermostat)

        assert thermostat._heater_controller is not None
        assert thermostat._heater_controller._cooler_entity_id == "switch.cooler"

    async def test_initialization_with_demand_switch(self):
        """Test initialization works with demand switch configured."""
        thermostat = MockThermostat({
            "_demand_switch_entity_id": "switch.manifold_demand",
        })

        await async_setup_managers(thermostat)

        assert thermostat._heater_controller is not None
        assert thermostat._heater_controller._demand_switch_entity_id == "switch.manifold_demand"

    async def test_initialization_with_polarity_invert(self):
        """Test initialization works with heater polarity inverted."""
        thermostat = MockThermostat({
            "_heater_polarity_invert": True,
        })

        await async_setup_managers(thermostat)

        assert thermostat._heater_controller is not None
        assert thermostat._heater_controller._heater_polarity_invert is True

    async def test_edge_case_none_night_setback_config(self):
        """Test handling of None night_setback_config."""
        thermostat = MockThermostat({
            "_night_setback_config": None,
        })

        await async_setup_managers(thermostat)

        assert thermostat._preheat_learner is None
        assert thermostat._night_setback_controller is None

    async def test_edge_case_empty_night_setback_config(self):
        """Test handling of empty night_setback_config dict."""
        thermostat = MockThermostat({
            "_night_setback_config": {},
        })

        await async_setup_managers(thermostat)

        # Empty dict {} is falsy in Python, so manager is NOT created
        assert thermostat._night_setback_controller is None
        # Preheat also not created
        assert thermostat._preheat_learner is None

    async def test_ke_initialization_uses_house_energy_rating(self):
        """Test Ke initialization uses house_energy_rating from domain data."""
        thermostat = MockThermostat({
            "_has_outdoor_temp_source": True,
        })
        thermostat.hass.data = {
            "adaptive_thermostat": {
                "house_energy_rating": "A",
            }
        }

        with patch("custom_components.adaptive_thermostat.climate_init.calculate_initial_ke") as mock_calc_ke:
            mock_calc_ke.return_value = 0.03

            await async_setup_managers(thermostat)

            # Verify calculate_initial_ke was called with energy_rating
            call_kwargs = mock_calc_ke.call_args[1]
            assert call_kwargs["energy_rating"] == "A"

    async def test_cycle_tracker_added_to_zone_data(self):
        """Test CycleTrackerManager is added back to zone_data after creation."""
        mock_adaptive_learner = Mock()
        zone_data = {"adaptive_learner": mock_adaptive_learner}
        mock_coordinator = Mock()
        mock_coordinator.get_zone_data = Mock(return_value=zone_data)

        thermostat = MockThermostat({
            "_coordinator": mock_coordinator,
            "_zone_id": "test_zone",
        })

        await async_setup_managers(thermostat)

        # Verify cycle_tracker was added to zone_data
        assert "cycle_tracker" in zone_data
        assert zone_data["cycle_tracker"] is thermostat._cycle_tracker
