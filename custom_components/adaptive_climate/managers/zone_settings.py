"""Validation for runtime-editable per-zone settings."""

from __future__ import annotations

import re
from typing import Any

PRESET_NAMES: tuple[str, ...] = ("away", "eco", "boost", "comfort", "home", "sleep", "activity")
NIGHT_SETBACK_FIELDS: tuple[str, ...] = ("start", "delta", "recovery_deadline")

DELTA_MIN = 0.5
DELTA_MAX = 5.0

_TIME_RE = re.compile(r"([0-9]{1,2}):([0-9]{2})")
_SUNSET_RE = re.compile(r"sunset([+-][0-9]{1,3})?")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_preset_temperature(preset: str, temperature: float | None, min_temp: float, max_temp: float) -> None:
    if preset not in PRESET_NAMES:
        raise ValueError(f"Unknown preset '{preset}'")
    if temperature is None:
        return
    if not _is_number(temperature):
        raise ValueError(f"Preset '{preset}' temperature must be a number")
    if not min_temp <= temperature <= max_temp:
        raise ValueError(f"Preset '{preset}' temperature {temperature} outside {min_temp}-{max_temp}")


def _normalize_time(value: Any, field: str) -> str:
    match = _TIME_RE.fullmatch(value) if isinstance(value, str) else None
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError(f"{field} must be HH:MM")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _normalize_field(key: str, value: Any) -> Any:
    if key == "delta":
        if not _is_number(value) or not DELTA_MIN <= value <= DELTA_MAX:
            raise ValueError(f"delta must be between {DELTA_MIN} and {DELTA_MAX}")
        return float(value)
    if key == "start":
        if isinstance(value, str) and _SUNSET_RE.fullmatch(value):
            return value
        return _normalize_time(value, "start")
    if key == "recovery_deadline":
        return _normalize_time(value, "recovery_deadline")
    raise ValueError(f"Unknown night setback field '{key}'")


def validate_night_setback(changes: dict[str, Any]) -> dict[str, Any]:
    result = {key: _normalize_field(key, value) for key, value in changes.items() if value is not None}
    if not result:
        raise ValueError("No night setback values given")
    return result


def sanitize_stored(stored: dict[str, Any], min_temp: float, max_temp: float) -> tuple[dict[str, Any], list[str]]:
    """Return the valid subset of stored settings and one message per dropped value."""
    result: dict[str, Any] = {}
    dropped: list[str] = []

    presets: dict[str, float | None] = {}
    raw_presets = stored.get("presets")
    for name, temp in raw_presets.items() if isinstance(raw_presets, dict) else ():
        try:
            validate_preset_temperature(name, temp, min_temp, max_temp)
            presets[name] = temp
        except ValueError as err:
            dropped.append(str(err))
    if presets:
        result["presets"] = presets

    night: dict[str, Any] = {}
    raw_night = stored.get("night_setback")
    for key, value in raw_night.items() if isinstance(raw_night, dict) else ():
        try:
            night.update(validate_night_setback({key: value}))
        except ValueError as err:
            dropped.append(str(err))
    if night:
        result["night_setback"] = night

    return result, dropped
