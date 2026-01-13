"""Service handlers for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant, ServiceCall
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any
    ServiceCall = Any

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import AdaptiveThermostatCoordinator
    from .adaptive.vacation import VacationMode

_LOGGER = logging.getLogger(__name__)

# Service names
SERVICE_RUN_LEARNING = "run_learning"
SERVICE_HEALTH_CHECK = "health_check"
SERVICE_WEEKLY_REPORT = "weekly_report"
SERVICE_COST_REPORT = "cost_report"
SERVICE_SET_VACATION_MODE = "set_vacation_mode"
SERVICE_ENERGY_STATS = "energy_stats"
SERVICE_PID_RECOMMENDATIONS = "pid_recommendations"


# =============================================================================
# Helper Functions (Deduplication)
# =============================================================================


def _collect_zones_health_data(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
) -> dict:
    """Collect health check data for all zones.

    Returns:
        Dictionary mapping zone_id to zone health data
    """
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

    return zones_data


async def _run_health_check_core(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
    is_scheduled: bool = False,
) -> dict:
    """Core health check logic shared by manual and scheduled calls.

    Args:
        hass: Home Assistant instance
        coordinator: Thermostat coordinator
        notify_service: Notification service name
        persistent_notification: Whether to send persistent notifications
        async_send_notification_func: Function to send mobile notifications
        async_send_persistent_notification_func: Function to send persistent notifications
        is_scheduled: True if called from scheduled task, False if manual

    Returns:
        Health check result dictionary
    """
    from .analytics.health import SystemHealthMonitor, HealthStatus

    log_prefix = "scheduled " if is_scheduled else ""
    _LOGGER.debug("Running %shealth check", log_prefix)

    # Collect zones data using shared helper
    zones_data = _collect_zones_health_data(hass, coordinator)

    # Run health check
    health_monitor = SystemHealthMonitor()
    health_result = health_monitor.check_all_zones(zones_data)

    status = health_result["status"]
    summary = health_result["summary"]

    # Determine if notifications should be sent
    # Scheduled checks only notify on issues, manual always notifies
    should_notify = True
    if is_scheduled and status == HealthStatus.HEALTHY:
        _LOGGER.debug("Scheduled health check: all zones healthy")
        should_notify = False
    else:
        log_level = logging.WARNING if is_scheduled else logging.INFO
        _LOGGER.log(log_level, "Health check complete: %s - %s", status.value, summary)

    # Send notifications if needed and there are issues
    if should_notify and status != HealthStatus.HEALTHY:
        zone_issues = health_result.get("zone_issues", {})
        zone_count = len(zone_issues)

        # Short message for mobile notification
        short_message = f"{zone_count} zone{'s' if zone_count != 1 else ''} need{'s' if zone_count == 1 else ''} attention"

        # Detailed message for persistent notification
        message_parts = [summary]
        for zone_name, issues in zone_issues.items():
            for issue in issues:
                message_parts.append(f"- {zone_name}: {issue.message}")
        detailed_message = "\n".join(message_parts)

        # Title differs between manual and scheduled
        if is_scheduled:
            title = f"Heating System Alert: {status.value.upper()}"
        else:
            title = f"Heating System Health: {status.value.upper()}"

        # Send mobile notification (short)
        await async_send_notification_func(
            hass,
            notify_service,
            title=title,
            message=short_message,
        )

        # Send persistent notification (detailed) if enabled
        if persistent_notification:
            await async_send_persistent_notification_func(
                hass,
                notification_id="adaptive_thermostat_health",
                title=title,
                message=detailed_message,
            )

    return health_result


async def _run_weekly_report_core(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
) -> dict:
    """Core weekly report logic shared by manual and scheduled calls.

    Args:
        hass: Home Assistant instance
        coordinator: Thermostat coordinator
        notify_service: Notification service name
        persistent_notification: Whether to send persistent notifications
        async_send_notification_func: Function to send mobile notifications
        async_send_persistent_notification_func: Function to send persistent notifications

    Returns:
        Report result dictionary with report object and metadata
    """
    from .analytics.reports import WeeklyReport

    _LOGGER.info("Generating weekly report")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    report = WeeklyReport(start_date, end_date)

    # Collect data for each zone
    all_zones = coordinator.get_all_zones()
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

    # Build short summary for mobile
    avg_duty = report.get_average_duty_cycle() if hasattr(report, 'get_average_duty_cycle') else None
    short_parts = []
    if avg_duty is not None:
        short_parts.append(f"Avg {avg_duty:.0f}% duty")
    if has_energy_data and total_cost > 0:
        short_parts.append(f"€{total_cost:.2f} cost")
    short_message = ", ".join(short_parts) if short_parts else "Weekly summary ready"

    title = "Heating System Weekly Report"

    # Send mobile notification (short)
    await async_send_notification_func(
        hass,
        notify_service,
        title=title,
        message=short_message,
    )

    # Send persistent notification (detailed) if enabled
    if persistent_notification:
        await async_send_persistent_notification_func(
            hass,
            notification_id="adaptive_thermostat_weekly",
            title=title,
            message=report_text,
        )

    return {
        "report": report,
        "has_energy_data": has_energy_data,
        "total_cost": total_cost,
    }


# =============================================================================
# Service Handlers
# =============================================================================


async def async_handle_run_learning(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    call: ServiceCall,
) -> dict:
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


async def async_handle_health_check(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    call: ServiceCall,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
) -> dict:
    """Handle the health_check service call.

    Returns:
        Health check result dictionary
    """
    _LOGGER.info("Running health check for all zones")
    return await _run_health_check_core(
        hass=hass,
        coordinator=coordinator,
        notify_service=notify_service,
        persistent_notification=persistent_notification,
        async_send_notification_func=async_send_notification_func,
        async_send_persistent_notification_func=async_send_persistent_notification_func,
        is_scheduled=False,
    )


async def async_handle_weekly_report(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    call: ServiceCall,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
) -> None:
    """Handle the weekly_report service call."""
    await _run_weekly_report_core(
        hass=hass,
        coordinator=coordinator,
        notify_service=notify_service,
        persistent_notification=persistent_notification,
        async_send_notification_func=async_send_notification_func,
        async_send_persistent_notification_func=async_send_persistent_notification_func,
    )


async def async_handle_cost_report(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    call: ServiceCall,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
) -> None:
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

    cost = None  # Track if cost was set

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
                    report_lines.append("(Estimated from weekly data)")
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

    # Build short message
    if cost is not None:
        short_message = f"€{cost:.2f} this {period}"
    else:
        short_message = f"{period.capitalize()} cost report ready"

    title = f"Heating System Cost Report ({period.capitalize()})"

    # Send mobile notification (short)
    await async_send_notification_func(
        hass,
        notify_service,
        title=title,
        message=short_message,
    )

    # Send persistent notification (detailed) if enabled
    if persistent_notification:
        await async_send_persistent_notification_func(
            hass,
            notification_id="adaptive_thermostat_cost",
            title=title,
            message=report_text,
        )


async def async_handle_set_vacation_mode(
    hass: HomeAssistant,
    vacation_mode: VacationMode,
    call: ServiceCall,
    default_target_temp: float,
) -> None:
    """Handle the set_vacation_mode service call."""
    enabled = call.data["enabled"]
    target_temp = call.data.get("target_temp", default_target_temp)

    if enabled:
        await vacation_mode.async_enable(target_temp)
    else:
        await vacation_mode.async_disable()


async def async_handle_energy_stats(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    call: ServiceCall,
) -> dict:
    """Handle the energy_stats service call.

    Returns dictionary with:
    - total_power_w: Current total power
    - zone_powers: Dict of zone power values
    - energy_today_kwh: Today's energy if available
    - cost_today: Today's cost if available
    """
    _LOGGER.info("Getting energy statistics")

    result = {
        "total_power_w": None,
        "zone_powers": {},
        "energy_today_kwh": None,
        "cost_today": None,
        "weekly_energy_kwh": None,
        "weekly_cost": None,
    }

    # Get total power
    total_power_state = hass.states.get("sensor.heating_total_power")
    if total_power_state and total_power_state.state not in ("unknown", "unavailable"):
        try:
            result["total_power_w"] = float(total_power_state.state)
            result["zone_powers"] = total_power_state.attributes.get("zone_powers", {})
        except (ValueError, TypeError):
            pass

    # Get weekly cost/energy data
    weekly_cost_state = hass.states.get("sensor.heating_weekly_cost")
    if weekly_cost_state and weekly_cost_state.state not in ("unknown", "unavailable"):
        try:
            result["weekly_cost"] = float(weekly_cost_state.state)
            result["weekly_energy_kwh"] = weekly_cost_state.attributes.get("weekly_energy_kwh")
        except (ValueError, TypeError):
            pass

    # Estimate today's values from weekly if available
    if result["weekly_energy_kwh"] is not None:
        # Rough estimate: divide weekly by 7
        result["energy_today_kwh"] = result["weekly_energy_kwh"] / 7
    if result["weekly_cost"] is not None:
        result["cost_today"] = result["weekly_cost"] / 7

    # Get per-zone duty cycles
    all_zones = coordinator.get_all_zones()
    zone_duty_cycles = {}
    for zone_id in all_zones:
        duty_sensor_id = f"sensor.{zone_id}_duty_cycle"
        duty_state = hass.states.get(duty_sensor_id)
        if duty_state and duty_state.state not in ("unknown", "unavailable"):
            try:
                zone_duty_cycles[zone_id] = float(duty_state.state)
            except (ValueError, TypeError):
                pass
    result["zone_duty_cycles"] = zone_duty_cycles

    _LOGGER.info("Energy stats: total_power=%s W, zones=%d",
                 result["total_power_w"], len(zone_duty_cycles))

    return result


async def async_handle_pid_recommendations(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    call: ServiceCall,
) -> dict:
    """Handle the pid_recommendations service call.

    Returns dictionary with:
    - zones: Dict of zone PID data with current and recommended values
    - zones_with_recommendations: Count
    - zones_insufficient_data: Count
    """
    _LOGGER.info("Getting PID recommendations for all zones")

    all_zones = coordinator.get_all_zones()
    result = {
        "zones": {},
        "zones_with_recommendations": 0,
        "zones_insufficient_data": 0,
        "zones_error": 0,
    }

    for zone_id, zone_data in all_zones.items():
        adaptive_learner = zone_data.get("adaptive_learner")
        climate_entity_id = zone_data.get("climate_entity_id")

        if not adaptive_learner:
            result["zones"][zone_id] = {
                "status": "learning_disabled",
                "current_pid": None,
                "recommended_pid": None,
            }
            continue

        # Get current PID values from climate entity
        state = hass.states.get(climate_entity_id) if climate_entity_id else None
        if not state:
            result["zones"][zone_id] = {
                "status": "entity_not_found",
                "current_pid": None,
                "recommended_pid": None,
            }
            result["zones_error"] += 1
            continue

        current_kp = state.attributes.get("kp", 100.0)
        current_ki = state.attributes.get("ki", 0.01)
        current_kd = state.attributes.get("kd", 0.0)
        current_pid = {"kp": current_kp, "ki": current_ki, "kd": current_kd}

        try:
            cycle_count = adaptive_learner.get_cycle_count()

            # Get recommendation WITHOUT applying it
            recommendation = adaptive_learner.calculate_pid_adjustment(
                current_kp=current_kp,
                current_ki=current_ki,
                current_kd=current_kd,
            )

            if recommendation is None:
                result["zones"][zone_id] = {
                    "status": "insufficient_data",
                    "cycle_count": cycle_count,
                    "current_pid": current_pid,
                    "recommended_pid": None,
                }
                result["zones_insufficient_data"] += 1
            else:
                # Calculate percentage changes
                kp_change = ((recommendation["kp"] - current_kp) / current_kp * 100) if current_kp != 0 else 0
                ki_change = ((recommendation["ki"] - current_ki) / current_ki * 100) if current_ki != 0 else 0
                kd_change = ((recommendation["kd"] - current_kd) / current_kd * 100) if current_kd != 0 else 0

                result["zones"][zone_id] = {
                    "status": "recommendation_available",
                    "cycle_count": cycle_count,
                    "current_pid": current_pid,
                    "recommended_pid": recommendation,
                    "changes_percent": {"kp": kp_change, "ki": ki_change, "kd": kd_change},
                }
                result["zones_with_recommendations"] += 1
        except Exception as e:
            _LOGGER.error("Failed to get PID recommendation for zone %s: %s", zone_id, e)
            result["zones"][zone_id] = {
                "status": "error",
                "error": str(e),
                "current_pid": current_pid,
                "recommended_pid": None,
            }
            result["zones_error"] += 1

    _LOGGER.info(
        "PID recommendations: %d with recommendations, %d insufficient data, %d errors",
        result["zones_with_recommendations"],
        result["zones_insufficient_data"],
        result["zones_error"],
    )

    return result


# =============================================================================
# Scheduled Callbacks
# =============================================================================


async def async_scheduled_health_check(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
    _now,
) -> None:
    """Run scheduled health check and send alerts if issues detected."""
    await _run_health_check_core(
        hass=hass,
        coordinator=coordinator,
        notify_service=notify_service,
        persistent_notification=persistent_notification,
        async_send_notification_func=async_send_notification_func,
        async_send_persistent_notification_func=async_send_persistent_notification_func,
        is_scheduled=True,
    )


async def async_scheduled_weekly_report(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
    _now,
) -> None:
    """Generate and send weekly report on Sunday mornings."""
    # Only run on Sundays (weekday() returns 6 for Sunday)
    if _now.weekday() != 6:
        return

    _LOGGER.info("Generating scheduled weekly report")
    await _run_weekly_report_core(
        hass=hass,
        coordinator=coordinator,
        notify_service=notify_service,
        persistent_notification=persistent_notification,
        async_send_notification_func=async_send_notification_func,
        async_send_persistent_notification_func=async_send_persistent_notification_func,
    )


async def async_daily_learning(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    learning_window_days: int,
    _now,
) -> None:
    """Run adaptive learning analysis daily at 3:00 AM."""
    _LOGGER.info(
        "Starting scheduled daily learning analysis (window: %d days)",
        learning_window_days,
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


# =============================================================================
# Service Registration
# =============================================================================


def async_register_services(
    hass: HomeAssistant,
    coordinator: AdaptiveThermostatCoordinator,
    vacation_mode: VacationMode,
    notify_service: str | None,
    persistent_notification: bool,
    async_send_notification_func,
    async_send_persistent_notification_func,
    vacation_schema,
    cost_report_schema,
    default_vacation_target_temp: float,
) -> None:
    """Register all services for the Adaptive Thermostat integration.

    Args:
        hass: Home Assistant instance
        coordinator: Thermostat coordinator
        vacation_mode: Vacation mode handler
        notify_service: Notification service name
        persistent_notification: Whether to send persistent notifications
        async_send_notification_func: Function to send mobile notifications
        async_send_persistent_notification_func: Function to send persistent notifications
        vacation_schema: Schema for vacation mode service
        cost_report_schema: Schema for cost report service
        default_vacation_target_temp: Default target temp for vacation mode
    """

    # Create service handler wrappers that capture the context
    async def _run_learning_handler(call: ServiceCall) -> dict:
        return await async_handle_run_learning(hass, coordinator, call)

    async def _health_check_handler(call: ServiceCall) -> dict:
        return await async_handle_health_check(
            hass, coordinator, call, notify_service, persistent_notification,
            async_send_notification_func, async_send_persistent_notification_func,
        )

    async def _weekly_report_handler(call: ServiceCall) -> None:
        await async_handle_weekly_report(
            hass, coordinator, call, notify_service, persistent_notification,
            async_send_notification_func, async_send_persistent_notification_func,
        )

    async def _cost_report_handler(call: ServiceCall) -> None:
        await async_handle_cost_report(
            hass, coordinator, call, notify_service, persistent_notification,
            async_send_notification_func, async_send_persistent_notification_func,
        )

    async def _vacation_mode_handler(call: ServiceCall) -> None:
        await async_handle_set_vacation_mode(
            hass, vacation_mode, call, default_vacation_target_temp,
        )

    async def _energy_stats_handler(call: ServiceCall) -> dict:
        return await async_handle_energy_stats(hass, coordinator, call)

    async def _pid_recommendations_handler(call: ServiceCall) -> dict:
        return await async_handle_pid_recommendations(hass, coordinator, call)

    # Register all services
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_LEARNING, _run_learning_handler
    )
    hass.services.async_register(
        DOMAIN, SERVICE_HEALTH_CHECK, _health_check_handler
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WEEKLY_REPORT, _weekly_report_handler
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COST_REPORT, _cost_report_handler,
        schema=cost_report_schema,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_VACATION_MODE, _vacation_mode_handler,
        schema=vacation_schema,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ENERGY_STATS, _energy_stats_handler
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PID_RECOMMENDATIONS, _pid_recommendations_handler
    )

    _LOGGER.debug("Registered %d services for %s domain", 7, DOMAIN)
