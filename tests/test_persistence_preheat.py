"""Tests for preheat persistence support."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from custom_components.adaptive_thermostat.adaptive.persistence import LearningDataStore
from custom_components.adaptive_thermostat.adaptive.preheat import PreheatLearner


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


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.config = MagicMock()
    hass.config.path = MagicMock(return_value="/mock/config")
    return hass


def test_update_zone_data_with_preheat_data(mock_hass):
    """Test update_zone_data accepts and stores preheat_data parameter."""
    store = LearningDataStore(mock_hass)

    adaptive_data = {"cycle_history": []}
    ke_data = {"current_ke": 0.5}
    preheat_data = {
        "heating_type": "radiator",
        "max_hours": 2.0,
        "observations": [],
    }

    store.update_zone_data(
        zone_id="test_zone",
        adaptive_data=adaptive_data,
        ke_data=ke_data,
        preheat_data=preheat_data,
    )

    # Verify all data was updated
    assert store._data["zones"]["test_zone"]["adaptive_learner"] == adaptive_data
    assert store._data["zones"]["test_zone"]["ke_learner"] == ke_data
    assert store._data["zones"]["test_zone"]["preheat_learner"] == preheat_data
    assert "last_updated" in store._data["zones"]["test_zone"]


@pytest.mark.asyncio
async def test_async_save_zone_with_preheat_data(mock_hass):
    """Test async_save_zone persists preheat_data alongside other data."""
    mock_storage_module = create_mock_storage_module(load_data=None)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        await store.async_load()

        # Create sample zone data including preheat
        adaptive_data = {
            "cycle_history": [
                {"overshoot": 0.3, "undershoot": 0.2, "settling_time": 45.0}
            ],
        }
        ke_data = {
            "current_ke": 0.5,
            "enabled": True,
        }
        preheat_data = {
            "heating_type": "floor_hydronic",
            "max_hours": 4.0,
            "observations": [
                {
                    "bin_key": ["2-4", "mild"],
                    "start_temp": 18.0,
                    "end_temp": 20.0,
                    "outdoor_temp": 8.0,
                    "duration_minutes": 60.0,
                    "rate": 2.0,
                    "timestamp": "2026-01-20T10:00:00",
                }
            ],
        }

        # Save zone data
        await store.async_save_zone("living_room", adaptive_data, ke_data, preheat_data)

        # Verify internal data structure
        assert "living_room" in store._data["zones"]
        zone_data = store._data["zones"]["living_room"]
        assert "adaptive_learner" in zone_data
        assert zone_data["adaptive_learner"] == adaptive_data
        assert "ke_learner" in zone_data
        assert zone_data["ke_learner"] == ke_data
        assert "preheat_learner" in zone_data
        assert zone_data["preheat_learner"] == preheat_data
        assert "last_updated" in zone_data


def test_get_zone_data_returns_preheat_field(mock_hass):
    """Test get_zone_data returns preheat_data field when present."""
    store = LearningDataStore(mock_hass)

    # Create zone with preheat data
    preheat_data = {
        "heating_type": "radiator",
        "max_hours": 2.0,
        "observations": [],
    }

    store._data["zones"]["test_zone"] = {
        "adaptive_learner": {"cycle_history": []},
        "ke_learner": {"current_ke": 0.5},
        "preheat_learner": preheat_data,
    }

    # Get zone data
    zone_data = store.get_zone_data("test_zone")

    # Verify preheat_learner is present
    assert zone_data is not None
    assert "preheat_learner" in zone_data
    assert zone_data["preheat_learner"] == preheat_data


def test_restore_preheat_learner(mock_hass):
    """Test restore_preheat_learner recreates PreheatLearner from saved dict."""
    store = LearningDataStore(mock_hass)

    # Create sample preheat data
    timestamp = datetime(2026, 1, 20, 10, 0, 0)
    preheat_data = {
        "heating_type": "radiator",
        "max_hours": 2.0,
        "observations": [
            {
                "bin_key": ["2-4", "mild"],
                "start_temp": 18.0,
                "end_temp": 20.0,
                "outdoor_temp": 8.0,
                "duration_minutes": 60.0,
                "rate": 2.0,
                "timestamp": timestamp.isoformat(),
            }
        ],
    }

    zone_data = {
        "preheat_learner": preheat_data,
    }

    # Restore PreheatLearner
    restored = store.restore_preheat_learner(zone_data)

    # Verify restoration
    assert restored is not None
    assert isinstance(restored, PreheatLearner)
    assert restored.heating_type == "radiator"
    assert restored.max_hours == 2.0
    assert restored.get_observation_count() == 1

    # Verify observations were restored
    bin_key = ("2-4", "mild")
    assert bin_key in restored._observations
    assert len(restored._observations[bin_key]) == 1
    obs = restored._observations[bin_key][0]
    assert obs.start_temp == 18.0
    assert obs.end_temp == 20.0
    assert obs.outdoor_temp == 8.0
    assert obs.duration_minutes == 60.0
    assert obs.rate == 2.0


def test_restore_preheat_learner_missing_data(mock_hass):
    """Test restore_preheat_learner returns None when preheat_data missing."""
    store = LearningDataStore(mock_hass)

    # Zone data without preheat_learner
    zone_data = {
        "adaptive_learner": {"cycle_history": []},
        "ke_learner": {"current_ke": 0.5},
    }

    # Restore should return None
    restored = store.restore_preheat_learner(zone_data)
    assert restored is None


def test_restore_preheat_learner_empty_observations(mock_hass):
    """Test restore_preheat_learner handles empty observations list."""
    store = LearningDataStore(mock_hass)

    preheat_data = {
        "heating_type": "floor_hydronic",
        "max_hours": 4.0,
        "observations": [],
    }

    zone_data = {
        "preheat_learner": preheat_data,
    }

    # Restore PreheatLearner
    restored = store.restore_preheat_learner(zone_data)

    # Verify restoration with empty observations
    assert restored is not None
    assert isinstance(restored, PreheatLearner)
    assert restored.heating_type == "floor_hydronic"
    assert restored.max_hours == 4.0
    assert restored.get_observation_count() == 0


def test_restore_preheat_learner_multiple_observations(mock_hass):
    """Test restore_preheat_learner handles multiple observations across bins."""
    store = LearningDataStore(mock_hass)

    timestamp = datetime(2026, 1, 20, 10, 0, 0)
    preheat_data = {
        "heating_type": "radiator",
        "max_hours": 2.0,
        "observations": [
            {
                "bin_key": ["2-4", "mild"],
                "start_temp": 18.0,
                "end_temp": 20.0,
                "outdoor_temp": 8.0,
                "duration_minutes": 60.0,
                "rate": 2.0,
                "timestamp": timestamp.isoformat(),
            },
            {
                "bin_key": ["2-4", "cold"],
                "start_temp": 17.0,
                "end_temp": 19.0,
                "outdoor_temp": 3.0,
                "duration_minutes": 70.0,
                "rate": 1.71,
                "timestamp": timestamp.isoformat(),
            },
            {
                "bin_key": ["4-6", "mild"],
                "start_temp": 16.0,
                "end_temp": 21.0,
                "outdoor_temp": 7.0,
                "duration_minutes": 100.0,
                "rate": 3.0,
                "timestamp": timestamp.isoformat(),
            },
        ],
    }

    zone_data = {
        "preheat_learner": preheat_data,
    }

    # Restore PreheatLearner
    restored = store.restore_preheat_learner(zone_data)

    # Verify all observations were restored
    assert restored is not None
    assert restored.get_observation_count() == 3

    # Verify bins were populated correctly
    assert ("2-4", "mild") in restored._observations
    assert len(restored._observations[("2-4", "mild")]) == 1

    assert ("2-4", "cold") in restored._observations
    assert len(restored._observations[("2-4", "cold")]) == 1

    assert ("4-6", "mild") in restored._observations
    assert len(restored._observations[("4-6", "mild")]) == 1


@pytest.mark.asyncio
async def test_migration_v4_without_preheat_loads_successfully(mock_hass):
    """Test migration: v4 data without preheat_data loads with empty/None preheat."""
    # Simulate v4 format without preheat_learner
    v4_data = {
        "version": 4,
        "zones": {
            "climate.living_room": {
                "adaptive_learner": {
                    "cycle_history": [{"overshoot": 0.3}],
                },
                "ke_learner": {
                    "current_ke": 0.5,
                },
                "last_updated": "2026-01-20T10:00:00",
            },
        },
    }

    mock_storage_module = create_mock_storage_module(load_data=v4_data)

    with patch.dict('sys.modules', {'homeassistant.helpers.storage': mock_storage_module}):
        store = LearningDataStore(mock_hass)
        data = await store.async_load()

        # Should load successfully and be migrated to v5
        assert data["version"] == 5
        assert "climate.living_room" in data["zones"]

        # Get zone data
        zone_data = store.get_zone_data("climate.living_room")
        assert zone_data is not None

        # preheat_learner should not be present (backward compatibility)
        assert "preheat_learner" not in zone_data

        # restore_preheat_learner should return None
        restored = store.restore_preheat_learner(zone_data)
        assert restored is None


def test_restore_preheat_learner_error_handling(mock_hass):
    """Test restore_preheat_learner handles corrupt data gracefully."""
    store = LearningDataStore(mock_hass)

    # Invalid preheat data (missing heating_type)
    invalid_data = {
        "preheat_learner": {
            "max_hours": 2.0,
            # Missing heating_type
        },
    }

    # Should return None on error
    restored = store.restore_preheat_learner(invalid_data)
    assert restored is None
