"""Night setback controller manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

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

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


class NightSetbackController:
    """Controller for night setback temperature adjustments.

    Manages the calculation of effective setpoint temperatures based on
    night setback configuration, sunrise/sunset times, weather conditions,
    and solar recovery logic. Delegates calculation logic to NightSetbackCalculator.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        night_setback: Optional[NightSetback],
        night_setback_config: Optional[Dict[str, Any]],
        solar_recovery: Optional[Any],
        window_orientation: Optional[str],
        get_target_temp: Callable[[], Optional[float]],
        get_current_temp: Callable[[], Optional[float]],
        preheat_learner: Optional[Any] = None,
        preheat_enabled: bool = False,
    ):
        """Initialize the NightSetbackController.

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
        """
        self._entity_id = entity_id

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
        )

        # Grace period tracking state variables (state management, not calculation)
        self._learning_grace_until: Optional[datetime] = None
        self._night_setback_was_active: Optional[bool] = None

    @property
    def calculator(self) -> NightSetbackCalculator:
        """Return the underlying calculator for direct access if needed."""
        return self._calculator

    @property
    def is_configured(self) -> bool:
        """Return True if night setback is configured."""
        return self._calculator.is_configured

    @property
    def in_learning_grace_period(self) -> bool:
        """Check if learning should be paused due to recent night setback transition."""
        if self._learning_grace_until is None:
            return False
        return dt_util.utcnow() < self._learning_grace_until

    @property
    def learning_grace_until(self) -> Optional[datetime]:
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
            self._entity_id, minutes, self._learning_grace_until.strftime("%H:%M")
        )

    def calculate_night_setback_adjustment(
        self,
        current_time: Optional[datetime] = None
    ) -> Tuple[float, bool, Dict[str, Any]]:
        """Calculate night setback adjustment for effective target temperature.

        Handles both static end time (NightSetback object) and dynamic end time
        (sunrise/orientation/weather-based) configurations.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            A tuple of (effective_target, in_night_period, night_setback_info) where:
            - effective_target: The adjusted target temperature
            - in_night_period: Whether we are currently in the night setback period
            - night_setback_info: Dict with additional info for state attributes
        """
        # Delegate calculation to the calculator
        effective_target, in_night_period, info = self._calculator.calculate_night_setback_adjustment(
            current_time
        )

        # Handle transition detection for learning grace period (state management)
        if self._calculator.is_configured:
            if self._night_setback_was_active is not None and in_night_period != self._night_setback_was_active:
                transition = "started" if in_night_period else "ended"
                _LOGGER.info("%s: Night setback %s - setting learning grace period", self._entity_id, transition)
                self.set_learning_grace_period(minutes=60)
            self._night_setback_was_active = in_night_period

        return effective_target, in_night_period, info

    def calculate_effective_setpoint(
        self,
        current_time: Optional[datetime] = None
    ) -> float:
        """Calculate the effective setpoint with night setback applied.

        This is the main interface method for getting the adjusted target temperature.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            The effective target temperature after night setback adjustments
        """
        effective_target, _, _ = self.calculate_night_setback_adjustment(current_time)
        return effective_target

    def get_state_attributes(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Get state attributes for the night setback status.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            Dictionary of state attributes
        """
        attributes: Dict[str, Any] = {}

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
        learning_grace_until: Optional[datetime] = None,
        night_setback_was_active: Optional[bool] = None,
    ) -> None:
        """Restore state from saved data.

        Args:
            learning_grace_until: Restored learning grace until time
            night_setback_was_active: Restored night setback active state
        """
        self._learning_grace_until = learning_grace_until
        self._night_setback_was_active = night_setback_was_active
