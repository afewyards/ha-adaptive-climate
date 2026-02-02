"""Tests for HeatingRateLearner."""
import pytest
from datetime import datetime, timezone

from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateObservation,
    RecoverySession,
    HeatingRateLearner,
)
from custom_components.adaptive_climate.const import HeatingType


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


class TestBinning:
    """Tests for observation binning."""

    def test_get_bin_key_delta_0_2_cold(self):
        """Test bin key for small delta, cold outdoor."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        key = learner._get_bin_key(delta=1.5, outdoor_temp=3.0)
        assert key == "delta_0_2_cold"

    def test_get_bin_key_delta_4_6_mild(self):
        """Test bin key for medium delta, mild outdoor."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        key = learner._get_bin_key(delta=5.0, outdoor_temp=10.0)
        assert key == "delta_4_6_mild"

    def test_get_bin_key_delta_6_plus_moderate(self):
        """Test bin key for large delta, warm outdoor."""
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        key = learner._get_bin_key(delta=8.0, outdoor_temp=18.0)
        assert key == "delta_6_plus_moderate"

    def test_all_12_bins_exist(self):
        """Test learner initializes all 12 bins."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        assert len(learner._bins) == 12
        assert "delta_0_2_cold" in learner._bins
        assert "delta_6_plus_moderate" in learner._bins


class TestAddObservation:
    """Tests for adding observations to bins."""

    def test_add_observation_to_correct_bin(self):
        """Test observation lands in correct bin."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        learner.add_observation(
            rate=0.5,
            duration_min=60.0,
            source="session",
            stalled=False,
            delta=3.0,
            outdoor_temp=8.0,
        )
        assert len(learner._bins["delta_2_4_mild"]) == 1
        assert learner._bins["delta_2_4_mild"][0].rate == 0.5

    def test_max_observations_per_bin(self):
        """Test bin is capped at MAX_OBSERVATIONS_PER_BIN."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        for i in range(25):
            learner.add_observation(
                rate=0.1 * i,
                duration_min=60.0,
                source="session",
                stalled=False,
                delta=1.0,
                outdoor_temp=3.0,
            )
        assert len(learner._bins["delta_0_2_cold"]) == 20
        # Oldest should be dropped, newest kept
        assert learner._bins["delta_0_2_cold"][-1].rate == pytest.approx(2.4)

    def test_get_observation_count(self):
        """Test total observation count across all bins."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        learner.add_observation(rate=0.5, duration_min=60, source="session", stalled=False, delta=1.0, outdoor_temp=3.0)
        learner.add_observation(rate=0.6, duration_min=60, source="session", stalled=False, delta=3.0, outdoor_temp=10.0)
        assert learner.get_observation_count() == 2
