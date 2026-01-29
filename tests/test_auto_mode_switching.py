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

    @pytest.mark.asyncio
    async def test_async_placeholder_methods_raise_not_implemented(
        self, mock_hass, mock_coordinator, default_config
    ):
        """Test that async placeholder methods raise NotImplementedError."""
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

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


class TestGetMedianSetpoint:
    """Tests for get_median_setpoint method."""

    def test_returns_median_of_zone_setpoints(self, mock_hass, mock_coordinator, default_config):
        """Test returns correct median from zone setpoints."""
        mock_coordinator.get_active_zone_setpoints.return_value = [20.0, 21.0, 22.0]
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_median_setpoint()

        assert result == 21.0

    def test_returns_none_when_no_active_zones(self, mock_hass, mock_coordinator, default_config):
        """Test returns None when no active zones."""
        mock_coordinator.get_active_zone_setpoints.return_value = []
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_median_setpoint()

        assert result is None

    def test_returns_median_with_even_number_of_zones(self, mock_hass, mock_coordinator, default_config):
        """Test median calculation with even number of zones."""
        mock_coordinator.get_active_zone_setpoints.return_value = [20.0, 22.0]
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_median_setpoint()

        assert result == 21.0  # (20 + 22) / 2

    def test_returns_single_setpoint(self, mock_hass, mock_coordinator, default_config):
        """Test with single zone."""
        mock_coordinator.get_active_zone_setpoints.return_value = [19.5]
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_median_setpoint()

        assert result == 19.5


class TestGetForecastMedian:
    """Tests for _get_forecast_median method."""

    def test_returns_median_from_forecast(self, mock_hass, mock_coordinator, default_config):
        """Test returns correct median from forecast."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 10.0},
                {"temperature": 12.0},
                {"temperature": 8.0},
                {"temperature": 15.0},
                {"temperature": 11.0},
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager._get_forecast_median()

        assert result == 11.0  # median of [8, 10, 11, 12, 15]

    def test_returns_none_when_no_weather_entity(self, mock_hass, mock_coordinator, default_config):
        """Test returns None when no weather entity configured."""
        mock_coordinator.weather_entity = None
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager._get_forecast_median()

        assert result is None

    def test_returns_none_when_weather_entity_not_found(self, mock_hass, mock_coordinator, default_config):
        """Test returns None when weather entity doesn't exist."""
        mock_hass.states.get.return_value = None
        mock_coordinator.weather_entity = "weather.nonexistent"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager._get_forecast_median()

        assert result is None

    def test_returns_none_when_no_forecast(self, mock_hass, mock_coordinator, default_config):
        """Test returns None when forecast is empty."""
        mock_state = MagicMock()
        mock_state.attributes = {"forecast": []}
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager._get_forecast_median()

        assert result is None

    def test_uses_up_to_7_forecast_entries(self, mock_hass, mock_coordinator, default_config):
        """Test only uses first 7 forecast entries."""
        mock_state = MagicMock()
        # 10 entries, but should only use first 7
        mock_state.attributes = {
            "forecast": [
                {"temperature": 10.0},
                {"temperature": 11.0},
                {"temperature": 12.0},
                {"temperature": 13.0},
                {"temperature": 14.0},
                {"temperature": 15.0},
                {"temperature": 16.0},
                {"temperature": 100.0},  # Entry 8 - should be ignored
                {"temperature": 100.0},  # Entry 9 - should be ignored
                {"temperature": 100.0},  # Entry 10 - should be ignored
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager._get_forecast_median()

        assert result == 13.0  # median of [10, 11, 12, 13, 14, 15, 16]

    def test_skips_entries_without_temperature(self, mock_hass, mock_coordinator, default_config):
        """Test skips forecast entries without temperature."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 10.0},
                {"condition": "sunny"},  # No temperature
                {"temperature": 12.0},
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager._get_forecast_median()

        assert result == 11.0  # median of [10, 12]


class TestGetSeason:
    """Tests for get_season method."""

    def test_returns_winter_when_cold(self, mock_hass, mock_coordinator, default_config):
        """Test returns winter when forecast median is below winter_below."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 5.0},
                {"temperature": 7.0},
                {"temperature": 8.0},
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_season()

        assert result == "winter"  # median 7.0 < 12.0

    def test_returns_summer_when_hot(self, mock_hass, mock_coordinator, default_config):
        """Test returns summer when forecast median is above summer_above."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 25.0},
                {"temperature": 28.0},
                {"temperature": 22.0},
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_season()

        assert result == "summer"  # median 25.0 > 18.0

    def test_returns_shoulder_when_moderate(self, mock_hass, mock_coordinator, default_config):
        """Test returns shoulder when forecast median is between thresholds."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 14.0},
                {"temperature": 15.0},
                {"temperature": 16.0},
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_season()

        assert result == "shoulder"  # median 15.0 is between 12.0 and 18.0

    def test_returns_shoulder_when_no_forecast(self, mock_hass, mock_coordinator, default_config):
        """Test returns shoulder when no forecast available."""
        mock_coordinator.weather_entity = None
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = manager.get_season()

        assert result == "shoulder"

    def test_uses_custom_thresholds(self, mock_hass, mock_coordinator):
        """Test uses custom season thresholds."""
        config = {
            CONF_SEASON_THRESHOLDS: {
                CONF_WINTER_BELOW: 10.0,
                CONF_SUMMER_ABOVE: 20.0,
            },
        }
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [{"temperature": 15.0}]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        manager = AutoModeSwitchingManager(mock_hass, config, mock_coordinator)

        result = manager.get_season()

        assert result == "shoulder"  # 15.0 is between 10.0 and 20.0


class TestCheckForecast:
    """Tests for _check_forecast method."""

    @pytest.mark.asyncio
    async def test_returns_cool_when_hot_weather_coming(self, mock_hass, mock_coordinator, default_config):
        """Test returns COOL when forecast shows hot weather."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 25.0},  # > 21.0 + 2.0
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        mock_coordinator.get_active_zone_setpoints.return_value = [21.0]
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = await manager._check_forecast()

        assert result == HVACMode.COOL

    @pytest.mark.asyncio
    async def test_returns_heat_when_cold_weather_coming(self, mock_hass, mock_coordinator, default_config):
        """Test returns HEAT when forecast shows cold weather."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 5.0},  # < 21.0 - 2.0
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        mock_coordinator.get_active_zone_setpoints.return_value = [21.0]
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = await manager._check_forecast()

        assert result == HVACMode.HEAT

    @pytest.mark.asyncio
    async def test_returns_none_when_weather_moderate(self, mock_hass, mock_coordinator, default_config):
        """Test returns None when forecast within threshold."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 20.0},  # within 21.0 ± 2.0
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        mock_coordinator.get_active_zone_setpoints.return_value = [21.0]
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = await manager._check_forecast()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_weather_entity(self, mock_hass, mock_coordinator, default_config):
        """Test returns None when no weather entity configured."""
        mock_coordinator.weather_entity = None
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = await manager._check_forecast()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_zones(self, mock_hass, mock_coordinator, default_config):
        """Test returns None when no active zones."""
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [{"temperature": 25.0}]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        mock_coordinator.get_active_zone_setpoints.return_value = []
        manager = AutoModeSwitchingManager(mock_hass, default_config, mock_coordinator)

        result = await manager._check_forecast()

        assert result is None

    @pytest.mark.asyncio
    async def test_respects_forecast_hours_config(self, mock_hass, mock_coordinator):
        """Test only checks entries within forecast_hours window."""
        config = {
            CONF_AUTO_MODE_THRESHOLD: 2.0,
            CONF_FORECAST_HOURS: 2,  # Only check first 2 entries
        }
        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"temperature": 20.0},  # Entry 1 - moderate
                {"temperature": 21.0},  # Entry 2 - moderate
                {"temperature": 30.0},  # Entry 3 - hot but outside window
            ]
        }
        mock_hass.states.get.return_value = mock_state
        mock_coordinator.weather_entity = "weather.home"
        mock_coordinator.get_active_zone_setpoints.return_value = [21.0]
        manager = AutoModeSwitchingManager(mock_hass, config, mock_coordinator)

        result = await manager._check_forecast()

        assert result is None  # Hot weather in entry 3 is outside window
