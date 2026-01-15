"""Scheduled task handlers for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any

from ..const import DOMAIN

if TYPE_CHECKING:
    from ..coordinator import AdaptiveThermostatCoordinator

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
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
    from ..analytics.health import SystemHealthMonitor, HealthStatus

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
    from ..analytics.reports import WeeklyReport
    from ..analytics.history_store import HistoryStore, WeeklySnapshot, ZoneSnapshot
    from ..analytics.charts import ChartGenerator, save_chart_to_www, cleanup_old_charts

    _LOGGER.info("Generating weekly report with charts")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)
    year, week_number, _ = end_date.isocalendar()

    report = WeeklyReport(start_date, end_date)

    # Load history for week-over-week comparison
    history_store = HistoryStore(hass)
    await history_store.async_load()

    # Collect data for each zone
    all_zones = coordinator.get_all_zones()
    total_cost = 0.0
    has_energy_data = False
    zone_snapshots: dict[str, ZoneSnapshot] = {}

    for zone_id in all_zones:
        zone_data = coordinator.get_zone_data(zone_id)

        # Get duty cycle
        duty_sensor_id = f"sensor.{zone_id}_duty_cycle"
        duty_state = hass.states.get(duty_sensor_id)
        duty_cycle = 0.0
        if duty_state and duty_state.state not in ("unknown", "unavailable"):
            try:
                duty_cycle = float(duty_state.state)
            except (ValueError, TypeError):
                pass

        # Get comfort score
        comfort_sensor_id = f"sensor.{zone_id}_comfort_score"
        comfort_state = hass.states.get(comfort_sensor_id)
        comfort_score = None
        if comfort_state and comfort_state.state not in ("unknown", "unavailable"):
            try:
                comfort_score = float(comfort_state.state)
            except (ValueError, TypeError):
                pass

        # Get time at target
        time_at_target_sensor_id = f"sensor.{zone_id}_time_at_target"
        time_at_target_state = hass.states.get(time_at_target_sensor_id)
        time_at_target = None
        if time_at_target_state and time_at_target_state.state not in ("unknown", "unavailable"):
            try:
                time_at_target = float(time_at_target_state.state)
            except (ValueError, TypeError):
                pass

        # Get zone area
        area_m2 = zone_data.get("area_m2") if zone_data else None

        report.add_zone_data(
            zone_id,
            duty_cycle,
            comfort_score=comfort_score,
            time_at_target=time_at_target,
            area_m2=area_m2,
        )

        # Build zone snapshot for history
        zone_snapshots[zone_id] = ZoneSnapshot(
            zone_id=zone_id,
            duty_cycle=duty_cycle,
            comfort_score=comfort_score,
            time_at_target=time_at_target,
            area_m2=area_m2,
        )

    # Get system totals if available
    weekly_cost_state = hass.states.get("sensor.heating_weekly_cost")
    weekly_energy = None

    if weekly_cost_state and weekly_cost_state.state not in ("unknown", "unavailable"):
        try:
            total_cost = float(weekly_cost_state.state)
            has_energy_data = True
            weekly_energy = weekly_cost_state.attributes.get("weekly_energy_kwh", 0)
            report.set_totals(weekly_energy, total_cost)
        except (ValueError, TypeError):
            pass

    # Calculate zone cost breakdown
    report.calculate_zone_costs()

    # Create snapshot for history
    current_snapshot = WeeklySnapshot(
        year=year,
        week_number=week_number,
        total_cost=total_cost if has_energy_data else None,
        total_energy_kwh=weekly_energy,
        zones=zone_snapshots,
        timestamp=datetime.now().isoformat(),
    )

    # Calculate week-over-week comparison
    wow_changes = history_store.calculate_week_over_week(current_snapshot)
    report.set_week_over_week(
        cost_change_pct=wow_changes.get("cost_change_pct"),
        energy_change_pct=wow_changes.get("energy_change_pct"),
    )

    # Get health status
    health_sensor = hass.states.get("sensor.heating_system_health")
    if health_sensor and health_sensor.state not in ("unknown", "unavailable"):
        report.health_status = health_sensor.state

    # Save snapshot to history
    await history_store.async_save_snapshot(current_snapshot)

    # Generate charts
    chart_url = None
    chart_gen = ChartGenerator()

    if chart_gen.available:
        # Generate zone duty cycle chart
        zone_duty_cycles = {
            zone_id.replace("_", " ").title(): data["duty_cycle"]
            for zone_id, data in report.zones.items()
        }

        chart_bytes = chart_gen.create_bar_chart(
            zone_duty_cycles,
            title="Zone Activity This Week",
            unit="%",
            max_value=100,
        )

        if chart_bytes:
            filename = f"weekly_{year}_{week_number:02d}_duty.png"
            chart_url = await save_chart_to_www(hass, chart_bytes, filename)
            _LOGGER.debug("Chart saved to %s", chart_url)

        # Cleanup old charts
        await cleanup_old_charts(hass)

    # Format and send report
    report_text = report.format_report()
    _LOGGER.info("Weekly report generated:\n%s", report_text)

    # Use the new digestible summary
    short_message = report.format_summary()

    title = "Weekly Heating Report"

    # Build notification data with optional image
    notification_data = {}
    if chart_url:
        notification_data["image"] = chart_url

    # Send mobile notification with chart
    await async_send_notification_func(
        hass,
        notify_service,
        title=title,
        message=short_message,
        data=notification_data if notification_data else None,
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
        "chart_url": chart_url,
    }


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
