"""Auto-apply safety gates and threshold management for Adaptive Climate.

This module contains logic for automatic PID adjustment application, including:
- Heating-type-specific confidence thresholds
- Safety gates (validation mode, limits, seasonal shifts)
- Auto-apply decision orchestration
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode

from ..const import (
    AUTO_APPLY_THRESHOLDS,
    SUBSEQUENT_LEARNING_CYCLE_MULTIPLIER,
    HeatingType,
    MIN_CYCLES_FOR_LEARNING,
    CONFIDENCE_TIER_1,
    CONFIDENCE_TIER_2,
    CONFIDENCE_TIER_3,
    HEATING_TYPE_CONFIDENCE_SCALE,
)

from ..helpers.hvac_mode import mode_to_str

_LOGGER = logging.getLogger(__name__)


def get_auto_apply_thresholds(heating_type: str | None = None) -> dict[str, float]:
    """
    Get auto-apply thresholds for a specific heating type.

    Returns heating-type-specific thresholds if available, otherwise returns
    convector thresholds as the default baseline.

    Args:
        heating_type: One of HEATING_TYPE_* constants, or None for default

    Returns:
        Dict with auto-apply threshold values (confidence_first, confidence_subsequent,
        min_cycles, cooldown_hours, cooldown_cycles)
    """
    if heating_type and heating_type in AUTO_APPLY_THRESHOLDS:
        return AUTO_APPLY_THRESHOLDS[heating_type]
    return AUTO_APPLY_THRESHOLDS[HeatingType.CONVECTOR]


class AutoApplyManager:
    """Manages auto-apply safety gates and threshold-based decision making.

    This class orchestrates the safety checks required before automatically
    applying PID adjustments, including validation mode checks, safety limits,
    seasonal shift detection, and heating-type-specific confidence thresholds.
    """

    def __init__(self, heating_type: str | None = None):
        """Initialize the AutoApplyManager.

        Args:
            heating_type: Heating system type for threshold selection
        """
        self._heating_type = heating_type

    def _compute_learning_status(
        self,
        cycle_count: int,
        confidence: float,
        heating_type: str,
        contribution_tracker: Any = None,
        mode: HVACMode = None,
    ) -> str:
        """Compute learning status based on cycle metrics and recovery cycle requirements.

        Args:
            cycle_count: Number of cycles collected
            confidence: Convergence confidence (0.0-1.0)
            heating_type: HeatingType value (e.g., "floor_hydronic", "radiator")
            contribution_tracker: Optional ConfidenceContributionTracker for tier gate checks
            mode: Optional HVACMode for tier gate checks (defaults to HEAT)

        Returns:
            Learning status string: "collecting" | "stable" | "tuned" | "optimized"
        """
        # Confidence is already in 0-1 range from ConfidenceTracker
        convergence_confidence = confidence

        # Get heating-type-specific confidence scaling factor
        # Default to CONVECTOR (1.0) if heating_type not recognized
        scale = HEATING_TYPE_CONFIDENCE_SCALE.get(
            heating_type, HEATING_TYPE_CONFIDENCE_SCALE.get(HeatingType.CONVECTOR, 1.0)
        )

        # Calculate scaled thresholds (tier 3 is NOT scaled - always 95%)
        scaled_tier_1 = min(CONFIDENCE_TIER_1 * scale / 100.0, 0.95)  # Cap at 95%
        scaled_tier_2 = min(CONFIDENCE_TIER_2 * scale / 100.0, 0.95)  # Cap at 95%
        tier_3 = CONFIDENCE_TIER_3 / 100.0  # Always 95%

        # Check tier gates (recovery cycle requirements) if tracker provided
        can_reach_tier_1 = True
        can_reach_tier_2 = True
        if contribution_tracker is not None:
            can_reach_tier_1 = contribution_tracker.can_reach_tier(1, mode)
            can_reach_tier_2 = contribution_tracker.can_reach_tier(2, mode)

        # Collecting: not enough cycles OR confidence below tier 1 OR not enough recovery cycles
        # Stable: confidence >= tier 1 AND < tier 2 AND enough recovery cycles for tier 1
        # Tuned: confidence >= tier 2 AND < tier 3 AND enough recovery cycles for tier 2
        # Optimized: confidence >= tier 3
        if cycle_count < MIN_CYCLES_FOR_LEARNING or convergence_confidence < scaled_tier_1:
            return "collecting"
        elif not can_reach_tier_1:
            # Confidence threshold met but not enough recovery cycles for tier 1
            return "collecting"
        elif convergence_confidence >= tier_3:
            return "optimized"
        elif convergence_confidence >= scaled_tier_2:
            if can_reach_tier_2:
                return "tuned"
            else:
                # Confidence threshold met but not enough recovery cycles for tier 2
                return "stable"
        else:
            return "stable"

    def check_auto_apply_safety_gates(
        self,
        validation_manager: Any,  # ValidationManager
        confidence_tracker: Any,  # ConfidenceTracker
        current_kp: float,
        current_ki: float,
        current_kd: float,
        outdoor_temp: float | None,
        pid_history: list[dict[str, Any]],
        mode: HVACMode = None,
        contribution_tracker: Any = None,  # ConfidenceContributionTracker for tier gates
    ) -> tuple[bool, int | None, int | None, int | None]:
        """Check all auto-apply safety gates and return adjusted parameters.

        This method performs a comprehensive safety check before auto-applying
        PID adjustments. It enforces:
        1. Validation mode check (skip if validating previous auto-apply)
        2. Safety limits (lifetime, seasonal, drift, shift cooldown)
        3. Seasonal shift detection
        4. Confidence threshold (first apply vs subsequent)
        5. Recovery cycle requirements (tier gates)

        Args:
            validation_manager: ValidationManager instance for safety checks
            confidence_tracker: ConfidenceTracker for confidence/count tracking
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            outdoor_temp: Current outdoor temperature for seasonal shift detection
            pid_history: List of PID history snapshots
            mode: HVACMode (HEAT or COOL) for mode-specific thresholds
            contribution_tracker: Optional ConfidenceContributionTracker for tier gate checks

        Returns:
            Tuple of:
            - bool: True if all gates passed, False if blocked
            - int or None: Adjusted min_interval_hours (or None if blocked)
            - int or None: Adjusted min_adjustment_cycles (or None if blocked)
            - int or None: Adjusted min_cycles (or None if blocked)
        """
        from ..helpers.hvac_mode import get_hvac_cool_mode

        # Get mode-specific counts
        auto_apply_count = confidence_tracker.get_auto_apply_count(mode)
        convergence_confidence = confidence_tracker.get_convergence_confidence(mode)
        heating_auto_apply_count = confidence_tracker.get_auto_apply_count(None)  # HEAT mode
        cooling_auto_apply_count = confidence_tracker.get_auto_apply_count(get_hvac_cool_mode())

        # Check 1: Skip if in validation mode (validating previous auto-apply)
        if validation_manager.is_in_validation_mode():
            _LOGGER.debug("Auto-apply blocked: currently in validation mode, waiting for validation to complete")
            return False, None, None, None

        # Check 2: Safety limits (lifetime, seasonal, drift, shift cooldown)
        limit_msg = validation_manager.check_auto_apply_limits(
            current_kp, current_ki, current_kd, heating_auto_apply_count, cooling_auto_apply_count, pid_history
        )
        if limit_msg:
            _LOGGER.warning(f"Auto-apply blocked: {limit_msg}")
            return False, None, None, None

        # Check 3: Seasonal shift detection
        if outdoor_temp is not None and validation_manager.check_seasonal_shift(outdoor_temp):
            from ..const import SEASONAL_SHIFT_BLOCK_DAYS

            validation_manager.record_seasonal_shift()
            _LOGGER.warning(
                "Auto-apply blocked: seasonal temperature shift detected, "
                f"blocking for {SEASONAL_SHIFT_BLOCK_DAYS} days"
            )
            return False, None, None, None

        # Get heating-type-specific thresholds
        thresholds = get_auto_apply_thresholds(self._heating_type)

        # Check 4: Learning status tier requirement (first apply vs subsequent)
        # Get cycle count from confidence tracker
        cycle_count = confidence_tracker.get_cycle_count(mode)

        # Compute learning status based on tier thresholds and recovery cycle gates
        learning_status = self._compute_learning_status(
            cycle_count,
            convergence_confidence,
            self._heating_type,
            contribution_tracker=contribution_tracker,
            mode=mode,
        )

        # First auto-apply requires "tuned" or "optimized"
        # Subsequent auto-applies require "optimized"
        if auto_apply_count == 0:
            required_statuses = ("tuned", "optimized")
            tier_description = "tuned (tier 2) or optimized (tier 3)"
        else:
            required_statuses = ("optimized",)
            tier_description = "optimized (tier 3)"

        if learning_status not in required_statuses:
            _LOGGER.debug(
                f"Auto-apply blocked: learning_status={learning_status}, "
                f"required={tier_description} "
                f"(heating_type={self._heating_type}, mode={mode_to_str(mode)}, "
                f"apply_count={auto_apply_count}, confidence={convergence_confidence:.2f})"
            )
            return False, None, None, None

        # All gates passed - calculate adjusted parameters
        min_interval_hours = thresholds["cooldown_hours"]
        min_adjustment_cycles = thresholds["cooldown_cycles"]
        min_cycles = thresholds["min_cycles"]

        # Subsequent learning requires more cycles for higher confidence
        if auto_apply_count > 0:
            min_cycles = int(min_cycles * SUBSEQUENT_LEARNING_CYCLE_MULTIPLIER)

        _LOGGER.debug(
            f"Auto-apply checks passed: learning_status={learning_status}, "
            f"confidence={convergence_confidence:.2f}, heating_type={self._heating_type}, "
            f"mode={mode_to_str(mode)}, min_cycles={min_cycles} (apply_count={auto_apply_count})"
        )

        return True, min_interval_hours, min_adjustment_cycles, min_cycles
