# Control card design

## Intent

A second Lovelace card, shipped with the integration, where the house owner
changes the heating of every room from one place: the target, the preset and
on/off day to day, and per room the night setback and the preset temperatures.

Today the last two live only in `configuration.yaml`, need a restart to change,
and preset temperatures are one house-wide set, which is why the owner's
config sets none: one "comfort" value is wrong for a bedroom and a bathroom at
once. The card makes presets per room and makes both settings editable live.

## Goals

- One card, `custom:adaptive-climate-control-card`, default title "Heating",
  listed in the card picker the same way as the learning card.
- One row per zone:
  - name, current temperature, and whether it is heating or off
  - − / + stepper for the target temperature
  - preset picker (hidden while the room has no presets)
  - on/off switch
- Card header: "All rooms" preset menu and a vacation switch.
- A room opens up (one at a time) to show its settings:
  - **Night setback:** starts at, lowers by, warm again by
  - **Preset temperatures:** eco, comfort, sleep, away
  - **Back to config**, shown only when the room has card changes
- A change made on the card applies immediately, without a restart, and
  survives a restart.
- Same configuration as the learning card: zero-config zone discovery,
  optional `entities:` and `title:`.
- Follows the HA theme, readable from about 330 px wide, keyboard reachable
  with visible focus, every control labelled for screen readers.

Reference mockup: `.superpowers/brainstorm/76262-1794752966/content/control-examples.html`,
version B. Differences from the mockup, all agreed afterwards: no "allowed
range" line, no learning buttons, night setback has no end time (see below).

## Non-goals

- Editing the allowed temperature range, PID or learning actions.
- Setting up night setback for a room that has none in YAML. The card says
  so in one line instead of offering fields.
- A fixed night setback end time, the preheat switch, `max_preheat_hours`.
  The owner's rooms end the setback by sunrise and window orientation.
- A vacation end date or a vacation temperature field. The existing service
  has neither date nor schedule; the switch uses its default temperature.
- Schedules, cooling-specific presets, history.

## Anti-goals

- Never write to `configuration.yaml`.
- No new entities.
- No change to the learning store, its `STORAGE_VERSION`, or the learning card.
- No build step or npm dependency; one plain JavaScript file, as the first card.
- No change to control behaviour beyond the values these settings already feed.

## Constraints

- Project rules in `CLAUDE.md`: pyright strict, ruff, 800 lines per file,
  HA Store API for persistence, `ThermostatState` protocol for managers.
  `climate.py` and `managers/state_attributes.py` are already over the limit,
  so new code goes in its own modules.
- Zone services are registered with `platform.async_register_entity_service`
  in `climate_setup.py`; the new ones follow that.
- Preset temperatures are held twice today, on the thermostat (`_eco_temp`
  and so on, published as the `eco_temp` attributes) and in
  `TemperatureManager`. A change has to reach both.
- `ClimateEntityFeature.PRESET_MODE` is decided once in `__init__`
  (`climate.py`). A room that gains its first preset at runtime has to gain
  the feature without a restart.
- For a zone without a fixed `end`, the night setback calculator reads
  `start`, `delta` and `recovery_deadline` from `thermostat._night_setback_config`
  on every evaluation (`managers/night_setback_calculator.py`), so changing
  the dict in place takes effect at the next one. A zone with `end` in YAML
  gets a `NightSetback` object parsed once at startup (`climate.py`), which
  the dict does not reach.
- Vacation state lives only in `hass.data[DOMAIN]["vacation_mode"]`; nothing
  in entity state carries it.
- `preset_sync_mode` defaults to `none` and the owner does not set it. Under
  `sync`, a target equal to a preset temperature switches the zone into that
  preset, and two presets with one temperature collapse into one
  (`managers/temperature_manager.py`).
- The `start` setting accepts a clock time or a sunset form ("sunset",
  "sunset+30").

## Approaches

How a card change is stored and reaches the thermostat:

1. **Services plus a settings store.** Picked. New zone services apply the
   change in place and save it in a store of its own. No entity clutter; the
   services remain usable from automations.
2. A number or time entity per setting. HA persists them for free and they
   work on any dashboard, but it is about 40 loose entities for six rooms
   (the integration has no devices), and the existing number platform already
   logs a duplicate-ID error on every start.
3. The card rewrites `configuration.yaml`. Dropped: a restart per change and
   the owner's real config at risk.

Picked by the owner: 1. Also picked by the owner: preset temperatures per
room rather than house-wide.

## Design

### Backend: services

All three target climate entities of this integration.

| service | fields | effect |
|---|---|---|
| `set_preset_temperature` | `preset` (away, eco, boost, comfort, home, sleep, activity), `temperature` (°C, or empty to clear) | sets or clears that preset's temperature for the zone |
| `set_night_setback` | any of `start`, `delta`, `recovery_deadline`; at least one | changes those night setback values for the zone |
| `reset_zone_settings` | none | drops every card change for the zone and puts the YAML values back |

Rules:

- `temperature` must lie within the zone's min and max; `delta` between 0.5
  and 5 °C; `start` a clock time or a sunset form; `recovery_deadline` a clock
  time. A rejected call raises a service error with the reason and changes
  nothing.
- `set_night_setback` is rejected on a zone without night setback in YAML,
  and on a zone with a fixed `end` in YAML. The card shows such a room's
  setback as read-only text with "set in the config".
- The 0.5 to 5 °C depth limit applies to service calls and stored values
  only. A YAML depth outside it stays valid and the card shows the real value.
- Preset sync behaviour is left as it is. Two presets may share a temperature.
- If the zone is in the preset whose temperature changes, its target follows
  at once. If that preset is cleared while active, the zone first goes back
  to no preset (its saved target), then the preset disappears.
- The preset picker appears and disappears with the zone's presets, without
  a restart.

### Backend: storage and precedence

- A new HA store, separate from the learning store, holding per zone only the
  values changed from the card.
- At startup a stored value wins over YAML. YAML stays the starting value for
  everything never changed from the card, including the house-wide preset
  temperatures, which remain the default for every zone.
- A preset cleared from the card is stored as cleared and stays cleared after
  a restart, even when YAML gives a house-wide value for it. Absent from the
  store means "use YAML".
- `reset_zone_settings` removes the zone's stored values.
- A stored value that no longer validates (for example outside a min/max that
  YAML has since narrowed) is ignored with a warning, not applied and not
  fatal.

### Backend: what the card reads

- Preset temperatures: the existing `eco_temp`-style attributes.
- Night setback `start`, `delta`, `recovery_deadline`: published on the
  climate entity if not already there.
- Whether the zone has card changes, so "Back to config" can show.
- Whether vacation mode is on: a new attribute on each climate entity of the
  integration, updated when vacation mode changes, so the switch shows the
  real state after a reload or an automation call.

### Frontend: the card

- `custom_components/adaptive_climate/frontend/adaptive-climate-control-card.js`,
  registered beside the first card with the same content-hash URL scheme.
- Row controls call standard services: `climate.set_temperature`,
  `climate.set_preset_mode`, `climate.turn_on` / `climate.turn_off`.
  Stepper step, min and max come from the entity.
- "All rooms" lists every preset at least one room has and applies it to the
  rooms that have it. Vacation calls `adaptive_climate.set_vacation_mode`.
- Opened room:
  - Night setback: a time field, a degrees stepper, a time field. A
    sunset-based start is shown as text with an action to replace it by a
    clock time; the card does not offer sunset forms itself.
  - Preset temperatures: four fixed slots (eco, comfort, sleep, away), plus
    any other preset the zone already has. An empty slot reads "not set";
    filling it creates the preset, emptying it clears it.
  - "Back to config" asks for confirmation.
- A stepper press updates the number at once and sends the service call after
  a short pause, so holding + does not flood the thermostat.
- A failed call puts the old value back and shows the error text in the row.
- An unavailable zone shows its name and "unavailable", controls disabled.
- No zones: "No Adaptive Climate zones found."

### Tests and docs

- Python: each service (happy path, each rejection), a change surviving a
  restart, YAML used when nothing is stored, reset, the invalid-stored-value
  case, preset feature appearing and disappearing, registration of the second
  card file.
- Card: a self-checking harness page with a fake `hass`, as the first card has.
- Docs: `services.yaml`, README card section, `docs/subsystems/`, and the wiki.

## Calls made by the author, not the owner

- Stored card values win over YAML after a restart, with "Back to config" as
  the way out.
- Night setback fields are start, depth and warm-again-by, because the owner's
  rooms have no fixed end.
- Four fixed preset slots: eco, comfort, sleep, away.
- Allowed range and learning buttons left out, read from "B, with night
  setback and preset temperatures".
- Vacation is a plain switch at the service's default temperature.
- Depth limited to 0.5 to 5 °C, for card and service changes only.
- Rooms with a fixed setback end in YAML are read-only on the card.
- Vacation state published as an attribute on every zone.
- A cleared preset stays cleared across restarts.
- Preset sync mode untouched.
- Card title "Heating"; a separate card, not a mode of the learning card.

## Left to the builder

- Module, store key and attribute names.
- Whether the store loads per zone at entity setup or once for the domain.
- Card internals: DOM, CSS, how a room opens, the pause length on the stepper.
- Whether the harness is a second page or a second section of the existing one.
