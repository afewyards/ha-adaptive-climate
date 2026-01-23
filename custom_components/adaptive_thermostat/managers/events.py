"""Cycle event types and dispatcher for adaptive thermostat.

This module provides a pub/sub event system for decoupling cycle tracking
from the components that trigger cycle events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Union

_LOGGER = logging.getLogger(__name__)


class CycleEventType(Enum):
    """Types of cycle events."""

    CYCLE_STARTED = "cycle_started"
    CYCLE_ENDED = "cycle_ended"
    HEATING_STARTED = "heating_started"
    HEATING_ENDED = "heating_ended"
    SETTLING_STARTED = "settling_started"
    SETPOINT_CHANGED = "setpoint_changed"
    MODE_CHANGED = "mode_changed"
    CONTACT_PAUSE = "contact_pause"
    CONTACT_RESUME = "contact_resume"
    TEMPERATURE_UPDATE = "temperature_update"


@dataclass
class CycleStartedEvent:
    """Event emitted when a heating/cooling cycle starts."""

    hvac_mode: str
    timestamp: datetime
    target_temp: float
    current_temp: float

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.CYCLE_STARTED


@dataclass
class CycleEndedEvent:
    """Event emitted when a heating/cooling cycle ends."""

    hvac_mode: str
    timestamp: datetime
    metrics: dict[str, Any] | None = None

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.CYCLE_ENDED


@dataclass
class HeatingStartedEvent:
    """Event emitted when the heating device turns on."""

    hvac_mode: str
    timestamp: datetime

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.HEATING_STARTED


@dataclass
class HeatingEndedEvent:
    """Event emitted when the heating device turns off."""

    hvac_mode: str
    timestamp: datetime

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.HEATING_ENDED


@dataclass
class SettlingStartedEvent:
    """Event emitted when settling phase begins."""

    hvac_mode: str
    timestamp: datetime
    was_clamped: bool = False

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.SETTLING_STARTED


@dataclass
class SetpointChangedEvent:
    """Event emitted when target temperature changes."""

    hvac_mode: str
    timestamp: datetime
    old_target: float
    new_target: float

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.SETPOINT_CHANGED


@dataclass
class ModeChangedEvent:
    """Event emitted when HVAC mode changes."""

    timestamp: datetime
    old_mode: str
    new_mode: str

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.MODE_CHANGED


@dataclass
class ContactPauseEvent:
    """Event emitted when contact sensor pauses heating."""

    hvac_mode: str
    timestamp: datetime
    entity_id: str

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.CONTACT_PAUSE


@dataclass
class ContactResumeEvent:
    """Event emitted when contact sensor resumes heating."""

    hvac_mode: str
    timestamp: datetime
    entity_id: str
    pause_duration_seconds: float

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.CONTACT_RESUME


@dataclass
class TemperatureUpdateEvent:
    """Event emitted when temperature is updated."""

    timestamp: datetime
    temperature: float
    setpoint: float
    pid_integral: float
    pid_error: float

    @property
    def event_type(self) -> CycleEventType:
        """Return the event type."""
        return CycleEventType.TEMPERATURE_UPDATE


# Type alias for any cycle event
CycleEvent = Union[
    CycleStartedEvent,
    CycleEndedEvent,
    HeatingStartedEvent,
    HeatingEndedEvent,
    SettlingStartedEvent,
    SetpointChangedEvent,
    ModeChangedEvent,
    ContactPauseEvent,
    ContactResumeEvent,
    TemperatureUpdateEvent,
]


class CycleEventDispatcher:
    """Dispatcher for cycle events using pub/sub pattern."""

    def __init__(self) -> None:
        """Initialize the dispatcher."""
        self._listeners: dict[CycleEventType, list[Callable[[CycleEvent], None]]] = {}

    def subscribe(
        self, event_type: CycleEventType, callback: Callable[[CycleEvent], None]
    ) -> Callable[[], None]:
        """Subscribe to an event type.

        Args:
            event_type: The type of event to subscribe to.
            callback: The function to call when the event is emitted.

        Returns:
            A callable that unsubscribes the listener when called.
        """
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)

        def unsubscribe() -> None:
            """Remove this callback from the listeners."""
            if event_type in self._listeners and callback in self._listeners[event_type]:
                self._listeners[event_type].remove(callback)

        return unsubscribe

    def emit(self, event: CycleEvent) -> None:
        """Emit an event to all subscribers.

        Args:
            event: The event to emit.
        """
        event_type = event.event_type
        if event_type not in self._listeners:
            return

        for callback in self._listeners[event_type]:
            try:
                callback(event)
            except Exception:
                _LOGGER.exception(
                    "Error in event listener for %s: %s",
                    event_type.value,
                    callback,
                )
