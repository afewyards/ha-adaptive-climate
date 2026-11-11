"""Bounded repair of executable PWM intervals, without creating heat budgets.

The cyclic planner remains a steady-state preference. This pass sees remaining
credit, committed ON time, switching bounds and advisory future opportunities.
It moves only unstarted ordinary pulses; forecasts never authorize commands.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise
import math


@dataclass(frozen=True)
class RuntimeWindow:
    entity_id: str
    weight: float
    start: float
    duration: float
    earliest: float
    latest: float
    movable: bool = False
    forecast: bool = False

    @property
    def end(self) -> float:
        return self.start + self.duration


def measure_windows(windows: list[RuntimeWindow], highest: str, overlap: float) -> tuple[int, float, float, float]:
    """Measure union starts, handover shortfall, high and weighted occupancy."""
    points = sorted({at for window in windows for at in (window.start, window.end)})
    starts, shortfall, high, weighted = 0, 0.0, 0.0, 0.0
    was_on = False
    for start, end in pairwise(points):
        active = [w for w in windows if w.start <= start < w.end]
        starts += int(bool(active) and not was_on)
        was_on = bool(active)
        if len(active) > 1:
            if any(w.entity_id == highest for w in active):
                high += end - start
            weights = [w.weight for w in active]
            weighted += (end - start) * (sum(weights) ** 2 - sum(w * w for w in weights)) / 2
    # Only a transition connecting an existing component needs a handover.
    # Remaining committed time can legitimately be shorter than the target.
    previous: RuntimeWindow | None = None
    for window in sorted(windows, key=lambda w: (w.start, w.entity_id)):
        if previous is not None and window.start <= previous.end + 0.001:
            target = min(overlap, previous.duration, window.duration)
            shortfall += max(0.0, target - max(0.0, min(previous.end, window.end) - window.start))
        if previous is None or window.end > previous.end:
            previous = window
    return starts, shortfall, high, weighted


def repair_windows(
    windows: list[RuntimeWindow], highest: str, overlap: float, previous: dict[str, float]
) -> list[RuntimeWindow]:
    """Two deterministic coordinate passes; at most 16 windows, O(n^4).

    Candidate endpoints align a real pulse with another real/forecast interval.
    Union starts outrank handover targets and concurrency, then movement breaks
    ties. Frozen starts (active, priority, continuous, forecast) never move.
    This bounded local repair is not a claim of a global combinatorial optimum.
    """
    if len(windows) > 16:
        raise ValueError("expected at most 16 executable windows")
    if not math.isfinite(overlap) or overlap < 0:
        raise ValueError("invalid handover target")
    if len({w.entity_id for w in windows}) != len(windows) or any(
        not all(math.isfinite(v) for v in (w.start, w.duration, w.earliest, w.latest, w.weight))
        or w.duration <= 0
        or w.weight <= 0
        or not w.earliest <= w.start <= w.latest
        for w in windows
    ):
        raise ValueError("invalid executable PWM window")
    preferred = {w.entity_id: previous.get(w.entity_id, w.start) for w in windows if w.movable}

    def score(candidate: list[RuntimeWindow]) -> tuple[float, ...]:
        metrics = measure_windows(candidate, highest, overlap)
        movement = sum(abs(w.start - preferred[w.entity_id]) for w in candidate if w.movable)
        return (*metrics, movement, *(w.start for w in sorted(candidate, key=lambda w: w.entity_id)))

    best = list(windows)
    best_score = score(best)
    # Prefer moving small zones before disturbing the high-weight placement.
    order = sorted(range(len(best)), key=lambda i: (best[i].weight, best[i].entity_id))
    for _ in range(2):
        changed = False
        for index in order:
            window = best[index]
            if not window.movable:
                continue
            candidates = {window.start, window.earliest, preferred[window.entity_id]}
            for other in best:
                if other.entity_id == window.entity_id:
                    continue
                handover = min(overlap, window.duration, other.duration)
                candidates.add(other.end - handover)
                candidates.add(other.start + handover - window.duration)
            for at in sorted({max(window.earliest, min(t, window.latest)) for t in candidates}):
                trial = list(best)
                trial[index] = replace(window, start=at)
                trial_score = score(trial)
                if trial_score < best_score:
                    best, best_score = trial, trial_score
                    changed = True
        if not changed:
            break
    return best
