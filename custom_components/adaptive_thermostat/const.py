"""Constants for Adaptive Thermostat"""
from typing import Dict, Optional

DOMAIN = "adaptive_thermostat"

DEFAULT_NAME = "Adaptive Thermostat"
DEFAULT_OUTPUT_PRECISION = 1
DEFAULT_OUTPUT_MIN = 0
DEFAULT_OUTPUT_MAX = 100
DEFAULT_OUT_CLAMP_LOW = 0
DEFAULT_OUT_CLAMP_HIGH = 100
DEFAULT_PWM = '00:15:00'
DEFAULT_MIN_CYCLE_DURATION = '00:00:00'
DEFAULT_MIN_OFF_CYCLE_DURATION = '00:00:00'
DEFAULT_TOLERANCE = 0.3
DEFAULT_MIN_TEMP = 7.0
DEFAULT_MAX_TEMP = 35.0
DEFAULT_TARGET_TEMP = 20.0
DEFAULT_TARGET_TEMP_STEP = 0.5
DEFAULT_PRECISION = 0.1
DEFAULT_KP = 100
DEFAULT_KI = 0
DEFAULT_KD = 0
DEFAULT_KE = 0
DEFAULT_SAMPLING_PERIOD = '00:00:00'
DEFAULT_CONTROL_INTERVAL = 60  # seconds - used when keep_alive not specified
DEFAULT_SENSOR_STALL = '06:00:00'
DEFAULT_OUTPUT_SAFETY = 5.0
DEFAULT_PRESET_SYNC_MODE = "none"

CONF_HEATER = "heater"
CONF_COOLER = "cooler"
CONF_INVERT_HEATER = 'invert_heater'
CONF_SENSOR = "target_sensor"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_WIND_SPEED_SENSOR = "wind_speed_sensor"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_TARGET_TEMP = "target_temp"
CONF_HOT_TOLERANCE = "hot_tolerance"
CONF_COLD_TOLERANCE = "cold_tolerance"
CONF_AC_MODE = "ac_mode"
CONF_FORCE_OFF_STATE = "force_off_state"
CONF_MIN_CYCLE_DURATION = "min_cycle_duration"
CONF_MIN_OFF_CYCLE_DURATION = "min_off_cycle_duration"
CONF_MIN_CYCLE_DURATION_PID_OFF = 'min_cycle_duration_pid_off'
CONF_MIN_OFF_CYCLE_DURATION_PID_OFF = 'min_off_cycle_duration_pid_off'
CONF_CONTROL_INTERVAL = "control_interval"
CONF_SAMPLING_PERIOD = "sampling_period"
CONF_SENSOR_STALL = 'sensor_stall'
CONF_OUTPUT_SAFETY = 'output_safety'
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_PRESET_SYNC_MODE = "preset_sync_mode"
CONF_AWAY_TEMP = "away_temp"
CONF_ECO_TEMP = "eco_temp"
CONF_BOOST_TEMP = "boost_temp"
CONF_COMFORT_TEMP = "comfort_temp"
CONF_HOME_TEMP = "home_temp"
CONF_SLEEP_TEMP = "sleep_temp"
CONF_ACTIVITY_TEMP = "activity_temp"
CONF_PRECISION = "precision"
CONF_TARGET_TEMP_STEP = "target_temp_step"
CONF_OUTPUT_PRECISION = "output_precision"
CONF_OUTPUT_MIN = "output_min"
CONF_OUTPUT_MAX = "output_max"
CONF_OUT_CLAMP_LOW = "out_clamp_low"
CONF_OUT_CLAMP_HIGH = "out_clamp_high"
CONF_PWM = "pwm"
CONF_BOOST_PID_OFF = 'boost_pid_off'
CONF_DEBUG = 'debug'
DEFAULT_DEBUG = False
CONF_HEATING_TYPE = "heating_type"
CONF_DERIVATIVE_FILTER = "derivative_filter_alpha"
CONF_DISTURBANCE_REJECTION_ENABLED = "disturbance_rejection_enabled"
CONF_KE_LEARNING_FIRST = "ke_learning_first"
CONF_AUTO_APPLY_PID = "auto_apply_pid"

# Heating system types
HEATING_TYPE_FLOOR_HYDRONIC = "floor_hydronic"
HEATING_TYPE_RADIATOR = "radiator"
HEATING_TYPE_CONVECTOR = "convector"
HEATING_TYPE_FORCED_AIR = "forced_air"

# Valid heating types
VALID_HEATING_TYPES = [
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
]

# Heating type characteristics lookup table
# Used by adaptive/physics.py for PID initialization
HEATING_TYPE_CHARACTERISTICS = {
    HEATING_TYPE_FLOOR_HYDRONIC: {
        "pid_modifier": 0.5,      # Very conservative
        "pwm_period": 900,        # 15 minutes
        "derivative_filter_alpha": 0.05,  # Heavy filtering - high thermal mass reduces noise sensitivity
        "baseline_power_w_m2": 20,  # Baseline power density for process gain scaling (W/m²)
        "reference_supply_temp": 45.0,  # Reference supply temp for PID scaling (°C) - typical floor heating
        "description": "Floor heating with water - high thermal mass, slow response",
    },
    HEATING_TYPE_RADIATOR: {
        "pid_modifier": 0.7,      # Moderately conservative
        "pwm_period": 600,        # 10 minutes
        "derivative_filter_alpha": 0.10,  # Moderate filtering
        "baseline_power_w_m2": 50,  # Baseline power density for process gain scaling (W/m²)
        "reference_supply_temp": 70.0,  # Reference supply temp for PID scaling (°C) - traditional radiator
        "description": "Traditional radiators - moderate thermal mass",
    },
    HEATING_TYPE_CONVECTOR: {
        "pid_modifier": 1.0,      # Standard
        "pwm_period": 300,        # 5 minutes
        "derivative_filter_alpha": 0.15,  # Light filtering - default balance
        "baseline_power_w_m2": 60,  # Baseline power density for process gain scaling (W/m²)
        "reference_supply_temp": 55.0,  # Reference supply temp for PID scaling (°C)
        "description": "Convection heaters - low thermal mass, faster response",
    },
    HEATING_TYPE_FORCED_AIR: {
        "pid_modifier": 1.3,      # Aggressive
        "pwm_period": 180,        # 3 minutes
        "derivative_filter_alpha": 0.25,  # Minimal filtering - fast response needed
        "baseline_power_w_m2": 80,  # Baseline power density for process gain scaling (W/m²)
        "reference_supply_temp": 45.0,  # Reference supply temp for PID scaling (°C) - heat pump typical
        "description": "Forced air heating - very low thermal mass, fast response",
    },
}

# Adaptive learning PID limits
# These ensure PID values stay within safe operating ranges
PID_LIMITS = {
    "kp_min": 10.0,
    "kp_max": 500.0,
    "ki_min": 0.0,
    "ki_max": 1000.0,  # Increased from 100.0 to 1000.0 in v0.7.0 (100x scaling for hourly units)
    "kd_min": 0.0,
    "kd_max": 3.3,  # v0.7.1: 60x total reduction for I/D balance (was 5.0, reduced from 200.0 in v0.7.0)
    "ke_min": 0.0,
    "ke_max": 2.0,  # Restored from 0.02 to 2.0 in v0.7.1 (100x restoration)
}

# Convergence thresholds for adaptive learning
# System is considered "tuned" when ALL metrics are within these bounds
# Default thresholds (used for unknown heating types)
DEFAULT_CONVERGENCE_THRESHOLDS = {
    "overshoot_max": 0.2,       # Maximum acceptable overshoot in °C
    "oscillations_max": 1,      # Maximum acceptable oscillations
    "settling_time_max": 60,    # Maximum settling time in minutes
    "rise_time_max": 45,        # Maximum rise time in minutes
}

# Heating-type-specific convergence thresholds
# Slow systems (high thermal mass) get relaxed criteria to avoid false negatives
HEATING_TYPE_CONVERGENCE_THRESHOLDS = {
    HEATING_TYPE_FLOOR_HYDRONIC: {
        "overshoot_max": 0.3,       # Relaxed from 0.2°C - high thermal mass makes precise control harder
        "oscillations_max": 1,      # Same as default
        "settling_time_max": 120,   # Relaxed from 60 min - slow systems take longer to stabilize
        "rise_time_max": 90,        # Relaxed from 45 min - slower heating rate
    },
    HEATING_TYPE_RADIATOR: {
        "overshoot_max": 0.25,      # Slightly relaxed from 0.2°C
        "oscillations_max": 1,      # Same as default
        "settling_time_max": 90,    # Relaxed from 60 min - moderate thermal mass
        "rise_time_max": 60,        # Relaxed from 45 min - moderate heating rate
    },
    HEATING_TYPE_CONVECTOR: {
        "overshoot_max": 0.2,       # Baseline (same as default)
        "oscillations_max": 1,      # Same as default
        "settling_time_max": 60,    # Baseline (same as default)
        "rise_time_max": 45,        # Baseline (same as default)
    },
    HEATING_TYPE_FORCED_AIR: {
        "overshoot_max": 0.15,      # Tightened from 0.2°C - fast systems should be more precise
        "oscillations_max": 1,      # Same as default
        "settling_time_max": 45,    # Tightened from 60 min - fast settling expected
        "rise_time_max": 30,        # Tightened from 45 min - rapid heating rate
    },
}

# Legacy alias for backward compatibility
CONVERGENCE_THRESHOLDS = DEFAULT_CONVERGENCE_THRESHOLDS


def get_convergence_thresholds(heating_type: Optional[str] = None) -> Dict[str, float]:
    """
    Get convergence thresholds for a specific heating type.

    Returns heating-type-specific thresholds if available, otherwise returns default thresholds.
    Slow systems (high thermal mass) get relaxed criteria to avoid false convergence negatives.

    Args:
        heating_type: One of HEATING_TYPE_* constants, or None for default thresholds

    Returns:
        Dict with convergence threshold values (overshoot_max, oscillations_max,
        settling_time_max, rise_time_max)

    Example:
        >>> thresholds = get_convergence_thresholds(HEATING_TYPE_FLOOR_HYDROMIC)
        >>> thresholds["rise_time_max"]
        90  # Relaxed from 45 min default for slow floor heating
    """
    if heating_type and heating_type in HEATING_TYPE_CONVERGENCE_THRESHOLDS:
        return HEATING_TYPE_CONVERGENCE_THRESHOLDS[heating_type]
    return DEFAULT_CONVERGENCE_THRESHOLDS


# Rule threshold multipliers - scale convergence thresholds for rule activation
# Each rule uses: threshold = convergence_threshold * multiplier
RULE_THRESHOLD_MULTIPLIERS = {
    "moderate_overshoot": 1.0,   # Activate at convergence threshold (0.2°C baseline)
    "high_overshoot": 5.0,       # Activate at 5x convergence threshold (1.0°C baseline)
    "slow_response": 4 / 3,      # Activate at 1.33x rise time max (60 min baseline)
    "slow_settling": 1.5,        # Activate at 1.5x settling time max (90 min baseline)
    "undershoot": 1.5,           # Activate at 1.5x convergence threshold (0.3°C baseline)
    "many_oscillations": 3.0,    # Activate at 3x oscillation max (3 oscillations baseline)
    "some_oscillations": 1.0,    # Activate at convergence threshold (1 oscillation baseline)
}

# Rule threshold floors - minimum absolute thresholds to prevent excessive sensitivity
# Floors override calculated thresholds to account for sensor noise and comfort minimums
RULE_THRESHOLD_FLOORS = {
    "moderate_overshoot": 0.15,  # Minimum 0.15°C (sensor noise + comfort minimum)
    "high_overshoot": 1.0,       # Minimum 1.0°C (significant overshoot)
    "undershoot": 0.2,           # Minimum 0.2°C (sensor noise + comfort minimum)
}


def get_rule_thresholds(heating_type: Optional[str] = None) -> Dict[str, float]:
    """
    Get rule activation thresholds for a specific heating type.

    Rule thresholds are coupled to convergence thresholds to maintain consistency:
    each rule threshold is computed by multiplying the corresponding convergence
    threshold by a multiplier from RULE_THRESHOLD_MULTIPLIERS. Floors from
    RULE_THRESHOLD_FLOORS are applied to prevent excessive sensitivity.

    This coupling ensures that slow systems (high thermal mass) get proportionally
    relaxed rule thresholds to match their relaxed convergence criteria.

    Args:
        heating_type: One of HEATING_TYPE_* constants, or None for default thresholds

    Returns:
        Dict with rule threshold values (moderate_overshoot, high_overshoot,
        slow_response, slow_settling, undershoot, many_oscillations, some_oscillations)

    Example:
        >>> thresholds = get_rule_thresholds(HEATING_TYPE_FLOOR_HYDRONIC)
        >>> thresholds["slow_response"]
        120.0  # 90 min rise_time_max * 1.33 multiplier
        >>> thresholds["moderate_overshoot"]
        0.3  # 0.3°C overshoot_max * 1.0 multiplier (no floor applied)
    """
    # Get base convergence thresholds for this heating type
    convergence = get_convergence_thresholds(heating_type)

    # Compute rule thresholds by multiplying convergence values by multipliers
    moderate_overshoot = convergence["overshoot_max"] * RULE_THRESHOLD_MULTIPLIERS["moderate_overshoot"]
    high_overshoot = convergence["overshoot_max"] * RULE_THRESHOLD_MULTIPLIERS["high_overshoot"]
    slow_response = convergence["rise_time_max"] * RULE_THRESHOLD_MULTIPLIERS["slow_response"]
    slow_settling = convergence["settling_time_max"] * RULE_THRESHOLD_MULTIPLIERS["slow_settling"]
    undershoot = convergence["overshoot_max"] * RULE_THRESHOLD_MULTIPLIERS["undershoot"]
    many_oscillations = convergence["oscillations_max"] * RULE_THRESHOLD_MULTIPLIERS["many_oscillations"]
    some_oscillations = convergence["oscillations_max"] * RULE_THRESHOLD_MULTIPLIERS["some_oscillations"]

    # Apply floors to prevent excessive sensitivity
    moderate_overshoot = max(moderate_overshoot, RULE_THRESHOLD_FLOORS["moderate_overshoot"])
    high_overshoot = max(high_overshoot, RULE_THRESHOLD_FLOORS["high_overshoot"])
    undershoot = max(undershoot, RULE_THRESHOLD_FLOORS["undershoot"])

    return {
        "moderate_overshoot": moderate_overshoot,
        "high_overshoot": high_overshoot,
        "slow_response": slow_response,
        "slow_settling": slow_settling,
        "undershoot": undershoot,
        "many_oscillations": many_oscillations,
        "some_oscillations": some_oscillations,
    }


# Rule priority levels for PID adjustment conflict resolution
# Higher priority = takes precedence when rules conflict
RULE_PRIORITY_OSCILLATION = 3   # Safety - prevent oscillation damage
RULE_PRIORITY_OVERSHOOT = 2     # Stability - avoid temperature swings
RULE_PRIORITY_SLOW_RESPONSE = 1  # Performance - improve comfort

# Minimum number of cycles required before adaptive learning can recommend PID changes
# Increased from 3 to 6 in v0.7.0 for robust outlier detection with MAD
MIN_CYCLES_FOR_LEARNING = 6

# Maximum number of cycles to retain in history (FIFO eviction when exceeded)
MAX_CYCLE_HISTORY = 100

# Slow response rule diagnostic thresholds
MIN_OUTDOOR_TEMP_RANGE = 3.0  # Minimum outdoor temp variation needed for correlation analysis (°C)
SLOW_RESPONSE_CORRELATION_THRESHOLD = 0.6  # Correlation threshold for diagnosing Ki vs Kp issues

# Rule hysteresis band for preventing oscillation in PID adjustments (v0.7.0)
RULE_HYSTERESIS_BAND_PCT = 0.20  # 20% hysteresis band between activate/release thresholds

# Minimum interval between PID adjustments in hours (rate limiting)
# Updated from 24h to 8h in v0.7.0 for faster convergence with hybrid gate
MIN_ADJUSTMENT_INTERVAL = 8

# Minimum number of cycles between PID adjustments (hybrid rate limiting gate)
# Works in conjunction with MIN_ADJUSTMENT_INTERVAL - both must be satisfied
MIN_ADJUSTMENT_CYCLES = 3

# Convergence confidence tracking constants
CONVERGENCE_CONFIDENCE_HIGH = 1.0  # Maximum confidence level (fully converged)
CONFIDENCE_DECAY_RATE_DAILY = 0.02  # 2% confidence decay per day
CONFIDENCE_INCREASE_PER_GOOD_CYCLE = 0.1  # 10% confidence increase per good cycle

# Settling detection (v0.7.0)
SETTLING_MAD_THRESHOLD = 0.05  # Maximum MAD (°C) for temperature stability detection

# Settling timeout configuration (v0.7.0) - dynamic timeout based on thermal mass
SETTLING_TIMEOUT_MULTIPLIER = 30  # Multiplier for tau to calculate settling timeout
SETTLING_TIMEOUT_MIN = 60  # Minimum settling timeout in minutes
SETTLING_TIMEOUT_MAX = 240  # Maximum settling timeout in minutes (4 hours)

# Overshoot peak tracking window (v0.7.0) - time window after heater stops to track peaks
OVERSHOOT_PEAK_WINDOW_MINUTES = 45  # Default 45 minutes - prevents late peaks from external factors

# Segment detection noise tolerance
# Temperature fluctuations below this threshold are ignored as sensor noise
SEGMENT_NOISE_TOLERANCE = 0.05  # 0.05C default

# Rate bounds for segment validation (C/hour)
# Rates outside these bounds are rejected as physically impossible
SEGMENT_RATE_MIN = 0.1   # Minimum 0.1 C/hour (extremely slow, but physically possible)
SEGMENT_RATE_MAX = 10.0  # Maximum 10 C/hour (very fast forced air systems)

# System-level configuration
CONF_MAIN_HEATER_SWITCH = "main_heater_switch"
CONF_MAIN_COOLER_SWITCH = "main_cooler_switch"
CONF_SOURCE_STARTUP_DELAY = "source_startup_delay"
CONF_SYNC_MODES = "sync_modes"
CONF_LEARNING_WINDOW_DAYS = "learning_window_days"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_PERSISTENT_NOTIFICATION = "persistent_notification"
CONF_HOUSE_ENERGY_RATING = "house_energy_rating"
CONF_SUPPLY_TEMP_SENSOR = "supply_temp_sensor"
CONF_RETURN_TEMP_SENSOR = "return_temp_sensor"
CONF_FLOW_RATE_SENSOR = "flow_rate_sensor"
CONF_VOLUME_METER_ENTITY = "volume_meter_entity"
CONF_FALLBACK_FLOW_RATE = "fallback_flow_rate"
CONF_ENERGY_METER_ENTITY = "energy_meter_entity"
CONF_ENERGY_COST_ENTITY = "energy_cost_entity"
CONF_SUPPLY_TEMPERATURE = "supply_temperature"

# Supply temperature validation bounds
SUPPLY_TEMP_MIN = 25.0
SUPPLY_TEMP_MAX = 80.0

# Per-zone configuration
CONF_AREA_M2 = "area_m2"
CONF_CEILING_HEIGHT = "ceiling_height"
CONF_WINDOW_AREA_M2 = "window_area_m2"
CONF_WINDOW_ORIENTATION = "window_orientation"
CONF_WINDOW_RATING = "window_rating"
CONF_DEMAND_SWITCH = "demand_switch"

# Thermal coupling configuration
CONF_THERMAL_COUPLING = "thermal_coupling"
CONF_OPEN_ZONES = "open"
CONF_STAIRWELL_ZONES = "stairwell_zones"
CONF_SEED_COEFFICIENTS = "seed_coefficients"
CONF_MIN_LEARNING_EVENTS = "min_learning_events"
CONF_MIN_CYCLE_TIME_WARNING = "min_cycle_time_warning"
CONF_MIN_CYCLE_TIME_CRITICAL = "min_cycle_time_critical"
CONF_MAX_POWER_M2 = "max_power_m2"
CONF_MAX_POWER_W = "max_power_w"  # Total heater power in watts (for process gain scaling)

# Actuator wear tracking configuration
CONF_HEATER_RATED_CYCLES = "heater_rated_cycles"  # Expected lifetime cycles for heater actuator
CONF_COOLER_RATED_CYCLES = "cooler_rated_cycles"  # Expected lifetime cycles for cooler actuator

# Default rated cycles by actuator type
DEFAULT_RATED_CYCLES = {
    "contactor": 100000,  # Mechanical contactors (typical HVAC relay)
    "valve": 50000,       # Motorized valves (higher wear due to mechanical movement)
    "switch": 100000,     # Solid-state relays and electronic switches
}

# Actuator maintenance thresholds (percentage of rated cycles)
ACTUATOR_MAINTENANCE_SOON_PCT = 80   # Warning threshold - maintenance soon
ACTUATOR_MAINTENANCE_DUE_PCT = 90    # Critical threshold - maintenance due

# Contact sensor configuration
CONF_CONTACT_SENSORS = "contact_sensors"
CONF_CONTACT_ACTION = "contact_action"
CONF_CONTACT_DELAY = "contact_delay"
CONF_CONTACT_LEARNING_GRACE = "contact_learning_grace"

# Night setback configuration
CONF_NIGHT_SETBACK = "night_setback"
CONF_NIGHT_SETBACK_ENABLED = "enabled"
CONF_NIGHT_SETBACK_DELTA = "delta"
CONF_NIGHT_SETBACK_START = "start"
CONF_NIGHT_SETBACK_END = "end"
CONF_NIGHT_SETBACK_SOLAR_RECOVERY = "solar_recovery"
CONF_NIGHT_SETBACK_RECOVERY_DEADLINE = "recovery_deadline"
CONF_MIN_EFFECTIVE_ELEVATION = "min_effective_elevation"
DEFAULT_MIN_EFFECTIVE_ELEVATION = 10.0

# Contact action types
CONTACT_ACTION_PAUSE = "pause"
CONTACT_ACTION_FROST = "frost_protection"
CONTACT_ACTION_NONE = "none"

VALID_CONTACT_ACTIONS = [
    CONTACT_ACTION_PAUSE,
    CONTACT_ACTION_FROST,
    CONTACT_ACTION_NONE,
]

# Window orientations
WINDOW_ORIENTATION_NORTH = "north"
WINDOW_ORIENTATION_NORTHEAST = "northeast"
WINDOW_ORIENTATION_EAST = "east"
WINDOW_ORIENTATION_SOUTHEAST = "southeast"
WINDOW_ORIENTATION_SOUTH = "south"
WINDOW_ORIENTATION_SOUTHWEST = "southwest"
WINDOW_ORIENTATION_WEST = "west"
WINDOW_ORIENTATION_NORTHWEST = "northwest"
WINDOW_ORIENTATION_ROOF = "roof"

VALID_WINDOW_ORIENTATIONS = [
    WINDOW_ORIENTATION_NORTH,
    WINDOW_ORIENTATION_NORTHEAST,
    WINDOW_ORIENTATION_EAST,
    WINDOW_ORIENTATION_SOUTHEAST,
    WINDOW_ORIENTATION_SOUTH,
    WINDOW_ORIENTATION_SOUTHWEST,
    WINDOW_ORIENTATION_WEST,
    WINDOW_ORIENTATION_NORTHWEST,
    WINDOW_ORIENTATION_ROOF,
]

# Energy ratings
VALID_ENERGY_RATINGS = [
    "A++++", "A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"
]

# Default values for new configuration options
DEFAULT_SOURCE_STARTUP_DELAY = 30
DEFAULT_CONTACT_DELAY = 120
DEFAULT_CONTACT_LEARNING_GRACE = 300
DEFAULT_LEARNING_WINDOW_DAYS = 7
DEFAULT_MIN_LEARNING_EVENTS = 3
DEFAULT_MIN_CYCLE_TIME_WARNING = 15
DEFAULT_MIN_CYCLE_TIME_CRITICAL = 10
DEFAULT_MAX_POWER_M2 = 20
DEFAULT_CEILING_HEIGHT = 2.5
DEFAULT_WINDOW_RATING = "hr++"
DEFAULT_FALLBACK_FLOW_RATE = 0.5
DEFAULT_NIGHT_SETBACK_DELTA = 2.0
DEFAULT_VACATION_TARGET_TEMP = 12.0
DEFAULT_FROST_PROTECTION_TEMP = 5.0
DEFAULT_SYNC_MODES = True
DEFAULT_DEMAND_THRESHOLD = 5.0  # PID output % threshold for zone demand
DEFAULT_PERSISTENT_NOTIFICATION = True

# Ke Learning constants
# Minimum consecutive converged cycles before Ke learning activates
MIN_CONVERGENCE_CYCLES_FOR_KE = 3
# Minimum observations required for Ke correlation calculation
KE_MIN_OBSERVATIONS = 5
# Minimum outdoor temperature range (Celsius) for meaningful correlation
KE_MIN_TEMP_RANGE = 5.0
# Minimum hours between Ke adjustments (rate limiting)
KE_ADJUSTMENT_INTERVAL = 48
# Step size for Ke adjustments (restored from 0.001 to 0.1 in v0.7.1 - 100x restoration)
KE_ADJUSTMENT_STEP = 0.1
# Minimum absolute correlation to consider Ke adjustment
KE_CORRELATION_THRESHOLD = 0.3
# Maximum observations to retain (FIFO eviction)
KE_MAX_OBSERVATIONS = 100
# Duration in minutes at target temperature to consider steady state
KE_STEADY_STATE_DURATION = 15

# Thermal coupling constants
# Default seed coefficients for thermal coupling between zones
# Values represent expected heat transfer rate (°C/hour per °C source rise)
DEFAULT_SEED_COEFFICIENTS = {
    "same_floor": 0.15,      # Adjacent zones on same floor
    "up": 0.40,              # Heat rises - zone above gets more heat
    "down": 0.10,            # Zone below gets less heat (heat rises)
    "open": 0.60,            # Open floor plan - high coupling
    "stairwell_up": 0.45,    # Stairwell acts as heat chimney
    "stairwell_down": 0.10,  # Stairwell downward (minimal)
}

# Maximum coupling compensation by heating type (°C reduction)
# Faster systems can compensate more aggressively
MAX_COUPLING_COMPENSATION = {
    HEATING_TYPE_FLOOR_HYDRONIC: 1.0,  # Conservative - slow response
    HEATING_TYPE_RADIATOR: 1.2,        # Moderate
    HEATING_TYPE_CONVECTOR: 1.5,       # Standard
    HEATING_TYPE_FORCED_AIR: 2.0,      # Aggressive - fast recovery
}

# Coupling learner constants
COUPLING_MIN_OBSERVATIONS = 3           # Min observations before using learned coefficient
COUPLING_MAX_OBSERVATIONS_PER_PAIR = 50  # Max observations to retain per zone pair (FIFO)
COUPLING_SEED_WEIGHT = 6                # Weight of seed in Bayesian blend (pseudo-observations)
COUPLING_MAX_COEFFICIENT = 0.5          # Maximum allowed coupling coefficient
COUPLING_MIN_DURATION_MINUTES = 15      # Minimum heating duration for valid observation
COUPLING_MIN_SOURCE_RISE = 0.3          # Minimum source temp rise (°C) for valid observation
COUPLING_MAX_OUTDOOR_CHANGE = 3.0       # Max outdoor temp change (°C) during observation
COUPLING_CONFIDENCE_THRESHOLD = 0.3     # Min confidence to use coefficient
COUPLING_CONFIDENCE_MAX = 0.5           # Confidence level for full effect
COUPLING_MASS_RECOVERY_THRESHOLD = 0.5  # Skip observation if >50% zones demanding
COUPLING_VALIDATION_CYCLES = 5          # Cycles to validate coefficient after change
COUPLING_VALIDATION_DEGRADATION = 0.30  # Overshoot increase threshold for rollback (30%)

# Auto-apply PID constants
# Maximum auto-applies per season (90 days) to prevent runaway tuning
MAX_AUTO_APPLIES_PER_SEASON = 5
# Maximum lifetime auto-applies before requiring manual review
MAX_AUTO_APPLIES_LIFETIME = 20
# Maximum cumulative drift from physics baseline (50% = 1.5x original values)
MAX_CUMULATIVE_DRIFT_PCT = 50
# Number of PID snapshots to retain for history/rollback
PID_HISTORY_SIZE = 10
# Number of cycles in validation window after auto-apply
VALIDATION_CYCLE_COUNT = 5
# Performance degradation threshold for validation failure (30% worse)
VALIDATION_DEGRADATION_THRESHOLD = 0.30
# Days to block auto-apply after seasonal shift detected
SEASONAL_SHIFT_BLOCK_DAYS = 7

# Heating-type-specific auto-apply thresholds
# Slow systems (high thermal mass) require higher confidence and longer cooldowns
# to ensure stability before automatic PID changes are applied.
#
# Keys:
#   confidence_first: Required confidence for first auto-apply (no history)
#   confidence_subsequent: Required confidence for subsequent auto-applies
#   min_cycles: Minimum cycles before auto-apply can trigger
#   cooldown_hours: Minimum hours between auto-applies
#   cooldown_cycles: Minimum cycles between auto-applies
AUTO_APPLY_THRESHOLDS = {
    HEATING_TYPE_FLOOR_HYDRONIC: {
        "confidence_first": 0.80,        # High confidence - slow response makes mistakes costly
        "confidence_subsequent": 0.90,   # Very high - each change needs strong evidence
        "min_cycles": 8,                 # More cycles needed due to long cycle times
        "cooldown_hours": 96,            # 4 days between applies
        "cooldown_cycles": 15,           # ~1 week of normal operation
    },
    HEATING_TYPE_RADIATOR: {
        "confidence_first": 0.70,        # Moderate confidence
        "confidence_subsequent": 0.85,   # Higher for subsequent changes
        "min_cycles": 7,                 # Moderate cycle requirement
        "cooldown_hours": 72,            # 3 days between applies
        "cooldown_cycles": 12,           # ~5 days of normal operation
    },
    HEATING_TYPE_CONVECTOR: {
        "confidence_first": 0.60,        # Standard confidence threshold
        "confidence_subsequent": 0.80,   # Higher for subsequent changes
        "min_cycles": 6,                 # Standard cycle requirement
        "cooldown_hours": 48,            # 2 days between applies
        "cooldown_cycles": 10,           # ~3-4 days of normal operation
    },
    HEATING_TYPE_FORCED_AIR: {
        "confidence_first": 0.60,        # Standard confidence (fast recovery if wrong)
        "confidence_subsequent": 0.80,   # Higher for subsequent changes
        "min_cycles": 6,                 # Standard cycle requirement
        "cooldown_hours": 36,            # 1.5 days between applies
        "cooldown_cycles": 8,            # ~2 days of normal operation
    },
}


def get_auto_apply_thresholds(heating_type: Optional[str] = None) -> Dict[str, float]:
    """
    Get auto-apply thresholds for a specific heating type.

    Returns heating-type-specific thresholds if available, otherwise returns
    convector thresholds as the default baseline.

    Args:
        heating_type: One of HEATING_TYPE_* constants, or None for default

    Returns:
        Dict with auto-apply threshold values (confidence_first, confidence_subsequent,
        min_cycles, cooldown_hours, cooldown_cycles)
    """
    if heating_type and heating_type in AUTO_APPLY_THRESHOLDS:
        return AUTO_APPLY_THRESHOLDS[heating_type]
    return AUTO_APPLY_THRESHOLDS[HEATING_TYPE_CONVECTOR]


# Entity attribute constants for auto-apply status
ATTR_AUTO_APPLY_ENABLED = "auto_apply_pid_enabled"
ATTR_AUTO_APPLY_COUNT = "auto_apply_count"
ATTR_VALIDATION_MODE = "validation_mode"
ATTR_PID_HISTORY = "pid_history"
ATTR_PENDING_RECOMMENDATION = "pending_recommendation"

# Floor heating construction configuration
CONF_FLOOR_CONSTRUCTION = 'floor_construction'
CONF_PIPE_SPACING_MM = 'pipe_spacing_mm'

# Top floor material thermal properties
# Properties: conductivity (W/(m·K)), density (kg/m³), specific_heat (J/(kg·K))
TOP_FLOOR_MATERIALS = {
    "ceramic_tile": {
        "conductivity": 1.3,
        "density": 2300,
        "specific_heat": 840,
    },
    "porcelain": {
        "conductivity": 1.5,
        "density": 2400,
        "specific_heat": 880,
    },
    "natural_stone": {
        "conductivity": 2.8,
        "density": 2700,
        "specific_heat": 900,
    },
    "terrazzo": {
        "conductivity": 1.8,
        "density": 2200,
        "specific_heat": 850,
    },
    "polished_concrete": {
        "conductivity": 1.4,
        "density": 2100,
        "specific_heat": 880,
    },
    "hardwood": {
        "conductivity": 0.15,
        "density": 700,
        "specific_heat": 1600,
    },
    "engineered_wood": {
        "conductivity": 0.13,
        "density": 650,
        "specific_heat": 1600,
    },
    "laminate": {
        "conductivity": 0.17,
        "density": 800,
        "specific_heat": 1500,
    },
    "vinyl": {
        "conductivity": 0.19,
        "density": 1200,
        "specific_heat": 1400,
    },
    "carpet": {
        "conductivity": 0.06,
        "density": 200,
        "specific_heat": 1300,
    },
    "cork": {
        "conductivity": 0.04,
        "density": 200,
        "specific_heat": 1800,
    },
}

# Screed material thermal properties
# Properties: conductivity (W/(m·K)), density (kg/m³), specific_heat (J/(kg·K))
SCREED_MATERIALS = {
    "cement": {
        "conductivity": 1.4,
        "density": 2100,
        "specific_heat": 840,
    },
    "anhydrite": {
        "conductivity": 1.2,
        "density": 2000,
        "specific_heat": 1000,
    },
    "lightweight": {
        "conductivity": 0.47,
        "density": 1000,
        "specific_heat": 1000,
    },
    "mastic_asphalt": {
        "conductivity": 0.7,
        "density": 2100,
        "specific_heat": 920,
    },
    "synthetic": {
        "conductivity": 0.3,
        "density": 1200,
        "specific_heat": 1200,
    },
    "self_leveling": {
        "conductivity": 1.3,
        "density": 1900,
        "specific_heat": 900,
    },
    "dry_screed": {
        "conductivity": 0.2,
        "density": 800,
        "specific_heat": 1000,
    },
}

# Pipe spacing efficiency factors
# Maps pipe spacing (mm) to heat distribution efficiency (0-1)
PIPE_SPACING_EFFICIENCY = {
    100: 0.92,
    150: 0.87,
    200: 0.80,
    300: 0.68,
}

# Floor construction thickness limits (mm)
# Used for validation of user-provided thickness values
FLOOR_THICKNESS_LIMITS = {
    'top_floor': (5, 25),
    'screed': (30, 100),
}
