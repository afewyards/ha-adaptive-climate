"""Platform setup for adaptive thermostat climate entities."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.helpers import entity_platform, discovery
from homeassistant.helpers.typing import ConfigType
from homeassistant.const import (
    CONF_NAME,
    CONF_UNIQUE_ID,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
)
from homeassistant.components.climate import PLATFORM_SCHEMA, HVACMode
import homeassistant.helpers.config_validation as cv
from homeassistant.util import slugify

from . import DOMAIN
from . import const
from .adaptive.learning import AdaptiveLearner
from .adaptive.persistence import LearningDataStore
from .manifold_config import LOOPS_REMOVED_MESSAGE, REMOVED_LOOPS_KEY, removed_key

_LOGGER = logging.getLogger(__name__)

# Extend PLATFORM_SCHEMA with all configuration options
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(const.CONF_HEATER): cv.entity_ids,
        vol.Optional(const.CONF_COOLER): cv.entity_ids,
        vol.Optional(const.CONF_DEMAND_SWITCH): cv.entity_ids,
        vol.Required(const.CONF_INVERT_HEATER, default=False): cv.boolean,
        vol.Required(const.CONF_SENSOR): cv.entity_id,
        vol.Optional(const.CONF_FORCE_OFF_STATE, default=True): cv.boolean,
        vol.Optional(const.CONF_MAX_TEMP): vol.Coerce(float),
        vol.Optional(const.CONF_MIN_TEMP): vol.Coerce(float),
        vol.Optional(CONF_NAME, default=const.DEFAULT_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID, default="none"): cv.string,
        vol.Optional(const.CONF_TARGET_TEMP): vol.Coerce(float),
        vol.Optional(const.CONF_HOT_TOLERANCE): vol.Coerce(float),
        vol.Optional(const.CONF_COLD_TOLERANCE): vol.Coerce(float),
        vol.Optional(const.CONF_MIN_OPEN_TIME): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_MIN_CLOSED_TIME): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_MIN_OPEN_TIME_PID_OFF): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_MIN_CLOSED_TIME_PID_OFF): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_CONTROL_INTERVAL): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(const.CONF_SAMPLING_PERIOD, default=const.DEFAULT_SAMPLING_PERIOD): vol.All(
            cv.time_period, cv.positive_timedelta
        ),
        vol.Optional(const.CONF_SENSOR_STALL, default=const.DEFAULT_SENSOR_STALL): vol.All(
            cv.time_period, cv.positive_timedelta
        ),
        vol.Optional(const.CONF_OUTPUT_SAFETY, default=const.DEFAULT_OUTPUT_SAFETY): vol.Coerce(float),
        vol.Optional(const.CONF_INITIAL_HVAC_MODE): vol.In([HVACMode.COOL, HVACMode.HEAT, HVACMode.OFF]),
        vol.Optional(const.CONF_PRECISION): vol.In([PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]),
        vol.Optional(const.CONF_TARGET_TEMP_STEP): vol.In([PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]),
        vol.Optional(const.CONF_OUTPUT_PRECISION, default=const.DEFAULT_OUTPUT_PRECISION): vol.Coerce(int),
        vol.Optional(const.CONF_OUTPUT_MIN, default=const.DEFAULT_OUTPUT_MIN): vol.Coerce(float),
        vol.Optional(const.CONF_OUTPUT_MAX, default=const.DEFAULT_OUTPUT_MAX): vol.Coerce(float),
        vol.Optional(const.CONF_OUT_CLAMP_LOW, default=const.DEFAULT_OUT_CLAMP_LOW): vol.Coerce(float),
        vol.Optional(const.CONF_OUT_CLAMP_HIGH, default=const.DEFAULT_OUT_CLAMP_HIGH): vol.Coerce(float),
        vol.Optional(const.CONF_PWM): vol.All(cv.time_period, cv.positive_timedelta),
        # Adaptive learning options
        vol.Optional(const.CONF_HEATING_TYPE): vol.In(const.VALID_HEATING_TYPES),
        vol.Optional(const.CONF_AREA): cv.string,  # Home Assistant area ID to assign entity to
        vol.Optional(const.CONF_DERIVATIVE_FILTER): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional(const.CONF_AUTO_APPLY_PID, default=True): cv.boolean,
        vol.Optional(const.CONF_AREA_M2): vol.Coerce(float),
        vol.Optional(const.CONF_MAX_POWER_W): vol.Coerce(float),
        vol.Optional(const.CONF_CEILING_HEIGHT, default=const.DEFAULT_CEILING_HEIGHT): vol.Coerce(float),
        # Actuator wear tracking
        vol.Optional(const.CONF_HEATER_RATED_CYCLES): vol.Coerce(int),
        vol.Optional(const.CONF_COOLER_RATED_CYCLES): vol.Coerce(int),
        vol.Optional(const.CONF_WINDOW_AREA_M2): vol.Coerce(float),
        vol.Optional(const.CONF_WINDOW_ORIENTATION): vol.In(const.VALID_WINDOW_ORIENTATIONS),
        vol.Optional(const.CONF_WINDOW_RATING): cv.string,
        # Contact sensors
        vol.Optional(const.CONF_CONTACT_SENSORS): cv.entity_ids,
        vol.Optional(const.CONF_CONTACT_ACTION, default=const.CONTACT_ACTION_PAUSE): vol.In(
            const.VALID_CONTACT_ACTIONS
        ),
        vol.Optional(const.CONF_CONTACT_DELAY, default=const.DEFAULT_CONTACT_DELAY): vol.Coerce(int),
        # Humidity detection
        vol.Optional(const.CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(const.CONF_EXCLUDE_FROM_DEW_POINT, default=False): cv.boolean,
        vol.Optional(const.CONF_HUMIDITY_SPIKE_THRESHOLD, default=const.DEFAULT_HUMIDITY_SPIKE_THRESHOLD): vol.Coerce(
            float
        ),
        vol.Optional(const.CONF_HUMIDITY_ABSOLUTE_MAX, default=const.DEFAULT_HUMIDITY_ABSOLUTE_MAX): vol.Coerce(float),
        vol.Optional(const.CONF_HUMIDITY_DETECTION_WINDOW, default=const.DEFAULT_HUMIDITY_DETECTION_WINDOW): vol.Coerce(
            int
        ),
        vol.Optional(
            const.CONF_HUMIDITY_STABILIZATION_DELAY, default=const.DEFAULT_HUMIDITY_STABILIZATION_DELAY
        ): vol.Coerce(int),
        vol.Optional(const.CONF_HUMIDITY_EXIT_THRESHOLD, default=const.DEFAULT_HUMIDITY_EXIT_THRESHOLD): vol.Coerce(
            float
        ),
        vol.Optional(const.CONF_HUMIDITY_EXIT_DROP, default=const.DEFAULT_HUMIDITY_EXIT_DROP): vol.Coerce(float),
        # Night setback
        vol.Optional(const.CONF_NIGHT_SETBACK): vol.Schema(
            {
                vol.Optional(const.CONF_NIGHT_SETBACK_START): cv.string,
                vol.Optional(const.CONF_NIGHT_SETBACK_END): cv.string,
                vol.Optional(const.CONF_NIGHT_SETBACK_DELTA, default=const.DEFAULT_NIGHT_SETBACK_DELTA): vol.Coerce(
                    float
                ),
                vol.Optional(const.CONF_NIGHT_SETBACK_RECOVERY_DEADLINE): cv.string,
                vol.Optional(
                    const.CONF_MIN_EFFECTIVE_ELEVATION, default=const.DEFAULT_MIN_EFFECTIVE_ELEVATION
                ): vol.Coerce(float),
                vol.Optional(const.CONF_PREHEAT_ENABLED, default=False): cv.boolean,
                vol.Optional(const.CONF_MAX_PREHEAT_HOURS): vol.Coerce(float),
            }
        ),
        # Floor construction (for floor_hydronic heating type)
        vol.Optional(const.CONF_FLOOR_CONSTRUCTION): vol.Schema(
            {
                vol.Optional(const.CONF_PIPE_SPACING_MM, default=150): vol.In([100, 150, 200, 300]),
                vol.Required("layers"): vol.All(
                    cv.ensure_list,
                    [
                        vol.Schema(
                            {
                                vol.Required("type"): vol.In(["top_floor", "screed"]),
                                vol.Required("material"): cv.string,
                                vol.Required("thickness_mm"): vol.All(vol.Coerce(int), vol.Range(min=5, max=100)),
                            }
                        )
                    ],
                ),
            }
        ),
        # Manifold integration
        vol.Optional(REMOVED_LOOPS_KEY): removed_key(LOOPS_REMOVED_MESSAGE),
        # Setpoint boost
        vol.Optional(const.CONF_SETPOINT_BOOST, default=True): cv.boolean,
        vol.Optional(const.CONF_SETPOINT_BOOST_FACTOR): vol.Coerce(float),
        vol.Optional(const.CONF_SETPOINT_DEBOUNCE, default=const.DEFAULT_SETPOINT_DEBOUNCE): cv.positive_int,
        # Valve actuation time
        vol.Optional(const.CONF_VALVE_ACTUATION_TIME): vol.All(cv.time_period, cv.positive_timedelta),
    }
)


def validate_pwm_compatibility(config):
    """Validate that PWM mode is not used with climate entities.

    PWM (Pulse Width Modulation) creates nested control loops when used with
    climate entities, which have their own internal PID controllers. This can
    cause instability and erratic behavior.

    Args:
        config: Platform configuration dictionary

    Raises:
        vol.Invalid: If PWM is configured with a climate entity

    Returns:
        config: Validated configuration (unchanged if valid)
    """
    pwm = config.get(const.CONF_PWM)
    pwm_seconds = pwm.seconds if pwm else 0

    # Only validate if PWM is actually enabled (> 0 seconds)
    if pwm_seconds == 0:
        return config

    # Check heater entity
    heater_entities = config.get(const.CONF_HEATER, [])
    for entity_id in heater_entities:
        domain, _ = split_entity_id(entity_id)
        if domain == "climate":
            raise vol.Invalid(
                f"PWM mode cannot be used with climate entity '{entity_id}'. "
                f"Climate entities have their own PID controllers, creating nested control loops. "
                f"Solutions: (1) Set pwm to '00:00:00' for valve mode, or (2) Use a switch/light entity instead."
            )

    # Check cooler entity
    cooler_entities = config.get(const.CONF_COOLER, [])
    for entity_id in cooler_entities:
        domain, _ = split_entity_id(entity_id)
        if domain == "climate":
            raise vol.Invalid(
                f"PWM mode cannot be used with climate entity '{entity_id}'. "
                f"Climate entities have their own PID controllers, creating nested control loops. "
                f"Solutions: (1) Set pwm to '00:00:00' for valve mode, or (2) Use a switch/light entity instead."
            )

    return config


def _resolve_pwm(
    config_pwm: timedelta | None,
    domain_pwm: timedelta | None,
) -> timedelta:
    """Resolve PWM with None-aware logic (timedelta(0) is valid, not falsy).

    Priority: entity config → domain config → 15-min default.
    Uses explicit None checks so that timedelta(0) (valve mode) is preserved
    instead of being treated as falsy and falling through to the default.

    The default (15 min) mirrors const.DEFAULT_PWM = "00:15:00".
    Expressed as a timedelta literal to avoid a cv dependency inside the helper.
    """
    if config_pwm is not None:
        return config_pwm
    if domain_pwm is not None:
        return domain_pwm
    return timedelta(minutes=15)  # const.DEFAULT_PWM = "00:15:00"


def build_dew_point_zone_data(config: ConfigType) -> dict[str, Any]:
    """Return the zone_data keys the water-temperature dew point scanner reads.

    Extracted as a helper so the contract between ``register_zone`` and
    ``DewPointScanner`` is directly unit-testable.

    Args:
        config: The validated entity platform config.

    Returns:
        Dict with ``humidity_sensor`` (entity id or None) and
        ``exclude_from_dew_point`` (bool).
    """
    return {
        "humidity_sensor": config.get(const.CONF_HUMIDITY_SENSOR),
        "exclude_from_dew_point": bool(config.get(const.CONF_EXCLUDE_FROM_DEW_POINT, False)),
    }


async def async_restore_water_temp_state(learning_store: Any, controller: Any) -> None:
    """Restore water temperature control state from the learning store.

    Mirrors the manifold restore: the coordinator (and therefore the controller)
    is created in ``async_setup`` before the store exists, so restoration happens
    here, on first-zone setup.  ``restore_state`` is always called — even with
    ``None`` — so the controller's ``_restored`` gate opens on first run too.

    Args:
        learning_store: The LearningDataStore singleton.
        controller: WaterTempController instance, or None when not configured.
    """
    if controller is None:
        return
    water_temp_state = await learning_store.async_load_water_temp_state()
    controller.restore_state(water_temp_state)
    _LOGGER.info("Restored water temperature control state")


async def async_setup_platform(hass: HomeAssistant, config: ConfigType, async_add_entities, discovery_info=None):
    """Set up the generic thermostat platform."""
    # Import here to avoid circular dependency
    from .climate import AdaptiveThermostat
    from .thermostat_config import AdaptiveThermostatConfig

    platform = entity_platform.current_platform.get()
    assert platform

    # Get name and create zone_id
    name = config.get(CONF_NAME)
    zone_id = slugify(name)

    # Create LearningDataStore singleton if it doesn't exist
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    if "learning_store" not in hass.data[DOMAIN]:
        learning_store = LearningDataStore(hass)
        await learning_store.async_load()
        hass.data[DOMAIN]["learning_store"] = learning_store
        _LOGGER.info("Created LearningDataStore singleton")

        # M01: Restore manifold state now that the store exists.
        # The manifold_registry is created in async_setup() (__init__.py) before
        # climate_setup runs, so the store wasn't available yet during domain setup.
        manifold_registry = hass.data[DOMAIN].get("manifold_registry")
        if manifold_registry:
            manifold_state = await learning_store.async_load_manifold_state()
            if manifold_state:
                manifold_registry.restore_state(manifold_state)
                _LOGGER.info("Restored manifold state for %d manifolds", len(manifold_state))

        # Restore water temperature control state (same reason as manifold above)
        coordinator = hass.data[DOMAIN].get("coordinator")
        if coordinator is not None:
            await async_restore_water_temp_state(learning_store, coordinator.water_temp_controller)

        # M02: Trigger number platform discovery once so LearningWindowNumber exists.
        # Done here (first zone only) because number entities are system-wide, not
        # per-zone, and must be created before any zone reads learning_window_days.
        hass.data[DOMAIN]["number_platform_loaded"] = True
        hass.async_create_task(discovery.async_load_platform(hass, "number", DOMAIN, {}, config))
        _LOGGER.info("Triggered number platform discovery for LearningWindowNumber")
    else:
        learning_store = hass.data[DOMAIN]["learning_store"]

    # Validate that at least one output entity is configured
    heater = config.get(const.CONF_HEATER)
    cooler = config.get(const.CONF_COOLER)
    demand_switch = config.get(const.CONF_DEMAND_SWITCH)
    if not heater and not cooler and not demand_switch:
        _LOGGER.error("%s: At least one of heater, cooler, or demand_switch must be configured", name)
        return

    # Validate PWM compatibility with entity types
    try:
        validate_pwm_compatibility(config)
    except vol.Invalid as ex:
        _LOGGER.error("%s: Configuration error - %s", name, ex)
        raise

    # ── Resolve valve actuation time ──────────────────────────────────────────
    heating_type = config.get(const.CONF_HEATING_TYPE)
    valve_actuation_config = config.get(const.CONF_VALVE_ACTUATION_TIME)
    if valve_actuation_config is not None:
        valve_actuation_seconds = float(valve_actuation_config.total_seconds())
    else:
        valve_actuation_seconds = float(const.HEATING_TYPE_VALVE_DEFAULTS.get(heating_type, 0))

    # ── Build typed config (A08) ───────────────────────────────────────────────
    domain_data = hass.data.get(DOMAIN, {})
    thermostat_config = AdaptiveThermostatConfig(
        # Identity
        name=name,
        unique_id=config.get(CONF_UNIQUE_ID),
        unit=hass.config.units.temperature_unit,
        zone_id=zone_id,
        ha_area=config.get(const.CONF_AREA),
        # Sensors
        sensor_entity_id=config.get(const.CONF_SENSOR),
        ext_sensor_entity_id=domain_data.get("outdoor_sensor"),
        weather_entity_id=domain_data.get("weather_entity"),
        wind_speed_sensor_entity_id=domain_data.get("wind_speed_sensor"),
        humidity_sensor=config.get(const.CONF_HUMIDITY_SENSOR),
        # Heater / cooler
        heater_entity_id=config.get(const.CONF_HEATER),
        cooler_entity_id=config.get(const.CONF_COOLER),
        demand_switch_entity_id=config.get(const.CONF_DEMAND_SWITCH),
        invert_heater=config.get(const.CONF_INVERT_HEATER, False),
        ac_mode=bool(cooler) or bool(domain_data.get("main_cooler_switch")),
        force_off_state=config.get(const.CONF_FORCE_OFF_STATE, True),
        # Temperature range: entity → domain → default
        min_temp=config.get(const.CONF_MIN_TEMP) or domain_data.get("min_temp", const.DEFAULT_MIN_TEMP),
        max_temp=config.get(const.CONF_MAX_TEMP) or domain_data.get("max_temp", const.DEFAULT_MAX_TEMP),
        target_temp=config.get(const.CONF_TARGET_TEMP) or domain_data.get("target_temp"),
        hot_tolerance=config.get(const.CONF_HOT_TOLERANCE) or domain_data.get("hot_tolerance", const.DEFAULT_TOLERANCE),
        cold_tolerance=config.get(const.CONF_COLD_TOLERANCE)
        or domain_data.get("cold_tolerance", const.DEFAULT_TOLERANCE),
        # Preset temperatures (domain-level)
        away_temp=domain_data.get("away_temp"),
        eco_temp=domain_data.get("eco_temp"),
        boost_temp=domain_data.get("boost_temp"),
        comfort_temp=domain_data.get("comfort_temp"),
        home_temp=domain_data.get("home_temp"),
        sleep_temp=domain_data.get("sleep_temp"),
        activity_temp=domain_data.get("activity_temp"),
        preset_sync_mode=domain_data.get("preset_sync_mode"),
        # Timing: entity → domain → default
        min_open_time=config.get(const.CONF_MIN_OPEN_TIME) or domain_data.get("min_open_time") or timedelta(0),
        min_closed_time=config.get(const.CONF_MIN_CLOSED_TIME) or domain_data.get("min_closed_time"),
        min_open_time_pid_off=config.get(const.CONF_MIN_OPEN_TIME_PID_OFF),
        min_closed_time_pid_off=config.get(const.CONF_MIN_CLOSED_TIME_PID_OFF),
        control_interval=config.get(const.CONF_CONTROL_INTERVAL),
        sampling_period=config.get(const.CONF_SAMPLING_PERIOD),
        sensor_stall=config.get(const.CONF_SENSOR_STALL),
        pwm=_resolve_pwm(config.get(const.CONF_PWM), domain_data.get("pwm")),
        valve_actuation_time=valve_actuation_seconds,
        # Output
        output_safety=config.get(const.CONF_OUTPUT_SAFETY),
        output_precision=config.get(const.CONF_OUTPUT_PRECISION),
        output_min=config.get(const.CONF_OUTPUT_MIN),
        output_max=config.get(const.CONF_OUTPUT_MAX),
        output_clamp_low=config.get(const.CONF_OUT_CLAMP_LOW),
        output_clamp_high=config.get(const.CONF_OUT_CLAMP_HIGH),
        # Mode / display
        initial_hvac_mode=config.get(const.CONF_INITIAL_HVAC_MODE),
        boost_pid_off=domain_data.get("boost_pid_off"),
        # Precision and step: entity → domain → default
        precision=config.get(const.CONF_PRECISION) or domain_data.get("precision", const.DEFAULT_PRECISION),
        target_temp_step=config.get(const.CONF_TARGET_TEMP_STEP)
        or domain_data.get("target_temp_step", const.DEFAULT_TARGET_TEMP_STEP),
        # Zone physics
        heating_type=heating_type,
        area_m2=config.get(const.CONF_AREA_M2),
        ceiling_height=config.get(const.CONF_CEILING_HEIGHT, const.DEFAULT_CEILING_HEIGHT),
        window_area_m2=config.get(const.CONF_WINDOW_AREA_M2),
        window_orientation=config.get(const.CONF_WINDOW_ORIENTATION),
        window_rating=config.get(const.CONF_WINDOW_RATING)
        or domain_data.get("window_rating", const.DEFAULT_WINDOW_RATING),
        floor_construction=config.get(const.CONF_FLOOR_CONSTRUCTION),
        max_power_w=config.get(const.CONF_MAX_POWER_W),
        supply_temperature=domain_data.get("supply_temperature"),
        # PID / adaptive
        derivative_filter_alpha=config.get(const.CONF_DERIVATIVE_FILTER),
        auto_apply_pid=config.get(const.CONF_AUTO_APPLY_PID, True),
        # Contact sensors
        contact_sensors=config.get(const.CONF_CONTACT_SENSORS),
        contact_action=config.get(const.CONF_CONTACT_ACTION, const.CONTACT_ACTION_PAUSE),
        contact_delay=config.get(const.CONF_CONTACT_DELAY, const.DEFAULT_CONTACT_DELAY),
        # Humidity detection
        humidity_spike_threshold=config.get(
            const.CONF_HUMIDITY_SPIKE_THRESHOLD, const.DEFAULT_HUMIDITY_SPIKE_THRESHOLD
        ),
        humidity_absolute_max=config.get(const.CONF_HUMIDITY_ABSOLUTE_MAX, const.DEFAULT_HUMIDITY_ABSOLUTE_MAX),
        humidity_detection_window=config.get(
            const.CONF_HUMIDITY_DETECTION_WINDOW, const.DEFAULT_HUMIDITY_DETECTION_WINDOW
        ),
        humidity_stabilization_delay=config.get(
            const.CONF_HUMIDITY_STABILIZATION_DELAY, const.DEFAULT_HUMIDITY_STABILIZATION_DELAY
        ),
        humidity_exit_threshold=config.get(const.CONF_HUMIDITY_EXIT_THRESHOLD, const.DEFAULT_HUMIDITY_EXIT_THRESHOLD),
        humidity_exit_drop=config.get(const.CONF_HUMIDITY_EXIT_DROP, const.DEFAULT_HUMIDITY_EXIT_DROP),
        # Night setback
        night_setback_config=config.get(const.CONF_NIGHT_SETBACK),
        # Setpoint boost
        setpoint_boost=config.get(const.CONF_SETPOINT_BOOST, True),
        setpoint_boost_factor=config.get(const.CONF_SETPOINT_BOOST_FACTOR),
        setpoint_debounce=config.get(const.CONF_SETPOINT_DEBOUNCE, const.DEFAULT_SETPOINT_DEBOUNCE),
    )

    thermostat = AdaptiveThermostat(config=thermostat_config)

    # Register zone with coordinator BEFORE adding entity
    # This ensures zone_data is available when async_added_to_hass runs
    coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
    if coordinator:
        # Get domain-level chronic approach historic scan flag
        chronic_approach_historic_scan = hass.data.get(DOMAIN, {}).get("chronic_approach_historic_scan", False)

        # Create AdaptiveLearner and restore from storage if data exists
        adaptive_learner = AdaptiveLearner(
            heating_type=config.get(const.CONF_HEATING_TYPE),
            chronic_approach_historic_scan=chronic_approach_historic_scan,
        )

        # Get stored zone data from LearningDataStore
        stored_zone_data = learning_store.get_zone_data(zone_id)
        if stored_zone_data:
            # Restore adaptive learner state
            if "adaptive_learner" in stored_zone_data:
                adaptive_learner.restore_from_dict(stored_zone_data["adaptive_learner"])
                _LOGGER.info("Restored AdaptiveLearner for zone %s from storage", zone_id)

        zone_data = {
            "climate_entity_id": f"climate.{zone_id}",
            "zone_name": name,
            "area_m2": config.get(const.CONF_AREA_M2, 0),
            "heating_type": config.get(const.CONF_HEATING_TYPE),
            "learning_enabled": True,  # Always enabled, vacation mode can toggle
            "adaptive_learner": adaptive_learner,
            "pwm_seconds": config.get(const.CONF_PWM).seconds if config.get(const.CONF_PWM) else 0,
            "valve_actuation_time": valve_actuation_seconds,
            "window_orientation": config.get(const.CONF_WINDOW_ORIENTATION),
            **build_dew_point_zone_data(config),
        }

        # Store ke_learner data for async_added_to_hass to use
        if stored_zone_data and "ke_learner" in stored_zone_data:
            zone_data["stored_ke_data"] = stored_zone_data["ke_learner"]
            _LOGGER.info("Stored ke_learner data for zone %s for later restoration", zone_id)

        # Store preheat_learner data for async_added_to_hass to use
        if stored_zone_data and "preheat_learner" in stored_zone_data:
            zone_data["stored_preheat_data"] = stored_zone_data["preheat_learner"]
            _LOGGER.info("Stored preheat_learner data for zone %s for later restoration", zone_id)

        coordinator.register_zone(zone_id, zone_data)
        _LOGGER.info("Registered zone %s with coordinator", zone_id)

    async_add_entities([thermostat])

    # Trigger sensor platform discovery for this zone (after entity is added)
    if coordinator:
        hass.async_create_task(
            discovery.async_load_platform(
                hass,
                "sensor",
                DOMAIN,
                {
                    "zone_id": zone_id,
                    "zone_name": name,
                    "climate_entity_id": f"climate.{zone_id}",
                    "heater_rated_cycles": config.get(const.CONF_HEATER_RATED_CYCLES),
                    "cooler_rated_cycles": config.get(const.CONF_COOLER_RATED_CYCLES),
                    # H06: pass heat-output sensor config so HeatOutputSensor gets non-None refs
                    "supply_temp_sensor": domain_data.get("supply_temp_sensor"),
                    "return_temp_sensor": domain_data.get("return_temp_sensor"),
                    "flow_rate_sensor": domain_data.get("flow_rate_sensor"),
                    "fallback_flow_rate": domain_data.get("fallback_flow_rate", const.DEFAULT_FALLBACK_FLOW_RATE),
                },
                config,
            )
        )

    # Register debug-only entity services
    debug = hass.data[DOMAIN].get("debug", False)
    if debug:
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
        platform.async_register_entity_service(  # type: ignore
            "apply_adaptive_ke",
            {},
            "async_apply_adaptive_ke",
        )
        platform.async_register_entity_service(  # type: ignore
            "clear_learning",
            {},
            "async_clear_learning",
        )
        platform.async_register_entity_service(  # type: ignore
            "rollback_pid",
            {},
            "async_rollback_pid",
        )

    # Register public entity services (always available)
    platform.async_register_entity_service(  # type: ignore
        "delete_pid_history",
        {
            vol.Required("indices"): [cv.positive_int],
            vol.Optional("mode", default="heat"): vol.In(["heat", "cool"]),
        },
        "async_delete_pid_history",
    )
    platform.async_register_entity_service(  # type: ignore
        "restore_pid_history",
        {
            vol.Required("index"): cv.positive_int,
            vol.Optional("mode", default="heat"): vol.In(["heat", "cool"]),
        },
        "async_restore_pid_history",
    )
