"""Tests for ZoneLinker."""
import asyncio
import sys
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


@pytest.fixture
def hass():
    """Mock Home Assistant instance."""
    mock_hass = Mock()
    mock_hass.states = Mock()
    mock_hass.services = Mock()
    return mock_hass


@pytest.fixture
def coordinator(hass):
    """Create a coordinator instance."""
    return AdaptiveThermostatCoordinator(hass)


@pytest.fixture
def zone_linker(hass, coordinator):
    """Create a ZoneLinker instance."""
    return ZoneLinker(hass, coordinator)


@pytest.mark.asyncio
async def test_linked_zone_delay(zone_linker):
    """Test that linked zones are delayed when primary zone heats."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Living room starts heating - should delay bedroom
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=30)

    # Bedroom should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Living room itself should not be delayed
    assert zone_linker.is_zone_delayed("living_room") is False

    # Check remaining time is approximately 30 minutes
    remaining = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining is not None
    assert 29.9 < remaining <= 30.0


@pytest.mark.asyncio
async def test_delay_expiration(zone_linker):
    """Test that delay expires after configured time."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with very short delay for testing
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=0.01)  # 0.6 seconds

    # Initially delayed
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Wait for delay to expire
    await asyncio.sleep(0.7)

    # Delay should have expired
    assert zone_linker.is_zone_delayed("bedroom") is False
    assert zone_linker.get_delay_remaining_minutes("bedroom") is None


@pytest.mark.asyncio
async def test_unlinked_zones_independence(zone_linker):
    """Test that unlinked zones are not affected by heating."""
    # Configure: living_room is linked to bedroom only
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Living room starts heating
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=30)

    # Kitchen (unlinked) should not be delayed
    assert zone_linker.is_zone_delayed("kitchen") is False
    assert zone_linker.get_delay_remaining_minutes("kitchen") is None

    # Bedroom (linked) should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is True


@pytest.mark.asyncio
async def test_bidirectional_linking(zone_linker):
    """Test bidirectional linking between zones."""
    # Configure bidirectional links
    zone_linker.configure_linked_zones("living_room", ["bedroom"])
    zone_linker.configure_linked_zones("bedroom", ["living_room"])

    # Scenario 1: Living room heats first
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=30)
    assert zone_linker.is_zone_delayed("bedroom") is True
    assert zone_linker.is_zone_delayed("living_room") is False

    # Clear delays
    zone_linker.clear_delay("bedroom")

    # Scenario 2: Bedroom heats first
    await zone_linker.on_zone_heating_started("bedroom", delay_minutes=30)
    assert zone_linker.is_zone_delayed("living_room") is True
    assert zone_linker.is_zone_delayed("bedroom") is False


@pytest.mark.asyncio
async def test_multiple_linked_zones(zone_linker):
    """Test that one zone can delay multiple linked zones."""
    # Configure: living_room is linked to bedroom and study
    zone_linker.configure_linked_zones("living_room", ["bedroom", "study"])

    # Living room starts heating
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=30)

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


@pytest.mark.asyncio
async def test_clear_delay(zone_linker):
    """Test manually clearing a delay."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=30)

    # Bedroom should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is True

    # Clear the delay
    zone_linker.clear_delay("bedroom")

    # Bedroom should no longer be delayed
    assert zone_linker.is_zone_delayed("bedroom") is False
    assert zone_linker.get_delay_remaining_minutes("bedroom") is None


@pytest.mark.asyncio
async def test_get_linked_zones(zone_linker):
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


@pytest.mark.asyncio
async def test_no_linked_zones(zone_linker):
    """Test behavior when zone has no linked zones."""
    # Don't configure any links for living_room

    # Start heating
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=30)

    # No zones should be delayed
    assert zone_linker.is_zone_delayed("bedroom") is False
    assert zone_linker.is_zone_delayed("kitchen") is False


@pytest.mark.asyncio
async def test_get_active_delays(zone_linker):
    """Test getting all active delays."""
    # Configure links
    zone_linker.configure_linked_zones("living_room", ["bedroom", "study"])

    # Start heating
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=30)

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


@pytest.mark.asyncio
async def test_delay_remaining_decreases(zone_linker):
    """Test that delay remaining time decreases over time."""
    # Configure: living_room is linked to bedroom
    zone_linker.configure_linked_zones("living_room", ["bedroom"])

    # Start heating with 1 minute delay
    await zone_linker.on_zone_heating_started("living_room", delay_minutes=1.0)

    # Get initial remaining time
    remaining_1 = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining_1 is not None
    assert 0.9 < remaining_1 <= 1.0

    # Wait a bit (at least 0.1 seconds to ensure measurable difference)
    await asyncio.sleep(0.1)

    # Remaining time should have decreased
    remaining_2 = zone_linker.get_delay_remaining_minutes("bedroom")
    assert remaining_2 is not None
    assert remaining_2 < remaining_1
    # Should have decreased by roughly 0.1 minutes (allow some tolerance)
    assert remaining_2 < remaining_1 - 0.001  # At least some decrease
