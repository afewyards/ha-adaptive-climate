"""Serialization utilities for AdaptiveLearner state persistence.

This module provides functions to serialize and deserialize AdaptiveLearner state
to/from dictionaries for persistence across Home Assistant restarts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import logging

from .cycle_analysis import CycleMetrics
from .heating_rate_learner import HeatingRateLearner

_LOGGER = logging.getLogger(__name__)

# Current serialization format version
CURRENT_VERSION = 10


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
    undershoot_detector: Any | None = None,
    contribution_tracker: Any | None = None,
    heating_rate_learner: HeatingRateLearner | None = None,
) -> dict[str, Any]:
    """Serialize AdaptiveLearner state to a dictionary in v10 format.

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

    Returns:
        Dictionary containing v10 structure with heating_rate_learner state

    Note:
        pid_history is no longer managed by AdaptiveLearner - it's now owned by PIDGainsManager.
    """
    # Serialize cycle histories
    serialized_heating_cycles = [serialize_cycle(cycle) for cycle in heating_cycle_history]
    serialized_cooling_cycles = [serialize_cycle(cycle) for cycle in cooling_cycle_history]

    # Serialize unified undershoot detector state (v8 format)
    undershoot_state = {}
    if undershoot_detector is not None:
        undershoot_state = {
            "cumulative_ki_multiplier": undershoot_detector.cumulative_ki_multiplier,
            "last_adjustment_time": undershoot_detector.last_adjustment_time,
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


def _default_learner_state() -> dict[str, Any]:
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
        "format_version": "v10",
    }


def restore_learner_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Restore AdaptiveLearner state from v10 format dictionary.

    Args:
        data: Dictionary containing v10 format data

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
        - consecutive_converged_cycles: Number of consecutive converged cycles
        - pid_converged_for_ke: Whether PID has converged for Ke learning
        - undershoot_detector_state: Dict with unified detector state
        - contribution_tracker_state: Dict with contribution tracker state
        - heating_rate_learner_state: Dict with heating rate learner state
        - format_version: 'v10' to indicate v10 format
    """
    stored_version = data.get("format_version", 0)

    if stored_version != 10:
        _LOGGER.warning(
            "Unrecognized learner format version %s, using defaults",
            stored_version,
        )
        return _default_learner_state()

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
        last_adjustment_time = datetime.fromisoformat(last_adj_time)
    else:
        last_adjustment_time = None

    # Restore convergence tracking fields
    consecutive_converged_cycles = data.get("consecutive_converged_cycles", 0)
    pid_converged_for_ke = data.get("pid_converged_for_ke", False)

    _LOGGER.info(
        "AdaptiveLearner state restored (v10): heating=%d cycles, cooling=%d cycles",
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
        "consecutive_converged_cycles": consecutive_converged_cycles,
        "pid_converged_for_ke": pid_converged_for_ke,
        "undershoot_detector_state": undershoot_detector_state,
        "contribution_tracker_state": contribution_tracker_state,
        "heating_rate_learner_state": heating_rate_learner_state,
        "format_version": "v10",
    }
