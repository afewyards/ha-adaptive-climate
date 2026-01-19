"""Tests for learning data persistence."""

import pytest
import json
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, Mock

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


# New tests for Story 2.1: HA Store helper with zone-keyed storage


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.config = MagicMock()
    hass.config.path = MagicMock(return_value="/mock/config")
    return hass


@pytest.mark.asyncio
async def test_learning_store_init(mock_hass):
    """Test that LearningDataStore creates Store with correct key."""
    store = LearningDataStore(mock_hass)

    # Should not create Store instance until async_load is called
    assert store.hass == mock_hass
    assert store._store is None
    assert store._data == {"version": 3, "zones": {}}


@pytest.mark.asyncio
async def test_learning_store_async_load_empty(mock_hass):
    """Test async_load returns default structure when no file exists."""
    # Create mock Store class
    mock_store_class = Mock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value=None)
    mock_store_class.return_value = mock_store_instance

    # Mock the homeassistant.helpers.storage module
    mock_storage_module = Mock()
    mock_storage_module.Store = mock_store_class

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should create Store with correct parameters
        mock_store_class.assert_called_once_with(mock_hass, 3, "adaptive_thermostat_learning")

        # Should return default structure when no data
        assert data == {"version": 3, "zones": {}}
        assert store._data == {"version": 3, "zones": {}}


@pytest.mark.asyncio
async def test_learning_store_async_load_v2_migration(mock_hass):
    """Test async_load migrates v2 format to v3 zone-keyed format."""
    # Simulate old v2 format (flat structure, no zones)
    v2_data = {
        "version": 2,
        "last_updated": "2026-01-19T10:00:00",
        "thermal_learner": {
            "cooling_rates": [0.5, 0.6],
            "heating_rates": [2.0, 2.2],
            "outlier_threshold": 1.5,
        },
        "adaptive_learner": {
            "cycle_history": [
                {"overshoot": 0.3, "undershoot": 0.2, "settling_time": 45.0, "oscillations": 1, "rise_time": 30.0}
            ],
            "last_adjustment_time": None,
            "max_history": 50,
            "heating_type": "radiator",
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False,
        },
        "valve_tracker": {
            "cycle_count": 2,
            "last_state": True,
        },
    }

    # Create mock Store class
    mock_store_class = Mock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value=v2_data)
    mock_store_class.return_value = mock_store_instance

    # Mock the homeassistant.helpers.storage module
    mock_storage_module = Mock()
    mock_storage_module.Store = mock_store_class

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should migrate to v3 format with zone-keyed storage
        assert data["version"] == 3
        assert "zones" in data
        assert "default_zone" in data["zones"]

        # Check that old data was migrated to default_zone
        default_zone = data["zones"]["default_zone"]
        assert "thermal_learner" in default_zone
        assert default_zone["thermal_learner"]["cooling_rates"] == [0.5, 0.6]
        assert "adaptive_learner" in default_zone
        assert len(default_zone["adaptive_learner"]["cycle_history"]) == 1
        assert "valve_tracker" in default_zone
        assert default_zone["valve_tracker"]["cycle_count"] == 2


@pytest.mark.asyncio
async def test_learning_store_get_zone_data(mock_hass):
    """Test get_zone_data returns correct zone dict or None."""
    v3_data = {
        "version": 3,
        "zones": {
            "living_room": {
                "thermal_learner": {"cooling_rates": [0.5], "heating_rates": [2.0], "outlier_threshold": 2.0}
            },
            "bedroom": {
                "thermal_learner": {"cooling_rates": [0.4], "heating_rates": [1.8], "outlier_threshold": 2.0}
            },
        },
    }

    # Create mock Store class
    mock_store_class = Mock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value=v3_data)
    mock_store_class.return_value = mock_store_instance

    # Mock the homeassistant.helpers.storage module
    mock_storage_module = Mock()
    mock_storage_module.Store = mock_store_class

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        await store.async_load()

        # Test getting existing zones
        living_room_data = store.get_zone_data("living_room")
        assert living_room_data is not None
        assert "thermal_learner" in living_room_data
        assert living_room_data["thermal_learner"]["cooling_rates"] == [0.5]

        bedroom_data = store.get_zone_data("bedroom")
        assert bedroom_data is not None
        assert bedroom_data["thermal_learner"]["heating_rates"] == [1.8]

        # Test getting non-existent zone
        missing_zone = store.get_zone_data("non_existent")
        assert missing_zone is None


# Story 2.2 tests: async_save_zone() and schedule_zone_save()


@pytest.mark.asyncio
async def test_learning_store_async_save_zone(mock_hass):
    """Test async_save_zone saves zone data with lock protection."""
    # Create mock Store class
    mock_store_class = Mock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value=None)
    mock_store_instance.async_save = AsyncMock()
    mock_store_class.return_value = mock_store_instance

    # Mock the homeassistant.helpers.storage module
    mock_storage_module = Mock()
    mock_storage_module.Store = mock_store_class

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        await store.async_load()

        # Create sample zone data
        adaptive_data = {
            "cycle_history": [
                {"overshoot": 0.3, "undershoot": 0.2, "settling_time": 45.0, "oscillations": 1, "rise_time": 30.0}
            ],
            "last_adjustment_time": None,
            "max_history": 50,
            "heating_type": "radiator",
        }
        ke_data = {
            "current_ke": 0.5,
            "enabled": True,
            "observation_count": 10,
        }

        # Save zone data
        await store.async_save_zone("living_room", adaptive_data, ke_data)

        # Verify Store.async_save was called
        mock_store_instance.async_save.assert_called_once()

        # Verify internal data structure
        assert "living_room" in store._data["zones"]
        zone_data = store._data["zones"]["living_room"]
        assert "adaptive_learner" in zone_data
        assert zone_data["adaptive_learner"] == adaptive_data
        assert "ke_learner" in zone_data
        assert zone_data["ke_learner"] == ke_data
        assert "last_updated" in zone_data


@pytest.mark.asyncio
async def test_learning_store_schedule_zone_save(mock_hass):
    """Test schedule_zone_save uses async_delay_save with 30s delay."""
    # Create mock Store class
    mock_store_class = Mock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value=None)
    mock_store_instance.async_delay_save = Mock()
    mock_store_class.return_value = mock_store_instance

    # Mock the homeassistant.helpers.storage module
    mock_storage_module = Mock()
    mock_storage_module.Store = mock_store_class

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        await store.async_load()

        # Schedule a zone save
        store.schedule_zone_save()

        # Verify async_delay_save was called with 30s delay
        mock_store_instance.async_delay_save.assert_called_once()
        # Check that the delay parameter is 30 seconds
        call_kwargs = mock_store_instance.async_delay_save.call_args
        assert call_kwargs is not None
        # Store.async_delay_save signature: async_delay_save(self, delay=None)
        # where delay is in seconds (default: None uses Store's default)
        # Our implementation should pass delay=30


@pytest.mark.asyncio
async def test_learning_store_concurrent_saves(mock_hass):
    """Test lock prevents race conditions during concurrent saves."""
    import asyncio

    # Create mock Store class
    mock_store_class = Mock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value=None)

    # Make async_save slow to simulate concurrent access
    async def slow_async_save(data):
        await asyncio.sleep(0.1)  # 100ms delay

    mock_store_instance.async_save = AsyncMock(side_effect=slow_async_save)
    mock_store_class.return_value = mock_store_instance

    # Mock the homeassistant.helpers.storage module
    mock_storage_module = Mock()
    mock_storage_module.Store = mock_store_class

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        await store.async_load()

        # Create sample zone data for two zones
        adaptive_data_1 = {"cycle_history": [{"overshoot": 0.3}]}
        ke_data_1 = {"current_ke": 0.5}

        adaptive_data_2 = {"cycle_history": [{"overshoot": 0.4}]}
        ke_data_2 = {"current_ke": 0.6}

        # Launch concurrent saves
        task1 = asyncio.create_task(
            store.async_save_zone("zone_1", adaptive_data_1, ke_data_1)
        )
        task2 = asyncio.create_task(
            store.async_save_zone("zone_2", adaptive_data_2, ke_data_2)
        )

        # Wait for both to complete
        await asyncio.gather(task1, task2)

        # Verify both zones were saved correctly (no race condition)
        assert "zone_1" in store._data["zones"]
        assert "zone_2" in store._data["zones"]
        assert store._data["zones"]["zone_1"]["adaptive_learner"] == adaptive_data_1
        assert store._data["zones"]["zone_2"]["adaptive_learner"] == adaptive_data_2

        # async_save should have been called twice (once per zone save)
        assert mock_store_instance.async_save.call_count == 2


def test_update_zone_data_new_zone(mock_hass):
    """Test update_zone_data creates zone if doesn't exist."""
    store = LearningDataStore(mock_hass)

    # Verify zone doesn't exist yet
    assert "new_zone" not in store._data["zones"]

    # Update zone data
    adaptive_data = {"cycle_history": [{"overshoot": 0.3}]}
    store.update_zone_data("new_zone", adaptive_data=adaptive_data)

    # Verify zone was created
    assert "new_zone" in store._data["zones"]
    assert store._data["zones"]["new_zone"]["adaptive_learner"] == adaptive_data
    assert "last_updated" in store._data["zones"]["new_zone"]


def test_update_zone_data_existing_zone(mock_hass):
    """Test update_zone_data updates existing zone."""
    store = LearningDataStore(mock_hass)

    # Create initial zone data
    store._data["zones"]["existing_zone"] = {
        "adaptive_learner": {"cycle_history": []},
        "ke_learner": {"current_ke": 0.3},
    }

    # Update only adaptive data
    new_adaptive_data = {"cycle_history": [{"overshoot": 0.5}]}
    store.update_zone_data("existing_zone", adaptive_data=new_adaptive_data)

    # Verify adaptive data was updated but ke_learner preserved
    assert store._data["zones"]["existing_zone"]["adaptive_learner"] == new_adaptive_data
    assert store._data["zones"]["existing_zone"]["ke_learner"]["current_ke"] == 0.3


def test_update_zone_data_does_not_trigger_save(mock_hass):
    """Test update_zone_data does not trigger disk save."""
    # Create mock Store class
    mock_store_class = Mock()
    mock_store_instance = Mock()
    mock_store_instance.async_save = Mock()
    mock_store_instance.async_delay_save = Mock()
    mock_store_class.return_value = mock_store_instance

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': Mock(Store=mock_store_class)}):
        store = LearningDataStore(mock_hass)
        store._store = mock_store_instance  # Simulate initialized store

        # Update zone data
        store.update_zone_data("test_zone", adaptive_data={"cycle_history": []})

        # Verify no save methods were called
        mock_store_instance.async_save.assert_not_called()
        mock_store_instance.async_delay_save.assert_not_called()


def test_update_zone_data_with_ke_data(mock_hass):
    """Test update_zone_data can update ke_data."""
    store = LearningDataStore(mock_hass)

    adaptive_data = {"cycle_history": []}
    ke_data = {"current_ke": 0.5, "observations": []}

    store.update_zone_data(
        zone_id="test_zone",
        adaptive_data=adaptive_data,
        ke_data=ke_data,
    )

    # Verify both were updated
    assert store._data["zones"]["test_zone"]["adaptive_learner"] == adaptive_data
    assert store._data["zones"]["test_zone"]["ke_learner"] == ke_data
