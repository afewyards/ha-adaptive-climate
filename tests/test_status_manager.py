"""Tests for StatusManager."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from custom_components.adaptive_climate.managers.status_manager import (
    StatusManager,
    calculate_resume_at,
    convert_setback_end,
)
from custom_components.adaptive_climate.adaptive.contact_sensors import ContactAction
from custom_components.adaptive_climate.const import ThermostatCondition


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

        with patch("custom_components.adaptive_climate.managers.status_manager.dt_util.utcnow", return_value=fixed_now):
            result = calculate_resume_at(300)  # 5 minutes

        assert result is not None
        # Expected: 2024-01-15T10:35:00+00:00
        assert result == "2024-01-15T10:35:00+00:00"

    def test_large_duration(self):
        """Test calculation with larger duration."""
        # Mock dt_util.utcnow
        fixed_now = datetime(2024, 1, 15, 23, 45, 0, tzinfo=timezone.utc)

        with patch("custom_components.adaptive_climate.managers.status_manager.dt_util.utcnow", return_value=fixed_now):
            result = calculate_resume_at(3600)  # 1 hour

        assert result is not None
        # Expected: 2024-01-16T00:45:00+00:00 (crosses midnight)
        assert result == "2024-01-16T00:45:00+00:00"


class TestStatusInfoTypedDict:
    """Test StatusInfo TypedDict structure."""

    def test_minimal_status_info_structure(self):
        """Test StatusInfo can be constructed with minimal required fields."""
        from custom_components.adaptive_climate.managers.status_manager import StatusInfo

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
        from custom_components.adaptive_climate.managers.status_manager import StatusInfo

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
        from custom_components.adaptive_climate.managers.status_manager import StatusInfo

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
        from custom_components.adaptive_climate.managers.status_manager import StatusInfo

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

        with patch("custom_components.adaptive_climate.managers.status_manager.dt_util.now", return_value=fixed_now):
            result = convert_setback_end("07:00")

        assert result is not None
        assert "T07:00:00" in result  # Should contain the time we specified


class TestThermostatState:
    """Test ThermostatState enum."""

    def test_enum_values_exist(self):
        """Test that all required ThermostatState values exist."""
        from custom_components.adaptive_climate.const import ThermostatState

        assert ThermostatState.IDLE == "idle"
        assert ThermostatState.HEATING == "heating"
        assert ThermostatState.COOLING == "cooling"
        assert ThermostatState.SETTLING == "settling"

    def test_enum_is_string(self):
        """Test that ThermostatState enum values are strings."""
        from custom_components.adaptive_climate.const import ThermostatState

        assert isinstance(ThermostatState.IDLE, str)
        assert isinstance(ThermostatState.HEATING, str)
        assert isinstance(ThermostatState.COOLING, str)
        assert isinstance(ThermostatState.SETTLING, str)

    def test_enum_str_conversion(self):
        """Test that str() returns the enum value."""
        from custom_components.adaptive_climate.const import ThermostatState

        assert str(ThermostatState.IDLE) == "idle"
        assert str(ThermostatState.HEATING) == "heating"
        assert str(ThermostatState.COOLING) == "cooling"
        assert str(ThermostatState.SETTLING) == "settling"


class TestFormatIso8601:
    """Test ISO8601 formatting helper function."""

    def test_format_timezone_aware_datetime(self):
        """Test formatting timezone-aware datetime to ISO8601."""
        from custom_components.adaptive_climate.managers.status_manager import format_iso8601

        # Create timezone-aware datetime
        dt = datetime(2024, 1, 15, 14, 30, 45, 123456, tzinfo=timezone.utc)
        result = format_iso8601(dt)

        # Should produce ISO8601 format
        assert result == "2024-01-15T14:30:45.123456+00:00"
        assert isinstance(result, str)

    def test_format_different_timezone(self):
        """Test formatting datetime with non-UTC timezone."""
        from datetime import timedelta
        from custom_components.adaptive_climate.managers.status_manager import format_iso8601

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
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions()
        assert result == []
        assert isinstance(result, list)

    def test_night_setback_active(self):
        """Test night setback active returns correct condition."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(night_setback_active=True)
        assert result == ["night_setback"]
        assert len(result) == 1

    def test_open_window_detected(self):
        """Test open window detected returns correct condition."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(open_window_detected=True)
        assert result == ["open_window"]
        assert len(result) == 1

    def test_multiple_conditions_order_matters(self):
        """Test that multiple conditions are returned in priority order."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(night_setback_active=True, open_window_detected=True)
        # Both should be present
        assert len(result) == 2
        # Open window should come before night setback
        assert result == ["open_window", "night_setback"]

    def test_explicit_false_values(self):
        """Test that explicitly False values don't add conditions."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(
            night_setback_active=False,
            open_window_detected=False,
            humidity_spike_active=False,
            contact_open=False,
            learning_grace_active=False,
        )
        assert result == []

    def test_returns_enum_values_as_strings(self):
        """Test that returned conditions are string values from ThermostatCondition enum."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(night_setback_active=True)
        # Should return string value, not enum object
        assert isinstance(result[0], str)
        assert result[0] == ThermostatCondition.NIGHT_SETBACK.value
        assert result[0] == "night_setback"

    def test_open_window_returns_enum_value(self):
        """Test that open_window returns the correct enum string value."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(open_window_detected=True)
        assert isinstance(result[0], str)
        assert result[0] == ThermostatCondition.OPEN_WINDOW.value
        assert result[0] == "open_window"

    def test_humidity_spike_active(self):
        """Test humidity spike active returns correct condition."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(humidity_spike_active=True)
        assert result == ["humidity_spike"]
        assert len(result) == 1

    def test_contact_open(self):
        """Test contact open returns correct condition."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(contact_open=True)
        assert result == ["contact_open"]
        assert len(result) == 1

    def test_all_four_conditions_returns_correct_order(self):
        """Test all four conditions returns in priority order: contact, humidity, open_window, night_setback."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(
            contact_open=True, humidity_spike_active=True, open_window_detected=True, night_setback_active=True
        )
        assert len(result) == 4
        assert result == ["contact_open", "humidity_spike", "open_window", "night_setback"]

    def test_contact_and_night_setback(self):
        """Test contact open + night setback returns both in correct order."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(contact_open=True, night_setback_active=True)
        assert len(result) == 2
        assert result == ["contact_open", "night_setback"]

    def test_learning_grace_active(self):
        """Test learning grace active returns correct condition."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(learning_grace_active=True)
        assert result == ["learning_grace"]
        assert len(result) == 1

    def test_all_five_conditions_returns_correct_order(self):
        """Test all five conditions returns in correct priority order."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(
            contact_open=True,
            humidity_spike_active=True,
            open_window_detected=True,
            night_setback_active=True,
            learning_grace_active=True,
        )
        assert len(result) == 5
        assert result == ["contact_open", "humidity_spike", "open_window", "night_setback", "learning_grace"]

    def test_night_setback_and_learning_grace(self):
        """Test night setback + learning grace returns both in correct order."""
        from custom_components.adaptive_climate.managers.status_manager import build_conditions

        result = build_conditions(night_setback_active=True, learning_grace_active=True)
        assert len(result) == 2
        assert result == ["night_setback", "learning_grace"]


class TestDeriveState:
    """Test derive_state() function for determining operational state."""

    def test_hvac_off_returns_idle(self):
        """Test that HVAC mode off returns idle state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="off")
        assert result == "idle"

    def test_hvac_off_with_heater_on_returns_idle(self):
        """Test that HVAC off overrides heater state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="off", heater_on=True)
        assert result == "idle"

    def test_heater_on_returns_heating(self):
        """Test that heater on returns heating state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat", heater_on=True)
        assert result == "heating"

    def test_heater_off_returns_idle(self):
        """Test that heater off returns idle state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat", heater_on=False)
        assert result == "idle"

    def test_heat_mode_no_heater_state_returns_idle(self):
        """Test that heat mode with no heater state returns idle."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat")
        assert result == "idle"

    def test_paused_no_longer_affects_activity(self):
        """Test that paused state no longer affects activity (it's now an override)."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        # Activity shows what system would be doing (heating), pause is handled by overrides
        result = derive_state(hvac_mode="heat", heater_on=True)
        assert result == "heating"

    def test_paused_with_cooling_no_longer_affects_activity(self):
        """Test that paused state no longer affects activity for cooling."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        # Activity shows what system would be doing (cooling), pause is handled by overrides
        result = derive_state(hvac_mode="cool", cooler_on=True)
        assert result == "cooling"

    def test_paused_without_heater_shows_idle(self):
        """Test that without heater on, activity is idle (pause doesn't create activity)."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat")
        assert result == "idle"

    def test_cooler_on_returns_cooling(self):
        """Test that cooler on returns cooling state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="cool", cooler_on=True)
        assert result == "cooling"

    def test_cooler_off_returns_idle(self):
        """Test that cooler off returns idle state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="cool", cooler_on=False)
        assert result == "idle"

    def test_cool_mode_no_cooler_state_returns_idle(self):
        """Test that cool mode with no cooler state returns idle."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="cool")
        assert result == "idle"

    def test_preheat_returns_preheating_activity(self):
        """Test that preheat_active returns preheating activity."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        # Preheating is a valid activity state
        result = derive_state(hvac_mode="heat", preheat_active=True)
        assert result == "preheating"

    def test_preheat_overrides_heater_on(self):
        """Test that preheating activity overrides heater state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        # Preheat takes priority over heater state
        result = derive_state(hvac_mode="heat", preheat_active=True, heater_on=True)
        assert result == "preheating"

    def test_cycle_settling_returns_settling(self):
        """Test that cycle_state='settling' returns settling state."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state
        from custom_components.adaptive_climate.const import ThermostatState

        result = derive_state(hvac_mode="heat", cycle_state="settling")
        assert result == ThermostatState.SETTLING

    def test_settling_with_no_heater(self):
        """Test that settling state is shown when cycle is settling."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat", cycle_state="settling")
        assert result == "settling"

    def test_cycle_heating_with_heater_on_returns_heating(self):
        """Test that cycle_state='heating' with heater on returns heating (not settling)."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat", heater_on=True, cycle_state="heating")
        assert result == "heating"

    def test_settling_priority_over_heater_on(self):
        """Test that settling state takes priority over heater on."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat", heater_on=True, cycle_state="settling")
        assert result == "settling"

    def test_preheat_priority_over_settling(self):
        """Test that preheat takes priority over settling."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        result = derive_state(hvac_mode="heat", preheat_active=True, cycle_state="settling")
        assert result == "preheating"


class TestStatusManagerBuildStatus:
    """Test StatusManager.build_status() method."""

    def test_minimal_status_with_hvac_off(self):
        """Test minimal status with just hvac_mode='off'."""
        manager = StatusManager()

        result = manager.build_status(hvac_mode="off")

        assert result["activity"] == "idle"
        assert result["overrides"] == []

    def test_status_with_heater_on(self):
        """Test status with heater_on=True returns heating activity."""
        manager = StatusManager()

        result = manager.build_status(hvac_mode="heat", heater_on=True)

        assert result["activity"] == "heating"
        assert result["overrides"] == []

    def test_status_with_night_setback_override(self):
        """Test status with night_setback_active=True includes override."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat", night_setback_active=True, night_setback_delta=-2.0, night_setback_ends_at="07:00"
        )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "night_setback"

    def test_status_with_contact_open_override(self):
        """Test status with contact_open includes override with resume_at."""
        manager = StatusManager()

        fixed_now = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        with patch("custom_components.adaptive_climate.managers.status_manager.dt_util.utcnow", return_value=fixed_now):
            result = manager.build_status(
                hvac_mode="heat",
                contact_open=True,
                contact_sensors=["binary_sensor.window"],
                contact_since="2024-01-15T10:30:00+00:00",
            )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "contact_open"
        assert result["overrides"][0]["sensors"] == ["binary_sensor.window"]

    def test_status_with_night_setback_delta(self):
        """Test status with setback_delta includes the value in override."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat", night_setback_active=True, night_setback_delta=-2.0, night_setback_ends_at="07:00"
        )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "night_setback"
        assert result["overrides"][0]["delta"] == -2.0

    def test_status_with_night_setback_ends_at(self):
        """Test status with setback_end_time includes ends_at in override."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat", night_setback_active=True, night_setback_delta=-2.0, night_setback_ends_at="07:00"
        )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "night_setback"
        assert result["overrides"][0]["ends_at"] == "07:00"

    def test_status_with_multiple_overrides(self):
        """Test status with multiple overrides in correct priority order."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since="2024-01-15T10:30:00+00:00",
            humidity_active=True,
            humidity_state="paused",
            humidity_resume_at="2024-01-15T11:00:00+00:00",
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
        )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 3
        assert result["overrides"][0]["type"] == "contact_open"
        assert result["overrides"][1]["type"] == "humidity"
        assert result["overrides"][2]["type"] == "night_setback"

    def test_status_with_all_override_types(self):
        """Test status with all override types populated."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            heater_on=True,
            night_setback_active=True,
            night_setback_delta=-2.5,
            night_setback_ends_at="07:00",
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since="2024-01-15T10:30:00+00:00",
        )

        assert result["activity"] == "heating"  # Activity shows heating
        assert len(result["overrides"]) == 2
        assert result["overrides"][0]["type"] == "contact_open"
        assert result["overrides"][1]["type"] == "night_setback"
        assert result["overrides"][1]["delta"] == -2.5
        assert result["overrides"][1]["ends_at"] == "07:00"

    def test_status_humidity_without_resume_at(self):
        """Test that humidity override doesn't include resume_at when not stabilizing."""
        manager = StatusManager()

        result = manager.build_status(hvac_mode="heat", humidity_active=True, humidity_state="paused")

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "humidity"
        assert "resume_at" not in result["overrides"][0]

    def test_status_humidity_with_resume_at(self):
        """Test that humidity override includes resume_at when stabilizing."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            humidity_active=True,
            humidity_state="stabilizing",
            humidity_resume_at="2024-01-15T11:00:00+00:00",
        )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "humidity"
        assert result["overrides"][0]["resume_at"] == "2024-01-15T11:00:00+00:00"

    def test_status_with_preheating_override(self):
        """Test status with preheat_active includes preheating override."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat",
            preheat_active=True,
            preheating_active=True,
            preheating_target_time="07:00",
            preheating_started_at="2024-01-15T05:30:00+00:00",
            preheating_target_delta=2.0,
        )

        assert result["activity"] == "preheating"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "preheating"

    def test_status_with_cycle_settling(self):
        """Test status with cycle_state='settling' returns settling activity."""
        manager = StatusManager()

        result = manager.build_status(hvac_mode="heat", cycle_state="settling")

        assert result["activity"] == "settling"
        assert result["overrides"] == []

    def test_status_with_learning_grace(self):
        """Test status with learning_grace_active includes override."""
        manager = StatusManager()

        result = manager.build_status(
            hvac_mode="heat", learning_grace_active=True, learning_grace_until="2024-01-15T11:00:00+00:00"
        )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "learning_grace"

    def test_debug_fields_not_in_build_status(self):
        """Test that build_status doesn't include debug fields (those are added by build_state_attributes)."""
        manager = StatusManager(debug=False)

        result = manager.build_status(
            hvac_mode="heat",
            humidity_active=True,
            humidity_state="paused",
        )

        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "humidity"
        # Debug fields are NOT in the status object from build_status
        assert "debug" not in result

    def test_build_status_only_returns_activity_and_overrides(self):
        """Test that build_status only returns activity and overrides (no debug)."""
        manager = StatusManager(debug=True)

        result = manager.build_status(
            hvac_mode="heat",
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since="2024-01-15T10:30:00+00:00",
        )

        # Should only have activity and overrides
        assert set(result.keys()) == {"activity", "overrides"}
        assert result["activity"] == "idle"
        assert len(result["overrides"]) == 1
        assert result["overrides"][0]["type"] == "contact_open"


class TestBuildOverride:
    """Test build_override() function."""

    def test_build_contact_open_override(self):
        """Contact open override should have correct structure."""
        from custom_components.adaptive_climate.managers.status_manager import build_override
        from custom_components.adaptive_climate.const import OverrideType

        override = build_override(
            OverrideType.CONTACT_OPEN,
            sensors=["binary_sensor.window_1", "binary_sensor.door_1"],
            since="2024-01-15T10:30:00+00:00",
        )

        assert override["type"] == "contact_open"
        assert override["sensors"] == ["binary_sensor.window_1", "binary_sensor.door_1"]
        assert override["since"] == "2024-01-15T10:30:00+00:00"

    def test_build_night_setback_override(self):
        """Night setback override should have correct structure."""
        from custom_components.adaptive_climate.managers.status_manager import build_override
        from custom_components.adaptive_climate.const import OverrideType

        override = build_override(
            OverrideType.NIGHT_SETBACK,
            delta=-2.0,
            ends_at="07:00",
            limited_to=1.0,
        )

        assert override["type"] == "night_setback"
        assert override["delta"] == -2.0
        assert override["ends_at"] == "07:00"
        assert override["limited_to"] == 1.0

    def test_build_night_setback_override_without_limited(self):
        """Night setback override without limited_to should omit field."""
        from custom_components.adaptive_climate.managers.status_manager import build_override
        from custom_components.adaptive_climate.const import OverrideType

        override = build_override(
            OverrideType.NIGHT_SETBACK,
            delta=-2.0,
            ends_at="07:00",
        )

        assert "limited_to" not in override

    def test_build_humidity_override(self):
        """Humidity override should have state and resume_at."""
        from custom_components.adaptive_climate.managers.status_manager import build_override
        from custom_components.adaptive_climate.const import OverrideType

        override = build_override(
            OverrideType.HUMIDITY,
            state="paused",
            resume_at="2024-01-15T10:45:00+00:00",
        )

        assert override["type"] == "humidity"
        assert override["state"] == "paused"
        assert override["resume_at"] == "2024-01-15T10:45:00+00:00"

    def test_build_preheating_override(self):
        """Preheating override should have target_time, started_at, target_delta."""
        from custom_components.adaptive_climate.managers.status_manager import build_override
        from custom_components.adaptive_climate.const import OverrideType

        override = build_override(
            OverrideType.PREHEATING,
            target_time="07:00",
            started_at="2024-01-15T05:30:00+00:00",
            target_delta=2.0,
        )

        assert override["type"] == "preheating"
        assert override["target_time"] == "07:00"
        assert override["started_at"] == "2024-01-15T05:30:00+00:00"
        assert override["target_delta"] == 2.0

    def test_build_open_window_override(self):
        """Open window override should have since and resume_at."""
        from custom_components.adaptive_climate.managers.status_manager import build_override
        from custom_components.adaptive_climate.const import OverrideType

        override = build_override(
            OverrideType.OPEN_WINDOW,
            since="2024-01-15T10:30:00+00:00",
            resume_at="2024-01-15T10:45:00+00:00",
        )

        assert override["type"] == "open_window"
        assert override["since"] == "2024-01-15T10:30:00+00:00"
        assert override["resume_at"] == "2024-01-15T10:45:00+00:00"

    def test_build_learning_grace_override(self):
        """Learning grace override should have until."""
        from custom_components.adaptive_climate.managers.status_manager import build_override
        from custom_components.adaptive_climate.const import OverrideType

        override = build_override(
            OverrideType.LEARNING_GRACE,
            until="2024-01-15T11:00:00+00:00",
        )

        assert override["type"] == "learning_grace"
        assert override["until"] == "2024-01-15T11:00:00+00:00"


class TestBuildOverrides:
    """Test build_overrides() function."""

    def test_build_overrides_empty_when_no_conditions(self):
        """Overrides should be empty list when no conditions active."""
        from custom_components.adaptive_climate.managers.status_manager import build_overrides

        overrides = build_overrides()
        assert overrides == []

    def test_build_overrides_single_contact_open(self):
        """Single contact_open override should be in list."""
        from custom_components.adaptive_climate.managers.status_manager import build_overrides

        overrides = build_overrides(
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since="2024-01-15T10:30:00+00:00",
        )

        assert len(overrides) == 1
        assert overrides[0]["type"] == "contact_open"
        assert overrides[0]["sensors"] == ["binary_sensor.window"]

    def test_build_overrides_priority_order(self):
        """Multiple overrides should be in priority order."""
        from custom_components.adaptive_climate.managers.status_manager import build_overrides

        overrides = build_overrides(
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since="2024-01-15T10:30:00+00:00",
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
            learning_grace_active=True,
            learning_grace_until="2024-01-15T11:00:00+00:00",
        )

        # Priority: contact_open > night_setback > learning_grace
        assert len(overrides) == 3
        assert overrides[0]["type"] == "contact_open"
        assert overrides[1]["type"] == "night_setback"
        assert overrides[2]["type"] == "learning_grace"

    def test_build_overrides_preheating_before_night_setback(self):
        """Preheating should come before night_setback in priority."""
        from custom_components.adaptive_climate.managers.status_manager import build_overrides

        overrides = build_overrides(
            preheating_active=True,
            preheating_target_time="07:00",
            preheating_started_at="2024-01-15T05:30:00+00:00",
            preheating_target_delta=2.0,
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
        )

        assert len(overrides) == 2
        assert overrides[0]["type"] == "preheating"
        assert overrides[1]["type"] == "night_setback"


class TestDeriveStateNoLegacyStates:
    """Test that derive_state no longer has PAUSED in ThermostatState enum."""

    def test_derive_state_no_paused_parameter(self):
        """derive_state should not have is_paused parameter - pause is handled by overrides."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        # is_paused is no longer a parameter - pause doesn't affect activity
        state = derive_state(
            hvac_mode="heat",
            heater_on=True,
        )

        # Activity is heating (overrides handle pause separately)
        assert state == "heating"

    def test_derive_state_preheating_is_valid_activity(self):
        """derive_state CAN return 'preheating' activity - it's not an enum value but a valid string."""
        from custom_components.adaptive_climate.managers.status_manager import derive_state

        # preheat_active DOES affect activity (it's a valid activity string)
        state = derive_state(
            hvac_mode="heat",
            preheat_active=True,
        )

        # Activity is preheating (as a string, not ThermostatState enum)
        assert state == "preheating"


class TestBuildStatusNewStructure:
    """Test StatusManager.build_status() new structure (activity + overrides)."""

    def test_build_status_new_structure(self):
        """build_status should return activity + overrides structure."""
        manager = StatusManager()
        status = manager.build_status(
            hvac_mode="heat",
            heater_on=True,
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since="2024-01-15T10:30:00+00:00",
        )

        # New structure
        assert "activity" in status
        assert "overrides" in status
        assert status["activity"] == "heating"
        assert len(status["overrides"]) == 1
        assert status["overrides"][0]["type"] == "contact_open"

    def test_build_status_idle_with_no_overrides(self):
        """Idle activity with no overrides should return empty list."""
        manager = StatusManager()
        status = manager.build_status(hvac_mode="off")

        assert status["activity"] == "idle"
        assert status["overrides"] == []

    def test_build_status_multiple_overrides_in_priority_order(self):
        """Multiple overrides should be in priority order."""
        manager = StatusManager()
        status = manager.build_status(
            hvac_mode="heat",
            heater_on=True,
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since="2024-01-15T10:30:00+00:00",
            humidity_active=True,
            humidity_state="paused",
            humidity_resume_at="2024-01-15T11:00:00+00:00",
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="2024-01-15T07:00:00+00:00",
        )

        assert status["activity"] == "heating"
        assert len(status["overrides"]) == 3
        assert status["overrides"][0]["type"] == "contact_open"
        assert status["overrides"][1]["type"] == "humidity"
        assert status["overrides"][2]["type"] == "night_setback"

    def test_build_status_preheating_activity(self):
        """Preheating activity should show in status."""
        manager = StatusManager()
        status = manager.build_status(
            hvac_mode="heat",
            preheat_active=True,
            preheating_active=True,
            preheating_target_time="07:00",
            preheating_started_at="2024-01-15T05:30:00+00:00",
            preheating_target_delta=2.0,
        )

        assert status["activity"] == "preheating"
        assert len(status["overrides"]) == 1
        assert status["overrides"][0]["type"] == "preheating"
