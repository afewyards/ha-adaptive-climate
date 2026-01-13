"""Tests for config schema validation in adaptive_thermostat __init__.py."""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys

# Store original modules
_original_modules = {}

# Mock Home Assistant modules before importing
def setup_module(module):
    """Set up mocks for Home Assistant modules."""
    modules_to_mock = [
        'homeassistant',
        'homeassistant.core',
        'homeassistant.helpers',
        'homeassistant.helpers.config_validation',
        'homeassistant.helpers.typing',
        'homeassistant.helpers.event',
    ]
    for mod in modules_to_mock:
        if mod in sys.modules:
            _original_modules[mod] = sys.modules[mod]
        sys.modules[mod] = MagicMock()


def teardown_module(module):
    """Restore original modules."""
    for mod, original in _original_modules.items():
        sys.modules[mod] = original


# Try to import voluptuous, skip tests if not available
try:
    import voluptuous as vol
    HAS_VOLUPTUOUS = True
except ImportError:
    HAS_VOLUPTUOUS = False
    vol = None


pytestmark = pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")


# Import constants from the module
from custom_components.adaptive_thermostat.const import (
    DOMAIN,
    CONF_NOTIFY_SERVICE,
    CONF_PERSISTENT_NOTIFICATION,
    CONF_ENERGY_METER_ENTITY,
    CONF_ENERGY_COST_ENTITY,
    CONF_MAIN_HEATER_SWITCH,
    CONF_MAIN_COOLER_SWITCH,
    CONF_SOURCE_STARTUP_DELAY,
    CONF_SYNC_MODES,
    CONF_LEARNING_WINDOW_DAYS,
    CONF_WEATHER_ENTITY,
    CONF_HOUSE_ENERGY_RATING,
    CONF_WINDOW_RATING,
    CONF_SUPPLY_TEMP_SENSOR,
    CONF_RETURN_TEMP_SENSOR,
    CONF_FLOW_RATE_SENSOR,
    CONF_VOLUME_METER_ENTITY,
    CONF_FALLBACK_FLOW_RATE,
    DEFAULT_SOURCE_STARTUP_DELAY,
    DEFAULT_SYNC_MODES,
    DEFAULT_LEARNING_WINDOW_DAYS,
    DEFAULT_FALLBACK_FLOW_RATE,
    DEFAULT_WINDOW_RATING,
    DEFAULT_PERSISTENT_NOTIFICATION,
    VALID_ENERGY_RATINGS,
)


# =============================================================================
# Import valid_notify_service after mocking HA
# =============================================================================

from custom_components.adaptive_thermostat import valid_notify_service


# =============================================================================
# Helper to create a schema for testing (mirrors the real CONFIG_SCHEMA)
# =============================================================================

def create_test_schema():
    """Create a test schema matching the real CONFIG_SCHEMA."""
    # Mock cv functions for testing
    def mock_entity_id(value):
        """Mock entity_id validator."""
        if not isinstance(value, str):
            raise vol.Invalid("entity_id must be a string")
        if "." not in value:
            raise vol.Invalid("entity_id must contain a domain")
        return value

    def mock_boolean(value):
        """Mock boolean validator."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("true", "yes", "on", "1"):
                return True
            if value.lower() in ("false", "no", "off", "0"):
                return False
        raise vol.Invalid("Expected boolean")

    def mock_string(value):
        """Mock string validator."""
        if not isinstance(value, str):
            raise vol.Invalid("Expected string")
        return value

    return vol.Schema(
        {
            DOMAIN: vol.Schema({
                # Notification settings
                vol.Optional(CONF_NOTIFY_SERVICE): valid_notify_service,
                vol.Optional(
                    CONF_PERSISTENT_NOTIFICATION,
                    default=DEFAULT_PERSISTENT_NOTIFICATION
                ): mock_boolean,

                # Energy tracking
                vol.Optional(CONF_ENERGY_METER_ENTITY): mock_entity_id,
                vol.Optional(CONF_ENERGY_COST_ENTITY): mock_entity_id,

                # Central heat source control
                vol.Optional(CONF_MAIN_HEATER_SWITCH): mock_entity_id,
                vol.Optional(CONF_MAIN_COOLER_SWITCH): mock_entity_id,
                vol.Optional(
                    CONF_SOURCE_STARTUP_DELAY,
                    default=DEFAULT_SOURCE_STARTUP_DELAY
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=0,
                        max=300,
                        msg="source_startup_delay must be between 0 and 300 seconds"
                    )
                ),

                # Mode synchronization
                vol.Optional(
                    CONF_SYNC_MODES,
                    default=DEFAULT_SYNC_MODES
                ): mock_boolean,

                # Learning configuration
                vol.Optional(
                    CONF_LEARNING_WINDOW_DAYS,
                    default=DEFAULT_LEARNING_WINDOW_DAYS
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=1,
                        max=30,
                        msg="learning_window_days must be between 1 and 30 days"
                    )
                ),

                # Weather and physics
                vol.Optional(CONF_WEATHER_ENTITY): mock_entity_id,
                vol.Optional(CONF_HOUSE_ENERGY_RATING): vol.In(
                    VALID_ENERGY_RATINGS,
                    msg=f"house_energy_rating must be one of: {', '.join(VALID_ENERGY_RATINGS)}"
                ),
                vol.Optional(
                    CONF_WINDOW_RATING,
                    default=DEFAULT_WINDOW_RATING
                ): mock_string,

                # Heat output sensors
                vol.Optional(CONF_SUPPLY_TEMP_SENSOR): mock_entity_id,
                vol.Optional(CONF_RETURN_TEMP_SENSOR): mock_entity_id,
                vol.Optional(CONF_FLOW_RATE_SENSOR): mock_entity_id,
                vol.Optional(CONF_VOLUME_METER_ENTITY): mock_entity_id,
                vol.Optional(
                    CONF_FALLBACK_FLOW_RATE,
                    default=DEFAULT_FALLBACK_FLOW_RATE
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(
                        min=0.01,
                        max=10.0,
                        msg="fallback_flow_rate must be between 0.01 and 10.0 L/s"
                    )
                ),
            })
        },
        extra=vol.ALLOW_EXTRA,
    )


# =============================================================================
# Test Valid Config Acceptance
# =============================================================================


class TestValidConfigAcceptance:
    """Tests for valid configuration acceptance."""

    def test_minimal_config(self):
        """Test that minimal config (empty domain config) is accepted."""
        schema = create_test_schema()
        config = {DOMAIN: {}}
        result = schema(config)
        assert DOMAIN in result
        # Check defaults are applied
        assert result[DOMAIN][CONF_PERSISTENT_NOTIFICATION] == DEFAULT_PERSISTENT_NOTIFICATION
        assert result[DOMAIN][CONF_SOURCE_STARTUP_DELAY] == DEFAULT_SOURCE_STARTUP_DELAY
        assert result[DOMAIN][CONF_SYNC_MODES] == DEFAULT_SYNC_MODES
        assert result[DOMAIN][CONF_LEARNING_WINDOW_DAYS] == DEFAULT_LEARNING_WINDOW_DAYS
        assert result[DOMAIN][CONF_WINDOW_RATING] == DEFAULT_WINDOW_RATING
        assert result[DOMAIN][CONF_FALLBACK_FLOW_RATE] == DEFAULT_FALLBACK_FLOW_RATE

    def test_full_config(self):
        """Test that a full valid config is accepted."""
        schema = create_test_schema()
        config = {
            DOMAIN: {
                CONF_NOTIFY_SERVICE: "mobile_app_phone",
                CONF_PERSISTENT_NOTIFICATION: True,
                CONF_ENERGY_METER_ENTITY: "sensor.energy_meter",
                CONF_ENERGY_COST_ENTITY: "sensor.energy_cost",
                CONF_MAIN_HEATER_SWITCH: "switch.main_heater",
                CONF_MAIN_COOLER_SWITCH: "switch.main_cooler",
                CONF_SOURCE_STARTUP_DELAY: 60,
                CONF_SYNC_MODES: True,
                CONF_LEARNING_WINDOW_DAYS: 14,
                CONF_WEATHER_ENTITY: "weather.home",
                CONF_HOUSE_ENERGY_RATING: "B",
                CONF_WINDOW_RATING: "hr++",
                CONF_SUPPLY_TEMP_SENSOR: "sensor.supply_temp",
                CONF_RETURN_TEMP_SENSOR: "sensor.return_temp",
                CONF_FLOW_RATE_SENSOR: "sensor.flow_rate",
                CONF_VOLUME_METER_ENTITY: "sensor.volume",
                CONF_FALLBACK_FLOW_RATE: 1.5,
            }
        }
        result = schema(config)
        assert result[DOMAIN][CONF_NOTIFY_SERVICE] == "mobile_app_phone"
        assert result[DOMAIN][CONF_LEARNING_WINDOW_DAYS] == 14
        assert result[DOMAIN][CONF_HOUSE_ENERGY_RATING] == "B"

    def test_other_domains_allowed(self):
        """Test that other domains are allowed in config."""
        schema = create_test_schema()
        config = {
            DOMAIN: {CONF_NOTIFY_SERVICE: "test_service"},
            "other_domain": {"some_key": "some_value"},
        }
        result = schema(config)
        assert DOMAIN in result
        assert "other_domain" in result

    def test_notify_service_with_domain_prefix(self):
        """Test notify_service with 'notify.' prefix is accepted."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"}}
        result = schema(config)
        assert result[DOMAIN][CONF_NOTIFY_SERVICE] == "notify.mobile_app_phone"

    def test_all_energy_ratings_accepted(self):
        """Test that all valid energy ratings are accepted."""
        schema = create_test_schema()
        for rating in VALID_ENERGY_RATINGS:
            config = {DOMAIN: {CONF_HOUSE_ENERGY_RATING: rating}}
            result = schema(config)
            assert result[DOMAIN][CONF_HOUSE_ENERGY_RATING] == rating


# =============================================================================
# Test Invalid Config Rejection
# =============================================================================


class TestInvalidConfigRejection:
    """Tests for invalid configuration rejection with clear errors."""

    def test_invalid_notify_service_format_uppercase(self):
        """Test that uppercase notify_service is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: "Mobile_App"}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "Invalid notify_service format" in str(exc_info.value)

    def test_invalid_notify_service_format_spaces(self):
        """Test that notify_service with spaces is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: "mobile app"}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "Invalid notify_service format" in str(exc_info.value)

    def test_invalid_notify_service_format_special_chars(self):
        """Test that notify_service with special chars is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: "mobile-app!"}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "Invalid notify_service format" in str(exc_info.value)

    def test_invalid_notify_service_starts_with_number(self):
        """Test that notify_service starting with number is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: "1mobile_app"}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "Invalid notify_service format" in str(exc_info.value)

    def test_invalid_notify_service_empty(self):
        """Test that empty notify_service is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: ""}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "cannot be empty" in str(exc_info.value)

    def test_invalid_notify_service_whitespace_only(self):
        """Test that whitespace-only notify_service is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: "   "}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "cannot be empty" in str(exc_info.value)

    def test_invalid_notify_service_wrong_type(self):
        """Test that non-string notify_service is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_NOTIFY_SERVICE: 123}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "must be a string" in str(exc_info.value)

    def test_invalid_energy_rating(self):
        """Test that invalid energy rating is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_HOUSE_ENERGY_RATING: "X"}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        # Voluptuous raises error for invalid In() value
        error_msg = str(exc_info.value)
        assert "X" in error_msg or "house_energy_rating" in error_msg

    def test_invalid_startup_delay_too_high(self):
        """Test that startup_delay > 300 is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_SOURCE_STARTUP_DELAY: 500}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "300" in str(exc_info.value) or "range" in str(exc_info.value).lower()

    def test_invalid_startup_delay_negative(self):
        """Test that negative startup_delay is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_SOURCE_STARTUP_DELAY: -10}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "0" in str(exc_info.value) or "range" in str(exc_info.value).lower()

    def test_invalid_learning_window_too_high(self):
        """Test that learning_window_days > 30 is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_LEARNING_WINDOW_DAYS: 60}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "30" in str(exc_info.value) or "range" in str(exc_info.value).lower()

    def test_invalid_learning_window_zero(self):
        """Test that learning_window_days = 0 is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_LEARNING_WINDOW_DAYS: 0}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "1" in str(exc_info.value) or "range" in str(exc_info.value).lower()

    def test_invalid_fallback_flow_rate_too_high(self):
        """Test that fallback_flow_rate > 10 is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_FALLBACK_FLOW_RATE: 15.0}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "10" in str(exc_info.value) or "range" in str(exc_info.value).lower()

    def test_invalid_fallback_flow_rate_too_low(self):
        """Test that fallback_flow_rate < 0.01 is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_FALLBACK_FLOW_RATE: 0.001}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "0.01" in str(exc_info.value) or "range" in str(exc_info.value).lower()

    def test_invalid_entity_id_no_domain(self):
        """Test that entity_id without domain is rejected."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_ENERGY_METER_ENTITY: "no_domain_here"}}
        with pytest.raises(vol.Invalid) as exc_info:
            schema(config)
        assert "domain" in str(exc_info.value).lower() or "entity" in str(exc_info.value).lower()


# =============================================================================
# Test Notify Service Validator
# =============================================================================


class TestNotifyServiceValidator:
    """Tests for the valid_notify_service validator function."""

    def test_simple_service_name(self):
        """Test valid simple service name."""
        assert valid_notify_service("mobile_app_phone") == "mobile_app_phone"

    def test_service_name_with_numbers(self):
        """Test valid service name with numbers."""
        assert valid_notify_service("mobile_app_1") == "mobile_app_1"

    def test_service_name_with_notify_prefix(self):
        """Test valid service name with notify prefix."""
        assert valid_notify_service("notify.my_service") == "notify.my_service"

    def test_service_name_single_letter(self):
        """Test valid single letter service name."""
        assert valid_notify_service("a") == "a"

    def test_service_name_with_underscores(self):
        """Test valid service name with underscores."""
        assert valid_notify_service("my_service_name_here") == "my_service_name_here"

    def test_strips_whitespace(self):
        """Test that whitespace is stripped."""
        assert valid_notify_service("  mobile_app  ") == "mobile_app"

    def test_rejects_uppercase(self):
        """Test that uppercase is rejected."""
        with pytest.raises(vol.Invalid) as exc_info:
            valid_notify_service("Mobile_App")
        assert "lowercase" in str(exc_info.value).lower()

    def test_rejects_starting_with_number(self):
        """Test that starting with number is rejected."""
        with pytest.raises(vol.Invalid) as exc_info:
            valid_notify_service("1service")
        assert "start with a letter" in str(exc_info.value)

    def test_rejects_hyphen(self):
        """Test that hyphen is rejected."""
        with pytest.raises(vol.Invalid) as exc_info:
            valid_notify_service("my-service")
        assert "Invalid notify_service format" in str(exc_info.value)

    def test_rejects_wrong_domain_prefix(self):
        """Test that wrong domain prefix is rejected."""
        with pytest.raises(vol.Invalid) as exc_info:
            valid_notify_service("other.my_service")
        assert "Invalid notify_service format" in str(exc_info.value)

    def test_rejects_non_string(self):
        """Test that non-string is rejected."""
        with pytest.raises(vol.Invalid) as exc_info:
            valid_notify_service(["list"])
        assert "must be a string" in str(exc_info.value)

    def test_rejects_none(self):
        """Test that None is rejected."""
        with pytest.raises(vol.Invalid) as exc_info:
            valid_notify_service(None)
        assert "must be a string" in str(exc_info.value)


# =============================================================================
# Test Boundary Values
# =============================================================================


class TestBoundaryValues:
    """Tests for boundary values in config validation."""

    def test_startup_delay_at_minimum(self):
        """Test startup_delay at minimum (0)."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_SOURCE_STARTUP_DELAY: 0}}
        result = schema(config)
        assert result[DOMAIN][CONF_SOURCE_STARTUP_DELAY] == 0

    def test_startup_delay_at_maximum(self):
        """Test startup_delay at maximum (300)."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_SOURCE_STARTUP_DELAY: 300}}
        result = schema(config)
        assert result[DOMAIN][CONF_SOURCE_STARTUP_DELAY] == 300

    def test_learning_window_at_minimum(self):
        """Test learning_window_days at minimum (1)."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_LEARNING_WINDOW_DAYS: 1}}
        result = schema(config)
        assert result[DOMAIN][CONF_LEARNING_WINDOW_DAYS] == 1

    def test_learning_window_at_maximum(self):
        """Test learning_window_days at maximum (30)."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_LEARNING_WINDOW_DAYS: 30}}
        result = schema(config)
        assert result[DOMAIN][CONF_LEARNING_WINDOW_DAYS] == 30

    def test_fallback_flow_rate_at_minimum(self):
        """Test fallback_flow_rate at minimum (0.01)."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_FALLBACK_FLOW_RATE: 0.01}}
        result = schema(config)
        assert result[DOMAIN][CONF_FALLBACK_FLOW_RATE] == pytest.approx(0.01)

    def test_fallback_flow_rate_at_maximum(self):
        """Test fallback_flow_rate at maximum (10.0)."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_FALLBACK_FLOW_RATE: 10.0}}
        result = schema(config)
        assert result[DOMAIN][CONF_FALLBACK_FLOW_RATE] == pytest.approx(10.0)


# =============================================================================
# Test Type Coercion
# =============================================================================


class TestTypeCoercion:
    """Tests for type coercion in config validation."""

    def test_startup_delay_string_to_int(self):
        """Test startup_delay is coerced from string to int."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_SOURCE_STARTUP_DELAY: "60"}}
        result = schema(config)
        assert result[DOMAIN][CONF_SOURCE_STARTUP_DELAY] == 60
        assert isinstance(result[DOMAIN][CONF_SOURCE_STARTUP_DELAY], int)

    def test_learning_window_string_to_int(self):
        """Test learning_window_days is coerced from string to int."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_LEARNING_WINDOW_DAYS: "14"}}
        result = schema(config)
        assert result[DOMAIN][CONF_LEARNING_WINDOW_DAYS] == 14
        assert isinstance(result[DOMAIN][CONF_LEARNING_WINDOW_DAYS], int)

    def test_fallback_flow_rate_int_to_float(self):
        """Test fallback_flow_rate is coerced from int to float."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_FALLBACK_FLOW_RATE: 5}}
        result = schema(config)
        assert result[DOMAIN][CONF_FALLBACK_FLOW_RATE] == 5.0
        assert isinstance(result[DOMAIN][CONF_FALLBACK_FLOW_RATE], float)

    def test_fallback_flow_rate_string_to_float(self):
        """Test fallback_flow_rate is coerced from string to float."""
        schema = create_test_schema()
        config = {DOMAIN: {CONF_FALLBACK_FLOW_RATE: "2.5"}}
        result = schema(config)
        assert result[DOMAIN][CONF_FALLBACK_FLOW_RATE] == pytest.approx(2.5)


# =============================================================================
# Module Import Test
# =============================================================================


def test_config_validation_module_exists():
    """Test that CONFIG_SCHEMA and valid_notify_service are importable."""
    from custom_components.adaptive_thermostat import valid_notify_service
    assert callable(valid_notify_service)


# =============================================================================
# Unload Tests (Do not require voluptuous)
# =============================================================================


# These tests don't require voluptuous - they only test mock functionality
class TestAsyncUnload:
    """Tests for async_unload function."""

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unload_with_no_domain_data(self):
        """Test unload when domain data doesn't exist."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Create mock hass without domain data
        hass = MagicMock()
        hass.data = {}

        # Run unload
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))

        # Should succeed even if nothing to unload
        assert result is True

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unload_cancels_scheduled_tasks(self):
        """Test that unload cancels all scheduled task callbacks."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Create mock callbacks
        mock_unsub_1 = MagicMock()
        mock_unsub_2 = MagicMock()
        mock_unsub_3 = MagicMock()

        # Create mock hass with domain data
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "unsub_callbacks": [mock_unsub_1, mock_unsub_2, mock_unsub_3],
                "coordinator": MagicMock(),
            }
        }
        hass.services.has_service.return_value = False

        # Run unload
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))

        # Verify all callbacks were called
        assert result is True
        mock_unsub_1.assert_called_once()
        mock_unsub_2.assert_called_once()
        mock_unsub_3.assert_called_once()

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unload_handles_callback_errors_gracefully(self):
        """Test that unload continues even if a callback raises an error."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Create mock callbacks - one that raises an error
        mock_unsub_1 = MagicMock(side_effect=Exception("Test error"))
        mock_unsub_2 = MagicMock()

        # Create mock hass with domain data
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "unsub_callbacks": [mock_unsub_1, mock_unsub_2],
                "coordinator": MagicMock(),
            }
        }
        hass.services.has_service.return_value = False

        # Run unload - should not raise
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))

        # Should still succeed and call both
        assert result is True
        mock_unsub_1.assert_called_once()
        mock_unsub_2.assert_called_once()

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unload_handles_none_callbacks(self):
        """Test that unload handles None values in callback list."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Create mock callbacks with None
        mock_unsub = MagicMock()

        # Create mock hass with domain data
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "unsub_callbacks": [None, mock_unsub, None],
                "coordinator": MagicMock(),
            }
        }
        hass.services.has_service.return_value = False

        # Run unload
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))

        # Should succeed and only call non-None callback
        assert result is True
        mock_unsub.assert_called_once()

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unload_removes_domain_data(self):
        """Test that unload removes all domain data from hass.data."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Create mock hass with domain data
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "coordinator": MagicMock(),
                "vacation_mode": MagicMock(),
                "central_controller": None,
                "mode_sync": MagicMock(),
                "zone_linker": MagicMock(),
                "unsub_callbacks": [],
                "notify_service": "test",
            },
            "other_domain": {"keep": "this"},
        }
        hass.services.has_service.return_value = False

        # Run unload
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))

        # Domain data should be removed, other domains preserved
        assert result is True
        assert DOMAIN not in hass.data
        assert "other_domain" in hass.data

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unload_clears_central_controller_reference(self):
        """Test that unload clears central controller reference from coordinator."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Create mock coordinator and central controller
        mock_coordinator = MagicMock()
        mock_central_controller = MagicMock()

        # Create mock hass with domain data
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "coordinator": mock_coordinator,
                "central_controller": mock_central_controller,
                "unsub_callbacks": [],
            }
        }
        hass.services.has_service.return_value = False

        # Run unload
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))

        # Coordinator should have central controller cleared
        assert result is True
        mock_coordinator.set_central_controller.assert_called_once_with(None)


class TestAsyncUnregisterServices:
    """Tests for async_unregister_services function."""

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unregister_services_removes_all_services(self):
        """Test that all services are unregistered."""
        from custom_components.adaptive_thermostat.services import (
            async_unregister_services,
            SERVICE_RUN_LEARNING,
            SERVICE_HEALTH_CHECK,
            SERVICE_WEEKLY_REPORT,
            SERVICE_COST_REPORT,
            SERVICE_SET_VACATION_MODE,
            SERVICE_ENERGY_STATS,
            SERVICE_PID_RECOMMENDATIONS,
        )
        from custom_components.adaptive_thermostat.const import DOMAIN

        # Create mock hass where all services exist
        hass = MagicMock()
        hass.services.has_service.return_value = True

        # Run unregister
        async_unregister_services(hass)

        # Verify async_remove was called for each service
        expected_services = [
            SERVICE_RUN_LEARNING,
            SERVICE_HEALTH_CHECK,
            SERVICE_WEEKLY_REPORT,
            SERVICE_COST_REPORT,
            SERVICE_SET_VACATION_MODE,
            SERVICE_ENERGY_STATS,
            SERVICE_PID_RECOMMENDATIONS,
        ]

        assert hass.services.async_remove.call_count == len(expected_services)
        for service in expected_services:
            hass.services.async_remove.assert_any_call(DOMAIN, service)

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unregister_services_skips_nonexistent(self):
        """Test that unregister skips services that don't exist."""
        from custom_components.adaptive_thermostat.services import (
            async_unregister_services,
        )
        from custom_components.adaptive_thermostat.const import DOMAIN

        # Create mock hass where no services exist
        hass = MagicMock()
        hass.services.has_service.return_value = False

        # Run unregister
        async_unregister_services(hass)

        # async_remove should not have been called
        hass.services.async_remove.assert_not_called()


class TestReloadWithoutLeftoverState:
    """Tests for reload scenarios ensuring no leftover state."""

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_setup_then_unload_then_setup_is_clean(self):
        """Test that after unload, setup can run cleanly again."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Simulate first setup by populating hass.data
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "coordinator": MagicMock(),
                "vacation_mode": MagicMock(),
                "central_controller": MagicMock(),
                "mode_sync": MagicMock(),
                "zone_linker": MagicMock(),
                "unsub_callbacks": [MagicMock(), MagicMock()],
                "notify_service": "test_service",
                "persistent_notification": True,
                "energy_meter_entity": "sensor.energy",
                "learning_window_days": 7,
            }
        }
        hass.services.has_service.return_value = True

        # Run unload
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))
        assert result is True

        # After unload, domain should not exist in hass.data
        assert DOMAIN not in hass.data

        # A subsequent setup can now use setdefault without conflict
        hass.data.setdefault(DOMAIN, {})
        assert hass.data[DOMAIN] == {}

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_unload_clears_all_expected_keys(self):
        """Test that unload removes all keys that setup creates."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # All keys that async_setup creates
        expected_keys = [
            "coordinator",
            "vacation_mode",
            "notify_service",
            "persistent_notification",
            "energy_meter_entity",
            "energy_cost_entity",
            "main_heater_switch",
            "main_cooler_switch",
            "source_startup_delay",
            "central_controller",
            "sync_modes",
            "mode_sync",
            "zone_linker",
            "learning_window_days",
            "weather_entity",
            "house_energy_rating",
            "window_rating",
            "supply_temp_sensor",
            "return_temp_sensor",
            "flow_rate_sensor",
            "volume_meter_entity",
            "fallback_flow_rate",
            "unsub_callbacks",
        ]

        # Create mock hass with all keys populated
        hass = MagicMock()
        hass.data = {DOMAIN: {key: MagicMock() for key in expected_keys}}
        hass.services.has_service.return_value = False

        # Run unload
        result = asyncio.get_event_loop().run_until_complete(async_unload(hass))

        # All keys should be removed (entire domain entry removed)
        assert result is True
        assert DOMAIN not in hass.data

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_multiple_unloads_are_safe(self):
        """Test that calling unload multiple times is safe."""
        from custom_components.adaptive_thermostat import async_unload
        from custom_components.adaptive_thermostat.const import DOMAIN
        import asyncio

        # Create mock hass with domain data
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "coordinator": MagicMock(),
                "unsub_callbacks": [],
            }
        }
        hass.services.has_service.return_value = False

        # First unload
        result1 = asyncio.get_event_loop().run_until_complete(async_unload(hass))
        assert result1 is True
        assert DOMAIN not in hass.data

        # Second unload should also succeed (idempotent)
        result2 = asyncio.get_event_loop().run_until_complete(async_unload(hass))
        assert result2 is True

    @pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")
    def test_services_not_duplicated_after_reload(self):
        """Test that services aren't registered twice after reload."""
        from custom_components.adaptive_thermostat.services import (
            async_register_services,
            async_unregister_services,
            SERVICE_RUN_LEARNING,
        )
        from custom_components.adaptive_thermostat.const import DOMAIN

        hass = MagicMock()

        # Track service registrations
        registered_services = {}

        def mock_async_register(domain, service, handler, schema=None):
            key = f"{domain}.{service}"
            registered_services[key] = registered_services.get(key, 0) + 1

        def mock_has_service(domain, service):
            return f"{domain}.{service}" in registered_services

        def mock_async_remove(domain, service):
            key = f"{domain}.{service}"
            if key in registered_services:
                del registered_services[key]

        hass.services.async_register = mock_async_register
        hass.services.has_service = mock_has_service
        hass.services.async_remove = mock_async_remove

        # First registration
        async_register_services(
            hass=hass,
            coordinator=MagicMock(),
            vacation_mode=MagicMock(),
            notify_service=None,
            persistent_notification=True,
            async_send_notification_func=MagicMock(),
            async_send_persistent_notification_func=MagicMock(),
            vacation_schema=None,
            cost_report_schema=None,
            default_vacation_target_temp=12.0,
        )

        # All services should be registered once
        assert registered_services[f"{DOMAIN}.{SERVICE_RUN_LEARNING}"] == 1

        # Unregister
        async_unregister_services(hass)

        # Services should be removed
        assert f"{DOMAIN}.{SERVICE_RUN_LEARNING}" not in registered_services

        # Re-register
        async_register_services(
            hass=hass,
            coordinator=MagicMock(),
            vacation_mode=MagicMock(),
            notify_service=None,
            persistent_notification=True,
            async_send_notification_func=MagicMock(),
            async_send_persistent_notification_func=MagicMock(),
            vacation_schema=None,
            cost_report_schema=None,
            default_vacation_target_temp=12.0,
        )

        # Service should be registered exactly once again (not duplicated)
        assert registered_services[f"{DOMAIN}.{SERVICE_RUN_LEARNING}"] == 1
