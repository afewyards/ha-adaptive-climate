"""Tests for StateRestorer manager."""

import sys
import pytest
from unittest.mock import MagicMock, Mock

# Mock homeassistant modules before importing StateRestorer
mock_ha_const = MagicMock()
mock_ha_const.ATTR_TEMPERATURE = "temperature"
sys.modules['homeassistant.const'] = mock_ha_const

mock_ha_climate = MagicMock()
mock_ha_climate.ATTR_PRESET_MODE = "preset_mode"
sys.modules['homeassistant.components.climate'] = mock_ha_climate

from custom_components.adaptive_thermostat.managers.state_restorer import StateRestorer


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
