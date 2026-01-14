"""Tests for learning data persistence."""

import pytest
import json
import os
import tempfile
from datetime import datetime

from custom_components.adaptive_thermostat.adaptive.learning import (
    LearningDataStore,
    ThermalRateLearner,
    AdaptiveLearner,
    CycleMetrics,
    ValveCycleTracker,
)


@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_data_saving(temp_storage_dir):
    """Test that learning data is saved correctly."""
    store = LearningDataStore(temp_storage_dir)

    # Create ThermalRateLearner with data
    thermal_learner = ThermalRateLearner(outlier_threshold=1.5)
    thermal_learner.add_cooling_measurement(0.5)
    thermal_learner.add_cooling_measurement(0.6)
    thermal_learner.add_heating_measurement(2.0)
    thermal_learner.add_heating_measurement(2.2)

    # Create AdaptiveLearner with cycle metrics
    adaptive_learner = AdaptiveLearner()
    for i in range(3):
        metrics = CycleMetrics(
            overshoot=0.3,
            undershoot=0.2,
            settling_time=45.0,
            oscillations=1,
            rise_time=30.0,
        )
        adaptive_learner.add_cycle_metrics(metrics)

    # Create ValveCycleTracker with some cycles
    valve_tracker = ValveCycleTracker()
    valve_tracker.update(False)
    valve_tracker.update(True)  # Cycle 1
    valve_tracker.update(False)
    valve_tracker.update(True)  # Cycle 2

    # Save all data
    result = store.save(
        thermal_learner=thermal_learner,
        adaptive_learner=adaptive_learner,
        valve_tracker=valve_tracker,
    )

    assert result is True

    # Verify file exists
    assert os.path.exists(store.storage_file)

    # Verify file content
    with open(store.storage_file, "r") as f:
        data = json.load(f)

    assert data["version"] == 2  # Version 2 for Ke learner support
    assert "last_updated" in data

    # Check ThermalRateLearner data
    assert "thermal_learner" in data
    assert data["thermal_learner"]["cooling_rates"] == [0.5, 0.6]
    assert data["thermal_learner"]["heating_rates"] == [2.0, 2.2]
    assert data["thermal_learner"]["outlier_threshold"] == 1.5

    # Check AdaptiveLearner data
    assert "adaptive_learner" in data
    assert len(data["adaptive_learner"]["cycle_history"]) == 3
    assert data["adaptive_learner"]["cycle_history"][0]["overshoot"] == 0.3
    assert data["adaptive_learner"]["cycle_history"][0]["undershoot"] == 0.2

    # Check ValveCycleTracker data
    assert "valve_tracker" in data
    assert data["valve_tracker"]["cycle_count"] == 2
    assert data["valve_tracker"]["last_state"] is True


def test_data_loading(temp_storage_dir):
    """Test that learning data is loaded correctly."""
    store = LearningDataStore(temp_storage_dir)

    # Create and save some data first
    thermal_learner = ThermalRateLearner()
    thermal_learner.add_cooling_measurement(0.5)
    thermal_learner.add_heating_measurement(2.0)

    adaptive_learner = AdaptiveLearner()
    metrics = CycleMetrics(overshoot=0.4, undershoot=0.1, settling_time=50.0)
    adaptive_learner.add_cycle_metrics(metrics)

    valve_tracker = ValveCycleTracker()
    valve_tracker.update(False)
    valve_tracker.update(True)

    store.save(
        thermal_learner=thermal_learner,
        adaptive_learner=adaptive_learner,
        valve_tracker=valve_tracker,
    )

    # Load the data
    data = store.load()

    assert data is not None
    assert data["version"] == 2  # Version 2 for Ke learner support

    # Restore ThermalRateLearner
    restored_thermal = store.restore_thermal_learner(data)
    assert restored_thermal is not None
    assert len(restored_thermal._cooling_rates) == 1
    assert len(restored_thermal._heating_rates) == 1
    assert restored_thermal._cooling_rates[0] == 0.5
    assert restored_thermal._heating_rates[0] == 2.0

    # Restore AdaptiveLearner
    restored_adaptive = store.restore_adaptive_learner(data)
    assert restored_adaptive is not None
    assert restored_adaptive.get_cycle_count() == 1

    # Restore ValveCycleTracker
    restored_valve = store.restore_valve_tracker(data)
    assert restored_valve is not None
    assert restored_valve.get_cycle_count() == 1
    assert restored_valve._last_state is True


def test_corrupt_data_handling(temp_storage_dir):
    """Test that corrupt data is handled gracefully."""
    store = LearningDataStore(temp_storage_dir)

    # Test 1: Non-existent file
    data = store.load()
    assert data is None

    # Test 2: Invalid JSON
    with open(store.storage_file, "w") as f:
        f.write("{ invalid json }")

    data = store.load()
    assert data is None

    # Test 3: Missing version field
    with open(store.storage_file, "w") as f:
        json.dump({"some_data": "value"}, f)

    data = store.load()
    assert data is None

    # Test 4: Corrupted thermal learner data (restore should return None)
    valid_data = {
        "version": 1,
        "thermal_learner": {
            "cooling_rates": "not_a_list",  # Invalid type
        }
    }
    with open(store.storage_file, "w") as f:
        json.dump(valid_data, f)

    data = store.load()
    assert data is not None  # Load succeeds
    restored = store.restore_thermal_learner(data)
    assert restored is None  # But restore fails gracefully

    # Test 5: Corrupted adaptive learner data
    valid_data = {
        "version": 1,
        "adaptive_learner": {
            "cycle_history": "not_a_list",  # Invalid type
        }
    }
    with open(store.storage_file, "w") as f:
        json.dump(valid_data, f)

    data = store.load()
    assert data is not None
    restored = store.restore_adaptive_learner(data)
    assert restored is None  # Restore fails gracefully

    # Test 6: Missing data sections (restore should return None)
    valid_data = {
        "version": 1,
        # No learner data at all
    }
    with open(store.storage_file, "w") as f:
        json.dump(valid_data, f)

    data = store.load()
    assert data is not None
    assert store.restore_thermal_learner(data) is None
    assert store.restore_adaptive_learner(data) is None
    assert store.restore_valve_tracker(data) is None


def test_atomic_save(temp_storage_dir):
    """Test that save is atomic (uses temp file and rename)."""
    store = LearningDataStore(temp_storage_dir)

    thermal_learner = ThermalRateLearner()
    thermal_learner.add_cooling_measurement(0.5)

    # Save should not leave temp file behind
    result = store.save(thermal_learner=thermal_learner)
    assert result is True

    temp_file = f"{store.storage_file}.tmp"
    assert not os.path.exists(temp_file)
    assert os.path.exists(store.storage_file)


def test_partial_save(temp_storage_dir):
    """Test saving only some components."""
    store = LearningDataStore(temp_storage_dir)

    # Save only thermal learner
    thermal_learner = ThermalRateLearner()
    thermal_learner.add_cooling_measurement(0.5)

    result = store.save(thermal_learner=thermal_learner)
    assert result is True

    # Load and verify
    data = store.load()
    assert data is not None
    assert "thermal_learner" in data
    assert "adaptive_learner" not in data
    assert "valve_tracker" not in data

    restored = store.restore_thermal_learner(data)
    assert restored is not None
    assert len(restored._cooling_rates) == 1


def test_empty_learners(temp_storage_dir):
    """Test saving and loading empty learners."""
    store = LearningDataStore(temp_storage_dir)

    # Create empty learners
    thermal_learner = ThermalRateLearner()
    adaptive_learner = AdaptiveLearner()
    valve_tracker = ValveCycleTracker()

    # Save
    result = store.save(
        thermal_learner=thermal_learner,
        adaptive_learner=adaptive_learner,
        valve_tracker=valve_tracker,
    )
    assert result is True

    # Load
    data = store.load()
    assert data is not None

    # Restore
    restored_thermal = store.restore_thermal_learner(data)
    assert restored_thermal is not None
    assert len(restored_thermal._cooling_rates) == 0
    assert len(restored_thermal._heating_rates) == 0

    restored_adaptive = store.restore_adaptive_learner(data)
    assert restored_adaptive is not None
    assert restored_adaptive.get_cycle_count() == 0

    restored_valve = store.restore_valve_tracker(data)
    assert restored_valve is not None
    assert restored_valve.get_cycle_count() == 0
