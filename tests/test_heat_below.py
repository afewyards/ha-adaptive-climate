"""Per-cycle share of time a zone on a lower floor was heating."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_climate.adaptive.cycle_analysis import calculate_heat_below
from custom_components.adaptive_climate.helpers.registry import lower_zone_heating
from custom_components.adaptive_climate.managers.cycle_tracker import CycleTrackerManager
from custom_components.adaptive_climate.managers.events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    SettlingStartedEvent,
)

START = datetime(2025, 1, 14, 10, 0, 0)
FLOORS = {
    "climate.up": 1,
    "climate.down": 0,
    "climate.hall": 0,
    "climate.cellar": -1,
    "climate.side": 1,
    "climate.attic": 2,
}
TEMPS = [18.0, 18.5, 19.0, 19.5, 20.0, 20.5, 20.3, 20.1, 20.0, 19.9]
DOWN = {"climate.down": "heat"}
HALL = {"climate.hall": "heat"}
CELLAR = {"climate.cellar": "heat"}
NONE: dict[str, str] = {}


class FakeCoordinator:
    def __init__(self, zones):
        self.zones = zones
        self.active: dict[str, str] = {}

    def get_all_zones(self):
        return {entity_id.split(".")[1]: {"climate_entity_id": entity_id} for entity_id in self.zones}

    def get_active_zones(self, hvac_mode=None):
        return {
            entity_id: {"climate_entity_id": entity_id}
            for entity_id, mode in self.active.items()
            if hvac_mode is None or mode == hvac_mode
        }


async def heat_below(heating_by_minute, floors=FLOORS, lookup=None):
    """Run a 10-minute cycle in climate.up, sampled each minute, and return the recorded heat_below."""
    coordinator = FakeCoordinator(floors)
    learner = MagicMock()
    dispatcher = CycleEventDispatcher()
    tracker = CycleTrackerManager(
        hass=MagicMock(),
        zone_id="up",
        adaptive_learner=learner,
        get_target_temp=lambda: 20.0,
        get_current_temp=lambda: 18.0,
        get_hvac_mode=lambda: "heat",
        get_in_grace_period=lambda: False,
        dispatcher=dispatcher,
        get_heat_below=lambda: lower_zone_heating(MagicMock(), coordinator, "climate.up"),
    )
    tracker.set_restoration_complete()
    floors_of = lookup or (lambda _hass, ids: {entity_id: floors.get(entity_id) for entity_id in ids})
    with (
        patch("custom_components.adaptive_climate.helpers.registry.discover_zone_floors", side_effect=floors_of),
        patch(
            "custom_components.adaptive_climate.managers.cycle_metrics.dt_util.utcnow",
            return_value=START + timedelta(minutes=10),
        ),
    ):
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=START, target_temp=20.0, current_temp=18.0))
        for minute, (active, temp) in enumerate(zip(heating_by_minute, TEMPS)):
            coordinator.active = active
            await tracker.update_temperature(START + timedelta(minutes=minute), temp)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=START + timedelta(minutes=10)))
        await tracker._finalize_cycle()
    learner.add_cycle_metrics.assert_called_once()
    return learner.add_cycle_metrics.call_args[0][0].heat_below


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("heating_by_minute", "expected"),
    [
        ([NONE] * 10, 0.0),
        ([DOWN] * 4 + [NONE] * 6, 0.4),
        ([DOWN] * 10, 1.0),
        ([DOWN] * 3 + [NONE] * 2 + [HALL, DOWN | HALL] + [NONE] * 3, 0.5),
        ([{"climate.up": "heat", "climate.side": "heat", "climate.attic": "heat"}] * 10, 0.0),
        ([CELLAR] * 10, 0.0),
        ([{"climate.down": "cool"}] * 10, 0.0),
    ],
    ids=[
        "none",
        "part",
        "all",
        "several_zones_on_the_floor_below",
        "same_or_higher_floor_ignored",
        "two_floors_down_ignored_when_the_floor_below_has_zones",
        "cooling_below_does_not_count",
    ],
)
async def test_fraction_of_the_cycle(heating_by_minute, expected):
    assert await heat_below(heating_by_minute) == expected


@pytest.mark.asyncio
async def test_floor_without_zones_is_skipped_for_the_next_one_down():
    floors = {"climate.up": 2, "climate.cellar": 0, "climate.attic": 3}
    assert await heat_below([CELLAR] * 5 + [NONE] * 5, floors=floors) == 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "floors",
    [
        {**FLOORS, "climate.up": None},
        {"climate.up": 0, "climate.side": 0, "climate.attic": 1},
    ],
    ids=["unknown_floor", "no_lower_zone"],
)
async def test_unknowable_is_none(floors):
    assert await heat_below([DOWN] * 10, floors=floors) is None


@pytest.mark.asyncio
async def test_lookup_raising_is_none_and_the_cycle_is_still_recorded():
    def broken(_hass, _ids):
        raise RuntimeError("registry gone")

    assert await heat_below([DOWN] * 10, lookup=broken) is None


def test_each_sample_holds_until_the_next_and_the_first_covers_the_start():
    samples = [(START + timedelta(minutes=2), True), (START + timedelta(minutes=8), False)]
    assert calculate_heat_below(samples, START, START + timedelta(minutes=10)) == 0.8
    assert calculate_heat_below(samples, None, START) is None
