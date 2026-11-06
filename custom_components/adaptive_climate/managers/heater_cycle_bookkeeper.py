"""Cycle counting, state tracking, and lifecycle event emission for HeaterController."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

try:
    from homeassistant.util import dt as dt_util
    from homeassistant.components.climate import HVACMode

    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HVACMode = Any  # type: ignore[assignment,misc]
    dt_util = None  # type: ignore[assignment]

from .events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    HeatingEndedEvent,
    HeatingStartedEvent,
    SettlingStartedEvent,
)

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


class HeaterCycleBookkeeper:
    """Manages per-heating-session state and emits cycle lifecycle events.

    Owns:
    - ``_cycle_active`` / ``_has_demand`` session flags
    - Actuator wear counters (``_heater_cycle_count``, ``_cooler_cycle_count``)
    - Last-state tracking for on→off edge detection
    - PID clamp-state callbacks
    - All CYCLE_STARTED / HEATING_STARTED / HEATING_ENDED / SETTLING_STARTED emission
    """

    def __init__(
        self,
        thermostat: AdaptiveThermostat,
        dispatcher: CycleEventDispatcher | None,
        get_was_clamped: Callable[[], bool] | None,
        reset_clamp_state: Callable[[], None] | None,
    ) -> None:
        """Initialise.

        Args:
            thermostat: Parent thermostat entity (for current/target temp and entity_id).
            dispatcher: Optional event bus; if None all emit calls are no-ops.
            get_was_clamped: Callback returning PID ``was_clamped`` flag; None → False.
            reset_clamp_state: Callback to clear ``was_clamped`` at cycle start; None → no-op.
        """
        self._thermostat = thermostat
        self._dispatcher = dispatcher
        self._get_was_clamped = get_was_clamped
        self._reset_clamp_state = reset_clamp_state

        # Session state
        self._cycle_active: bool = False
        self._has_demand: bool = False

        # Actuator wear tracking
        self._heater_cycle_count: int = 0
        self._cooler_cycle_count: int = 0
        self._last_heater_state: bool = False
        self._last_cooler_state: bool = False

    # ── Session-state properties ───────────────────────────────────────────────

    @property
    def cycle_active(self) -> bool:
        """Whether a heating/cooling cycle is currently active."""
        return self._cycle_active

    @cycle_active.setter
    def cycle_active(self, value: bool) -> None:
        self._cycle_active = value

    @property
    def has_demand(self) -> bool:
        """Whether the PID output is currently non-zero (demand present)."""
        return self._has_demand

    @has_demand.setter
    def has_demand(self, value: bool) -> None:
        self._has_demand = value

    @property
    def last_heater_state(self) -> bool:
        """Last-known heater on/off state (used for on→off edge detection)."""
        return self._last_heater_state

    @last_heater_state.setter
    def last_heater_state(self, value: bool) -> None:
        self._last_heater_state = value

    @property
    def last_cooler_state(self) -> bool:
        """Last-known cooler on/off state (used for on→off edge detection)."""
        return self._last_cooler_state

    @last_cooler_state.setter
    def last_cooler_state(self, value: bool) -> None:
        self._last_cooler_state = value

    @property
    def heater_cycle_count(self) -> int:
        """Total number of heater on→off cycles."""
        return self._heater_cycle_count

    @property
    def cooler_cycle_count(self) -> int:
        """Total number of cooler on→off cycles."""
        return self._cooler_cycle_count

    # ── PID clamp-state callbacks ──────────────────────────────────────────────

    def get_pid_was_clamped(self) -> bool:
        """Return ``was_clamped`` from the PID controller via callback.

        Returns:
            True if PID reports clamping occurred, False if unavailable.
        """
        if self._get_was_clamped is None:
            return False
        return self._get_was_clamped()

    def reset_pid_clamp_state(self) -> None:
        """Reset PID clamp state at cycle start via callback."""
        if self._reset_clamp_state is not None:
            self._reset_clamp_state()

    # ── State management ───────────────────────────────────────────────────────

    def restore(self, cycle_active: bool, has_demand: bool) -> None:
        """Restore cycle tracking state after HA restart.

        Args:
            cycle_active: Whether a cycle was active before restart.
            has_demand: Whether demand was present before restart.
        """
        self._cycle_active = cycle_active
        self._has_demand = has_demand

    def set_heater_cycle_count(self, count: int) -> None:
        """Set heater cycle count during state restoration.

        Args:
            count: Cycle count to restore.
        """
        self._heater_cycle_count = count

    def set_cooler_cycle_count(self, count: int) -> None:
        """Set cooler cycle count during state restoration.

        Args:
            count: Cycle count to restore.
        """
        self._cooler_cycle_count = count

    def abort(self) -> None:
        """Reset cycle_active without emitting events.

        Timer cancellation is the caller's responsibility (HeaterTimerManager.cancel_all).
        """
        self._cycle_active = False

    def increment_cycle_count(self, hvac_mode: HVACMode, is_now_off: bool) -> None:
        """Increment the appropriate cycle counter on an on→off transition.

        Args:
            hvac_mode: Current HVAC mode (determines heater vs cooler counter).
            is_now_off: True if the device just turned off.
        """
        if not is_now_off:
            return

        if hvac_mode == HVACMode.COOL:
            if self._last_cooler_state:
                self._cooler_cycle_count += 1
                _LOGGER.debug(
                    "%s: Cooler cycle count incremented to %d",
                    self._thermostat.entity_id,
                    self._cooler_cycle_count,
                )
            self._last_cooler_state = False
        else:
            if self._last_heater_state:
                self._heater_cycle_count += 1
                _LOGGER.debug(
                    "%s: Heater cycle count incremented to %d",
                    self._thermostat.entity_id,
                    self._heater_cycle_count,
                )
            self._last_heater_state = False

    # ── Event emission ─────────────────────────────────────────────────────────

    def emit_cycle_started(self, hvac_mode: HVACMode, at: float | None = None) -> None:
        """Emit CycleStartedEvent with current temperature state.

        Args:
            hvac_mode: Current HVAC mode.
        """
        if self._dispatcher:
            target_temp = getattr(self._thermostat, "target_temperature", 0.0)
            current_temp = getattr(self._thermostat, "_current_temp", 0.0)
            self._dispatcher.emit(
                CycleStartedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=dt_util.utcnow() - timedelta(seconds=max(0.0, time.monotonic() - at))
                    if at is not None
                    else dt_util.utcnow(),
                    target_temp=target_temp,
                    current_temp=current_temp,
                )
            )

    def emit_settling_started(self, hvac_mode: HVACMode, was_clamped: bool) -> None:
        """Emit SettlingStartedEvent.

        Args:
            hvac_mode: Current HVAC mode.
            was_clamped: Whether the PID was clamped during this cycle.
        """
        if self._dispatcher:
            self._dispatcher.emit(
                SettlingStartedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=dt_util.utcnow(),
                    was_clamped=was_clamped,
                )
            )

    def emit_heating_started(self, hvac_mode: HVACMode, at: float | None = None) -> None:
        """Emit HeatingStartedEvent.

        Args:
            hvac_mode: Current HVAC mode.
        """
        if self._dispatcher:
            self._dispatcher.emit(
                HeatingStartedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=dt_util.utcnow() - timedelta(seconds=max(0.0, time.monotonic() - at))
                    if at is not None
                    else dt_util.utcnow(),
                )
            )

    def emit_heating_ended(
        self, hvac_mode: HVACMode, committed_heat_seconds: float = 0.0, at: float | None = None
    ) -> None:
        """Emit HeatingEndedEvent.

        Args:
            hvac_mode: Current HVAC mode.
            committed_heat_seconds: In-flight heat snapshot from the pipeline at valve close.
        """
        if self._dispatcher:
            self._dispatcher.emit(
                HeatingEndedEvent(
                    hvac_mode=hvac_mode,
                    timestamp=dt_util.utcnow() - timedelta(seconds=max(0.0, time.monotonic() - at))
                    if at is not None
                    else dt_util.utcnow(),
                    committed_heat_seconds=committed_heat_seconds,
                )
            )
