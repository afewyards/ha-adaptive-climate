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
sys.modules['homeassistant.exceptions'] = Mock()
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
    """Test that HEAT mode syncs to all other active zones.

    Mode sync only affects zones in an active mode (heat/cool).
    OFF zones remain independent and are not synced.
    """
    # Register zones with climate entity IDs
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})
    coord.register_zone("kitchen", {"climate_entity_id": "climate.kitchen"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Mock climate entity states (other zones in COOL mode - active)
    def mock_get_state(entity_id):
        state = Mock()
        if entity_id == "climate.living_room":
            state.state = "heat"  # Originating zone
        else:
            state.state = "cool"  # Other zones in active mode
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change in living_room to HEAT
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="cool",
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

    # Mock climate entity states (kitchen in active mode, bedroom in active mode but sync-disabled)
    def mock_get_state(entity_id):
        state = Mock()
        if entity_id == "climate.living_room":
            state.state = "heat"
        else:
            state.state = "cool"  # Both in active mode
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change to HEAT
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="cool",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Should only sync to kitchen (bedroom has sync disabled)
    assert mock_hass.services.async_call.call_count == 1

    call_obj = mock_hass.services.async_call.call_args
    assert call_obj[0][2]["entity_id"] == "climate.kitchen"


@pytest.mark.asyncio
async def test_off_zone_not_synced(mock_hass, coord):
    """Test that an OFF zone is NOT synced when another zone changes mode.

    OFF zones should remain independent - mode sync only affects zones
    that are already in an active mode (heat/cool).
    """
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

    # Bedroom should NOT be synced because it's OFF (independent)
    assert mock_hass.services.async_call.call_count == 0


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

    # Mock climate entity states (bedroom in active mode)
    def mock_get_state(entity_id):
        state = Mock()
        if entity_id == "climate.living_room":
            state.state = "heat"
        else:
            state.state = "cool"  # Bedroom in active mode
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change to HEAT
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="cool",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Bedroom should now be synced (sync was re-enabled, and it's in active mode)
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


@pytest.mark.asyncio
async def test_sync_not_triggering_reverse_sync(mock_hass, coord):
    """Test that sync does not trigger a reverse sync (feedback loop prevention).

    When zone A syncs zone B to HEAT mode, zone B's resulting mode change
    should NOT trigger a sync back to zone A.
    """
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Initially, sync flag should be False
    assert mode_sync.is_sync_in_progress() is False

    # Mock climate entity states (bedroom in active mode so it gets synced)
    def mock_get_state(entity_id):
        state = Mock()
        if entity_id == "climate.living_room":
            state.state = "heat"
        else:
            state.state = "cool"  # Bedroom in active mode
        return state

    mock_hass.states.get = mock_get_state

    # Track calls to verify no reverse sync
    call_count_before_reverse = 0

    # Create a side effect that simulates the synced zone calling back
    original_async_call = mock_hass.services.async_call

    async def mock_async_call_with_callback(*args, **kwargs):
        nonlocal call_count_before_reverse
        # Call the original mock
        await original_async_call(*args, **kwargs)

        # Simulate the feedback: when bedroom is set to heat,
        # it would normally trigger on_mode_change for bedroom
        if args[0] == "climate" and args[1] == "set_hvac_mode":
            entity_id = args[2].get("entity_id")
            if entity_id == "climate.bedroom":
                call_count_before_reverse = mock_hass.services.async_call.call_count
                # This simulates the reverse sync attempt that should be blocked
                await mode_sync.on_mode_change(
                    zone_id="bedroom",
                    old_mode="cool",
                    new_mode="heat",
                    climate_entity_id="climate.bedroom",
                )

    mock_hass.services.async_call = AsyncMock(side_effect=mock_async_call_with_callback)

    # Trigger initial mode change in living_room
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="cool",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Should have only 1 service call (living_room -> bedroom)
    # The reverse sync from bedroom should have been blocked
    assert mock_hass.services.async_call.call_count == 1

    # Verify the call was to sync bedroom to heat
    call_obj = mock_hass.services.async_call.call_args
    assert call_obj[0][2]["entity_id"] == "climate.bedroom"
    assert call_obj[0][2]["hvac_mode"] == "heat"

    # After sync completes, flag should be False
    assert mode_sync.is_sync_in_progress() is False


@pytest.mark.asyncio
async def test_multiple_zones_syncing_without_loop(mock_hass, coord):
    """Test that multiple zones can be synced without creating a feedback loop.

    When zone A syncs zones B, C, and D to HEAT mode, none of those zones'
    resulting mode changes should trigger additional syncs.
    """
    # Register multiple zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})
    coord.register_zone("kitchen", {"climate_entity_id": "climate.kitchen"})
    coord.register_zone("bathroom", {"climate_entity_id": "climate.bathroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Mock climate entity states (all zones in active mode)
    def mock_get_state(entity_id):
        state = Mock()
        if entity_id == "climate.living_room":
            state.state = "heat"  # Originating zone
        else:
            state.state = "cool"  # Other zones in active mode
        return state

    mock_hass.states.get = mock_get_state

    # Track all on_mode_change calls
    on_mode_change_calls = []
    original_on_mode_change = mode_sync.on_mode_change

    async def tracked_on_mode_change(zone_id, old_mode, new_mode, climate_entity_id):
        on_mode_change_calls.append(zone_id)
        return await original_on_mode_change(zone_id, old_mode, new_mode, climate_entity_id)

    mode_sync.on_mode_change = tracked_on_mode_change

    # Create a side effect that simulates all synced zones calling back
    original_async_call = mock_hass.services.async_call

    async def mock_async_call_with_callbacks(*args, **kwargs):
        await original_async_call(*args, **kwargs)

        # Simulate feedback from each synced zone
        if args[0] == "climate" and args[1] == "set_hvac_mode":
            entity_id = args[2].get("entity_id")
            zone_id = entity_id.replace("climate.", "")
            # Simulate the mode change event that would trigger on_mode_change
            await mode_sync.on_mode_change(
                zone_id=zone_id,
                old_mode="cool",
                new_mode="heat",
                climate_entity_id=entity_id,
            )

    mock_hass.services.async_call = AsyncMock(side_effect=mock_async_call_with_callbacks)

    # Trigger initial mode change in living_room
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="cool",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # Should have exactly 3 service calls (living_room -> bedroom, kitchen, bathroom)
    # No additional calls from the reverse syncs
    assert mock_hass.services.async_call.call_count == 3

    # Verify all expected zones were synced
    entity_ids_called = {
        call[0][2]["entity_id"]
        for call in mock_hass.services.async_call.call_args_list
    }
    assert entity_ids_called == {
        "climate.bedroom",
        "climate.kitchen",
        "climate.bathroom",
    }

    # After sync completes, flag should be False
    assert mode_sync.is_sync_in_progress() is False


@pytest.mark.asyncio
async def test_sync_flag_cleared_on_exception(mock_hass, coord):
    """Test that sync flag is cleared even if an exception occurs during sync."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Mock climate entity states
    def mock_get_state(entity_id):
        state = Mock()
        state.state = "off"
        return state

    mock_hass.states.get = mock_get_state

    # Make the service call raise an exception
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("Service call failed"))

    # Initially, sync flag should be False
    assert mode_sync.is_sync_in_progress() is False

    # Trigger mode change - should not raise (exception is caught in _set_zone_mode)
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="off",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # After sync (even with exception), flag should be False
    assert mode_sync.is_sync_in_progress() is False


@pytest.mark.asyncio
async def test_is_sync_in_progress_method(mock_hass, coord):
    """Test that is_sync_in_progress correctly reports sync state."""
    # Register zones
    coord.register_zone("living_room", {"climate_entity_id": "climate.living_room"})
    coord.register_zone("bedroom", {"climate_entity_id": "climate.bedroom"})

    # Create mode sync
    mode_sync = coordinator.ModeSync(mock_hass, coord)

    # Initially should be False
    assert mode_sync.is_sync_in_progress() is False

    # Track sync state during the sync operation
    sync_states_during_operation = []

    def mock_get_state(entity_id):
        # Record sync state during state lookup (happens during sync)
        sync_states_during_operation.append(mode_sync.is_sync_in_progress())
        state = Mock()
        state.state = "off"
        return state

    mock_hass.states.get = mock_get_state

    # Trigger mode change
    await mode_sync.on_mode_change(
        zone_id="living_room",
        old_mode="off",
        new_mode="heat",
        climate_entity_id="climate.living_room",
    )

    # During sync, the flag should have been True
    assert len(sync_states_during_operation) > 0
    assert all(state is True for state in sync_states_during_operation)

    # After sync, flag should be False again
    assert mode_sync.is_sync_in_progress() is False
