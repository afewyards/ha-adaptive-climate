from types import SimpleNamespace

from custom_components.adaptive_climate.adaptive.physics import calculate_expected_heating_rate
from custom_components.adaptive_climate.const import get_convergence_thresholds
from custom_components.adaptive_climate.managers.learning_profile import build_learning_profile


def c(overshoot, settling_time):
    return SimpleNamespace(overshoot=overshoot, settling_time=settling_time)


def stub(cycles, rate):
    return SimpleNamespace(
        cycle_history=cycles, get_cycle_count=lambda: len(cycles), get_mean_heating_rate=lambda: rate
    )


def test_full_profile():
    p = build_learning_profile(stub(cycles=[c(0.1, 20), c(0.3, 40)], rate=0.914), "radiator", 18.0, None, None, None)
    th = get_convergence_thresholds("radiator")
    assert p == {
        "cycles": 2,
        "heating_type": "radiator",
        "area_m2": 18.0,
        "heating_rate": 0.91,
        "heating_rate_expected": round(calculate_expected_heating_rate("radiator")["baseline"], 2),
        "overshoot": 0.2,
        "overshoot_limit": th["overshoot_max"],
        "settling_time": 30.0,
        "settling_time_limit": th["settling_time_max"],
    }


def test_no_cycles_omits_measured_keys():
    p = build_learning_profile(stub(cycles=[], rate=None), "radiator", None, None, None, None)
    assert p["cycles"] == 0
    assert not {"heating_rate", "overshoot", "settling_time", "area_m2"} & p.keys()
    assert {"heating_rate_expected", "overshoot_limit", "settling_time_limit"} <= p.keys()


def test_none_metrics_are_skipped():
    p = build_learning_profile(
        stub(cycles=[c(None, None), c(0.4, None)], rate=None), "radiator", None, None, None, None
    )
    assert p["overshoot"] == 0.4 and "settling_time" not in p


def test_overshoot_uses_last_20_cycles():
    p = build_learning_profile(
        stub(cycles=[c(1.0, 10)] * 5 + [c(0.2, 10)] * 20, rate=None), "radiator", None, None, None, None
    )
    assert p["overshoot"] == 0.2 and p["settling_time"] == 10.0


def test_unknown_heating_type_does_not_raise():
    none = build_learning_profile(stub(cycles=[], rate=None), None, None, None, None, None)
    odd = build_learning_profile(stub(cycles=[], rate=None), "nonsense", None, None, None, None)
    assert "heating_type" not in none and odd["heating_type"] == "nonsense"
    assert "overshoot_limit" in none and "overshoot_limit" in odd
