"""Zone settings published in the ``settings`` state attribute."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat


def build_settings_attributes(thermostat: AdaptiveThermostat) -> dict[str, Any]:
    store = thermostat._zone_settings_store
    settings: dict[str, Any] = {
        "changed": store is not None and bool(store.get_zone(thermostat._zone_settings_key)),
        "vacation": thermostat._vacation_active,
    }
    config = thermostat._night_setback_config
    if config is not None:
        settings["night_setback"] = {
            "start": config["start"],
            "delta": config["delta"],
            "recovery_deadline": config["recovery_deadline"],
            "editable": thermostat.night_setback_editable,
        }
    return {"settings": settings}
