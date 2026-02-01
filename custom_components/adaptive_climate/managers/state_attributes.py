"""State attribute builder for Adaptive Climate."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..climate import SmartThermostat

from homeassistant.util import dt as dt_util


# Learning/adaptation state attribute constants
ATTR_LEARNING_STATUS = "learning_status"
ATTR_CYCLES_COLLECTED = "cycles_collected"
ATTR_CONVERGENCE_CONFIDENCE = "convergence_confidence_pct"

# Optimized status threshold - 95% confidence
OPTIMIZED_CONFIDENCE_THRESHOLD = 0.95


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
        # PID integral - always persisted for restoration
        "integral": thermostat.pid_control_i,
    }

    # Debug-only core attributes
    debug_mode = thermostat.hass.data.get(DOMAIN, {}).get("debug", False)
    if debug_mode:
        attrs["duty_accumulator_pct"] = _compute_duty_accumulator_pct(thermostat)

    # Consolidated status attribute
    attrs["status"] = _build_status_attribute(thermostat)

    # Learning/adaptation status
    _add_learning_status_attributes(thermostat, attrs)

    # Preheat status
    _add_preheat_attributes(thermostat, attrs)

    # Humidity detection status
    _add_humidity_detection_attributes(thermostat, attrs)

    # Auto mode switching status (coordinator-level)
    _add_auto_mode_switching_attributes(thermostat, attrs)

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
    heating_type: str,
    is_paused: bool = False,
) -> str:
    """Compute learning status based on cycle metrics.

    Args:
        cycle_count: Number of cycles collected
        convergence_confidence: Convergence confidence (0.0-1.0)
        heating_type: HeatingType value (e.g., "floor_hydronic", "radiator")
        is_paused: Whether any pause condition is active (contact_open, humidity_spike, learning_grace)

    Returns:
        Learning status string: "idle" | "collecting" | "stable" | "tuned" | "optimized"
    """
    from ..const import (
        MIN_CYCLES_FOR_LEARNING,
        CONFIDENCE_TIER_1,
        CONFIDENCE_TIER_2,
        CONFIDENCE_TIER_3,
        HEATING_TYPE_CONFIDENCE_SCALE,
        HeatingType,
    )

    # Return idle first if any pause condition is active
    if is_paused:
        return "idle"

    # Get heating-type-specific confidence scaling factor
    # Default to CONVECTOR (1.0) if heating_type not recognized
    scale = HEATING_TYPE_CONFIDENCE_SCALE.get(
        heating_type, HEATING_TYPE_CONFIDENCE_SCALE.get(HeatingType.CONVECTOR, 1.0)
    )

    # Calculate scaled thresholds (tier 3 is NOT scaled - always 95%)
    scaled_tier_1 = min(CONFIDENCE_TIER_1 * scale / 100.0, 0.95)  # Cap at 95%
    scaled_tier_2 = min(CONFIDENCE_TIER_2 * scale / 100.0, 0.95)  # Cap at 95%
    tier_3 = CONFIDENCE_TIER_3 / 100.0  # Always 95%

    # Collecting: not enough cycles OR confidence below tier 1
    # Stable: confidence >= tier 1 AND < tier 2
    # Tuned: confidence >= tier 2 AND < tier 3
    # Optimized: confidence >= tier 3
    if cycle_count < MIN_CYCLES_FOR_LEARNING or convergence_confidence < scaled_tier_1:
        return "collecting"
    elif convergence_confidence >= tier_3:
        return "optimized"
    elif convergence_confidence >= scaled_tier_2:
        return "tuned"
    else:
        return "stable"


def _add_learning_status_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add learning/adaptation status attributes.

    Exposes only essential learning metrics:
    - learning_status: overall learning state
    - pid_history: list of PID adjustments (if any)

    Debug mode adds:
    - cycles_collected: number of complete cycles observed
    - convergence_confidence_pct: 0-100% confidence in convergence
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

    # Get cycle count and convergence confidence (needed for learning_status computation)
    cycle_count = adaptive_learner.get_cycle_count()
    convergence_confidence = adaptive_learner.get_convergence_confidence()

    # Get heating type from thermostat
    heating_type = thermostat._heating_type if hasattr(thermostat, '_heating_type') else None

    # Detect all pause conditions
    is_paused = False

    # Check learning grace period
    if thermostat._night_setback_controller:
        try:
            is_paused = thermostat._night_setback_controller.in_learning_grace_period
        except (TypeError, AttributeError):
            pass

    # Check contact sensor pause
    if not is_paused and thermostat._contact_sensor_handler:
        try:
            is_paused = thermostat._contact_sensor_handler.is_any_contact_open()
        except (TypeError, AttributeError):
            pass

    # Check humidity pause
    if not is_paused and thermostat._humidity_detector:
        try:
            is_paused = thermostat._humidity_detector.should_pause()
        except (TypeError, AttributeError):
            pass

    # Compute learning status
    attrs[ATTR_LEARNING_STATUS] = _compute_learning_status(
        cycle_count, convergence_confidence, heating_type, is_paused
    )

    # Debug-only attributes
    if debug_mode:
        attrs[ATTR_CYCLES_COLLECTED] = cycle_count
        attrs[ATTR_CONVERGENCE_CONFIDENCE] = round(convergence_confidence * 100)
        attrs["current_cycle_state"] = cycle_tracker.get_state_name()
        attrs["cycles_required_for_learning"] = MIN_CYCLES_FOR_LEARNING

        # Undershoot detector debug attributes
        if hasattr(adaptive_learner, '_undershoot_detector') and adaptive_learner._undershoot_detector:
            detector = adaptive_learner._undershoot_detector
            attrs["undershoot_time_hours"] = round(detector.time_below_target / 3600.0, 2)
            attrs["undershoot_thermal_debt"] = round(detector.thermal_debt, 2)
            attrs["undershoot_ki_multiplier"] = round(detector.cumulative_ki_multiplier, 3)

    # Format PID history (only include if non-empty)
    # PID history is now managed by PIDGainsManager, not AdaptiveLearner
    pid_history = thermostat._gains_manager.get_history() if thermostat._gains_manager else []
    if pid_history:
        from ..const import ATTR_PID_HISTORY
        formatted_history = [
            {
                "timestamp": entry["timestamp"],  # Already ISO string from PIDGainsManager
                "kp": round(entry["kp"], 2),
                "ki": round(entry["ki"], 4),
                "kd": round(entry["kd"], 2),
                "ke": round(entry.get("ke", 0.0), 2),
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

                    # Get effective delta from night setback calculation
                    # This accounts for learning gate limitations
                    effective_delta = None
                    try:
                        _, _, night_info = thermostat._night_setback_controller.calculate_night_setback_adjustment(now)
                        effective_delta = night_info.get("effective_delta")
                    except (TypeError, AttributeError):
                        pass

                    preheat_info = thermostat._night_setback_controller.calculator.get_preheat_info(
                        now=now,
                        current_temp=current_temp,
                        target_temp=target_temp,
                        outdoor_temp=outdoor_temp,
                        deadline=deadline,
                        humidity_paused=humidity_paused,
                        effective_delta=effective_delta,
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


def _add_auto_mode_switching_attributes(
    thermostat: SmartThermostat, attrs: dict[str, Any]
) -> None:
    """Add auto mode switching state attributes from coordinator.

    Args:
        thermostat: The SmartThermostat instance
        attrs: Dictionary to update with auto mode switching attributes
    """
    from ..const import DOMAIN

    coordinator = thermostat._coordinator
    if not coordinator or not coordinator.auto_mode_switching_enabled:
        return

    auto_mode_mgr = coordinator.auto_mode_switching
    if not auto_mode_mgr:
        return

    debug = thermostat.hass.data.get(DOMAIN, {}).get("debug", False)
    attrs.update(auto_mode_mgr.get_state_attributes(debug=debug))


def build_learning_object(status: str, confidence: int) -> dict[str, Any]:
    """Build learning status object.

    Args:
        status: Learning status ("idle"|"collecting"|"stable"|"tuned"|"optimized")
        confidence: Convergence confidence 0-100%

    Returns:
        Dict with status and confidence
    """
    return {"status": status, "confidence": confidence}


def build_debug_object(**kwargs) -> dict[str, Any]:
    """Build debug object grouped by feature.

    Groups:
    - pwm: duty_accumulator_pct
    - cycle: state, cycles_collected, cycles_required
    - preheat: heating_rate_learned, observation_count
    - humidity: state, peak
    - undershoot: thermal_debt, consecutive_failures, ki_boost_applied
    - ke: observations, current_ke
    - pid: p_term, i_term, d_term, e_term, f_term

    Args:
        **kwargs: Prefixed args like pwm_duty_accumulator_pct, cycle_state, etc.

    Returns:
        Dict with feature groups, empty groups omitted
    """
    debug: dict[str, Any] = {}

    # PWM group
    pwm = {}
    if kwargs.get("pwm_duty_accumulator_pct") is not None:
        pwm["duty_accumulator_pct"] = kwargs["pwm_duty_accumulator_pct"]
    if pwm:
        debug["pwm"] = pwm

    # Cycle group
    cycle = {}
    if kwargs.get("cycle_state") is not None:
        cycle["state"] = kwargs["cycle_state"]
    if kwargs.get("cycle_cycles_collected") is not None:
        cycle["cycles_collected"] = kwargs["cycle_cycles_collected"]
    if kwargs.get("cycle_cycles_required") is not None:
        cycle["cycles_required"] = kwargs["cycle_cycles_required"]
    if cycle:
        debug["cycle"] = cycle

    # Preheat group
    preheat = {}
    if kwargs.get("preheat_heating_rate_learned") is not None:
        preheat["heating_rate_learned"] = kwargs["preheat_heating_rate_learned"]
    if kwargs.get("preheat_observation_count") is not None:
        preheat["observation_count"] = kwargs["preheat_observation_count"]
    if preheat:
        debug["preheat"] = preheat

    # Humidity group
    humidity = {}
    if kwargs.get("humidity_state") is not None:
        humidity["state"] = kwargs["humidity_state"]
    if kwargs.get("humidity_peak") is not None:
        humidity["peak"] = kwargs["humidity_peak"]
    if humidity:
        debug["humidity"] = humidity

    # Undershoot group
    undershoot = {}
    if kwargs.get("undershoot_thermal_debt") is not None:
        undershoot["thermal_debt"] = kwargs["undershoot_thermal_debt"]
    if kwargs.get("undershoot_consecutive_failures") is not None:
        undershoot["consecutive_failures"] = kwargs["undershoot_consecutive_failures"]
    if kwargs.get("undershoot_ki_boost_applied") is not None:
        undershoot["ki_boost_applied"] = kwargs["undershoot_ki_boost_applied"]
    if undershoot:
        debug["undershoot"] = undershoot

    # Ke group
    ke = {}
    if kwargs.get("ke_observations") is not None:
        ke["observations"] = kwargs["ke_observations"]
    if kwargs.get("ke_current_ke") is not None:
        ke["current_ke"] = kwargs["ke_current_ke"]
    if ke:
        debug["ke"] = ke

    # PID group
    pid = {}
    if kwargs.get("pid_p_term") is not None:
        pid["p_term"] = kwargs["pid_p_term"]
    if kwargs.get("pid_i_term") is not None:
        pid["i_term"] = kwargs["pid_i_term"]
    if kwargs.get("pid_d_term") is not None:
        pid["d_term"] = kwargs["pid_d_term"]
    if kwargs.get("pid_e_term") is not None:
        pid["e_term"] = kwargs["pid_e_term"]
    if kwargs.get("pid_f_term") is not None:
        pid["f_term"] = kwargs["pid_f_term"]
    if pid:
        debug["pid"] = pid

    return debug


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
    suppressed_reason = None
    allowed_setback = None
    if night_setback_active and thermostat._night_setback_controller:
        try:
            _, _, info = thermostat._night_setback_controller.calculate_night_setback_adjustment()
            setback_delta = info.get("night_setback_delta")
            setback_end_time = info.get("night_setback_end")
            suppressed_reason = info.get("suppressed_reason")
            # When suppressed_reason is "limited", setback_delta IS the allowed amount
            if suppressed_reason == "limited" and setback_delta is not None:
                allowed_setback = setback_delta
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
        suppressed_reason=suppressed_reason,
        allowed_setback=allowed_setback,
        humidity_peak=humidity_peak,
        open_sensors=open_sensors,
    )
