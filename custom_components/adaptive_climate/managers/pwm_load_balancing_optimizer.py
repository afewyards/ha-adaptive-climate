"""Bounded deterministic cyclic phase planning; no HA or hardware imports.

Durations supplied here are already effective electrical ON seconds. Runtime
eligibility, minimum times and urgent demand override these advisory phases.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise


@dataclass(frozen=True)
class ZoneRequest:
    """One contiguous electrical pulse per cyclic planning period."""

    entity_id: str
    on_seconds: float
    weight: float = 1.0


@dataclass(frozen=True)
class Interval:
    """Half-open interval in seconds relative to the common period."""

    entity_id: str
    start: float
    end: float


@dataclass(frozen=True)
class Schedule:
    """A steady-state phase proposal, not permission to energize a relay."""

    period: float
    phases: dict[str, float]
    intervals: tuple[Interval, ...]
    energized_seconds: float
    cyclic_starts: int
    high_weight_overlap_seconds: float
    weighted_overlap_seconds: float


def measure(period: float, requests: list[ZoneRequest], phases: dict[str, float]) -> Schedule:
    """Measure the exact circular interval union and concurrency, without slots."""
    intervals: list[Interval] = []
    for request in requests:
        duration = request.on_seconds
        if duration <= 0:
            continue
        start = phases[request.entity_id] % period
        if duration >= period:
            intervals.append(Interval(request.entity_id, 0.0, period))
        elif start + duration <= period:
            intervals.append(Interval(request.entity_id, start, start + duration))
        else:
            intervals.extend(
                (
                    Interval(request.entity_id, start, period),
                    Interval(request.entity_id, 0.0, start + duration - period),
                )
            )
    points = sorted({0.0, period, *(i.start for i in intervals), *(i.end for i in intervals)})
    weights = {r.entity_id: r.weight for r in requests}
    highest = min(requests, key=lambda r: (-r.weight, r.entity_id)).entity_id if requests else None
    union = high = weighted = 0.0
    occupancy: list[bool] = []
    for start, end in pairwise(points):
        active = {i.entity_id for i in intervals if i.start <= start < i.end}
        duration = end - start
        occupancy.append(bool(active))
        if active:
            union += duration
        if highest in active and len(active) > 1:
            high += duration
        members = sorted(active)
        weighted += duration * sum(weights[a] * weights[b] for n, a in enumerate(members) for b in members[n + 1 :])
    starts = sum(active and not occupancy[n - 1] for n, active in enumerate(occupancy))
    return Schedule(period, phases, tuple(intervals), union, starts, high, weighted)


def plan(requests: list[ZoneRequest], period: float, overlap: float = 0.0, strategy: str = "minimal") -> Schedule:
    """Build an O(n²) chain, placing unavoidable longer overlap on small zones.

    A circular chain is used when genuine demand covers its handovers. Otherwise
    one open contiguous run leaves a legitimate pump-OFF gap. Maximal strategy
    aligns starts, achieving the smallest possible union without timing bounds.
    Real timing bounds are reconciled by the runtime rather than hidden here.
    """
    if not math.isfinite(period) or period <= 0 or not math.isfinite(overlap) or overlap < 0:
        raise ValueError("period must be positive and overlap nonnegative and finite")
    if strategy not in ("minimal", "maximal"):
        raise ValueError("overlap strategy must be minimal or maximal")
    if len(requests) > 16 or len({r.entity_id for r in requests}) != len(requests):
        raise ValueError("expected at most 16 distinct zones")
    for request in requests:
        if (
            not math.isfinite(request.on_seconds)
            or not 0 <= request.on_seconds <= period
            or not math.isfinite(request.weight)
            or request.weight <= 0
        ):
            raise ValueError("expected finite positive weights and durations within period")
    phases = dict.fromkeys((r.entity_id for r in requests), 0.0)
    active = sorted((r for r in requests if r.on_seconds > 0), key=lambda r: (-r.weight, r.entity_id))
    if strategy == "maximal" or len(active) < 2:
        return measure(period, requests, phases)
    count = len(active)
    capacities = [min(active[n].on_seconds, active[(n + 1) % count].on_seconds) for n in range(count)]
    handovers = [min(overlap, capacity) for capacity in capacities]
    excess = sum(r.on_seconds for r in active) - period
    if excess >= sum(handovers):
        # Fill low-weight edges first; high-weight boundary handovers stay short.
        remaining = excess - sum(handovers)
        edges = sorted(
            range(count),
            key=lambda n: (max(active[n].weight, active[(n + 1) % count].weight), n),
        )
        for edge in edges:
            extra = min(remaining, capacities[edge] - handovers[edge])
            handovers[edge] += extra
            remaining -= extra
    else:
        # Open chain: no requirement to bridge the last/first boundary.
        handovers[-1] = 0.0
    cursor = 0.0
    for n, request in enumerate(active):
        phases[request.entity_id] = cursor % period
        cursor += request.on_seconds - handovers[n]
    return measure(period, requests, phases)
