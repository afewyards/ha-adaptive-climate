"""Serialization utilities for AdaptiveLearner state persistence.

This module provides functions to serialize and deserialize AdaptiveLearner state
to/from dictionaries for persistence across Home Assistant restarts.

Supports both v4 (flat) and v5 (mode-keyed) formats for backward compatibility.
"""

from datetime import datetime
from typing import Dict, List, Any, Optional
import logging

from .cycle_analysis import CycleMetrics

_LOGGER = logging.getLogger(__name__)


def serialize_cycle(cycle: CycleMetrics) -> Dict[str, Any]:
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
    }


def _serialize_pid_history(pid_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Serialize PID history with ISO timestamps.

    Args:
        pid_history: List of PID snapshot dictionaries

    Returns:
        List of serialized PID snapshots with ISO timestamp strings
    """
    serialized = []
    for entry in pid_history:
        serialized_entry = entry.copy()
        if isinstance(entry.get("timestamp"), datetime):
            serialized_entry["timestamp"] = entry["timestamp"].isoformat()
        serialized.append(serialized_entry)
    return serialized


def learner_to_dict(
    heating_cycle_history: List[CycleMetrics],
    cooling_cycle_history: List[CycleMetrics],
    heating_auto_apply_count: int,
    cooling_auto_apply_count: int,
    heating_convergence_confidence: float,
    cooling_convergence_confidence: float,
    pid_history: List[Dict[str, Any]],
    last_adjustment_time: Optional[datetime],
    consecutive_converged_cycles: int,
    pid_converged_for_ke: bool,
    undershoot_detector: Optional[Any] = None,
) -> Dict[str, Any]:
    """Serialize AdaptiveLearner state to a dictionary in v6 format with backward compatibility.

    Args:
        heating_cycle_history: List of heating cycle metrics
        cooling_cycle_history: List of cooling cycle metrics
        heating_auto_apply_count: Number of auto-applies for heating mode
        cooling_auto_apply_count: Number of auto-applies for cooling mode
        heating_convergence_confidence: Convergence confidence for heating mode
        cooling_convergence_confidence: Convergence confidence for cooling mode
        pid_history: List of PID snapshots
        last_adjustment_time: Timestamp of last PID adjustment
        consecutive_converged_cycles: Number of consecutive converged cycles
        pid_converged_for_ke: Whether PID has converged for Ke learning
        undershoot_detector: UndershootDetector instance for state serialization

    Returns:
        Dictionary containing:
        - v6 structure with undershoot detector state
        - v5 mode-keyed structure (heating/cooling sub-dicts)
        - v4 backward-compatible top-level keys (cycle_history, auto_apply_count, etc.)
    """
    # Serialize PID history with ISO timestamps
    serialized_pid_history = _serialize_pid_history(pid_history)

    # Serialize cycle histories
    serialized_heating_cycles = [serialize_cycle(cycle) for cycle in heating_cycle_history]
    serialized_cooling_cycles = [serialize_cycle(cycle) for cycle in cooling_cycle_history]

    # Serialize undershoot detector state
    undershoot_state = {}
    if undershoot_detector is not None:
        undershoot_state = {
            "time_below_target": undershoot_detector.time_below_target,
            "thermal_debt": undershoot_detector.thermal_debt,
            "cumulative_ki_multiplier": undershoot_detector.cumulative_ki_multiplier,
            # Note: last_adjustment_time uses monotonic, not persisted
        }

    return {
        # V6 undershoot detector state
        "undershoot_detector": undershoot_state,
        # V5 mode-keyed structure
        "heating": {
            "cycle_history": serialized_heating_cycles,
            "auto_apply_count": heating_auto_apply_count,
            "convergence_confidence": heating_convergence_confidence,
            "pid_history": serialized_pid_history,
        },
        "cooling": {
            "cycle_history": serialized_cooling_cycles,
            "auto_apply_count": cooling_auto_apply_count,
            "convergence_confidence": cooling_convergence_confidence,
            "pid_history": [],  # Cooling mode typically shares PID history or has separate tracking
        },
        # TODO: V4 backward-compatible top-level keys are still needed for users upgrading
        # from versions prior to v5 format (v0.36.0 and earlier). Consider removing after
        # a few major versions when all users have migrated to v5.
        # V4 backward-compatible top-level keys (for heating mode as default)
        "cycle_history": serialized_heating_cycles,
        "auto_apply_count": heating_auto_apply_count,
        "convergence_confidence": heating_convergence_confidence,
        # Shared fields
        "last_adjustment_time": (
            last_adjustment_time.isoformat()
            if last_adjustment_time is not None
            else None
        ),
        "consecutive_converged_cycles": consecutive_converged_cycles,
        "pid_converged_for_ke": pid_converged_for_ke,
    }


def _deserialize_cycle(cycle_dict: Dict[str, Any]) -> CycleMetrics:
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
    )


def _deserialize_pid_history(pid_history_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deserialize PID history from dictionaries with ISO timestamp strings.

    Args:
        pid_history_data: List of PID snapshot dictionaries with ISO timestamps

    Returns:
        List of PID snapshot dictionaries with parsed datetime objects
    """
    restored = []
    for entry in pid_history_data:
        try:
            restored_entry = entry.copy()
            timestamp_str = entry.get("timestamp")
            if timestamp_str is not None and isinstance(timestamp_str, str):
                restored_entry["timestamp"] = datetime.fromisoformat(timestamp_str)
            restored.append(restored_entry)
        except (ValueError, TypeError) as e:
            _LOGGER.warning("Failed to restore PID history entry: %s - %s", entry, e)
            continue
    return restored


def restore_learner_from_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Restore AdaptiveLearner state from a dictionary.

    Supports v4 (flat), v5 (mode-keyed), and v6 (undershoot detector) formats.

    Args:
        data: Dictionary containing either:
            v4 format: cycle_history, auto_apply_count, etc. at top level
            v5 format: heating/cooling sub-dicts with mode-specific data
            v6 format: v5 + undershoot_detector state

    Returns:
        Dictionary with restored state containing:
        - heating_cycle_history: List of CycleMetrics for heating mode
        - cooling_cycle_history: List of CycleMetrics for cooling mode
        - heating_auto_apply_count: Auto-apply count for heating mode
        - cooling_auto_apply_count: Auto-apply count for cooling mode
        - heating_convergence_confidence: Convergence confidence for heating mode
        - cooling_convergence_confidence: Convergence confidence for cooling mode
        - pid_history: List of PID snapshots
        - last_adjustment_time: Timestamp of last PID adjustment (datetime or None)
        - consecutive_converged_cycles: Number of consecutive converged cycles
        - pid_converged_for_ke: Whether PID has converged for Ke learning
        - undershoot_detector_state: Dict with detector state (time_below_target, etc.)
        - format_version: 'v6', 'v5', or 'v4' to indicate which format was detected
    """
    # Detect format version by checking for version-specific keys
    is_v6_format = "undershoot_detector" in data
    is_v5_format = "heating" in data

    if is_v6_format or is_v5_format:
        # V6/V5 format: mode-keyed structure
        heating_cycle_history = [
            _deserialize_cycle(cycle_dict)
            for cycle_dict in data.get("heating", {}).get("cycle_history", [])
        ]
        cooling_cycle_history = [
            _deserialize_cycle(cycle_dict)
            for cycle_dict in data.get("cooling", {}).get("cycle_history", [])
        ]

        # Restore mode-specific auto_apply_counts
        heating_auto_apply_count = data.get("heating", {}).get("auto_apply_count", 0)
        cooling_auto_apply_count = data.get("cooling", {}).get("auto_apply_count", 0)

        # Restore mode-specific convergence confidence
        heating_convergence_confidence = data.get("heating", {}).get("convergence_confidence", 0.0)
        cooling_convergence_confidence = data.get("cooling", {}).get("convergence_confidence", 0.0)

        # Restore PID history from heating mode
        pid_history = _deserialize_pid_history(data.get("heating", {}).get("pid_history", []))

        # Restore undershoot detector state (v6 only)
        if is_v6_format:
            undershoot_detector_state = data.get("undershoot_detector", {})
            format_version = 'v6'
        else:
            # Migration from v5: initialize with defaults
            undershoot_detector_state = {
                "time_below_target": 0.0,
                "thermal_debt": 0.0,
                "cumulative_ki_multiplier": 1.0,
            }
            format_version = 'v5'

        _LOGGER.info(
            "AdaptiveLearner state restored (%s): heating=%d cycles, cooling=%d cycles, "
            "heating_auto_apply=%d, cooling_auto_apply=%d, pid_history=%d",
            format_version,
            len(heating_cycle_history),
            len(cooling_cycle_history),
            heating_auto_apply_count,
            cooling_auto_apply_count,
            len(pid_history),
        )

    else:
        # V4 format: flat structure (backward compatibility)
        heating_cycle_history = [
            _deserialize_cycle(cycle_dict)
            for cycle_dict in data.get("cycle_history", [])
        ]
        cooling_cycle_history = []

        # Restore auto_apply_count to heating mode (v4 didn't have mode split)
        heating_auto_apply_count = data.get("auto_apply_count", 0)
        cooling_auto_apply_count = 0

        # Restore convergence_confidence if present (otherwise default to 0)
        heating_convergence_confidence = data.get("convergence_confidence", 0.0)
        cooling_convergence_confidence = 0.0

        # V4 didn't store PID history or undershoot detector state
        pid_history = []
        undershoot_detector_state = {
            "time_below_target": 0.0,
            "thermal_debt": 0.0,
            "cumulative_ki_multiplier": 1.0,
        }

        _LOGGER.info(
            "AdaptiveLearner state restored (v4 compat): %d cycles, auto_apply=%d",
            len(heating_cycle_history),
            heating_auto_apply_count,
        )

        format_version = 'v4'

    # Restore shared fields (present in all versions)
    last_adj_time = data.get("last_adjustment_time")
    if last_adj_time is not None and isinstance(last_adj_time, str):
        last_adjustment_time = datetime.fromisoformat(last_adj_time)
    else:
        last_adjustment_time = None

    # Restore convergence tracking fields
    consecutive_converged_cycles = data.get("consecutive_converged_cycles", 0)
    pid_converged_for_ke = data.get("pid_converged_for_ke", False)

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
        "format_version": format_version,
    }
