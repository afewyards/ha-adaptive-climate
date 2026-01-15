"""PWM tuning utilities for Adaptive Thermostat."""

from typing import List, Optional
import statistics
import logging

_LOGGER = logging.getLogger(__name__)


def calculate_pwm_adjustment(
    cycle_times: List[float],
    current_pwm_period: float,
    min_pwm_period: float = 180.0,
    max_pwm_period: float = 1800.0,
    short_cycle_threshold: float = 10.0,
) -> Optional[float]:
    """
    Calculate PWM period adjustment based on observed cycling behavior.

    Detects short cycling and increases PWM period to reduce valve wear.
    Short cycling occurs when the heating switches on/off too frequently.

    Args:
        cycle_times: List of cycle times in minutes
        current_pwm_period: Current PWM period in seconds
        min_pwm_period: Minimum allowed PWM period in seconds (default 180s = 3 min)
        max_pwm_period: Maximum allowed PWM period in seconds (default 1800s = 30 min)
        short_cycle_threshold: Threshold for short cycling in minutes (default 10 min)

    Returns:
        Recommended PWM period in seconds, or None if insufficient data
    """
    if not cycle_times or len(cycle_times) < 3:
        return None

    # Calculate average cycle time
    avg_cycle_time = statistics.mean(cycle_times)

    # Check for short cycling
    if avg_cycle_time < short_cycle_threshold:
        # Increase PWM period to reduce cycling
        # Increase by 20% for each minute below threshold
        shortage = short_cycle_threshold - avg_cycle_time
        increase_factor = 1.0 + (shortage / short_cycle_threshold) * 0.2
        new_pwm_period = current_pwm_period * increase_factor

        # Enforce bounds
        new_pwm_period = max(min_pwm_period, min(max_pwm_period, new_pwm_period))

        _LOGGER.info(
            f"Short cycling detected (avg {avg_cycle_time:.1f} min): "
            f"increasing PWM period from {current_pwm_period:.0f}s to {new_pwm_period:.0f}s"
        )

        return new_pwm_period

    # If cycles are acceptable, keep current PWM period
    return current_pwm_period


class ValveCycleTracker:
    """Track valve cycles for wear monitoring."""

    def __init__(self):
        """Initialize the ValveCycleTracker."""
        self._cycle_count: int = 0
        self._last_state: Optional[bool] = None

    def update(self, valve_open: bool) -> None:
        """
        Update valve state and count cycles.

        A cycle is counted each time the valve transitions from closed to open.

        Args:
            valve_open: Current valve state (True = open, False = closed)
        """
        if self._last_state is not None and not self._last_state and valve_open:
            # Transition from closed to open
            self._cycle_count += 1

        self._last_state = valve_open

    def get_cycle_count(self) -> int:
        """
        Get total number of valve cycles.

        Returns:
            Total cycle count
        """
        return self._cycle_count

    def reset(self) -> None:
        """Reset cycle counter."""
        self._cycle_count = 0
        self._last_state = None
