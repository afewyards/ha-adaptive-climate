"""Safety gates for adding a second, scheduled entry into HeaterController.

The formerly failing cases now test the opt-in serialized actuation path.
Unconfigured controllers retain their existing independent PWM implementation.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_climate.managers import heater_controller as heater_module
from custom_components.adaptive_climate.managers import heater_actuation as actuation_module
from custom_components.adaptive_climate.managers import pwm_controller as pwm_module
from custom_components.adaptive_climate.managers.events import CycleEventType


@pytest.fixture
def actuator(monkeypatch):
    """Use the real controllers with an explicitly observed binary output."""
    mode = SimpleNamespace(HEAT="heat", COOL="cool", OFF="off")
    monkeypatch.setattr(heater_module, "HVACMode", mode)
    monkeypatch.setattr(pwm_module, "HVACMode", mode)
    clock = SimpleNamespace(now=10000.0)
    monotonic = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(heater_module, "time", monotonic)
    monkeypatch.setattr(actuation_module, "time", monotonic)
    monkeypatch.setattr(pwm_module, "time", monotonic)
    state = {"switch.zone": "off"}
    hass = MagicMock()
    hass.states.is_state.side_effect = lambda entity, expected: state.get(entity) == expected
    hass.states.get.side_effect = lambda entity: SimpleNamespace(state=state[entity]) if entity in state else None
    thermostat = SimpleNamespace(
        entity_id="climate.zone", _current_temp=19.0, target_temperature=22.0, _target_temp=22.0
    )
    dispatcher = MagicMock()
    controller = heater_module.HeaterController(
        hass=hass,
        thermostat=thermostat,
        heater_entity_id=["switch.zone"],
        cooler_entity_id=None,
        demand_switch_entity_id=None,
        heater_polarity_invert=False,
        pwm=3600,
        difference=100.0,
        min_open_time=600.0,
        min_closed_time=600.0,
        dispatcher=dispatcher,
    )
    controller.enable_safe_actuation()
    tracking = SimpleNamespace(changed=0.0, heating=False)
    callbacks = {
        "hvac_mode": "heat",
        "get_cycle_start_time": lambda: tracking.changed,
        "set_is_heating": lambda active: setattr(tracking, "heating", active),
        "set_last_heat_cycle_time": lambda at: setattr(tracking, "changed", at),
    }

    async def successful_service(domain, service, data, **kwargs):
        state[data["entity_id"]] = "on" if service == "turn_on" else "off"

    hass.services.async_call = AsyncMock(side_effect=successful_service)
    return SimpleNamespace(
        controller=controller,
        hass=hass,
        state=state,
        clock=clock,
        tracking=tracking,
        callbacks=callbacks,
        dispatcher=dispatcher,
    )


def emitted_types(actuator):
    return [call.args[0].event_type for call in actuator.dispatcher.emit.call_args_list]


async def accumulated_pulse(actuator):
    controller = actuator.controller
    controller.set_duty_accumulator(600.0)
    await controller.async_pwm_switch(
        control_output=5.0,
        **actuator.callbacks,
        time_changed=0.0,
        set_time_changed=MagicMock(),
        force_on=True,
        force_off=False,
        set_force_on=MagicMock(),
        set_force_off=MagicMock(),
    )


@pytest.mark.asyncio
async def test_successful_start_tracks_observed_output(actuator):
    """Positive control: the harness can deliver a real controller pulse."""
    actuator.controller._has_demand = True
    await actuator.controller.async_turn_on(**actuator.callbacks)
    assert actuator.state["switch.zone"] == "on"
    assert actuator.tracking.heating
    assert CycleEventType.HEATING_STARTED in emitted_types(actuator)


@pytest.mark.asyncio
async def test_failed_start_does_not_commit_delivery_or_learning(actuator):
    actuator.hass.services.async_call.side_effect = RuntimeError("relay unavailable")
    actuator.controller._has_demand = True
    await actuator.controller.async_turn_on(**actuator.callbacks)
    assert actuator.state["switch.zone"] == "off"
    assert actuator.controller.heater_control_failed
    assert CycleEventType.HEATING_STARTED not in emitted_types(actuator)
    assert not actuator.tracking.heating
    assert actuator.controller._heat_pipeline._valve_opened_at is None


@pytest.mark.asyncio
async def test_min_off_rejection_retains_accumulated_credit(actuator):
    actuator.tracking.changed = actuator.clock.now - 420.0  # 180s of min-OFF left
    await accumulated_pulse(actuator)
    actuator.hass.services.async_call.assert_not_awaited()
    assert actuator.state["switch.zone"] == "off"
    assert actuator.controller.duty_accumulator_seconds == 600.0


@pytest.mark.asyncio
async def test_failed_accumulated_pulse_retains_credit(actuator):
    actuator.hass.services.async_call.side_effect = RuntimeError("relay unavailable")
    await accumulated_pulse(actuator)
    assert actuator.state["switch.zone"] == "off"
    assert actuator.controller.heater_control_failed
    assert actuator.controller.duty_accumulator_seconds == 600.0


@pytest.mark.asyncio
async def test_force_off_wins_over_inflight_start(actuator):
    """Model separate scheduler and mode entry points with a suspended service."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_service(domain, service, data, **kwargs):
        if service == "turn_on":
            entered.set()
            await release.wait()
        actuator.state[data["entity_id"]] = "on" if service == "turn_on" else "off"

    actuator.hass.services.async_call.side_effect = delayed_service
    pending = asyncio.create_task(actuator.controller.async_turn_on(**actuator.callbacks))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        actuator.controller._has_demand = False
        stopping = asyncio.create_task(actuator.controller.async_turn_off(**actuator.callbacks, force=True))
        await asyncio.sleep(0)
    finally:
        release.set()
        await pending
        await stopping
    assert actuator.state["switch.zone"] == "off"


@pytest.mark.asyncio
async def test_failed_stop_does_not_commit_learning_end(actuator):
    actuator.controller._has_demand = True
    await actuator.controller.async_turn_on(**actuator.callbacks)
    actuator.clock.now += 900.0
    actuator.dispatcher.reset_mock()
    actuator.hass.services.async_call.side_effect = RuntimeError("relay unavailable")
    await actuator.controller.async_turn_off(**actuator.callbacks)
    assert actuator.state["switch.zone"] == "on"
    assert actuator.controller.heater_control_failed
    assert CycleEventType.HEATING_ENDED not in emitted_types(actuator)
    assert actuator.tracking.heating


@pytest.mark.asyncio
async def test_command_acceptance_waits_for_observed_state(actuator, monkeypatch):
    listeners = []
    monkeypatch.setattr(
        actuator.controller._actuation,
        "_listen",
        lambda entity, callback: listeners.append(callback) or (lambda: None),
    )

    async def accepted_without_feedback(*args, **kwargs):
        pass

    actuator.hass.services.async_call.side_effect = accepted_without_feedback
    actuator.controller._has_demand = True
    pending = asyncio.create_task(actuator.controller.async_turn_on(**actuator.callbacks))
    await asyncio.sleep(0)
    assert not actuator.dispatcher.emit.called
    assert not pending.done()
    actuator.state["switch.zone"] = "on"
    listeners[0](None)
    await pending
    assert emitted_types(actuator).count(CycleEventType.HEATING_STARTED) == 1


@pytest.mark.asyncio
async def test_force_off_revokes_waiting_state_confirmation(actuator):
    async def accepted_without_feedback(*args, **kwargs):
        pass

    actuator.hass.services.async_call.side_effect = accepted_without_feedback
    pending = asyncio.create_task(actuator.controller.async_turn_on(**actuator.callbacks))
    await asyncio.sleep(0)
    stopping = asyncio.create_task(actuator.controller.async_turn_off(**actuator.callbacks, force=True))
    await asyncio.wait_for(asyncio.gather(pending, stopping), timeout=1)
    assert actuator.state["switch.zone"] == "off"
    assert not actuator.dispatcher.emit.called


@pytest.mark.asyncio
async def test_slow_service_keeps_observed_cycle_and_pipeline_timestamps(actuator, monkeypatch):
    from custom_components.adaptive_climate.managers import heater_cycle_bookkeeper as bookkeeper_module

    monkeypatch.setattr(bookkeeper_module, "time", SimpleNamespace(monotonic=lambda: actuator.clock.now))

    async def slow_ack(domain, name, data, **kwargs):
        actuator.state[data["entity_id"]] = "on"
        actuator.controller._actuation.observe(data["entity_id"], True)
        actuator.clock.now += 20

    actuator.hass.services.async_call.side_effect = slow_ack
    actuator.controller._has_demand = True
    await actuator.controller.async_turn_on(**actuator.callbacks)
    assert actuator.tracking.changed == 10000
    assert actuator.controller._heat_pipeline._valve_opened_at == 10000
    for call in actuator.dispatcher.emit.call_args_list:
        event = call.args[0]
        assert 19.9 <= (bookkeeper_module.dt_util.utcnow() - event.timestamp).total_seconds() <= 20.1


def test_cancelled_valve_event_cannot_emit_after_timer_cancel(actuator, monkeypatch):
    from custom_components.adaptive_climate.managers import heater_timers as timers_module

    callbacks = []
    monkeypatch.setattr(
        timers_module, "async_call_later", lambda hass, delay, callback: callbacks.append(callback) or (lambda: None)
    )
    timers = actuator.controller._timers
    timers.schedule_heating_started("heat")
    timers.cancel_all()
    callbacks[0](None)
    assert not actuator.dispatcher.emit.called
