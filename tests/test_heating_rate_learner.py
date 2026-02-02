"""Tests for HeatingRateLearner."""
import pytest
from datetime import datetime, timezone

from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateObservation,
    RecoverySession,
)


def test_heating_rate_observation_creation():
    """Test observation dataclass stores all fields."""
    obs = HeatingRateObservation(
        rate=0.15,
        duration_min=180.0,
        source="session",
        stalled=False,
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
    )
    assert obs.rate == 0.15
    assert obs.duration_min == 180.0
    assert obs.source == "session"
    assert obs.stalled is False


def test_recovery_session_creation():
    """Test session dataclass stores tracking state."""
    session = RecoverySession(
        start_temp=18.0,
        start_time=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
        target_setpoint=21.0,
        outdoor_temp=5.0,
    )
    assert session.start_temp == 18.0
    assert session.target_setpoint == 21.0
    assert session.cycles_in_session == 0
    assert session.cycle_duties == []
    assert session.last_progress_cycle == 0
