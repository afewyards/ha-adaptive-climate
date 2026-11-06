"""Real heater dispatch, effective pulse preparation and HA adapter boundaries."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_climate.climate_state_setters import ClimateStateSettersMixin
from custom_components.adaptive_climate.managers import heater_actuation, pwm_controller, pwm_coordination
from custom_components.adaptive_climate.managers.heater_controller import HeaterController
from custom_components.adaptive_climate.managers import heater_controller as heater_module
from custom_components.adaptive_climate.managers.pwm_pulse import prepare_pulse
from custom_components.adaptive_climate.pwm_coordination_config import PWM_COORDINATION_SCHEMA


class Entity(ClimateStateSettersMixin):
    def __init__(self, entity_id, hass, output_domain="switch"):
        self.entity_id = entity_id
        self.hass = hass
        self._pwm = 3600
        self._active = True
        self.hvac_mode = "heat"
        self._hvac_mode = SimpleNamespace(value="heat")
        self._current_temp = 19.0
        self._target_temp = 21.0
        self.target_temperature = 21.0
        self._control_output = 40.0
        self._status_manager = SimpleNamespace(is_paused=lambda _: False)
        self._cooler_entity_id = None
        self._ac_mode = False
        self._heater_polarity_invert = False
        self._zone_id = None
        self._coordinator = None
        self._force_on = self._force_off = False
        self._time_changed = 0.0
        self._last_heat_cycle_time = 0.0
        self._pwm_coordination = None
        self._cycle_dispatcher = None
        self._setpoint_boost_manager = None
        self._heater_controller = HeaterController(
            hass,
            self,
            [entity_id.replace("climate.", f"{output_domain}.")],
            None,
            None,
            False,
            3600,
            100.0,
            600.0,
            600.0,
            valve_actuation_time=120.0,
        )

    def _get_cycle_start_time(self):
        return self._last_heat_cycle_time

    def async_write_ha_state(self):
        pass

    async def _async_control_heating(self, **kwargs):
        pass

    async def apply(self):
        await self._heater_controller.async_set_control_value(
            self._control_output,
            "heat",
            self._get_cycle_start_time,
            self._set_is_heating,
            self._set_last_heat_cycle_time,
            self._time_changed,
            self._set_time_changed,
            self._force_on,
            self._force_off,
            self._set_force_on,
            self._set_force_off,
        )


@pytest.fixture(params=["switch", "input_boolean"])
def live(monkeypatch, request):
    domain = request.param
    clock = SimpleNamespace(now=10000.0)
    clock.monotonic = lambda: clock.now
    for module in (heater_actuation, pwm_controller, pwm_coordination):
        monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(pwm_coordination, "HVACMode", SimpleNamespace(HEAT="heat", OFF="off"))
    monkeypatch.setattr(heater_module, "HVACMode", SimpleNamespace(HEAT="heat", COOL="cool", OFF="off"))
    monkeypatch.setattr(pwm_coordination, "split_entity_id", lambda value: value.split(".", 1))
    state = {f"{domain}.a": "off", f"{domain}.b": "off"}
    hass = MagicMock()
    hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    hass.states.get.side_effect = lambda entity: SimpleNamespace(state=state[entity]) if entity in state else None
    hass.states.is_state.side_effect = lambda entity, expected: state.get(entity) == expected
    commands = []

    async def service(domain, name, data, **kwargs):
        commands.append((clock.now, name, data["entity_id"]))
        state[data["entity_id"]] = "on" if name == "turn_on" else "off"

    hass.services.async_call = service
    config = PWM_COORDINATION_SCHEMA(
        {
            "enabled": True,
            "groups": [
                {"name": "pump", "zones": [{"entity": "climate.a", "hydraulic_weight": 4}, {"entity": "climate.b"}]}
            ],
        }
    )
    registry = pwm_coordination.PWMCoordinationRegistry(hass, config)
    entities = [Entity("climate.a", hass, domain), Entity("climate.b", hass, domain)]
    for entity in entities:
        entity._pwm_coordination = registry
        registry.bind(entity)
    return SimpleNamespace(
        clock=clock, state=state, registry=registry, entities=entities, commands=commands, domain=domain, config=config
    )


@pytest.mark.asyncio
async def test_real_dispatch_uses_one_writer_and_common_setpoint_setter(live):
    entity = live.entities[0]
    await entity.apply()
    assert live.registry.groups[0].mode == "active"
    assert live.state[f"{live.domain}.a"] == "on"
    count = len(live.commands)
    live.clock.now += 300
    entity._set_target_temp(23.0)  # shared by UI, preset, automation and schedule paths
    await entity.apply()
    assert len(live.commands) == count
    ledger = live.registry.groups[0]._zones[entity.entity_id]
    assert ledger.delivered == 300
    assert ledger.credit + ledger.delivered == ledger.requested


def test_real_effective_duration_has_valve_close_offset_and_minimum(live):
    heater = live.entities[0]._heater_controller
    pulse = prepare_pulse(heater, 40, live.clock.now)
    assert pulse.seconds == 1500  # 1440 heat +120 open -60 close lead
    assert pulse.seconds >= heater.effective_min_open_time
    heater.set_transport_delay(300)
    heater._heat_pipeline._q_at_close = 2000
    heater._heat_pipeline._valve_opened_at = live.clock.now - 1000
    heater._heat_pipeline._valve_closed_at = live.clock.now
    assert prepare_pulse(heater, 40, live.clock.now).seconds == 0  # I18: committed heat covers demand


def test_low_duty_is_not_fabricated_and_credit_is_reserved(live):
    heater = live.entities[0]._heater_controller
    assert prepare_pulse(heater, 5, live.clock.now).seconds == 0
    assert heater.duty_accumulator_seconds == 0
    heater.set_duty_accumulator(600)
    pulse = prepare_pulse(heater, 5, live.clock.now)
    assert pulse.seconds == 720
    assert pulse.accumulator_debit == 600
    assert heater.duty_accumulator_seconds == 600


@pytest.mark.asyncio
async def test_unavailable_relay_never_runs_speculative_on(live):
    live.state[f"{live.domain}.a"] = "unavailable"
    await live.entities[0].apply()
    assert live.registry.groups[0].mode == "fallback"
    assert not live.commands


@pytest.mark.asyncio
async def test_target_reduction_zero_cancels_future_on(live):
    entity = live.entities[0]
    await entity.apply()
    live.clock.now += 800
    entity._set_target_temp(18)
    entity._control_output = 0
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert live.registry.groups[0]._zones[entity.entity_id].credit == 0


def test_shutdown_does_not_schedule_legacy_control(live):
    live.registry.close()
    assert live.registry.groups[0].mode == "fallback"
    live.entities[0].hass.async_create_task.assert_not_called()


def test_shared_output_disables_group(live):
    extra = Entity("climate.extra", live.entities[0].hass)
    extra._heater_controller._heater_entity_id = [f"{live.domain}.a"]
    live.registry.bind(extra)
    assert live.registry.groups[0].mode == "fallback"


@pytest.mark.asyncio
async def test_entity_rename_revokes_old_group_owner(live):
    entity = live.entities[0]
    entity.entity_id = "climate.renamed"
    assert not await live.registry.apply(entity.entity_id)
    assert live.registry.groups[0].mode == "fallback"
    assert not live.commands


@pytest.mark.asyncio
async def test_stale_temperature_rechecks_original_safety_output(live):
    entity = live.entities[0]
    entity._sensor_stall = 60
    entity._last_sensor_update = live.clock.now - 120
    entity._output_safety = 0
    await entity.apply()
    assert not any(command[2] == f"{live.domain}.a" for command in live.commands)


def test_effective_schedule_target_is_used_in_priority_signature(live):
    entity = live.entities[0]
    entity._control_output_manager = SimpleNamespace(_last_effective_target=18.0)
    adapter = live.registry._adapters[entity.entity_id]
    assert adapter.signature()[0] == 18
    entity._control_output_manager._last_effective_target = 22.0
    assert adapter.signature()[0] == 22


@pytest.mark.asyncio
async def test_fallback_does_not_repeat_failed_start_in_same_control_transaction(live):
    attempted = []

    async def failed_service(domain, name, data, **kwargs):
        attempted.append(name)
        raise RuntimeError("unavailable relay")

    live.entities[0].hass.services.async_call = failed_service
    await live.entities[0].apply()
    assert live.registry.groups[0].mode == "fallback"
    assert attempted == ["turn_on", "turn_off"]


@pytest.mark.asyncio
async def test_mixed_binary_domains_use_existing_controller_service(live):
    """Switches and boolean proxies may share a group; no direct device writes."""
    entity = live.entities[1]
    other_domain = "input_boolean" if live.domain == "switch" else "switch"
    output = f"{other_domain}.b"
    live.state[output] = "off"
    entity._heater_controller._heater_entity_id = [output]
    live.registry.close()
    live.registry = pwm_coordination.PWMCoordinationRegistry(entity.hass, live.config)
    for member in live.entities:
        member._pwm_coordination = live.registry
        live.registry.bind(member)
    calls = []

    async def service(domain, name, data, **kwargs):
        calls.append((domain, name, data["entity_id"]))
        live.state[data["entity_id"]] = "on" if name == "turn_on" else "off"

    entity.hass.services.async_call = service
    entity._set_target_temp(23)
    await entity.apply()
    assert live.registry.groups[0].mode == "active"
    assert live.state[output] == "on"
    assert ("homeassistant", "turn_on", output) in calls
    live.clock.now += 800
    entity._control_output = 0
    await entity.apply()
    assert live.state[output] == "off"
    assert ("homeassistant", "turn_off", output) in calls


@pytest.mark.parametrize("domain", ["input_number", "light"])
def test_nonbinary_output_domains_remain_unsupported(live, domain):
    entity = live.entities[0]
    entity._heater_controller._heater_entity_id = [f"{domain}.a"]
    live.registry.bind(entity)
    assert live.registry.groups[0].mode == "fallback"
    assert "switch or input_boolean" in live.registry.groups[0].reason
