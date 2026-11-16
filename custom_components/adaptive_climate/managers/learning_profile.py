"""Learned per-zone numbers published in the ``learning`` state attribute."""

from __future__ import annotations

import logging
import math
from collections import Counter
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from ..adaptive.auto_apply import get_auto_apply_thresholds
from ..adaptive.learning_coordinator import cycle_failures
from ..adaptive.physics import calculate_expected_heating_rate
from ..const import (
    MAX_UNDERSHOOT_KI_MULTIPLIER,
    SUBSEQUENT_LEARNING_CYCLE_MULTIPLIER,
    VALIDATION_CYCLE_COUNT,
    get_convergence_thresholds,
)
from ..helpers.hvac_mode import get_hvac_cool_mode
from .pid_tuning import calculate_physics_gains

if TYPE_CHECKING:
    from ..adaptive.learning import AdaptiveLearner
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)

RECENT_CYCLES = 20

_diagnosis_failed: set[str] = set()


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_learning_profile(
    learner: AdaptiveLearner,
    heating_type: str | None,
    area_m2: float | None,
    tau: float | None,
    max_power_w: float | None,
    supply_temperature: float | None,
) -> dict[str, Any]:
    """Build learned heat-up, overshoot, undershoot and settling values; unknown values are omitted."""
    history = learner.cycle_history
    recent = history[-RECENT_CYCLES:]
    profile: dict[str, Any] = {"cycles": learner.get_cycle_count()}
    if heating_type is not None:
        profile["heating_type"] = heating_type
    if area_m2:
        profile["area_m2"] = area_m2

    thresholds = get_convergence_thresholds(heating_type)
    measured: dict[str, float | None] = {
        "heating_rate": learner.get_mean_heating_rate(),
        "overshoot": _mean([c.overshoot for c in recent if c.overshoot is not None]),
        "undershoot": _mean([c.undershoot for c in recent if c.undershoot is not None]),
        "settling_time": _mean([c.settling_time for c in history if c.settling_time is not None]),
    }
    expected = calculate_expected_heating_rate(heating_type, tau, area_m2, max_power_w, supply_temperature)["baseline"]
    values: dict[str, float | None] = {
        **measured,
        "heating_rate_expected": expected if expected > 0 else None,
        "overshoot_limit": thresholds["overshoot_max"],
        "undershoot_limit": thresholds["undershoot_max"],
        "settling_time_limit": thresholds["settling_time_max"],
    }
    for key, value in values.items():
        if value is not None:
            profile[key] = round(value, 1 if key.startswith("settling_time") else 2)

    if recent:
        # Recovery is judged at today's confidence, so an old cycle can read differently from how it was counted
        confidence = learner.get_convergence_confidence()
        failures = [cycle_failures(learner, c, confidence) for c in recent]
        profile["recent"] = "".join("p" if failed else "g" for failed in failures)
        fails = Counter(cause for failed in failures for cause in failed)
        if fails:
            profile["recent_fails"] = dict(fails)

    if learner.undershoot_detector.cumulative_ki_multiplier >= MAX_UNDERSHOOT_KI_MULTIPLIER:
        profile["ki_boost_capped"] = True
    return profile


def _gains(kp: float, ki: float, kd: float) -> dict[str, float]:
    return {"kp": round(kp, 2), "ki": round(ki, 4), "kd": round(kd, 2)}


def tune_state(thermostat: AdaptiveThermostat, learner: AdaptiveLearner) -> dict[str, Any]:
    """Describe what heating auto-tune waits for, mirroring the auto-apply gates without their side effects."""
    if learner.is_in_validation_mode():
        return {"state": "checking", "done": len(learner._validation_cycles), "of": VALIDATION_CYCLE_COUNT}
    if not thermostat._auto_apply_pid:
        return {"state": "off", "reason": "disabled"}
    if learner._physics_baseline_kp is None:
        return {"state": "off", "reason": "no_baseline" if thermostat._area_m2 else "no_room_size"}

    history = learner._pid_gains_manager.get_history(None) if learner._pid_gains_manager is not None else []
    blocked = learner._validation.auto_apply_limit(
        thermostat._kp,
        thermostat._ki,
        thermostat._kd,
        learner._heating_auto_apply_count,
        learner._cooling_auto_apply_count,
        history,
        warn=False,
    )
    if blocked is not None:
        return {"state": "blocked", "reason": blocked[0]}

    thresholds = get_auto_apply_thresholds(learner._heating_type)
    min_cycles = thresholds["min_cycles"]
    if learner._heating_auto_apply_count > 0:
        min_cycles = int(min_cycles * SUBSEQUENT_LEARNING_CYCLE_MULTIPLIER)
    cycles_left = int(min_cycles) - len(learner.cycle_history)
    hours_left = 0
    last = learner.get_last_adjustment_time()
    if last is not None:
        cycles_left = max(cycles_left, int(thresholds["cooldown_cycles"]) - learner._cycles_since_last_adjustment)
        hours_left = math.ceil(thresholds["cooldown_hours"] - (dt_util.utcnow() - last).total_seconds() / 3600)
    left = {key: value for key, value in (("cycles_left", cycles_left), ("hours_left", hours_left)) if value > 0}
    tune: dict[str, Any] = {"state": "waiting", **left} if left else {"state": "ready"}
    if learner._last_skip_reason is not None:
        tune["last"] = learner._last_skip_reason
    return tune


def _setback_hold(thermostat: AdaptiveThermostat) -> dict[str, Any]:
    gate = thermostat._learning_gate
    config = thermostat._night_setback_config
    if gate is None or not config or gate._is_environmentally_disrupted():
        return {}
    allowed = gate.get_allowed_delta()
    if allowed is None or allowed >= config["delta"]:
        return {}
    hold: dict[str, Any] = {"setback_limit": allowed}
    until = gate.relax_confidence(allowed)
    if until is not None:
        hold["setback_until"] = round(until * 100)
    return hold


def build_learning_diagnosis(thermostat: AdaptiveThermostat, learner: AdaptiveLearner) -> dict[str, Any]:
    """Build gains, tune state and night setback hold for the learning card; unknown values are omitted.

    A failure leaves these keys out and is logged once per zone, so it never blocks a state write.
    """
    try:
        return _diagnosis(thermostat, learner)
    except Exception:
        if thermostat.entity_id not in _diagnosis_failed:
            _diagnosis_failed.add(thermostat.entity_id)
            _LOGGER.exception("%s: could not build the learning diagnosis", thermostat.entity_id)
        return {}


def _diagnosis(thermostat: AdaptiveThermostat, learner: AdaptiveLearner) -> dict[str, Any]:
    diagnosis: dict[str, Any] = {"gains": _gains(thermostat._kp, thermostat._ki, thermostat._kd)}
    if thermostat._hvac_mode != get_hvac_cool_mode():
        diagnosis["tune"] = tune_state(thermostat, learner)
    physics = calculate_physics_gains(thermostat)
    if physics is not None:
        diagnosis["gains_physics"] = _gains(*physics[1:])
    diagnosis.update(_setback_hold(thermostat))
    return diagnosis
