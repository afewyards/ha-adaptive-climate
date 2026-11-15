"""Per-zone sensors must find heater entity, cycle counts and max power from reachable data."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, Mock

if "homeassistant.components.sensor" not in sys.modules:
    _m = MagicMock()
    _m.SensorEntity = type("SensorEntity", (), {})
    sys.modules["homeassistant.components.sensor"] = _m

import pytest

from custom_components.adaptive_climate.sensors.actuator_wear import ActuatorWearSensor
from custom_components.adaptive_climate.sensors.energy import PowerPerM2Sensor
from custom_components.adaptive_climate.sensors import performance as perf
from custom_components.adaptive_climate.sensors.performance import (
    AdaptiveThermostatSensor,
    CycleTimeSensor,
    DutyCycleSensor,
)


def _hass(zone_data: dict, states: dict | None = None) -> Mock:
    hass = Mock()
    coordinator = Mock()
    coordinator.get_zone_data.return_value = zone_data
    hass.data = {"adaptive_climate": {"coordinator": coordinator}}
    states = states or {}
    hass.states.get.side_effect = states.get
    return hass


def _state(state: str, **attrs) -> Mock:
    s = Mock()
    s.state = state
    s.attributes = attrs
    return s


@pytest.mark.parametrize("cls", [DutyCycleSensor, CycleTimeSensor])
def test_heater_entity_resolved_from_zone_data(cls, monkeypatch):
    async def _noop(self):
        return None

    monkeypatch.setattr(AdaptiveThermostatSensor.__mro__[1], "async_added_to_hass", _noop, raising=False)
    monkeypatch.setattr(perf, "async_track_state_change_event", Mock())
    hass = _hass(
        {"heater_entity_id": ["switch.floor", "switch.other"]},
        {"climate.z1": _state("heat"), "switch.floor": _state("on")},
    )
    sensor = cls(hass, "z1", "Zone 1", "climate.z1")
    asyncio.run(sensor.async_added_to_hass())
    assert sensor._heater_entity_id == "switch.floor"


@pytest.mark.parametrize(
    ("cycle_count", "actuator", "expected"),
    [
        (500, "heater", 500),
        ({"heater": 500, "cooler": 200}, "heater", 500),
        ({"heater": 500, "cooler": 200}, "cooler", 200),
    ],
)
def test_actuator_wear_reads_cycle_count(cycle_count, actuator, expected):
    hass = _hass({}, {"climate.z1": _state("heat", cycle_count=cycle_count)})
    sensor = ActuatorWearSensor(hass, "z1", "Zone 1", "climate.z1", actuator, rated_cycles=1000)
    sensor.async_write_ha_state = Mock()
    asyncio.run(sensor._async_update_from_climate_state())
    assert sensor._current_cycles == expected
    assert sensor.native_value == pytest.approx(expected / 10)


def test_power_m2_uses_zone_max_power():
    hass = _hass({"area_m2": 10.0, "max_power_w_m2": 40.0}, {"sensor.z1_duty_cycle": _state("50")})
    sensor = PowerPerM2Sensor(hass, "z1", "Zone 1", "climate.z1")
    assert asyncio.run(sensor._calculate_power_m2()) == pytest.approx(20.0)


def test_zone_data_carries_heater_and_power_density():
    from custom_components.adaptive_climate.climate_setup import build_zone_power_data

    assert build_zone_power_data({"heater": ["switch.a"], "max_power_w": 800.0, "area_m2": 10.0}) == {
        "heater_entity_id": ["switch.a"],
        "max_power_w_m2": 80.0,
    }
    assert build_zone_power_data({"heater": ["switch.a"]}) == {"heater_entity_id": ["switch.a"]}
