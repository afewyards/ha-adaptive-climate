"""Per-cycle samples taken alongside each temperature reading."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime


class CycleSamples:
    """Outdoor, heat-below and output-saturation samples for the cycle in progress."""

    def __init__(
        self,
        get_outdoor_temp: Callable[[], float | None] | None = None,
        get_heat_below: Callable[[], bool | None] | None = None,
        get_output_saturated: Callable[[], bool] | None = None,
    ) -> None:
        self._get_outdoor_temp = get_outdoor_temp
        self._get_heat_below = get_heat_below
        self._get_output_saturated = get_output_saturated
        self.outdoor: deque[tuple[datetime, float]] = deque(maxlen=500)
        self.heat_below: deque[tuple[datetime, bool | None]] = deque(maxlen=5000)
        self._saturated: deque[bool] = deque(maxlen=5000)

    def take(self, timestamp: datetime) -> None:
        if self._get_outdoor_temp is not None:
            outdoor_temp = self._get_outdoor_temp()
            if outdoor_temp is not None:
                self.outdoor.append((timestamp, outdoor_temp))
        if self._get_heat_below is not None:
            self.heat_below.append((timestamp, self._get_heat_below()))
        if self._get_output_saturated is not None:
            self._saturated.append(self._get_output_saturated())

    def clear(self) -> None:
        self.outdoor.clear()
        self.heat_below.clear()
        self._saturated.clear()

    def saturated_share(self) -> float | None:
        """Share (0-1) of samples with the output saturated, None without samples."""
        if not self._saturated:
            return None
        return sum(self._saturated) / len(self._saturated)
