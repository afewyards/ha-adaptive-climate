"""Adds support for smart (PID) thermostat units.
For more details about this platform, please refer to the documentation at
https://github.com/ScratMan/HASmartThermostat"""

import asyncio
import logging
import time
from abc import ABC

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import condition, entity_platform, discovery
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    CONF_UNIQUE_ID,
    EVENT_HOMEASSISTANT_START,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.components.number.const import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
    DOMAIN as NUMBER_DOMAIN
)
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.light import (DOMAIN as LIGHT_DOMAIN, SERVICE_TURN_ON as SERVICE_TURN_LIGHT_ON,
                                            ATTR_BRIGHTNESS_PCT)
from homeassistant.components.valve import (DOMAIN as VALVE_DOMAIN, SERVICE_SET_VALVE_POSITION, ATTR_POSITION)
from homeassistant.core import DOMAIN as HA_DOMAIN, CoreState, Event, EventStateChangedData, callback
from homeassistant.util import slugify
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound

from .adaptive.physics import calculate_thermal_time_constant, calculate_initial_pid
from .adaptive.night_setback import NightSetback
from .adaptive.solar_recovery import SolarRecovery

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    HVACMode,
    HVACAction,
    PRESET_AWAY,
    PRESET_NONE,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_HOME,
    PRESET_SLEEP,
    PRESET_ACTIVITY,
)

from . import DOMAIN, PLATFORMS
from . import const
from . import pid_controller
from .adaptive.learning import AdaptiveLearner, ThermalRateLearner

_LOGGER = logging.getLogger(__name__)


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(const.CONF_HEATER): cv.entity_ids,
        vol.Optional(const.CONF_COOLER): cv.entity_ids,
        vol.Optional(const.CONF_DEMAND_SWITCH): cv.entity_ids,
        vol.Required(const.CONF_INVERT_HEATER, default=False): cv.boolean,
        vol.Required(const.CONF_SENSOR): cv.entity_id,
        vol.Optional(const.CONF_OUTDOOR_SENSOR): cv.entity_id,
        vol.Optional(const.CONF_FORCE_OFF_STATE, default=True): cv.boolean,
        vol.Optional(const.CONF_MAX_TEMP): vol.Coerce(float),
        vol.Optional(const.CONF_MIN_TEMP): vol.Coerce(float),
        vol.Optional(CONF_NAME, default=const.DEFAULT_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID, default='none'): cv.string,
        vol.Optional(const.CONF_TARGET_TEMP): vol.Coerce(float),
        vol.Optional(const.CONF_HOT_TOLERANCE, default=const.DEFAULT_TOLERANCE): vol.Coerce(float),
        vol.Optional(const.CONF_COLD_TOLERANCE, default=const.DEFAULT_TOLERANCE): vol.Coerce(
            float),
        vol.Optional(const.CONF_MIN_CYCLE_DURATION, default=const.DEFAULT_MIN_CYCLE_DURATION):
            vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_MIN_OFF_CYCLE_DURATION): vol.All(
            cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_MIN_CYCLE_DURATION_PID_OFF): vol.All(
            cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_MIN_OFF_CYCLE_DURATION_PID_OFF): vol.All(
            cv.time_period, cv.positive_timedelta),
        vol.Required(const.CONF_KEEP_ALIVE): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_SAMPLING_PERIOD, default=const.DEFAULT_SAMPLING_PERIOD): vol.All(
            cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_SENSOR_STALL, default=const.DEFAULT_SENSOR_STALL): vol.All(
            cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_OUTPUT_SAFETY, default=const.DEFAULT_OUTPUT_SAFETY): vol.Coerce(
            float),
        vol.Optional(const.CONF_INITIAL_HVAC_MODE): vol.In(
            [HVACMode.COOL, HVACMode.HEAT, HVACMode.OFF]
        ),
        vol.Optional(const.CONF_PRECISION): vol.In(
            [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
        ),
        vol.Optional(const.CONF_TARGET_TEMP_STEP): vol.In(
            [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
        ),
        vol.Optional(const.CONF_OUTPUT_PRECISION, default=const.DEFAULT_OUTPUT_PRECISION): vol.Coerce(int),
        vol.Optional(const.CONF_OUTPUT_MIN, default=const.DEFAULT_OUTPUT_MIN): vol.Coerce(float),
        vol.Optional(const.CONF_OUTPUT_MAX, default=const.DEFAULT_OUTPUT_MAX): vol.Coerce(float),
        vol.Optional(const.CONF_OUT_CLAMP_LOW, default=const.DEFAULT_OUT_CLAMP_LOW): vol.Coerce(float),
        vol.Optional(const.CONF_OUT_CLAMP_HIGH, default=const.DEFAULT_OUT_CLAMP_HIGH): vol.Coerce(float),
        vol.Optional(const.CONF_PWM, default=const.DEFAULT_PWM): vol.All(
            cv.time_period, cv.positive_timedelta
        ),
        # Adaptive learning options
        vol.Optional(const.CONF_HEATING_TYPE): vol.In(const.VALID_HEATING_TYPES),
        vol.Optional(const.CONF_AREA_M2): vol.Coerce(float),
        vol.Optional(const.CONF_CEILING_HEIGHT, default=const.DEFAULT_CEILING_HEIGHT): vol.Coerce(float),
        vol.Optional(const.CONF_WINDOW_AREA_M2): vol.Coerce(float),
        vol.Optional(const.CONF_WINDOW_ORIENTATION): vol.In(const.VALID_WINDOW_ORIENTATIONS),
        vol.Optional(const.CONF_WINDOW_RATING): cv.string,
        # Zone linking
        vol.Optional(const.CONF_LINKED_ZONES): cv.entity_ids,
        vol.Optional(const.CONF_LINK_DELAY_MINUTES, default=const.DEFAULT_LINK_DELAY_MINUTES): vol.Coerce(int),
        # Contact sensors
        vol.Optional(const.CONF_CONTACT_SENSORS): cv.entity_ids,
        vol.Optional(const.CONF_CONTACT_ACTION, default=const.CONTACT_ACTION_PAUSE): vol.In(const.VALID_CONTACT_ACTIONS),
        vol.Optional(const.CONF_CONTACT_DELAY, default=const.DEFAULT_CONTACT_DELAY): vol.Coerce(int),
        # Health monitoring
        vol.Optional(const.CONF_HIGH_POWER_EXCEPTION, default=const.DEFAULT_HIGH_POWER_EXCEPTION): cv.boolean,
        # Night setback
        vol.Optional(const.CONF_NIGHT_SETBACK): vol.Schema({
            vol.Optional(const.CONF_NIGHT_SETBACK_START): cv.string,
            vol.Optional(const.CONF_NIGHT_SETBACK_END): cv.string,
            vol.Optional(const.CONF_NIGHT_SETBACK_DELTA, default=const.DEFAULT_NIGHT_SETBACK_DELTA): vol.Coerce(float),
            vol.Optional(const.CONF_NIGHT_SETBACK_RECOVERY_DEADLINE): cv.string,
            vol.Optional(const.CONF_NIGHT_SETBACK_SOLAR_RECOVERY, default=False): cv.boolean,
        }),
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the generic thermostat platform."""
    platform = entity_platform.current_platform.get()
    assert platform

    # Get name and create zone_id
    name = config.get(CONF_NAME)
    zone_id = slugify(name)

    # Validate that at least one output entity is configured
    heater = config.get(const.CONF_HEATER)
    cooler = config.get(const.CONF_COOLER)
    demand_switch = config.get(const.CONF_DEMAND_SWITCH)
    if not heater and not cooler and not demand_switch:
        _LOGGER.error(
            "%s: At least one of heater, cooler, or demand_switch must be configured",
            name
        )
        return

    parameters = {
        'name': name,
        'unique_id': config.get(CONF_UNIQUE_ID),
        'heater_entity_id': config.get(const.CONF_HEATER),
        'cooler_entity_id': config.get(const.CONF_COOLER),
        'demand_switch_entity_id': config.get(const.CONF_DEMAND_SWITCH),
        'invert_heater': config.get(const.CONF_INVERT_HEATER),
        'sensor_entity_id': config.get(const.CONF_SENSOR),
        'ext_sensor_entity_id': config.get(const.CONF_OUTDOOR_SENSOR),
        'min_temp': config.get(const.CONF_MIN_TEMP),
        'max_temp': config.get(const.CONF_MAX_TEMP),
        'target_temp': config.get(const.CONF_TARGET_TEMP),
        'hot_tolerance': config.get(const.CONF_HOT_TOLERANCE),
        'cold_tolerance': config.get(const.CONF_COLD_TOLERANCE),
        # Derive ac_mode from cooler presence (zone or controller level)
        'ac_mode': bool(cooler) or bool(hass.data.get(DOMAIN, {}).get("main_cooler_switch")),
        'force_off_state': config.get(const.CONF_FORCE_OFF_STATE),
        'min_cycle_duration': config.get(const.CONF_MIN_CYCLE_DURATION),
        'min_off_cycle_duration': config.get(const.CONF_MIN_OFF_CYCLE_DURATION),
        'min_cycle_duration_pid_off': config.get(const.CONF_MIN_CYCLE_DURATION_PID_OFF),
        'min_off_cycle_duration_pid_off': config.get(const.CONF_MIN_OFF_CYCLE_DURATION_PID_OFF),
        'keep_alive': config.get(const.CONF_KEEP_ALIVE),
        'sampling_period': config.get(const.CONF_SAMPLING_PERIOD),
        'sensor_stall': config.get(const.CONF_SENSOR_STALL),
        'output_safety': config.get(const.CONF_OUTPUT_SAFETY),
        'initial_hvac_mode': config.get(const.CONF_INITIAL_HVAC_MODE),
        'preset_sync_mode': hass.data.get(DOMAIN, {}).get("preset_sync_mode"),
        'away_temp': hass.data.get(DOMAIN, {}).get("away_temp"),
        'eco_temp': hass.data.get(DOMAIN, {}).get("eco_temp"),
        'boost_temp': hass.data.get(DOMAIN, {}).get("boost_temp"),
        'comfort_temp': hass.data.get(DOMAIN, {}).get("comfort_temp"),
        'home_temp': hass.data.get(DOMAIN, {}).get("home_temp"),
        'activity_temp': hass.data.get(DOMAIN, {}).get("activity_temp"),
        'precision': config.get(const.CONF_PRECISION),
        'target_temp_step': config.get(const.CONF_TARGET_TEMP_STEP),
        'unit': hass.config.units.temperature_unit,
        'output_precision': config.get(const.CONF_OUTPUT_PRECISION),
        'output_min': config.get(const.CONF_OUTPUT_MIN),
        'output_max': config.get(const.CONF_OUTPUT_MAX),
        'output_clamp_low': config.get(const.CONF_OUT_CLAMP_LOW),
        'output_clamp_high': config.get(const.CONF_OUT_CLAMP_HIGH),
        'pwm': config.get(const.CONF_PWM),
        'boost_pid_off': hass.data.get(DOMAIN, {}).get("boost_pid_off"),
        # New adaptive learning parameters
        'zone_id': zone_id,
        'heating_type': config.get(const.CONF_HEATING_TYPE),
        'area_m2': config.get(const.CONF_AREA_M2),
        'ceiling_height': config.get(const.CONF_CEILING_HEIGHT),
        'window_area_m2': config.get(const.CONF_WINDOW_AREA_M2),
        'window_orientation': config.get(const.CONF_WINDOW_ORIENTATION),
        # Window rating: use zone-level config, fall back to controller default
        'window_rating': config.get(const.CONF_WINDOW_RATING) or hass.data.get(DOMAIN, {}).get("window_rating", const.DEFAULT_WINDOW_RATING),
        'linked_zones': config.get(const.CONF_LINKED_ZONES),
        'link_delay_minutes': config.get(const.CONF_LINK_DELAY_MINUTES),
        'contact_sensors': config.get(const.CONF_CONTACT_SENSORS),
        'contact_action': config.get(const.CONF_CONTACT_ACTION),
        'contact_delay': config.get(const.CONF_CONTACT_DELAY),
        'high_power_exception': config.get(const.CONF_HIGH_POWER_EXCEPTION),
        'night_setback_config': config.get(const.CONF_NIGHT_SETBACK),
    }

    smart_thermostat = SmartThermostat(**parameters)
    async_add_entities([smart_thermostat])

    # Register zone with coordinator
    coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
    if coordinator:
        zone_data = {
            "climate_entity_id": f"climate.{zone_id}",
            "zone_name": name,
            "area_m2": config.get(const.CONF_AREA_M2, 0),
            "heating_type": config.get(const.CONF_HEATING_TYPE),
            "learning_enabled": True,  # Always enabled, vacation mode can toggle
            "adaptive_learner": AdaptiveLearner(),
            "linked_zones": config.get(const.CONF_LINKED_ZONES, []),
            "high_power_exception": config.get(const.CONF_HIGH_POWER_EXCEPTION, False),
        }
        coordinator.register_zone(zone_id, zone_data)
        _LOGGER.info("Registered zone %s with coordinator", zone_id)

        # Trigger sensor platform discovery for this zone
        hass.async_create_task(
            discovery.async_load_platform(
                hass,
                "sensor",
                DOMAIN,
                {
                    "zone_id": zone_id,
                    "zone_name": name,
                    "climate_entity_id": f"climate.{zone_id}",
                },
                config,
            )
        )

    platform.async_register_entity_service(  # type: ignore
        "reset_pid_to_physics",
        {},
        "async_reset_pid_to_physics",
    )
    platform.async_register_entity_service(  # type: ignore
        "apply_adaptive_pid",
        {},
        "async_apply_adaptive_pid",
    )


class SmartThermostat(ClimateEntity, RestoreEntity, ABC):
    """Representation of a Smart Thermostat device."""

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
        if self._unique_id == 'none':
            self._unique_id = slugify(f"{DOMAIN}_{self._name}_{self._heater_entity_id}")
        self._ac_mode = kwargs.get('ac_mode', False)
        self._force_off_state = kwargs.get('force_off_state', True)
        self._keep_alive = kwargs.get('keep_alive')
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
        self._output_clamp_high = kwargs.get('output_clamp_high')
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
        self._heating_type = kwargs.get('heating_type', 'floor_hydronic')
        self._area_m2 = kwargs.get('area_m2')
        self._ceiling_height = kwargs.get('ceiling_height', 2.5)
        self._window_area_m2 = kwargs.get('window_area_m2')
        self._window_rating = kwargs.get('window_rating', 'hr++')
        self._window_orientation = kwargs.get('window_orientation')

        # Night setback
        self._night_setback = None
        self._night_setback_config = None
        self._solar_recovery = None
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
                    'solar_recovery': night_setback_config.get(const.CONF_NIGHT_SETBACK_SOLAR_RECOVERY, False),
                }
                # Only create NightSetback if end is explicitly configured
                if end:
                    self._night_setback = NightSetback(
                        start_time=start,
                        end_time=end,
                        setback_delta=self._night_setback_config['delta'],
                        recovery_deadline=self._night_setback_config['recovery_deadline'],
                    )
                    # Solar recovery (uses window_orientation from zone config)
                    if self._night_setback_config['solar_recovery'] and self._window_orientation:
                        self._solar_recovery = SolarRecovery(
                            window_orientation=self._window_orientation,
                            base_recovery_time=end,
                            recovery_deadline=self._night_setback_config['recovery_deadline'],
                        )

        # Zone linking for thermally connected zones
        self._linked_zones = kwargs.get('linked_zones', [])
        self._link_delay_minutes = kwargs.get('link_delay_minutes', 10)
        self._zone_linker = None  # Will be set in async_added_to_hass
        self._is_heating = False  # Track heating state for zone linking

        # Heater control failure tracking
        self._heater_control_failed = False
        self._last_heater_error: str | None = None

        # Calculate PID values from physics (adaptive learning will refine them)
        if self._area_m2:
            volume_m3 = self._area_m2 * self._ceiling_height
            tau = calculate_thermal_time_constant(
                volume_m3=volume_m3,
                window_area_m2=self._window_area_m2,
                floor_area_m2=self._area_m2,
                window_rating=self._window_rating,
            )
            self._kp, self._ki, self._kd = calculate_initial_pid(tau, self._heating_type)
            _LOGGER.info("%s: Physics-based PID init (tau=%.2f, type=%s, window=%s): Kp=%.4f, Ki=%.5f, Kd=%.3f",
                         self.unique_id, tau, self._heating_type, self._window_rating, self._kp, self._ki, self._kd)
        else:
            # Fallback defaults if no zone properties
            self._kp = 0.5
            self._ki = 0.01
            self._kd = 5.0
            _LOGGER.warning("%s: No area_m2 configured, using default PID values", self.unique_id)
        self._ke = const.DEFAULT_KE

        self._pwm = kwargs.get('pwm').seconds
        self._p = self._i = self._d = self._e = self._dt = 0
        self._control_output = self._output_min
        self._force_on = False
        self._force_off = False
        self._boost_pid_off = kwargs.get('boost_pid_off')
        self._cold_tolerance = abs(kwargs.get('cold_tolerance'))
        self._hot_tolerance = abs(kwargs.get('hot_tolerance'))
        self._time_changed = 0
        self._last_sensor_update = time.time()
        self._last_ext_sensor_update = time.time()
        _LOGGER.info("%s: Active PID values - Kp=%.4f, Ki=%.5f, Kd=%.3f, Ke=%s",
                     self.unique_id, self._kp, self._ki, self._kd, self._ke or 0)
        self._pid_controller = pid_controller.PID(self._kp, self._ki, self._kd, self._ke,
                                                  self._min_out, self._max_out,
                                                  self._sampling_period, self._cold_tolerance,
                                                  self._hot_tolerance)
        self._pid_controller.mode = "AUTO"

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Configure zone linking if linked zones are defined
        if self._linked_zones:
            zone_linker = self.hass.data.get(DOMAIN, {}).get("zone_linker")
            if zone_linker:
                self._zone_linker = zone_linker
                zone_linker.configure_linked_zones(self._unique_id, self._linked_zones)
                _LOGGER.info(
                    "%s: Zone linking configured with %s (delay=%d min)",
                    self.entity_id, self._linked_zones, self._link_delay_minutes
                )

        # Set up state change listeners
        self._setup_state_listeners()

        # Restore state from previous session
        old_state = await self.async_get_last_state()
        self._restore_state(old_state)

        # Restore PID values if we have old state
        if old_state is not None:
            self._restore_pid_values(old_state)

        # Set default state to off
        if not self._hvac_mode:
            self._hvac_mode = HVACMode.OFF
        await self._async_control_heating(calc_pid=True)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is being removed from Home Assistant.

        This method unregisters the zone from the coordinator to ensure
        clean removal and prevent stale zone data.
        """
        await super().async_will_remove_from_hass()

        # Unregister zone from coordinator
        if self._zone_id:
            coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
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

        # Keep-alive interval timer
        if self._keep_alive:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_control_heating,
                    self._keep_alive))

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

        if self.hass.state == CoreState.running:
            _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

    def _restore_state(self, old_state) -> None:
        """Restore climate entity state from Home Assistant's state restoration.

        This method restores:
        - Target temperature setpoint
        - Active preset mode
        - HVAC mode

        Note: Preset temperatures are not restored as they now come from controller config.

        Args:
            old_state: The restored state object from async_get_last_state(),
                      or None if no previous state exists.
        """
        if old_state is not None:
            # Restore target temperature
            if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                if self._target_temp is None:
                    if self._ac_mode:
                        self._target_temp = self.max_temp
                    else:
                        self._target_temp = self.min_temp
                _LOGGER.warning("%s: No setpoint available in old state, falling back to %s",
                                self.entity_id, self._target_temp)
            else:
                self._target_temp = float(old_state.attributes.get(ATTR_TEMPERATURE))

            # Restore preset mode
            if old_state.attributes.get(ATTR_PRESET_MODE) is not None:
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)

            # Restore HVAC mode
            if not self._hvac_mode and old_state.state:
                self.set_hvac_mode(old_state.state)
        else:
            # No previous state, set defaults
            if self._target_temp is None:
                if self._ac_mode:
                    self._target_temp = self.max_temp
                else:
                    self._target_temp = self.min_temp
            _LOGGER.warning("%s: No setpoint to restore, setting to %s", self.entity_id,
                            self._target_temp)

    def _restore_pid_values(self, old_state) -> None:
        """Restore PID controller values from Home Assistant's state restoration.

        This method restores:
        - PID integral value (pid_i)
        - PID gains: Kp, Ki, Kd, Ke (supports both lowercase and uppercase attribute names)
        - PID mode (auto/off)

        Args:
            old_state: The restored state object from async_get_last_state().
                      Must not be None.
        """
        if old_state is None or self._pid_controller is None:
            return

        # Restore PID integral value
        if isinstance(old_state.attributes.get('pid_i'), (float, int)):
            self._i = float(old_state.attributes.get('pid_i'))
            self._pid_controller.integral = self._i

        # Restore Kp (supports both 'kp' and 'Kp')
        if old_state.attributes.get('kp') is not None:
            self._kp = float(old_state.attributes.get('kp'))
            self._pid_controller.set_pid_param(kp=self._kp)
        elif old_state.attributes.get('Kp') is not None:
            self._kp = float(old_state.attributes.get('Kp'))
            self._pid_controller.set_pid_param(kp=self._kp)

        # Restore Ki (supports both 'ki' and 'Ki')
        if old_state.attributes.get('ki') is not None:
            self._ki = float(old_state.attributes.get('ki'))
            self._pid_controller.set_pid_param(ki=self._ki)
        elif old_state.attributes.get('Ki') is not None:
            self._ki = float(old_state.attributes.get('Ki'))
            self._pid_controller.set_pid_param(ki=self._ki)

        # Restore Kd (supports both 'kd' and 'Kd')
        if old_state.attributes.get('kd') is not None:
            self._kd = float(old_state.attributes.get('kd'))
            self._pid_controller.set_pid_param(kd=self._kd)
        elif old_state.attributes.get('Kd') is not None:
            self._kd = float(old_state.attributes.get('Kd'))
            self._pid_controller.set_pid_param(kd=self._kd)

        # Restore Ke (supports both 'ke' and 'Ke')
        if old_state.attributes.get('ke') is not None:
            self._ke = float(old_state.attributes.get('ke'))
            self._pid_controller.set_pid_param(ke=self._ke)
        elif old_state.attributes.get('Ke') is not None:
            self._ke = float(old_state.attributes.get('Ke'))
            self._pid_controller.set_pid_param(ke=self._ke)

        _LOGGER.info("%s: Restored PID values - Kp=%.4f, Ki=%.5f, Kd=%.3f, Ke=%s",
                     self.entity_id, self._kp, self._ki, self._kd, self._ke or 0)

        # Restore PID mode
        if old_state.attributes.get('pid_mode') is not None:
            self._pid_controller.mode = old_state.attributes.get('pid_mode')

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

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
        return self._attr_preset_mode

    @property
    def preset_modes(self):
        """Return a list of available preset modes."""
        preset_modes = [PRESET_NONE]
        for mode, preset_mode_temp in self._preset_modes_temp.items():
            if preset_mode_temp is not None:
                preset_modes.append(mode)
        return preset_modes

    @property
    def _preset_modes_temp(self):
        """Return a list of preset modes and their temperatures"""
        return {
            PRESET_AWAY: self._away_temp,
            PRESET_ECO: self._eco_temp,
            PRESET_BOOST: self._boost_temp,
            PRESET_COMFORT: self._comfort_temp,
            PRESET_HOME: self._home_temp,
            PRESET_SLEEP: self._sleep_temp,
            PRESET_ACTIVITY: self._activity_temp,
        }

    @property
    def _preset_temp_modes(self):
        """Return a list of preset temperature and their modes"""
        return {
            self._away_temp: PRESET_AWAY,
            self._eco_temp: PRESET_ECO,
            self._boost_temp: PRESET_BOOST,
            self._comfort_temp: PRESET_COMFORT,
            self._home_temp: PRESET_HOME,
            self._sleep_temp: PRESET_SLEEP,
            self._activity_temp: PRESET_ACTIVITY,
        }

    @property
    def presets(self):
        """Return a dict of available preset and temperatures."""
        presets = {}
        for mode, preset_mode_temp in self._preset_modes_temp.items():
            if preset_mode_temp is not None:
                presets.update({mode: preset_mode_temp})
        return presets

    def _get_sunset_time(self):
        """Get sunset time from Home Assistant sun component."""
        from datetime import datetime
        sun_state = self.hass.states.get("sun.sun")
        if sun_state and sun_state.attributes.get("next_setting"):
            try:
                return datetime.fromisoformat(
                    sun_state.attributes["next_setting"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                return None
        return None

    @property
    def in_learning_grace_period(self) -> bool:
        """Check if learning should be paused due to recent night setback transition."""
        if self._learning_grace_until is None:
            return False
        from datetime import datetime
        return datetime.now() < self._learning_grace_until

    def _set_learning_grace_period(self, minutes: int = 60):
        """Set a grace period to pause learning after night setback transitions."""
        from datetime import datetime, timedelta
        self._learning_grace_until = datetime.now() + timedelta(minutes=minutes)
        _LOGGER.info(
            "%s: Learning grace period set for %d minutes (until %s)",
            self.entity_id, minutes, self._learning_grace_until.strftime("%H:%M")
        )

    def _get_sunrise_time(self):
        """Get sunrise time from Home Assistant sun component."""
        from datetime import datetime
        sun_state = self.hass.states.get("sun.sun")
        if sun_state and sun_state.attributes.get("next_rising"):
            try:
                return datetime.fromisoformat(
                    sun_state.attributes["next_rising"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                return None
        return None

    def _get_weather_condition(self):
        """Get current weather condition from coordinator's weather entity."""
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator and hasattr(coordinator, '_weather_entity'):
            weather_state = self.hass.states.get(coordinator._weather_entity)
            if weather_state:
                return weather_state.state
        # Try common weather entity names as fallback
        for entity_id in ["weather.home", "weather.knmi_home", "weather.forecast_home"]:
            weather_state = self.hass.states.get(entity_id)
            if weather_state:
                return weather_state.state
        return None

    def _calculate_dynamic_night_end(self):
        """Calculate dynamic night setback end time based on sunrise, orientation, weather.

        Returns time object for when night setback should end, or None if cannot calculate.

        Logic:
        - Base: sunrise + 60 min (sun needs time to rise high enough to heat windows)
        - Orientation: adjusts based on when direct sun reaches windows
        - Weather: cloudy = need active heating sooner, clear = can wait for sun
        """
        from datetime import time, timedelta

        sunrise = self._get_sunrise_time()
        if not sunrise:
            return None

        # Base: sunrise + 60 min (sun needs to rise high enough to provide heat)
        end_time = sunrise + timedelta(minutes=60)

        # Orientation offsets - when does direct sun actually hit these windows?
        # South: sun hits when higher in sky, can rely on solar gain longer
        # East: gets early morning sun, moderate delay
        # West: no morning sun at all, need active heating
        # North: minimal direct sun ever, need active heating
        orientation_offsets = {
            "south": +30,   # Wait longer - sun will heat this room well once high enough
            "east": +15,    # Gets morning sun fairly soon
            "west": -30,    # No morning sun - start heating earlier
            "north": -45,   # No direct sun - need heating earliest
        }

        if self._window_orientation:
            offset = orientation_offsets.get(self._window_orientation.lower(), 0)
            end_time = end_time + timedelta(minutes=offset)

        # Weather adjustment
        weather = self._get_weather_condition()
        if weather:
            weather_lower = weather.lower().replace("-", "").replace("_", "")
            if any(c in weather_lower for c in ["cloud", "rain", "snow", "fog", "hail", "storm"]):
                # Cloudy: no solar gain expected - end setback earlier to allow heating
                end_time = end_time - timedelta(minutes=30)
            elif any(c in weather_lower for c in ["sunny", "clear"]):
                # Clear: good solar gain - can delay heating longer
                end_time = end_time + timedelta(minutes=15)

        return end_time.time()

    def _parse_night_start_time(self, start_str: str, current_time):
        """Parse night setback start time string.

        Args:
            start_str: Start time as "HH:MM" or "sunset" or "sunset+30"
            current_time: Current datetime for sunset lookup

        Returns:
            time object for the start time
        """
        from datetime import time as dt_time, timedelta

        if start_str.lower().startswith("sunset"):
            sunset = self._get_sunset_time()
            if sunset:
                offset = 0
                if "+" in start_str:
                    offset = int(start_str.split("+")[1])
                elif "-" in start_str:
                    offset = -int(start_str.split("-")[1])
                return (sunset + timedelta(minutes=offset)).time()
            else:
                return dt_time(21, 0)  # Fallback to 21:00
        else:
            hour, minute = map(int, start_str.split(":"))
            return dt_time(hour, minute)

    def _is_in_night_time_period(self, current_time_only, start_time, end_time) -> bool:
        """Check if current time is within night period, handling midnight crossing.

        Args:
            current_time_only: time object for current time
            start_time: time object for period start
            end_time: time object for period end

        Returns:
            True if in night period
        """
        if start_time > end_time:
            # Period crosses midnight (e.g., 22:00 to 06:00)
            return current_time_only >= start_time or current_time_only < end_time
        else:
            # Normal period (e.g., 00:00 to 06:00)
            return start_time <= current_time_only < end_time

    def _calculate_night_setback_adjustment(self, current_time=None):
        """Calculate night setback adjustment for effective target temperature.

        Handles both static end time (NightSetback object) and dynamic end time
        (sunrise/orientation/weather-based) configurations.

        Args:
            current_time: Optional datetime for testing; defaults to datetime.now()

        Returns:
            A tuple of (effective_target, in_night_period, night_setback_info) where:
            - effective_target: The adjusted target temperature
            - in_night_period: Whether we are currently in the night setback period
            - night_setback_info: Dict with additional info for state attributes
        """
        from datetime import datetime, time as dt_time

        if current_time is None:
            current_time = datetime.now()

        effective_target = self._target_temp
        in_night_period = False
        info = {}

        if self._night_setback:
            # Static end time mode - use NightSetback object
            sunset_time = self._get_sunset_time() if self._night_setback.use_sunset else None
            in_night_period = self._night_setback.is_night_period(current_time, sunset_time)

            info["night_setback_delta"] = self._night_setback.setback_delta
            info["night_setback_end"] = self._night_setback.end_time.strftime("%H:%M")
            info["night_setback_end_dynamic"] = False

            if self._solar_recovery:
                solar_recovery_active = self._solar_recovery.should_use_solar_recovery(
                    current_time, self._current_temp or 0, self._target_temp or 0
                )
                info["solar_recovery_active"] = solar_recovery_active
                info["solar_recovery_time"] = self._solar_recovery.adjusted_recovery_time.strftime("%H:%M")

                if in_night_period:
                    effective_target = self._target_temp - self._night_setback.setback_delta
                elif solar_recovery_active:
                    # Continue setback during solar recovery
                    effective_target = self._target_temp - self._night_setback.setback_delta
            else:
                if in_night_period:
                    effective_target = self._target_temp - self._night_setback.setback_delta

        elif self._night_setback_config:
            # Dynamic end time mode - calculate based on sunrise, orientation, weather
            current_time_only = current_time.time()

            # Parse start time
            start_time = self._parse_night_start_time(
                self._night_setback_config['start'], current_time
            )

            # Calculate dynamic end time
            end_time = self._calculate_dynamic_night_end()
            if not end_time:
                # Fallback: use recovery_deadline or default 07:00
                deadline = self._night_setback_config.get('recovery_deadline')
                if deadline:
                    hour, minute = map(int, deadline.split(":"))
                    end_time = dt_time(hour, minute)
                else:
                    end_time = dt_time(7, 0)

            # Check if in night period
            in_night_period = self._is_in_night_time_period(
                current_time_only, start_time, end_time
            )

            info["night_setback_delta"] = self._night_setback_config['delta']
            info["night_setback_end"] = end_time.strftime("%H:%M")
            info["night_setback_end_dynamic"] = True

            # Include weather for debugging
            weather = self._get_weather_condition()
            if weather:
                info["weather_condition"] = weather

            _LOGGER.debug(
                "%s: Night setback check: current=%s, start=%s, end=%s, in_night=%s, target=%s, delta=%s",
                self.entity_id, current_time_only, start_time, end_time, in_night_period,
                self._target_temp, self._night_setback_config['delta']
            )

            if in_night_period:
                effective_target = self._target_temp - self._night_setback_config['delta']
                _LOGGER.info("%s: Night setback active, effective_target=%s", self.entity_id, effective_target)
            elif self._night_setback_config.get('solar_recovery') and self._window_orientation:
                # Check solar recovery even without NightSetback object
                sunrise = self._get_sunrise_time()
                if sunrise and current_time_only < end_time:
                    # We're in morning recovery window - check weather
                    if weather and any(c in weather.lower() for c in ["sunny", "clear"]):
                        # Clear sky: delay heating to let sun warm zone
                        effective_target = self._target_temp - self._night_setback_config['delta']

        info["night_setback_active"] = in_night_period

        # Handle transition detection for learning grace period
        if self._night_setback or self._night_setback_config:
            if self._night_setback_was_active is not None and in_night_period != self._night_setback_was_active:
                transition = "started" if in_night_period else "ended"
                _LOGGER.info("%s: Night setback %s - setting learning grace period", self.entity_id, transition)
                self._set_learning_grace_period(minutes=60)
            self._night_setback_was_active = in_night_period

        return effective_target, in_night_period, info

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
        """attributes to include in entity"""
        device_state_attributes = {
            'away_temp': self._away_temp,
            'eco_temp': self._eco_temp,
            'boost_temp': self._boost_temp,
            'comfort_temp': self._comfort_temp,
            'home_temp': self._home_temp,
            'sleep_temp': self._sleep_temp,
            'activity_temp': self._activity_temp,
            "control_output": self._control_output,
            "kp": self._kp,
            "ki": self._ki,
            "kd": self._kd,
            "ke": self._ke,
            "pid_mode": self.pid_mode,
            "pid_i": self.pid_control_i,
        }
        if self.hass.data.get(DOMAIN, {}).get("debug", False):
            device_state_attributes.update({
                "pid_p": self.pid_control_p,
                "pid_d": self.pid_control_d,
                "pid_e": self.pid_control_e,
                "pid_dt": self._dt,
            })
        if self._night_setback or self._night_setback_config:
            # Use consolidated night setback calculation method
            _, _, night_info = self._calculate_night_setback_adjustment()
            device_state_attributes.update(night_info)

        # Learning grace period (after night setback transitions)
        if self.in_learning_grace_period:
            device_state_attributes["learning_paused"] = True
            device_state_attributes["learning_resumes"] = self._learning_grace_until.strftime("%H:%M")

        # Zone linking status
        if self._zone_linker:
            is_delayed = self._zone_linker.is_zone_delayed(self._unique_id)
            device_state_attributes["zone_link_delayed"] = is_delayed
            if is_delayed:
                remaining = self._zone_linker.get_delay_remaining_minutes(self._unique_id)
                device_state_attributes["zone_link_delay_remaining"] = round(remaining, 1) if remaining else 0
            if self._linked_zones:
                device_state_attributes["linked_zones"] = self._linked_zones

        # Heater control failure status
        if self._heater_control_failed:
            device_state_attributes["heater_control_failed"] = True
            device_state_attributes["last_heater_error"] = self._last_heater_error

        return device_state_attributes

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

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if self._current_temp is not None and temperature > self._current_temp:
            self._force_on = True
        elif self._current_temp is not None and temperature < self._current_temp:
            self._force_off = True
        if temperature in self._preset_temp_modes and self._preset_sync_mode == 'sync':
            await self.async_set_preset_mode(self._preset_temp_modes[temperature])
        else:
            await self.async_set_preset_mode(PRESET_NONE)
            self._target_temp = temperature
        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

    async def async_set_pid(self, **kwargs):
        """Set PID parameters."""
        for pid_kx, gain in kwargs.items():
            if gain is not None:
                setattr(self, f'_{pid_kx}', float(gain))
        self._pid_controller.set_pid_param(self._kp, self._ki, self._kd, self._ke)
        await self._async_control_heating(calc_pid=True)

    async def async_set_pid_mode(self, **kwargs):
        """Set PID parameters."""
        mode = kwargs.get('mode', None)
        if str(mode).upper() in ['AUTO', 'OFF'] and self._pid_controller is not None:
            self._pid_controller.mode = str(mode).upper()
        await self._async_control_heating(calc_pid=True)

    async def async_set_preset_temp(self, **kwargs):
        """Set the presets modes temperatures."""
        for preset_name, preset_temp in kwargs.items():
            value = None if 'disable' in preset_name and preset_temp else (
                max(min(float(preset_temp), self.max_temp), self.min_temp)
            )
            setattr(
                self,
                f"_{preset_name.replace('_disable', '')}",
                value
            )
        await self._async_control_heating(calc_pid=True)

    async def clear_integral(self, **kwargs):
        """Clear the integral value."""
        self._pid_controller.integral = 0.0
        self._i = self._pid_controller.integral
        self.async_write_ha_state()

    async def async_reset_pid_to_physics(self, **kwargs):
        """Reset PID values to physics-based defaults."""
        if not self._area_m2:
            _LOGGER.warning(
                "%s: Cannot reset PID to physics - no area_m2 configured",
                self.entity_id
            )
            return

        volume_m3 = self._area_m2 * self._ceiling_height
        tau = calculate_thermal_time_constant(
            volume_m3=volume_m3,
            window_area_m2=self._window_area_m2,
            floor_area_m2=self._area_m2,
            window_rating=self._window_rating,
        )
        self._kp, self._ki, self._kd = calculate_initial_pid(tau, self._heating_type)

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0
        self._i = 0.0

        self._pid_controller.set_pid_param(self._kp, self._ki, self._kd, self._ke)

        _LOGGER.info(
            "%s: Reset PID to physics defaults (tau=%.2f, type=%s, window=%s): Kp=%.4f, Ki=%.5f, Kd=%.3f",
            self.entity_id, tau, self._heating_type, self._window_rating, self._kp, self._ki, self._kd
        )

        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

    async def async_apply_adaptive_pid(self, **kwargs):
        """Apply adaptive PID values based on learned metrics."""
        # Get coordinator and find our zone's adaptive learner
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            _LOGGER.warning(
                "%s: Cannot apply adaptive PID - no coordinator",
                self.entity_id
            )
            return

        all_zones = coordinator.get_all_zones()
        adaptive_learner = None

        for zone_id, zone_data in all_zones.items():
            if zone_data.get("climate_entity_id") == self.entity_id:
                adaptive_learner = zone_data.get("adaptive_learner")
                break

        if not adaptive_learner:
            _LOGGER.warning(
                "%s: Cannot apply adaptive PID - no adaptive learner (learning_enabled: false?)",
                self.entity_id
            )
            return

        # Calculate recommendation based on current PID values
        recommendation = adaptive_learner.calculate_pid_adjustment(
            current_kp=self._kp,
            current_ki=self._ki,
            current_kd=self._kd,
        )

        if recommendation is None:
            cycle_count = adaptive_learner.get_cycle_count()
            _LOGGER.warning(
                "%s: Insufficient data for adaptive PID (cycles: %d, need >= 3)",
                self.entity_id,
                cycle_count,
            )
            return

        # Apply the recommended values
        old_kp, old_ki, old_kd = self._kp, self._ki, self._kd
        self._kp = recommendation["kp"]
        self._ki = recommendation["ki"]
        self._kd = recommendation["kd"]

        # Clear integral to avoid wind-up from old tuning
        self._pid_controller.integral = 0.0
        self._i = 0.0

        self._pid_controller.set_pid_param(self._kp, self._ki, self._kd, self._ke)

        _LOGGER.info(
            "%s: Applied adaptive PID: Kp=%.4f (was %.4f), Ki=%.5f (was %.5f), Kd=%.3f (was %.3f)",
            self.entity_id,
            self._kp, old_kp,
            self._ki, old_ki,
            self._kd, old_kd,
        )

        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

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

    @callback
    async def _async_sensor_changed(self, event: Event[EventStateChangedData]):
        """Handle temperature changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._previous_temp_time = self._cur_temp_time
        self._cur_temp_time = time.time()
        self._async_update_temp(new_state)
        self._trigger_source = 'sensor'
        _LOGGER.debug("%s: Received new temperature: %s", self.entity_id, self._current_temp)
        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

    @callback
    async def _async_ext_sensor_changed(self, event: Event[EventStateChangedData]):
        """Handle temperature changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._async_update_ext_temp(new_state)
        self._trigger_source = 'ext_sensor'
        _LOGGER.debug("%s: Received new external temperature: %s", self.entity_id, self._ext_temp)
        await self._async_control_heating(calc_pid=False)

    @callback
    def _async_switch_changed(self, event: Event[EventStateChangedData]):
        """Handle heater switch state changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        # Update zone demand for CentralController when valve state changes
        if self._zone_id:
            coordinator = self.hass.data.get(const.DOMAIN, {}).get("coordinator")
            if coordinator:
                coordinator.update_zone_demand(self._zone_id, self._is_device_active)

        self.async_write_ha_state()

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            _LOGGER.debug("%s: Sensor %s is %s, skipping update",
                          self.entity_id, self._sensor_entity_id, state.state)
            return
        try:
            self._previous_temp = self._current_temp
            self._current_temp = float(state.state)
            self._last_sensor_update = time.time()
        except ValueError as ex:
            _LOGGER.debug("%s: Unable to update from sensor %s: %s", self.entity_id,
                          self._sensor_entity_id, ex)

    @callback
    def _async_update_ext_temp(self, state):
        """Update thermostat with latest state from sensor."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            _LOGGER.debug("%s: External sensor %s is %s, skipping update",
                          self.entity_id, self._ext_sensor_entity_id, state.state)
            return
        try:
            self._ext_temp = float(state.state)
            self._last_ext_sensor_update = time.time()
        except ValueError as ex:
            _LOGGER.debug("%s: Unable to update from sensor %s: %s", self.entity_id,
                          self._ext_sensor_entity_id, ex)

    async def _async_control_heating(
            self, time_func: object = None, calc_pid: object = False) -> object:
        """Run PID controller, optional autotune for faster integration"""
        async with self._temp_lock:
            if not self._active and None not in (self._current_temp, self._target_temp):
                self._active = True
                _LOGGER.info("%s: Obtained temperature %s with set point %s. Activating Smart"
                             "Thermostat.", self.entity_id, self._current_temp, self._target_temp)

            if not self._active or self._hvac_mode == HVACMode.OFF:
                if self._force_off_state and self._hvac_mode == HVACMode.OFF and \
                        self._is_device_active:
                    _LOGGER.debug("%s: %s is active while HVAC mode is %s. Turning it OFF.",
                                  self.entity_id, ", ".join([entity for entity in self.heater_or_cooler_entity]), self._hvac_mode)
                    if self._pwm:
                        await self._async_heater_turn_off(force=True)
                    else:
                        self._control_output = self._output_min
                        await self._async_set_valve_value(self._control_output)
                # Update zone demand to False when OFF/inactive
                if self._zone_id:
                    coordinator = self.hass.data.get(const.DOMAIN, {}).get("coordinator")
                    if coordinator:
                        coordinator.update_zone_demand(self._zone_id, False)
                self.async_write_ha_state()
                return

            if self._sensor_stall != 0 and time.time() - self._last_sensor_update > \
                    self._sensor_stall:
                # sensor not updated for too long, considered as stall, set to safety level
                self._control_output = self._output_safety
            elif calc_pid or self._sampling_period != 0:
                await self.calc_output()
            await self.set_control_value()

            # Update zone demand for CentralController (based on actual device state, not PID output)
            if self._zone_id:
                coordinator = self.hass.data.get(const.DOMAIN, {}).get("coordinator")
                if coordinator:
                    coordinator.update_zone_demand(self._zone_id, self._is_device_active)

            self.async_write_ha_state()

    @property
    def _is_device_active(self):
        if self._pwm:
            """If the toggleable device is currently active."""
            expected = STATE_ON
            if self._heater_polarity_invert:
                expected = STATE_OFF
            return any([self.hass.states.is_state(heater_or_cooler_entity, expected) for heater_or_cooler_entity
                        in self.heater_or_cooler_entity])
        else:
            """If the valve device is currently active."""
            is_active = False
            try:  # do not throw an error if the state is not yet available on startup
                for heater_or_cooler_entity in self.heater_or_cooler_entity:
                    state = self.hass.states.get(heater_or_cooler_entity).state
                    try:
                        value = float(state)
                        if value > 0:
                            is_active = True
                    except ValueError:
                        if state in ['on', 'open']:
                            is_active = True
                return is_active
            except AttributeError as ex:
                _LOGGER.debug(
                    "Entity state not available during device active check: %s",
                    ex
                )
                return False

    def _get_cycle_start_time(self) -> float:
        """Get the time when the current heating/cooling cycle started.

        Returns our tracked time if available. If not yet tracked (startup),
        returns 0 to allow immediate action since we have no reliable data
        about when the cycle actually started (HA's last_changed reflects
        restart time, not actual device state change time).
        """
        if self._last_heat_cycle_time is not None:
            return self._last_heat_cycle_time

        # No tracked time yet - allow immediate action on first cycle after startup
        return 0

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    @property
    def heater_or_cooler_entity(self):
        """Return the entities to be controlled based on HVAC MODE.

        Returns heater or cooler entities based on mode, plus any demand_switch
        entities which are controlled regardless of heat/cool mode.
        """
        entities = []

        # Add heater or cooler based on mode
        if self.hvac_mode == HVACMode.COOL and self._cooler_entity_id is not None:
            entities.extend(self._cooler_entity_id)
        elif self._heater_entity_id is not None:
            entities.extend(self._heater_entity_id)

        # Add demand_switch entities (controlled in both heat and cool modes)
        if self._demand_switch_entity_id is not None:
            entities.extend(self._demand_switch_entity_id)

        return entities

    def _fire_heater_control_failed_event(
        self,
        entity_id: str,
        operation: str,
        error: str,
    ) -> None:
        """Fire an event when heater control fails.

        Args:
            entity_id: Entity that failed to control
            operation: Operation that failed (turn_on, turn_off, set_value)
            error: Error message
        """
        self.hass.bus.async_fire(
            f"{DOMAIN}_heater_control_failed",
            {
                "climate_entity_id": self.entity_id,
                "heater_entity_id": entity_id,
                "operation": operation,
                "error": error,
            },
        )

    async def _async_call_heater_service(
        self,
        entity_id: str,
        domain: str,
        service: str,
        data: dict,
    ) -> bool:
        """Call a heater/cooler service with error handling.

        Args:
            entity_id: Entity ID being controlled
            domain: Service domain (homeassistant, light, valve, number, etc.)
            service: Service name (turn_on, turn_off, set_value, etc.)
            data: Service call data

        Returns:
            True if successful, False otherwise
        """
        try:
            await self.hass.services.async_call(domain, service, data)
            # Clear failure state on success
            self._heater_control_failed = False
            self._last_heater_error = None
            return True

        except ServiceNotFound as e:
            _LOGGER.error(
                "%s: Service '%s.%s' not found for %s: %s",
                self.entity_id,
                domain,
                service,
                entity_id,
                e,
            )
            self._heater_control_failed = True
            self._last_heater_error = f"Service not found: {domain}.{service}"
            self._fire_heater_control_failed_event(entity_id, service, str(e))
            return False

        except HomeAssistantError as e:
            _LOGGER.error(
                "%s: Home Assistant error calling %s.%s on %s: %s",
                self.entity_id,
                domain,
                service,
                entity_id,
                e,
            )
            self._heater_control_failed = True
            self._last_heater_error = str(e)
            self._fire_heater_control_failed_event(entity_id, service, str(e))
            return False

        except Exception as e:
            _LOGGER.error(
                "%s: Unexpected error calling %s.%s on %s: %s",
                self.entity_id,
                domain,
                service,
                entity_id,
                e,
            )
            self._heater_control_failed = True
            self._last_heater_error = str(e)
            self._fire_heater_control_failed_event(entity_id, service, str(e))
            return False

    async def _async_heater_turn_on(self):
        """Turn heater toggleable device on."""
        # Check if zone is delayed due to linked zone heating
        if self._zone_linker and self._zone_linker.is_zone_delayed(self._unique_id):
            remaining = self._zone_linker.get_delay_remaining_minutes(self._unique_id)
            _LOGGER.info(
                "%s: Zone linking delay active - heating delayed for %.1f more minutes",
                self.entity_id, remaining or 0
            )
            return

        if self._is_device_active:
            # It's a state refresh call from keep_alive, just force switch ON.
            _LOGGER.info("%s: Refresh state ON %s", self.entity_id,
                         ", ".join([entity for entity in self.heater_or_cooler_entity]))
        elif time.time() - self._get_cycle_start_time() >= self._min_off_cycle_duration.seconds:
            _LOGGER.info("%s: Turning ON %s", self.entity_id,
                         ", ".join([entity for entity in self.heater_or_cooler_entity]))
            self._last_heat_cycle_time = time.time()

            # Notify zone linker that this zone started heating (for linked zones)
            if self._zone_linker and self._linked_zones and not self._is_heating:
                self._is_heating = True
                await self._zone_linker.on_zone_heating_started(
                    self._unique_id, self._link_delay_minutes
                )
        else:
            _LOGGER.info("%s: Reject request turning ON %s: Cycle is too short",
                         self.entity_id, ", ".join([entity for entity in self.heater_or_cooler_entity]))
            return
        for heater_or_cooler_entity in self.heater_or_cooler_entity:
            data = {ATTR_ENTITY_ID: heater_or_cooler_entity}
            if self._heater_polarity_invert:
                service = SERVICE_TURN_OFF
            else:
                service = SERVICE_TURN_ON
            await self._async_call_heater_service(
                heater_or_cooler_entity, HA_DOMAIN, service, data
            )

    async def _async_heater_turn_off(self, force=False):
        """Turn heater toggleable device off."""
        if not self._is_device_active:
            # It's a state refresh call from keep_alive, just force switch OFF.
            _LOGGER.info("%s: Refresh state OFF %s", self.entity_id,
                         ", ".join([entity for entity in self.heater_or_cooler_entity]))
        elif time.time() - self._get_cycle_start_time() >= self._min_on_cycle_duration.seconds or force:
            _LOGGER.info("%s: Turning OFF %s", self.entity_id,
                         ", ".join([entity for entity in self.heater_or_cooler_entity]))
            self._last_heat_cycle_time = time.time()
            # Reset heating state for zone linking
            self._is_heating = False
        else:
            _LOGGER.info("%s: Reject request turning OFF %s: Cycle is too short",
                         self.entity_id, ", ".join([entity for entity in self.heater_or_cooler_entity]))
            return
        for heater_or_cooler_entity in self.heater_or_cooler_entity:
            data = {ATTR_ENTITY_ID: heater_or_cooler_entity}
            if self._heater_polarity_invert:
                service = SERVICE_TURN_ON
            else:
                service = SERVICE_TURN_OFF
            await self._async_call_heater_service(
                heater_or_cooler_entity, HA_DOMAIN, service, data
            )

    async def _async_set_valve_value(self, value: float):
        _LOGGER.info("%s: Change state of %s to %s", self.entity_id,
                     ", ".join([entity for entity in self.heater_or_cooler_entity]), value)
        for heater_or_cooler_entity in self.heater_or_cooler_entity:
            if heater_or_cooler_entity[0:6] == 'light.':
                data = {ATTR_ENTITY_ID: heater_or_cooler_entity, ATTR_BRIGHTNESS_PCT: value}
                await self._async_call_heater_service(
                    heater_or_cooler_entity,
                    LIGHT_DOMAIN,
                    SERVICE_TURN_LIGHT_ON,
                    data,
                )
            elif heater_or_cooler_entity[0:6] == 'valve.':
                data = {ATTR_ENTITY_ID: heater_or_cooler_entity, ATTR_POSITION: value}
                await self._async_call_heater_service(
                    heater_or_cooler_entity,
                    VALVE_DOMAIN,
                    SERVICE_SET_VALVE_POSITION,
                    data,
                )
            else:
                data = {ATTR_ENTITY_ID: heater_or_cooler_entity, ATTR_VALUE: value}
                await self._async_call_heater_service(
                    heater_or_cooler_entity,
                    self._get_number_entity_domain(heater_or_cooler_entity),
                    SERVICE_SET_VALUE,
                    data,
                )

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode.
        This method must be run in the event loop and returns a coroutine.
        """
        if preset_mode not in self.preset_modes:
            return None
        if preset_mode != PRESET_NONE and self.preset_mode == PRESET_NONE:
            # self._is_away = True
            self._saved_target_temp = self._target_temp
            self._target_temp = self.presets[preset_mode]
        elif preset_mode == PRESET_NONE and self.preset_mode != PRESET_NONE:
            # self._is_away = False
            self._target_temp = self._saved_target_temp
        elif preset_mode == PRESET_NONE and self.preset_mode == PRESET_NONE:
            return None
        else:
            self._target_temp = self.presets[preset_mode]
        self._attr_preset_mode = preset_mode
        if self._boost_pid_off and self._attr_preset_mode == PRESET_BOOST:
            # Force PID OFF if requested and boost mode is active
            await self.async_set_pid_mode(mode='off')
        elif self._boost_pid_off and self._attr_preset_mode != PRESET_BOOST:
            # Force PID Auto if managed by boost_pid_off and not in boost mode
            await self.async_set_pid_mode(mode='auto')
        else:
            # if boost_pid_off is false, don't change the PID mode
            await self._async_control_heating(calc_pid=True)

    async def calc_output(self):
        """calculate control output"""
        update = False
        if self._previous_temp_time is None:
            self._previous_temp_time = time.time()
        if self._cur_temp_time is None:
            self._cur_temp_time = time.time()
        if self._previous_temp_time > self._cur_temp_time:
            self._previous_temp_time = self._cur_temp_time

        # Apply night setback adjustment if configured
        effective_target, _, _ = self._calculate_night_setback_adjustment()

        if self._pid_controller.sampling_period == 0:
            self._control_output, update = self._pid_controller.calc(self._current_temp,
                                                                     effective_target,
                                                                     self._cur_temp_time,
                                                                     self._previous_temp_time,
                                                                     self._ext_temp)
        else:
            self._control_output, update = self._pid_controller.calc(self._current_temp,
                                                                     effective_target,
                                                                     ext_temp=self._ext_temp)
        self._p = round(self._pid_controller.proportional, 1)
        self._i = round(self._pid_controller.integral, 1)
        self._d = round(self._pid_controller.derivative, 1)
        self._e = round(self._pid_controller.external, 1)
        self._control_output = round(self._control_output, self._output_precision)
        if not self._output_precision:
            self._control_output = int(self._control_output)
        error = self._pid_controller.error
        self._dt = self._pid_controller.dt

        if update:
            _LOGGER.debug("%s: New PID control output: %s (error = %.2f, dt = %.2f, "
                          "p=%.2f, i=%.2f, d=%.2f, e=%.2f) [Kp=%.4f, Ki=%.4f, Kd=%.2f, Ke=%.2f]",
                          self.entity_id, str(self._control_output), error, self._dt,
                          self._p, self._i, self._d, self._e,
                          self._kp or 0, self._ki or 0, self._kd or 0, self._ke or 0)

    async def set_control_value(self):
        """Set Output value for heater"""
        if self._pwm:
            if abs(self._control_output) == self._difference:
                if not self._is_device_active:
                    _LOGGER.info("%s: Output is %s. Request turning ON %s", self.entity_id,
                                 self._difference, ", ".join([entity for entity in self.heater_or_cooler_entity]))
                    self._time_changed = time.time()
                await self._async_heater_turn_on()
            elif abs(self._control_output) > 0:
                await self.pwm_switch()
            else:
                if self._is_device_active:
                    _LOGGER.info("%s: Output is 0. Request turning OFF %s", self.entity_id,
                                 ", ".join([entity for entity in self.heater_or_cooler_entity]))
                    self._time_changed = time.time()
                await self._async_heater_turn_off()
        else:
            await self._async_set_valve_value(abs(self._control_output))

    async def pwm_switch(self):
        """turn off and on the heater proportionally to control_value."""
        time_passed = time.time() - self._time_changed
        # Compute time_on based on PWM duration and PID output
        time_on = self._pwm * abs(self._control_output) / self._difference
        time_off = self._pwm - time_on
        # Check time_on and time_off are not too short
        if 0 < time_on < self._min_on_cycle_duration.seconds:
            # time_on is too short, increase time_off and time_on
            time_off *= self._min_on_cycle_duration.seconds / time_on
            time_on = self._min_on_cycle_duration.seconds
        if 0 < time_off < self._min_off_cycle_duration.seconds:
            # time_off is too short, increase time_on and time_off
            time_on *= self._min_off_cycle_duration.seconds / time_off
            time_off = self._min_off_cycle_duration.seconds
        if self._is_device_active:
            if time_on <= time_passed or self._force_off:
                _LOGGER.info(
                    "%s: ON time passed. Request turning OFF %s",
                    self.entity_id,
                    ", ".join([entity for entity in self.heater_or_cooler_entity])
                )
                await self._async_heater_turn_off()
                self._time_changed = time.time()
            else:
                _LOGGER.info(
                    "%s: Time until %s turns OFF: %s sec",
                    self.entity_id,
                    ", ".join([entity for entity in self.heater_or_cooler_entity]),
                    int(time_on - time_passed)
                )
                if self._keep_alive:
                    await self._async_heater_turn_on()
        else:
            if time_off <= time_passed or self._force_on:
                _LOGGER.info(
                    "%s: OFF time passed. Request turning ON %s", self.entity_id,
                    ", ".join([entity for entity in self.heater_or_cooler_entity])
                )
                await self._async_heater_turn_on()
                self._time_changed = time.time()
            else:
                _LOGGER.info(
                    "%s: Time until %s turns ON: %s sec", self.entity_id,
                    ", ".join([entity for entity in self.heater_or_cooler_entity]),
                    int(time_off - time_passed)
                )
                if self._keep_alive:
                    await self._async_heater_turn_off()
        self._force_on = False
        self._force_off = False
