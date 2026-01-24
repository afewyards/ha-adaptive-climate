"""Tests for PreheatLearner class."""

import pytest
from datetime import datetime, timedelta

from custom_components.adaptive_thermostat.adaptive.preheat import (
    HeatingObservation,
    PreheatLearner,
)
from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
)


class TestPreheatLearner:
    """Tests for PreheatLearner class."""

    def test_get_delta_bin(self):
        """Test delta binning logic."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)

        assert learner.get_delta_bin(0.5) == "0-2"
        assert learner.get_delta_bin(1.0) == "0-2"
        assert learner.get_delta_bin(1.99) == "0-2"
        assert learner.get_delta_bin(2.0) == "2-4"
        assert learner.get_delta_bin(3.5) == "2-4"
        assert learner.get_delta_bin(3.99) == "2-4"
        assert learner.get_delta_bin(4.0) == "4-6"
        assert learner.get_delta_bin(5.0) == "4-6"
        assert learner.get_delta_bin(5.99) == "4-6"
        assert learner.get_delta_bin(6.0) == "6+"
        assert learner.get_delta_bin(10.0) == "6+"

    def test_get_outdoor_bin(self):
        """Test outdoor temperature binning logic."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)

        assert learner.get_outdoor_bin(-5.0) == "cold"
        assert learner.get_outdoor_bin(0.0) == "cold"
        assert learner.get_outdoor_bin(4.99) == "cold"
        assert learner.get_outdoor_bin(5.0) == "mild"
        assert learner.get_outdoor_bin(8.0) == "mild"
        assert learner.get_outdoor_bin(11.99) == "mild"
        assert learner.get_outdoor_bin(12.0) == "moderate"
        assert learner.get_outdoor_bin(15.0) == "moderate"
        assert learner.get_outdoor_bin(20.0) == "moderate"

    def test_add_observation(self):
        """Test adding observations stores in correct bin and calculates rate."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)
        now = datetime(2024, 1, 1, 12, 0, 0)

        learner.add_observation(
            start_temp=18.0,
            end_temp=21.0,
            outdoor_temp=8.0,
            duration_minutes=90,
            timestamp=now
        )

        # Delta = 3.0 -> "2-4" bin
        # Outdoor = 8.0 -> "mild" bin
        # Rate = 3.0 / (90/60) = 2.0 C/hour
        observations = learner._observations.get(("2-4", "mild"), [])
        assert len(observations) == 1
        obs = observations[0]
        assert obs.start_temp == 18.0
        assert obs.end_temp == 21.0
        assert obs.outdoor_temp == 8.0
        assert obs.duration_minutes == 90
        assert obs.rate == pytest.approx(2.0, abs=0.01)
        assert obs.timestamp == now

    def test_add_multiple_observations_different_bins(self):
        """Test adding observations to different bins."""
        learner = PreheatLearner(HEATING_TYPE_RADIATOR)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Small delta, cold outdoor
        learner.add_observation(19.0, 20.5, 2.0, 60, now)
        # Medium delta, mild outdoor
        learner.add_observation(18.0, 21.0, 8.0, 90, now)
        # Large delta, moderate outdoor
        learner.add_observation(15.0, 21.0, 15.0, 180, now)

        assert len(learner._observations) == 3
        assert ("0-2", "cold") in learner._observations
        assert ("2-4", "mild") in learner._observations
        assert ("6+", "moderate") in learner._observations

    def test_90_day_expiry_removes_old_observations(self):
        """Test that observations older than 90 days are removed on add."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add old observation (91 days ago)
        # Delta = 2.0 (20.0 - 18.0) -> "2-4" bin
        old_time = now - timedelta(days=91)
        learner.add_observation(18.0, 20.0, 10.0, 60, old_time)

        # Add fresh observation (10 days ago)
        fresh_time = now - timedelta(days=10)
        learner.add_observation(18.0, 20.0, 10.0, 60, fresh_time)

        # Add current observation - should trigger expiry
        learner.add_observation(18.0, 20.0, 10.0, 60, now)

        # Only 2 observations should remain (old one expired)
        # Delta = 2.0 -> "2-4" bin, outdoor = 10.0 -> "mild" bin
        observations = learner._observations.get(("2-4", "mild"), [])
        assert len(observations) == 2
        assert all(obs.timestamp >= old_time + timedelta(days=1) for obs in observations)

    def test_estimate_time_to_target_with_insufficient_data(self):
        """Test that fallback rate is used with <3 observations."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR, max_hours=2.0)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add only 2 observations
        learner.add_observation(18.0, 20.0, 10.0, 60, now)
        learner.add_observation(18.5, 20.5, 10.0, 60, now)

        # Delta = 3.0, outdoor = 10.0
        # Should use fallback_rate * margin
        # fallback_rate for convector = 2.0 C/hour
        # margin = (1.0 + 3.0/10 * 0.3) * 1.2 = 1.308
        # time = (3.0 / 2.0) * 60 * 1.308 = 117.72 minutes
        result = learner.estimate_time_to_target(18.0, 21.0, 10.0)
        expected = (3.0 / 2.0) * 60 * (1.0 + 3.0/10 * 0.3) * 1.2
        assert result == pytest.approx(expected, abs=1.0)

    def test_estimate_time_to_target_with_sufficient_data(self):
        """Test that median of last 10 rates is used with >=3 observations."""
        learner = PreheatLearner(HEATING_TYPE_RADIATOR, max_hours=4.0)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add 5 observations with different rates
        # Delta ~3.0, outdoor ~10.0
        learner.add_observation(18.0, 21.0, 10.0, 60, now)   # rate = 3.0 C/h
        learner.add_observation(18.0, 21.0, 10.0, 90, now)   # rate = 2.0 C/h
        learner.add_observation(18.0, 21.0, 10.0, 120, now)  # rate = 1.5 C/h
        learner.add_observation(18.0, 21.0, 10.0, 100, now)  # rate = 1.8 C/h
        learner.add_observation(18.0, 21.0, 10.0, 80, now)   # rate = 2.25 C/h

        # Median of [1.5, 1.8, 2.0, 2.25, 3.0] = 2.0 C/h
        # Delta = 3.0, outdoor = 10.0
        # margin = (1.0 + 3.0/10 * 0.3) * 1.3 = 1.417
        # time = (3.0 / 2.0) * 60 * 1.417 = 127.53 minutes
        result = learner.estimate_time_to_target(18.0, 21.0, 10.0)
        expected = (3.0 / 2.0) * 60 * (1.0 + 3.0/10 * 0.3) * 1.3
        assert result == pytest.approx(expected, abs=1.0)

    def test_estimate_time_to_target_clamped_to_max_hours(self):
        """Test that result is clamped to max_hours * 60."""
        learner = PreheatLearner(HEATING_TYPE_FORCED_AIR, max_hours=1.5)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add observations with very slow rate
        learner.add_observation(18.0, 19.0, 5.0, 300, now)  # rate = 0.2 C/h
        learner.add_observation(18.0, 19.0, 5.0, 300, now)
        learner.add_observation(18.0, 19.0, 5.0, 300, now)

        # Delta = 10.0 would normally take many hours
        # Should clamp to 1.5 * 60 = 90 minutes
        result = learner.estimate_time_to_target(18.0, 28.0, 5.0)
        assert result == 90.0

    def test_estimate_time_to_target_returns_zero_when_at_target(self):
        """Test that 0 is returned when current >= target."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)

        assert learner.estimate_time_to_target(21.0, 21.0, 10.0) == 0.0
        assert learner.estimate_time_to_target(22.0, 21.0, 10.0) == 0.0

    def test_cold_soak_margin_scales_with_delta(self):
        """Test that cold soak margin scales with delta."""
        learner = PreheatLearner(HEATING_TYPE_FLOOR_HYDRONIC, max_hours=8.0)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add observations
        learner.add_observation(18.0, 20.0, 10.0, 60, now)
        learner.add_observation(18.0, 20.0, 10.0, 60, now)
        learner.add_observation(18.0, 20.0, 10.0, 60, now)

        # Small delta (2.0)
        result_small = learner.estimate_time_to_target(19.0, 21.0, 10.0)
        # margin_small = (1.0 + 2.0/10 * 0.3) * 1.5 = 1.59

        # Large delta (8.0)
        result_large = learner.estimate_time_to_target(13.0, 21.0, 10.0)
        # margin_large = (1.0 + 8.0/10 * 0.3) * 1.5 = 1.86

        # Larger delta should have proportionally more margin
        assert result_large > result_small

    def test_get_confidence_with_no_observations(self):
        """Test confidence is 0.0 with no observations."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)
        assert learner.get_confidence() == 0.0

    def test_get_confidence_scales_with_observation_count(self):
        """Test confidence scales from 0 to 1.0 with 10+ observations."""
        learner = PreheatLearner(HEATING_TYPE_RADIATOR)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # 0 observations -> 0.0
        assert learner.get_confidence() == 0.0

        # Add 5 observations -> 0.5
        for i in range(5):
            learner.add_observation(18.0, 20.0, 10.0, 60, now + timedelta(hours=i))
        assert learner.get_confidence() == pytest.approx(0.5, abs=0.01)

        # Add 5 more (total 10) -> 1.0
        for i in range(5):
            learner.add_observation(18.0, 20.0, 10.0, 60, now + timedelta(hours=10+i))
        assert learner.get_confidence() == pytest.approx(1.0, abs=0.01)

        # Add more (total 15) -> still 1.0 (capped)
        for i in range(5):
            learner.add_observation(18.0, 20.0, 10.0, 60, now + timedelta(hours=20+i))
        assert learner.get_confidence() == pytest.approx(1.0, abs=0.01)

    def test_get_observation_count(self):
        """Test get_observation_count returns total across all bins."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)
        now = datetime(2024, 1, 1, 12, 0, 0)

        assert learner.get_observation_count() == 0

        # Add to different bins
        learner.add_observation(19.0, 20.0, 2.0, 60, now)    # 0-2, cold
        learner.add_observation(18.0, 21.0, 8.0, 90, now)    # 2-4, mild
        learner.add_observation(15.0, 21.0, 15.0, 180, now)  # 6+, moderate

        assert learner.get_observation_count() == 3

    def test_get_learned_rate_returns_median_of_bin(self):
        """Test get_learned_rate returns median rate for specific bin."""
        learner = PreheatLearner(HEATING_TYPE_RADIATOR)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add observations to "2-4", "mild" bin with rates: 1.5, 2.0, 2.5 C/h
        learner.add_observation(18.0, 21.0, 8.0, 120, now)  # rate = 1.5
        learner.add_observation(18.0, 21.0, 8.0, 90, now)   # rate = 2.0
        learner.add_observation(18.0, 21.0, 8.0, 72, now)   # rate = 2.5

        rate = learner.get_learned_rate(delta=3.0, outdoor_temp=8.0)
        assert rate == pytest.approx(2.0, abs=0.01)

    def test_get_learned_rate_returns_none_when_no_data(self):
        """Test get_learned_rate returns None when bin has no data."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)

        rate = learner.get_learned_rate(delta=3.0, outdoor_temp=8.0)
        assert rate is None

    def test_to_dict_serialization(self):
        """Test to_dict serializes all state."""
        learner = PreheatLearner(HEATING_TYPE_FLOOR_HYDRONIC, max_hours=8.0)
        now = datetime(2024, 1, 1, 12, 0, 0)

        learner.add_observation(18.0, 21.0, 8.0, 120, now)
        learner.add_observation(19.0, 20.0, 2.0, 60, now)

        data = learner.to_dict()

        assert data["heating_type"] == HEATING_TYPE_FLOOR_HYDRONIC
        assert data["max_hours"] == 8.0
        assert "observations" in data
        assert len(data["observations"]) == 2

        # Check observation structure
        obs_keys = {"bin_key", "start_temp", "end_temp", "outdoor_temp", "duration_minutes", "rate", "timestamp"}
        for obs in data["observations"]:
            assert set(obs.keys()) == obs_keys

    def test_from_dict_deserialization(self):
        """Test from_dict restores state correctly."""
        original = PreheatLearner(HEATING_TYPE_RADIATOR, max_hours=4.0)
        now = datetime(2024, 1, 1, 12, 0, 0)

        original.add_observation(18.0, 21.0, 8.0, 120, now)
        original.add_observation(19.0, 20.0, 2.0, 60, now)

        data = original.to_dict()
        restored = PreheatLearner.from_dict(data)

        assert restored.heating_type == HEATING_TYPE_RADIATOR
        assert restored.max_hours == 4.0
        assert restored.get_observation_count() == 2

        # Verify observations are in correct bins
        assert ("2-4", "mild") in restored._observations
        assert ("0-2", "cold") in restored._observations

    def test_round_trip_serialization(self):
        """Test that to_dict -> from_dict preserves state."""
        original = PreheatLearner(HEATING_TYPE_CONVECTOR, max_hours=2.0)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add diverse observations
        original.add_observation(18.0, 21.0, 8.0, 120, now)
        original.add_observation(19.0, 20.0, 2.0, 60, now - timedelta(days=10))
        original.add_observation(15.0, 21.0, 15.0, 180, now - timedelta(days=20))

        # Round trip
        data = original.to_dict()
        restored = PreheatLearner.from_dict(data)

        # Verify estimates are identical
        result_original = original.estimate_time_to_target(18.0, 21.0, 8.0)
        result_restored = restored.estimate_time_to_target(18.0, 21.0, 8.0)
        assert result_restored == pytest.approx(result_original, abs=0.01)

        # Verify confidence is identical
        assert restored.get_confidence() == pytest.approx(original.get_confidence(), abs=0.01)

    def test_heating_type_defaults_from_config(self):
        """Test that heating type uses correct defaults from HEATING_TYPE_PREHEAT_CONFIG."""
        floor = PreheatLearner(HEATING_TYPE_FLOOR_HYDRONIC)
        radiator = PreheatLearner(HEATING_TYPE_RADIATOR)
        convector = PreheatLearner(HEATING_TYPE_CONVECTOR)
        forced_air = PreheatLearner(HEATING_TYPE_FORCED_AIR)

        # Verify max_hours from config
        assert floor.max_hours == 8.0
        assert radiator.max_hours == 4.0
        assert convector.max_hours == 2.0
        assert forced_air.max_hours == 1.5

    def test_observation_limit_per_bin(self):
        """Test that observations are limited to prevent unbounded growth."""
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add more than max observations to same bin
        for i in range(150):
            learner.add_observation(18.0, 21.0, 8.0, 90, now + timedelta(hours=i))

        # Should be limited to 100 observations per bin
        observations = learner._observations.get(("2-4", "mild"), [])
        assert len(observations) <= 100
