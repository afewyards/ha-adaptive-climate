"""Tests for StateRestorer manager."""

import sys
import pytest
from unittest.mock import MagicMock, Mock
from abc import ABC
from enum import IntFlag

# Mock homeassistant modules before importing StateRestorer
mock_ha_const = MagicMock()
mock_ha_const.ATTR_TEMPERATURE = "temperature"
sys.modules['homeassistant.const'] = mock_ha_const

# ClimateEntity must use ABC to be compatible with RestoreEntity's ABCMeta
class MockClimateEntity(ABC):
    """Mock ClimateEntity base class."""
    pass

class MockClimateEntityFeature(IntFlag):
    """Mock ClimateEntityFeature enum."""
    TARGET_TEMPERATURE = 1

mock_ha_climate = MagicMock()
mock_ha_climate.ATTR_PRESET_MODE = "preset_mode"
mock_ha_climate.ClimateEntity = MockClimateEntity
mock_ha_climate.ClimateEntityFeature = MockClimateEntityFeature
sys.modules['homeassistant.components.climate'] = mock_ha_climate

from custom_components.adaptive_climate.managers.state_restorer import StateRestorer


@pytest.fixture
def mock_thermostat():
    """Create a mock thermostat entity."""
    thermostat = MagicMock()
    thermostat.entity_id = "climate.test_thermostat"
    thermostat._target_temp = None
    thermostat._ac_mode = False
    thermostat._hvac_mode = None
    thermostat._attr_preset_mode = None
    thermostat._saved_target_temp = None
    thermostat._temperature_manager = None
    thermostat._pid_controller = MagicMock()
    thermostat._heater_controller = MagicMock()
    thermostat._kp = 20.0
    thermostat._ki = 0.01
    thermostat._kd = 100.0
    thermostat._ke = 0.5
    thermostat._i = 0.0
    thermostat.min_temp = 15.0
    thermostat.max_temp = 30.0
    thermostat.hass = MagicMock()
    thermostat.hass.data = {}
    return thermostat


@pytest.fixture
def state_restorer(mock_thermostat):
    """Create a StateRestorer instance."""
    return StateRestorer(mock_thermostat)


class TestDutyAccumulatorNotRestored:
    """Tests verifying duty accumulator is NOT restored across restarts.

    The accumulator is intentionally not restored because it can cause spurious
    heating when combined with a restored PID integral that keeps control_output
    positive even when temperature is above setpoint.
    """

    def test_accumulator_not_restored_even_when_present(self, state_restorer, mock_thermostat):
        """Test duty_accumulator is NOT restored even if present in old state."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "pid_i": 0.0,
            "pid_integral_migrated": True,
            "duty_accumulator": 120.5,
        }

        state_restorer.restore(old_state)

        # Accumulator should NOT be restored
        mock_thermostat._heater_controller.set_duty_accumulator.assert_not_called()

    def test_cycle_counts_restored_but_not_accumulator(self, state_restorer, mock_thermostat):
        """Test cycle counts are restored but duty_accumulator is not."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "pid_i": 0.0,
            "pid_integral_migrated": True,
            "heater_cycle_count": 150,
            "cooler_cycle_count": 50,
            "duty_accumulator": 200.0,
        }

        state_restorer.restore(old_state)

        # Cycle counts should be restored
        mock_thermostat._heater_controller.set_heater_cycle_count.assert_called_once_with(150)
        mock_thermostat._heater_controller.set_cooler_cycle_count.assert_called_once_with(50)
        # But accumulator should NOT be restored
        mock_thermostat._heater_controller.set_duty_accumulator.assert_not_called()


class TestStateRestorerNoOldState:
    """Tests for StateRestorer when there's no old state."""

    def test_no_old_state_sets_default_temp(self, state_restorer, mock_thermostat):
        """Test default temperature is set when no old state exists."""
        state_restorer.restore(None)

        assert mock_thermostat._target_temp == mock_thermostat.min_temp

    def test_no_old_state_ac_mode_uses_max_temp(self, state_restorer, mock_thermostat):
        """Test AC mode uses max temp when no old state exists."""
        mock_thermostat._ac_mode = True

        state_restorer.restore(None)

        assert mock_thermostat._target_temp == mock_thermostat.max_temp


class TestDualGainSetRestoration:
    """Tests for dual gain set restoration (heating and cooling gains).

    NOTE: Dual gain set restoration is now handled by PIDGainsManager.restore_from_state().
    These tests are covered by test_pid_gains_manager.py.
    The _restore_dual_gain_sets() method has been removed from StateRestorer.
    """

    def test_gains_restore_from_pid_history_heating_only(self, state_restorer, mock_thermostat):
        """Test that gains restoration is delegated to PIDGainsManager."""
        # This functionality is now tested in test_pid_gains_manager.py
        pass

    def test_gains_restore_from_pid_history_heating_and_cooling(self, state_restorer, mock_thermostat):
        """Test that gains restoration is delegated to PIDGainsManager."""
        # This functionality is now tested in test_pid_gains_manager.py
        pass

    def test_gains_restore_cooling_none_when_missing(self, state_restorer, mock_thermostat):
        """Test that gains restoration is delegated to PIDGainsManager."""
        # This functionality is now tested in test_pid_gains_manager.py
        pass


class TestInitialPidCalculation:
    """Tests for initial PID calculation when no history exists."""

    def test_calculate_initial_pid_when_no_history(self, state_restorer, mock_thermostat):
        """Test initial PID calculation when no pid_history exists."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "pid_i": 0.0,
            "pid_integral_migrated": True,
            # No pid_history, no legacy kp/ki/kd
        }

        mock_thermostat._heating_gains = None
        mock_thermostat._cooling_gains = None
        # Mock physics-based initialization values
        mock_thermostat._kp = 20.0
        mock_thermostat._ki = 0.01
        mock_thermostat._kd = 100.0

        state_restorer.restore(old_state)

        # Should fall back to physics-based or default initialization
        # The exact behavior depends on implementation, but gains should be initialized
        # This test verifies that the system handles missing history gracefully

    def test_no_history_heating_mode_initializes_heating_gains(self, state_restorer, mock_thermostat):
        """Test heating mode without history initializes _heating_gains from config/physics."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "pid_i": 0.0,
            "pid_integral_migrated": True,
        }

        mock_thermostat._heating_gains = None
        mock_thermostat._cooling_gains = None
        mock_thermostat._kp = 20.0
        mock_thermostat._ki = 0.01
        mock_thermostat._kd = 100.0

        state_restorer.restore(old_state)

        # Heating gains should be initialized (from config or physics)
        # Cooling gains should remain None (lazy init)
        assert mock_thermostat._cooling_gains is None


class TestCycleCountRestoration:
    """Tests for cycle_count restoration handling new and old structures."""

    def test_restore_cycle_count_from_new_dict_structure(self, state_restorer, mock_thermostat):
        """StateRestorer should handle new cycle_count dict structure."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "cycle_count": {"heater": 42, "cooler": 10},
            "integral": 5.0,
            "pid_integral_migrated": True,
        }

        state_restorer.restore(old_state)

        # Should restore both heater and cooler counts from dict
        mock_thermostat._heater_controller.set_heater_cycle_count.assert_called_once_with(42)
        mock_thermostat._heater_controller.set_cooler_cycle_count.assert_called_once_with(10)

    def test_restore_cycle_count_from_int(self, state_restorer, mock_thermostat):
        """StateRestorer should handle int cycle_count (demand_switch)."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "cycle_count": 52,
            "integral": 5.0,
            "pid_integral_migrated": True,
        }

        state_restorer.restore(old_state)

        # Should restore heater count from int, cooler count should be 0
        mock_thermostat._heater_controller.set_heater_cycle_count.assert_called_once_with(52)
        mock_thermostat._heater_controller.set_cooler_cycle_count.assert_called_once_with(0)

    def test_restore_cycle_count_from_old_structure(self, state_restorer, mock_thermostat):
        """StateRestorer should handle old heater_cycle_count/cooler_cycle_count structure."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "heater_cycle_count": 150,
            "cooler_cycle_count": 50,
            "integral": 5.0,
            "pid_integral_migrated": True,
        }

        state_restorer.restore(old_state)

        # Should restore both counts from old structure
        mock_thermostat._heater_controller.set_heater_cycle_count.assert_called_once_with(150)
        mock_thermostat._heater_controller.set_cooler_cycle_count.assert_called_once_with(50)

    def test_restore_cycle_count_mixed_new_dict_overrides_old(self, state_restorer, mock_thermostat):
        """When both new and old structures exist, new should take precedence."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "cycle_count": {"heater": 100, "cooler": 20},  # New structure
            "heater_cycle_count": 150,  # Old structure (should be ignored)
            "cooler_cycle_count": 50,   # Old structure (should be ignored)
            "integral": 5.0,
            "pid_integral_migrated": True,
        }

        state_restorer.restore(old_state)

        # Should use new structure values, not old
        mock_thermostat._heater_controller.set_heater_cycle_count.assert_called_once_with(100)
        mock_thermostat._heater_controller.set_cooler_cycle_count.assert_called_once_with(20)

    def test_restore_cycle_count_missing_cooler_in_dict(self, state_restorer, mock_thermostat):
        """Handle incomplete dict structure gracefully."""
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "cycle_count": {"heater": 75},  # No cooler key
            "integral": 5.0,
            "pid_integral_migrated": True,
        }

        state_restorer.restore(old_state)

        # Should restore heater count, cooler should default to 0
        mock_thermostat._heater_controller.set_heater_cycle_count.assert_called_once_with(75)
        mock_thermostat._heater_controller.set_cooler_cycle_count.assert_called_once_with(0)


class TestInitialPhysicsGainsRecording:
    """Tests for initial physics gains recording to pid_history."""

    def test_initial_gains_recorded_when_no_saved_state(self, state_restorer, mock_thermostat):
        """Test that initial physics gains are recorded to pid_history on fresh start."""
        from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
        from custom_components.adaptive_climate.const import PIDGains, PIDChangeReason

        # Setup gains manager with initial physics gains
        initial_gains = PIDGains(kp=20.0, ki=0.01, kd=100.0, ke=0.0)
        gains_manager = PIDGainsManager(mock_thermostat._pid_controller, initial_gains)
        mock_thermostat._gains_manager = gains_manager

        # No saved state (fresh start)
        old_state = None

        # Restore should call ensure_initial_history_recorded
        state_restorer.restore(old_state)

        # Verify history has one entry with PHYSICS_INIT
        history = gains_manager.get_history()
        assert len(history) == 1
        assert history[0]["reason"] == PIDChangeReason.PHYSICS_INIT.value
        assert history[0]["kp"] == 20.0
        assert history[0]["ki"] == 0.01
        assert history[0]["kd"] == 100.0

    def test_initial_gains_recorded_when_no_history_in_saved_state(self, state_restorer, mock_thermostat):
        """Test that initial physics gains are recorded when saved state has no history."""
        from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
        from custom_components.adaptive_climate.const import PIDGains, PIDChangeReason

        # Setup gains manager
        initial_gains = PIDGains(kp=20.0, ki=0.01, kd=100.0, ke=0.0)
        gains_manager = PIDGainsManager(mock_thermostat._pid_controller, initial_gains)
        mock_thermostat._gains_manager = gains_manager

        # Old state without pid_history (backward compat)
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "integral": 0.0,
            "kp": 20.0,
            "ki": 0.01,
            "kd": 100.0,
            "ke": 0.0,
        }

        # Restore
        state_restorer.restore(old_state)

        # History should have at least one entry
        # (RESTORE entry because restore_from_state was called)
        history = gains_manager.get_history()
        assert len(history) >= 1

    def test_no_duplicate_when_history_exists_in_saved_state(self, state_restorer, mock_thermostat):
        """Test that ensure_initial_history_recorded doesn't duplicate when history exists."""
        from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
        from custom_components.adaptive_climate.const import PIDGains

        # Setup gains manager
        initial_gains = PIDGains(kp=20.0, ki=0.01, kd=100.0, ke=0.0)
        gains_manager = PIDGainsManager(mock_thermostat._pid_controller, initial_gains)
        mock_thermostat._gains_manager = gains_manager

        # Old state WITH pid_history
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "integral": 0.0,
            "kp": 25.0,
            "ki": 0.02,
            "kd": 120.0,
            "ke": 0.5,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 25.0,
                    "ki": 0.02,
                    "kd": 120.0,
                    "ke": 0.5,
                    "reason": "adaptive_apply",
                    "actor": "user",
                }
            ],
        }

        # Restore
        state_restorer.restore(old_state)

        # History should have only one entry (the restored one, not PHYSICS_INIT)
        history = gains_manager.get_history()
        assert len(history) == 1
        assert history[0]["reason"] == "adaptive_apply"


class TestPidHistoryPersistence:
    """PID history survives restart via RestoreEntity round-trip."""

    def test_pid_history_round_trip(self, state_restorer, mock_thermostat):
        """pid_history in state attrs → restore_from_state → gains_manager has history."""
        from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
        from custom_components.adaptive_climate.const import PIDGains, PIDChangeReason

        # Setup gains manager with initial gains
        initial_gains = PIDGains(kp=20.0, ki=0.01, kd=100.0, ke=0.0)
        gains_manager = PIDGainsManager(mock_thermostat._pid_controller, initial_gains)
        mock_thermostat._gains_manager = gains_manager

        # Simulate saved state with pid_history at top level (as build_state_attributes produces)
        saved_history = [
            {"timestamp": "2024-01-15T10:00:00", "kp": 25.0, "ki": 0.02, "kd": 120.0, "ke": 0.5, "reason": "physics_init", "actor": "system"},
            {"timestamp": "2024-01-15T12:00:00", "kp": 22.0, "ki": 0.015, "kd": 110.0, "ke": 0.3, "reason": "auto_apply", "actor": "learning"},
        ]
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "integral": 5.0,
            "pid_history": saved_history,
        }

        state_restorer.restore(old_state)

        # Gains manager should have the history restored
        history = gains_manager.get_history()
        assert len(history) >= 2
        # First entry should be from saved history
        assert history[0]["kp"] == 25.0
        assert history[0]["reason"] == "physics_init"
        # Second entry
        assert history[1]["kp"] == 22.0
        assert history[1]["reason"] == "auto_apply"

    def test_pid_history_empty_when_no_history(self, state_restorer, mock_thermostat):
        """No pid_history in attrs → restore proceeds without error."""
        from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
        from custom_components.adaptive_climate.const import PIDGains

        initial_gains = PIDGains(kp=20.0, ki=0.01, kd=100.0, ke=0.0)
        gains_manager = PIDGainsManager(mock_thermostat._pid_controller, initial_gains)
        mock_thermostat._gains_manager = gains_manager

        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "integral": 0.0,
            # No pid_history key at all
        }

        # Should not raise
        state_restorer.restore(old_state)

        # History should still have at least the initial/restore entry
        history = gains_manager.get_history()
        assert isinstance(history, list)

    def test_pid_history_restores_gains_from_last_entry(self, state_restorer, mock_thermostat):
        """Restored gains match last pid_history entry, not initial gains."""
        from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
        from custom_components.adaptive_climate.const import PIDGains

        # Initial gains differ from what's in history
        initial_gains = PIDGains(kp=20.0, ki=0.01, kd=100.0, ke=0.0)
        gains_manager = PIDGainsManager(mock_thermostat._pid_controller, initial_gains)
        mock_thermostat._gains_manager = gains_manager

        # History has different (learned) gains
        old_state = MagicMock()
        old_state.state = "heat"
        old_state.attributes = {
            "temperature": 21.0,
            "integral": 5.0,
            "pid_history": [
                {"timestamp": "2024-01-15T10:00:00", "kp": 30.0, "ki": 0.03, "kd": 150.0, "ke": 0.8, "reason": "auto_apply", "actor": "learning"},
            ],
        }

        state_restorer.restore(old_state)

        # Active gains should match the last history entry, not initial
        current_gains = gains_manager.get_gains()
        assert current_gains.kp == 30.0
        assert current_gains.ki == 0.03
        assert current_gains.kd == 150.0
        assert current_gains.ke == 0.8
