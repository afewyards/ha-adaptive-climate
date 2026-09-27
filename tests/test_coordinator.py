"""Tests for the AdaptiveThermostatCoordinator."""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock
from datetime import timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_climate"))

# Mock homeassistant modules before importing coordinator
# NOTE: Most mocks are already set up in conftest.py, but we need to ensure
# they're available before importing coordinator module
# DO NOT replace sys.modules['homeassistant.components.climate'] - use the one from conftest.py

# Just ensure the mocks exist (conftest.py should have already set these up)
if "homeassistant" not in sys.modules:
    sys.modules["homeassistant"] = Mock()

if "homeassistant.core" not in sys.modules:
    # Event needs to support subscripting for type hints like Event[EventStateChangedData]
    class MockEvent:
        """Mock Event class that supports generic subscripting."""

        def __class_getitem__(cls, item):
            return cls

    mock_core = Mock()
    mock_core.Event = MockEvent
    mock_core.callback = lambda f: f  # Mock the callback decorator
    sys.modules["homeassistant.core"] = mock_core

if "homeassistant.helpers" not in sys.modules:
    sys.modules["homeassistant.helpers"] = Mock()
if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = Mock()
if "homeassistant.helpers.event" not in sys.modules:
    sys.modules["homeassistant.helpers.event"] = Mock()
if "homeassistant.exceptions" not in sys.modules:
    sys.modules["homeassistant.exceptions"] = Mock()
if "homeassistant.components" not in sys.modules:
    sys.modules["homeassistant.components"] = Mock()

# DO NOT replace homeassistant.components.climate - it's already set up in conftest.py
# with the correct MockHVACMode that uses global singleton values

# managers/events.py has no HA dependencies — load it directly via importlib so
# the real CycleEventDispatcher, ZoneRegisteredEvent, etc. are available even
# after we mock the managers package (which has HA-dependent __init__.py).
import importlib.util as _importlib_util

_events_path = Path(__file__).parent.parent / "custom_components" / "adaptive_climate" / "managers" / "events.py"
_events_spec = _importlib_util.spec_from_file_location("managers.events", str(_events_path))
_events_mod = _importlib_util.module_from_spec(_events_spec)  # type: ignore[arg-type]
sys.modules["managers.events"] = _events_mod  # must be set BEFORE exec_module (dataclass needs it)
_events_spec.loader.exec_module(_events_mod)  # type: ignore[union-attr]

# Mock managers package (has HA-dependent __init__.py) and auto_mode_switching
# Keep the real events module accessible via the mock
mock_managers = Mock()
mock_managers.events = _events_mod
sys.modules["managers"] = mock_managers
sys.modules["managers.auto_mode_switching"] = Mock()
sys.modules["managers.water_temp_controller"] = Mock()

# Evict coordinator (and central_controller) from sys.modules so that the
# subsequent `import coordinator` performs a FRESH import.  If
# test_central_controller.py ran first it will have cached a coordinator
# module whose globals are bound to the MOCK managers.events classes.
# Removing the cached entries forces re-execution of coordinator.py, which
# will now pick up the real managers.events module we installed above.
for _stale_mod in ("coordinator", "central_controller"):
    sys.modules.pop(_stale_mod, None)


# Create mock base class
class MockDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval


sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = MockDataUpdateCoordinator

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
    """Test that re-registering a zone (e.g. config reload) preserves demand state.

    H09: demand state must NOT be wiped on re-registration so an actively-heating
    zone's demand is preserved across config reloads instead of shutting off the
    boiler mid-cycle.
    """
    # Register a zone and set demand
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.update_zone_demand("zone1", True, "heat")

    # Verify demand is True
    assert coord._demand_states["zone1"]["demand"] is True
    assert coord._demand_states["zone1"]["mode"] == "heat"

    # Re-register the zone (e.g. config reload)
    coord.register_zone("zone1", {"name": "Zone 1 Updated"})

    # Verify demand is PRESERVED (H09 fix)
    assert coord._demand_states["zone1"]["demand"] is True
    assert coord._demand_states["zone1"]["mode"] == "heat"

    # But zone_data is still updated to the new data
    zone = coord.get_zone_data("zone1")
    assert zone["name"] == "Zone 1 Updated"


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
    hass.data = {const.DOMAIN: {"weather_entity": "weather.home"}}

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
    hass.data = {const.DOMAIN: {"weather_entity": "weather.home"}}

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
    _delay = coord.get_transport_delay_for_zone("zone1")

    # Verify registry was called with empty active zones dict
    mock_registry.get_transport_delay.assert_called_once()
    call_args = mock_registry.get_transport_delay.call_args
    assert call_args[0][0] == "zone1"
    assert call_args[0][1] == set()  # No active zones


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
    _delay = coord.get_transport_delay_for_zone("zone1")

    # Verify only heating zones are in active list (with entity_id keys)
    call_args = mock_registry.get_transport_delay.call_args
    active_zones = call_args[0][1]
    assert "climate.zone1" in active_zones
    assert "climate.zone2" not in active_zones  # Cooling mode, not active for heating
    assert "climate.zone3" in active_zones


def test_get_transport_delay_converts_slug_to_entity_id(coord):
    """Test that transport delay converts slug keys to entity_ids for manifold registry.

    This tests the fix for the bug where _demand_states is keyed by slug
    (e.g. 'bathroom_2nd') but the manifold registry expects
    entity_ids (e.g. 'climate.bathroom_2nd').
    """
    # Create a mock registry
    mock_registry = MagicMock()
    mock_registry.get_transport_delay.return_value = 4.5

    coord.set_manifold_registry(mock_registry)

    # Register zones using slugs (as climate_setup.py does)
    coord.register_zone("bathroom_2nd", {"name": "Bathroom 2nd"})
    coord.register_zone("living_room", {"name": "Living Room"})

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

    # Second arg should be set of active zones with entity_id keys, not slugs
    active_zones = call_args[0][1]

    # These should be entity_ids with "climate." prefix
    assert "climate.bathroom_2nd" in active_zones
    assert "climate.living_room" in active_zones

    # These slug keys should NOT be present
    assert "bathroom_2nd" not in active_zones
    assert "living_room" not in active_zones


class TestCoordinatorAutoModeSwitching:
    """Tests for coordinator auto mode switching integration."""

    def test_coordinator_initializes_auto_mode_switching_when_enabled(self, hass):
        """Test coordinator initializes AutoModeSwitchingManager when config has enabled=true."""
        config = {
            "auto_mode_switching": {
                "enabled": True,
                "auto_mode_threshold": 2.0,
            }
        }

        # Mock weather entity in hass.data
        hass.data = {const.DOMAIN: {"weather_entity": "weather.home"}}

        coord = coordinator.AdaptiveThermostatCoordinator(hass, config)

        assert coord.auto_mode_switching_enabled is True
        assert coord.auto_mode_switching is not None

    def test_coordinator_does_not_initialize_auto_mode_switching_when_disabled(self, hass):
        """Test coordinator does not initialize AutoModeSwitchingManager when disabled."""
        config = {
            "auto_mode_switching": {
                "enabled": False,
            }
        }

        coord = coordinator.AdaptiveThermostatCoordinator(hass, config)

        assert coord.auto_mode_switching_enabled is False
        assert coord.auto_mode_switching is None

    def test_coordinator_does_not_initialize_auto_mode_switching_when_not_configured(self, hass):
        """Test coordinator does not initialize AutoModeSwitchingManager when not configured."""
        config = {}

        coord = coordinator.AdaptiveThermostatCoordinator(hass, config)

        assert coord.auto_mode_switching_enabled is False
        assert coord.auto_mode_switching is None

    @pytest.mark.asyncio
    async def test_apply_house_mode_calls_service_for_non_off_zones(self, hass):
        """Test _apply_house_mode calls set_hvac_mode service for non-OFF zones.

        hvac_mode is read from the climate entity state, not from zone data, so
        zone data does NOT include hvac_mode.  Zones that are actually OFF in HA
        must be skipped even though their zone dict has no hvac_mode key.
        """
        from unittest.mock import AsyncMock, MagicMock

        coord = coordinator.AdaptiveThermostatCoordinator(hass, {})

        # Mock async_call to be an async function
        hass.services.async_call = AsyncMock()

        # Register zones WITHOUT hvac_mode in zone data (matches production)
        coord.register_zone("climate.zone1", {"climate_entity_id": "climate.zone1"})
        coord.register_zone("climate.zone2", {"climate_entity_id": "climate.zone2"})
        coord.register_zone("climate.zone3", {"climate_entity_id": "climate.zone3"})

        # Simulate HA entity states: zone1=heat, zone2=cool, zone3=off
        def fake_states_get(entity_id):
            state_map = {
                "climate.zone1": MagicMock(state="heat"),
                "climate.zone2": MagicMock(state="cool"),
                "climate.zone3": MagicMock(state="off"),
            }
            return state_map.get(entity_id)

        hass.states.get = fake_states_get

        # Apply COOL mode
        await coord._apply_house_mode("cool")

        # Verify service was called for zone1 and zone2, but not zone3
        assert hass.services.async_call.call_count == 2

        # Get call args
        calls = hass.services.async_call.call_args_list

        # Check that zone1 and zone2 were called
        # Call args format: call[0] is positional args, call[1] is kwargs
        entity_ids = []
        for call in calls:
            # The call format is: async_call("climate", "set_hvac_mode", {...}, blocking=False)
            # So args[2] contains the service data dict
            entity_ids.append(call[0][2]["entity_id"])

        assert "climate.zone1" in entity_ids
        assert "climate.zone2" in entity_ids
        assert "climate.zone3" not in entity_ids

        # Check that the mode was set to COOL
        for call in calls:
            assert call[0][2]["hvac_mode"] == "cool"


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
    delay = coord.get_worst_case_transport_delay_for_zone("climate.bathroom_2nd")

    # 20L / 4 L/min = 5 min
    assert delay == 5.0
    mock_registry.get_worst_case_transport_delay.assert_called_once_with("climate.bathroom_2nd")


def test_get_worst_case_transport_delay_for_zone_no_registry(hass):
    """Test get_worst_case_transport_delay_for_zone returns 0.0 when no manifold registry."""
    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # No manifold registry set
    delay = coord.get_worst_case_transport_delay_for_zone("climate.test")

    # Should return 0.0
    assert delay == 0.0


def test_get_worst_case_transport_delay_for_zone_unknown_zone(hass):
    """Test get_worst_case_transport_delay_for_zone returns 0.0 for unknown zone."""
    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Create mock manifold registry that returns 0.0 for unknown zone
    mock_registry = MagicMock()
    mock_registry.get_worst_case_transport_delay.return_value = 0.0
    coord.set_manifold_registry(mock_registry)

    # Test with zone not in any manifold
    delay = coord.get_worst_case_transport_delay_for_zone("climate.unknown_zone")

    # Should return 0.0
    assert delay == 0.0
    mock_registry.get_worst_case_transport_delay.assert_called_once_with("climate.unknown_zone")


# --- Shared outdoor temp EMA tests ---


@pytest.mark.asyncio
async def test_outdoor_temp_lagged_defaults_to_none(hass):
    """outdoor_temp_lagged is None when no weather entity configured."""
    hass.data = {}  # No weather entity
    coord = coordinator.AdaptiveThermostatCoordinator(hass)
    assert coord.outdoor_temp_lagged is None


@pytest.mark.asyncio
async def test_outdoor_temp_lagged_initialized_on_first_reading(hass):
    """First weather reading sets outdoor_temp_lagged directly."""
    hass.data = {const.DOMAIN: {"weather_entity": "weather.home", "house_energy_rating": "B"}}
    hass.states.get.return_value = None  # No state yet
    coord = coordinator.AdaptiveThermostatCoordinator(hass)
    coord.update_outdoor_temp_lagged(5.0, dt_seconds=0)
    assert coord.outdoor_temp_lagged == 5.0


@pytest.mark.asyncio
async def test_outdoor_temp_lagged_ema_filter(hass):
    """EMA filter smooths outdoor temp with tau from house_energy_rating."""
    hass.data = {const.DOMAIN: {"weather_entity": "weather.home", "house_energy_rating": "A"}}
    hass.states.get.return_value = None  # No state yet
    coord = coordinator.AdaptiveThermostatCoordinator(hass)
    # tau = 4.0h for rating "A"
    coord.update_outdoor_temp_lagged(10.0, dt_seconds=0)  # init
    assert coord.outdoor_temp_lagged == 10.0

    # After 1 hour, temp jumps to 20.
    # H03: exponential alpha = 1 - exp(-dt/tau) = 1 - exp(-3600/(4*3600)) = 1 - exp(-0.25)
    import math

    alpha = 1.0 - math.exp(-3600 / (4.0 * 3600))
    expected = alpha * 20.0 + (1.0 - alpha) * 10.0
    coord.update_outdoor_temp_lagged(20.0, dt_seconds=3600)
    assert abs(coord.outdoor_temp_lagged - expected) < 0.01


@pytest.mark.asyncio
async def test_outdoor_temp_lagged_default_tau_without_rating(hass):
    """Uses default tau=4.0h when no house_energy_rating configured."""
    hass.data = {const.DOMAIN: {"weather_entity": "weather.home"}}
    hass.states.get.return_value = None
    coord = coordinator.AdaptiveThermostatCoordinator(hass)
    assert coord.outdoor_temp_tau == 4.0


@pytest.mark.asyncio
async def test_outdoor_temp_lagged_tau_from_rating(hass):
    """Tau derived from house_energy_rating."""
    hass.data = {const.DOMAIN: {"weather_entity": "weather.home", "house_energy_rating": "B"}}
    hass.states.get.return_value = None
    coord = coordinator.AdaptiveThermostatCoordinator(hass)
    assert coord.outdoor_temp_tau == 3.0


# =============================================================================
# H09: register_zone preserves demand state on re-registration
# =============================================================================


def test_register_zone_new_zone_initialises_demand(coord):
    """New zone gets demand state initialised to False/None."""
    coord.register_zone("zone1", {"name": "Zone 1"})
    assert coord._demand_states["zone1"] == {"demand": False, "mode": None}


def test_register_zone_rereg_preserves_active_demand(coord):
    """Re-registering an active zone must NOT wipe its demand state (H09)."""
    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.update_zone_demand("zone1", True, "heat")

    # Re-register (e.g. config entry reload)
    coord.register_zone("zone1", {"name": "Zone 1 v2"})

    # Demand state preserved
    assert coord._demand_states["zone1"]["demand"] is True
    assert coord._demand_states["zone1"]["mode"] == "heat"
    # Zone data still updated
    assert coord.get_zone_data("zone1")["name"] == "Zone 1 v2"


def test_register_zone_rereg_preserves_idle_demand(coord):
    """Re-registering an idle zone keeps it at False/None (H09)."""
    coord.register_zone("zone1", {"name": "Zone 1"})
    # No demand update — demand is False

    coord.register_zone("zone1", {"name": "Zone 1 v2"})

    assert coord._demand_states["zone1"]["demand"] is False
    assert coord._demand_states["zone1"]["mode"] is None


# =============================================================================
# H10: unregister_zone removes zone from thermal_group_manager
# =============================================================================


def test_unregister_zone_calls_thermal_group_remove(coord):
    """unregister_zone calls remove_zone on thermal_group_manager (H10)."""
    from unittest.mock import MagicMock

    mock_tgm = MagicMock()
    coord.set_thermal_group_manager(mock_tgm)

    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.unregister_zone("zone1")

    mock_tgm.remove_zone.assert_called_once_with("zone1")


def test_unregister_zone_no_thermal_group_manager_ok(coord):
    """unregister_zone works even when no thermal_group_manager is set (H10)."""
    coord.register_zone("zone1", {"name": "Zone 1"})
    # No thermal_group_manager set — should not raise
    coord.unregister_zone("zone1")
    assert "zone1" not in coord._zones


# =============================================================================
# H04: get_active_zone_setpoints reads user_target_temp from zone_data
# =============================================================================


def test_get_active_zone_setpoints_reads_user_target_temp(coord):
    """get_active_zone_setpoints uses zone_data['user_target_temp'] (H04)."""
    from unittest.mock import MagicMock

    # Zone with user_target_temp set (pre-setback value)
    coord.register_zone("zone1", {"climate_entity_id": "climate.zone1", "user_target_temp": 21.0})
    coord._demand_states["zone1"] = {"demand": True, "mode": "heat"}

    # HA entity state shows night-setback-reduced value (19.0)
    mock_state = MagicMock()
    mock_state.attributes = {"temperature": 19.0}
    coord.hass.states.get = MagicMock(return_value=mock_state)

    setpoints = coord.get_active_zone_setpoints()
    # Must return 21.0 (user setpoint), not 19.0 (effective setpoint)
    assert setpoints == [21.0]


def test_get_active_zone_setpoints_falls_back_to_entity_state(coord):
    """get_active_zone_setpoints falls back to entity state when no user_target_temp (H04)."""
    from unittest.mock import MagicMock

    coord.register_zone("zone1", {"climate_entity_id": "climate.zone1"})
    coord._demand_states["zone1"] = {"demand": True, "mode": "heat"}

    mock_state = MagicMock()
    mock_state.attributes = {"temperature": 20.0}
    coord.hass.states.get = MagicMock(return_value=mock_state)

    setpoints = coord.get_active_zone_setpoints()
    assert setpoints == [20.0]


def test_get_active_zone_setpoints_excludes_off_zones(coord):
    """get_active_zone_setpoints skips zones not in an active mode (H04)."""
    from unittest.mock import MagicMock

    coord.register_zone("zone1", {"climate_entity_id": "climate.zone1", "user_target_temp": 21.0})
    coord._demand_states["zone1"] = {"demand": False, "mode": "off"}

    coord.register_zone("zone2", {"climate_entity_id": "climate.zone2", "user_target_temp": 20.0})
    coord._demand_states["zone2"] = {"demand": True, "mode": "heat"}

    setpoints = coord.get_active_zone_setpoints()
    assert setpoints == [20.0]


# =============================================================================
# H06: _apply_house_mode returns count of zones switched
# =============================================================================


@pytest.mark.asyncio
async def test_apply_house_mode_returns_switched_count(hass):
    """_apply_house_mode returns count of zones that received the command (H06)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone1", {"climate_entity_id": "climate.zone1"})
    coord.register_zone("zone2", {"climate_entity_id": "climate.zone2"})
    coord.register_zone("zone3", {"climate_entity_id": "climate.zone3"})  # OFF

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone1": MagicMock(state="heat"),
            "climate.zone2": MagicMock(state="cool"),
            "climate.zone3": MagicMock(state="off"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    count = await coord._apply_house_mode("cool")
    assert count == 2  # zone1 + zone2, not zone3


@pytest.mark.asyncio
async def test_apply_house_mode_returns_zero_when_all_off(hass):
    """_apply_house_mode returns 0 when all zones are OFF (H06)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone1", {"climate_entity_id": "climate.zone1"})

    hass.states.get = MagicMock(return_value=MagicMock(state="off"))

    count = await coord._apply_house_mode("heat")
    assert count == 0


# =============================================================================
# A02: Zone lifecycle pub/sub via CycleEventDispatcher
# =============================================================================


def test_zone_registered_event_emitted(coord):
    """ZoneRegisteredEvent is emitted when register_zone is called (A02)."""
    from managers.events import CycleEventType, ZoneRegisteredEvent

    received = []
    coord.zone_dispatcher.subscribe(CycleEventType.ZONE_REGISTERED, received.append)

    coord.register_zone("zone1", {"climate_entity_id": "climate.zone1"})

    assert len(received) == 1
    assert isinstance(received[0], ZoneRegisteredEvent)
    assert received[0].zone_id == "zone1"
    assert received[0].entity_id == "climate.zone1"


def test_zone_registered_event_uses_zone_id_when_no_entity(coord):
    """ZoneRegisteredEvent falls back to zone_id when no climate_entity_id (A02)."""
    from managers.events import CycleEventType, ZoneRegisteredEvent

    received = []
    coord.zone_dispatcher.subscribe(CycleEventType.ZONE_REGISTERED, received.append)

    coord.register_zone("zone1", {"name": "Zone 1"})  # no climate_entity_id

    assert len(received) == 1
    assert received[0].entity_id == "zone1"


def test_zone_unregistered_event_emitted(coord):
    """ZoneUnregisteredEvent is emitted when unregister_zone is called (A02)."""
    from managers.events import CycleEventType, ZoneUnregisteredEvent

    received = []
    coord.zone_dispatcher.subscribe(CycleEventType.ZONE_UNREGISTERED, received.append)

    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.unregister_zone("zone1")

    assert len(received) == 1
    assert isinstance(received[0], ZoneUnregisteredEvent)
    assert received[0].zone_id == "zone1"


def test_zone_unregistered_event_not_emitted_for_unknown_zone(coord):
    """ZoneUnregisteredEvent is NOT emitted for zones that were never registered (A02)."""
    from managers.events import CycleEventType

    received = []
    coord.zone_dispatcher.subscribe(CycleEventType.ZONE_UNREGISTERED, received.append)

    coord.unregister_zone("nonexistent_zone")

    assert len(received) == 0


def test_mode_sync_receives_zone_registered_event(coord):
    """ModeSync._on_zone_registered is called when a zone registers (A02)."""
    mode_sync = coordinator.ModeSync(coord.hass, coord)

    # Register a zone — should trigger ModeSync._on_zone_registered (logged, no error)
    coord.register_zone("zone1", {"name": "Zone 1"})
    # Verify ModeSync is still in a clean state (no error raised)
    assert "zone1" not in mode_sync._zone_modes


def test_mode_sync_receives_zone_unregistered_event(coord):
    """ModeSync.unregister_zone is called via event when zone is unregistered (A02)."""
    mode_sync = coordinator.ModeSync(coord.hass, coord)
    # Manually seed ModeSync zone tracking as if zone was synced
    mode_sync._zone_modes["zone1"] = "heat"
    mode_sync._sync_disabled_zones.add("zone1")

    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.unregister_zone("zone1")

    # ModeSync should have cleaned up both dicts via event subscription
    assert "zone1" not in mode_sync._zone_modes
    assert "zone1" not in mode_sync._sync_disabled_zones


def test_thermal_group_manager_receives_zone_unregistered_event(coord):
    """ThermalGroupManager.remove_zone is called via event when zone is unregistered (A02)."""
    from unittest.mock import MagicMock

    mock_tgm = MagicMock()
    coord.set_thermal_group_manager(mock_tgm)

    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.unregister_zone("zone1")

    mock_tgm.remove_zone.assert_called_once_with("zone1")


def test_thermal_group_manager_receives_zone_registered_event(coord):
    """ThermalGroupManager subscription for ZONE_REGISTERED fires without error (A02)."""
    from unittest.mock import MagicMock

    mock_tgm = MagicMock()
    coord.set_thermal_group_manager(mock_tgm)

    # Should not raise; debug log is emitted but no method on TGM is called
    coord.register_zone("zone1", {"name": "Zone 1"})


def test_central_controller_receives_zone_unregistered_event(coord):
    """CentralController.update is scheduled via event when zone is unregistered (A02)."""
    from unittest.mock import MagicMock

    mock_controller = MagicMock()
    coord.set_central_controller(mock_controller)

    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.unregister_zone("zone1")

    # hass.async_create_task should have been called with controller.update()
    coord.hass.async_create_task.assert_called()


def test_all_subscribers_receive_zone_unregistered(coord):
    """All three subscribers (ModeSync, CentralController, ThermalGroupManager) receive the event (A02)."""
    from unittest.mock import MagicMock

    # Set up ModeSync subscription
    mode_sync = coordinator.ModeSync(coord.hass, coord)
    mode_sync._zone_modes["zone1"] = "heat"

    # Set up CentralController subscription
    mock_controller = MagicMock()
    coord.set_central_controller(mock_controller)

    # Set up ThermalGroupManager subscription
    mock_tgm = MagicMock()
    coord.set_thermal_group_manager(mock_tgm)

    coord.register_zone("zone1", {"name": "Zone 1"})
    coord.unregister_zone("zone1")

    # ModeSync cleaned up
    assert "zone1" not in mode_sync._zone_modes
    # ThermalGroupManager notified
    mock_tgm.remove_zone.assert_called_once_with("zone1")
    # CentralController update scheduled
    coord.hass.async_create_task.assert_called()


# =============================================================================
# M11: ModeSync turn-on conform (OFF→active adopts prevailing house mode)
# =============================================================================


@pytest.mark.asyncio
async def test_mode_sync_turn_on_conforms_to_prevailing_house_mode(hass):
    """Zone turning ON (off→heat) adopts prevailing COOL mode; zone A stays COOL (M11)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone_a", {"climate_entity_id": "climate.zone_a"})
    coord.register_zone("zone_b", {"climate_entity_id": "climate.zone_b"})

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone_a": MagicMock(state="cool"),
            "climate.zone_b": MagicMock(state="off"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    mode_sync = coordinator.ModeSync(hass, coord)
    await mode_sync.on_mode_change("zone_b", "off", "heat", "climate.zone_b")

    # Should have been called exactly once — to conform zone_b to "cool"
    hass.services.async_call.assert_called_once()
    call_kwargs = hass.services.async_call.call_args
    # Service data dict is the third positional arg
    assert call_kwargs[0][2]["entity_id"] == "climate.zone_b"
    assert call_kwargs[0][2]["hvac_mode"] == "cool"


@pytest.mark.asyncio
async def test_mode_sync_turn_on_no_other_active_zones_keeps_own_mode(hass):
    """Zone turning ON with no other active zones keeps its own mode; no service call (M11)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone_a", {"climate_entity_id": "climate.zone_a"})
    coord.register_zone("zone_b", {"climate_entity_id": "climate.zone_b"})

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone_a": MagicMock(state="off"),
            "climate.zone_b": MagicMock(state="off"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    mode_sync = coordinator.ModeSync(hass, coord)
    await mode_sync.on_mode_change("zone_b", "off", "heat", "climate.zone_b")

    # No other active zones → no conform needed, no propagation (others are OFF)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_mode_sync_deliberate_switch_still_propagates(hass):
    """Zone actively switching HEAT→COOL propagates to other active zones (M11 regression)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone_a", {"climate_entity_id": "climate.zone_a"})
    coord.register_zone("zone_b", {"climate_entity_id": "climate.zone_b"})

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone_a": MagicMock(state="heat"),
            "climate.zone_b": MagicMock(state="heat"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    mode_sync = coordinator.ModeSync(hass, coord)
    await mode_sync.on_mode_change("zone_a", "heat", "cool", "climate.zone_a")

    # Deliberate switch: zone_b must be propagated to "cool"
    hass.services.async_call.assert_called_once()
    assert hass.services.async_call.call_args[0][2]["entity_id"] == "climate.zone_b"
    assert hass.services.async_call.call_args[0][2]["hvac_mode"] == "cool"


@pytest.mark.asyncio
async def test_mode_sync_turn_on_matching_mode_no_redundant_call(hass):
    """Zone turning ON with mode matching prevailing house mode causes no service call (M11)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone_a", {"climate_entity_id": "climate.zone_a"})
    coord.register_zone("zone_b", {"climate_entity_id": "climate.zone_b"})

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone_a": MagicMock(state="cool"),
            "climate.zone_b": MagicMock(state="off"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    mode_sync = coordinator.ModeSync(hass, coord)
    await mode_sync.on_mode_change("zone_b", "off", "cool", "climate.zone_b")

    # zone_b is already turning on in "cool" which matches prevailing → no service call needed
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_mode_sync_conform_reentrant_call_suppressed(hass):
    """Re-entrant on_mode_change during conform is suppressed by _sync_in_progress (M11)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})

    coord.register_zone("zone_a", {"climate_entity_id": "climate.zone_a"})
    coord.register_zone("zone_b", {"climate_entity_id": "climate.zone_b"})

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone_a": MagicMock(state="cool"),
            "climate.zone_b": MagicMock(state="off"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    mode_sync = coordinator.ModeSync(hass, coord)

    # Simulate what the real climate entity does: when the service call sets zone_b
    # to "cool", async_set_hvac_mode fires on_mode_change again (re-entrant).
    async def reentrant_service_call(*args, **kwargs):
        # Re-enter on_mode_change as if zone_b's climate entity processed the mode change
        await mode_sync.on_mode_change("zone_b", "heat", "cool", "climate.zone_b")

    hass.services.async_call = AsyncMock(side_effect=reentrant_service_call)

    await mode_sync.on_mode_change("zone_b", "off", "heat", "climate.zone_b")

    # _sync_in_progress guard must suppress the re-entrant call:
    # async_call is invoked exactly once (the conform call), not twice.
    assert hass.services.async_call.call_count == 1
    assert hass.services.async_call.call_args[0][2]["entity_id"] == "climate.zone_b"
    assert hass.services.async_call.call_args[0][2]["hvac_mode"] == "cool"

    # Flag must be cleared by the finally block after on_mode_change returns.
    assert mode_sync._sync_in_progress is False


@pytest.mark.asyncio
async def test_mode_sync_disabled_originator_turn_on_does_not_propagate(hass):
    """Sync-disabled zone turning ON (off→heat) does NOT propagate to other zones (M11)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone_a", {"climate_entity_id": "climate.zone_a"})
    coord.register_zone("zone_b", {"climate_entity_id": "climate.zone_b"})

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone_a": MagicMock(state="cool"),
            "climate.zone_b": MagicMock(state="off"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    mode_sync = coordinator.ModeSync(hass, coord)
    # zone_b has sync disabled — it is fully independent
    mode_sync.disable_sync_for_zone("zone_b")
    # zone_b turns ON into heat (off→heat): must NOT flip zone_a and must NOT conform itself
    await mode_sync.on_mode_change("zone_b", "off", "heat", "climate.zone_b")

    # sync-disabled originator imposes nothing — no service call at all
    hass.services.async_call.assert_not_called()
    # zone_b's mode is still tracked
    assert mode_sync._zone_modes.get("zone_b") == "heat"


@pytest.mark.asyncio
async def test_mode_sync_disabled_originator_deliberate_switch_does_not_propagate(hass):
    """Sync-disabled zone doing heat→cool does NOT propagate to other active zones (M11)."""
    from unittest.mock import AsyncMock, MagicMock

    coord = coordinator.AdaptiveThermostatCoordinator(hass, {})
    hass.services.async_call = AsyncMock()

    coord.register_zone("zone_a", {"climate_entity_id": "climate.zone_a"})
    coord.register_zone("zone_b", {"climate_entity_id": "climate.zone_b"})

    def fake_states_get(entity_id):
        state_map = {
            "climate.zone_a": MagicMock(state="heat"),
            "climate.zone_b": MagicMock(state="heat"),
        }
        return state_map.get(entity_id)

    hass.states.get = fake_states_get

    mode_sync = coordinator.ModeSync(hass, coord)
    mode_sync.disable_sync_for_zone("zone_b")
    # zone_b deliberately switches heat→cool: must NOT push "cool" onto zone_a
    await mode_sync.on_mode_change("zone_b", "heat", "cool", "climate.zone_b")

    hass.services.async_call.assert_not_called()
    assert mode_sync._zone_modes.get("zone_b") == "cool"


class TestZoneModeHelpers:
    """get_zones_in_mode / get_zone_current_temp read hass.states, not demand state."""

    @staticmethod
    def _coordinator_with_zones(zones):
        from unittest.mock import MagicMock

        from custom_components.adaptive_climate.coordinator import (
            AdaptiveThermostatCoordinator,
        )

        coordinator = AdaptiveThermostatCoordinator.__new__(AdaptiveThermostatCoordinator)
        coordinator._zones = zones
        coordinator._demand_states = {}
        coordinator.hass = MagicMock()
        return coordinator

    @staticmethod
    def _state(state_value, attributes=None):
        from unittest.mock import MagicMock

        state = MagicMock()
        state.state = state_value
        state.attributes = attributes or {}
        return state

    def test_returns_only_zones_whose_entity_state_matches(self):
        coordinator = self._coordinator_with_zones(
            {
                "living": {"climate_entity_id": "climate.living"},
                "bedroom": {"climate_entity_id": "climate.bedroom"},
                "attic": {"climate_entity_id": "climate.attic"},
            }
        )
        states = {
            "climate.living": self._state("cool"),
            "climate.bedroom": self._state("cool"),
            "climate.attic": self._state("off"),
        }
        coordinator.hass.states.get = states.get

        result = coordinator.get_zones_in_mode("cool")
        assert set(result) == {"living", "bedroom"}

    def test_includes_satisfied_zones_that_have_no_demand(self):
        """Demand is irrelevant — a satisfied COOL zone still counts."""
        coordinator = self._coordinator_with_zones({"living": {"climate_entity_id": "climate.living"}})
        coordinator._demand_states = {"living": {"demand": False, "mode": "cool"}}
        coordinator.hass.states.get = {"climate.living": self._state("cool")}.get

        assert "living" in coordinator.get_zones_in_mode("cool")

    def test_skips_zones_with_missing_state_or_entity_id(self):
        coordinator = self._coordinator_with_zones(
            {
                "ghost": {"climate_entity_id": "climate.ghost"},
                "nameless": {},
            }
        )

        def states_get(entity_id):
            if entity_id == "climate.ghost":
                # A real state exists but does not match the requested mode --
                # pins the state-mismatch branch (as opposed to state is None).
                return self._state("off")
            if entity_id is None:
                # zone_data.get("climate_entity_id") for "nameless" is None. If the
                # missing-entity-id guard were removed, this lookup would return a
                # MATCHING state, so "nameless" would incorrectly be included below.
                # This pins the entity-id guard as load-bearing.
                return self._state("cool")
            return None

        coordinator.hass.states.get = states_get

        assert coordinator.get_zones_in_mode("cool") == {}

    def test_get_zone_current_temp_reads_entity_attribute(self):
        coordinator = self._coordinator_with_zones({"living": {"climate_entity_id": "climate.living"}})
        coordinator.hass.states.get = {"climate.living": self._state("cool", {"current_temperature": 23.4})}.get

        assert coordinator.get_zone_current_temp("living") == 23.4

    def test_get_zone_current_temp_returns_none_for_missing_data(self):
        coordinator = self._coordinator_with_zones({"living": {"climate_entity_id": "climate.living"}})
        coordinator.hass.states.get = {"climate.living": self._state("cool", {})}.get

        assert coordinator.get_zone_current_temp("living") is None
        assert coordinator.get_zone_current_temp("nope") is None

    def test_get_zone_current_temp_rejects_non_numeric_attribute(self):
        coordinator = self._coordinator_with_zones({"living": {"climate_entity_id": "climate.living"}})
        coordinator.hass.states.get = {"climate.living": self._state("cool", {"current_temperature": "unknown"})}.get

        assert coordinator.get_zone_current_temp("living") is None

    def test_get_zone_current_temp_rejects_nan_and_infinity(self):
        coordinator = self._coordinator_with_zones({"living": {"climate_entity_id": "climate.living"}})

        for bad_value in (float("nan"), float("inf"), float("-inf")):
            coordinator.hass.states.get = {
                "climate.living": self._state("cool", {"current_temperature": bad_value})
            }.get

            assert coordinator.get_zone_current_temp("living") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
