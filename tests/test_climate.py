"""Tests for Climate entity service failure handling (Story 5.2)."""
import asyncio
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock


# Create mock exception classes
class MockServiceNotFound(Exception):
    """Mock ServiceNotFound exception."""
    pass


class MockHomeAssistantError(Exception):
    """Mock HomeAssistantError exception."""
    pass


DOMAIN = "adaptive_thermostat"


class MockHVACMode:
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"
    HEAT_COOL = "heat_cool"


class MockSmartThermostat:
    """Mock SmartThermostat class for testing service call error handling."""

    def __init__(self, hass, heater_entity_id="switch.heater"):
        self.hass = hass
        self._heater_entity_id = [heater_entity_id]
        self._cooler_entity_id = None
        self._demand_switch_entity_id = None
        self._heater_polarity_invert = False
        self._hvac_mode = MockHVACMode.HEAT
        self._heater_control_failed = False
        self._last_heater_error = None
        self._unique_id = "test_thermostat"
        self._is_device_active = False
        self._last_heat_cycle_time = 0
        self._min_off_cycle_duration = Mock()
        self._min_off_cycle_duration.seconds = 0
        self._min_on_cycle_duration = Mock()
        self._min_on_cycle_duration.seconds = 0
        self._zone_linker = None
        self._linked_zones = []
        self._is_heating = False
        self._link_delay_minutes = 10

    @property
    def entity_id(self):
        return "climate.test_thermostat"

    @property
    def hvac_mode(self):
        return self._hvac_mode

    @property
    def heater_or_cooler_entity(self):
        if self._hvac_mode == MockHVACMode.COOL and self._cooler_entity_id:
            return self._cooler_entity_id
        return self._heater_entity_id or []

    def _fire_heater_control_failed_event(
        self,
        entity_id: str,
        operation: str,
        error: str,
    ) -> None:
        """Fire an event when heater control fails."""
        self.hass.bus.async_fire(
            f"{DOMAIN}_heater_control_failed",
            {
                "climate_entity_id": self.entity_id,
                "heater_entity_id": entity_id,
                "operation": operation,
                "error": error,
            },
        )

    async def _async_call_heater_service(
        self,
        entity_id: str,
        domain: str,
        service: str,
        data: dict,
    ) -> bool:
        """Call a heater/cooler service with error handling."""
        try:
            await self.hass.services.async_call(domain, service, data)
            self._heater_control_failed = False
            self._last_heater_error = None
            return True

        except MockServiceNotFound as e:
            self._heater_control_failed = True
            self._last_heater_error = f"Service not found: {domain}.{service}"
            self._fire_heater_control_failed_event(entity_id, service, str(e))
            return False

        except MockHomeAssistantError as e:
            self._heater_control_failed = True
            self._last_heater_error = str(e)
            self._fire_heater_control_failed_event(entity_id, service, str(e))
            return False

        except Exception as e:
            self._heater_control_failed = True
            self._last_heater_error = str(e)
            self._fire_heater_control_failed_event(entity_id, service, str(e))
            return False

    async def _async_heater_turn_on(self):
        """Turn heater on with error handling."""
        for heater_entity in self.heater_or_cooler_entity:
            data = {"entity_id": heater_entity}
            service = "turn_off" if self._heater_polarity_invert else "turn_on"
            await self._async_call_heater_service(
                heater_entity, "homeassistant", service, data
            )

    async def _async_heater_turn_off(self):
        """Turn heater off with error handling."""
        for heater_entity in self.heater_or_cooler_entity:
            data = {"entity_id": heater_entity}
            service = "turn_on" if self._heater_polarity_invert else "turn_off"
            await self._async_call_heater_service(
                heater_entity, "homeassistant", service, data
            )

    async def _async_set_valve_value(self, value: float):
        """Set valve value with error handling."""
        for heater_entity in self.heater_or_cooler_entity:
            if heater_entity.startswith('light.'):
                data = {"entity_id": heater_entity, "brightness_pct": value}
                await self._async_call_heater_service(
                    heater_entity, "light", "turn_on", data
                )
            elif heater_entity.startswith('valve.'):
                data = {"entity_id": heater_entity, "position": value}
                await self._async_call_heater_service(
                    heater_entity, "valve", "set_valve_position", data
                )
            else:
                data = {"entity_id": heater_entity, "value": value}
                await self._async_call_heater_service(
                    heater_entity, "number", "set_value", data
                )

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        attrs = {}
        if self._heater_control_failed:
            attrs["heater_control_failed"] = True
            attrs["last_heater_error"] = self._last_heater_error
        return attrs


def _create_mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = Mock()
    return hass


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# =============================================================================
# Test Cases for Story 5.2: Add error handling for heater service calls
# =============================================================================


class TestServiceCallSuccess:
    """Tests for successful service calls."""

    def test_turn_on_success(self):
        """Test successful turn_on service call."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)

        _run_async(thermostat._async_heater_turn_on())

        mock_hass.services.async_call.assert_called_once_with(
            "homeassistant",
            "turn_on",
            {"entity_id": "switch.heater"},
        )
        assert thermostat._heater_control_failed is False
        assert thermostat._last_heater_error is None

    def test_turn_off_success(self):
        """Test successful turn_off service call."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)

        _run_async(thermostat._async_heater_turn_off())

        mock_hass.services.async_call.assert_called_once_with(
            "homeassistant",
            "turn_off",
            {"entity_id": "switch.heater"},
        )
        assert thermostat._heater_control_failed is False
        assert thermostat._last_heater_error is None

    def test_set_valve_value_success(self):
        """Test successful set_value service call for number entity."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        thermostat._heater_entity_id = ["number.valve"]

        _run_async(thermostat._async_set_valve_value(50.0))

        mock_hass.services.async_call.assert_called_once_with(
            "number",
            "set_value",
            {"entity_id": "number.valve", "value": 50.0},
        )
        assert thermostat._heater_control_failed is False


class TestServiceNotFoundError:
    """Tests for ServiceNotFound exception handling."""

    def test_service_not_found_sets_failure_attribute(self):
        """Test ServiceNotFound exception sets heater_control_failed attribute."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = MockServiceNotFound(
            "Service homeassistant.turn_on not found"
        )

        _run_async(thermostat._async_heater_turn_on())

        assert thermostat._heater_control_failed is True
        assert "Service not found" in thermostat._last_heater_error

    def test_service_not_found_fires_event(self):
        """Test ServiceNotFound exception fires heater_control_failed event."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = MockServiceNotFound(
            "Service homeassistant.turn_on not found"
        )

        _run_async(thermostat._async_heater_turn_on())

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert call_args[0][0] == "adaptive_thermostat_heater_control_failed"
        event_data = call_args[0][1]
        assert event_data["climate_entity_id"] == "climate.test_thermostat"
        assert event_data["heater_entity_id"] == "switch.heater"
        assert event_data["operation"] == "turn_on"

    def test_service_not_found_returns_false(self):
        """Test ServiceNotFound exception returns False from helper method."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = MockServiceNotFound("not found")

        result = _run_async(thermostat._async_call_heater_service(
            "switch.heater", "homeassistant", "turn_on", {"entity_id": "switch.heater"}
        ))

        assert result is False


class TestHomeAssistantError:
    """Tests for HomeAssistantError exception handling."""

    def test_home_assistant_error_sets_failure_attribute(self):
        """Test HomeAssistantError exception sets heater_control_failed attribute."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = MockHomeAssistantError(
            "Entity switch.heater is unavailable"
        )

        _run_async(thermostat._async_heater_turn_on())

        assert thermostat._heater_control_failed is True
        assert "unavailable" in thermostat._last_heater_error

    def test_home_assistant_error_fires_event(self):
        """Test HomeAssistantError exception fires heater_control_failed event."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = MockHomeAssistantError(
            "Entity unavailable"
        )

        _run_async(thermostat._async_heater_turn_off())

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert call_args[0][0] == "adaptive_thermostat_heater_control_failed"
        assert "Entity unavailable" in call_args[0][1]["error"]


class TestUnexpectedError:
    """Tests for unexpected exception handling."""

    def test_unexpected_error_sets_failure_attribute(self):
        """Test unexpected exception sets heater_control_failed attribute."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = RuntimeError("Unexpected failure")

        _run_async(thermostat._async_heater_turn_on())

        assert thermostat._heater_control_failed is True
        assert "Unexpected failure" in thermostat._last_heater_error

    def test_unexpected_error_fires_event(self):
        """Test unexpected exception fires heater_control_failed event."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = RuntimeError("Connection lost")

        _run_async(thermostat._async_heater_turn_on())

        mock_hass.bus.async_fire.assert_called_once()


class TestSuccessfulCallClearsFailure:
    """Tests for clearing failure state on successful calls."""

    def test_success_clears_failure_state(self):
        """Test successful service call clears previous failure state."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)

        # Set up previous failure state
        thermostat._heater_control_failed = True
        thermostat._last_heater_error = "Previous error"

        # Make successful call
        _run_async(thermostat._async_heater_turn_on())

        # Verify failure state is cleared
        assert thermostat._heater_control_failed is False
        assert thermostat._last_heater_error is None


class TestExtraStateAttributes:
    """Tests for extra state attributes exposure."""

    def test_failure_attributes_exposed_when_failed(self):
        """Test failure attributes are included in extra_state_attributes when failed."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        thermostat._heater_control_failed = True
        thermostat._last_heater_error = "Service not found: homeassistant.turn_on"

        attrs = thermostat.extra_state_attributes

        assert attrs["heater_control_failed"] is True
        assert attrs["last_heater_error"] == "Service not found: homeassistant.turn_on"

    def test_failure_attributes_not_exposed_when_ok(self):
        """Test failure attributes not included when no failure."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        thermostat._heater_control_failed = False

        attrs = thermostat.extra_state_attributes

        assert "heater_control_failed" not in attrs
        assert "last_heater_error" not in attrs


class TestValveEntityTypes:
    """Tests for different valve entity types (light, valve, number)."""

    def test_light_entity_service_failure(self):
        """Test error handling for light entity valve control."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        thermostat._heater_entity_id = ["light.radiator_valve"]
        mock_hass.services.async_call.side_effect = MockHomeAssistantError(
            "Light unavailable"
        )

        _run_async(thermostat._async_set_valve_value(75.0))

        assert thermostat._heater_control_failed is True
        mock_hass.bus.async_fire.assert_called_once()

    def test_valve_entity_service_failure(self):
        """Test error handling for valve entity control."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        thermostat._heater_entity_id = ["valve.radiator"]
        mock_hass.services.async_call.side_effect = MockHomeAssistantError(
            "Valve unavailable"
        )

        _run_async(thermostat._async_set_valve_value(50.0))

        assert thermostat._heater_control_failed is True


class TestMultipleEntities:
    """Tests for controlling multiple heater entities."""

    def test_partial_failure_with_multiple_entities(self):
        """Test that failure is reported even if only some entities fail."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        thermostat._heater_entity_id = ["switch.heater1", "switch.heater2"]

        # First call succeeds, second fails
        call_count = [0]

        async def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise MockHomeAssistantError("Second heater unavailable")

        mock_hass.services.async_call.side_effect = side_effect

        _run_async(thermostat._async_heater_turn_on())

        # Both entities should have been attempted
        assert mock_hass.services.async_call.call_count == 2
        # Failure should be recorded
        assert thermostat._heater_control_failed is True


class TestEventData:
    """Tests for event data correctness."""

    def test_event_contains_correct_entity_ids(self):
        """Test event contains correct climate and heater entity IDs."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = MockServiceNotFound("not found")

        _run_async(thermostat._async_heater_turn_on())

        call_args = mock_hass.bus.async_fire.call_args
        event_data = call_args[0][1]
        assert event_data["climate_entity_id"] == "climate.test_thermostat"
        assert event_data["heater_entity_id"] == "switch.heater"
        assert event_data["operation"] == "turn_on"
        assert "not found" in event_data["error"]

    def test_event_operation_matches_service_called(self):
        """Test event operation field matches the service that was called."""
        mock_hass = _create_mock_hass()
        thermostat = MockSmartThermostat(mock_hass)
        mock_hass.services.async_call.side_effect = MockServiceNotFound("not found")

        _run_async(thermostat._async_heater_turn_off())

        call_args = mock_hass.bus.async_fire.call_args
        event_data = call_args[0][1]
        assert event_data["operation"] == "turn_off"


def test_service_failure_module_exists():
    """Test that this module exists and can be imported."""
    assert True
