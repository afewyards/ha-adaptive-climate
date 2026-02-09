"""Tests for preheat persistence support."""

import pytest
from unittest.mock import MagicMock, patch

from custom_components.adaptive_climate.adaptive.persistence import LearningDataStore


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

    with patch.dict("sys.modules", {"homeassistant.helpers.storage": mock_storage_module}):
        store = LearningDataStore(mock_hass)
        await store.async_load()

        # Create sample zone data including preheat
        adaptive_data = {
            "cycle_history": [{"overshoot": 0.3, "undershoot": 0.2, "settling_time": 45.0}],
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
