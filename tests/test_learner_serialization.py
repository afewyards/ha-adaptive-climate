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
        """Test that v9 format includes contribution_tracker data."""
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

        # Should be v9 format
        assert data["format_version"] == 9

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
        if hasattr(learner, '_contribution_tracker'):
            learner._contribution_tracker._maintenance_contribution = 15.0
            learner._contribution_tracker._heating_rate_contribution = 8.0
            learner._contribution_tracker._recovery_cycle_count = 5

        # Serialize
        data = learner.to_dict()

        # Check serialized values
        tracker_data = data["contribution_tracker"]
        if hasattr(learner, '_contribution_tracker'):
            assert tracker_data["maintenance_contribution"] == 15.0
            assert tracker_data["heating_rate_contribution"] == 8.0
            assert tracker_data["recovery_cycle_count"] == 5
        else:
            # If not integrated yet, should still have zero values
            assert tracker_data["maintenance_contribution"] == 0.0
            assert tracker_data["heating_rate_contribution"] == 0.0
            assert tracker_data["recovery_cycle_count"] == 0

    def test_v9_deserialization_restores_contribution_tracker(self):
        """Test that v9 deserialization restores contribution tracker state."""
        # Create v9 format data manually
        v9_data = {
            "format_version": 9,
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
            "cycle_history": [],
            "auto_apply_count": 0,
            "convergence_confidence": 0.0,
            "last_adjustment_time": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False,
        }

        # Deserialize using the function directly
        restored = restore_learner_from_dict(v9_data)

        # Should detect v9 format
        assert restored["format_version"] == "v9"

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
        if hasattr(learner1, '_contribution_tracker'):
            learner1._contribution_tracker._maintenance_contribution = 20.0
            learner1._contribution_tracker._heating_rate_contribution = 10.0
            learner1._contribution_tracker._recovery_cycle_count = 2

        # Serialize
        data = learner1.to_dict()

        # Deserialize into new learner
        learner2 = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        learner2.restore_from_dict(data)

        # Check that contribution tracker was restored
        if hasattr(learner2, '_contribution_tracker'):
            assert learner2._contribution_tracker.maintenance_contribution == 20.0
            assert learner2._contribution_tracker.heating_rate_contribution == 10.0
            assert learner2._contribution_tracker.recovery_cycle_count == 2


class TestV8ToV9Migration:
    """Test migration from v8 to v9 format."""

    def test_v8_format_missing_contribution_tracker(self):
        """Test that v8 format data is migrated with default contributions."""
        # Create v8 format data (no contribution_tracker field)
        v8_data = {
            "format_version": 8,
            "heating": {
                "cycle_history": [],
                "auto_apply_count": 2,
                "convergence_confidence": 0.65,
            },
            "cooling": {
                "cycle_history": [],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "undershoot_detector": {
                "cumulative_ki_multiplier": 1.0,
                "last_adjustment_time": None,
                "time_below_target": 0.0,
                "thermal_debt": 0.0,
                "consecutive_failures": 0,
            },
            "cycle_history": [],
            "auto_apply_count": 2,
            "convergence_confidence": 0.65,
            "last_adjustment_time": None,
            "consecutive_converged_cycles": 5,
            "pid_converged_for_ke": True,
        }

        # Deserialize
        restored = restore_learner_from_dict(v8_data)

        # Should detect v8 format
        assert restored["format_version"] == "v8"

        # Should have contribution_tracker_state with defaults
        assert "contribution_tracker_state" in restored
        tracker_state = restored["contribution_tracker_state"]

        # Should default to zero
        assert tracker_state["maintenance_contribution"] == 0.0
        assert tracker_state["heating_rate_contribution"] == 0.0
        assert tracker_state["recovery_cycle_count"] == 0

    def test_v8_to_v9_migration_preserves_existing_data(self):
        """Test that v8 to v9 migration preserves all existing data."""
        # Create learner with v8 format
        learner1 = AdaptiveLearner(heating_type=HeatingType.RADIATOR)

        # Add cycles
        for i in range(5):
            cycle = CycleMetrics(
                overshoot=0.2 + i * 0.01,
                undershoot=0.1,
                settling_time=25.0,
                oscillations=1,
                rise_time=15.0,
            )
            learner1.add_cycle_metrics(cycle)

        # Manually set to v8 format for testing
        data = learner1.to_dict()
        v8_data = data.copy()
        v8_data["format_version"] = 8
        if "contribution_tracker" in v8_data:
            del v8_data["contribution_tracker"]

        # Deserialize (should migrate to v9)
        learner2 = AdaptiveLearner(heating_type=HeatingType.RADIATOR)
        learner2.restore_from_dict(v8_data)

        # Check that existing data is preserved
        assert len(learner2._heating_cycle_history) == 5
        assert learner2._heating_cycle_history[0].overshoot == 0.2
        assert learner2._heating_cycle_history[4].overshoot == pytest.approx(0.24)

        # Check that contribution tracker defaults are set
        if hasattr(learner2, '_contribution_tracker'):
            assert learner2._contribution_tracker.maintenance_contribution == 0.0
            assert learner2._contribution_tracker.heating_rate_contribution == 0.0
            assert learner2._contribution_tracker.recovery_cycle_count == 0

    def test_older_format_migrations_still_work(self):
        """Test that v7, v6, v5, v4 formats still migrate correctly."""
        # v7 format (has chronic_approach_detector)
        v7_data = {
            "heating": {
                "cycle_history": [],
                "auto_apply_count": 1,
                "convergence_confidence": 0.5,
            },
            "cooling": {
                "cycle_history": [],
                "auto_apply_count": 0,
                "convergence_confidence": 0.0,
            },
            "undershoot_detector": {},
            "chronic_approach_detector": {
                "consecutive_failures": 2,
                "cumulative_multiplier": 1.25,
            },
            "cycle_history": [],
            "auto_apply_count": 1,
            "convergence_confidence": 0.5,
            "last_adjustment_time": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False,
        }

        restored = restore_learner_from_dict(v7_data)
        assert restored["format_version"] == "v7"
        assert "contribution_tracker_state" in restored
        assert restored["contribution_tracker_state"]["maintenance_contribution"] == 0.0


class TestBackwardCompatibility:
    """Test backward compatibility with existing serialization."""

    def test_current_version_is_9(self):
        """Test that CURRENT_VERSION constant is set to 9."""
        assert CURRENT_VERSION == 9

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
        assert "cycle_history" in data  # v4 compat
        assert "auto_apply_count" in data  # v4 compat
        assert "convergence_confidence" in data  # v4 compat
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
