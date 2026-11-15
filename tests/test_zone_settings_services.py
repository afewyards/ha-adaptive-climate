"""Tests for runtime per-zone preset temperatures."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.adaptive_climate.adaptive.zone_settings_store import ZoneSettingsStore
from custom_components.adaptive_climate.const import DOMAIN

KEY = "living_uid"


class FakeStore:
    def __init__(self, backing):
        self.backing = backing

    async def async_load(self):
        return self.backing.get("data")

    async def async_save(self, data):
        self.backing["data"] = data


async def _zone(stored=None, vacation=False, **presets):
    """Real thermostat with a real TemperatureManager, wired as climate_init does."""
    # Imported here: other test modules swap the climate mock during collection; conftest restores it per test.
    from custom_components.adaptive_climate.climate import AdaptiveThermostat
    from custom_components.adaptive_climate.managers.temperature_manager import TemperatureManager
    from custom_components.adaptive_climate.thermostat_config import AdaptiveThermostatConfig

    backing = {} if stored is None else {"data": {"zones": {KEY: stored}}}
    with patch(
        "custom_components.adaptive_climate.adaptive.zone_settings_store._create_store",
        lambda hass, version, key: FakeStore(backing),
    ):
        store = ZoneSettingsStore(None)
    await store.async_load()

    t = AdaptiveThermostat(
        AdaptiveThermostatConfig(
            name="living",
            unique_id=KEY,
            unit="°C",
            sensor_entity_id="sensor.temp",
            heater_entity_id=["switch.heater"],
            min_temp=7.0,
            max_temp=25.0,
            target_temp=20.0,
            **presets,
        )
    )
    t.entity_id = "climate.living"
    t.hass = SimpleNamespace(
        data={DOMAIN: {"zone_settings_store": store, "vacation_mode": SimpleNamespace(enabled=vacation)}}
    )
    t._async_control_heating = AsyncMock()
    t.async_write_ha_state = Mock()
    t._temperature_manager = TemperatureManager(
        thermostat=None,
        away_temp=t._away_temp,
        eco_temp=t._eco_temp,
        boost_temp=t._boost_temp,
        comfort_temp=t._comfort_temp,
        home_temp=t._home_temp,
        sleep_temp=t._sleep_temp,
        activity_temp=t._activity_temp,
        preset_sync_mode=t._preset_sync_mode,
        min_temp=t.min_temp,
        max_temp=t.max_temp,
        boost_pid_off=False,
        get_target_temp=lambda: t._target_temp,
        set_target_temp=t._set_target_temp,
        get_current_temp=lambda: t._current_temp,
        set_force_on=t._set_force_on,
        set_force_off=t._set_force_off,
        async_set_pid_mode=t._async_set_pid_mode_internal,
        async_control_heating=t._async_control_heating_internal,
    )
    return t, store, backing


def _attrs(t):
    from custom_components.adaptive_climate.managers.state_attributes import build_state_attributes

    return build_state_attributes(t)


def _has_preset_feature(t):
    from homeassistant.components.climate import ClimateEntityFeature

    return bool(t.supported_features & ClimateEntityFeature.PRESET_MODE)


@pytest.mark.asyncio
async def test_set_preset_creates_preset_and_feature():
    t, store, backing = await _zone()
    assert not _has_preset_feature(t)

    await t.async_set_preset_temperature("eco", 18.5)

    assert "eco" in t.preset_modes
    assert _has_preset_feature(t)
    assert _attrs(t)["eco_temp"] == 18.5
    assert store.get_zone(KEY) == {"presets": {"eco": 18.5}}
    assert backing["data"] == {"zones": {KEY: {"presets": {"eco": 18.5}}}}
    t._async_control_heating.assert_awaited()
    t.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_active_preset_follows_new_temperature():
    t, _, _ = await _zone(comfort_temp=21.0)
    await t.async_set_preset_mode("comfort")
    assert t.target_temperature == 21.0

    await t.async_set_preset_temperature("comfort", 20.5)

    assert t.preset_mode == "comfort"
    assert t.target_temperature == 20.5


@pytest.mark.asyncio
async def test_clearing_active_preset_restores_saved_target():
    t, _, _ = await _zone(eco_temp=18.0, comfort_temp=21.0)
    await t.async_set_preset_mode("eco")
    assert t.target_temperature == 18.0

    await t.async_set_preset_temperature("eco", None)

    assert t.preset_mode == "none"
    assert t._attr_preset_mode == "none"
    assert t.target_temperature == 20.0
    assert "eco" not in t.preset_modes
    assert "eco_temp" not in _attrs(t)


@pytest.mark.asyncio
async def test_clearing_last_preset_removes_feature():
    t, store, _ = await _zone(eco_temp=18.0)
    assert _has_preset_feature(t)

    await t.async_set_preset_temperature("eco", None)

    assert not _has_preset_feature(t)
    assert t.preset_modes == ["none"]
    assert store.get_zone(KEY) == {"presets": {"eco": None}}


@pytest.mark.asyncio
async def test_out_of_range_rejected_and_nothing_saved():
    from homeassistant.exceptions import ServiceValidationError

    t, store, backing = await _zone()

    with pytest.raises(ServiceValidationError, match=r"outside 7\.0-25\.0"):
        await t.async_set_preset_temperature("eco", 30.0)

    assert t._eco_temp is None
    assert "eco" not in t.preset_modes
    assert store.get_zone(KEY) == {}
    assert "data" not in backing
    t._async_control_heating.assert_not_awaited()


@pytest.mark.asyncio
async def test_stored_value_wins_over_yaml_on_startup():
    t, _, _ = await _zone(stored={"presets": {"eco": 18.5}}, eco_temp=17.0)

    await t.async_apply_stored_zone_settings()

    assert t._eco_temp == 18.5
    assert t.presets["eco"] == 18.5
    assert t._yaml_presets["eco"] == 17.0


@pytest.mark.asyncio
async def test_stored_null_clears_yaml_preset_on_startup():
    t, _, _ = await _zone(stored={"presets": {"eco": None}}, eco_temp=17.0)

    await t.async_apply_stored_zone_settings()

    assert "eco" not in t.preset_modes
    assert not _has_preset_feature(t)
    assert t._yaml_presets["eco"] == 17.0


@pytest.mark.asyncio
async def test_invalid_stored_value_ignored_with_warning(caplog):
    t, _, _ = await _zone(stored={"presets": {"eco": 30.0}}, eco_temp=17.0)

    with caplog.at_level(logging.WARNING):
        await t.async_apply_stored_zone_settings()

    assert t._eco_temp == 17.0
    assert t.presets["eco"] == 17.0
    assert any("eco" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_vacation_on_does_not_move_target():
    t, store, _ = await _zone(vacation=True, comfort_temp=21.0)
    await t.async_set_preset_mode("comfort")

    await t.async_set_preset_temperature("comfort", 19.0)

    assert t.target_temperature == 21.0
    assert t.presets["comfort"] == 19.0
    assert store.get_zone(KEY) == {"presets": {"comfort": 19.0}}


@pytest.mark.asyncio
async def test_vacation_on_clearing_active_preset_keeps_target():
    t, store, _ = await _zone(vacation=True, eco_temp=18.0)
    await t.async_set_preset_mode("eco")
    assert t.target_temperature == 18.0

    await t.async_set_preset_temperature("eco", None)

    assert t.preset_mode == "none"
    assert t.target_temperature == 18.0
    assert "eco" not in t.preset_modes
    assert store.get_zone(KEY) == {"presets": {"eco": None}}
