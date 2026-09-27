"""Tests for PumpFlowController."""

from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_climate.managers import pump_flow_controller as pump_module
from custom_components.adaptive_climate.managers.pump_flow_controller import PumpFlowController

TARGET = "number.heatpump_flow_setpoint"
LIVING = "climate.living"
KITCHEN = "climate.kitchen"
ZONE_FLOWS = {LIVING: 4.0, KITCHEN: 3.5}


class FakeScheduler:
    """Deterministic monotonic clock plus an ``async_call_later`` stand-in."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._timers: list[dict] = []

    def monotonic(self) -> float:
        return self.now

    def call_later(self, _hass, delay, action):
        timer = {"due": self.now + delay, "action": action, "cancelled": False}
        self._timers.append(timer)

        def cancel() -> None:
            timer["cancelled"] = True

        return cancel

    def active(self) -> list[dict]:
        return [t for t in self._timers if not t["cancelled"]]

    async def advance(self, seconds: float) -> None:
        """Move time forward, firing every timer that comes due, earliest first."""
        end = self.now + seconds
        while True:
            due = [t for t in self.active() if t["due"] <= end]
            if not due:
                break
            timer = min(due, key=lambda t: t["due"])
            timer["cancelled"] = True
            self.now = max(self.now, timer["due"])
            await timer["action"](None)
        self.now = end


def number_state(value, *, step=0.5, minimum=0.0, maximum=60.0):
    state = MagicMock()
    state.state = str(value)
    state.attributes = {"step": step, "min": minimum, "max": maximum}
    return state


def build(monkeypatch, *, initial=0.0, min_flow=None, min_write_interval=300, limits=None, target_state=True):
    scheduler = FakeScheduler()
    monkeypatch.setattr(pump_module, "async_call_later", scheduler.call_later)
    limits = limits or {}
    states = {TARGET: number_state(initial, **limits)} if target_state else {}

    async def set_value(_domain, _service, data, blocking=False):
        states[data["entity_id"]] = number_state(data["value"], **limits)

    hass = MagicMock()
    hass.states.get = states.get
    hass.services.async_call = AsyncMock(side_effect=set_value)

    config = {"target_entity": TARGET, "min_write_interval": min_write_interval}
    if min_flow is not None:
        config["min_flow"] = min_flow
    controller = PumpFlowController(hass, config, dict(ZONE_FLOWS), monotonic=scheduler.monotonic)
    return controller, hass, states, scheduler


def written(hass) -> list[float]:
    return [call.args[2]["value"] for call in hass.services.async_call.call_args_list]


async def start(controller) -> None:
    await controller._async_startup(None)


class TestValveTiming:
    @pytest.mark.asyncio
    async def test_zone_flow_waits_for_valve_actuation(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(119)
        assert written(hass) == []

        await sched.advance(1 + 30)  # counts at 120 s, window closes at 150 s
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_valve_closing_before_actuation_never_counts(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(60)
        controller.on_zone_demand(LIVING, False, 0.0)
        await sched.advance(600)

        assert written(hass) == []
        assert controller.diagnostics()["pending_zones"] == {}

    @pytest.mark.asyncio
    async def test_reopen_report_keeps_original_timing(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(60)
        controller.on_zone_demand(LIVING, True, 120.0)  # HEAT -> COOL with the valve still open
        await sched.advance(60 + 30)

        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_zone_without_manifold_flow_is_ignored(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand("climate.garage", True, 0.0)
        await sched.advance(600)

        assert written(hass) == []
        assert sched.active() == []

    @pytest.mark.asyncio
    async def test_diagnostics_report_seconds_until_a_zone_counts(self, monkeypatch):
        controller, _hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(20)

        diagnostics = controller.diagnostics()
        assert diagnostics["pending_zones"] == {LIVING: 100.0}
        assert diagnostics["open_zones"] == []


class TestWritePolicy:
    @pytest.mark.asyncio
    async def test_increase_waits_for_the_coalescing_window(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(29)
        assert written(hass) == []
        await sched.advance(1)
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_window_is_not_extended_by_later_changes(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(20)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(10)

        assert written(hass) == [7.5]

    @pytest.mark.asyncio
    async def test_decrease_waits_for_min_write_interval(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)
        assert written(hass) == []

        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(30 + 299)
        assert written(hass) == []
        assert controller.diagnostics()["raw_sum"] == pytest.approx(4.0)
        assert controller.diagnostics()["decrease_pending_since"] is not None

        await sched.advance(1)
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_pending_decrease_is_dropped_when_target_recovers(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)

        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(100)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(600)

        assert written(hass) == []
        assert controller.diagnostics()["decrease_pending_since"] is None

    @pytest.mark.asyncio
    async def test_drop_to_idle_is_written_after_the_window(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)

        controller.on_zone_demand(LIVING, False, 0.0)
        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(30)

        assert written(hass) == [0.0]

    @pytest.mark.asyncio
    async def test_zero_min_write_interval_writes_decreases_at_once(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5, min_write_interval=0)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)

        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(30)

        assert written(hass) == [4.0]


class TestTarget:
    @pytest.mark.asyncio
    async def test_min_flow_floors_a_small_sum(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, min_flow=12.0)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(30)

        assert written(hass) == [12.0]
        assert controller.diagnostics()["min_flow_applied"] is True

    @pytest.mark.asyncio
    async def test_rounds_up_to_the_entity_step(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, limits={"step": 1.0})
        await start(controller)

        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(30)

        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_entity_maximum_clamps_and_is_flagged(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, limits={"maximum": 5.0})
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(30)

        assert written(hass) == [5.0]
        assert controller.diagnostics()["binding_constraint"] == "entity_limit"

    @pytest.mark.asyncio
    async def test_idle_with_entity_minimum_writes_that_minimum_at_once(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch, initial=8.0, limits={"minimum": 5.0})

        await start(controller)

        assert written(hass) == [5.0]
        assert controller.diagnostics()["binding_constraint"] == "entity_limit"


class TestTargetEntityState:
    @pytest.mark.asyncio
    async def test_missing_target_state_skips_then_retries_on_the_interval(self, monkeypatch):
        controller, hass, states, sched = build(monkeypatch, target_state=False)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(30)
        assert written(hass) == []

        states[TARGET] = number_state(0.0)
        await controller._async_timer_tick(None)
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_non_numeric_target_state_is_overwritten(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch, initial="unavailable")
        controller.on_zone_demand(LIVING, True, 0.0)

        await start(controller)

        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_manual_lowering_is_reverted_on_the_next_interval(self, monkeypatch):
        controller, hass, states, _sched = build(monkeypatch)
        controller.on_zone_demand(LIVING, True, 0.0)
        await start(controller)

        states[TARGET] = number_state(2.0)
        await controller._async_timer_tick(None)

        assert written(hass) == [4.0, 4.0]

    @pytest.mark.asyncio
    async def test_manual_raise_is_reverted_after_min_write_interval(self, monkeypatch):
        controller, hass, states, sched = build(monkeypatch)
        controller.on_zone_demand(LIVING, True, 0.0)
        await start(controller)

        states[TARGET] = number_state(9.0)
        await controller._async_timer_tick(None)
        assert written(hass) == [4.0]

        await sched.advance(300)
        assert written(hass) == [4.0, 4.0]


class TestStartup:
    @pytest.mark.asyncio
    async def test_no_writes_before_startup(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(600)

        assert written(hass) == []

    @pytest.mark.asyncio
    async def test_valves_open_at_startup_count_immediately(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch)
        controller.on_zone_demand(LIVING, True, 120.0)

        await start(controller)

        assert written(hass) == [4.0]
        assert controller.diagnostics()["pending_zones"] == {}


class TestRobustness:
    @pytest.mark.asyncio
    async def test_write_failure_does_not_raise(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch)
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("boom"))
        controller.on_zone_demand(LIVING, True, 0.0)

        await start(controller)

        assert controller.diagnostics()["last_write"] is None

    @pytest.mark.asyncio
    async def test_writes_number_set_value_and_waits_for_the_result(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch)
        controller.on_zone_demand(LIVING, True, 0.0)

        await start(controller)

        assert hass.services.async_call.await_args.args[:2] == ("number", "set_value")
        assert hass.services.async_call.await_args.kwargs["blocking"] is True

    @pytest.mark.asyncio
    async def test_write_errors_are_logged_at_most_once_an_hour(self, monkeypatch, caplog):
        controller, hass, _states, sched = build(monkeypatch)
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("boom"))
        controller.on_zone_demand(LIVING, True, 0.0)

        with caplog.at_level(logging.ERROR):
            await start(controller)
            await controller._async_timer_tick(None)

        errors = [r for r in caplog.records if r.levelname == "ERROR" and "failed to write" in r.getMessage()]
        assert len(errors) == 1

        sched.now += 3600
        with caplog.at_level(logging.ERROR):
            await controller._async_timer_tick(None)

        errors = [r for r in caplog.records if r.levelname == "ERROR" and "failed to write" in r.getMessage()]
        assert len(errors) == 2


class TestConstruction:
    def test_from_config_maps_every_manifold_zone_to_its_flow(self):
        manifolds = [
            {
                "name": "Ground",
                "pipe_volume": 12.0,
                "zones": [{"entity": LIVING, "flow": 4.0}, {"entity": KITCHEN, "flow": 3.5}],
            },
            {"name": "First", "pipe_volume": 8.0, "zones": [{"entity": "climate.bed", "flow": 2.0}]},
        ]

        controller = PumpFlowController.from_config(MagicMock(), {"target_entity": TARGET}, manifolds)

        assert controller._zone_flows == {LIVING: 4.0, KITCHEN: 3.5, "climate.bed": 2.0}


class TestTimers:
    def test_start_registers_a_started_listener_and_an_interval(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch)
        tracked = {}

        def fake_interval(_hass, action, interval):
            tracked["interval"] = interval
            return lambda: None

        monkeypatch.setattr(pump_module, "async_track_time_interval", fake_interval)
        hass.bus.async_listen_once = MagicMock(return_value=lambda: None)

        controller.async_start()

        assert tracked["interval"] == timedelta(seconds=300)
        hass.bus.async_listen_once.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_cancels_every_handle(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        cancelled = []
        monkeypatch.setattr(
            pump_module, "async_track_time_interval", lambda *_args: lambda: cancelled.append("interval")
        )
        hass.bus.async_listen_once = MagicMock(return_value=lambda: cancelled.append("once"))
        controller.async_start()
        await start(controller)
        controller.on_zone_demand(LIVING, True, 120.0)

        controller.async_cleanup()

        assert sorted(cancelled) == ["interval", "once"]
        assert sched.active() == []
