"""Authoritative PID inputs and precise outcomes shared by learning services."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..helpers.hvac_mode import get_hvac_heat_mode

if TYPE_CHECKING:
    from ..adaptive.learning import AdaptiveLearner


def current_pid_gains(learner: AdaptiveLearner) -> dict[str, float] | None:
    """Read heating gains from their manager; never invent HA attribute defaults."""
    manager = learner._pid_gains_manager
    if manager is None:
        return None
    gains = manager.get_gains(get_hvac_heat_mode())
    return {"kp": gains.kp, "ki": gains.ki, "kd": gains.kd}


def pid_analysis_details(learner: AdaptiveLearner) -> dict[str, Any]:
    """Copy this analysis' actual outcome rather than calling every None missing data."""
    details = dict(learner._last_pid_analysis)
    return details or {"status": "no_adjustment_needed", "reason": "reason_unavailable"}
