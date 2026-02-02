"""Unified heating rate learning from cycle and session data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class HeatingRateObservation:
    """Single heating rate observation."""

    rate: float  # degrees C per hour
    duration_min: float  # session/cycle duration in minutes
    source: str  # "cycle" or "session"
    stalled: bool  # True if ended without reaching setpoint
    timestamp: datetime


@dataclass
class RecoverySession:
    """Tracks an active recovery session spanning multiple cycles."""

    start_temp: float
    start_time: datetime
    target_setpoint: float
    outdoor_temp: float  # snapshot at session start
    cycles_in_session: int = 0
    cycle_duties: list[float] = field(default_factory=list)
    last_progress_cycle: int = 0
    last_temp: float | None = None  # for progress detection


# Bin boundaries
DELTA_BINS = [(0, 2), (2, 4), (4, 6), (6, float("inf"))]
DELTA_BIN_NAMES = ["delta_0_2", "delta_2_4", "delta_4_6", "delta_6_plus"]
OUTDOOR_BINS = [(-float("inf"), 5), (5, 15), (15, float("inf"))]
OUTDOOR_BIN_NAMES = ["cold", "mild", "moderate"]


class HeatingRateLearner:
    """Unified heating rate learning from cycle and session data."""

    MAX_OBSERVATIONS_PER_BIN = 20

    def __init__(self, heating_type: str) -> None:
        """Initialize learner.

        Args:
            heating_type: HeatingType enum value (e.g., "floor_hydronic")
        """
        self._heating_type = heating_type
        self._bins: dict[str, list[HeatingRateObservation]] = {}
        self._active_session: RecoverySession | None = None
        self._stall_counter: int = 0
        self._last_stall_outdoor: float | None = None
        self._last_stall_setpoint: float | None = None

        # Initialize all 12 bins
        for delta_name in DELTA_BIN_NAMES:
            for outdoor_name in OUTDOOR_BIN_NAMES:
                key = f"{delta_name}_{outdoor_name}"
                self._bins[key] = []

    def _get_bin_key(self, delta: float, outdoor_temp: float) -> str:
        """Get bin key for given delta and outdoor temp."""
        # Find delta bin
        delta_name = DELTA_BIN_NAMES[-1]  # default to largest
        for i, (low, high) in enumerate(DELTA_BINS):
            if low <= delta < high:
                delta_name = DELTA_BIN_NAMES[i]
                break

        # Find outdoor bin
        outdoor_name = OUTDOOR_BIN_NAMES[-1]  # default to warmest
        for i, (low, high) in enumerate(OUTDOOR_BINS):
            if low <= outdoor_temp < high:
                outdoor_name = OUTDOOR_BIN_NAMES[i]
                break

        return f"{delta_name}_{outdoor_name}"
