"""Voluptuous validators for manifold and pump flow configuration.

Plain voluptuous only (no ``cv.*``), so tests exercise these exact validators.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

import voluptuous as vol

from .const import (
    CONF_MANIFOLD_ZONE_ENTITY,
    CONF_MANIFOLD_ZONE_FLOW,
    CONF_MANIFOLDS,
    CONF_PIPE_VOLUME,
    CONF_PUMP_FLOW_CONTROL,
    CONF_PUMP_FLOW_MIN_FLOW,
    CONF_PUMP_FLOW_MIN_WRITE_INTERVAL,
    CONF_PUMP_FLOW_TARGET_ENTITY,
    DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL,
)

REMOVED_LOOPS_KEY = "loops"
REMOVED_FLOW_PER_LOOP_KEY = "flow_per_loop"

_MIGRATION_HINT = "list each zone on its manifold as {entity: climate.<zone>, flow: <L/min>}"
LOOPS_REMOVED_MESSAGE = f"'loops' was removed; {_MIGRATION_HINT}"
FLOW_PER_LOOP_REMOVED_MESSAGE = f"'flow_per_loop' was removed; {_MIGRATION_HINT}"
ZONE_STRING_REMOVED_MESSAGE = f"plain entity ids in manifold zones were removed; {_MIGRATION_HINT}"

_CLIMATE_ENTITY_RE = re.compile(r"^climate\.[a-z0-9_]+$")
_NUMBER_ENTITY_RE = re.compile(r"^(number|input_number)\.[a-z0-9_]+$")


def removed_key(message: str) -> Callable[[Any], Any]:
    """Return a validator that rejects a removed config key with a migration message."""

    def _reject(_value: Any) -> Any:
        raise vol.Invalid(message)

    return _reject


def _entity_id(pattern: re.Pattern[str], description: str) -> Callable[[Any], str]:
    def _validate(value: Any) -> str:
        if not isinstance(value, str):
            raise vol.Invalid(f"expected {description} entity id")
        normalized = value.strip().lower()
        if not pattern.match(normalized):
            raise vol.Invalid(f"expected {description} entity id, got '{value}'")
        return normalized

    return _validate


_climate_entity_id = _entity_id(_CLIMATE_ENTITY_RE, "a climate.*")
_number_entity_id = _entity_id(_NUMBER_ENTITY_RE, "a number.* or input_number.*")


def _non_empty_string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise vol.Invalid("expected a non-empty string")
    return value


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


_POSITIVE_FLOAT = vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False))

_MANIFOLD_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MANIFOLD_ZONE_ENTITY): _climate_entity_id,
        vol.Required(CONF_MANIFOLD_ZONE_FLOW): _POSITIVE_FLOAT,
    }
)


def _manifold_zone(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        raise vol.Invalid(f"{ZONE_STRING_REMOVED_MESSAGE} (got '{value}')")
    return _MANIFOLD_ZONE_SCHEMA(value)


MANIFOLD_SCHEMA = vol.Schema(
    {
        vol.Required("name"): _non_empty_string,
        vol.Required("zones"): vol.All(_ensure_list, [_manifold_zone], vol.Length(min=1)),
        vol.Required(CONF_PIPE_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
        vol.Optional(REMOVED_FLOW_PER_LOOP_KEY): removed_key(FLOW_PER_LOOP_REMOVED_MESSAGE),
    }
)

PUMP_FLOW_CONTROL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PUMP_FLOW_TARGET_ENTITY): _number_entity_id,
        vol.Optional(CONF_PUMP_FLOW_MIN_FLOW): _POSITIVE_FLOAT,
        vol.Optional(CONF_PUMP_FLOW_MIN_WRITE_INTERVAL, default=DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
    }
)


def validate_manifolds_and_pump_flow(config: dict[str, Any]) -> dict[str, Any]:
    """Reject a zone listed on two manifolds, and pump flow control without manifolds."""
    owner: dict[str, str] = {}
    for manifold in config.get(CONF_MANIFOLDS) or []:
        for zone in manifold["zones"]:
            entity_id = zone[CONF_MANIFOLD_ZONE_ENTITY]
            if entity_id in owner:
                raise vol.Invalid(
                    f"{entity_id} is listed in manifold '{owner[entity_id]}' and '{manifold['name']}'; "
                    "a zone may belong to one manifold only"
                )
            owner[entity_id] = manifold["name"]
    if config.get(CONF_PUMP_FLOW_CONTROL) and not owner:
        raise vol.Invalid("pump_flow_control requires manifolds with per-zone flow")
    return config
