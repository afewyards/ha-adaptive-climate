"""Deterministic pure planning, runtime ledger and urgent demand tests."""

from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest
import voluptuous as vol

from custom_components.adaptive_climate.managers.heater_actuation import ActuationResult
from custom_components.adaptive_climate.managers.pwm_load_balancing_manager import PWMLoadBalancingManager
from custom_components.adaptive_climate.managers.pwm_load_balancing_optimizer import ZoneRequest, plan
from custom_components.adaptive_climate.managers.pwm_pulse import Pulse
from custom_components.adaptive_climate.pwm_coordination_config import PWM_COORDINATION_SCHEMA


@dataclass
class SimZone:
    entity_id: str
    clock: object
    seconds: float = 720.0
    period: float = 3600.0
    mode: str = "heat"
    target: float = 21.0
    paused: bool = False
    unavailable: bool = False
    on: bool = False
    last_change: float = -100000.0
    min_on: float = 0.0
    min_off: float = 0.0
    failure: bool = False
    released: bool = False
    prepare_count: int = 0
    transitions: list = field(default_factory=list)

    @property
    def outputs(self):
        return (self.entity_id.replace("climate.", "switch."),)

    def eligible(self):
        return self.mode == "heat" and not self.paused and self.seconds > 0

    def available(self):
        return not self.unavailable

    def active(self):
        return self.on

    def active_elapsed(self, now):
        return max(0.0, now - self.last_change) if self.on else 0.0

    def output(self):
        return self.seconds / self.period * 100

    def signature(self):
        return (self.target, self.mode)

    def prepare(self, now):
        self.prepare_count += 1
        return Pulse(self.seconds, continuous=self.seconds == self.period)

    def minimum_remaining(self, on, now):
        return max(0.0, (self.min_off if on else self.min_on) - (now - self.last_change))

    def minimum_on_seconds(self):
        return self.min_on

    def invalidate(self):
        pass

    def commit_credit(self, seconds):
        pass

    async def command(self, on):
        if self.failure:
            return ActuationResult.FAILED
        if on and not self.eligible():
            return ActuationResult.UNAVAILABLE
        if self.on == on:
            return ActuationResult.UNCHANGED
        self.on = on
        self.last_change = self.clock.now
        self.transitions.append((self.clock.now, on))
        return ActuationResult.CHANGED

    def release(self):
        self.released = True


class Simulation:
    def __init__(self, durations=(1440, 1080, 1080, 1440), strategy="minimal", period=3600.0):
        self.now = 0.0
        self.timers = {}
        self.tasks = []
        self.token = 0
        self.zones = [SimZone(f"climate.zone_{n}", self, value, period) for n, value in enumerate(durations)]
        self.manager = PWMLoadBalancingManager(
            "test",
            {z.entity_id: 4.0 if n == 0 else 1.0 for n, z in enumerate(self.zones)},
            240,
            7200,
            strategy,
            self.later,
            self.tasks.append,
            lambda: self.now,
        )
        for zone in self.zones:
            self.manager.register(zone)

    def later(self, delay, callback):
        self.token += 1
        key = self.token
        self.timers[key] = (self.now + delay, callback)
        return lambda: self.timers.pop(key, None)

    async def update(self, zone=0):
        return await self.manager.update(self.zones[zone].entity_id)

    async def advance(self, until):
        iterations = 0
        while self.timers:
            key = min(self.timers, key=lambda k: self.timers[k][0])
            when, callback = self.timers[key]
            if when > until:
                break
            self.now = when
            self.timers.pop(key)
            callback()
            while self.tasks:
                await self.tasks.pop(0)
            iterations += 1
            if iterations > 10000:
                raise RuntimeError("scheduler failed to make bounded progress")
        self.now = until


@pytest.mark.parametrize("strategy", ["minimal", "maximal"])
@pytest.mark.parametrize(
    "duties",
    [
        (0, 0, 0, 0),
        (20, 10, 5, 5),
        (35, 20, 15, 10),
        (40, 30, 20, 10),
        (40, 30, 30, 40),
        (90, 70, 50, 40),
        (100, 100, 100, 100),
    ],
)
def test_planner_exact_durations(strategy, duties):
    requests = [ZoneRequest(str(n), d * 36, 4 if n == 0 else 1) for n, d in enumerate(duties)]
    schedule = plan(requests, 3600, 240, strategy)
    for request in requests:
        assert sum(i.end - i.start for i in schedule.intervals if i.entity_id == request.entity_id) == pytest.approx(
            request.on_seconds
        )
    assert schedule.cyclic_starts <= 1
    assert schedule == plan(requests, 3600, 240, strategy)


def test_reference_high_weight_solo_and_strategy_difference():
    requests = [ZoneRequest(str(n), d, 4 if n == 0 else 1) for n, d in enumerate((1440, 1080, 1080, 1440))]
    minimal = plan(requests, 3600, 240, "minimal")
    maximal = plan(requests, 3600, 240, "maximal")
    assert minimal.energized_seconds == 3600
    assert minimal.cyclic_starts == 0
    assert minimal.high_weight_overlap_seconds == 480
    assert maximal.energized_seconds == 1440


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1])
def test_planner_invalid_numbers(bad):
    with pytest.raises(ValueError):
        plan([ZoneRequest("a", bad)], 3600)


def test_schema_disabled_and_modes():
    assert PWM_COORDINATION_SCHEMA({}) == {"enabled": False, "mode": "load_balancing", "groups": []}
    for strategy in ("minimal", "maximal"):
        config = {
            "enabled": True,
            "groups": [
                {
                    "name": "pump",
                    "overlap_strategy": strategy,
                    "zones": [{"entity": "climate.a"}, {"entity": "climate.b"}],
                }
            ],
        }
        assert PWM_COORDINATION_SCHEMA(config)["groups"][0]["overlap_strategy"] == strategy
        config["groups"][0]["zones"][1]["entity"] = "climate.a"
        with pytest.raises(vol.Invalid):
            PWM_COORDINATION_SCHEMA(config)


@pytest.mark.parametrize("case", ["setpoint", "off_heat", "pump_off", "overlap"])
@pytest.mark.asyncio
async def test_immediate_eligible_demand_replaces_future_slot(case):
    sim = Simulation()
    await sim.update()
    await sim.advance(3600 + 1020)
    zone = sim.zones[3]
    ledger = sim.manager._zones[zone.entity_id]
    if zone.on:
        await zone.command(False)
        ledger.observed_on = False
    ledger.credit = 720
    ledger.allocation = zone.seconds
    ledger.priority = False
    ledger.target_signature = (zone.target, "off" if case == "off_heat" else "heat")
    zone.target += 1
    stale = next(iter(sim.timers.values()))[1]
    before = len(zone.transitions)
    await sim.update(3)
    assert zone.on
    assert zone.transitions[-1] == (sim.now, True)
    assert ledger.priority_latency_ms == 0
    stale()
    assert not sim.tasks  # obsolete callback must not even dispatch a task
    assert len(zone.transitions) == before + 1


@pytest.mark.asyncio
async def test_first_safe_min_off_retry():
    sim = Simulation((720, 720))
    zone = sim.zones[0]
    zone.min_off = 180
    zone.last_change = 0
    await sim.update()
    assert not zone.on
    await sim.advance(179)
    assert not zone.on
    await sim.advance(180)
    assert zone.on
    assert zone.transitions[0] == (180, True)


@pytest.mark.asyncio
async def test_already_on_replan_freezes_preparation_and_single_payment():
    sim = Simulation((1200, 1200))
    await sim.update()
    zone = sim.zones[0]
    count = zone.prepare_count
    await sim.advance(400)
    await sim.update()
    assert zone.prepare_count == count
    zone.target += 1
    zone.seconds = 1500
    await sim.update()
    ledger = sim.manager._zones[zone.entity_id]
    assert zone.transitions == [(0, True)]
    assert ledger.delivered == 400
    assert ledger.credit == 1100
    assert ledger.requested == ledger.delivered + ledger.credit


@pytest.mark.parametrize("reason", ["off", "pause", "zero"])
@pytest.mark.asyncio
async def test_cancelled_demand_cannot_resurrect(reason):
    sim = Simulation((720, 720))
    await sim.update()
    zone = sim.zones[0]
    if reason == "off":
        zone.mode = "off"
    elif reason == "pause":
        zone.paused = True
    else:
        zone.seconds = 0
    sim.manager.invalidate(zone.entity_id)
    await sim.update()
    assert not zone.on
    await sim.advance(2 * 3600)
    assert not zone.on
    assert zone.transitions == [(0, True), (0, False)]


@pytest.mark.asyncio
async def test_unavailable_and_failure_release_ownership_without_off_glitch():
    for failure in (False, True):
        sim = Simulation((720, 720))
        sim.zones[0].unavailable = not failure
        sim.zones[0].failure = failure
        assert not await sim.update()
        assert sim.manager.mode == "fallback"
        assert all(z.released for z in sim.zones)
        assert not sim.timers


@pytest.mark.asyncio
async def test_wrap_debt_is_paid_once_and_full_demand_has_no_off_edge():
    sim = Simulation((3600, 1200))
    await sim.update()
    await sim.advance(24 * 3600)
    full = sim.zones[0]
    ledger = sim.manager._zones[full.entity_id]
    assert full.transitions == [(0, True)]
    assert ledger.delivered == 24 * 3600
    assert ledger.credit == 3600


@pytest.mark.asyncio
async def test_removal_revokes_saved_callback():
    sim = Simulation((720, 720))
    await sim.update()
    stale = next(iter(sim.timers.values()))[1]
    sim.manager.remove(sim.zones[0].entity_id)
    stale()
    assert not sim.tasks
    assert not sim.timers
    assert sim.manager.mode == "fallback"


@pytest.mark.parametrize("trigger", ["setpoint", "off_heat"])
@pytest.mark.asyncio
async def test_120_minute_period_does_not_defer_priority(trigger):
    sim = Simulation((2880, 2160, 2160, 2880), period=7200)
    await sim.update()
    await sim.advance(7200 + 1020)
    zone = sim.zones[3]
    assert not zone.on
    assert sim.manager.schedule.phases[zone.entity_id] > 3600
    if trigger == "setpoint":
        zone.target += 1
    else:
        zone.mode = "off"
        await sim.update(3)
        zone.mode = "heat"
    await sim.update(3)
    assert zone.on
    assert zone.transitions[-1] == (sim.now, True)


@pytest.mark.parametrize("mode", ["heat", "off"])
@pytest.mark.asyncio
async def test_i04_i05_zero_demand_never_gets_priority_heat(mode):
    sim = Simulation((0, 720))
    zone = sim.zones[0]
    zone.mode = mode
    await sim.update()
    zone.mode = "heat"
    zone.target += 1
    await sim.update()
    await sim.advance(7200)
    assert not zone.transitions


@pytest.mark.asyncio
async def test_i11_latest_repeated_change_no_duplicate_payment():
    sim = Simulation((720, 720))
    await sim.update()
    await sim.advance(100)
    zone = sim.zones[0]
    for target, seconds in ((22, 1000), (23, 1200), (22, 900)):
        zone.target, zone.seconds = target, seconds
        await sim.update()
    ledger = sim.manager._zones[zone.entity_id]
    assert zone.transitions == [(0, True)]
    assert ledger.credit == 800
    assert ledger.requested == 900


@pytest.mark.asyncio
async def test_i16_ordinary_pid_change_uses_phase_not_priority():
    sim = Simulation()
    await sim.update()
    await sim.advance(3600 + 1020)
    zone = sim.zones[3]
    assert not zone.on
    zone.seconds += 30
    await sim.update(3)
    assert not zone.on
    assert not sim.manager._zones[zone.entity_id].priority


@pytest.mark.asyncio
async def test_new_eligible_setpoint_after_paid_pulse_still_starts_current_period():
    sim = Simulation((720, 720))
    await sim.update()
    await sim.advance(1020)
    zone = sim.zones[0]
    assert not zone.on
    zone.target += 1
    await sim.update()
    assert zone.transitions[-1] == (1020, True)
    ledger = sim.manager._zones[zone.entity_id]
    assert ledger.delivered == 720
    assert ledger.credit == 720
    assert ledger.requested == 1440


@pytest.mark.asyncio
async def test_priority_living_room_accepts_overlap_with_active_small_zone():
    sim = Simulation()
    await sim.update()
    await sim.advance(1500)
    assert not sim.zones[0].on and sim.zones[1].on
    sim.zones[0].target += 1
    await sim.update()
    assert sim.zones[0].transitions[-1] == (1500, True)
    assert sim.zones[1].on


@pytest.mark.asyncio
async def test_urgent_pulse_crosses_boundary_without_duplicate_start():
    sim = Simulation((0, 720))
    await sim.update()
    await sim.advance(3500)
    sim.zones[0].seconds = 720
    sim.zones[0].target += 1
    await sim.update()
    await sim.advance(3620)
    assert sim.zones[0].transitions == [(3500, True)]
    ledger = sim.manager._zones[sim.zones[0].entity_id]
    assert ledger.delivered == 100  # sampled at the boundary; 20s still running
    sim.manager.observe(sim.zones[0].entity_id)
    assert ledger.delivered == 120
    assert ledger.requested == ledger.delivered + ledger.credit


@pytest.mark.asyncio
async def test_fallback_during_priority_does_not_cycle_other_active_output():
    sim = Simulation((720, 720))
    await sim.update()
    sim.zones[1].target += 1
    sim.zones[1].failure = True
    assert not await sim.update(1)
    assert sim.zones[0].transitions == [(0, True)]
    assert all(zone.released for zone in sim.zones)
    assert not sim.timers


@pytest.mark.asyncio
async def test_200_pid_replans_cannot_move_existing_deadline_forever():
    sim = Simulation()
    sim.manager.max_deferral = 120
    await sim.update()
    zone = sim.zones[3]
    for n in range(200):
        await sim.advance(float(n * 2))
        zone.seconds = 1440 + (30 if n % 2 else 0)
        await sim.update(3)
        assert len(sim.timers) <= 1
    assert zone.transitions[0] == (120, True)
    ledger = sim.manager._zones[zone.entity_id]
    assert ledger.requested == ledger.delivered + ledger.credit


@pytest.mark.asyncio
async def test_known_startup_on_is_not_a_new_pump_start_or_full_duplicate_pulse():
    sim = Simulation((720, 720))
    sim.manager.mode = "waiting_for_members"
    sim.manager._zones.clear()
    sim.zones[0].on = True
    sim.zones[0].last_change = -100
    for zone in sim.zones:
        sim.manager.register(zone)
    await sim.update()
    ledger = sim.manager._zones[sim.zones[0].entity_id]
    assert ledger.delivered == 100
    assert ledger.credit == 620
    assert sim.manager.estimated_starts == 0


@pytest.mark.asyncio
async def test_late_callback_charges_real_elapsed_and_no_busy_loop():
    sim = Simulation((720, 720))
    await sim.update()
    callback = next(iter(sim.timers.values()))[1]
    sim.timers.clear()
    sim.now = 780
    callback()
    await sim.tasks.pop(0)
    ledger = sim.manager._zones[sim.zones[0].entity_id]
    assert ledger.delivered == 780
    assert ledger.credit == -60
    assert not sim.zones[0].on
    assert all(when > sim.now for when, _ in sim.timers.values())


def test_invalid_group_mixed_period_and_duplicate_output():
    sim = Simulation((720, 720))
    sim.manager.remove(sim.zones[0].entity_id)
    assert sim.manager.mode == "fallback"
    sim = Simulation((720, 720))
    sim.manager.mode = "waiting_for_members"
    sim.manager._zones.clear()
    sim.zones[1].period = 7200
    for zone in sim.zones:
        sim.manager.register(zone)
    assert sim.manager.mode == "fallback"
