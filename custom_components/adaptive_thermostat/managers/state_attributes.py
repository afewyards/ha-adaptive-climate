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
        "ke": thermostat._ke,
        "pid_mode": thermostat.pid_mode,
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
        # Duty accumulator for sub-threshold PWM outputs
        "duty_accumulator": (
            thermostat._heater_controller.duty_accumulator_seconds
            if thermostat._heater_controller
            else 0.0
        ),
        "duty_accumulator_pct": _compute_duty_accumulator_pct(thermostat),
        # Transport delay from manifold (when heating is active)
        "transport_delay": thermostat._transport_delay,
    }

    # Debug-only attributes
    if thermostat.hass.data.get(DOMAIN, {}).get("debug", False):
        attrs["integral"] = thermostat.pid_control_i

    # Night setback attributes
    _add_night_setback_attributes(thermostat, attrs)

    # Learning grace period
    _add_learning_grace_attributes(thermostat, attrs)

    # Heater control failure status
    _add_heater_failure_attributes(thermostat, attrs)

    # Contact sensor status
    _add_contact_sensor_attributes(thermostat, attrs)

    # Humidity detector status
    _add_humidity_detector_attributes(thermostat, attrs)

    # Ke learning status
    _add_ke_learning_attributes(thermostat, attrs)

    # Learning/adaptation status
    _add_learning_status_attributes(thermostat, attrs)

    # Per-mode convergence confidence
    _add_per_mode_convergence_attributes(thermostat, attrs)

    # Preheat status
    _add_preheat_attributes(thermostat, attrs)

    # Humidity detection status
    _add_humidity_detection_attributes(thermostat, attrs)

    return attrs


def _compute_duty_accumulator_pct(thermostat: SmartThermostat) -> float:
    """Compute duty accumulator as percentage of threshold.

    Args:
        thermostat: The SmartThermostat instance.

    Returns:
        Percentage of min_on_cycle_duration (0.0-200.0, since max is 2x threshold).
    """
    if not thermostat._heater_controller:
        return 0.0

    min_on = thermostat._heater_controller.min_on_cycle_duration
    if min_on <= 0:
        return 0.0

    accumulator = thermostat._heater_controller.duty_accumulator_seconds
    return round(100.0 * accumulator / min_on, 1)


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


def _add_humidity_detector_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add humidity detector status attributes."""
    if thermostat._humidity_detector:
        state = thermostat._humidity_detector.get_state()
        is_paused = thermostat._humidity_detector.should_pause()
        attrs["humidity_detection_state"] = state
        attrs["humidity_paused"] = is_paused
        if state == "stabilizing":
            time_until = thermostat._humidity_detector.get_time_until_resume()
            if time_until is not None and time_until > 0:
                attrs["humidity_resume_in"] = time_until


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

            # Get last PID adjustment timestamp (only include if set)
            last_adjustment = adaptive_learner.get_last_adjustment_time()
            if last_adjustment:
                # Format as ISO 8601 timestamp
                attrs[ATTR_LAST_PID_ADJUSTMENT] = last_adjustment.isoformat()

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

            # Format PID history (only include if non-empty)
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

            break


def _add_per_mode_convergence_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add per-mode convergence confidence attributes.

    Args:
        thermostat: The SmartThermostat instance.
        attrs: Dictionary of state attributes to update.
    """
    from ..const import DOMAIN
    from homeassistant.components.climate import HVACMode

    # Get adaptive learner from coordinator
    coordinator = thermostat.hass.data.get(DOMAIN, {}).get("coordinator")
    if not coordinator:
        return

    all_zones = coordinator.get_all_zones()
    for zone_id, zone_data in all_zones.items():
        if zone_data.get("climate_entity_id") == thermostat.entity_id:
            adaptive_learner = zone_data.get("adaptive_learner")

            if not adaptive_learner:
                return

            # Get convergence confidence for heating mode (0.0-1.0 -> 0-100%)
            heating_confidence = adaptive_learner.get_convergence_confidence(HVACMode.HEAT)
            attrs["heating_convergence_confidence"] = round(heating_confidence * 100)

            # Get convergence confidence for cooling mode (0.0-1.0 -> 0-100%)
            cooling_confidence = adaptive_learner.get_convergence_confidence(HVACMode.COOL)
            attrs["cooling_convergence_confidence"] = round(cooling_confidence * 100)

            break


def _add_preheat_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add preheat-related state attributes.

    Args:
        thermostat: The SmartThermostat instance
        attrs: Dictionary to update with preheat attributes
    """
    from ..const import DOMAIN

    # Only expose in debug mode when preheat is enabled
    if not thermostat.hass.data.get(DOMAIN, {}).get("debug", False):
        return
    if thermostat._preheat_learner is None:
        return

    # Get learner data
    learner = thermostat._preheat_learner
    attrs["preheat_learning_confidence"] = learner.get_confidence()
    attrs["preheat_observation_count"] = learner.get_observation_count()

    # Get learned rate for current conditions (if available)
    # We need current temp, target temp, and outdoor temp
    try:
        current_temp = thermostat._get_current_temp() if hasattr(thermostat, '_get_current_temp') else None
        target_temp = thermostat._get_target_temp() if hasattr(thermostat, '_get_target_temp') else None
        outdoor_temp = getattr(thermostat, '_outdoor_sensor_temp', None)

        # Ensure we have valid numeric values (not MagicMock)
        if (isinstance(current_temp, (int, float)) and
            isinstance(target_temp, (int, float)) and
            isinstance(outdoor_temp, (int, float))):
            delta = target_temp - current_temp
            if delta > 0:
                learned_rate = learner.get_learned_rate(delta, outdoor_temp)
                if learned_rate is not None:
                    attrs["preheat_heating_rate_learned"] = learned_rate
    except (TypeError, AttributeError):
        # If anything goes wrong, just skip setting the learned rate
        pass

    # Get preheat schedule info from night setback controller's calculator
    if thermostat._night_setback_controller:
        try:
            # We need to call get_preheat_info with appropriate parameters
            # Need: now, current_temp, target_temp, outdoor_temp, deadline
            from datetime import datetime
            now = datetime.now()

            # Get deadline from night setback config
            if (thermostat._night_setback_config and
                "recovery_deadline" in thermostat._night_setback_config):
                deadline_str = thermostat._night_setback_config["recovery_deadline"]
                hour, minute = map(int, deadline_str.split(":"))
                deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # If deadline is in the past today, it's for tomorrow
                if deadline < now:
                    from datetime import timedelta
                    deadline = deadline + timedelta(days=1)

                # Re-get temps in case they weren't set above
                current_temp = thermostat._get_current_temp() if hasattr(thermostat, '_get_current_temp') else None
                target_temp = thermostat._get_target_temp() if hasattr(thermostat, '_get_target_temp') else None
                outdoor_temp = getattr(thermostat, '_outdoor_sensor_temp', None)

                if (isinstance(current_temp, (int, float)) and
                    isinstance(target_temp, (int, float)) and
                    isinstance(outdoor_temp, (int, float))):
                    # Check if humidity detector is paused
                    humidity_paused = (
                        thermostat._humidity_detector.should_pause()
                        if hasattr(thermostat, '_humidity_detector') and thermostat._humidity_detector
                        else False
                    )
                    preheat_info = thermostat._night_setback_controller.calculator.get_preheat_info(
                        now=now,
                        current_temp=current_temp,
                        target_temp=target_temp,
                        outdoor_temp=outdoor_temp,
                        deadline=deadline,
                        humidity_paused=humidity_paused,
                    )

                    attrs["preheat_active"] = preheat_info["active"]
                    attrs["preheat_estimated_duration_min"] = int(preheat_info["estimated_duration"])

                    if preheat_info["scheduled_start"] is not None:
                        attrs["preheat_scheduled_start"] = preheat_info["scheduled_start"].isoformat()
        except (TypeError, AttributeError, ValueError):
            # If anything goes wrong, just skip setting schedule info
            pass


def _add_humidity_detection_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add humidity detection state attributes.

    Args:
        thermostat: The SmartThermostat instance
        attrs: Dictionary to update with humidity detection attributes
    """
    # Check if humidity detector exists
    if not thermostat._humidity_detector:
        return

    # Get detector state
    detector = thermostat._humidity_detector
    attrs["humidity_detection_state"] = detector.get_state()
    attrs["humidity_resume_in"] = detector.get_time_until_resume()
