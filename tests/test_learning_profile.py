import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_climate.adaptive.learning_coordinator import cycle_failures
from custom_components.adaptive_climate.adaptive.physics import calculate_expected_heating_rate
from custom_components.adaptive_climate.const import (
    MAX_AUTO_APPLIES_LIFETIME,
    MAX_AUTO_APPLIES_PER_SEASON,
    HeatingType,
    get_convergence_thresholds,
)
from custom_components.adaptive_climate.managers.learning_gate import LearningGateManager
from custom_components.adaptive_climate.managers.learning_profile import (
    build_learning_diagnosis,
    build_learning_profile,
    tune_state,
)
from custom_components.adaptive_climate.managers.pid_tuning import calculate_physics_gains


def c(overshoot=None, settling_time=None, **kwargs):
    return CycleMetrics(overshoot=overshoot, settling_time=settling_time, **kwargs)


def make_learner(cycles=(), rate=None, heating_type="radiator", confidence=0.0):
    learner = AdaptiveLearner(heating_type=heating_type)
    learner.cycle_history = list(cycles)
    learner.get_mean_heating_rate = lambda: rate
    learner._heating_convergence_confidence = confidence
    return learner


def profile(learner, heating_type="radiator"):
    return build_learning_profile(learner, heating_type, None, None, None, None)


def test_full_profile():
    p = build_learning_profile(
        make_learner(cycles=[c(0.1, 20), c(0.3, 40)], rate=0.914), "radiator", 18.0, None, None, None
    )
    th = get_convergence_thresholds("radiator")
    assert p == {
        "cycles": 2,
        "heating_type": "radiator",
        "area_m2": 18.0,
        "heating_rate": 0.91,
        "heating_rate_expected": round(calculate_expected_heating_rate("radiator")["baseline"], 2),
        "overshoot": 0.2,
        "overshoot_limit": th["overshoot_max"],
        "undershoot_limit": th["undershoot_max"],
        "settling_time": 30.0,
        "settling_time_limit": th["settling_time_max"],
        "recent": "gp",
        "recent_fails": {"overshoot": 1},
    }


def test_no_cycles_omits_measured_keys():
    p = profile(make_learner())
    assert p["cycles"] == 0
    assert not {"heating_rate", "overshoot", "undershoot", "settling_time", "area_m2", "recent"} & p.keys()
    assert {"heating_rate_expected", "overshoot_limit", "undershoot_limit", "settling_time_limit"} <= p.keys()


def test_none_metrics_are_skipped():
    p = profile(make_learner(cycles=[c(None, None), c(0.4, None)]))
    assert p["overshoot"] == 0.4 and "settling_time" not in p and "undershoot" not in p


def test_overshoot_and_undershoot_use_last_20_cycles():
    old = [c(1.0, 10, undershoot=1.0)] * 5
    new = [c(0.2, 10, undershoot=0.123)] * 20
    p = profile(make_learner(cycles=old + new))
    assert p["overshoot"] == 0.2 and p["undershoot"] == 0.12 and p["settling_time"] == 10.0
    assert p["undershoot_limit"] == get_convergence_thresholds("radiator")["undershoot_max"]


def test_unknown_heating_type_does_not_raise():
    none = profile(make_learner(heating_type=None), None)
    odd = profile(make_learner(heating_type=None), "nonsense")
    assert "heating_type" not in none and odd["heating_type"] == "nonsense"
    assert "overshoot_limit" in none and "overshoot_limit" in odd


def test_recent_marks_each_cycle_oldest_first_and_counts_causes():
    cycles = [
        c(0.1, 20),  # good
        c(0.1, 20, undershoot=0.4),  # undershoot
        c(0.5, 120, oscillations=3),  # overshoot, settling, oscillation
        c(0.1, 20, rise_time=90),  # rise_time
        c(0.1, 20, undershoot=0.3, rise_time=90),  # undershoot, rise_time
    ]
    p = profile(make_learner(cycles=cycles))
    assert p["recent"] == "gpppp"
    assert p["recent_fails"] == {
        "undershoot": 2,
        "overshoot": 1,
        "settling": 1,
        "oscillation": 1,
        "rise_time": 2,
    }


def test_recent_keeps_last_20_and_omits_fails_when_all_good():
    p = profile(make_learner(cycles=[c(1.0, 10)] * 5 + [c(0.1, 10)] * 20))
    assert p["recent"] == "g" * 20 and "recent_fails" not in p


def test_recent_judges_recovery_with_todays_confidence():
    # Radiator recovery starts at 0.3° while collecting and 0.5° once stable; recovery skips rise_time
    cycles = [c(0.1, 20, rise_time=90, starting_delta=0.4)]
    assert profile(make_learner(cycles=cycles, confidence=0.0))["recent"] == "g"
    stable = profile(make_learner(cycles=cycles, confidence=0.5))
    assert stable["recent"] == "p" and stable["recent_fails"] == {"rise_time": 1}


def test_below_counts_overshoots_and_other_cycles_with_a_room_below_heating():
    cycles = [
        c(1.0, 10, heat_below=0.25),
        c(1.0, 10, heat_below=0.24),
        c(1.0, 10, undershoot=1.0, heat_below=0.0),
        c(0.1, 10, undershoot=1.0, heat_below=0.5),
        c(0.1, 10, heat_below=1.0),
        c(0.1, 10, heat_below=0.0),
        c(1.0, 10),
    ]
    assert profile(make_learner(cycles=cycles))["below"] == {"over": 3, "over_with": 1, "rest": 3, "rest_with": 2}


def test_below_uses_the_last_20_cycles_with_a_value():
    cycles = [c(1.0, 10, heat_below=1.0)] * 5 + [c(0.1, 10, heat_below=0.0)] * 20 + [c(1.0, 10)] * 3
    assert profile(make_learner(cycles=cycles))["below"] == {"over": 0, "over_with": 0, "rest": 20, "rest_with": 0}


def test_below_omitted_when_no_cycle_has_a_value():
    assert "below" not in profile(make_learner(cycles=[c(1.0, 10), c(0.1, 10)]))


def test_cycle_failures_matches_confidence_update():
    learner = make_learner()
    for metrics in (c(0.1, 20), c(0.5, 20), c(0.1, 20, undershoot=0.5), c(0.1, 20, rise_time=90)):
        before = learner.get_convergence_confidence()
        failed = cycle_failures(learner, metrics, before)
        learner.update_convergence_confidence(metrics)
        assert (learner.get_convergence_confidence() > before) == (not failed)


def test_ki_boost_capped_only_at_the_raw_cap():
    learner = make_learner()
    for multiplier in (1.0, 1.728, 2.996):
        learner.undershoot_detector.cumulative_ki_multiplier = multiplier
        assert not {"ki_boost", "ki_boost_capped"} & profile(learner).keys()
    learner.undershoot_detector.cumulative_ki_multiplier = 3.0
    assert profile(learner)["ki_boost_capped"] is True


def tuned_learner(cycles=10):
    learner = make_learner(cycles=[c(0.1, 20)] * cycles)
    learner.set_physics_baseline(1.0, 0.01, 10.0)
    return learner


def tune(learner, **overrides):
    return tune_state(thermostat(**{"_kp": 1.0, "_ki": 0.01, "_kd": 10.0, **overrides}), learner)


def test_tune_off():
    assert tune(tuned_learner(), _auto_apply_pid=False) == {"state": "off", "reason": "disabled"}
    assert tune(make_learner()) == {"state": "off", "reason": "no_room_size"}
    assert tune(make_learner(), _area_m2=20.0) == {"state": "off", "reason": "no_baseline"}


def test_tune_ready():
    assert tune(tuned_learner()) == {"state": "ready"}


def test_tune_last_skip_reason_only_when_ready_or_waiting():
    learner = tuned_learner()
    assert "last" not in tune(learner)
    learner._last_skip_reason = "converged"
    assert tune(learner) == {"state": "ready", "last": "converged"}
    assert tune(learner, _auto_apply_pid=False) == {"state": "off", "reason": "disabled"}
    learner.cycle_history = learner.cycle_history[:2]
    assert tune(learner) == {"state": "waiting", "cycles_left": 5, "last": "converged"}


def test_tune_waiting_for_cycles():
    # Radiator needs 7 cycles, doubled after the first auto-apply
    assert tune(tuned_learner(cycles=2)) == {"state": "waiting", "cycles_left": 5}
    learner = tuned_learner(cycles=2)
    learner._heating_auto_apply_count = 1
    assert tune(learner) == {"state": "waiting", "cycles_left": 12}


def test_tune_waiting_for_cooldown():
    learner = tuned_learner(cycles=20)
    learner.mark_adjustment_applied()
    learner._cycles_since_last_adjustment = 4
    assert tune(learner) == {"state": "waiting", "cycles_left": 8, "hours_left": 72}
    learner._cycles_since_last_adjustment = 12
    learner._last_adjustment_time = dt_util.utcnow() - timedelta(hours=58.5)
    assert tune(learner) == {"state": "waiting", "hours_left": 14}
    learner._last_adjustment_time = dt_util.utcnow() - timedelta(hours=73)
    assert tune(learner) == {"state": "ready"}


def test_tune_checking():
    learner = tuned_learner()
    learner.start_validation_mode(0.1)
    learner.add_validation_cycle(c(0.1, 20))
    learner.add_validation_cycle(c(0.1, 20))
    assert tune(learner) == {"state": "checking", "done": 2, "of": 5}


@pytest.mark.parametrize("reason", ["lifetime_limit", "seasonal_limit", "drift_limit", "seasonal_shift"])
def test_tune_blocked(reason):
    learner = tuned_learner()
    gains = {}
    if reason == "lifetime_limit":
        learner._heating_auto_apply_count = MAX_AUTO_APPLIES_LIFETIME
    elif reason == "seasonal_limit":
        now = dt_util.utcnow().isoformat()
        history = [{"reason": "auto_apply", "timestamp": now}] * MAX_AUTO_APPLIES_PER_SEASON
        learner.set_pid_gains_manager(SimpleNamespace(get_history=lambda mode: history))
    elif reason == "drift_limit":
        gains = {"_kp": 5.0, "_ki": 0.05, "_kd": 50.0}
    else:
        learner._last_seasonal_shift = dt_util.utcnow()
    assert tune(learner, **gains) == {"state": "blocked", "reason": reason}


def test_tune_does_not_warn_on_malformed_history(caplog):
    learner = tuned_learner()
    history = [{"reason": "auto_apply", "timestamp": "not a time"}]
    learner.set_pid_gains_manager(SimpleNamespace(get_history=lambda mode: history))
    with caplog.at_level(logging.DEBUG):
        assert tune(learner) == {"state": "ready"}
    assert [r.levelno for r in caplog.records if "malformed" in r.message] == [logging.DEBUG]


def test_tune_agrees_with_the_auto_apply_decision():
    def overshooting(cycles=10):
        learner = make_learner(cycles=[c(0.6, 20, rise_time=30)] * cycles)
        learner.set_physics_baseline(1.0, 0.01, 10.0)
        return learner

    def cooled_down(hours, cycles_since):
        learner = overshooting()
        learner._last_adjustment_time = dt_util.utcnow() - timedelta(hours=hours)
        learner._cycles_since_last_adjustment = cycles_since
        return learner

    validating = overshooting()
    validating.start_validation_mode(0.1)
    at_limit = overshooting()
    at_limit._heating_auto_apply_count = MAX_AUTO_APPLIES_LIFETIME
    no_baseline = make_learner(cycles=[c(0.6, 20, rise_time=30)] * 10)
    cases = {
        "ready": overshooting(),
        "below min cycles": overshooting(cycles=3),
        "cooldown by cycles": cooled_down(hours=100, cycles_since=4),
        "cooldown by hours": cooled_down(hours=10, cycles_since=20),
        "cooldown over": cooled_down(hours=100, cycles_since=20),
        "validation": validating,
        "limit": at_limit,
        "no baseline": no_baseline,
    }
    for name, learner in cases.items():
        shown = tune(learner, _area_m2=20.0)["state"]
        tuned = learner.calculate_pid_adjustment(1.0, 0.01, 10.0, check_auto_apply=True) is not None
        assert (shown == "ready") == tuned, f"{name}: card says {shown}, auto-apply tunes={tuned}"
    assert tune(cases["cooldown over"])["state"] == "ready"


def test_diagnosis_failure_leaves_keys_out_and_logs_once(caplog):
    broken = thermostat(entity_id="climate.broken", _kp=None)
    with caplog.at_level(logging.ERROR):
        assert build_learning_diagnosis(broken, tuned_learner()) == {}
        assert build_learning_diagnosis(broken, tuned_learner()) == {}
    assert len([r for r in caplog.records if "climate.broken" in r.message]) == 1


def test_no_tune_in_cooling_mode():
    assert "tune" not in build_learning_diagnosis(thermostat(_hvac_mode=HVACMode.COOL), tuned_learner())
    assert "tune" in build_learning_diagnosis(thermostat(_hvac_mode=HVACMode.HEAT), tuned_learner())


def validation_snapshot(learner):
    v = learner._validation
    return (
        v._validation_mode,
        len(v._validation_cycles),
        v._last_seasonal_shift,
        v._last_seasonal_check,
        list(v._outdoor_temp_history),
        learner._heating_auto_apply_count,
        learner._cycles_since_last_adjustment,
        learner._last_adjustment_time,
    )


def test_tune_state_has_no_side_effects():
    ready = tuned_learner()
    shifted = tuned_learner()
    shifted._last_seasonal_shift = dt_util.utcnow() - timedelta(days=1)
    checking = tuned_learner()
    checking.start_validation_mode(0.1)
    for learner in (ready, shifted, checking):
        learner._validation._outdoor_temp_history.extend([5.0] * 12)
        before = validation_snapshot(learner)
        tune(learner)
        build_learning_diagnosis(thermostat(), learner)
        assert validation_snapshot(learner) == before


def thermostat(**overrides):
    values = {
        "_kp": 1.23456,
        "_ki": 0.0123456,
        "_kd": 12.3456,
        "entity_id": "climate.test",
        "_hvac_mode": HVACMode.HEAT,
        "_auto_apply_pid": True,
        "_learning_gate": None,
        "_night_setback_config": None,
        "_area_m2": None,
        "_ceiling_height": 2.5,
        "_window_area_m2": 0.0,
        "_window_rating": "hr++",
        "_floor_construction": None,
        "heating_type": HeatingType.RADIATOR,
        "_max_power_w": None,
        "_supply_temperature": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_diagnosis_gains_and_tune():
    d = build_learning_diagnosis(thermostat(), tuned_learner())
    assert d == {"gains": {"kp": 1.23, "ki": 0.0123, "kd": 12.35}, "tune": {"state": "ready"}}


def test_gains_physics_is_what_a_reset_would_set():
    t = thermostat(_area_m2=20.0)
    _, kp, ki, kd = calculate_physics_gains(t)
    d = build_learning_diagnosis(t, tuned_learner())
    assert d["gains_physics"] == {"kp": round(kp, 2), "ki": round(ki, 4), "kd": round(kd, 2)}
    assert "gains_physics" not in build_learning_diagnosis(thermostat(), tuned_learner())


def gate(learner):
    return LearningGateManager(object(), None, None, lambda: learner, HeatingType.RADIATOR)


def test_setback_limit_below_configured_depth():
    learner = tuned_learner()
    g = gate(learner)
    d = build_learning_diagnosis(thermostat(_learning_gate=g, _night_setback_config={"delta": 2.0}), learner)
    assert d["setback_limit"] == 0.5 and d["setback_until"] == round(g._scaled_tier_1 * 100)
    learner._heating_convergence_confidence = g._scaled_tier_1
    d = build_learning_diagnosis(thermostat(_learning_gate=g, _night_setback_config={"delta": 2.0}), learner)
    assert d["setback_limit"] == 1.0 and d["setback_until"] == round(g._scaled_tier_2 * 100)


def test_setback_limit_zero_has_no_until():
    learner = make_learner()
    d = build_learning_diagnosis(
        thermostat(_learning_gate=gate(learner), _night_setback_config={"delta": 2.0}), learner
    )
    assert d["setback_limit"] == 0.0 and "setback_until" not in d


def test_setback_limit_absent_during_contact_or_humidity_pause():
    learner = tuned_learner()
    opened = SimpleNamespace(is_any_contact_open=lambda: True)
    humid = SimpleNamespace(should_pause=lambda: True)
    for contact, humidity in ((opened, None), (None, humid)):
        g = LearningGateManager(object(), contact, humidity, lambda: learner, HeatingType.RADIATOR)
        d = build_learning_diagnosis(thermostat(_learning_gate=g, _night_setback_config={"delta": 2.0}), learner)
        assert not {"setback_limit", "setback_until"} & d.keys()


def test_setback_limit_absent_when_not_limiting():
    learner = tuned_learner()
    shallow = thermostat(_learning_gate=gate(learner), _night_setback_config={"delta": 0.5})
    assert not {"setback_limit", "setback_until"} & build_learning_diagnosis(shallow, learner).keys()
    learner._heating_convergence_confidence = 0.95
    deep = thermostat(_learning_gate=gate(learner), _night_setback_config={"delta": 2.0})
    assert "setback_limit" not in build_learning_diagnosis(deep, learner)
    assert "setback_limit" not in build_learning_diagnosis(thermostat(), learner)
