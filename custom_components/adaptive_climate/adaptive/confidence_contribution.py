"""Confidence contribution tracking for weighted learning."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode

from ..const import (
    HeatingType,
    MAINTENANCE_CONFIDENCE_CAP,
    HEATING_RATE_CONFIDENCE_CAP,
    RECOVERY_CYCLES_FOR_TIER1,
    RECOVERY_CYCLES_FOR_TIER2,
    MAINTENANCE_DIMINISHING_RATE,
)
from ..helpers.hvac_mode import get_hvac_heat_mode, get_hvac_cool_mode


class ConfidenceContributionTracker:
    """Tracks confidence contributions from different sources with caps."""

    def __init__(self, heating_type: HeatingType) -> None:
        """Initialize tracker.

        Args:
            heating_type: The heating type for this zone.
        """
        self._heating_type = heating_type
        # Mode-specific tracking
        self._heating_maintenance_contribution: float = 0.0
        self._cooling_maintenance_contribution: float = 0.0
        self._heating_rate_contribution: float = 0.0
        self._heating_recovery_cycle_count: int = 0
        self._cooling_recovery_cycle_count: int = 0

    def get_maintenance_contribution(self, mode: "HVACMode" = None) -> float:
        """Get current maintenance confidence contribution for mode."""
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            return self._cooling_maintenance_contribution
        return self._heating_maintenance_contribution

    def get_heating_rate_contribution(self) -> float:
        """Current heating rate confidence contribution."""
        return self._heating_rate_contribution

    def get_recovery_cycle_count(self, mode: "HVACMode" = None) -> int:
        """Number of recovery cycles completed for mode."""
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            return self._cooling_recovery_cycle_count
        return self._heating_recovery_cycle_count

    def apply_maintenance_gain(self, gain: float, mode: "HVACMode" = None) -> float:
        """Apply maintenance confidence gain with cap and diminishing returns.

        Args:
            gain: Raw confidence gain to apply.
            mode: HVAC mode (HEAT or COOL) to apply gain to.

        Returns:
            Actual gain after cap and diminishing returns.
        """
        if mode is None:
            mode = get_hvac_heat_mode()

        cap = MAINTENANCE_CONFIDENCE_CAP[self._heating_type]

        # Get current contribution for this mode
        if mode == get_hvac_cool_mode():
            current_contribution = self._cooling_maintenance_contribution
        else:
            current_contribution = self._heating_maintenance_contribution

        if current_contribution >= cap:
            # Already at cap - diminishing returns
            actual_gain = gain * MAINTENANCE_DIMINISHING_RATE
            if mode == get_hvac_cool_mode():
                self._cooling_maintenance_contribution += actual_gain
            else:
                self._heating_maintenance_contribution += actual_gain
            return actual_gain

        # Room under cap
        room = cap - current_contribution
        if gain <= room:
            # Fully under cap
            if mode == get_hvac_cool_mode():
                self._cooling_maintenance_contribution += gain
            else:
                self._heating_maintenance_contribution += gain
            return gain

        # Crosses cap - split the gain
        under_cap_gain = room
        over_cap_gain = (gain - room) * MAINTENANCE_DIMINISHING_RATE
        actual_gain = under_cap_gain + over_cap_gain
        if mode == get_hvac_cool_mode():
            self._cooling_maintenance_contribution += actual_gain
        else:
            self._heating_maintenance_contribution += actual_gain
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

    def add_recovery_cycle(self, mode: "HVACMode" = None) -> None:
        """Record a completed recovery cycle for mode."""
        if mode is None:
            mode = get_hvac_heat_mode()
        if mode == get_hvac_cool_mode():
            self._cooling_recovery_cycle_count += 1
        else:
            self._heating_recovery_cycle_count += 1

    def can_reach_tier(self, tier: int, mode: "HVACMode" = None) -> bool:
        """Check if tier can be reached based on recovery cycle count for mode.

        Args:
            tier: Tier number (1 or 2).
            mode: HVAC mode (HEAT or COOL) to check.

        Returns:
            True if enough recovery cycles for tier.
        """
        if mode is None:
            mode = get_hvac_heat_mode()

        if tier == 1:
            required = RECOVERY_CYCLES_FOR_TIER1[self._heating_type]
        elif tier == 2:
            required = RECOVERY_CYCLES_FOR_TIER2[self._heating_type]
        else:
            return True  # Tier 3+ has no cycle requirement

        recovery_count = self.get_recovery_cycle_count(mode)
        return recovery_count >= required

    def to_dict(self) -> dict:
        """Serialize to dict for persistence.

        Returns:
            Dict with contribution values (mode-aware v9 format with backward compat).
        """
        return {
            # Backward compat (use heating values as default)
            "maintenance_contribution": self._heating_maintenance_contribution,
            "heating_rate_contribution": self._heating_rate_contribution,
            "recovery_cycle_count": self._heating_recovery_cycle_count,
            # v9 mode-specific values
            "heating_maintenance_contribution": self._heating_maintenance_contribution,
            "cooling_maintenance_contribution": self._cooling_maintenance_contribution,
            "heating_recovery_cycle_count": self._heating_recovery_cycle_count,
            "cooling_recovery_cycle_count": self._cooling_recovery_cycle_count,
        }

    @classmethod
    def from_dict(
        cls, data: dict, heating_type: HeatingType
    ) -> ConfidenceContributionTracker:
        """Deserialize from dict.

        Args:
            data: Dict with contribution values (may be empty or old format).
            heating_type: The heating type for this zone.

        Returns:
            Initialized tracker.
        """
        tracker = cls(heating_type)

        # Try v9 format first (mode-specific)
        if "heating_maintenance_contribution" in data:
            tracker._heating_maintenance_contribution = data["heating_maintenance_contribution"]
            tracker._cooling_maintenance_contribution = data.get("cooling_maintenance_contribution", 0.0)
            tracker._heating_recovery_cycle_count = data.get("heating_recovery_cycle_count", 0)
            tracker._cooling_recovery_cycle_count = data.get("cooling_recovery_cycle_count", 0)
        else:
            # Fall back to old format (apply to heating mode)
            tracker._heating_maintenance_contribution = data.get("maintenance_contribution", 0.0)
            tracker._cooling_maintenance_contribution = 0.0
            tracker._heating_recovery_cycle_count = data.get("recovery_cycle_count", 0)
            tracker._cooling_recovery_cycle_count = 0

        tracker._heating_rate_contribution = data.get("heating_rate_contribution", 0.0)
        return tracker
