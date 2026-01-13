"""The adaptive_thermostat component."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

# These imports are only needed when running in Home Assistant
try:
    import voluptuous as vol
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

from .const import DOMAIN
from .services import (
    SERVICE_RUN_LEARNING,
    SERVICE_HEALTH_CHECK,
    SERVICE_WEEKLY_REPORT,
    SERVICE_COST_REPORT,
    SERVICE_SET_VACATION_MODE,
    SERVICE_ENERGY_STATS,
    SERVICE_PID_RECOMMENDATIONS,
    async_register_services,
    async_scheduled_health_check,
    async_scheduled_weekly_report,
    async_daily_learning,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "switch", "number"]


async def async_send_notification(
    hass: HomeAssistant,
    notify_service: str | None,
    title: str,
    message: str,
) -> bool:
    """Send a notification via the configured notification service.

    Args:
        hass: Home Assistant instance
        notify_service: The notification service name (e.g., "mobile_app_phone" or "notify.mobile_app_phone")
        title: Notification title
        message: Notification message

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
        await hass.services.async_call(
            "notify",
            service_name,
            {
                "title": title,
                "message": message,
            },
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

    # Import modules that require Home Assistant
    from .const import (
        CONF_NOTIFY_SERVICE,
        CONF_PERSISTENT_NOTIFICATION,
        CONF_ENERGY_METER_ENTITY,
        CONF_ENERGY_COST_ENTITY,
        CONF_MAIN_HEATER_SWITCH,
        CONF_MAIN_COOLER_SWITCH,
        CONF_SOURCE_STARTUP_DELAY,
        CONF_SYNC_MODES,
        CONF_LEARNING_WINDOW_DAYS,
        CONF_WEATHER_ENTITY,
        CONF_HOUSE_ENERGY_RATING,
        CONF_WINDOW_RATING,
        CONF_SUPPLY_TEMP_SENSOR,
        CONF_RETURN_TEMP_SENSOR,
        CONF_FLOW_RATE_SENSOR,
        CONF_VOLUME_METER_ENTITY,
        CONF_FALLBACK_FLOW_RATE,
        DEFAULT_VACATION_TARGET_TEMP,
        DEFAULT_SOURCE_STARTUP_DELAY,
        DEFAULT_SYNC_MODES,
        DEFAULT_LEARNING_WINDOW_DAYS,
        DEFAULT_FALLBACK_FLOW_RATE,
        DEFAULT_WINDOW_RATING,
        DEFAULT_PERSISTENT_NOTIFICATION,
    )
    from .coordinator import (
        AdaptiveThermostatCoordinator,
        CentralController,
        ModeSync,
        ZoneLinker,
    )
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
    hass.data[DOMAIN]["energy_meter_entity"] = energy_meter
    hass.data[DOMAIN]["energy_cost_entity"] = energy_cost

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

    # Zone linking for thermally connected zones
    zone_linker = ZoneLinker(hass=hass, coordinator=coordinator)
    hass.data[DOMAIN]["zone_linker"] = zone_linker
    _LOGGER.debug("Zone linker initialized")

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

    # Schedule daily adaptive learning at 3:00 AM
    async def _async_daily_learning_callback(_now) -> None:
        """Wrapper for scheduled daily learning."""
        learning_window = hass.data[DOMAIN].get("learning_window_days", DEFAULT_LEARNING_WINDOW_DAYS)
        await async_daily_learning(hass, coordinator, learning_window, _now)

    async_track_time_change(hass, _async_daily_learning_callback, hour=3, minute=0, second=0)
    _LOGGER.debug("Scheduled daily adaptive learning at 3:00 AM")

    # Schedule health check every 6 hours (at 0:00, 6:00, 12:00, 18:00)
    async def _async_scheduled_health_check_callback(_now) -> None:
        """Wrapper for scheduled health check."""
        await async_scheduled_health_check(
            hass, coordinator, notify_service, persistent_notification,
            async_send_notification, async_send_persistent_notification, _now,
        )

    for hour in [0, 6, 12, 18]:
        async_track_time_change(hass, _async_scheduled_health_check_callback, hour=hour, minute=0, second=0)
    _LOGGER.debug("Scheduled health checks every 6 hours")

    # Schedule weekly report on Sunday at 9:00 AM
    async def _async_scheduled_weekly_report_callback(_now) -> None:
        """Wrapper for scheduled weekly report."""
        await async_scheduled_weekly_report(
            hass, coordinator, notify_service, persistent_notification,
            async_send_notification, async_send_persistent_notification, _now,
        )

    async_track_time_change(hass, _async_scheduled_weekly_report_callback, hour=9, minute=0, second=0)
    _LOGGER.debug("Scheduled weekly report on Sundays at 9:00 AM")

    _LOGGER.info("Adaptive Thermostat integration setup complete")
    return True
