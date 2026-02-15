"""Test configuration and fixtures for adaptive thermostat tests."""

import sys
from abc import ABC, ABCMeta
from enum import IntFlag
from unittest.mock import AsyncMock, MagicMock

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
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
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


# Event needs to support subscripting for type hints like Event[EventStateChangedData]
class MockEvent:
    """Mock Event class that supports generic subscripting."""

    def __class_getitem__(cls, item):
        return cls


mock_core.Event = MockEvent
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

# Mock storage — Store subclass needed for HistoryStore migration
mock_storage = MagicMock()
mock_storage.Store = type("Store", (), {"__init__": lambda self, *a, **kw: None})
mock_helpers.storage = mock_storage

# Mock typing
mock_typing = MagicMock()
mock_typing.ConfigType = MagicMock()
mock_typing.DiscoveryInfoType = MagicMock()
mock_helpers.typing = mock_typing


# RestoreEntity must use ABCMeta to be compatible with ClimateEntity
class MockRestoreEntity(metaclass=ABCMeta):
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
class MockClimateEntity(metaclass=ABCMeta):
    """Mock ClimateEntity base class."""

    pass


class MockClimateEntityFeature(IntFlag):
    """Mock ClimateEntityFeature enum."""

    TARGET_TEMPERATURE = 1
    TARGET_TEMPERATURE_RANGE = 2
    TURN_ON = 4
    TURN_OFF = 8
    PRESET_MODE = 16


# Create global singleton HVACMode values that persist even if modules are replaced
# Store them in a way that ensures they're always the same object
_HVAC_MODE_VALUES = {
    "OFF": sys.intern("off"),
    "HEAT": sys.intern("heat"),
    "COOL": sys.intern("cool"),
    "HEAT_COOL": sys.intern("heat_cool"),
    "AUTO": sys.intern("auto"),
}


class _ImmutableHVACModeMeta(type):
    """Metaclass that prevents modification of class attributes and returns global singletons."""

    def __getattribute__(cls, name):
        # Return the global singleton values instead of class attributes
        if name in _HVAC_MODE_VALUES:
            return _HVAC_MODE_VALUES[name]
        return super().__getattribute__(name)

    def __setattr__(cls, name, value):
        if name in ("OFF", "HEAT", "COOL", "HEAT_COOL", "AUTO"):
            raise AttributeError(f"Cannot modify HVACMode.{name}")
        super().__setattr__(name, value)

    def __delattr__(cls, name):
        if name in ("OFF", "HEAT", "COOL", "HEAT_COOL", "AUTO"):
            raise AttributeError(f"Cannot delete HVACMode.{name}")
        super().__delattr__(name)


class MockHVACMode(metaclass=_ImmutableHVACModeMeta):
    """Mock HVACMode enum with immutable global singleton attributes.

    This implementation ensures that HVACMode.HEAT always returns the same
    interned string object, even if sys.modules['homeassistant.components.climate']
    is replaced by other tests. The values are stored in a global dictionary.
    """

    # Set initial values (but __getattribute__ will return the global singletons)
    OFF = _HVAC_MODE_VALUES["OFF"]
    HEAT = _HVAC_MODE_VALUES["HEAT"]
    COOL = _HVAC_MODE_VALUES["COOL"]
    HEAT_COOL = _HVAC_MODE_VALUES["HEAT_COOL"]
    AUTO = _HVAC_MODE_VALUES["AUTO"]


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
sys.modules["homeassistant.helpers.storage"] = mock_storage
sys.modules["homeassistant.helpers.restore_state"] = mock_restore_state
sys.modules["homeassistant.components"] = mock_components
sys.modules["homeassistant.components.input_number"] = mock_input_number
sys.modules["homeassistant.components.light"] = mock_light
sys.modules["homeassistant.components.valve"] = mock_valve
sys.modules["homeassistant.components.climate"] = mock_climate

# ============================================================================
# Pytest Fixtures
# ============================================================================

import pytest

# Save reference to the original mock_climate for restoration
_ORIGINAL_MOCK_CLIMATE = mock_climate


def pytest_runtest_setup(item):
    """Pytest hook that runs before each test item.

    Restores the original climate mock before each test to prevent pollution
    from tests that replace sys.modules['homeassistant.components.climate'].
    """
    # Restore the original mock before each test
    sys.modules["homeassistant.components.climate"] = _ORIGINAL_MOCK_CLIMATE


@pytest.fixture
def mock_hass():
    """Create a shared mock Home Assistant instance.

    Provides the minimum HA interface needed by adaptive_climate components:
    states, services, event bus, data store, and async helpers.
    """
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = AsyncMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro)
    hass.async_call_later = MagicMock(return_value=MagicMock())  # returns cancel handle
    hass.data = {
        "adaptive_climate": {
            "coordinator": None,
            "learning_store": None,
        }
    }
    return hass


# ============================================================================
# Time Travel Fixture
# ============================================================================

from datetime import timedelta, timezone
from unittest.mock import patch


class TimeTravelController:
    """Controls dt_util.utcnow() and time.monotonic() together."""

    def __init__(self):
        self._current_dt = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        self._current_mono = 1000.0

    def now(self) -> datetime:
        return self._current_dt

    def monotonic(self) -> float:
        return self._current_mono

    def advance(self, hours: float = 0, minutes: float = 0, seconds: float = 0) -> None:
        total_seconds = hours * 3600 + minutes * 60 + seconds
        self._current_dt += timedelta(seconds=total_seconds)
        self._current_mono += total_seconds


@pytest.fixture
def time_travel():
    """Fixture for deterministic time control in tests.

    Patches both dt_util.utcnow() and time.monotonic() to advance together.

    Usage:
        def test_time_progression(time_travel):
            start = time_travel.now()
            time_travel.advance(minutes=30)
            assert time_travel.now() == start + timedelta(minutes=30)
    """
    controller = TimeTravelController()

    # Patch utcnow on the mocked dt_util module
    # conftest.py sets up sys.modules["homeassistant.util.dt"] as a MagicMock
    # We set utcnow to return our controlled time
    from homeassistant.util import dt as dt_util

    original_utcnow = dt_util.utcnow
    dt_util.utcnow = controller.now

    # Patch time.monotonic using unittest.mock.patch
    with patch("time.monotonic", side_effect=lambda: controller.monotonic()):
        yield controller

    # Restore utcnow to original mock after test
    dt_util.utcnow = original_utcnow


# ============================================================================
# Thermostat Factory Fixture
# ============================================================================

from types import SimpleNamespace
from typing import Union, Optional


@pytest.fixture
def make_thermostat(mock_hass):
    """Factory fixture that creates real component instances wired together.

    Returns a factory function that creates a namespace with:
    - learner: Real AdaptiveLearner instance
    - pid: Real PID controller instance
    - gains_manager: Real PIDGainsManager instance
    - cycle_tracker: Real CycleTrackerManager instance
    - dispatcher: Real CycleEventDispatcher instance
    - heating_rate_learner: Reference to learner._heating_rate_learner
    - target_temp: Mutable temperature setpoint (default 21.0)
    - current_temp: Mutable current temperature (default 19.0)

    Usage:
        def test_something(make_thermostat):
            t = make_thermostat()  # default radiator
            t.target_temp = 22.0
            t.learner.add_cycle_metrics(...)

            t2 = make_thermostat(heating_type=HeatingType.FLOOR_HYDRONIC)
    """
    from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
    from custom_components.adaptive_climate.adaptive.physics import calculate_initial_pid
    from custom_components.adaptive_climate.pid_controller import PID
    from custom_components.adaptive_climate.managers.pid_gains_manager import PIDGainsManager
    from custom_components.adaptive_climate.managers.cycle_tracker import CycleTrackerManager
    from custom_components.adaptive_climate.managers.events import CycleEventDispatcher
    from custom_components.adaptive_climate.const import HeatingType, PIDGains, HEATING_TYPE_CHARACTERISTICS
    from homeassistant.components.climate import HVACMode

    def _factory(heating_type: "HeatingType | str | None" = None):
        # Default to radiator if not specified
        if heating_type is None:
            heating_type = HeatingType.RADIATOR
        elif isinstance(heating_type, str):
            heating_type = HeatingType(heating_type)

        # Create namespace for mutable temps
        ns = SimpleNamespace(
            target_temp=21.0,
            current_temp=19.0,
            hvac_mode=HVACMode.HEAT,
        )

        # Create real AdaptiveLearner
        learner = AdaptiveLearner(
            heating_type=heating_type.value,
            chronic_approach_historic_scan=False,
        )

        # Calculate physics-based PID gains
        # Use reasonable defaults for thermal time constant based on heating type
        tau_map = {
            HeatingType.FLOOR_HYDRONIC: 8.0,
            HeatingType.RADIATOR: 4.0,
            HeatingType.CONVECTOR: 2.0,
            HeatingType.FORCED_AIR: 1.0,
        }
        tau = tau_map.get(heating_type, 4.0)
        kp, ki, kd = calculate_initial_pid(tau, heating_type.value)

        # Get heating type characteristics for derivative filter
        chars = HEATING_TYPE_CHARACTERISTICS[heating_type]

        # Create real PID controller
        pid = PID(
            kp=kp,
            ki=ki,
            kd=kd,
            ke=0.0,
            out_min=0.0,
            out_max=100.0,
            cold_tolerance=chars["cold_tolerance"],
            hot_tolerance=chars["hot_tolerance"],
            derivative_filter_alpha=chars["derivative_filter_alpha"],
            heating_type=heating_type.value,
        )

        # Create PIDGains for gains manager
        initial_gains = PIDGains(kp=kp, ki=ki, kd=kd, ke=0.0)

        # Create real PIDGainsManager
        gains_manager = PIDGainsManager(
            pid_controller=pid,
            initial_heating_gains=initial_gains,
            initial_cooling_gains=None,
            get_hvac_mode=lambda: ns.hvac_mode,
        )

        # Create real CycleEventDispatcher
        dispatcher = CycleEventDispatcher()

        # Create real CycleTrackerManager with minimal callbacks
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=learner,
            get_target_temp=lambda: ns.target_temp,
            get_current_temp=lambda: ns.current_temp,
            get_hvac_mode=lambda: ns.hvac_mode,
            get_in_grace_period=lambda: False,
            get_is_device_active=lambda: False,
            thermal_time_constant=tau,
            dispatcher=dispatcher,
            heating_type=heating_type.value,
        )

        # Populate namespace with components
        ns.learner = learner
        ns.pid = pid
        ns.gains_manager = gains_manager
        ns.cycle_tracker = cycle_tracker
        ns.dispatcher = dispatcher
        ns.heating_rate_learner = learner._heating_rate_learner

        return ns

    return _factory
