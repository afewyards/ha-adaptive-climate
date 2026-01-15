"""Ke (outdoor temperature compensation) learning manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.components.climate import HVACMode
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HVACMode = Any

from ..adaptive.ke_learning import KeLearner
from .. import const

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


class KeController:
    """Controller for Ke (outdoor temperature compensation) learning.

    Manages the adaptive learning of the Ke parameter based on observed
    correlations between outdoor temperature and required heating effort.
    This includes:
    - Steady state detection
    - Ke observation recording
    - Ke adjustment calculation and application
    """

    def __init__(
        self,
        thermostat: AdaptiveThermostat,
        ke_learner: Optional[KeLearner],
        get_hvac_mode: callable,
        get_current_temp: callable,
        get_target_temp: callable,
        get_ext_temp: callable,
        get_control_output: callable,
        get_cold_tolerance: callable,
        get_hot_tolerance: callable,
        get_ke: callable,
        set_ke: callable,
        get_pid_controller: callable,
        async_control_heating: callable,
        async_write_ha_state: callable,
        get_is_pid_converged: callable = None,
    ):
        """Initialize the KeController.

        Args:
            thermostat: Reference to the parent thermostat entity
            ke_learner: KeLearner instance (may be None if no outdoor sensor)
            get_hvac_mode: Callback to get current HVAC mode
            get_current_temp: Callback to get current indoor temperature
            get_target_temp: Callback to get target temperature
            get_ext_temp: Callback to get external/outdoor temperature
            get_control_output: Callback to get current PID control output
            get_cold_tolerance: Callback to get cold tolerance
            get_hot_tolerance: Callback to get hot tolerance
            get_ke: Callback to get current Ke value
            set_ke: Callback to set Ke value
            get_pid_controller: Callback to get PID controller
            async_control_heating: Async callback to trigger heating control
            async_write_ha_state: Async callback to write HA state
            get_is_pid_converged: Callback to check if PID has converged for Ke learning
        """
        self._thermostat = thermostat
        self._ke_learner = ke_learner

        # Callbacks
        self._get_hvac_mode = get_hvac_mode
        self._get_current_temp = get_current_temp
        self._get_target_temp = get_target_temp
        self._get_ext_temp = get_ext_temp
        self._get_control_output = get_control_output
        self._get_cold_tolerance = get_cold_tolerance
        self._get_hot_tolerance = get_hot_tolerance
        self._get_ke = get_ke
        self._set_ke = set_ke
        self._get_pid_controller = get_pid_controller
        self._async_control_heating = async_control_heating
        self._async_write_ha_state = async_write_ha_state
        self._get_is_pid_converged = get_is_pid_converged

        # State tracking
        self._steady_state_start: Optional[float] = None
        self._last_ke_observation_time: Optional[float] = None

    @property
    def ke_learner(self) -> Optional[KeLearner]:
        """Return the KeLearner instance."""
        return self._ke_learner

    @property
    def steady_state_start(self) -> Optional[float]:
        """Return the timestamp when steady state began."""
        return self._steady_state_start

    @property
    def last_ke_observation_time(self) -> Optional[float]:
        """Return the timestamp of the last Ke observation."""
        return self._last_ke_observation_time

    def update_ke_learner(self, ke_learner: Optional[KeLearner]) -> None:
        """Update the KeLearner instance.

        Args:
            ke_learner: New KeLearner instance (or None to disable)
        """
        self._ke_learner = ke_learner

    def is_at_steady_state(self) -> bool:
        """Check if the system is at steady state (maintaining target temperature).

        Steady state is determined by:
        1. Temperature within tolerance of target
        2. Maintained for KE_STEADY_STATE_DURATION minutes
        3. HVAC mode is active (not OFF)

        Returns:
            True if at steady state, False otherwise
        """
        hvac_mode = self._get_hvac_mode()
        if hvac_mode == HVACMode.OFF:
            self._steady_state_start = None
            return False

        current_temp = self._get_current_temp()
        target_temp = self._get_target_temp()

        if current_temp is None or target_temp is None:
            self._steady_state_start = None
            return False

        # Check if within tolerance band
        cold_tolerance = self._get_cold_tolerance()
        hot_tolerance = self._get_hot_tolerance()
        tolerance = max(cold_tolerance, hot_tolerance, 0.2)
        within_tolerance = abs(current_temp - target_temp) <= tolerance

        if not within_tolerance:
            self._steady_state_start = None
            return False

        # Start tracking steady state if not already
        current_time = time.time()
        if self._steady_state_start is None:
            self._steady_state_start = current_time

        # Check if we've maintained steady state long enough
        steady_duration_seconds = current_time - self._steady_state_start
        required_duration_seconds = const.KE_STEADY_STATE_DURATION * 60

        return steady_duration_seconds >= required_duration_seconds

    def maybe_record_observation(self) -> None:
        """Record a Ke observation if conditions are met.

        Conditions:
        1. Ke learner exists and is enabled (or becomes enabled when PID converges)
        2. System is at steady state
        3. Outdoor temperature sensor is available
        4. Minimum time has passed since last observation (5 minutes)
        """
        if not self._ke_learner:
            return

        # Check if PID has converged and enable Ke learning if not already enabled
        if not self._ke_learner.enabled:
            if self._get_is_pid_converged and self._get_is_pid_converged():
                # PID has converged - enable Ke learning and apply physics-based Ke
                self._ke_learner.enable()
                physics_ke = self._ke_learner.current_ke
                if physics_ke > 0:
                    self._set_ke(physics_ke)
                    pid_controller = self._get_pid_controller()
                    if pid_controller:
                        pid_controller.set_pid_param(ke=physics_ke)
                    _LOGGER.info(
                        "%s: PID converged - enabled Ke learning and applied physics-based Ke=%.3f",
                        self._thermostat.entity_id,
                        physics_ke,
                    )
            else:
                # PID not converged yet, skip observation
                return

        if not self.is_at_steady_state():
            return

        ext_temp = self._get_ext_temp()
        if ext_temp is None:
            return

        # Rate limit: at least 5 minutes between observations
        current_time = time.time()
        if self._last_ke_observation_time is not None:
            time_since_last = current_time - self._last_ke_observation_time
            if time_since_last < 300:  # 5 minutes
                return

        # Record the observation
        control_output = self._get_control_output()
        current_temp = self._get_current_temp()
        target_temp = self._get_target_temp()

        self._ke_learner.add_observation(
            outdoor_temp=ext_temp,
            pid_output=control_output,
            indoor_temp=current_temp,
            target_temp=target_temp,
        )
        self._last_ke_observation_time = current_time

        _LOGGER.debug(
            "%s: Ke observation recorded: outdoor=%.1f, pid=%.1f, indoor=%.1f, target=%.1f",
            self._thermostat.entity_id,
            ext_temp,
            control_output,
            current_temp,
            target_temp,
        )

    async def async_apply_adaptive_ke(self, **kwargs) -> None:
        """Apply adaptive Ke value based on learned outdoor temperature correlations."""
        if not self._ke_learner:
            _LOGGER.warning(
                "%s: Cannot apply adaptive Ke - no Ke learner (outdoor sensor not configured?)",
                self._thermostat.entity_id
            )
            return

        if not self._ke_learner.enabled:
            _LOGGER.warning(
                "%s: Cannot apply adaptive Ke - learning not enabled (PID not converged yet)",
                self._thermostat.entity_id
            )
            return

        recommendation = self._ke_learner.calculate_ke_adjustment()

        if recommendation is None:
            summary = self._ke_learner.get_observations_summary()
            _LOGGER.warning(
                "%s: Insufficient data for adaptive Ke (observations: %d, "
                "temp_range: %s, correlation: %s)",
                self._thermostat.entity_id,
                summary.get("count", 0),
                summary.get("outdoor_temp_range"),
                summary.get("correlation"),
            )
            return

        # Apply the recommended Ke value
        old_ke = self._get_ke()
        self._set_ke(recommendation)
        self._ke_learner.apply_ke_adjustment(recommendation)

        # Update PID controller
        pid_controller = self._get_pid_controller()
        pid_controller.set_pid_param(ke=recommendation)

        _LOGGER.info(
            "%s: Applied adaptive Ke: %.2f (was %.2f)",
            self._thermostat.entity_id, recommendation, old_ke
        )

        await self._async_control_heating(calc_pid=True)
        await self._async_write_ha_state()

    def restore_state(
        self,
        steady_state_start: Optional[float] = None,
        last_ke_observation_time: Optional[float] = None,
    ) -> None:
        """Restore state from saved data.

        Args:
            steady_state_start: Restored steady state start timestamp
            last_ke_observation_time: Restored last Ke observation timestamp
        """
        self._steady_state_start = steady_state_start
        self._last_ke_observation_time = last_ke_observation_time
