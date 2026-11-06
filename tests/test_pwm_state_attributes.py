"""PWM diagnostics must reach HA state without mutating scheduling state."""

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_climate.managers.state_attributes import build_state_attributes

from test_pwm_ha_integration import live


@pytest.mark.parametrize("configured", [False, True])
def test_public_state_exposes_only_configured_group_diagnostics(live, configured):
    thermostat = MagicMock()
    thermostat.entity_id = live.entities[0].entity_id
    thermostat.hass = live.entities[0].hass
    thermostat.hass.data = {"adaptive_climate": {"debug": False}}
    thermostat._control_output = 40.0
    thermostat.pid_control_i = 5.0
    thermostat._heater_controller = live.entities[0]._heater_controller
    thermostat._demand_switch_entity_id = None
    thermostat._gains_manager = None
    thermostat._coordinator = None
    thermostat._night_setback_controller = None
    thermostat._humidity_detector = None
    thermostat._pwm_coordination = live.registry if configured else None
    expected = live.registry.diagnostics(thermostat.entity_id)

    for _ in range(2):
        attrs = build_state_attributes(thermostat)
        if configured:
            assert attrs["pwm_coordination"] == expected
            assert "remaining_credit_seconds" in attrs["pwm_coordination"]
            assert "runtime_plan_reason" in attrs["pwm_coordination"]
        else:
            assert "pwm_coordination" not in attrs
    assert live.registry.diagnostics(thermostat.entity_id) == expected
