"""Tests for KeManager - Protocol-based refactoring.

This test suite verifies that KeManager works correctly with the new Protocol-based
approach using KeManagerState for read-only state access and explicit callbacks for actions.
"""
import pytest
import time
from unittest.mock import AsyncMock, Mock, MagicMock
from homeassistant.components.climate import HVACMode

from custom_components.adaptive_climate.managers.ke_manager import KeManager
from custom_components.adaptive_climate.adaptive.ke_learning import KeLearner
from custom_components.adaptive_climate.const import PIDChangeReason, HeatingType
from custom_components.adaptive_climate.protocols import KeManagerState


class MockKeManagerState:
    """Mock implementation of KeManagerState protocol for testing.

    This mock provides all read-only state that KeManager needs without
    requiring a full thermostat instance.
    """

    def __init__(self):
        """Initialize mock state with default values."""
        # Use __dict__ to set internal values directly
        self.__dict__['_current_temperature_value'] = 20.0
        self.__dict__['_target_temperature_value'] = 21.0
        self.__dict__['_ext_temp_value'] = 5.0
        self.__dict__['_cold_tolerance_value'] = 0.3
        self.__dict__['_hot_tolerance_value'] = 0.3

        # PIDState properties
        self.__dict__['_kp_value'] = 1.0
        self.__dict__['_ki_value'] = 0.1
        self.__dict__['_kd_value'] = 10.0
        self.__dict__['_ke_value'] = 0.0
        self.__dict__['_control_output_value'] = 50.0
        self.__dict__['_pid_control_p_value'] = 20.0
        self.__dict__['_pid_control_i_value'] = 15.0
        self.__dict__['_pid_control_d_value'] = 10.0
        self.__dict__['_pid_control_e_value'] = 5.0

        # HVACState properties
        self.__dict__['_hvac_mode_value'] = HVACMode.HEAT
        self.__dict__['_heating_type_value'] = HeatingType.RADIATOR
        self.__dict__['_is_device_active_value'] = True

        # Entity ID for logging
        self.entity_id = "climate.test_zone"

    @property
    def current_temperature(self):
        """Return current temperature."""
        return self.__dict__.get('_current_temperature_value')

    @current_temperature.setter
    def current_temperature(self, value):
        """Set current temperature."""
        self.__dict__['_current_temperature_value'] = value

    @property
    def target_temperature(self):
        """Return target temperature."""
        return self.__dict__.get('_target_temperature_value')

    @target_temperature.setter
    def target_temperature(self, value):
        """Set target temperature."""
        self.__dict__['_target_temperature_value'] = value

    @property
    def _ext_temp(self):
        """Return external temperature."""
        return self.__dict__.get('_ext_temp_value')

    @_ext_temp.setter
    def _ext_temp(self, value):
        """Set external temperature."""
        self.__dict__['_ext_temp_value'] = value

    @property
    def _cold_tolerance(self):
        """Return cold tolerance."""
        return self.__dict__.get('_cold_tolerance_value', 0.3)

    @_cold_tolerance.setter
    def _cold_tolerance(self, value):
        """Set cold tolerance."""
        self.__dict__['_cold_tolerance_value'] = value

    @property
    def _hot_tolerance(self):
        """Return hot tolerance."""
        return self.__dict__.get('_hot_tolerance_value', 0.3)

    @_hot_tolerance.setter
    def _hot_tolerance(self, value):
        """Set hot tolerance."""
        self.__dict__['_hot_tolerance_value'] = value

    @property
    def _kp(self):
        """Return proportional gain."""
        return self.__dict__.get('_kp_value', 1.0)

    @property
    def _ki(self):
        """Return integral gain."""
        return self.__dict__.get('_ki_value', 0.1)

    @property
    def _kd(self):
        """Return derivative gain."""
        return self.__dict__.get('_kd_value', 10.0)

    @property
    def _ke(self):
        """Return outdoor compensation gain."""
        return self.__dict__.get('_ke_value', 0.0)

    @_ke.setter
    def _ke(self, value):
        """Set outdoor compensation gain."""
        self.__dict__['_ke_value'] = value

    @property
    def _control_output(self):
        """Return control output."""
        return self.__dict__.get('_control_output_value', 50.0)

    @property
    def pid_control_p(self):
        """Return P component."""
        return self.__dict__.get('_pid_control_p_value')

    @property
    def pid_control_i(self):
        """Return I component."""
        return self.__dict__.get('_pid_control_i_value')

    @property
    def pid_control_d(self):
        """Return D component."""
        return self.__dict__.get('_pid_control_d_value')

    @property
    def pid_control_e(self):
        """Return E component."""
        return self.__dict__.get('_pid_control_e_value')

    @property
    def _hvac_mode(self):
        """Return HVAC mode."""
        return self.__dict__.get('_hvac_mode_value', HVACMode.HEAT)

    @_hvac_mode.setter
    def _hvac_mode(self, value):
        """Set HVAC mode."""
        self.__dict__['_hvac_mode_value'] = value

    @property
    def heating_type(self):
        """Return heating type."""
        return self.__dict__.get('_heating_type_value')

    @property
    def _is_device_active(self):
        """Return device active state."""
        return self.__dict__.get('_is_device_active_value')


@pytest.fixture
def mock_state():
    """Create a mock KeManagerState for testing."""
    return MockKeManagerState()


@pytest.fixture
def mock_thermostat():
    """Create a minimal mock thermostat for backward compatibility tests."""
    thermostat = Mock()
    thermostat.entity_id = "climate.test_zone"
    return thermostat


@pytest.fixture
def mock_ke_learner():
    """Create a mock KeLearner."""
    learner = Mock(spec=KeLearner)
    learner.enabled = False
    learner.current_ke = 0.5
    learner.add_observation = Mock()
    learner.calculate_ke_adjustment = Mock(return_value=0.6)
    learner.apply_ke_adjustment = Mock()
    learner.get_observations_summary = Mock(return_value={
        "count": 10,
        "outdoor_temp_range": "5-15",
        "correlation": 0.8
    })
    learner.enable = Mock()
    return learner


@pytest.fixture
def mock_gains_manager():
    """Create a mock PIDGainsManager."""
    manager = Mock()
    manager.set_gains = Mock()
    return manager


@pytest.fixture
def action_callbacks():
    """Create action callbacks for KeManager."""
    return {
        'async_control_heating': AsyncMock(),
        'async_write_ha_state': AsyncMock(),
    }


# =============================================================================
# Protocol-based Initialization Tests
# =============================================================================

class TestKeManagerProtocolInitialization:
    """Test KeManager initialization with KeManagerState protocol."""

    def test_initialization_with_protocol_state(
        self, mock_thermostat, mock_state, mock_ke_learner, mock_gains_manager, action_callbacks
    ):
        """Test that KeManager can be initialized with a KeManagerState protocol object."""
        # Old callback-based approach (for comparison/backward compat)
        manager_old = KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
            get_is_pid_converged=lambda: True,
            gains_manager=mock_gains_manager,
        )

        assert manager_old is not None
        assert manager_old._ke_learner == mock_ke_learner
        assert manager_old._gains_manager == mock_gains_manager
        assert manager_old.steady_state_start is None
        assert manager_old.last_ke_observation_time is None

    def test_state_access_through_callbacks(
        self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks
    ):
        """Test that all state reading works through callbacks."""
        manager = KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
        )

        # Verify callbacks work
        assert manager._get_hvac_mode() == HVACMode.HEAT
        assert manager._get_current_temp() == 20.0
        assert manager._get_target_temp() == 21.0
        assert manager._get_ext_temp() == 5.0
        assert manager._get_control_output() == 50.0
        assert manager._get_cold_tolerance() == 0.3
        assert manager._get_hot_tolerance() == 0.3
        assert manager._get_ke() == 0.0


# =============================================================================
# Steady State Detection Tests
# =============================================================================

class TestKeManagerSteadyState:
    """Test steady state detection logic."""

    @pytest.fixture
    def manager(self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks):
        """Create KeManager with callbacks."""
        return KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
        )

    def test_not_steady_when_hvac_off(self, manager, mock_state):
        """Test steady state is False when HVAC mode is OFF."""
        mock_state._hvac_mode = HVACMode.OFF

        assert manager.is_at_steady_state() is False
        assert manager.steady_state_start is None

    def test_not_steady_when_temp_none(self, manager, mock_state):
        """Test steady state is False when temperature is None."""
        mock_state.current_temperature = None

        assert manager.is_at_steady_state() is False
        assert manager.steady_state_start is None

    def test_not_steady_when_outside_tolerance(self, manager, mock_state):
        """Test steady state is False when temperature outside tolerance."""
        mock_state.current_temperature = 18.0  # 3°C below target
        mock_state.target_temperature = 21.0

        assert manager.is_at_steady_state() is False
        assert manager.steady_state_start is None

    def test_steady_state_tracking_starts(self, manager, mock_state):
        """Test that steady state tracking starts when within tolerance."""
        mock_state.current_temperature = 20.8  # Within 0.3°C tolerance
        mock_state.target_temperature = 21.0

        # First call should start tracking
        result = manager.is_at_steady_state()

        # Should have started tracking but not yet reached duration
        assert manager.steady_state_start is not None
        assert result is False  # Not yet reached required duration

    def test_steady_state_achieved_after_duration(self, manager, mock_state):
        """Test that steady state is True after maintaining for required duration."""
        from custom_components.adaptive_climate import const

        mock_state.current_temperature = 20.8
        mock_state.target_temperature = 21.0

        # Start tracking
        manager.is_at_steady_state()

        # Simulate time passing (mock the steady state start time)
        required_seconds = const.KE_STEADY_STATE_DURATION * 60
        manager._steady_state_start = time.monotonic() - required_seconds - 1

        # Should now be at steady state
        assert manager.is_at_steady_state() is True

    def test_steady_state_resets_when_temp_leaves_tolerance(self, manager, mock_state):
        """Test that steady state tracking resets when temp leaves tolerance."""
        mock_state.current_temperature = 20.8
        mock_state.target_temperature = 21.0

        # Start tracking
        manager.is_at_steady_state()
        assert manager.steady_state_start is not None

        # Temperature leaves tolerance
        mock_state.current_temperature = 18.0
        manager.is_at_steady_state()

        # Should have reset
        assert manager.steady_state_start is None

    def test_tolerance_uses_maximum_of_cold_hot(self, manager, mock_state):
        """Test that tolerance uses max of cold_tolerance and hot_tolerance."""
        mock_state._cold_tolerance = 0.5
        mock_state._hot_tolerance = 0.2
        mock_state.current_temperature = 20.4  # 0.6°C below target
        mock_state.target_temperature = 21.0

        # 0.6°C is outside 0.5°C tolerance (max of 0.5 and 0.2)
        assert manager.is_at_steady_state() is False

        # 0.4°C is within 0.5°C tolerance
        mock_state.current_temperature = 20.6
        manager.is_at_steady_state()
        assert manager.steady_state_start is not None


# =============================================================================
# Ke Observation Recording Tests
# =============================================================================

class TestKeManagerObservationRecording:
    """Test Ke observation recording logic."""

    @pytest.fixture
    def manager_with_converged(self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks, mock_gains_manager):
        """Create KeManager with PID converged callback."""
        return KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
            get_is_pid_converged=lambda: True,
            gains_manager=mock_gains_manager,
        )

    def test_no_observation_when_learner_disabled_and_not_converged(
        self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks
    ):
        """Test that no observation is recorded when learner disabled and PID not converged."""
        manager = KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
            get_is_pid_converged=lambda: False,  # Not converged
        )

        mock_ke_learner.enabled = False
        manager.maybe_record_observation()

        # Should not enable learner or record observation
        mock_ke_learner.enable.assert_not_called()
        mock_ke_learner.add_observation.assert_not_called()

    def test_learner_enabled_when_pid_converges(
        self, manager_with_converged, mock_ke_learner, mock_gains_manager
    ):
        """Test that learner is enabled and physics Ke applied when PID converges."""
        mock_ke_learner.enabled = False
        mock_ke_learner.current_ke = 0.5

        manager_with_converged.maybe_record_observation()

        # Should enable learner
        mock_ke_learner.enable.assert_called_once()

        # Should apply physics Ke via gains manager
        mock_gains_manager.set_gains.assert_called_once_with(
            PIDChangeReason.KE_PHYSICS,
            ke=0.5,
        )

    def test_observation_recorded_at_steady_state(
        self, manager_with_converged, mock_state, mock_ke_learner
    ):
        """Test that observation is recorded when at steady state."""
        from custom_components.adaptive_climate import const

        mock_ke_learner.enabled = True

        # Set up steady state
        mock_state.current_temperature = 20.8
        mock_state.target_temperature = 21.0
        mock_state._ext_temp = 5.0

        # Simulate being at steady state
        manager_with_converged.is_at_steady_state()
        required_seconds = const.KE_STEADY_STATE_DURATION * 60
        manager_with_converged._steady_state_start = time.monotonic() - required_seconds - 1

        manager_with_converged.maybe_record_observation()

        # Should record observation
        mock_ke_learner.add_observation.assert_called_once_with(
            outdoor_temp=5.0,
            pid_output=50.0,
            indoor_temp=20.8,  # Uses actual current temperature
            target_temp=21.0,
        )
        assert manager_with_converged.last_ke_observation_time is not None

    def test_observation_rate_limited(
        self, manager_with_converged, mock_state, mock_ke_learner
    ):
        """Test that observations are rate limited to 5 minutes."""
        from custom_components.adaptive_climate import const

        mock_ke_learner.enabled = True

        # Set up steady state
        mock_state.current_temperature = 20.8
        mock_state.target_temperature = 21.0
        manager_with_converged.is_at_steady_state()
        required_seconds = const.KE_STEADY_STATE_DURATION * 60
        manager_with_converged._steady_state_start = time.monotonic() - required_seconds - 1

        # Record first observation
        manager_with_converged.maybe_record_observation()
        assert mock_ke_learner.add_observation.call_count == 1

        # Try to record again immediately
        manager_with_converged.maybe_record_observation()

        # Should not record again (still only 1 call)
        assert mock_ke_learner.add_observation.call_count == 1

        # Simulate 5+ minutes passing
        manager_with_converged._last_ke_observation_time = time.monotonic() - 301

        manager_with_converged.maybe_record_observation()

        # Should record again
        assert mock_ke_learner.add_observation.call_count == 2

    def test_no_observation_when_no_outdoor_temp(
        self, manager_with_converged, mock_state, mock_ke_learner
    ):
        """Test that no observation is recorded when outdoor temp unavailable."""
        from custom_components.adaptive_climate import const

        mock_ke_learner.enabled = True
        mock_state._ext_temp = None

        # Set up steady state
        mock_state.current_temperature = 20.8
        mock_state.target_temperature = 21.0
        manager_with_converged.is_at_steady_state()
        required_seconds = const.KE_STEADY_STATE_DURATION * 60
        manager_with_converged._steady_state_start = time.monotonic() - required_seconds - 1

        manager_with_converged.maybe_record_observation()

        # Should not record observation
        mock_ke_learner.add_observation.assert_not_called()


# =============================================================================
# Adaptive Ke Application Tests
# =============================================================================

class TestKeManagerAdaptiveApplication:
    """Test adaptive Ke application logic."""

    @pytest.fixture
    def manager(self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks, mock_gains_manager):
        """Create KeManager for testing."""
        return KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
            gains_manager=mock_gains_manager,
        )

    @pytest.mark.asyncio
    async def test_apply_adaptive_ke_success(
        self, manager, mock_ke_learner, mock_gains_manager, action_callbacks, mock_state
    ):
        """Test successful adaptive Ke application."""
        mock_ke_learner.enabled = True
        mock_ke_learner.calculate_ke_adjustment.return_value = 0.6
        mock_state._ke = 0.4

        await manager.async_apply_adaptive_ke()

        # Should apply adjustment
        mock_ke_learner.apply_ke_adjustment.assert_called_once_with(0.6)

        # Should set Ke via gains manager
        mock_gains_manager.set_gains.assert_called_once_with(
            PIDChangeReason.KE_LEARNING,
            ke=0.6,
        )

        # Should trigger control and state update
        action_callbacks['async_control_heating'].assert_called_once_with(calc_pid=True)
        action_callbacks['async_write_ha_state'].assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_adaptive_ke_no_learner(self, manager, action_callbacks):
        """Test that apply fails gracefully when no learner exists."""
        manager._ke_learner = None

        await manager.async_apply_adaptive_ke()

        # Should not crash or call callbacks
        action_callbacks['async_control_heating'].assert_not_called()
        action_callbacks['async_write_ha_state'].assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_adaptive_ke_learner_disabled(
        self, manager, mock_ke_learner, action_callbacks
    ):
        """Test that apply fails when learner not enabled."""
        mock_ke_learner.enabled = False

        await manager.async_apply_adaptive_ke()

        # Should not apply or call callbacks
        mock_ke_learner.apply_ke_adjustment.assert_not_called()
        action_callbacks['async_control_heating'].assert_not_called()
        action_callbacks['async_write_ha_state'].assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_adaptive_ke_insufficient_data(
        self, manager, mock_ke_learner, action_callbacks
    ):
        """Test that apply fails when insufficient data."""
        mock_ke_learner.enabled = True
        mock_ke_learner.calculate_ke_adjustment.return_value = None  # Insufficient data

        await manager.async_apply_adaptive_ke()

        # Should not apply or call callbacks
        mock_ke_learner.apply_ke_adjustment.assert_not_called()
        action_callbacks['async_control_heating'].assert_not_called()
        action_callbacks['async_write_ha_state'].assert_not_called()


# =============================================================================
# State Restoration Tests
# =============================================================================

class TestKeManagerStateRestoration:
    """Test state restoration."""

    @pytest.fixture
    def manager(self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks):
        """Create KeManager for testing."""
        return KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
        )

    def test_restore_state_full(self, manager):
        """Test restoring all state values."""
        steady_start = time.monotonic() - 100
        observation_time = time.monotonic() - 50

        manager.restore_state(
            steady_state_start=steady_start,
            last_ke_observation_time=observation_time,
        )

        assert manager.steady_state_start == steady_start
        assert manager.last_ke_observation_time == observation_time

    def test_restore_state_partial(self, manager):
        """Test restoring only some state values."""
        steady_start = time.monotonic() - 100

        manager.restore_state(steady_state_start=steady_start)

        assert manager.steady_state_start == steady_start
        assert manager.last_ke_observation_time is None

    def test_restore_state_none_values(self, manager):
        """Test restoring with None values."""
        manager.restore_state(
            steady_state_start=None,
            last_ke_observation_time=None,
        )

        assert manager.steady_state_start is None
        assert manager.last_ke_observation_time is None


# =============================================================================
# Backward Compatibility Tests
# =============================================================================

class TestKeManagerBackwardCompatibility:
    """Test backward compatibility with fallback set_ke."""

    @pytest.mark.asyncio
    async def test_fallback_set_ke_when_no_gains_manager(
        self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks
    ):
        """Test that set_ke fallback is used when gains_manager is None."""
        set_ke_called = []

        def mock_set_ke(ke):
            set_ke_called.append(ke)
            mock_state._ke = ke

        manager = KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=mock_set_ke,
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
            get_is_pid_converged=lambda: True,
            gains_manager=None,  # No gains manager
        )

        mock_ke_learner.enabled = False
        mock_ke_learner.current_ke = 0.7

        # Trigger Ke physics application
        manager.maybe_record_observation()

        # Should use fallback set_ke
        assert len(set_ke_called) == 1
        assert set_ke_called[0] == 0.7


# =============================================================================
# Properties and Utilities Tests
# =============================================================================

class TestKeManagerPropertiesAndUtilities:
    """Test KeManager properties and utility methods."""

    @pytest.fixture
    def manager(self, mock_thermostat, mock_state, mock_ke_learner, action_callbacks):
        """Create KeManager for testing."""
        return KeManager(
            thermostat=mock_thermostat,
            ke_learner=mock_ke_learner,
            get_hvac_mode=lambda: mock_state._hvac_mode,
            get_current_temp=lambda: mock_state.current_temperature,
            get_target_temp=lambda: mock_state.target_temperature,
            get_ext_temp=lambda: mock_state._ext_temp,
            get_control_output=lambda: mock_state._control_output,
            get_cold_tolerance=lambda: mock_state._cold_tolerance,
            get_hot_tolerance=lambda: mock_state._hot_tolerance,
            get_ke=lambda: mock_state._ke,
            set_ke=lambda ke: setattr(mock_state, '_ke', ke),
            get_pid_controller=Mock(),
            async_control_heating=action_callbacks['async_control_heating'],
            async_write_ha_state=action_callbacks['async_write_ha_state'],
        )

    def test_ke_learner_property(self, manager, mock_ke_learner):
        """Test ke_learner property returns learner."""
        assert manager.ke_learner == mock_ke_learner

    def test_update_ke_learner(self, manager):
        """Test updating the KeLearner instance."""
        new_learner = Mock(spec=KeLearner)
        manager.update_ke_learner(new_learner)

        assert manager.ke_learner == new_learner

    def test_update_ke_learner_to_none(self, manager):
        """Test disabling Ke learning by setting learner to None."""
        manager.update_ke_learner(None)

        assert manager.ke_learner is None
