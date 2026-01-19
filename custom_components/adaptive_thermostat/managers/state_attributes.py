"""State attribute builder for Adaptive Thermostat."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..climate import SmartThermostat


# Learning/adaptation state attribute constants
ATTR_LEARNING_STATUS = "learning_status"
ATTR_CYCLES_COLLECTED = "cycles_collected"
ATTR_CYCLES_REQUIRED = "cycles_required_for_learning"
ATTR_CONVERGENCE_CONFIDENCE = "convergence_confidence_pct"
ATTR_CURRENT_CYCLE_STATE = "current_cycle_state"
ATTR_LAST_CYCLE_INTERRUPTED = "last_cycle_interrupted"
ATTR_LAST_PID_ADJUSTMENT = "last_pid_adjustment"


def build_state_attributes(thermostat: SmartThermostat) -> dict[str, Any]:
    """Build the extra state attributes dictionary for a thermostat entity.

    Args:
        thermostat: The SmartThermostat instance to build attributes for.

    Returns:
        Dictionary of state attributes for exposure in Home Assistant.
    """
    from ..const import DOMAIN

    # Core attributes - always present
    attrs: dict[str, Any] = {
        "away_temp": thermostat._away_temp,
        "eco_temp": thermostat._eco_temp,
        "boost_temp": thermostat._boost_temp,
        "comfort_temp": thermostat._comfort_temp,
        "home_temp": thermostat._home_temp,
        "sleep_temp": thermostat._sleep_temp,
        "activity_temp": thermostat._activity_temp,
        "control_output": thermostat._control_output,
        "kp": thermostat._kp,
        "ki": thermostat._ki,
        "kd": thermostat._kd,
        "ke": thermostat._ke,
        "pid_mode": thermostat.pid_mode,
        "pid_i": thermostat.pid_control_i,
        # Migration markers for dimensional analysis fixes
        # pid_integral_migrated: integral stored in hourly units (not seconds) - v0.7.0
        # ke_v071_migrated: Ke restored to proper range (100x from v0.7.0) - v0.7.1
        "pid_integral_migrated": True,
        "ke_v071_migrated": True,
        # Outdoor temperature lag state
        "outdoor_temp_lagged": thermostat._pid_controller.outdoor_temp_lagged,
        "outdoor_temp_lag_tau": thermostat._pid_controller.outdoor_temp_lag_tau,
        # Actuator wear tracking - cycle counts
        "heater_cycle_count": (
            thermostat._heater_controller.heater_cycle_count
            if thermostat._heater_controller
            else 0
        ),
        "cooler_cycle_count": (
            thermostat._heater_controller.cooler_cycle_count
            if thermostat._heater_controller
            else 0
        ),
    }

    # Debug-only attributes
    if thermostat.hass.data.get(DOMAIN, {}).get("debug", False):
        attrs.update({
            "pid_p": thermostat.pid_control_p,
            "pid_d": thermostat.pid_control_d,
            "pid_e": thermostat.pid_control_e,
            "pid_dt": thermostat._dt,
        })

    # Night setback attributes
    _add_night_setback_attributes(thermostat, attrs)

    # Learning grace period
    _add_learning_grace_attributes(thermostat, attrs)

    # Zone linking status
    _add_zone_linking_attributes(thermostat, attrs)

    # Heater control failure status
    _add_heater_failure_attributes(thermostat, attrs)

    # Contact sensor status
    _add_contact_sensor_attributes(thermostat, attrs)

    # Ke learning status
    _add_ke_learning_attributes(thermostat, attrs)

    # Learning/adaptation status
    _add_learning_status_attributes(thermostat, attrs)

    return attrs


def _add_night_setback_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add night setback related attributes."""
    if thermostat._night_setback or thermostat._night_setback_config:
        _, _, night_info = thermostat._calculate_night_setback_adjustment()
        attrs.update(night_info)


def _add_learning_grace_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add learning grace period attributes."""
    if thermostat.in_learning_grace_period:
        attrs["learning_paused"] = True
        grace_until = (
            thermostat._night_setback_controller.learning_grace_until
            if thermostat._night_setback_controller
            else thermostat._learning_grace_until
        )
        if grace_until:
            attrs["learning_resumes"] = grace_until.strftime("%H:%M")


def _add_zone_linking_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add zone linking status attributes."""
    if thermostat._zone_linker:
        is_delayed = thermostat._zone_linker.is_zone_delayed(thermostat._unique_id)
        attrs["zone_link_delayed"] = is_delayed
        if is_delayed:
            remaining = thermostat._zone_linker.get_delay_remaining_minutes(
                thermostat._unique_id
            )
            attrs["zone_link_delay_remaining"] = round(remaining, 1) if remaining else 0
        if thermostat._linked_zones:
            attrs["linked_zones"] = thermostat._linked_zones


def _add_heater_failure_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add heater control failure attributes."""
    if thermostat._heater_control_failed:
        attrs["heater_control_failed"] = True
        attrs["last_heater_error"] = thermostat._last_heater_error


def _add_contact_sensor_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add contact sensor status attributes."""
    if thermostat._contact_sensor_handler:
        is_open = thermostat._contact_sensor_handler.is_any_contact_open()
        is_paused = thermostat._contact_sensor_handler.should_take_action()
        attrs["contact_open"] = is_open
        attrs["contact_paused"] = is_paused
        if is_open and not is_paused:
            time_until = thermostat._contact_sensor_handler.get_time_until_action()
            if time_until is not None and time_until > 0:
                attrs["contact_pause_in"] = time_until


def _add_ke_learning_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add Ke learning status attributes."""
    from ..const import DOMAIN

    if thermostat._ke_learner:
        attrs["ke_learning_enabled"] = thermostat._ke_learner.enabled
        attrs["ke_observations"] = thermostat._ke_learner.observation_count

        # Include PID convergence status from coordinator's adaptive learner
        coordinator = thermostat.hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator:
            all_zones = coordinator.get_all_zones()
            for zone_id, zone_data in all_zones.items():
                if zone_data.get("climate_entity_id") == thermostat.entity_id:
                    adaptive_learner = zone_data.get("adaptive_learner")
                    if adaptive_learner:
                        attrs["pid_converged"] = (
                            adaptive_learner.is_pid_converged_for_ke()
                        )
                        attrs["consecutive_converged_cycles"] = (
                            adaptive_learner.get_consecutive_converged_cycles()
                        )
                    break


def _compute_learning_status(
    cycle_count: int,
    convergence_confidence: float,
    consecutive_converged: int,
) -> str:
    """Compute learning status based on cycle metrics.

    Args:
        cycle_count: Number of cycles collected
        convergence_confidence: Convergence confidence (0.0-1.0)
        consecutive_converged: Number of consecutive converged cycles

    Returns:
        Learning status string: "collecting" | "ready" | "active" | "converged"
    """
    from ..const import MIN_CYCLES_FOR_LEARNING, MIN_CONVERGENCE_CYCLES_FOR_KE

    if cycle_count < MIN_CYCLES_FOR_LEARNING:
        return "collecting"
    elif consecutive_converged >= MIN_CONVERGENCE_CYCLES_FOR_KE:
        return "converged"
    elif convergence_confidence >= 0.5:
        return "active"
    else:
        return "ready"


def _add_learning_status_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add learning/adaptation status attributes."""
    from ..const import DOMAIN, MIN_CYCLES_FOR_LEARNING

    # Get adaptive learner and cycle tracker from coordinator
    coordinator = thermostat.hass.data.get(DOMAIN, {}).get("coordinator")
    if not coordinator:
        return

    all_zones = coordinator.get_all_zones()
    for zone_id, zone_data in all_zones.items():
        if zone_data.get("climate_entity_id") == thermostat.entity_id:
            adaptive_learner = zone_data.get("adaptive_learner")
            cycle_tracker = zone_data.get("cycle_tracker")

            if not adaptive_learner or not cycle_tracker:
                return

            # Get cycle count
            cycle_count = adaptive_learner.get_cycle_count()
            attrs[ATTR_CYCLES_COLLECTED] = cycle_count
            attrs[ATTR_CYCLES_REQUIRED] = MIN_CYCLES_FOR_LEARNING

            # Get convergence confidence (0.0-1.0 -> 0-100%)
            convergence_confidence = adaptive_learner.get_convergence_confidence()
            attrs[ATTR_CONVERGENCE_CONFIDENCE] = round(convergence_confidence * 100)

            # Get consecutive converged cycles
            consecutive_converged = adaptive_learner.get_consecutive_converged_cycles()

            # Compute learning status
            attrs[ATTR_LEARNING_STATUS] = _compute_learning_status(
                cycle_count, convergence_confidence, consecutive_converged
            )

            # Get current cycle state
            attrs[ATTR_CURRENT_CYCLE_STATE] = cycle_tracker.get_state_name()

            # Get last interruption reason
            last_interruption = cycle_tracker.get_last_interruption_reason()
            attrs[ATTR_LAST_CYCLE_INTERRUPTED] = last_interruption

            # Get last PID adjustment timestamp
            last_adjustment = adaptive_learner.get_last_adjustment_time()
            if last_adjustment:
                # Format as ISO 8601 timestamp
                attrs[ATTR_LAST_PID_ADJUSTMENT] = last_adjustment.isoformat()
            else:
                attrs[ATTR_LAST_PID_ADJUSTMENT] = None

            # Auto-apply status attributes
            from ..const import (
                ATTR_AUTO_APPLY_ENABLED,
                ATTR_AUTO_APPLY_COUNT,
                ATTR_VALIDATION_MODE,
                ATTR_PID_HISTORY,
            )

            attrs[ATTR_AUTO_APPLY_ENABLED] = getattr(thermostat, "_auto_apply_pid", False)
            attrs[ATTR_AUTO_APPLY_COUNT] = adaptive_learner.get_auto_apply_count()
            attrs[ATTR_VALIDATION_MODE] = adaptive_learner.is_in_validation_mode()

            # Format PID history (all entries for persistence and rollback support)
            pid_history = adaptive_learner.get_pid_history()
            if pid_history:
                formatted_history = [
                    {
                        "timestamp": entry["timestamp"].isoformat(),
                        "kp": round(entry["kp"], 2),
                        "ki": round(entry["ki"], 4),
                        "kd": round(entry["kd"], 2),
                        "reason": entry["reason"],
                    }
                    for entry in pid_history
                ]
                attrs[ATTR_PID_HISTORY] = formatted_history
            else:
                attrs[ATTR_PID_HISTORY] = []

            break
