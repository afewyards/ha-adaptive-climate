"""Tests for zone settings validation and store."""

from unittest.mock import patch

import pytest

from custom_components.adaptive_climate.adaptive.zone_settings_store import ZoneSettingsStore
from custom_components.adaptive_climate.managers.zone_settings import (
    sanitize_stored,
    validate_night_setback,
    validate_preset_temperature,
)


def test_preset_temperature_range():
    validate_preset_temperature("eco", 18.5, 16, 25)
    validate_preset_temperature("eco", None, 16, 25)
    with pytest.raises(ValueError):
        validate_preset_temperature("eco", 26, 16, 25)
    with pytest.raises(ValueError):
        validate_preset_temperature("party", 20, 16, 25)


def test_night_setback_validation():
    assert validate_night_setback({"start": "9:00", "delta": 2, "recovery_deadline": None}) == {
        "start": "09:00",
        "delta": 2.0,
    }
    assert validate_night_setback({"start": "sunset+30"}) == {"start": "sunset+30"}
    assert validate_night_setback({"start": "sunset-15"}) == {"start": "sunset-15"}
    for bad in (
        {},
        {"delta": 0.4},
        {"delta": 5.5},
        {"start": "25:00"},
        {"recovery_deadline": "sunset"},
        {"start": "noon"},
    ):
        with pytest.raises(ValueError):
            validate_night_setback(bad)


def test_sanitize_drops_invalid_and_reports():
    ok, dropped = sanitize_stored(
        {"presets": {"eco": 30, "sleep": 18, "away": None}, "night_setback": {"delta": 9, "start": "21:00"}}, 16, 25
    )
    assert ok == {"presets": {"sleep": 18, "away": None}, "night_setback": {"start": "21:00"}}
    assert len(dropped) == 2


class FakeStore:
    def __init__(self, backing):
        self.backing = backing

    async def async_load(self):
        return self.backing.get("data")

    async def async_save(self, data):
        self.backing["data"] = data


def _store(backing):
    with patch(
        "custom_components.adaptive_climate.adaptive.zone_settings_store._create_store",
        lambda hass, version, key: FakeStore(backing),
    ):
        return ZoneSettingsStore(None)


@pytest.mark.asyncio
async def test_store_roundtrip():
    backing: dict = {}
    store = _store(backing)
    await store.async_load()
    await store.async_set_preset("z1", "eco", 18.0)
    await store.async_set_night_setback("z1", {"start": "21:00"})
    await store.async_set_night_setback("z1", {"delta": 2.0})

    other = _store(backing)
    await other.async_load()
    assert other.get_zone("z1") == {"presets": {"eco": 18.0}, "night_setback": {"start": "21:00", "delta": 2.0}}
    assert other.get_zone("other") == {}

    await other.async_clear_zone("z1")
    assert other.get_zone("z1") == {}
    assert backing["data"] == {"zones": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, "garbage", {"zones": "x"}])
async def test_store_missing_or_malformed_file_loads_empty(stored):
    store = _store({"data": stored})
    await store.async_load()
    assert store.get_zone("z1") == {}
