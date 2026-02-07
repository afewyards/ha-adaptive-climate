"""Night setback controller manager for Adaptive Climate integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant
    from homeassistant.util import dt as dt_util

    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any
    dt_util = None

from ..adaptive.night_setback import NightSetback
from .night_setback_calculator import NightSetbackCalculator
from ..const import (
    AUTO_LEARNING_SETBACK_DELTA,
    AUTO_LEARNING_SETBACK_WINDOW_START,
    AUTO_LEARNING_SETBACK_WINDOW_END,
    AUTO_LEARNING_SETBACK_TRIGGER_DAYS,
    AUTO_LEARNING_SETBACK_COOLDOWN_DAYS,
)

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class NightSetbackManager:
    """Manager for night setback temperature adjustments.

    Manages the calculation of effective setpoint temperatures based on
    night setback configuration, sunrise/sunset times, weather conditions,
    and solar recovery logic. Delegates calculation logic to NightSetbackCalculator.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        night_setback: NightSetback | None,
        night_setback_config: dict[str, Any] | None,
        solar_recovery: Any | None,
        window_orientation: str | None,
        get_target_temp: Callable[[], float | None],
        get_current_temp: Callable[[], float | None],
        preheat_learner: Any | None = None,
        preheat_enabled: bool = False,
        manifold_transport_delay: float = 0.0,
        get_learning_status: Callable[[], str] | None = None,
        get_allowed_setback_delta: Callable[[], float | None] | None = None,
        auto_learning_enabled: bool = True,
    ):
        """Initialize the NightSetbackManager.

        Args:
            hass: Home Assistant instance
            entity_id: Entity ID of the thermostat (for logging)
            night_setback: NightSetback instance (for static end time mode)
            night_setback_config: Night setback configuration dict (for dynamic mode)
            solar_recovery: Deprecated parameter (ignored, will be removed)
            window_orientation: Window orientation for solar calculations
            get_target_temp: Callback to get current target temperature
            get_current_temp: Callback to get current temperature
            preheat_learner: Optional PreheatLearner instance for time estimation
            preheat_enabled: Whether preheat functionality is enabled
            manifold_transport_delay: Manifold transport delay in minutes (default: 0.0)
            get_learning_status: Optional callback to get current learning status
                (DEPRECATED - use get_allowed_setback_delta)
            get_allowed_setback_delta: Optional callback to get allowed setback delta based on learning progress
            auto_learning_enabled: Whether auto-learning setback is enabled (default: True)
        """
        self._entity_id = entity_id
        self._get_learning_status = get_learning_status
        self._get_allowed_setback_delta = get_allowed_setback_delta
        self._auto_learning_enabled = auto_learning_enabled

        # Initialize the calculator for pure calculation logic
        self._calculator = NightSetbackCalculator(
            hass=hass,
            entity_id=entity_id,
            night_setback=night_setback,
            night_setback_config=night_setback_config,
            window_orientation=window_orientation,
            get_target_temp=get_target_temp,
            get_current_temp=get_current_temp,
            preheat_learner=preheat_learner,
            preheat_enabled=preheat_enabled,
            manifold_transport_delay=manifold_transport_delay,
        )

        # Grace period tracking state variables (state management, not calculation)
        self._learning_grace_until: datetime | None = None
        self._night_setback_was_active: bool | None = None
        self._learning_suppressed: bool = False

        # Auto-learning setback tracking
        self._days_at_maintenance_cap: int = 0
        self._last_auto_setback: datetime | None = None

    @property
    def calculator(self) -> NightSetbackCalculator:
        """Return the underlying calculator for direct access if needed."""
        return self._calculator

    @property
    def is_configured(self) -> bool:
        """Return True if night setback is configured."""
        return self._calculator.is_configured

    def _is_learning_stable(self) -> bool:
        """Check if learning is stable enough for night setback to be active.

        Returns:
            True if learning status callback is None (backwards compat) or
            if status is "tuned" or "optimized".
            False if status is "idle", "collecting", or "stable".

        Note: "stable" status means system is stable but not yet tuned, so we
        suppress night setback until it reaches "tuned" status to ensure
        PID has collected enough data before applying temperature reductions.
        """
        if self._get_learning_status is None:
            return True

        status = self._get_learning_status()
        return status in ("tuned", "optimized")

    @property
    def in_learning_grace_period(self) -> bool:
        """Check if learning should be paused due to recent night setback transition."""
        if self._learning_grace_until is None:
            return False
        return dt_util.utcnow() < self._learning_grace_until

    @property
    def learning_grace_until(self) -> datetime | None:
        """Return the time until which learning is paused."""
        return self._learning_grace_until

    def set_learning_grace_period(self, minutes: int = 60) -> None:
        """Set a grace period to pause learning after night setback transitions.

        Args:
            minutes: Number of minutes to pause learning
        """
        self._learning_grace_until = dt_util.utcnow() + timedelta(minutes=minutes)
        _LOGGER.info(
            "%s: Learning grace period set for %d minutes (until %s)",
            self._entity_id,
            minutes,
            self._learning_grace_until.strftime("%H:%M"),
        )

    def update_days_at_maintenance_cap(self, at_cap: bool) -> None:
        """Update tracking of days stuck at maintenance cap.

        Args:
            at_cap: Whether the zone is currently stuck at the maintenance cap
        """
        if at_cap:
            self._days_at_maintenance_cap += 1
            _LOGGER.debug("%s: Days at maintenance cap: %d", self._entity_id, self._days_at_maintenance_cap)
        else:
            if self._days_at_maintenance_cap > 0:
                _LOGGER.debug(
                    "%s: Resetting days at maintenance cap (was %d)", self._entity_id, self._days_at_maintenance_cap
                )
            self._days_at_maintenance_cap = 0

    def should_apply_auto_learning_setback(self) -> bool:
        """Check if auto-learning setback should be applied.

        Returns:
            True if all conditions are met for auto-learning setback
        """
        # Feature must be enabled
        if not self._auto_learning_enabled:
            return False

        # Don't apply if user has configured night setback
        if self._calculator.is_configured:
            return False

        # Don't apply if already tuned or optimized
        if self._get_learning_status is not None:
            status = self._get_learning_status()
            if status in ("tuned", "optimized"):
                return False

        # Check if we've been at cap for trigger days
        if self._days_at_maintenance_cap < AUTO_LEARNING_SETBACK_TRIGGER_DAYS:
            return False

        # Check cooldown period
        if self._last_auto_setback is not None:
            days_since_last = (dt_util.utcnow() - self._last_auto_setback).days
            if days_since_last < AUTO_LEARNING_SETBACK_COOLDOWN_DAYS:
                return False

        return True

    def _is_in_auto_learning_window(self, current_time: datetime) -> bool:
        """Check if current time is within auto-learning setback window (3-5am).

        Args:
            current_time: Current datetime

        Returns:
            True if within the auto-learning window
        """
        hour = current_time.hour
        return AUTO_LEARNING_SETBACK_WINDOW_START <= hour < AUTO_LEARNING_SETBACK_WINDOW_END

    def calculate_night_setback_adjustment(
        self, current_time: datetime | None = None
    ) -> tuple[float, bool, dict[str, Any]]:
        """Calculate night setback adjustment for effective target temperature.

        Handles both static end time (NightSetback object) and dynamic end time
        (sunrise/orientation/weather-based) configurations.

        Applies graduated setback delta based on learning progress when callback is provided.

        Also applies auto-learning setback when conditions are met.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            A tuple of (effective_target, in_night_period, night_setback_info) where:
            - effective_target: The adjusted target temperature
            - in_night_period: Whether we are currently in the night setback period
            - night_setback_info: Dict with additional info for state attributes
        """
        if current_time is None:
            current_time = dt_util.utcnow()

        # Check if auto-learning setback should apply
        if self.should_apply_auto_learning_setback() and self._is_in_auto_learning_window(current_time):
            target_temp = self._calculator._get_target_temp()
            if target_temp is None:
                target_temp = 20.0  # Fallback

            effective_target = target_temp - AUTO_LEARNING_SETBACK_DELTA
            info = {
                "night_setback_delta": AUTO_LEARNING_SETBACK_DELTA,
                "effective_delta": AUTO_LEARNING_SETBACK_DELTA,
                "auto_learning": True,
            }

            # Update last auto setback time when we first enter the window
            if self._last_auto_setback is None or (current_time - self._last_auto_setback).days > 0:
                self._last_auto_setback = current_time
                _LOGGER.info(
                    "%s: Auto-learning setback activated (stuck at cap for %d days)",
                    self._entity_id,
                    self._days_at_maintenance_cap,
                )

            return effective_target, True, info

        # First calculate what the full setback would be
        effective_target, in_night_period, info = self._calculator.calculate_night_setback_adjustment(current_time)

        # If not in night period, return as-is with zero effective delta
        if not in_night_period:
            # Clear suppression flag if we were suppressed
            if self._learning_suppressed:
                self._learning_suppressed = False
            info["effective_delta"] = 0.0
            return effective_target, in_night_period, info

        # Apply graduated delta if callback is provided
        if self._get_allowed_setback_delta is not None:
            allowed_delta = self._get_allowed_setback_delta()
            target_temp = self._calculator._get_target_temp()
            if target_temp is None:
                target_temp = 20.0  # Fallback

            # Calculate what the configured delta would be
            configured_delta = target_temp - effective_target  # How much setback was calculated

            if allowed_delta == 0.0:
                # Fully suppressed - no setback
                if not self._learning_suppressed:
                    _LOGGER.info("Night setback fully suppressed - learning not ready")
                    self._learning_suppressed = True
                return (
                    target_temp,
                    True,
                    {"night_setback_delta": 0.0, "effective_delta": 0.0, "suppressed_reason": "learning"},
                )

            elif allowed_delta is not None and allowed_delta > 0.0:
                # Partially allowed - cap the delta
                capped_delta = min(configured_delta, allowed_delta)
                capped_target = target_temp - capped_delta

                # Build info dict with night_setback_delta and effective_delta
                result_info = {"night_setback_delta": capped_delta, "effective_delta": capped_delta}

                # Add suppressed_reason if delta is reduced
                if capped_delta < configured_delta:
                    if not self._learning_suppressed:
                        _LOGGER.info(
                            "Night setback capped to %.1f°C (allowed: %.1f°C, configured: %.1f°C)",
                            capped_delta,
                            allowed_delta,
                            configured_delta,
                        )
                        self._learning_suppressed = True
                    result_info["suppressed_reason"] = "limited"
                else:
                    # Full configured delta allowed (allowed_delta >= configured_delta)
                    if self._learning_suppressed:
                        _LOGGER.info("Night setback no longer capped - full configured delta allowed")
                        self._learning_suppressed = False

                return (capped_target, True, result_info)

            else:
                # allowed_delta is None - unlimited (full setback allowed)
                if self._learning_suppressed:
                    _LOGGER.info("Night setback enabled - learning reached stable status")
                    self._learning_suppressed = False
                # Add night_setback_delta and effective_delta to info for consistency
                info["night_setback_delta"] = configured_delta
                info["effective_delta"] = configured_delta

        # Fallback to old behavior if callback not provided
        elif not self._is_learning_stable():
            target_temp = self._calculator._get_target_temp()
            if target_temp is None:
                target_temp = 20.0  # Fallback

            # Log when suppression state changes
            if not self._learning_suppressed:
                status = self._get_learning_status() if self._get_learning_status else "unknown"
                _LOGGER.info("Night setback suppressed - learning status: %s", status)
                self._learning_suppressed = True

            return (target_temp, True, {"suppressed_reason": "learning", "effective_delta": 0.0})
        else:
            # If we were previously suppressed but now stable, log the transition
            if self._learning_suppressed:
                _LOGGER.info("Night setback enabled - learning reached stable status")
                self._learning_suppressed = False

            # Calculate effective delta for old callback path
            target_temp = self._calculator._get_target_temp()
            if target_temp is None:
                target_temp = 20.0
            configured_delta = target_temp - effective_target
            info["effective_delta"] = configured_delta

        # Handle transition detection for learning grace period (state management)
        if self._calculator.is_configured:
            if self._night_setback_was_active is not None and in_night_period != self._night_setback_was_active:
                transition = "started" if in_night_period else "ended"
                _LOGGER.info("%s: Night setback %s - setting learning grace period", self._entity_id, transition)
                self.set_learning_grace_period(minutes=60)
            self._night_setback_was_active = in_night_period

        return effective_target, in_night_period, info

    def calculate_effective_setpoint(self, current_time: datetime | None = None) -> float:
        """Calculate the effective setpoint with night setback applied.

        This is the main interface method for getting the adjusted target temperature.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            The effective target temperature after night setback adjustments
        """
        effective_target, _, _ = self.calculate_night_setback_adjustment(current_time)
        return effective_target

    def get_state_attributes(self, current_time: datetime | None = None) -> dict[str, Any]:
        """Get state attributes for the night setback status.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            Dictionary of state attributes
        """
        attributes: dict[str, Any] = {}

        if self._calculator.is_configured:
            _, _, night_info = self.calculate_night_setback_adjustment(current_time)
            attributes.update(night_info)

        # Learning grace period (after night setback transitions)
        if self.in_learning_grace_period:
            attributes["learning_paused"] = True
            if self._learning_grace_until:
                attributes["learning_resumes"] = self._learning_grace_until.strftime("%H:%M")

        return attributes

    def restore_state(
        self,
        learning_grace_until: datetime | None = None,
        night_setback_was_active: bool | None = None,
        days_at_maintenance_cap: int = 0,
        last_auto_setback: datetime | None = None,
    ) -> None:
        """Restore state from saved data.

        Args:
            learning_grace_until: Restored learning grace until time
            night_setback_was_active: Restored night setback active state
            days_at_maintenance_cap: Restored days at maintenance cap counter
            last_auto_setback: Restored last auto setback datetime
        """
        self._learning_grace_until = learning_grace_until
        self._night_setback_was_active = night_setback_was_active
        self._days_at_maintenance_cap = days_at_maintenance_cap
        self._last_auto_setback = last_auto_setback
