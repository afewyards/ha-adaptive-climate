"""Strict, standalone schema for opt-in binary heating PWM groups."""

from __future__ import annotations

import math
import re
from typing import Any

import voluptuous as vol

CONF_PWM_COORDINATION = "pwm_coordination"


def _finite(value: Any) -> float:
    if isinstance(value, bool):
        raise vol.Invalid("expected a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise vol.Invalid("expected a finite number") from error
    if not math.isfinite(result):
        raise vol.Invalid("expected a finite number")
    return result


def _climate(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"climate\.[a-z0-9_]+", value):
        raise vol.Invalid("expected an explicit climate.* entity ID")
    return value


_ZONE = vol.Schema(
    {
        vol.Required("entity"): _climate,
        vol.Optional("hydraulic_weight", default=1.0): vol.All(_finite, vol.Range(min=0, min_included=False)),
    }
)
_GROUP = vol.Schema(
    {
        vol.Required("name"): vol.All(str, vol.Length(min=1)),
        vol.Optional("overlap_strategy", default="minimal"): vol.In(("minimal", "maximal")),
        vol.Optional("handover_overlap_seconds", default=240.0): vol.All(_finite, vol.Range(min=0, max=1800)),
        vol.Optional("max_deferral_seconds", default=3600.0): vol.All(_finite, vol.Range(min=0, max=7200)),
        vol.Required("zones"): vol.All([_ZONE], vol.Length(min=2, max=16)),
    }
)


def _unique(config: dict[str, Any]) -> dict[str, Any]:
    names: set[str] = set()
    members: set[str] = set()
    for group in config["groups"]:
        if group["name"] in names:
            raise vol.Invalid("duplicate PWM group name")
        names.add(group["name"])
        for zone in group["zones"]:
            if zone["entity"] in members:
                raise vol.Invalid(f"duplicate PWM group member: {zone['entity']}")
            members.add(zone["entity"])
    if config["enabled"] and not config["groups"]:
        raise vol.Invalid("enabled PWM coordination requires groups")
    return config


PWM_COORDINATION_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional("enabled", default=False): bool,
            vol.Optional("mode", default="load_balancing"): vol.In(("load_balancing",)),
            vol.Optional("groups", default=list): [_GROUP],
        }
    ),
    _unique,
)
