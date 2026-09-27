"""Pure write-policy helpers for water temperature control.

Stateless rounding/clamping/direction-safety helpers used by
``WaterTempController`` when writing a computed supply temperature to its
target ``number`` / ``input_number`` entity.  Split out of
``water_temp_controller.py`` to keep that module under the project's
800-line ceiling — these functions hold no state of their own.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from ..const import (
    CONF_WATER_TEMP_RAMP_RATE,
    CONF_WATER_TEMP_RAMP_START,
    DEFAULT_WATER_TEMP_COOLING_RAMP_RATE,
    DEFAULT_WATER_TEMP_COOLING_RAMP_START,
    DEFAULT_WATER_TEMP_HEATING_RAMP_RATE,
    DEFAULT_WATER_TEMP_HEATING_RAMP_START,
    DEFAULT_WATER_TEMP_STEP,
    SUPPLY_TEMP_MAX,
    SUPPLY_TEMP_MIN,
    WATER_TEMP_MODE_COOLING,
    WATER_TEMP_MODE_HEATING,
)

_LOGGER = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400.0


def elapsed_days(start: datetime, now: datetime) -> float:
    """Return elapsed days, clamped at >= 0 to survive clock corrections."""
    return max(0.0, (now - start).total_seconds() / _SECONDS_PER_DAY)


def is_safe_direction(mode: str, new_value: float, last_value: float) -> bool:
    """Return True when the change moves in the condensation-safe direction.

    Cooling: warmer water is safer (up).  Heating: cooler water is safer (down).
    """
    if mode == WATER_TEMP_MODE_COOLING:
        return new_value > last_value
    return new_value < last_value


def round_safe(value: float, mode: str, step: float) -> float:
    """Round to the entity step, always toward the safe side."""
    if step <= 0:
        return round(value, 3)
    if mode == WATER_TEMP_MODE_COOLING:
        return round(math.ceil(value / step - 1e-9) * step, 3)
    return round(math.floor(value / step + 1e-9) * step, 3)


def coerce_float(value: Any, default: float) -> float:
    """Coerce an attribute to float, falling back to a default."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def entity_limits(
    state: Any,
    *,
    min_default: float = SUPPLY_TEMP_MIN,
    max_default: float = SUPPLY_TEMP_MAX,
    step_default: float = DEFAULT_WATER_TEMP_STEP,
) -> tuple[float, float, float]:
    """Return a target entity's (min, max, step), with safe fallbacks.

    Args:
        state: The entity's ``hass.states`` state object, or None if unavailable.
        min_default: Fallback minimum when missing or non-numeric.
        max_default: Fallback maximum when missing or non-numeric.
        step_default: Fallback step when missing, non-numeric, or non-positive.

    Returns:
        A ``(minimum, maximum, step)`` tuple, falling back to the provided
        defaults when attributes are missing, non-numeric, or the entity
        itself is unavailable.
    """
    if state is None:
        return min_default, max_default, step_default

    attributes = state.attributes or {}
    step = coerce_float(attributes.get("step"), step_default)
    if step <= 0:
        step = step_default
    minimum = coerce_float(attributes.get("min"), min_default)
    maximum = coerce_float(attributes.get("max"), max_default)
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return minimum, maximum, step


def configured_ramp_start(mode: str, config: dict[str, Any] | None) -> float:
    """Return the configured ``ramp_start`` for a mode, with its default.

    Shared by ramp seeding, the live per-cycle ramp origin (R13 — read live
    every compute, never cached), and the deactivation park value: all three
    need the same "what does config say right now" resolution.
    """
    default = (
        DEFAULT_WATER_TEMP_HEATING_RAMP_START
        if mode == WATER_TEMP_MODE_HEATING
        else DEFAULT_WATER_TEMP_COOLING_RAMP_START
    )
    return float((config or {}).get(CONF_WATER_TEMP_RAMP_START, default))


def configured_ramp_rate(mode: str, config: dict[str, Any] | None) -> float:
    """Return the configured ``ramp_rate`` for a mode, with its default.

    Shared by the ramp math and the days-remaining diagnostic.
    """
    default = (
        DEFAULT_WATER_TEMP_HEATING_RAMP_RATE
        if mode == WATER_TEMP_MODE_HEATING
        else DEFAULT_WATER_TEMP_COOLING_RAMP_RATE
    )
    return float((config or {}).get(CONF_WATER_TEMP_RAMP_RATE, default))


def ramp_origin(configured_start: float, persisted_seed: float | None) -> float:
    """Return the live ramp origin for a heating ramp.

    Config is read live every compute so a mid-ramp edit to ``ramp_start``
    takes effect immediately. The persisted seed (the entity value captured
    at ramp start, when it ran hotter than config -- backup-heater trap)
    still wins whenever it's higher, protecting against a later config drop.
    """
    if persisted_seed is None:
        return configured_start
    return max(configured_start, persisted_seed)


def entity_limit_binds(mode: str, rounded: float, minimum: float, maximum: float) -> bool:
    """Return True when the entity's own min/max clamps *past* safety.

    Cooling's unsafe direction is down, so only a ``maximum`` below the
    computed target matters; heating's unsafe direction is up, so only a
    ``minimum`` above the computed target matters (mirrors
    :func:`is_safe_direction`). A clamp in the *safe* direction (e.g.
    cooling's minimum forcing the value up) is not flagged — it can't
    undercut condensation safety.
    """
    if mode == WATER_TEMP_MODE_COOLING:
        return maximum < rounded
    return minimum > rounded


def warn_entity_limited(
    warned: dict[str, datetime],
    mode: str,
    entity_id: str,
    desired: float,
    actual: float,
    now: datetime,
    warn_interval_seconds: float,
) -> None:
    """Log a rate-limited WARNING when an entity limit clamps past the computed target.

    Args:
        warned: Per-entity last-warned timestamps, mutated in place.
        mode: Mode key the value belongs to.
        entity_id: Target ``number`` / ``input_number`` entity.
        desired: The rounded, unclamped target that couldn't be reached.
        actual: The value that will actually be written.
        now: Current wall-clock time.
        warn_interval_seconds: Minimum seconds between repeat warnings.
    """
    last = warned.get(entity_id)
    if last is not None and (now - last).total_seconds() < warn_interval_seconds:
        return
    warned[entity_id] = now
    _LOGGER.warning(
        "Water temp control: %s cannot reach %.1f°C for %s (entity limit clamps to %.1f°C)",
        entity_id,
        desired,
        mode,
        actual,
    )
