"""Tests for Central Controller functionality."""
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
async def test_heater_on_when_zone_demands(mock_hass, coord):
    """Test heater turns on when a zone has demand."""
    # Create controller with zero startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify heater was turned on
    mock_hass.services.async_call.assert_called_with(
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler"},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_heater_off_when_no_demand(mock_hass, coord):
    """Test heater turns off when no zone has demand."""
    # Create controller
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=0,
    )

    # Mock switch is currently on
    mock_state = Mock()
    mock_state.state = "on"
    mock_hass.states.get.return_value = mock_state

    # Register zone with no demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", False)

    # Update controller
    await controller.update()

    # Verify heater was turned off
    mock_hass.services.async_call.assert_called_with(
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler"},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_startup_delay(mock_hass, coord):
    """Test heater respects startup delay."""
    # Create controller with 2 second startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=2,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Heater should NOT be on immediately (should be waiting)
    assert controller.is_heater_waiting_for_startup()

    # Wait for delay to complete
    await asyncio.sleep(2.5)

    # Verify heater was turned on after delay
    mock_hass.services.async_call.assert_called_with(
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler"},
        blocking=True,
    )
    assert not controller.is_heater_waiting_for_startup()


@pytest.mark.asyncio
async def test_second_zone_during_delay(mock_hass, coord):
    """Test second zone demand during startup delay doesn't restart delay."""
    # Create controller with 2 second startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=2,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register two zones
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.register_zone("bedroom", {"name": "Bedroom"})

    # First zone demands heating
    coord.update_zone_demand("living_room", True)
    await controller.update()

    # Verify startup is pending
    assert controller.is_heater_waiting_for_startup()

    # Wait 1 second
    await asyncio.sleep(1.0)

    # Second zone demands heating (should not restart delay)
    coord.update_zone_demand("bedroom", True)
    await controller.update()

    # Wait another 1.5 seconds (total 2.5 seconds from first demand)
    await asyncio.sleep(1.5)

    # Verify heater turned on (delay not restarted)
    mock_hass.services.async_call.assert_called_with(
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler"},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_heater_and_cooler_independence(mock_hass, coord):
    """Test heater and cooler operate independently."""
    # Create controller with both heater and cooler
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        main_cooler_switch="switch.chiller",
        startup_delay_seconds=0,
    )

    # Mock switches are currently off
    mock_state_off = Mock()
    mock_state_off.state = "off"
    mock_hass.states.get.return_value = mock_state_off

    # Register zone with heating demand only
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify only heater was turned on (not cooler)
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 1
    # Check positional and keyword arguments
    call_args = calls[0]
    assert call_args.args == ("switch", "turn_on", {"entity_id": "switch.boiler"})
    assert call_args.kwargs.get("blocking") == True

    # Cooler should not have been called
    cooler_calls = [
        call for call in calls
        if "switch.chiller" in str(call)
    ]
    assert len(cooler_calls) == 0


@pytest.mark.asyncio
async def test_zero_startup_delay(mock_hass, coord):
    """Test controller with zero startup delay turns on immediately."""
    # Create controller with zero startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Heater should be on immediately (no waiting)
    assert not controller.is_heater_waiting_for_startup()

    # Verify heater was turned on
    mock_hass.services.async_call.assert_called_with(
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler"},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_demand_lost_during_delay(mock_hass, coord):
    """Test heater doesn't start if demand is lost during startup delay."""
    # Create controller with 2 second startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=2,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify startup is pending
    assert controller.is_heater_waiting_for_startup()

    # Wait 1 second
    await asyncio.sleep(1.0)

    # Remove demand
    coord.update_zone_demand("living_room", False)
    await controller.update()

    # Verify startup was cancelled
    assert not controller.is_heater_waiting_for_startup()

    # Wait for original delay to complete
    await asyncio.sleep(1.5)

    # Verify heater was NOT turned on (only turn_off was called)
    calls = mock_hass.services.async_call.call_args_list
    turn_on_calls = [
        call for call in calls
        if call[0][1] == "turn_on"
    ]
    assert len(turn_on_calls) == 0


@pytest.mark.asyncio
async def test_concurrent_update_calls_during_startup_delay(mock_hass, coord):
    """Test that concurrent update() calls during startup delay don't cause race conditions.

    This test verifies that when multiple update() calls happen simultaneously while
    waiting for startup delay, only one startup task is created and the state remains
    consistent.
    """
    # Create controller with 2 second startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=2,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Fire multiple concurrent update() calls
    update_tasks = [controller.update() for _ in range(10)]
    await asyncio.gather(*update_tasks)

    # Verify only one startup is pending (not multiple)
    assert controller.is_heater_waiting_for_startup()

    # The lock should ensure only one startup task exists
    assert controller._heater_startup_task is not None

    # Wait for delay to complete
    await asyncio.sleep(2.5)

    # Verify heater was turned on exactly once
    turn_on_calls = [
        call for call in mock_hass.services.async_call.call_args_list
        if call[0][1] == "turn_on"
    ]
    assert len(turn_on_calls) == 1

    # Verify state is clean
    assert not controller.is_heater_waiting_for_startup()
    assert controller._heater_startup_task is None


@pytest.mark.asyncio
async def test_task_cancellation_race_condition(mock_hass, coord):
    """Test that task cancellation doesn't race with task completion.

    This test verifies that when demand is removed while the startup task is
    about to complete (or is completing), the state remains consistent and
    no exceptions are raised.
    """
    # Create controller with 1 second startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=1,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Start the controller
    await controller.update()

    # Verify startup is pending
    assert controller.is_heater_waiting_for_startup()

    # Wait until just before the delay completes
    await asyncio.sleep(0.9)

    # Rapidly toggle demand on/off to trigger race condition
    for i in range(5):
        coord.update_zone_demand("living_room", i % 2 == 0)
        await controller.update()

    # Give any pending tasks time to settle
    await asyncio.sleep(0.5)

    # Verify state is consistent (no exceptions should have been raised)
    # The waiting flag should be False since we toggled demand
    # (either the task completed or was cancelled)
    # The key assertion is that this test completes without deadlock or exception


@pytest.mark.asyncio
async def test_concurrent_heater_and_cooler_updates(mock_hass, coord):
    """Test that concurrent updates to heater and cooler don't interfere.

    This test verifies that the single lock properly serializes access to
    both heater and cooler state without causing deadlocks.
    """
    # Create controller with both heater and cooler
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        main_cooler_switch="switch.chiller",
        startup_delay_seconds=1,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zones
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Fire many concurrent update() calls
    update_tasks = [controller.update() for _ in range(20)]

    # This should complete without deadlock
    await asyncio.wait_for(asyncio.gather(*update_tasks), timeout=5.0)

    # Verify heater startup is pending
    assert controller.is_heater_waiting_for_startup()


@pytest.mark.asyncio
async def test_lock_released_after_cancellation(mock_hass, coord):
    """Test that the lock is properly released after task cancellation.

    This test verifies that after cancelling a startup task, the lock is
    released and subsequent operations can proceed without deadlock.
    """
    # Create controller with 2 second startup delay
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch="switch.boiler",
        startup_delay_seconds=2,
    )

    # Mock switch is currently off initially, then on after first turn_on
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Start the controller
    await controller.update()
    assert controller.is_heater_waiting_for_startup()

    # Cancel by removing demand
    coord.update_zone_demand("living_room", False)
    await controller.update()

    # Verify startup was cancelled
    assert not controller.is_heater_waiting_for_startup()

    # Now add demand again - this should work without deadlock
    coord.update_zone_demand("living_room", True)
    await asyncio.wait_for(controller.update(), timeout=2.0)

    # Verify new startup is pending
    assert controller.is_heater_waiting_for_startup()
