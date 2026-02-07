"""PID tuning manager for Adaptive Climate integration."""

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict

from ..adaptive.physics import calculate_thermal_time_constant, calculate_initial_pid
from ..adaptive.learning import get_auto_apply_thresholds
from ..protocols import PIDTuningManagerState
from .. import const
from ..const import VALIDATION_CYCLE_COUNT, PIDChangeReason

if TYPE_CHECKING:
    from ..pid_controller import PIDController
    from ..adaptive.learning import AdaptiveLearner

_LOGGER = logging.getLogger(__name__)

# Constants for attribute names
DOMAIN = const.DOMAIN


class PIDTuningManager:
    """Manager for PID parameter tuning operations.

    Handles all PID parameter adjustments including:
    - Manual PID parameter setting (Kp, Ki, Kd, Ke)
    - PID mode setting (AUTO/OFF)
    - Reset to physics-based defaults
    - Application of adaptive PID recommendations
    - Application of adaptive Ke recommendations

    This manager centralizes PID tuning logic that was previously
    scattered throughout the climate entity.
    """

    def __init__(
        self,
        thermostat_state: PIDTuningManagerState,
        pid_controller: PIDController,
        gains_manager: Any,  # PIDGainsManager
        async_control_heating: Callable[..., Awaitable[None]],
        async_write_ha_state: Callable[[], None],
    ):
        """Initialize the PIDTuningManager.

        Args:
            thermostat_state: Protocol for state queries (read-only access)
            pid_controller: Direct reference to PID controller (for integral resets)
            gains_manager: PID gains manager instance (for gain mutations)
            async_control_heating: Async callback to trigger heating control
            async_write_ha_state: Async callback to write HA state
        """
        self._state = thermostat_state
        self._pid_controller = pid_controller
        self._gains_manager = gains_manager

        # Async callbacks
        self._async_control_heating = async_control_heating
        self._async_write_ha_state = async_write_ha_state

    async def async_set_pid(self, **kwargs) -> None:
        """Set PID parameters.

        Args:
            **kwargs: PID parameters to set (kp, ki, kd, ke)
        """
        # Extract gains from kwargs
        kp = kwargs.get("kp")
        ki = kwargs.get("ki")
        kd = kwargs.get("kd")
        ke = kwargs.get("ke")

        # Use PIDGainsManager to set gains and record to history
        self._gains_manager.set_gains(
            PIDChangeReason.SERVICE_CALL,
            kp=float(kp) if kp is not None else None,
            ki=float(ki) if ki is not None else None,
            kd=float(kd) if kd is not None else None,
            ke=float(ke) if ke is not None else None,
        )

        await self._async_control_heating(calc_pid=True)

    async def async_set_pid_mode(self, **kwargs) -> None:
        """Set PID mode (AUTO or OFF).

        Args:
            **kwargs: Contains 'mode' key with value 'AUTO' or 'OFF'
        """
        mode = kwargs.get("mode")
        if str(mode).upper() in ["AUTO", "OFF"] and self._pid_controller is not None:
            self._pid_controller.mode = str(mode).upper()
        await self._async_control_heating(calc_pid=True)

    async def async_reset_pid_to_physics(self, **kwargs) -> None:
        """Reset PID values to physics-based defaults.

        Calculates initial PID parameters based on room thermal properties
        using the Ziegler-Nichols method. Includes floor construction if configured.
        """
        area_m2 = self._state._area_m2
        if not area_m2:
            _LOGGER.warning("%s: Cannot reset PID to physics - no area_m2 configured", self._state.entity_id)
            return

        ceiling_height = self._state._ceiling_height
        volume_m3 = area_m2 * ceiling_height
        window_area_m2 = self._state._window_area_m2
        window_rating = self._state._window_rating
        heating_type = self._state.heating_type
        floor_construction = self._state._floor_construction

        tau = calculate_thermal_time_constant(
            volume_m3=volume_m3,
            window_area_m2=window_area_m2,
            floor_area_m2=area_m2,
            window_rating=window_rating,
            floor_construction=floor_construction,
            area_m2=area_m2,
            heating_type=heating_type,
        )
        max_power_w = self._state._max_power_w
        supply_temperature = self._state._supply_temperature
        kp, ki, kd = calculate_initial_pid(
            tau, heating_type, area_m2=area_m2, max_power_w=max_power_w, supply_temperature=supply_temperature
        )

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0

        # Set gains via PIDGainsManager (auto-records history)
        self._gains_manager.set_gains(
            PIDChangeReason.PHYSICS_RESET,
            kp=kp,
            ki=ki,
            kd=kd,
        )

        # Record physics baseline for auto-apply tracking
        coordinator = self._state._coordinator
        if coordinator:
            adaptive_learner = coordinator.get_adaptive_learner(self._state.entity_id)
            if adaptive_learner:
                adaptive_learner.set_physics_baseline(kp, ki, kd)

        power_info = f", power={max_power_w}W" if max_power_w else ""
        supply_info = f", supply={supply_temperature}°C" if supply_temperature else ""
        _LOGGER.info(
            "%s: Reset PID to physics defaults (tau=%.2f, type=%s, window=%s, floor=%s%s%s): Kp=%.4f, Ki=%.5f, Kd=%.3f",
            self._state.entity_id,
            tau,
            heating_type,
            window_rating,
            "configured" if floor_construction else "none",
            power_info,
            supply_info,
            kp,
            ki,
            kd,
        )

        await self._async_control_heating(calc_pid=True)
        await self._async_write_ha_state()

    async def async_apply_adaptive_pid(self, **kwargs) -> None:
        """Apply adaptive PID values based on learned metrics.

        Retrieves recommendations from the AdaptiveLearner and applies
        them to the PID controller.
        """
        coordinator = self._state._coordinator
        if not coordinator:
            _LOGGER.warning("%s: Cannot apply adaptive PID - no coordinator", self._state.entity_id)
            return

        adaptive_learner = coordinator.get_adaptive_learner(self._state.entity_id)
        if not adaptive_learner:
            _LOGGER.warning(
                "%s: Cannot apply adaptive PID - no adaptive learner (learning_enabled: false?)", self._state.entity_id
            )
            return

        # Calculate recommendation based on current PID values
        recommendation = adaptive_learner.calculate_pid_adjustment(
            current_kp=self._state._kp,
            current_ki=self._state._ki,
            current_kd=self._state._kd,
            pwm_seconds=self._state._pwm,
        )

        if recommendation is None:
            cycle_count = adaptive_learner.get_cycle_count()
            _LOGGER.warning(
                "%s: Insufficient data for adaptive PID (cycles: %d, need >= 3)",
                self._state.entity_id,
                cycle_count,
            )
            return

        # Store old values for logging
        old_kp = self._state._kp
        old_ki = self._state._ki
        old_kd = self._state._kd

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0

        # Apply the recommended values via PIDGainsManager (auto-records history)
        self._gains_manager.set_gains(
            PIDChangeReason.ADAPTIVE_APPLY,
            kp=recommendation["kp"],
            ki=recommendation["ki"],
            kd=recommendation["kd"],
        )

        # Clear learning history for manual apply
        coordinator = self._state._coordinator
        if coordinator:
            learner = coordinator.get_adaptive_learner(self._state.entity_id)
            if learner:
                learner.clear_history()

        _LOGGER.info(
            "%s: Applied adaptive PID: Kp=%.4f (was %.4f), Ki=%.5f (was %.5f), Kd=%.3f (was %.3f)",
            self._state.entity_id,
            self._state._kp,
            old_kp,
            self._state._ki,
            old_ki,
            self._state._kd,
            old_kd,
        )

        await self._async_control_heating(calc_pid=True)
        await self._async_write_ha_state()

    async def async_auto_apply_adaptive_pid(self, outdoor_temp: float | None = None) -> dict[str, Any]:
        """Automatically apply adaptive PID values with safety checks.

        Unlike async_apply_adaptive_pid(), this method:
        - Checks all safety limits (lifetime, seasonal, drift, cooldown)
        - Uses heating-type-specific confidence thresholds
        - Enters validation mode after applying
        - Records PID snapshots for rollback capability

        Args:
            outdoor_temp: Current outdoor temperature for seasonal shift detection

        Returns:
            Dict with keys:
                applied (bool): Whether PID was applied
                reason (str): Why it was or wasn't applied
                recommendation (dict or None): The PID values if applied
                old_values (dict or None): Previous PID values if applied
                new_values (dict or None): New PID values if applied
        """
        coordinator = self._state._coordinator
        if not coordinator:
            return {
                "applied": False,
                "reason": "No coordinator available",
                "recommendation": None,
            }

        adaptive_learner = coordinator.get_adaptive_learner(self._state.entity_id)
        if not adaptive_learner:
            return {
                "applied": False,
                "reason": "No adaptive learner (learning_enabled: false?)",
                "recommendation": None,
            }

        # Get heating type and thresholds
        heating_type = self._state.heating_type
        thresholds = get_auto_apply_thresholds(heating_type)

        # Calculate baseline overshoot from recent cycles
        cycle_history = adaptive_learner.cycle_history
        recent_cycles = cycle_history[-6:] if len(cycle_history) >= 6 else cycle_history
        overshoot_values = [c.overshoot for c in recent_cycles if c.overshoot is not None]
        baseline_overshoot = statistics.mean(overshoot_values) if overshoot_values else 0.0

        # Calculate recommendation with auto-apply safety checks
        recommendation = adaptive_learner.calculate_pid_adjustment(
            current_kp=self._state._kp,
            current_ki=self._state._ki,
            current_kd=self._state._kd,
            pwm_seconds=self._state._pwm,
            check_auto_apply=True,
            outdoor_temp=outdoor_temp,
        )

        if recommendation is None:
            return {
                "applied": False,
                "reason": "No recommendation (insufficient data, limits reached, or in validation)",
                "recommendation": None,
            }

        # Store old values for logging
        old_kp = self._state._kp
        old_ki = self._state._ki
        old_kd = self._state._kd

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0

        # Apply the recommended values via PIDGainsManager (auto-records history)
        self._gains_manager.set_gains(
            PIDChangeReason.AUTO_APPLY,
            kp=recommendation["kp"],
            ki=recommendation["ki"],
            kd=recommendation["kd"],
            metrics={
                "baseline_overshoot": baseline_overshoot,
                "confidence": getattr(adaptive_learner, "_convergence_confidence", 0.0),
            },
        )

        # Clear learning history
        adaptive_learner.clear_history()

        # Increment auto-apply count
        adaptive_learner._auto_apply_count += 1

        # Sync auto-apply count to PID controller for safety net control
        # The PID controller uses this to disable integral decay safety net after first auto-apply
        self._pid_controller.set_auto_apply_count(adaptive_learner._auto_apply_count)

        # Start validation mode
        adaptive_learner.start_validation_mode(baseline_overshoot)

        _LOGGER.warning(
            "%s: Auto-applied adaptive PID (apply #%d): "
            "Kp=%.4f→%.4f, Ki=%.5f→%.5f, Kd=%.3f→%.3f. "
            "Entering validation mode for %d cycles.",
            self._state.entity_id,
            adaptive_learner._auto_apply_count,
            old_kp,
            recommendation["kp"],
            old_ki,
            recommendation["ki"],
            old_kd,
            recommendation["kd"],
            VALIDATION_CYCLE_COUNT,
        )

        await self._async_control_heating(calc_pid=True)
        await self._async_write_ha_state()

        return {
            "applied": True,
            "reason": "Auto-applied successfully",
            "recommendation": recommendation,
            "old_values": {"kp": old_kp, "ki": old_ki, "kd": old_kd},
            "new_values": {
                "kp": recommendation["kp"],
                "ki": recommendation["ki"],
                "kd": recommendation["kd"],
            },
        }

    async def async_rollback_pid(self) -> bool:
        """Rollback PID values to the previous configuration.

        Retrieves the second-to-last PID snapshot from history and restores
        those values. This is typically used when validation fails after
        an auto-apply, or when a user wants to undo a recent change.

        Returns:
            bool: True if rollback succeeded, False if no history available
        """
        coordinator = self._state._coordinator
        if not coordinator:
            _LOGGER.warning("%s: Cannot rollback PID - no coordinator", self._state.entity_id)
            return False

        adaptive_learner = coordinator.get_adaptive_learner(self._state.entity_id)
        if not adaptive_learner:
            _LOGGER.warning("%s: Cannot rollback PID - no adaptive learner", self._state.entity_id)
            return False

        # Get previous PID values
        previous_pid = adaptive_learner.get_previous_pid()
        if previous_pid is None:
            _LOGGER.warning("%s: Cannot rollback PID - no previous configuration in history", self._state.entity_id)
            return False

        # Store current values for logging
        current_kp = self._state._kp
        current_ki = self._state._ki
        current_kd = self._state._kd

        # Clear integral to avoid wind-up
        self._pid_controller.integral = 0.0

        # Apply previous PID values via PIDGainsManager (auto-records history)
        self._gains_manager.set_gains(
            PIDChangeReason.ROLLBACK,
            kp=previous_pid["kp"],
            ki=previous_pid["ki"],
            kd=previous_pid["kd"],
            metrics={
                "rolled_back_from_kp": current_kp,
                "rolled_back_from_ki": current_ki,
                "rolled_back_from_kd": current_kd,
            },
        )

        # Clear history to reset learning state
        adaptive_learner.clear_history()

        _LOGGER.warning(
            "%s: Rolled back PID to previous config (from %s): Kp=%.4f→%.4f, Ki=%.5f→%.5f, Kd=%.3f→%.3f",
            self._state.entity_id,
            previous_pid.get("timestamp", "unknown"),
            current_kp,
            previous_pid["kp"],
            current_ki,
            previous_pid["ki"],
            current_kd,
            previous_pid["kd"],
        )

        await self._async_control_heating(calc_pid=True)
        await self._async_write_ha_state()

        return True

    async def async_apply_adaptive_ke(self, **kwargs) -> None:
        """Apply adaptive Ke value based on learned outdoor temperature correlations.

        Note: This method delegates to the KeManager for the actual implementation.
        It is included here for consistency with the PID tuning interface.
        """
        # Get the KeManager from the thermostat state
        ke_controller = getattr(self._state, "_ke_controller", None)
        if ke_controller is not None:
            await ke_controller.async_apply_adaptive_ke(**kwargs)
        else:
            _LOGGER.warning("%s: Cannot apply adaptive Ke - no Ke manager", self._state.entity_id)

    async def async_clear_learning(self, **kwargs) -> None:
        """Clear all learning data and reset PID to physics defaults.

        This clears:
        - Cycle history from AdaptiveLearner
        - Ke observations from KeLearner
        - Preheat observations from PreheatLearner
        - Convergence state
        - Resets PID to physics-based defaults
        - Persists cleared state to disk
        """
        coordinator = self._state._coordinator
        adaptive_learner = None
        if coordinator:
            adaptive_learner = coordinator.get_adaptive_learner(self._state.entity_id)
            if adaptive_learner:
                adaptive_learner.clear_history()
                _LOGGER.info("%s: Cleared adaptive learning cycle history", self._state.entity_id)

        # Clear Ke learner observations
        ke_controller = getattr(self._state, "_ke_controller", None)
        if ke_controller and ke_controller.ke_learner:
            ke_controller.ke_learner.clear_observations()
            _LOGGER.info("%s: Cleared Ke learning observations", self._state.entity_id)

        # Clear preheat learner observations
        preheat_learner = getattr(self._state, "_preheat_learner", None)
        if preheat_learner:
            preheat_learner._observations.clear()
            preheat_learner._add_observation_counter = 0
            _LOGGER.info("%s: Cleared preheat learning observations", self._state.entity_id)

        # Persist cleared state to disk immediately (not debounced)
        zone_id = getattr(self._state, "_zone_id", None)
        hass = getattr(self._state, "hass", None)
        if zone_id and hass:
            learning_store = hass.data.get(DOMAIN, {}).get("learning_store")
            if learning_store:
                adaptive_data = adaptive_learner.to_dict() if adaptive_learner else None
                ke_data = ke_controller.ke_learner.to_dict() if (ke_controller and ke_controller.ke_learner) else None
                preheat_data = preheat_learner.to_dict() if preheat_learner else None

                await learning_store.async_save_zone(
                    zone_id=zone_id,
                    adaptive_data=adaptive_data,
                    ke_data=ke_data,
                    preheat_data=preheat_data,
                )
                _LOGGER.info("%s: Persisted cleared learning state to disk for zone %s", self._state.entity_id, zone_id)

        # Reset PID to physics defaults
        await self.async_reset_pid_to_physics()

        _LOGGER.info("%s: Learning cleared and PID reset to physics defaults", self._state.entity_id)
