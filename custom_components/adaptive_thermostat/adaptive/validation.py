"""Validation and safety checks for auto-applied PID adjustments."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import statistics
import logging

from homeassistant.util import dt as dt_util

from .cycle_analysis import CycleMetrics

from ..const import (
    VALIDATION_CYCLE_COUNT,
    VALIDATION_DEGRADATION_THRESHOLD,
    MAX_AUTO_APPLIES_PER_SEASON,
    MAX_AUTO_APPLIES_LIFETIME,
    MAX_CUMULATIVE_DRIFT_PCT,
    SEASONAL_SHIFT_BLOCK_DAYS,
)

_LOGGER = logging.getLogger(__name__)


class ValidationManager:
    """Manages validation mode and safety checks for auto-applied PID adjustments.

    Validation mode monitors cycles after auto-apply to detect performance degradation.
    Safety checks enforce limits on auto-apply frequency and cumulative drift.
    """

    def __init__(self):
        """Initialize validation manager with clean state."""
        # Validation mode state
        self._validation_mode: bool = False
        self._validation_baseline_overshoot: Optional[float] = None
        self._validation_cycles: List[CycleMetrics] = []

        # Seasonal tracking
        self._last_seasonal_check: Optional[datetime] = None
        self._last_seasonal_shift: Optional[datetime] = None
        self._outdoor_temp_history: List[float] = []

        # Physics baseline for drift calculation
        self._physics_baseline_kp: Optional[float] = None
        self._physics_baseline_ki: Optional[float] = None
        self._physics_baseline_kd: Optional[float] = None

    def start_validation_mode(self, baseline_overshoot: float) -> None:
        """Start validation mode after auto-applying PID changes.

        Validation mode monitors the next VALIDATION_CYCLE_COUNT cycles
        to verify the auto-applied changes don't degrade performance.

        Args:
            baseline_overshoot: Average overshoot from cycles before auto-apply,
                               used as reference for degradation detection.
        """
        self._validation_mode = True
        self._validation_baseline_overshoot = baseline_overshoot
        self._validation_cycles = []
        _LOGGER.info(
            "Validation mode started: monitoring next %d cycles "
            "(baseline overshoot: %.2f°C)",
            VALIDATION_CYCLE_COUNT,
            baseline_overshoot,
        )

    def add_validation_cycle(self, metrics: CycleMetrics) -> Optional[str]:
        """Add a cycle to validation tracking and check for completion.

        Collects cycles during validation mode and evaluates performance
        once VALIDATION_CYCLE_COUNT cycles have been recorded.

        Args:
            metrics: CycleMetrics from the completed cycle

        Returns:
            None if still collecting cycles,
            'success' if validation passed (performance maintained or improved),
            'rollback' if validation failed (significant degradation detected).
        """
        if not self._validation_mode:
            return None

        self._validation_cycles.append(metrics)
        _LOGGER.debug(
            "Validation cycle %d/%d added (overshoot: %.2f°C)",
            len(self._validation_cycles),
            VALIDATION_CYCLE_COUNT,
            metrics.overshoot if metrics.overshoot is not None else 0.0,
        )

        # Still collecting cycles
        if len(self._validation_cycles) < VALIDATION_CYCLE_COUNT:
            return None

        # Validation complete - calculate average overshoot
        overshoot_values = [
            c.overshoot for c in self._validation_cycles if c.overshoot is not None
        ]

        if not overshoot_values:
            _LOGGER.warning(
                "Validation complete but no overshoot data - assuming success"
            )
            self._validation_mode = False
            return "success"

        avg_overshoot = statistics.mean(overshoot_values)
        baseline = self._validation_baseline_overshoot or 0.1  # Avoid division by zero

        # Calculate degradation as percentage increase from baseline
        # Use max(baseline, 0.1) to handle very small baseline values
        degradation_pct = (avg_overshoot - baseline) / max(baseline, 0.1)

        if degradation_pct > VALIDATION_DEGRADATION_THRESHOLD:
            _LOGGER.warning(
                "Validation FAILED: overshoot degraded %.1f%% "
                "(baseline: %.2f°C, validation avg: %.2f°C, threshold: %.0f%%)",
                degradation_pct * 100,
                baseline,
                avg_overshoot,
                VALIDATION_DEGRADATION_THRESHOLD * 100,
            )
            self._validation_mode = False
            return "rollback"

        _LOGGER.info(
            "Validation SUCCESS: overshoot change %.1f%% within threshold "
            "(baseline: %.2f°C, validation avg: %.2f°C)",
            degradation_pct * 100,
            baseline,
            avg_overshoot,
        )
        self._validation_mode = False
        return "success"

    def is_in_validation_mode(self) -> bool:
        """Check if currently in validation mode.

        Returns:
            True if validation mode is active, False otherwise.
        """
        return self._validation_mode

    def check_auto_apply_limits(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
        heating_auto_apply_count: int,
        cooling_auto_apply_count: int,
        pid_history: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Check if auto-apply is allowed based on safety limits.

        Performs four safety checks before allowing auto-apply:
        1. Lifetime limit: Maximum total auto-applies (prevents runaway drift)
        2. Seasonal limit: Maximum auto-applies per 90-day season
        3. Drift limit: Maximum cumulative drift from physics baseline
        4. Seasonal shift block: Cooldown period after weather regime change

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            heating_auto_apply_count: Number of heating mode auto-applies
            cooling_auto_apply_count: Number of cooling mode auto-applies
            pid_history: List of PID snapshot dictionaries

        Returns:
            None if all checks pass (OK to auto-apply),
            Error message string if any check fails (blocked).
        """
        # Check 1: Lifetime limit (combined across all modes)
        total_auto_apply_count = heating_auto_apply_count + cooling_auto_apply_count
        if total_auto_apply_count >= MAX_AUTO_APPLIES_LIFETIME:
            return (
                f"Lifetime limit reached: {total_auto_apply_count} auto-applies "
                f"(max {MAX_AUTO_APPLIES_LIFETIME}). Manual review required."
            )

        # Check 2: Seasonal limit (auto-applies in last 90 days)
        now = dt_util.utcnow()
        cutoff = now - timedelta(days=90)
        recent_applies = [
            entry
            for entry in pid_history
            if entry.get("reason") == "auto_apply" and entry.get("timestamp", now) > cutoff
        ]
        if len(recent_applies) >= MAX_AUTO_APPLIES_PER_SEASON:
            return (
                f"Seasonal limit reached: {len(recent_applies)} auto-applies in last 90 days "
                f"(max {MAX_AUTO_APPLIES_PER_SEASON})."
            )

        # Check 3: Drift limit (cumulative drift from physics baseline)
        drift_pct = self.calculate_drift_from_baseline(current_kp, current_ki, current_kd)
        max_drift = MAX_CUMULATIVE_DRIFT_PCT / 100.0
        if drift_pct > max_drift:
            return (
                f"Cumulative drift limit exceeded: {drift_pct * 100:.1f}% drift from physics baseline "
                f"(max {MAX_CUMULATIVE_DRIFT_PCT}%). Consider reset_pid_to_physics service."
            )

        # Check 4: Seasonal shift block (cooldown after weather regime change)
        if self._last_seasonal_shift is not None:
            days_since_shift = (now - self._last_seasonal_shift).total_seconds() / 86400
            if days_since_shift < SEASONAL_SHIFT_BLOCK_DAYS:
                days_remaining = SEASONAL_SHIFT_BLOCK_DAYS - days_since_shift
                return (
                    f"Seasonal shift block active: {days_remaining:.1f} days remaining "
                    f"(of {SEASONAL_SHIFT_BLOCK_DAYS} day cooldown after weather regime change)."
                )

        # All checks passed
        return None

    def record_seasonal_shift(self) -> None:
        """Record that a seasonal shift has occurred.

        Sets the last_seasonal_shift timestamp to now, which starts
        the SEASONAL_SHIFT_BLOCK_DAYS cooldown period for auto-apply.
        This prevents auto-applying PID changes during weather regime transitions
        when system behavior may be unstable.
        """
        self._last_seasonal_shift = dt_util.utcnow()
        _LOGGER.warning(
            "Seasonal shift recorded - auto-apply blocked for next %d days",
            SEASONAL_SHIFT_BLOCK_DAYS,
        )

    def check_performance_degradation(
        self,
        cycle_history: List[CycleMetrics],
        baseline_window: int = 10,
    ) -> bool:
        """Check if recent performance has degraded compared to baseline.

        Compares recent cycles to earlier baseline to detect if tuning has drifted.

        Args:
            cycle_history: List of cycle metrics to analyze
            baseline_window: Number of cycles to use for baseline comparison

        Returns:
            True if performance has degraded significantly, False otherwise
        """
        if len(cycle_history) < baseline_window * 2:
            return False  # Not enough data

        # Get baseline (older cycles) and recent cycles
        baseline_cycles = cycle_history[:baseline_window]
        recent_cycles = cycle_history[-baseline_window:]

        # Calculate average overshoot for both windows
        baseline_overshoot = statistics.mean(
            [c.overshoot for c in baseline_cycles if c.overshoot is not None]
        ) if any(c.overshoot is not None for c in baseline_cycles) else 0.0

        recent_overshoot = statistics.mean(
            [c.overshoot for c in recent_cycles if c.overshoot is not None]
        ) if any(c.overshoot is not None for c in recent_cycles) else 0.0

        # Check if recent performance is significantly worse (>50% increase in overshoot)
        if recent_overshoot > baseline_overshoot * 1.5 and recent_overshoot > 0.3:
            _LOGGER.warning(
                f"Performance degradation detected: overshoot increased from "
                f"{baseline_overshoot:.2f}°C to {recent_overshoot:.2f}°C"
            )
            return True

        return False

    def check_seasonal_shift(self, outdoor_temp: Optional[float] = None) -> bool:
        """Check if outdoor temperature regime has shifted significantly.

        Detects seasonal changes (e.g., winter to spring) that may require
        re-tuning. Uses 10°C shift threshold.

        Args:
            outdoor_temp: Current outdoor temperature in °C

        Returns:
            True if significant shift detected, False otherwise
        """
        if outdoor_temp is None:
            return False

        # Add to history
        self._outdoor_temp_history.append(outdoor_temp)

        # Keep last 30 readings (roughly 15 hours at 30-min intervals)
        if len(self._outdoor_temp_history) > 30:
            self._outdoor_temp_history = self._outdoor_temp_history[-30:]

        # Need at least 10 readings to detect shift
        if len(self._outdoor_temp_history) < 10:
            return False

        # Check daily (avoid checking too frequently)
        now = dt_util.utcnow()
        if self._last_seasonal_check is not None:
            if now - self._last_seasonal_check < timedelta(days=1):
                return False

        self._last_seasonal_check = now

        # Calculate average of old vs new readings
        old_avg = statistics.mean(self._outdoor_temp_history[:10])
        new_avg = statistics.mean(self._outdoor_temp_history[-10:])

        # Detect 10°C shift
        if abs(new_avg - old_avg) >= 10.0:
            _LOGGER.warning(
                f"Seasonal shift detected: outdoor temp changed from "
                f"{old_avg:.1f}°C to {new_avg:.1f}°C"
            )
            return True

        return False

    def set_physics_baseline(self, kp: float, ki: float, kd: float) -> None:
        """Set the physics-based baseline PID values for drift calculation.

        The baseline represents the initial physics-calculated PID values.
        Used to calculate how far the adaptive tuning has drifted from
        the original physics-based estimates.

        Args:
            kp: Physics-based proportional gain
            ki: Physics-based integral gain
            kd: Physics-based derivative gain
        """
        self._physics_baseline_kp = kp
        self._physics_baseline_ki = ki
        self._physics_baseline_kd = kd
        _LOGGER.info(
            "Physics baseline set: Kp=%.2f, Ki=%.4f, Kd=%.2f",
            kp,
            ki,
            kd,
        )

    def calculate_drift_from_baseline(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
    ) -> float:
        """Calculate how far current PID values have drifted from physics baseline.

        Returns the maximum percentage drift across all three PID parameters.
        Used to enforce safety limits on cumulative adaptive changes.

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain

        Returns:
            Maximum drift as a decimal (e.g., 0.5 = 50% drift).
            Returns 0.0 if no baseline is set.
        """
        if self._physics_baseline_kp is None:
            _LOGGER.debug("No physics baseline set, drift calculation returns 0.0")
            return 0.0

        # Calculate percentage drift for each parameter
        # Use absolute difference divided by baseline value
        kp_drift = abs(current_kp - self._physics_baseline_kp) / self._physics_baseline_kp
        ki_drift = (
            abs(current_ki - self._physics_baseline_ki) / self._physics_baseline_ki
            if self._physics_baseline_ki > 0
            else 0.0
        )
        kd_drift = (
            abs(current_kd - self._physics_baseline_kd) / self._physics_baseline_kd
            if self._physics_baseline_kd > 0
            else 0.0
        )

        max_drift = max(kp_drift, ki_drift, kd_drift)
        _LOGGER.debug(
            "PID drift from baseline: Kp=%.1f%%, Ki=%.1f%%, Kd=%.1f%%, max=%.1f%%",
            kp_drift * 100,
            ki_drift * 100,
            kd_drift * 100,
            max_drift * 100,
        )
        return max_drift

    def reset_validation_state(self) -> None:
        """Reset validation mode state.

        Called when clearing history or resetting the learner.
        """
        self._validation_mode = False
        self._validation_cycles = []
