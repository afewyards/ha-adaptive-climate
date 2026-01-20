"""Tests for cycle event types and dispatcher."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_thermostat.managers.events import (
    CycleEventType,
    CycleStartedEvent,
    CycleEndedEvent,
    HeatingStartedEvent,
    HeatingEndedEvent,
    SettlingStartedEvent,
    SetpointChangedEvent,
    ModeChangedEvent,
    ContactPauseEvent,
    ContactResumeEvent,
    CycleEventDispatcher,
)


class TestCycleEventTypeEnum:
    """Tests for CycleEventType enum."""

    def test_cycle_event_type_enum_has_all_types(self):
        """Verify all event types exist."""
        assert CycleEventType.CYCLE_STARTED
        assert CycleEventType.CYCLE_ENDED
        assert CycleEventType.HEATING_STARTED
        assert CycleEventType.HEATING_ENDED
        assert CycleEventType.SETTLING_STARTED
        assert CycleEventType.SETPOINT_CHANGED
        assert CycleEventType.MODE_CHANGED
        assert CycleEventType.CONTACT_PAUSE
        assert CycleEventType.CONTACT_RESUME

    def test_cycle_event_type_values_are_strings(self):
        """Verify event type values are descriptive strings."""
        assert CycleEventType.CYCLE_STARTED.value == "cycle_started"
        assert CycleEventType.CYCLE_ENDED.value == "cycle_ended"
        assert CycleEventType.HEATING_STARTED.value == "heating_started"
        assert CycleEventType.HEATING_ENDED.value == "heating_ended"
        assert CycleEventType.SETTLING_STARTED.value == "settling_started"
        assert CycleEventType.SETPOINT_CHANGED.value == "setpoint_changed"
        assert CycleEventType.MODE_CHANGED.value == "mode_changed"
        assert CycleEventType.CONTACT_PAUSE.value == "contact_pause"
        assert CycleEventType.CONTACT_RESUME.value == "contact_resume"


class TestCycleStartedEventDataclass:
    """Tests for CycleStartedEvent dataclass."""

    def test_cycle_started_event_has_required_fields(self):
        """Verify CycleStartedEvent has hvac_mode, timestamp, target_temp, current_temp."""
        now = datetime.now()
        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=now,
            target_temp=21.0,
            current_temp=19.0,
        )
        assert event.hvac_mode == "heat"
        assert event.timestamp == now
        assert event.target_temp == 21.0
        assert event.current_temp == 19.0

    def test_cycle_started_event_type(self):
        """Verify event_type property returns correct type."""
        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=21.0,
            current_temp=19.0,
        )
        assert event.event_type == CycleEventType.CYCLE_STARTED


class TestHeatingEventDataclass:
    """Tests for HeatingStartedEvent and HeatingEndedEvent dataclasses."""

    def test_heating_started_event_has_required_fields(self):
        """Verify HeatingStartedEvent has hvac_mode, timestamp."""
        now = datetime.now()
        event = HeatingStartedEvent(hvac_mode="heat", timestamp=now)
        assert event.hvac_mode == "heat"
        assert event.timestamp == now

    def test_heating_started_event_type(self):
        """Verify event_type property returns correct type."""
        event = HeatingStartedEvent(hvac_mode="heat", timestamp=datetime.now())
        assert event.event_type == CycleEventType.HEATING_STARTED

    def test_heating_ended_event_has_required_fields(self):
        """Verify HeatingEndedEvent has hvac_mode, timestamp."""
        now = datetime.now()
        event = HeatingEndedEvent(hvac_mode="heat", timestamp=now)
        assert event.hvac_mode == "heat"
        assert event.timestamp == now

    def test_heating_ended_event_type(self):
        """Verify event_type property returns correct type."""
        event = HeatingEndedEvent(hvac_mode="heat", timestamp=datetime.now())
        assert event.event_type == CycleEventType.HEATING_ENDED


class TestSettlingStartedEventDataclass:
    """Tests for SettlingStartedEvent dataclass."""

    def test_settling_started_event_has_required_fields(self):
        """Verify SettlingStartedEvent has hvac_mode, timestamp."""
        now = datetime.now()
        event = SettlingStartedEvent(hvac_mode="heat", timestamp=now)
        assert event.hvac_mode == "heat"
        assert event.timestamp == now

    def test_settling_started_event_type(self):
        """Verify event_type property returns correct type."""
        event = SettlingStartedEvent(hvac_mode="heat", timestamp=datetime.now())
        assert event.event_type == CycleEventType.SETTLING_STARTED


class TestCycleEndedEventDataclass:
    """Tests for CycleEndedEvent dataclass."""

    def test_cycle_ended_event_has_required_fields(self):
        """Verify CycleEndedEvent has hvac_mode, timestamp, metrics optional."""
        now = datetime.now()
        event = CycleEndedEvent(hvac_mode="heat", timestamp=now)
        assert event.hvac_mode == "heat"
        assert event.timestamp == now
        assert event.metrics is None

    def test_cycle_ended_event_with_metrics(self):
        """Verify CycleEndedEvent can include optional metrics."""
        now = datetime.now()
        metrics = {"overshoot": 0.5, "duration": 300}
        event = CycleEndedEvent(hvac_mode="heat", timestamp=now, metrics=metrics)
        assert event.metrics == metrics

    def test_cycle_ended_event_type(self):
        """Verify event_type property returns correct type."""
        event = CycleEndedEvent(hvac_mode="heat", timestamp=datetime.now())
        assert event.event_type == CycleEventType.CYCLE_ENDED


class TestUserActionEventsDataclass:
    """Tests for user action event dataclasses."""

    def test_setpoint_changed_event_has_required_fields(self):
        """Verify SetpointChangedEvent has required fields."""
        now = datetime.now()
        event = SetpointChangedEvent(
            hvac_mode="heat",
            timestamp=now,
            old_target=20.0,
            new_target=21.0,
        )
        assert event.hvac_mode == "heat"
        assert event.timestamp == now
        assert event.old_target == 20.0
        assert event.new_target == 21.0

    def test_setpoint_changed_event_type(self):
        """Verify event_type property returns correct type."""
        event = SetpointChangedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            old_target=20.0,
            new_target=21.0,
        )
        assert event.event_type == CycleEventType.SETPOINT_CHANGED

    def test_mode_changed_event_has_required_fields(self):
        """Verify ModeChangedEvent has required fields."""
        now = datetime.now()
        event = ModeChangedEvent(
            timestamp=now,
            old_mode="heat",
            new_mode="off",
        )
        assert event.timestamp == now
        assert event.old_mode == "heat"
        assert event.new_mode == "off"

    def test_mode_changed_event_type(self):
        """Verify event_type property returns correct type."""
        event = ModeChangedEvent(
            timestamp=datetime.now(),
            old_mode="heat",
            new_mode="off",
        )
        assert event.event_type == CycleEventType.MODE_CHANGED

    def test_contact_pause_event_has_required_fields(self):
        """Verify ContactPauseEvent has required fields."""
        now = datetime.now()
        event = ContactPauseEvent(hvac_mode="heat", timestamp=now, entity_id="binary_sensor.window")
        assert event.hvac_mode == "heat"
        assert event.timestamp == now
        assert event.entity_id == "binary_sensor.window"

    def test_contact_pause_event_type(self):
        """Verify event_type property returns correct type."""
        event = ContactPauseEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            entity_id="binary_sensor.window",
        )
        assert event.event_type == CycleEventType.CONTACT_PAUSE

    def test_contact_resume_event_has_required_fields(self):
        """Verify ContactResumeEvent has required fields."""
        now = datetime.now()
        event = ContactResumeEvent(
            hvac_mode="heat",
            timestamp=now,
            entity_id="binary_sensor.window",
            pause_duration_seconds=120.0,
        )
        assert event.hvac_mode == "heat"
        assert event.timestamp == now
        assert event.entity_id == "binary_sensor.window"
        assert event.pause_duration_seconds == 120.0

    def test_contact_resume_event_type(self):
        """Verify event_type property returns correct type."""
        event = ContactResumeEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            entity_id="binary_sensor.window",
            pause_duration_seconds=120.0,
        )
        assert event.event_type == CycleEventType.CONTACT_RESUME


class TestCycleEventDispatcher:
    """Tests for CycleEventDispatcher."""

    def test_dispatcher_subscribe_returns_unsubscribe(self):
        """Subscribing returns an unsubscribe callable."""
        dispatcher = CycleEventDispatcher()
        callback = MagicMock()
        unsubscribe = dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback)
        assert callable(unsubscribe)

    def test_dispatcher_emit_calls_subscribers(self):
        """Emitting calls all subscribers for that event type."""
        dispatcher = CycleEventDispatcher()
        callback = MagicMock()
        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback)

        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(event)

        callback.assert_called_once_with(event)

    def test_dispatcher_unsubscribe_removes_listener(self):
        """Calling unsubscribe removes the listener."""
        dispatcher = CycleEventDispatcher()
        callback = MagicMock()
        unsubscribe = dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback)

        unsubscribe()

        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(event)

        callback.assert_not_called()

    def test_dispatcher_multiple_subscribers_all_receive_event(self):
        """Multiple subscribers all receive the event."""
        dispatcher = CycleEventDispatcher()
        callback1 = MagicMock()
        callback2 = MagicMock()
        callback3 = MagicMock()

        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback1)
        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback2)
        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback3)

        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(event)

        callback1.assert_called_once_with(event)
        callback2.assert_called_once_with(event)
        callback3.assert_called_once_with(event)

    def test_dispatcher_subscriber_error_isolation(self):
        """One subscriber throwing doesn't block others."""
        dispatcher = CycleEventDispatcher()
        callback1 = MagicMock(side_effect=ValueError("Test error"))
        callback2 = MagicMock()

        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback1)
        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, callback2)

        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(event)

        # Both should be called, even though callback1 raises
        callback1.assert_called_once_with(event)
        callback2.assert_called_once_with(event)

    def test_dispatcher_different_event_types_isolated(self):
        """Subscribers only receive events they subscribed to."""
        dispatcher = CycleEventDispatcher()
        cycle_callback = MagicMock()
        heating_callback = MagicMock()

        dispatcher.subscribe(CycleEventType.CYCLE_STARTED, cycle_callback)
        dispatcher.subscribe(CycleEventType.HEATING_STARTED, heating_callback)

        cycle_event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(cycle_event)

        cycle_callback.assert_called_once_with(cycle_event)
        heating_callback.assert_not_called()
