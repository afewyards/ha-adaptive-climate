"""Ordinary output edges require a period entitlement, not merely positive credit."""

import asyncio
from collections import Counter
import json

import pytest

from test_pwm_ha_integration import live
from test_pwm_load_balancing import Simulation
from test_pwm_restart import journal


def balanced(ledger):
    assert ledger.requested == pytest.approx(ledger.delivered + ledger.credit + ledger.cancelled_balance, abs=0.01)


async def finish(live):
    entity = live.entities[0]
    entity._control_output = 21.8
    await entity.apply()
    live.clock.now += 844.8
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_218_to_219_after_finished_pulse_does_not_start_again(live):
    entity = live.entities[0]
    output = f"{live.domain}.a"
    entity._control_output = 21.8
    await entity.apply()
    live.clock.now += 844.8  # 784.8 demand +120 open -60 close lead
    await entity.apply()
    assert live.state[output] == "off"
    entity._control_output = 21.9
    await entity.apply()
    live.clock.now += 600
    await entity.apply()
    on_edges = [at for at, service, target in live.commands if target == output and service == "turn_on"]
    assert len(on_edges) == 1
    assert live.state[output] == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [0.1, 0.5, 1.0])
async def test_repeated_pid_fluctuations_and_other_zone_replans_cannot_restart(live, delta):
    await finish(live)
    for value in (21.8 + delta, 21.8 - delta, 0, 21.8 + delta, 21.8, 21.8 + delta):
        live.entities[0]._control_output = value
        live.entities[1]._control_output += 1
        live.clock.now += 120
        await live.entities[1].apply()
        await live.entities[0].apply()
        ledger = live.registry.groups[0]._zones["climate.a"]
        balanced(ledger)
        assert ledger.credit <= 0
        assert live.state[f"{live.domain}.a"] == "off"
    assert sum(service == "turn_on" and target == f"{live.domain}.a" for _, service, target in live.commands) == 1


@pytest.mark.asyncio
async def test_pid_change_while_on_adjusts_contiguous_duration_once(live):
    entity = live.entities[0]
    entity._control_output = 21.8
    await entity.apply()
    live.clock.now += 300
    entity._control_output = 22.8
    await entity.apply()
    live.clock.now += 580.8
    await entity.apply()
    edges = [(at, service) for at, service, target in live.commands if target == f"{live.domain}.a"]
    assert edges == [(10000, "turn_on"), (10880.8, "turn_off")]
    ledger = live.registry.groups[0]._zones["climate.a"]
    assert ledger.delivered == pytest.approx(880.8)
    assert ledger.credit == pytest.approx(0)
    balanced(ledger)


@pytest.mark.asyncio
async def test_next_logical_period_grants_one_start_not_a_rolling_hour_cooldown(live):
    live.clock.now += 300
    await finish(live)  # first ON at +300, not at origin
    live.entities[0]._control_output = 21.9
    live.clock.now = 13600  # original group origin +3600, <3600 after first start
    await live.entities[0].apply()
    edges = [at for at, service, target in live.commands if service == "turn_on" and target == f"{live.domain}.a"]
    assert edges == [10300, 13600]
    assert live.registry.groups[0]._zones["climate.a"].last_ordinary_start_epoch == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["target", "off_heat", "effective_schedule"])
async def test_deliberate_priority_is_exception_but_spends_remaining_ordinary_allowance(live, transition):
    await finish(live)
    entity = live.entities[0]
    live.clock.now += 100
    if transition == "target":
        entity._set_target_temp(23)
    elif transition == "off_heat":
        entity.hvac_mode = "off"
        await entity.apply()
        entity.hvac_mode = "heat"
        live.registry.invalidate(entity.entity_id, urgent=True)
    else:
        # Existing IMMEDIATE_DEMAND_SPEC explicitly includes effective schedule
        # targets. A genuinely changed target is not an ordinary PID fluctuation.
        from types import SimpleNamespace

        entity._control_output_manager = SimpleNamespace(_last_effective_target=23)
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "off"
    ledger = live.registry.groups[0]._zones[entity.entity_id]
    assert ledger.priority_blocked_reason == "minimum_off"
    live.clock.now += 499
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "off"
    live.clock.now += 1
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "on"
    assert ledger.priority_start_count == 1
    live.clock.now += 844.8
    await entity.apply()
    entity._control_output = 21.9
    live.clock.now += 600
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert sum(service == "turn_on" and target == f"{live.domain}.a" for _, service, target in live.commands) == 2
    assert ledger.priority_start_count == 1
    balanced(ledger)


@pytest.mark.asyncio
async def test_stale_phase_budget_cannot_allocate_twice_in_same_epoch(live):
    await finish(live)
    ledger = live.registry.groups[0]._zones["climate.a"]
    requested = ledger.requested
    ledger.next_budget_at = live.clock.now  # displaced phase deadline from an earlier replan
    live.clock.now += 600
    await live.entities[0].apply()
    assert ledger.epoch == 0
    assert ledger.requested == requested
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_timers_at_minimum_off_boundary_do_not_duplicate_pulse():
    sim = Simulation((784.8, 720))
    zone = sim.zones[0]
    zone.min_on = zone.min_off = 600
    await sim.update()
    await sim.advance(784.8)
    assert not zone.on
    zone.seconds = 788.4
    await sim.update()
    callbacks = [action for _, action in sim.timers.values()]
    await sim.advance(1384.8)
    for callback in callbacks:
        callback()
    while sim.tasks:
        await sim.tasks.pop(0)
    assert zone.transitions == [(0, True), (784.8, False)]


@pytest.mark.asyncio
async def test_closed_pid_revisions_do_not_ratchet_or_erase_real_negative_credit():
    sim = Simulation((300, 720))
    zone = sim.zones[0]
    zone.min_on = zone.min_off = 600
    await sim.update()
    await sim.advance(600)
    ledger = sim.manager._zones[zone.entity_id]
    assert not zone.on
    assert ledger.credit == -300  # actual minimum-ON overrun
    for duration in (303.6, 298.2, 318, 336, 300):
        zone.seconds = duration
        await sim.update()
        assert ledger.credit == -300
        balanced(ledger)
    assert ledger.cancelled_balance == pytest.approx(0)
    assert sim.manager.diagnostics(zone.entity_id)["unmet_debt_seconds"] == 0
    assert sim.manager.diagnostics(zone.entity_id)["remaining_credit_seconds"] == -300


@pytest.mark.asyncio
async def test_blocked_ordinary_start_is_not_consumed_until_output_changes(live):
    entity = live.entities[0]
    entity._last_heat_cycle_time = live.clock.now - 500
    await entity.apply()
    ledger = live.registry.groups[0]._zones["climate.a"]
    assert ledger.last_start_epoch == -1
    assert ledger.last_ordinary_start_epoch == -1
    live.clock.now += 100
    await entity.apply()
    assert ledger.last_start_epoch == ledger.last_ordinary_start_epoch == 0


@pytest.mark.asyncio
async def test_delayed_ordinary_edge_is_attributed_to_actual_period_not_original_budget():
    sim = Simulation((720, 720))
    # Isolate observed epoch attribution from optional ordinary handover repair.
    sim.manager.max_deferral = 0
    zone = sim.zones[0]
    zone.min_on = zone.min_off = 600
    sim.now = 3500
    zone.last_change = 3200
    await sim.update()
    assert not zone.on
    await sim.advance(3800)
    assert zone.transitions[0] == (3800, True)
    assert sim.manager._zones[zone.entity_id].last_ordinary_start_epoch == 1
    await sim.advance(5300)
    assert not zone.on
    zone.seconds += 3.6
    await sim.update()
    await sim.advance(7100)
    assert Counter(int(at // 3600) for at, on in zone.transitions if on) == {1: 1}


@pytest.mark.asyncio
async def test_command_crossing_boundary_records_observed_edge_epoch():
    sim = Simulation((720, 720))
    zone = sim.zones[0]
    command = zone.command

    async def delayed(on):
        if on:
            sim.now += 200
        return await command(on)

    zone.command = delayed
    sim.now = 3500
    await sim.update()
    assert zone.transitions[0] == (3700, True)
    ledger = sim.manager._zones[zone.entity_id]
    assert ledger.last_start_epoch == ledger.last_ordinary_start_epoch == 1


@pytest.mark.asyncio
async def test_concurrent_reconciliation_cannot_duplicate_start(live):
    await asyncio.gather(*(live.entities[0].apply() for _ in range(4)))
    assert sum(service == "turn_on" and target == f"{live.domain}.a" for _, service, target in live.commands) == 1
    assert live.registry.groups[0]._zones["climate.a"].last_ordinary_start_epoch == 0


@pytest.mark.asyncio
async def test_pause_resume_does_not_grant_another_ordinary_pulse(live):
    await finish(live)
    entity = live.entities[0]
    entity._status_manager.is_paused = lambda _: True
    await entity.apply()
    live.clock.now += 600
    entity._status_manager.is_paused = lambda _: False
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_low_duty_credit_waits_for_next_period_without_repeated_minimum_pulses(live):
    entity = live.entities[0]
    heater = entity._heater_controller
    entity._control_output = 5
    heater.set_duty_accumulator(600)
    await entity.apply()
    live.clock.now += 720
    await entity.apply()
    heater.set_duty_accumulator(600)
    for _ in range(3):
        live.clock.now += 300
        await entity.apply()
        assert live.state[f"{live.domain}.a"] == "off"
        assert heater.duty_accumulator_seconds == 600
    live.clock.now = 13600
    await entity.apply()
    assert live.state[f"{live.domain}.a"] == "on"
    assert heater.duty_accumulator_seconds == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_completed_entitlement_survives_restarts_and_legacy_journals(journal, clean, legacy):
    live = journal.live
    entity = live.entities[0]
    entity._control_output = 21.8
    await entity.apply()
    journal.advance(844.8)
    await entity.apply()
    if clean:
        await live.registry.async_save()
    if legacy:
        for row in journal.disk.data["pwm_coordination"]["pump"]["zones"].values():
            for key in (
                "last_start_epoch",
                "allocation_started",
                "last_ordinary_start_epoch",
                "pending_start_epoch",
                "suppressed_ordinary_starts",
            ):
                row.pop(key, None)
    for _ in range(4):
        await journal.reboot(offline=30)
        live.entities[0]._control_output = 21.9
        await live.entities[0].apply()
        assert live.state[f"{live.domain}.a"] == "off"
    journal.advance(600)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().last_start_epoch == 0
    balanced(journal.ledger())
    assert sum(service == "turn_on" and target == f"{live.domain}.a" for _, service, target in live.commands) == 1


@pytest.mark.asyncio
async def test_output_edge_during_checkpoint_cannot_duplicate_start(journal):
    live = journal.live
    journal.disk.block = True
    task = asyncio.create_task(live.entities[0].apply())
    await asyncio.wait_for(journal.disk.entered.wait(), 1)
    output = f"{live.domain}.a"
    # An external edge pair occurred while the checkpoint yielded. No demand
    # generation changed, but it still spent the period's observed start.
    live.state[output] = "on"
    live.registry.groups[0].observe("climate.a")
    live.state[output] = "off"
    live.registry.groups[0].observe("climate.a")
    journal.disk.release.set()
    await asyncio.wait_for(task, 1)
    assert not any(service == "turn_on" and target == output for _, service, target in live.commands)
    assert journal.ledger().last_start_epoch == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("last_start_epoch", True),
        ("last_start_epoch", None),
        ("last_start_epoch", 9999),
        ("allocation_started", "false"),
        ("pending_start_epoch", 0.5),
    ],
)
async def test_malformed_allowance_does_not_authorize_fresh_heat(journal, key, value):
    live = journal.live
    await journal.complete_first()
    journal.disk.data["pwm_coordination"]["pump"]["zones"]["climate.a"][key] = value
    await journal.reboot(offline=5)
    await live.entities[0].apply()
    assert live.registry.groups[0].reason == "restart_state_invalid"
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["minimal", "maximal"])
async def test_24h_fluctuating_pid_preserves_period_start_limit_and_delivery(strategy):
    sim = Simulation((784.8, 720, 720, 720), strategy=strategy)
    base = [z.seconds for z in sim.zones]
    for zone in sim.zones:
        zone.min_on = zone.min_off = 600
    for second in range(0, 86400, 30):
        await sim.advance(second)
        epoch = second // 3600
        for index, zone in enumerate(sim.zones):
            ledger = sim.manager._zones[zone.entity_id]
            delta = (3.6, 18, 36, -3.6)[(second // 30 + index) % 4]
            zone.seconds = base[index] + delta if not zone.on and ledger.last_start_epoch == epoch else base[index]
        await sim.update()
    await sim.advance(86400 - 0.001)
    counts = []
    for index, zone in enumerate(sim.zones):
        epochs = Counter(int(at // 3600) for at, on in zone.transitions if on)
        assert max(epochs.values()) == 1
        assert sum(epochs.values()) == 24
        ledger = sim.manager._zones[zone.entity_id]
        assert ledger.delivered == pytest.approx(base[index] * 24, abs=0.01)
        assert ledger.credit <= 0.001
        balanced(ledger)
        counts.append(sum(epochs.values()))
    assert sim.manager.estimated_starts == 24
    if strategy == "minimal":
        assert sim.manager.high_weight_overlap_seconds <= 24 * 480
    print(
        json.dumps(
            {
                "strategy": strategy,
                "period": 3600,
                "simulated_seconds": 86400,
                "ordinary_starts_per_zone": counts,
                "pump_starts": sim.manager.estimated_starts,
                "delivered_seconds": [round(sim.manager._zones[z.entity_id].delivered, 3) for z in sim.zones],
                "high_weight_overlap_seconds": sim.manager.high_weight_overlap_seconds,
            },
            sort_keys=True,
        )
    )
