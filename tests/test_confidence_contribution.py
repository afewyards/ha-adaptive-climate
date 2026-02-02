"""Tests for confidence contribution tracking."""

import pytest
from custom_components.adaptive_climate.adaptive.confidence_contribution import (
    ConfidenceContributionTracker,
)
from custom_components.adaptive_climate.const import HeatingType


class TestMaintenanceCap:
    """Test maintenance confidence capping."""

    def test_maintenance_below_cap(self):
        """Maintenance contribution below cap is fully applied."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        gain = tracker.apply_maintenance_gain(0.10)
        assert gain == pytest.approx(0.10, rel=0.01)
        assert tracker.maintenance_contribution == pytest.approx(0.10, rel=0.01)

    def test_maintenance_at_cap(self):
        """Maintenance at cap gets diminishing returns."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor cap is 25%
        tracker._maintenance_contribution = 0.25
        gain = tracker.apply_maintenance_gain(0.10)
        # 10% * 0.1 diminishing rate = 1%
        assert gain == pytest.approx(0.01, rel=0.01)

    def test_maintenance_crossing_cap(self):
        """Gain that crosses cap is split."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        tracker._maintenance_contribution = 0.20
        gain = tracker.apply_maintenance_gain(0.10)
        # 5% to reach cap + 5% * 0.1 = 5.5%
        assert gain == pytest.approx(0.055, rel=0.01)

    def test_different_caps_by_heating_type(self):
        """Different heating types have different caps."""
        floor = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        forced = ConfidenceContributionTracker(HeatingType.FORCED_AIR)

        # Floor cap is 25%, forced_air is 35%
        floor._maintenance_contribution = 0.30
        forced._maintenance_contribution = 0.30

        floor_gain = floor.apply_maintenance_gain(0.10)
        forced_gain = forced.apply_maintenance_gain(0.10)

        # Floor is over cap, forced is under
        assert floor_gain < forced_gain


class TestHeatingRateCap:
    """Test heating rate confidence capping."""

    def test_heating_rate_below_cap(self):
        """Heating rate below cap is fully applied."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        gain = tracker.apply_heating_rate_gain(0.15)
        assert gain == pytest.approx(0.15, rel=0.01)
        assert tracker.heating_rate_contribution == pytest.approx(0.15, rel=0.01)

    def test_heating_rate_capped(self):
        """Heating rate gain is capped at max."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor cap is 30%
        gain = tracker.apply_heating_rate_gain(0.50)
        assert gain == pytest.approx(0.30, rel=0.01)
        assert tracker.heating_rate_contribution == pytest.approx(0.30, rel=0.01)

    def test_forced_air_low_heating_rate_cap(self):
        """Forced air has low heating rate cap (5%)."""
        tracker = ConfidenceContributionTracker(HeatingType.FORCED_AIR)
        gain = tracker.apply_heating_rate_gain(0.20)
        assert gain == pytest.approx(0.05, rel=0.01)


class TestRecoveryCycles:
    """Test recovery cycle tracking."""

    def test_recovery_cycle_count(self):
        """Recovery cycles are counted separately."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        tracker.add_recovery_cycle()
        tracker.add_recovery_cycle()
        assert tracker.recovery_cycle_count == 2

    def test_can_reach_tier1(self):
        """Tier 1 requires enough recovery cycles."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor needs 12 recovery cycles for tier 1
        for _ in range(11):
            tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(1) is False

        tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(1) is True

    def test_can_reach_tier2(self):
        """Tier 2 requires more recovery cycles."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor needs 20 recovery cycles for tier 2
        for _ in range(19):
            tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(2) is False

        tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(2) is True


class TestIntegrationWithLearning:
    """Test integration with learning.py."""

    def test_heating_rate_gain_applied_with_rise_time(self):
        """Heating rate gain is applied when rise_time is present."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)

        # Simulate a weighted gain being passed through
        weighted_gain = 0.08
        actual_gain = tracker.apply_heating_rate_gain(weighted_gain)

        # Below cap, so should be fully applied
        assert actual_gain == pytest.approx(0.08, rel=0.01)
        assert tracker.heating_rate_contribution == pytest.approx(0.08, rel=0.01)

    def test_heating_rate_gain_respects_cap(self):
        """Heating rate gain respects cap when applied multiple times."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)

        # Floor cap is 30%, apply multiple gains
        tracker.apply_heating_rate_gain(0.15)
        tracker.apply_heating_rate_gain(0.15)
        tracker.apply_heating_rate_gain(0.15)

        # Total should be capped at 30%
        assert tracker.heating_rate_contribution == pytest.approx(0.30, rel=0.01)


class TestSerialization:
    """Test serialization/deserialization."""

    def test_to_dict(self):
        """Tracker serializes to dict."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        tracker._maintenance_contribution = 0.15
        tracker._heating_rate_contribution = 0.10
        tracker._recovery_cycle_count = 5

        data = tracker.to_dict()

        assert data["maintenance_contribution"] == pytest.approx(0.15, rel=0.01)
        assert data["heating_rate_contribution"] == pytest.approx(0.10, rel=0.01)
        assert data["recovery_cycle_count"] == 5

    def test_from_dict(self):
        """Tracker deserializes from dict."""
        data = {
            "maintenance_contribution": 0.20,
            "heating_rate_contribution": 0.12,
            "recovery_cycle_count": 8,
        }
        tracker = ConfidenceContributionTracker.from_dict(
            data, HeatingType.FLOOR_HYDRONIC
        )

        assert tracker.maintenance_contribution == pytest.approx(0.20, rel=0.01)
        assert tracker.heating_rate_contribution == pytest.approx(0.12, rel=0.01)
        assert tracker.recovery_cycle_count == 8

    def test_from_dict_missing_fields(self):
        """Missing fields default to zero."""
        tracker = ConfidenceContributionTracker.from_dict({}, HeatingType.FLOOR_HYDRONIC)

        assert tracker.maintenance_contribution == 0.0
        assert tracker.heating_rate_contribution == 0.0
        assert tracker.recovery_cycle_count == 0
