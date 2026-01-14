"""Tests for ZoneLinker."""
import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

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

# Now we can import the coordinator
from coordinator import AdaptiveThermostatCoordinator, ZoneLinker


def run_async(coro):
    """Helper function to run async code in tests without pytest-asyncio."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def hass():
    """Mock Home Assistant instance."""
    mock_hass = Mock()
    mock_hass.states = Mock()
    mock_hass.services = Mock()
    mock_hass.data = {}
    return mock_hass


@pytest.fixture
def coordinator(hass):
    """Create a coordinator instance."""
    return AdaptiveThermostatCoordinator(hass)


@pytest.fixture
def zone_linker(hass, coordinator):
    """Create a ZoneLinker instance."""
    return ZoneLinker(hass, coordinator)


def test_linked_zone_delay(zone_linker):
    """Test that linked zones are delayed when primary zone heats."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Living room starts heating - should delay bedroom
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))

    # Bedroom should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Living room itself should not be delayed
    assert zone_linker.is_zone_delayed("living_room") is False

    # Check remaining time is approximately 30 minutes
    remaining = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining is not None
    assert 29.9 < remaining <= 30.0


def test_delay_expiration(zone_linker):
    """Test that delay expires after configured time."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with very short delay for testing
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=0.01))  # 0.6 seconds

    # Initially delayed
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Wait for delay to expire
    time.sleep(0.7)

    # Delay should have expired
    assert zone_linker.is_zone_delayed("bedroom") is False
    assert zone_linker.get_delay_remaining_minutes("bedroom") is None


def test_unlinked_zones_independence(zone_linker):
    """Test that unlinked zones are not affected by heating."""
    # Configure: living_room is linked to bedroom only
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Living room starts heating
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))

    # Kitchen (unlinked) should not be delayed
    assert zone_linker.is_zone_delayed("kitchen") is False
    assert zone_linker.get_delay_remaining_minutes("kitchen") is None

    # Bedroom (linked) should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is True


def test_bidirectional_linking(zone_linker):
    """Test bidirectional linking between zones."""
    # Configure bidirectional links
    zone_linker.configure_linked_zones("living_room", ["bedroom"])
    zone_linker.configure_linked_zones("bedroom", ["living_room"])

    # Scenario 1: Living room heats first
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))
    assert zone_linker.is_zone_delayed("bedroom") is True
    assert zone_linker.is_zone_delayed("living_room") is False

    # Clear delays
    zone_linker.clear_delay("bedroom")

    # Scenario 2: Bedroom heats first
    run_async(zone_linker.on_zone_heating_started("bedroom", delay_minutes=30))
    assert zone_linker.is_zone_delayed("living_room") is True
    assert zone_linker.is_zone_delayed("bedroom") is False


def test_multiple_linked_zones(zone_linker):
    """Test that one zone can delay multiple linked zones."""
    # Configure: living_room is linked to bedroom and study
    zone_linker.configure_linked_zones("living_room", ["bedroom", "study"])

    # Living room starts heating
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))

    # Both linked zones should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is True
    assert zone_linker.is_zone_delayed("study") is True

    # Check remaining time for both
    bedroom_remaining = zone_linker.get_delay_remaining_minutes("bedroom")
    study_remaining = zone_linker.get_delay_remaining_minutes("study")
    assert bedroom_remaining is not None
    assert study_remaining is not None
    assert 29.9 < bedroom_remaining <= 30.0
    assert 29.9 < study_remaining <= 30.0


def test_clear_delay(zone_linker):
    """Test manually clearing a delay."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))

    # Bedroom should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Clear the delay
    zone_linker.clear_delay("bedroom")

    # Bedroom should no longer be delayed
    assert zone_linker.is_zone_delayed("bedroom") is False
    assert zone_linker.get_delay_remaining_minutes("bedroom") is None


def test_get_linked_zones(zone_linker):
    """Test getting linked zones for a zone."""
    # Configure links
    zone_linker.configure_linked_zones("living_room", ["bedroom", "study"])
    zone_linker.configure_linked_zones("kitchen", ["dining_room"])

    # Get linked zones
    living_room_links = zone_linker.get_linked_zones("living_room")
    kitchen_links = zone_linker.get_linked_zones("kitchen")
    bathroom_links = zone_linker.get_linked_zones("bathroom")  # No links configured

    assert living_room_links == ["bedroom", "study"]
    assert kitchen_links == ["dining_room"]
    assert bathroom_links == []


def test_no_linked_zones(zone_linker):
    """Test behavior when zone has no linked zones."""
    # Don't configure any links for living_room

    # Start heating
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))

    # No zones should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is False
    assert zone_linker.is_zone_delayed("kitchen") is False


def test_get_active_delays(zone_linker):
    """Test getting all active delays."""
    # Configure links
    zone_linker.configure_linked_zones("living_room", ["bedroom", "study"])

    # Start heating
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))

    # Get active delays
    active_delays = zone_linker.get_active_delays()

    # Should have two active delays
    assert len(active_delays) == 2
    assert "bedroom" in active_delays
    assert "study" in active_delays

    # Check delay info structure
    assert "start_time" in active_delays["bedroom"]
    assert "delay_minutes" in active_delays["bedroom"]
    assert "source_zone" in active_delays["bedroom"]
    assert active_delays["bedroom"]["delay_minutes"] == 30
    assert active_delays["bedroom"]["source_zone"] == "living_room"


def test_delay_remaining_decreases(zone_linker):
    """Test that delay remaining time decreases over time."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with 1 minute delay
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=1.0))

    # Get initial remaining time
    remaining_1 = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining_1 is not None
    assert 0.9 < remaining_1 <= 1.0

    # Wait a bit (at least 0.1 seconds to ensure measurable difference)
    time.sleep(0.1)

    # Remaining time should have decreased
    remaining_2 = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining_2 is not None
    assert remaining_2 < remaining_1
    # Should have decreased by roughly 0.1 minutes (allow some tolerance)
    assert remaining_2 < remaining_1 - 0.001  # At least some decrease


# =============================================================================
# Integration Tests: Climate Entity ZoneLinker Integration
# =============================================================================


class MockThermostat:
    """Mock climate entity simulating AdaptiveThermostat behavior for ZoneLinker integration."""

    def __init__(self, zone_id: str, zone_linker: ZoneLinker, linked_zones: list = None):
        self._unique_id = zone_id
        self._zone_linker = zone_linker
        self._linked_zones = linked_zones or []
        self._link_delay_minutes = 20
        self._is_heating = False
        self._is_device_active = False
        self._last_heat_cycle_time = 0
        self._heater_turn_on_blocked = False
        self._heater_turn_on_called = False
        self._block_reason = None

    async def _async_heater_turn_on(self):
        """Simulates AdaptiveThermostat._async_heater_turn_on() with ZoneLinker integration."""
        # Check if zone is delayed due to linked zone heating
        if self._zone_linker and self._zone_linker.is_zone_delayed(self._unique_id):
            remaining = self._zone_linker.get_delay_remaining_minutes(self._unique_id)
            self._heater_turn_on_blocked = True
            self._block_reason = f"Zone linking delay active - heating delayed for {remaining:.1f} more minutes"
            return

        self._heater_turn_on_blocked = False
        self._block_reason = None

        if self._is_device_active:
            # Refresh state - heater already on
            pass
        else:
            # Actually turn on the heater
            self._is_device_active = True
            self._heater_turn_on_called = True

            # Notify zone linker that this zone started heating (for linked zones)
            if self._zone_linker and self._linked_zones and not self._is_heating:
                self._is_heating = True
                await self._zone_linker.on_zone_heating_started(
                    self._unique_id, self._link_delay_minutes
                )

    async def _async_heater_turn_off(self):
        """Simulates AdaptiveThermostat._async_heater_turn_off()."""
        self._is_device_active = False
        self._is_heating = False
        self._heater_turn_on_called = False

    def get_extra_state_attributes(self) -> dict:
        """Simulates extra_state_attributes property for zone linking status."""
        attrs = {}
        if self._zone_linker:
            is_delayed = self._zone_linker.is_zone_delayed(self._unique_id)
            attrs["zone_link_delayed"] = is_delayed
            if is_delayed:
                remaining = self._zone_linker.get_delay_remaining_minutes(self._unique_id)
                attrs["zone_link_delay_remaining"] = round(remaining, 1) if remaining else 0
            if self._linked_zones:
                attrs["linked_zones"] = self._linked_zones
        return attrs


def test_integration_heating_start_triggers_linked_zone_notification(zone_linker):
    """
    Integration test: When a zone starts heating, it notifies the ZoneLinker
    which then applies delay to linked zones.

    This tests the climate entity's integration with ZoneLinker when heating starts.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Create mock thermostats
    living_room = MockThermostat("living_room", zone_linker, linked_zones=["bedroom"])
    bedroom = MockThermostat("bedroom", zone_linker)

    # Living room starts heating
    run_async(living_room._async_heater_turn_on())

    # Verify living room heating actually started
    assert living_room._is_device_active is True
    assert living_room._is_heating is True
    assert living_room._heater_turn_on_called is True

    # Verify bedroom is now delayed
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Verify bedroom's extra_state_attributes reflects the delay
    bedroom_attrs = bedroom.get_extra_state_attributes()
    assert bedroom_attrs["zone_link_delayed"] is True
    assert "zone_link_delay_remaining" in bedroom_attrs
    assert bedroom_attrs["zone_link_delay_remaining"] > 0


def test_integration_delayed_zone_skips_heating_activation(zone_linker):
    """
    Integration test: When a zone is delayed due to linked zone heating,
    its heater turn on request is blocked.

    This tests the climate entity's check for is_zone_delayed() before heating.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Create mock thermostats
    living_room = MockThermostat("living_room", zone_linker, linked_zones=["bedroom"])
    bedroom = MockThermostat("bedroom", zone_linker)

    # Living room starts heating first
    run_async(living_room._async_heater_turn_on())
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Bedroom tries to start heating while delayed
    run_async(bedroom._async_heater_turn_on())

    # Verify bedroom heating was blocked
    assert bedroom._heater_turn_on_blocked is True
    assert bedroom._is_device_active is False
    assert bedroom._heater_turn_on_called is False
    assert "Zone linking delay active" in bedroom._block_reason

    # Verify living room is still heating
    assert living_room._is_device_active is True


def test_integration_delay_expiration_allows_heating_to_resume(zone_linker):
    """
    Integration test: After the zone linking delay expires, the previously
    delayed zone can start heating normally.

    This tests the full cycle: delay -> expiration -> heating allowed.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Create mock thermostats with short delay for testing
    living_room = MockThermostat("living_room", zone_linker, linked_zones=["bedroom"])
    living_room._link_delay_minutes = 0.01  # Very short delay (0.6 seconds)
    bedroom = MockThermostat("bedroom", zone_linker)

    # Living room starts heating - delays bedroom
    run_async(living_room._async_heater_turn_on())
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Bedroom tries to heat - should be blocked
    run_async(bedroom._async_heater_turn_on())
    assert bedroom._heater_turn_on_blocked is True
    assert bedroom._is_device_active is False

    # Wait for delay to expire
    time.sleep(0.7)

    # Verify delay has expired
    assert zone_linker.is_zone_delayed("bedroom") is False

    # Bedroom tries to heat again - should succeed now
    run_async(bedroom._async_heater_turn_on())
    assert bedroom._heater_turn_on_blocked is False
    assert bedroom._is_device_active is True
    assert bedroom._heater_turn_on_called is True


def test_integration_extra_state_attributes_show_delay_status(zone_linker):
    """
    Integration test: The zone_link_delayed and zone_link_delay_remaining
    attributes are correctly exposed in extra_state_attributes.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Create mock thermostats
    living_room = MockThermostat("living_room", zone_linker, linked_zones=["bedroom"])
    bedroom = MockThermostat("bedroom", zone_linker)

    # Before any heating - no delay
    bedroom_attrs_before = bedroom.get_extra_state_attributes()
    assert bedroom_attrs_before["zone_link_delayed"] is False
    assert "zone_link_delay_remaining" not in bedroom_attrs_before

    # Living room starts heating
    run_async(living_room._async_heater_turn_on())

    # After heating starts - bedroom should show delay
    bedroom_attrs_after = bedroom.get_extra_state_attributes()
    assert bedroom_attrs_after["zone_link_delayed"] is True
    assert "zone_link_delay_remaining" in bedroom_attrs_after
    assert bedroom_attrs_after["zone_link_delay_remaining"] > 0

    # Living room attributes should show linked zones
    living_attrs = living_room.get_extra_state_attributes()
    assert living_attrs["zone_link_delayed"] is False
    assert living_attrs["linked_zones"] == ["bedroom"]


def test_integration_multiple_heating_starts_dont_re_notify(zone_linker):
    """
    Integration test: Multiple turn_on calls while already heating don't
    repeatedly notify the ZoneLinker (no duplicate delays).

    This tests the _is_heating flag logic in the climate entity.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Create mock thermostat
    living_room = MockThermostat("living_room", zone_linker, linked_zones=["bedroom"])

    # First heating start
    run_async(living_room._async_heater_turn_on())
    assert living_room._is_heating is True

    # Get initial delay info
    initial_delays = zone_linker.get_active_delays()
    assert "bedroom" in initial_delays
    initial_start_time = initial_delays["bedroom"]["start_time"]

    # Wait a small amount
    time.sleep(0.05)

    # Second turn_on call (like keep_alive refresh) - should not re-notify
    # because _is_heating is already True
    run_async(living_room._async_heater_turn_on())

    # Delay start time should remain unchanged
    current_delays = zone_linker.get_active_delays()
    assert current_delays["bedroom"]["start_time"] == initial_start_time


def test_integration_heater_off_resets_heating_state(zone_linker):
    """
    Integration test: Turning off the heater resets the _is_heating state,
    allowing future heating cycles to properly notify linked zones.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Create mock thermostat
    living_room = MockThermostat("living_room", zone_linker, linked_zones=["bedroom"])

    # Start and stop heating
    run_async(living_room._async_heater_turn_on())
    assert living_room._is_heating is True
    run_async(living_room._async_heater_turn_off())
    assert living_room._is_heating is False

    # Clear delays to simulate time passing
    zone_linker.clear_delay("bedroom")

    # Start heating again - should re-notify and apply new delay
    run_async(living_room._async_heater_turn_on())
    assert living_room._is_heating is True
    assert zone_linker.is_zone_delayed("bedroom") is True


# =============================================================================
# Story 7.3: Idempotent Query Methods Tests
# =============================================================================


def test_is_zone_delayed_is_idempotent(zone_linker):
    """
    Test that is_zone_delayed() is idempotent - it does not modify state.

    Calling the method multiple times should return consistent results
    without removing expired entries from _active_delays.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with very short delay
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=0.01))  # 0.6 seconds

    # Wait for delay to expire
    time.sleep(0.7)

    # First query - should return False (expired) but NOT delete the entry
    result1 = zone_linker.is_zone_delayed("bedroom")
    assert result1 is False

    # The entry should still exist in _active_delays (not cleaned up by query)
    assert "bedroom" in zone_linker._active_delays

    # Second query - should also return False and entry should still exist
    result2 = zone_linker.is_zone_delayed("bedroom")
    assert result2 is False
    assert "bedroom" in zone_linker._active_delays

    # Third query - still idempotent
    result3 = zone_linker.is_zone_delayed("bedroom")
    assert result3 is False
    assert "bedroom" in zone_linker._active_delays


def test_get_delay_remaining_minutes_is_idempotent(zone_linker):
    """
    Test that get_delay_remaining_minutes() is idempotent - it does not modify state.

    Calling the method multiple times should return consistent results
    without removing expired entries from _active_delays.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with very short delay
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=0.01))  # 0.6 seconds

    # Wait for delay to expire
    time.sleep(0.7)

    # First query - should return None (expired) but NOT delete the entry
    remaining1 = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining1 is None

    # The entry should still exist in _active_delays (not cleaned up by query)
    assert "bedroom" in zone_linker._active_delays

    # Second query - should also return None and entry should still exist
    remaining2 = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining2 is None
    assert "bedroom" in zone_linker._active_delays


def test_cleanup_expired_delays_removes_expired_entries(zone_linker):
    """
    Test that cleanup_expired_delays() removes expired delay entries.
    """
    # Configure: living_room is linked to bedroom and study
    zone_linker.configure_linked_zones("living_room", ["bedroom", "study"])

    # Start heating with very short delay
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=0.01))  # 0.6 seconds

    # Verify both delays are active
    assert "bedroom" in zone_linker._active_delays
    assert "study" in zone_linker._active_delays

    # Wait for delays to expire
    time.sleep(0.7)

    # Both delays should still be in _active_delays (not cleaned up by queries)
    assert "bedroom" in zone_linker._active_delays
    assert "study" in zone_linker._active_delays

    # Now call cleanup - should remove both expired entries
    cleaned_count = zone_linker.cleanup_expired_delays()
    assert cleaned_count == 2

    # Entries should now be removed
    assert "bedroom" not in zone_linker._active_delays
    assert "study" not in zone_linker._active_delays


def test_cleanup_expired_delays_preserves_active_entries(zone_linker):
    """
    Test that cleanup_expired_delays() preserves active (non-expired) delay entries.
    """
    # Configure two sets of linked zones
    zone_linker.configure_linked_zones("living_room", ["bedroom"])
    zone_linker.configure_linked_zones("kitchen", ["dining_room"])

    # Start heating with different delays
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=0.01))  # Will expire
    run_async(zone_linker.on_zone_heating_started("kitchen", delay_minutes=30))  # Won't expire

    # Wait for bedroom delay to expire (but not dining_room)
    time.sleep(0.7)

    # Verify both are still in _active_delays
    assert "bedroom" in zone_linker._active_delays
    assert "dining_room" in zone_linker._active_delays

    # Call cleanup
    cleaned_count = zone_linker.cleanup_expired_delays()
    assert cleaned_count == 1

    # Bedroom should be removed (expired), dining_room preserved (still active)
    assert "bedroom" not in zone_linker._active_delays
    assert "dining_room" in zone_linker._active_delays

    # Verify dining_room is still delayed
    assert zone_linker.is_zone_delayed("dining_room") is True


def test_cleanup_expired_delays_returns_zero_when_nothing_expired(zone_linker):
    """
    Test that cleanup_expired_delays() returns 0 when no delays have expired.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with long delay
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=30))

    # Call cleanup immediately - nothing should be expired
    cleaned_count = zone_linker.cleanup_expired_delays()
    assert cleaned_count == 0

    # Entry should still exist and be active
    assert "bedroom" in zone_linker._active_delays
    assert zone_linker.is_zone_delayed("bedroom") is True


def test_cleanup_expired_delays_handles_empty_delays(zone_linker):
    """
    Test that cleanup_expired_delays() handles empty _active_delays gracefully.
    """
    # No delays configured - _active_delays is empty
    cleaned_count = zone_linker.cleanup_expired_delays()
    assert cleaned_count == 0


def test_query_methods_combined_with_cleanup(zone_linker):
    """
    Integration test: Query methods are idempotent, cleanup removes expired entries.

    This demonstrates the complete workflow: queries don't modify state,
    but cleanup (called from coordinator update cycle) does.
    """
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with very short delay
    run_async(zone_linker.on_zone_heating_started("living_room", delay_minutes=0.01))

    # Initially delayed
    assert zone_linker.is_zone_delayed("bedroom") is True
    remaining = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining is not None and remaining > 0

    # Wait for delay to expire
    time.sleep(0.7)

    # Query methods return "not delayed" but entry still exists
    assert zone_linker.is_zone_delayed("bedroom") is False
    assert zone_linker.get_delay_remaining_minutes("bedroom") is None
    assert "bedroom" in zone_linker._active_delays

    # Call multiple queries - all idempotent
    for _ in range(5):
        assert zone_linker.is_zone_delayed("bedroom") is False
        assert zone_linker.get_delay_remaining_minutes("bedroom") is None
        assert "bedroom" in zone_linker._active_delays

    # Now cleanup (would be called from coordinator._async_update_data)
    cleaned_count = zone_linker.cleanup_expired_delays()
    assert cleaned_count == 1
    assert "bedroom" not in zone_linker._active_delays

    # Subsequent cleanups don't find anything
    assert zone_linker.cleanup_expired_delays() == 0
