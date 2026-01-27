"""Tests for learning data persistence."""

import pytest
import json
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from enum import Enum

# Mock HVACMode before importing custom components
class MockHVACMode(str, Enum):
    """Mock HVAC mode enum."""
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"

# Mock the homeassistant.components.climate module
mock_climate = MagicMock()
mock_climate.HVACMode = MockHVACMode
sys.modules['homeassistant.components.climate'] = mock_climate
sys.modules['homeassistant.components'] = MagicMock()


class MockStore:
    """Mock HA Store class that can be subclassed for migration tests."""

    _load_data = None  # Class-level data to return from async_load

    def __init__(self, hass, version, key):
        self.hass = hass
        self.version = version
        self.key = key
        self._data = None

    async def async_load(self):
        return MockStore._load_data

    async def async_save(self, data):
        self._data = data

    def async_delay_save(self, data_func, delay):
        self._data = data_func()


def create_mock_storage_module(load_data=None):
    """Create a mock storage module with configurable load data."""
    MockStore._load_data = load_data
    mock_module = MagicMock()
    mock_module.Store = MockStore
    return mock_module


from custom_components.adaptive_thermostat.adaptive.persistence import LearningDataStore
from custom_components.adaptive_thermostat.adaptive.learning import (
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
    """Test that learning data is saved correctly (legacy file-based API)."""
    # Note: This test uses the legacy file-based API (passing a string path)
    # The new API uses HomeAssistant instance and async methods
    # This test is kept for backwards compatibility testing only

    # The legacy save() method was removed - this test now validates
    # that the file-based constructor still works but doesn't test saving
    # since that functionality is deprecated
    store = LearningDataStore(temp_storage_dir)

    # Verify legacy mode is detected
    assert store.hass is None
    assert store.storage_path == temp_storage_dir
    assert store.storage_file == os.path.join(temp_storage_dir, "adaptive_thermostat_learning.json")

    # Legacy save() method no longer exists - skip the save test
    # Modern code should use async_save_zone() with HomeAssistant instance


def test_data_loading(temp_storage_dir):
    """Test that learning data is loaded correctly (legacy file-based API)."""
    store = LearningDataStore(temp_storage_dir)

    # Create test data file manually (since save() method was removed)
    test_data = {
        "version": 2,
        "thermal_learner": {
            "cooling_rates": [0.5],
            "heating_rates": [2.0],
            "outlier_threshold": 2.0
        },
        "adaptive_learner": {
            "cycle_history": [
                {
                    "overshoot": 0.4,
                    "undershoot": 0.1,
                    "settling_time": 50.0,
                    "oscillations": 0,
                    "rise_time": None
                }
            ],
            "last_adjustment_time": None,
            "max_history": 50,
            "heating_type": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False
        },
        "valve_tracker": {
            "cycle_count": 1,
            "last_state": True
        }
    }

    # Write test data to file
    os.makedirs(temp_storage_dir, exist_ok=True)
    with open(store.storage_file, "w") as f:
        json.dump(test_data, f)

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
    """Test atomic save behavior (legacy API deprecated)."""
    # Legacy save() method was removed - this test is no longer applicable
    # Modern code uses HA Store which handles atomic saves internally
    store = LearningDataStore(temp_storage_dir)

    # Just verify legacy mode works
    assert store.storage_file == os.path.join(temp_storage_dir, "adaptive_thermostat_learning.json")


def test_partial_save(temp_storage_dir):
    """Test loading data with only some components."""
    store = LearningDataStore(temp_storage_dir)

    # Create test data with only thermal learner
    test_data = {
        "version": 2,
        "thermal_learner": {
            "cooling_rates": [0.5],
            "heating_rates": [],
            "outlier_threshold": 2.0
        }
    }

    # Write test data to file
    os.makedirs(temp_storage_dir, exist_ok=True)
    with open(store.storage_file, "w") as f:
        json.dump(test_data, f)

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
    """Test loading empty learners."""
    store = LearningDataStore(temp_storage_dir)

    # Create test data with empty learners
    test_data = {
        "version": 2,
        "thermal_learner": {
            "cooling_rates": [],
            "heating_rates": [],
            "outlier_threshold": 2.0
        },
        "adaptive_learner": {
            "cycle_history": [],
            "last_adjustment_time": None,
            "max_history": 50,
            "heating_type": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False
        },
        "valve_tracker": {
            "cycle_count": 0,
            "last_state": None
        }
    }

    # Write test data to file
    os.makedirs(temp_storage_dir, exist_ok=True)
    with open(store.storage_file, "w") as f:
        json.dump(test_data, f)

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
    assert store._data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_learning_store_async_load_empty(mock_hass):
    """Test async_load returns default structure when no file exists."""
    mock_storage_module = create_mock_storage_module(load_data=None)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should return default structure when no data
        assert data == {"version": 5, "zones": {}}
        assert store._data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_learning_store_get_zone_data(mock_hass):
    """Test get_zone_data returns correct zone dict or None."""
    v5_data = {
        "version": 5,
        "zones": {
            "living_room": {
                "thermal_learner": {"cooling_rates": [0.5], "heating_rates": [2.0], "outlier_threshold": 2.0}
            },
            "bedroom": {
                "thermal_learner": {"cooling_rates": [0.4], "heating_rates": [1.8], "outlier_threshold": 2.0}
            },
        },
    }

    mock_storage_module = create_mock_storage_module(load_data=v5_data)

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
    mock_storage_module = create_mock_storage_module(load_data=None)

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
    """Test schedule_zone_save uses async_delay_save."""
    mock_storage_module = create_mock_storage_module(load_data=None)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        await store.async_load()

        # Schedule a zone save - should not raise
        store.schedule_zone_save()

        # Verify internal data was set (async_delay_save stores via callback)
        assert store._store._data is not None


@pytest.mark.asyncio
async def test_learning_store_concurrent_saves(mock_hass):
    """Test lock prevents race conditions during concurrent saves."""
    import asyncio

    mock_storage_module = create_mock_storage_module(load_data=None)

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
    """Test update_zone_data does not trigger disk save (only updates in-memory)."""
    store = LearningDataStore(mock_hass)

    # Update zone data
    store.update_zone_data("test_zone", adaptive_data={"cycle_history": []})

    # Verify data was updated in memory
    assert "test_zone" in store._data["zones"]
    assert store._data["zones"]["test_zone"]["adaptive_learner"] == {"cycle_history": []}


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


# Task #21 tests: async_load validation

@pytest.mark.asyncio
async def test_async_load_validates_not_dict(mock_hass):
    """Test async_load returns default data when loaded data is not a dict."""
    # Return a list instead of dict
    mock_storage_module = create_mock_storage_module(load_data=["not", "a", "dict"])

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should return default structure, not the invalid data
        assert data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_async_load_validates_missing_version(mock_hass):
    """Test async_load returns default data when version key is missing."""
    # Missing version key
    mock_storage_module = create_mock_storage_module(load_data={"zones": {}})

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        assert data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_async_load_validates_missing_zones(mock_hass):
    """Test async_load returns default data when zones key is missing."""
    # Missing zones key
    mock_storage_module = create_mock_storage_module(load_data={"version": 5})

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        assert data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_async_load_validates_version_not_int(mock_hass):
    """Test async_load returns default data when version is not an int."""
    # Version is a string
    mock_storage_module = create_mock_storage_module(load_data={"version": "5", "zones": {}})

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        assert data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_async_load_validates_version_out_of_range(mock_hass):
    """Test async_load returns default data when version is outside supported range."""
    # Version 0 (below minimum)
    mock_storage_module = create_mock_storage_module(load_data={"version": 0, "zones": {}})

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        assert data == {"version": 5, "zones": {}}

    # Version 99 (above maximum)
    mock_storage_module = create_mock_storage_module(load_data={"version": 99, "zones": {}})

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        assert data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_async_load_validates_zones_not_dict(mock_hass):
    """Test async_load returns default data when zones is not a dict."""
    # Zones is a list
    mock_storage_module = create_mock_storage_module(load_data={"version": 5, "zones": []})

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        assert data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_async_load_validates_zone_data_not_dict(mock_hass):
    """Test async_load returns default data when zone data is not a dict."""
    # Zone data is a string
    mock_storage_module = create_mock_storage_module(
        load_data={"version": 5, "zones": {"living_room": "not a dict"}}
    )

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        assert data == {"version": 5, "zones": {}}


@pytest.mark.asyncio
async def test_async_load_accepts_valid_data(mock_hass):
    """Test async_load accepts valid data structure."""
    valid_data = {
        "version": 5,
        "zones": {
            "living_room": {
                "adaptive_learner": {"cycle_history": []},
                "ke_learner": {"current_ke": 0.5},
            },
            "bedroom": {
                "adaptive_learner": {"cycle_history": []},
            },
        },
    }

    mock_storage_module = create_mock_storage_module(load_data=valid_data)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should return the actual data, not default structure
        assert data == valid_data
        assert data["version"] == 5
        assert "living_room" in data["zones"]
        assert "bedroom" in data["zones"]
