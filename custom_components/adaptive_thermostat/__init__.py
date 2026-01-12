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

# Service names
SERVICE_RUN_LEARNING = "run_learning"
SERVICE_APPLY_RECOMMENDED_PID = "apply_recommended_pid"
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
        DEFAULT_VACATION_TARGET_TEMP,
    )
    from .coordinator import AdaptiveThermostatCoordinator
    from .adaptive.vacation import VacationMode
    from .analytics.health import SystemHealthMonitor, HealthStatus
    from .analytics.reports import WeeklyReport

    # Service schemas
    APPLY_PID_SCHEMA = vol.Schema({
        vol.Required("entity_id"): cv.entity_id,
    })

    VACATION_MODE_SCHEMA = vol.Schema({
        vol.Required("enabled"): cv.boolean,
        vol.Optional("target_temp", default=DEFAULT_VACATION_TARGET_TEMP): vol.Coerce(float),
    })

    # Initialize domain data storage
    hass.data.setdefault(DOMAIN, {})

    # Create coordinator
    coordinator = AdaptiveThermostatCoordinator(hass)
    hass.data[DOMAIN]["coordinator"] = coordinator

    # Create vacation mode handler
    vacation_mode = VacationMode(hass, coordinator)
    hass.data[DOMAIN]["vacation_mode"] = vacation_mode

    # Get configuration options
    domain_config = config.get(DOMAIN, {})
    notify_service = domain_config.get(CONF_NOTIFY_SERVICE)
    energy_meter = domain_config.get(CONF_ENERGY_METER_ENTITY)
    energy_cost = domain_config.get(CONF_ENERGY_COST_ENTITY)

    hass.data[DOMAIN]["notify_service"] = notify_service
    hass.data[DOMAIN]["energy_meter_entity"] = energy_meter
    hass.data[DOMAIN]["energy_cost_entity"] = energy_cost

    # Register services
    async def async_handle_run_learning(call: ServiceCall) -> None:
        """Handle the run_learning service call."""
        _LOGGER.info("Running adaptive learning analysis for all zones")

        all_zones = coordinator.get_all_zones()

        for zone_id, zone_data in all_zones.items():
            adaptive_learner = zone_data.get("adaptive_learner")
            if adaptive_learner:
                try:
                    # Trigger learning analysis
                    adjustment = adaptive_learner.calculate_pid_adjustment()
                    _LOGGER.info(
                        "Zone %s PID adjustment: Kp=%.2f%%, Ki=%.2f%%, Kd=%.2f%%",
                        zone_id,
                        adjustment.get("kp_adjustment", 0) * 100,
                        adjustment.get("ki_adjustment", 0) * 100,
                        adjustment.get("kd_adjustment", 0) * 100,
                    )
                except Exception as e:
                    _LOGGER.error("Learning failed for zone %s: %s", zone_id, e)
            else:
                _LOGGER.debug("No adaptive learner for zone %s", zone_id)

    async def async_handle_apply_recommended_pid(call: ServiceCall) -> None:
        """Handle the apply_recommended_pid service call."""
        entity_id = call.data["entity_id"]
        _LOGGER.info("Applying recommended PID to %s", entity_id)

        # Find the zone for this entity
        all_zones = coordinator.get_all_zones()
        zone_data = None
        zone_id = None

        for zid, zdata in all_zones.items():
            if zdata.get("climate_entity_id") == entity_id:
                zone_data = zdata
                zone_id = zid
                break

        if not zone_data:
            _LOGGER.error("Zone not found for entity %s", entity_id)
            return

        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            _LOGGER.error("No adaptive learner for zone %s", zone_id)
            return

        # Get recommended adjustments
        adjustment = adaptive_learner.calculate_pid_adjustment()

        # Get current PID values from climate entity
        state = hass.states.get(entity_id)
        if not state:
            _LOGGER.error("Cannot get state for %s", entity_id)
            return

        current_kp = state.attributes.get("kp", 100)
        current_ki = state.attributes.get("ki", 0)
        current_kd = state.attributes.get("kd", 0)

        # Calculate new values
        new_kp = current_kp * (1 + adjustment.get("kp_adjustment", 0))
        new_ki = current_ki * (1 + adjustment.get("ki_adjustment", 0))
        new_kd = current_kd * (1 + adjustment.get("kd_adjustment", 0))

        # Apply via set_pid_gain service
        await hass.services.async_call(
            DOMAIN,
            "set_pid_gain",
            {
                "entity_id": entity_id,
                "kp": new_kp,
                "ki": new_ki,
                "kd": new_kd,
            },
            blocking=True,
        )
        _LOGGER.info(
            "Applied PID to %s: Kp=%.2f, Ki=%.4f, Kd=%.2f",
            entity_id, new_kp, new_ki, new_kd,
        )

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

        # Send notification if issues found and notify service configured
        if notify_service and status != HealthStatus.HEALTHY:
            try:
                await hass.services.async_call(
                    "notify",
                    notify_service.split(".")[-1] if "." in notify_service else notify_service,
                    {
                        "title": f"Heating System Health: {status.value.upper()}",
                        "message": summary,
                    },
                    blocking=True,
                )
            except Exception as e:
                _LOGGER.error("Failed to send health notification: %s", e)

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

        # Send notification if configured
        if notify_service:
            try:
                await hass.services.async_call(
                    "notify",
                    notify_service.split(".")[-1] if "." in notify_service else notify_service,
                    {
                        "title": "Heating System Weekly Report",
                        "message": report_text,
                    },
                    blocking=True,
                )
            except Exception as e:
                _LOGGER.error("Failed to send weekly report notification: %s", e)

    async def async_handle_cost_report(call: ServiceCall) -> None:
        """Handle the cost_report service call."""
        _LOGGER.info("Generating cost report")

        report_lines = ["Energy Cost Report", "=" * 40]

        # Get weekly cost sensor data
        weekly_cost_state = hass.states.get("sensor.heating_weekly_cost")
        if weekly_cost_state and weekly_cost_state.state not in ("unknown", "unavailable"):
            try:
                cost = float(weekly_cost_state.state)
                energy = weekly_cost_state.attributes.get("weekly_energy_kwh", 0)
                currency = weekly_cost_state.attributes.get("native_unit_of_measurement", "EUR")
                price = weekly_cost_state.attributes.get("price_per_kwh")

                report_lines.append(f"Weekly Energy: {energy:.1f} kWh")
                report_lines.append(f"Weekly Cost: {cost:.2f} {currency}")
                if price:
                    report_lines.append(f"Price/kWh: {price:.4f} {currency}")
            except (ValueError, TypeError):
                report_lines.append("Cost data unavailable")
        else:
            report_lines.append("No energy meter configured")

        # Get per-zone power data
        report_lines.append("")
        report_lines.append("Zone Power Consumption:")
        report_lines.append("-" * 30)

        total_power_state = hass.states.get("sensor.heating_total_power")
        if total_power_state:
            zone_powers = total_power_state.attributes.get("zone_powers", {})
            for zone_id, power in zone_powers.items():
                report_lines.append(f"  {zone_id}: {power:.1f} W")

            try:
                total = float(total_power_state.state)
                report_lines.append(f"  Total: {total:.1f} W")
            except (ValueError, TypeError):
                pass

        report_text = "\n".join(report_lines)
        _LOGGER.info("Cost report:\n%s", report_text)

        # Send notification if configured
        if notify_service:
            try:
                await hass.services.async_call(
                    "notify",
                    notify_service.split(".")[-1] if "." in notify_service else notify_service,
                    {
                        "title": "Heating System Cost Report",
                        "message": report_text,
                    },
                    blocking=True,
                )
            except Exception as e:
                _LOGGER.error("Failed to send cost report notification: %s", e)

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
        DOMAIN, SERVICE_APPLY_RECOMMENDED_PID, async_handle_apply_recommended_pid,
        schema=APPLY_PID_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_HEALTH_CHECK, async_handle_health_check
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WEEKLY_REPORT, async_handle_weekly_report
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COST_REPORT, async_handle_cost_report
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_VACATION_MODE, async_handle_set_vacation_mode,
        schema=VACATION_MODE_SCHEMA,
    )

    _LOGGER.info("Adaptive Thermostat integration setup complete")
    return True
