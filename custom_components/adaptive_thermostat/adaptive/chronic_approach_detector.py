"""Chronic approach failure detector for persistent inability to reach setpoint.

Detects patterns where the zone consistently fails to reach setpoint across
multiple consecutive cycles, indicating insufficient integral gain. This is
distinct from single-cycle undershoot - it's a persistent heating capacity issue.

Pattern:
    - rise_time = None (never reached setpoint during rise)
    - undershoot >= threshold (significant gap remains)
    - cycle duration >= min_duration (avoid short transient cycles)
    - consecutive across N cycles (not isolated events)

The detector tracks cumulative Ki adjustments and respects a cap to prevent
runaway integral gain.
"""
import logging
import time
from typing import Optional

from ..const import (
    CHRONIC_APPROACH_THRESHOLDS,
    HeatingType,
    MAX_UNDERSHOOT_KI_MULTIPLIER,
)
from .cycle_analysis import CycleMetrics

_LOGGER = logging.getLogger(__name__)


class ChronicApproachDetector:
    """Detects chronic inability to reach setpoint across multiple cycles."""

    def __init__(self, heating_type: HeatingType):
        """Initialize the detector.

        Args:
            heating_type: The heating system type, determines thresholds
        """
        self._heating_type = heating_type
        self._thresholds = CHRONIC_APPROACH_THRESHOLDS[heating_type]

        # Pattern tracking
        self._consecutive_failures = 0

        # Cumulative multiplier tracking
        self.cumulative_ki_multiplier = 1.0

        # Cooldown tracking
        self.last_adjustment_time: Optional[float] = None

    def add_cycle(self, cycle: CycleMetrics, cycle_duration_minutes: Optional[float] = None) -> None:
        """Add a cycle for pattern analysis.

        Args:
            cycle: The cycle metrics to analyze
            cycle_duration_minutes: Duration of the cycle in minutes (optional, for testing)
        """
        # Check if cycle meets the failure criteria
        is_failure = self._is_chronic_approach_failure(cycle, cycle_duration_minutes)

        if is_failure:
            # Increment consecutive failure count
            self._consecutive_failures += 1
        else:
            # Reset on any successful cycle (has rise_time)
            if cycle.rise_time is not None:
                self._consecutive_failures = 0

    def _is_chronic_approach_failure(
        self, cycle: CycleMetrics, cycle_duration_minutes: Optional[float] = None
    ) -> bool:
        """Check if cycle meets chronic approach failure criteria.

        Args:
            cycle: The cycle metrics to check
            cycle_duration_minutes: Duration of the cycle in minutes

        Returns:
            True if cycle represents chronic approach failure
        """
        # Must not have reached setpoint during rise
        if cycle.rise_time is not None:
            return False

        # Must have significant undershoot
        if cycle.undershoot is None or cycle.undershoot < self._thresholds["undershoot_threshold"]:
            return False

        # Must not have overshoot (can't be stuck below if you went above)
        if cycle.overshoot is not None and cycle.overshoot > 0.0:
            return False

        # Must be a substantial cycle (not a short transient)
        if cycle_duration_minutes is not None:
            if cycle_duration_minutes < self._thresholds["min_cycle_duration"]:
                return False

        return True

    def should_adjust_ki(self) -> bool:
        """Check if pattern is detected and adjustment should be applied.

        Returns:
            True if chronic approach pattern detected and adjustment allowed
        """
        # Check if we have enough consecutive failures
        if self._consecutive_failures < self._thresholds["min_cycles"]:
            return False

        # Check if in cooldown
        if self._is_in_cooldown():
            return False

        # Check if applying adjustment would exceed cap
        configured_multiplier = self._thresholds["ki_multiplier"]
        new_cumulative = self.cumulative_ki_multiplier * configured_multiplier
        if new_cumulative > MAX_UNDERSHOOT_KI_MULTIPLIER:
            return False

        return True

    def get_adjustment(self) -> float:
        """Get the Ki multiplier adjustment.

        Returns clamped multiplier to respect cumulative cap.

        Returns:
            Ki multiplier (1.0 = no adjustment, >1.0 = increase)
        """
        # Calculate how much we can increase before hitting cap
        max_allowed_multiplier = MAX_UNDERSHOOT_KI_MULTIPLIER / self.cumulative_ki_multiplier

        # Return the smaller of configured multiplier or max allowed
        configured_multiplier = self._thresholds["ki_multiplier"]
        return min(configured_multiplier, max_allowed_multiplier)

    def apply_adjustment(self) -> float:
        """Apply the adjustment and update internal state.

        Returns:
            The multiplier that was applied
        """
        multiplier = self.get_adjustment()

        # Update cumulative multiplier
        self.cumulative_ki_multiplier *= multiplier

        # Record adjustment time for cooldown
        self.last_adjustment_time = time.monotonic()

        # Reset consecutive counter (cooldown handles suppression)
        self._consecutive_failures = 0

        _LOGGER.info(
            "Applied chronic approach Ki adjustment: %.3fx (cumulative: %.3fx)",
            multiplier,
            self.cumulative_ki_multiplier,
        )

        return multiplier

    def _is_in_cooldown(self) -> bool:
        """Check if detector is in cooldown period.

        Returns:
            True if in cooldown, False otherwise
        """
        if self.last_adjustment_time is None:
            return False

        # Cooldown duration is heating-type specific
        # Use similar pattern to undershoot detection
        cooldown_hours = self._get_cooldown_hours()
        cooldown_seconds = cooldown_hours * 3600

        elapsed = time.monotonic() - self.last_adjustment_time
        return elapsed < cooldown_seconds

    def _get_cooldown_hours(self) -> float:
        """Get cooldown duration in hours for this heating type.

        Returns:
            Cooldown duration in hours
        """
        # Cooldown scales with thermal mass - slower systems need longer cooldown
        # to observe effects of Ki adjustment
        cooldown_map = {
            HeatingType.FLOOR_HYDRONIC: 24.0,  # 1 day
            HeatingType.RADIATOR: 12.0,         # 12 hours
            HeatingType.CONVECTOR: 6.0,         # 6 hours
            HeatingType.FORCED_AIR: 3.0,        # 3 hours
        }
        return cooldown_map[self._heating_type]

    def get_cumulative_multiplier(self) -> float:
        """Get the current cumulative Ki multiplier.

        Returns:
            Cumulative multiplier (1.0 = no adjustments)
        """
        return self.cumulative_ki_multiplier

    def is_in_cooldown(self) -> bool:
        """Check if detector is in cooldown period (public API).

        Returns:
            True if in cooldown, False otherwise
        """
        return self._is_in_cooldown()
