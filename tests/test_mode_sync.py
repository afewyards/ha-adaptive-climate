"""Tests for ModeSync class."""
import asyncio
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock
from datetime import timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat"))

# Mock homeassistant modules before importing coordinator
sys.modules['homeassistant'] = Mock()
sys.modules['homeassistant.core'] = Mock()
sys.modules['homeassistant.helpers'] = Mock()
sys.modules['homeassistant.helpers.update_coordinator'] = Mock()

# Create mock base class
class MockDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval

sys.modules['homeassistant.helpers.update_coordinator'].DataUpdateCoordinator = MockDataUpdateCoordinator

# Import coordinator module
import coordinator


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def coord(mock_hass):
    """Create a coordinator instance."""
    return coordinator.AdaptiveThermostatCoordinator(mock_hass)


@pytest.mark.asyncio
async def test_heat_mode_sync(mock_hass, coord):
    """Test that HEAT mode syncs to all other zones."""
    # Register zones with climate entity IDs
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})
    coord.register_zone("kitchen", {"climate_entity_id": "climate.kitchen"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Mock climate entity states (all initially in OFF mode)
    def mock_get_state(entity_id):
        state = Mock()
        state.state = "off"
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change in living_room to HEAT
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="off",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Should have called set_hvac_mode for bedroom and kitchen (not living_room)
    assert mock_hass.services.async_call.call_count == 2

    # Check that both zones were synced to heat mode
    calls = mock_hass.services.async_call.call_args_list
    entity_ids_called = {call[0][2]["entity_id"] for call in calls}
    assert "climate.bedroom" in entity_ids_called
    assert "climate.kitchen" in entity_ids_called
    assert "climate.living_room" not in entity_ids_called

    # All calls should be to set_hvac_mode with heat
    for call_obj in calls:
        assert call_obj[0][0] == "climate"
        assert call_obj[0][1] == "set_hvac_mode"
        assert call_obj[0][2]["hvac_mode"] == "heat"


@pytest.mark.asyncio
async def test_cool_mode_sync(mock_hass, coord):
    """Test that COOL mode syncs to all other zones."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Mock climate entity states
    def mock_get_state(entity_id):
        state = Mock()
        state.state = "heat"
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change to COOL
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="heat",
        new_mode="cool",
        climate_entity_id="climate.living_room",
    )

    # Should have called set_hvac_mode for bedroom
    assert mock_hass.services.async_call.call_count == 1

    call_obj = mock_hass.services.async_call.call_args
    assert call_obj[0][0] == "climate"
    assert call_obj[0][1] == "set_hvac_mode"
    assert call_obj[0][2]["entity_id"] == "climate.bedroom"
    assert call_obj[0][2]["hvac_mode"] == "cool"


@pytest.mark.asyncio
async def test_off_mode_independence(mock_hass, coord):
    """Test that OFF mode does NOT sync to other zones."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Trigger mode change to OFF
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="heat",
        new_mode="off",
        climate_entity_id="climate.living_room",
    )

    # Should NOT have called set_hvac_mode for any zone (OFF is independent)
    assert mock_hass.services.async_call.call_count == 0


@pytest.mark.asyncio
async def test_sync_disabled_per_zone(mock_hass, coord):
    """Test that sync can be disabled for specific zones."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})
    coord.register_zone("kitchen", {"climate_entity_id": "climate.kitchen"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Disable sync for bedroom
    mode_sync.disable_sync_for_zone("bedroom")
    assert mode_sync.is_sync_disabled("bedroom") is True
    assert mode_sync.is_sync_disabled("kitchen") is False

    # Mock climate entity states
    def mock_get_state(entity_id):
        state = Mock()
        state.state = "off"
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change to HEAT
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="off",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Should only sync to kitchen (bedroom has sync disabled)
    assert mock_hass.services.async_call.call_count == 1

    call_obj = mock_hass.services.async_call.call_args
    assert call_obj[0][2]["entity_id"] == "climate.kitchen"


@pytest.mark.asyncio
async def test_off_zone_getting_synced(mock_hass, coord):
    """Test that an OFF zone gets synced when another zone goes to HEAT."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Mock climate entity states (bedroom is OFF)
    def mock_get_state(entity_id):
        state = Mock()
        if entity_id == "climate.bedroom":
            state.state = "off"
        else:
            state.state = "heat"
        return state

    mock_hass.states.get = mock_get_state

    # Living room goes to HEAT mode
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="off",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Bedroom should be synced from OFF to HEAT
    assert mock_hass.services.async_call.call_count == 1

    call_obj = mock_hass.services.async_call.call_args
    assert call_obj[0][0] == "climate"
    assert call_obj[0][1] == "set_hvac_mode"
    assert call_obj[0][2]["entity_id"] == "climate.bedroom"
    assert call_obj[0][2]["hvac_mode"] == "heat"


@pytest.mark.asyncio
async def test_enable_sync_after_disabling(mock_hass, coord):
    """Test that sync can be re-enabled after being disabled."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Disable and then enable sync for bedroom
    mode_sync.disable_sync_for_zone("bedroom")
    assert mode_sync.is_sync_disabled("bedroom") is True

    mode_sync.enable_sync_for_zone("bedroom")
    assert mode_sync.is_sync_disabled("bedroom") is False

    # Mock climate entity states
    def mock_get_state(entity_id):
        state = Mock()
        state.state = "off"
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change to HEAT
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="off",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Bedroom should now be synced (sync was re-enabled)
    assert mock_hass.services.async_call.call_count == 1

    call_obj = mock_hass.services.async_call.call_args
    assert call_obj[0][2]["entity_id"] == "climate.bedroom"


@pytest.mark.asyncio
async def test_zone_already_in_target_mode(mock_hass, coord):
    """Test that no service call is made if zone is already in target mode."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Mock climate entity states (bedroom is already in HEAT mode)
    def mock_get_state(entity_id):
        state = Mock()
        state.state = "heat"
        return state

    mock_hass.states.get = mock_get_state

    # Living room goes to HEAT mode
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="off",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Should NOT call service since bedroom is already in heat mode
    assert mock_hass.services.async_call.call_count == 0
