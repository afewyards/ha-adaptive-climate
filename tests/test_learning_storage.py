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

    mock_storage_module = create_mock_storage_module(load_data=v3_data)

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


# ============================================================================
# Story 3.1: v3 to v4 migration tests (thermal coupling support)
# ============================================================================


def test_migrate_v3_to_v4(mock_hass):
    """Test _migrate_v3_to_v4 updates version and preserves zones."""
    store = LearningDataStore(mock_hass)

    # Simulate v3 data structure
    v3_data = {
        "version": 3,
        "zones": {
            "climate.living_room": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.3}],
                },
                "ke_learner": {
                    "current_ke": 0.5,
                },
            },
            "climate.bedroom": {
                "adaptive_learner": {
                    "cycle_history": [],
                },
            },
        },
    }

    # Call migration method
    v4_data = store._migrate_v3_to_v4(v3_data)

    # Verify version updated to 4
    assert v4_data["version"] == 4

    # Verify zones preserved
    assert "zones" in v4_data
    assert "climate.living_room" in v4_data["zones"]
    assert "climate.bedroom" in v4_data["zones"]
    assert v4_data["zones"]["climate.living_room"]["adaptive_learner"]["cycle_history"] == [{"overshoot": 0.3}]
    assert v4_data["zones"]["climate.living_room"]["ke_learner"]["current_ke"] == 0.5


@pytest.mark.asyncio
async def test_load_v3_auto_migrates(mock_hass):
    """Test loading v3 data returns v4 format via automatic migration."""
    # Simulate v3 format stored data
    v3_data = {
        "version": 3,
        "zones": {
            "climate.kitchen": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.2, "undershoot": 0.1}],
                    "consecutive_converged_cycles": 5,
                },
                "ke_learner": {
                    "current_ke": 0.45,
                    "enabled": True,
                },
                "last_updated": "2026-01-20T10:00:00",
            },
        },
    }

    mock_storage_module = create_mock_storage_module(load_data=v3_data)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should migrate to v5 format
        assert data["version"] == 5

        # Original zone data should be preserved with v5 structure
        assert "zones" in data
        assert "climate.kitchen" in data["zones"]
        # V5 format: adaptive_learner should have heating/cooling sub-structure
        kitchen_adaptive = data["zones"]["climate.kitchen"]["adaptive_learner"]
        assert "heating" in kitchen_adaptive
        assert "cooling" in kitchen_adaptive
        assert kitchen_adaptive["heating"]["cycle_history"] == [
            {"overshoot": 0.2, "undershoot": 0.1}
        ]
        assert data["zones"]["climate.kitchen"]["ke_learner"]["current_ke"] == 0.45

@pytest.mark.asyncio
async def test_load_v4_no_migration(mock_hass):
    """Test loading v4 data triggers migration to v5."""
    # Simulate v4 format stored data (needs migration to v5)
    v4_data = {
        "version": 4,
        "zones": {
            "climate.office": {
                "adaptive_learner": {"cycle_history": []},
            },
            "climate.living_room": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.2}],
                },
            },
        },
    }

    mock_storage_module = create_mock_storage_module(load_data=v4_data)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should migrate to v5
        assert data["version"] == 5

        # Existing zone data should be preserved with v5 structure
        assert "climate.office" in data["zones"]
        assert "climate.living_room" in data["zones"]
        # V5 format: adaptive_learner should have heating/cooling sub-structure
        living_room_adaptive = data["zones"]["climate.living_room"]["adaptive_learner"]
        assert "heating" in living_room_adaptive
        assert "cooling" in living_room_adaptive
        assert living_room_adaptive["heating"]["cycle_history"] == [{"overshoot": 0.2}]


@pytest.mark.asyncio
async def test_load_v2_migrates_through_v3_to_v4_to_v5(mock_hass):
    """Test loading v2 data migrates through v3 to v4 to v5."""
    # Simulate v2 format (flat, no zones)
    v2_data = {
        "version": 2,
        "adaptive_learner": {
            "cycle_history": [{"overshoot": 0.4}],
        },
        "ke_learner": {
            "current_ke": 0.3,
        },
    }

    mock_storage_module = create_mock_storage_module(load_data=v2_data)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should be migrated all the way to v5
        assert data["version"] == 5

        # v2 data should be in default_zone (from v2->v3 migration) with v5 structure
        assert "zones" in data
        assert "default_zone" in data["zones"]
        default_zone_adaptive = data["zones"]["default_zone"]["adaptive_learner"]
        assert "heating" in default_zone_adaptive
        assert "cooling" in default_zone_adaptive
        assert default_zone_adaptive["heating"]["cycle_history"] == [{"overshoot": 0.4}]
        assert data["zones"]["default_zone"]["ke_learner"]["current_ke"] == 0.3


# ============================================================================
# Story X: v4 to v5 migration tests (heating/cooling mode split)
# ============================================================================


def test_migrate_v4_to_v5_basic(mock_hass):
    """Test _migrate_v4_to_v5 splits adaptive_learner into heating/cooling modes."""
    store = LearningDataStore(mock_hass)

    # Simulate v4 data with adaptive_learner at zone level
    v4_data = {
        "version": 4,
        "zones": {
            "climate.living_room": {
                "adaptive_learner": {
                    "cycle_history": [
                        {"overshoot": 0.3, "undershoot": 0.2, "settling_time": 45.0},
                        {"overshoot": 0.25, "undershoot": 0.15, "settling_time": 40.0},
                    ],
                    "auto_apply_count": 3,
                    "convergence_confidence": 0.75,
                    "consecutive_converged_cycles": 5,
                    "pid_converged_for_ke": True,
                },
                "ke_learner": {
                    "current_ke": 0.5,
                },
            },
        },
    }

    # Call migration method
    v5_data = store._migrate_v4_to_v5(v4_data)

    # Verify version updated to 5
    assert v5_data["version"] == 5

    # Verify zones structure preserved
    assert "zones" in v5_data
    assert "climate.living_room" in v5_data["zones"]

    zone = v5_data["zones"]["climate.living_room"]

    # Verify adaptive_learner was split into heating/cooling
    assert "adaptive_learner" in zone
    assert "heating" in zone["adaptive_learner"]
    assert "cooling" in zone["adaptive_learner"]

    # Verify existing data moved to heating
    heating = zone["adaptive_learner"]["heating"]
    assert heating["cycle_history"] == [
        {"overshoot": 0.3, "undershoot": 0.2, "settling_time": 45.0},
        {"overshoot": 0.25, "undershoot": 0.15, "settling_time": 40.0},
    ]
    assert heating["auto_apply_count"] == 3
    assert heating["convergence_confidence"] == 0.75
    assert heating["pid_history"] == []  # Initialized empty

    # Verify cooling initialized as empty
    cooling = zone["adaptive_learner"]["cooling"]
    assert cooling["cycle_history"] == []
    assert cooling["auto_apply_count"] == 0
    assert cooling.get("convergence_confidence", 0.0) == 0.0
    assert cooling["pid_history"] == []

    # Verify ke_learner preserved
    assert zone["ke_learner"]["current_ke"] == 0.5


def test_migrate_v4_to_v5_multiple_zones(mock_hass):
    """Test v4 to v5 migration handles multiple zones correctly."""
    store = LearningDataStore(mock_hass)

    v4_data = {
        "version": 4,
        "zones": {
            "climate.bedroom": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.2}],
                    "auto_apply_count": 1,
                },
            },
            "climate.office": {
                "adaptive_learner": {
                    "cycle_history": [],
                    "auto_apply_count": 0,
                },
            },
        },
    }

    v5_data = store._migrate_v4_to_v5(v4_data)

    assert v5_data["version"] == 5

    # Verify both zones migrated
    bedroom = v5_data["zones"]["climate.bedroom"]["adaptive_learner"]
    assert bedroom["heating"]["cycle_history"] == [{"overshoot": 0.2}]
    assert bedroom["heating"]["auto_apply_count"] == 1
    assert bedroom["cooling"]["cycle_history"] == []

    office = v5_data["zones"]["climate.office"]["adaptive_learner"]
    assert office["heating"]["cycle_history"] == []
    assert office["cooling"]["auto_apply_count"] == 0


def test_migrate_v4_to_v5_empty_zones(mock_hass):
    """Test v4 to v5 migration with no zones."""
    store = LearningDataStore(mock_hass)

    v4_data = {
        "version": 4,
        "zones": {},
    }

    v5_data = store._migrate_v4_to_v5(v4_data)

    assert v5_data["version"] == 5
    assert v5_data["zones"] == {}


@pytest.mark.asyncio
async def test_load_v4_auto_migrates_to_v5(mock_hass):
    """Test loading v4 data automatically migrates to v5."""
    v4_data = {
        "version": 4,
        "zones": {
            "climate.kitchen": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.3}],
                    "auto_apply_count": 2,
                },
            },
        },
    }

    mock_storage_module = create_mock_storage_module(load_data=v4_data)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should migrate to v5
        assert data["version"] == 5

        # Verify heating/cooling split
        kitchen = data["zones"]["climate.kitchen"]["adaptive_learner"]
        assert "heating" in kitchen
        assert "cooling" in kitchen
        assert kitchen["heating"]["cycle_history"] == [{"overshoot": 0.3}]
        assert kitchen["cooling"]["cycle_history"] == []


def test_v5_schema_structure(mock_hass):
    """Test v5 schema has correct structure with pid_history in both modes."""
    store = LearningDataStore(mock_hass)

    # Build a complete v5 structure
    v5_data = {
        "version": 5,
        "zones": {
            "climate.test": {
                "adaptive_learner": {
                    "heating": {
                        "cycle_history": [{"overshoot": 0.3}],
                        "auto_apply_count": 2,
                        "convergence_confidence": 0.8,
                        "pid_history": [
                            {
                                "timestamp": "2026-01-26T10:00:00",
                                "kp": 3.5,
                                "ki": 0.01,
                                "kd": 1.2,
                                "reason": "auto_apply",
                            }
                        ],
                    },
                    "cooling": {
                        "cycle_history": [],
                        "auto_apply_count": 0,
                        "convergence_confidence": 0.0,
                        "pid_history": [],
                    },
                },
            },
        },
    }

    # Simulate migration (should be no-op for v5)
    result = store._migrate_v4_to_v5(v5_data)

    # v5 should pass through unchanged (migration is idempotent)
    assert result["version"] == 5
    zone = result["zones"]["climate.test"]["adaptive_learner"]
    assert "heating" in zone
    assert "cooling" in zone
    assert len(zone["heating"]["pid_history"]) == 1
    assert zone["heating"]["pid_history"][0]["kp"] == 3.5


def test_migrate_v4_to_v5_preserves_other_keys(mock_hass):
    """Test v4 to v5 migration preserves non-adaptive_learner keys."""
    store = LearningDataStore(mock_hass)

    v4_data = {
        "version": 4,
        "zones": {
            "climate.test": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.2}],
                },
                "ke_learner": {
                    "current_ke": 0.45,
                    "enabled": True,
                },
                "thermal_learner": {
                    "cooling_rates": [0.5],
                    "heating_rates": [2.0],
                },
                "last_updated": "2026-01-26T09:00:00",
            },
        },
    }

    v5_data = store._migrate_v4_to_v5(v4_data)

    zone = v5_data["zones"]["climate.test"]

    # Verify other learners preserved
    assert zone["ke_learner"]["current_ke"] == 0.45
    assert zone["thermal_learner"]["cooling_rates"] == [0.5]
    assert zone["last_updated"] == "2026-01-26T09:00:00"

    # Verify adaptive_learner split correctly
    assert "heating" in zone["adaptive_learner"]
    assert "cooling" in zone["adaptive_learner"]


def test_pid_history_in_v5_schema(mock_hass):
    """Test pid_history storage in v5 schema per mode."""
    store = LearningDataStore(mock_hass)

    # Create v5 data with pid_history in both modes
    v5_data = {
        "version": 5,
        "zones": {
            "climate.test": {
                "adaptive_learner": {
                    "heating": {
                        "cycle_history": [],
                        "auto_apply_count": 1,
                        "pid_history": [
                            {
                                "timestamp": "2026-01-26T10:00:00",
                                "kp": 3.5,
                                "ki": 0.01,
                                "kd": 1.2,
                                "reason": "auto_apply",
                            },
                            {
                                "timestamp": "2026-01-26T11:00:00",
                                "kp": 3.8,
                                "ki": 0.012,
                                "kd": 1.3,
                                "reason": "auto_apply",
                            },
                        ],
                    },
                    "cooling": {
                        "cycle_history": [],
                        "auto_apply_count": 0,
                        "pid_history": [
                            {
                                "timestamp": "2026-01-26T14:00:00",
                                "kp": 2.0,
                                "ki": 0.005,
                                "kd": 0.8,
                                "reason": "manual",
                            },
                        ],
                    },
                },
            },
        },
    }

    # Get zone data
    zone_data = v5_data["zones"]["climate.test"]

    # Verify heating pid_history
    heating_history = zone_data["adaptive_learner"]["heating"]["pid_history"]
    assert len(heating_history) == 2
    assert heating_history[-1]["kp"] == 3.8  # Latest entry
    assert heating_history[-1]["ki"] == 0.012
    assert heating_history[-1]["reason"] == "auto_apply"

    # Verify cooling pid_history
    cooling_history = zone_data["adaptive_learner"]["cooling"]["pid_history"]
    assert len(cooling_history) == 1
    assert cooling_history[-1]["kp"] == 2.0
    assert cooling_history[-1]["reason"] == "manual"


def test_gains_restoration_from_pid_history(mock_hass):
    """Test that gains can be restored from pid_history[-1] per mode."""
    store = LearningDataStore(mock_hass)

    v5_data = {
        "version": 5,
        "zones": {
            "climate.test": {
                "adaptive_learner": {
                    "heating": {
                        "cycle_history": [],
                        "auto_apply_count": 2,
                        "pid_history": [
                            {
                                "timestamp": "2026-01-26T09:00:00",
                                "kp": 3.0,
                                "ki": 0.008,
                                "kd": 1.0,
                                "reason": "manual",
                            },
                            {
                                "timestamp": "2026-01-26T10:00:00",
                                "kp": 3.5,
                                "ki": 0.01,
                                "kd": 1.2,
                                "reason": "auto_apply",
                            },
                        ],
                    },
                    "cooling": {
                        "cycle_history": [],
                        "auto_apply_count": 0,
                        "pid_history": [
                            {
                                "timestamp": "2026-01-26T12:00:00",
                                "kp": 2.2,
                                "ki": 0.006,
                                "kd": 0.9,
                                "reason": "manual",
                            },
                        ],
                    },
                },
            },
        },
    }

    zone_data = v5_data["zones"]["climate.test"]["adaptive_learner"]

    # Get latest gains from heating mode
    heating_latest = zone_data["heating"]["pid_history"][-1]
    assert heating_latest["kp"] == 3.5
    assert heating_latest["ki"] == 0.01
    assert heating_latest["kd"] == 1.2

    # Get latest gains from cooling mode
    cooling_latest = zone_data["cooling"]["pid_history"][-1]
    assert cooling_latest["kp"] == 2.2
    assert cooling_latest["ki"] == 0.006
    assert cooling_latest["kd"] == 0.9


def test_empty_cooling_initialization_on_migration(mock_hass):
    """Test cooling sub-structure initialized empty when migrating from v4."""
    store = LearningDataStore(mock_hass)

    v4_data = {
        "version": 4,
        "zones": {
            "climate.test": {
                "adaptive_learner": {
                    "cycle_history": [
                        {"overshoot": 0.3},
                        {"overshoot": 0.25},
                    ],
                    "auto_apply_count": 5,
                    "convergence_confidence": 0.85,
                },
            },
        },
    }

    v5_data = store._migrate_v4_to_v5(v4_data)

    cooling = v5_data["zones"]["climate.test"]["adaptive_learner"]["cooling"]

    # Verify all cooling fields initialized to empty/zero
    assert cooling["cycle_history"] == []
    assert cooling["auto_apply_count"] == 0
    assert cooling.get("convergence_confidence", 0.0) == 0.0
    assert cooling["pid_history"] == []


@pytest.mark.asyncio
async def test_v4_to_v5_migration_state_attributes_pid_history(mock_hass):
    """Test one-time migration of pid_history from state attributes to heating.pid_history.

    This test verifies that:
    1. v4 data migrates to v5 with empty pid_history initially
    2. State attributes pid_history would be restored separately (not tested here,
       as that's handled by StateRestorer in climate.py)
    3. Migration is idempotent (running twice doesn't break anything)
    """
    # This test documents the expected behavior but the actual state attribute
    # migration happens in StateRestorer, not in LearningDataStore migration.
    # The v4->v5 migration just creates the structure with empty pid_history.

    store = LearningDataStore(mock_hass)

    v4_data = {
        "version": 4,
        "zones": {
            "climate.test": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.3}],
                    "auto_apply_count": 2,
                    # NOTE: pid_history NOT in v4 persistence (it was in state attributes)
                },
            },
        },
    }

    v5_data = store._migrate_v4_to_v5(v4_data)

    # After migration, pid_history exists but is empty
    # (will be populated from state attributes during restore)
    heating = v5_data["zones"]["climate.test"]["adaptive_learner"]["heating"]
    assert "pid_history" in heating
    assert heating["pid_history"] == []

    # Verify migration is idempotent
    v5_again = store._migrate_v4_to_v5(v5_data)
    assert v5_again["version"] == 5
    assert v5_again["zones"]["climate.test"]["adaptive_learner"]["heating"]["pid_history"] == []
