"""The adaptive_climate component."""

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

from .manifold_config import MANIFOLD_SCHEMA, validate_manifolds_and_pump_flow

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
    CONF_CHRONIC_APPROACH_HISTORIC_SCAN,
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
    # Auto mode switching
    CONF_AUTO_MODE_SWITCHING,
    CONF_AUTO_MODE_THRESHOLD,
    CONF_MIN_SWITCH_INTERVAL,
    CONF_FORECAST_DAYS,
    CONF_SEASON_THRESHOLDS,
    CONF_WINTER_BELOW,
    CONF_SUMMER_ABOVE,
    DEFAULT_AUTO_MODE_THRESHOLD,
    DEFAULT_MIN_SWITCH_INTERVAL,
    DEFAULT_FORECAST_DAYS,
    DEFAULT_WINTER_BELOW,
    DEFAULT_SUMMER_ABOVE,
    # Cooling supply temperature
    CONF_COOLING_SUPPLY_TEMP,
    CONF_COOLING_SUPPLY_MARGIN,
    DEFAULT_COOLING_SUPPLY_MARGIN,
    # Water temperature control
    CONF_WATER_TEMP_CONTROL,
    CONF_WATER_TEMP_COOLING,
    CONF_WATER_TEMP_HEATING,
    CONF_WATER_TEMP_TARGET,
    CONF_WATER_TEMP_TARGET_ENTITY,
    CONF_WATER_TEMP_MIN_SUPPLY_TEMP,
    CONF_WATER_TEMP_DEW_POINT_MARGIN,
    CONF_WATER_TEMP_FALLBACK_HUMIDITY,
    CONF_WATER_TEMP_RAMP_START,
    CONF_WATER_TEMP_RAMP_RATE,
    CONF_WATER_TEMP_EXTRA_SENSORS,
    CONF_WATER_TEMP_EXTRA_HUMIDITY,
    CONF_WATER_TEMP_EXTRA_TEMPERATURE,
    CONF_WATER_TEMP_IDLE_DAYS,
    CONF_WATER_TEMP_MIN_WRITE_INTERVAL,
    CONF_WATER_TEMP_CONDENSATION_SENSOR,
    DEFAULT_WATER_TEMP_IDLE_DAYS,
    DEFAULT_WATER_TEMP_MIN_WRITE_INTERVAL,
    DEFAULT_WATER_TEMP_MIN_SUPPLY_TEMP,
    DEFAULT_WATER_TEMP_DEW_POINT_MARGIN,
    DEFAULT_WATER_TEMP_FALLBACK_HUMIDITY,
    DEFAULT_WATER_TEMP_COOLING_RAMP_START,
    DEFAULT_WATER_TEMP_COOLING_RAMP_RATE,
    DEFAULT_WATER_TEMP_HEATING_RAMP_START,
    DEFAULT_WATER_TEMP_HEATING_RAMP_RATE,
    WATER_TEMP_HEATING_TARGET_MIN,
    WATER_TEMP_HEATING_TARGET_MAX,
    # Climate settings (domain-level defaults with per-entity override)
    CONF_MIN_TEMP,
    CONF_MAX_TEMP,
    CONF_TARGET_TEMP,
    CONF_TARGET_TEMP_STEP,
    CONF_HOT_TOLERANCE,
    CONF_COLD_TOLERANCE,
    CONF_PRECISION,
    CONF_PWM,
    CONF_MIN_OPEN_TIME,
    CONF_MIN_CLOSED_TIME,
    DEFAULT_DEBUG,
    DEFAULT_SOURCE_STARTUP_DELAY,
    DEFAULT_SYNC_MODES,
    DEFAULT_LEARNING_WINDOW_DAYS,
    DEFAULT_FALLBACK_FLOW_RATE,
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
    SERVICE_SET_VACATION_MODE,
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
        raise vol.Invalid(f"notify_service must be a string, got {type(value).__name__}")

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


def validate_water_temp_control(config: dict[str, Any]) -> dict[str, Any]:
    """Validate ``water_temp_control`` against sibling domain keys.

    Runs as a domain-level ``vol.All`` wrapper because these rules reach outside
    ``WATER_TEMP_CONTROL_SCHEMA``:

    1. ``heating.target`` falls back to the domain-level ``supply_temperature``.
       Neither present is a configuration error.
    2. ``cooling.target_entity`` and ``heating.target_entity`` must differ —
       writing both halves to one entity would make the two controllers fight.
    3. ``cooling.ramp_start`` must be >= ``cooling.min_supply_temp`` — a lower
       ramp_start would have the ramp park/interlock below the safety floor.
    4. ``heating.ramp_start`` must be <= the resolved heating target (explicit
       or the ``supply_temperature`` fallback) — a higher ramp_start makes the
       ramp a no-op (it would immediately clamp up to the target).

    Args:
        config: The validated ``adaptive_climate:`` domain config dict.

    Returns:
        The same dict, unchanged, when valid.

    Raises:
        vol.Invalid: When a rule above is violated.
    """
    water_temp = config.get(CONF_WATER_TEMP_CONTROL)
    if not water_temp:
        return config

    cooling = water_temp.get(CONF_WATER_TEMP_COOLING)
    heating = water_temp.get(CONF_WATER_TEMP_HEATING)

    resolved_heating_target: float | None = None
    if heating is not None:
        explicit_target = heating.get(CONF_WATER_TEMP_TARGET)
        if explicit_target is None:
            fallback = config.get(CONF_SUPPLY_TEMPERATURE)
            if fallback is None:
                raise vol.Invalid(
                    "water_temp_control.heating.target is required when the domain-level "
                    "supply_temperature is not configured"
                )
            if not WATER_TEMP_HEATING_TARGET_MIN <= float(fallback) <= WATER_TEMP_HEATING_TARGET_MAX:
                raise vol.Invalid(
                    f"supply_temperature ({fallback}°C) is outside the water_temp_control heating "
                    f"target range ({WATER_TEMP_HEATING_TARGET_MIN}-{WATER_TEMP_HEATING_TARGET_MAX}°C); "
                    "set water_temp_control.heating.target explicitly"
                )
            resolved_heating_target = float(fallback)
        else:
            resolved_heating_target = float(explicit_target)

    if cooling is not None:
        min_supply_temp = float(cooling.get(CONF_WATER_TEMP_MIN_SUPPLY_TEMP, DEFAULT_WATER_TEMP_MIN_SUPPLY_TEMP))
        cooling_ramp_start = float(cooling.get(CONF_WATER_TEMP_RAMP_START, DEFAULT_WATER_TEMP_COOLING_RAMP_START))
        if cooling_ramp_start < min_supply_temp:
            raise vol.Invalid(
                f"water_temp_control.cooling.ramp_start ({cooling_ramp_start}°C) must be >= "
                f"cooling.min_supply_temp ({min_supply_temp}°C); a lower ramp_start would park/"
                "interlock below the safety floor"
            )

    if heating is not None and resolved_heating_target is not None:
        heating_ramp_start = float(heating.get(CONF_WATER_TEMP_RAMP_START, DEFAULT_WATER_TEMP_HEATING_RAMP_START))
        if heating_ramp_start > resolved_heating_target:
            raise vol.Invalid(
                f"water_temp_control.heating.ramp_start ({heating_ramp_start}°C) must be <= the "
                f"resolved heating target ({resolved_heating_target}°C); a higher ramp_start makes "
                "the ramp a no-op"
            )

    if cooling is not None and heating is not None:
        cool_entity = cooling.get(CONF_WATER_TEMP_TARGET_ENTITY)
        heat_entity = heating.get(CONF_WATER_TEMP_TARGET_ENTITY)
        if cool_entity == heat_entity:
            raise vol.Invalid(
                "water_temp_control.cooling.target_entity and "
                "water_temp_control.heating.target_entity must differ "
                f"(both set to '{cool_entity}')"
            )

    return config


# Domain configuration schema
# This validates the configuration under the adaptive_climate: key
if HAS_HOMEASSISTANT:
    # Thermal group schema
    THERMAL_GROUP_SCHEMA = vol.Schema(
        {
            vol.Required("name"): cv.string,
            vol.Required("zones"): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional("type", default="open_plan"): vol.In(["open_plan"]),
            vol.Optional("leader"): cv.string,
            vol.Optional("receives_from"): cv.string,
            vol.Optional("transfer_factor", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional("delay_minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        }
    )

    # Auto mode switching schema
    AUTO_MODE_SWITCHING_SCHEMA = vol.Schema(
        {
            vol.Required("enabled"): cv.boolean,
            vol.Optional(CONF_AUTO_MODE_THRESHOLD, default=DEFAULT_AUTO_MODE_THRESHOLD): vol.Coerce(float),
            vol.Optional(CONF_MIN_SWITCH_INTERVAL, default=DEFAULT_MIN_SWITCH_INTERVAL): cv.positive_int,
            # M04: No default here — if the user only sets forecast_hours the manager's
            # `or` chain must reach it.  The default (DEFAULT_FORECAST_DAYS) is applied
            # inside AutoModeSwitchingManager when both keys are absent.
            vol.Optional(CONF_FORECAST_DAYS): cv.positive_int,
            # Backward-compat alias: "forecast_hours" was the original key name.
            # forecast_days takes precedence; this is accepted to avoid breaking
            # existing YAML configs.
            vol.Optional("forecast_hours"): cv.positive_int,
            vol.Optional(CONF_SEASON_THRESHOLDS): vol.Schema(
                {
                    vol.Optional(CONF_WINTER_BELOW, default=DEFAULT_WINTER_BELOW): vol.Coerce(float),
                    vol.Optional(CONF_SUMMER_ABOVE, default=DEFAULT_SUMMER_ABOVE): vol.Coerce(float),
                }
            ),
            # Cooling supply temperature clamp (for floor cooling with limited supply temp)
            vol.Optional(CONF_COOLING_SUPPLY_TEMP): vol.Coerce(float),
            vol.Optional(CONF_COOLING_SUPPLY_MARGIN, default=DEFAULT_COOLING_SUPPLY_MARGIN): vol.Coerce(float),
        }
    )

    def _water_temp_ensure_list(value: Any) -> list[Any]:
        """Wrap a single mapping in a list, same semantics as ``cv.ensure_list``.

        Defined locally (rather than using ``cv.ensure_list`` directly) because
        the value is fed straight into a list-item schema below; keeping the
        wrapping logic here makes it independent of how ``cv`` is provided.
        """
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def _water_temp_entity_id(value: Any) -> str:
        """Validate an entity ID, same semantics as ``cv.entity_id``.

        Defined locally (rather than using ``cv.entity_id`` directly) so the
        validated value is stable in every environment this schema is
        exercised in. Lowercases the input and enforces Home Assistant's real
        entity-id pattern (``domain.object_id``, no leading/trailing/double
        underscores) so that e.g. ``number.HP`` and ``number.hp`` normalize to
        the same string — required for the cooling/heating ``target_entity``
        distinctness check in ``validate_water_temp_control()`` to actually
        catch case-variant duplicates.
        """
        if not isinstance(value, str):
            raise vol.Invalid(f"entity ID must be a string, got {type(value).__name__}")
        value = value.lower()
        if not re.fullmatch(r"(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)", value):
            raise vol.Invalid(f"{value!r} is not a valid entity ID")
        return value

    # Water temperature control — extra (non-zone) humidity/temperature pair
    WATER_TEMP_EXTRA_SENSOR_SCHEMA = vol.Schema(
        {
            vol.Required(CONF_WATER_TEMP_EXTRA_HUMIDITY): _water_temp_entity_id,
            vol.Required(CONF_WATER_TEMP_EXTRA_TEMPERATURE): _water_temp_entity_id,
        }
    )

    # Water temperature control — cooling half (dew-point driven)
    WATER_TEMP_COOLING_SCHEMA = vol.Schema(
        {
            vol.Required(CONF_WATER_TEMP_TARGET_ENTITY): _water_temp_entity_id,
            vol.Optional(CONF_WATER_TEMP_MIN_SUPPLY_TEMP, default=DEFAULT_WATER_TEMP_MIN_SUPPLY_TEMP): vol.All(
                vol.Coerce(float),
                vol.Range(min=5.0, max=30.0, msg="min_supply_temp must be between 5 and 30°C"),
            ),
            vol.Optional(CONF_WATER_TEMP_DEW_POINT_MARGIN, default=DEFAULT_WATER_TEMP_DEW_POINT_MARGIN): vol.All(
                vol.Coerce(float),
                vol.Range(min=0.0, max=10.0, msg="dew_point_margin must be between 0 and 10°C"),
            ),
            vol.Optional(CONF_WATER_TEMP_FALLBACK_HUMIDITY, default=DEFAULT_WATER_TEMP_FALLBACK_HUMIDITY): vol.All(
                vol.Coerce(float),
                vol.Range(min=15.0, max=100.0, msg="fallback_humidity must be between 15 and 100%"),
            ),
            vol.Optional(CONF_WATER_TEMP_RAMP_START, default=DEFAULT_WATER_TEMP_COOLING_RAMP_START): vol.All(
                vol.Coerce(float),
                vol.Range(min=5.0, max=40.0, msg="cooling ramp_start must be between 5 and 40°C"),
            ),
            vol.Optional(CONF_WATER_TEMP_RAMP_RATE, default=DEFAULT_WATER_TEMP_COOLING_RAMP_RATE): vol.All(
                vol.Coerce(float),
                vol.Range(min=0.1, max=10.0, msg="cooling ramp_rate must be between 0.1 and 10°C/day"),
            ),
            vol.Optional(CONF_WATER_TEMP_EXTRA_SENSORS, default=[]): vol.All(
                _water_temp_ensure_list, [WATER_TEMP_EXTRA_SENSOR_SCHEMA]
            ),
        }
    )

    # Water temperature control — heating half (fixed target + ramp)
    WATER_TEMP_HEATING_SCHEMA = vol.Schema(
        {
            vol.Required(CONF_WATER_TEMP_TARGET_ENTITY): _water_temp_entity_id,
            # No default: absence triggers the supply_temperature fallback in
            # validate_water_temp_control().
            vol.Optional(CONF_WATER_TEMP_TARGET): vol.All(
                vol.Coerce(float),
                vol.Range(
                    min=WATER_TEMP_HEATING_TARGET_MIN,
                    max=WATER_TEMP_HEATING_TARGET_MAX,
                    msg=(
                        f"heating target must be between {WATER_TEMP_HEATING_TARGET_MIN} "
                        f"and {WATER_TEMP_HEATING_TARGET_MAX}°C"
                    ),
                ),
            ),
            vol.Optional(CONF_WATER_TEMP_RAMP_START, default=DEFAULT_WATER_TEMP_HEATING_RAMP_START): vol.All(
                vol.Coerce(float),
                vol.Range(min=15.0, max=60.0, msg="heating ramp_start must be between 15 and 60°C"),
            ),
            vol.Optional(CONF_WATER_TEMP_RAMP_RATE, default=DEFAULT_WATER_TEMP_HEATING_RAMP_RATE): vol.All(
                vol.Coerce(float),
                vol.Range(min=0.1, max=10.0, msg="heating ramp_rate must be between 0.1 and 10°C/day"),
            ),
        }
    )

    # Water temperature control — top level
    WATER_TEMP_CONTROL_SCHEMA = vol.Schema(
        {
            vol.Optional(CONF_WATER_TEMP_IDLE_DAYS, default=DEFAULT_WATER_TEMP_IDLE_DAYS): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=365, msg="idle_days must be between 1 and 365"),
            ),
            vol.Optional(CONF_WATER_TEMP_MIN_WRITE_INTERVAL, default=DEFAULT_WATER_TEMP_MIN_WRITE_INTERVAL): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400, msg="min_write_interval must be between 0 and 86400 seconds"),
            ),
            vol.Optional(CONF_WATER_TEMP_CONDENSATION_SENSOR): _water_temp_entity_id,
            vol.Optional(CONF_WATER_TEMP_COOLING): WATER_TEMP_COOLING_SCHEMA,
            vol.Optional(CONF_WATER_TEMP_HEATING): WATER_TEMP_HEATING_SCHEMA,
        }
    )

    CONFIG_SCHEMA = vol.Schema(
        {
            DOMAIN: vol.All(
                vol.Schema(
                    {
                        # Notification settings
                        vol.Optional(CONF_NOTIFY_SERVICE): valid_notify_service,
                        vol.Optional(CONF_PERSISTENT_NOTIFICATION, default=DEFAULT_PERSISTENT_NOTIFICATION): cv.boolean,
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
                                msg=f"supply_temperature must be between {SUPPLY_TEMP_MIN} and {SUPPLY_TEMP_MAX}°C",
                            ),
                        ),
                        # Central heat source control
                        vol.Optional(CONF_MAIN_HEATER_SWITCH): cv.entity_ids,
                        vol.Optional(CONF_MAIN_COOLER_SWITCH): cv.entity_ids,
                        vol.Optional(CONF_SOURCE_STARTUP_DELAY, default=DEFAULT_SOURCE_STARTUP_DELAY): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=300, msg="source_startup_delay must be between 0 and 300 seconds"),
                        ),
                        # Mode synchronization
                        vol.Optional(CONF_SYNC_MODES, default=DEFAULT_SYNC_MODES): cv.boolean,
                        # Learning configuration
                        vol.Optional(CONF_LEARNING_WINDOW_DAYS, default=DEFAULT_LEARNING_WINDOW_DAYS): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=1, max=30, msg="learning_window_days must be between 1 and 30 days"),
                        ),
                        vol.Optional(CONF_CHRONIC_APPROACH_HISTORIC_SCAN, default=False): cv.boolean,
                        # Weather and physics
                        vol.Optional(CONF_WEATHER_ENTITY): cv.entity_id,
                        vol.Optional(CONF_OUTDOOR_SENSOR): cv.entity_id,
                        vol.Optional(CONF_WIND_SPEED_SENSOR): cv.entity_id,
                        vol.Optional(CONF_HOUSE_ENERGY_RATING): vol.In(
                            VALID_ENERGY_RATINGS,
                            msg=f"house_energy_rating must be one of: {', '.join(VALID_ENERGY_RATINGS)}",
                        ),
                        vol.Optional(CONF_WINDOW_RATING, default=DEFAULT_WINDOW_RATING): cv.string,
                        # Heat output sensors
                        vol.Optional(CONF_SUPPLY_TEMP_SENSOR): cv.entity_id,
                        vol.Optional(CONF_RETURN_TEMP_SENSOR): cv.entity_id,
                        vol.Optional(CONF_FLOW_RATE_SENSOR): cv.entity_id,
                        vol.Optional(CONF_VOLUME_METER_ENTITY): cv.entity_id,
                        vol.Optional(CONF_FALLBACK_FLOW_RATE, default=DEFAULT_FALLBACK_FLOW_RATE): vol.All(
                            vol.Coerce(float),
                            vol.Range(min=0.01, max=10.0, msg="fallback_flow_rate must be between 0.01 and 10.0 L/s"),
                        ),
                        # Preset temperatures
                        vol.Optional(CONF_AWAY_TEMP): vol.Coerce(float),
                        vol.Optional(CONF_ECO_TEMP): vol.Coerce(float),
                        vol.Optional(CONF_BOOST_TEMP): vol.Coerce(float),
                        vol.Optional(CONF_COMFORT_TEMP): vol.Coerce(float),
                        vol.Optional(CONF_HOME_TEMP): vol.Coerce(float),
                        vol.Optional(CONF_ACTIVITY_TEMP): vol.Coerce(float),
                        vol.Optional(CONF_PRESET_SYNC_MODE, default=DEFAULT_PRESET_SYNC_MODE): vol.In(["sync", "none"]),
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
                        vol.Optional(CONF_MIN_OPEN_TIME): vol.All(cv.time_period, cv.positive_timedelta),
                        vol.Optional(CONF_MIN_CLOSED_TIME): vol.All(cv.time_period, cv.positive_timedelta),
                        # Thermal groups for static multi-zone coordination
                        vol.Optional(CONF_THERMAL_GROUPS): vol.All(cv.ensure_list, [THERMAL_GROUP_SCHEMA]),
                        # Manifolds for hydraulic transport delay tracking
                        vol.Optional(CONF_MANIFOLDS): vol.All(cv.ensure_list, [MANIFOLD_SCHEMA]),
                        # Auto mode switching for heat/cool based on outdoor temperature
                        vol.Optional(CONF_AUTO_MODE_SWITCHING): AUTO_MODE_SWITCHING_SCHEMA,
                        # Cooling supply temperature clamp (for floor cooling with limited supply temp)
                        vol.Optional(CONF_COOLING_SUPPLY_TEMP): vol.Coerce(float),
                        vol.Optional(CONF_COOLING_SUPPLY_MARGIN, default=DEFAULT_COOLING_SUPPLY_MARGIN): vol.Coerce(
                            float
                        ),
                        # Water temperature control (dew-point cooling + startup ramps)
                        vol.Optional(CONF_WATER_TEMP_CONTROL): WATER_TEMP_CONTROL_SCHEMA,
                    }
                ),
                validate_water_temp_control,
                validate_manifolds_and_pump_flow,
            )
        },
        extra=vol.ALLOW_EXTRA,  # Allow other domains in config
    )
else:
    # Provide stub for testing without Home Assistant
    CONFIG_SCHEMA = None
    THERMAL_GROUP_SCHEMA = None
    WATER_TEMP_EXTRA_SENSOR_SCHEMA = None
    WATER_TEMP_COOLING_SCHEMA = None
    WATER_TEMP_HEATING_SCHEMA = None
    WATER_TEMP_CONTROL_SCHEMA = None


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
            "Notification service 'notify.%s' is not available. Check your Home Assistant notification configuration.",
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


async def async_save_water_temp_state_now(hass: HomeAssistant) -> None:
    """Persist the water temperature controller's state immediately.

    Called on ``homeassistant_stop`` and on unload so a ramp survives a restart.
    Never raises — a failed save must not block shutdown.

    Args:
        hass: Home Assistant instance.
    """
    domain_data = hass.data.get(DOMAIN, {})
    coordinator = domain_data.get("coordinator")
    learning_store = domain_data.get("learning_store")
    controller = getattr(coordinator, "water_temp_controller", None) if coordinator else None

    if controller is None or learning_store is None:
        return

    try:
        await learning_store.async_save_water_temp_state(controller.get_state_for_persistence())
        _LOGGER.info("Saved water temperature control state")
    except Exception as err:  # shutdown must not fail on a bad save
        _LOGGER.error("Failed to save water temperature state: %s", err)


def check_cooling_supply_conflict(domain_config: dict[str, Any]) -> str | None:
    """Return a warning when the static and dynamic cooling floors disagree.

    ``cooling_supply_temp`` (static) and ``water_temp_control.cooling.min_supply_temp``
    (dynamic floor) describe the same physical limit.  Divergent values mean one
    of them is wrong.

    Args:
        domain_config: The validated ``adaptive_climate:`` domain config.

    Returns:
        A warning message naming both values, or None when there is no conflict.
    """
    water_temp = domain_config.get(CONF_WATER_TEMP_CONTROL) or {}
    cooling = water_temp.get(CONF_WATER_TEMP_COOLING)
    if not cooling:
        return None

    auto_mode = domain_config.get(CONF_AUTO_MODE_SWITCHING) or {}
    static = auto_mode.get(CONF_COOLING_SUPPLY_TEMP) or domain_config.get(CONF_COOLING_SUPPLY_TEMP)
    if static is None:
        return None

    dynamic = cooling.get(CONF_WATER_TEMP_MIN_SUPPLY_TEMP, DEFAULT_WATER_TEMP_MIN_SUPPLY_TEMP)
    if float(static) == float(dynamic):
        return None

    return (
        f"cooling_supply_temp ({static}°C) differs from "
        f"water_temp_control.cooling.min_supply_temp ({dynamic}°C). "
        "The dynamic value now drives min_cooling_target; the static one is only a "
        "fallback before the first computation. Align them to avoid surprises."
    )


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
    """Set up the Adaptive Climate integration."""
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
    VACATION_MODE_SCHEMA = vol.Schema(
        {
            vol.Required("enabled"): cv.boolean,
            vol.Optional("target_temp", default=DEFAULT_VACATION_TARGET_TEMP): vol.Coerce(float),
        }
    )

    # Initialize domain data storage
    hass.data.setdefault(DOMAIN, {})

    # Create www directory for chart images
    from pathlib import Path

    www_dir = Path(hass.config.path("www")) / "adaptive_climate"
    try:
        www_dir.mkdir(parents=True, exist_ok=True)
        _LOGGER.debug("Chart directory ready: %s", www_dir)
    except OSError as e:
        _LOGGER.warning("Could not create chart directory: %s", e)

    # Get configuration options from domain config
    domain_config = config.get(DOMAIN, {})

    # Store config values needed by coordinator during __init__
    weather_entity = domain_config.get(CONF_WEATHER_ENTITY)
    hass.data[DOMAIN]["weather_entity"] = weather_entity
    if weather_entity:
        _LOGGER.info("Weather entity configured: %s", weather_entity)

    outdoor_sensor = domain_config.get(CONF_OUTDOOR_SENSOR)
    hass.data[DOMAIN]["outdoor_sensor"] = outdoor_sensor
    if outdoor_sensor:
        _LOGGER.info("Outdoor sensor configured: %s", outdoor_sensor)

    house_energy_rating = domain_config.get(CONF_HOUSE_ENERGY_RATING)
    hass.data[DOMAIN]["house_energy_rating"] = house_energy_rating
    if house_energy_rating:
        _LOGGER.info("House energy rating: %s", house_energy_rating)

    # Create coordinator with domain config for auto mode switching
    coordinator = AdaptiveThermostatCoordinator(hass, domain_config)
    hass.data[DOMAIN]["coordinator"] = coordinator

    conflict = check_cooling_supply_conflict(domain_config)
    if conflict:
        _LOGGER.warning(conflict)

    # Create vacation mode handler
    vacation_mode = VacationMode(hass, coordinator)
    hass.data[DOMAIN]["vacation_mode"] = vacation_mode

    # Notification and energy tracking
    notify_service = domain_config.get(CONF_NOTIFY_SERVICE)
    persistent_notification = domain_config.get(CONF_PERSISTENT_NOTIFICATION, DEFAULT_PERSISTENT_NOTIFICATION)

    # Create notification manager for event-driven notifications
    from .managers.notification_manager import NotificationManager

    notification_manager = NotificationManager(
        hass=hass,
        notify_service=notify_service,
        persistent_notification=persistent_notification,
    )
    coordinator.notification_manager = notification_manager
    _LOGGER.info("Notification manager initialized")
    energy_meter = domain_config.get(CONF_ENERGY_METER_ENTITY)
    energy_cost = domain_config.get(CONF_ENERGY_COST_ENTITY)

    hass.data[DOMAIN]["notify_service"] = notify_service
    hass.data[DOMAIN]["persistent_notification"] = persistent_notification
    hass.data[DOMAIN]["debug"] = domain_config.get(CONF_DEBUG, DEFAULT_DEBUG)

    # Set logger level based on debug flag - suppresses info/debug when debug=false
    pkg_logger = logging.getLogger("custom_components.adaptive_climate")
    pkg_logger.setLevel(logging.DEBUG if hass.data[DOMAIN]["debug"] else logging.WARNING)
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
    source_startup_delay = domain_config.get(CONF_SOURCE_STARTUP_DELAY, DEFAULT_SOURCE_STARTUP_DELAY)

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
            from .adaptive.manifold_registry import ManifoldRegistry, manifolds_from_config

            # Create Manifold objects from config
            manifolds = manifolds_from_config(manifolds_config)
            # Create registry
            manifold_registry = ManifoldRegistry(manifolds)
            hass.data[DOMAIN]["manifold_registry"] = manifold_registry
            _LOGGER.info("Manifold registry enabled with %d manifolds", len(manifolds))

            # Note: manifold state is restored in climate_setup.py after
            # LearningDataStore is created (they run in the correct order there).
        except (ValueError, ImportError) as e:
            _LOGGER.error("Failed to initialize manifold registry: %s", e)
            # Don't fail setup, just disable manifold registry
            hass.data[DOMAIN]["manifold_registry"] = None

    # Learning configuration
    learning_window_days = domain_config.get(CONF_LEARNING_WINDOW_DAYS, DEFAULT_LEARNING_WINDOW_DAYS)
    hass.data[DOMAIN]["learning_window_days"] = learning_window_days

    # Chronic approach historic scan flag
    chronic_approach_historic_scan = domain_config.get(CONF_CHRONIC_APPROACH_HISTORIC_SCAN, False)
    hass.data[DOMAIN]["chronic_approach_historic_scan"] = chronic_approach_historic_scan
    if chronic_approach_historic_scan:
        _LOGGER.info("Chronic approach historic scan enabled")

    # Default window rating for physics-based initialization (can be overridden per zone)
    window_rating = domain_config.get(CONF_WINDOW_RATING, DEFAULT_WINDOW_RATING)
    hass.data[DOMAIN]["window_rating"] = window_rating
    _LOGGER.info("Default window rating: %s", window_rating)

    # Heat output sensors
    supply_temp_sensor = domain_config.get(CONF_SUPPLY_TEMP_SENSOR)
    return_temp_sensor = domain_config.get(CONF_RETURN_TEMP_SENSOR)
    flow_rate_sensor = domain_config.get(CONF_FLOW_RATE_SENSOR)
    volume_meter_entity = domain_config.get(CONF_VOLUME_METER_ENTITY)
    fallback_flow_rate = domain_config.get(CONF_FALLBACK_FLOW_RATE, DEFAULT_FALLBACK_FLOW_RATE)

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
    hass.data[DOMAIN]["preset_sync_mode"] = domain_config.get(CONF_PRESET_SYNC_MODE, DEFAULT_PRESET_SYNC_MODE)
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
    hass.data[DOMAIN]["min_open_time"] = domain_config.get(CONF_MIN_OPEN_TIME)
    hass.data[DOMAIN]["min_closed_time"] = domain_config.get(CONF_MIN_CLOSED_TIME)

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
        default_vacation_target_temp=DEFAULT_VACATION_TARGET_TEMP,
        debug=domain_config.get(CONF_DEBUG, DEFAULT_DEBUG),
    )

    # Event listener to set integral values (for restoration/debugging)
    async def _handle_set_integral_event(event):
        """Handle adaptive_climate_set_integral event."""
        values = event.data.get("values", {})
        entity_component = hass.data.get("climate")
        if not entity_component:
            _LOGGER.warning("set_integral: Climate component not available")
            return
        for entity_id, integral_value in values.items():
            thermostat = entity_component.get_entity(entity_id)
            if thermostat and hasattr(thermostat, "async_set_integral"):
                # L17: Use the lock-protected method to avoid racing _async_control_heating.
                await thermostat.async_set_integral(float(integral_value))
                _LOGGER.info("%s: Set integral to %.2f via event", entity_id, integral_value)
            else:
                _LOGGER.warning("set_integral: Entity not found or no PID controller: %s", entity_id)

    # Store unsub handle for cleanup during unload (C3 fix)
    set_integral_unsub = hass.bus.async_listen("adaptive_climate_set_integral", _handle_set_integral_event)
    hass.data[DOMAIN]["set_integral_unsub"] = set_integral_unsub

    # Store cancel callbacks for scheduled tasks (needed for unload)
    unsub_callbacks = []

    # Schedule daily adaptive learning at 3:00 AM
    async def _async_daily_learning_callback(_now) -> None:
        """Wrapper for scheduled daily learning."""
        learning_window = hass.data[DOMAIN].get("learning_window_days", DEFAULT_LEARNING_WINDOW_DAYS)
        await async_daily_learning(hass, coordinator, learning_window, _now)

    unsub_callbacks.append(async_track_time_change(hass, _async_daily_learning_callback, hour=3, minute=0, second=0))
    _LOGGER.debug("Scheduled daily adaptive learning at 3:00 AM")

    # Schedule health check every 6 hours (at 0:00, 6:00, 12:00, 18:00)
    async def _async_scheduled_health_check_callback(_now) -> None:
        """Wrapper for scheduled health check."""
        await async_scheduled_health_check(
            hass,
            coordinator,
            notify_service,
            persistent_notification,
            async_send_notification,
            async_send_persistent_notification,
            _now,
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
            hass,
            coordinator,
            notify_service,
            persistent_notification,
            async_send_notification,
            async_send_persistent_notification,
            _now,
        )

    unsub_callbacks.append(
        async_track_time_change(hass, _async_scheduled_weekly_report_callback, hour=9, minute=0, second=0)
    )
    _LOGGER.debug("Scheduled weekly report on Sundays at 9:00 AM")

    # Store unsubscribe callbacks for cleanup during unload
    hass.data[DOMAIN]["unsub_callbacks"] = unsub_callbacks

    # Register shutdown handler for manifold + water temperature state persistence
    async def _async_save_state_on_shutdown(event):
        """Save manifold and water temperature state on Home Assistant shutdown."""
        manifold_registry = hass.data.get(DOMAIN, {}).get("manifold_registry")
        learning_store = hass.data.get(DOMAIN, {}).get("learning_store")

        if manifold_registry and learning_store:
            try:
                manifold_state = manifold_registry.get_state_for_persistence()
                await learning_store.async_save_manifold_state(manifold_state)
                _LOGGER.info("Saved manifold state on shutdown: %d manifolds", len(manifold_state))
            except Exception as e:
                _LOGGER.error("Failed to save manifold state on shutdown: %s", e)

        await async_save_water_temp_state_now(hass)

    # Listen for HA stop event
    shutdown_unsub = hass.bus.async_listen_once("homeassistant_stop", _async_save_state_on_shutdown)
    hass.data[DOMAIN]["shutdown_unsub"] = shutdown_unsub

    _LOGGER.info("Adaptive Climate integration setup complete")
    return True


async def async_unload(hass: HomeAssistant) -> bool:
    """Unload the Adaptive Climate integration.

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

    _LOGGER.info("Unloading Adaptive Climate integration")

    # Cancel all scheduled tasks
    unsub_callbacks = hass.data[DOMAIN].get("unsub_callbacks", [])
    for unsub in unsub_callbacks:
        if unsub is not None:
            try:
                unsub()
            except Exception as e:
                _LOGGER.warning("Error cancelling scheduled task: %s", e)
    _LOGGER.debug("Cancelled %d scheduled tasks", len(unsub_callbacks))

    # Cancel sensor timers (C2/H8 fix)
    sensor_timer_unsubs = hass.data[DOMAIN].get("sensor_timer_unsubs", [])
    for unsub in sensor_timer_unsubs:
        if unsub is not None:
            try:
                unsub()
            except Exception as e:
                _LOGGER.warning("Error cancelling sensor timer: %s", e)
    _LOGGER.debug("Cancelled %d sensor timers", len(sensor_timer_unsubs))

    # Cancel set_integral event listener (C3 fix)
    set_integral_unsub = hass.data[DOMAIN].get("set_integral_unsub")
    if set_integral_unsub is not None:
        try:
            set_integral_unsub()
        except Exception as e:
            _LOGGER.warning("Error cancelling set_integral listener: %s", e)
        _LOGGER.debug("Cancelled set_integral event listener")

    # Cancel shutdown listener and save manifold state
    shutdown_unsub = hass.data[DOMAIN].get("shutdown_unsub")
    if shutdown_unsub is not None:
        try:
            shutdown_unsub()
        except Exception as e:
            _LOGGER.warning("Error cancelling shutdown listener: %s", e)

    # Save manifold state on unload
    manifold_registry = hass.data[DOMAIN].get("manifold_registry")
    learning_store = hass.data[DOMAIN].get("learning_store")
    if manifold_registry and learning_store:
        try:
            manifold_state = manifold_registry.get_state_for_persistence()
            await learning_store.async_save_manifold_state(manifold_state)
            _LOGGER.info("Saved manifold state on unload: %d manifolds", len(manifold_state))
        except Exception as e:
            _LOGGER.error("Failed to save manifold state on unload: %s", e)

    # Save water temperature state on unload
    await async_save_water_temp_state_now(hass)

    # Unregister all services
    async_unregister_services(hass)

    # Clean up coordinator if it exists
    coordinator = hass.data[DOMAIN].get("coordinator")
    if coordinator is not None:
        # Clean up coordinator resources (outdoor temp listener, etc.)
        await coordinator.async_cleanup()
        _LOGGER.debug("Cleaned up coordinator")

    # Clean up central controller if it exists
    central_controller = hass.data[DOMAIN].get("central_controller")
    if central_controller is not None:
        # Cancel all pending async tasks
        await central_controller.async_cleanup()
        # Clear reference to coordinator
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
    _LOGGER.info("Adaptive Climate integration unloaded successfully")

    return True
