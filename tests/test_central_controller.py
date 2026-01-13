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

# Create mock exception classes
class MockServiceNotFound(Exception):
    """Mock ServiceNotFound exception."""
    pass


class MockHomeAssistantError(Exception):
    """Mock HomeAssistantError exception."""
    pass


# Set up exceptions module with mock classes
mock_exceptions_module = Mock()
mock_exceptions_module.ServiceNotFound = MockServiceNotFound
mock_exceptions_module.HomeAssistantError = MockHomeAssistantError
sys.modules['homeassistant.exceptions'] = mock_exceptions_module

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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
        main_cooler_switch=["switch.chiller"],
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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
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
        main_heater_switch=["switch.boiler"],
        main_cooler_switch=["switch.chiller"],
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
        main_heater_switch=["switch.boiler"],
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


# ============================================================================
# Error Handling and Retry Logic Tests (Story 1.4)
# ============================================================================


@pytest.mark.asyncio
async def test_service_call_success(mock_hass, coord):
    """Test successful service call resets failure counter."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Simulate previous failures
    controller._consecutive_failures["switch.boiler"] = 2

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller (should succeed)
    await controller.update()

    # Verify service was called
    mock_hass.services.async_call.assert_called_with(
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler"},
        blocking=True,
    )

    # Verify failure counter was reset
    assert controller.get_consecutive_failures("switch.boiler") == 0


@pytest.mark.asyncio
async def test_service_not_found_no_retry(mock_hass, coord):
    """Test ServiceNotFound exception does not trigger retry."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Make service call raise ServiceNotFound
    mock_hass.services.async_call = AsyncMock(
        side_effect=MockServiceNotFound("Service not found")
    )

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify service was only called once (no retry for ServiceNotFound)
    assert mock_hass.services.async_call.call_count == 1

    # Verify failure was recorded
    assert controller.get_consecutive_failures("switch.boiler") == 1


@pytest.mark.asyncio
async def test_home_assistant_error_with_retry(mock_hass, coord):
    """Test HomeAssistantError triggers retry with exponential backoff."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Make service call fail twice then succeed
    call_count = [0]

    async def mock_service_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise MockHomeAssistantError("Transient error")
        return None

    mock_hass.services.async_call = AsyncMock(side_effect=mock_service_call)

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify service was called 3 times (2 failures + 1 success)
    assert mock_hass.services.async_call.call_count == 3

    # Verify failure counter was reset after success
    assert controller.get_consecutive_failures("switch.boiler") == 0


@pytest.mark.asyncio
async def test_all_retries_exhausted(mock_hass, coord):
    """Test all retries are exhausted and failure is recorded."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Make service call always fail
    mock_hass.services.async_call = AsyncMock(
        side_effect=MockHomeAssistantError("Persistent error")
    )

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify service was called MAX_SERVICE_CALL_RETRIES times
    assert mock_hass.services.async_call.call_count == coordinator.MAX_SERVICE_CALL_RETRIES

    # Verify failure was recorded
    assert controller.get_consecutive_failures("switch.boiler") == 1


@pytest.mark.asyncio
async def test_consecutive_failure_warning_threshold(mock_hass, coord):
    """Test warning is emitted when consecutive failure threshold is reached."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Make service call always fail with ServiceNotFound (immediate failure, no retry)
    mock_hass.services.async_call = AsyncMock(
        side_effect=MockServiceNotFound("Service not found")
    )

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Call update multiple times to trigger threshold
    for i in range(coordinator.CONSECUTIVE_FAILURE_WARNING_THRESHOLD):
        mock_hass.services.async_call.reset_mock()
        await controller.update()

    # Verify consecutive failures equals threshold
    assert (
        controller.get_consecutive_failures("switch.boiler")
        == coordinator.CONSECUTIVE_FAILURE_WARNING_THRESHOLD
    )


@pytest.mark.asyncio
async def test_failure_counter_reset_on_success_after_failures(mock_hass, coord):
    """Test failure counter resets after successful call following failures."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Simulate 2 prior failures
    controller._consecutive_failures["switch.boiler"] = 2

    # Make service call succeed
    mock_hass.services.async_call = AsyncMock(return_value=None)

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify failure counter was reset
    assert controller.get_consecutive_failures("switch.boiler") == 0


@pytest.mark.asyncio
async def test_unexpected_exception_triggers_retry(mock_hass, coord):
    """Test unexpected exceptions trigger retry with backoff."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Make service call fail with unexpected exception twice then succeed
    call_count = [0]

    async def mock_service_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise RuntimeError("Unexpected error")
        return None

    mock_hass.services.async_call = AsyncMock(side_effect=mock_service_call)

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify service was called 3 times
    assert mock_hass.services.async_call.call_count == 3

    # Verify failure counter was reset after success
    assert controller.get_consecutive_failures("switch.boiler") == 0


@pytest.mark.asyncio
async def test_turn_off_also_uses_retry_logic(mock_hass, coord):
    """Test turn_off also uses retry logic when it fails."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently ON (so turn_off will be called)
    mock_state = Mock()
    mock_state.state = "on"
    mock_hass.states.get.return_value = mock_state

    # Make service call fail twice then succeed
    call_count = [0]

    async def mock_service_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise MockHomeAssistantError("Transient error")
        return None

    mock_hass.services.async_call = AsyncMock(side_effect=mock_service_call)

    # Register zone with no demand (to trigger turn_off)
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", False)

    # Update controller
    await controller.update()

    # Verify turn_off was eventually called successfully
    assert mock_hass.services.async_call.call_count == 3

    # Verify failure counter was reset
    assert controller.get_consecutive_failures("switch.boiler") == 0


@pytest.mark.asyncio
async def test_get_consecutive_failures_returns_zero_for_unknown_entity(mock_hass, coord):
    """Test get_consecutive_failures returns 0 for entities not tracked."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Entity not in failure tracking should return 0
    assert controller.get_consecutive_failures("switch.unknown") == 0


@pytest.mark.asyncio
async def test_multiple_switches_independent_failure_tracking(mock_hass, coord):
    """Test heater and cooler have independent failure tracking."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        main_cooler_switch=["switch.chiller"],
        startup_delay_seconds=0,
    )

    # Simulate failures for heater only
    controller._consecutive_failures["switch.boiler"] = 3

    # Verify cooler is still at 0
    assert controller.get_consecutive_failures("switch.boiler") == 3
    assert controller.get_consecutive_failures("switch.chiller") == 0


@pytest.mark.asyncio
async def test_exponential_backoff_timing(mock_hass, coord):
    """Test exponential backoff delays are applied correctly."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler"],
        startup_delay_seconds=0,
    )

    # Mock switch is currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Track timing of calls
    call_times = []

    async def mock_service_call(*args, **kwargs):
        call_times.append(asyncio.get_event_loop().time())
        raise MockHomeAssistantError("Transient error")

    mock_hass.services.async_call = AsyncMock(side_effect=mock_service_call)

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify all retries were attempted
    assert len(call_times) == coordinator.MAX_SERVICE_CALL_RETRIES

    # Verify delays between attempts follow exponential backoff pattern
    # Expected delays: 1s, 2s (based on BASE_RETRY_DELAY_SECONDS * 2^attempt)
    if len(call_times) >= 2:
        delay1 = call_times[1] - call_times[0]
        assert delay1 >= coordinator.BASE_RETRY_DELAY_SECONDS * 0.9  # Allow 10% margin

    if len(call_times) >= 3:
        delay2 = call_times[2] - call_times[1]
        expected_delay2 = coordinator.BASE_RETRY_DELAY_SECONDS * 2
        assert delay2 >= expected_delay2 * 0.9  # Allow 10% margin


# ========================================
# Multiple Entity Tests
# ========================================


@pytest.mark.asyncio
async def test_multiple_heaters_all_turn_on(mock_hass, coord):
    """Test all heaters in a list turn on when there is demand."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler", "switch.pump", "switch.valve"],
        startup_delay_seconds=0,
    )

    # Mock all switches are currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify all three switches were turned on
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 3

    turned_on_entities = [call.args[2]["entity_id"] for call in calls]
    assert "switch.boiler" in turned_on_entities
    assert "switch.pump" in turned_on_entities
    assert "switch.valve" in turned_on_entities


@pytest.mark.asyncio
async def test_multiple_heaters_all_turn_off(mock_hass, coord):
    """Test all heaters in a list turn off when no demand."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler", "switch.pump"],
        startup_delay_seconds=0,
    )

    # Mock all switches are currently on
    mock_state = Mock()
    mock_state.state = "on"
    mock_hass.states.get.return_value = mock_state

    # Register zone with no demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", False)

    # Update controller
    await controller.update()

    # Verify all switches were turned off
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 2

    for call in calls:
        assert call.args[1] == "turn_off"


@pytest.mark.asyncio
async def test_multiple_coolers_all_turn_on(mock_hass, coord):
    """Test all coolers in a list turn on when there is cooling demand."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_cooler_switch=["switch.chiller", "switch.fan"],
        startup_delay_seconds=0,
    )

    # Mock all switches are currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Mock cooling demand (since coordinator doesn't track cooling yet)
    coord.get_aggregate_demand = Mock(return_value={"heating": False, "cooling": True})

    # Update controller
    await controller.update()

    # Verify both cooler switches were turned on
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 2

    turned_on_entities = [call.args[2]["entity_id"] for call in calls]
    assert "switch.chiller" in turned_on_entities
    assert "switch.fan" in turned_on_entities


@pytest.mark.asyncio
async def test_multiple_heaters_and_coolers(mock_hass, coord):
    """Test multiple heaters and coolers operate independently."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler", "switch.pump"],
        main_cooler_switch=["switch.chiller", "switch.fan"],
        startup_delay_seconds=0,
    )

    # Mock all switches are currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone with heating demand only
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify only heater switches were turned on (not coolers)
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 2

    turned_on_entities = [call.args[2]["entity_id"] for call in calls]
    assert "switch.boiler" in turned_on_entities
    assert "switch.pump" in turned_on_entities
    assert "switch.chiller" not in turned_on_entities
    assert "switch.fan" not in turned_on_entities


@pytest.mark.asyncio
async def test_multiple_heaters_partial_failure_continues(mock_hass, coord):
    """Test that if one heater fails, others still get controlled."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler", "switch.pump"],
        startup_delay_seconds=0,
    )

    # Mock switches are currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # First call fails, second succeeds
    call_count = [0]
    async def mock_service_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= coordinator.MAX_SERVICE_CALL_RETRIES:
            # First entity fails all retries
            raise MockHomeAssistantError("Transient error")
        # Second entity succeeds
        return None

    mock_hass.services.async_call = AsyncMock(side_effect=mock_service_call)

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Verify both entities were attempted
    # First entity: 3 retries, second entity: 1 success
    assert call_count[0] == coordinator.MAX_SERVICE_CALL_RETRIES + 1


@pytest.mark.asyncio
async def test_is_any_switch_on_with_mixed_states(mock_hass, coord):
    """Test _is_any_switch_on returns True if any switch is on."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler", "switch.pump", "switch.valve"],
        startup_delay_seconds=0,
    )

    # Set up mock states: boiler off, pump on, valve off
    def mock_get_state(entity_id):
        state = Mock()
        if entity_id == "switch.pump":
            state.state = "on"
        else:
            state.state = "off"
        return state

    mock_hass.states.get.side_effect = mock_get_state

    # Check if any switch is on
    result = await controller._is_any_switch_on(["switch.boiler", "switch.pump", "switch.valve"])

    assert result is True


@pytest.mark.asyncio
async def test_is_any_switch_on_all_off(mock_hass, coord):
    """Test _is_any_switch_on returns False if all switches are off."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler", "switch.pump"],
        startup_delay_seconds=0,
    )

    # All switches are off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    result = await controller._is_any_switch_on(["switch.boiler", "switch.pump"])

    assert result is False


@pytest.mark.asyncio
async def test_multiple_heaters_with_startup_delay(mock_hass, coord):
    """Test multiple heaters respect startup delay."""
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.boiler", "switch.pump"],
        startup_delay_seconds=1,
    )

    # Mock all switches are currently off
    mock_state = Mock()
    mock_state.state = "off"
    mock_hass.states.get.return_value = mock_state

    # Register zone and add demand
    coord.register_zone("living_room", {"name": "Living Room"})
    coord.update_zone_demand("living_room", True)

    # Update controller
    await controller.update()

    # Heaters should NOT be on immediately
    assert controller.is_heater_waiting_for_startup()
    assert mock_hass.services.async_call.call_count == 0

    # Wait for delay
    await asyncio.sleep(1.5)

    # Now both heaters should be on
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 2

    turned_on_entities = [call.args[2]["entity_id"] for call in calls]
    assert "switch.boiler" in turned_on_entities
    assert "switch.pump" in turned_on_entities
