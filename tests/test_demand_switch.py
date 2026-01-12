"""Tests for demand switch functionality."""
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, AsyncMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock homeassistant modules before importing
sys.modules['homeassistant'] = Mock()
sys.modules['homeassistant.core'] = Mock()
sys.modules['homeassistant.components'] = Mock()
sys.modules['homeassistant.components.switch'] = Mock()
sys.modules['homeassistant.helpers'] = Mock()
sys.modules['homeassistant.helpers.entity_platform'] = Mock()
sys.modules['homeassistant.helpers.typing'] = Mock()
sys.modules['homeassistant.helpers.event'] = Mock()

# Create mock base class
class MockSwitchEntity:
    """Mock SwitchEntity base class."""
    pass

sys.modules['homeassistant.components.switch'].SwitchEntity = MockSwitchEntity

# Mock async_track_state_change_event
def mock_track_state_change(hass, entities, callback):
    """Mock state tracking."""
    pass

sys.modules['homeassistant.helpers.event'].async_track_state_change_event = mock_track_state_change

# Import the switch module
from custom_components.adaptive_thermostat.switch import DemandSwitch, DEFAULT_DEMAND_THRESHOLD


@pytest.fixture
def hass():
    """Create a mock Home Assistant instance."""
    mock_hass = MagicMock()
    mock_hass.states = MagicMock()
    return mock_hass


@pytest.fixture
def demand_switch(hass):
    """Create a demand switch instance."""
    return DemandSwitch(
        hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
        demand_threshold=5.0,
    )


def test_demand_switch_on_when_heating(hass, demand_switch):
    """Test demand switch turns on when heater is on and PID output > threshold."""
    # Mock climate entity state with PID output above threshold
    climate_state = Mock()
    climate_state.attributes = {
        "pid_output": 50.0,
        "heater_entity_id": "switch.living_room_heater",
    }
    hass.states.get.return_value = climate_state

    # Update switch state
    import asyncio
    asyncio.run(demand_switch.async_update())

    # Verify switch is ON
    assert demand_switch.is_on is True


def test_demand_switch_off_when_satisfied(hass, demand_switch):
    """Test demand switch turns off when PID output is zero."""
    # Mock climate entity state with zero PID output
    climate_state = Mock()
    climate_state.attributes = {
        "pid_output": 0.0,
        "heater_entity_id": "switch.living_room_heater",
    }
    hass.states.get.return_value = climate_state

    # Update switch state
    import asyncio
    asyncio.run(demand_switch.async_update())

    # Verify switch is OFF
    assert demand_switch.is_on is False


def test_demand_switch_threshold(hass, demand_switch):
    """Test demand switch respects configured threshold."""
    # Test PID output just below threshold (should be OFF)
    climate_state = Mock()
    climate_state.attributes = {
        "pid_output": 4.9,
        "heater_entity_id": "switch.living_room_heater",
    }
    hass.states.get.return_value = climate_state

    import asyncio
    asyncio.run(demand_switch.async_update())
    assert demand_switch.is_on is False

    # Test PID output just above threshold (should be ON)
    climate_state.attributes["pid_output"] = 5.1
    asyncio.run(demand_switch.async_update())
    assert demand_switch.is_on is True

    # Test PID output exactly at threshold (should be OFF)
    climate_state.attributes["pid_output"] = 5.0
    asyncio.run(demand_switch.async_update())
    assert demand_switch.is_on is False


def test_demand_switch_fallback_to_heater_state(hass, demand_switch):
    """Test demand switch falls back to heater state when PID output unavailable."""
    # Mock climate entity without PID output attribute
    climate_state = Mock()
    climate_state.attributes = {
        "heater_entity_id": "switch.living_room_heater",
    }
    hass.states.get.side_effect = lambda entity_id: {
        "climate.living_room": climate_state,
        "switch.living_room_heater": Mock(state="on"),
    }.get(entity_id)

    import asyncio
    asyncio.run(demand_switch.async_update())

    # Verify switch falls back to heater state
    assert demand_switch.is_on is True

    # Test with heater off
    hass.states.get.side_effect = lambda entity_id: {
        "climate.living_room": climate_state,
        "switch.living_room_heater": Mock(state="off"),
    }.get(entity_id)
    asyncio.run(demand_switch.async_update())
    assert demand_switch.is_on is False


def test_demand_switch_unavailable_when_climate_missing(hass, demand_switch):
    """Test demand switch is unavailable when climate entity doesn't exist."""
    # Mock climate entity not found
    hass.states.get.return_value = None

    # Verify switch is not available
    assert demand_switch.available is False


def test_demand_switch_extra_attributes(hass, demand_switch):
    """Test demand switch provides useful extra state attributes."""
    # Mock climate entity state
    climate_state = Mock()
    climate_state.attributes = {
        "pid_output": 35.0,
        "heater_entity_id": "switch.living_room_heater",
    }
    hass.states.get.return_value = climate_state

    # Get extra attributes
    attrs = demand_switch.extra_state_attributes

    # Verify attributes
    assert attrs["climate_entity_id"] == "climate.living_room"
    assert attrs["demand_threshold"] == 5.0
    assert attrs["pid_output"] == 35.0
    assert attrs["zone_id"] == "living_room"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
