"""Tests for the settings state attribute group and vacation refresh."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.adaptive_climate.const import DOMAIN
from custom_components.adaptive_climate.managers.settings_attributes import build_settings_attributes

from test_zone_settings_services import KEY, _zone

SETBACK = {"start": "21:00", "delta": 1, "recovery_deadline": "9:00"}


async def _built(setback=None, **kw):
    t, store, _ = await _zone(**kw)
    t._night_setback_config = setback and dict(setback)
    return t, store


@pytest.mark.asyncio
async def test_settings_group_for_zone_with_setback():
    t, _ = await _built({**SETBACK, "end": None})
    assert build_settings_attributes(t) == {
        "settings": {
            "changed": False,
            "vacation": False,
            "night_setback": {"start": "21:00", "delta": 1, "recovery_deadline": "9:00", "editable": True},
        }
    }


@pytest.mark.asyncio
async def test_no_night_setback_key_without_setback():
    t, _ = await _built()
    assert "night_setback" not in build_settings_attributes(t)["settings"]


@pytest.mark.asyncio
async def test_fixed_end_is_not_editable():
    t, _ = await _built({**SETBACK, "end": "07:00"})
    assert build_settings_attributes(t)["settings"]["night_setback"]["editable"] is False


@pytest.mark.asyncio
async def test_changed_true_after_store_entry():
    t, store = await _built()
    await store.async_set_preset(KEY, "away", 15.0)
    assert build_settings_attributes(t)["settings"]["changed"] is True


@pytest.mark.asyncio
async def test_vacation_true_when_enabled():
    t, _ = await _built(vacation=True)
    assert build_settings_attributes(t)["settings"]["vacation"] is True


@pytest.mark.asyncio
async def test_vacation_toggle_sends_signal():
    from custom_components.adaptive_climate.adaptive.vacation import VacationMode

    coordinator = SimpleNamespace(get_all_zones=lambda: {})
    vacation = VacationMode(SimpleNamespace(states=None, services=SimpleNamespace(async_call=AsyncMock())), coordinator)
    with patch("custom_components.adaptive_climate.adaptive.vacation.async_dispatcher_send") as send:
        await vacation.async_enable(12.0)
        assert send.call_count == 1
        await vacation.async_disable()
        assert send.call_count == 2
