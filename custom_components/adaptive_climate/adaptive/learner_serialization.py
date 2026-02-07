"""Serialization utilities for AdaptiveLearner state persistence.

This module provides functions to serialize and deserialize AdaptiveLearner state
to/from dictionaries for persistence across Home Assistant restarts.

Supports both v4 (flat) and v5 (mode-keyed) formats for backward compatibility.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Any
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
    chronic_approach_detector: Any | None = None,
    contribution_tracker: Any | None = None,
    heating_rate_learner: HeatingRateLearner | None = None,
) -> dict[str, Any]:
    """Serialize AdaptiveLearner state to a dictionary in v10 format with backward compatibility.

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
        chronic_approach_detector: Deprecated, kept for backward compatibility (ignored)
        contribution_tracker: ConfidenceContributionTracker instance for state serialization
        heating_rate_learner: HeatingRateLearner instance for state serialization

    Returns:
        Dictionary containing:
        - v10 structure with heating_rate_learner state
        - v9 structure with contribution_tracker state
        - v8 structure with unified undershoot_detector state
        - v5 mode-keyed structure (heating/cooling sub-dicts)
        - v4 backward-compatible top-level keys (cycle_history, auto_apply_count, etc.)

    Note:
        pid_history is no longer managed by AdaptiveLearner - it's now owned by PIDGainsManager.
        chronic_approach_detector parameter is deprecated - unified detector is used instead.
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
        # TODO: V4 backward-compatible top-level keys are still needed for users upgrading
        # from versions prior to v5 format (v0.36.0 and earlier). Consider removing after
        # a few major versions when all users have migrated to v5.
        # V4 backward-compatible top-level keys (for heating mode as default)
        "cycle_history": serialized_heating_cycles,
        "auto_apply_count": heating_auto_apply_count,
        "convergence_confidence": heating_convergence_confidence,
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


def restore_learner_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Restore AdaptiveLearner state from a dictionary.

    Supports v4 (flat), v5 (mode-keyed), v6 (undershoot detector), v7 (chronic approach detector),
    v8 (unified detector), v9 (contribution tracker), and v10 (heating rate learner) formats.

    Args:
        data: Dictionary containing either:
            v4 format: cycle_history, auto_apply_count, etc. at top level
            v5 format: heating/cooling sub-dicts with mode-specific data
            v6 format: v5 + undershoot_detector state
            v7 format: v6 + chronic_approach_detector state (separate)
            v8 format: v5 + unified undershoot_detector state (merged)
            v9 format: v8 + contribution_tracker state
            v10 format: v9 + heating_rate_learner state

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
        - undershoot_detector_state: Dict with unified detector state
        - contribution_tracker_state: Dict with contribution tracker state
        - heating_rate_learner_state: Dict with heating rate learner state
        - format_version: 'v10', 'v9', 'v8', 'v7', 'v6', 'v5', or 'v4' to indicate which format was detected
    """
    # Detect format version by checking for version-specific keys
    stored_version = data.get("format_version", 0)
    is_v10_format = stored_version == 10
    is_v9_format = stored_version == 9 and not is_v10_format
    is_v8_format = stored_version == 8 and not is_v9_format and not is_v10_format
    is_v7_format = "chronic_approach_detector" in data and not is_v8_format and not is_v9_format and not is_v10_format
    is_v6_format = (
        "undershoot_detector" in data
        and not is_v7_format
        and not is_v8_format
        and not is_v9_format
        and not is_v10_format
    )
    is_v5_format = (
        "heating" in data
        and not is_v6_format
        and not is_v7_format
        and not is_v8_format
        and not is_v9_format
        and not is_v10_format
    )

    if is_v10_format or is_v9_format or is_v8_format or is_v7_format or is_v6_format or is_v5_format:
        # V10/V9/V8/V7/V6/V5 format: mode-keyed structure
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
        # For backward compatibility, we ignore it if it exists in old persisted data
        pid_history = []

        # Restore heating rate learner state (v10)
        if is_v10_format:
            heating_rate_learner_state = data.get("heating_rate_learner", {})
        else:
            # Migration from v9 and earlier: default to empty state (will create fresh learner)
            heating_rate_learner_state = {}

        # Restore contribution tracker state (v9)
        if is_v10_format or is_v9_format:
            contribution_tracker_state = data.get(
                "contribution_tracker",
                {
                    "maintenance_contribution": 0.0,
                    "heating_rate_contribution": 0.0,
                    "recovery_cycle_count": 0,
                },
            )
        else:
            # Migration from v8 and earlier: default to zero contributions
            contribution_tracker_state = {
                "maintenance_contribution": 0.0,
                "heating_rate_contribution": 0.0,
                "recovery_cycle_count": 0,
            }

        # Restore unified undershoot detector state
        if is_v10_format or is_v9_format or is_v8_format:
            # V10/V9/V8 format: unified detector state
            undershoot_detector_state = data.get("undershoot_detector", {})
            if is_v10_format:
                format_version = "v10"
            elif is_v9_format:
                format_version = "v9"
            else:
                format_version = "v8"
        elif is_v7_format:
            # Migration from v7: merge undershoot and chronic approach detector states
            undershoot_state = data.get("undershoot_detector", {})
            chronic_state = data.get("chronic_approach_detector", {})

            # Take max of both cumulative_ki_multiplier values
            undershoot_multiplier = undershoot_state.get("cumulative_ki_multiplier", 1.0)
            chronic_multiplier = chronic_state.get("cumulative_multiplier", 1.0)
            merged_multiplier = max(undershoot_multiplier, chronic_multiplier)

            # Preserve last_adjustment_time from either (take most recent)
            # Note: In v7 format, last_adjustment_time was not persisted (monotonic)
            # so we don't migrate it here

            undershoot_detector_state = {
                "cumulative_ki_multiplier": merged_multiplier,
                "last_adjustment_time": None,  # Not persisted in v7
                "time_below_target": undershoot_state.get("time_below_target", 0.0),
                "thermal_debt": undershoot_state.get("thermal_debt", 0.0),
                "consecutive_failures": chronic_state.get("consecutive_failures", 0),
            }
            format_version = "v7"
            _LOGGER.info(
                "Migrated v7 to v8: merged cumulative multipliers (%.3f, %.3f) -> %.3f",
                undershoot_multiplier,
                chronic_multiplier,
                merged_multiplier,
            )
        elif is_v6_format:
            # Migration from v6: add missing fields for unified detector
            undershoot_state = data.get("undershoot_detector", {})
            undershoot_detector_state = {
                "cumulative_ki_multiplier": undershoot_state.get("cumulative_ki_multiplier", 1.0),
                "last_adjustment_time": None,
                "time_below_target": undershoot_state.get("time_below_target", 0.0),
                "thermal_debt": undershoot_state.get("thermal_debt", 0.0),
                "consecutive_failures": 0,  # New field in v8
            }
            format_version = "v6"
        else:
            # Migration from v5: initialize with defaults
            undershoot_detector_state = {
                "cumulative_ki_multiplier": 1.0,
                "last_adjustment_time": None,
                "time_below_target": 0.0,
                "thermal_debt": 0.0,
                "consecutive_failures": 0,
            }
            format_version = "v5"

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
        heating_cycle_history = [_deserialize_cycle(cycle_dict) for cycle_dict in data.get("cycle_history", [])]
        cooling_cycle_history = []

        # Restore auto_apply_count to heating mode (v4 didn't have mode split)
        heating_auto_apply_count = data.get("auto_apply_count", 0)
        cooling_auto_apply_count = 0

        # Restore convergence_confidence if present (otherwise default to 0)
        heating_convergence_confidence = data.get("convergence_confidence", 0.0)
        cooling_convergence_confidence = 0.0

        # V4 didn't store PID history or detector states
        pid_history = []
        undershoot_detector_state = {
            "cumulative_ki_multiplier": 1.0,
            "last_adjustment_time": None,
            "time_below_target": 0.0,
            "thermal_debt": 0.0,
            "consecutive_failures": 0,
        }
        contribution_tracker_state = {
            "maintenance_contribution": 0.0,
            "heating_rate_contribution": 0.0,
            "recovery_cycle_count": 0,
        }
        heating_rate_learner_state = {}

        _LOGGER.info(
            "AdaptiveLearner state restored (v4 compat): %d cycles, auto_apply=%d",
            len(heating_cycle_history),
            heating_auto_apply_count,
        )

        format_version = "v4"

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
        "contribution_tracker_state": contribution_tracker_state,
        "heating_rate_learner_state": heating_rate_learner_state,
        "format_version": format_version,
    }
