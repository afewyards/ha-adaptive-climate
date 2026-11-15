"""Learned per-zone numbers published in the ``learning`` state attribute."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..adaptive.physics import calculate_expected_heating_rate
from ..const import get_convergence_thresholds

if TYPE_CHECKING:
    from ..adaptive.learning import AdaptiveLearner

OVERSHOOT_WINDOW = 20


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
    """Build learned heat-up, overshoot and settling values; unknown values are omitted."""
    history = learner.cycle_history
    profile: dict[str, Any] = {"cycles": learner.get_cycle_count()}
    if heating_type is not None:
        profile["heating_type"] = heating_type
    if area_m2:
        profile["area_m2"] = area_m2

    thresholds = get_convergence_thresholds(heating_type)
    measured: dict[str, float | None] = {
        "heating_rate": learner.get_mean_heating_rate(),
        "overshoot": _mean([c.overshoot for c in history[-OVERSHOOT_WINDOW:] if c.overshoot is not None]),
        "settling_time": _mean([c.settling_time for c in history if c.settling_time is not None]),
    }
    expected = calculate_expected_heating_rate(heating_type, tau, area_m2, max_power_w, supply_temperature)["baseline"]
    values: dict[str, float | None] = {
        **measured,
        "heating_rate_expected": expected if expected > 0 else None,
        "overshoot_limit": thresholds["overshoot_max"],
        "settling_time_limit": thresholds["settling_time_max"],
    }
    for key, value in values.items():
        if value is not None:
            profile[key] = round(value, 1 if key.startswith("settling_time") else 2)
    return profile
