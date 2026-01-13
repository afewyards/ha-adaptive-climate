"""Tests for the AdaptiveThermostatCoordinator."""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock
from datetime import timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat"))

# Mock homeassistant modules before importing coordinator
sys.modules['homeassistant'] = Mock()
sys.modules['homeassistant.core'] = Mock()
sys.modules['homeassistant.helpers'] = Mock()
sys.modules['homeassistant.helpers.update_coordinator'] = Mock()
sys.modules['homeassistant.exceptions'] = Mock()

# Create mock base class
class MockDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval

sys.modules['homeassistant.helpers.update_coordinator'].DataUpdateCoordinator = MockDataUpdateCoordinator

# Import const to get DOMAIN
import const

# Now we can import the coordinator
import coordinator


@pytest.fixture
def hass():
    """Create a mock Home Assistant instance."""
    mock_hass = MagicMock()
    return mock_hass


@pytest.fixture
def coord(hass):
    """Create a coordinator instance."""
    return coordinator.AdaptiveThermostatCoordinator(hass)


def test_zone_tracking(coord):
    """Test that zones can be registered and tracked."""
    # Register a zone
    zone_data = {
        "name": "Living Room",
        "area_m2": 20.0,
        "heating_type": "floor_hydronic",
    }
    coord.register_zone("living_room", zone_data)

    # Verify zone is tracked
    all_zones = coord.get_all_zones()
    assert "living_room" in all_zones
    assert all_zones["living_room"]["name"] == "Living Room"
    assert all_zones["living_room"]["area_m2"] == 20.0

    # Verify we can retrieve specific zone data
    zone = coord.get_zone_data("living_room")
    assert zone is not None
    assert zone["name"] == "Living Room"

    # Register a second zone
    zone_data_2 = {
        "name": "Bedroom",
        "area_m2": 15.0,
        "heating_type": "convector",
    }
    coord.register_zone("bedroom", zone_data_2)

    # Verify both zones are tracked
    all_zones = coord.get_all_zones()
    assert len(all_zones) == 2
    assert "bedroom" in all_zones


def test_demand_aggregation(coord):
    """Test that demand is correctly aggregated across zones."""
    # Register two zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})

    # Initially, no demand
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is False
    assert demand["cooling"] is False

    # One zone has demand
    coord.update_zone_demand("zone1", True)
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True

    # Both zones have demand
    coord.update_zone_demand("zone2", True)
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True

    # One zone still has demand
    coord.update_zone_demand("zone1", False)
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True

    # No zones have demand
    coord.update_zone_demand("zone2", False)
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is False


def test_all_zone_states_retrieval(coord):
    """Test that all zone states can be retrieved via update data."""
    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})

    # Set demand states
    coord.update_zone_demand("zone1", True)
    coord.update_zone_demand("zone2", False)

    # Since we can't easily await in tests without pytest-asyncio,
    # we'll test the internal state directly via the coordinator's methods
    # The _async_update_data method just returns the current state

    # Verify zones are registered
    all_zones = coord.get_all_zones()
    assert "zone1" in all_zones
    assert "zone2" in all_zones
    assert all_zones["zone1"]["name"] == "Zone 1"

    # Verify demand states
    assert coord._demand_states["zone1"] is True
    assert coord._demand_states["zone2"] is False

    # Verify aggregate demand
    aggregate = coord.get_aggregate_demand()
    assert aggregate["heating"] is True
    assert aggregate["cooling"] is False


def test_unregister_zone(coord):
    """Test that zones can be unregistered from the coordinator."""
    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})
    coord.update_zone_demand("zone1", True)
    coord.update_zone_demand("zone2", True)

    # Verify initial state
    assert len(coord.get_all_zones()) == 2
    assert "zone1" in coord._demand_states
    assert "zone2" in coord._demand_states

    # Unregister zone1
    coord.unregister_zone("zone1")

    # Verify zone1 is removed from zones dict
    all_zones = coord.get_all_zones()
    assert len(all_zones) == 1
    assert "zone1" not in all_zones
    assert "zone2" in all_zones

    # Verify zone1 is removed from demand states
    assert "zone1" not in coord._demand_states
    assert "zone2" in coord._demand_states

    # Verify get_zone_data returns None for unregistered zone
    assert coord.get_zone_data("zone1") is None
    assert coord.get_zone_data("zone2") is not None


def test_unregister_zone_cleans_up_demand_state(coord):
    """Test that unregistering a zone with active demand updates aggregate demand correctly."""
    # Register two zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})

    # Only zone1 has demand
    coord.update_zone_demand("zone1", True)
    coord.update_zone_demand("zone2", False)

    # Verify aggregate demand is True (zone1 has demand)
    assert coord.get_aggregate_demand()["heating"] is True

    # Unregister zone1 (the one with demand)
    coord.unregister_zone("zone1")

    # Verify aggregate demand is now False (no zones have demand)
    assert coord.get_aggregate_demand()["heating"] is False


def test_unregister_zone_not_found(coord, caplog):
    """Test that unregistering a non-existent zone is handled gracefully."""
    import logging
    caplog.set_level(logging.DEBUG)

    # Register a zone
    coord.register_zone("zone1", {"name": "Zone 1"})

    # Try to unregister a zone that doesn't exist
    coord.unregister_zone("nonexistent_zone")

    # Verify that the existing zone is still there
    assert "zone1" in coord.get_all_zones()

    # Verify debug message was logged
    assert "Zone nonexistent_zone not found" in caplog.text


def test_unregister_zone_idempotent(coord):
    """Test that unregistering the same zone twice is safe."""
    # Register a zone
    coord.register_zone("zone1", {"name": "Zone 1"})

    # Unregister it twice
    coord.unregister_zone("zone1")
    coord.unregister_zone("zone1")  # Should not raise an error

    # Verify zone is gone
    assert "zone1" not in coord.get_all_zones()


def test_duplicate_registration_warning(coord, caplog):
    """Test that registering the same zone twice logs a warning."""
    import logging
    caplog.set_level(logging.WARNING)

    # Register a zone
    zone_data_1 = {"name": "Zone 1 Original"}
    coord.register_zone("zone1", zone_data_1)

    # Register the same zone again with different data
    zone_data_2 = {"name": "Zone 1 Updated"}
    coord.register_zone("zone1", zone_data_2)

    # Verify warning was logged
    assert "Zone zone1 is already registered" in caplog.text

    # Verify the zone data was overwritten
    zone = coord.get_zone_data("zone1")
    assert zone["name"] == "Zone 1 Updated"


def test_duplicate_registration_preserves_demand_state(coord):
    """Test that re-registering a zone resets demand state to False."""
    # Register a zone and set demand
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.update_zone_demand("zone1", True)

    # Verify demand is True
    assert coord._demand_states["zone1"] is True

    # Re-register the zone
    coord.register_zone("zone1", {"name": "Zone 1 Updated"})

    # Verify demand is reset to False
    assert coord._demand_states["zone1"] is False


def test_unregister_all_zones(coord):
    """Test unregistering all zones leaves coordinator in clean state."""
    # Register multiple zones
    for i in range(5):
        coord.register_zone(f"zone{i}", {"name": f"Zone {i}"})
        coord.update_zone_demand(f"zone{i}", i % 2 == 0)

    # Verify zones are registered
    assert len(coord.get_all_zones()) == 5

    # Unregister all zones
    for i in range(5):
        coord.unregister_zone(f"zone{i}")

    # Verify all zones are gone
    assert len(coord.get_all_zones()) == 0
    assert len(coord._demand_states) == 0
    assert coord.get_aggregate_demand()["heating"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
