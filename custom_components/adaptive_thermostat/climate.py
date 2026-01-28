"""Adds support for smart (PID) thermostat units.
For more details about this platform, please refer to the documentation at
https://github.com/ScratMan/HASmartThermostat"""

import asyncio
import logging
import time
# ABC removed - no abstract methods in this class
from datetime import datetime, timedelta
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.const import (
    ATTR_TEMPERATURE,
    EVENT_HOMEASSISTANT_START,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
NUMBER_DOMAIN = "number"  # Avoid importing from number.const for HA version compatibility
from homeassistant.core import CoreState, Event, EventStateChangedData, callback
from homeassistant.util import slugify
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .adaptive.physics import calculate_thermal_time_constant, calculate_initial_pid
from .adaptive.night_setback import NightSetback
from .adaptive.contact_sensors import ContactSensorHandler, ContactAction
from .adaptive.humidity_detector import HumidityDetector
from .adaptive.ke_learning import KeLearner
from .adaptive.preheat import PreheatLearner

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    HVACMode,
    HVACAction,
)

from . import DOMAIN
from . import const
from . import pid_controller
from .adaptive.learning import AdaptiveLearner
from .adaptive.persistence import LearningDataStore
from .managers import ControlOutputManager, HeaterController, KeManager, NightSetbackManager, PIDTuningManager, StateRestorer, TemperatureManager, CycleTrackerManager
from .managers.events import (
    CycleEventDispatcher,
    CycleEndedEvent,
    SetpointChangedEvent,
    ModeChangedEvent,
    ContactPauseEvent,
    ContactResumeEvent,
    TemperatureUpdateEvent,
)
from .managers.state_attributes import build_state_attributes
from .climate_init import async_setup_managers
from .climate_control import ClimateControlMixin
from .climate_handlers import ClimateHandlersMixin

_LOGGER = logging.getLogger(__name__)

# Re-export setup functions and schema from climate_setup module
# This maintains backward compatibility for any imports expecting these in climate.py
from .climate_setup import async_setup_platform, PLATFORM_SCHEMA, validate_pwm_compatibility

# Note: The actual implementations are in climate_setup.py
# These re-exports ensure existing imports continue to work


class AdaptiveThermostat(ClimateControlMixin, ClimateHandlersMixin, ClimateEntity, RestoreEntity):
    """Representation of an Adaptive Thermostat device."""

    def __init__(self, **kwargs):
        """Initialize the thermostat."""
        self._name = kwargs.get('name')
        self._unique_id = kwargs.get('unique_id')
        self._heater_entity_id = kwargs.get('heater_entity_id')
        self._cooler_entity_id = kwargs.get('cooler_entity_id', None)
        self._demand_switch_entity_id = kwargs.get('demand_switch_entity_id', None)
        self._heater_polarity_invert = kwargs.get('invert_heater')
        self._sensor_entity_id = kwargs.get('sensor_entity_id')
        self._ext_sensor_entity_id = kwargs.get('ext_sensor_entity_id')
        self._weather_entity_id = kwargs.get('weather_entity_id')
        self._wind_speed_sensor_entity_id = kwargs.get('wind_speed_sensor_entity_id')
        if self._unique_id == 'none':
            self._unique_id = slugify(f"{DOMAIN}_{self._name}_{self._heater_entity_id}")
        self._ac_mode = kwargs.get('ac_mode', False)
        self._force_off_state = kwargs.get('force_off_state', True)
        self._control_interval = kwargs.get('control_interval')
        self._sampling_period = kwargs.get('sampling_period').seconds
        self._sensor_stall = kwargs.get('sensor_stall').seconds
        self._output_safety = kwargs.get('output_safety')
        self._hvac_mode = kwargs.get('initial_hvac_mode', None)
        self._saved_target_temp = kwargs.get('target_temp', None) or kwargs.get('away_temp', None)
        self._temp_precision = kwargs.get('precision')
        self._target_temperature_step = kwargs.get('target_temp_step')
        self._last_heat_cycle_time = None  # None means use device's last_changed time
        self._min_on_cycle_duration_pid_on = kwargs.get('min_cycle_duration')
        self._min_off_cycle_duration_pid_on = kwargs.get('min_off_cycle_duration')
        self._min_on_cycle_duration_pid_off = kwargs.get('min_cycle_duration_pid_off')
        self._min_off_cycle_duration_pid_off = kwargs.get('min_off_cycle_duration_pid_off')
        if self._min_off_cycle_duration_pid_on is None:
            self._min_off_cycle_duration_pid_on = self._min_on_cycle_duration_pid_on
        if self._min_on_cycle_duration_pid_off is None:
            self._min_on_cycle_duration_pid_off = self._min_on_cycle_duration_pid_on
        if self._min_off_cycle_duration_pid_off is None:
            self._min_off_cycle_duration_pid_off = self._min_on_cycle_duration_pid_off
        self._active = False
        self._trigger_source = None
        self._current_temp = None
        self._cur_temp_time = None
        self._previous_temp = None
        self._previous_temp_time = None
        self._ext_temp = None
        self._wind_speed = None
        self._temp_lock = asyncio.Lock()
        self._min_temp = kwargs.get('min_temp')
        self._max_temp = kwargs.get('max_temp')
        self._target_temp = kwargs.get('target_temp')
        self._unit = kwargs.get('unit')
        self._support_flags = ClimateEntityFeature.TARGET_TEMPERATURE
        self._support_flags |= ClimateEntityFeature.TURN_OFF
        self._support_flags |= ClimateEntityFeature.TURN_ON
        self._enable_turn_on_off_backwards_compatibility = False  # Remove after deprecation period
        self._attr_preset_mode = 'none'
        self._away_temp = kwargs.get('away_temp')
        self._eco_temp = kwargs.get('eco_temp')
        self._boost_temp = kwargs.get('boost_temp')
        self._comfort_temp = kwargs.get('comfort_temp')
        self._home_temp = kwargs.get('home_temp')
        self._sleep_temp = kwargs.get('sleep_temp')
        self._activity_temp = kwargs.get('activity_temp')
        self._preset_sync_mode = kwargs.get('preset_sync_mode')
        if True in [temp is not None for temp in [self._away_temp,
                                                  self._eco_temp,
                                                  self._boost_temp,
                                                  self._comfort_temp,
                                                  self._home_temp,
                                                  self._sleep_temp,
                                                  self._activity_temp]]:
            self._support_flags |= ClimateEntityFeature.PRESET_MODE

        self._output_precision = kwargs.get('output_precision')
        self._output_min = kwargs.get('output_min')
        self._output_max = kwargs.get('output_max')
        self._output_clamp_low = kwargs.get('output_clamp_low')
        if self._output_clamp_low is None:
            self._output_clamp_low = const.DEFAULT_OUT_CLAMP_LOW
        self._output_clamp_high = kwargs.get('output_clamp_high')
        if self._output_clamp_high is None:
            self._output_clamp_high = const.DEFAULT_OUT_CLAMP_HIGH
        self._difference = self._output_max - self._output_min
        if self._ac_mode:
            self._attr_hvac_modes = [HVACMode.COOL, HVACMode.HEAT, HVACMode.OFF]
            self._min_out = -self._output_clamp_high
            self._max_out = -self._output_clamp_low
        else:
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
            self._min_out = self._output_clamp_low
            self._max_out = self._output_clamp_high
        # Zone properties for physics-based initialization
        self._zone_id = kwargs.get('zone_id')
        self._heating_type = kwargs.get('heating_type', const.HeatingType.FLOOR_HYDRONIC)
        self._area_m2 = kwargs.get('area_m2')
        self._max_power_w = kwargs.get('max_power_w')
        self._supply_temperature = kwargs.get('supply_temperature')
        self._ceiling_height = kwargs.get('ceiling_height', 2.5)
        self._window_area_m2 = kwargs.get('window_area_m2')
        self._window_rating = kwargs.get('window_rating', 'hr++')
        self._window_orientation = kwargs.get('window_orientation')
        self._floor_construction = kwargs.get('floor_construction')
        self._ha_area = kwargs.get('ha_area')  # Home Assistant area to assign entity to
        self._loops = kwargs.get('loops', const.DEFAULT_LOOPS)

        # Derivative filter alpha - get from config or use heating-type-specific default
        self._derivative_filter_alpha = kwargs.get('derivative_filter_alpha')
        if self._derivative_filter_alpha is None:
            # Use heating-type-specific default from HEATING_TYPE_CHARACTERISTICS
            heating_chars = const.HEATING_TYPE_CHARACTERISTICS.get(self._heating_type, {})
            self._derivative_filter_alpha = heating_chars.get('derivative_filter_alpha', 0.15)

        # Auto-apply PID mode (automatic application of adaptive PID recommendations)
        self._auto_apply_pid = kwargs.get('auto_apply_pid', True)

        # Night setback
        self._night_setback = None
        self._night_setback_config = None
        self._night_setback_was_active = None  # Track previous state for transition detection
        self._learning_grace_until = None  # Pause learning until this time after transitions
        night_setback_config = kwargs.get('night_setback_config')
        _LOGGER.debug("%s: night_setback_config from kwargs: %s", self._name, night_setback_config)
        if night_setback_config:
            start = night_setback_config.get(const.CONF_NIGHT_SETBACK_START)
            end = night_setback_config.get(const.CONF_NIGHT_SETBACK_END)
            _LOGGER.info("%s: Night setback configured: start=%s, end=%s", self._name, start, end)
            if start:
                # Store config for dynamic end time calculation
                self._night_setback_config = {
                    'start': start,
                    'end': end,  # May be None - will use dynamic calculation
                    'delta': night_setback_config.get(
                        const.CONF_NIGHT_SETBACK_DELTA,
                        const.DEFAULT_NIGHT_SETBACK_DELTA
                    ),
                    'recovery_deadline': night_setback_config.get(const.CONF_NIGHT_SETBACK_RECOVERY_DEADLINE),
                    'min_effective_elevation': night_setback_config.get(
                        const.CONF_MIN_EFFECTIVE_ELEVATION,
                        const.DEFAULT_MIN_EFFECTIVE_ELEVATION
                    ),
                    'preheat_enabled': night_setback_config.get(const.CONF_PREHEAT_ENABLED),
                    'max_preheat_hours': night_setback_config.get(const.CONF_MAX_PREHEAT_HOURS),
                }
                # Only create NightSetback if end is explicitly configured
                if end:
                    self._night_setback = NightSetback(
                        start_time=start,
                        end_time=end,
                        setback_delta=self._night_setback_config['delta'],
                        recovery_deadline=self._night_setback_config['recovery_deadline'],
                    )

        # Contact sensors (window/door open detection)
        self._contact_sensor_handler = None
        contact_sensors = kwargs.get('contact_sensors')
        if contact_sensors:
            contact_action = kwargs.get('contact_action', 'pause')
            contact_delay = kwargs.get('contact_delay', 300)  # Default 5 minutes
            # Convert delay to seconds if it's a timedelta-like value
            if hasattr(contact_delay, 'total_seconds'):
                contact_delay = int(contact_delay.total_seconds())
            action_enum = ContactAction.PAUSE if contact_action == 'pause' else ContactAction.FROST_PROTECTION
            self._contact_sensor_handler = ContactSensorHandler(
                contact_sensors=contact_sensors,
                contact_delay_seconds=contact_delay,
                action=action_enum,
            )
            _LOGGER.info(
                "%s: Contact sensors configured: %s (action=%s, delay=%ds)",
                self._name, contact_sensors, contact_action, contact_delay
            )

        # Humidity detector (shower/bathroom humidity spike detection)
        self._humidity_detector = None
        self._humidity_sensor_entity_id = None
        humidity_sensor = kwargs.get('humidity_sensor')
        if humidity_sensor:
            spike_threshold = kwargs.get('humidity_spike_threshold', const.DEFAULT_HUMIDITY_SPIKE_THRESHOLD)
            absolute_max = kwargs.get('humidity_absolute_max', const.DEFAULT_HUMIDITY_ABSOLUTE_MAX)
            detection_window = kwargs.get('humidity_detection_window', const.DEFAULT_HUMIDITY_DETECTION_WINDOW)
            stabilization_delay = kwargs.get('humidity_stabilization_delay', const.DEFAULT_HUMIDITY_STABILIZATION_DELAY)
            max_pause_duration = kwargs.get('humidity_max_pause_duration', const.DEFAULT_HUMIDITY_MAX_PAUSE)

            self._humidity_sensor_entity_id = humidity_sensor
            self._humidity_detector = HumidityDetector(
                spike_threshold=spike_threshold,
                absolute_max=absolute_max,
                detection_window=detection_window,
                stabilization_delay=stabilization_delay,
                max_pause_duration=max_pause_duration,
            )
            _LOGGER.info(
                "%s: Humidity detection configured: sensor=%s (spike_threshold=%.1f%%, absolute_max=%.1f%%)",
                self._name, humidity_sensor, spike_threshold, absolute_max
            )

        # Status manager - aggregates all pause mechanisms
        from .managers.status_manager import StatusManager
        self._status_manager = StatusManager(
            contact_sensor_handler=self._contact_sensor_handler,
            humidity_detector=self._humidity_detector,
        )

        # Heater controller (initialized in async_added_to_hass when hass is available)
        self._heater_controller: HeaterController | None = None

        # Night setback controller (initialized in async_added_to_hass when hass is available)
        self._night_setback_controller: NightSetbackManager | None = None

        # Temperature manager (initialized in async_added_to_hass when hass is available)
        self._temperature_manager: TemperatureManager | None = None

        # Ke learning controller (initialized in async_added_to_hass when hass is available)
        self._ke_controller: KeManager | None = None

        # PID tuning manager (initialized in async_added_to_hass when hass is available)
        self._pid_tuning_manager: PIDTuningManager | None = None

        # Cycle tracker for adaptive learning (initialized in async_added_to_hass when hass is available)
        self._cycle_tracker: CycleTrackerManager | None = None

        # Cycle event dispatcher (initialized in async_added_to_hass when hass is available)
        self._cycle_dispatcher: CycleEventDispatcher | None = None

        # Contact sensor pause tracking (for calculating pause duration in ContactResumeEvent)
        self._contact_pause_times: dict[str, datetime] = {}

        # Control output manager (initialized in async_added_to_hass when hass is available)
        self._control_output_manager: ControlOutputManager | None = None

        # Heater control failure tracking (managed by HeaterController when available)
        self._heater_control_failed = False
        self._last_heater_error: str | None = None

        # Transport delay from manifold (set when heating starts)
        self._transport_delay: float | None = None

        # Calculate PID values from physics (adaptive learning will refine them)
        # Get energy rating from controller domain config
        # Note: hass is not available during __init__, it will be set in async_added_to_hass
        self._energy_rating = None

        if self._area_m2:
            volume_m3 = self._area_m2 * self._ceiling_height
            self._thermal_time_constant = calculate_thermal_time_constant(
                volume_m3=volume_m3,
                window_area_m2=self._window_area_m2,
                floor_area_m2=self._area_m2,
                window_rating=self._window_rating,
                floor_construction=self._floor_construction,
                area_m2=self._area_m2,
                heating_type=self._heating_type,
            )
            self._kp, self._ki, self._kd = calculate_initial_pid(
                self._thermal_time_constant, self._heating_type, self._area_m2, self._max_power_w, self._supply_temperature
            )
            # Calculate outdoor temperature lag time constant: tau_lag = 2 * tau_building
            # This models the thermal inertia of the building envelope
            self._outdoor_temp_lag_tau = 2.0 * self._thermal_time_constant

            # Log power and supply temp scaling info if configured
            power_info = f", power={self._max_power_w}W" if self._max_power_w else ""
            supply_info = f", supply={self._supply_temperature}°C" if self._supply_temperature else ""
            _LOGGER.info("%s: Physics-based PID init (tau=%.2f, type=%s, window=%s%s%s): Kp=%.4f, Ki=%.5f, Kd=%.3f, outdoor_lag_tau=%.2f",
                         self.unique_id, self._thermal_time_constant, self._heating_type, self._window_rating, power_info, supply_info, self._kp, self._ki, self._kd, self._outdoor_temp_lag_tau)
        else:
            # Fallback defaults if no zone properties
            self._thermal_time_constant = None
            self._kp = 0.5
            self._ki = 0.01
            self._kd = 5.0
            self._outdoor_temp_lag_tau = 4.0  # Default 4 hours if no tau available
            _LOGGER.warning("%s: No area_m2 configured, using default PID values and outdoor_lag_tau=%.2f",
                          self.unique_id, self._outdoor_temp_lag_tau)

        # Calculate initial Ke from physics (adaptive learning will refine it)
        # Note: energy_rating may not be available during __init__ since hass isn't fully set up
        # It will be recalculated in async_added_to_hass if needed
        self._ke = const.DEFAULT_KE

        # Initialize KeLearner (will be configured properly in async_added_to_hass)
        self._ke_learner: Optional[KeLearner] = None

        # Initialize dual gain sets for mode-specific PID tuning (heating and cooling)
        # These will be restored from persistence or initialized from physics-based values
        # _heating_gains: PID gains for HEAT mode
        # _cooling_gains: PID gains for COOL mode (lazy init on first COOL mode)
        self._heating_gains: Optional[const.PIDGains] = None
        self._cooling_gains: Optional[const.PIDGains] = None

        # Initialize PreheatLearner (will be configured properly in async_added_to_hass)
        self._preheat_learner: Optional[PreheatLearner] = None
        self._preheat_cycle_unsub = None  # H7 fix - store unsub handle

        self._pwm = kwargs.get('pwm').seconds
        self._p = self._i = self._d = self._e = self._dt = 0
        self._control_output = self._output_min
        self._force_on = False
        self._force_off = False
        self._boost_pid_off = kwargs.get('boost_pid_off')

        # Get tolerances from HEATING_TYPE_CHARACTERISTICS based on heating_type
        # User-configured values are overridden by heating type defaults for consistency
        heating_type_chars = const.HEATING_TYPE_CHARACTERISTICS.get(
            self._heating_type, const.HEATING_TYPE_CHARACTERISTICS[const.HeatingType.RADIATOR]
        )
        self._cold_tolerance = heating_type_chars['cold_tolerance']
        self._hot_tolerance = heating_type_chars['hot_tolerance']

        self._time_changed = time.monotonic()
        self._last_sensor_update = time.monotonic()
        self._last_ext_sensor_update = time.monotonic()
        self._last_control_time = time.monotonic()
        _LOGGER.info("%s: Active PID values - Kp=%.4f, Ki=%.5f, Kd=%.3f, Ke=%s, D_filter_alpha=%.2f, outdoor_lag_tau=%.2f",
                     self.unique_id, self._kp, self._ki, self._kd, self._ke or 0, self._derivative_filter_alpha, self._outdoor_temp_lag_tau)
        decay_rate = const.HEATING_TYPE_INTEGRAL_DECAY.get(
            self._heating_type, const.DEFAULT_INTEGRAL_DECAY
        )
        exp_decay_tau = const.HEATING_TYPE_EXP_DECAY_TAU.get(
            self._heating_type, const.DEFAULT_EXP_DECAY_TAU
        )
        self._pid_controller = pid_controller.PID(self._kp, self._ki, self._kd, self._ke,
                                                  out_min=self._min_out, out_max=self._max_out,
                                                  sampling_period=self._sampling_period,
                                                  cold_tolerance=self._cold_tolerance,
                                                  hot_tolerance=self._hot_tolerance,
                                                  derivative_filter_alpha=self._derivative_filter_alpha,
                                                  outdoor_temp_lag_tau=self._outdoor_temp_lag_tau,
                                                  integral_decay_multiplier=decay_rate,
                                                  integral_exp_decay_tau=exp_decay_tau,
                                                  heating_type=self._heating_type)
        self._pid_controller.mode = "AUTO"

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Assign entity to Home Assistant area if configured
        if self._ha_area:
            await self._async_assign_area()

        # Assign integration label to this entity
        await self._async_assign_label()

        # Initialize all manager instances (HeaterController, CycleTrackerManager, etc.)
        await async_setup_managers(self)

        # Set night setback controller in status manager after managers are initialized
        if self._night_setback_controller:
            self._status_manager.set_night_setback_controller(self._night_setback_controller)

        # Set up state change listeners
        self._setup_state_listeners()

        # Restore state from previous session using StateRestorer
        old_state = await self.async_get_last_state()
        state_restorer = StateRestorer(self)
        state_restorer.restore(old_state)

        # Mark cycle tracker restoration complete so it can process temperature updates
        if self._cycle_tracker:
            self._cycle_tracker.set_restoration_complete()
            _LOGGER.debug("%s: Cycle tracker restoration complete", self.entity_id)

        # Set physics baseline for adaptive learning after PID values are finalized
        # (either restored from previous state or calculated from physics in __init__)
        coordinator = self._coordinator
        if coordinator and self._zone_id and self._area_m2:
            zone_data = coordinator.get_zone_data(self._zone_id)
            if zone_data:
                adaptive_learner = zone_data.get("adaptive_learner")
                if adaptive_learner:
                    adaptive_learner.set_physics_baseline(self._kp, self._ki, self._kd)
                    _LOGGER.info(
                        "%s: Set physics baseline for adaptive learning (Kp=%.4f, Ki=%.5f, Kd=%.3f)",
                        self.entity_id, self._kp, self._ki, self._kd
                    )
                    # Sync auto_apply_count from AdaptiveLearner to PID controller
                    # This ensures PID controller knows if system has been auto-tuned (for safety net control)
                    self._pid_controller.set_auto_apply_count(adaptive_learner._auto_apply_count)
                    _LOGGER.debug(
                        "%s: Synced auto_apply_count=%d to PID controller",
                        self.entity_id, adaptive_learner._auto_apply_count
                    )

        # Register manifold configuration with coordinator
        if coordinator and self._zone_id:
            manifold_registry = self.hass.data.get(DOMAIN, {}).get("manifold_registry")
            if manifold_registry:
                # Set manifold registry in coordinator if not already set
                if not coordinator.has_manifold_registry():
                    coordinator.set_manifold_registry(manifold_registry)
                # Update zone's loop count in coordinator
                coordinator.update_zone_loops(self.entity_id, self._loops)
                _LOGGER.info(
                    "%s: Registered with manifold registry (loops=%d)",
                    self.entity_id, self._loops
                )

        # Set default state to off
        if not self._hvac_mode:
            self._hvac_mode = HVACMode.OFF
        await self._async_control_heating(calc_pid=True)

    async def _async_assign_area(self) -> None:
        """Assign this entity to a Home Assistant area.

        Uses the area registry to look up the area by ID,
        then updates the entity registry to assign this entity to that area.
        """
        from homeassistant.helpers import entity_registry as er, area_registry as ar

        entity_registry = er.async_get(self.hass)
        area_registry = ar.async_get(self.hass)

        # Look up the area by ID
        area = area_registry.async_get_area(self._ha_area)
        if area is None:
            _LOGGER.warning(
                "%s: Area ID '%s' not found, skipping area assignment",
                self.entity_id,
                self._ha_area,
            )
            return

        # Update entity to assign it to the area
        entity_registry.async_update_entity(
            self.entity_id,
            area_id=area.id,
        )
        _LOGGER.info(
            "%s: Assigned to area '%s' (ID: %s)",
            self.entity_id,
            area.name,
            self._ha_area,
        )

    async def _async_assign_label(self) -> None:
        """Assign integration label to this entity."""
        from homeassistant.helpers import entity_registry as er, label_registry as lr

        entity_registry = er.async_get(self.hass)
        label_registry = lr.async_get(self.hass)

        label_name = "Adaptive Thermostat"
        label = label_registry.async_get_label_by_name(label_name)

        if label is None:
            label = label_registry.async_create(
                label_name,
                icon="mdi:thermostat-box",
                color="indigo",
            )

        entity_registry.async_update_entity(
            self.entity_id,
            labels={label.label_id},
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is being removed from Home Assistant.

        This method saves learning data and unregisters the zone from the
        coordinator to ensure clean removal and prevent stale zone data.
        """
        await super().async_will_remove_from_hass()

        # Clean up preheat cycle dispatcher subscription (H7 fix)
        if self._preheat_cycle_unsub:
            self._preheat_cycle_unsub()
            self._preheat_cycle_unsub = None

        # Clean up cycle tracker subscriptions and timers
        if self._cycle_tracker:
            self._cycle_tracker.cleanup()

        # Save learning data before removal
        if self._zone_id:
            learning_store = self.hass.data.get(DOMAIN, {}).get("learning_store")
            if learning_store:
                # Get adaptive_learner from coordinator zone_data
                coordinator = self._coordinator
                adaptive_data = None
                if coordinator:
                    zone_data = coordinator.get_zone_data(self._zone_id)
                    if zone_data:
                        adaptive_learner = zone_data.get("adaptive_learner")
                        if adaptive_learner:
                            adaptive_data = adaptive_learner.to_dict()

                # Get ke_learner data from this entity
                ke_data = None
                if self._ke_learner:
                    ke_data = self._ke_learner.to_dict()

                # Save both learners to storage
                await learning_store.async_save_zone(
                    zone_id=self._zone_id,
                    adaptive_data=adaptive_data,
                    ke_data=ke_data,
                )
                _LOGGER.info(
                    "%s: Saved learning data for zone %s on removal "
                    "(adaptive=%s, ke=%s)",
                    self.entity_id,
                    self._zone_id,
                    adaptive_data is not None,
                    ke_data is not None,
                )

        # Unregister zone from coordinator
        if self._zone_id:
            coordinator = self._coordinator
            if coordinator:
                coordinator.unregister_zone(self._zone_id)
                _LOGGER.info(
                    "%s: Unregistered zone %s from coordinator",
                    self.entity_id,
                    self._zone_id,
                )

    def _setup_state_listeners(self) -> None:
        """Set up all state change listeners for sensors and controlled entities.

        This method registers listeners for:
        - Temperature sensor changes
        - External temperature sensor changes (if configured)
        - Heater entity state changes (if configured)
        - Cooler entity state changes (if configured)
        - Demand switch state changes (if configured)
        - Keep-alive interval timer (if configured)
        - Startup callback to initialize sensor values
        """
        # Temperature sensor listener
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self._sensor_entity_id,
                self._async_sensor_changed))

        # External temperature sensor listener
        if self._ext_sensor_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._ext_sensor_entity_id,
                    self._async_ext_sensor_changed))
        elif self._weather_entity_id is not None:
            # Use weather entity temperature as fallback when no outdoor sensor
            _LOGGER.info(
                "%s: Using weather entity %s temperature as outdoor temperature fallback",
                self.entity_id, self._weather_entity_id
            )
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._weather_entity_id,
                    self._async_weather_entity_changed))

        # Wind speed sensor listener
        if self._wind_speed_sensor_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._wind_speed_sensor_entity_id,
                    self._async_wind_speed_sensor_changed))
        elif self._weather_entity_id is not None:
            _LOGGER.info(
                "%s: Using weather entity %s wind_speed as fallback",
                self.entity_id, self._weather_entity_id
            )
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._weather_entity_id,
                    self._async_weather_entity_wind_changed))

        # Heater entity listener
        if self._heater_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._heater_entity_id,
                    self._async_switch_changed))

        # Cooler entity listener
        if self._cooler_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._cooler_entity_id,
                    self._async_switch_changed))

        # Demand switch entity listener
        if self._demand_switch_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._demand_switch_entity_id,
                    self._async_switch_changed))

        # Contact sensor listeners (window/door open detection)
        if self._contact_sensor_handler:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._contact_sensor_handler.contact_sensors,
                    self._async_contact_sensor_changed))
            # Initialize contact sensor states on startup
            self._update_contact_sensor_states()

        # Humidity sensor listener (shower/bathroom humidity spike detection)
        if self._humidity_detector and self._humidity_sensor_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._humidity_sensor_entity_id],
                    self._async_humidity_sensor_changed))

        # Thermal groups leader tracking (follower zones track leader setpoint)
        coordinator = self._coordinator
        if self._zone_id and coordinator:
            thermal_group_manager = coordinator.thermal_group_manager
            if thermal_group_manager:
                leader_zone_id = thermal_group_manager.get_leader_zone(self._zone_id)
                if leader_zone_id:
                    # This is a follower zone - track leader's state
                    leader_entity_id = f"climate.{leader_zone_id}"
                    _LOGGER.info(
                        "%s: Follower zone tracking leader %s",
                        self.entity_id, leader_entity_id
                    )
                    self.async_on_remove(
                        async_track_state_change_event(
                            self.hass,
                            leader_entity_id,
                            self._async_leader_changed))

        # Control loop interval timer
        # Derive interval: explicit control_interval > sampling_period > default 60s
        if self._control_interval:
            control_interval = self._control_interval
        elif self._sampling_period > 0:
            control_interval = timedelta(seconds=self._sampling_period)
        else:
            control_interval = timedelta(seconds=const.DEFAULT_CONTROL_INTERVAL)
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_control_heating,
                control_interval))

        # Startup callback to initialize sensor values
        @callback
        def _async_startup(*_):
            """Init on startup."""
            sensor_state = self.hass.states.get(self._sensor_entity_id)
            if sensor_state and sensor_state.state != STATE_UNKNOWN:
                self._async_update_temp(sensor_state)
            if self._ext_sensor_entity_id is not None:
                ext_sensor_state = self.hass.states.get(self._ext_sensor_entity_id)
                if ext_sensor_state and ext_sensor_state.state != STATE_UNKNOWN:
                    self._async_update_ext_temp(ext_sensor_state)
            elif self._weather_entity_id is not None:
                # Use weather entity temperature as fallback
                weather_state = self.hass.states.get(self._weather_entity_id)
                if weather_state and weather_state.state != STATE_UNKNOWN:
                    self._async_update_ext_temp_from_weather(weather_state)

            # Initialize wind speed sensor state
            if self._wind_speed_sensor_entity_id is not None:
                wind_sensor_state = self.hass.states.get(self._wind_speed_sensor_entity_id)
                if wind_sensor_state and wind_sensor_state.state != STATE_UNKNOWN:
                    self._async_update_wind_speed(wind_sensor_state)
            elif self._weather_entity_id is not None:
                # Use weather entity wind_speed as fallback
                weather_state = self.hass.states.get(self._weather_entity_id)
                if weather_state and weather_state.state != STATE_UNKNOWN:
                    self._async_update_wind_speed_from_weather(weather_state)

        if self.hass.state == CoreState.running:
            _async_startup()
        else:
            # H1 fix - wrap async_listen_once with async_on_remove
            self.async_on_remove(
                self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)
            )

    def _restore_state(self, old_state) -> None:
        """Restore climate entity state from Home Assistant's state restoration.

        This is a compatibility wrapper that delegates to StateRestorer.
        """
        state_restorer = StateRestorer(self)
        state_restorer._restore_state(old_state)

    def _restore_pid_values(self, old_state) -> None:
        """Restore PID controller values from Home Assistant's state restoration.

        This is a compatibility wrapper that delegates to StateRestorer.
        """
        state_restorer = StateRestorer(self)
        state_restorer._restore_pid_values(old_state)

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def _has_outdoor_temp_source(self) -> bool:
        """Check if any outdoor temperature source is configured."""
        return self._ext_sensor_entity_id is not None or self._weather_entity_id is not None

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def _coordinator(self):
        """Return the coordinator instance (cached lookup)."""
        return self.hass.data.get(DOMAIN, {}).get("coordinator")

    @staticmethod
    def _get_number_entity_domain(entity_id):
        return INPUT_NUMBER_DOMAIN if "input_number" in entity_id else NUMBER_DOMAIN

    @property
    def precision(self):
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._target_temperature_step

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._current_temp

    @property
    def hvac_mode(self):
        """Return current operation."""
        return self._hvac_mode

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported.
        Need to be one of CURRENT_HVAC_*.
        """
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if not self._is_device_active:
            return HVACAction.IDLE
        if self._hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        return HVACAction.HEATING

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temp

    @property
    def preset_mode(self):
        """Return the current preset mode, e.g., home, away, temp."""
        return self._temperature_manager.preset_mode

    @property
    def preset_modes(self):
        """Return a list of available preset modes."""
        return self._temperature_manager.preset_modes

    @property
    def _preset_modes_temp(self):
        """Return a dict of preset modes and their temperatures."""
        return self._temperature_manager._preset_modes_temp

    @property
    def _preset_temp_modes(self):
        """Return a dict of preset temperatures and their modes."""
        return self._temperature_manager._preset_temp_modes

    @property
    def presets(self):
        """Return a dict of available presets and their temperatures."""
        return self._temperature_manager.presets

    @property
    def in_learning_grace_period(self) -> bool:
        """Check if learning should be paused due to recent night setback transition."""
        if self._night_setback_controller:
            return self._night_setback_controller.in_learning_grace_period
        # No night setback controller means no grace period
        return False

    def _set_learning_grace_period(self, minutes: int = 60):
        """Set a grace period to pause learning after night setback transitions."""
        if self._night_setback_controller:
            self._night_setback_controller.set_learning_grace_period(minutes)

    def _calculate_night_setback_adjustment(self, current_time=None):
        """Calculate night setback adjustment for effective target temperature.

        Delegates to NightSetbackManager for all calculation logic.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            A tuple of (effective_target, in_night_period, night_setback_info) where:
            - effective_target: The adjusted target temperature
            - in_night_period: Whether we are currently in the night setback period
            - night_setback_info: Dict with additional info for state attributes
        """
        # Delegate to controller if available
        if self._night_setback_controller:
            return self._night_setback_controller.calculate_night_setback_adjustment(current_time)

        # Fallback: return defaults when controller not yet initialized
        # (e.g., before async_added_to_hass is called)
        return self._target_temp, False, {"night_setback_active": False}

    @property
    def _min_on_cycle_duration(self):
        if self.pid_mode == 'off':
            return self._min_on_cycle_duration_pid_off
        return self._min_on_cycle_duration_pid_on

    @property
    def _min_off_cycle_duration(self):
        if self.pid_mode == 'off':
            return self._min_off_cycle_duration_pid_off
        return self._min_off_cycle_duration_pid_on

    @property
    def pid_parm(self):
        """Return the pid parameters of the thermostat."""
        return self._kp, self._ki, self._kd

    @property
    def pid_control_p(self):
        """Return the proportional output of PID controller."""
        return self._p

    @property
    def pid_control_i(self):
        """Return the integral output of PID controller."""
        return self._i

    @property
    def pid_control_d(self):
        """Return the derivative output of PID controller."""
        return self._d

    @property
    def pid_control_e(self):
        """Return the external output of external temperature compensation."""
        return self._e

    @property
    def loops(self) -> int:
        """Return the number of heating loops for this zone."""
        return self._loops

    @property
    def pid_mode(self):
        """Return the PID operating mode."""
        if getattr(self, '_pid_controller', None) is not None:
            return self._pid_controller.mode.lower()
        return 'off'

    @property
    def pid_control_output(self):
        """Return the pid control output of the thermostat."""
        return self._control_output

    @property
    def extra_state_attributes(self):
        """Return extra state attributes to include in entity."""
        return build_state_attributes(self)

    def set_hvac_mode(self, hvac_mode: (HVACMode, str)) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.HEAT:
            self._min_out = self._output_clamp_low
            self._max_out = self._output_clamp_high
            self._hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.COOL:
            self._min_out = -self._output_clamp_high
            self._max_out = -self._output_clamp_low
            self._hvac_mode = HVACMode.COOL
        elif hvac_mode == HVACMode.HEAT_COOL:
            self._min_out = -self._output_clamp_high
            self._max_out = self._output_clamp_high
            self._hvac_mode = HVACMode.HEAT_COOL
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
            self._control_output = self._output_min
            self._previous_temp = None
            self._previous_temp_time = None
            if self._pid_controller is not None:
                self._pid_controller.clear_samples()
        if self._pid_controller:
            self._pid_controller.out_max = self._max_out
            self._pid_controller.out_min = self._min_out

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        old_mode = self._hvac_mode

        await self._async_heater_turn_off(force=True)
        if hvac_mode == HVACMode.HEAT:
            self._min_out = self._output_clamp_low
            self._max_out = self._output_clamp_high
            self._hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.COOL:
            self._min_out = -self._output_clamp_high
            self._max_out = -self._output_clamp_low
            self._hvac_mode = HVACMode.COOL
        elif hvac_mode == HVACMode.HEAT_COOL:
            self._min_out = -self._output_clamp_high
            self._max_out = self._output_clamp_high
            self._hvac_mode = HVACMode.HEAT_COOL
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
            self._control_output = self._output_min
            # Reset duty accumulator when turning OFF
            if self._heater_controller is not None:
                self._heater_controller.reset_duty_accumulator()
            if self._pwm:
                _LOGGER.debug("%s: Turn OFF heater from async_set_hvac_mode(%s)",
                              self.entity_id,
                              hvac_mode)
                await self._async_heater_turn_off(force=True)
            else:
                _LOGGER.debug("%s: Set heater to %s from async_set_hvac_mode(%s)",
                              self.entity_id,
                              self._control_output,
                              hvac_mode)
                await self._async_set_valve_value(self._control_output)
            # Clear the samples to avoid integrating the off period
            self._previous_temp = None
            self._previous_temp_time = None
            if self._pid_controller is not None:
                self._pid_controller.clear_samples()
            # Reset PID calc timing to avoid stale dt when turned back on
            if self._control_output_manager is not None:
                self._control_output_manager.reset_calc_timing()
        else:
            _LOGGER.error("%s: Unrecognized HVAC mode: %s", self.entity_id, hvac_mode)
            return
        if self._pid_controller:
            self._pid_controller.out_max = self._max_out
            self._pid_controller.out_min = self._min_out
        if self._hvac_mode != HVACMode.OFF:
            await self._async_control_heating(calc_pid=True)
        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

        # Trigger mode sync if configured
        if self._zone_id and old_mode != self._hvac_mode:
            mode_sync = self.hass.data.get(DOMAIN, {}).get("mode_sync")
            if mode_sync:
                await mode_sync.on_mode_change(
                    zone_id=self._zone_id,
                    old_mode=old_mode.value if old_mode else "off",
                    new_mode=self._hvac_mode.value if self._hvac_mode else "off",
                    climate_entity_id=self.entity_id,
                )

        # Emit mode changed event and notify cycle tracker
        if old_mode != self._hvac_mode:
            old_mode_str = old_mode.value if old_mode else "off"
            new_mode_str = self._hvac_mode.value if self._hvac_mode else "off"

            # Emit event
            if hasattr(self, "_cycle_dispatcher") and self._cycle_dispatcher:
                self._cycle_dispatcher.emit(
                    ModeChangedEvent(
                        timestamp=dt_util.utcnow(),
                        old_mode=old_mode_str,
                        new_mode=new_mode_str,
                    )
                )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._temperature_manager.async_set_temperature(temperature)
        self.async_write_ha_state()

    async def async_set_pid(self, **kwargs):
        """Set PID parameters.

        Delegates to PIDTuningManager for the actual implementation.
        """
        await self._pid_tuning_manager.async_set_pid(**kwargs)

    async def async_set_pid_mode(self, **kwargs):
        """Set PID mode (AUTO or OFF).

        Delegates to PIDTuningManager for the actual implementation.
        """
        await self._pid_tuning_manager.async_set_pid_mode(**kwargs)

    async def async_set_preset_temp(self, **kwargs):
        """Set the presets modes temperatures."""
        await self._temperature_manager.async_set_preset_temp(**kwargs)

    async def clear_integral(self, **kwargs):
        """Clear the integral value."""
        self._pid_controller.integral = 0.0
        self._i = self._pid_controller.integral
        self.async_write_ha_state()

    async def async_reset_pid_to_physics(self, **kwargs):
        """Reset PID values to physics-based defaults.

        Delegates to PIDTuningManager for the actual implementation.
        """
        await self._pid_tuning_manager.async_reset_pid_to_physics(**kwargs)

    async def async_apply_adaptive_pid(self, **kwargs):
        """Apply adaptive PID values based on learned metrics.

        Delegates to PIDTuningManager for the actual implementation.
        """
        await self._pid_tuning_manager.async_apply_adaptive_pid(**kwargs)

    async def async_apply_adaptive_ke(self, **kwargs):
        """Apply adaptive Ke value based on learned outdoor temperature correlations.

        Delegates to PIDTuningManager (which delegates to KeManager) for the actual implementation.
        """
        if self._ke_controller is not None:
            await self._ke_controller.async_apply_adaptive_ke(**kwargs)

    async def async_clear_learning(self, **kwargs):
        """Clear all learning data and reset PID to physics defaults.

        Delegates to PIDTuningManager for the actual implementation.
        """
        await self._pid_tuning_manager.async_clear_learning(**kwargs)

    async def async_rollback_pid(self, **kwargs):
        """Rollback PID to previous configuration.

        Service call handler for rollback_pid.
        Delegates to PIDTuningManager for the actual implementation.
        """
        await self._pid_tuning_manager.async_rollback_pid()

    async def _check_auto_apply_pid(self) -> None:
        """Check and potentially auto-apply adaptive PID recommendations.

        Called after each cycle finalization when auto_apply_pid is enabled.
        Obtains outdoor temperature from sensor state if available and triggers
        auto-apply evaluation through PIDTuningManager.
        """
        if not self._auto_apply_pid or not self._pid_tuning_manager:
            return

        # Get outdoor temperature from sensor state if available
        outdoor_temp = None
        if self._ext_sensor_entity_id is not None:
            ext_sensor_state = self.hass.states.get(self._ext_sensor_entity_id)
            if ext_sensor_state and ext_sensor_state.state not in (
                STATE_UNAVAILABLE, STATE_UNKNOWN
            ):
                try:
                    outdoor_temp = float(ext_sensor_state.state)
                except (ValueError, TypeError):
                    pass
        elif self._ext_temp is not None:
            outdoor_temp = self._ext_temp

        result = await self._pid_tuning_manager.async_auto_apply_adaptive_pid(outdoor_temp)

        if result.get("applied"):
            # Send persistent notification about auto-apply
            recommendation = result.get("recommendation", {})
            old_values = result.get("old_values", {})
            new_values = result.get("new_values", {})

            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": f"adaptive_thermostat_auto_apply_{self._zone_id}",
                    "title": f"🔧 PID Auto-Applied: {self._name}",
                    "message": (
                        f"Adaptive PID values have been automatically applied.\n\n"
                        f"**Previous values:**\n"
                        f"- Kp: {old_values.get('kp', 'N/A'):.4f}\n"
                        f"- Ki: {old_values.get('ki', 'N/A'):.5f}\n"
                        f"- Kd: {old_values.get('kd', 'N/A'):.3f}\n\n"
                        f"**New values:**\n"
                        f"- Kp: {new_values.get('kp', 'N/A'):.4f}\n"
                        f"- Ki: {new_values.get('ki', 'N/A'):.5f}\n"
                        f"- Kd: {new_values.get('kd', 'N/A'):.3f}\n\n"
                        f"The system will validate performance over the next 5 cycles. "
                        f"If performance degrades, it will automatically rollback.\n\n"
                        f"To manually rollback, call service: "
                        f"`adaptive_thermostat.rollback_pid`"
                    ),
                },
                blocking=False,
            )
            _LOGGER.info(
                "%s: Auto-applied PID values: Kp=%.4f→%.4f, Ki=%.5f→%.5f, Kd=%.3f→%.3f",
                self.entity_id,
                old_values.get("kp", 0),
                new_values.get("kp", 0),
                old_values.get("ki", 0),
                new_values.get("ki", 0),
                old_values.get("kd", 0),
                new_values.get("kd", 0),
            )

    def _handle_cycle_ended_for_preheat(self, event: CycleEndedEvent) -> None:
        """Handle CYCLE_ENDED event to record preheat observations.

        Called when a heating cycle completes. Records heating rate observation
        if cycle was successful (not interrupted) and outdoor temperature is available.

        Args:
            event: The CycleEndedEvent containing cycle metrics
        """
        if not self._preheat_learner:
            return

        # Only record if cycle completed successfully (not interrupted)
        if not event.metrics or event.metrics.get("interrupted"):
            return

        # Extract cycle data
        start_temp = event.metrics.get("start_temp")
        end_temp = event.metrics.get("end_temp")
        duration_minutes = event.metrics.get("duration_minutes")
        outdoor_temp = self._ext_temp

        # Record observation if we have all required data
        if start_temp and end_temp and duration_minutes and outdoor_temp is not None:
            self._preheat_learner.add_observation(
                start_temp=start_temp,
                end_temp=end_temp,
                outdoor_temp=outdoor_temp,
                duration_minutes=duration_minutes,
                timestamp=event.timestamp,
            )
            _LOGGER.debug(
                "%s: Recorded preheat observation (delta=%.1f°C, outdoor=%.1f°C, duration=%.0fmin)",
                self.entity_id,
                end_temp - start_temp,
                outdoor_temp,
                duration_minutes,
            )

            # Schedule persistence save
            coordinator = self._coordinator
            if coordinator and self._zone_id:
                zone_data = coordinator.get_zone_data(self._zone_id)
                if zone_data:
                    # Update preheat data in zone_data for persistence
                    from .adaptive.persistence import LearningDataStore
                    learning_store = self.hass.data.get(DOMAIN, {}).get("learning_store")
                    if learning_store:
                        learning_store.update_zone_data(
                            self._zone_id,
                            preheat_data=self._preheat_learner.to_dict(),
                        )
                        learning_store.schedule_zone_save()

    async def _handle_validation_failure(self) -> None:
        """Handle validation failure by rolling back PID values.

        Called by CycleTrackerManager when validation detects performance degradation
        after an auto-apply. Triggers automatic rollback and notifies the user.
        """
        if not self._pid_tuning_manager:
            return

        success = await self._pid_tuning_manager.async_rollback_pid()

        if success:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": f"adaptive_thermostat_rollback_{self._zone_id}",
                    "title": f"⚠️ PID Rolled Back: {self._name}",
                    "message": (
                        f"The auto-applied PID values caused performance degradation "
                        f"(>30% worse overshoot). The system has automatically rolled "
                        f"back to the previous configuration.\n\n"
                        f"Learning will continue and may recommend new values "
                        f"when confidence improves."
                    ),
                },
                blocking=False,
            )
            _LOGGER.warning(
                "%s: Validation failed - PID values rolled back automatically",
                self.entity_id,
            )
        else:
            _LOGGER.error(
                "%s: Validation failed but rollback failed - no previous PID history",
                self.entity_id,
            )

    def _is_at_steady_state(self) -> bool:
        """Check if the system is at steady state (maintaining target temperature).

        Delegates to KeManager for the actual implementation.

        Returns:
            True if at steady state, False otherwise
        """
        if self._ke_controller is not None:
            return self._ke_controller.is_at_steady_state()
        return False

    def _maybe_record_ke_observation(self) -> None:
        """Record a Ke observation if conditions are met.

        Delegates to KeManager for the actual implementation.
        """
        if self._ke_controller is not None:
            self._ke_controller.maybe_record_observation()

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        if self._min_temp:
            return self._min_temp

        # get default temp from super class
        return super().min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._max_temp:
            return self._max_temp

        # Get default temp from super class
        return super().max_temp

    # Event handlers and state update methods moved to ClimateHandlersMixin (climate_handlers.py)

    # _async_control_heating, _is_device_active, and _get_cycle_start_time moved to ClimateControlMixin

    # Setter callbacks for HeaterController
    def _set_is_heating(self, value: bool) -> None:
        """Set the heating state flag."""
        self._is_heating = value

    def _set_last_heat_cycle_time(self, value: float) -> None:
        """Set the last heat cycle time."""
        self._last_heat_cycle_time = value

    def _set_time_changed(self, value: float) -> None:
        """Set the time changed value."""
        self._time_changed = value

    def _set_force_on(self, value: bool) -> None:
        """Set the force on flag."""
        self._force_on = value

    def _set_force_off(self, value: bool) -> None:
        """Set the force off flag."""
        self._force_off = value

    # Setter callbacks for KeManager
    def _set_ke(self, value: float) -> None:
        """Set the Ke value."""
        self._ke = value

    # Setter callbacks for PIDTuningManager
    def _set_kp(self, value: float) -> None:
        """Set the Kp value."""
        self._kp = value

    def _set_ki(self, value: float) -> None:
        """Set the Ki value."""
        self._ki = value

    def _set_kd(self, value: float) -> None:
        """Set the Kd value."""
        self._kd = value

    # Setter callbacks for ControlOutputManager
    def _set_control_output(self, value: float) -> None:
        """Set the control output value."""
        self._control_output = value

    def _set_p(self, value: float) -> None:
        """Set the proportional component value."""
        self._p = value

    def _set_i(self, value: float) -> None:
        """Set the integral component value."""
        self._i = value

    def _set_d(self, value: float) -> None:
        """Set the derivative component value."""
        self._d = value

    def _set_e(self, value: float) -> None:
        """Set the external component value."""
        self._e = value

    def _set_dt(self, value: float) -> None:
        """Set the delta time value."""
        self._dt = value

    def _set_previous_temp_time(self, value: float) -> None:
        """Set the previous temperature time."""
        self._previous_temp_time = value

    def _set_cur_temp_time(self, value: float) -> None:
        """Set the current temperature time."""
        self._cur_temp_time = value

    def _is_pid_converged_for_ke(self) -> bool:
        """Check if PID has converged sufficiently for Ke learning.

        Returns True if the adaptive learner reports PID convergence
        (stable performance for required number of consecutive cycles).
        """
        coordinator = self._coordinator
        if not coordinator or not self._zone_id:
            return False
        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return False
        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return False
        return adaptive_learner.is_pid_converged_for_ke()

    async def _async_write_ha_state_internal(self) -> None:
        """Write HA state (internal callback for managers)."""
        self.async_write_ha_state()

    # Setter callbacks for TemperatureManager
    def _set_target_temp(self, value: float) -> None:
        """Set the target temperature."""
        # Track old temperature for cycle tracker
        old_temp = self._target_temp

        # Update target temperature
        self._target_temp = value

        # Emit setpoint changed event
        if old_temp is not None and old_temp != value:
            # Reset duty accumulator if setpoint changes by more than 0.5°C
            if abs(value - old_temp) > 0.5 and self._heater_controller is not None:
                self._heater_controller.reset_duty_accumulator()

            if hasattr(self, "_cycle_dispatcher") and self._cycle_dispatcher:
                self._cycle_dispatcher.emit(
                    SetpointChangedEvent(
                        hvac_mode=str(self._hvac_mode.value) if self._hvac_mode else "off",
                        timestamp=dt_util.utcnow(),
                        old_target=old_temp,
                        new_target=value,
                    )
                )

    async def _async_set_pid_mode_internal(self, mode: str) -> None:
        """Internal callback to set PID mode from TemperatureManager."""
        await self.async_set_pid_mode(mode=mode)

    async def _async_control_heating_internal(self, calc_pid: bool) -> None:
        """Internal callback to trigger heating control from TemperatureManager."""
        await self._async_control_heating(calc_pid=calc_pid)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    @property
    def heater_or_cooler_entity(self):
        """Return the entities to be controlled based on HVAC MODE.

        Returns heater or cooler entities based on mode, plus any demand_switch
        entities which are controlled regardless of heat/cool mode.

        Delegates to HeaterController for the actual list.
        """
        return self._heater_controller.get_entities(self.hvac_mode)

    def _fire_heater_control_failed_event(
        self,
        entity_id: str,
        operation: str,
        error: str,
    ) -> None:
        """Fire an event when heater control fails.

        Delegates to HeaterController for the actual event firing.

        Args:
            entity_id: Entity that failed to control
            operation: Operation that failed (turn_on, turn_off, set_value)
            error: Error message
        """
        self._heater_controller._fire_heater_control_failed_event(
            entity_id, operation, error
        )

    async def _async_call_heater_service(
        self,
        entity_id: str,
        domain: str,
        service: str,
        data: dict,
    ) -> bool:
        """Call a heater/cooler service with error handling.

        Delegates to HeaterController for the actual service call.

        Args:
            entity_id: Entity ID being controlled
            domain: Service domain (homeassistant, light, valve, number, etc.)
            service: Service name (turn_on, turn_off, set_value, etc.)
            data: Service call data

        Returns:
            True if successful, False otherwise
        """
        result = await self._heater_controller._async_call_heater_service(
            entity_id, domain, service, data
        )
        # Sync failure state from controller
        self._heater_control_failed = self._heater_controller.heater_control_failed
        self._last_heater_error = self._heater_controller.last_heater_error
        return result

    @property
    def _effective_min_on_seconds(self) -> int:
        """Minimum on-cycle duration including manifold transport delay."""
        base = self._min_on_cycle_duration.seconds
        if self._transport_delay and self._transport_delay > 0:
            base += int(self._transport_delay * 60)
        return base

    async def _async_heater_turn_on(self):
        """Turn heater toggleable device on.

        Delegates to HeaterController for the actual turn on operation.
        """
        # Query transport delay from manifold registry
        coordinator = self._coordinator
        if coordinator and self._zone_id:
            delay = coordinator.get_transport_delay_for_zone(self.entity_id)
            if delay is not None and delay > 0:
                self._transport_delay = delay
                # Pass to PID controller for dead time compensation
                self._pid_controller.set_transport_delay(delay)
                # Pass to cycle tracker if available
                if self._cycle_tracker:
                    self._cycle_tracker.set_transport_delay(delay)
                _LOGGER.debug(
                    "%s: Set transport delay %.1f minutes for heating start",
                    self.entity_id, delay
                )

        # Update cycle durations in case PID mode changed
        self._heater_controller.update_cycle_durations(
            self._effective_min_on_seconds,
            self._min_off_cycle_duration.seconds,
        )
        await self._heater_controller.async_turn_on(
            hvac_mode=self.hvac_mode,
            get_cycle_start_time=self._get_cycle_start_time,
            set_is_heating=self._set_is_heating,
            set_last_heat_cycle_time=self._set_last_heat_cycle_time,
        )

        # Mark manifold as active now that heater is on
        manifold_registry = self.hass.data.get(DOMAIN, {}).get("manifold_registry")
        if manifold_registry:
            manifold_registry.mark_manifold_active(self.entity_id)

    async def _async_heater_turn_off(self, force=False):
        """Turn heater toggleable device off.

        Delegates to HeaterController for the actual turn off operation.
        """
        # Reset transport delay when heating stops
        if self._transport_delay is not None:
            self._pid_controller.reset_dead_time()
            self._transport_delay = None
            _LOGGER.debug("%s: Reset transport delay on heating stop", self.entity_id)

        # Update cycle durations in case PID mode changed
        self._heater_controller.update_cycle_durations(
            self._effective_min_on_seconds,
            self._min_off_cycle_duration.seconds,
        )
        await self._heater_controller.async_turn_off(
            hvac_mode=self.hvac_mode,
            get_cycle_start_time=self._get_cycle_start_time,
            set_is_heating=self._set_is_heating,
            set_last_heat_cycle_time=self._set_last_heat_cycle_time,
            force=force,
        )

    async def _async_set_valve_value(self, value: float):
        """Set valve value for non-PWM devices.

        Delegates to HeaterController for the actual valve control.
        """
        await self._heater_controller.async_set_valve_value(value, self.hvac_mode)

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode.
        This method must be run in the event loop and returns a coroutine.
        """
        await self._temperature_manager.async_set_preset_mode(preset_mode)
        # Sync internal state for backward compatibility
        self._attr_preset_mode = self._temperature_manager.preset_mode
        self._saved_target_temp = self._temperature_manager.saved_target_temp

    # calc_output, set_control_value, and pwm_switch moved to ClimateControlMixin
