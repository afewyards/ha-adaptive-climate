"""Runtime-editable per-zone settings for adaptive thermostat."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.exceptions import ServiceValidationError

from .adaptive.zone_settings_store import ZoneSettingsStore
from .const import DOMAIN
from .managers.zone_settings import (
    NIGHT_SETBACK_FIELDS,
    PRESET_NAMES,
    sanitize_stored,
    validate_night_setback,
    validate_preset_temperature,
)

_LOGGER = logging.getLogger(__name__)


class ClimateSettingsServicesMixin:
    """Presets and night setback changed at runtime, saved per zone, overlaid on YAML at startup."""

    @property
    def _zone_settings_key(self) -> str:
        return self._unique_id

    @property
    def _zone_settings_store(self) -> ZoneSettingsStore | None:
        return self.hass.data.get(DOMAIN, {}).get("zone_settings_store")

    @property
    def _vacation_active(self) -> bool:
        vacation = self.hass.data.get(DOMAIN, {}).get("vacation_mode")
        return vacation is not None and vacation.enabled

    @property
    def night_setback_editable(self) -> bool:
        # A fixed end time is parsed once into a NightSetback object that never sees the config dict.
        return self._night_setback_config is not None and self._night_setback_config.get("end") is None

    async def _async_apply_preset_temperature(self, preset: str, temperature: float | None, move_target: bool) -> None:
        setattr(self, f"_{preset}_temp", temperature)
        await self._temperature_manager.async_set_preset_temperature(preset, temperature, move_target)
        self._attr_preset_mode = self._temperature_manager.preset_mode
        self._saved_target_temp = self._temperature_manager.saved_target_temp

    async def async_set_preset_temperature(self, preset: str, temperature: float | None = None) -> None:
        try:
            validate_preset_temperature(preset, temperature, self.min_temp, self.max_temp)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        await self._async_apply_preset_temperature(preset, temperature, move_target=not self._vacation_active)
        store = self._zone_settings_store
        if store is not None:
            await store.async_set_preset(self._zone_settings_key, preset, temperature)
        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

    async def async_set_night_setback(
        self, start: str | None = None, delta: float | None = None, recovery_deadline: str | None = None
    ) -> None:
        if self._night_setback_config is None:
            raise ServiceValidationError("Night setback is not set up for this zone")
        if not self.night_setback_editable:
            raise ServiceValidationError(
                "This zone uses a fixed night setback end time; change it in the configuration"
            )
        try:
            changes = validate_night_setback({"start": start, "delta": delta, "recovery_deadline": recovery_deadline})
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        # In place: the night setback calculator holds this same dict.
        self._night_setback_config.update(changes)
        store = self._zone_settings_store
        if store is not None:
            await store.async_set_night_setback(self._zone_settings_key, changes)
        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

    async def async_reset_zone_settings(self) -> None:
        store = self._zone_settings_store
        if store is not None:
            await store.async_clear_zone(self._zone_settings_key)
        for preset, temperature in self._yaml_presets.items():
            await self._async_apply_preset_temperature(preset, temperature, move_target=not self._vacation_active)
        if self.night_setback_editable:
            self._night_setback_config.update(self._yaml_night_setback)
        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

    async def async_apply_stored_zone_settings(self) -> None:
        self._yaml_presets: dict[str, float | None] = {name: getattr(self, f"_{name}_temp") for name in PRESET_NAMES}
        config = self._night_setback_config
        self._yaml_night_setback: dict[str, Any] = (
            {key: config.get(key) for key in NIGHT_SETBACK_FIELDS} if config is not None else {}
        )
        store = self._zone_settings_store
        if store is None:
            return
        settings, dropped = sanitize_stored(store.get_zone(self._zone_settings_key), self.min_temp, self.max_temp)
        for message in dropped:
            _LOGGER.warning("%s: ignoring stored setting: %s", self.entity_id, message)
        for preset, temperature in settings.get("presets", {}).items():
            await self._async_apply_preset_temperature(preset, temperature, move_target=False)
        if self.night_setback_editable:
            self._night_setback_config.update(settings.get("night_setback", {}))
