"""PID tuning manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..adaptive.physics import calculate_thermal_time_constant, calculate_initial_pid
from .. import const
from ..const import get_auto_apply_thresholds, VALIDATION_CYCLE_COUNT

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat
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
        thermostat: AdaptiveThermostat,
        pid_controller: PIDController,
        get_kp: callable,
        get_ki: callable,
        get_kd: callable,
        get_ke: callable,
        set_kp: callable,
        set_ki: callable,
        set_kd: callable,
        set_ke: callable,
        get_area_m2: callable,
        get_ceiling_height: callable,
        get_window_area_m2: callable,
        get_window_rating: callable,
        get_heating_type: callable,
        get_hass: callable,
        get_zone_id: callable,
        get_floor_construction: callable,
        get_supply_temperature: callable,
        get_max_power_w: callable,
        async_control_heating: callable,
        async_write_ha_state: callable,
    ):
        """Initialize the PIDTuningManager.

        Args:
            thermostat: Reference to the parent thermostat entity
            pid_controller: Reference to the PID controller
            get_kp: Callback to get current Kp value
            get_ki: Callback to get current Ki value
            get_kd: Callback to get current Kd value
            get_ke: Callback to get current Ke value
            set_kp: Callback to set Kp value
            set_ki: Callback to set Ki value
            set_kd: Callback to set Kd value
            set_ke: Callback to set Ke value
            get_area_m2: Callback to get room area in m2
            get_ceiling_height: Callback to get ceiling height
            get_window_area_m2: Callback to get window area in m2
            get_window_rating: Callback to get window rating
            get_heating_type: Callback to get heating type
            get_hass: Callback to get Home Assistant instance
            get_zone_id: Callback to get zone ID
            get_floor_construction: Callback to get floor construction config
            get_supply_temperature: Callback to get supply temperature
            get_max_power_w: Callback to get max power in watts
            async_control_heating: Async callback to trigger heating control
            async_write_ha_state: Async callback to write HA state
        """
        self._thermostat = thermostat
        self._pid_controller = pid_controller

        # Getters
        self._get_kp = get_kp
        self._get_ki = get_ki
        self._get_kd = get_kd
        self._get_ke = get_ke
        self._get_area_m2 = get_area_m2
        self._get_ceiling_height = get_ceiling_height
        self._get_window_area_m2 = get_window_area_m2
        self._get_window_rating = get_window_rating
        self._get_heating_type = get_heating_type
        self._get_hass = get_hass
        self._get_zone_id = get_zone_id
        self._get_floor_construction = get_floor_construction
        self._get_supply_temperature = get_supply_temperature
        self._get_max_power_w = get_max_power_w

        # Setters
        self._set_kp = set_kp
        self._set_ki = set_ki
        self._set_kd = set_kd
        self._set_ke = set_ke

        # Async callbacks
        self._async_control_heating = async_control_heating
        self._async_write_ha_state = async_write_ha_state

    async def async_set_pid(self, **kwargs) -> None:
        """Set PID parameters.

        Args:
            **kwargs: PID parameters to set (kp, ki, kd, ke)
        """
        for pid_kx, gain in kwargs.items():
            if gain is not None:
                # Map parameter names to setters
                setter_map = {
                    'kp': self._set_kp,
                    'ki': self._set_ki,
                    'kd': self._set_kd,
                    'ke': self._set_ke,
                }
                if pid_kx in setter_map:
                    setter_map[pid_kx](float(gain))

        self._pid_controller.set_pid_param(
            self._get_kp(),
            self._get_ki(),
            self._get_kd(),
            self._get_ke(),
        )
        await self._async_control_heating(calc_pid=True)

    async def async_set_pid_mode(self, **kwargs) -> None:
        """Set PID mode (AUTO or OFF).

        Args:
            **kwargs: Contains 'mode' key with value 'AUTO' or 'OFF'
        """
        mode = kwargs.get('mode', None)
        if str(mode).upper() in ['AUTO', 'OFF'] and self._pid_controller is not None:
            self._pid_controller.mode = str(mode).upper()
        await self._async_control_heating(calc_pid=True)

    async def async_reset_pid_to_physics(self, **kwargs) -> None:
        """Reset PID values to physics-based defaults.

        Calculates initial PID parameters based on room thermal properties
        using the Ziegler-Nichols method. Includes floor construction if configured.
        """
        area_m2 = self._get_area_m2()
        if not area_m2:
            _LOGGER.warning(
                "%s: Cannot reset PID to physics - no area_m2 configured",
                self._thermostat.entity_id
            )
            return

        ceiling_height = self._get_ceiling_height()
        volume_m3 = area_m2 * ceiling_height
        window_area_m2 = self._get_window_area_m2()
        window_rating = self._get_window_rating()
        heating_type = self._get_heating_type()
        floor_construction = self._get_floor_construction()

        tau = calculate_thermal_time_constant(
            volume_m3=volume_m3,
            window_area_m2=window_area_m2,
            floor_area_m2=area_m2,
            window_rating=window_rating,
            floor_construction=floor_construction,
            area_m2=area_m2,
            heating_type=heating_type,
        )
        max_power_w = self._get_max_power_w()
        supply_temperature = self._get_supply_temperature()
        kp, ki, kd = calculate_initial_pid(
            tau, heating_type, area_m2=area_m2, max_power_w=max_power_w, supply_temperature=supply_temperature
        )

        self._set_kp(kp)
        self._set_ki(ki)
        self._set_kd(kd)

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0

        self._pid_controller.set_pid_param(
            self._get_kp(),
            self._get_ki(),
            self._get_kd(),
            self._get_ke(),
        )

        # Record physics baseline and PID snapshot for auto-apply tracking
        hass = self._get_hass()
        coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator:
            all_zones = coordinator.get_all_zones()
            for zone_id, zone_data in all_zones.items():
                if zone_data.get("climate_entity_id") == self._thermostat.entity_id:
                    adaptive_learner = zone_data.get("adaptive_learner")
                    if adaptive_learner:
                        adaptive_learner.set_physics_baseline(kp, ki, kd)
                        adaptive_learner.record_pid_snapshot(
                            kp=kp,
                            ki=ki,
                            kd=kd,
                            reason="physics_reset",
                        )
                    break

        power_info = f", power={max_power_w}W" if max_power_w else ""
        supply_info = f", supply={supply_temperature}°C" if supply_temperature else ""
        _LOGGER.info(
            "%s: Reset PID to physics defaults (tau=%.2f, type=%s, window=%s, floor=%s%s%s): "
            "Kp=%.4f, Ki=%.5f, Kd=%.3f",
            self._thermostat.entity_id,
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
        hass = self._get_hass()
        coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            _LOGGER.warning(
                "%s: Cannot apply adaptive PID - no coordinator",
                self._thermostat.entity_id
            )
            return

        all_zones = coordinator.get_all_zones()
        adaptive_learner = None

        for zone_id, zone_data in all_zones.items():
            if zone_data.get("climate_entity_id") == self._thermostat.entity_id:
                adaptive_learner = zone_data.get("adaptive_learner")
                break

        if not adaptive_learner:
            _LOGGER.warning(
                "%s: Cannot apply adaptive PID - no adaptive learner "
                "(learning_enabled: false?)",
                self._thermostat.entity_id
            )
            return

        # Calculate recommendation based on current PID values
        recommendation = adaptive_learner.calculate_pid_adjustment(
            current_kp=self._get_kp(),
            current_ki=self._get_ki(),
            current_kd=self._get_kd(),
            pwm_seconds=self._thermostat._pwm,
        )

        if recommendation is None:
            cycle_count = adaptive_learner.get_cycle_count()
            _LOGGER.warning(
                "%s: Insufficient data for adaptive PID (cycles: %d, need >= 3)",
                self._thermostat.entity_id,
                cycle_count,
            )
            return

        # Apply the recommended values
        old_kp = self._get_kp()
        old_ki = self._get_ki()
        old_kd = self._get_kd()

        self._set_kp(recommendation["kp"])
        self._set_ki(recommendation["ki"])
        self._set_kd(recommendation["kd"])

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0

        self._pid_controller.set_pid_param(
            self._get_kp(),
            self._get_ki(),
            self._get_kd(),
            self._get_ke(),
        )

        # Record PID snapshot and clear learning history for manual apply
        hass = self._get_hass()
        coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator:
            all_zones = coordinator.get_all_zones()
            for zone_id, zone_data in all_zones.items():
                if zone_data.get("climate_entity_id") == self._thermostat.entity_id:
                    learner = zone_data.get("adaptive_learner")
                    if learner:
                        learner.record_pid_snapshot(
                            kp=recommendation["kp"],
                            ki=recommendation["ki"],
                            kd=recommendation["kd"],
                            reason="manual_apply",
                        )
                        learner.clear_history()
                    break

        _LOGGER.info(
            "%s: Applied adaptive PID: Kp=%.4f (was %.4f), Ki=%.5f (was %.5f), "
            "Kd=%.3f (was %.3f)",
            self._thermostat.entity_id,
            self._get_kp(),
            old_kp,
            self._get_ki(),
            old_ki,
            self._get_kd(),
            old_kd,
        )

        await self._async_control_heating(calc_pid=True)
        await self._async_write_ha_state()

    async def async_auto_apply_adaptive_pid(
        self, outdoor_temp: Optional[float] = None
    ) -> Dict[str, Any]:
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
        hass = self._get_hass()
        coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            return {
                "applied": False,
                "reason": "No coordinator available",
                "recommendation": None,
            }

        # Find adaptive learner for this thermostat
        all_zones = coordinator.get_all_zones()
        adaptive_learner = None

        for zone_id, zone_data in all_zones.items():
            if zone_data.get("climate_entity_id") == self._thermostat.entity_id:
                adaptive_learner = zone_data.get("adaptive_learner")
                break

        if not adaptive_learner:
            return {
                "applied": False,
                "reason": "No adaptive learner (learning_enabled: false?)",
                "recommendation": None,
            }

        # Get heating type and thresholds
        heating_type = self._get_heating_type()
        thresholds = get_auto_apply_thresholds(heating_type)

        # Calculate baseline overshoot from recent cycles
        cycle_history = adaptive_learner.cycle_history
        recent_cycles = cycle_history[-6:] if len(cycle_history) >= 6 else cycle_history
        overshoot_values = [
            c.overshoot for c in recent_cycles
            if c.overshoot is not None
        ]
        baseline_overshoot = (
            statistics.mean(overshoot_values) if overshoot_values else 0.0
        )

        # Calculate recommendation with auto-apply safety checks
        recommendation = adaptive_learner.calculate_pid_adjustment(
            current_kp=self._get_kp(),
            current_ki=self._get_ki(),
            current_kd=self._get_kd(),
            pwm_seconds=self._thermostat._pwm,
            check_auto_apply=True,
            outdoor_temp=outdoor_temp,
        )

        if recommendation is None:
            return {
                "applied": False,
                "reason": "No recommendation (insufficient data, limits reached, or in validation)",
                "recommendation": None,
            }

        # Store old values
        old_kp = self._get_kp()
        old_ki = self._get_ki()
        old_kd = self._get_kd()

        # Record PID snapshot before applying
        adaptive_learner.record_pid_snapshot(
            kp=old_kp,
            ki=old_ki,
            kd=old_kd,
            reason="before_auto_apply",
            metrics={
                "baseline_overshoot": baseline_overshoot,
            },
        )

        # Apply the recommended values
        self._set_kp(recommendation["kp"])
        self._set_ki(recommendation["ki"])
        self._set_kd(recommendation["kd"])

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0

        self._pid_controller.set_pid_param(
            self._get_kp(),
            self._get_ki(),
            self._get_kd(),
            self._get_ke(),
        )

        # Record PID snapshot after applying
        adaptive_learner.record_pid_snapshot(
            kp=recommendation["kp"],
            ki=recommendation["ki"],
            kd=recommendation["kd"],
            reason="auto_apply",
            metrics={
                "baseline_overshoot": baseline_overshoot,
                "confidence": getattr(adaptive_learner, '_convergence_confidence', 0.0),
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
            self._thermostat.entity_id,
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
        hass = self._get_hass()
        coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            _LOGGER.warning(
                "%s: Cannot rollback PID - no coordinator",
                self._thermostat.entity_id
            )
            return False

        # Find adaptive learner for this thermostat
        all_zones = coordinator.get_all_zones()
        adaptive_learner = None

        for zone_id, zone_data in all_zones.items():
            if zone_data.get("climate_entity_id") == self._thermostat.entity_id:
                adaptive_learner = zone_data.get("adaptive_learner")
                break

        if not adaptive_learner:
            _LOGGER.warning(
                "%s: Cannot rollback PID - no adaptive learner",
                self._thermostat.entity_id
            )
            return False

        # Get previous PID values
        previous_pid = adaptive_learner.get_previous_pid()
        if previous_pid is None:
            _LOGGER.warning(
                "%s: Cannot rollback PID - no previous configuration in history",
                self._thermostat.entity_id
            )
            return False

        # Store current values for logging
        current_kp = self._get_kp()
        current_ki = self._get_ki()
        current_kd = self._get_kd()

        # Apply previous PID values
        self._set_kp(previous_pid["kp"])
        self._set_ki(previous_pid["ki"])
        self._set_kd(previous_pid["kd"])

        # Clear integral to avoid wind-up
        self._pid_controller.integral = 0.0

        self._pid_controller.set_pid_param(
            self._get_kp(),
            self._get_ki(),
            self._get_kd(),
            self._get_ke(),
        )

        # Record rollback snapshot
        adaptive_learner.record_pid_snapshot(
            kp=previous_pid["kp"],
            ki=previous_pid["ki"],
            kd=previous_pid["kd"],
            reason="rollback",
            metrics={
                "rolled_back_from_kp": current_kp,
                "rolled_back_from_ki": current_ki,
                "rolled_back_from_kd": current_kd,
            },
        )

        # Clear history to reset learning state
        adaptive_learner.clear_history()

        _LOGGER.warning(
            "%s: Rolled back PID to previous config (from %s): "
            "Kp=%.4f→%.4f, Ki=%.5f→%.5f, Kd=%.3f→%.3f",
            self._thermostat.entity_id,
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

        Note: This method delegates to the KeController for the actual implementation.
        It is included here for consistency with the PID tuning interface.
        """
        # Get the KeController from the thermostat
        ke_controller = getattr(self._thermostat, '_ke_controller', None)
        if ke_controller is not None:
            await ke_controller.async_apply_adaptive_ke(**kwargs)
        else:
            _LOGGER.warning(
                "%s: Cannot apply adaptive Ke - no Ke controller",
                self._thermostat.entity_id
            )

    async def async_clear_learning(self, **kwargs) -> None:
        """Clear all learning data and reset PID to physics defaults.

        This clears:
        - Cycle history from AdaptiveLearner
        - Ke observations from KeLearner
        - Convergence state
        - Resets PID to physics-based defaults
        """
        hass = self._get_hass()
        coordinator = hass.data.get(DOMAIN, {}).get("coordinator")

        if coordinator:
            all_zones = coordinator.get_all_zones()
            for zone_id, zone_data in all_zones.items():
                if zone_data.get("climate_entity_id") == self._thermostat.entity_id:
                    # Clear AdaptiveLearner cycle history
                    adaptive_learner = zone_data.get("adaptive_learner")
                    if adaptive_learner:
                        adaptive_learner.clear_history()
                        _LOGGER.info(
                            "%s: Cleared adaptive learning cycle history",
                            self._thermostat.entity_id
                        )
                    break

        # Clear Ke learner observations
        ke_controller = getattr(self._thermostat, '_ke_controller', None)
        if ke_controller and ke_controller.ke_learner:
            ke_controller.ke_learner.clear_observations()
            _LOGGER.info(
                "%s: Cleared Ke learning observations",
                self._thermostat.entity_id
            )

        # Reset PID to physics defaults
        await self.async_reset_pid_to_physics()

        _LOGGER.info(
            "%s: Learning cleared and PID reset to physics defaults",
            self._thermostat.entity_id
        )
