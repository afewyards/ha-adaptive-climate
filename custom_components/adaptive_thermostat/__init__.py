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
    from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    # Provide stubs for testing
    HomeAssistant = Any
    ServiceCall = Any
    ConfigType = Any

from .const import DOMAIN

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

# Service names
SERVICE_RUN_LEARNING = "run_learning"
SERVICE_HEALTH_CHECK = "health_check"
SERVICE_WEEKLY_REPORT = "weekly_report"
SERVICE_COST_REPORT = "cost_report"
SERVICE_SET_VACATION_MODE = "set_vacation_mode"

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Adaptive Thermostat integration."""
    if not HAS_HOMEASSISTANT:
        return False

    # Import modules that require Home Assistant
    from .const import (
        CONF_NOTIFY_SERVICE,
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
    )
    from .coordinator import (
        AdaptiveThermostatCoordinator,
        CentralController,
        ModeSync,
        ZoneLinker,
    )
    from .adaptive.vacation import VacationMode
    from .analytics.health import SystemHealthMonitor, HealthStatus
    from .analytics.reports import WeeklyReport

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
    energy_meter = domain_config.get(CONF_ENERGY_METER_ENTITY)
    energy_cost = domain_config.get(CONF_ENERGY_COST_ENTITY)

    hass.data[DOMAIN]["notify_service"] = notify_service
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
        _LOGGER.info(
            "Central controller configured: heater=%s, cooler=%s, delay=%ds",
            main_heater_switch,
            main_cooler_switch,
            source_startup_delay,
        )

        # Set up periodic update for central controller (every 30 seconds)
        async def _update_central_controller(_now):
            """Periodically update central controller."""
            if central_controller:
                await central_controller.update()

        async_track_time_interval(
            hass, _update_central_controller, timedelta(seconds=30)
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

    # Register services
    async def async_handle_run_learning(call: ServiceCall) -> dict:
        """Handle the run_learning service call.

        Returns:
            Dictionary with learning results for all zones
        """
        _LOGGER.info("Running adaptive learning analysis for all zones")

        all_zones = coordinator.get_all_zones()
        results = {
            "zones_analyzed": 0,
            "zones_with_recommendations": 0,
            "zones_skipped": 0,
            "zone_results": {},
        }

        for zone_id, zone_data in all_zones.items():
            adaptive_learner = zone_data.get("adaptive_learner")
            climate_entity_id = zone_data.get("climate_entity_id")

            if not adaptive_learner:
                _LOGGER.debug("No adaptive learner for zone %s", zone_id)
                results["zones_skipped"] += 1
                results["zone_results"][zone_id] = {
                    "status": "skipped",
                    "reason": "learning_disabled",
                }
                continue

            # Get current PID values from climate entity
            state = hass.states.get(climate_entity_id) if climate_entity_id else None
            if not state:
                _LOGGER.warning("Cannot get state for zone %s (%s)", zone_id, climate_entity_id)
                results["zones_skipped"] += 1
                results["zone_results"][zone_id] = {
                    "status": "skipped",
                    "reason": "entity_not_found",
                }
                continue

            current_kp = state.attributes.get("kp", 100.0)
            current_ki = state.attributes.get("ki", 0.01)
            current_kd = state.attributes.get("kd", 0.0)

            try:
                # Get cycle count for reporting
                cycle_count = adaptive_learner.get_cycle_count()

                # Trigger learning analysis with current PID values
                recommendation = adaptive_learner.calculate_pid_adjustment(
                    current_kp=current_kp,
                    current_ki=current_ki,
                    current_kd=current_kd,
                )

                results["zones_analyzed"] += 1

                if recommendation is None:
                    _LOGGER.info(
                        "Zone %s: insufficient data for recommendations (cycles: %d)",
                        zone_id,
                        cycle_count,
                    )
                    results["zone_results"][zone_id] = {
                        "status": "insufficient_data",
                        "cycle_count": cycle_count,
                        "current_pid": {"kp": current_kp, "ki": current_ki, "kd": current_kd},
                    }
                else:
                    # Calculate percentage changes for logging
                    kp_change = ((recommendation["kp"] - current_kp) / current_kp * 100) if current_kp != 0 else 0
                    ki_change = ((recommendation["ki"] - current_ki) / current_ki * 100) if current_ki != 0 else 0
                    kd_change = ((recommendation["kd"] - current_kd) / current_kd * 100) if current_kd != 0 else 0

                    _LOGGER.info(
                        "Zone %s PID recommendation: Kp=%.2f (%.1f%%), Ki=%.4f (%.1f%%), Kd=%.2f (%.1f%%)",
                        zone_id,
                        recommendation["kp"], kp_change,
                        recommendation["ki"], ki_change,
                        recommendation["kd"], kd_change,
                    )

                    results["zones_with_recommendations"] += 1
                    results["zone_results"][zone_id] = {
                        "status": "recommendation_available",
                        "cycle_count": cycle_count,
                        "current_pid": {"kp": current_kp, "ki": current_ki, "kd": current_kd},
                        "recommended_pid": recommendation,
                        "changes_percent": {"kp": kp_change, "ki": ki_change, "kd": kd_change},
                    }
            except Exception as e:
                _LOGGER.error("Learning failed for zone %s: %s", zone_id, e)
                results["zone_results"][zone_id] = {
                    "status": "error",
                    "error": str(e),
                }

        _LOGGER.info(
            "Learning analysis complete: %d zones analyzed, %d with recommendations, %d skipped",
            results["zones_analyzed"],
            results["zones_with_recommendations"],
            results["zones_skipped"],
        )

        return results

    async def async_handle_health_check(call: ServiceCall) -> None:
        """Handle the health_check service call."""
        _LOGGER.info("Running health check for all zones")

        # Collect zones data
        all_zones = coordinator.get_all_zones()
        zones_data = {}

        for zone_id in all_zones:
            # Get cycle time sensor
            cycle_time_sensor_id = f"sensor.{zone_id}_cycle_time"
            cycle_time_state = hass.states.get(cycle_time_sensor_id)
            cycle_time_min = None
            if cycle_time_state and cycle_time_state.state not in ("unknown", "unavailable"):
                try:
                    cycle_time_min = float(cycle_time_state.state)
                except (ValueError, TypeError):
                    pass

            # Get power/m2 sensor
            power_m2_sensor_id = f"sensor.{zone_id}_power_m2"
            power_m2_state = hass.states.get(power_m2_sensor_id)
            power_w_m2 = None
            if power_m2_state and power_m2_state.state not in ("unknown", "unavailable"):
                try:
                    power_w_m2 = float(power_m2_state.state)
                except (ValueError, TypeError):
                    pass

            zones_data[zone_id] = {
                "cycle_time_min": cycle_time_min,
                "power_w_m2": power_w_m2,
                "sensor_available": True,
            }

        # Run health check
        health_monitor = SystemHealthMonitor()
        health_result = health_monitor.check_all_zones(zones_data)

        status = health_result["status"]
        summary = health_result["summary"]

        _LOGGER.info("Health check complete: %s - %s", status.value, summary)

        # Send notification if issues found
        if status != HealthStatus.HEALTHY:
            # Build detailed message with zone-specific issues
            message_parts = [summary]
            zone_issues = health_result.get("zone_issues", {})
            for zone_name, issues in zone_issues.items():
                for issue in issues:
                    message_parts.append(f"- {zone_name}: {issue.message}")

            await async_send_notification(
                hass,
                notify_service,
                title=f"Heating System Health: {status.value.upper()}",
                message="\n".join(message_parts),
            )

    async def async_handle_weekly_report(call: ServiceCall) -> None:
        """Handle the weekly_report service call."""
        _LOGGER.info("Generating weekly report")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        report = WeeklyReport(start_date, end_date)

        # Collect data for each zone
        all_zones = coordinator.get_all_zones()
        total_energy = 0.0
        total_cost = 0.0
        has_energy_data = False

        for zone_id in all_zones:
            # Get duty cycle
            duty_sensor_id = f"sensor.{zone_id}_duty_cycle"
            duty_state = hass.states.get(duty_sensor_id)
            duty_cycle = 0.0
            if duty_state and duty_state.state not in ("unknown", "unavailable"):
                try:
                    duty_cycle = float(duty_state.state)
                except (ValueError, TypeError):
                    pass

            report.add_zone_data(zone_id, duty_cycle)

        # Get system totals if available
        total_power_state = hass.states.get("sensor.heating_total_power")
        weekly_cost_state = hass.states.get("sensor.heating_weekly_cost")

        if weekly_cost_state and weekly_cost_state.state not in ("unknown", "unavailable"):
            try:
                total_cost = float(weekly_cost_state.state)
                has_energy_data = True
                weekly_energy = weekly_cost_state.attributes.get("weekly_energy_kwh", 0)
                report.set_totals(weekly_energy, total_cost)
            except (ValueError, TypeError):
                pass

        # Format and send report
        report_text = report.format_report()
        _LOGGER.info("Weekly report generated:\n%s", report_text)

        # Send notification
        await async_send_notification(
            hass,
            notify_service,
            title="Heating System Weekly Report",
            message=report_text,
        )

    async def async_handle_cost_report(call: ServiceCall) -> None:
        """Handle the cost_report service call.

        Supports daily, weekly, and monthly periods.
        """
        period = call.data.get("period", "weekly")
        _LOGGER.info("Generating %s cost report", period)

        # Calculate period days for estimation
        period_days = {"daily": 1, "weekly": 7, "monthly": 30}
        days = period_days.get(period, 7)

        report_lines = [f"Energy Cost Report ({period.capitalize()})", "=" * 40]

        # Get cost sensor data
        cost_sensor_id = f"sensor.heating_{period}_cost"
        cost_state = hass.states.get(cost_sensor_id)

        # Fallback to weekly sensor and scale if specific period sensor doesn't exist
        if not cost_state or cost_state.state in ("unknown", "unavailable"):
            weekly_cost_state = hass.states.get("sensor.heating_weekly_cost")
            if weekly_cost_state and weekly_cost_state.state not in ("unknown", "unavailable"):
                try:
                    weekly_cost = float(weekly_cost_state.state)
                    weekly_energy = weekly_cost_state.attributes.get("weekly_energy_kwh", 0)
                    currency = weekly_cost_state.attributes.get("native_unit_of_measurement", "EUR")
                    price = weekly_cost_state.attributes.get("price_per_kwh")

                    # Scale from weekly to requested period
                    scale_factor = days / 7.0
                    energy = weekly_energy * scale_factor
                    cost = weekly_cost * scale_factor

                    report_lines.append(f"{period.capitalize()} Energy: {energy:.1f} kWh")
                    report_lines.append(f"{period.capitalize()} Cost: {cost:.2f} {currency}")
                    if price:
                        report_lines.append(f"Price/kWh: {price:.4f} {currency}")
                    if scale_factor != 1.0:
                        report_lines.append(f"(Estimated from weekly data)")
                except (ValueError, TypeError):
                    report_lines.append("Cost data unavailable")
            else:
                report_lines.append("No energy meter configured")
        else:
            try:
                cost = float(cost_state.state)
                energy_key = f"{period}_energy_kwh"
                energy = cost_state.attributes.get(energy_key, 0)
                currency = cost_state.attributes.get("native_unit_of_measurement", "EUR")
                price = cost_state.attributes.get("price_per_kwh")

                report_lines.append(f"{period.capitalize()} Energy: {energy:.1f} kWh")
                report_lines.append(f"{period.capitalize()} Cost: {cost:.2f} {currency}")
                if price:
                    report_lines.append(f"Price/kWh: {price:.4f} {currency}")
            except (ValueError, TypeError):
                report_lines.append("Cost data unavailable")

        # Get per-zone power data
        report_lines.append("")
        report_lines.append("Zone Power Consumption:")
        report_lines.append("-" * 30)

        total_power_state = hass.states.get("sensor.heating_total_power")
        if total_power_state:
            zone_powers = total_power_state.attributes.get("zone_powers", {})
            for zone_id, power in sorted(zone_powers.items()):
                report_lines.append(f"  {zone_id}: {power:.1f} W")

            try:
                total = float(total_power_state.state)
                report_lines.append(f"  Total: {total:.1f} W")
            except (ValueError, TypeError):
                pass
        else:
            # Try to get power data from duty cycle sensors
            all_zones = coordinator.get_all_zones()
            for zone_id in sorted(all_zones.keys()):
                duty_sensor_id = f"sensor.{zone_id}_duty_cycle"
                duty_state = hass.states.get(duty_sensor_id)
                if duty_state and duty_state.state not in ("unknown", "unavailable"):
                    try:
                        duty_cycle = float(duty_state.state)
                        report_lines.append(f"  {zone_id}: {duty_cycle:.1f}% duty cycle")
                    except (ValueError, TypeError):
                        pass

        report_text = "\n".join(report_lines)
        _LOGGER.info("%s cost report:\n%s", period.capitalize(), report_text)

        # Send notification
        await async_send_notification(
            hass,
            notify_service,
            title=f"Heating System Cost Report ({period.capitalize()})",
            message=report_text,
        )

    async def async_handle_set_vacation_mode(call: ServiceCall) -> None:
        """Handle the set_vacation_mode service call."""
        enabled = call.data["enabled"]
        target_temp = call.data.get("target_temp", DEFAULT_VACATION_TARGET_TEMP)

        if enabled:
            await vacation_mode.async_enable(target_temp)
        else:
            await vacation_mode.async_disable()

    # Register all services
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_LEARNING, async_handle_run_learning
    )
    hass.services.async_register(
        DOMAIN, SERVICE_HEALTH_CHECK, async_handle_health_check
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WEEKLY_REPORT, async_handle_weekly_report
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COST_REPORT, async_handle_cost_report,
        schema=COST_REPORT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_VACATION_MODE, async_handle_set_vacation_mode,
        schema=VACATION_MODE_SCHEMA,
    )

    # Schedule daily adaptive learning at 3:00 AM
    async def async_daily_learning(_now) -> None:
        """Run adaptive learning analysis daily at 3:00 AM."""
        learning_window = hass.data[DOMAIN].get("learning_window_days", DEFAULT_LEARNING_WINDOW_DAYS)
        _LOGGER.info(
            "Starting scheduled daily learning analysis (window: %d days)",
            learning_window,
        )

        all_zones = coordinator.get_all_zones()
        zones_analyzed = 0
        zones_with_adjustments = 0

        for zone_id, zone_data in all_zones.items():
            adaptive_learner = zone_data.get("adaptive_learner")
            climate_entity_id = zone_data.get("climate_entity_id")

            if not adaptive_learner:
                _LOGGER.debug("No adaptive learner for zone %s", zone_id)
                continue

            # Get current PID values from climate entity
            state = hass.states.get(climate_entity_id) if climate_entity_id else None
            if not state:
                _LOGGER.debug("Cannot get state for zone %s (%s)", zone_id, climate_entity_id)
                continue

            current_kp = state.attributes.get("kp", 100.0)
            current_ki = state.attributes.get("ki", 0.01)
            current_kd = state.attributes.get("kd", 0.0)

            try:
                # Trigger learning analysis with current PID values
                recommendation = adaptive_learner.calculate_pid_adjustment(
                    current_kp=current_kp,
                    current_ki=current_ki,
                    current_kd=current_kd,
                )
                zones_analyzed += 1

                if recommendation is None:
                    _LOGGER.debug(
                        "Zone %s: insufficient data for recommendations",
                        zone_id,
                    )
                    continue

                # Calculate percentage changes
                kp_change = ((recommendation["kp"] - current_kp) / current_kp * 100) if current_kp != 0 else 0
                ki_change = ((recommendation["ki"] - current_ki) / current_ki * 100) if current_ki != 0 else 0
                kd_change = ((recommendation["kd"] - current_kd) / current_kd * 100) if current_kd != 0 else 0

                # Check if any significant adjustments were recommended (>1% change)
                if abs(kp_change) > 1 or abs(ki_change) > 1 or abs(kd_change) > 1:
                    zones_with_adjustments += 1
                    _LOGGER.info(
                        "Zone %s PID recommendation: Kp=%.2f (%.1f%%), Ki=%.4f (%.1f%%), Kd=%.2f (%.1f%%)",
                        zone_id,
                        recommendation["kp"], kp_change,
                        recommendation["ki"], ki_change,
                        recommendation["kd"], kd_change,
                    )
                else:
                    _LOGGER.debug(
                        "Zone %s: no significant PID adjustments needed",
                        zone_id,
                    )
            except Exception as e:
                _LOGGER.error("Daily learning failed for zone %s: %s", zone_id, e)

        _LOGGER.info(
            "Daily learning complete: %d zones analyzed, %d with recommended adjustments",
            zones_analyzed,
            zones_with_adjustments,
        )

    # Register the daily learning trigger at 3:00 AM
    async_track_time_change(hass, async_daily_learning, hour=3, minute=0, second=0)
    _LOGGER.debug("Scheduled daily adaptive learning at 3:00 AM")

    # Schedule health check every 6 hours (at 0:00, 6:00, 12:00, 18:00)
    async def async_scheduled_health_check(_now) -> None:
        """Run scheduled health check and send alerts if issues detected."""
        _LOGGER.debug("Running scheduled health check")

        # Collect zones data
        all_zones = coordinator.get_all_zones()
        zones_data = {}

        for zone_id in all_zones:
            # Get cycle time sensor
            cycle_time_sensor_id = f"sensor.{zone_id}_cycle_time"
            cycle_time_state = hass.states.get(cycle_time_sensor_id)
            cycle_time_min = None
            if cycle_time_state and cycle_time_state.state not in ("unknown", "unavailable"):
                try:
                    cycle_time_min = float(cycle_time_state.state)
                except (ValueError, TypeError):
                    pass

            # Get power/m2 sensor
            power_m2_sensor_id = f"sensor.{zone_id}_power_m2"
            power_m2_state = hass.states.get(power_m2_sensor_id)
            power_w_m2 = None
            if power_m2_state and power_m2_state.state not in ("unknown", "unavailable"):
                try:
                    power_w_m2 = float(power_m2_state.state)
                except (ValueError, TypeError):
                    pass

            zones_data[zone_id] = {
                "cycle_time_min": cycle_time_min,
                "power_w_m2": power_w_m2,
                "sensor_available": True,
            }

        # Run health check
        health_monitor = SystemHealthMonitor()
        health_result = health_monitor.check_all_zones(zones_data)

        status = health_result["status"]
        summary = health_result["summary"]

        # Only send notification if there are issues
        if status != HealthStatus.HEALTHY:
            _LOGGER.warning("Scheduled health check found issues: %s - %s", status.value, summary)

            # Build detailed message with zone-specific issues
            message_parts = [summary]
            zone_issues = health_result.get("zone_issues", {})
            for zone_name, issues in zone_issues.items():
                for issue in issues:
                    message_parts.append(f"- {zone_name}: {issue.message}")

            await async_send_notification(
                hass,
                notify_service,
                title=f"Heating System Alert: {status.value.upper()}",
                message="\n".join(message_parts),
            )
        else:
            _LOGGER.debug("Scheduled health check: all zones healthy")

    # Register health check at 0:00, 6:00, 12:00, 18:00
    for hour in [0, 6, 12, 18]:
        async_track_time_change(hass, async_scheduled_health_check, hour=hour, minute=0, second=0)
    _LOGGER.debug("Scheduled health checks every 6 hours")

    # Schedule weekly report on Sunday at 9:00 AM
    async def async_scheduled_weekly_report(_now) -> None:
        """Generate and send weekly report on Sunday mornings."""
        # Only run on Sundays (weekday() returns 6 for Sunday)
        if _now.weekday() != 6:
            return

        _LOGGER.info("Generating scheduled weekly report")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        report = WeeklyReport(start_date, end_date)

        # Collect data for each zone
        all_zones = coordinator.get_all_zones()

        for zone_id in all_zones:
            # Get duty cycle
            duty_sensor_id = f"sensor.{zone_id}_duty_cycle"
            duty_state = hass.states.get(duty_sensor_id)
            duty_cycle = 0.0
            if duty_state and duty_state.state not in ("unknown", "unavailable"):
                try:
                    duty_cycle = float(duty_state.state)
                except (ValueError, TypeError):
                    pass

            report.add_zone_data(zone_id, duty_cycle)

        # Get system totals if available
        weekly_cost_state = hass.states.get("sensor.heating_weekly_cost")

        if weekly_cost_state and weekly_cost_state.state not in ("unknown", "unavailable"):
            try:
                total_cost = float(weekly_cost_state.state)
                weekly_energy = weekly_cost_state.attributes.get("weekly_energy_kwh", 0)
                report.set_totals(weekly_energy, total_cost)
            except (ValueError, TypeError):
                pass

        # Format and send report
        report_text = report.format_report()
        _LOGGER.info("Weekly report generated:\n%s", report_text)

        # Send notification
        await async_send_notification(
            hass,
            notify_service,
            title="Heating System Weekly Report",
            message=report_text,
        )

    # Register weekly report trigger at 9:00 AM (runs daily, but only executes on Sunday)
    async_track_time_change(hass, async_scheduled_weekly_report, hour=9, minute=0, second=0)
    _LOGGER.debug("Scheduled weekly report on Sundays at 9:00 AM")

    _LOGGER.info("Adaptive Thermostat integration setup complete")
    return True
