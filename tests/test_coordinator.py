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

# Import thermal_coupling BEFORE coordinator (to ensure it's in sys.modules)
from adaptive.thermal_coupling import ThermalCouplingLearner

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


def test_coordinator_has_coupling_learner(coord):
    """Test that coordinator has a thermal coupling learner accessible."""
    # Learner should be accessible via property
    assert hasattr(coord, "thermal_coupling_learner")
    learner = coord.thermal_coupling_learner
    assert learner is not None

    # Verify it's a ThermalCouplingLearner instance
    assert isinstance(learner, ThermalCouplingLearner)


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
# Thermal Coupling Observation Trigger Tests (Story 5.3)
# =============================================================================


def test_demand_true_starts_observation(hass):
    """Test that demand=True transition starts a thermal coupling observation."""
    # Set up weather entity for outdoor temp
    hass.data = {
        const.DOMAIN: {
            "weather_entity": "weather.home"
        }
    }
    mock_state = MagicMock()
    mock_state.attributes = {"temperature": 5.0}
    hass.states.get.return_value = mock_state

    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Register zones with temperatures
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.0})
    coord.register_zone("zone2", {"name": "Zone 2", "current_temp": 19.0})
    coord.register_zone("zone3", {"name": "Zone 3", "current_temp": 18.0})

    # Initially no pending observations
    learner = coord.thermal_coupling_learner
    assert len(learner._pending) == 0

    # Zone1 starts heating (demand False -> True)
    coord.update_zone_demand("zone1", True, "heat")

    # Observation should be started for zone1
    assert "zone1" in learner._pending
    context = learner._pending["zone1"]
    assert context.source_zone == "zone1"
    assert context.source_temp_start == 20.0
    assert context.outdoor_temp_start == 5.0
    # Target temps should include zone2 and zone3, not zone1
    assert "zone2" in context.target_temps_start
    assert "zone3" in context.target_temps_start
    assert "zone1" not in context.target_temps_start


def test_demand_false_ends_observation(hass):
    """Test that demand=False transition ends observation and creates records."""
    from datetime import datetime, timedelta

    # Set up weather entity
    hass.data = {
        const.DOMAIN: {
            "weather_entity": "weather.home"
        }
    }
    mock_state = MagicMock()
    mock_state.attributes = {"temperature": 5.0}
    hass.states.get.return_value = mock_state

    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Register zones with temperatures
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.0})
    coord.register_zone("zone2", {"name": "Zone 2", "current_temp": 19.0})
    coord.register_zone("zone3", {"name": "Zone 3", "current_temp": 18.0})

    # Start heating zone1
    coord.update_zone_demand("zone1", True, "heat")
    learner = coord.thermal_coupling_learner
    assert "zone1" in learner._pending

    # Simulate temperature changes and time passing
    coord.update_zone_temp("zone1", 22.0)  # Source warmed up
    coord.update_zone_temp("zone2", 19.5)  # Target warmed a bit
    coord.update_zone_temp("zone3", 18.2)  # Target warmed a bit

    # Manually adjust start time to simulate 20 minutes passing
    learner._pending["zone1"] = learner._pending["zone1"].__class__(
        source_zone=learner._pending["zone1"].source_zone,
        start_time=datetime.now() - timedelta(minutes=20),
        source_temp_start=learner._pending["zone1"].source_temp_start,
        target_temps_start=learner._pending["zone1"].target_temps_start,
        outdoor_temp_start=learner._pending["zone1"].outdoor_temp_start,
    )

    # Zone1 stops heating (demand True -> False)
    coord.update_zone_demand("zone1", False, "heat")

    # Pending observation should be cleared
    assert "zone1" not in learner._pending


def test_mass_recovery_skips_observation(hass):
    """Test that observation is skipped when >50% zones are demanding (mass recovery)."""
    # Set up weather entity
    hass.data = {
        const.DOMAIN: {
            "weather_entity": "weather.home"
        }
    }
    mock_state = MagicMock()
    mock_state.attributes = {"temperature": 5.0}
    hass.states.get.return_value = mock_state

    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Register 4 zones with temperatures
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.0})
    coord.register_zone("zone2", {"name": "Zone 2", "current_temp": 19.0})
    coord.register_zone("zone3", {"name": "Zone 3", "current_temp": 18.0})
    coord.register_zone("zone4", {"name": "Zone 4", "current_temp": 17.0})

    # First, start zone1, zone2, and zone3 heating (75% of zones)
    coord.update_zone_demand("zone1", True, "heat")
    coord.update_zone_demand("zone2", True, "heat")
    coord.update_zone_demand("zone3", True, "heat")

    learner = coord.thermal_coupling_learner

    # zone1 started when it was the only one, so it should have an observation
    # zone2 was started when 2/4 = 50%, at threshold - should start
    # zone3 was started when 3/4 = 75%, above threshold - should NOT start
    # (depends on implementation - let's verify the last one)

    # Now try to start zone4 when 3/4 already demanding (75%)
    # Clear any existing pending to test fresh
    learner._pending.clear()

    # With 3 zones already demanding out of 4, starting zone4 would be 100%
    # But we check BEFORE adding the new demand
    # So zone4 starting when 3/4 (75%) already demanding should skip
    coord.update_zone_demand("zone4", True, "heat")

    # zone4 should NOT have a pending observation (mass recovery)
    assert "zone4" not in learner._pending


def test_observation_only_for_heat_mode(hass):
    """Test that observations are only started for heating mode, not cooling."""
    # Set up weather entity
    hass.data = {
        const.DOMAIN: {
            "weather_entity": "weather.home"
        }
    }
    mock_state = MagicMock()
    mock_state.attributes = {"temperature": 25.0}
    hass.states.get.return_value = mock_state

    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 26.0})
    coord.register_zone("zone2", {"name": "Zone 2", "current_temp": 27.0})

    learner = coord.thermal_coupling_learner

    # Zone1 starts cooling (not heating)
    coord.update_zone_demand("zone1", True, "cool")

    # No observation should be started for cooling
    assert "zone1" not in learner._pending


def test_observation_not_started_without_outdoor_temp(hass):
    """Test that observation is not started when outdoor temp unavailable."""
    # No weather entity configured
    hass.data = {}

    coord = coordinator.AdaptiveThermostatCoordinator(hass)

    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.0})
    coord.register_zone("zone2", {"name": "Zone 2", "current_temp": 19.0})

    learner = coord.thermal_coupling_learner

    # Zone1 starts heating
    coord.update_zone_demand("zone1", True, "heat")

    # No observation should be started (outdoor temp unavailable)
    assert "zone1" not in learner._pending


def test_demand_transition_no_change_no_observation(coord):
    """Test that no observation starts when demand doesn't actually change."""
    # Register zones
    coord.register_zone("zone1", {"name": "Zone 1", "current_temp": 20.0})

    learner = coord.thermal_coupling_learner

    # Set demand to False (same as initial)
    coord.update_zone_demand("zone1", False, "heat")

    # No observation should be started
    assert "zone1" not in learner._pending

    # Set demand to True, then True again
    coord.update_zone_demand("zone1", True, "heat")
    # Clear pending to test
    learner._pending.clear()

    # Call again with same state
    coord.update_zone_demand("zone1", True, "heat")

    # Should not start new observation (no transition)
    assert "zone1" not in learner._pending


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
