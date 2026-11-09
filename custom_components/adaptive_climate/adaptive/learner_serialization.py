"""Serialization utilities for AdaptiveLearner state persistence.

This module provides functions to serialize and deserialize AdaptiveLearner state
to/from dictionaries for persistence across Home Assistant restarts.

Migration policy:
- Every format bump requires a migrator function added to this module.
- Migrators NEVER wipe cycle_history or convergence_confidence — data loss is
  worse than a stale gain.
- Each migrator updates data["format_version"] in place and returns the dict.
- The _migrate() dispatch function chains v4→v5→...→CURRENT_VERSION, skipping
  any steps already satisfied by the stored version.
- On migration, a single WARN is logged with old and new version numbers.
- If stored version > CURRENT_VERSION, data is returned unchanged with a WARN
  (forward-compatibility: the user downgraded the component).

-------------------------------------------------------------------------------
Cross-restart state matrix (A04 audit)
-------------------------------------------------------------------------------
All state holders in adaptive/ and managers/ — what is persisted vs transient.

AdaptiveLearner (learning.py):
  Field                         | Unit         | Persisted | Notes
  ------------------------------|--------------|-----------|----------------------
  _heating_cycle_history        | CycleMetrics | YES v5+   | heating.cycle_history
  _cooling_cycle_history        | CycleMetrics | YES v5+   | cooling.cycle_history
  _last_adjustment_time         | datetime UTC | YES       | ISO-8601 string
  _consecutive_converged_cycles | int          | YES       |
  _pid_converged_for_ke         | bool         | YES       |
  _cycles_since_last_adjustment | int          | YES       | Missing = 0
  _rule_state_tracker           | RuleState    | NO        | Transient; fresh is correct

ConfidenceTracker (confidence.py):
  _heating_convergence_confidence | float | YES v5+ | heating.convergence_confidence
  _cooling_convergence_confidence | float | YES v5+ | cooling.convergence_confidence
  _heating_auto_apply_count       | int   | YES v5+ | heating.auto_apply_count
  _cooling_auto_apply_count       | int   | YES v5+ | cooling.auto_apply_count
  _heating_cycle_count            | int   | NO      | Derived from len(history) on restore
  _cooling_cycle_count            | int   | NO      | Derived from len(history) on restore

UndershootDetector (undershoot_detector.py):
  cumulative_ki_multiplier | float    | YES v6+ | clamped [1.0, MAX] on restore
  last_adjustment_time     | datetime | YES v8+ | ISO-8601; monotonic float → None in v10 migr
  _time_below_target       | float s  | YES v6+ |
  _thermal_debt            | float °C·h | YES v6+ |
  _consecutive_failures    | int      | YES v8+ |
  _heating_rate_learner    | object   | NO      | Wired externally after restore

ValidationManager (validation.py):
  _validation_mode              | bool         | NO  | Transient; restart aborts pending validation
  _validation_baseline_overshoot| float|None   | NO  | Transient (same)
  _validation_cycles            | list         | NO  | Transient (same)
  _last_seasonal_check          | datetime|None| NO  | Rate-limit only; missing = check runs once
  _last_seasonal_shift          | datetime|None| YES v11+ | FIXED: 7-day auto-apply block must survive restart
  _outdoor_temp_history         | list[float]  | NO  | Transient; repopulated from sensor readings
  _physics_baseline_kp/ki/kd    | float|None   | NO  | Re-set by physics init on every startup

KeManager (ke_manager.py):
  _steady_state_start       | float monotonic | NO | Intentional: meaningless across restart
  _last_ke_observation_time | float monotonic | NO | Intentional: meaningless across restart
  (ke_learner observations are persisted separately via LearningDataStore)

PreheatLearner (preheat.py):
  _observations            | dict[bin→list[HeatingObservation]] | YES | via to_dict()/from_dict()
  heating_type             | str   | YES | serialized in to_dict()
  max_hours                | float | YES | serialized in to_dict()
  _add_observation_counter | int   | NO  | Optimization counter; reset 0 is correct
-------------------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import logging

from .cycle_analysis import CycleMetrics
from .heating_rate_learner import HeatingRateLearner

_LOGGER = logging.getLogger(__name__)

# Current serialization format version
CURRENT_VERSION = 11


def serialize_cycle(cycle: CycleMetrics) -> dict[str, Any]:
    """Convert a CycleMetrics object to a dictionary.

    Args:
        cycle: CycleMetrics object to serialize

    Returns:
        Dictionary representation of the cycle metrics
    """
    return {
        "overshoot": cycle.overshoot,
        "undershoot": cycle.undershoot,
        "settling_time": cycle.settling_time,
        "oscillations": cycle.oscillations,
        "rise_time": cycle.rise_time,
        "integral_at_tolerance_entry": cycle.integral_at_tolerance_entry,
        "integral_at_setpoint_cross": cycle.integral_at_setpoint_cross,
        "decay_contribution": cycle.decay_contribution,
        "mode": cycle.mode,
        "starting_delta": cycle.starting_delta,
    }


def learner_to_dict(
    heating_cycle_history: list[CycleMetrics],
    cooling_cycle_history: list[CycleMetrics],
    heating_auto_apply_count: int,
    cooling_auto_apply_count: int,
    heating_convergence_confidence: float,
    cooling_convergence_confidence: float,
    last_adjustment_time: datetime | None,
    consecutive_converged_cycles: int,
    pid_converged_for_ke: bool,
    cycles_since_last_adjustment: int = 0,
    undershoot_detector: Any | None = None,
    contribution_tracker: Any | None = None,
    heating_rate_learner: HeatingRateLearner | None = None,
    last_seasonal_shift: datetime | None = None,
) -> dict[str, Any]:
    """Serialize AdaptiveLearner state to a dictionary in v11 format.

    Args:
        heating_cycle_history: List of heating cycle metrics
        cooling_cycle_history: List of cooling cycle metrics
        heating_auto_apply_count: Number of auto-applies for heating mode
        cooling_auto_apply_count: Number of auto-applies for cooling mode
        heating_convergence_confidence: Convergence confidence for heating mode
        cooling_convergence_confidence: Convergence confidence for cooling mode
        last_adjustment_time: Timestamp of last PID adjustment
        consecutive_converged_cycles: Number of consecutive converged cycles
        pid_converged_for_ke: Whether PID has converged for Ke learning
        undershoot_detector: UndershootDetector instance for state serialization
        contribution_tracker: ConfidenceContributionTracker instance for state serialization
        heating_rate_learner: HeatingRateLearner instance for state serialization
        last_seasonal_shift: Timestamp of last detected seasonal shift for auto-apply cooldown

    Returns:
        Dictionary containing v11 structure with last_seasonal_shift

    Note:
        pid_history is no longer managed by AdaptiveLearner - it's now owned by PIDGainsManager.
    """
    # Serialize cycle histories
    serialized_heating_cycles = [serialize_cycle(cycle) for cycle in heating_cycle_history]
    serialized_cooling_cycles = [serialize_cycle(cycle) for cycle in cooling_cycle_history]

    # Serialize unified undershoot detector state (v8 format)
    undershoot_state = {}
    if undershoot_detector is not None:
        # Serialize last_adjustment_time as ISO string (C10: monotonic floats are meaningless across restarts)
        last_adj = undershoot_detector.last_adjustment_time
        last_adj_iso = last_adj.isoformat() if last_adj is not None else None
        undershoot_state = {
            "cumulative_ki_multiplier": undershoot_detector.cumulative_ki_multiplier,
            "last_adjustment_time": last_adj_iso,
            "time_below_target": undershoot_detector._time_below_target,
            "thermal_debt": undershoot_detector._thermal_debt,
            "consecutive_failures": undershoot_detector._consecutive_failures,
        }

    # Serialize contribution tracker state (v9 format)
    contribution_tracker_state = {
        "maintenance_contribution": 0.0,
        "heating_rate_contribution": 0.0,
        "recovery_cycle_count": 0,
    }
    if contribution_tracker is not None:
        contribution_tracker_state = contribution_tracker.to_dict()

    # Serialize heating rate learner state (v10 format)
    heating_rate_learner_state = {}
    if heating_rate_learner is not None:
        heating_rate_learner_state = heating_rate_learner.to_dict()

    return {
        # V11 seasonal shift timestamp for auto-apply cooldown
        "last_seasonal_shift": (last_seasonal_shift.isoformat() if last_seasonal_shift is not None else None),
        # V10 heating rate learner state
        "heating_rate_learner": heating_rate_learner_state,
        # V9 contribution tracker state
        "contribution_tracker": contribution_tracker_state,
        # V8 unified undershoot detector state
        "undershoot_detector": undershoot_state,
        "format_version": CURRENT_VERSION,
        # V5 mode-keyed structure
        "heating": {
            "cycle_history": serialized_heating_cycles,
            "auto_apply_count": heating_auto_apply_count,
            "convergence_confidence": heating_convergence_confidence,
        },
        "cooling": {
            "cycle_history": serialized_cooling_cycles,
            "auto_apply_count": cooling_auto_apply_count,
            "convergence_confidence": cooling_convergence_confidence,
        },
        # Shared fields
        "last_adjustment_time": (last_adjustment_time.isoformat() if last_adjustment_time is not None else None),
        "consecutive_converged_cycles": consecutive_converged_cycles,
        "cycles_since_last_adjustment": cycles_since_last_adjustment,
        "pid_converged_for_ke": pid_converged_for_ke,
    }


def _deserialize_cycle(cycle_dict: dict[str, Any]) -> CycleMetrics:
    """Convert a dictionary to a CycleMetrics object.

    Args:
        cycle_dict: Dictionary representation of cycle metrics

    Returns:
        CycleMetrics object
    """
    return CycleMetrics(
        overshoot=cycle_dict.get("overshoot"),
        undershoot=cycle_dict.get("undershoot"),
        settling_time=cycle_dict.get("settling_time"),
        oscillations=cycle_dict.get("oscillations", 0),
        rise_time=cycle_dict.get("rise_time"),
        integral_at_tolerance_entry=cycle_dict.get("integral_at_tolerance_entry"),
        integral_at_setpoint_cross=cycle_dict.get("integral_at_setpoint_cross"),
        decay_contribution=cycle_dict.get("decay_contribution"),
        mode=cycle_dict.get("mode"),
        starting_delta=cycle_dict.get("starting_delta"),
    )


def _migrate_v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    """Flat → mode-keyed. Move cycle_history/auto_apply_count/convergence_confidence under heating{}."""
    cycle_history = data.pop("cycle_history", [])
    auto_apply_count = data.pop("auto_apply_count", 0)
    convergence_confidence = data.pop("convergence_confidence", 0.0)

    data["heating"] = {
        "cycle_history": cycle_history,
        "auto_apply_count": auto_apply_count,
        "convergence_confidence": convergence_confidence,
    }
    data.setdefault("cooling", {"cycle_history": [], "auto_apply_count": 0, "convergence_confidence": 0.0})
    data["format_version"] = 5
    return data


def _migrate_v5_to_v6(data: dict[str, Any]) -> dict[str, Any]:
    """Add undershoot_detector with zero defaults."""
    data.setdefault(
        "undershoot_detector",
        {"cumulative_ki_multiplier": 1.0, "time_below_target": 0.0, "thermal_debt": 0.0},
    )
    data["format_version"] = 6
    return data


def _migrate_v6_to_v7(data: dict[str, Any]) -> dict[str, Any]:
    """Add chronic_approach_detector with zero defaults (was briefly present in v7)."""
    data.setdefault("chronic_approach_detector", {"cumulative_multiplier": 1.0, "consecutive_failures": 0})
    data["format_version"] = 7
    return data


def _migrate_v7_to_v8(data: dict[str, Any]) -> dict[str, Any]:
    """Merge chronic_approach_detector into undershoot_detector. Take max of multipliers. Add consecutive_failures=0."""
    chronic = data.pop("chronic_approach_detector", {})
    undershoot = data.get("undershoot_detector", {})

    # Merge: take max of multipliers so we don't lose accumulated Ki boost
    chronic_mult = float(chronic.get("cumulative_multiplier", 1.0))
    undershoot_mult = float(undershoot.get("cumulative_ki_multiplier", 1.0))
    merged_mult = max(chronic_mult, undershoot_mult)

    undershoot["cumulative_ki_multiplier"] = merged_mult
    undershoot.setdefault("consecutive_failures", 0)
    undershoot.setdefault("last_adjustment_time", None)
    data["undershoot_detector"] = undershoot
    data["format_version"] = 8
    return data


def _migrate_v8_to_v9(data: dict[str, Any]) -> dict[str, Any]:
    """Add contribution_tracker with zero defaults."""
    data.setdefault(
        "contribution_tracker",
        {"maintenance_contribution": 0.0, "heating_rate_contribution": 0.0, "recovery_cycle_count": 0},
    )
    data["format_version"] = 9
    return data


def _migrate_v9_to_v10(data: dict[str, Any]) -> dict[str, Any]:
    """Add heating_rate_learner={}. Convert undershoot last_adjustment_time from monotonic float to None.

    Monotonic floats are meaningless across restarts, so we reset to None rather
    than trying to convert them. This means the undershoot detector will have a
    fresh cooldown window after migration, which is the safe default.
    """
    data.setdefault("heating_rate_learner", {})

    # Convert monotonic float to None — can't meaningfully translate across restart
    undershoot = data.get("undershoot_detector", {})
    last_adj = undershoot.get("last_adjustment_time")
    if isinstance(last_adj, (int, float)):
        undershoot["last_adjustment_time"] = None
    data["undershoot_detector"] = undershoot
    data["format_version"] = 10
    return data


def _migrate_v10_to_v11(data: dict[str, Any]) -> dict[str, Any]:
    """Add last_seasonal_shift=null.

    The seasonal-shift auto-apply block (SEASONAL_SHIFT_BLOCK_DAYS=7) was not
    previously persisted.  On upgrade the cooldown resets to None (unblocked),
    which is the safe default — any in-progress block expires at restart instead
    of persisting.  Future restarts will correctly preserve the value.
    """
    data.setdefault("last_seasonal_shift", None)
    data["format_version"] = 11
    return data


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Chain migrate data from stored version up to CURRENT_VERSION.

    Returns migrated dict. Never wipes cycle_history.

    If version < 4: treated as v4 (flat structure, best effort).
    If version > CURRENT_VERSION: returned unchanged with a warning.
    """
    stored_version = data.get("format_version", 0)
    try:
        stored_version = int(stored_version)
    except (TypeError, ValueError):
        stored_version = 0

    if stored_version > CURRENT_VERSION:
        _LOGGER.warning(
            "Learner state has future format version %s (current %s) — returning unchanged",
            stored_version,
            CURRENT_VERSION,
        )
        return data

    if stored_version < 4:
        # Treat as v4 flat structure (best effort)
        stored_version = 4
        data.setdefault("format_version", 4)

    _steps = [
        (4, _migrate_v4_to_v5),
        (5, _migrate_v5_to_v6),
        (6, _migrate_v6_to_v7),
        (7, _migrate_v7_to_v8),
        (8, _migrate_v8_to_v9),
        (9, _migrate_v9_to_v10),
        (10, _migrate_v10_to_v11),
    ]

    for from_version, migrator in _steps:
        if stored_version <= from_version:
            data = migrator(data)

    return data


def default_learner_state() -> dict[str, Any]:
    """Return default learner state for when restoration fails or data is missing.

    Returns:
        Dictionary with default empty state
    """
    return {
        "heating_cycle_history": [],
        "cooling_cycle_history": [],
        "heating_auto_apply_count": 0,
        "cooling_auto_apply_count": 0,
        "heating_convergence_confidence": 0.0,
        "cooling_convergence_confidence": 0.0,
        "pid_history": [],
        "last_adjustment_time": None,
        "last_seasonal_shift": None,
        "consecutive_converged_cycles": 0,
        "pid_converged_for_ke": False,
        "undershoot_detector_state": {
            "cumulative_ki_multiplier": 1.0,
            "last_adjustment_time": None,
            "time_below_target": 0.0,
            "thermal_debt": 0.0,
            "consecutive_failures": 0,
        },
        "contribution_tracker_state": {
            "maintenance_contribution": 0.0,
            "heating_rate_contribution": 0.0,
            "recovery_cycle_count": 0,
        },
        "heating_rate_learner_state": {},
        "format_version": CURRENT_VERSION,
    }


def restore_learner_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Restore AdaptiveLearner state from v11 format dictionary.

    Args:
        data: Dictionary containing v11 (or earlier, auto-migrated) format data

    Returns:
        Dictionary with restored state containing:
        - heating_cycle_history: List of CycleMetrics for heating mode
        - cooling_cycle_history: List of CycleMetrics for cooling mode
        - heating_auto_apply_count: Auto-apply count for heating mode
        - cooling_auto_apply_count: Auto-apply count for cooling mode
        - heating_convergence_confidence: Convergence confidence for heating mode
        - cooling_convergence_confidence: Convergence confidence for cooling mode
        - pid_history: List of PID snapshots (always empty, managed by PIDGainsManager)
        - last_adjustment_time: Timestamp of last PID adjustment (datetime or None)
        - last_seasonal_shift: Timestamp of last seasonal shift (datetime or None)
        - consecutive_converged_cycles: Number of consecutive converged cycles
        - pid_converged_for_ke: Whether PID has converged for Ke learning
        - undershoot_detector_state: Dict with unified detector state
        - contribution_tracker_state: Dict with contribution tracker state
        - heating_rate_learner_state: Dict with heating rate learner state
        - format_version: CURRENT_VERSION to indicate migrated format
    """
    stored_version = data.get("format_version", 0)
    try:
        stored_version = int(stored_version)
    except (TypeError, ValueError):
        stored_version = 0

    if stored_version != CURRENT_VERSION:
        _LOGGER.warning(
            "Migrating learner state from v%s to v%s — cycle history preserved",
            stored_version,
            CURRENT_VERSION,
        )
        data = _migrate(data)

    # V10 format: mode-keyed structure
    heating_cycle_history = [
        _deserialize_cycle(cycle_dict) for cycle_dict in data.get("heating", {}).get("cycle_history", [])
    ]
    cooling_cycle_history = [
        _deserialize_cycle(cycle_dict) for cycle_dict in data.get("cooling", {}).get("cycle_history", [])
    ]

    # Restore mode-specific auto_apply_counts
    heating_auto_apply_count = data.get("heating", {}).get("auto_apply_count", 0)
    cooling_auto_apply_count = data.get("cooling", {}).get("auto_apply_count", 0)

    # Restore mode-specific convergence confidence
    heating_convergence_confidence = data.get("heating", {}).get("convergence_confidence", 0.0)
    cooling_convergence_confidence = data.get("cooling", {}).get("convergence_confidence", 0.0)

    # pid_history is no longer stored in learner data (now managed by PIDGainsManager)
    pid_history = []

    # Restore heating rate learner state (v10)
    heating_rate_learner_state = data.get("heating_rate_learner", {})

    # Restore contribution tracker state (v10)
    contribution_tracker_state = data.get(
        "contribution_tracker",
        {
            "maintenance_contribution": 0.0,
            "heating_rate_contribution": 0.0,
            "recovery_cycle_count": 0,
        },
    )

    # Restore unified undershoot detector state (v10)
    undershoot_detector_state = data.get("undershoot_detector", {})

    # Restore shared fields
    last_adj_time = data.get("last_adjustment_time")
    if last_adj_time is not None and isinstance(last_adj_time, str):
        try:
            last_adjustment_time = datetime.fromisoformat(last_adj_time)
        except (ValueError, TypeError):
            _LOGGER.warning("Could not parse last_adjustment_time: %s — using None", last_adj_time)
            last_adjustment_time = None
    else:
        last_adjustment_time = None

    # Restore seasonal shift timestamp (v11) — governs auto-apply cooldown
    last_seasonal_shift_raw = data.get("last_seasonal_shift")
    if last_seasonal_shift_raw is not None and isinstance(last_seasonal_shift_raw, str):
        try:
            last_seasonal_shift: datetime | None = datetime.fromisoformat(last_seasonal_shift_raw)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Could not parse last_seasonal_shift: %s — using None (auto-apply block reset)",
                last_seasonal_shift_raw,
            )
            last_seasonal_shift = None
    else:
        last_seasonal_shift = None

    # Restore convergence tracking fields
    consecutive_converged_cycles = data.get("consecutive_converged_cycles", 0)
    pid_converged_for_ke = data.get("pid_converged_for_ke", False)

    _LOGGER.info(
        "AdaptiveLearner state restored (v11): heating=%d cycles, cooling=%d cycles",
        len(heating_cycle_history),
        len(cooling_cycle_history),
    )

    return {
        "heating_cycle_history": heating_cycle_history,
        "cooling_cycle_history": cooling_cycle_history,
        "heating_auto_apply_count": heating_auto_apply_count,
        "cooling_auto_apply_count": cooling_auto_apply_count,
        "heating_convergence_confidence": heating_convergence_confidence,
        "cooling_convergence_confidence": cooling_convergence_confidence,
        "pid_history": pid_history,
        "last_adjustment_time": last_adjustment_time,
        "last_seasonal_shift": last_seasonal_shift,
        "consecutive_converged_cycles": consecutive_converged_cycles,
        "cycles_since_last_adjustment": data.get("cycles_since_last_adjustment", 0),
        "pid_converged_for_ke": pid_converged_for_ke,
        "undershoot_detector_state": undershoot_detector_state,
        "contribution_tracker_state": contribution_tracker_state,
        "heating_rate_learner_state": heating_rate_learner_state,
        "format_version": CURRENT_VERSION,
    }
