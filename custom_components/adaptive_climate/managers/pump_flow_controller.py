"""Requests the summed design flow of open, fully actuated zones from one system pump."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_MANIFOLD_ZONE_ENTITY,
    CONF_MANIFOLD_ZONE_FLOW,
    CONF_PUMP_FLOW_MIN_FLOW,
    CONF_PUMP_FLOW_MIN_WRITE_INTERVAL,
    CONF_PUMP_FLOW_TARGET_ENTITY,
    DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL,
    PUMP_FLOW_BINDING_ENTITY_LIMIT,
    PUMP_FLOW_DEBOUNCE_SECONDS,
    PUMP_FLOW_STARTUP_DELAY_SECONDS,
    PUMP_FLOW_UPDATE_INTERVAL_SECONDS,
    PUMP_FLOW_WARN_INTERVAL_SECONDS,
)
from .heater_service_caller import HeaterServiceCaller
from .pump_flow_math import FLOW_EPSILON, decide_write, flow_limits, parse_current, round_up, target_flow

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _cancel(unsub: CALLBACK_TYPE | None) -> None:
    if unsub is not None:
        unsub()


class PumpFlowController:
    """Writes the summed flow of open, fully actuated zones to one pump setpoint entity.

    A zone's flow joins the sum ``valve_actuation_time`` seconds after its valve
    opens and leaves it the moment the valve closes. Increases and the drop to
    idle are written after a short coalescing window; other decreases must
    persist for ``min_write_interval`` first.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        zone_flows: dict[str, float],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hass = hass
        self._target_entity: str = config[CONF_PUMP_FLOW_TARGET_ENTITY]
        min_flow = config.get(CONF_PUMP_FLOW_MIN_FLOW)
        self._min_flow: float | None = None if min_flow is None else float(min_flow)
        self._min_write_interval = float(
            config.get(CONF_PUMP_FLOW_MIN_WRITE_INTERVAL, DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL)
        )
        self._zone_flows = dict(zone_flows)
        self._monotonic = monotonic

        self._counts_from: dict[str, float] = {}
        self._started = False
        self._decrease_pending_since: float | None = None
        self._requested: float | None = None
        self._raw_sum = 0.0
        self._binding: str | None = None
        self._last_write_at: datetime | None = None
        self._last_written: float | None = None
        self._last_error_at: float | None = None
        self._last_limit_warn_at: float | None = None
        self._last_missing_warn_at: float | None = None

        self._started_unsub: CALLBACK_TYPE | None = None
        self._startup_unsub: CALLBACK_TYPE | None = None
        self._interval_unsub: CALLBACK_TYPE | None = None
        self._window_unsub: CALLBACK_TYPE | None = None
        self._maturity_unsub: CALLBACK_TYPE | None = None
        self._decrease_unsub: CALLBACK_TYPE | None = None

    @classmethod
    def from_config(
        cls,
        hass: HomeAssistant,
        config: dict[str, Any],
        manifolds_config: list[dict[str, Any]],
    ) -> PumpFlowController:
        """Build the controller from validated ``pump_flow_control`` and ``manifolds`` config."""
        zone_flows = {
            zone[CONF_MANIFOLD_ZONE_ENTITY]: float(zone[CONF_MANIFOLD_ZONE_FLOW])
            for manifold in manifolds_config
            for zone in manifold["zones"]
        }
        return cls(hass, config, zone_flows)

    # ── demand intake ────────────────────────────────────────────────────────

    def on_zone_demand(self, zone_entity_id: str, is_open: bool, valve_actuation_time: float) -> None:
        """Record a zone valve opening or closing.

        Re-reporting an already open valve keeps its original timing, so an HVAC
        mode change while the valve stays open does not restart the wait.
        """
        if zone_entity_id not in self._zone_flows:
            return
        if is_open:
            if zone_entity_id in self._counts_from:
                return
            self._counts_from[zone_entity_id] = self._monotonic() + max(0.0, valve_actuation_time)
        elif self._counts_from.pop(zone_entity_id, None) is None:
            return
        self._schedule_maturity()
        self._request_evaluation()

    # ── scheduling ───────────────────────────────────────────────────────────

    def _schedule_maturity(self) -> None:
        _cancel(self._maturity_unsub)
        self._maturity_unsub = None
        now = self._monotonic()
        pending = [counts_from for counts_from in self._counts_from.values() if counts_from > now]
        if pending:
            self._maturity_unsub = async_call_later(self.hass, min(pending) - now, self._async_on_maturity)

    def _request_evaluation(self) -> None:
        if not self._started or self._window_unsub is not None:
            return
        self._window_unsub = async_call_later(self.hass, PUMP_FLOW_DEBOUNCE_SECONDS, self._async_on_window)

    async def _async_on_maturity(self, _now: datetime | None) -> None:
        self._maturity_unsub = None
        self._schedule_maturity()
        self._request_evaluation()

    async def _async_on_window(self, _now: datetime | None) -> None:
        self._window_unsub = None
        await self._async_run()

    async def _async_on_decrease_due(self, _now: datetime | None) -> None:
        self._decrease_unsub = None
        await self._async_run()

    # ── evaluation ───────────────────────────────────────────────────────────

    async def _async_run(self) -> None:
        try:
            await self._async_evaluate()
        except Exception:  # broad: one bad cycle must not kill the timers
            _LOGGER.exception("Pump flow control: evaluation failed")

    async def _async_evaluate(self) -> None:
        if not self._started:
            return
        now = self._monotonic()
        counted = [self._zone_flows[zone] for zone, counts_from in self._counts_from.items() if counts_from <= now]
        self._raw_sum = sum(counted)
        target = target_flow(counted, self._min_flow)

        state = self.hass.states.get(self._target_entity)
        if state is None:
            self._warn_missing_target(now)
            return

        minimum, maximum, step = flow_limits(state)
        rounded = round_up(target, step)
        final = min(max(rounded, minimum), maximum)
        self._requested = final
        self._binding = None
        if abs(final - rounded) >= FLOW_EPSILON:
            self._binding = PUMP_FLOW_BINDING_ENTITY_LIMIT
            idle_clamped_to_minimum = rounded <= FLOW_EPSILON and abs(final - minimum) < FLOW_EPSILON
            if not idle_clamped_to_minimum:
                self._warn_entity_limited(rounded, final, now)

        current = parse_current(state)
        if current is None:
            current = self._last_written

        decision = decide_write(
            final,
            current,
            step=step,
            idle=not counted,
            pending_since=self._decrease_pending_since,
            now=now,
            min_write_interval=self._min_write_interval,
        )
        self._decrease_pending_since = decision.decrease_pending_since
        _cancel(self._decrease_unsub)
        self._decrease_unsub = None
        if decision.recheck_in is not None:
            self._decrease_unsub = async_call_later(self.hass, decision.recheck_in, self._async_on_decrease_due)
        if decision.write:
            await self._async_write(final, now)

    async def _async_write(self, value: float, now: float) -> None:
        domain = HeaterServiceCaller.get_number_entity_domain(self._target_entity)
        try:
            await self.hass.services.async_call(
                domain,
                "set_value",
                {"entity_id": self._target_entity, "value": value},
                blocking=True,
            )
        except ServiceNotFound as err:
            self._log_write_error(now, "service '%s.set_value' not found: %s", domain, err)
            return
        except HomeAssistantError as err:
            self._log_write_error(now, "Home Assistant error: %s", err)
            return
        except Exception as err:  # broad: a failed write must not kill the timers
            self._log_write_error(now, "unexpected error: %s", err)
            return
        self._last_error_at = None
        self._last_write_at = dt_util.utcnow()
        self._last_written = value
        _LOGGER.debug("Pump flow control: wrote %.2f L/min to %s", value, self._target_entity)

    def _log_write_error(self, now: float, message: str, *args: Any) -> None:
        if self._last_error_at is not None and now - self._last_error_at < PUMP_FLOW_WARN_INTERVAL_SECONDS:
            return
        self._last_error_at = now
        _LOGGER.error("Pump flow control: failed to write %s — " + message, self._target_entity, *args)

    def _warn_entity_limited(self, desired: float, actual: float, now: float) -> None:
        last = self._last_limit_warn_at
        if last is not None and now - last < PUMP_FLOW_WARN_INTERVAL_SECONDS:
            return
        self._last_limit_warn_at = now
        _LOGGER.warning(
            "Pump flow control: %s cannot take %.2f L/min (entity limit clamps to %.2f L/min)",
            self._target_entity,
            desired,
            actual,
        )

    def _warn_missing_target(self, now: float) -> None:
        last = self._last_missing_warn_at
        if last is not None and now - last < PUMP_FLOW_WARN_INTERVAL_SECONDS:
            return
        self._last_missing_warn_at = now
        _LOGGER.warning("Pump flow control: target entity %s not found", self._target_entity)

    def _warn_on_config_problems(self) -> None:
        for zone in sorted(self._zone_flows):
            if self.hass.states.get(zone) is None:
                _LOGGER.warning("Pump flow control: manifold zone %s has no entity; its flow is never requested", zone)
        state = self.hass.states.get(self._target_entity)
        if state is None:
            return
        unit = state.attributes.get("unit_of_measurement")
        if unit is not None and unit != "L/min":
            _LOGGER.warning(
                "Pump flow control: target entity %s reports unit %s, expected L/min", self._target_entity, unit
            )

    # ── diagnostics ──────────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        """Return the current request and the state behind it, for the diagnostic sensor."""
        now = self._monotonic()
        pending_since: str | None = None
        if self._decrease_pending_since is not None:
            waited = timedelta(seconds=now - self._decrease_pending_since)
            pending_since = (dt_util.utcnow() - waited).isoformat()
        return {
            "requested": self._requested,
            "open_zones": sorted(zone for zone, counts_from in self._counts_from.items() if counts_from <= now),
            "pending_zones": {
                zone: round(counts_from - now, 1)
                for zone, counts_from in sorted(self._counts_from.items())
                if counts_from > now
            },
            "raw_sum": round(self._raw_sum, 3),
            "min_flow_applied": self._min_flow is not None and 0 < self._raw_sum < self._min_flow,
            "decrease_pending_since": pending_since,
            "binding_constraint": self._binding,
            "last_write": None if self._last_write_at is None else self._last_write_at.isoformat(),
        }

    # ── lifecycle ────────────────────────────────────────────────────────────

    def async_start(self) -> None:
        """Register the startup evaluation and the periodic re-evaluation (sync, for ``coordinator.__init__``)."""
        self._started_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._async_on_ha_started)
        self._interval_unsub = async_track_time_interval(
            self.hass,
            self._async_timer_tick,
            timedelta(seconds=PUMP_FLOW_UPDATE_INTERVAL_SECONDS),
        )

    def async_cleanup(self) -> None:
        """Cancel every listener and timer. Called from coordinator cleanup."""
        for unsub in (
            self._started_unsub,
            self._startup_unsub,
            self._interval_unsub,
            self._window_unsub,
            self._maturity_unsub,
            self._decrease_unsub,
        ):
            _cancel(unsub)
        self._started_unsub = None
        self._startup_unsub = None
        self._interval_unsub = None
        self._window_unsub = None
        self._maturity_unsub = None
        self._decrease_unsub = None

    async def _async_on_ha_started(self, _event: Any) -> None:
        self._started_unsub = None
        self._startup_unsub = async_call_later(self.hass, PUMP_FLOW_STARTUP_DELAY_SECONDS, self._async_startup)

    async def _async_startup(self, _now: datetime | None) -> None:
        """Start writing. Valves reported open by now were open before the restart, so they count at once."""
        self._startup_unsub = None
        now = self._monotonic()
        self._counts_from = {zone: min(counts_from, now) for zone, counts_from in self._counts_from.items()}
        self._schedule_maturity()
        self._warn_on_config_problems()
        self._started = True
        await self._async_run()

    async def _async_timer_tick(self, _now: datetime | None) -> None:
        await self._async_run()
