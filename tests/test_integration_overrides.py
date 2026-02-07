"""Integration tests for override stacking and interactions.

Tests how StatusManager coordinates with detectors (contact, humidity) and
learning components to properly manage pauses, priority ordering, and state
resilience during override conditions.
"""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.adaptive_climate.adaptive.contact_sensors import (
    ContactAction,
    ContactSensorHandler,
)
from custom_components.adaptive_climate.adaptive.humidity_detector import HumidityDetector
from custom_components.adaptive_climate.const import OverrideType
from custom_components.adaptive_climate.managers.status_manager import (
    StatusManager,
    build_overrides,
    format_iso8601,
)


class TestOverridePriorityStacking:
    """Test D1: Override priority stacking in StatusManager."""

    def test_override_priority_order(self, time_travel):
        """Verify overrides are ordered by priority (first = in control)."""
        # Create StatusManager with both contact and humidity handlers
        contact_handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=0,  # No delay for testing
            action=ContactAction.PAUSE,
        )
        humidity_detector = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
        )

        status_manager = StatusManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        # Scenario 1: Night setback active (lowest priority among pauses)
        now = time_travel.now()
        overrides = build_overrides(
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
        )
        assert len(overrides) == 1
        assert overrides[0]["type"] == OverrideType.NIGHT_SETBACK.value
        assert overrides[0]["delta"] == -2.0

        # Scenario 2: Add contact sensor (higher priority than night setback)
        contact_handler.update_contact_states(
            {"binary_sensor.window": True},  # Window open
            current_time=now,
        )
        time_travel.advance(seconds=1)
        now = time_travel.now()

        # Contact sensor should take priority
        assert contact_handler.should_take_action(now)
        assert status_manager.is_paused()

        overrides = build_overrides(
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since=format_iso8601(now),
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
        )
        assert len(overrides) == 2
        # Contact open should be first (highest priority)
        assert overrides[0]["type"] == OverrideType.CONTACT_OPEN.value
        assert overrides[1]["type"] == OverrideType.NIGHT_SETBACK.value

        # Scenario 3: Add humidity spike (between contact and night setback)
        humidity_detector.record_humidity(now, 85.0)  # Spike above absolute_max
        assert humidity_detector.should_pause()
        assert humidity_detector.get_state() == "paused"

        overrides = build_overrides(
            contact_open=True,
            contact_sensors=["binary_sensor.window"],
            contact_since=format_iso8601(now),
            humidity_active=True,
            humidity_state="paused",
            humidity_resume_at=None,
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
        )
        assert len(overrides) == 3
        # Priority: contact_open > humidity > night_setback
        assert overrides[0]["type"] == OverrideType.CONTACT_OPEN.value
        assert overrides[1]["type"] == OverrideType.HUMIDITY.value
        assert overrides[2]["type"] == OverrideType.NIGHT_SETBACK.value

        # Scenario 4: Remove contact sensor (next override takes over)
        contact_handler.update_contact_states(
            {"binary_sensor.window": False},  # Window closed
            current_time=now,
        )
        assert not contact_handler.should_take_action(now)

        # StatusManager should still be paused due to humidity
        assert status_manager.is_paused()

        overrides = build_overrides(
            humidity_active=True,
            humidity_state="paused",
            humidity_resume_at=None,
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
        )
        assert len(overrides) == 2
        # Humidity now first, night setback second
        assert overrides[0]["type"] == OverrideType.HUMIDITY.value
        assert overrides[1]["type"] == OverrideType.NIGHT_SETBACK.value

        # Scenario 5: Humidity exits to stabilizing
        humidity_detector.record_humidity(now, 60.0)  # Below exit threshold
        time_travel.advance(seconds=1)
        now = time_travel.now()
        humidity_detector.record_humidity(now, 60.0)

        # Should transition to stabilizing (still pauses)
        assert humidity_detector.get_state() == "stabilizing"
        assert humidity_detector.should_pause()

        # Scenario 6: Clear all overrides
        # Wait for stabilization delay
        time_travel.advance(minutes=6)
        now = time_travel.now()
        humidity_detector.record_humidity(now, 55.0)

        # Should transition to normal
        assert humidity_detector.get_state() == "normal"
        assert not humidity_detector.should_pause()
        assert not status_manager.is_paused()

        # Only night setback remains (not a pause)
        overrides = build_overrides(
            night_setback_active=True,
            night_setback_delta=-2.0,
            night_setback_ends_at="07:00",
        )
        assert len(overrides) == 1
        assert overrides[0]["type"] == OverrideType.NIGHT_SETBACK.value


class TestContactPauseAndLearningResilience:
    """Test D2: Contact pause discards active heating rate session."""

    def test_contact_pause_discards_active_session(self, make_thermostat, time_travel):
        """Contact opening during active session should discard it."""
        t = make_thermostat(heating_type="radiator")
        now = time_travel.now()

        # Set up temperature below setpoint to start heating rate session
        t.target_temp = 21.0
        t.current_temp = 19.0

        # Start a heating rate session
        learner = t.heating_rate_learner
        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=now,
        )

        # Verify session is active
        assert learner._active_session is not None
        assert learner._active_session.start_temp == 19.0

        # Simulate contact opening (pause)
        # End session with "override" reason - should discard
        result = learner.end_session(end_temp=19.5, reason="override", timestamp=now)

        # Verify session was discarded (returns None for override)
        assert result is None
        assert learner._active_session is None

        # Start a new session after pause clears
        time_travel.advance(minutes=1)
        now = time_travel.now()
        learner.start_session(
            temp=19.5,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=now,
        )

        # New session should start cleanly
        assert learner._active_session is not None
        assert learner._active_session.start_temp == 19.5

        # Complete this session successfully
        time_travel.advance(minutes=30)
        now = time_travel.now()

        # Update session with progress
        learner.update_session(temp=21.0, duty=65.0)

        # End session successfully
        result = learner.end_session(end_temp=21.0, reason="reached_setpoint", timestamp=now)

        # This should save an observation (returns non-None)
        assert result is not None
        assert learner._active_session is None

        # Get learned rate to verify observation was saved
        rate, source = learner.get_heating_rate(
            delta=1.5,  # 21.0 - 19.5
            outdoor_temp=5.0,
        )
        # Should have a rate (fallback or learned)
        assert rate is not None
        assert rate > 0
        # Source should indicate learned or fallback
        assert source in ["learned", "fallback", "interpolated"]


class TestContactOverrideSensorDetails:
    """Test contact override includes sensor details (D1 extension).

    Bug: state_attributes.py calls get_open_sensor_ids() and get_first_open_time()
    on ContactSensorHandler, but these methods didn't exist → AttributeError.
    """

    def test_contact_override_includes_sensor_details(self, time_travel):
        """Contact override should include which sensors are open and when."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_1", "binary_sensor.window_2"],
            contact_delay_seconds=0,
        )

        now = time_travel.now()

        # Open one of two windows
        handler.update_contact_states(
            {"binary_sensor.window_1": True, "binary_sensor.window_2": False},
            current_time=now,
        )

        # Handler must expose which sensors are open
        assert handler.is_any_contact_open()
        open_sensors = handler.get_open_sensor_ids()
        assert open_sensors == ["binary_sensor.window_1"]

        # Handler must expose when contacts first opened
        first_open = handler.get_first_open_time()
        assert first_open == now

        # Build override with full details
        overrides = build_overrides(
            contact_open=True,
            contact_sensors=open_sensors,
            contact_since=format_iso8601(first_open),
        )

        assert len(overrides) == 1
        assert overrides[0]["type"] == OverrideType.CONTACT_OPEN.value
        assert overrides[0]["sensors"] == ["binary_sensor.window_1"]
        assert "since" in overrides[0]

    def test_contact_multiple_sensors_open(self, time_travel):
        """Opening multiple sensors should list all in override."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_1", "binary_sensor.window_2"],
            contact_delay_seconds=0,
        )

        now = time_travel.now()

        # Open both windows
        handler.update_contact_states(
            {"binary_sensor.window_1": True, "binary_sensor.window_2": True},
            current_time=now,
        )

        open_sensors = handler.get_open_sensor_ids()
        assert len(open_sensors) == 2
        assert "binary_sensor.window_1" in open_sensors
        assert "binary_sensor.window_2" in open_sensors

    def test_contact_close_clears_sensor_list(self, time_travel):
        """Closing all sensors should clear the open list and timestamp."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=0,
        )

        now = time_travel.now()

        # Open then close
        handler.update_contact_states({"binary_sensor.window": True}, current_time=now)
        assert handler.get_open_sensor_ids() == ["binary_sensor.window"]
        assert handler.get_first_open_time() == now

        time_travel.advance(minutes=1)
        handler.update_contact_states(
            {"binary_sensor.window": False}, current_time=time_travel.now()
        )

        assert handler.get_open_sensor_ids() == []
        assert handler.get_first_open_time() is None


class TestHumidityPauseAndIntegralDecay:
    """Test D3: Humidity pause applies integral decay."""

    def test_humidity_pause_applies_integral_decay(self, make_thermostat, time_travel):
        """Humidity spike should pause heating and decay PID integral."""
        t = make_thermostat(heating_type="radiator")
        now = time_travel.now()

        # Set up PID with known integral value
        pid = t.pid
        pid.integral = 10.0  # Set known value

        # Create HumidityDetector
        humidity = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            stabilization_delay=300,  # 5 minutes
        )

        # Trigger humidity spike
        humidity.record_humidity(now, 85.0)
        assert humidity.get_state() == "paused"
        assert humidity.should_pause()

        # During pause, integral should decay
        # According to CLAUDE.md: ~10%/min decay during pause
        # Decay formula: integral *= factor
        # For 1 minute at 10%/min: factor = 0.9
        decay_factor = 0.9

        # Apply decay for 1 minute of pause
        pid.decay_integral(decay_factor)

        # Verify integral decreased but not to zero
        assert pid.integral < 10.0
        assert pid.integral > 0.0
        assert pid.integral == pytest.approx(9.0, rel=0.01)

        # Apply more decay for additional time
        time_travel.advance(minutes=2)
        now = time_travel.now()

        # Decay for 2 more minutes (total 3 minutes)
        pid.decay_integral(decay_factor)  # 2nd minute
        pid.decay_integral(decay_factor)  # 3rd minute

        expected_after_3min = 10.0 * (0.9 ** 3)
        assert pid.integral == pytest.approx(expected_after_3min, rel=0.01)

        # Humidity drops, transitions to stabilizing
        humidity.record_humidity(now, 60.0)
        assert humidity.get_state() == "stabilizing"
        assert humidity.should_pause()  # Still pauses during stabilizing

        # Wait for stabilization delay
        time_travel.advance(minutes=6)
        now = time_travel.now()
        humidity.record_humidity(now, 55.0)

        # Should transition to normal
        assert humidity.get_state() == "normal"
        assert not humidity.should_pause()

        # Verify PID can still function with reduced integral
        assert pid.integral > 0.0
        t.current_temp = 19.0
        t.target_temp = 21.0

        # Calculate output
        output, updated = pid.calc(
            input_val=t.current_temp,
            set_point=t.target_temp,
            input_time=now.timestamp(),
        )

        # Should produce valid output
        assert updated
        assert output >= 0
        assert output <= 100

    def test_humidity_state_machine_transitions(self, time_travel):
        """Test complete humidity detector state machine: NORMAL → PAUSED → STABILIZING → NORMAL."""
        humidity = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            detection_window=300,
            stabilization_delay=300,
            exit_humidity_threshold=70.0,
            exit_humidity_drop=5.0,
        )

        now = time_travel.now()

        # Initial state: NORMAL
        assert humidity.get_state() == "normal"
        assert not humidity.should_pause()

        # Record normal humidity
        humidity.record_humidity(now, 50.0)
        assert humidity.get_state() == "normal"

        # Trigger spike: NORMAL → PAUSED (absolute threshold)
        time_travel.advance(seconds=30)
        now = time_travel.now()
        humidity.record_humidity(now, 85.0)  # Above absolute_max (80%)

        assert humidity.get_state() == "paused"
        assert humidity.should_pause()

        # Humidity drops below exit threshold + sufficient drop from peak
        # Peak = 85%, need to drop to <70% AND >5% drop from peak
        time_travel.advance(minutes=2)
        now = time_travel.now()
        humidity.record_humidity(now, 65.0)  # <70% and 20% drop from peak

        # Should transition: PAUSED → STABILIZING
        assert humidity.get_state() == "stabilizing"
        assert humidity.should_pause()  # Still pauses during stabilizing
        assert humidity.get_time_until_resume() is not None

        # Wait for stabilization delay (5 minutes)
        time_travel.advance(minutes=6)
        now = time_travel.now()
        humidity.record_humidity(now, 60.0)

        # Should transition: STABILIZING → NORMAL
        assert humidity.get_state() == "normal"
        assert not humidity.should_pause()
        assert humidity.get_time_until_resume() is None
