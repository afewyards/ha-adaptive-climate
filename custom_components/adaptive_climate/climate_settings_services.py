"""Runtime-editable per-zone settings for adaptive thermostat."""

from __future__ import annotations

import logging

from homeassistant.exceptions import ServiceValidationError

from .adaptive.zone_settings_store import ZoneSettingsStore
from .const import DOMAIN
from .managers.zone_settings import PRESET_NAMES, sanitize_stored, validate_preset_temperature

_LOGGER = logging.getLogger(__name__)


class ClimateSettingsServicesMixin:
    """Preset temperatures changed at runtime, saved per zone, overlaid on YAML at startup."""

    @property
    def _zone_settings_key(self) -> str:
        return self._unique_id

    @property
    def _zone_settings_store(self) -> ZoneSettingsStore | None:
        return self.hass.data.get(DOMAIN, {}).get("zone_settings_store")

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

        vacation = self.hass.data.get(DOMAIN, {}).get("vacation_mode")
        await self._async_apply_preset_temperature(
            preset, temperature, move_target=not (vacation is not None and vacation.enabled)
        )
        store = self._zone_settings_store
        if store is not None:
            await store.async_set_preset(self._zone_settings_key, preset, temperature)
        await self._async_control_heating(calc_pid=True)
        self.async_write_ha_state()

    async def async_apply_stored_zone_settings(self) -> None:
        self._yaml_presets: dict[str, float | None] = {name: getattr(self, f"_{name}_temp") for name in PRESET_NAMES}
        store = self._zone_settings_store
        if store is None:
            return
        settings, dropped = sanitize_stored(store.get_zone(self._zone_settings_key), self.min_temp, self.max_temp)
        for message in dropped:
            _LOGGER.warning("%s: ignoring stored setting: %s", self.entity_id, message)
        for preset, temperature in settings.get("presets", {}).items():
            await self._async_apply_preset_temperature(preset, temperature, move_target=False)
