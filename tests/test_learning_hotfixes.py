"""Regressions for learning sessions, PID diagnostics, and adjustment cooldowns."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from custom_components.adaptive_climate.adaptive import learning as learning_module
from custom_components.adaptive_climate.adaptive import learning_adjustments, learning_coordinator
from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner, CycleMetrics
from custom_components.adaptive_climate.adaptive.learner_serialization import restore_learner_from_dict
from custom_components.adaptive_climate.const import PIDGains, PIDChangeReason, RULE_PRIORITY_OSCILLATION
from custom_components.adaptive_climate.managers import heater_timers
from custom_components.adaptive_climate.managers.events import CycleEventType
from custom_components.adaptive_climate.managers.pid_tuning import PIDTuningManager
from custom_components.adaptive_climate.managers.state_restorer import StateRestorer
from custom_components.adaptive_climate.services import async_handle_run_learning, async_handle_pid_recommendations
from custom_components.adaptive_climate.services.scheduled import async_daily_learning

from custom_components.adaptive_climate.managers import heater_controller as heater_module
from custom_components.adaptive_climate.managers import pwm_controller as pwm_module


@pytest.fixture
def actuator(monkeypatch):
    """Use the real controllers with an explicitly observed binary output."""
    mode = SimpleNamespace(HEAT="heat", COOL="cool", OFF="off")
    monkeypatch.setattr(heater_module, "HVACMode", mode)
    monkeypatch.setattr(pwm_module, "HVACMode", mode)
    clock = SimpleNamespace(now=10000.0)
    monotonic = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(heater_module, "time", monotonic)
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


@pytest.fixture
def clock(monkeypatch):
    now = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    for module in (learning_module, learning_adjustments, learning_coordinator):
        monkeypatch.setattr(module, "dt_util", SimpleNamespace(utcnow=lambda: now))
    return now


def learner_with_cycles(count=7):
    learner = AdaptiveLearner()
    for _ in range(count):
        learner.add_cycle_metrics(CycleMetrics(overshoot=0.6, oscillations=0, settling_time=30, rise_time=20))
    return learner


@pytest.mark.parametrize("count", [3, 7])
@pytest.mark.parametrize("handler", [async_handle_run_learning, async_handle_pid_recommendations])
@pytest.mark.asyncio
async def test_services_report_authoritative_gains_and_actual_gate(clock, count, handler):
    learner = learner_with_cycles(count)
    learner._last_adjustment_time = clock - timedelta(hours=25)
    learner._cycles_since_last_adjustment = 0
    gains = PIDGains(kp=80.0, ki=1.5, kd=20.0)
    manager = Mock()
    manager.get_gains.return_value = gains
    learner.set_pid_gains_manager(manager)
    # Stale/default HA attributes must not become the reported current gains.
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: SimpleNamespace(attributes={"kp": 100, "ki": 0.01, "kd": 0}))
    )
    coordinator = SimpleNamespace(
        get_all_zones=lambda: {
            "zone": {"adaptive_learner": learner, "climate_entity_id": "climate.zone", "pwm_seconds": 3600}
        }
    )
    result = await handler(hass, coordinator, SimpleNamespace(data={}))
    entry = result["zone_results" if handler is async_handle_run_learning else "zones"]["zone"]
    assert entry["current_pid"] == {"kp": 80.0, "ki": 1.5, "kd": 20.0}
    assert entry["status"] == ("rate_limited" if count == 7 else "insufficient_data")
    assert entry["reason"] == ("cycle_gate" if count == 7 else "insufficient_cycles")
    if count == 7:
        assert entry["cycles_until_next_adjustment"] == 3
        assert entry["cycles_since_last_adjustment"] == 0
    assert learner.get_last_adjustment_time() == clock - timedelta(hours=25)


@pytest.mark.parametrize("handler", [async_handle_run_learning, async_handle_pid_recommendations])
@pytest.mark.asyncio
async def test_missing_gain_manager_never_invents_defaults(handler):
    learner = learner_with_cycles()
    learner.set_pid_gains_manager(None)
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: SimpleNamespace(attributes={"kp": 100})))
    coordinator = SimpleNamespace(
        get_all_zones=lambda: {"zone": {"adaptive_learner": learner, "climate_entity_id": "climate.zone"}}
    )
    result = await handler(hass, coordinator, SimpleNamespace(data={}))
    entry = result["zone_results" if handler is async_handle_run_learning else "zones"]["zone"]
    assert entry["reason"] == "pid_gains_unavailable"
    assert entry.get("current_pid") is None


def test_recommendations_do_not_spend_cycles_or_start_cooldown(clock):
    learner = learner_with_cycles()
    assert learner.calculate_pid_adjustment(100, 1, 10) is not None
    assert learner._last_pid_analysis["status"] == "recommendation_available"
    learner.calculate_pid_adjustment(100, 1, 10)
    assert learner.get_last_adjustment_time() is None
    assert learner._cycles_since_last_adjustment == 7
    assert learner.get_cycle_count() == 7
    learner.record_pid_adjustment()
    assert learner.get_last_adjustment_time() == clock
    assert learner._cycles_since_last_adjustment == 0


@pytest.mark.asyncio
async def test_daily_analysis_uses_same_real_gains_and_read_only_cooldown(clock):
    learner = learner_with_cycles()
    manager = Mock()
    manager.get_gains.return_value = PIDGains(kp=80.0, ki=1.5, kd=20.0)
    learner.set_pid_gains_manager(manager)
    learner.calculate_pid_adjustment = Mock(wraps=learner.calculate_pid_adjustment)
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: SimpleNamespace(attributes={})))
    coordinator = SimpleNamespace(
        get_all_zones=lambda: {"zone": {"adaptive_learner": learner, "climate_entity_id": "climate.zone"}}
    )
    await async_daily_learning(hass, coordinator, 7, clock)
    assert learner.calculate_pid_adjustment.call_args.kwargs["current_kp"] == 80.0
    assert learner.calculate_pid_adjustment.call_args.kwargs["current_ki"] == 1.5
    assert learner.calculate_pid_adjustment.call_args.kwargs["current_kd"] == 20.0
    assert learner._cycles_since_last_adjustment == 7
    assert learner.get_last_adjustment_time() is None


@pytest.mark.parametrize(
    "hours,cycles,reason", [(1, 0, "time_and_cycle_gate"), (1, 4, "time_gate"), (25, 0, "cycle_gate")]
)
def test_rate_limit_reasons_remain_distinct(clock, hours, cycles, reason):
    learner = learner_with_cycles()
    learner._last_adjustment_time = clock - timedelta(hours=hours)
    learner._cycles_since_last_adjustment = cycles
    assert learner.calculate_pid_adjustment(100, 1, 10) is None
    assert learner._last_pid_analysis["status"] == "rate_limited"
    assert learner._last_pid_analysis["reason"] == reason


def test_no_adjustment_and_convergence_are_not_missing_data(clock, monkeypatch):
    learner = learner_with_cycles()
    monkeypatch.setattr(learning_coordinator, "check_convergence", lambda *a, **k: True)
    assert learner.calculate_pid_adjustment(100, 1, 10) is None
    assert learner._last_pid_analysis == {"status": "converged", "reason": "pid_converged"}
    monkeypatch.setattr(learning_coordinator, "check_convergence", lambda *a, **k: False)
    monkeypatch.setattr(learning_coordinator, "evaluate_pid_rules", lambda *a, **k: [])
    learner.calculate_pid_adjustment(100, 1, 10)
    assert learner._last_pid_analysis["reason"] == "no_pid_rule_triggered"
    monkeypatch.setattr(
        learning_coordinator,
        "evaluate_pid_rules",
        lambda *a, **k: [SimpleNamespace(rule=SimpleNamespace(priority=RULE_PRIORITY_OSCILLATION))],
    )
    learner.calculate_pid_adjustment(100, 1, 10, pwm_seconds=3600)
    assert learner._last_pid_analysis["reason"] == "pwm_rules_filtered"


def test_disturbed_cycles_report_usable_data_gate(clock):
    learner = learner_with_cycles()
    for cycle in learner._heating_cycle_history:
        cycle.disturbances = ["solar_gain"]
    assert learner.calculate_pid_adjustment(100, 1, 10) is None
    assert learner._last_pid_analysis["reason"] == "insufficient_usable_cycles"
    assert learner._last_pid_analysis["undisturbed_cycles"] == 0


def test_adjustment_counter_survives_two_serialized_restarts(clock):
    learner = learner_with_cycles()
    learner._last_adjustment_time = clock - timedelta(hours=25)
    learner._cycles_since_last_adjustment = 4
    for _ in range(2):
        payload = json.loads(json.dumps(learner.to_dict()))
        assert payload["format_version"] == 12
        restored = AdaptiveLearner()
        restored.restore_from_dict(payload)
        assert restored._cycles_since_last_adjustment == 4
        assert restored.get_cycle_count() == 7
        assert not restored._check_rate_limit(8, 3)
        learner = restored


def test_v11_migration_is_conservative_and_preserves_history(clock):
    learner = learner_with_cycles()
    learner._last_adjustment_time = clock - timedelta(hours=25)
    payload = learner.to_dict()
    payload["format_version"] = 11
    del payload["cycles_since_last_adjustment"]
    restored = AdaptiveLearner()
    restored.restore_from_dict(payload)
    assert restored.get_cycle_count() == 7
    assert restored.get_last_adjustment_time() == learner.get_last_adjustment_time()
    assert restored._cycles_since_last_adjustment == 0
    assert restored._check_rate_limit(8, 3)
    assert restored.to_dict()["format_version"] == 12


@pytest.mark.parametrize("value,expected", [(None, 0), (-3, 0), ("broken", 0), (float("inf"), 0), ("5", 5)])
def test_malformed_stored_counter_does_not_erase_cycles(value, expected):
    payload = learner_with_cycles().to_dict()
    payload["cycles_since_last_adjustment"] = value
    restored = restore_learner_from_dict(payload)
    assert restored["cycles_since_last_adjustment"] == expected
    assert len(restored["heating_cycle_history"]) == 7


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_only_successful_gain_application_commits_after_history_reset(clock, automatic, fails):
    learner = learner_with_cycles()
    learner.calculate_pid_adjustment = Mock(return_value={"kp": 80, "ki": 1, "kd": 10})
    coordinator = SimpleNamespace(get_adaptive_learner=lambda _: learner)
    state = SimpleNamespace(_coordinator=coordinator, entity_id="climate.zone", _kp=100.0, _ki=1.0, _kd=10.0, _pwm=3600)
    gains_manager = Mock()
    if fails:
        gains_manager.set_gains.side_effect = RuntimeError("application failed")
    manager = PIDTuningManager(state, Mock(), gains_manager, AsyncMock(), AsyncMock())
    method = manager.async_auto_apply_adaptive_pid if automatic else manager.async_apply_adaptive_pid
    if fails:
        with pytest.raises(RuntimeError, match="application failed"):
            await method()
        assert learner.get_last_adjustment_time() is None
        assert learner._cycles_since_last_adjustment == 7
        assert learner.get_cycle_count() == 7
    else:
        await method()
        assert learner.get_last_adjustment_time() == clock
        assert learner._cycles_since_last_adjustment == 0
        assert learner.get_cycle_count() == 0
        assert gains_manager.set_gains.call_args.args[0] == (
            PIDChangeReason.AUTO_APPLY if automatic else PIDChangeReason.ADAPTIVE_APPLY
        )


@pytest.mark.asyncio
async def test_force_off_discards_learning_flags_even_when_relay_already_off(actuator):
    controller = actuator.controller
    controller.restore_cycle_state(True, True)
    await controller.async_turn_off(**actuator.callbacks, force=True)
    assert not controller.cycle_active and not controller.has_demand
    actuator.hass.services.async_call.assert_not_awaited()
    controller._has_demand = True
    await controller.async_turn_on(**actuator.callbacks)
    types = [call.args[0].event_type for call in actuator.dispatcher.emit.call_args_list]
    assert types.count(CycleEventType.CYCLE_STARTED) == 1


@pytest.mark.asyncio
async def test_zero_small_zero_demand_keeps_one_original_settling_deadline(actuator, monkeypatch):
    callbacks = []
    cancel = Mock()
    monkeypatch.setattr(
        heater_timers,
        "async_call_later",
        lambda hass, delay, callback: callbacks.append((actuator.clock.now + delay, callback)) or cancel,
    )
    controller = actuator.controller
    controller.restore_cycle_state(True, True)
    initial = actuator.clock.now
    for value in (0.0, 0.1, 0.0, 1.5, 0.0):
        await controller.async_set_control_value(
            control_output=value,
            **actuator.callbacks,
            time_changed=0,
            set_time_changed=Mock(),
            force_on=False,
            force_off=False,
            set_force_on=Mock(),
            set_force_off=Mock(),
        )
        actuator.clock.now += 60
    assert len(callbacks) == 1
    assert callbacks[0][0] == initial + 7200
    cancel.assert_not_called()
    actuator.hass.services.async_call.assert_not_awaited()
    actuator.clock.now = initial + 7200
    callbacks[0][1](None)
    types = [call.args[0].event_type for call in actuator.dispatcher.emit.call_args_list]
    assert types.count(CycleEventType.SETTLING_STARTED) == 1


def test_restart_discards_only_transient_learning_flags_and_keeps_wear_and_gains(actuator):
    heater = actuator.controller
    heater.restore_cycle_state(True, True)
    pid = Mock(integral=12.0)
    gains = Mock()
    thermostat = SimpleNamespace(
        entity_id="climate.zone",
        _pid_controller=pid,
        _gains_manager=gains,
        _heater_controller=heater,
        _kp=80.0,
        _ki=1.5,
        _kd=20.0,
        _ke=0.5,
        _i=0,
    )
    old = SimpleNamespace(
        attributes={
            "integral": 12,
            "_cycle_active": True,
            "_has_demand": True,
            "cycle_count": {"heater": 150, "cooler": 8},
        }
    )
    StateRestorer(thermostat)._restore_pid_values(old)
    assert not heater.cycle_active and not heater.has_demand
    assert heater.heater_cycle_count == 150 and heater.cooler_cycle_count == 8
    gains.restore_from_state.assert_called_once_with(old)
    gains.set_integral.assert_called_once_with(12.0, PIDChangeReason.RESTORE)
