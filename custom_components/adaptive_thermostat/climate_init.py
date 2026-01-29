"""Manager initialization for AdaptiveThermostat entity.

This module extracts the manager creation logic from climate.py's async_added_to_hass.
It creates and configures all manager instances (HeaterController, CycleTrackerManager,
TemperatureManager, KeManager, etc.) with their required dependencies.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .adaptive.physics import calculate_initial_ke
from .adaptive.ke_learning import KeLearner
from .adaptive.preheat import PreheatLearner
from .managers import (
    ControlOutputManager,
    HeaterController,
    KeManager,
    NightSetbackManager,
    PIDTuningManager,
    SetpointBoostManager,
    TemperatureManager,
    CycleTrackerManager,
)
from .managers.events import (
    CycleEventDispatcher,
    CycleEventType,
)
from . import DOMAIN

if TYPE_CHECKING:
    from .climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


def _has_recovery_deadline(night_setback_config: dict | None) -> bool:
    """Check if night setback config has a recovery_deadline set.

    Args:
        night_setback_config: The night setback configuration dict

    Returns:
        True if recovery_deadline is configured, False otherwise
    """
    return (
        night_setback_config.get("recovery_deadline") is not None
        if night_setback_config
        else False
    )


async def async_setup_managers(thermostat: "AdaptiveThermostat") -> None:
    """Initialize all manager instances for the thermostat entity.

    This function creates and configures:
    - CycleEventDispatcher for decoupled event communication
    - HeaterController for PWM/valve control
    - PreheatLearner for predictive pre-heating (if enabled)
    - NightSetbackManager for night setback management (if enabled)
    - TemperatureManager for preset and temperature management
    - KeLearner for outdoor temperature compensation learning
    - KeManager for outdoor temperature compensation
    - PIDTuningManager for PID parameter tuning
    - ControlOutputManager for control output calculation
    - CycleTrackerManager for adaptive learning cycle tracking (if available)

    Args:
        thermostat: The AdaptiveThermostat entity instance to configure
    """
    # Create cycle event dispatcher for decoupled event communication
    thermostat._cycle_dispatcher = CycleEventDispatcher()

    # Initialize heater controller now that hass is available
    thermostat._heater_controller = HeaterController(
        hass=thermostat.hass,
        thermostat=thermostat,
        heater_entity_id=thermostat._heater_entity_id,
        cooler_entity_id=thermostat._cooler_entity_id,
        demand_switch_entity_id=thermostat._demand_switch_entity_id,
        heater_polarity_invert=thermostat._heater_polarity_invert,
        pwm=thermostat._pwm,
        difference=thermostat._difference,
        min_on_cycle_duration=thermostat._min_on_cycle_duration.seconds,
        min_off_cycle_duration=thermostat._min_off_cycle_duration.seconds,
        dispatcher=thermostat._cycle_dispatcher,
    )

    # Initialize PreheatLearner if preheat is enabled
    # Check if we have stored preheat_learner data from persistence
    coordinator = thermostat._coordinator
    stored_preheat_data = None
    if coordinator and thermostat._zone_id:
        zone_data = coordinator.get_zone_data(thermostat._zone_id)
        if zone_data:
            stored_preheat_data = zone_data.get("stored_preheat_data")

    # Initialize or restore PreheatLearner (enabled by default when recovery_deadline is set)
    has_recovery_deadline = _has_recovery_deadline(thermostat._night_setback_config)
    preheat_should_init = (
        thermostat._night_setback_config.get("preheat_enabled", has_recovery_deadline)
        if thermostat._night_setback_config
        else False
    )
    if preheat_should_init:
        if stored_preheat_data:
            # Restore from persistence
            thermostat._preheat_learner = PreheatLearner.from_dict(stored_preheat_data)
            _LOGGER.info(
                "%s: PreheatLearner restored from storage (heating_type=%s, observations=%d)",
                thermostat.entity_id, thermostat._preheat_learner.heating_type, thermostat._preheat_learner.get_observation_count()
            )
        else:
            # Create new learner
            max_hours = thermostat._night_setback_config.get("max_preheat_hours")
            thermostat._preheat_learner = PreheatLearner(
                heating_type=thermostat._heating_type,
                max_hours=max_hours,
            )
            _LOGGER.info(
                "%s: PreheatLearner initialized (heating_type=%s, max_hours=%.1f)",
                thermostat.entity_id, thermostat._heating_type, thermostat._preheat_learner.max_hours
            )

    # Initialize night setback controller now that hass is available
    if thermostat._night_setback or thermostat._night_setback_config:
        # Preheat defaults to True when recovery_deadline is set, False otherwise
        has_recovery_deadline = _has_recovery_deadline(thermostat._night_setback_config)
        preheat_enabled = (
            thermostat._night_setback_config.get("preheat_enabled", has_recovery_deadline)
            if thermostat._night_setback_config
            else False
        )
        thermostat._night_setback_controller = NightSetbackManager(
            hass=thermostat.hass,
            entity_id=thermostat.entity_id,
            night_setback=thermostat._night_setback,
            night_setback_config=thermostat._night_setback_config,
            solar_recovery=None,
            window_orientation=thermostat._window_orientation,
            get_target_temp=lambda: thermostat._target_temp,
            get_current_temp=lambda: thermostat._current_temp,
            preheat_learner=thermostat._preheat_learner,
            preheat_enabled=preheat_enabled,
        )
        _LOGGER.info(
            "%s: Night setback controller initialized (preheat=%s)",
            thermostat.entity_id, preheat_enabled
        )

    # Initialize temperature manager
    thermostat._temperature_manager = TemperatureManager(
        thermostat=thermostat,
        away_temp=thermostat._away_temp,
        eco_temp=thermostat._eco_temp,
        boost_temp=thermostat._boost_temp,
        comfort_temp=thermostat._comfort_temp,
        home_temp=thermostat._home_temp,
        sleep_temp=thermostat._sleep_temp,
        activity_temp=thermostat._activity_temp,
        preset_sync_mode=thermostat._preset_sync_mode,
        min_temp=thermostat.min_temp,
        max_temp=thermostat.max_temp,
        boost_pid_off=thermostat._boost_pid_off or False,
        get_target_temp=lambda: thermostat._target_temp,
        set_target_temp=thermostat._set_target_temp,
        get_current_temp=lambda: thermostat._current_temp,
        set_force_on=thermostat._set_force_on,
        set_force_off=thermostat._set_force_off,
        async_set_pid_mode=thermostat._async_set_pid_mode_internal,
        async_control_heating=thermostat._async_control_heating_internal,
    )
    # Sync initial preset mode state
    thermostat._temperature_manager.restore_state(
        preset_mode=thermostat._attr_preset_mode,
        saved_target_temp=thermostat._saved_target_temp,
    )
    _LOGGER.info(
        "%s: Temperature manager initialized",
        thermostat.entity_id
    )

    # Initialize Ke learning
    # Check if we have stored ke_learner data from persistence
    coordinator = thermostat._coordinator
    stored_ke_data = None
    if coordinator and thermostat._zone_id:
        zone_data = coordinator.get_zone_data(thermostat._zone_id)
        if zone_data:
            stored_ke_data = zone_data.get("stored_ke_data")

    energy_rating = thermostat.hass.data.get(DOMAIN, {}).get("house_energy_rating")
    if thermostat._has_outdoor_temp_source:
        if stored_ke_data:
            # Restore KeLearner from storage
            thermostat._ke_learner = KeLearner.from_dict(stored_ke_data)
            thermostat._ke = thermostat._ke_learner.current_ke
            thermostat._pid_controller.set_pid_param(ke=thermostat._ke)
            _LOGGER.info(
                "%s: KeLearner restored from storage (Ke=%.4f, enabled=%s, observations=%d)",
                thermostat.entity_id, thermostat._ke, thermostat._ke_learner.enabled, thermostat._ke_learner.observation_count
            )
        else:
            # Calculate physics-based Ke as reference
            initial_ke = calculate_initial_ke(
                energy_rating=energy_rating,
                window_area_m2=thermostat._window_area_m2,
                floor_area_m2=thermostat._area_m2,
                window_rating=thermostat._window_rating,
                heating_type=thermostat._heating_type,
            )
            # Apply physics-based Ke from startup for accurate PID learning
            thermostat._ke = initial_ke
            thermostat._ke_learner = KeLearner(initial_ke=initial_ke)
            # PID controller starts with physics-based Ke compensation
            thermostat._pid_controller.set_pid_param(ke=initial_ke)
            temp_source = "outdoor sensor" if thermostat._ext_sensor_entity_id else "weather entity"
            _LOGGER.info(
                "%s: Ke initialized from physics using %s (Ke=%.4f) "
                "(energy_rating=%s, heating_type=%s)",
                thermostat.entity_id, temp_source, initial_ke, energy_rating or "default", thermostat._heating_type
            )
    else:
        _LOGGER.debug(
            "%s: Ke learning disabled - no outdoor temperature source configured",
            thermostat.entity_id
        )

    # Initialize Ke controller (always, even without outdoor sensor)
    thermostat._ke_controller = KeManager(
        thermostat=thermostat,
        ke_learner=thermostat._ke_learner,
        get_hvac_mode=lambda: thermostat._hvac_mode,
        get_current_temp=lambda: thermostat._current_temp,
        get_target_temp=lambda: thermostat._target_temp,
        get_ext_temp=lambda: thermostat._ext_temp,
        get_control_output=lambda: thermostat._control_output,
        get_cold_tolerance=lambda: thermostat._cold_tolerance,
        get_hot_tolerance=lambda: thermostat._hot_tolerance,
        get_ke=lambda: thermostat._ke,
        set_ke=thermostat._set_ke,
        get_pid_controller=lambda: thermostat._pid_controller,
        async_control_heating=thermostat._async_control_heating_internal,
        async_write_ha_state=thermostat._async_write_ha_state_internal,
        get_is_pid_converged=thermostat._is_pid_converged_for_ke,
    )
    _LOGGER.info(
        "%s: Ke controller initialized",
        thermostat.entity_id
    )

    # Initialize PID tuning manager
    thermostat._pid_tuning_manager = PIDTuningManager(
        thermostat=thermostat,
        pid_controller=thermostat._pid_controller,
        get_kp=lambda: thermostat._kp,
        get_ki=lambda: thermostat._ki,
        get_kd=lambda: thermostat._kd,
        get_ke=lambda: thermostat._ke,
        set_kp=thermostat._set_kp,
        set_ki=thermostat._set_ki,
        set_kd=thermostat._set_kd,
        set_ke=thermostat._set_ke,
        get_area_m2=lambda: thermostat._area_m2,
        get_ceiling_height=lambda: thermostat._ceiling_height,
        get_window_area_m2=lambda: thermostat._window_area_m2,
        get_window_rating=lambda: thermostat._window_rating,
        get_heating_type=lambda: thermostat._heating_type,
        get_hass=lambda: thermostat.hass,
        get_zone_id=lambda: thermostat._zone_id,
        get_floor_construction=lambda: thermostat._floor_construction,
        get_supply_temperature=lambda: thermostat._supply_temperature,
        get_max_power_w=lambda: thermostat._max_power_w,
        async_control_heating=thermostat._async_control_heating_internal,
        async_write_ha_state=thermostat._async_write_ha_state_internal,
    )
    _LOGGER.info(
        "%s: PID tuning manager initialized",
        thermostat.entity_id
    )

    # Initialize control output manager
    thermostat._control_output_manager = ControlOutputManager(
        thermostat_state=thermostat,
        pid_controller=thermostat._pid_controller,
        heater_controller=thermostat._heater_controller,
        set_previous_temp_time=thermostat._set_previous_temp_time,
        set_cur_temp_time=thermostat._set_cur_temp_time,
        set_control_output=thermostat._set_control_output,
        set_p=thermostat._set_p,
        set_i=thermostat._set_i,
        set_d=thermostat._set_d,
        set_e=thermostat._set_e,
        set_dt=thermostat._set_dt,
    )
    _LOGGER.info(
        "%s: Control output manager initialized",
        thermostat.entity_id
    )

    # Initialize setpoint boost manager
    # Create callback to check if night setback is active
    def is_night_period() -> bool:
        """Check if currently in night setback period."""
        if not thermostat._night_setback_controller:
            return False
        _, in_night_period, _ = thermostat._night_setback_controller.calculate_night_setback_adjustment()
        return in_night_period

    thermostat._setpoint_boost_manager = SetpointBoostManager(
        hass=thermostat.hass,
        heating_type=thermostat._heating_type,
        pid_controller=thermostat._pid_controller,
        is_night_period_cb=is_night_period,
        enabled=thermostat._setpoint_boost,
        boost_factor=thermostat._setpoint_boost_factor,
        debounce_seconds=thermostat._setpoint_debounce,
    )
    _LOGGER.info(
        "%s: Setpoint boost manager initialized (enabled=%s, debounce=%ds)",
        thermostat.entity_id, thermostat._setpoint_boost, thermostat._setpoint_debounce
    )

    # Initialize cycle tracker for adaptive learning
    coordinator = thermostat._coordinator
    if coordinator and thermostat._zone_id:
        zone_data = coordinator.get_zone_data(thermostat._zone_id)
        if zone_data:
            adaptive_learner = zone_data.get("adaptive_learner")
            if adaptive_learner:
                thermostat._cycle_tracker = CycleTrackerManager(
                    hass=thermostat.hass,
                    zone_id=thermostat._zone_id,
                    adaptive_learner=adaptive_learner,
                    get_target_temp=lambda: thermostat._target_temp,
                    get_current_temp=lambda: thermostat._current_temp,
                    get_hvac_mode=lambda: thermostat._hvac_mode,
                    get_in_grace_period=lambda: thermostat.in_learning_grace_period,
                    get_is_device_active=lambda: thermostat._is_device_active,
                    thermal_time_constant=thermostat._thermal_time_constant,
                    get_outdoor_temp=lambda: thermostat._ext_temp,
                    on_validation_failed=thermostat._handle_validation_failure,
                    on_auto_apply_check=thermostat._check_auto_apply_pid,
                    dispatcher=thermostat._cycle_dispatcher,
                    heating_type=thermostat._heating_type,
                )
                # Add cycle_tracker to zone_data for state_attributes access
                zone_data["cycle_tracker"] = thermostat._cycle_tracker
                _LOGGER.info(
                    "%s: Initialized CycleTrackerManager",
                    thermostat.entity_id
                )

    # Subscribe to CYCLE_ENDED events for preheat learning (H7 fix - store unsub handle)
    if thermostat._preheat_learner and thermostat._cycle_dispatcher:
        thermostat._preheat_cycle_unsub = thermostat._cycle_dispatcher.subscribe(
            CycleEventType.CYCLE_ENDED,
            thermostat._handle_cycle_ended_for_preheat,
        )
        _LOGGER.debug(
            "%s: Subscribed to CYCLE_ENDED events for preheat learning",
            thermostat.entity_id
        )
