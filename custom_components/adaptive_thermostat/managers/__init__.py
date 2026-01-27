"""Manager classes for Adaptive Thermostat integration."""
from __future__ import annotations

from .control_output import ControlOutputManager
from .cycle_tracker import CycleState, CycleTrackerManager
from .events import (
    ContactPauseEvent,
    ContactResumeEvent,
    CycleEndedEvent,
    CycleEvent,
    CycleEventDispatcher,
    CycleEventType,
    CycleStartedEvent,
    HeatingEndedEvent,
    HeatingStartedEvent,
    ModeChangedEvent,
    SetpointChangedEvent,
    SettlingStartedEvent,
)
from .heater_controller import HeaterController
from .ke_manager import KeController
from .night_setback_calculator import NightSetbackCalculator
from .night_setback_manager import NightSetbackController
from .pause_manager import PauseManager
from .pid_tuning import PIDTuningManager
from .state_attributes import build_state_attributes
from .state_restorer import StateRestorer
from .temperature_manager import TemperatureManager

__all__ = [
    "ContactPauseEvent",
    "ContactResumeEvent",
    "ControlOutputManager",
    "CycleEndedEvent",
    "CycleEvent",
    "CycleEventDispatcher",
    "CycleEventType",
    "CycleStartedEvent",
    "CycleState",
    "CycleTrackerManager",
    "HeaterController",
    "HeatingEndedEvent",
    "HeatingStartedEvent",
    "KeController",
    "ModeChangedEvent",
    "NightSetbackCalculator",
    "NightSetbackController",
    "PauseManager",
    "PIDTuningManager",
    "SetpointChangedEvent",
    "SettlingStartedEvent",
    "StateRestorer",
    "TemperatureManager",
    "build_state_attributes",
]
