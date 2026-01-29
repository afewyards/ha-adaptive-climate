"""Tests for registry helpers."""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat"))

# Mock homeassistant modules before importing
sys.modules['homeassistant'] = Mock()

# Event needs to support subscripting for type hints like Event[EventStateChangedData]
class MockEvent:
    """Mock Event class that supports generic subscripting."""
    def __class_getitem__(cls, item):
        return cls

mock_core = Mock()
mock_core.Event = MockEvent
sys.modules['homeassistant.core'] = mock_core
sys.modules['homeassistant.helpers'] = Mock()
sys.modules['homeassistant.helpers.entity_registry'] = Mock()
sys.modules['homeassistant.helpers.area_registry'] = Mock()
sys.modules['homeassistant.helpers.floor_registry'] = Mock()


@pytest.fixture
def hass():
    """Create a mock Home Assistant instance."""
    return MagicMock()


class TestDiscoverZoneFloors:
    """Tests for discover_zone_floors() function."""

    @patch('helpers.registry.er')
    @patch('helpers.registry.ar')
    @patch('helpers.registry.fr')
    def test_discover_zone_floors_all_assigned(self, mock_fr, mock_ar, mock_er, hass):
        """Test returns floor levels for all zones with complete registry chain.

        Story 1.1, Step 1: All entities have area_id, all areas have floor_id,
        all floors have valid levels.
        """
        # Import here after patching
        from helpers.registry import discover_zone_floors

        # Setup mock entity registry
        entity_entry_1 = MagicMock()
        entity_entry_1.area_id = "area_living_room"
        entity_entry_2 = MagicMock()
        entity_entry_2.area_id = "area_bedroom"

        entity_registry = MagicMock()
        entity_registry.async_get = lambda entity_id: {
            "climate.living_room": entity_entry_1,
            "climate.bedroom": entity_entry_2,
        }.get(entity_id)
        mock_er.async_get.return_value = entity_registry

        # Setup mock area registry
        area_entry_1 = MagicMock()
        area_entry_1.floor_id = "floor_1"
        area_entry_2 = MagicMock()
        area_entry_2.floor_id = "floor_2"

        area_registry = MagicMock()
        area_registry.async_get = lambda area_id: {
            "area_living_room": area_entry_1,
            "area_bedroom": area_entry_2,
        }.get(area_id)
        mock_ar.async_get.return_value = area_registry

        # Setup mock floor registry
        floor_entry_1 = MagicMock()
        floor_entry_1.level = 0
        floor_entry_2 = MagicMock()
        floor_entry_2.level = 1

        floor_registry = MagicMock()
        floor_registry.async_get = lambda floor_id: {
            "floor_1": floor_entry_1,
            "floor_2": floor_entry_2,
        }.get(floor_id)
        mock_fr.async_get.return_value = floor_registry

        # Execute
        zone_entity_ids = ["climate.living_room", "climate.bedroom"]
        result = discover_zone_floors(hass, zone_entity_ids)

        # Assert
        assert result == {
            "climate.living_room": 0,
            "climate.bedroom": 1,
        }

    @patch('helpers.registry.er')
    @patch('helpers.registry.ar')
    @patch('helpers.registry.fr')
    def test_discover_zone_floors_missing_area(self, mock_fr, mock_ar, mock_er, hass):
        """Test returns None for entity without area_id.

        Story 1.1, Step 3: Entity has no area_id assigned.
        """
        from helpers.registry import discover_zone_floors

        # Setup mock entity registry
        entity_entry_1 = MagicMock()
        entity_entry_1.area_id = None  # No area assigned
        entity_entry_2 = MagicMock()
        entity_entry_2.area_id = "area_bedroom"

        entity_registry = MagicMock()
        entity_registry.async_get = lambda entity_id: {
            "climate.living_room": entity_entry_1,
            "climate.bedroom": entity_entry_2,
        }.get(entity_id)
        mock_er.async_get.return_value = entity_registry

        # Setup mock area registry
        area_entry_2 = MagicMock()
        area_entry_2.floor_id = "floor_2"

        area_registry = MagicMock()
        area_registry.async_get = lambda area_id: {
            "area_bedroom": area_entry_2,
        }.get(area_id)
        mock_ar.async_get.return_value = area_registry

        # Setup mock floor registry
        floor_entry_2 = MagicMock()
        floor_entry_2.level = 1

        floor_registry = MagicMock()
        floor_registry.async_get = lambda floor_id: {
            "floor_2": floor_entry_2,
        }.get(floor_id)
        mock_fr.async_get.return_value = floor_registry

        # Execute
        zone_entity_ids = ["climate.living_room", "climate.bedroom"]
        result = discover_zone_floors(hass, zone_entity_ids)

        # Assert
        assert result == {
            "climate.living_room": None,  # No area assigned
            "climate.bedroom": 1,
        }

    @patch('helpers.registry.er')
    @patch('helpers.registry.ar')
    @patch('helpers.registry.fr')
    def test_discover_zone_floors_missing_floor(self, mock_fr, mock_ar, mock_er, hass):
        """Test returns None for area without floor_id.

        Story 1.1, Step 5: Area has no floor_id assigned.
        """
        from helpers.registry import discover_zone_floors

        # Setup mock entity registry
        entity_entry_1 = MagicMock()
        entity_entry_1.area_id = "area_living_room"
        entity_entry_2 = MagicMock()
        entity_entry_2.area_id = "area_bedroom"

        entity_registry = MagicMock()
        entity_registry.async_get = lambda entity_id: {
            "climate.living_room": entity_entry_1,
            "climate.bedroom": entity_entry_2,
        }.get(entity_id)
        mock_er.async_get.return_value = entity_registry

        # Setup mock area registry
        area_entry_1 = MagicMock()
        area_entry_1.floor_id = None  # No floor assigned
        area_entry_2 = MagicMock()
        area_entry_2.floor_id = "floor_2"

        area_registry = MagicMock()
        area_registry.async_get = lambda area_id: {
            "area_living_room": area_entry_1,
            "area_bedroom": area_entry_2,
        }.get(area_id)
        mock_ar.async_get.return_value = area_registry

        # Setup mock floor registry
        floor_entry_2 = MagicMock()
        floor_entry_2.level = 1

        floor_registry = MagicMock()
        floor_registry.async_get = lambda floor_id: {
            "floor_2": floor_entry_2,
        }.get(floor_id)
        mock_fr.async_get.return_value = floor_registry

        # Execute
        zone_entity_ids = ["climate.living_room", "climate.bedroom"]
        result = discover_zone_floors(hass, zone_entity_ids)

        # Assert
        assert result == {
            "climate.living_room": None,  # Area has no floor
            "climate.bedroom": 1,
        }

    @patch('helpers.registry.er')
    @patch('helpers.registry.ar')
    @patch('helpers.registry.fr')
    def test_discover_zone_floors_mixed(self, mock_fr, mock_ar, mock_er, hass):
        """Test returns partial results with some None, some int.

        Story 1.1, Step 7: Mix of complete chains and missing assignments.
        """
        from helpers.registry import discover_zone_floors

        # Setup mock entity registry
        entity_entry_1 = MagicMock()
        entity_entry_1.area_id = "area_living_room"
        entity_entry_2 = MagicMock()
        entity_entry_2.area_id = None  # No area
        entity_entry_3 = MagicMock()
        entity_entry_3.area_id = "area_bedroom"
        entity_entry_4 = MagicMock()
        entity_entry_4.area_id = "area_office"

        entity_registry = MagicMock()
        entity_registry.async_get = lambda entity_id: {
            "climate.living_room": entity_entry_1,
            "climate.kitchen": entity_entry_2,
            "climate.bedroom": entity_entry_3,
            "climate.office": entity_entry_4,
        }.get(entity_id)
        mock_er.async_get.return_value = entity_registry

        # Setup mock area registry
        area_entry_1 = MagicMock()
        area_entry_1.floor_id = "floor_1"
        area_entry_3 = MagicMock()
        area_entry_3.floor_id = None  # No floor
        area_entry_4 = MagicMock()
        area_entry_4.floor_id = "floor_2"

        area_registry = MagicMock()
        area_registry.async_get = lambda area_id: {
            "area_living_room": area_entry_1,
            "area_bedroom": area_entry_3,
            "area_office": area_entry_4,
        }.get(area_id)
        mock_ar.async_get.return_value = area_registry

        # Setup mock floor registry
        floor_entry_1 = MagicMock()
        floor_entry_1.level = 0
        floor_entry_2 = MagicMock()
        floor_entry_2.level = 2

        floor_registry = MagicMock()
        floor_registry.async_get = lambda floor_id: {
            "floor_1": floor_entry_1,
            "floor_2": floor_entry_2,
        }.get(floor_id)
        mock_fr.async_get.return_value = floor_registry

        # Execute
        zone_entity_ids = ["climate.living_room", "climate.kitchen", "climate.bedroom", "climate.office"]
        result = discover_zone_floors(hass, zone_entity_ids)

        # Assert
        assert result == {
            "climate.living_room": 0,
            "climate.kitchen": None,  # No area
            "climate.bedroom": None,  # Area has no floor
            "climate.office": 2,
        }

        # Verify loop continues processing after None cases (step 8)
        assert len(result) == 4

    @patch('helpers.registry.er')
    @patch('helpers.registry.ar')
    @patch('helpers.registry.fr')
    def test_discover_zone_floors_entity_not_in_registry(self, mock_fr, mock_ar, mock_er, hass):
        """Test handles entity not found in entity registry."""
        from helpers.registry import discover_zone_floors

        # Setup mock entity registry to return None for unknown entity
        entity_registry = MagicMock()
        entity_registry.async_get = lambda entity_id: None
        mock_er.async_get.return_value = entity_registry

        area_registry = MagicMock()
        mock_ar.async_get.return_value = area_registry

        floor_registry = MagicMock()
        mock_fr.async_get.return_value = floor_registry

        # Execute
        zone_entity_ids = ["climate.nonexistent"]
        result = discover_zone_floors(hass, zone_entity_ids)

        # Assert
        assert result == {
            "climate.nonexistent": None,
        }

    @patch('helpers.registry.er')
    @patch('helpers.registry.ar')
    @patch('helpers.registry.fr')
    def test_discover_zone_floors_empty_list(self, mock_fr, mock_ar, mock_er, hass):
        """Test handles empty zone list gracefully."""
        from helpers.registry import discover_zone_floors

        entity_registry = MagicMock()
        mock_er.async_get.return_value = entity_registry

        area_registry = MagicMock()
        mock_ar.async_get.return_value = area_registry

        floor_registry = MagicMock()
        mock_fr.async_get.return_value = floor_registry

        # Execute
        zone_entity_ids = []
        result = discover_zone_floors(hass, zone_entity_ids)

        # Assert
        assert result == {}
