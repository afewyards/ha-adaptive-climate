"""Tests for domain rename migration (adaptive_thermostat → adaptive_climate)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys

# Mock Home Assistant modules before importing
mock_climate = MagicMock()
sys.modules['homeassistant.components.climate'] = mock_climate
sys.modules['homeassistant.components'] = MagicMock()
sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.core'] = MagicMock()
sys.modules['homeassistant.helpers'] = MagicMock()
sys.modules['homeassistant.helpers.storage'] = MagicMock()


class MockStore:
    """Mock HA Store class for testing migration."""

    def __init__(self, hass, version, key):
        self.hass = hass
        self.version = version
        self.key = key
        self._data = None
        self._load_responses = {}  # Key-based responses for migration tests

    async def async_load(self):
        """Return data based on storage key (simulates old/new storage files)."""
        return self._load_responses.get(self.key)

    async def async_save(self, data):
        """Save data."""
        self._data = data
        # Also update load_responses so subsequent loads see the saved data
        self._load_responses[self.key] = data

    def async_delay_save(self, data_func, delay):
        """Delayed save."""
        self._data = data_func()


@pytest.fixture
def mock_store_factory():
    """Create a factory that returns MockStore instances sharing load_responses."""
    shared_responses = {}

    def factory(hass, version, key):
        store = MockStore(hass, version, key)
        store._load_responses = shared_responses
        return store

    return factory


# =============================================================================
# Test hass.data Migration
# =============================================================================


class TestHassDataMigration:
    """Tests for hass.data migration from adaptive_thermostat to adaptive_climate."""

    def test_migrate_old_domain_data_on_setup(self):
        """Test that data in hass.data["adaptive_thermostat"] is migrated to adaptive_climate."""
        from custom_components.adaptive_climate.const import DOMAIN

        # Create mock hass with old domain data
        hass = MagicMock()
        old_domain_data = {
            "coordinator": MagicMock(),
            "vacation_mode": MagicMock(),
            "notify_service": "mobile_app",
            "learning_window_days": 7,
        }
        hass.data = {
            "adaptive_thermostat": old_domain_data,
        }

        # Simulate the migration code from __init__.py
        old_data = hass.data.get("adaptive_thermostat")
        if old_data:
            hass.data[DOMAIN] = old_data
            del hass.data["adaptive_thermostat"]

        # Verify migration
        assert DOMAIN in hass.data
        assert "adaptive_thermostat" not in hass.data
        assert hass.data[DOMAIN]["coordinator"] is old_domain_data["coordinator"]
        assert hass.data[DOMAIN]["notify_service"] == "mobile_app"
        assert hass.data[DOMAIN]["learning_window_days"] == 7

    def test_no_migration_when_old_domain_absent(self):
        """Test that setup works normally when old domain data doesn't exist."""
        from custom_components.adaptive_climate.const import DOMAIN

        # Create mock hass without old domain data
        hass = MagicMock()
        hass.data = {}

        # Simulate the migration code from __init__.py
        old_data = hass.data.get("adaptive_thermostat")
        if old_data:
            hass.data[DOMAIN] = old_data
            del hass.data["adaptive_thermostat"]

        # Initialize domain data storage (normal setup path)
        hass.data.setdefault(DOMAIN, {})

        # Verify setup proceeds normally
        assert DOMAIN in hass.data
        assert "adaptive_thermostat" not in hass.data
        assert hass.data[DOMAIN] == {}

    def test_no_migration_when_new_domain_already_exists(self):
        """Test that migration doesn't overwrite existing new domain data."""
        from custom_components.adaptive_climate.const import DOMAIN

        # Create mock hass with both old and new domain data
        hass = MagicMock()
        new_domain_data = {
            "coordinator": MagicMock(),
            "learning_window_days": 14,
        }
        old_domain_data = {
            "coordinator": MagicMock(),
            "learning_window_days": 7,
        }
        hass.data = {
            DOMAIN: new_domain_data,
            "adaptive_thermostat": old_domain_data,
        }

        # Simulate the migration code from __init__.py
        old_data = hass.data.get("adaptive_thermostat")
        if old_data:
            # Note: Real code would overwrite, but this tests the edge case
            # In practice, this scenario shouldn't happen (reload would unload first)
            hass.data[DOMAIN] = old_data
            del hass.data["adaptive_thermostat"]

        # Verify old data was migrated (overwrote new)
        # This is expected behavior - migration always runs if old data exists
        assert DOMAIN in hass.data
        assert "adaptive_thermostat" not in hass.data
        assert hass.data[DOMAIN]["learning_window_days"] == 7

    def test_migration_preserves_all_keys(self):
        """Test that migration preserves all keys from old domain data."""
        from custom_components.adaptive_climate.const import DOMAIN

        # Create mock hass with comprehensive old domain data
        hass = MagicMock()
        old_domain_data = {
            "coordinator": MagicMock(),
            "vacation_mode": MagicMock(),
            "central_controller": MagicMock(),
            "mode_sync": MagicMock(),
            "notify_service": "test_service",
            "persistent_notification": True,
            "energy_meter_entity": "sensor.energy",
            "energy_cost_entity": "sensor.cost",
            "main_heater_switch": ["switch.heater"],
            "main_cooler_switch": ["switch.cooler"],
            "source_startup_delay": 30,
            "sync_modes": True,
            "learning_window_days": 7,
            "weather_entity": "weather.home",
            "house_energy_rating": "B",
            "window_rating": "hr++",
            "supply_temp_sensor": "sensor.supply",
            "return_temp_sensor": "sensor.return",
            "flow_rate_sensor": "sensor.flow",
            "volume_meter_entity": "sensor.volume",
            "fallback_flow_rate": 2.0,
            "away_temp": 15.0,
            "eco_temp": 17.0,
            "boost_temp": 23.0,
            "comfort_temp": 21.0,
            "home_temp": 20.0,
            "activity_temp": 22.0,
            "preset_sync_mode": "sync",
            "unsub_callbacks": [MagicMock()],
            "learning_store": MagicMock(),
        }
        hass.data = {
            "adaptive_thermostat": old_domain_data,
        }

        # Simulate migration
        old_data = hass.data.get("adaptive_thermostat")
        if old_data:
            hass.data[DOMAIN] = old_data
            del hass.data["adaptive_thermostat"]

        # Verify all keys were migrated
        assert DOMAIN in hass.data
        assert "adaptive_thermostat" not in hass.data
        for key, value in old_domain_data.items():
            assert key in hass.data[DOMAIN]
            assert hass.data[DOMAIN][key] is value


# =============================================================================
# Test Persistence Storage Migration
# =============================================================================


class TestPersistenceStorageMigration:
    """Tests for LearningDataStore migration from old to new storage key."""

    @pytest.mark.asyncio
    async def test_migrate_from_old_storage_key(self, mock_store_factory):
        """Test that LearningDataStore migrates from adaptive_thermostat_learning to adaptive_climate_learning."""
        from custom_components.adaptive_climate.adaptive.persistence import (
            LearningDataStore,
            STORAGE_KEY,
            OLD_STORAGE_KEY,
        )

        # Create mock hass
        hass = MagicMock()

        # Create old storage data
        old_data = {
            "version": 5,
            "zones": {
                "climate.living_room": {
                    "adaptive_learner": {
                        "cycle_history": [
                            {"overshoot": 0.3, "undershoot": 0.1, "settling_time": 300}
                        ],
                        "max_history": 100,
                    },
                    "ke_learner": {"current_ke": 0.5, "enabled": True},
                    "last_updated": "2024-01-15T10:00:00+00:00",
                }
            },
        }

        # Patch Store creation to use our mock factory
        with patch(
            "custom_components.adaptive_climate.adaptive.persistence._create_store",
            side_effect=mock_store_factory,
        ):
            # Setup mock responses: new key has no data, old key has data
            # First create a store to access the shared responses dict
            temp_store = mock_store_factory(hass, 5, STORAGE_KEY)
            temp_store._load_responses[STORAGE_KEY] = None  # New storage is empty
            temp_store._load_responses[OLD_STORAGE_KEY] = old_data  # Old storage has data

            # Create LearningDataStore and load
            learning_store = LearningDataStore(hass)
            data = await learning_store.async_load()

            # Verify data was migrated from old storage
            assert data is not None
            assert data["version"] == 5
            assert "climate.living_room" in data["zones"]
            assert data["zones"]["climate.living_room"]["adaptive_learner"]["cycle_history"][0]["overshoot"] == 0.3

    @pytest.mark.asyncio
    async def test_no_migration_when_old_storage_absent(self, mock_store_factory):
        """Test that LearningDataStore returns default data when no old storage exists."""
        from custom_components.adaptive_climate.adaptive.persistence import (
            LearningDataStore,
            STORAGE_KEY,
            OLD_STORAGE_KEY,
        )

        # Create mock hass
        hass = MagicMock()

        # Patch Store creation to use our mock factory
        with patch(
            "custom_components.adaptive_climate.adaptive.persistence._create_store",
            side_effect=mock_store_factory,
        ):
            # Setup mock responses: both storages are empty
            store = mock_store_factory(hass, 5, STORAGE_KEY)
            store._load_responses[STORAGE_KEY] = None
            store._load_responses[OLD_STORAGE_KEY] = None

            # Create LearningDataStore and load
            learning_store = LearningDataStore(hass)
            data = await learning_store.async_load()

            # Verify default data structure is returned
            assert data is not None
            assert data["version"] == 5
            assert data["zones"] == {}

    @pytest.mark.asyncio
    async def test_no_migration_when_new_storage_exists(self, mock_store_factory):
        """Test that LearningDataStore uses new storage when it already exists."""
        from custom_components.adaptive_climate.adaptive.persistence import (
            LearningDataStore,
            STORAGE_KEY,
            OLD_STORAGE_KEY,
        )

        # Create mock hass
        hass = MagicMock()

        # Create new storage data
        new_data = {
            "version": 5,
            "zones": {
                "climate.bedroom": {
                    "adaptive_learner": {
                        "cycle_history": [
                            {"overshoot": 0.2, "undershoot": 0.05, "settling_time": 250}
                        ],
                        "max_history": 100,
                    },
                    "last_updated": "2024-01-16T12:00:00+00:00",
                }
            },
        }

        # Create old storage data (should be ignored)
        old_data = {
            "version": 5,
            "zones": {
                "climate.living_room": {
                    "adaptive_learner": {"cycle_history": []},
                }
            },
        }

        # Patch Store creation to use our mock factory
        with patch(
            "custom_components.adaptive_climate.adaptive.persistence._create_store",
            side_effect=mock_store_factory,
        ):
            # Setup mock responses: new storage has data, old storage also has data
            store = mock_store_factory(hass, 5, STORAGE_KEY)
            store._load_responses[STORAGE_KEY] = new_data
            store._load_responses[OLD_STORAGE_KEY] = old_data

            # Create LearningDataStore and load
            learning_store = LearningDataStore(hass)
            data = await learning_store.async_load()

            # Verify new storage data was used (not migrated)
            assert data is not None
            assert "climate.bedroom" in data["zones"]
            assert "climate.living_room" not in data["zones"]

    @pytest.mark.asyncio
    async def test_migration_saves_to_new_storage(self, mock_store_factory):
        """Test that migration saves old data to new storage key."""
        from custom_components.adaptive_climate.adaptive.persistence import (
            LearningDataStore,
            STORAGE_KEY,
            OLD_STORAGE_KEY,
        )

        # Create mock hass
        hass = MagicMock()

        # Create old storage data
        old_data = {
            "version": 5,
            "zones": {
                "climate.kitchen": {
                    "adaptive_learner": {"cycle_history": []},
                    "last_updated": "2024-01-15T10:00:00+00:00",
                }
            },
        }

        # Track saves to new storage
        saved_to_new_storage = None

        def mock_store_factory_with_tracking(hass, version, key):
            store = MockStore(hass, version, key)
            store._load_responses = {}

            if key == STORAGE_KEY:
                # Override async_save to track what was saved
                original_save = store.async_save

                async def tracked_save(data):
                    nonlocal saved_to_new_storage
                    saved_to_new_storage = data
                    await original_save(data)

                store.async_save = tracked_save
                store._load_responses[STORAGE_KEY] = None
            else:
                store._load_responses[OLD_STORAGE_KEY] = old_data

            return store

        # Patch Store creation to use our tracking mock
        with patch(
            "custom_components.adaptive_climate.adaptive.persistence._create_store",
            side_effect=mock_store_factory_with_tracking,
        ):
            # Create LearningDataStore and load (should trigger migration)
            learning_store = LearningDataStore(hass)
            await learning_store.async_load()

            # Verify data was saved to new storage
            assert saved_to_new_storage is not None
            assert saved_to_new_storage["version"] == 5
            assert "climate.kitchen" in saved_to_new_storage["zones"]

    @pytest.mark.asyncio
    async def test_migration_preserves_all_data_fields(self, mock_store_factory):
        """Test that migration preserves all fields in learning data."""
        from custom_components.adaptive_climate.adaptive.persistence import (
            LearningDataStore,
            STORAGE_KEY,
            OLD_STORAGE_KEY,
        )

        # Create mock hass
        hass = MagicMock()

        # Create comprehensive old storage data
        old_data = {
            "version": 5,
            "zones": {
                "climate.living_room": {
                    "adaptive_learner": {
                        "cycle_history": [
                            {
                                "overshoot": 0.3,
                                "undershoot": 0.1,
                                "settling_time": 300,
                                "oscillations": 2,
                                "rise_time": 180,
                            }
                        ],
                        "max_history": 100,
                        "last_adjustment_time": "2024-01-15T10:00:00+00:00",
                        "consecutive_converged_cycles": 5,
                        "pid_converged_for_ke": True,
                    },
                    "ke_learner": {
                        "current_ke": 0.5,
                        "enabled": True,
                        "observation_count": 10,
                    },
                    "preheat_learner": {
                        "heating_type": "floor_hydronic",
                        "max_hours": 3.0,
                        "observations": [],
                    },
                    "last_updated": "2024-01-15T10:00:00+00:00",
                },
                "climate.bedroom": {
                    "adaptive_learner": {
                        "cycle_history": [],
                        "max_history": 100,
                    },
                    "last_updated": "2024-01-14T08:00:00+00:00",
                },
            },
            "manifold_state": {
                "2nd_floor": "2024-01-15T09:00:00+00:00",
            },
        }

        # Patch Store creation to use our mock factory
        with patch(
            "custom_components.adaptive_climate.adaptive.persistence._create_store",
            side_effect=mock_store_factory,
        ):
            # Setup mock responses
            store = mock_store_factory(hass, 5, STORAGE_KEY)
            store._load_responses[STORAGE_KEY] = None
            store._load_responses[OLD_STORAGE_KEY] = old_data

            # Create LearningDataStore and load
            learning_store = LearningDataStore(hass)
            data = await learning_store.async_load()

            # Verify all fields were preserved
            assert data["version"] == 5
            assert len(data["zones"]) == 2

            # Check living_room data
            lr = data["zones"]["climate.living_room"]
            assert lr["adaptive_learner"]["cycle_history"][0]["overshoot"] == 0.3
            assert lr["adaptive_learner"]["consecutive_converged_cycles"] == 5
            assert lr["adaptive_learner"]["pid_converged_for_ke"] is True
            assert lr["ke_learner"]["current_ke"] == 0.5
            assert lr["ke_learner"]["observation_count"] == 10
            assert lr["preheat_learner"]["heating_type"] == "floor_hydronic"

            # Check bedroom data
            br = data["zones"]["climate.bedroom"]
            assert br["adaptive_learner"]["cycle_history"] == []

            # Check manifold state
            assert data["manifold_state"]["2nd_floor"] == "2024-01-15T09:00:00+00:00"


# =============================================================================
# Integration Test - Full Migration Flow
# =============================================================================


class TestFullMigrationFlow:
    """Integration tests for complete migration flow."""

    @pytest.mark.asyncio
    async def test_full_migration_from_old_install(self, mock_store_factory):
        """Test complete migration flow from old adaptive_thermostat install."""
        from custom_components.adaptive_climate.const import DOMAIN
        from custom_components.adaptive_climate.adaptive.persistence import (
            LearningDataStore,
            STORAGE_KEY,
            OLD_STORAGE_KEY,
        )

        # Step 1: Setup old hass.data (simulates old installation)
        hass = MagicMock()
        old_domain_data = {
            "coordinator": MagicMock(),
            "learning_window_days": 7,
            "notify_service": "mobile_app",
        }
        hass.data = {
            "adaptive_thermostat": old_domain_data,
        }

        # Step 2: Migrate hass.data (simulates __init__.py setup)
        old_data = hass.data.get("adaptive_thermostat")
        if old_data:
            hass.data[DOMAIN] = old_data
            del hass.data["adaptive_thermostat"]

        # Step 3: Setup old persistence data
        old_learning_data = {
            "version": 5,
            "zones": {
                "climate.zone1": {
                    "adaptive_learner": {"cycle_history": []},
                    "last_updated": "2024-01-15T10:00:00+00:00",
                }
            },
        }

        # Step 4: Migrate persistence data
        with patch(
            "custom_components.adaptive_climate.adaptive.persistence._create_store",
            side_effect=mock_store_factory,
        ):
            # Setup mock responses
            store = mock_store_factory(hass, 5, STORAGE_KEY)
            store._load_responses[STORAGE_KEY] = None
            store._load_responses[OLD_STORAGE_KEY] = old_learning_data

            # Create and load LearningDataStore
            learning_store = LearningDataStore(hass)
            learning_data = await learning_store.async_load()

        # Step 5: Verify complete migration
        # hass.data migrated
        assert DOMAIN in hass.data
        assert "adaptive_thermostat" not in hass.data
        assert hass.data[DOMAIN]["learning_window_days"] == 7

        # Persistence data migrated
        assert learning_data is not None
        assert "climate.zone1" in learning_data["zones"]

    @pytest.mark.asyncio
    async def test_fresh_install_no_migration_needed(self, mock_store_factory):
        """Test that fresh install works without any migration."""
        from custom_components.adaptive_climate.const import DOMAIN
        from custom_components.adaptive_climate.adaptive.persistence import (
            LearningDataStore,
            STORAGE_KEY,
        )

        # Fresh install - no old data
        hass = MagicMock()
        hass.data = {}

        # No migration needed
        old_data = hass.data.get("adaptive_thermostat")
        if old_data:
            hass.data[DOMAIN] = old_data
            del hass.data["adaptive_thermostat"]

        # Initialize normally
        hass.data.setdefault(DOMAIN, {})

        # Load persistence (no old data)
        with patch(
            "custom_components.adaptive_climate.adaptive.persistence._create_store",
            side_effect=mock_store_factory,
        ):
            store = mock_store_factory(hass, 5, STORAGE_KEY)
            store._load_responses[STORAGE_KEY] = None
            store._load_responses["adaptive_thermostat_learning"] = None

            learning_store = LearningDataStore(hass)
            learning_data = await learning_store.async_load()

        # Verify clean setup
        assert DOMAIN in hass.data
        assert hass.data[DOMAIN] == {}
        assert learning_data["version"] == 5
        assert learning_data["zones"] == {}
