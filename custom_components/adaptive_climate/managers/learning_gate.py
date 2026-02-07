"""Learning gate manager for Adaptive Climate integration.

Manages learning suppression and graduated night setback based on learning progress.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from ..const import (
    CONFIDENCE_TIER_1,
    CONFIDENCE_TIER_2,
    CONFIDENCE_TIER_3,
    HEATING_TYPE_CONFIDENCE_SCALE,
    HeatingType,
)

if TYPE_CHECKING:
    from ..adaptive.contact_sensors import ContactSensorHandler
    from ..adaptive.humidity_detector import HumidityDetector
    from ..adaptive.learning import AdaptiveLearner
    from ..managers.night_setback_manager import NightSetbackManager

_LOGGER = logging.getLogger(__name__)


class LearningGateManager:
    """Manages learning suppression and graduated night setback delta.

    Determines:
    - When learning should be suppressed (contact open, humidity spike, grace period)
    - Maximum allowed night setback delta based on learning progress

    Graduated delta approach:
    - idle (< 3 cycles): 0.0°C (fully suppressed)
    - collecting (≥ 3 cycles, < tier 1): 0.5°C (limited)
    - stable (≥ tier 1, < tier 2): 1.0°C (moderate)
    - tuned/optimized (≥ tier 2): None (unlimited)
    """

    def __init__(
        self,
        night_setback_controller: NightSetbackManager | None,
        contact_sensor_handler: ContactSensorHandler | None,
        humidity_detector: HumidityDetector | None,
        get_adaptive_learner: Callable[[], AdaptiveLearner | None],
        heating_type: HeatingType,
    ):
        """Initialize learning gate manager.

        Args:
            night_setback_controller: Night setback manager (None if not enabled)
            contact_sensor_handler: Contact sensor handler (None if not configured)
            humidity_detector: Humidity detector (None if not configured)
            get_adaptive_learner: Callable that returns adaptive learner instance
            heating_type: Heating type for confidence threshold scaling
        """
        self._night_setback_controller = night_setback_controller
        self._contact_sensor_handler = contact_sensor_handler
        self._humidity_detector = humidity_detector
        self._get_adaptive_learner = get_adaptive_learner
        self._heating_type = heating_type

        # Calculate scaled thresholds for this heating type
        scale = HEATING_TYPE_CONFIDENCE_SCALE.get(
            heating_type, HEATING_TYPE_CONFIDENCE_SCALE.get(HeatingType.CONVECTOR, 1.0)
        )
        self._scaled_tier_1 = min(CONFIDENCE_TIER_1 * scale / 100.0, 0.95)
        self._scaled_tier_2 = min(CONFIDENCE_TIER_2 * scale / 100.0, 0.95)
        self._tier_3 = CONFIDENCE_TIER_3 / 100.0  # Always 95%, not scaled

    def get_allowed_delta(self) -> float | None:
        """Return max allowed setback delta, or None if unlimited.

        Returns:
            float | None: Maximum allowed delta in °C, or None for unlimited:
                - None: Night setback not enabled
                - 0.0: Fully suppressed (contact open, humidity spike, grace period, or idle)
                - 0.5: Early learning (collecting with ≥ 3 cycles)
                - 1.0: Moderate learning (stable status)
                - None: Unlimited (tuned or optimized status)
        """
        # If night setback not enabled, return None (unlimited)
        if self._night_setback_controller is None:
            return None

        # Check suppression conditions (return 0.0 if any are true)
        if self.is_learning_suppressed():
            return 0.0

        # Get adaptive learner
        adaptive_learner = self._get_adaptive_learner()
        if adaptive_learner is None:
            return 0.0  # No learner - suppress

        # Get cycle count and convergence confidence
        cycle_count = adaptive_learner.get_cycle_count()
        convergence_confidence = adaptive_learner.get_convergence_confidence()

        # Determine allowed delta based on confidence tiers and cycle count
        if convergence_confidence >= self._tier_3:
            return None  # Optimized - unlimited
        elif convergence_confidence >= self._scaled_tier_2:
            return None  # Tuned - unlimited
        elif convergence_confidence >= self._scaled_tier_1:
            return 1.0  # Stable - 1°C allowed
        elif cycle_count >= 3:
            return 0.5  # Collecting with data - 0.5°C allowed
        else:
            return 0.0  # Collecting without data - suppressed

    def is_learning_suppressed(self) -> bool:
        """Return True if learning should be suppressed.

        Learning is suppressed when:
        - Contact sensor is open
        - Humidity spike detected
        - In learning grace period after night setback transition

        Returns:
            bool: True if learning should be suppressed
        """
        # Check learning grace period
        if self._night_setback_controller:
            try:
                if self._night_setback_controller.in_learning_grace_period:
                    return True
            except (TypeError, AttributeError):
                pass

        # Check contact sensor pause
        if self._contact_sensor_handler:
            try:
                if self._contact_sensor_handler.is_any_contact_open():
                    return True
            except (TypeError, AttributeError):
                pass

        # Check humidity pause
        if self._humidity_detector:
            try:
                if self._humidity_detector.should_pause():
                    return True
            except (TypeError, AttributeError):
                pass

        return False
