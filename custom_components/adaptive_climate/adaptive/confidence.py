"""Convergence confidence tracking for adaptive PID learning.

Tracks confidence in system convergence (tuning quality) on a per-mode basis
(heating vs cooling). Confidence increases with good cycles and decreases with
poor cycles. Used to scale learning rate adjustments and gate auto-apply operations.
"""

from typing import TYPE_CHECKING, Optional
import logging

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode

from ..const import (
    CONVERGENCE_CONFIDENCE_HIGH,
    CONFIDENCE_DECAY_RATE_DAILY,
    CONFIDENCE_INCREASE_PER_GOOD_CYCLE,
)
from ..helpers.hvac_mode import mode_to_str, get_hvac_heat_mode, get_hvac_cool_mode

_LOGGER = logging.getLogger(__name__)


class ConfidenceTracker:
    """Tracks convergence confidence and auto-apply counts per HVAC mode.

    Maintains separate confidence scores for heating and cooling modes,
    tracking how well the PID controller is tuned for each operating mode.
    Confidence increases with good cycles (low overshoot, oscillations, etc.)
    and decreases with poor cycles.

    Also tracks auto-apply counts per mode to enforce safety limits and
    adjust thresholds for subsequent applications.
    """

    def __init__(self, convergence_thresholds: dict):
        """Initialize the confidence tracker.

        Args:
            convergence_thresholds: Dict with threshold values for determining
                                   good vs poor cycles (overshoot_max, oscillations_max, etc.)
        """
        self._convergence_thresholds = convergence_thresholds

        # Mode-specific convergence confidence tracking
        self._heating_convergence_confidence: float = 0.0
        self._cooling_convergence_confidence: float = 0.0

        # Mode-specific auto-apply tracking state
        self._heating_auto_apply_count: int = 0
        self._cooling_auto_apply_count: int = 0

    def get_convergence_confidence(self, mode: "HVACMode" = None) -> float:
        """Get current convergence confidence level for specified mode.

        Args:
            mode: HVACMode (HEAT or COOL) to check (defaults to HEAT)

        Returns:
            Confidence in range [0.0, 1.0]
        """
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            return self._cooling_convergence_confidence
        else:
            return self._heating_convergence_confidence

    def get_auto_apply_count(self, mode: "HVACMode" = None) -> int:
        """Get number of times PID has been auto-applied for specified mode.

        Args:
            mode: HVACMode (HEAT or COOL) to check (defaults to HEAT)

        Returns:
            Total count of auto-applied PID adjustments for the specified mode.
        """
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            return self._cooling_auto_apply_count
        else:
            return self._heating_auto_apply_count

    def update_convergence_confidence(self, metrics, mode: "HVACMode" = None) -> None:
        """Update convergence confidence based on cycle performance.

        Confidence increases when cycles meet convergence criteria, and is used
        to scale learning rate adjustments.

        Args:
            metrics: CycleMetrics from the latest completed cycle
            mode: HVACMode (HEAT or COOL) to update (defaults to HEAT)
        """
        if mode is None:
            mode = get_hvac_heat_mode()
        # Select confidence value based on mode
        if mode == get_hvac_cool_mode():
            current_confidence = self._cooling_convergence_confidence
        else:
            current_confidence = self._heating_convergence_confidence

        # Check if this cycle meets convergence criteria
        is_good_cycle = (
            (metrics.overshoot is None or metrics.overshoot <= self._convergence_thresholds["overshoot_max"]) and
            (metrics.undershoot is None or metrics.undershoot <= self._convergence_thresholds.get("undershoot_max", 0.2)) and
            metrics.oscillations <= self._convergence_thresholds["oscillations_max"] and
            (metrics.settling_time is None or metrics.settling_time <= self._convergence_thresholds["settling_time_max"]) and
            (metrics.rise_time is None or metrics.rise_time <= self._convergence_thresholds["rise_time_max"])
        )

        if is_good_cycle:
            # Increase confidence, capped at maximum
            current_confidence = min(
                CONVERGENCE_CONFIDENCE_HIGH,
                current_confidence + CONFIDENCE_INCREASE_PER_GOOD_CYCLE
            )
            _LOGGER.debug(
                f"Convergence confidence ({mode_to_str(mode)} mode) increased to {current_confidence:.2f} "
                f"(good cycle: overshoot={metrics.overshoot:.2f}°C, "
                f"oscillations={metrics.oscillations}, "
                f"settling={metrics.settling_time:.1f}min)"
            )
        else:
            # Poor cycle - reduce confidence slightly
            current_confidence = max(
                0.0,
                current_confidence - CONFIDENCE_INCREASE_PER_GOOD_CYCLE * 0.5
            )
            _LOGGER.debug(
                f"Convergence confidence ({mode_to_str(mode)} mode) decreased to {current_confidence:.2f} "
                f"(poor cycle detected)"
            )

        # Store updated confidence back
        if mode == get_hvac_cool_mode():
            self._cooling_convergence_confidence = current_confidence
        else:
            self._heating_convergence_confidence = current_confidence

    def apply_confidence_decay(self) -> None:
        """Apply daily confidence decay to account for drift over time.

        Reduces confidence by CONFIDENCE_DECAY_RATE_DAILY (2% per day).
        Call this once per day or on each cycle with appropriate scaling.
        Decays both heating and cooling mode confidence.
        """
        # Decay heating confidence
        self._heating_convergence_confidence = max(
            0.0,
            self._heating_convergence_confidence * (1.0 - CONFIDENCE_DECAY_RATE_DAILY)
        )
        # Decay cooling confidence
        self._cooling_convergence_confidence = max(
            0.0,
            self._cooling_convergence_confidence * (1.0 - CONFIDENCE_DECAY_RATE_DAILY)
        )

    def get_learning_rate_multiplier(self, confidence: Optional[float] = None) -> float:
        """Get learning rate multiplier based on convergence confidence.

        Args:
            confidence: Optional confidence value to use. If None, uses heating mode confidence.

        Returns:
            Multiplier in range [0.5, 2.0]:
            - Low confidence (0.0): 2.0x faster learning
            - High confidence (1.0): 0.5x slower learning
        """
        if confidence is None:
            confidence = self._heating_convergence_confidence
        # Low confidence = faster learning (larger adjustments)
        # High confidence = slower learning (smaller adjustments)
        # Linear interpolation from 2.0 (confidence=0) to 0.5 (confidence=1)
        multiplier = 2.0 - (confidence * 1.5)
        return max(0.5, min(2.0, multiplier))

    def increment_auto_apply_count(self, mode: "HVACMode" = None) -> None:
        """Increment the auto-apply count for the specified mode.

        Args:
            mode: HVACMode (HEAT or COOL) to increment (defaults to HEAT)
        """
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            self._cooling_auto_apply_count += 1
        else:
            self._heating_auto_apply_count += 1

    def reset_confidence(self, mode: "HVACMode" = None) -> None:
        """Reset confidence to zero for specified mode.

        Args:
            mode: HVACMode (HEAT or COOL) to reset, or None to reset both modes
        """
        if mode is None:
            # Reset both modes
            self._heating_convergence_confidence = 0.0
            self._cooling_convergence_confidence = 0.0
        elif mode == get_hvac_cool_mode():
            self._cooling_convergence_confidence = 0.0
        else:
            self._heating_convergence_confidence = 0.0
