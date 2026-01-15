"""PID tuning manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from ..adaptive.physics import calculate_thermal_time_constant, calculate_initial_pid
from .. import const

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
        using the Ziegler-Nichols method.
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

        tau = calculate_thermal_time_constant(
            volume_m3=volume_m3,
            window_area_m2=window_area_m2,
            floor_area_m2=area_m2,
            window_rating=window_rating,
        )
        kp, ki, kd = calculate_initial_pid(tau, heating_type)

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

        _LOGGER.info(
            "%s: Reset PID to physics defaults (tau=%.2f, type=%s, window=%s): "
            "Kp=%.4f, Ki=%.5f, Kd=%.3f",
            self._thermostat.entity_id,
            tau,
            heating_type,
            window_rating,
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
