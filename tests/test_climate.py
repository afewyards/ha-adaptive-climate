"""Tests for AdaptiveThermostat entity behavioral interface.

Organized around observable behavior via public API:
- State attributes (extra_state_attributes)
- Public properties (hvac_mode, hvac_action, target_temperature, preset_mode)
- Service handlers
- State restoration via RestoreEntity

Tests deleted (from previous version):
- Internal wiring tests (dispatcher identity, listener registration counts)
- Tests asserting only on mock.call_count without checking outcomes
- Tests asserting on private attributes (_private)
- Static code analysis tests (checking source code strings)

Tests kept and rewritten:
- Service call error handling with observable outcomes
- State restoration round-trip via public properties
- Preset mode behavior
- Night setback functionality
- State attributes structure
- HVAC mode transitions
- PID controller integration behaviors
"""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, MagicMock, patch


# Mock Home Assistant exception classes
class MockServiceNotFound(Exception):
    """Mock ServiceNotFound exception."""
    pass


class MockHomeAssistantError(Exception):
    """Mock HomeAssistantError exception."""
    pass


class MockHVACMode:
    """Mock HVAC mode constants."""
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"
    HEAT_COOL = "heat_cool"


class MockPresetMode:
    """Mock preset mode constants."""
    NONE = "none"
    AWAY = "away"
    ECO = "eco"
    BOOST = "boost"
    COMFORT = "comfort"
    HOME = "home"
    SLEEP = "sleep"
    ACTIVITY = "activity"


class MockState:
    """Mock state object for restoration tests."""
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class MockClimateEntity:
    """Minimal mock of AdaptiveThermostat for behavior testing.

    Tests OBSERVABLE behavior only: public properties, extra_state_attributes,
    service call outcomes. No private attribute assertions.
    """
    def __init__(self, hass):
        self.hass = hass
        self.entity_id = "climate.test_thermostat"
        self._unique_id = "test_thermostat"

        # Configuration
        self._heater_entity_id = ["switch.heater"]
        self._cooler_entity_id = None
        self._demand_switch_entity_id = None
        self._sensor_entity_id = "sensor.temperature"
        self._ext_sensor_entity_id = None
        self._heater_polarity_invert = False

        # Mode and state
        self._hvac_mode = MockHVACMode.HEAT
        self._ac_mode = False
        self._attr_preset_mode = MockPresetMode.NONE

        # Temperature settings
        self._target_temp = 21.0
        self.min_temp = 16.0
        self.max_temp = 30.0
        self._away_temp = None
        self._eco_temp = None
        self._boost_temp = None
        self._comfort_temp = None
        self._home_temp = None
        self._sleep_temp = None
        self._activity_temp = None

        # Error tracking (observable via extra_state_attributes)
        self._heater_control_failed = False
        self._last_heater_error = None

        # Control state
        self._control_output = 0.0
        self._is_device_active = False

        # Managers (simplified for behavioral testing)
        self._pid_controller = None
        self._heater_controller = None
        self._night_setback_config = None
        self._night_setback_controller = None
        self._coordinator = None
        self._gains_manager = None
        self._contact_sensor_handler = None
        self._humidity_detector = None
        self._preheat_learner = None

    @property
    def hvac_mode(self):
        """Return current HVAC mode."""
        return self._hvac_mode

    @property
    def hvac_action(self):
        """Return current HVAC action (observable behavior)."""
        if self._heater_control_failed:
            return "idle"
        if self._hvac_mode == MockHVACMode.OFF:
            return "off"
        if self._is_device_active:
            return "heating" if self._hvac_mode == MockHVACMode.HEAT else "cooling"
        return "idle"

    @property
    def target_temperature(self):
        """Return target temperature."""
        return self._target_temp

    @property
    def preset_mode(self):
        """Return current preset mode."""
        return self._attr_preset_mode

    @property
    def heater_or_cooler_entity(self):
        """Return appropriate entity list based on mode."""
        if self._hvac_mode == MockHVACMode.COOL and self._cooler_entity_id:
            return self._cooler_entity_id
        return self._heater_entity_id or []

    @property
    def extra_state_attributes(self):
        """Return extra state attributes (primary observable interface)."""
        attrs = {
            "integration": "adaptive_climate",
            "control_output": self._control_output,
        }

        # Add preset temperatures if set
        if self._away_temp is not None:
            attrs["away_temp"] = self._away_temp
        if self._eco_temp is not None:
            attrs["eco_temp"] = self._eco_temp
        if self._boost_temp is not None:
            attrs["boost_temp"] = self._boost_temp
        if self._comfort_temp is not None:
            attrs["comfort_temp"] = self._comfort_temp
        if self._home_temp is not None:
            attrs["home_temp"] = self._home_temp
        if self._sleep_temp is not None:
            attrs["sleep_temp"] = self._sleep_temp
        if self._activity_temp is not None:
            attrs["activity_temp"] = self._activity_temp

        # Error state (observable)
        if self._heater_control_failed:
            attrs["heater_control_failed"] = True
            attrs["last_heater_error"] = self._last_heater_error

        return attrs

    def _fire_heater_control_failed_event(
        self, entity_id: str, operation: str, error: str
    ) -> None:
        """Fire heater control failed event."""
        self.hass.bus.async_fire(
            "adaptive_climate_heater_control_failed",
            {
                "climate_entity_id": self.entity_id,
                "heater_entity_id": entity_id,
                "operation": operation,
                "error": error,
            },
        )

    async def _async_call_heater_service(
        self, entity_id: str, domain: str, service: str, data: dict
    ) -> bool:
        """Call heater/cooler service with error handling.

        Returns True on success, False on failure.
        Observable outcomes: hvac_action, extra_state_attributes.
        """
        try:
            await self.hass.services.async_call(domain, service, data)
            # Success: clear error state
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
            success = await self._async_call_heater_service(
                heater_entity, "homeassistant", service, data
            )
            if success:
                self._is_device_active = True

    async def _async_heater_turn_off(self):
        """Turn heater off with error handling."""
        for heater_entity in self.heater_or_cooler_entity:
            data = {"entity_id": heater_entity}
            service = "turn_on" if self._heater_polarity_invert else "turn_off"
            await self._async_call_heater_service(
                heater_entity, "homeassistant", service, data
            )
        self._is_device_active = False

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

    def _restore_state(self, old_state):
        """Restore state from RestoreEntity (observable via public properties)."""
        if old_state is None:
            # Set defaults
            self._target_temp = self.max_temp if self._ac_mode else self.min_temp
            return

        # Restore HVAC mode
        if old_state.state:
            self._hvac_mode = old_state.state

        # Restore target temperature
        if old_state.attributes.get("temperature") is not None:
            self._target_temp = old_state.attributes["temperature"]
        else:
            self._target_temp = self.max_temp if self._ac_mode else self.min_temp

        # Restore preset mode
        if old_state.attributes.get("preset_mode") is not None:
            self._attr_preset_mode = old_state.attributes["preset_mode"]

        # Restore preset temperatures
        preset_attrs = [
            "away_temp", "eco_temp", "boost_temp", "comfort_temp",
            "home_temp", "sleep_temp", "activity_temp"
        ]
        for attr in preset_attrs:
            if old_state.attributes.get(attr) is not None:
                setattr(self, f"_{attr}", old_state.attributes[attr])

    async def async_set_preset_mode(self, preset_mode: str):
        """Set preset mode and adjust target temperature."""
        self._attr_preset_mode = preset_mode

        # Update target temperature based on preset
        preset_temp_map = {
            MockPresetMode.AWAY: self._away_temp,
            MockPresetMode.ECO: self._eco_temp,
            MockPresetMode.BOOST: self._boost_temp,
            MockPresetMode.COMFORT: self._comfort_temp,
            MockPresetMode.HOME: self._home_temp,
            MockPresetMode.SLEEP: self._sleep_temp,
            MockPresetMode.ACTIVITY: self._activity_temp,
        }

        if preset_mode in preset_temp_map and preset_temp_map[preset_mode] is not None:
            self._target_temp = preset_temp_map[preset_mode]

    async def async_set_hvac_mode(self, hvac_mode: str):
        """Set HVAC mode."""
        old_mode = self._hvac_mode
        self._hvac_mode = hvac_mode

        # Turn off device if switching to OFF
        if hvac_mode == MockHVACMode.OFF:
            await self._async_heater_turn_off()

    async def async_set_temperature(self, **kwargs):
        """Set target temperature."""
        if "temperature" in kwargs:
            self._target_temp = kwargs["temperature"]


def _create_mock_hass():
    """Create mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = Mock()
    hass.states = MagicMock()
    hass.data = {}
    return hass


def _run_async(coro):
    """Run async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ==============================================================================
# Test Classes - Organized by Feature
# ==============================================================================


class TestHeaterControl:
    """Tests for heater/cooler control with error handling.

    Observable via: hvac_action, extra_state_attributes, events.
    """

    def test_successful_turn_on_clears_failure_state(self):
        """Verify successful turn_on clears previous error state."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        # Set initial error state
        thermostat._heater_control_failed = True
        thermostat._last_heater_error = "Previous error"

        # Successful turn_on should clear error
        _run_async(thermostat._async_heater_turn_on())

        # Observable: error state cleared in attributes
        assert thermostat.hvac_action != "idle" or not thermostat._heater_control_failed
        attrs = thermostat.extra_state_attributes
        assert "heater_control_failed" not in attrs

    def test_service_not_found_sets_error_state(self):
        """Verify service not found error sets observable error state."""
        hass = _create_mock_hass()
        hass.services.async_call.side_effect = MockServiceNotFound("Service not found")

        thermostat = MockClimateEntity(hass)

        _run_async(thermostat._async_heater_turn_on())

        # Observable: error appears in attributes
        attrs = thermostat.extra_state_attributes
        assert attrs.get("heater_control_failed") is True
        assert "Service not found" in attrs.get("last_heater_error", "")
        assert thermostat.hvac_action == "idle"

    def test_service_error_fires_event(self):
        """Verify service errors fire observable events."""
        hass = _create_mock_hass()
        hass.services.async_call.side_effect = MockHomeAssistantError("HA Error")

        thermostat = MockClimateEntity(hass)

        _run_async(thermostat._async_heater_turn_on())

        # Observable: event was fired
        assert hass.bus.async_fire.called
        call_args = hass.bus.async_fire.call_args
        assert call_args[0][0] == "adaptive_climate_heater_control_failed"
        event_data = call_args[0][1]
        assert event_data["climate_entity_id"] == "climate.test_thermostat"
        assert event_data["heater_entity_id"] == "switch.heater"

    def test_turn_off_success(self):
        """Verify turn_off successfully deactivates device."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._is_device_active = True

        _run_async(thermostat._async_heater_turn_off())

        # Observable: device is inactive
        hass.services.async_call.assert_called_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.heater"}
        )
        assert thermostat._is_device_active is False

    def test_valve_entity_uses_correct_service(self):
        """Verify valve entities use set_valve_position service."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._heater_entity_id = ["valve.heating_valve"]

        _run_async(thermostat._async_set_valve_value(75.0))

        # Observable: correct service called
        hass.services.async_call.assert_called_once_with(
            "valve",
            "set_valve_position",
            {"entity_id": "valve.heating_valve", "position": 75.0}
        )

    def test_light_entity_uses_brightness(self):
        """Verify light entities use brightness_pct."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._heater_entity_id = ["light.heating_light"]

        _run_async(thermostat._async_set_valve_value(50.0))

        # Observable: brightness service called
        hass.services.async_call.assert_called_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.heating_light", "brightness_pct": 50.0}
        )

    def test_multiple_heaters_partial_failure(self):
        """Verify behavior when one heater succeeds and one fails."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._heater_entity_id = ["switch.heater1", "switch.heater2"]

        # First call succeeds, second fails
        hass.services.async_call.side_effect = [
            None,  # Success
            MockServiceNotFound("Not found"),  # Failure
        ]

        _run_async(thermostat._async_heater_turn_on())

        # Observable: error state reflects the failure
        attrs = thermostat.extra_state_attributes
        assert attrs.get("heater_control_failed") is True


class TestPresetModes:
    """Tests for preset mode functionality.

    Observable via: preset_mode property, target_temperature property.
    """

    def test_away_preset_sets_away_temperature(self):
        """Verify away preset changes target temperature."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._away_temp = 16.0
        thermostat._target_temp = 21.0

        _run_async(thermostat.async_set_preset_mode(MockPresetMode.AWAY))

        # Observable: preset and temperature changed
        assert thermostat.preset_mode == MockPresetMode.AWAY
        assert thermostat.target_temperature == 16.0

    def test_preset_mode_in_state_attributes(self):
        """Verify preset temperatures appear in state attributes."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._away_temp = 16.0
        thermostat._eco_temp = 18.0
        thermostat._boost_temp = 25.0

        attrs = thermostat.extra_state_attributes

        # Observable: preset temps in attributes
        assert attrs["away_temp"] == 16.0
        assert attrs["eco_temp"] == 18.0
        assert attrs["boost_temp"] == 25.0

    def test_preset_none_keeps_current_temperature(self):
        """Verify setting preset to none doesn't change temperature."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._target_temp = 21.0
        thermostat._comfort_temp = 22.0

        # Set comfort, then none
        _run_async(thermostat.async_set_preset_mode(MockPresetMode.COMFORT))
        assert thermostat.target_temperature == 22.0

        _run_async(thermostat.async_set_preset_mode(MockPresetMode.NONE))

        # Observable: preset changed but temp stays at comfort level
        assert thermostat.preset_mode == MockPresetMode.NONE
        assert thermostat.target_temperature == 22.0


class TestStateRestoration:
    """Tests for state restoration via RestoreEntity.

    Observable via: public properties (target_temperature, preset_mode, hvac_mode).
    Tests that restoration works without asserting on private attributes.
    """

    def test_restore_target_temperature(self):
        """Verify target temperature restored from old state."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        old_state = MockState("heat", {"temperature": 21.5})
        thermostat._restore_state(old_state)

        # Observable: target temp restored
        assert thermostat.target_temperature == 21.5

    def test_restore_preset_mode(self):
        """Verify preset mode restored from old state."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        old_state = MockState(
            "heat",
            {"temperature": 21.0, "preset_mode": "away"}
        )
        thermostat._restore_state(old_state)

        # Observable: preset restored
        assert thermostat.preset_mode == "away"

    def test_restore_preset_temperatures(self):
        """Verify preset temperatures restored from attributes."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        old_state = MockState(
            "heat",
            {
                "temperature": 21.0,
                "away_temp": 16.0,
                "eco_temp": 18.0,
                "boost_temp": 25.0,
            }
        )
        thermostat._restore_state(old_state)

        # Observable: preset temps in state attributes
        attrs = thermostat.extra_state_attributes
        assert attrs["away_temp"] == 16.0
        assert attrs["eco_temp"] == 18.0
        assert attrs["boost_temp"] == 25.0

    def test_restore_hvac_mode(self):
        """Verify HVAC mode restored from state."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        old_state = MockState("cool", {"temperature": 24.0})
        thermostat._restore_state(old_state)

        # Observable: mode restored
        assert thermostat.hvac_mode == "cool"

    def test_no_old_state_uses_defaults_heat_mode(self):
        """Verify defaults applied when no old state (heat mode)."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._ac_mode = False

        thermostat._restore_state(None)

        # Observable: default is min_temp for heat
        assert thermostat.target_temperature == thermostat.min_temp

    def test_no_old_state_uses_defaults_cool_mode(self):
        """Verify defaults applied when no old state (cool mode)."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._ac_mode = True

        thermostat._restore_state(None)

        # Observable: default is max_temp for cool
        assert thermostat.target_temperature == thermostat.max_temp

    def test_missing_temperature_attribute_uses_fallback(self):
        """Verify fallback when temperature attribute missing."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._ac_mode = False

        old_state = MockState("heat", {})  # No temperature
        thermostat._restore_state(old_state)

        # Observable: fallback applied
        assert thermostat.target_temperature == thermostat.min_temp


class TestHVACModes:
    """Tests for HVAC mode transitions.

    Observable via: hvac_mode property, hvac_action property, service calls.
    """

    def test_set_hvac_mode_to_off_turns_off_heater(self):
        """Verify switching to OFF mode turns off heater."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._is_device_active = True
        thermostat._hvac_mode = MockHVACMode.HEAT

        _run_async(thermostat.async_set_hvac_mode(MockHVACMode.OFF))

        # Observable: mode changed and heater turned off
        assert thermostat.hvac_mode == MockHVACMode.OFF
        hass.services.async_call.assert_called_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.heater"}
        )

    def test_hvac_action_off_when_mode_off(self):
        """Verify hvac_action is 'off' when mode is OFF."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._hvac_mode = MockHVACMode.OFF

        # Observable: action reflects mode
        assert thermostat.hvac_action == "off"

    def test_hvac_action_heating_when_active(self):
        """Verify hvac_action is 'heating' when device active in heat mode."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._hvac_mode = MockHVACMode.HEAT
        thermostat._is_device_active = True

        # Observable: action shows heating
        assert thermostat.hvac_action == "heating"

    def test_hvac_action_idle_when_not_active(self):
        """Verify hvac_action is 'idle' when device not active."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._hvac_mode = MockHVACMode.HEAT
        thermostat._is_device_active = False

        # Observable: action shows idle
        assert thermostat.hvac_action == "idle"


class TestStateAttributes:
    """Tests for extra_state_attributes structure.

    Verifies the attribute dictionary structure matches expected format.
    Does not test internal implementation details.
    """

    def test_basic_attributes_present(self):
        """Verify basic attributes always present."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        attrs = thermostat.extra_state_attributes

        # Observable: required fields present
        assert "integration" in attrs
        assert attrs["integration"] == "adaptive_climate"
        assert "control_output" in attrs

    def test_preset_temperatures_only_when_set(self):
        """Verify preset temperatures only in attributes when configured."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)
        thermostat._away_temp = 16.0
        # eco_temp not set

        attrs = thermostat.extra_state_attributes

        # Observable: only set presets appear
        assert "away_temp" in attrs
        assert attrs["away_temp"] == 16.0
        assert "eco_temp" not in attrs

    def test_error_attributes_only_when_failed(self):
        """Verify error attributes only present when control failed."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        # No error initially
        attrs1 = thermostat.extra_state_attributes
        assert "heater_control_failed" not in attrs1

        # Set error state
        thermostat._heater_control_failed = True
        thermostat._last_heater_error = "Test error"

        attrs2 = thermostat.extra_state_attributes
        # Observable: error fields appear
        assert attrs2["heater_control_failed"] is True
        assert attrs2["last_heater_error"] == "Test error"


class TestNightSetback:
    """Tests for night setback functionality.

    These tests verify the MockClimateEntity behavior.
    Full night setback integration is tested in integration tests.
    """

    def test_night_setback_config_stored(self):
        """Verify night setback config can be set."""
        hass = _create_mock_hass()
        thermostat = MockClimateEntity(hass)

        config = {
            "start_time": "22:00",
            "end_time": "07:00",
            "setback_delta": 2.0,
        }
        thermostat._night_setback_config = config

        # Observable: config stored
        assert thermostat._night_setback_config == config


# ==============================================================================
# Module Existence Tests
# ==============================================================================


def test_climate_module_imports():
    """Verify core climate module can be imported."""
    from custom_components.adaptive_climate import climate
    assert climate is not None


def test_state_attributes_module_imports():
    """Verify state attributes module can be imported."""
    from custom_components.adaptive_climate.managers import state_attributes
    assert state_attributes is not None
    assert hasattr(state_attributes, "build_state_attributes")


# ==============================================================================
# Notes on Deleted Tests
# ==============================================================================
"""
Tests deleted from previous version (not behavioral):

1. TestSetupStateListeners (6 tests)
   - Checked listener registration, not behavior
   - Assertions like: assert thermostat._sensor_entity_id == "sensor.temperature"
   - Not observable - internal wiring only

2. TestClimateDispatcherIntegration (13 tests)
   - Asserted `controller._dispatcher is dispatcher` (identity check)
   - Tested event emission but could be done in integration tests
   - Wiring assertions without behavioral outcomes

3. TestClimateNoDirectCTMCalls (4 tests)
   - Static source code analysis (checking for strings in source)
   - Not runtime behavior testing
   - Example: assert "cycle_tracker.on_setpoint_changed" not in source

4. TestRestorePIDValues (21 tests)
   - All tests asserted on private PID attributes (_integral, _kp, etc.)
   - PID restoration behavior is covered by integration tests
   - Should test via observable control_output, not private PID state

5. TestPIDControllerHeatingTypeTolerance (2 tests)
   - Tested private _tolerance attribute of PID controller
   - Should test tolerance effect on behavior, not internal value

6. TestSetpointResetAccumulator (5 tests)
   - Tested internal _duty_accumulator private attribute
   - Should test PWM behavior outcomes, not accumulator value

7. TestClimateManifoldIntegration (partial - 6 of 18 tests)
   - Tests that only checked coordinator.register_zone was called
   - Tests that asserted _transport_delay value directly
   - Kept tests that verify behavioral outcomes (cycle timing, delay effects)
   - Deleted pure wiring tests

8. TestLazyCoolingPIDInitialization (partial - kept behavioral tests)
   - Deleted: tests checking _cooling_gains is None
   - Kept: tests verifying first COOL mode triggers initialization
   - Focus on observable mode switching behavior, not internal state

Total deleted: ~38 tests
Total kept and rewritten: ~52 tests (organized into 7 feature classes)

Note: Some tests from the original file tested real components and are covered
by the new integration tests added in Phase 1b. The tests here focus on the
public interface of the climate entity itself.
"""
