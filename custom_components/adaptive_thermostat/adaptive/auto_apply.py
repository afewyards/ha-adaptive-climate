"""Auto-apply safety gates and threshold management for Adaptive Thermostat.

This module contains logic for automatic PID adjustment application, including:
- Heating-type-specific confidence thresholds
- Safety gates (validation mode, limits, seasonal shifts)
- Auto-apply decision orchestration
"""

from typing import Dict, List, Optional, Any, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode

from ..const import (
    AUTO_APPLY_THRESHOLDS,
    SUBSEQUENT_LEARNING_CYCLE_MULTIPLIER,
    HeatingType,
)

from ..helpers.hvac_mode import mode_to_str

_LOGGER = logging.getLogger(__name__)


def get_auto_apply_thresholds(heating_type: Optional[str] = None) -> Dict[str, float]:
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

    def __init__(self, heating_type: Optional[str] = None):
        """Initialize the AutoApplyManager.

        Args:
            heating_type: Heating system type for threshold selection
        """
        self._heating_type = heating_type

    def check_auto_apply_safety_gates(
        self,
        validation_manager: Any,  # ValidationManager
        confidence_tracker: Any,  # ConfidenceTracker
        current_kp: float,
        current_ki: float,
        current_kd: float,
        outdoor_temp: Optional[float],
        pid_history: List[Dict[str, Any]],
        mode: "HVACMode" = None,
    ) -> tuple[bool, Optional[int], Optional[int], Optional[int]]:
        """Check all auto-apply safety gates and return adjusted parameters.

        This method performs a comprehensive safety check before auto-applying
        PID adjustments. It enforces:
        1. Validation mode check (skip if validating previous auto-apply)
        2. Safety limits (lifetime, seasonal, drift, shift cooldown)
        3. Seasonal shift detection
        4. Confidence threshold (first apply vs subsequent)

        Args:
            validation_manager: ValidationManager instance for safety checks
            confidence_tracker: ConfidenceTracker for confidence/count tracking
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            outdoor_temp: Current outdoor temperature for seasonal shift detection
            pid_history: List of PID history snapshots
            mode: HVACMode (HEAT or COOL) for mode-specific thresholds

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
            _LOGGER.debug(
                "Auto-apply blocked: currently in validation mode, "
                "waiting for validation to complete"
            )
            return False, None, None, None

        # Check 2: Safety limits (lifetime, seasonal, drift, shift cooldown)
        limit_msg = validation_manager.check_auto_apply_limits(
            current_kp, current_ki, current_kd,
            heating_auto_apply_count,
            cooling_auto_apply_count,
            pid_history
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

        # Check 4: Confidence threshold (first apply vs subsequent)
        confidence_threshold = (
            thresholds["confidence_first"]
            if auto_apply_count == 0
            else thresholds["confidence_subsequent"]
        )
        if convergence_confidence < confidence_threshold:
            _LOGGER.debug(
                f"Auto-apply blocked: confidence {convergence_confidence:.2f} "
                f"< threshold {confidence_threshold:.2f} "
                f"(heating_type={self._heating_type}, mode={mode_to_str(mode)}, "
                f"apply_count={auto_apply_count})"
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
            f"Auto-apply checks passed: confidence={convergence_confidence:.2f}, "
            f"threshold={confidence_threshold:.2f}, heating_type={self._heating_type}, "
            f"mode={mode_to_str(mode)}, min_cycles={min_cycles} (apply_count={auto_apply_count})"
        )

        return True, min_interval_hours, min_adjustment_cycles, min_cycles
