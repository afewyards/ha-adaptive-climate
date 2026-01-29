"""Setpoint boost manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import (
    HEATING_TYPE_BOOST_FACTORS,
    DEFAULT_SETPOINT_DEBOUNCE,
    HeatingType,
)

if TYPE_CHECKING:
    from datetime import datetime
    from ..pid_controller import PID

_LOGGER = logging.getLogger(__name__)


class SetpointBoostManager:
    """Manager for setpoint change boost and integral decay.

    Detects setpoint changes, debounces rapid clicks, applies integral boost
    for increases and decay for decreases to PID controller.

    Boost behavior:
    - When user increases setpoint: boost integral to speed up response
    - When user decreases setpoint: decay integral to prevent overshoot

    Debounce:
    - Rapid setpoint changes are accumulated during debounce window
    - Single boost/decay applied after debounce period expires
    """

    def __init__(
        self,
        hass: HomeAssistant,
        heating_type: HeatingType,
        pid_controller: PID,
        is_night_period_cb: Callable[[], bool],
        enabled: bool = True,
        boost_factor: float | None = None,
        debounce_seconds: int = DEFAULT_SETPOINT_DEBOUNCE,
    ):
        """Initialize the SetpointBoostManager.

        Args:
            hass: Home Assistant instance
            heating_type: Heating system type
            pid_controller: PID controller instance
            is_night_period_cb: Callback to check if night setback is active
            enabled: Whether setpoint boost is enabled
            boost_factor: Override default boost factor for this heating type
            debounce_seconds: Debounce window in seconds
        """
        self._hass = hass
        self._heating_type = heating_type
        self._pid = pid_controller
        self._is_night_period_cb = is_night_period_cb
        self._enabled = enabled
        self._debounce_seconds = debounce_seconds

        # Get boost/decay factors for this heating type
        default_boost, default_decay = HEATING_TYPE_BOOST_FACTORS.get(
            heating_type, (15.0, 0.20)
        )
        self._boost_factor = boost_factor if boost_factor is not None else default_boost
        self._decay_rate = default_decay

        # Debounce state
        self._pending_delta: float = 0.0
        self._debounce_timer = None

    def on_setpoint_change(self, old_temp: float, new_temp: float) -> None:
        """Handle setpoint change with debounce.

        Accumulates delta and resets debounce timer. After debounce period,
        applies single boost/decay for accumulated change.

        Args:
            old_temp: Previous setpoint temperature
            new_temp: New setpoint temperature
        """
        if not self._enabled:
            return

        delta = new_temp - old_temp

        # Skip tiny changes (noise)
        if abs(delta) < 0.3:
            return

        # Accumulate delta
        if self._debounce_timer is None:
            # First change - start accumulation
            self._pending_delta = delta
        else:
            # Subsequent change - cancel pending timer and accumulate
            self._debounce_timer()
            self._pending_delta += delta

        # Schedule debounced boost/decay
        self._debounce_timer = async_call_later(
            self._hass,
            self._debounce_seconds,
            self._apply_boost,
        )

        _LOGGER.debug(
            "Setpoint change: delta=%.2f, accumulated=%.2f, debounce=%ds",
            delta,
            self._pending_delta,
            self._debounce_seconds,
        )

    async def _apply_boost(self, _now: datetime) -> None:
        """Apply accumulated boost or decay after debounce period.

        Timer callback - applies integral boost for increases or decay for decreases.

        Args:
            _now: Current datetime (unused, required by async_call_later)
        """
        if not self._enabled:
            self._pending_delta = 0.0
            return

        delta = self._pending_delta
        self._pending_delta = 0.0
        self._debounce_timer = None

        # Skip if below threshold
        if abs(delta) < 0.3:
            _LOGGER.debug("Setpoint boost: delta=%.2f below threshold, skipping", delta)
            return

        # Skip during night setback
        if self._is_night_period_cb():
            _LOGGER.debug(
                "Setpoint boost: delta=%.2f skipped (night setback active)", delta
            )
            return

        if delta > 0:
            # Setpoint INCREASE - boost integral
            boost = delta * self._boost_factor
            cap = max(abs(self._pid.integral) * 0.5, 15.0)
            boost = min(boost, cap)

            self._pid.integral += boost

            _LOGGER.debug(
                "Setpoint boost: delta=+%.2f, boost=%.2f, new_integral=%.2f",
                delta,
                boost,
                self._pid.integral,
            )
        else:
            # Setpoint DECREASE - decay integral
            decay = max(0.3, 1.0 - abs(delta) * self._decay_rate)
            old_integral = self._pid.integral
            self._pid.integral *= decay

            _LOGGER.debug(
                "Setpoint decay: delta=%.2f, decay_factor=%.2f, integral %.2f -> %.2f",
                delta,
                decay,
                old_integral,
                self._pid.integral,
            )

    def cancel(self) -> None:
        """Cancel pending debounce timer."""
        if self._debounce_timer is not None:
            self._debounce_timer()
            self._debounce_timer = None
            self._pending_delta = 0.0
