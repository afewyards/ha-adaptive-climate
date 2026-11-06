"""Executable handovers must use real budgets and switching opportunities."""

from collections import Counter
import json
import random
from time import perf_counter

import pytest

from test_pwm_load_balancing import Simulation
from test_pwm_ha_integration import live
from custom_components.adaptive_climate.managers.pwm_runtime_planner import (
    RuntimeWindow,
    measure_windows,
    repair_windows,
)


def protected(durations):
    sim = Simulation(durations)
    sim.manager.max_deferral = 3600
    for zone in sim.zones:
        zone.min_on = zone.min_off = 600
    return sim


def balanced(sim):
    for ledger in sim.manager._zones.values():
        assert ledger.requested == pytest.approx(ledger.delivered + ledger.credit + ledger.cancelled_balance, abs=0.01)


@pytest.mark.asyncio
async def test_signed_overdelivery_uses_remaining_pulse_in_handover():
    sim = protected((1200, 900))
    ledger = sim.manager._zones[sim.zones[0].entity_id]
    ledger.credit, ledger.delivered = -600, 600
    await sim.update()
    await sim.advance(2000)
    assert sim.zones[0].transitions == [(0, True), (600, False)]
    assert sim.zones[1].transitions == [(360, True), (1260, False)]
    assert sim.manager.estimated_starts == 1
    balanced(sim)


@pytest.mark.asyncio
async def test_known_minimum_off_shifts_unstarted_predecessor():
    sim = protected((1200, 900))
    sim.zones[1].last_change = -100
    sim.zones[1].min_off = 1600
    await sim.update()
    await sim.advance(2500)
    assert sim.zones[0].transitions == [(540, True), (1740, False)]
    assert sim.zones[1].transitions == [(1500, True), (2400, False)]
    assert sim.manager.estimated_starts == 1
    balanced(sim)


@pytest.mark.asyncio
async def test_late_ordinary_demand_joins_next_authorized_neighbor():
    sim = protected((1200, 0))
    await sim.update()
    await sim.advance(2990)
    sim.zones[1].seconds = 600
    await sim.update()
    assert not sim.zones[1].on
    await sim.advance(5300)
    assert sim.zones[0].transitions == [(0, True), (1200, False), (3600, True), (4800, False)]
    assert sim.zones[1].transitions == [(3240, True), (3840, False), (4560, True), (5160, False)]
    assert sim.manager.estimated_starts == 2
    assert sim.manager._zones[sim.zones[1].entity_id].priority_start_count == 0
    balanced(sim)


@pytest.mark.asyncio
async def test_real_controller_handover_with_signed_credit(live):
    first, second = live.entities
    second._control_output = 25  # effective pulse 960s including valve timing
    group = live.registry.groups[0]
    ledger = group._zones[first.entity_id]
    ledger.credit, ledger.delivered = -600, 600
    await first.apply()  # 1500s nominal, 900s remaining, effective min-ON 720s
    assert live.state[f"{live.domain}.a"] == "on"
    assert live.state[f"{live.domain}.b"] == "off"
    live.clock.now = 10660
    await second.apply()
    assert live.state[f"{live.domain}.b"] == "on"
    live.clock.now = 10900
    await first.apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert group.estimated_starts == 1
    assert ledger.delivered == 1500  # 600 historical + 900 current


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["minimal", "maximal"])
async def test_real_priority_is_never_held_for_future_handover(strategy):
    sim = protected((1200, 0))
    sim.manager.strategy = strategy
    await sim.update()
    await sim.advance(2990)
    sim.zones[1].seconds = 600
    sim.zones[1].target += 1
    await sim.update()
    assert sim.zones[1].transitions == [(2990, True)]
    assert sim.manager._zones[sim.zones[1].entity_id].priority_start_count == 1


@pytest.mark.asyncio
async def test_maximal_keeps_concentrated_ordinary_policy():
    sim = protected((1200, 0))
    sim.manager.strategy = "maximal"
    await sim.update()
    await sim.advance(2990)
    sim.zones[1].seconds = 600
    await sim.update()
    assert sim.zones[1].transitions == [(2990, True)]


@pytest.mark.asyncio
async def test_handover_wait_never_moves_original_deadline():
    sim = protected((1200, 0))
    sim.manager.max_deferral = 120
    await sim.update()
    await sim.advance(2990)
    sim.zones[1].seconds = 600
    await sim.update()
    for instant in range(3000, 3110, 10):
        await sim.advance(instant)
        await sim.update()
    await sim.advance(3110)
    assert sim.zones[1].transitions == [(3110, True)]
    assert sim.manager._zones[sim.zones[1].entity_id].max_deferral <= 120


@pytest.mark.asyncio
async def test_withdrawn_pulse_cannot_execute_saved_handover_timer():
    sim = protected((1200, 0))
    await sim.update()
    await sim.advance(2990)
    sim.zones[1].seconds = 600
    await sim.update()
    stale = next(iter(sim.timers.values()))[1]
    await sim.advance(3100)
    sim.zones[1].seconds = 0
    sim.manager.invalidate(sim.zones[1].entity_id)
    await sim.update()
    stale()
    assert not sim.tasks
    await sim.advance(5300)
    assert not sim.zones[1].transitions
    balanced(sim)


@pytest.mark.asyncio
async def test_forecast_is_not_an_unconditional_future_heat_command():
    sim = protected((1200, 0))
    await sim.update()
    await sim.advance(2990)
    sim.zones[1].seconds = 600
    await sim.update()
    await sim.advance(3100)
    sim.zones[0].seconds = 0
    await sim.update()
    await sim.advance(5300)
    assert sim.zones[0].transitions == [(0, True), (1200, False)]
    balanced(sim)


@pytest.mark.asyncio
async def test_expiring_predecessor_can_handover_before_same_transaction_off():
    sim = protected((600, 0))
    await sim.update()
    # Deliberately let the event loop reach the expiry before delivering its timer.
    sim.now = 600
    sim.zones[1].seconds = 900
    await sim.update()
    assert sim.zones[0].transitions[-1] == (600, False)
    assert sim.zones[1].transitions == [(600, True)]
    assert sim.manager.estimated_starts == 1
    balanced(sim)


@pytest.mark.asyncio
async def test_on_budget_revision_replans_successor_without_restarting_predecessor():
    sim = protected((1200, 900))
    await sim.update()
    await sim.advance(300)
    sim.zones[0].seconds = 900
    await sim.update()
    await sim.advance(1800)
    assert sim.zones[0].transitions == [(0, True), (900, False)]
    assert sim.zones[1].transitions == [(660, True), (1560, False)]
    assert sim.manager.estimated_starts == 1
    balanced(sim)


@pytest.mark.asyncio
async def test_24h_late_ordinary_trace_preserves_heat_and_reduces_starts(monkeypatch):
    reports = []
    for repaired in (False, True):
        sim = protected((1200, 0))
        if not repaired:
            # Same allocation/actuation runtime, with the pre-fix nominal-only
            # placement. This is not the independent upstream PWM baseline.
            monkeypatch.setattr(sim.manager, "_plan_executable", lambda *_: None)
        await sim.update()
        for epoch in range(24):
            await sim.advance(epoch * 3600 + 2990)
            sim.zones[1].seconds = 600
            await sim.update()
            await sim.advance(epoch * 3600 + 4000)
            sim.zones[1].seconds = 0
            await sim.update()
        await sim.advance(24 * 3600 + 1800)  # include the last wrapping pulse
        for ledger in sim.manager._zones.values():
            sim.manager._sample(ledger, sim.now)
        for zone in sim.zones:
            counts = Counter(int(at // 3600) for at, on in zone.transitions if on)
            assert max(counts.values()) == 1
        balanced(sim)
        reports.append(
            {
                "executable_repair": repaired,
                "trace_seconds": 86400,
                "drain_seconds": 1800,
                "group_starts": sim.manager.estimated_starts,
                "delivered": [z.delivered for z in sim.manager._zones.values()],
                "credit": [z.credit for z in sim.manager._zones.values()],
                "high_weight_overlap": sim.manager.high_weight_overlap_seconds,
            }
        )
    assert reports[0]["delivered"] == reports[1]["delivered"] == [30000, 14400]
    assert reports[0]["group_starts"] == 49
    assert reports[1]["group_starts"] == 25
    print(json.dumps({"late_ordinary_24h": reports}, sort_keys=True))


@pytest.mark.parametrize("seed", range(20))
def test_bounded_repair_preserves_constraints_and_never_worsens_metrics(seed):
    rng = random.Random(seed)
    windows = []
    for index in range(16):
        earliest = rng.randrange(0, 3600)
        latest = earliest + rng.randrange(0, 1800)
        start = rng.uniform(earliest, latest)
        windows.append(
            RuntimeWindow(
                str(index), 4 if index == 0 else 1, start, rng.randrange(300, 1800), earliest, latest, index % 3 != 0
            )
        )
    before = measure_windows(windows, "0", 240)
    clock = perf_counter()
    repaired = repair_windows(windows, "0", 240, {})
    elapsed = perf_counter() - clock
    assert measure_windows(repaired, "0", 240) <= before
    assert repair_windows(windows, "0", 240, {}) == repaired
    for old, new in zip(windows, repaired):
        assert new.duration == old.duration
        assert old.earliest <= new.start <= old.latest
        if not old.movable:
            assert new == old
    assert elapsed < 5  # broad CPU guard, not a Raspberry Pi timing claim


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_overlap_cannot_create_a_runtime_plan(value):
    with pytest.raises(ValueError):
        repair_windows([], "zone", value, {})
