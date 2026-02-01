"""Track committed heat in hydronic pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HeatPipeline:
    """Track heat in-flight through manifold pipes.

    Attributes:
        transport_delay: Time for water to travel manifold to zone (seconds)
        valve_time: Time for valve to fully open/close (seconds)
    """

    transport_delay: float
    valve_time: float
    _valve_opened_at: float | None = field(default=None, repr=False)
    _valve_closed_at: float | None = field(default=None, repr=False)

    def valve_opened(self, at: float) -> None:
        """Record valve open time (monotonic)."""
        self._valve_opened_at = at
        self._valve_closed_at = None

    def valve_closed(self, at: float) -> None:
        """Record valve half-close time (monotonic)."""
        self._valve_closed_at = at

    def reset(self) -> None:
        """Clear valve timing state."""
        self._valve_opened_at = None
        self._valve_closed_at = None

    def committed_heat_remaining(self, now: float) -> float:
        """Calculate seconds of heat still in-flight.

        Args:
            now: Current monotonic time

        Returns:
            Seconds of heat delivery remaining in pipes
        """
        if self._valve_opened_at is None:
            return 0.0

        if self._valve_closed_at is None:
            # Valve still open - pipe filling or full
            time_open = now - self._valve_opened_at
            return min(time_open, self.transport_delay)

        # Valve closed - pipe draining
        time_since_close = now - self._valve_closed_at
        remaining = self.transport_delay - time_since_close
        return max(0.0, remaining)

    def calculate_valve_open_duration(
        self,
        requested_duty: float,
        pwm_period: float,
        committed: float,
    ) -> float:
        """Calculate how long to keep valve open this cycle.

        Args:
            requested_duty: Target duty cycle 0.0-1.0
            pwm_period: PWM period in seconds
            committed: Seconds of heat already in-flight

        Returns:
            Seconds to keep valve open (0 if committed exceeds need)
        """
        desired_heat = requested_duty * pwm_period
        needed_heat = desired_heat - committed

        if needed_heat <= 0:
            return 0.0

        # Add half valve time for close delay
        return needed_heat + (self.valve_time / 2)
