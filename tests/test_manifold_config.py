"""Tests for manifold and pump flow config validators."""

import pytest
import voluptuous as vol

from custom_components.adaptive_climate.manifold_config import (
    LOOPS_REMOVED_MESSAGE,
    MANIFOLD_SCHEMA,
    PUMP_FLOW_CONTROL_SCHEMA,
    removed_key,
    validate_manifolds_and_pump_flow,
)


def manifold(**overrides):
    config = {
        "name": "Ground",
        "pipe_volume": 12,
        "zones": [{"entity": "climate.living", "flow": 4}, {"entity": "climate.kitchen", "flow": 3.5}],
    }
    config.update(overrides)
    return config


class TestManifoldSchema:
    def test_valid_manifold_is_normalised(self):
        result = MANIFOLD_SCHEMA(manifold())
        assert result["pipe_volume"] == 12.0
        assert result["zones"] == [
            {"entity": "climate.living", "flow": 4.0},
            {"entity": "climate.kitchen", "flow": 3.5},
        ]

    def test_plain_string_zone_fails_with_migration_message(self):
        with pytest.raises(vol.Invalid, match="plain entity ids in manifold zones were removed"):
            MANIFOLD_SCHEMA(manifold(zones=["climate.living"]))

    def test_flow_per_loop_fails_with_migration_message(self):
        with pytest.raises(vol.Invalid, match="'flow_per_loop' was removed"):
            MANIFOLD_SCHEMA(manifold(flow_per_loop=2.0))

    def test_missing_flow_is_rejected(self):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(zones=[{"entity": "climate.living"}]))

    @pytest.mark.parametrize("flow", [0, -1.5])
    def test_non_positive_flow_is_rejected(self, flow):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(zones=[{"entity": "climate.living", "flow": flow}]))

    def test_non_climate_zone_entity_is_rejected(self):
        with pytest.raises(vol.Invalid, match="climate"):
            MANIFOLD_SCHEMA(manifold(zones=[{"entity": "switch.living_valve", "flow": 4}]))

    def test_empty_zone_list_is_rejected(self):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(zones=[]))

    def test_small_pipe_volume_is_rejected(self):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(pipe_volume=0.05))


class TestPumpFlowControlSchema:
    def test_minimal_config_gets_default_write_interval_and_no_floor(self):
        result = PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "number.heatpump_flow_setpoint"})
        assert result == {"target_entity": "number.heatpump_flow_setpoint", "min_write_interval": 300}

    def test_full_config(self):
        result = PUMP_FLOW_CONTROL_SCHEMA(
            {"target_entity": "input_number.pump_flow", "min_flow": "12", "min_write_interval": 0}
        )
        assert result == {"target_entity": "input_number.pump_flow", "min_flow": 12.0, "min_write_interval": 0}

    def test_target_must_be_a_number_entity(self):
        with pytest.raises(vol.Invalid):
            PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "sensor.pump_flow"})

    def test_min_flow_must_be_positive(self):
        with pytest.raises(vol.Invalid):
            PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "number.pump_flow", "min_flow": 0})

    def test_negative_write_interval_is_rejected(self):
        with pytest.raises(vol.Invalid):
            PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "number.pump_flow", "min_write_interval": -1})


class TestCrossValidation:
    def test_zone_in_two_manifolds_is_rejected(self):
        config = {
            "manifolds": [
                MANIFOLD_SCHEMA(manifold()),
                MANIFOLD_SCHEMA(manifold(name="First", zones=[{"entity": "climate.living", "flow": 2}])),
            ]
        }
        with pytest.raises(vol.Invalid, match="one manifold only"):
            validate_manifolds_and_pump_flow(config)

    def test_pump_flow_control_requires_manifolds(self):
        with pytest.raises(vol.Invalid, match="requires manifolds"):
            validate_manifolds_and_pump_flow({"pump_flow_control": {"target_entity": "number.pump_flow"}})

    def test_valid_combination_passes_through(self):
        config = {
            "manifolds": [MANIFOLD_SCHEMA(manifold())],
            "pump_flow_control": {"target_entity": "number.pump_flow", "min_write_interval": 300},
        }
        assert validate_manifolds_and_pump_flow(config) is config

    def test_no_manifolds_and_no_pump_passes(self):
        assert validate_manifolds_and_pump_flow({}) == {}


def test_removed_key_always_fails_with_its_message():
    schema = vol.Schema({vol.Optional("loops"): removed_key(LOOPS_REMOVED_MESSAGE)})
    with pytest.raises(vol.Invalid, match="'loops' was removed"):
        schema({"loops": 2})
    assert schema({}) == {}
