"""Tests for PIDTuningManager Protocol-based refactoring."""

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from datetime import datetime

from custom_components.adaptive_climate.managers.pid_tuning import PIDTuningManager
from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
from custom_components.adaptive_climate.protocols import PIDTuningManagerState
from custom_components.adaptive_climate.const import PIDChangeReason, HeatingType, PIDGains
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

    # Verify integral was cleared via gains_manager (D4: centralized mutation)
    gains_manager.set_integral.assert_called_once_with(0.0, PIDChangeReason.PHYSICS_RESET)

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

    # Verify integral was cleared via gains_manager (D4: centralized mutation)
    gains_manager.set_integral.assert_called_once_with(0.0, PIDChangeReason.ADAPTIVE_APPLY)

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

    # increment_auto_apply_count should mutate _auto_apply_count and return new value
    def _mock_increment_auto_apply_count(mode=None):
        mock_learner._auto_apply_count += 1
        return mock_learner._auto_apply_count

    mock_learner.increment_auto_apply_count.side_effect = _mock_increment_auto_apply_count
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

    # Integral is in output units, so a Ki change needs no integral change
    gains_manager.set_integral.assert_not_called()
    gains_manager.scale_integral.assert_not_called()

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
    """H07: rollback reads history[-2] from _gains_manager, not get_previous_pid()."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    pid_controller.integral = 50.0
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    # Two-entry history: [previous, current]
    gains_manager = MagicMock()
    previous_entry = {"kp": 1.2, "ki": 0.008, "kd": 8.0, "ke": 0.3, "timestamp": "2024-01-15T10:00:00"}
    current_entry = {"kp": 1.5, "ki": 0.01, "kd": 10.0, "ke": 0.5, "timestamp": "2024-01-16T10:00:00"}
    gains_manager.get_history.return_value = [previous_entry, current_entry]

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    result = await manager.async_rollback_pid()

    # H07: returns the previous history entry (dict), not a bool
    assert result == previous_entry

    # get_history called with current hvac_mode (no coordinator access needed)
    gains_manager.get_history.assert_called_once_with(mock_state._hvac_mode)

    # Integral is in output units; zeroing it would drop the output by the whole heat demand
    gains_manager.set_integral.assert_not_called()

    # Gains applied with ROLLBACK reason using history[-2] values
    gains_manager.set_gains.assert_called_once()
    call_args = gains_manager.set_gains.call_args
    assert call_args[0][0] == PIDChangeReason.ROLLBACK
    assert call_args[1]["kp"] == 1.2
    assert call_args[1]["ki"] == 0.008
    assert call_args[1]["kd"] == 8.0
    assert call_args[1]["ke"] == 0.3

    # Callbacks triggered
    async_control_heating.assert_called_once_with(calc_pid=True)
    async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_without_history():
    """H07: rollback raises (HomeAssistantError = Exception in tests) when < 2 entries."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    gains_manager.get_history.return_value = [{"kp": 1.5, "ki": 0.01, "kd": 10.0}]  # only 1
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # HomeAssistantError is mapped to Exception in the test environment (conftest.py)
    with pytest.raises(Exception, match="No previous gains"):
        await manager.async_rollback_pid()

    gains_manager.set_gains.assert_not_called()
    gains_manager.set_integral.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_ke_defaults_to_zero_when_missing():
    """H07: ke defaults to 0.0 when not present in history entry."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    # History without 'ke' key
    gains_manager.get_history.return_value = [
        {"kp": 1.0, "ki": 0.005, "kd": 6.0},  # previous — no ke
        {"kp": 1.5, "ki": 0.01, "kd": 10.0},  # current
    ]
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    await manager.async_rollback_pid()

    call_args = gains_manager.set_gains.call_args
    assert call_args[1]["ke"] == 0.0


@pytest.mark.asyncio
async def test_rollback_empty_history():
    """H07: rollback raises (HomeAssistantError = Exception in tests) when history is empty."""
    mock_state = MockPIDTuningManagerState()
    pid_controller = MagicMock(spec=PID)
    gains_manager = MagicMock()
    gains_manager.get_history.return_value = []
    async_control_heating = AsyncMock()
    async_write_ha_state = AsyncMock()

    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=async_control_heating,
        async_write_ha_state=async_write_ha_state,
    )

    # HomeAssistantError is mapped to Exception in the test environment (conftest.py)
    with pytest.raises(Exception, match="No previous gains"):
        await manager.async_rollback_pid()

    gains_manager.set_gains.assert_not_called()


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


# ---------------------------------------------------------------------------
# H07 — rollback must use PIDGainsManager history, not learner.get_previous_pid()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRollbackUsesGainsHistory:
    """H07: async_rollback_pid must read from PIDGainsManager.get_history()."""

    def _make_real_gains_manager(self, hvac_mode=None):
        """Build a real PIDGainsManager wired to a mock PID controller."""
        from homeassistant.components.climate import HVACMode

        mode = hvac_mode or HVACMode.HEAT
        mock_pid = Mock()
        mock_pid.set_pid_param = Mock()
        mock_pid.integral = 0.0
        gains_manager = PIDGainsManager(
            pid_controller=mock_pid,
            initial_heating_gains=PIDGains(kp=1.0, ki=0.01, kd=5.0, ke=0.0),
            get_hvac_mode=lambda: mode,
        )
        return gains_manager, mock_pid

    async def test_rollback_restores_previous_gains_from_history(self):
        """H07: After applying two sets of gains, rollback restores the first set."""
        gains_manager, mock_pid = self._make_real_gains_manager()

        # Apply first set of gains (entry 0 in history)
        gains_manager.set_gains(PIDChangeReason.SERVICE_CALL, kp=1.5, ki=0.015, kd=8.0)
        # Apply second set (entry 1 in history — becomes "current")
        gains_manager.set_gains(PIDChangeReason.SERVICE_CALL, kp=2.0, ki=0.020, kd=12.0)

        mock_state = MockPIDTuningManagerState()
        mock_state._coordinator = None  # No coordinator — must NOT be needed

        async_control_heating = AsyncMock()
        async_write_ha_state = AsyncMock()

        manager = PIDTuningManager(
            thermostat_state=mock_state,
            pid_controller=mock_pid,
            gains_manager=gains_manager,
            async_control_heating=async_control_heating,
            async_write_ha_state=async_write_ha_state,
        )

        result = await manager.async_rollback_pid()

        # Returns the previous snapshot dict, not a boolean
        assert isinstance(result, dict)
        assert result["kp"] == pytest.approx(1.5)
        assert result["ki"] == pytest.approx(0.015)
        assert result["kd"] == pytest.approx(8.0)

        # Active gains should now reflect the rolled-back values
        from homeassistant.components.climate import HVACMode

        active = gains_manager.get_gains(HVACMode.HEAT)
        assert active.kp == pytest.approx(1.5)
        assert active.ki == pytest.approx(0.015)
        assert active.kd == pytest.approx(8.0)

        # Callbacks must be invoked
        async_control_heating.assert_called_once_with(calc_pid=True)
        async_write_ha_state.assert_called_once()

    async def test_rollback_raises_when_history_too_short(self):
        """H07: HomeAssistantError raised when fewer than 2 history entries."""
        gains_manager, mock_pid = self._make_real_gains_manager()
        # Only one entry in history (first set_gains call)
        gains_manager.set_gains(PIDChangeReason.SERVICE_CALL, kp=1.5, ki=0.015, kd=8.0)

        mock_state = MockPIDTuningManagerState()
        mock_state._coordinator = None

        manager = PIDTuningManager(
            thermostat_state=mock_state,
            pid_controller=mock_pid,
            gains_manager=gains_manager,
            async_control_heating=AsyncMock(),
            async_write_ha_state=AsyncMock(),
        )

        with pytest.raises(Exception, match="No previous gains"):
            await manager.async_rollback_pid()

    async def test_rollback_raises_when_history_empty(self):
        """H07: HomeAssistantError raised when history is empty."""
        gains_manager, mock_pid = self._make_real_gains_manager()
        # No set_gains calls — history is empty

        mock_state = MockPIDTuningManagerState()
        mock_state._coordinator = None

        manager = PIDTuningManager(
            thermostat_state=mock_state,
            pid_controller=mock_pid,
            gains_manager=gains_manager,
            async_control_heating=AsyncMock(),
            async_write_ha_state=AsyncMock(),
        )

        with pytest.raises(Exception, match="No previous gains"):
            await manager.async_rollback_pid()


def _undershoot_learner_with_cooling_history():
    from homeassistant.components.climate import HVACMode

    from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
    from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner

    learner = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
    for _ in range(8):
        learner.add_cycle_metrics(
            CycleMetrics(
                overshoot=0.0, undershoot=0.5, oscillations=0, settling_time=30, rise_time=30, starting_delta=0.2
            ),
            HVACMode.HEAT,
        )
    for _ in range(3):
        learner.add_cycle_metrics(CycleMetrics(overshoot=0.1, oscillations=0, settling_time=20.0), HVACMode.COOL)
    learner._cooling_convergence_confidence = 0.3
    learner.set_physics_baseline(100.0, 10.0, 2.0)
    return learner


def _manager_with_learner(learner, gains_manager):
    mock_state = MockPIDTuningManagerState()
    mock_state._kp, mock_state._ki, mock_state._kd = 100.0, 10.0, 2.0
    mock_state._coordinator = MagicMock()
    mock_state._coordinator.get_adaptive_learner.return_value = learner
    pid_controller = MagicMock(spec=PID)
    manager = PIDTuningManager(
        thermostat_state=mock_state,
        pid_controller=pid_controller,
        gains_manager=gains_manager,
        async_control_heating=AsyncMock(),
        async_write_ha_state=AsyncMock(),
    )
    return manager


@pytest.mark.asyncio
async def test_auto_apply_clears_only_applied_mode_and_keeps_integral():
    from homeassistant.components.climate import HVACMode

    learner = _undershoot_learner_with_cooling_history()
    gains_manager = MagicMock()
    manager = _manager_with_learner(learner, gains_manager)

    result = await manager.async_auto_apply_adaptive_pid(outdoor_temp=None, mode=HVACMode.HEAT)

    assert result["applied"] is True
    gains_manager.set_integral.assert_not_called()
    gains_manager.scale_integral.assert_not_called()
    assert learner.get_cycle_count(HVACMode.HEAT) == 0
    assert learner.get_cycle_count(HVACMode.COOL) == 3
    assert learner.get_convergence_confidence(HVACMode.COOL) == 0.3
    assert learner.is_in_validation_mode()


@pytest.mark.asyncio
async def test_auto_apply_keeps_safety_net_on_until_tuned():
    """The applied mode restarts at 'collecting', so the controller sees count 0 and keeps integral decay."""
    from homeassistant.components.climate import HVACMode

    learner = _undershoot_learner_with_cooling_history()
    manager = _manager_with_learner(learner, MagicMock())

    result = await manager.async_auto_apply_adaptive_pid(outdoor_temp=None, mode=HVACMode.HEAT)

    assert result["applied"] is True
    assert learner.get_auto_apply_count(HVACMode.HEAT) == 1
    manager._pid_controller.set_auto_apply_count.assert_called_once_with(0)


@pytest.mark.asyncio
async def test_rollback_clears_mode_history_and_restarts_cooldown():
    """Cycles run under the rolled-back gains must not feed the next recommendation."""
    from homeassistant.components.climate import HVACMode

    learner = _undershoot_learner_with_cooling_history()
    gains_manager = MagicMock()
    gains_manager.get_history.return_value = [
        {"kp": 100.0, "ki": 8.0, "kd": 2.0},
        {"kp": 100.0, "ki": 10.0, "kd": 2.0},
    ]
    manager = _manager_with_learner(learner, gains_manager)

    await manager.async_rollback_pid()

    assert learner.get_cycle_count(HVACMode.HEAT) == 0
    assert learner.get_cycle_count(HVACMode.COOL) == 3
    assert learner._last_adjustment_time is not None
    assert learner._cycles_since_last_adjustment == 0
