"""The adaptive_thermostat component."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

# Import voluptuous separately as it's a standalone dependency
try:
    import voluptuous as vol
except ImportError:
    vol = None

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers.typing import ConfigType
    from homeassistant.helpers.event import async_track_time_change
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    # Provide stubs for testing
    HomeAssistant = Any
    ServiceCall = Any
    ConfigType = Any
    cv = None

from .const import (
    DOMAIN,
    CONF_DEBUG,
    CONF_NOTIFY_SERVICE,
    CONF_PERSISTENT_NOTIFICATION,
    CONF_ENERGY_METER_ENTITY,
    CONF_ENERGY_COST_ENTITY,
    CONF_SUPPLY_TEMPERATURE,
    SUPPLY_TEMP_MIN,
    SUPPLY_TEMP_MAX,
    CONF_MAIN_HEATER_SWITCH,
    CONF_MAIN_COOLER_SWITCH,
    CONF_SOURCE_STARTUP_DELAY,
    CONF_SYNC_MODES,
    CONF_LEARNING_WINDOW_DAYS,
    CONF_WEATHER_ENTITY,
    CONF_OUTDOOR_SENSOR,
    CONF_WIND_SPEED_SENSOR,
    CONF_HOUSE_ENERGY_RATING,
    CONF_WINDOW_RATING,
    CONF_SUPPLY_TEMP_SENSOR,
    CONF_RETURN_TEMP_SENSOR,
    CONF_FLOW_RATE_SENSOR,
    CONF_VOLUME_METER_ENTITY,
    CONF_FALLBACK_FLOW_RATE,
    CONF_AWAY_TEMP,
    CONF_ECO_TEMP,
    CONF_BOOST_TEMP,
    CONF_COMFORT_TEMP,
    CONF_HOME_TEMP,
    CONF_ACTIVITY_TEMP,
    CONF_PRESET_SYNC_MODE,
    CONF_BOOST_PID_OFF,
    CONF_THERMAL_GROUPS,
    CONF_MANIFOLDS,
    CONF_PIPE_VOLUME,
    CONF_FLOW_PER_LOOP,
    # Climate settings (domain-level defaults with per-entity override)
    CONF_MIN_TEMP,
    CONF_MAX_TEMP,
    CONF_TARGET_TEMP,
    CONF_TARGET_TEMP_STEP,
    CONF_HOT_TOLERANCE,
    CONF_COLD_TOLERANCE,
    CONF_PRECISION,
    CONF_PWM,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_OFF_CYCLE_DURATION,
    DEFAULT_DEBUG,
    DEFAULT_SOURCE_STARTUP_DELAY,
    DEFAULT_SYNC_MODES,
    DEFAULT_LEARNING_WINDOW_DAYS,
    DEFAULT_FALLBACK_FLOW_RATE,
    DEFAULT_FLOW_PER_LOOP,
    DEFAULT_WINDOW_RATING,
    DEFAULT_PERSISTENT_NOTIFICATION,
    DEFAULT_VACATION_TARGET_TEMP,
    DEFAULT_PRESET_SYNC_MODE,
    DEFAULT_MIN_TEMP,
    DEFAULT_MAX_TEMP,
    DEFAULT_TARGET_TEMP,
    DEFAULT_TARGET_TEMP_STEP,
    DEFAULT_TOLERANCE,
    DEFAULT_PRECISION,
    VALID_ENERGY_RATINGS,
)
from .services import (
    SERVICE_RUN_LEARNING,
    SERVICE_HEALTH_CHECK,
    SERVICE_WEEKLY_REPORT,
    SERVICE_COST_REPORT,
    SERVICE_SET_VACATION_MODE,
    SERVICE_ENERGY_STATS,
    SERVICE_PID_RECOMMENDATIONS,
    async_register_services,
    async_unregister_services,
    async_scheduled_health_check,
    async_scheduled_weekly_report,
    async_daily_learning,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "switch", "number"]


def valid_notify_service(value: Any) -> str:
    """Validate notify service format.

    Accepts formats:
    - "service_name" (will be called as notify.service_name)
    - "notify.service_name" (explicit domain)

    Args:
        value: The config value to validate

    Returns:
        The validated service name string

    Raises:
        vol.Invalid: If the value is not a valid notify service format
    """
    if not isinstance(value, str):
        raise vol.Invalid(
            f"notify_service must be a string, got {type(value).__name__}"
        )

    value = value.strip()
    if not value:
        raise vol.Invalid("notify_service cannot be empty")

    # Allow "service_name" or "notify.service_name" format
    # Service names must start with a letter and contain only lowercase letters,
    # numbers, and underscores
    pattern = r"^(notify\.)?[a-z][a-z0-9_]*$"
    if not re.match(pattern, value):
        raise vol.Invalid(
            f"Invalid notify_service format '{value}'. "
            "Expected format: 'service_name' or 'notify.service_name' "
            "(must start with a letter, contain only lowercase letters, numbers, and underscores). "
            "Example: 'mobile_app_phone' or 'notify.mobile_app_phone'"
        )
    return value


# Domain configuration schema
# This validates the configuration under the adaptive_thermostat: key
if HAS_HOMEASSISTANT:
    # Thermal group schema
    THERMAL_GROUP_SCHEMA = vol.Schema({
        vol.Required("name"): cv.string,
        vol.Required("zones"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("type", default="open_plan"): vol.In(["open_plan"]),
        vol.Optional("leader"): cv.string,
        vol.Optional("receives_from"): cv.string,
        vol.Optional("transfer_factor", default=0.0): vol.All(
            vol.Coerce(float),
            vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional("delay_minutes", default=0): vol.All(
            vol.Coerce(int),
            vol.Range(min=0)
        ),
    })

    # Manifold schema
    MANIFOLD_SCHEMA = vol.Schema({
        vol.Required("name"): cv.string,
        vol.Required("zones"): vol.All(cv.ensure_list, [cv.entity_id], vol.Length(min=1)),
        vol.Required(CONF_PIPE_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
        vol.Optional(CONF_FLOW_PER_LOOP, default=DEFAULT_FLOW_PER_LOOP): vol.All(
            vol.Coerce(float), vol.Range(min=0.1)
        ),
    })

    CONFIG_SCHEMA = vol.Schema(
        {
            DOMAIN: vol.Schema({
                # Notification settings
                vol.Optional(CONF_NOTIFY_SERVICE): valid_notify_service,
                vol.Optional(
                    CONF_PERSISTENT_NOTIFICATION,
                    default=DEFAULT_PERSISTENT_NOTIFICATION
                ): cv.boolean,

                # Debug mode
                vol.Optional(CONF_DEBUG, default=DEFAULT_DEBUG): cv.boolean,

                # Energy tracking
                vol.Optional(CONF_ENERGY_METER_ENTITY): cv.entity_id,
                vol.Optional(CONF_ENERGY_COST_ENTITY): cv.entity_id,

                # Supply temperature for physics-based PID scaling
                vol.Optional(CONF_SUPPLY_TEMPERATURE): vol.All(
                    vol.Coerce(float),
                    vol.Range(
                        min=SUPPLY_TEMP_MIN,
                        max=SUPPLY_TEMP_MAX,
                        msg=f"supply_temperature must be between {SUPPLY_TEMP_MIN} and {SUPPLY_TEMP_MAX}°C"
                    )
                ),

                # Central heat source control
                vol.Optional(CONF_MAIN_HEATER_SWITCH): cv.entity_ids,
                vol.Optional(CONF_MAIN_COOLER_SWITCH): cv.entity_ids,
                vol.Optional(
                    CONF_SOURCE_STARTUP_DELAY,
                    default=DEFAULT_SOURCE_STARTUP_DELAY
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=0,
                        max=300,
                        msg="source_startup_delay must be between 0 and 300 seconds"
                    )
                ),

                # Mode synchronization
                vol.Optional(
                    CONF_SYNC_MODES,
                    default=DEFAULT_SYNC_MODES
                ): cv.boolean,

                # Learning configuration
                vol.Optional(
                    CONF_LEARNING_WINDOW_DAYS,
                    default=DEFAULT_LEARNING_WINDOW_DAYS
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=1,
                        max=30,
                        msg="learning_window_days must be between 1 and 30 days"
                    )
                ),

                # Weather and physics
                vol.Optional(CONF_WEATHER_ENTITY): cv.entity_id,
                vol.Optional(CONF_OUTDOOR_SENSOR): cv.entity_id,
                vol.Optional(CONF_WIND_SPEED_SENSOR): cv.entity_id,
                vol.Optional(CONF_HOUSE_ENERGY_RATING): vol.In(
                    VALID_ENERGY_RATINGS,
                    msg=f"house_energy_rating must be one of: {', '.join(VALID_ENERGY_RATINGS)}"
                ),
                vol.Optional(
                    CONF_WINDOW_RATING,
                    default=DEFAULT_WINDOW_RATING
                ): cv.string,

                # Heat output sensors
                vol.Optional(CONF_SUPPLY_TEMP_SENSOR): cv.entity_id,
                vol.Optional(CONF_RETURN_TEMP_SENSOR): cv.entity_id,
                vol.Optional(CONF_FLOW_RATE_SENSOR): cv.entity_id,
                vol.Optional(CONF_VOLUME_METER_ENTITY): cv.entity_id,
                vol.Optional(
                    CONF_FALLBACK_FLOW_RATE,
                    default=DEFAULT_FALLBACK_FLOW_RATE
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(
                        min=0.01,
                        max=10.0,
                        msg="fallback_flow_rate must be between 0.01 and 10.0 L/s"
                    )
                ),

                # Preset temperatures
                vol.Optional(CONF_AWAY_TEMP): vol.Coerce(float),
                vol.Optional(CONF_ECO_TEMP): vol.Coerce(float),
                vol.Optional(CONF_BOOST_TEMP): vol.Coerce(float),
                vol.Optional(CONF_COMFORT_TEMP): vol.Coerce(float),
                vol.Optional(CONF_HOME_TEMP): vol.Coerce(float),
                vol.Optional(CONF_ACTIVITY_TEMP): vol.Coerce(float),
                vol.Optional(
                    CONF_PRESET_SYNC_MODE,
                    default=DEFAULT_PRESET_SYNC_MODE
                ): vol.In(['sync', 'none']),
                vol.Optional(CONF_BOOST_PID_OFF, default=False): cv.boolean,

                # Climate settings (domain-level defaults, can be overridden per-entity)
                vol.Optional(CONF_MIN_TEMP): vol.Coerce(float),
                vol.Optional(CONF_MAX_TEMP): vol.Coerce(float),
                vol.Optional(CONF_TARGET_TEMP): vol.Coerce(float),
                vol.Optional(CONF_TARGET_TEMP_STEP): vol.In([0.1, 0.5, 1.0]),
                vol.Optional(CONF_HOT_TOLERANCE): vol.Coerce(float),
                vol.Optional(CONF_COLD_TOLERANCE): vol.Coerce(float),
                vol.Optional(CONF_PRECISION): vol.In([0.1, 0.5, 1.0]),
                vol.Optional(CONF_PWM): vol.All(cv.time_period, cv.positive_timedelta),
                vol.Optional(CONF_MIN_CYCLE_DURATION): vol.All(cv.time_period, cv.positive_timedelta),
                vol.Optional(CONF_MIN_OFF_CYCLE_DURATION): vol.All(cv.time_period, cv.positive_timedelta),

                # Thermal groups for static multi-zone coordination
                vol.Optional(CONF_THERMAL_GROUPS): vol.All(
                    cv.ensure_list,
                    [THERMAL_GROUP_SCHEMA]
                ),

                # Manifolds for hydraulic transport delay tracking
                vol.Optional(CONF_MANIFOLDS): vol.All(cv.ensure_list, [MANIFOLD_SCHEMA]),
            })
        },
        extra=vol.ALLOW_EXTRA,  # Allow other domains in config
    )
else:
    # Provide stub for testing without Home Assistant
    CONFIG_SCHEMA = None
    THERMAL_GROUP_SCHEMA = None
    MANIFOLD_SCHEMA = None


async def async_send_notification(
    hass: HomeAssistant,
    notify_service: str | None,
    title: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> bool:
    """Send a notification via the configured notification service.

    Args:
        hass: Home Assistant instance
        notify_service: The notification service name (e.g., "mobile_app_phone" or "notify.mobile_app_phone")
        title: Notification title
        message: Notification message
        data: Optional additional data (e.g., image attachments)

    Returns:
        True if notification was sent successfully, False otherwise
    """
    if not notify_service:
        _LOGGER.debug("No notification service configured, skipping notification")
        return False

    # Extract service name - handle both "notify.service_name" and "service_name" formats
    if "." in notify_service:
        domain, service_name = notify_service.split(".", 1)
        if domain != "notify":
            _LOGGER.warning(
                "Invalid notification service format '%s', expected 'notify.service_name' or 'service_name'",
                notify_service,
            )
            return False
    else:
        service_name = notify_service

    # Check if service exists
    if not hass.services.has_service("notify", service_name):
        _LOGGER.warning(
            "Notification service 'notify.%s' is not available. "
            "Check your Home Assistant notification configuration.",
            service_name,
        )
        return False

    try:
        service_data = {
            "title": title,
            "message": message,
        }
        if data:
            service_data["data"] = data

        await hass.services.async_call(
            "notify",
            service_name,
            service_data,
            blocking=True,
        )
        _LOGGER.debug("Notification sent successfully via notify.%s", service_name)
        return True
    except Exception as e:
        _LOGGER.error(
            "Failed to send notification via notify.%s: %s",
            service_name,
            e,
        )
        return False


async def async_send_persistent_notification(
    hass: HomeAssistant,
    notification_id: str,
    title: str,
    message: str,
) -> bool:
    """Send a persistent notification that stays in HA until dismissed.

    Args:
        hass: Home Assistant instance
        notification_id: Unique ID for the notification (allows updating/dismissing)
        title: Notification title
        message: Notification message (can be longer/detailed)

    Returns:
        True if notification was created successfully, False otherwise
    """
    try:
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "notification_id": notification_id,
                "title": title,
                "message": message,
            },
            blocking=True,
        )
        _LOGGER.debug("Persistent notification created: %s", notification_id)
        return True
    except Exception as e:
        _LOGGER.error("Failed to create persistent notification: %s", e)
        return False


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Adaptive Thermostat integration."""
    if not HAS_HOMEASSISTANT:
        return False

    # Import coordinator modules
    from .coordinator import (
        AdaptiveThermostatCoordinator,
        ModeSync,
    )
    from .central_controller import CentralController
    from .adaptive.vacation import VacationMode

    # Service schemas
    VACATION_MODE_SCHEMA = vol.Schema({
        vol.Required("enabled"): cv.boolean,
        vol.Optional("target_temp", default=DEFAULT_VACATION_TARGET_TEMP): vol.Coerce(float),
    })

    COST_REPORT_SCHEMA = vol.Schema({
        vol.Optional("period", default="weekly"): vol.In(["daily", "weekly", "monthly"]),
    })

    # Initialize domain data storage
    hass.data.setdefault(DOMAIN, {})

    # Create www directory for chart images
    from pathlib import Path
    www_dir = Path(hass.config.path("www")) / "adaptive_thermostat"
    try:
        www_dir.mkdir(parents=True, exist_ok=True)
        _LOGGER.debug("Chart directory ready: %s", www_dir)
    except (OSError, IOError) as e:
        _LOGGER.warning("Could not create chart directory: %s", e)

    # Create coordinator
    coordinator = AdaptiveThermostatCoordinator(hass)
    hass.data[DOMAIN]["coordinator"] = coordinator

    # Create vacation mode handler
    vacation_mode = VacationMode(hass, coordinator)
    hass.data[DOMAIN]["vacation_mode"] = vacation_mode

    # Get configuration options from domain config
    domain_config = config.get(DOMAIN, {})

    # Notification and energy tracking
    notify_service = domain_config.get(CONF_NOTIFY_SERVICE)
    persistent_notification = domain_config.get(
        CONF_PERSISTENT_NOTIFICATION, DEFAULT_PERSISTENT_NOTIFICATION
    )
    energy_meter = domain_config.get(CONF_ENERGY_METER_ENTITY)
    energy_cost = domain_config.get(CONF_ENERGY_COST_ENTITY)

    hass.data[DOMAIN]["notify_service"] = notify_service
    hass.data[DOMAIN]["persistent_notification"] = persistent_notification
    hass.data[DOMAIN]["debug"] = domain_config.get(CONF_DEBUG, DEFAULT_DEBUG)
    hass.data[DOMAIN]["energy_meter_entity"] = energy_meter
    hass.data[DOMAIN]["energy_cost_entity"] = energy_cost

    # Supply temperature for physics-based PID scaling
    supply_temperature = domain_config.get(CONF_SUPPLY_TEMPERATURE)
    hass.data[DOMAIN]["supply_temperature"] = supply_temperature
    if supply_temperature:
        _LOGGER.info("Supply temperature configured: %.1f°C", supply_temperature)

    # Central heat source control
    main_heater_switch = domain_config.get(CONF_MAIN_HEATER_SWITCH)
    main_cooler_switch = domain_config.get(CONF_MAIN_COOLER_SWITCH)
    source_startup_delay = domain_config.get(
        CONF_SOURCE_STARTUP_DELAY, DEFAULT_SOURCE_STARTUP_DELAY
    )

    hass.data[DOMAIN]["main_heater_switch"] = main_heater_switch
    hass.data[DOMAIN]["main_cooler_switch"] = main_cooler_switch
    hass.data[DOMAIN]["source_startup_delay"] = source_startup_delay

    # Create central controller if any main switch is configured
    central_controller = None
    if main_heater_switch or main_cooler_switch:
        central_controller = CentralController(
            hass=hass,
            coordinator=coordinator,
            main_heater_switch=main_heater_switch,
            main_cooler_switch=main_cooler_switch,
            startup_delay_seconds=source_startup_delay,
        )
        hass.data[DOMAIN]["central_controller"] = central_controller
        coordinator.set_central_controller(central_controller)
        _LOGGER.info(
            "Central controller configured: heater=%s, cooler=%s, delay=%ds",
            main_heater_switch,
            main_cooler_switch,
            source_startup_delay,
        )

    # Mode synchronization
    sync_modes = domain_config.get(CONF_SYNC_MODES, DEFAULT_SYNC_MODES)
    hass.data[DOMAIN]["sync_modes"] = sync_modes

    mode_sync = None
    if sync_modes:
        mode_sync = ModeSync(hass=hass, coordinator=coordinator)
        hass.data[DOMAIN]["mode_sync"] = mode_sync
        _LOGGER.info("Mode synchronization enabled")

    # Thermal groups for static multi-zone coordination
    thermal_groups_config = domain_config.get(CONF_THERMAL_GROUPS)
    thermal_group_manager = None
    if thermal_groups_config:
        try:
            from .adaptive.thermal_groups import ThermalGroupManager, validate_thermal_groups_config
            # Validate config
            validate_thermal_groups_config(thermal_groups_config)
            # Create manager
            thermal_group_manager = ThermalGroupManager(hass, thermal_groups_config)
            hass.data[DOMAIN]["thermal_group_manager"] = thermal_group_manager
            coordinator.set_thermal_group_manager(thermal_group_manager)
            _LOGGER.info("Thermal groups enabled with %d groups", len(thermal_groups_config))
        except (ValueError, ImportError) as e:
            _LOGGER.error("Failed to initialize thermal groups: %s", e)
            # Don't fail setup, just disable thermal groups
            hass.data[DOMAIN]["thermal_group_manager"] = None

    # Manifold registry for hydraulic transport delay tracking
    manifolds_config = domain_config.get(CONF_MANIFOLDS)
    manifold_registry = None
    if manifolds_config:
        try:
            from .adaptive.manifold_registry import ManifoldRegistry, Manifold
            # Create Manifold objects from config
            manifolds = [
                Manifold(
                    name=m["name"],
                    zones=m["zones"],
                    pipe_volume=m[CONF_PIPE_VOLUME],
                    flow_per_loop=m[CONF_FLOW_PER_LOOP],
                )
                for m in manifolds_config
            ]
            # Create registry
            manifold_registry = ManifoldRegistry(manifolds)
            hass.data[DOMAIN]["manifold_registry"] = manifold_registry
            _LOGGER.info("Manifold registry enabled with %d manifolds", len(manifolds))
        except (ValueError, ImportError) as e:
            _LOGGER.error("Failed to initialize manifold registry: %s", e)
            # Don't fail setup, just disable manifold registry
            hass.data[DOMAIN]["manifold_registry"] = None

    # Learning configuration
    learning_window_days = domain_config.get(
        CONF_LEARNING_WINDOW_DAYS, DEFAULT_LEARNING_WINDOW_DAYS
    )
    hass.data[DOMAIN]["learning_window_days"] = learning_window_days

    # Weather entity for solar gain prediction
    weather_entity = domain_config.get(CONF_WEATHER_ENTITY)
    hass.data[DOMAIN]["weather_entity"] = weather_entity
    if weather_entity:
        _LOGGER.info("Weather entity configured: %s", weather_entity)

    # Outdoor temperature sensor for Ke learning (weather compensation)
    outdoor_sensor = domain_config.get(CONF_OUTDOOR_SENSOR)
    hass.data[DOMAIN]["outdoor_sensor"] = outdoor_sensor
    if outdoor_sensor:
        _LOGGER.info("Outdoor sensor configured: %s", outdoor_sensor)

    # House energy rating for physics-based initialization
    house_energy_rating = domain_config.get(CONF_HOUSE_ENERGY_RATING)
    hass.data[DOMAIN]["house_energy_rating"] = house_energy_rating
    if house_energy_rating:
        _LOGGER.info("House energy rating: %s", house_energy_rating)

    # Default window rating for physics-based initialization (can be overridden per zone)
    window_rating = domain_config.get(CONF_WINDOW_RATING, DEFAULT_WINDOW_RATING)
    hass.data[DOMAIN]["window_rating"] = window_rating
    _LOGGER.info("Default window rating: %s", window_rating)

    # Heat output sensors
    supply_temp_sensor = domain_config.get(CONF_SUPPLY_TEMP_SENSOR)
    return_temp_sensor = domain_config.get(CONF_RETURN_TEMP_SENSOR)
    flow_rate_sensor = domain_config.get(CONF_FLOW_RATE_SENSOR)
    volume_meter_entity = domain_config.get(CONF_VOLUME_METER_ENTITY)
    fallback_flow_rate = domain_config.get(
        CONF_FALLBACK_FLOW_RATE, DEFAULT_FALLBACK_FLOW_RATE
    )

    hass.data[DOMAIN]["supply_temp_sensor"] = supply_temp_sensor
    hass.data[DOMAIN]["return_temp_sensor"] = return_temp_sensor
    hass.data[DOMAIN]["flow_rate_sensor"] = flow_rate_sensor
    hass.data[DOMAIN]["volume_meter_entity"] = volume_meter_entity
    hass.data[DOMAIN]["fallback_flow_rate"] = fallback_flow_rate

    # Preset temperatures
    hass.data[DOMAIN]["away_temp"] = domain_config.get(CONF_AWAY_TEMP)
    hass.data[DOMAIN]["eco_temp"] = domain_config.get(CONF_ECO_TEMP)
    hass.data[DOMAIN]["boost_temp"] = domain_config.get(CONF_BOOST_TEMP)
    hass.data[DOMAIN]["comfort_temp"] = domain_config.get(CONF_COMFORT_TEMP)
    hass.data[DOMAIN]["home_temp"] = domain_config.get(CONF_HOME_TEMP)
    hass.data[DOMAIN]["activity_temp"] = domain_config.get(CONF_ACTIVITY_TEMP)
    hass.data[DOMAIN]["preset_sync_mode"] = domain_config.get(
        CONF_PRESET_SYNC_MODE, DEFAULT_PRESET_SYNC_MODE
    )
    hass.data[DOMAIN]["boost_pid_off"] = domain_config.get(CONF_BOOST_PID_OFF, False)

    # Climate settings (domain-level defaults, can be overridden per-entity)
    hass.data[DOMAIN]["min_temp"] = domain_config.get(CONF_MIN_TEMP)
    hass.data[DOMAIN]["max_temp"] = domain_config.get(CONF_MAX_TEMP)
    hass.data[DOMAIN]["target_temp"] = domain_config.get(CONF_TARGET_TEMP)
    hass.data[DOMAIN]["target_temp_step"] = domain_config.get(CONF_TARGET_TEMP_STEP)
    hass.data[DOMAIN]["hot_tolerance"] = domain_config.get(CONF_HOT_TOLERANCE)
    hass.data[DOMAIN]["cold_tolerance"] = domain_config.get(CONF_COLD_TOLERANCE)
    hass.data[DOMAIN]["precision"] = domain_config.get(CONF_PRECISION)
    hass.data[DOMAIN]["pwm"] = domain_config.get(CONF_PWM)
    hass.data[DOMAIN]["min_cycle_duration"] = domain_config.get(CONF_MIN_CYCLE_DURATION)
    hass.data[DOMAIN]["min_off_cycle_duration"] = domain_config.get(CONF_MIN_OFF_CYCLE_DURATION)

    if supply_temp_sensor and return_temp_sensor:
        _LOGGER.info(
            "Heat output sensors configured: supply=%s, return=%s",
            supply_temp_sensor,
            return_temp_sensor,
        )

    # Register all services using the services module
    async_register_services(
        hass=hass,
        coordinator=coordinator,
        vacation_mode=vacation_mode,
        notify_service=notify_service,
        persistent_notification=persistent_notification,
        async_send_notification_func=async_send_notification,
        async_send_persistent_notification_func=async_send_persistent_notification,
        vacation_schema=VACATION_MODE_SCHEMA,
        cost_report_schema=COST_REPORT_SCHEMA,
        default_vacation_target_temp=DEFAULT_VACATION_TARGET_TEMP,
    )

    # Store cancel callbacks for scheduled tasks (needed for unload)
    unsub_callbacks = []

    # Schedule daily adaptive learning at 3:00 AM
    async def _async_daily_learning_callback(_now) -> None:
        """Wrapper for scheduled daily learning."""
        learning_window = hass.data[DOMAIN].get("learning_window_days", DEFAULT_LEARNING_WINDOW_DAYS)
        await async_daily_learning(hass, coordinator, learning_window, _now)

    unsub_callbacks.append(
        async_track_time_change(hass, _async_daily_learning_callback, hour=3, minute=0, second=0)
    )
    _LOGGER.debug("Scheduled daily adaptive learning at 3:00 AM")

    # Schedule health check every 6 hours (at 0:00, 6:00, 12:00, 18:00)
    async def _async_scheduled_health_check_callback(_now) -> None:
        """Wrapper for scheduled health check."""
        await async_scheduled_health_check(
            hass, coordinator, notify_service, persistent_notification,
            async_send_notification, async_send_persistent_notification, _now,
        )

    for hour in [0, 6, 12, 18]:
        unsub_callbacks.append(
            async_track_time_change(hass, _async_scheduled_health_check_callback, hour=hour, minute=0, second=0)
        )
    _LOGGER.debug("Scheduled health checks every 6 hours")

    # Schedule weekly report on Sunday at 9:00 AM
    async def _async_scheduled_weekly_report_callback(_now) -> None:
        """Wrapper for scheduled weekly report."""
        await async_scheduled_weekly_report(
            hass, coordinator, notify_service, persistent_notification,
            async_send_notification, async_send_persistent_notification, _now,
        )

    unsub_callbacks.append(
        async_track_time_change(hass, _async_scheduled_weekly_report_callback, hour=9, minute=0, second=0)
    )
    _LOGGER.debug("Scheduled weekly report on Sundays at 9:00 AM")

    # Store unsubscribe callbacks for cleanup during unload
    hass.data[DOMAIN]["unsub_callbacks"] = unsub_callbacks

    _LOGGER.info("Adaptive Thermostat integration setup complete")
    return True


async def async_unload(hass: HomeAssistant) -> bool:
    """Unload the Adaptive Thermostat integration.

    This function handles cleanup when the integration is being unloaded or reloaded:
    - Cancels all scheduled tasks (health checks, weekly reports, daily learning)
    - Unregisters all services
    - Cleans up coordinator and other component references
    - Removes domain data from hass.data

    Args:
        hass: Home Assistant instance

    Returns:
        True if unload was successful, False otherwise
    """
    if DOMAIN not in hass.data:
        _LOGGER.debug("Domain data not found, nothing to unload")
        return True

    _LOGGER.info("Unloading Adaptive Thermostat integration")

    # Cancel all scheduled tasks
    unsub_callbacks = hass.data[DOMAIN].get("unsub_callbacks", [])
    for unsub in unsub_callbacks:
        if unsub is not None:
            try:
                unsub()
            except Exception as e:
                _LOGGER.warning("Error cancelling scheduled task: %s", e)
    _LOGGER.debug("Cancelled %d scheduled tasks", len(unsub_callbacks))

    # Unregister all services
    async_unregister_services(hass)

    # Clean up central controller if it exists
    central_controller = hass.data[DOMAIN].get("central_controller")
    if central_controller is not None:
        # Cancel all pending async tasks
        await central_controller.async_cleanup()
        # Clear reference to coordinator
        coordinator = hass.data[DOMAIN].get("coordinator")
        if coordinator is not None:
            coordinator.set_central_controller(None)
        _LOGGER.debug("Cleaned up central controller")

    # Clean up mode sync if it exists
    mode_sync = hass.data[DOMAIN].get("mode_sync")
    if mode_sync is not None:
        _LOGGER.debug("Cleaned up mode sync")

    # Clean up vacation mode if it exists
    vacation_mode = hass.data[DOMAIN].get("vacation_mode")
    if vacation_mode is not None:
        _LOGGER.debug("Cleaned up vacation mode")

    # Remove all domain data
    hass.data.pop(DOMAIN, None)
    _LOGGER.info("Adaptive Thermostat integration unloaded successfully")

    return True
