# Learning card design

## Intent

A Lovelace card, shipped with the integration, that shows what the adaptive
learning has worked out about each room and where a room deviates from what
it should do. Today that knowledge is stored but not visible: the frontend
only gets `learning.status` and `learning.confidence`.

The reader is the house owner looking at a dashboard. They should be able to
tell, per room, whether it heats as fast as expected, whether it overshoots,
how long it takes to settle, and how much the system trusts those numbers.

## Goals

- One card, `custom:adaptive-climate-learning-card`, titled "What the system knows".
- One row per zone, rooms with a deviation first, then by confidence ascending.
- Per row:
  - name, heating type, area
  - confidence %, learning status, number of cycles learned from
  - three bars: **heats at** (°C/h), **overshoot** (°C), **settles in** (min)
  - a tick on each bar: the physics-expected rate for *heats at*, the zone's
    limit for *overshoot* and *settles in*
  - a bar turns amber when off: heat-up below 75% of expected, overshoot above
    its limit, settling above its limit
  - one line of text under an off room naming what is off
- The integration serves and registers the card itself, so it appears in the
  card picker with no HACS frontend install and no `resources:` entry.
- Zero configuration by default: the card finds the zones itself. Optional
  `entities:` list to pick and order zones, optional `title:`.
- Follows the active HA theme (light and dark) through theme CSS variables.
- Readable at a normal dashboard column (about 330 to 500 px) and wide. Narrow:
  header line, then the three bars in a row beneath. Wide: name, bars and
  confidence on one line.

Reference mockup: `.superpowers/brainstorm/29215-1794737275/content/card-final.html`
(open in a browser). Layout, labels and tone are as approved, with one change
made afterwards: the first bar in the mockup, "moves after", is replaced by
"settles in" as the third bar.

## Non-goals

- A measured response delay. The stored `dead_time` is the configured pipe
  transport delay, not a measurement, so it is not shown.
- Efficiency figures (boiler starts and runtime, energy per degree-day, energy
  share per room). They need new tracking and would be a second card.
- History or trends over time. Stored cycles have no timestamps.
- Actions from the card (apply gains, rollback).
- Outdoor compensation (Ke), cooling mode. Heating only.
- A count of unusable cycles in the reason line.

## Anti-goals

- No build step, bundler, framework or npm dependency. One plain JavaScript file.
- No change to stored data, `STORAGE_VERSION`, or the meaning of existing attributes.
- No new entities.
- No change to controller behaviour or its thresholds.

## Constraints

- Domain is configured by YAML (`async_setup` in `__init__.py`), no config entry.
- Pyright strict, ruff, max 800 lines per Python file, `X | None`, no `assert`
  in production code (see `CLAUDE.md`). `managers/state_attributes.py` is
  already over 800 lines, so the new code goes in its own module.
- Attribute values land in the recorder, so the published group stays small and
  changes rarely (rounded values, no per-cycle lists).

## Approaches

How the numbers reach the card:

1. **Extend each climate entity's `learning` attribute.** Picked. It already
   carries status and confidence; the card reads climate entities, which it
   needs anyway for the name. Smallest change, follows the existing structure.
2. One summary sensor for the whole house with all rooms in its attributes, as
   x1-smartcharge does. One entity for the card, but a new sensor duplicating
   per-zone data.
3. A websocket command the card calls. Nothing in state and room for long
   histories, but the most code, and the card has to poll. Worth it only once
   the card shows history.

Picked by the owner: 1.

## Design

### Backend: published fields

Extend the `learning` group. Existing keys stay as they are. New keys, all
optional, omitted (not null-filled, not zero-filled) when unknown:

| key | unit | source |
|---|---|---|
| `cycles` | count | number of stored heating cycles in the learner |
| `heating_rate` | °C/h | mean of all non-stalled heat-up observations in `HeatingRateLearner`, the same figure `check_physics_underperformance` uses, subject to its minimum observation count |
| `heating_rate_expected` | °C/h | `calculate_expected_heating_rate` (`adaptive/physics.py`) with the zone's heating type, plus tau, area, `max_power_w` and supply temperature where the zone has them |
| `overshoot` | °C | mean overshoot of the last 20 stored heating cycles |
| `overshoot_limit` | °C | `overshoot_max` from `get_convergence_thresholds` for the zone's heating type |
| `settling_time` | min | mean settling time over stored heating cycles |
| `settling_time_limit` | min | `settling_time_max` from the same thresholds |

Values rounded to one decimal (rates and overshoot to two). `heating_type` and
`area_m2` are added to the group only if the climate entity does not already
expose them as attributes.

### Frontend: the card

- `custom_components/adaptive_climate/frontend/adaptive-climate-learning-card.js`,
  a plain `HTMLElement` custom element with `setConfig`, `set hass`,
  `getCardSize`, `getStubConfig`, and a `window.customCards` entry.
- Zone discovery: every `climate.*` state whose attributes contain a `learning`
  object with a `confidence` key. `entities:` overrides it.
- Re-render only when one of the watched entities' state objects changed.
- Bar scales are fixed per column so rooms compare: heating rate 0 to 2 °C/h,
  overshoot 0 to 0.5 °C, settling 0 to 120 min; values beyond the scale clamp.
- The card decides "off" itself from the published pairs: rate below 75% of
  expected, overshoot above limit, settling above limit.
- A missing value shows a dash and an empty track, never a zero.
- A zone with no learned values yet shows its header line and "Still collecting".
- No zones found: "No Adaptive Climate zones found."
- Colours from theme variables (`--primary-text-color`,
  `--secondary-text-color`, `--divider-color`, `--warning-color`,
  `--primary-color` for the tick); no hard-coded palette.
- The amber state is never colour alone: the reason line carries it in words.

### Registration

Same pattern as `x1-smartcharge/custom_components/anker_x1_smartgrid/__init__.py`
(`async_setup`): read the file in the executor, register a static path under
`/adaptive_climate/`, call `add_extra_js_url` with a content-hash query string
for cache busting when `frontend` is loaded, log a warning and carry on if the
file is unreadable or `hass.http` is absent. Add `frontend` and `http` to
`after_dependencies` in `manifest.json`.

### Tests and docs

- Python: the new `learning` keys (present, absent when unknown, absent rather
  than 0.0 with no cycles) and registration (static path plus extra JS URL,
  unreadable file does not fail setup).
- Card: a standalone `harness.html` next to the card with a fake `hass`
  object covering good, off, collecting and empty cases, as x1 has. No JS test
  runner.
- Docs: `docs/subsystems/state-attributes.md` for the new keys, a short card
  section in the README, and the wiki alongside.

## Calls made by the author, not the owner

- Amber for settling above its limit, by analogy with the two the owner saw.
- 75% of expected as the card's heat-up threshold. The controller's own 60%
  and 50% thresholds are separate and untouched.
- Overshoot and settling limits are the learning's own per-heating-type
  thresholds (overshoot 0.15 to 0.25 °C, settling 45 to 90 min), tighter than
  the numbers in the mockup.
- Efficiency deferred to a second card.
- Zero-config zone discovery with an optional `entities:` list.

## Left to the builder

- The name and shape of the new Python module that builds the extra keys.
- Card internals: DOM structure, CSS, the wide/narrow breakpoint.
- Wording of the three reason lines, within the tone of the mockup.
