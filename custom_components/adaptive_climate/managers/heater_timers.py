"""Valve-actuation and settling-debounce timer management for HeaterController."""

from __future__ import annotations

from typing import Any

try:
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.helpers.event import async_call_later
    from homeassistant.components.climate import HVACMode

    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any  # type: ignore[assignment,misc]
    HVACMode = Any  # type: ignore[assignment,misc]

    def callback(fn):  # type: ignore[misc]
        return fn

    def async_call_later(*args: Any) -> Any:  # type: ignore[misc]
        del args
        return lambda: None


from .heater_cycle_bookkeeper import HeaterCycleBookkeeper
from ..const import MIN_OUTPUT_THRESHOLD


class HeaterTimerManager:
    """Owns async timer handles for valve actuation delays and settling debounce.

    Responsible for:
    - Scheduling/cancelling the valve-open and valve-close delay timers
      (HEATING_STARTED / HEATING_ENDED delayed signals).
    - Scheduling/cancelling the demand→0 debounce timer (multi-PWM aggregation).
    - Scheduling/cancelling the low-output maintenance timeout.
    - Firing the appropriate @callback when each timer expires.

    All emitted events and session-state updates go through ``HeaterCycleBookkeeper``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        thermostat_entity_id: str,
        pwm: int,
        valve_actuation_time: float,
        bookkeeper: HeaterCycleBookkeeper,
    ) -> None:
        """Initialise.

        Args:
            hass: Home Assistant instance.
            thermostat_entity_id: Used for debug logging.
            pwm: PWM period in seconds (used to size debounce = 2×PWM).
            valve_actuation_time: Seconds for motorised valve to fully open.
            bookkeeper: Cycle bookkeeper used for state updates and event emission.
        """
        self._hass = hass
        self._thermostat_entity_id = thermostat_entity_id
        self._pwm = pwm
        self._valve_actuation_time = valve_actuation_time
        self._bookkeeper = bookkeeper

        # HA timer cancel-handles (callable → cancels the scheduled call)
        self._valve_open_timer: Any | None = None
        self._valve_close_timer: Any | None = None
        self._demand_zero_timer: Any | None = None
        self._low_output_timer: Any | None = None

    # ── State queries ──────────────────────────────────────────────────────────

    @property
    def demand_zero_active(self) -> bool:
        """Return True if the demand→0 debounce timer is currently running."""
        return self._demand_zero_timer is not None

    @property
    def low_output_active(self) -> bool:
        """Return True if the low-output maintenance timeout is currently running."""
        return self._low_output_timer is not None

    def update_learning_demand(self, control_output: float, hvac_mode: HVACMode) -> None:
        """Keep one continuous low-demand window, independently of relay PWM.

        Zero and tiny positive demand belong to the same learning interval.
        Switching between them must not restart the two-period settling timer.
        """
        bookkeeper = self._bookkeeper
        old_has_demand = bookkeeper.has_demand
        new_has_demand = abs(control_output) > 0
        bookkeeper.has_demand = new_has_demand
        if self._pwm and bookkeeper._dispatcher:
            if bookkeeper.cycle_active and abs(control_output) < MIN_OUTPUT_THRESHOLD:
                if not self.demand_zero_active:
                    self.schedule_demand_zero_debounce(hvac_mode, bookkeeper.get_pid_was_clamped())
            else:
                self.cancel_demand_zero()
            self.cancel_low_output()
        elif old_has_demand and not new_has_demand and bookkeeper.cycle_active:
            bookkeeper.emit_settling_started(hvac_mode, bookkeeper.get_pid_was_clamped())
            bookkeeper.cycle_active = False

    # ── Bulk cancellation ──────────────────────────────────────────────────────

    def cancel_all(self) -> None:
        """Cancel every pending timer. Call on entity removal or forced shutdown."""
        if self._demand_zero_timer is not None:
            self._demand_zero_timer()
            self._demand_zero_timer = None
        if self._low_output_timer is not None:
            self._low_output_timer()
            self._low_output_timer = None
        if self._valve_open_timer is not None:
            self._valve_open_timer()
            self._valve_open_timer = None
        if self._valve_close_timer is not None:
            self._valve_close_timer()
            self._valve_close_timer = None

    # ── Valve-open delay (HEATING_STARTED) ────────────────────────────────────

    def cancel_valve_open(self) -> None:
        """Cancel the pending valve-open delay timer, if any."""
        if self._valve_open_timer is not None:
            self._valve_open_timer()
            self._valve_open_timer = None

    def schedule_heating_started(self, hvac_mode: HVACMode) -> None:
        """Schedule delayed HEATING_STARTED after valve fully opens.

        Args:
            hvac_mode: Current HVAC mode (forwarded to the event).
        """
        self._valve_open_timer = async_call_later(
            self._hass,
            self._valve_actuation_time,
            lambda _: self._on_heating_started(hvac_mode),
        )

    @callback
    def _on_heating_started(self, hvac_mode: HVACMode) -> None:
        """Fire when the valve-open delay elapses → emit HEATING_STARTED."""
        self._bookkeeper.emit_heating_started(hvac_mode)
        self._valve_open_timer = None

    # ── Valve-close delay (HEATING_ENDED) ─────────────────────────────────────

    def schedule_heating_ended(self, hvac_mode: HVACMode, committed_heat_seconds: float) -> None:
        """Schedule delayed HEATING_ENDED at half the valve-close time.

        Args:
            hvac_mode: Current HVAC mode (forwarded to the event).
            committed_heat_seconds: In-flight heat snapshot to carry in the event.
        """
        half_valve_time = self._valve_actuation_time / 2.0
        self._valve_close_timer = async_call_later(
            self._hass,
            half_valve_time,
            lambda _, c=committed_heat_seconds: self._on_heating_ended(hvac_mode, c),
        )

    @callback
    def _on_heating_ended(self, hvac_mode: HVACMode, committed_heat_seconds: float) -> None:
        """Fire when the half-valve-close delay elapses → emit HEATING_ENDED."""
        self._bookkeeper.emit_heating_ended(hvac_mode, committed_heat_seconds)
        self._valve_close_timer = None

    # ── Demand→0 debounce (SETTLING_STARTED) ──────────────────────────────────

    def schedule_demand_zero_debounce(self, hvac_mode: HVACMode, was_clamped: bool) -> None:
        """Start a 2×PWM debounce timer before emitting SETTLING_STARTED.

        Allows brief demand=0 dips during multi-cycle aggregation without
        prematurely closing the heating session. Also cancels any concurrent
        low-output timer (mutual exclusion).

        Args:
            hvac_mode: Current HVAC mode (forwarded to the event).
            was_clamped: PID clamp state captured at time of demand drop.
        """
        if self._low_output_timer is not None:
            self._low_output_timer()
            self._low_output_timer = None
        self._demand_zero_timer = async_call_later(
            self._hass,
            float(2 * self._pwm),
            lambda _: self._on_demand_zero(hvac_mode, was_clamped),
        )

    def cancel_demand_zero(self) -> None:
        """Cancel the demand→0 debounce timer (demand returned before it fired)."""
        if self._demand_zero_timer is not None:
            self._demand_zero_timer()
            self._demand_zero_timer = None

    @callback
    def _on_demand_zero(self, hvac_mode: HVACMode, was_clamped: bool) -> None:
        """Fire when the demand→0 debounce elapses → emit SETTLING_STARTED.

        Also cancels any simultaneously-pending low-output timer.
        """
        self._demand_zero_timer = None
        if self._low_output_timer is not None:
            self._low_output_timer()
            self._low_output_timer = None
        if self._bookkeeper.cycle_active:
            self._bookkeeper.emit_settling_started(hvac_mode, was_clamped)
        self._bookkeeper.cycle_active = False

    # ── Low-output maintenance timeout (SETTLING_STARTED) ─────────────────────

    def schedule_low_output_timeout(self, hvac_mode: HVACMode, was_clamped: bool) -> None:
        """Start a 2×PWM timeout for maintenance cycles that never reach zero output.

        Replaces the v0.28 ``async_turn_off()`` SETTLING_STARTED path for cycles
        where ``control_output`` hovers below ``MIN_OUTPUT_THRESHOLD`` without dropping
        to 0.

        Args:
            hvac_mode: Current HVAC mode (forwarded to the event).
            was_clamped: PID clamp state captured at timeout start.
        """
        self._low_output_timer = async_call_later(
            self._hass,
            float(2 * self._pwm),
            lambda _: self._on_low_output(hvac_mode, was_clamped),
        )

    def cancel_low_output(self) -> None:
        """Cancel the low-output maintenance timer (output rose above threshold)."""
        if self._low_output_timer is not None:
            self._low_output_timer()
            self._low_output_timer = None

    @callback
    def _on_low_output(self, hvac_mode: HVACMode, was_clamped: bool) -> None:
        """Fire when the low-output maintenance timeout elapses → emit SETTLING_STARTED.

        Also cancels any simultaneously-pending demand→0 timer.
        """
        self._low_output_timer = None
        if self._demand_zero_timer is not None:
            self._demand_zero_timer()
            self._demand_zero_timer = None
        if self._bookkeeper.cycle_active:
            self._bookkeeper.emit_settling_started(hvac_mode, was_clamped)
        self._bookkeeper.cycle_active = False
