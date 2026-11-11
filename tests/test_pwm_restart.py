"""Restart actual HA adapters/controllers against a durable in-memory HA Store."""

import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from custom_components.adaptive_climate.adaptive import persistence
from custom_components.adaptive_climate.managers import pwm_coordination
from test_pwm_ha_integration import Entity, live


@pytest_asyncio.fixture
async def journal(live, monkeypatch):
    disk = SimpleNamespace(
        data=None, fail=False, saves=0, fail_on=None, block=False, entered=asyncio.Event(), release=asyncio.Event()
    )
    hass = live.entities[0].hass
    hass.states.get = lambda entity: SimpleNamespace(state=live.state[entity]) if entity in live.state else None
    hass.states.is_state = lambda entity, expected: live.state.get(entity) == expected
    wall = SimpleNamespace(seconds=0.0)
    monkeypatch.setattr(
        pwm_coordination,
        "dt_util",
        SimpleNamespace(utcnow=lambda: datetime.fromtimestamp(1793818800 + wall.seconds, timezone.utc)),
    )

    class DiskStore:
        async def async_load(self):
            return copy.deepcopy(disk.data)

        async def async_save(self, data):
            if disk.block:
                disk.entered.set()
                await disk.release.wait()
            if disk.fail or disk.saves + 1 == disk.fail_on:
                raise OSError("disk full")
            disk.data = copy.deepcopy(data)
            disk.saves += 1

    monkeypatch.setattr(persistence, "_create_store", lambda *args: DiskStore())

    class Restart:
        async def reboot(self, offline=0, bind_all=True):
            old = live.entities
            live.registry.close()
            wall.seconds += offline
            live.clock.now = 10000.0 if disk.data is None else 100.0  # simulate a reset after first journal
            self.store = persistence.LearningDataStore(old[0].hass)
            await self.store.async_load()
            live.registry = pwm_coordination.PWMCoordinationRegistry(old[0].hass, live.config)
            await live.registry.async_initialize(self.store)
            live.entities = [Entity(e.entity_id, e.hass, live.domain) for e in old]
            for previous, entity in zip(old, live.entities, strict=True):
                entity._target_temp = previous._target_temp
                entity.target_temperature = previous.target_temperature
                entity._control_output = previous._control_output
                entity.hvac_mode = previous.hvac_mode
                entity._pwm_coordination = live.registry
            for entity in live.entities if bind_all else live.entities[:1]:
                live.registry.bind(entity)

        def advance(self, seconds):
            wall.seconds += seconds
            live.clock.now += seconds

        def ledger(self, index=0):
            return live.registry.groups[0]._zones[live.entities[index].entity_id]

        async def complete_first(self):
            await live.entities[0].apply()
            self.advance(1500)
            await live.entities[0].apply()
            assert live.state[f"{live.domain}.a"] == "off"

    helper = Restart()
    helper.live, helper.disk, helper.wall = live, disk, wall
    await helper.reboot()
    return helper


@pytest.mark.asyncio
async def test_repeated_restarts_do_not_repeat_completed_entitlement(journal):
    live = journal.live
    await journal.complete_first()
    for _ in range(5):
        await journal.reboot(offline=15)
        await live.entities[0].apply()
        assert live.state[f"{live.domain}.a"] == "off"
        assert journal.ledger().requested == 1500
        assert journal.ledger().delivered == 1500
        assert journal.ledger().credit == 0
    assert sum(name == "turn_on" and output == f"{live.domain}.a" for _, name, output in live.commands) == 1


@pytest.mark.asyncio
async def test_running_pulse_resumes_only_remainder_without_fabricating_offline_delivery(journal):
    live = journal.live
    await live.entities[0].apply()
    journal.advance(300)
    await live.registry.async_save()
    await journal.reboot(offline=60)
    await live.entities[0].apply()
    zone = journal.ledger()
    assert (zone.requested, zone.delivered, zone.credit, zone.uncertain_seconds) == (1500, 300, 1140, 60)
    journal.advance(1140)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().requested == pytest.approx(
        journal.ledger().delivered + journal.ledger().credit + journal.ledger().cancelled_balance
    )


@pytest.mark.asyncio
async def test_recent_off_keeps_minimum_off_across_restart_and_priority_request(journal):
    live = journal.live
    await journal.complete_first()
    journal.advance(60)
    await journal.reboot(offline=12)
    live.entities[0]._set_target_temp(23)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().priority_blocked_reason == "minimum_off"
    journal.advance(527)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    journal.advance(1)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "on"


@pytest.mark.asyncio
async def test_priority_blocked_before_restart_starts_at_first_eligible_time_after_restart(journal):
    live = journal.live
    await journal.complete_first()
    journal.advance(1500)
    await live.entities[1].apply()
    assert live.state[f"{live.domain}.b"] == "off"
    live.entities[1]._set_target_temp(23)
    await live.entities[1].apply()
    assert journal.ledger(1).priority
    assert journal.ledger(1).priority_blocked_reason == "minimum_off"
    await journal.reboot(offline=100)
    journal.advance(499)
    await live.entities[1].apply()
    assert live.state[f"{live.domain}.b"] == "off"
    journal.advance(1)
    await live.entities[1].apply()
    assert live.state[f"{live.domain}.b"] == "on"
    assert journal.ledger(1).priority_latency_ms == 600000


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["target", "off_heat"])
async def test_real_priority_change_after_restart_is_not_deferred_to_next_period(journal, transition):
    live = journal.live
    await journal.complete_first()
    if transition == "off_heat":
        live.entities[0].hvac_mode = "off"
        await live.entities[0].apply()
    journal.advance(600)
    await journal.reboot(offline=5)
    if transition == "target":
        live.entities[0]._set_target_temp(23)
    else:
        live.entities[0].hvac_mode = "heat"
        live.registry.invalidate(live.entities[0].entity_id, urgent=True)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "on"
    assert journal.ledger().priority_latency_ms == 0


@pytest.mark.asyncio
async def test_partial_readiness_retains_other_members_history_and_never_resets_existing_zone(journal):
    live = journal.live
    await journal.complete_first()
    await journal.reboot(offline=15, bind_all=False)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert set(journal.disk.data["pwm_coordination"]["pump"]["zones"]) == {"climate.a", "climate.b"}
    live.registry.bind(live.entities[1])
    assert journal.ledger().delivered == 1500
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_long_offline_time_does_not_replay_missed_periods(journal):
    live = journal.live
    await journal.complete_first()
    await journal.reboot(offline=24 * 3600)
    await live.entities[0].apply()
    assert journal.ledger().requested == 3000  # one current entitlement, not 24 missed ones
    assert journal.ledger().credit <= 1500


@pytest.mark.asyncio
async def test_crash_between_on_intent_and_confirmation_reconciles_off_without_replay(journal):
    live = journal.live
    await live.entities[0].apply()
    saved = journal.disk.data["pwm_coordination"]["pump"]["zones"]["climate.a"]
    saved["pending_on"] = True
    saved["controller"]["on"] = False
    await journal.reboot(offline=10)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().credit == 0
    assert journal.ledger().uncertain_seconds == 1500
    await journal.reboot(offline=5)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_old_uncertain_on_consumes_recovery_period_before_next_ordinary_start(journal):
    live = journal.live
    await live.entities[0].apply()
    saved = journal.disk.data["pwm_coordination"]["pump"]["zones"]["climate.a"]
    saved["pending_on"] = True
    saved["controller"]["on"] = False
    await journal.reboot(offline=86400)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().requested == 3000
    assert journal.ledger().credit == 0
    assert journal.ledger().last_start_epoch == 24  # uncertain ON may occupy the recovery period
    journal.advance(600)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    journal.advance(3000)  # next stable logical period, not a rolling cooldown
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "on"


@pytest.mark.asyncio
async def test_storage_failure_blocks_unjournaled_on_and_legacy_bypass(journal):
    live = journal.live
    journal.disk.fail = True
    await live.entities[0].apply()
    assert live.registry.groups[0].reason == "restart_storage_error"
    assert not live.commands
    await live.entities[0].apply()
    assert not live.commands


@pytest.mark.asyncio
async def test_storage_failure_stops_existing_on_through_controller(journal):
    live = journal.live
    await live.entities[0].apply()
    journal.disk.fail = True
    await live.entities[0].apply()
    assert live.registry.groups[0].reason == "restart_storage_error"
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_invalidation_while_checkpoint_is_awaited_prevents_obsolete_on(journal):
    live = journal.live
    journal.disk.block = True
    task = asyncio.create_task(live.entities[0].apply())
    await asyncio.wait_for(journal.disk.entered.wait(), 1)
    live.entities[0]._set_target_temp(18)
    live.entities[0]._control_output = 0
    journal.disk.release.set()
    await asyncio.wait_for(task, 1)
    assert not live.commands
    journal.disk.block = False
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().credit == 0


@pytest.mark.asyncio
async def test_failed_post_on_checkpoint_stops_output_and_does_not_replay_on_restart(journal):
    live = journal.live
    journal.disk.fail_on = 2
    await live.entities[0].apply()
    assert live.registry.groups[0].reason == "restart_storage_error"
    assert live.state[f"{live.domain}.a"] == "off"
    journal.disk.fail_on = None
    await journal.reboot(offline=5)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().requested == 1500
    assert journal.ledger().credit == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), "1500", True])
async def test_invalid_journal_does_not_reset_budget_or_fall_back_to_new_on(journal, bad):
    live = journal.live
    await journal.complete_first()
    journal.disk.data["pwm_coordination"]["pump"]["zones"]["climate.a"]["credit"] = bad
    await journal.reboot(offline=10)
    await live.entities[0].apply()
    assert live.registry.groups[0].reason == "restart_state_invalid"
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_clock_rollback_is_explicitly_blocked(journal):
    live = journal.live
    await journal.complete_first()
    await journal.reboot(offline=-600)
    await live.entities[0].apply()
    assert live.registry.groups[0].reason == "restart_state_invalid"
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_full_demand_after_restart_uses_direct_response_with_minimum_guard(journal):
    live = journal.live
    await journal.complete_first()
    live.entities[0]._control_output = 100
    journal.advance(600)
    await journal.reboot(offline=10)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "on"


@pytest.mark.asyncio
async def test_journal_updates_preserve_learning_payload_and_do_not_alias_callers(journal):
    store = journal.store
    await store.async_save_zone("a", adaptive_data={"cycle_count": 7})
    state = {"version": 1, "zones": {"a": {"credit": 5}}}
    await store.async_save_pwm_coordination("other", state)
    state["zones"]["a"]["credit"] = 999
    result = store.load_pwm_coordination()
    result["other"]["zones"]["a"]["credit"] = 123
    assert store.load_pwm_coordination()["other"]["zones"]["a"]["credit"] == 5
    assert store.get_zone_data("a")["adaptive_learner"]["cycle_count"] == 7


@pytest.mark.asyncio
async def test_low_duty_credit_is_neither_reearned_offline_nor_replayed_after_restart(journal):
    live = journal.live
    heater = live.entities[0]._heater_controller
    live.entities[0]._control_output = 5
    heater.set_duty_accumulator(600)
    await live.entities[0].apply()
    assert heater.duty_accumulator_seconds == 0
    journal.advance(300)
    await live.registry.async_save()
    await journal.reboot(offline=30)
    await live.entities[0].apply()
    assert live.entities[0]._heater_controller.duty_accumulator_seconds == 0
    assert journal.ledger().requested == 720
    journal.advance(390)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    await journal.reboot(offline=600)
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert live.entities[0]._heater_controller.duty_accumulator_seconds == 0


@pytest.mark.asyncio
async def test_not_yet_initialized_zone_keeps_its_saved_budget_until_temperature_ready(journal):
    live = journal.live
    await journal.complete_first()
    await journal.reboot(offline=15)
    live.entities[0]._active = False
    live.entities[0]._current_temp = None
    await live.entities[1].apply()
    assert journal.ledger().requested == 1500
    assert journal.ledger().delivered == 1500
    live.entities[0]._active = True
    live.entities[0]._current_temp = 19
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"


@pytest.mark.asyncio
async def test_pause_after_restart_cancels_remaining_budget_without_inventing_demand(journal):
    live = journal.live
    await live.entities[0].apply()
    journal.advance(900)
    await live.registry.async_save()
    await journal.reboot(offline=30)
    live.entities[0]._status_manager.is_paused = lambda _: True
    await live.entities[0].apply()
    assert live.state[f"{live.domain}.a"] == "off"
    assert journal.ledger().credit == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_every", [0, 600])
async def test_24h_persistence_replay_with_and_without_repeated_crashes(journal, restart_every):
    live = journal.live
    observed = [0, 0]
    starts, union, previous, reboots = 0, 0, False, 0
    for second in range(0, 86400, 30):
        if second:
            journal.advance(30)
        if second and restart_every and second % restart_every == 0:
            await journal.reboot()  # unclean: no final flush; last checkpoint is 30s old
            reboots += 1
        for entity in live.entities:
            await entity.apply()
        active = [live.state[f"{live.domain}.{zone}"] == "on" for zone in ("a", "b")]
        starts += int(any(active) and not previous)
        union += 30 * int(any(active))
        previous = any(active)
        for index, on in enumerate(active):
            observed[index] += 30 * int(on)
    assert observed == [36000, 36000]
    assert starts == 24
    assert union == 66240
    for index in range(2):
        zone = journal.ledger(index)
        assert zone.requested == 36000
        assert zone.credit == pytest.approx(0)
        assert zone.requested == pytest.approx(zone.delivered + zone.cancelled_balance)
    print(
        json.dumps(
            {
                "output_domain": live.domain,
                "simulated_seconds": 86400,
                "reboots": reboots,
                "observed_output_seconds": observed,
                "estimated_starts": starts,
                "union_seconds": union,
                "journal_saves": journal.disk.saves,
                "unknown_seconds": [journal.ledger(i).uncertain_seconds for i in range(2)],
            },
            sort_keys=True,
        )
    )


@pytest.mark.asyncio
async def test_later_zone_setup_cannot_replace_live_journal_ownership(journal, live):
    await journal.complete_first()
    registry = live.registry
    group = registry.groups[0]
    original_store = registry._store
    original_checkpoint = group.checkpoint
    original_restore = group.on_register

    class UnexpectedStore:
        def load_pwm_coordination(self):
            raise AssertionError("later zone setup must not reload the journal")

    await registry.async_initialize(UnexpectedStore())
    assert registry._store is original_store
    assert group.checkpoint is original_checkpoint
    assert group.on_register is original_restore
    await group.save_checkpoint()
