"""Adds support for smart (PID) thermostat units.
For more details about this platform, please refer to the documentation at
https://github.com/ScratMan/HASmartThermostat"""

from __future__ import annotations

import asyncio
import logging
import time

# ABC removed - no abstract methods in this class
from datetime import datetime, timedelta

from homeassistant.const import (
    ATTR_TEMPERATURE,
    EVENT_HOMEASSISTANT_START,
    STATE_UNKNOWN,
)
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN

NUMBER_DOMAIN = "number"  # Avoid importing from number.const for HA version compatibility
from homeassistant.core import CoreState, callback
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
    HVACMode,
    HVACAction,
)

from . import DOMAIN
from . import const
from . import pid_controller
from .const import PIDGains, PIDChangeReason
from .managers import (
    ControlOutputManager,
    HeaterController,
    KeManager,
    NightSetbackManager,
    PIDTuningManager,
    SetpointBoostManager,
    StateRestorer,
    TemperatureManager,
    CycleTrackerManager,
)
from .managers.pid_gains_manager import PIDGainsManager
from .managers.events import (
    CycleEventDispatcher,
    ModeChangedEvent,
)
from .managers.state_attributes import build_state_attributes
from .climate_init import async_setup_managers
from .climate_control import ClimateControlMixin
from .climate_cycle_handlers import ClimateCycleHandlersMixin
from .climate_handlers import ClimateHandlersMixin
from .climate_pid_services import ClimatePIDServicesMixin
from .climate_state_setters import ClimateStateSettersMixin
from .thermostat_config import AdaptiveThermostatConfig

_LOGGER = logging.getLogger(__name__)

# Re-export setup functions and schema from climate_setup module
# HA loads platform setup from this module — these re-exports are required
from .climate_setup import async_setup_platform as async_setup_platform
from .climate_setup import PLATFORM_SCHEMA as PLATFORM_SCHEMA


class AdaptiveThermostat(
    ClimateCycleHandlersMixin,
    ClimatePIDServicesMixin,
    ClimateStateSettersMixin,
    ClimateControlMixin,
    ClimateHandlersMixin,
    ClimateEntity,
    RestoreEntity,
):
    """Representation of an Adaptive Climate device."""

    def __init__(self, config: AdaptiveThermostatConfig):
        """Initialize the thermostat."""
        self._name = config.name
        self._unique_id = config.unique_id
        self._heater_entity_id = config.heater_entity_id
        self._cooler_entity_id = config.cooler_entity_id
        self._demand_switch_entity_id = config.demand_switch_entity_id
        self._heater_polarity_invert = config.invert_heater
        self._sensor_entity_id = config.sensor_entity_id
        self._ext_sensor_entity_id = config.ext_sensor_entity_id
        self._weather_entity_id = config.weather_entity_id
        self._wind_speed_sensor_entity_id = config.wind_speed_sensor_entity_id
        if self._unique_id == "none":
            self._unique_id = slugify(f"{DOMAIN}_{self._name}_{self._heater_entity_id}")
        self._ac_mode = config.ac_mode
        self._force_off_state = config.force_off_state
        self._control_interval = config.control_interval
        self._sampling_period = config.sampling_period.seconds
        self._sensor_stall = config.sensor_stall.seconds
        self._output_safety = config.output_safety
        self._hvac_mode = config.initial_hvac_mode
        self._saved_target_temp = config.target_temp or config.away_temp
        self._temp_precision = config.precision
        self._target_temperature_step = config.target_temp_step
        self._last_heat_cycle_time = None  # None means use device's last_changed time
        self._min_open_time_pid_on = config.min_open_time
        self._min_closed_time_pid_on = config.min_closed_time
        self._min_open_time_pid_off = config.min_open_time_pid_off
        self._min_closed_time_pid_off = config.min_closed_time_pid_off
        if self._min_closed_time_pid_on is None:
            self._min_closed_time_pid_on = self._min_open_time_pid_on
        if self._min_open_time_pid_off is None:
            self._min_open_time_pid_off = self._min_open_time_pid_on
        if self._min_closed_time_pid_off is None:
            self._min_closed_time_pid_off = self._min_open_time_pid_off
        self._active = False
        self._trigger_source = None
        self._current_temp = None
        self._cur_temp_time = None
        self._previous_temp = None
        self._previous_temp_time = None
        self._ext_temp = None
        self._wind_speed = None
        self._temp_lock = asyncio.Lock()
        self._min_temp = config.min_temp
        self._max_temp = config.max_temp
        self._target_temp = config.target_temp
        self._unit = config.unit
        self._support_flags = ClimateEntityFeature.TARGET_TEMPERATURE
        self._support_flags |= ClimateEntityFeature.TURN_OFF
        self._support_flags |= ClimateEntityFeature.TURN_ON
        self._enable_turn_on_off_backwards_compatibility = False  # Remove after deprecation period
        self._attr_preset_mode = "none"
        self._away_temp = config.away_temp
        self._eco_temp = config.eco_temp
        self._boost_temp = config.boost_temp
        self._comfort_temp = config.comfort_temp
        self._home_temp = config.home_temp
        self._sleep_temp = config.sleep_temp
        self._activity_temp = config.activity_temp
        self._preset_sync_mode = config.preset_sync_mode
        if True in [
            temp is not None
            for temp in [
                self._away_temp,
                self._eco_temp,
                self._boost_temp,
                self._comfort_temp,
                self._home_temp,
                self._sleep_temp,
                self._activity_temp,
            ]
        ]:
            self._support_flags |= ClimateEntityFeature.PRESET_MODE

        self._output_precision = config.output_precision
        self._output_min = config.output_min
        self._output_max = config.output_max
        # Clamp defaults are already resolved in AdaptiveThermostatConfig
        self._output_clamp_low = config.output_clamp_low
        self._output_clamp_high = config.output_clamp_high
        self._difference = self._output_max - self._output_min
        if self._ac_mode:
            self._attr_hvac_modes = [HVACMode.COOL, HVACMode.HEAT, HVACMode.OFF]
            self._min_out = -self._output_clamp_high
            self._max_out = -self._output_clamp_low
        else:
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
            self._min_out = self._output_clamp_low
            self._max_out = self._output_clamp_high
        # Adjust limits based on initial_hvac_mode (not just ac_mode).
        # An AC-capable zone starting in HEAT mode must get heating limits [0, 100],
        # not the cooling defaults [-100, 0] set by the ac_mode block above.
        if self._hvac_mode == HVACMode.HEAT:
            self._min_out = self._output_clamp_low
            self._max_out = self._output_clamp_high
        elif self._hvac_mode == HVACMode.COOL:
            self._min_out = -self._output_clamp_high
            self._max_out = -self._output_clamp_low
        # Zone properties for physics-based initialization
        self._zone_id = config.zone_id
        self._heating_type = config.heating_type or const.HeatingType.FLOOR_HYDRONIC
        self._area_m2 = config.area_m2
        self._max_power_w = config.max_power_w
        self._supply_temperature = config.supply_temperature
        self._ceiling_height = config.ceiling_height
        self._window_area_m2 = config.window_area_m2
        self._window_rating = config.window_rating
        self._window_orientation = config.window_orientation
        self._floor_construction = config.floor_construction
        self._ha_area = config.ha_area  # Home Assistant area to assign entity to

        # Setpoint boost configuration
        self._setpoint_boost = config.setpoint_boost
        self._setpoint_boost_factor = config.setpoint_boost_factor
        self._setpoint_debounce = config.setpoint_debounce

        # Derivative filter alpha - get from config or use heating-type-specific default
        self._derivative_filter_alpha = config.derivative_filter_alpha
        if self._derivative_filter_alpha is None:
            # Use heating-type-specific default from HEATING_TYPE_CHARACTERISTICS
            heating_chars = const.HEATING_TYPE_CHARACTERISTICS.get(self._heating_type, {})
            self._derivative_filter_alpha = heating_chars.get("derivative_filter_alpha", 0.15)

        # Auto-apply PID mode (automatic application of adaptive PID recommendations)
        self._auto_apply_pid = config.auto_apply_pid

        # Night setback
        self._night_setback = None
        self._night_setback_config = None
        self._night_setback_was_active = None  # Track previous state for transition detection
        self._learning_grace_until = None  # Pause learning until this time after transitions
        night_setback_config = config.night_setback_config
        _LOGGER.debug("%s: night_setback_config from config: %s", self._name, night_setback_config)
        if night_setback_config:
            start = night_setback_config.get(const.CONF_NIGHT_SETBACK_START)
            end = night_setback_config.get(const.CONF_NIGHT_SETBACK_END)
            _LOGGER.info("%s: Night setback configured: start=%s, end=%s", self._name, start, end)
            if start:
                # Store config for dynamic end time calculation
                self._night_setback_config = {
                    "start": start,
                    "end": end,  # May be None - will use dynamic calculation
                    "delta": night_setback_config.get(
                        const.CONF_NIGHT_SETBACK_DELTA, const.DEFAULT_NIGHT_SETBACK_DELTA
                    ),
                    "recovery_deadline": night_setback_config.get(const.CONF_NIGHT_SETBACK_RECOVERY_DEADLINE),
                    "min_effective_elevation": night_setback_config.get(
                        const.CONF_MIN_EFFECTIVE_ELEVATION, const.DEFAULT_MIN_EFFECTIVE_ELEVATION
                    ),
                    "preheat_enabled": night_setback_config.get(const.CONF_PREHEAT_ENABLED),
                    "max_preheat_hours": night_setback_config.get(const.CONF_MAX_PREHEAT_HOURS),
                }
                # Only create NightSetback if end is explicitly configured
                if end:
                    self._night_setback = NightSetback(
                        start_time=start,
                        end_time=end,
                        setback_delta=self._night_setback_config["delta"],
                        recovery_deadline=self._night_setback_config["recovery_deadline"],
                    )

        # Contact sensors (window/door open detection)
        self._contact_sensor_handler = None
        if config.contact_sensors:
            contact_action = config.contact_action
            contact_delay = config.contact_delay  # Already int (seconds) from schema
            action_map = {
                "pause": ContactAction.PAUSE,
                "frost_protection": ContactAction.FROST_PROTECTION,
                "clamp": ContactAction.CLAMP,
                "none": ContactAction.NONE,
            }
            action_enum = action_map.get(contact_action, ContactAction.NONE)
            self._contact_sensor_handler = ContactSensorHandler(
                contact_sensors=config.contact_sensors,
                contact_delay_seconds=contact_delay,
                action=action_enum,
            )
            _LOGGER.info(
                "%s: Contact sensors configured: %s (action=%s, delay=%ds)",
                self._name,
                config.contact_sensors,
                contact_action,
                contact_delay,
            )

        # Humidity detector (shower/bathroom humidity spike detection)
        self._humidity_detector = None
        self._humidity_sensor_entity_id = None
        if config.humidity_sensor:
            self._humidity_sensor_entity_id = config.humidity_sensor
            self._humidity_detector = HumidityDetector(
                spike_threshold=config.humidity_spike_threshold,
                absolute_max=config.humidity_absolute_max,
                detection_window=config.humidity_detection_window,
                stabilization_delay=config.humidity_stabilization_delay,
                max_pause_duration=config.humidity_max_pause_duration,
                exit_humidity_threshold=config.humidity_exit_threshold,
                exit_humidity_drop=config.humidity_exit_drop,
            )
            _LOGGER.info(
                "%s: Humidity detection configured: sensor=%s (spike_threshold=%.1f%%, absolute_max=%.1f%%)",
                self._name,
                config.humidity_sensor,
                config.humidity_spike_threshold,
                config.humidity_absolute_max,
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

        # Tracks whether climate was paused due to contact sensor on last control loop
        # iteration. Used to detect the not-paused → paused transition so the duty
        # accumulator is reset exactly once when the pause begins (after contact_delay).
        self._contact_was_paused: bool = False

        # Weekly pause counters (for reporting)
        self._humidity_pause_count: int = 0
        self._contact_pause_count: int = 0

        # Control output manager (initialized in async_added_to_hass when hass is available)
        self._control_output_manager: ControlOutputManager | None = None

        # Setpoint boost manager (initialized in async_added_to_hass when hass is available)
        self._setpoint_boost_manager: SetpointBoostManager | None = None

        # PID gains manager (initialized in async_added_to_hass when hass is available)
        self._gains_manager = None

        # Heater control failure tracking (managed by HeaterController when available)
        self._heater_control_failed = False
        self._last_heater_error: str | None = None

        # Transport delay from manifold in minutes (set when heating starts).
        # C02: field name carries the unit to prevent seconds/minutes confusion.
        self._transport_delay_minutes: float | None = None

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
            kp, ki, kd = calculate_initial_pid(
                self._thermal_time_constant,
                self._heating_type,
                self._area_m2,
                self._max_power_w,
                self._supply_temperature,
            )

            # Log power and supply temp scaling info if configured
            power_info = f", power={self._max_power_w}W" if self._max_power_w else ""
            supply_info = f", supply={self._supply_temperature}°C" if self._supply_temperature else ""
            _LOGGER.info(
                "%s: Physics-based PID init (tau=%.2f, type=%s, window=%s%s%s): Kp=%.4f, Ki=%.5f, Kd=%.3f",
                self.unique_id,
                self._thermal_time_constant,
                self._heating_type,
                self._window_rating,
                power_info,
                supply_info,
                kp,
                ki,
                kd,
            )
        else:
            # Fallback defaults if no zone properties
            self._thermal_time_constant = None

            # Use fallback defaults
            kp = 0.5
            ki = 0.01
            kd = 5.0
            _LOGGER.warning("%s: No area_m2 configured, using default PID values", self.unique_id)

        # Initialize KeLearner (will be configured properly in async_added_to_hass)
        self._ke_learner: KeLearner | None = None

        # Initialize PreheatLearner (will be configured properly in async_added_to_hass)
        self._preheat_learner: PreheatLearner | None = None
        self._preheat_cycle_unsub = None  # H7 fix - store unsub handle
        self._heating_rate_cycle_unsub = None  # Heating rate session lifecycle unsub handle

        self._pwm = config.pwm.seconds
        self._valve_actuation_time = config.valve_actuation_time
        self._p = self._i = self._d = self._e = self._dt = 0
        self._control_output = self._output_min
        self._force_on = False
        self._force_off = False
        self._boost_pid_off = config.boost_pid_off

        # Get tolerances from HEATING_TYPE_CHARACTERISTICS based on heating_type
        # User-configured values are overridden by heating type defaults for consistency
        heating_type_chars = const.HEATING_TYPE_CHARACTERISTICS.get(
            self._heating_type, const.HEATING_TYPE_CHARACTERISTICS[const.HeatingType.RADIATOR]
        )
        self._cold_tolerance = heating_type_chars["cold_tolerance"]
        self._hot_tolerance = heating_type_chars["hot_tolerance"]

        self._time_changed = time.monotonic()
        self._last_sensor_update = time.monotonic()
        self._last_ext_sensor_update = time.monotonic()
        self._last_control_time = time.monotonic()
        _LOGGER.info(
            "%s: Active PID values - Kp=%.4f, Ki=%.5f, Kd=%.3f, Ke=%s, D_filter_alpha=%.2f",
            self.unique_id,
            kp,
            ki,
            kd,
            const.DEFAULT_KE,
            self._derivative_filter_alpha,
        )
        decay_rate = const.HEATING_TYPE_INTEGRAL_DECAY.get(self._heating_type, const.DEFAULT_INTEGRAL_DECAY)
        exp_decay_tau = const.HEATING_TYPE_EXP_DECAY_TAU.get(self._heating_type, const.DEFAULT_EXP_DECAY_TAU)
        self._pid_controller = pid_controller.PID(
            kp,
            ki,
            kd,
            const.DEFAULT_KE,
            out_min=self._min_out,
            out_max=self._max_out,
            sampling_period=self._sampling_period,
            cold_tolerance=self._cold_tolerance,
            hot_tolerance=self._hot_tolerance,
            derivative_filter_alpha=self._derivative_filter_alpha,
            integral_decay_multiplier=decay_rate,
            integral_exp_decay_tau=exp_decay_tau,
            heating_type=self._heating_type,
        )
        self._pid_controller.mode = "AUTO"

        # Initialize PID gains manager immediately after PID controller
        # This replaces the staging dict pattern with direct manager creation
        initial_heating_gains = PIDGains(kp=kp, ki=ki, kd=kd, ke=const.DEFAULT_KE)
        self._gains_manager = PIDGainsManager(
            pid_controller=self._pid_controller,
            initial_heating_gains=initial_heating_gains,
            get_hvac_mode=lambda: self._hvac_mode,
        )

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

        # Add climate entity reference to zone_data for reporting access
        coordinator = self._coordinator
        if coordinator and self._zone_id:
            zone_data = coordinator.get_zone_data(self._zone_id)
            if zone_data:
                zone_data["climate_entity"] = self

                # Create LearningMilestoneTracker for this zone
                notification_manager = getattr(coordinator, "notification_manager", None)
                if notification_manager:
                    from .managers.learning_milestone import LearningMilestoneTracker

                    milestone_tracker = LearningMilestoneTracker(
                        zone_id=self._zone_id,
                        zone_name=str(self._name or self._zone_id),
                        notification_manager=notification_manager,
                    )
                    zone_data["milestone_tracker"] = milestone_tracker
                    _LOGGER.info("%s: Learning milestone tracker initialized", self.entity_id)

        # Set physics baseline for adaptive learning after PID values are finalized
        # (either restored from previous state or calculated from physics in __init__)
        if coordinator and self._zone_id and self._area_m2:
            zone_data = coordinator.get_zone_data(self._zone_id)
            if zone_data:
                adaptive_learner = zone_data.get("adaptive_learner")
                if adaptive_learner:
                    adaptive_learner.set_physics_baseline(self._kp, self._ki, self._kd)
                    _LOGGER.info(
                        "%s: Set physics baseline for adaptive learning (Kp=%.4f, Ki=%.5f, Kd=%.3f)",
                        self.entity_id,
                        self._kp,
                        self._ki,
                        self._kd,
                    )
                    # Sync auto_apply_count from AdaptiveLearner to PID controller
                    # This ensures PID controller knows if system has been auto-tuned (for safety net control)
                    self._pid_controller.set_auto_apply_count(adaptive_learner._auto_apply_count)
                    _LOGGER.debug(
                        "%s: Synced auto_apply_count=%d to PID controller",
                        self.entity_id,
                        adaptive_learner._auto_apply_count,
                    )
                    # Wire PIDGainsManager so seasonal-limit gate uses real pid_history (C02)
                    adaptive_learner.set_pid_gains_manager(self._gains_manager)

        # Register manifold configuration with coordinator
        if coordinator and self._zone_id:
            manifold_registry = self.hass.data.get(DOMAIN, {}).get("manifold_registry")
            if manifold_registry:
                # Set manifold registry in coordinator if not already set
                if not coordinator.has_manifold_registry():
                    coordinator.set_manifold_registry(manifold_registry)
                _LOGGER.info("%s: Registered with manifold registry", self.entity_id)

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

        label_name = "Adaptive Climate"
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

        if self._heating_rate_cycle_unsub:
            self._heating_rate_cycle_unsub()
            self._heating_rate_cycle_unsub = None

        # Clean up cycle tracker subscriptions and timers
        if self._cycle_tracker:
            self._cycle_tracker.cleanup()

        # Clean up setpoint boost manager timers
        if hasattr(self, "_setpoint_boost_manager") and self._setpoint_boost_manager:
            self._setpoint_boost_manager.cancel()

        # Cancel any pending heater controller timers (demand-zero debounce, low-output, valve timers)
        if self._heater_controller is not None:
            self._heater_controller.cancel_pending_timers()

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
                    "%s: Saved learning data for zone %s on removal (adaptive=%s, ke=%s)",
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
            async_track_state_change_event(self.hass, self._sensor_entity_id, self._async_sensor_changed)
        )

        # External temperature sensor listener
        if self._ext_sensor_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(self.hass, self._ext_sensor_entity_id, self._async_ext_sensor_changed)
            )
        elif self._weather_entity_id is not None:
            # Use weather entity temperature as fallback when no outdoor sensor
            _LOGGER.info(
                "%s: Using weather entity %s temperature as outdoor temperature fallback",
                self.entity_id,
                self._weather_entity_id,
            )
            self.async_on_remove(
                async_track_state_change_event(self.hass, self._weather_entity_id, self._async_weather_entity_changed)
            )

        # Wind speed sensor listener
        if self._wind_speed_sensor_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._wind_speed_sensor_entity_id, self._async_wind_speed_sensor_changed
                )
            )
        elif self._weather_entity_id is not None:
            _LOGGER.info("%s: Using weather entity %s wind_speed as fallback", self.entity_id, self._weather_entity_id)
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._weather_entity_id, self._async_weather_entity_wind_changed
                )
            )

        # Heater entity listener
        if self._heater_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(self.hass, self._heater_entity_id, self._async_switch_changed)
            )

        # Cooler entity listener
        if self._cooler_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(self.hass, self._cooler_entity_id, self._async_switch_changed)
            )

        # Demand switch entity listener
        if self._demand_switch_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(self.hass, self._demand_switch_entity_id, self._async_switch_changed)
            )

        # Contact sensor listeners (window/door open detection)
        if self._contact_sensor_handler:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._contact_sensor_handler.contact_sensors, self._async_contact_sensor_changed
                )
            )
            # Initialize contact sensor states on startup
            self._update_contact_sensor_states()

        # Humidity sensor listener (shower/bathroom humidity spike detection)
        if self._humidity_detector and self._humidity_sensor_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._humidity_sensor_entity_id], self._async_humidity_sensor_changed
                )
            )

        # Thermal groups leader tracking (follower zones track leader setpoint)
        coordinator = self._coordinator
        if self._zone_id and coordinator:
            thermal_group_manager = coordinator.thermal_group_manager
            if thermal_group_manager:
                leader_zone_id = thermal_group_manager.get_leader_zone(self._zone_id)
                if leader_zone_id:
                    # This is a follower zone - track leader's state
                    leader_entity_id = f"climate.{leader_zone_id}"
                    _LOGGER.info("%s: Follower zone tracking leader %s", self.entity_id, leader_entity_id)
                    self.async_on_remove(
                        async_track_state_change_event(self.hass, leader_entity_id, self._async_leader_changed)
                    )

        # Control loop interval timer
        # Derive interval: explicit control_interval > sampling_period > default 60s
        if self._control_interval:
            control_interval = self._control_interval
        elif self._sampling_period > 0:
            control_interval = timedelta(seconds=self._sampling_period)
        else:
            control_interval = timedelta(seconds=const.DEFAULT_CONTROL_INTERVAL)
        self.async_on_remove(async_track_time_interval(self.hass, self._async_control_heating, control_interval))

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
            self.async_on_remove(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup))

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
    def water_temp_learning_gate_active(self) -> bool:
        """Return True while water temperature changes suppress this zone's learning.

        Water-temp changes move the plant gain under the adaptive learner
        (zone gain ~ T_room - T_water); a multi-day ramp looks exactly like the
        UndershootDetector failure signature.
        """
        coordinator = self._coordinator
        if coordinator is None:
            return False
        try:
            return bool(coordinator.water_temp_learning_gate(self._hvac_mode))
        except (TypeError, AttributeError):
            return False

    @property
    def in_learning_grace_period(self) -> bool:
        """Check if learning should be paused.

        True after a recent night setback transition, or while the water
        temperature controller is ramping / settling after a large write.
        """
        if self.water_temp_learning_gate_active:
            return True
        if self._night_setback_controller:
            return self._night_setback_controller.in_learning_grace_period
        # No night setback controller and no water-temp gate means no grace period
        return False

    def _set_learning_grace_period(self, minutes: int = 60):
        """Set a grace period to pause learning after night setback transitions."""
        if self._night_setback_controller:
            self._night_setback_controller.set_learning_grace_period(minutes)

    def _calculate_night_setback_adjustment(self, current_time=None):
        """Calculate effective target temperature with night setback, contact sensor, and cooling clamp.

        Delegates to NightSetbackManager for night setback logic, then applies
        contact sensor adjustments (frost_protection or clamp), then applies
        cooling supply temperature clamp when in COOL mode.

        Args:
            current_time: Optional datetime for testing; defaults to dt_util.utcnow()

        Returns:
            A tuple of (effective_target, in_night_period, night_setback_info) where:
            - effective_target: The adjusted target temperature
            - in_night_period: Whether we are currently in the night setback period
            - night_setback_info: Dict with additional info for state attributes
        """
        # Get night setback adjustment
        if self._night_setback_controller:
            effective_target, in_night, info = self._night_setback_controller.calculate_night_setback_adjustment(
                current_time
            )
        else:
            # Fallback when controller not yet initialized
            effective_target = self._target_temp
            in_night = False
            info = {"night_setback_active": False}

        # Apply contact sensor setpoint adjustment (FROST_PROTECTION or CLAMP)
        if self._contact_sensor_handler and self._contact_sensor_handler.should_take_action():
            hvac_mode_str = self._hvac_mode.value if self._hvac_mode else None
            action = self._contact_sensor_handler.get_action(hvac_mode_str)
            if action in (ContactAction.FROST_PROTECTION, ContactAction.CLAMP):
                adjusted = self._contact_sensor_handler.get_adjusted_setpoint(effective_target, hvac_mode=hvac_mode_str)
                if adjusted is not None:
                    info["contact_setpoint_adjustment"] = {
                        "action": action.value,
                        "original_target": effective_target,
                        "effective_target": adjusted,
                    }
                    effective_target = adjusted

        # Apply cooling supply temperature clamp when in COOL mode
        if self._hvac_mode == HVACMode.COOL:
            coordinator = self._coordinator
            if coordinator and coordinator.min_cooling_target is not None:
                min_target = coordinator.min_cooling_target
                if effective_target < min_target:
                    info["cooling_supply_clamp"] = {
                        "original_target": effective_target,
                        "effective_target": min_target,
                        "supply_temp": coordinator.effective_cooling_supply_temp,
                        "margin": coordinator.cooling_supply_margin,
                    }
                    effective_target = min_target

        return effective_target, in_night, info

    @property
    def _min_open_time(self):
        if self.pid_mode == "off":
            return self._min_open_time_pid_off
        return self._min_open_time_pid_on

    @property
    def _min_closed_time(self):
        if self.pid_mode == "off":
            return self._min_closed_time_pid_off
        return self._min_closed_time_pid_on

    @property
    def _kp(self) -> float:
        """Get proportional gain from gains manager."""
        return self._gains_manager.get_gains().kp

    @property
    def _ki(self) -> float:
        """Get integral gain from gains manager."""
        return self._gains_manager.get_gains().ki

    @property
    def _kd(self) -> float:
        """Get derivative gain from gains manager."""
        return self._gains_manager.get_gains().kd

    @property
    def _ke(self) -> float:
        """Get external temperature compensation gain from gains manager."""
        return self._gains_manager.get_gains().ke

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
    def heating_type(self) -> const.HeatingType:
        """Return the heating system type."""
        return self._heating_type

    @property
    def pid_mode(self):
        """Return the PID operating mode."""
        if getattr(self, "_pid_controller", None) is not None:
            return self._pid_controller.mode.lower()
        return "off"

    @property
    def pid_control_output(self):
        """Return the pid control output of the thermostat."""
        return self._control_output

    @property
    def extra_state_attributes(self):
        """Return extra state attributes to include in entity."""
        return build_state_attributes(self)

    def set_hvac_mode(self, hvac_mode: HVACMode | str) -> None:
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
            if self._pid_controller is not None:  # pyright: ignore[reportUnnecessaryComparison]
                self._pid_controller.clear_samples()
        if self._pid_controller:
            self._pid_controller.out_max = self._max_out
            self._pid_controller.out_min = self._min_out

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        old_mode = self._hvac_mode

        # C01: Commit ALL mode mutations under lock BEFORE any I/O awaits to prevent the
        # control-loop re-entry race. Without the lock, a control-loop tick can fire during
        # _async_heater_turn_off, observe the old mode, and turn the heater back on.
        async with self._temp_lock:
            # Reset integral when switching between HEAT and COOL modes.
            # The integral accumulated in one mode is meaningless in the other.
            if self._pid_controller is not None:
                switching_heat_cool = (old_mode == HVACMode.HEAT and hvac_mode == HVACMode.COOL) or (
                    old_mode == HVACMode.COOL and hvac_mode == HVACMode.HEAT
                )
                if switching_heat_cool:
                    _LOGGER.info(
                        "%s: Resetting integral on HEAT<->COOL switch (was %.2f)",
                        self.entity_id,
                        self._pid_controller.integral,
                    )
                    if self._gains_manager is not None:
                        self._gains_manager.set_integral(0.0, PIDChangeReason.MODE_SWITCH)
                    else:
                        self._pid_controller.integral = 0.0
                    self._i = 0.0

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
                # Clear the samples to avoid integrating the off period
                self._previous_temp = None
                self._previous_temp_time = None
                if self._pid_controller is not None:  # pyright: ignore[reportUnnecessaryComparison]
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

        # --- Lock released: _hvac_mode is now committed ---
        # I/O runs below. ModeChangedEvent is in the finally block to survive exceptions (M15).
        # The initial turn-off passes old_mode so the correct device (heater vs cooler) is stopped.
        try:
            await self._async_heater_turn_off(force=True, _effective_mode=old_mode)
            if hvac_mode == HVACMode.OFF:
                if self._pwm:
                    _LOGGER.debug("%s: Turn OFF heater from async_set_hvac_mode(%s)", self.entity_id, hvac_mode)
                    await self._async_heater_turn_off(force=True)
                else:
                    _LOGGER.debug(
                        "%s: Set heater to %s from async_set_hvac_mode(%s)",
                        self.entity_id,
                        self._control_output,
                        hvac_mode,
                    )
                    await self._async_set_valve_value(float(self._control_output or 0))
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
        finally:
            # M15: Emit ModeChangedEvent unconditionally so subscribers never miss a transition.
            if old_mode != self._hvac_mode:
                old_mode_str = old_mode.value if old_mode else "off"
                new_mode_str = self._hvac_mode.value if self._hvac_mode else "off"
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
