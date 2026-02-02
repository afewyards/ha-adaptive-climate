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
