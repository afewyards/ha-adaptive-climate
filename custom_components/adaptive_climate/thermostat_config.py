"""Typed configuration dataclass for AdaptiveThermostat.

A08: Replaces the untyped ``parameters`` dict that was built in
``climate_setup.async_setup_platform`` and passed as ``**kwargs`` to
``AdaptiveThermostat.__init__``.  Constructing this dataclass at setup time
gives Pyright strict-mode coverage over every config field and surfaces
type errors at load time rather than at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.components.climate import HVACMode

from .const import (
    CONTACT_ACTION_PAUSE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_CONTACT_DELAY,
    DEFAULT_HUMIDITY_ABSOLUTE_MAX,
    DEFAULT_HUMIDITY_DETECTION_WINDOW,
    DEFAULT_HUMIDITY_EXIT_DROP,
    DEFAULT_HUMIDITY_EXIT_THRESHOLD,
    DEFAULT_HUMIDITY_MAX_PAUSE,
    DEFAULT_HUMIDITY_SPIKE_THRESHOLD,
    DEFAULT_HUMIDITY_STABILIZATION_DELAY,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_OUT_CLAMP_HIGH,
    DEFAULT_OUT_CLAMP_LOW,
    DEFAULT_OUTPUT_MAX,
    DEFAULT_OUTPUT_MIN,
    DEFAULT_OUTPUT_PRECISION,
    DEFAULT_PRECISION,
    DEFAULT_SETPOINT_DEBOUNCE,
    DEFAULT_TARGET_TEMP_STEP,
    DEFAULT_TOLERANCE,
    DEFAULT_WINDOW_RATING,
    HeatingType,
)


@dataclass
class AdaptiveThermostatConfig:
    """Validated, typed configuration for one AdaptiveThermostat entity.

    Every field maps 1-to-1 to a key that was previously in the untyped
    ``parameters`` dict.  Fields that the schema always provides (via
    ``default=``) are typed as non-optional; fields that can legitimately
    be absent are typed as ``X | None``.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    name: str
    unit: str  # from hass.config.units.temperature_unit
    zone_id: str | None = None
    unique_id: str | None = None
    ha_area: str | None = None  # Home Assistant area to assign entity to

    # ── Sensors ───────────────────────────────────────────────────────────────
    sensor_entity_id: str = ""
    ext_sensor_entity_id: str | None = None
    weather_entity_id: str | None = None
    wind_speed_sensor_entity_id: str | None = None
    humidity_sensor: str | None = None

    # ── Heater / cooler ───────────────────────────────────────────────────────
    heater_entity_id: list[str] | None = None
    cooler_entity_id: list[str] | None = None
    demand_switch_entity_id: list[str] | None = None
    invert_heater: bool = False
    ac_mode: bool = False
    force_off_state: bool = True

    # ── Temperature range ─────────────────────────────────────────────────────
    min_temp: float = DEFAULT_MIN_TEMP
    max_temp: float = DEFAULT_MAX_TEMP
    target_temp: float | None = None
    hot_tolerance: float = DEFAULT_TOLERANCE
    cold_tolerance: float = DEFAULT_TOLERANCE

    # ── Preset temperatures ───────────────────────────────────────────────────
    away_temp: float | None = None
    eco_temp: float | None = None
    boost_temp: float | None = None
    comfort_temp: float | None = None
    home_temp: float | None = None
    sleep_temp: float | None = None
    activity_temp: float | None = None
    preset_sync_mode: str | None = None

    # ── Timing ────────────────────────────────────────────────────────────────
    min_open_time: timedelta = field(default_factory=lambda: timedelta(0))
    min_closed_time: timedelta | None = None
    min_open_time_pid_off: timedelta | None = None
    min_closed_time_pid_off: timedelta | None = None
    control_interval: timedelta | None = None
    sampling_period: timedelta = field(default_factory=lambda: timedelta(0))
    sensor_stall: timedelta = field(default_factory=lambda: timedelta(hours=6))
    pwm: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    valve_actuation_time: float = 0.0  # seconds

    # ── Output ────────────────────────────────────────────────────────────────
    output_safety: float | None = None
    output_precision: int = DEFAULT_OUTPUT_PRECISION
    output_min: float = DEFAULT_OUTPUT_MIN
    output_max: float = DEFAULT_OUTPUT_MAX
    output_clamp_low: float = DEFAULT_OUT_CLAMP_LOW
    output_clamp_high: float = DEFAULT_OUT_CLAMP_HIGH

    # ── Mode / display ────────────────────────────────────────────────────────
    initial_hvac_mode: HVACMode | None = None
    boost_pid_off: bool | None = None
    precision: float = DEFAULT_PRECISION
    target_temp_step: float = DEFAULT_TARGET_TEMP_STEP

    # ── Zone physics ──────────────────────────────────────────────────────────
    heating_type: HeatingType | None = None
    area_m2: float | None = None
    ceiling_height: float = DEFAULT_CEILING_HEIGHT
    window_area_m2: float | None = None
    window_orientation: str | None = None
    window_rating: str = DEFAULT_WINDOW_RATING
    floor_construction: dict | None = None
    max_power_w: float | None = None
    supply_temperature: float | None = None

    # ── PID / adaptive ────────────────────────────────────────────────────────
    derivative_filter_alpha: float | None = None
    auto_apply_pid: bool = True

    # ── Contact sensors ───────────────────────────────────────────────────────
    contact_sensors: list[str] | None = None
    contact_action: str = CONTACT_ACTION_PAUSE
    contact_delay: int = DEFAULT_CONTACT_DELAY

    # ── Humidity detection ────────────────────────────────────────────────────
    humidity_spike_threshold: float = DEFAULT_HUMIDITY_SPIKE_THRESHOLD
    humidity_absolute_max: float = DEFAULT_HUMIDITY_ABSOLUTE_MAX
    humidity_detection_window: int = DEFAULT_HUMIDITY_DETECTION_WINDOW
    humidity_stabilization_delay: int = DEFAULT_HUMIDITY_STABILIZATION_DELAY
    humidity_max_pause_duration: int = DEFAULT_HUMIDITY_MAX_PAUSE
    humidity_exit_threshold: float = DEFAULT_HUMIDITY_EXIT_THRESHOLD
    humidity_exit_drop: float = DEFAULT_HUMIDITY_EXIT_DROP

    # ── Night setback ─────────────────────────────────────────────────────────
    night_setback_config: dict | None = None

    # ── Setpoint boost ────────────────────────────────────────────────────────
    setpoint_boost: bool = True
    setpoint_boost_factor: float | None = None
    setpoint_debounce: float = DEFAULT_SETPOINT_DEBOUNCE
