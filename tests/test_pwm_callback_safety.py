"""Model HA dispatch: unmarked sync callbacks run in an executor thread."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_climate.managers import heater_actuation, pwm_coordination
from test_pwm_ha_integration import live  # Shared real-controller fixture


@pytest.fixture
def callbacks(monkeypatch, live):
    loop_thread = threading.get_ident()

    def callback(function):
        function._hass_callback = True
        return function

    monkeypatch.setattr(pwm_coordination, "callback", callback)
    monkeypatch.setattr(heater_actuation, "callback", callback)
    listeners, timers, task_threads = {}, [], []

    def track(hass, outputs, action):
        listeners[outputs[0]] = action
        return MagicMock()

    def later(hass, delay, action):
        timers.append((delay, action))
        return MagicMock()

    def create_task(coroutine):
        try:
            task_threads.append(threading.get_ident())
            if threading.get_ident() != loop_thread:
                raise RuntimeError("async_create_task called outside event loop")
        finally:
            coroutine.close()

    monkeypatch.setattr(pwm_coordination, "async_track_state_change_event", track)
    monkeypatch.setattr(pwm_coordination, "async_call_later", later)
    live.entities[0].hass.async_create_task.side_effect = create_task
    live.registry.close()
    live.registry = pwm_coordination.PWMCoordinationRegistry(live.entities[0].hass, live.config)
    for entity in live.entities:
        entity._pwm_coordination = live.registry
        live.registry.bind(entity)

    async def dispatch(action, event):
        if getattr(action, "_hass_callback", False):
            action(event)
        else:
            await asyncio.to_thread(action, event)

    return SimpleNamespace(
        live=live,
        listeners=listeners,
        timers=timers,
        task_threads=task_threads,
        loop_thread=loop_thread,
        dispatch=dispatch,
    )


@pytest.mark.asyncio
async def test_output_listener_creates_update_on_event_loop(callbacks):
    live = callbacks.live
    output = f"{live.domain}.a"
    event = SimpleNamespace(
        data={
            "entity_id": output,
            "old_state": SimpleNamespace(state="off"),
            "new_state": SimpleNamespace(state="on"),
        }
    )
    live.state[output] = "on"
    await callbacks.dispatch(callbacks.listeners[output], event)
    assert callbacks.task_threads == [callbacks.loop_thread]
    assert live.entities[0]._last_heat_cycle_time == live.clock.now


@pytest.mark.asyncio
async def test_timer_wakes_on_loop_and_stale_timer_does_nothing(callbacks):
    live = callbacks.live
    await live.entities[0].apply()
    delay, wake = callbacks.timers[-1]
    assert delay > 0
    await callbacks.dispatch(wake, None)
    assert callbacks.task_threads == [callbacks.loop_thread]
    live.registry.close()
    await callbacks.dispatch(wake, None)
    assert callbacks.task_threads == [callbacks.loop_thread]


@pytest.mark.asyncio
async def test_confirmation_listener_is_nonblocking_loop_callback(callbacks, monkeypatch):
    live = callbacks.live
    output = f"{live.domain}.a"
    actuation = live.entities[0]._heater_controller._actuation
    listeners = []
    cancel = MagicMock()
    monkeypatch.setattr(actuation, "_listen", lambda entity, action: listeners.append(action) or cancel)
    task = asyncio.create_task(actuation._confirm(output, True))
    try:
        await asyncio.sleep(0)
        assert len(listeners) == 1
        assert getattr(listeners[0], "_hass_callback", False)
        live.state[output] = "on"
        await callbacks.dispatch(listeners[0], None)
        assert await asyncio.wait_for(task, 1)
        cancel.assert_called_once()
        assert not actuation._waiters
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
