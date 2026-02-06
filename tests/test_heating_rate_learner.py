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


class TestStallCounter:
    """Tests for consecutive stall tracking and Ki boost trigger."""

    def test_stall_increments_counter(self):
        """Test stalled session increments stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner._stall_counter == 1

    def test_success_resets_counter(self):
        """Test successful session resets stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        # First session stalls
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)
        assert learner._stall_counter == 1

        # Second session succeeds
        start2 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start2)
        learner.end_session(end_temp=20.8, reason="reached_setpoint", timestamp=end2)

        assert learner._stall_counter == 0

    def test_outdoor_change_resets_counter(self):
        """Test significant outdoor temp change resets stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        # First stall at outdoor=5
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)
        assert learner._stall_counter == 1

        # Second stall at outdoor=-2 (>5 degree change)
        start2 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=-2.0, timestamp=start2)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end2)

        # Counter reset due to outdoor change, then incremented
        assert learner._stall_counter == 1

    def test_setpoint_change_resets_counter(self):
        """Test significant setpoint change resets stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        # First stall at setpoint=21
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        # Second stall at setpoint=23 (>1 degree change)
        start2 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        learner.start_session(temp=18.0, setpoint=23.0, outdoor_temp=5.0, timestamp=start2)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end2)

        assert learner._stall_counter == 1  # Reset then incremented

    def test_should_boost_ki_after_2_stalls_with_low_duty(self):
        """Test Ki boost triggered after 2 consecutive stalls with headroom."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for i in range(2):
            start = datetime(2026, 1, 15, 10 + i * 2, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 15, 11 + i * 2, 0, tzinfo=timezone.utc)
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
            learner.update_session(temp=18.5, duty=0.60)  # Low duty
            learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner.should_boost_ki() is True

    def test_no_boost_when_high_duty(self):
        """Test no Ki boost when duty is high (capacity limited)."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for i in range(2):
            start = datetime(2026, 1, 15, 10 + i * 2, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 15, 11 + i * 2, 0, tzinfo=timezone.utc)
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
            learner.update_session(temp=18.5, duty=0.90)  # High duty
            learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner.should_boost_ki() is False  # Capacity limited

    def test_acknowledge_boost_resets_counter(self):
        """Test acknowledging Ki boost resets the stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for i in range(2):
            start = datetime(2026, 1, 15, 10 + i * 2, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 15, 11 + i * 2, 0, tzinfo=timezone.utc)
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
            learner.update_session(temp=18.5, duty=0.60)
            learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner.should_boost_ki() is True
        learner.acknowledge_ki_boost()
        assert learner._stall_counter == 0
        assert learner.should_boost_ki() is False


class TestRateComparison:
    """Tests for comparing current rate against learned rate."""

    def test_get_rate_ratio_with_sufficient_data(self):
        """Test rate ratio calculation with enough observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Add 5 session observations (enough for comparison)
        for _ in range(5):
            learner.add_observation(
                rate=1.0, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        # Current rate is 0.4, expected is 1.0 -> ratio = 0.4
        ratio = learner.get_rate_ratio(
            current_rate=0.4, delta=3.0, outdoor_temp=8.0
        )
        assert ratio == pytest.approx(0.4)

    def test_get_rate_ratio_insufficient_data(self):
        """Test rate ratio returns None with insufficient observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Only 3 observations (need 5 for comparison)
        for _ in range(3):
            learner.add_observation(
                rate=1.0, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        ratio = learner.get_rate_ratio(
            current_rate=0.4, delta=3.0, outdoor_temp=8.0
        )
        assert ratio is None

    def test_is_underperforming_at_60_percent(self):
        """Test underperforming detection at 60% threshold."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for _ in range(5):
            learner.add_observation(
                rate=1.0, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        # 0.5 is 50% of expected 1.0 -> underperforming
        assert learner.is_underperforming(0.5, delta=3.0, outdoor_temp=8.0) is True

        # 0.7 is 70% of expected 1.0 -> not underperforming
        assert learner.is_underperforming(0.7, delta=3.0, outdoor_temp=8.0) is False

        # 0.6 is exactly 60% -> not underperforming (threshold is <60%)
        assert learner.is_underperforming(0.6, delta=3.0, outdoor_temp=8.0) is False


class TestSerialization:
    """Tests for HeatingRateLearner serialization."""

    def test_to_dict_includes_all_state(self):
        """Test to_dict captures complete state."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Add some observations
        learner.add_observation(
            rate=0.5, duration_min=60, source="session",
            stalled=False, delta=3.0, outdoor_temp=8.0
        )

        data = learner.to_dict()

        assert data["heating_type"] == "radiator"
        assert "bins" in data
        assert "delta_2_4_mild" in data["bins"]
        assert len(data["bins"]["delta_2_4_mild"]) == 1
        assert data["stall_counter"] == 0

    def test_from_dict_restores_state(self):
        """Test from_dict restores complete state."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Add observations and create state
        for _ in range(3):
            learner.add_observation(
                rate=0.5, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        # Serialize and restore
        data = learner.to_dict()
        restored = HeatingRateLearner.from_dict(data)

        assert restored._heating_type == learner._heating_type
        assert restored.get_observation_count() == learner.get_observation_count()
        assert len(restored._bins["delta_2_4_mild"]) == 3

    def test_observation_serialization(self):
        """Test observation round-trips correctly."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        ts = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.add_observation(
            rate=0.5, duration_min=60, source="session",
            stalled=True, delta=3.0, outdoor_temp=8.0, timestamp=ts
        )

        data = learner.to_dict()
        restored = HeatingRateLearner.from_dict(data)

        obs = restored._bins["delta_2_4_mild"][0]
        assert obs.rate == 0.5
        assert obs.duration_min == 60
        assert obs.source == "session"
        assert obs.stalled is True

    def test_stall_counter_persists(self):
        """Test stall counter survives serialization."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        data = learner.to_dict()
        restored = HeatingRateLearner.from_dict(data)

        assert restored._stall_counter == 1
        assert restored._last_stall_outdoor == 5.0
        assert restored._last_stall_setpoint == 21.0


class TestMinimumRateFilter:
    """Tests for minimum rate filtering."""

    def test_negative_rate_rejected_by_end_session(self):
        """Test end_session rejects negative rates (temp drop)."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)  # 60 min

        learner.start_session(temp=20.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=19.5, reason="reached_setpoint", timestamp=end)

        # Negative rate should be rejected
        assert obs is None
        assert learner.get_observation_count() == 0

    def test_near_zero_rate_rejected_by_end_session(self):
        """Test end_session rejects near-zero rates."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)  # 60 min

        # Rate = 0.01°C over 1h = 0.01°C/h (below 0.02 threshold)
        learner.start_session(temp=20.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=20.01, reason="reached_setpoint", timestamp=end)

        assert obs is None
        assert learner.get_observation_count() == 0

    def test_low_but_valid_rate_accepted(self):
        """Test end_session accepts low but valid rates above threshold."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)  # 60 min

        # Rate = 0.05°C over 1h = 0.05°C/h (above 0.02 threshold)
        learner.start_session(temp=20.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=20.05, reason="reached_setpoint", timestamp=end)

        assert obs is not None
        assert obs.rate == pytest.approx(0.05)
        assert learner.get_observation_count() == 1

    def test_add_observation_rejects_bad_rate(self):
        """Test add_observation rejects negative rates."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        learner.add_observation(
            rate=-0.1,
            duration_min=60,
            source="session",
            stalled=False,
            delta=3.0,
            outdoor_temp=8.0,
        )

        assert learner.get_observation_count() == 0

    def test_add_observation_rejects_near_zero_rate(self):
        """Test add_observation rejects near-zero rates."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        learner.add_observation(
            rate=0.01,
            duration_min=60,
            source="session",
            stalled=False,
            delta=3.0,
            outdoor_temp=8.0,
        )

        assert learner.get_observation_count() == 0

    def test_add_observation_accepts_valid_rate(self):
        """Test add_observation accepts valid rates."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        learner.add_observation(
            rate=0.05,
            duration_min=60,
            source="session",
            stalled=False,
            delta=3.0,
            outdoor_temp=8.0,
        )

        assert learner.get_observation_count() == 1


class TestPhysicsComparison:
    """Tests for physics-based rate comparison."""

    def test_insufficient_observations_returns_none(self):
        """Test returns None when not enough observations."""
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        # No observations
        result = learner.check_physics_underperformance()
        assert result is None

    def test_sufficient_observations_returns_comparison(self):
        """Test returns comparison dict with enough observations."""
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        base_time = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        # Add 3 session observations (minimum required)
        for i in range(3):
            start = base_time.replace(hour=10 + i * 3)
            end = start.replace(hour=start.hour + 2)
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=3.0, timestamp=start)
            learner.end_session(end_temp=20.5, reason="reached_target", timestamp=end)

        result = learner.check_physics_underperformance()
        assert result is not None
        assert "learned_rate" in result
        assert "expected_rate" in result
        assert "ratio" in result
        assert "is_underperforming" in result
        assert "observation_count" in result
        assert result["observation_count"] == 3

    def test_underperforming_detection(self):
        """Test detects underperforming when rate is below 50% of expected."""
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        base_time = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        # Add sessions with very low rate (~0.1°C/h, well below expected 0.3°C/h baseline)
        for i in range(3):
            start = base_time.replace(day=15 + i, hour=6)
            end = start.replace(hour=16)  # 10 hours for 1°C rise = 0.1°C/h
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=3.0, timestamp=start)
            learner.end_session(end_temp=19.0, reason="reached_target", timestamp=end)

        result = learner.check_physics_underperformance()
        assert result is not None
        assert result["is_underperforming"] is True
        assert result["ratio"] < 0.5
        assert result["suggested_ki_boost"] is not None
        assert result["suggested_ki_boost"] > 1.0

    def test_performing_well_no_boost_suggested(self):
        """Test no boost suggested when rate is adequate."""
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        base_time = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        # Add sessions with good rate (~0.5°C/h, above expected 0.3°C/h baseline)
        for i in range(3):
            start = base_time.replace(day=15 + i, hour=6)
            end = start.replace(hour=12)  # 6 hours for 3°C rise = 0.5°C/h
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=3.0, timestamp=start)
            learner.end_session(end_temp=21.0, reason="reached_target", timestamp=end)

        result = learner.check_physics_underperformance()
        assert result is not None
        assert result["is_underperforming"] is False
        assert result["suggested_ki_boost"] is None

    def test_tau_scaling_affects_expected_rate(self):
        """Test that higher tau lowers expected rate."""
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        base_time = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        # Add sessions
        for i in range(3):
            start = base_time.replace(day=15 + i, hour=6)
            end = start.replace(hour=11)  # 5 hours for 1°C rise = 0.2°C/h
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=3.0, timestamp=start)
            learner.end_session(end_temp=19.0, reason="reached_target", timestamp=end)

        # With default tau (4h), expected is ~0.3°C/h, learned 0.2°C/h = 67% (not underperforming)
        result_default = learner.check_physics_underperformance()

        # With higher tau (8h), expected is lower, learned 0.2°C/h might be adequate
        result_high_tau = learner.check_physics_underperformance(tau=8.0)

        assert result_default is not None
        assert result_high_tau is not None
        # Higher tau should lower expected rate
        assert result_high_tau["expected_rate"] < result_default["expected_rate"]
