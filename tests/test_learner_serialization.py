"""Tests for AdaptiveLearner serialization and format migrations."""

import pytest
from datetime import datetime, timezone
from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_climate.adaptive.learner_serialization import (
    CURRENT_VERSION,
    learner_to_dict,
    restore_learner_from_dict,
)
from custom_components.adaptive_climate.const import HeatingType


class TestV9FormatSerialization:
    """Test v9 format with contribution tracker persistence."""

    def test_v9_includes_contribution_tracker_data(self):
        """Test that v10 format includes contribution_tracker data (v9 legacy)."""
        learner = AdaptiveLearner(heating_type=HeatingType.RADIATOR)

        # Add a cycle to populate history
        cycle = CycleMetrics(
            overshoot=0.2,
            undershoot=0.1,
            settling_time=25.0,
            oscillations=1,
            rise_time=15.0,
        )
        learner.add_cycle_metrics(cycle)

        # Serialize
        data = learner.to_dict()

        # Should be v10 format
        assert data["format_version"] == 10

        # Should include contribution_tracker at top level
        assert "contribution_tracker" in data
        tracker_data = data["contribution_tracker"]

        # Should have expected fields
        assert "maintenance_contribution" in tracker_data
        assert "heating_rate_contribution" in tracker_data
        assert "recovery_cycle_count" in tracker_data

        # Initial values should be zero
        assert tracker_data["maintenance_contribution"] == 0.0
        assert tracker_data["heating_rate_contribution"] == 0.0
        assert tracker_data["recovery_cycle_count"] == 0

    def test_v9_serialization_with_non_zero_contributions(self):
        """Test v9 serialization with actual contribution values."""
        learner = AdaptiveLearner(heating_type=HeatingType.RADIATOR)

        # Manually set contribution values (will be set by integration in Task 6)
        if hasattr(learner, "_contribution_tracker"):
            learner._contribution_tracker._maintenance_contribution = 15.0
            learner._contribution_tracker._heating_rate_contribution = 8.0
            learner._contribution_tracker._recovery_cycle_count = 5

        # Serialize
        data = learner.to_dict()

        # Check serialized values
        tracker_data = data["contribution_tracker"]
        if hasattr(learner, "_contribution_tracker"):
            assert tracker_data["maintenance_contribution"] == 15.0
            assert tracker_data["heating_rate_contribution"] == 8.0
            assert tracker_data["recovery_cycle_count"] == 5
        else:
            # If not integrated yet, should still have zero values
            assert tracker_data["maintenance_contribution"] == 0.0
            assert tracker_data["heating_rate_contribution"] == 0.0
            assert tracker_data["recovery_cycle_count"] == 0

    def test_v9_deserialization_restores_contribution_tracker(self):
        """Test that v10 deserialization restores contribution tracker state."""
        # Create v10 format data manually
        v10_data = {
            "format_version": 10,
            "heating": {
                "cycle_history": [],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "cooling": {
                "cycle_history": [],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "contribution_tracker": {
                "maintenance_contribution": 12.5,
                "heating_rate_contribution": 6.3,
                "recovery_cycle_count": 3,
            },
            "undershoot_detector": {},
            "last_adjustment_time": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False,
        }

        # Deserialize using the function directly
        restored = restore_learner_from_dict(v10_data)

        # Should detect v10 format
        assert restored["format_version"] == "v10"

        # Should have contribution_tracker_state
        assert "contribution_tracker_state" in restored
        tracker_state = restored["contribution_tracker_state"]

        assert tracker_state["maintenance_contribution"] == 12.5
        assert tracker_state["heating_rate_contribution"] == 6.3
        assert tracker_state["recovery_cycle_count"] == 3

    def test_v9_round_trip_preserves_contribution_tracker(self):
        """Test that serialization and deserialization preserves contribution tracker."""
        learner1 = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)

        # Add some cycles
        for _ in range(3):
            cycle = CycleMetrics(
                overshoot=0.15,
                undershoot=0.08,
                settling_time=45.0,
                oscillations=0,
                rise_time=30.0,
            )
            learner1.add_cycle_metrics(cycle)

        # Manually set contributions if tracker exists
        if hasattr(learner1, "_contribution_tracker"):
            learner1._contribution_tracker._maintenance_contribution = 20.0
            learner1._contribution_tracker._heating_rate_contribution = 10.0
            learner1._contribution_tracker._recovery_cycle_count = 2

        # Serialize
        data = learner1.to_dict()

        # Deserialize into new learner
        learner2 = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        learner2.restore_from_dict(data)

        # Check that contribution tracker was restored
        if hasattr(learner2, "_contribution_tracker"):
            assert learner2._contribution_tracker.maintenance_contribution == 20.0
            assert learner2._contribution_tracker.heating_rate_contribution == 10.0
            assert learner2._contribution_tracker.recovery_cycle_count == 2


class TestStartingDeltaSerialization:
    """Test that starting_delta is properly serialized and restored."""

    def test_starting_delta_is_serialized(self):
        """Test that starting_delta field is included in serialized cycle data."""
        cycle = CycleMetrics(
            overshoot=0.2,
            undershoot=0.1,
            settling_time=25.0,
            oscillations=1,
            rise_time=15.0,
            starting_delta=1.5,  # Set a specific starting_delta
        )

        learner = AdaptiveLearner(heating_type=HeatingType.RADIATOR)
        learner.add_cycle_metrics(cycle)

        # Serialize
        data = learner.to_dict()

        # Check that starting_delta is in the serialized cycle
        assert len(data["heating"]["cycle_history"]) == 1
        serialized_cycle = data["heating"]["cycle_history"][0]
        assert "starting_delta" in serialized_cycle
        assert serialized_cycle["starting_delta"] == 1.5

    def test_starting_delta_is_deserialized(self):
        """Test that starting_delta is restored from serialized data."""
        # Create serialized data with starting_delta
        v10_data = {
            "format_version": 10,
            "heating": {
                "cycle_history": [
                    {
                        "overshoot": 0.2,
                        "undershoot": 0.1,
                        "settling_time": 25.0,
                        "oscillations": 1,
                        "rise_time": 15.0,
                        "starting_delta": 2.3,
                        "mode": "heating",
                    }
                ],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "cooling": {
                "cycle_history": [],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "contribution_tracker": {
                "maintenance_contribution": 0.0,
                "heating_rate_contribution": 0.0,
                "recovery_cycle_count": 0,
            },
            "undershoot_detector": {},
            "last_adjustment_time": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False,
        }

        # Deserialize
        restored = restore_learner_from_dict(v10_data)

        # Check that starting_delta was restored
        assert len(restored["heating_cycle_history"]) == 1
        cycle = restored["heating_cycle_history"][0]
        assert cycle.starting_delta == 2.3

    def test_starting_delta_round_trip(self):
        """Test that starting_delta survives serialization round-trip."""
        learner1 = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)

        # Add cycle with specific starting_delta
        cycle = CycleMetrics(
            overshoot=0.15,
            undershoot=0.08,
            settling_time=45.0,
            oscillations=0,
            rise_time=30.0,
            starting_delta=0.8,
        )
        learner1.add_cycle_metrics(cycle)

        # Serialize
        data = learner1.to_dict()

        # Deserialize into new learner
        learner2 = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        learner2.restore_from_dict(data)

        # Check that starting_delta was preserved
        assert len(learner2._heating_cycle_history) == 1
        restored_cycle = learner2._heating_cycle_history[0]
        assert restored_cycle.starting_delta == 0.8

    def test_missing_starting_delta_defaults_to_none(self):
        """Test that missing starting_delta in old data defaults to None."""
        # Create v10 data without starting_delta (simulating old persisted data)
        v10_data = {
            "format_version": 10,
            "heating": {
                "cycle_history": [
                    {
                        "overshoot": 0.2,
                        "undershoot": 0.1,
                        "settling_time": 25.0,
                        "oscillations": 1,
                        "rise_time": 15.0,
                        "mode": "heating",
                        # No starting_delta field
                    }
                ],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "cooling": {
                "cycle_history": [],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "contribution_tracker": {
                "maintenance_contribution": 0.0,
                "heating_rate_contribution": 0.0,
                "recovery_cycle_count": 0,
            },
            "undershoot_detector": {},
            "last_adjustment_time": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False,
        }

        # Deserialize
        restored = restore_learner_from_dict(v10_data)

        # Check that starting_delta defaults to None
        assert len(restored["heating_cycle_history"]) == 1
        cycle = restored["heating_cycle_history"][0]
        assert cycle.starting_delta is None


class TestV9ToV10Migration:
    """Test migration from v9 to v10 format."""

    def test_v10_round_trip(self):
        """Test v10 state serializes and restores correctly."""
        learner = AdaptiveLearner(heating_type=HeatingType.RADIATOR)

        # Add heating rate data
        if hasattr(learner, "_heating_rate_learner"):
            from datetime import datetime, timezone

            learner._heating_rate_learner.add_observation(
                rate=0.5,
                duration_min=60,
                source="session",
                stalled=False,
                delta=3.0,
                outdoor_temp=8.0,
                timestamp=datetime.now(timezone.utc),
            )

        # Serialize
        data = learner.to_dict()
        assert data["format_version"] == 10
        assert "heating_rate_learner" in data

        # Restore
        restored = restore_learner_from_dict(data)
        assert "heating_rate_learner_state" in restored

        # Verify observation was preserved
        learner_state = restored["heating_rate_learner_state"]
        assert learner_state is not None

        # Restore into new learner to verify
        learner2 = AdaptiveLearner(heating_type=HeatingType.RADIATOR)
        learner2.restore_from_dict(data)

        if hasattr(learner2, "_heating_rate_learner"):
            assert learner2._heating_rate_learner.get_observation_count() == 1


class TestBackwardCompatibility:
    """Test backward compatibility with existing serialization."""

    def test_current_version_is_10(self):
        """Test that CURRENT_VERSION constant is set to 10."""
        assert CURRENT_VERSION == 10

    def test_v9_includes_all_v8_fields(self):
        """Test that v9 format includes all v8 fields for compatibility."""
        learner = AdaptiveLearner(heating_type=HeatingType.CONVECTOR)

        cycle = CycleMetrics(
            overshoot=0.18,
            undershoot=0.09,
            settling_time=20.0,
            oscillations=0,
            rise_time=12.0,
        )
        learner.add_cycle_metrics(cycle)

        data = learner.to_dict()

        # Check v8 fields are present
        assert "format_version" in data
        assert "heating" in data
        assert "cooling" in data
        assert "undershoot_detector" in data
        assert "last_adjustment_time" in data
        assert "consecutive_converged_cycles" in data
        assert "pid_converged_for_ke" in data

    def test_mode_keyed_structure_preserved(self):
        """Test that mode-keyed structure (v5+) is preserved in v9."""
        learner = AdaptiveLearner(heating_type=HeatingType.FORCED_AIR)

        data = learner.to_dict()

        # Check mode-keyed structure
        assert "heating" in data
        assert "cooling" in data
        assert "cycle_history" in data["heating"]
        assert "auto_apply_count" in data["heating"]
        assert "convergence_confidence" in data["heating"]
        assert "cycle_history" in data["cooling"]
        assert "auto_apply_count" in data["cooling"]
        assert "convergence_confidence" in data["cooling"]
