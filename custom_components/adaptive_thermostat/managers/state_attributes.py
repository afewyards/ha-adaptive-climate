"""State attribute builder for Adaptive Thermostat."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..climate import SmartThermostat

from homeassistant.util import dt as dt_util


# Learning/adaptation state attribute constants
ATTR_LEARNING_STATUS = "learning_status"
ATTR_CYCLES_COLLECTED = "cycles_collected"
ATTR_CONVERGENCE_CONFIDENCE = "convergence_confidence_pct"


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
        "integration": DOMAIN,
        "control_output": thermostat._control_output,
        "ke": thermostat._ke,
        "kp": thermostat._kp,
        "ki": thermostat._ki,
        "kd": thermostat._kd,
        "pid_mode": thermostat.pid_mode,
        # Outdoor temperature lag state
        "outdoor_temp_lagged": thermostat._pid_controller.outdoor_temp_lagged,
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
        # Duty accumulator percentage
        "duty_accumulator_pct": _compute_duty_accumulator_pct(thermostat),
        # PID integral - always persisted for restoration
        "integral": thermostat.pid_control_i,
    }

    # Consolidated status attribute
    attrs["status"] = _build_status_attribute(thermostat)

    # Learning/adaptation status
    _add_learning_status_attributes(thermostat, attrs)

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
    """Add learning/adaptation status attributes.

    Exposes only essential learning metrics:
    - learning_status: overall learning state
    - cycles_collected: number of complete cycles observed
    - convergence_confidence_pct: 0-100% confidence in convergence
    - pid_history: list of PID adjustments (if any)

    Debug mode adds:
    - current_cycle_state: current cycle tracker state
    - cycles_required_for_learning: minimum cycles needed
    """
    from ..const import DOMAIN, MIN_CYCLES_FOR_LEARNING

    # Get adaptive learner and cycle tracker from coordinator
    coordinator = thermostat._coordinator
    if not coordinator:
        return

    debug_mode = thermostat.hass.data.get(DOMAIN, {}).get("debug", False)

    # Use typed coordinator method to get zone data
    zone_info = coordinator.get_zone_by_climate_entity(thermostat.entity_id)
    if zone_info is None:
        return

    _, zone_data = zone_info
    adaptive_learner = zone_data.get("adaptive_learner")
    cycle_tracker = zone_data.get("cycle_tracker")

    if not adaptive_learner or not cycle_tracker:
        return

    # Get cycle count
    cycle_count = adaptive_learner.get_cycle_count()
    attrs[ATTR_CYCLES_COLLECTED] = cycle_count

    # Get convergence confidence (0.0-1.0 -> 0-100%)
    convergence_confidence = adaptive_learner.get_convergence_confidence()
    attrs[ATTR_CONVERGENCE_CONFIDENCE] = round(convergence_confidence * 100)

    # Get consecutive converged cycles
    consecutive_converged = adaptive_learner.get_consecutive_converged_cycles()

    # Compute learning status
    attrs[ATTR_LEARNING_STATUS] = _compute_learning_status(
        cycle_count, convergence_confidence, consecutive_converged
    )

    # Debug-only attributes
    if debug_mode:
        attrs["current_cycle_state"] = cycle_tracker.get_state_name()
        attrs["cycles_required_for_learning"] = MIN_CYCLES_FOR_LEARNING

        # Undershoot detector debug attributes
        if hasattr(adaptive_learner, '_undershoot_detector') and adaptive_learner._undershoot_detector:
            detector = adaptive_learner._undershoot_detector
            attrs["undershoot_time_hours"] = round(detector.time_below_target / 3600.0, 2)
            attrs["undershoot_thermal_debt"] = round(detector.thermal_debt, 2)
            attrs["undershoot_ki_multiplier"] = round(detector.cumulative_ki_multiplier, 3)

    # Format PID history (only include if non-empty)
    pid_history = adaptive_learner.get_pid_history()
    if pid_history:
        from ..const import ATTR_PID_HISTORY
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
            now = dt_util.utcnow()

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


def _build_status_attribute(thermostat: SmartThermostat) -> dict[str, Any]:
    """Build consolidated status attribute using StatusManager.

    The status attribute provides unified information about heating status state
    from all possible sources (contact sensors, humidity detection, night setback).

    Args:
        thermostat: The SmartThermostat instance

    Returns:
        Dictionary with structure (new format):
        {
            "state": str,              # "idle" | "heating" | "cooling" | "paused" | "preheating" | "settling"
            "conditions": list[str],   # List of active conditions (e.g., ["contact_open", "humidity_spike"])
            "resume_at": str,          # Optional ISO8601 timestamp when pause ends
            "setback_delta": float,    # Optional temperature delta (night_setback only)
            "setback_end": str,        # Optional ISO8601 timestamp when night period ends
        }
    """
    from ..const import DOMAIN
    from ..managers.status_manager import StatusManager

    # Get debug setting from domain config
    debug = thermostat.hass.data.get(DOMAIN, {}).get("debug", False)

    # Create StatusManager on the fly (for test compatibility)
    # In production, thermostat will have _status_manager already initialized
    status_manager = StatusManager(
        contact_sensor_handler=thermostat._contact_sensor_handler,
        humidity_detector=thermostat._humidity_detector,
        debug=debug,
    )
    if thermostat._night_setback_controller:
        status_manager.set_night_setback_controller(thermostat._night_setback_controller)

    # Determine if heating is paused
    is_paused = status_manager.is_paused()

    # Get HVAC mode
    hvac_mode = thermostat.hvac_mode if hasattr(thermostat, 'hvac_mode') else "off"
    if hasattr(hvac_mode, 'value'):
        hvac_mode = hvac_mode.value

    # Get heater/cooler state
    heater_on = False
    cooler_on = False
    if thermostat._heater_controller:
        heater_on = getattr(thermostat._heater_controller, 'heater_on', False)
        cooler_on = getattr(thermostat._heater_controller, 'cooler_on', False)

    # Get preheat state
    preheat_active = False
    if hasattr(thermostat, '_night_setback_controller') and thermostat._night_setback_controller:
        # Check if preheat is currently active
        try:
            if hasattr(thermostat._night_setback_controller, 'calculator'):
                # Get preheat info - this requires current conditions
                # For now, just check if preheat learner exists
                preheat_active = getattr(thermostat, '_preheat_active', False)
        except (TypeError, AttributeError):
            pass

    # Get cycle state
    cycle_state = None
    if hasattr(thermostat, '_cycle_tracker') and thermostat._cycle_tracker:
        try:
            cycle_state = thermostat._cycle_tracker.get_state_name()
        except (TypeError, AttributeError):
            pass

    # Determine active conditions
    night_setback_active = False
    if thermostat._night_setback_controller:
        try:
            _, in_night, _ = thermostat._night_setback_controller.calculate_night_setback_adjustment()
            night_setback_active = in_night
        except (TypeError, AttributeError, ValueError):
            pass

    open_window_detected = False
    # TODO: Get from open window detector when implemented

    humidity_spike_active = False
    if thermostat._humidity_detector:
        humidity_spike_active = thermostat._humidity_detector.should_pause()

    contact_open = False
    if thermostat._contact_sensor_handler:
        contact_open = thermostat._contact_sensor_handler.is_any_contact_open()

    learning_grace_active = False
    if thermostat._night_setback_controller:
        try:
            learning_grace_active = thermostat._night_setback_controller.in_learning_grace_period
        except (TypeError, AttributeError):
            pass

    # Get resume time (if any pause is active)
    resume_in_seconds = None
    if humidity_spike_active and thermostat._humidity_detector:
        resume_in_seconds = thermostat._humidity_detector.get_time_until_resume()
    elif contact_open and thermostat._contact_sensor_handler:
        # If contact is open but not yet causing pause, get countdown
        if not thermostat._contact_sensor_handler.should_take_action():
            resume_in_seconds = thermostat._contact_sensor_handler.get_time_until_action()

    # Get night setback info
    setback_delta = None
    setback_end_time = None
    if night_setback_active and thermostat._night_setback_controller:
        try:
            _, _, info = thermostat._night_setback_controller.calculate_night_setback_adjustment()
            setback_delta = info.get("night_setback_delta")
            setback_end_time = info.get("night_setback_end")
        except (TypeError, AttributeError, ValueError):
            pass

    # Debug fields
    humidity_peak = None
    open_sensors = None
    if debug:
        if thermostat._humidity_detector and humidity_spike_active:
            try:
                humidity_peak = getattr(thermostat._humidity_detector, '_peak_humidity', None)
            except (TypeError, AttributeError):
                pass
        if thermostat._contact_sensor_handler and contact_open:
            try:
                open_sensors = thermostat._contact_sensor_handler.get_open_sensor_ids()
            except (TypeError, AttributeError):
                pass

    # Build status using StatusManager
    return status_manager.build_status(
        hvac_mode=hvac_mode,
        heater_on=heater_on,
        cooler_on=cooler_on,
        is_paused=is_paused,
        preheat_active=preheat_active,
        cycle_state=cycle_state,
        night_setback_active=night_setback_active,
        open_window_detected=open_window_detected,
        humidity_spike_active=humidity_spike_active,
        contact_open=contact_open,
        learning_grace_active=learning_grace_active,
        resume_in_seconds=resume_in_seconds,
        setback_delta=setback_delta,
        setback_end_time=setback_end_time,
        humidity_peak=humidity_peak,
        open_sensors=open_sensors,
    )
