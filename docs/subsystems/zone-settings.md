# Zone settings

Preset temperatures and night setback can be changed per zone at runtime, from the control card or from automations. A change applies at once, without a restart, and is saved in its own HA store (`.storage/adaptive_climate_settings`), separate from the learning store. `configuration.yaml` is never written.

## Services

All three target climate entities of this integration.

| Service | Fields | Effect |
|---|---|---|
| `adaptive_climate.set_preset_temperature` | `preset` (away, eco, boost, comfort, home, sleep, activity), `temperature` (°C, empty or `null` to clear) | Sets or clears that preset's temperature for the zone |
| `adaptive_climate.set_night_setback` | any of `start`, `delta`, `recovery_deadline`; at least one | Changes those night setback values for the zone |
| `adaptive_climate.reset_zone_settings` | none | Drops every saved change for the zone and puts the YAML values back |

```yaml
service: adaptive_climate.set_night_setback
target:
  entity_id: climate.bedroom
data:
  start: "22:30"
  delta: 1.5
  recovery_deadline: "06:45"
```

Rules (a rejected call raises a service error with the reason and changes nothing):

- `temperature` must lie within the zone's `min_temp` and `max_temp`.
- `delta` must be between 0.5 and 5 °C. The limit applies to service calls and saved values only; a YAML depth outside it stays valid and is shown as is.
- `start` is `HH:MM` or a sunset form: `sunset`, `sunset+30`, `sunset-15`. Offsets of 12 or less are read as hours (`sunset+10` is ten hours after sunset), larger ones as minutes.
- `recovery_deadline` is `HH:MM`.
- `set_night_setback` is rejected for a zone with no night setback in YAML, and for a zone with a fixed `end` in YAML.

Vacation mode keeps its own domain service, `adaptive_climate.set_vacation_mode` (`enabled`, optional `target_temp`), which the card's vacation switch calls with the default temperature.

## Precedence

- At startup a saved value wins over YAML, until "Back to config" (`reset_zone_settings`). YAML stays the starting value for everything never changed at runtime, including house-wide preset temperatures.
- A preset cleared at runtime stays cleared after a restart, even when YAML gives a value for it.
- A saved value that no longer validates (for example outside a `min_temp`/`max_temp` that YAML has since narrowed) is ignored with a warning.

## Behaviour

- If the zone is in the preset whose temperature changes, its target follows at once. Clearing the active preset first puts the zone back to no preset, then the preset disappears. The preset list (and the preset picker) follow without a restart.
- While vacation mode is on, preset changes are saved without moving the zone's target.
- Zones with a fixed night setback `end` or no night setback in YAML are read-only on the card.
- Adding a `recovery_deadline` at runtime to a zone that has none in YAML does not switch preheat on; preheat is decided at startup from the YAML config.
- `preset_sync_mode` is unchanged: two presets may share a temperature.

## What the card reads

The `settings` attribute on every zone (see [state attributes](state-attributes.md)): `changed` (the zone has saved runtime changes, so "Back to config" shows), `vacation`, and `night_setback` with `start`, `delta`, `recovery_deadline` and `editable`. Preset temperatures come from the `eco_temp`-style attributes, absent when not set.
