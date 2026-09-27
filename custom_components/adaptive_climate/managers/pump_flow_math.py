"""Pure pump flow helpers: target, rounding, entity limits and the write decision."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from typing import Any

from ..const import PUMP_FLOW_DEFAULT_STEP
from .water_temp_writer import entity_limits

FLOW_EPSILON = 1e-6


def target_flow(counted_flows: Iterable[float], min_flow: float | None) -> float:
    """Return 0 when nothing counts, else the summed flow floored at ``min_flow``."""
    total = sum(counted_flows)
    if total <= 0:
        return 0.0
    if min_flow is None:
        return total
    return max(total, min_flow)


def round_up(value: float, step: float) -> float:
    """Round up to the entity step so the pump never gets less than requested."""
    if step <= 0:
        return round(value, 3)
    return round(math.ceil(value / step - 1e-9) * step, 3)


def flow_limits(state: Any) -> tuple[float, float, float]:
    """Return the target entity's (min, max, step); missing attributes fall back to (0, inf, default step)."""
    return entity_limits(state, min_default=0.0, max_default=math.inf, step_default=PUMP_FLOW_DEFAULT_STEP)


def parse_current(state: Any) -> float | None:
    """Return the entity's numeric value, or None when unknown, unavailable or non-numeric."""
    try:
        return float(state.state)
    except (AttributeError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class WriteDecision:
    """Outcome of comparing the new target with the entity's current value."""

    write: bool
    decrease_pending_since: float | None
    recheck_in: float | None


def decide_write(
    final: float,
    current: float | None,
    *,
    idle: bool,
    pending_since: float | None,
    now: float,
    min_write_interval: float,
) -> WriteDecision:
    """Increases and the drop to idle write now; other decreases must persist ``min_write_interval``."""
    if current is None:
        return WriteDecision(True, None, None)
    if abs(final - current) < FLOW_EPSILON:
        return WriteDecision(False, None, None)
    if idle or final > current:
        return WriteDecision(True, None, None)
    since = now if pending_since is None else pending_since
    waited = now - since
    if waited >= min_write_interval:
        return WriteDecision(True, None, None)
    return WriteDecision(False, since, min_write_interval - waited)
