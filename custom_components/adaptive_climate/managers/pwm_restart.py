"""Versioned PWM journals; wall time crosses restarts, monotonic time runs control."""

from __future__ import annotations

import math
from typing import Any, TYPE_CHECKING

from .pwm_pulse import Pulse

if TYPE_CHECKING:
    from .pwm_coordination import HAZoneAdapter
    from .pwm_load_balancing_manager import PWMLoadBalancingManager


def number(value: Any) -> float:
    """Reject malformed state rather than silently minting a fresh heat budget."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid PWM journal number")
    if abs(value) > 1e12:
        raise ValueError("PWM journal number out of bounds")
    return float(value)


def controller_state(adapter: HAZoneAdapter, now: float, wall: float) -> dict[str, Any]:
    entity = adapter.entity
    heater = entity._heater_controller
    at = entity._get_cycle_start_time()
    pipeline = heater._heat_pipeline
    return {
        "outputs": list(adapter.outputs),
        "on": adapter.active(),
        "reconcile_off": adapter.restart_force_off,
        "switch_at": wall + at - now if entity._last_heat_cycle_time is not None else wall,
        "accumulator": heater.duty_accumulator_seconds,
        "accumulator_at": (
            wall + heater._last_accumulator_calc_time - now if heater._last_accumulator_calc_time is not None else None
        ),
        "opened_at": wall + pipeline._valve_opened_at - now
        if pipeline is not None and pipeline._valve_opened_at is not None
        else None,
        "closed_at": wall + pipeline._valve_closed_at - now
        if pipeline is not None and pipeline._valve_closed_at is not None
        else None,
        "q_at_close": pipeline._q_at_close if pipeline is not None else 0.0,
    }


def restore_controller(adapter: HAZoneAdapter, row: dict[str, Any], now: float, wall: float) -> None:
    state = row["controller"]
    if state["outputs"] != list(adapter.outputs) or not isinstance(state["on"], bool):
        raise ValueError("PWM output configuration changed")

    def stamp(value: Any) -> float | None:
        if value is None:
            return None
        at = number(value)
        if at > wall + 5:
            raise ValueError("PWM journal timestamp is in the future")
        return now - max(0.0, wall - at)

    switched = stamp(state["switch_at"])
    opened, closed = stamp(state["opened_at"]), stamp(state["closed_at"])
    number(state["accumulator"])
    q_at_close = number(state["q_at_close"])
    pending = row["pending_on"]
    if not isinstance(pending, bool):
        raise ValueError("Invalid PWM pending command")
    reconcile = state.get("reconcile_off", False)
    if not isinstance(reconcile, bool):
        raise ValueError("Invalid PWM reconciliation state")
    matches = state["on"] == adapter.active() and not pending and not reconcile
    entity = adapter.entity
    # A mismatching state has no trustworthy last edge. Start its minimum guard
    # now; an uncertain in-flight ON is reconciled OFF through HeaterController.
    entity._set_last_heat_cycle_time(switched if matches and switched is not None else now)
    entity._set_time_changed(entity._get_cycle_start_time())
    adapter.restart_force_off = adapter.active() and not matches
    heater = entity._heater_controller
    # Preserve earned credit, but never accrue the unobserved offline interval.
    debit = number(row["pulse"][1]) if pending else 0.0
    heater.set_duty_accumulator(max(0.0, number(state["accumulator"]) - debit))
    heater._last_accumulator_calc_time = now if state["accumulator_at"] is not None else None
    pipeline = heater._heat_pipeline
    if pipeline is not None and matches:
        pipeline._valve_opened_at, pipeline._valve_closed_at = opened, closed
        pipeline._q_at_close = max(0.0, q_at_close)


def snapshot(group: PWMLoadBalancingManager, now: float, wall: float) -> dict[str, Any]:
    rows = {}
    for entity, zone in group._zones.items():
        group._sample(zone, now)
        rows[entity] = {
            "at": wall,
            "priority": zone.priority,
            "priority_at": wall + zone.priority_at - now if zone.priority_at is not None else None,
            "pending_priority": zone.pending_priority,
            "owed_at": wall + zone.owed_since - now if zone.owed_since is not None else None,
            "priority_count": zone.priority_start_count,
            "priority_latency": zone.priority_latency_ms,
            "blocked_count": zone.priority_blocked_count,
            "blocked_reason": zone.priority_blocked_reason,
            "max_deferral": zone.max_deferral,
            "controller": zone.adapter.checkpoint_state(now, wall),
            "credit": zone.credit,
            "allocation": zone.allocation,
            "epoch": zone.epoch,
            "output": zone.output_value,
            "signature": list(zone.target_signature),
            "pulse": [zone.pulse.seconds, zone.pulse.accumulator_debit, zone.pulse.continuous],
            "next_at": wall + zone.next_budget_at - now if zone.next_budget_at is not None else None,
            "requested": zone.requested,
            "delivered": zone.delivered,
            "cancelled": zone.cancelled_balance,
            "pending_on": zone.pending_on,
            "pending_start_epoch": zone.pending_start_epoch,
            "last_start_epoch": zone.last_start_epoch,
            "allocation_started": zone.allocation_started,
            "last_ordinary_start_epoch": zone.last_ordinary_start_epoch,
            "suppressed_ordinary_starts": zone.suppressed_ordinary_starts,
            "uncertain": zone.uncertain_seconds,
        }
    return {
        "version": 1,
        "at": wall,
        "period": group._period,
        "origin_at": wall + group._origin - now if group._origin is not None else wall,
        "members": group.members,
        "zones": rows,
    }


def restore(group: PWMLoadBalancingManager, data: dict[str, Any], now: float, wall: float) -> None:
    """Restore the same entitlement once; never replay missed offline periods."""
    if data["version"] != 1 or data["members"] != group.members or number(data["period"]) != group._period:
        raise ValueError("PWM journal configuration/version mismatch")
    saved_at = number(data["at"])
    origin = number(data["origin_at"])
    if saved_at > wall + 5 or origin > saved_at or set(data["zones"]) != set(group.members):
        raise ValueError("Invalid PWM journal time/membership")
    # Validate every row before mutating any zone.
    for row in data["zones"].values():
        for key in ("credit", "allocation", "epoch", "output", "requested", "delivered", "cancelled", "uncertain"):
            number(row[key])
        if not 0 <= row["allocation"] <= group._period or any(
            row[k] < 0 for k in ("requested", "delivered", "uncertain")
        ):
            raise ValueError("Invalid PWM journal balance")
        if not math.isclose(row["requested"], row["delivered"] + row["credit"] + row["cancelled"], abs_tol=0.01):
            raise ValueError("Inconsistent PWM journal balance")
        if row["epoch"] != int(row["epoch"]) or row["epoch"] < -1:
            raise ValueError("Invalid PWM epoch")
        pulse = row["pulse"]
        if len(pulse) != 3 or not isinstance(pulse[2], bool):
            raise ValueError("Invalid PWM pulse")
        if not 0 <= number(pulse[0]) <= group._period or number(pulse[1]) < 0:
            raise ValueError("Invalid PWM pulse duration")
        if len(row["signature"]) != 2 or not isinstance(row["signature"][1], str):
            raise ValueError("Invalid PWM signature")
        if row["signature"][0] is not None:
            number(row["signature"][0])
        if row["next_at"] is not None:
            number(row["next_at"])
        for key in ("priority", "pending_priority", "allocation_started"):
            if not isinstance(row.get(key, False), bool):
                raise ValueError("Invalid PWM priority flag")
        for key in ("priority_at", "owed_at"):
            if row.get(key) is not None and number(row[key]) > wall + 5:
                raise ValueError("Invalid PWM priority timestamp")
        for key in ("priority_count", "blocked_count", "max_deferral"):
            if number(row.get(key, 0)) < 0:
                raise ValueError("Invalid PWM priority counter")
        if row.get("priority_latency") is not None:
            number(row["priority_latency"])
        for key in (
            "last_start_epoch",
            "last_ordinary_start_epoch",
            "pending_start_epoch",
            "suppressed_ordinary_starts",
        ):
            value = row.get(key)
            if key in row and (value is not None or key != "pending_start_epoch"):
                number(value)
                minimum = 0 if key in ("pending_start_epoch", "suppressed_ordinary_starts") else -1
                if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                    raise ValueError("Invalid PWM start entitlement")
                latest_epoch = int((number(row.get("at", saved_at)) - origin) // group._period)
                if key != "suppressed_ordinary_starts" and value > latest_epoch:
                    raise ValueError("PWM start entitlement is in the future")
        if (
            "last_start_epoch" in row
            and "last_ordinary_start_epoch" in row
            and row["last_ordinary_start_epoch"] > row["last_start_epoch"]
        ):
            raise ValueError("Inconsistent PWM start entitlement")
    group._origin = now - max(0.0, wall - origin)
    for entity, zone in group._zones.items():
        if entity in group.restored_members:
            continue
        row = data["zones"][entity]
        row_at = number(row.get("at", saved_at))
        if row_at > wall + 5:
            raise ValueError("PWM row timestamp is in the future")
        offline = max(0.0, wall - row_at)
        zone.credit, zone.allocation = number(row["credit"]), number(row["allocation"])
        zone.requested, zone.delivered = number(row["requested"]), number(row["delivered"])
        zone.cancelled_balance = number(row["cancelled"])
        zone.uncertain_seconds = number(row["uncertain"])
        zone.epoch, zone.output_value = int(row["epoch"]), number(row["output"])
        zone.target_signature = tuple(row["signature"])
        zone.priority = row.get("priority", False)
        zone.pending_priority = row.get("pending_priority", False)
        zone.priority_at = now - (wall - row["priority_at"]) if row.get("priority_at") is not None else None
        zone.owed_since = now - (wall - row["owed_at"]) if row.get("owed_at") is not None else None
        zone.priority_start_count = int(row.get("priority_count", 0))
        zone.priority_latency_ms = row.get("priority_latency")
        zone.priority_blocked_count = int(row.get("blocked_count", 0))
        zone.priority_blocked_reason = row.get("blocked_reason")
        zone.max_deferral = number(row.get("max_deferral", 0))
        if "last_start_epoch" in row:
            zone.last_start_epoch = row["last_start_epoch"]
        elif row["delivered"] > 0 or row["controller"]["on"] or row["pending_on"]:
            # Legacy journals have no explicit allowance. The last known edge
            # conservatively spends that period, including an OFF edge after a
            # wrapped pulse. Do not retroactively classify it as ordinary.
            edge = row["controller"]["switch_at"]
            edge = number(edge) if edge is not None else row_at
            zone.last_start_epoch = max(-1, int((edge - origin) // group._period))
        zone.allocation_started = (
            row.get("allocation_started", row["controller"]["on"] or zone.last_start_epoch >= zone.epoch)
            or row["pending_on"]
        )
        zone.last_ordinary_start_epoch = row.get("last_ordinary_start_epoch", -1)
        zone.suppressed_ordinary_starts = row.get("suppressed_ordinary_starts", 0)
        if row["pending_on"]:
            pending_epoch = row.get("pending_start_epoch")
            if pending_epoch is None:
                pending_epoch = int((row_at - origin) // group._period)
            zone.last_start_epoch = max(zone.last_start_epoch, pending_epoch)
            if zone.adapter.active():
                # The uncertain ON may belong to the recovery period. Reconcile
                # it OFF without allowing a second ordinary start in that period.
                zone.last_start_epoch = max(zone.last_start_epoch, group.period_epoch(now))
        zone.pulse = Pulse(*row["pulse"])
        next_at = row["next_at"]
        zone.next_budget_at = now + number(next_at) - wall if next_at is not None else None
        uncertain = min(max(0.0, zone.credit), offline) if row["controller"]["on"] else 0.0
        if row["pending_on"] or row["controller"]["on"] != zone.adapter.active():
            uncertain = max(0.0, zone.credit)
        # Unobserved time is neither verified delivery nor a new catch-up pulse.
        zone.credit -= uncertain
        zone.cancelled_balance += uncertain
        zone.uncertain_seconds += uncertain
        if next_at is not None and next_at <= wall:
            zone.cancelled_balance += max(0.0, zone.credit)
            zone.credit = min(0.0, zone.credit)
            zone.epoch, zone.allocation, zone.pulse = -1, 0.0, Pulse(0.0)
            zone.next_budget_at = None
        group.restored_members.add(entity)
        zone.restart_pending = True
        zone.sampled_at, zone.observed_on = now, zone.adapter.active()
    group.mode, group.reason = "active", "restart_restored"
    group._observed_group = {entity for entity, zone in group._zones.items() if zone.adapter.active()}
    group._pump_sample = now
