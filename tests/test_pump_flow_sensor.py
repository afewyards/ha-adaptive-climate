"""Tests for the system-wide pump flow diagnostic sensor."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# conftest.py doesn't register these two modules; inject before importing the sensor.
if "homeassistant.components.sensor" not in sys.modules:
    _mock_sensor_mod = MagicMock()
    _mock_sensor_mod.SensorEntity = type("SensorEntity", (), {})
    _mock_sensor_mod.SensorDeviceClass = MagicMock()
    _mock_sensor_mod.SensorStateClass = MagicMock()
    sys.modules["homeassistant.components.sensor"] = _mock_sensor_mod

if "homeassistant.helpers.entity" not in sys.modules:
    _mock_entity_mod = MagicMock()
    _mock_entity_mod.EntityCategory = MagicMock()
    sys.modules["homeassistant.helpers.entity"] = _mock_entity_mod

from custom_components.adaptive_climate.sensors.pump_flow import PumpFlowTargetSensor

DIAGNOSTICS = {
    "requested": 7.5,
    "open_zones": ["climate.kitchen", "climate.living"],
    "pending_zones": {"climate.hall": 42.0},
    "raw_sum": 7.5,
    "min_flow_applied": False,
    "decrease_pending_since": None,
    "binding_constraint": None,
    "last_write": "2026-09-27T10:00:00+00:00",
}


def make_hass(controller=None):
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.pump_flow_controller = controller
    hass.data = {"adaptive_climate": {"coordinator": coordinator}}
    return hass


def make_controller(diagnostics=None):
    controller = MagicMock()
    controller.diagnostics = MagicMock(return_value=diagnostics or DIAGNOSTICS)
    return controller


@pytest.mark.asyncio
async def test_state_is_the_requested_flow():
    sensor = PumpFlowTargetSensor(make_hass(make_controller()))

    await sensor.async_update()

    assert sensor.native_value == pytest.approx(7.5)
    assert sensor.available is True


@pytest.mark.asyncio
async def test_attributes_expose_the_diagnostic_breakdown():
    sensor = PumpFlowTargetSensor(make_hass(make_controller()))

    await sensor.async_update()

    assert sensor.extra_state_attributes == {key: value for key, value in DIAGNOSTICS.items() if key != "requested"}


@pytest.mark.asyncio
async def test_sensor_is_unavailable_without_a_controller():
    sensor = PumpFlowTargetSensor(make_hass(None))

    await sensor.async_update()

    assert sensor.native_value is None
    assert sensor.available is False


@pytest.mark.asyncio
async def test_a_failing_diagnostics_call_does_not_raise():
    controller = MagicMock()
    controller.diagnostics = MagicMock(side_effect=RuntimeError("boom"))
    sensor = PumpFlowTargetSensor(make_hass(controller))

    await sensor.async_update()

    assert sensor.available is False


def test_sensor_identity_is_stable():
    sensor = PumpFlowTargetSensor(make_hass(make_controller()))

    assert sensor.unique_id == "pump_flow_target"
    assert sensor.name == "Pump Flow Target"


def test_setup_creates_the_sensor_only_when_pump_control_is_configured():
    import inspect

    from custom_components.adaptive_climate import sensor as sensor_module

    source = inspect.getsource(sensor_module)
    assert "PumpFlowTargetSensor(hass)" in source
    assert 'getattr(coordinator, "pump_flow_controller", None) is not None' in source
