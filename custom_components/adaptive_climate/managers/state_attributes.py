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

    New grouped structure:
    - Flat restoration fields: integral, outdoor_temp_lagged, cycle_count, control_output
    - Preset temperatures: away_temp, eco_temp, etc. (if present)
    - status: grouped operational status (activity, overrides)
    - learning: grouped learning metrics (status, confidence)
    - debug: grouped debug info (only if debug mode)

    Args:
        thermostat: The SmartThermostat instance to build attributes for.

    Returns:
        Dictionary of state attributes for exposure in Home Assistant.
    """
    from ..const import DOMAIN

    # Flat restoration fields
    heater_count = thermostat._heater_controller.heater_cycle_count if thermostat._heater_controller else 0
    cooler_count = thermostat._heater_controller.cooler_cycle_count if thermostat._heater_controller else 0
    is_demand_switch = (
        hasattr(thermostat, "_demand_switch_entity_id") and thermostat._demand_switch_entity_id is not None
    )

    attrs: dict[str, Any] = {
        "integration": DOMAIN,
        "control_output": thermostat._control_output,
        "outdoor_temp_lagged": (
            coord.outdoor_temp_lagged
            if (coord := getattr(thermostat, "_coordinator", None)) and hasattr(coord, "outdoor_temp_lagged")
            else getattr(thermostat, "_ext_temp", None)
        ),
        "cycle_count": build_cycle_count(heater_count, cooler_count, is_demand_switch),
        "integral": thermostat.pid_control_i,
    }

    # PID history (flat for RestoreEntity round-trip)
    if thermostat._gains_manager:
        history = thermostat._gains_manager.get_history()
        if history:
            from ..const import ATTR_PID_HISTORY

            attrs[ATTR_PID_HISTORY] = [
                {
                    "timestamp": e["timestamp"],
                    "kp": round(e["kp"], 2),
                    "ki": round(e["ki"], 4),
                    "kd": round(e["kd"], 2),
                    "ke": round(e.get("ke", 0.0), 2),
                    "reason": e["reason"],
                }
                for e in history
            ]

    # Add preset temperatures if they exist
    preset_attrs = [
        "_away_temp",
        "_eco_temp",
        "_boost_temp",
        "_comfort_temp",
        "_home_temp",
        "_sleep_temp",
        "_activity_temp",
    ]
    for attr_name in preset_attrs:
        if hasattr(thermostat, attr_name):
            value = getattr(thermostat, attr_name, None)
            if value is not None:
                attrs[attr_name[1:]] = value  # Remove leading underscore

    # Consolidated status attribute (using new structure from StatusManager)
    attrs["status"] = _build_status_attribute(thermostat)

    # Learning object (grouped: status, confidence, pid_history)
    _add_learning_object(thermostat, attrs)

    # Debug object (grouped by feature)
    debug_mode = thermostat.hass.data.get(DOMAIN, {}).get("debug", False)
    if debug_mode:
        _add_debug_object(thermostat, attrs)

    # Preheat status (legacy - will be moved to debug later)
    _add_preheat_attributes(thermostat, attrs)

    # Humidity detection status (legacy - will be moved to debug later)
    _add_humidity_detection_attributes(thermostat, attrs)

    # Auto mode switching status (coordinator-level)
    _add_auto_mode_switching_attributes(thermostat, attrs)

    return attrs


def _compute_duty_accumulator_pct(thermostat: SmartThermostat) -> float:
    """Compute duty accumulator as percentage of threshold.

    Args:
        thermostat: The SmartThermostat instance.

    Returns:
        Percentage of min_open_time (0.0-200.0, since max is 2x threshold).
    """
    if not thermostat._heater_controller:
        return 0.0

    min_on = thermostat._heater_controller.min_open_time
    if min_on <= 0:
        return 0.0

    accumulator = thermostat._heater_controller.duty_accumulator_seconds
    return round(100.0 * accumulator / min_on, 1)


def _compute_learning_status(
    cycle_count: int,
    convergence_confidence: float,
    heating_type: str,
    is_paused: bool = False,
    contribution_tracker: Any = None,
    mode: Any = None,
) -> str:
    """Compute learning status based on cycle metrics and recovery cycle requirements.

    Args:
        cycle_count: Number of cycles collected
        convergence_confidence: Convergence confidence (0.0-1.0)
        heating_type: HeatingType value (e.g., "floor_hydronic", "radiator")
        is_paused: Whether any pause condition is active (contact_open, humidity_spike, learning_grace)
        contribution_tracker: Optional ConfidenceContributionTracker for tier gate checks
        mode: Optional HVACMode for tier gate checks (defaults to HEAT)

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

    # Check tier gates (recovery cycle requirements) if tracker provided
    can_reach_tier_1 = True
    can_reach_tier_2 = True
    if contribution_tracker is not None:
        can_reach_tier_1 = contribution_tracker.can_reach_tier(1, mode)
        can_reach_tier_2 = contribution_tracker.can_reach_tier(2, mode)

    # Collecting: not enough cycles OR confidence below tier 1 OR not enough recovery cycles
    # Stable: confidence >= tier 1 AND < tier 2 AND enough recovery cycles for tier 1
    # Tuned: confidence >= tier 2 AND < tier 3 AND enough recovery cycles for tier 2
    # Optimized: confidence >= tier 3
    if cycle_count < MIN_CYCLES_FOR_LEARNING or convergence_confidence < scaled_tier_1:
        return "collecting"
    elif not can_reach_tier_1:
        # Confidence threshold met but not enough recovery cycles for tier 1
        return "collecting"
    elif convergence_confidence >= tier_3:
        return "optimized"
    elif convergence_confidence >= scaled_tier_2:
        if can_reach_tier_2:
            return "tuned"
        else:
            # Confidence threshold met but not enough recovery cycles for tier 2
            return "stable"
    else:
        return "stable"


def _add_learning_object(thermostat: SmartThermostat, attrs: dict[str, Any]) -> None:
    """Add learning object with status, confidence, and pid_history.

    Args:
        thermostat: The SmartThermostat instance
        attrs: Dictionary to update with learning object
    """
    from ..const import DOMAIN, MIN_CYCLES_FOR_LEARNING

    # Get adaptive learner and cycle tracker from coordinator
    coordinator = thermostat._coordinator
    if not coordinator:
        # No coordinator means no learning - create empty learning object
        attrs["learning"] = build_learning_object(status="idle", confidence=0)
        return

    # Use typed coordinator method to get zone data
    zone_info = coordinator.get_zone_by_climate_entity(thermostat.entity_id)
    if zone_info is None:
        attrs["learning"] = build_learning_object(status="idle", confidence=0)
        return

    _, zone_data = zone_info
    adaptive_learner = zone_data.get("adaptive_learner")
    cycle_tracker = zone_data.get("cycle_tracker")

    if not adaptive_learner or not cycle_tracker:
        attrs["learning"] = build_learning_object(status="idle", confidence=0)
        return

    # Get cycle count and convergence confidence
    cycle_count = adaptive_learner.get_cycle_count()
    convergence_confidence = adaptive_learner.get_convergence_confidence()

    # Get heating type from thermostat
    heating_type = thermostat._heating_type if hasattr(thermostat, "_heating_type") else None

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

    # Get contribution tracker for tier gate checks
    contribution_tracker = getattr(adaptive_learner, "_contribution_tracker", None)

    # Compute learning status with tier gate checks
    learning_status = _compute_learning_status(
        cycle_count,
        convergence_confidence,
        heating_type,
        is_paused,
        contribution_tracker=contribution_tracker,
    )

    # Build learning object
    confidence_pct = round(convergence_confidence * 100)
    attrs["learning"] = build_learning_object(status=learning_status, confidence=confidence_pct)

    # Fire milestone check (fire-and-forget)
    milestone_tracker = zone_data.get("milestone_tracker")
    if milestone_tracker:
        thermostat.hass.async_create_task(milestone_tracker.async_check_milestone(learning_status, confidence_pct))


def _add_debug_object(thermostat: SmartThermostat, attrs: dict[str, Any]) -> None:
    """Add debug object grouped by feature.

    Args:
        thermostat: The SmartThermostat instance
        attrs: Dictionary to update with debug object
    """
    from ..const import MIN_CYCLES_FOR_LEARNING

    # Get adaptive learner from coordinator
    coordinator = thermostat._coordinator
    adaptive_learner = None
    cycle_tracker = None
    if coordinator:
        zone_info = coordinator.get_zone_by_climate_entity(thermostat.entity_id)
        if zone_info:
            _, zone_data = zone_info
            adaptive_learner = zone_data.get("adaptive_learner")
            cycle_tracker = zone_data.get("cycle_tracker")

    # Collect debug kwargs
    debug_kwargs = {}

    # PWM group
    if thermostat._heater_controller:
        debug_kwargs["pwm_duty_accumulator_pct"] = _compute_duty_accumulator_pct(thermostat)

    # Cycle group
    if cycle_tracker:
        debug_kwargs["cycle_state"] = cycle_tracker.get_state_name()
    if adaptive_learner:
        debug_kwargs["cycle_cycles_collected"] = adaptive_learner.get_cycle_count()
    debug_kwargs["cycle_cycles_required"] = MIN_CYCLES_FOR_LEARNING

    # Undershoot group
    if adaptive_learner and hasattr(adaptive_learner, "_undershoot_detector") and adaptive_learner._undershoot_detector:
        detector = adaptive_learner._undershoot_detector
        debug_kwargs["undershoot_thermal_debt"] = round(detector.thermal_debt, 2)
        debug_kwargs["undershoot_consecutive_failures"] = detector.consecutive_undershoot_cycles
        debug_kwargs["undershoot_ki_boost_applied"] = round(detector.cumulative_ki_multiplier, 3)

    # Build debug object
    attrs["debug"] = build_debug_object(**debug_kwargs)


def _add_preheat_attributes(thermostat: SmartThermostat, attrs: dict[str, Any]) -> None:
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
        current_temp = thermostat._get_current_temp() if hasattr(thermostat, "_get_current_temp") else None
        target_temp = thermostat._get_target_temp() if hasattr(thermostat, "_get_target_temp") else None
        outdoor_temp = getattr(thermostat, "_outdoor_sensor_temp", None)

        # Ensure we have valid numeric values (not MagicMock)
        if (
            isinstance(current_temp, (int, float))
            and isinstance(target_temp, (int, float))
            and isinstance(outdoor_temp, (int, float))
        ):
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
            if thermostat._night_setback_config and "recovery_deadline" in thermostat._night_setback_config:
                deadline_str = thermostat._night_setback_config["recovery_deadline"]
                hour, minute = map(int, deadline_str.split(":"))
                deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # If deadline is in the past today, it's for tomorrow
                if deadline < now:
                    from datetime import timedelta

                    deadline = deadline + timedelta(days=1)

                # Re-get temps in case they weren't set above
                current_temp = thermostat._get_current_temp() if hasattr(thermostat, "_get_current_temp") else None
                target_temp = thermostat._get_target_temp() if hasattr(thermostat, "_get_target_temp") else None
                outdoor_temp = getattr(thermostat, "_outdoor_sensor_temp", None)

                if (
                    isinstance(current_temp, (int, float))
                    and isinstance(target_temp, (int, float))
                    and isinstance(outdoor_temp, (int, float))
                ):
                    # Check if humidity detector is paused
                    humidity_paused = (
                        thermostat._humidity_detector.should_pause()
                        if hasattr(thermostat, "_humidity_detector") and thermostat._humidity_detector
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


def _add_humidity_detection_attributes(thermostat: SmartThermostat, attrs: dict[str, Any]) -> None:
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


def _add_auto_mode_switching_attributes(thermostat: SmartThermostat, attrs: dict[str, Any]) -> None:
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


def build_cycle_count(
    heater_count: int,
    cooler_count: int,
    is_demand_switch: bool,
) -> int | dict[str, int]:
    """Build cycle_count field based on configuration.

    Args:
        heater_count: Number of heater cycles
        cooler_count: Number of cooler cycles
        is_demand_switch: True if using demand_switch (single actuator)

    Returns:
        Single int for demand_switch, dict for heater/cooler
    """
    if is_demand_switch:
        return heater_count  # demand_switch only uses heater_count internally
    return {"heater": heater_count, "cooler": cooler_count}


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
            "activity": str,  # "idle" | "heating" | "cooling" | "preheating" | "settling"
            "overrides": {
                "contact_open": {...} | None,
                "humidity": {...} | None,
                "open_window": {...} | None,
                "preheating": {...} | None,
                "night_setback": {...} | None,
                "learning_grace": {...} | None,
            }
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
    hvac_mode = thermostat.hvac_mode if hasattr(thermostat, "hvac_mode") else "off"
    if hasattr(hvac_mode, "value"):
        hvac_mode = hvac_mode.value

    # Get heater/cooler state
    heater_on = False
    cooler_on = False
    if thermostat._heater_controller:
        heater_on = getattr(thermostat._heater_controller, "heater_on", False)
        cooler_on = getattr(thermostat._heater_controller, "cooler_on", False)

    # Get preheat state
    preheat_active = False
    if hasattr(thermostat, "_night_setback_controller") and thermostat._night_setback_controller:
        # Check if preheat is currently active
        try:
            if hasattr(thermostat._night_setback_controller, "calculator"):
                # Get preheat info - this requires current conditions
                # For now, just check if preheat learner exists
                preheat_active = getattr(thermostat, "_preheat_active", False)
        except (TypeError, AttributeError):
            pass

    # Get cycle state
    cycle_state = None
    if hasattr(thermostat, "_cycle_tracker") and thermostat._cycle_tracker:
        try:
            cycle_state = thermostat._cycle_tracker.get_state_name()
        except (TypeError, AttributeError):
            pass

    # === Contact open override data ===
    contact_open = False
    contact_sensors = None
    contact_since = None
    if thermostat._contact_sensor_handler:
        contact_open = thermostat._contact_sensor_handler.is_any_contact_open()
        if contact_open:
            contact_sensors = thermostat._contact_sensor_handler.get_open_sensor_ids()
            # Get timestamp when first contact opened
            try:
                first_open_time = thermostat._contact_sensor_handler.get_first_open_time()
                if first_open_time:
                    contact_since = first_open_time.isoformat()
            except (TypeError, AttributeError):
                pass

    # === Humidity override data ===
    humidity_active = False
    humidity_state = None
    humidity_resume_at = None
    if thermostat._humidity_detector:
        humidity_active = thermostat._humidity_detector.should_pause()
        if humidity_active:
            humidity_state = thermostat._humidity_detector.get_state()
            # Calculate resume timestamp
            resume_in_seconds = thermostat._humidity_detector.get_time_until_resume()
            if resume_in_seconds and resume_in_seconds > 0:
                from datetime import timedelta

                resume_time = dt_util.utcnow() + timedelta(seconds=resume_in_seconds)
                humidity_resume_at = resume_time.isoformat()

    # === Open window override data ===
    open_window_active = False
    open_window_since = None
    open_window_resume_at = None
    # TODO: Get from open window detector when implemented

    # === Preheating override data ===
    preheating_active = preheat_active
    preheating_target_time = None
    preheating_started_at = None
    preheating_target_delta = None
    # TODO: Extract preheat details from night setback controller when available

    # === Night setback override data ===
    night_setback_active = False
    night_setback_delta = None
    night_setback_ends_at = None
    night_setback_limited_to = None
    if thermostat._night_setback_controller:
        try:
            _, in_night, info = thermostat._night_setback_controller.calculate_night_setback_adjustment()
            if in_night:
                night_setback_delta = info.get("night_setback_delta")
                # Only show override if delta > 0 (not fully suppressed by learning gate)
                if night_setback_delta and night_setback_delta > 0:
                    night_setback_active = True
                    setback_end_time = info.get("night_setback_end")
                    # Convert "HH:MM" to ISO8601
                    if setback_end_time:
                        from ..managers.status_manager import convert_setback_end

                        night_setback_ends_at = convert_setback_end(setback_end_time)
                    # Check if limited by learning gate
                    suppressed_reason = info.get("suppressed_reason")
                    if suppressed_reason == "limited":
                        night_setback_limited_to = night_setback_delta
        except (TypeError, AttributeError, ValueError):
            pass

    # === Learning grace override data ===
    learning_grace_active = False
    learning_grace_until = None
    if thermostat._night_setback_controller:
        try:
            learning_grace_active = thermostat._night_setback_controller.in_learning_grace_period
            if learning_grace_active:
                # Get grace period end time if available
                grace_end = getattr(thermostat._night_setback_controller, "_learning_grace_end", None)
                if grace_end:
                    learning_grace_until = grace_end.isoformat()
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
        contact_open=contact_open,
        contact_sensors=contact_sensors,
        contact_since=contact_since,
        humidity_active=humidity_active,
        humidity_state=humidity_state,
        humidity_resume_at=humidity_resume_at,
        open_window_active=open_window_active,
        open_window_since=open_window_since,
        open_window_resume_at=open_window_resume_at,
        preheating_active=preheating_active,
        preheating_target_time=preheating_target_time,
        preheating_started_at=preheating_started_at,
        preheating_target_delta=preheating_target_delta,
        night_setback_active=night_setback_active,
        night_setback_delta=night_setback_delta,
        night_setback_ends_at=night_setback_ends_at,
        night_setback_limited_to=night_setback_limited_to,
        learning_grace_active=learning_grace_active,
        learning_grace_until=learning_grace_until,
    )
