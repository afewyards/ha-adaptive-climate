"""Tests for VacationMode class."""
import asyncio
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat"))

# Mock homeassistant modules before importing
sys.modules['homeassistant'] = Mock()
sys.modules['homeassistant.core'] = Mock()
sys.modules['homeassistant.const'] = Mock()
sys.modules['homeassistant.const'].SERVICE_SET_TEMPERATURE = "set_temperature"
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

# Import coordinator module for creating coordinator
import coordinator

# Import vacation module
from adaptive.vacation import VacationMode, DEFAULT_VACATION_TEMP


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


@pytest.fixture
def vacation_mode(mock_hass, coord):
    """Create a VacationMode instance."""
    return VacationMode(mock_hass, coord)


class TestVacationModeEnable:
    """Tests for enabling vacation mode."""

    @pytest.mark.asyncio
    async def test_enable_sets_all_zones_to_frost_protection(self, mock_hass, coord, vacation_mode):
        """Test that enabling vacation mode sets all zones to frost protection temp."""
        # Register zones with learning enabled
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })
        coord.register_zone("bedroom", {
            "climate_entity_id": "climate.bedroom",
            "learning_enabled": True,
        })
        coord.register_zone("kitchen", {
            "climate_entity_id": "climate.kitchen",
            "learning_enabled": True,
        })

        # Mock climate entity states with current temperatures
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode
        await vacation_mode.async_enable(target_temp=12.0)

        # Should have called set_temperature for all 3 zones
        assert mock_hass.services.async_call.call_count == 3

        # All calls should set temperature to 12.0
        for call_obj in mock_hass.services.async_call.call_args_list:
            assert call_obj[0][0] == "climate"
            assert call_obj[0][1] == "set_temperature"
            assert call_obj[0][2]["temperature"] == 12.0

        # Vacation mode should be enabled
        assert vacation_mode.enabled is True
        assert vacation_mode.target_temp == 12.0

    @pytest.mark.asyncio
    async def test_enable_pauses_learning(self, mock_hass, coord, vacation_mode):
        """Test that enabling vacation mode pauses learning for all zones."""
        # Register zones with learning enabled
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })
        coord.register_zone("bedroom", {
            "climate_entity_id": "climate.bedroom",
            "learning_enabled": True,
        })

        # Mock climate entity states
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode
        await vacation_mode.async_enable(target_temp=12.0)

        # Learning should be paused for all zones
        zones = coord.get_all_zones()
        assert zones["living_room"]["learning_enabled"] is False
        assert zones["bedroom"]["learning_enabled"] is False

    @pytest.mark.asyncio
    async def test_enable_stores_original_setpoints(self, mock_hass, coord, vacation_mode):
        """Test that enabling vacation mode stores original setpoints."""
        # Register zones
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })
        coord.register_zone("bedroom", {
            "climate_entity_id": "climate.bedroom",
            "learning_enabled": True,
        })

        # Mock different temperatures for each zone
        def mock_get_state(entity_id):
            state = Mock()
            if entity_id == "climate.living_room":
                state.attributes = {"temperature": 21.0}
            else:
                state.attributes = {"temperature": 19.5}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode
        await vacation_mode.async_enable(target_temp=12.0)

        # Check that original setpoints were stored
        status = vacation_mode.get_status()
        assert status["original_setpoints"]["living_room"] == 21.0
        assert status["original_setpoints"]["bedroom"] == 19.5

    @pytest.mark.asyncio
    async def test_enable_default_temp(self, mock_hass, coord, vacation_mode):
        """Test that enabling vacation mode uses default temp (12) if not specified."""
        # Register a zone
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })

        # Mock climate entity state
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode with default temp
        await vacation_mode.async_enable()

        # Should use default temp of 12.0
        assert vacation_mode.target_temp == DEFAULT_VACATION_TEMP
        assert vacation_mode.target_temp == 12.0


class TestVacationModeDisable:
    """Tests for disabling vacation mode."""

    @pytest.mark.asyncio
    async def test_disable_restores_original_setpoints(self, mock_hass, coord, vacation_mode):
        """Test that disabling vacation mode restores original setpoints."""
        # Register zones
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })
        coord.register_zone("bedroom", {
            "climate_entity_id": "climate.bedroom",
            "learning_enabled": True,
        })

        # Mock different temperatures for each zone
        def mock_get_state(entity_id):
            state = Mock()
            if entity_id == "climate.living_room":
                state.attributes = {"temperature": 21.0}
            else:
                state.attributes = {"temperature": 19.5}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode
        await vacation_mode.async_enable(target_temp=12.0)
        mock_hass.services.async_call.reset_mock()

        # Disable vacation mode
        await vacation_mode.async_disable()

        # Should have called set_temperature for both zones to restore temps
        assert mock_hass.services.async_call.call_count == 2

        # Verify restored temperatures
        calls = mock_hass.services.async_call.call_args_list
        restored_temps = {
            call[0][2]["entity_id"]: call[0][2]["temperature"]
            for call in calls
        }
        assert restored_temps["climate.living_room"] == 21.0
        assert restored_temps["climate.bedroom"] == 19.5

        # Vacation mode should be disabled
        assert vacation_mode.enabled is False

    @pytest.mark.asyncio
    async def test_disable_resumes_learning(self, mock_hass, coord, vacation_mode):
        """Test that disabling vacation mode resumes learning."""
        # Register zones with learning enabled
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })
        coord.register_zone("bedroom", {
            "climate_entity_id": "climate.bedroom",
            "learning_enabled": True,
        })

        # Mock climate entity states
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode (pauses learning)
        await vacation_mode.async_enable(target_temp=12.0)
        zones = coord.get_all_zones()
        assert zones["living_room"]["learning_enabled"] is False
        assert zones["bedroom"]["learning_enabled"] is False

        # Disable vacation mode (resumes learning)
        await vacation_mode.async_disable()

        # Learning should be restored
        zones = coord.get_all_zones()
        assert zones["living_room"]["learning_enabled"] is True
        assert zones["bedroom"]["learning_enabled"] is True

    @pytest.mark.asyncio
    async def test_disable_respects_original_learning_state(self, mock_hass, coord, vacation_mode):
        """Test that disable restores original learning state (even if was disabled)."""
        # Register zones - one with learning disabled
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })
        coord.register_zone("bedroom", {
            "climate_entity_id": "climate.bedroom",
            "learning_enabled": False,  # Already disabled before vacation
        })

        # Mock climate entity states
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable and then disable vacation mode
        await vacation_mode.async_enable(target_temp=12.0)
        await vacation_mode.async_disable()

        # Learning state should be restored to original
        zones = coord.get_all_zones()
        assert zones["living_room"]["learning_enabled"] is True  # Was enabled
        assert zones["bedroom"]["learning_enabled"] is False  # Was disabled


class TestVacationModeEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_enable_when_already_enabled(self, mock_hass, coord, vacation_mode):
        """Test that enabling when already enabled logs warning and returns."""
        # Register a zone
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })

        # Mock climate entity state
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode twice
        await vacation_mode.async_enable(target_temp=12.0)
        call_count_after_first = mock_hass.services.async_call.call_count

        await vacation_mode.async_enable(target_temp=10.0)

        # Second enable should not make any additional service calls
        assert mock_hass.services.async_call.call_count == call_count_after_first

    @pytest.mark.asyncio
    async def test_disable_when_not_enabled(self, mock_hass, coord, vacation_mode):
        """Test that disabling when not enabled logs warning and returns."""
        # Register a zone
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })

        # Disable without enabling first
        await vacation_mode.async_disable()

        # Should not make any service calls
        assert mock_hass.services.async_call.call_count == 0

    @pytest.mark.asyncio
    async def test_zone_without_climate_entity(self, mock_hass, coord, vacation_mode):
        """Test that zones without climate_entity_id are skipped."""
        # Register zone without climate_entity_id
        coord.register_zone("sensor_only", {
            "learning_enabled": True,
        })
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })

        # Mock climate entity state
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode
        await vacation_mode.async_enable(target_temp=12.0)

        # Should only call service for living_room
        assert mock_hass.services.async_call.call_count == 1

        call_obj = mock_hass.services.async_call.call_args
        assert call_obj[0][2]["entity_id"] == "climate.living_room"

    @pytest.mark.asyncio
    async def test_zone_with_no_temperature_attribute(self, mock_hass, coord, vacation_mode):
        """Test that zones without current temperature are handled gracefully."""
        # Register zones
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })

        # Mock state without temperature attribute
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {}  # No temperature attribute
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode
        await vacation_mode.async_enable(target_temp=12.0)

        # Should still set temperature (even without storing original)
        assert mock_hass.services.async_call.call_count == 1

        # Original setpoint should not be stored
        status = vacation_mode.get_status()
        assert "living_room" not in status["original_setpoints"]


class TestVacationModeStatus:
    """Tests for get_status method."""

    @pytest.mark.asyncio
    async def test_status_when_disabled(self, mock_hass, coord, vacation_mode):
        """Test status when vacation mode is disabled."""
        status = vacation_mode.get_status()

        assert status["enabled"] is False
        assert status["target_temp"] == DEFAULT_VACATION_TEMP
        assert status["zones_affected"] == 0
        assert status["original_setpoints"] == {}

    @pytest.mark.asyncio
    async def test_status_when_enabled(self, mock_hass, coord, vacation_mode):
        """Test status when vacation mode is enabled."""
        # Register zones
        coord.register_zone("living_room", {
            "climate_entity_id": "climate.living_room",
            "learning_enabled": True,
        })
        coord.register_zone("bedroom", {
            "climate_entity_id": "climate.bedroom",
            "learning_enabled": True,
        })

        # Mock climate entity states
        def mock_get_state(entity_id):
            state = Mock()
            state.attributes = {"temperature": 21.0}
            return state

        mock_hass.states.get = mock_get_state

        # Enable vacation mode with custom temp
        await vacation_mode.async_enable(target_temp=10.0)

        status = vacation_mode.get_status()

        assert status["enabled"] is True
        assert status["target_temp"] == 10.0
        assert status["zones_affected"] == 2
        assert len(status["original_setpoints"]) == 2
