"""Persistence for runtime-editable per-zone settings."""

from __future__ import annotations

import copy
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "adaptive_climate_settings"
STORAGE_VERSION = 1


def _create_store(hass, version: int, key: str):
    """Create a Store instance."""
    from homeassistant.helpers.storage import Store

    return Store(hass, version, key)


class ZoneSettingsStore:
    """Per-zone preset and night setback overrides."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self._store = _create_store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._zones: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        zones = data.get("zones") if isinstance(data, dict) else None
        if not isinstance(zones, dict):
            if data is not None:
                _LOGGER.warning("Zone settings file malformed, starting empty")
            self._zones = {}
            return
        self._zones = {k: v for k, v in zones.items() if isinstance(v, dict)}

    def get_zone(self, key: str) -> dict[str, Any]:
        return copy.deepcopy(self._zones.get(key, {}))

    async def _async_save(self) -> None:
        await self._store.async_save({"zones": self._zones})

    async def async_set_preset(self, key: str, preset: str, temperature: float | None) -> None:
        self._zones.setdefault(key, {}).setdefault("presets", {})[preset] = temperature
        await self._async_save()

    async def async_set_night_setback(self, key: str, changes: dict[str, Any]) -> None:
        self._zones.setdefault(key, {}).setdefault("night_setback", {}).update(changes)
        await self._async_save()

    async def async_clear_zone(self, key: str) -> None:
        self._zones.pop(key, None)
        await self._async_save()
