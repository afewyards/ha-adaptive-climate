"""Tests for StatusManager."""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from custom_components.adaptive_thermostat.managers.status_manager import (
    StatusManager,
    calculate_resume_at,
    convert_setback_end,
)
from custom_components.adaptive_thermostat.adaptive.contact_sensors import ContactAction
from custom_components.adaptive_thermostat.const import ThermostatCondition


class TestCalculateResumeAt:
    """Test calculate_resume_at() function."""

    def test_none_input_returns_none(self):
        """Test that None input returns None."""
        result = calculate_resume_at(None)
        assert result is None

    def test_zero_returns_none(self):
        """Test that zero seconds returns None."""
        result = calculate_resume_at(0)
        assert result is None

    def test_negative_returns_none(self):
        """Test that negative seconds returns None."""
        result = calculate_resume_at(-10)
        assert result is None

    def test_positive_seconds_returns_iso8601(self):
        """Test that positive seconds returns future ISO8601 timestamp."""
        # Mock dt_util.utcnow to return a fixed time
        fixed_now = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.utcnow', return_value=fixed_now):
            result = calculate_resume_at(300)  # 5 minutes

        assert result is not None
        # Expected: 2024-01-15T10:35:00+00:00
        assert result == "2024-01-15T10:35:00+00:00"

    def test_large_duration(self):
        """Test calculation with larger duration."""
        # Mock dt_util.utcnow
        fixed_now = datetime(2024, 1, 15, 23, 45, 0, tzinfo=timezone.utc)

        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.utcnow', return_value=fixed_now):
            result = calculate_resume_at(3600)  # 1 hour

        assert result is not None
        # Expected: 2024-01-16T00:45:00+00:00 (crosses midnight)
        assert result == "2024-01-16T00:45:00+00:00"


class TestStatusInfoTypedDict:
    """Test StatusInfo TypedDict structure."""

    def test_minimal_status_info_structure(self):
        """Test StatusInfo can be constructed with minimal required fields."""
        from custom_components.adaptive_thermostat.managers.status_manager import StatusInfo

        # Minimal fields: state and conditions
        status: StatusInfo = {
            "state": "idle",
            "conditions": [],
        }

        assert status["state"] == "idle"
        assert status["conditions"] == []
        assert isinstance(status["conditions"], list)

    def test_status_info_with_optional_fields(self):
        """Test StatusInfo can include optional fields."""
        from custom_components.adaptive_thermostat.managers.status_manager import StatusInfo

        status: StatusInfo = {
            "state": "paused",
            "conditions": ["contact_open"],
            "resume_at": "2024-01-15T10:30:00+00:00",
            "setback_delta": -2.0,
            "setback_end": "2024-01-16T07:00:00+00:00",
        }

        assert status["state"] == "paused"
        assert status["conditions"] == ["contact_open"]
        assert status["resume_at"] == "2024-01-15T10:30:00+00:00"
        assert status["setback_delta"] == -2.0
        assert status["setback_end"] == "2024-01-16T07:00:00+00:00"

    def test_status_info_with_debug_fields(self):
        """Test StatusInfo can include debug fields."""
        from custom_components.adaptive_thermostat.managers.status_manager import StatusInfo

        status: StatusInfo = {
            "state": "paused",
            "conditions": ["humidity_spike"],
            "humidity_peak": 85.5,
            "open_sensors": ["binary_sensor.window_1", "binary_sensor.window_2"],
        }

        assert status["humidity_peak"] == 85.5
        assert status["open_sensors"] == ["binary_sensor.window_1", "binary_sensor.window_2"]

    def test_status_info_field_types(self):
        """Test StatusInfo fields have correct types."""
        from custom_components.adaptive_thermostat.managers.status_manager import StatusInfo

        status: StatusInfo = {
            "state": "settling",
            "conditions": ["night_setback", "learning_grace"],
            "resume_at": "2024-01-15T11:00:00+00:00",
            "setback_delta": -1.5,
            "setback_end": "2024-01-16T08:00:00+00:00",
            "humidity_peak": 78.2,
            "open_sensors": ["binary_sensor.door"],
        }

        # Verify types
        assert isinstance(status["state"], str)
        assert isinstance(status["conditions"], list)
        assert all(isinstance(c, str) for c in status["conditions"])
        assert isinstance(status["resume_at"], str)
        assert isinstance(status["setback_delta"], float)
        assert isinstance(status["setback_end"], str)
        assert isinstance(status["humidity_peak"], float)
        assert isinstance(status["open_sensors"], list)
        assert all(isinstance(s, str) for s in status["open_sensors"])


class TestThermostatConditionEnum:
    """Test ThermostatCondition enum values."""

    def test_enum_values_exist(self):
        """Test that all expected ThermostatCondition enum values are defined."""
        assert hasattr(ThermostatCondition, "CONTACT_OPEN")
        assert hasattr(ThermostatCondition, "HUMIDITY_SPIKE")
        assert hasattr(ThermostatCondition, "OPEN_WINDOW")
        assert hasattr(ThermostatCondition, "NIGHT_SETBACK")
        assert hasattr(ThermostatCondition, "LEARNING_GRACE")

    def test_enum_string_values(self):
        """Test that enum values have expected string representations."""
        assert ThermostatCondition.CONTACT_OPEN == "contact_open"
        assert ThermostatCondition.HUMIDITY_SPIKE == "humidity_spike"
        assert ThermostatCondition.OPEN_WINDOW == "open_window"
        assert ThermostatCondition.NIGHT_SETBACK == "night_setback"
        assert ThermostatCondition.LEARNING_GRACE == "learning_grace"

    def test_enum_is_string(self):
        """Test that ThermostatCondition values are strings (StrEnum)."""
        assert isinstance(ThermostatCondition.CONTACT_OPEN, str)
        assert isinstance(ThermostatCondition.HUMIDITY_SPIKE, str)
        assert isinstance(ThermostatCondition.OPEN_WINDOW, str)
        assert isinstance(ThermostatCondition.NIGHT_SETBACK, str)
        assert isinstance(ThermostatCondition.LEARNING_GRACE, str)


class TestConvertSetbackEnd:
    """Test convert_setback_end() function."""

    def test_none_input_returns_none(self):
        """Test that None input returns None."""
        result = convert_setback_end(None)
        assert result is None

    def test_future_time_today(self):
        """Test end time that hasn't passed yet returns today's date."""
        # Current time: 06:00, end time: 07:00 → should return today at 07:00
        fixed_now = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)

        result = convert_setback_end("07:00", now=fixed_now)

        assert result is not None
        # Expected: 2024-01-15T07:00:00+00:00 (today)
        assert result == "2024-01-15T07:00:00+00:00"

    def test_past_time_returns_tomorrow(self):
        """Test end time that already passed today returns tomorrow's date."""
        # Current time: 08:00, end time: 07:00 → should return tomorrow at 07:00
        fixed_now = datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc)

        result = convert_setback_end("07:00", now=fixed_now)

        assert result is not None
        # Expected: 2024-01-16T07:00:00+00:00 (tomorrow)
        assert result == "2024-01-16T07:00:00+00:00"

    def test_exact_current_time_returns_tomorrow(self):
        """Test end time equal to current time returns tomorrow."""
        # Current time: 07:00:00, end time: 07:00 → should return tomorrow
        fixed_now = datetime(2024, 1, 15, 7, 0, 0, tzinfo=timezone.utc)

        result = convert_setback_end("07:00", now=fixed_now)

        assert result is not None
        # Expected: 2024-01-16T07:00:00+00:00 (tomorrow, since time <= now)
        assert result == "2024-01-16T07:00:00+00:00"

    def test_invalid_format_returns_none(self):
        """Test invalid time format returns None."""
        fixed_now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

        result = convert_setback_end("not_a_time", now=fixed_now)
        assert result is None

        result = convert_setback_end("25:00", now=fixed_now)
        assert result is None

        result = convert_setback_end("12", now=fixed_now)
        assert result is None

    def test_crosses_month_boundary(self):
        """Test that tomorrow calculation works across month boundary."""
        # End of month: Jan 31 at 23:00, end time: 07:00 → Feb 1 at 07:00
        fixed_now = datetime(2024, 1, 31, 23, 0, 0, tzinfo=timezone.utc)

        result = convert_setback_end("07:00", now=fixed_now)

        assert result is not None
        # Expected: 2024-02-01T07:00:00+00:00 (next month)
        assert result == "2024-02-01T07:00:00+00:00"

    def test_uses_now_when_now_not_provided(self):
        """Test that function uses dt_util.now() when now parameter is None."""
        # Don't provide now parameter - should use dt_util.now()
        fixed_now = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)

        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.now', return_value=fixed_now):
            result = convert_setback_end("07:00")

        assert result is not None
        assert "T07:00:00" in result  # Should contain the time we specified


class TestThermostatState:
    """Test ThermostatState enum."""

    def test_enum_values_exist(self):
        """Test that all required ThermostatState values exist."""
        from custom_components.adaptive_thermostat.const import ThermostatState
        assert ThermostatState.IDLE == "idle"
        assert ThermostatState.HEATING == "heating"
        assert ThermostatState.COOLING == "cooling"
        assert ThermostatState.PAUSED == "paused"
        assert ThermostatState.PREHEATING == "preheating"
        assert ThermostatState.SETTLING == "settling"

    def test_enum_is_string(self):
        """Test that ThermostatState enum values are strings."""
        from custom_components.adaptive_thermostat.const import ThermostatState
        assert isinstance(ThermostatState.IDLE, str)
        assert isinstance(ThermostatState.HEATING, str)
        assert isinstance(ThermostatState.COOLING, str)
        assert isinstance(ThermostatState.PAUSED, str)
        assert isinstance(ThermostatState.PREHEATING, str)
        assert isinstance(ThermostatState.SETTLING, str)

    def test_enum_str_conversion(self):
        """Test that str() returns the enum value."""
        from custom_components.adaptive_thermostat.const import ThermostatState
        assert str(ThermostatState.IDLE) == "idle"
        assert str(ThermostatState.HEATING) == "heating"
        assert str(ThermostatState.COOLING) == "cooling"
        assert str(ThermostatState.PAUSED) == "paused"
        assert str(ThermostatState.PREHEATING) == "preheating"
        assert str(ThermostatState.SETTLING) == "settling"

class TestFormatIso8601:
    """Test ISO8601 formatting helper function."""

    def test_format_timezone_aware_datetime(self):
        """Test formatting timezone-aware datetime to ISO8601."""
        from custom_components.adaptive_thermostat.managers.status_manager import format_iso8601

        # Create timezone-aware datetime
        dt = datetime(2024, 1, 15, 14, 30, 45, 123456, tzinfo=timezone.utc)
        result = format_iso8601(dt)

        # Should produce ISO8601 format
        assert result == "2024-01-15T14:30:45.123456+00:00"
        assert isinstance(result, str)

    def test_format_different_timezone(self):
        """Test formatting datetime with non-UTC timezone."""
        from datetime import timedelta
        from custom_components.adaptive_thermostat.managers.status_manager import format_iso8601

        # Create datetime with UTC+5 timezone
        tz = timezone(timedelta(hours=5))
        dt = datetime(2024, 6, 20, 10, 15, 30, tzinfo=tz)
        result = format_iso8601(dt)

        # Should include timezone offset
        assert result == "2024-06-20T10:15:30+05:00"
        assert "+05:00" in result


class TestBuildConditions:
    """Test build_conditions() function."""

    def test_no_conditions_active(self):
        """Test that no active conditions returns empty list."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions()
        assert result == []
        assert isinstance(result, list)

    def test_night_setback_active(self):
        """Test night setback active returns correct condition."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(night_setback_active=True)
        assert result == ["night_setback"]
        assert len(result) == 1

    def test_open_window_detected(self):
        """Test open window detected returns correct condition."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(open_window_detected=True)
        assert result == ["open_window"]
        assert len(result) == 1

    def test_multiple_conditions_order_matters(self):
        """Test that multiple conditions are returned in priority order."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(
            night_setback_active=True,
            open_window_detected=True
        )
        # Both should be present
        assert len(result) == 2
        # Open window should come before night setback
        assert result == ["open_window", "night_setback"]

    def test_explicit_false_values(self):
        """Test that explicitly False values don't add conditions."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(
            night_setback_active=False,
            open_window_detected=False,
            humidity_spike_active=False,
            contact_open=False,
            learning_grace_active=False
        )
        assert result == []

    def test_returns_enum_values_as_strings(self):
        """Test that returned conditions are string values from ThermostatCondition enum."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(night_setback_active=True)
        # Should return string value, not enum object
        assert isinstance(result[0], str)
        assert result[0] == ThermostatCondition.NIGHT_SETBACK.value
        assert result[0] == "night_setback"

    def test_open_window_returns_enum_value(self):
        """Test that open_window returns the correct enum string value."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(open_window_detected=True)
        assert isinstance(result[0], str)
        assert result[0] == ThermostatCondition.OPEN_WINDOW.value
        assert result[0] == "open_window"

    def test_humidity_spike_active(self):
        """Test humidity spike active returns correct condition."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(humidity_spike_active=True)
        assert result == ["humidity_spike"]
        assert len(result) == 1

    def test_contact_open(self):
        """Test contact open returns correct condition."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(contact_open=True)
        assert result == ["contact_open"]
        assert len(result) == 1

    def test_all_four_conditions_returns_correct_order(self):
        """Test all four conditions returns in priority order: contact, humidity, open_window, night_setback."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(
            contact_open=True,
            humidity_spike_active=True,
            open_window_detected=True,
            night_setback_active=True
        )
        assert len(result) == 4
        assert result == ["contact_open", "humidity_spike", "open_window", "night_setback"]

    def test_contact_and_night_setback(self):
        """Test contact open + night setback returns both in correct order."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(
            contact_open=True,
            night_setback_active=True
        )
        assert len(result) == 2
        assert result == ["contact_open", "night_setback"]

    def test_learning_grace_active(self):
        """Test learning grace active returns correct condition."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(learning_grace_active=True)
        assert result == ["learning_grace"]
        assert len(result) == 1

    def test_all_five_conditions_returns_correct_order(self):
        """Test all five conditions returns in priority order: contact, humidity, open_window, night_setback, learning_grace."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(
            contact_open=True,
            humidity_spike_active=True,
            open_window_detected=True,
            night_setback_active=True,
            learning_grace_active=True
        )
        assert len(result) == 5
        assert result == ["contact_open", "humidity_spike", "open_window", "night_setback", "learning_grace"]

    def test_night_setback_and_learning_grace(self):
        """Test night setback + learning grace returns both in correct order."""
        from custom_components.adaptive_thermostat.managers.status_manager import build_conditions

        result = build_conditions(
            night_setback_active=True,
            learning_grace_active=True
        )
        assert len(result) == 2
        assert result == ["night_setback", "learning_grace"]


class TestDeriveState:
    """Test derive_state() function for determining operational state."""

    def test_hvac_off_returns_idle(self):
        """Test that HVAC mode off returns idle state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="off")
        assert result == ThermostatState.IDLE

    def test_hvac_off_with_heater_on_returns_idle(self):
        """Test that HVAC off overrides heater state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="off", heater_on=True)
        assert result == ThermostatState.IDLE

    def test_heater_on_returns_heating(self):
        """Test that heater on returns heating state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", heater_on=True)
        assert result == ThermostatState.HEATING

    def test_heater_off_returns_idle(self):
        """Test that heater off returns idle state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", heater_on=False)
        assert result == ThermostatState.IDLE

    def test_heat_mode_no_heater_state_returns_idle(self):
        """Test that heat mode with no heater state returns idle."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat")
        assert result == ThermostatState.IDLE

    def test_paused_overrides_heating(self):
        """Test that paused state takes priority over heater on."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", heater_on=True, is_paused=True)
        assert result == ThermostatState.PAUSED

    def test_paused_overrides_cooling(self):
        """Test that paused state takes priority over cooler on."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="cool", cooler_on=True, is_paused=True)
        assert result == ThermostatState.PAUSED

    def test_paused_without_active_heater_or_cooler(self):
        """Test that paused is returned even without active heater/cooler."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", is_paused=True)
        assert result == ThermostatState.PAUSED

    def test_cooler_on_returns_cooling(self):
        """Test that cooler on returns cooling state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="cool", cooler_on=True)
        assert result == ThermostatState.COOLING

    def test_cooler_off_returns_idle(self):
        """Test that cooler off returns idle state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="cool", cooler_on=False)
        assert result == ThermostatState.IDLE

    def test_cool_mode_no_cooler_state_returns_idle(self):
        """Test that cool mode with no cooler state returns idle."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="cool")
        assert result == ThermostatState.IDLE

    def test_preheat_active_returns_preheating(self):
        """Test that preheat_active=True returns preheating state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", preheat_active=True)
        assert result == ThermostatState.PREHEATING

    def test_preheat_with_pause_returns_paused(self):
        """Test that pause takes priority over preheat."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", preheat_active=True, is_paused=True)
        assert result == ThermostatState.PAUSED

    def test_cycle_settling_returns_settling(self):
        """Test that cycle_state='settling' returns settling state."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", cycle_state="settling")
        assert result == ThermostatState.SETTLING

    def test_preheat_priority_over_settling(self):
        """Test that preheat takes priority over settling."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", preheat_active=True, cycle_state="settling")
        assert result == ThermostatState.PREHEATING

    def test_cycle_heating_with_heater_on_returns_heating(self):
        """Test that cycle_state='heating' with heater on returns heating (not settling)."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", heater_on=True, cycle_state="heating")
        assert result == ThermostatState.HEATING

    def test_settling_priority_over_heater_on(self):
        """Test that settling state takes priority over heater on."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", heater_on=True, cycle_state="settling")
        assert result == ThermostatState.SETTLING

    def test_preheat_with_heater_on_returns_preheating(self):
        """Test that preheat takes priority over heater on."""
        from custom_components.adaptive_thermostat.managers.status_manager import derive_state
        from custom_components.adaptive_thermostat.const import ThermostatState

        result = derive_state(hvac_mode="heat", preheat_active=True, heater_on=True)
        assert result == ThermostatState.PREHEATING


class TestStatusManagerBuildStatus:
    """Test StatusManager.build_status() method."""

    def test_minimal_status_with_hvac_off(self):
        """Test minimal status with just hvac_mode='off'."""
        manager = StatusManager()

        result = manager.build_status(hvac_mode="off")

        assert result["state"] == "idle"
        assert result["conditions"] == []
        assert "resume_at" not in result
        assert "setback_delta" not in result
        assert "setback_end" not in result

    def test_status_with_heater_on(self):
        """Test status with heater_on=True returns heating state."""
        manager = StatusManager()

        result = manager.build_status(hvac_mode="heat", heater_on=True)

        assert result["state"] == "heating"
        assert result["conditions"] == []

    def test_status_with_night_setback_condition(self):
        """Test status with night_setback_active=True includes condition."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            night_setback_active=True
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["night_setback"]

    def test_status_with_resume_in_seconds(self):
        """Test status with resume_in_seconds includes resume_at ISO8601."""
        manager = StatusManager()

        fixed_now = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.utcnow', return_value=fixed_now):
            result = manager.build_status(
                hvac_mode="heat",
                is_paused=True,
                resume_in_seconds=300  # 5 minutes
            )

        assert result["state"] == "paused"
        assert result["resume_at"] == "2024-01-15T10:35:00+00:00"

    def test_status_with_setback_delta(self):
        """Test status with setback_delta includes the value."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            night_setback_active=True,
            setback_delta=-2.0
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["night_setback"]
        assert result["setback_delta"] == -2.0

    def test_status_with_setback_end_time(self):
        """Test status with setback_end_time includes setback_end ISO8601."""
        manager = StatusManager()

        fixed_now = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.now', return_value=fixed_now):
            result = manager.build_status(
                hvac_mode="heat",
                night_setback_active=True,
                setback_end_time="07:00"
            )

        assert result["state"] == "idle"
        assert result["conditions"] == ["night_setback"]
        assert result["setback_end"] == "2024-01-15T07:00:00+00:00"

    def test_status_with_multiple_conditions(self):
        """Test status with multiple conditions in correct priority order."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            contact_open=True,
            humidity_spike_active=True,
            night_setback_active=True
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["contact_open", "humidity_spike", "night_setback"]

    def test_status_with_all_optional_fields(self):
        """Test status with all optional fields populated."""
        manager = StatusManager()

        fixed_now = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.utcnow', return_value=fixed_now):
            with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.now', return_value=fixed_now):
                result = manager.build_status(
                    hvac_mode="heat",
                    heater_on=True,
                    is_paused=True,
                    night_setback_active=True,
                    contact_open=True,
                    resume_in_seconds=180,
                    setback_delta=-2.5,
                    setback_end_time="07:00"
                )

        assert result["state"] == "paused"  # Paused takes priority
        assert "contact_open" in result["conditions"]
        assert "night_setback" in result["conditions"]
        assert result["resume_at"] == "2024-01-15T06:03:00+00:00"
        assert result["setback_delta"] == -2.5
        assert result["setback_end"] == "2024-01-15T07:00:00+00:00"

    def test_status_with_zero_resume_in_not_included(self):
        """Test that resume_in_seconds=0 doesn't add resume_at."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            resume_in_seconds=0
        )

        assert "resume_at" not in result

    def test_status_with_negative_resume_in_not_included(self):
        """Test that negative resume_in_seconds doesn't add resume_at."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            resume_in_seconds=-10
        )

        assert "resume_at" not in result

    def test_status_with_preheat_active(self):
        """Test status with preheat_active returns preheating state."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            preheat_active=True
        )

        assert result["state"] == "preheating"
        assert result["conditions"] == []

    def test_status_with_cycle_settling(self):
        """Test status with cycle_state='settling' returns settling state."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            cycle_state="settling"
        )

        assert result["state"] == "settling"
        assert result["conditions"] == []

    def test_status_with_learning_grace(self):
        """Test status with learning_grace_active includes condition."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            learning_grace_active=True
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["learning_grace"]

    def test_debug_false_excludes_humidity_peak(self):
        """Test that debug=False excludes humidity_peak from result."""
        manager = StatusManager(debug=False)

        result = manager.build_status(
            hvac_mode="heat",
            humidity_spike_active=True,
            humidity_peak=85.5
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["humidity_spike"]
        assert "humidity_peak" not in result

    def test_debug_false_excludes_open_sensors(self):
        """Test that debug=False excludes open_sensors from result."""
        manager = StatusManager(debug=False)

        result = manager.build_status(
            hvac_mode="heat",
            contact_open=True,
            open_sensors=["binary_sensor.window"]
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["contact_open"]
        assert "open_sensors" not in result

    def test_debug_true_includes_humidity_peak(self):
        """Test that debug=True includes humidity_peak in result."""
        manager = StatusManager(debug=True)

        result = manager.build_status(
            hvac_mode="heat",
            humidity_spike_active=True,
            humidity_peak=85.5
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["humidity_spike"]
        assert result["humidity_peak"] == 85.5

    def test_debug_true_includes_open_sensors(self):
        """Test that debug=True includes open_sensors in result."""
        manager = StatusManager(debug=True)

        result = manager.build_status(
            hvac_mode="heat",
            contact_open=True,
            open_sensors=["binary_sensor.window"]
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["contact_open"]
        assert result["open_sensors"] == ["binary_sensor.window"]

    def test_debug_true_excludes_empty_open_sensors(self):
        """Test that debug=True excludes empty open_sensors list."""
        manager = StatusManager(debug=True)

        result = manager.build_status(
            hvac_mode="heat",
            contact_open=True,
            open_sensors=[]
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["contact_open"]
        assert "open_sensors" not in result

    def test_debug_true_includes_multiple_debug_fields(self):
        """Test that debug=True includes both humidity_peak and open_sensors."""
        manager = StatusManager(debug=True)

        result = manager.build_status(
            hvac_mode="heat",
            humidity_spike_active=True,
            contact_open=True,
            humidity_peak=88.0,
            open_sensors=["binary_sensor.window_1", "binary_sensor.window_2"]
        )

        assert result["state"] == "idle"
        assert result["conditions"] == ["contact_open", "humidity_spike"]
        assert result["humidity_peak"] == 88.0
        assert result["open_sensors"] == ["binary_sensor.window_1", "binary_sensor.window_2"]
