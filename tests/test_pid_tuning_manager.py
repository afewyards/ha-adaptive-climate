"""Tests for PIDTuningManager Protocol-based refactoring."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from custom_components.adaptive_climate.managers.pid_tuning import PIDTuningManager
from custom_components.adaptive_climate.protocols import PIDTuningManagerState
from custom_components.adaptive_climate.const import PIDChangeReason, HeatingType
from custom_components.adaptive_climate.pid_controller import PID


class MockPIDTuningManagerState:
    """Mock implementation of PIDTuningManagerState protocol for testing.

    This mock provides all properties required by the protocol without
    needing a full thermostat entity instance.
    """

    def __init__(self):
        """Initialize mock state."""
        # Core identification
        self.entity_id = "climate.test_zone"
        self._zone_id = "test_zone"

        # Temperature properties
        self.current_temperature = 20.0
        self.target_temperature = 21.0
        self._ext_temp = 10.0
        self._cold_tolerance = 0.3
        self._hot_tolerance = 0.3
        self._target_temp = 21.0
        self._current_temp = 20.0
        self._wind_speed = 5.0

        # PID properties
        self._kp = 1.5
        self._ki = 0.01
        self._kd = 10.0
        self._ke = 0.5
        self._control_output = 50.0
        self.pid_control_p = 30.0
        self.pid_control_i = 10.0
        self.pid_control_d = 8.0
        self.pid_control_e = 2.0
        self.pid_mode = "AUTO"

        # HVAC properties
        from homeassistant.components.climate import HVACMode
        self._hvac_mode = HVACMode.HEAT
        self.hvac_mode = HVACMode.HEAT
        self.hvac_action = "heating"
        self.heating_type = HeatingType.RADIATOR
        self._is_device_active = True
        self.is_heating = True
        self._is_heating = True

        # Controllers and managers
        self._pid_controller = MagicMock(spec=PID)
        self._pid_controller.integral = 0.0
        self._pid_controller.mode = "AUTO"
        self._heater_controller = MagicMock()
        self._coordinator = None

        # Preset properties
        self.preset_mode = "none"
        self._away_temp = 18.0
        self._eco_temp = 19.0
        self._boost_temp = 23.0
        self._comfort_temp = 21.0
        self._home_temp = 21.0
        self._sleep_temp = 19.0
        self._activity_temp = 22.0

        # Physics properties for PID tuning
        self._area_m2 = 20.0
        self._ceiling_height = 2.5
        self._window_area_m2 = 2.0
        self._window_rating = "double"
        self._floor_construction = None
        self._supply_temperature = None
        self._max_power_w = None
        self._pwm = 600  # 10 minutes

        # Timing and other properties
        self._output_precision = 1
        self._previous_temp_time = None
        self._cur_temp_time = None
        self._night_setback = None
        self._night_setback_config = None
        self._night_setback_controller = None
        self._preheat_learner = None
        self._contact_sensor_handler = None
        self._humidity_detector = None

    def _calculate_night_setback_adjustment(self):
        """Calculate night setback adjustment."""
        return (self.target_temperature, False, None)

    def _get_current_temp(self):
        """Get the current temperature (method form)."""
        return self.current_temperature

    def _get_target_temp(self):
        """Get the target temperature (method form)."""
        return self.target_temperature


def test_protocol_implementation():
    """Test that MockPIDTuningManagerState implements the protocol."""
    mock_state = MockPIDTuningManagerState()

    # Verify key properties are accessible (protocol compliance)
    # Note: We don't use isinstance() because Protocol structural typing
    # doesn't require explicit inheritance. The fact that PIDTuningManager
    # accepts the mock proves it implements the protocol.
    assert mock_state.entity_id == "climate.test_zone"
    assert mock_state._kp == 1.5
    assert mock_state._area_m2 == 20.0
    assert mock_state.heating_type == HeatingType.RADIATOR

    # Verify the manager accepts the mock state (proves protocol compliance)
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    assert manager is not None
    assert manager._state is mock_state


def test_pid_tuning_manager_initialization():
    """Test PIDTuningManager can be initialized with protocol state."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    # Create manager with protocol state
    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    assert manager is not None
    assert manager._pid_controller is pid_controller
    assert manager._gains_manager is gains_manager
    assert manager._async_control_heating is async_control_heating
    assert manager._async_write_ha_state is async_write_ha_state


@pytest.mark.asyncio
async def test_async_set_pid():
    """Test setting PID parameters through the manager."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Set PID parameters
    await manager.async_set_pid(kp=2.0, ki=0.02, kd=15.0, ke=0.6)

    # Verify gains manager was called with correct parameters
    gains_manager.set_gains.assert_called_once_with(
        PIDChangeReason.SERVICE_CALL,
        kp=2.0,
        ki=0.02,
        kd=15.0,
        ke=0.6,
    )

    # Verify control heating was triggered
    async_control_heating.assert_called_once_with(calc_pid=True)


@pytest.mark.asyncio
async def test_async_set_pid_mode():
    """Test setting PID mode through the manager."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    pid_controller.mode = "AUTO"
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Set PID mode to OFF
    await manager.async_set_pid_mode(mode="OFF")

    # Verify mode was changed on PID controller
    assert pid_controller.mode == "OFF"

    # Verify control heating was triggered
    async_control_heating.assert_called_once_with(calc_pid=True)


@pytest.mark.asyncio
async def test_reset_pid_to_physics():
    """Test resetting PID to physics-based defaults."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    pid_controller.integral = 50.0  # Start with non-zero integral
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Reset PID to physics
    await manager.async_reset_pid_to_physics()

    # Verify integral was cleared
    assert pid_controller.integral == 0.0

    # Verify gains manager was called with physics reset reason
    assert gains_manager.set_gains.called
    call_args = gains_manager.set_gains.call_args
    assert call_args[0][0] == PIDChangeReason.PHYSICS_RESET

    # Verify callbacks were triggered
    async_control_heating.assert_called_once_with(calc_pid=True)
    async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_reset_pid_without_area():
    """Test that reset fails gracefully without area configuration."""
    mock_state = MockPIDTuningManagerState()
    mock_state._area_m2 = None  # No area configured
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Reset should return early without error
    await manager.async_reset_pid_to_physics()

    # Verify no gains were set
    assert not gains_manager.set_gains.called

    # Verify no callbacks were triggered
    assert not async_control_heating.called
    assert not async_write_ha_state.called


@pytest.mark.asyncio
async def test_apply_adaptive_pid():
    """Test applying adaptive PID recommendations."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    pid_controller.integral = 50.0
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    # Mock coordinator and learner
    mock_coordinator = MagicMock()
    mock_learner = MagicMock()
    mock_learner.get_cycle_count.return_value = 6
    mock_learner.calculate_pid_adjustment.return_value = {
        "kp": 1.8,
        "ki": 0.015,
        "kd": 12.0,
    }
    mock_coordinator.get_adaptive_learner.return_value = mock_learner
    mock_state._coordinator = mock_coordinator

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Apply adaptive PID
    await manager.async_apply_adaptive_pid()

    # Verify integral was cleared
    assert pid_controller.integral == 0.0

    # Verify gains were set
    gains_manager.set_gains.assert_called_once_with(
        PIDChangeReason.ADAPTIVE_APPLY,
        kp=1.8,
        ki=0.015,
        kd=12.0,
    )

    # Verify learning history was cleared
    mock_learner.clear_history.assert_called_once()

    # Verify callbacks were triggered
    async_control_heating.assert_called_once_with(calc_pid=True)
    async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_apply_adaptive_pid_without_coordinator():
    """Test that adaptive PID fails gracefully without coordinator."""
    mock_state = MockPIDTuningManagerState()
    mock_state._coordinator = None
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Apply should return early
    await manager.async_apply_adaptive_pid()

    # Verify no gains were set
    assert not gains_manager.set_gains.called


@pytest.mark.asyncio
async def test_auto_apply_adaptive_pid():
    """Test auto-apply with safety checks."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    pid_controller.integral = 50.0
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    # Mock coordinator and learner
    mock_coordinator = MagicMock()
    mock_learner = MagicMock()
    mock_learner._auto_apply_count = 0
    mock_learner._convergence_confidence = 0.7
    mock_learner.cycle_history = []
    mock_learner.calculate_pid_adjustment.return_value = {
        "kp": 1.8,
        "ki": 0.015,
        "kd": 12.0,
    }
    mock_coordinator.get_adaptive_learner.return_value = mock_learner
    mock_state._coordinator = mock_coordinator

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Auto-apply
    result = await manager.async_auto_apply_adaptive_pid(outdoor_temp=10.0)

    # Verify success
    assert result["applied"] is True
    assert result["recommendation"] is not None

    # Verify integral was cleared
    assert pid_controller.integral == 0.0

    # Verify gains were set with AUTO_APPLY reason
    assert gains_manager.set_gains.called
    call_args = gains_manager.set_gains.call_args
    assert call_args[0][0] == PIDChangeReason.AUTO_APPLY

    # Verify auto-apply count was incremented
    assert mock_learner._auto_apply_count == 1

    # Verify validation mode was started
    mock_learner.start_validation_mode.assert_called_once()

    # Verify callbacks were triggered
    async_control_heating.assert_called_once_with(calc_pid=True)
    async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_pid():
    """Test PID rollback functionality."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    pid_controller.integral = 50.0
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    # Mock coordinator and learner
    mock_coordinator = MagicMock()
    mock_learner = MagicMock()
    mock_learner.get_previous_pid.return_value = {
        "kp": 1.2,
        "ki": 0.008,
        "kd": 8.0,
        "timestamp": "2024-01-15T10:00:00",
    }
    mock_coordinator.get_adaptive_learner.return_value = mock_learner
    mock_state._coordinator = mock_coordinator

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Rollback PID
    result = await manager.async_rollback_pid()

    # Verify success
    assert result is True

    # Verify integral was cleared
    assert pid_controller.integral == 0.0

    # Verify gains were set with ROLLBACK reason
    assert gains_manager.set_gains.called
    call_args = gains_manager.set_gains.call_args
    assert call_args[0][0] == PIDChangeReason.ROLLBACK
    assert call_args[1]["kp"] == 1.2
    assert call_args[1]["ki"] == 0.008
    assert call_args[1]["kd"] == 8.0

    # Verify learning history was cleared
    mock_learner.clear_history.assert_called_once()

    # Verify callbacks were triggered
    async_control_heating.assert_called_once_with(calc_pid=True)
    async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_without_history():
    """Test that rollback fails gracefully without history."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    # Mock coordinator and learner with no history
    mock_coordinator = MagicMock()
    mock_learner = MagicMock()
    mock_learner.get_previous_pid.return_value = None
    mock_coordinator.get_adaptive_learner.return_value = mock_learner
    mock_state._coordinator = mock_coordinator

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Rollback should fail gracefully
    result = await manager.async_rollback_pid()

    # Verify failure
    assert result is False

    # Verify no gains were set
    assert not gains_manager.set_gains.called


@pytest.mark.asyncio
async def test_state_reading_through_protocol():
    """Test that all state reading works through the protocol."""
    mock_state = MockPIDTuningManagerState()

    # Set specific values to verify reading
    mock_state._kp = 2.5
    mock_state._ki = 0.025
    mock_state._kd = 15.5
    mock_state._ke = 0.75
    mock_state._area_m2 = 30.0
    mock_state._ceiling_height = 3.0
    mock_state._window_area_m2 = 4.0
    mock_state._window_rating = "triple"
    mock_state.heating_type = HeatingType.FLOOR_HYDRONIC

    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Verify state can be read through protocol properties
    assert mock_state._kp == 2.5
    assert mock_state._ki == 0.025
    assert mock_state._kd == 15.5
    assert mock_state._ke == 0.75
    assert mock_state._area_m2 == 30.0
    assert mock_state._ceiling_height == 3.0
    assert mock_state._window_area_m2 == 4.0
    assert mock_state._window_rating == "triple"
    assert mock_state.heating_type == HeatingType.FLOOR_HYDRONIC


@pytest.mark.asyncio
async def test_pid_controller_direct_access():
    """Test that PIDController can be accessed directly for performance."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    pid_controller.integral = 100.0
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Verify direct access to PID controller
    assert manager._pid_controller is pid_controller
    assert manager._pid_controller.integral == 100.0

    # Verify integral can be modified directly
    manager._pid_controller.integral = 0.0
    assert pid_controller.integral == 0.0


@pytest.mark.asyncio
async def test_action_callbacks_work():
    """Test that action callbacks are properly invoked."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()

    # Track callback invocations
    control_heating_calls = []
    write_state_calls = []

    async def track_control_heating(**kwargs):
        control_heating_calls.append(kwargs)

    async def track_write_state():
        write_state_calls.append(True)

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=track_control_heating,
        async_write_ha_state=track_write_state,
    )

    # Trigger action through set_pid
    await manager.async_set_pid(kp=2.0)

    # Verify callbacks were invoked
    assert len(control_heating_calls) == 1
    assert control_heating_calls[0] == {"calc_pid": True}
    assert len(write_state_calls) == 0  # set_pid doesn't call write_state

    # Reset tracking
    control_heating_calls.clear()
    write_state_calls.clear()

    # Trigger action through reset_to_physics
    await manager.async_reset_pid_to_physics()

    # Verify both callbacks were invoked
    assert len(control_heating_calls) == 1
    assert len(write_state_calls) == 1


@pytest.mark.asyncio
async def test_clear_learning():
    """Test clearing all learning data."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    # Mock coordinator, learner, and ke_controller
    mock_coordinator = MagicMock()
    mock_learner = MagicMock()
    mock_coordinator.get_adaptive_learner.return_value = mock_learner
    mock_state._coordinator = mock_coordinator

    mock_ke_controller = MagicMock()
    mock_ke_learner = MagicMock()
    mock_ke_controller.ke_learner = mock_ke_learner

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # Add ke_controller to thermostat (manager accesses via getattr)
    mock_state._ke_controller = mock_ke_controller

    # Clear learning
    await manager.async_clear_learning()

    # Verify adaptive learner history was cleared
    mock_learner.clear_history.assert_called_once()

    # Verify ke learner observations were cleared
    mock_ke_learner.clear_observations.assert_called_once()

    # Verify reset_to_physics was called (by checking gains manager)
    assert gains_manager.set_gains.called
