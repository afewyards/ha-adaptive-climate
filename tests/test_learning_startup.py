"""Concurrent zone startup must share fully restored learning state."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_climate import climate_setup
from custom_components.adaptive_climate.const import DOMAIN


@pytest.fixture
def startup(monkeypatch):
    """Run real platform setup with controlled persistence and HA boundaries."""
    store = MagicMock()
    store.async_load = AsyncMock()
    store.async_load_manifold_state = AsyncMock(return_value={"main": "warm"})
    store.async_load_water_temp_state = AsyncMock(return_value={"heating": {"target": 35}})
    store.get_zone_data.return_value = {"adaptive_learner": {"cycle_count": 7}}
    factory = MagicMock(return_value=store)
    discovery = MagicMock()
    coordinator = MagicMock()
    manifold = MagicMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {"coordinator": coordinator, "manifold_registry": manifold}}
    monkeypatch.setattr(climate_setup, "LearningDataStore", factory)
    monkeypatch.setattr(climate_setup, "discovery", discovery)
    learner = MagicMock()
    monkeypatch.setattr(climate_setup, "AdaptiveLearner", learner)
    monkeypatch.setattr("custom_components.adaptive_climate.climate.AdaptiveThermostat", MagicMock())
    monkeypatch.setattr("custom_components.adaptive_climate.thermostat_config.AdaptiveThermostatConfig", MagicMock())

    async def zone(index):
        await climate_setup.async_setup_platform(
            hass, {"name": f"Zone {index}", "heater": [f"switch.zone_{index}"]}, MagicMock()
        )

    return SimpleNamespace(
        hass=hass,
        store=store,
        factory=factory,
        discovery=discovery,
        coordinator=coordinator,
        manifold=manifold,
        learner=learner,
        zone=zone,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["async_load", "async_load_manifold_state", "async_load_water_temp_state"])
async def test_parallel_zones_wait_for_one_fully_restored_store(startup, stage):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        entered.set()
        await release.wait()
        return {"restored": True}

    getattr(startup.store, stage).side_effect = delayed
    first = asyncio.create_task(startup.zone(0))
    await asyncio.wait_for(entered.wait(), 1)
    followers = [asyncio.create_task(startup.zone(i)) for i in range(1, 4)]
    await asyncio.sleep(0)
    assert startup.factory.call_count == 1
    assert "learning_store" not in startup.hass.data[DOMAIN]
    startup.store.get_zone_data.assert_not_called()
    startup.discovery.async_load_platform.assert_not_called()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, *followers), 1)

    assert startup.hass.data[DOMAIN]["learning_store"] is startup.store
    startup.store.async_load.assert_awaited_once()
    startup.store.async_load_manifold_state.assert_awaited_once()
    startup.store.async_load_water_temp_state.assert_awaited_once()
    startup.manifold.restore_state.assert_called_once()
    startup.coordinator.water_temp_controller.restore_state.assert_called_once()
    assert startup.store.get_zone_data.call_count == 4
    assert startup.coordinator.register_zone.call_count == 4
    assert startup.learner.return_value.restore_from_dict.call_count == 4
    number_calls = [c for c in startup.discovery.async_load_platform.call_args_list if c.args[1] == "number"]
    assert len(number_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["async_load", "async_load_water_temp_state"])
async def test_failed_initialization_is_not_published_and_can_retry(startup, stage):
    getattr(startup.store, stage).side_effect = OSError("storage unavailable")
    with pytest.raises(OSError, match="storage unavailable"):
        await startup.zone(0)
    assert "learning_store" not in startup.hass.data[DOMAIN]
    assert not startup.hass.data[DOMAIN].get("number_platform_loaded")
    startup.discovery.async_load_platform.assert_not_called()
    getattr(startup.store, stage).side_effect = None
    await asyncio.wait_for(startup.zone(1), 1)
    assert startup.hass.data[DOMAIN]["learning_store"] is startup.store
    assert startup.factory.call_count == 2
    assert startup.discovery.async_load_platform.call_args_list[0].args[1] == "number"


@pytest.mark.asyncio
async def test_cancelled_initializer_releases_waiting_zone(startup):
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    startup.store.async_load.side_effect = blocked
    first = asyncio.create_task(startup.zone(0))
    await asyncio.wait_for(entered.wait(), 1)
    follower = asyncio.create_task(startup.zone(1))
    await asyncio.sleep(0)
    startup.store.async_load.side_effect = None
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(follower, 1)
    assert startup.factory.call_count == 2
    assert startup.hass.data[DOMAIN]["learning_store"] is startup.store
    startup.store.get_zone_data.assert_called_once_with("zone_1")
