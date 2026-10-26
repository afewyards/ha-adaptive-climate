"""Service handlers for Adaptive Climate integration."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    HomeAssistant = Any
    ServiceCall = Any
    SupportsResponse = Any

from ..const import DOMAIN

# Import scheduled task functions from scheduled module
from .scheduled import (
    async_scheduled_health_check,
    async_scheduled_weekly_report,
    async_daily_learning,
    _run_health_check_core,
    _run_weekly_report_core,
    _collect_zones_health_data,
)

if TYPE_CHECKING:
    from ..coordinator import AdaptiveThermostatCoordinator
    from ..adaptive.vacation import VacationMode

_LOGGER = logging.getLogger(__name__)

# Service names
SERVICE_RUN_LEARNING = "run_learning"
SERVICE_HEALTH_CHECK = "health_check"
SERVICE_WEEKLY_REPORT = "weekly_report"
SERVICE_SET_VACATION_MODE = "set_vacation_mode"
SERVICE_PID_RECOMMENDATIONS = "pid_recommendations"


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
        pwm_seconds = zone_data.get("pwm_seconds", 0)

        try:
            # Get cycle count for reporting
            cycle_count = adaptive_learner.get_cycle_count()

            # Trigger learning analysis with current PID values
            recommendation = adaptive_learner.calculate_pid_adjustment(
                current_kp=current_kp,
                current_ki=current_ki,
                current_kd=current_kd,
                pwm_seconds=pwm_seconds,
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
                # Calculate percentage changes for logging; use absolute delta label for tiny baselines
                # to avoid cosmetically alarming "100%" changes on values like ki=0.001→0.002.
                def _pct_or_abs(new: float, old: float) -> float:
                    if old == 0 or abs(old) < 0.01:
                        return 0.0  # Show 0% — absolute delta printed separately when needed
                    return (new - old) / old * 100

                kp_change = _pct_or_abs(recommendation["kp"], current_kp)
                ki_change = _pct_or_abs(recommendation["ki"], current_ki)
                kd_change = _pct_or_abs(recommendation["kd"], current_kd)

                _LOGGER.info(
                    "Zone %s PID recommendation: Kp=%.2f (%.1f%%), Ki=%.4f (%.1f%%), Kd=%.2f (%.1f%%)",
                    zone_id,
                    recommendation["kp"],
                    kp_change,
                    recommendation["ki"],
                    ki_change,
                    recommendation["kd"],
                    kd_change,
                )

                results["zones_with_recommendations"] += 1
                results["zone_results"][zone_id] = {
                    "status": "recommendation_available",
                    "cycle_count": cycle_count,
                    "current_pid": {"kp": current_kp, "ki": current_ki, "kd": current_kd},
                    "recommended_pid": recommendation,
                    "changes_percent": {"kp": kp_change, "ki": ki_change, "kd": kd_change},
                }
        except Exception:
            _LOGGER.exception("Learning analysis failed for zone %s", zone_id)
            results["zone_results"][zone_id] = {
                "status": "error",
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
    result = await _run_health_check_core(
        hass=hass,
        coordinator=coordinator,
        notify_service=notify_service,
        persistent_notification=persistent_notification,
        async_send_notification_func=async_send_notification_func,
        async_send_persistent_notification_func=async_send_persistent_notification_func,
        is_scheduled=False,
    )
    # Service responses must be JSON-serializable; the core result holds an Enum and HealthIssue objects.
    return {
        **result,
        "status": result["status"].value,
        "zone_issues": {
            zone: [{"severity": i.severity.value, "issue_type": i.issue_type, "message": i.message} for i in issues]
            for zone, issues in result.get("zone_issues", {}).items()
        },
    }


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
        pwm_seconds = zone_data.get("pwm_seconds", 0)

        try:
            cycle_count = adaptive_learner.get_cycle_count()

            # Get recommendation WITHOUT applying it
            recommendation = adaptive_learner.calculate_pid_adjustment(
                current_kp=current_kp,
                current_ki=current_ki,
                current_kd=current_kd,
                pwm_seconds=pwm_seconds,
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
        except Exception:
            _LOGGER.exception("Failed to get PID recommendation for zone %s", zone_id)
            result["zones"][zone_id] = {
                "status": "error",
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
    default_vacation_target_temp: float,
    debug: bool = False,
) -> None:
    """Register all services for the Adaptive Climate integration.

    Args:
        hass: Home Assistant instance
        coordinator: Thermostat coordinator
        vacation_mode: Vacation mode handler
        notify_service: Notification service name
        persistent_notification: Whether to send persistent notifications
        async_send_notification_func: Function to send mobile notifications
        async_send_persistent_notification_func: Function to send persistent notifications
        vacation_schema: Schema for vacation mode service
        default_vacation_target_temp: Default target temp for vacation mode
        debug: Debug mode flag
    """

    # Create service handler wrappers that capture the context
    async def _run_learning_handler(call: ServiceCall) -> dict:
        return await async_handle_run_learning(hass, coordinator, call)

    async def _health_check_handler(call: ServiceCall) -> dict:
        return await async_handle_health_check(
            hass,
            coordinator,
            call,
            notify_service,
            persistent_notification,
            async_send_notification_func,
            async_send_persistent_notification_func,
        )

    async def _weekly_report_handler(call: ServiceCall) -> None:
        await async_handle_weekly_report(
            hass,
            coordinator,
            call,
            notify_service,
            persistent_notification,
            async_send_notification_func,
            async_send_persistent_notification_func,
        )

    async def _vacation_mode_handler(call: ServiceCall) -> None:
        await async_handle_set_vacation_mode(
            hass,
            vacation_mode,
            call,
            default_vacation_target_temp,
        )

    async def _pid_recommendations_handler(call: ServiceCall) -> dict:
        return await async_handle_pid_recommendations(hass, coordinator, call)

    # Register public services (always available)
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VACATION_MODE,
        _vacation_mode_handler,
        schema=vacation_schema,
    )
    hass.services.async_register(DOMAIN, SERVICE_WEEKLY_REPORT, _weekly_report_handler)

    # Always register learning/recommendation services — they are advertised in services.yaml
    # and users get "service not found" errors when debug=False gates them (H05 fix).
    for name, handler in (
        (SERVICE_RUN_LEARNING, _run_learning_handler),
        (SERVICE_HEALTH_CHECK, _health_check_handler),
        (SERVICE_PID_RECOMMENDATIONS, _pid_recommendations_handler),
    ):
        hass.services.async_register(DOMAIN, name, handler, supports_response=SupportsResponse.OPTIONAL)

    _LOGGER.debug("Registered 5 services for %s domain (debug=%s)", DOMAIN, debug)


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister all services for the Adaptive Climate integration.

    Args:
        hass: Home Assistant instance
    """
    services_to_remove = [
        SERVICE_SET_VACATION_MODE,
        SERVICE_WEEKLY_REPORT,
        SERVICE_RUN_LEARNING,
        SERVICE_HEALTH_CHECK,
        SERVICE_PID_RECOMMENDATIONS,
    ]

    services_removed = 0
    for service in services_to_remove:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
            services_removed += 1

    _LOGGER.debug("Unregistered %d services for %s domain", services_removed, DOMAIN)


# Public API - expose everything that was previously available from services.py
__all__ = [
    "SERVICE_HEALTH_CHECK",
    "SERVICE_PID_RECOMMENDATIONS",
    "SERVICE_RUN_LEARNING",
    "SERVICE_SET_VACATION_MODE",
    "SERVICE_WEEKLY_REPORT",
    "_collect_zones_health_data",
    "_run_health_check_core",
    "_run_weekly_report_core",
    "async_daily_learning",
    "async_handle_health_check",
    "async_handle_pid_recommendations",
    "async_handle_run_learning",
    "async_handle_set_vacation_mode",
    "async_handle_weekly_report",
    "async_register_services",
    "async_scheduled_health_check",
    "async_scheduled_weekly_report",
    "async_unregister_services",
]
