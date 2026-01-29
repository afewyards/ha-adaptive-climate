"""Test configuration and fixtures for adaptive thermostat tests."""

import sys
from abc import ABC
from enum import IntFlag
from unittest.mock import MagicMock

# ============================================================================
# Mock Home Assistant modules before any test imports
#
# This must be done at module load time (not in fixtures) because Python
# caches imports in sys.modules and many tests import at module level.
# ============================================================================

# Create mock HomeAssistant base module
mock_ha = MagicMock()

# ---- Mock homeassistant.util ----
mock_util = MagicMock()
mock_util.slugify = lambda x: x.lower().replace(" ", "_")

# Mock homeassistant.util.dt for timestamp operations
from datetime import datetime
mock_dt = MagicMock()
mock_dt.utcnow = lambda: datetime.utcnow()
# Add parse_datetime for manifold persistence
def _parse_datetime(s):
    """Parse ISO 8601 datetime string."""
    try:
        # Try parsing with timezone
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None
mock_dt.parse_datetime = _parse_datetime
mock_util.dt = mock_dt

# ---- Mock homeassistant.const ----
mock_const = MagicMock()
mock_const.ATTR_TEMPERATURE = "temperature"
mock_const.CONF_NAME = "name"
mock_const.CONF_UNIQUE_ID = "unique_id"
mock_const.EVENT_HOMEASSISTANT_START = "homeassistant_start"
mock_const.PRECISION_HALVES = 0.5
mock_const.PRECISION_TENTHS = 0.1
mock_const.PRECISION_WHOLE = 1.0
mock_const.SERVICE_TURN_ON = "turn_on"
mock_const.STATE_ON = "on"
mock_const.STATE_OFF = "off"
mock_const.STATE_UNAVAILABLE = "unavailable"
mock_const.STATE_UNKNOWN = "unknown"

# ---- Mock homeassistant.core ----
mock_core = MagicMock()
mock_core.DOMAIN = "homeassistant"
mock_core.CoreState = MagicMock()
mock_core.Event = MagicMock()
mock_core.EventStateChangedData = MagicMock()
mock_core.callback = lambda f: f
mock_core.HomeAssistant = MagicMock

# ---- Mock homeassistant.exceptions ----
mock_exceptions = MagicMock()
mock_exceptions.HomeAssistantError = Exception
mock_exceptions.ServiceNotFound = Exception

# ---- Mock homeassistant.helpers modules ----
mock_helpers = MagicMock()
mock_helpers.event = MagicMock()
mock_helpers.event.async_call_later = MagicMock(return_value=MagicMock())
mock_helpers.event.async_track_state_change_event = MagicMock(return_value=MagicMock())
mock_helpers.event.async_track_time_interval = MagicMock(return_value=MagicMock())
mock_helpers.config_validation = MagicMock()
mock_helpers.entity_platform = MagicMock()
mock_helpers.discovery = MagicMock()

# Mock typing
mock_typing = MagicMock()
mock_typing.ConfigType = MagicMock()
mock_typing.DiscoveryInfoType = MagicMock()
mock_helpers.typing = mock_typing

# RestoreEntity must inherit from ABC to be compatible with ABCMeta
class MockRestoreEntity(ABC):
    """Mock RestoreEntity base class."""
    pass

mock_restore_state = MagicMock()
mock_restore_state.RestoreEntity = MockRestoreEntity
mock_helpers.restore_state = mock_restore_state

# ---- Mock homeassistant.components ----
mock_components = MagicMock()

# Input number
mock_input_number = MagicMock()
mock_input_number.DOMAIN = "input_number"
mock_components.input_number = mock_input_number

# Light
mock_light = MagicMock()
mock_light.SERVICE_TURN_ON = "turn_on"
mock_light.ATTR_BRIGHTNESS_PCT = "brightness_pct"
mock_components.light = mock_light

# Valve
mock_valve = MagicMock()
mock_valve.SERVICE_SET_VALVE_POSITION = "set_valve_position"
mock_valve.ATTR_POSITION = "position"
mock_components.valve = mock_valve

# Climate - needs proper class definitions to avoid metaclass conflicts
class MockClimateEntity(ABC):
    """Mock ClimateEntity base class."""
    pass

class MockClimateEntityFeature(IntFlag):
    """Mock ClimateEntityFeature enum."""
    TARGET_TEMPERATURE = 1
    TARGET_TEMPERATURE_RANGE = 2
    TURN_ON = 4
    TURN_OFF = 8
    PRESET_MODE = 16

class MockHVACMode:
    """Mock HVACMode enum."""
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    HEAT_COOL = "heat_cool"
    AUTO = "auto"

class MockHVACAction:
    """Mock HVACAction enum."""
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"
    OFF = "off"

mock_climate = MagicMock()
mock_climate.ClimateEntity = MockClimateEntity
mock_climate.ClimateEntityFeature = MockClimateEntityFeature
mock_climate.HVACMode = MockHVACMode
mock_climate.HVACAction = MockHVACAction
mock_climate.PLATFORM_SCHEMA = MagicMock()
mock_climate.ATTR_PRESET_MODE = "preset_mode"
mock_climate.PRESET_AWAY = "away"
mock_climate.PRESET_NONE = "none"
mock_climate.PRESET_ECO = "eco"
mock_climate.PRESET_BOOST = "boost"
mock_climate.PRESET_COMFORT = "comfort"
mock_climate.PRESET_HOME = "home"
mock_climate.PRESET_SLEEP = "sleep"
mock_components.climate = mock_climate

# ============================================================================
# Register all mock modules in sys.modules
#
# IMPORTANT: All submodules must be registered BEFORE any imports happen,
# otherwise Python will fail to resolve "homeassistant.X" imports.
# ============================================================================

sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.util"] = mock_util
sys.modules["homeassistant.util.dt"] = mock_dt
sys.modules["homeassistant.const"] = mock_const
sys.modules["homeassistant.core"] = mock_core
sys.modules["homeassistant.exceptions"] = mock_exceptions
sys.modules["homeassistant.helpers"] = mock_helpers
sys.modules["homeassistant.helpers.event"] = mock_helpers.event
sys.modules["homeassistant.helpers.config_validation"] = mock_helpers.config_validation
sys.modules["homeassistant.helpers.entity_platform"] = mock_helpers.entity_platform
sys.modules["homeassistant.helpers.discovery"] = mock_helpers.discovery
sys.modules["homeassistant.helpers.typing"] = mock_typing
sys.modules["homeassistant.helpers.restore_state"] = mock_restore_state
sys.modules["homeassistant.components"] = mock_components
sys.modules["homeassistant.components.input_number"] = mock_input_number
sys.modules["homeassistant.components.light"] = mock_light
sys.modules["homeassistant.components.valve"] = mock_valve
sys.modules["homeassistant.components.climate"] = mock_climate
