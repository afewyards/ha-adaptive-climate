"""Tests for HeatingRateLearner."""
import pytest
from datetime import datetime, timezone

from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateObservation,
    RecoverySession,
    HeatingRateLearner,
    MIN_SESSION_DURATION,
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


class TestGetHeatingRate:
    """Tests for querying learned heating rate."""

    def test_get_rate_from_session_observations(self):
        """Test returns average rate from session observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        # Add 3 session observations
        for rate in [0.4, 0.5, 0.6]:
            learner.add_observation(rate=rate, duration_min=60, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.5, outdoor_temp=10.0)
        assert rate == pytest.approx(0.5)
        assert source == "learned_session"

    def test_get_rate_prefers_session_over_cycle(self):
        """Test session observations preferred over cycle."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        # Add cycle observations
        for rate in [0.3, 0.3, 0.3]:
            learner.add_observation(rate=rate, duration_min=30, source="cycle", stalled=False, delta=3.0, outdoor_temp=8.0)
        # Add session observations
        for rate in [0.5, 0.5, 0.5]:
            learner.add_observation(rate=rate, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert rate == pytest.approx(0.5)
        assert source == "learned_session"

    def test_get_rate_falls_back_to_cycle(self):
        """Test falls back to cycle when <3 session observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        # Add only 2 session observations (not enough)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)
        # Add 3 cycle observations
        for rate in [0.3, 0.3, 0.3]:
            learner.add_observation(rate=rate, duration_min=30, source="cycle", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert rate == pytest.approx(0.3)
        assert source == "learned_cycle"

    def test_get_rate_returns_fallback(self):
        """Test returns fallback when insufficient data."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert source == "fallback"
        assert rate > 0  # Should return some fallback rate

    def test_min_observations_for_learned_rate(self):
        """Test requires 3 observations for learned rate."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert source == "fallback"  # Only 2 observations, not enough


class TestSessionTracking:
    """Tests for recovery session tracking."""

    def test_start_session_creates_active_session(self):
        """Test start_session creates tracking state."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(
            temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now
        )

        assert learner._active_session is not None
        assert learner._active_session.start_temp == 18.0
        assert learner._active_session.target_setpoint == 21.0
        assert learner._active_session.outdoor_temp == 5.0

    def test_end_session_success_banks_observation(self):
        """Test successful session banks rate observation."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 10, 45, tzinfo=timezone.utc)  # 45 min

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(
            end_temp=20.8, reason="reached_setpoint", timestamp=end
        )

        assert obs is not None
        # Rate = (20.8 - 18.0) / (45/60) = 2.8 / 0.75 = 3.73 C/h
        assert obs.rate == pytest.approx(3.73, rel=0.01)
        assert obs.stalled is False
        assert learner._active_session is None
        assert learner.get_observation_count() == 1

    def test_end_session_stalled_banks_observation(self):
        """Test stalled session banks observation with stalled=True."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)  # 60 min

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=19.5, reason="stalled", timestamp=end)

        assert obs is not None
        assert obs.stalled is True
        # Rate = (19.5 - 18.0) / 1.0 = 1.5 C/h
        assert obs.rate == pytest.approx(1.5)

    def test_end_session_too_short_discards(self):
        """Test session shorter than minimum is discarded."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 10, 15, tzinfo=timezone.utc)  # 15 min (radiator min is 30)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=19.0, reason="reached_setpoint", timestamp=end)

        assert obs is None  # Discarded
        assert learner.get_observation_count() == 0

    def test_end_session_override_discards(self):
        """Test session interrupted by override is discarded."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=19.0, reason="override", timestamp=end)

        assert obs is None
        assert learner.get_observation_count() == 0

    def test_min_session_duration_by_heating_type(self):
        """Test minimum duration varies by heating type."""
        assert MIN_SESSION_DURATION["floor_hydronic"] == 60
        assert MIN_SESSION_DURATION["radiator"] == 30
        assert MIN_SESSION_DURATION["convector"] == 15
        assert MIN_SESSION_DURATION["forced_air"] == 10


class TestSessionUpdates:
    """Tests for session progress tracking."""

    def test_update_session_tracks_progress(self):
        """Test update_session records cycle data."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.5, duty=0.75)

        assert learner._active_session.cycles_in_session == 1
        assert learner._active_session.cycle_duties == [0.75]
        assert learner._active_session.last_temp == 18.5
        assert learner._active_session.last_progress_cycle == 1

    def test_update_session_detects_no_progress(self):
        """Test stall detection when temp doesn't rise."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.05, duty=0.75)  # <0.1 rise = no progress
        learner.update_session(temp=18.08, duty=0.75)  # still no progress
        learner.update_session(temp=18.09, duty=0.75)  # still no progress

        # last_progress_cycle should still be 0 (no progress recorded)
        assert learner._active_session.last_progress_cycle == 0
        assert learner._active_session.cycles_in_session == 3

    def test_is_stalled_after_3_no_progress_cycles(self):
        """Test is_stalled returns True after 3 cycles without progress."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        assert learner.is_stalled() is False

        learner.update_session(temp=18.05, duty=0.75)
        assert learner.is_stalled() is False

        learner.update_session(temp=18.08, duty=0.75)
        assert learner.is_stalled() is False

        learner.update_session(temp=18.09, duty=0.75)
        assert learner.is_stalled() is True  # 3 cycles with no progress

    def test_progress_resets_stall_detection(self):
        """Test making progress resets the stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.05, duty=0.75)  # no progress
        learner.update_session(temp=18.08, duty=0.75)  # no progress
        learner.update_session(temp=18.3, duty=0.75)   # progress! (0.22 rise)

        assert learner._active_session.last_progress_cycle == 3
        assert learner.is_stalled() is False

    def test_get_avg_session_duty(self):
        """Test calculating average duty for session."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.5, duty=0.70)
        learner.update_session(temp=19.0, duty=0.80)
        learner.update_session(temp=19.5, duty=0.90)

        assert learner.get_avg_session_duty() == pytest.approx(0.80)
