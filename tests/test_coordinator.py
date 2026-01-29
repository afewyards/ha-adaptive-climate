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

    # One zone has heating demand
    coord.update_zone_demand("zone1", True, "heat")
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True
    assert demand["cooling"] is False

    # Both zones have heating demand
    coord.update_zone_demand("zone2", True, "heat")
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True

    # One zone still has heating demand
    coord.update_zone_demand("zone1", False, "heat")
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True

    # No zones have demand
    coord.update_zone_demand("zone2", False, "heat")
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is False

    # Test cooling demand
    coord.update_zone_demand("zone1", True, "cool")
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is False
    assert demand["cooling"] is True

    # Test that demand without mode doesn't count as heating or cooling
    coord.update_zone_demand("zone1", False, "cool")
    coord.update_zone_demand("zone2", True, None)
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is False
    assert demand["cooling"] is False


def test_all_zone_states_retrieval(coord):
    """Test that all zone states can be retrieved via update data."""
    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})

    # Set demand states with HVAC mode
    coord.update_zone_demand("zone1", True, "heat")
    coord.update_zone_demand("zone2", False, "heat")

    # Since we can't easily await in tests without pytest-asyncio,
    # we'll test the internal state directly via the coordinator's methods
    # The _async_update_data method just returns the current state

    # Verify zones are registered
    all_zones = coord.get_all_zones()
    assert "zone1" in all_zones
    assert "zone2" in all_zones
    assert all_zones["zone1"]["name"] == "Zone 1"

    # Verify demand states (now stored as dict with demand and mode)
    assert coord._demand_states["zone1"]["demand"] is True
    assert coord._demand_states["zone1"]["mode"] == "heat"
    assert coord._demand_states["zone2"]["demand"] is False
    assert coord._demand_states["zone2"]["mode"] == "heat"

    # Verify aggregate demand
    aggregate = coord.get_aggregate_demand()
    assert aggregate["heating"] is True
    assert aggregate["cooling"] is False


def test_unregister_zone(coord):
    """Test that zones can be unregistered from the coordinator."""
    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})
    coord.update_zone_demand("zone1", True, "heat")
    coord.update_zone_demand("zone2", True, "heat")

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

    # Only zone1 has heating demand
    coord.update_zone_demand("zone1", True, "heat")
    coord.update_zone_demand("zone2", False, "heat")

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
    coord.update_zone_demand("zone1", True, "heat")

    # Verify demand is True (now stored as dict)
    assert coord._demand_states["zone1"]["demand"] is True
    assert coord._demand_states["zone1"]["mode"] == "heat"

    # Re-register the zone
    coord.register_zone("zone1", {"name": "Zone 1 Updated"})

    # Verify demand is reset to False (with new dict structure)
    assert coord._demand_states["zone1"]["demand"] is False
    assert coord._demand_states["zone1"]["mode"] is None


def test_unregister_all_zones(coord):
    """Test unregistering all zones leaves coordinator in clean state."""
    # Register multiple zones with alternating heating demand
    for i in range(5):
        coord.register_zone(f"zone{i}", {"name": f"Zone {i}"})
        coord.update_zone_demand(f"zone{i}", i % 2 == 0, "heat")

    # Verify zones are registered
    assert len(coord.get_all_zones()) == 5

    # Unregister all zones
    for i in range(5):
        coord.unregister_zone(f"zone{i}")

    # Verify all zones are gone
    assert len(coord.get_all_zones()) == 0
    assert len(coord._demand_states) == 0
    assert coord.get_aggregate_demand()["heating"] is False


# =============================================================================
# Thermal Coupling Integration Tests (Story 5.2)
# =============================================================================


def test_coordinator_outdoor_temp_access(hass):
    """Test that coordinator provides outdoor temperature."""
    # Set up weather entity in hass.data
    hass.data = {
        const.DOMAIN: {
            "weather_entity": "weather.home"
        }
    }

    # Mock weather entity state with temperature attribute
    mock_state = MagicMock()
    mock_state.state = "sunny"
    mock_state.attributes = {"temperature": 5.5}
    hass.states.get.return_value = mock_state

    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Access outdoor temp
    outdoor_temp = coord.outdoor_temp
    assert outdoor_temp == 5.5


def test_coordinator_outdoor_temp_unavailable(hass):
    """Test that outdoor_temp returns None when weather entity unavailable."""
    # No weather entity configured
    hass.data = {}
    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Should return None when no weather entity
    assert coord.outdoor_temp is None


def test_coordinator_outdoor_temp_missing_attribute(hass):
    """Test that outdoor_temp returns None when temperature attribute missing."""
    hass.data = {
        const.DOMAIN: {
            "weather_entity": "weather.home"
        }
    }

    # Mock weather entity state without temperature attribute
    mock_state = MagicMock()
    mock_state.state = "sunny"
    mock_state.attributes = {}
    hass.states.get.return_value = mock_state

    coord = coordinator.AdaptiveThermostatCoordinator(hass)
    assert coord.outdoor_temp is None


def test_coordinator_get_active_zones(coord):
    """Test that get_active_zones returns zones with has_demand=True."""
    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})
    coord.register_zone("zone3", {"name": "Zone 3"})

    # No demand initially
    active = coord.get_active_zones()
    assert active == {}

    # Zone1 has heating demand
    coord.update_zone_demand("zone1", True, "heat")
    active = coord.get_active_zones()
    assert "zone1" in active
    assert "zone2" not in active
    assert "zone3" not in active

    # Zone1 and zone3 have heating demand
    coord.update_zone_demand("zone3", True, "heat")
    active = coord.get_active_zones()
    assert "zone1" in active
    assert "zone2" not in active
    assert "zone3" in active


def test_coordinator_get_active_zones_mode_filter(coord):
    """Test that get_active_zones only returns zones with specified mode."""
    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})

    # Zone1 has heating demand, zone2 has cooling demand
    coord.update_zone_demand("zone1", True, "heat")
    coord.update_zone_demand("zone2", True, "cool")

    # Default returns all active zones
    active = coord.get_active_zones()
    assert "zone1" in active
    assert "zone2" in active

    # Filter by heat mode
    active_heat = coord.get_active_zones(hvac_mode="heat")
    assert "zone1" in active_heat
    assert "zone2" not in active_heat

    # Filter by cool mode
    active_cool = coord.get_active_zones(hvac_mode="cool")
    assert "zone1" not in active_cool
    assert "zone2" in active_cool


def test_coordinator_zone_temps(coord):
    """Test that get_zone_temps provides current temperature per zone."""
    # Register zones with current_temp in zone_data
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.5})
    coord.register_zone("zone2", {"name": "Zone 2", "current_temp": 21.0})
    coord.register_zone("zone3", {"name": "Zone 3", "current_temp": 19.5})

    temps = coord.get_zone_temps()

    assert temps["zone1"] == 20.5
    assert temps["zone2"] == 21.0
    assert temps["zone3"] == 19.5


def test_coordinator_zone_temps_missing(coord):
    """Test that get_zone_temps handles zones without temperature."""
    # Zone1 has temp, zone2 doesn't
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.5})
    coord.register_zone("zone2", {"name": "Zone 2"})

    temps = coord.get_zone_temps()

    # Only zone1 should be in temps dict
    assert "zone1" in temps
    assert temps["zone1"] == 20.5
    assert "zone2" not in temps


def test_coordinator_update_zone_temp(coord):
    """Test that zone temperatures can be updated."""
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.5})

    # Update zone temperature
    coord.update_zone_temp("zone1", 21.0)

    temps = coord.get_zone_temps()
    assert temps["zone1"] == 21.0


def test_coordinator_update_zone_temp_unknown_zone(coord):
    """Test that updating temp for unknown zone is handled gracefully."""
    # Should not raise an error
    coord.update_zone_temp("nonexistent_zone", 21.0)

    # Should still work without issues
    temps = coord.get_zone_temps()
    assert "nonexistent_zone" not in temps


# =============================================================================
# Manifold Integration Tests
# =============================================================================


def test_set_manifold_registry(coord):
    """Test that coordinator stores manifold registry reference."""
    # Create a mock manifold registry
    mock_registry = MagicMock()

    # Set the registry
    coord.set_manifold_registry(mock_registry)

    # Verify registry was stored
    assert coord._manifold_registry is mock_registry


def test_update_zone_loops(coord):
    """Test that coordinator tracks loops per zone."""
    # Register zones first
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})

    # Update loops for zones
    coord.update_zone_loops("zone1", 2)
    coord.update_zone_loops("zone2", 3)

    # Verify loops are tracked
    assert coord._zone_loops["zone1"] == 2
    assert coord._zone_loops["zone2"] == 3


def test_update_zone_loops_unregistered_zone(coord):
    """Test that updating loops for unregistered zone is handled gracefully."""
    # Should not raise an error
    coord.update_zone_loops("nonexistent_zone", 2)

    # Verify it's still tracked (zones can be registered after loops are set)
    assert coord._zone_loops["nonexistent_zone"] == 2


def test_get_transport_delay_for_zone_with_registry(coord):
    """Test that get_transport_delay_for_zone queries registry with active zones."""
    # Create a mock registry
    mock_registry = MagicMock()
    mock_registry.get_transport_delay.return_value = 5.0  # 5 minutes

    # Set up coordinator with registry
    coord.set_manifold_registry(mock_registry)

    # Register zones and set demand
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})
    coord.register_zone("zone3", {"name": "Zone 3"})

    coord.update_zone_demand("zone1", True, "heat")
    coord.update_zone_demand("zone2", True, "heat")
    coord.update_zone_demand("zone3", False, "heat")

    # Query delay for zone1
    delay = coord.get_transport_delay_for_zone("zone1")

    # Verify delay was returned
    assert delay == 5.0

    # Verify registry was called with zone1 and list of active zones
    mock_registry.get_transport_delay.assert_called_once()
    call_args = mock_registry.get_transport_delay.call_args
    assert call_args[0][0] == "zone1"  # First positional arg is zone_id

    # Second positional arg is dict of active zones with entity_id keys (climate.*)
    active_zones = call_args[0][1]
    assert "climate.zone1" in active_zones
    assert "climate.zone2" in active_zones
    assert "climate.zone3" not in active_zones


def test_get_transport_delay_for_zone_no_registry(coord):
    """Test that get_transport_delay_for_zone returns 0 when no registry set."""
    # Don't set a registry

    # Register zones with demand
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.update_zone_demand("zone1", True, "heat")

    # Query delay - should return 0 when no registry
    delay = coord.get_transport_delay_for_zone("zone1")

    assert delay == 0


def test_get_transport_delay_for_zone_no_active_zones(coord):
    """Test transport delay when no zones have demand."""
    # Create a mock registry
    mock_registry = MagicMock()
    mock_registry.get_transport_delay.return_value = 0  # No delay when no active zones

    coord.set_manifold_registry(mock_registry)

    # Register zones with no demand
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})
    coord.update_zone_demand("zone1", False, "heat")
    coord.update_zone_demand("zone2", False, "heat")

    # Query delay
    delay = coord.get_transport_delay_for_zone("zone1")

    # Verify registry was called with empty active zones dict
    mock_registry.get_transport_delay.assert_called_once()
    call_args = mock_registry.get_transport_delay.call_args
    assert call_args[0][0] == "zone1"
    assert call_args[0][1] == {}  # No active zones


def test_get_transport_delay_for_zone_mode_filter(coord):
    """Test that only zones with heating demand are considered active."""
    # Create a mock registry
    mock_registry = MagicMock()
    mock_registry.get_transport_delay.return_value = 3.0

    coord.set_manifold_registry(mock_registry)

    # Register zones with mixed modes
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.register_zone("zone2", {"name": "Zone 2"})
    coord.register_zone("zone3", {"name": "Zone 3"})

    coord.update_zone_demand("zone1", True, "heat")
    coord.update_zone_demand("zone2", True, "cool")  # Cooling, not heating
    coord.update_zone_demand("zone3", True, "heat")

    # Query delay
    delay = coord.get_transport_delay_for_zone("zone1")

    # Verify only heating zones are in active list (with entity_id keys)
    call_args = mock_registry.get_transport_delay.call_args
    active_zones = call_args[0][1]
    assert "climate.zone1" in active_zones
    assert "climate.zone2" not in active_zones  # Cooling mode, not active for heating
    assert "climate.zone3" in active_zones


def test_get_transport_delay_converts_slug_to_entity_id(coord):
    """Test that transport delay converts slug keys to entity_ids for manifold registry.

    This tests the fix for the bug where _demand_states is keyed by slug
    (e.g. 'bathroom_2nd') but _zone_loops and manifold registry expect
    entity_ids (e.g. 'climate.bathroom_2nd').
    """
    # Create a mock registry
    mock_registry = MagicMock()
    mock_registry.get_transport_delay.return_value = 4.5

    coord.set_manifold_registry(mock_registry)

    # Register zones using slugs (as climate_setup.py does)
    coord.register_zone("bathroom_2nd", {"name": "Bathroom 2nd"})
    coord.register_zone("living_room", {"name": "Living Room"})

    # Update zone loops using entity_ids (as climate.py does)
    coord.update_zone_loops("climate.bathroom_2nd", 2)
    coord.update_zone_loops("climate.living_room", 3)

    # Set demand for zones using slugs (as they're registered)
    coord.update_zone_demand("bathroom_2nd", True, "heat")
    coord.update_zone_demand("living_room", True, "heat")

    # Query delay for a zone using entity_id
    delay = coord.get_transport_delay_for_zone("climate.bathroom_2nd")

    # Verify delay was returned
    assert delay == 4.5

    # Verify registry was called with entity_id format
    mock_registry.get_transport_delay.assert_called_once()
    call_args = mock_registry.get_transport_delay.call_args

    # First arg should be the entity_id
    assert call_args[0][0] == "climate.bathroom_2nd"

    # Second arg should be dict of active zones with entity_id keys, not slugs
    active_zones = call_args[0][1]

    # These should be entity_ids with "climate." prefix
    assert "climate.bathroom_2nd" in active_zones
    assert "climate.living_room" in active_zones

    # These slug keys should NOT be present
    assert "bathroom_2nd" not in active_zones
    assert "living_room" not in active_zones

    # Loop counts should be preserved
    assert active_zones["climate.bathroom_2nd"] == 2
    assert active_zones["climate.living_room"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# =============================================================================
# Worst-Case Transport Delay Tests (for preheat scheduling)
# =============================================================================


def test_get_worst_case_transport_delay_for_zone_with_registry(hass):
    """Test get_worst_case_transport_delay_for_zone wrapper works."""
    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Create mock manifold registry
    mock_registry = MagicMock()
    mock_registry.get_worst_case_transport_delay.return_value = 5.0
    coord.set_manifold_registry(mock_registry)

    # Test wrapper method
    delay = coord.get_worst_case_transport_delay_for_zone("climate.bathroom_2nd", zone_loops=2)

    # Expected: 20L / (2 loops × 2 L/min) = 5 min
    assert delay == 5.0
    mock_registry.get_worst_case_transport_delay.assert_called_once_with("climate.bathroom_2nd", 2)


def test_get_worst_case_transport_delay_for_zone_no_registry(hass):
    """Test get_worst_case_transport_delay_for_zone returns 0.0 when no manifold registry."""
    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # No manifold registry set
    delay = coord.get_worst_case_transport_delay_for_zone("climate.test", zone_loops=1)

    # Should return 0.0
    assert delay == 0.0


def test_get_worst_case_transport_delay_for_zone_default_loops(hass):
    """Test get_worst_case_transport_delay_for_zone uses default zone_loops=1."""
    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Create mock manifold registry
    mock_registry = MagicMock()
    mock_registry.get_worst_case_transport_delay.return_value = 15.0
    coord.set_manifold_registry(mock_registry)

    # Test without specifying zone_loops (should default to 1)
    delay = coord.get_worst_case_transport_delay_for_zone("climate.living_room")

    # Expected: 30L / (1 loop × 2 L/min) = 15 min
    assert delay == 15.0
    mock_registry.get_worst_case_transport_delay.assert_called_once_with("climate.living_room", 1)


def test_get_worst_case_transport_delay_for_zone_unknown_zone(hass):
    """Test get_worst_case_transport_delay_for_zone returns 0.0 for unknown zone."""
    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Create mock manifold registry that returns 0.0 for unknown zone
    mock_registry = MagicMock()
    mock_registry.get_worst_case_transport_delay.return_value = 0.0
    coord.set_manifold_registry(mock_registry)

    # Test with zone not in any manifold
    delay = coord.get_worst_case_transport_delay_for_zone("climate.unknown_zone", zone_loops=1)

    # Should return 0.0
    assert delay == 0.0
    mock_registry.get_worst_case_transport_delay.assert_called_once_with("climate.unknown_zone", 1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
