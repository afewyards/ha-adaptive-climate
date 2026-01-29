"""Tests for AutoModeSwitchingManager."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from custom_components.adaptive_thermostat.const import (
    CONF_AUTO_MODE_THRESHOLD,
    CONF_FORECAST_HOURS,
    CONF_MIN_SWITCH_INTERVAL,
    CONF_SEASON_THRESHOLDS,
    CONF_SUMMER_ABOVE,
    CONF_WINTER_BELOW,
)
from custom_components.adaptive_thermostat.managers.auto_mode_switching import (
    AutoModeSwitchingManager,
)


@pytest.fixture
def mock_hass():
    """Create mock HomeAssistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    hass.states = MagicMock()
    hass.services = MagicMock()
    return hass


@pytest.fixture
def mock_coordinator():
    """Create mock coordinator."""
    coordinator = MagicMock()
    coordinator.weather_entity = "weather.home"
    coordinator.get_active_zone_setpoints = MagicMock(return_value=[20.0, 21.0, 22.0])
    return coordinator


@pytest.fixture
def default_config():
    """Default auto mode switching config."""
    return {
        CONF_AUTO_MODE_THRESHOLD: 2.0,
        CONF_MIN_SWITCH_INTERVAL: 3600,
        CONF_FORECAST_HOURS: 6,
        CONF_SEASON_THRESHOLDS: {
            CONF_WINTER_BELOW: 12.0,
            CONF_SUMMER_ABOVE: 18.0,
        },
    }


class TestAutoModeSwitchingManager:
    """Test AutoModeSwitchingManager class."""

    def test_init_with_defaults(self, mock_hass, mock_coordinator, default_config):
        """Test manager initializes with default values."""
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        assert manager._threshold == 2.0
        assert manager._min_switch_interval == 3600
        assert manager._forecast_hours == 6
        assert manager._winter_below == 12.0
        assert manager._summer_above == 18.0
        assert manager._current_mode is None
        assert manager._last_switch == 0.0

    def test_init_with_custom_config(self, mock_hass, mock_coordinator):
        """Test manager initializes with custom config values."""
        config = {
            CONF_AUTO_MODE_THRESHOLD: 3.0,
            CONF_MIN_SWITCH_INTERVAL: 7200,
            CONF_FORECAST_HOURS: 12,
            CONF_SEASON_THRESHOLDS: {
                CONF_WINTER_BELOW: 10.0,
                CONF_SUMMER_ABOVE: 20.0,
            },
        }
        manager = AutoModeSwitchingManager(mock_hass, config, mock_coordinator)

        assert manager._threshold == 3.0
        assert manager._min_switch_interval == 7200
        assert manager._forecast_hours == 12
        assert manager._winter_below == 10.0
        assert manager._summer_above == 20.0

    def test_init_with_empty_config(self, mock_hass, mock_coordinator):
        """Test manager uses defaults when config is empty."""
        manager = AutoModeSwitchingManager(mock_hass, {}, mock_coordinator)

        assert manager._threshold == 2.0
        assert manager._min_switch_interval == 3600
        assert manager._forecast_hours == 6
        assert manager._winter_below == 12.0
        assert manager._summer_above == 18.0

    def test_properties(self, mock_hass, mock_coordinator, default_config):
        """Test public properties."""
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        assert manager.current_mode is None
        assert manager.last_switch_time == 0.0

        # Modify internal state and verify properties reflect it
        manager._current_mode = HVACMode.HEAT
        manager._last_switch = 12345.0

        assert manager.current_mode == HVACMode.HEAT
        assert manager.last_switch_time == 12345.0

    def test_placeholder_methods_raise_not_implemented(
        self, mock_hass, mock_coordinator, default_config
    ):
        """Test that placeholder methods raise NotImplementedError."""
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        with pytest.raises(NotImplementedError, match="Implemented in task 20"):
            manager.get_median_setpoint()

        with pytest.raises(NotImplementedError, match="Implemented in task 21"):
            manager.get_season()

        with pytest.raises(NotImplementedError, match="Implemented in task 20"):
            manager._get_forecast_median()

    @pytest.mark.asyncio
    async def test_async_placeholder_methods_raise_not_implemented(
        self, mock_hass, mock_coordinator, default_config
    ):
        """Test that async placeholder methods raise NotImplementedError."""
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        with pytest.raises(NotImplementedError, match="Implemented in task 21"):
            await manager._check_forecast()

        with pytest.raises(NotImplementedError, match="Implemented in task 22"):
            await manager.async_evaluate()

    def test_stores_coordinator_reference(self, mock_hass, mock_coordinator, default_config):
        """Test that manager stores coordinator reference."""
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        assert manager._coordinator is mock_coordinator

    def test_stores_hass_reference(self, mock_hass, mock_coordinator, default_config):
        """Test that manager stores hass reference."""
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        assert manager._hass is mock_hass

    def test_partial_season_thresholds_config(self, mock_hass, mock_coordinator):
        """Test with partial season_thresholds config."""
        config = {
            CONF_AUTO_MODE_THRESHOLD: 2.0,
            CONF_SEASON_THRESHOLDS: {
                CONF_WINTER_BELOW: 10.0,
                # summer_above missing - should use default
            },
        }
        manager = AutoModeSwitchingManager(mock_hass, config, mock_coordinator)

        assert manager._winter_below == 10.0
        assert manager._summer_above == 18.0  # default

    def test_missing_season_thresholds_config(self, mock_hass, mock_coordinator):
        """Test with missing season_thresholds section."""
        config = {
            CONF_AUTO_MODE_THRESHOLD: 2.0,
            # season_thresholds missing entirely
        }
        manager = AutoModeSwitchingManager(mock_hass, config, mock_coordinator)

        assert manager._winter_below == 12.0  # default
        assert manager._summer_above == 18.0  # default
