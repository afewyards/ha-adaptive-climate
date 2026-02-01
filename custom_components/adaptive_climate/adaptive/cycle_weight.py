"""Cycle weight calculation for weighted learning."""

from __future__ import annotations

from enum import Enum

from ..const import (
    HeatingType,
    RECOVERY_THRESHOLD_COLLECTING,
    RECOVERY_THRESHOLD_STABLE,
    MAINTENANCE_BASE_WEIGHT,
    RECOVERY_BASE_WEIGHT,
    DELTA_MULTIPLIER_SCALE,
    DELTA_MULTIPLIER_CAP,
    OUTCOME_FACTOR_CLEAN,
    OUTCOME_FACTOR_OVERSHOOT,
    OUTCOME_FACTOR_UNDERSHOOT,
    DUTY_BONUS,
    DUTY_BONUS_THRESHOLD,
    OUTDOOR_BONUS,
    OUTDOOR_BONUS_THRESHOLD,
    NIGHT_SETBACK_BONUS,
)


class CycleOutcome(Enum):
    """Outcome of a heating/cooling cycle."""

    CLEAN = "clean"
    OVERSHOOT = "overshoot"
    UNDERSHOOT = "undershoot"


class CycleWeightCalculator:
    """Calculates weight for cycles based on difficulty and outcome."""

    def __init__(self, heating_type: HeatingType) -> None:
        """Initialize with heating type."""
        self._heating_type = heating_type

    def get_recovery_threshold(self, is_stable: bool) -> float:
        """Get recovery threshold based on learning status.

        Args:
            is_stable: True if learning status is "stable" or higher.

        Returns:
            Threshold in °C. Cycles starting >= this delta are recovery cycles.
        """
        if is_stable:
            return RECOVERY_THRESHOLD_STABLE[self._heating_type]
        return RECOVERY_THRESHOLD_COLLECTING[self._heating_type]

    def is_recovery_cycle(self, starting_delta: float, is_stable: bool) -> bool:
        """Determine if cycle is recovery (vs maintenance).

        Args:
            starting_delta: Temperature difference from setpoint at cycle start.
            is_stable: True if learning status is "stable" or higher.

        Returns:
            True if recovery cycle, False if maintenance cycle.
        """
        threshold = self.get_recovery_threshold(is_stable)
        return starting_delta >= threshold

    def calculate_weight(
        self,
        starting_delta: float,
        is_stable: bool,
        outcome: CycleOutcome,
        effective_duty: float | None = None,
        outdoor_temp: float | None = None,
        is_night_setback_recovery: bool = False,
    ) -> float:
        """Calculate cycle weight.

        Formula: (challenge × outcome_factor) + bonuses
        Where: challenge = base_weight × delta_multiplier

        Args:
            starting_delta: Temperature difference from setpoint at cycle start.
            is_stable: True if learning status is "stable" or higher.
            outcome: Cycle outcome (clean, overshoot, undershoot).
            effective_duty: Peak duty minus committed heat ratio (0-1).
            outdoor_temp: Outdoor temperature in °C.
            is_night_setback_recovery: True if recovering from night setback.

        Returns:
            Cycle weight (typically 0.3-2.5).
        """
        # Determine base weight
        is_recovery = self.is_recovery_cycle(starting_delta, is_stable)
        base_weight = RECOVERY_BASE_WEIGHT if is_recovery else MAINTENANCE_BASE_WEIGHT

        # Calculate delta multiplier (only for recovery cycles)
        if is_recovery:
            threshold = self.get_recovery_threshold(is_stable)
            delta_multiplier = 1.0 + (starting_delta - threshold) * DELTA_MULTIPLIER_SCALE
            delta_multiplier = min(delta_multiplier, DELTA_MULTIPLIER_CAP)
        else:
            delta_multiplier = 1.0

        # Get outcome factor
        outcome_factors = {
            CycleOutcome.CLEAN: OUTCOME_FACTOR_CLEAN,
            CycleOutcome.OVERSHOOT: OUTCOME_FACTOR_OVERSHOOT,
            CycleOutcome.UNDERSHOOT: OUTCOME_FACTOR_UNDERSHOOT,
        }
        outcome_factor = outcome_factors[outcome]

        # Calculate challenge
        challenge = base_weight * delta_multiplier * outcome_factor

        # Calculate bonuses
        bonuses = 0.0

        if effective_duty is not None and effective_duty > DUTY_BONUS_THRESHOLD:
            bonuses += DUTY_BONUS

        if outdoor_temp is not None and outdoor_temp < OUTDOOR_BONUS_THRESHOLD:
            bonuses += OUTDOOR_BONUS

        if is_night_setback_recovery:
            bonuses += NIGHT_SETBACK_BONUS

        return challenge + bonuses
