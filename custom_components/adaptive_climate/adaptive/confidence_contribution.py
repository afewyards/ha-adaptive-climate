"""Confidence contribution tracking for weighted learning."""

from __future__ import annotations

from ..const import (
    HeatingType,
    MAINTENANCE_CONFIDENCE_CAP,
    HEATING_RATE_CONFIDENCE_CAP,
    RECOVERY_CYCLES_FOR_TIER1,
    RECOVERY_CYCLES_FOR_TIER2,
    MAINTENANCE_DIMINISHING_RATE,
)


class ConfidenceContributionTracker:
    """Tracks confidence contributions from different sources with caps."""

    def __init__(self, heating_type: HeatingType) -> None:
        """Initialize tracker.

        Args:
            heating_type: The heating type for this zone.
        """
        self._heating_type = heating_type
        self._maintenance_contribution: float = 0.0
        self._heating_rate_contribution: float = 0.0
        self._recovery_cycle_count: int = 0

    @property
    def maintenance_contribution(self) -> float:
        """Current maintenance confidence contribution."""
        return self._maintenance_contribution

    @property
    def heating_rate_contribution(self) -> float:
        """Current heating rate confidence contribution."""
        return self._heating_rate_contribution

    @property
    def recovery_cycle_count(self) -> int:
        """Number of recovery cycles completed."""
        return self._recovery_cycle_count

    def apply_maintenance_gain(self, gain: float) -> float:
        """Apply maintenance confidence gain with cap and diminishing returns.

        Args:
            gain: Raw confidence gain to apply.

        Returns:
            Actual gain after cap and diminishing returns.
        """
        cap = MAINTENANCE_CONFIDENCE_CAP[self._heating_type]

        if self._maintenance_contribution >= cap:
            # Already at cap - diminishing returns
            actual_gain = gain * MAINTENANCE_DIMINISHING_RATE
            self._maintenance_contribution += actual_gain
            return actual_gain

        # Room under cap
        room = cap - self._maintenance_contribution
        if gain <= room:
            # Fully under cap
            self._maintenance_contribution += gain
            return gain

        # Crosses cap - split the gain
        under_cap_gain = room
        over_cap_gain = (gain - room) * MAINTENANCE_DIMINISHING_RATE
        actual_gain = under_cap_gain + over_cap_gain
        self._maintenance_contribution += actual_gain
        return actual_gain

    def apply_heating_rate_gain(self, gain: float) -> float:
        """Apply heating rate confidence gain with hard cap.

        Args:
            gain: Raw confidence gain to apply.

        Returns:
            Actual gain after cap.
        """
        cap = HEATING_RATE_CONFIDENCE_CAP[self._heating_type]
        room = cap - self._heating_rate_contribution

        if room <= 0:
            return 0.0

        actual_gain = min(gain, room)
        self._heating_rate_contribution += actual_gain
        return actual_gain

    def add_recovery_cycle(self) -> None:
        """Record a completed recovery cycle."""
        self._recovery_cycle_count += 1

    def can_reach_tier(self, tier: int) -> bool:
        """Check if tier can be reached based on recovery cycle count.

        Args:
            tier: Tier number (1 or 2).

        Returns:
            True if enough recovery cycles for tier.
        """
        if tier == 1:
            required = RECOVERY_CYCLES_FOR_TIER1[self._heating_type]
        elif tier == 2:
            required = RECOVERY_CYCLES_FOR_TIER2[self._heating_type]
        else:
            return True  # Tier 3+ has no cycle requirement

        return self._recovery_cycle_count >= required

    def to_dict(self) -> dict:
        """Serialize to dict for persistence.

        Returns:
            Dict with contribution values.
        """
        return {
            "maintenance_contribution": self._maintenance_contribution,
            "heating_rate_contribution": self._heating_rate_contribution,
            "recovery_cycle_count": self._recovery_cycle_count,
        }

    @classmethod
    def from_dict(
        cls, data: dict, heating_type: HeatingType
    ) -> ConfidenceContributionTracker:
        """Deserialize from dict.

        Args:
            data: Dict with contribution values (may be empty).
            heating_type: The heating type for this zone.

        Returns:
            Initialized tracker.
        """
        tracker = cls(heating_type)
        tracker._maintenance_contribution = data.get("maintenance_contribution", 0.0)
        tracker._heating_rate_contribution = data.get("heating_rate_contribution", 0.0)
        tracker._recovery_cycle_count = data.get("recovery_cycle_count", 0)
        return tracker
