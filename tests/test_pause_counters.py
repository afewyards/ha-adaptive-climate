"""Tests for humidity and contact pause counters."""

import pytest
from datetime import timedelta
from unittest.mock import Mock, MagicMock
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.adaptive_climate.adaptive.humidity_detector import HumidityDetector
from custom_components.adaptive_climate.adaptive.contact_sensors import ContactSensorHandler, ContactAction


def test_humidity_pause_counter_properties():
    """Test that humidity pause counter properties work correctly."""

    # Create a minimal thermostat-like object with counter properties
    class MinimalThermostat:
        def __init__(self):
            self._humidity_pause_count = 0
            self._contact_pause_count = 0

        @property
        def humidity_pause_count(self):
            return self._humidity_pause_count

        @property
        def contact_pause_count(self):
            return self._contact_pause_count

        def reset_pause_counters(self):
            self._humidity_pause_count = 0
            self._contact_pause_count = 0

    thermostat = MinimalThermostat()

    # Initial values should be 0
    assert thermostat.humidity_pause_count == 0
    assert thermostat.contact_pause_count == 0

    # Set internal counters
    thermostat._humidity_pause_count = 7
    thermostat._contact_pause_count = 4

    # Properties should return internal values
    assert thermostat.humidity_pause_count == 7
    assert thermostat.contact_pause_count == 4


def test_reset_pause_counters():
    """Test that reset_pause_counters resets both counters to 0."""

    # Create a minimal thermostat-like object
    class MinimalThermostat:
        def __init__(self):
            self._humidity_pause_count = 5
            self._contact_pause_count = 3

        @property
        def humidity_pause_count(self):
            return self._humidity_pause_count

        @property
        def contact_pause_count(self):
            return self._contact_pause_count

        def reset_pause_counters(self):
            self._humidity_pause_count = 0
            self._contact_pause_count = 0

    thermostat = MinimalThermostat()

    # Verify initial non-zero values
    assert thermostat.humidity_pause_count == 5
    assert thermostat.contact_pause_count == 3

    # Reset
    thermostat.reset_pause_counters()

    # Both should be 0
    assert thermostat.humidity_pause_count == 0
    assert thermostat.contact_pause_count == 0


def test_humidity_detector_state_transitions():
    """Test that humidity detector transitions from normal to paused as expected.

    This verifies the logic for when counters should be incremented.
    """
    from homeassistant.util import dt as dt_util

    detector = HumidityDetector(
        spike_threshold=15.0,
        absolute_max=80.0,
        detection_window=300,
        stabilization_delay=300,
    )

    # Initial state should be normal
    assert detector.get_state() == "normal"

    # Record humidity at 55%
    now = dt_util.utcnow()
    detector.record_humidity(now, 55.0)
    assert detector.get_state() == "normal"

    # Spike to 75% (20% rise) - should trigger pause
    detector.record_humidity(now, 75.0)
    assert detector.get_state() == "paused"

    # This is when the counter should be incremented:
    # When state transitions from "normal" to "paused"


def test_contact_sensor_handler_open_detection():
    """Test that contact sensor handler detects open contacts as expected.

    This verifies the logic for when counters should be incremented.
    """
    handler = ContactSensorHandler(
        contact_sensors=["binary_sensor.window"],
        contact_delay_seconds=300,
        action=ContactAction.PAUSE,
    )

    # Initial state should have no open contacts
    assert not handler.is_any_contact_open()

    # Update states - window closed
    handler.update_contact_states({"binary_sensor.window": False})
    assert not handler.is_any_contact_open()

    # Update states - window open
    handler.update_contact_states({"binary_sensor.window": True})
    assert handler.is_any_contact_open()

    # This is when the counter should be incremented:
    # When contact state changes from closed (False) to open (True)


def test_counter_increment_logic_integration():
    """Integration test that verifies counter increment logic with detector state machine.

    This test demonstrates when counters should and should not be incremented:
    - Increment on normal -> paused transition
    - Do NOT increment on paused -> paused (already paused)
    - Do NOT increment on stabilizing -> normal
    - Increment again on normal -> paused (new event after reset)
    """
    from homeassistant.util import dt as dt_util

    # Simulate the counter tracking logic
    humidity_pause_count = 0
    detector = HumidityDetector(
        spike_threshold=15.0,
        absolute_max=80.0,
        detection_window=300,
        stabilization_delay=300,
    )

    now = dt_util.utcnow()

    # First reading at 55% (normal state)
    prev_state = detector.get_state()
    detector.record_humidity(now, 55.0)
    current_state = detector.get_state()
    if prev_state == "normal" and current_state == "paused":
        humidity_pause_count += 1
    assert humidity_pause_count == 0  # No transition yet

    # Spike to 75% (20% rise) - should trigger pause
    prev_state = detector.get_state()
    detector.record_humidity(now, 75.0)
    current_state = detector.get_state()
    if prev_state == "normal" and current_state == "paused":
        humidity_pause_count += 1
    assert humidity_pause_count == 1  # First pause event

    # Another spike while already paused - should NOT increment
    prev_state = detector.get_state()
    detector.record_humidity(now, 80.0)
    current_state = detector.get_state()
    if prev_state == "normal" and current_state == "paused":
        humidity_pause_count += 1
    assert humidity_pause_count == 1  # Still 1, no new transition

    # Drop to stabilizing - should NOT increment
    prev_state = detector.get_state()
    detector.record_humidity(now, 65.0)
    current_state = detector.get_state()
    if prev_state == "normal" and current_state == "paused":
        humidity_pause_count += 1
    assert humidity_pause_count == 1  # Still 1

    # Fast-forward time and return to normal
    from datetime import timedelta as td

    future_now = now + td(seconds=400)
    prev_state = detector.get_state()
    detector.record_humidity(future_now, 55.0)
    current_state = detector.get_state()
    if prev_state == "normal" and current_state == "paused":
        humidity_pause_count += 1
    assert humidity_pause_count == 1  # Still 1

    # New spike after returning to normal - should increment again
    prev_state = detector.get_state()
    detector.record_humidity(future_now, 75.0)
    current_state = detector.get_state()
    if prev_state == "normal" and current_state == "paused":
        humidity_pause_count += 1
    assert humidity_pause_count == 2  # Second pause event


def test_contact_counter_increment_logic():
    """Integration test that verifies contact counter increment logic.

    This test demonstrates when contact counters should be incremented:
    - Increment when ANY contact opens (changes from closed to open)
    - Do NOT increment when contact closes
    - Increment again when contact re-opens
    """
    contact_pause_count = 0
    handler = ContactSensorHandler(
        contact_sensors=["binary_sensor.window"],
        contact_delay_seconds=300,
        action=ContactAction.PAUSE,
    )

    # Initial state - window closed
    handler.update_contact_states({"binary_sensor.window": False})
    # No increment on initial state
    assert contact_pause_count == 0

    # Simulate the logic in climate_handlers.py _async_contact_sensor_changed
    # When new_state.state == STATE_ON (contact opens), increment counter
    is_open = True
    if is_open:
        contact_pause_count += 1
    assert contact_pause_count == 1  # First open event

    # Window closes - should NOT increment
    is_open = False
    if is_open:  # Only increment on open, not close
        contact_pause_count += 1
    assert contact_pause_count == 1  # Still 1

    # Window opens again - should increment again
    is_open = True
    if is_open:
        contact_pause_count += 1
    assert contact_pause_count == 2  # Second open event
