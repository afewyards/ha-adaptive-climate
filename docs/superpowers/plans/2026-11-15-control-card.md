# Control Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Second Lovelace card to change target, preset, on/off per room, plus per-room night setback and preset temperatures, live and restart-proof.

**Architecture:** Three entity services on the climate platform change values in place on the thermostat and its managers, and save only the changed values in a new HA store. At startup stored values overlay YAML. A new `settings` attribute group tells the card what it can't already read. The card is one plain JS file served next to the learning card.

**Tech Stack:** Python (HA custom component, voluptuous, HA Store), plain JavaScript custom element, pytest.

**Spec:** `docs/superpowers/specs/2026-11-15-control-card-design.md`

## Global Constraints

- Never write `configuration.yaml`. No new entities. No change to learning store, `STORAGE_VERSION`, or learning card behaviour.
- No build step, no npm dependency; one plain JS file.
- Pyright strict, ruff, max 800 lines per file, `X | None`, no `assert` in production code, no bare `# type: ignore`.
- `climate.py` (1257) and `managers/state_attributes.py` (845) may grow by call sites only; logic goes in new modules.
- Preset names: `away, eco, boost, comfort, home, sleep, activity`. Card slots: `eco, comfort, sleep, away`.
- Depth (`delta`) 0.5 to 5 °C for service calls and stored values only; YAML values not range-checked.
- Preset temperature within the zone's `min_temp`..`max_temp`.
- Stored value wins over YAML at startup; absent = YAML; stored `null` preset = cleared.
- Card element `adaptive-climate-control-card`, default title `Heating`.
- Empty state text: `No Adaptive Climate zones found.`
- Commits: Angular convention, stage files by name, no AI footer.
- Run before each commit: `ruff check custom_components/ tests/ && ruff format custom_components/ tests/ && pyright custom_components/adaptive_climate/`.

## Review Focus

1. Depth changed while setback is active → next evaluation uses new depth (Task 3 test).
2. Active preset cleared → zone back to saved target, preset `none`, picker loses it (Task 2 test).
3. Stored value outside a since-narrowed min/max → ignored with warning, setup continues (Task 1 + Task 2 tests).
4. Preset temperature changed for the active preset while vacation mode is on → stored, target not moved (Task 2 test).
5. Holding + on the stepper → one `climate.set_temperature` call with the final value (Task 6 harness check).

## File Structure

| file | role |
|---|---|
| `managers/zone_settings.py` (new) | pure validation and overlay logic |
| `adaptive/zone_settings_store.py` (new) | HA store for card changes |
| `climate_settings_services.py` (new) | `ClimateSettingsServicesMixin`: the three services + startup overlay |
| `managers/settings_attributes.py` (new) | builds the `settings` attribute group |
| `managers/temperature_manager.py` | `async_set_preset_temperature` |
| `climate_state_setters.py` | dynamic `supported_features` |
| `climate.py`, `climate_setup.py`, `__init__.py`, `managers/state_attributes.py`, `adaptive/vacation.py` | call sites only |
| `frontend_card.py` | serve two card files |
| `frontend/adaptive-climate-control-card.js`, `frontend/control-harness.html` (new) | card + self-checking harness |
| `services.yaml`, `README.md`, `docs/subsystems/state-attributes.md`, `docs/subsystems/zone-settings.md` (new) | docs |

All paths below are under `custom_components/adaptive_climate/` unless they start with `tests/` or `docs/`.

---

### Task 1: Validation and store

**Files:**
- Create: `managers/zone_settings.py`, `adaptive/zone_settings_store.py`
- Test: `tests/test_zone_settings.py`

**Interfaces — Produces:**
- `PRESET_NAMES: tuple[str, ...]`
- `validate_preset_temperature(preset: str, temperature: float | None, min_temp: float, max_temp: float) -> None` — raises `ValueError(reason)`
- `validate_night_setback(changes: dict[str, Any]) -> dict[str, Any]` — keys `start`, `delta`, `recovery_deadline`; drops `None`; raises `ValueError` when empty or invalid; returns normalized dict (`delta` float, times `HH:MM` zero-padded)
- `sanitize_stored(stored: dict[str, Any], min_temp: float, max_temp: float) -> tuple[dict[str, Any], list[str]]` — valid subset + one message per dropped value
- `ZoneSettingsStore(hass)`: `async_load() -> None`, `get_zone(key: str) -> dict[str, Any]` (copy; `{}` when none), `async_set_preset(key, preset, temperature: float | None) -> None`, `async_set_night_setback(key, changes: dict[str, Any]) -> None` (merges), `async_clear_zone(key) -> None`
- Store key `adaptive_climate_settings`, version 1, shape `{"zones": {key: {"presets": {name: float | None}, "night_setback": {...}}}}`. Create the store through a module-level `_create_store` as `adaptive/persistence.py` does, so tests patch it.

- [ ] **Step 1: failing tests**

```python
def test_preset_temperature_range():
    validate_preset_temperature("eco", 18.5, 16, 25)
    validate_preset_temperature("eco", None, 16, 25)
    with pytest.raises(ValueError): validate_preset_temperature("eco", 26, 16, 25)
    with pytest.raises(ValueError): validate_preset_temperature("party", 20, 16, 25)

def test_night_setback_validation():
    assert validate_night_setback({"start": "9:00", "delta": 2, "recovery_deadline": None}) == {"start": "09:00", "delta": 2.0}
    assert validate_night_setback({"start": "sunset+30"}) == {"start": "sunset+30"}
    for bad in ({}, {"delta": 0.4}, {"delta": 5.5}, {"start": "25:00"}, {"recovery_deadline": "sunset"}, {"start": "noon"}):
        with pytest.raises(ValueError): validate_night_setback(bad)

def test_sanitize_drops_invalid_and_reports():
    ok, dropped = sanitize_stored({"presets": {"eco": 30, "sleep": 18, "away": None}, "night_setback": {"delta": 9, "start": "21:00"}}, 16, 25)
    assert ok == {"presets": {"sleep": 18, "away": None}, "night_setback": {"start": "21:00"}}
    assert len(dropped) == 2

async def test_store_roundtrip():  # patch _create_store with a fake holding a dict
    # set preset + setback on "z1", new store instance loads same data,
    # get_zone("other") == {}, async_clear_zone("z1") -> {} and saved
async def test_store_missing_or_malformed_file_loads_empty():  # async_load returns None / "garbage" -> get_zone == {}
```

- [ ] **Step 2:** `pytest tests/test_zone_settings.py -v` → FAIL (import error)
- [ ] **Step 3:** implement both modules
- [ ] **Step 4:** `pytest tests/test_zone_settings.py -v` → PASS
- [ ] **Step 5:** commit `feat(settings): add zone settings validation and store`

---

### Task 2: Preset temperatures per room at runtime

**Files:**
- Create: `climate_settings_services.py`
- Modify: `managers/temperature_manager.py`, `climate_state_setters.py` (`supported_features`), `climate.py` (mixin in class bases; one call in `async_added_to_hass`), `climate_setup.py` (register service next to `delete_pid_history`), `__init__.py` (create + load store in `async_setup`), `services.yaml`
- Test: `tests/test_zone_settings_services.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `hass.data[DOMAIN]["zone_settings_store"]: ZoneSettingsStore`, loaded in `async_setup` before platforms; a load failure logs a warning and leaves an empty store.
  - `TemperatureManager.async_set_preset_temperature(preset: str, temperature: float | None, move_target: bool = True) -> None` — updates the stored temp; if `preset` is active: `temperature` set and `move_target` → target follows; `temperature is None` → switch to `PRESET_NONE` first (restores saved target).
  - `ClimateSettingsServicesMixin`:
    - `async_set_preset_temperature(self, preset: str, temperature: float | None = None) -> None`
    - `async_apply_stored_zone_settings(self) -> None` — called once in `async_added_to_hass` before the first control pass; remembers YAML values in `self._yaml_presets: dict[str, float | None]` first; runs `sanitize_stored`, logs each dropped message as a warning.
    - `self._zone_settings_key -> str` = `self._unique_id`
  - Service `adaptive_climate.set_preset_temperature`, schema `{vol.Required("preset"): vol.In(PRESET_NAMES), vol.Optional("temperature"): vol.Any(None, vol.Coerce(float))}`.
- Both copies change: `thermostat._<preset>_temp` and the manager's.
- `supported_features` adds `PRESET_MODE` when `self._temperature_manager.has_preset_support`, removes it otherwise.
- `move_target=False` while `hass.data[DOMAIN]["vacation_mode"].enabled`.
- Validation failure → `ServiceValidationError` (fall back to `HomeAssistantError` if the test mocks lack it) carrying the `ValueError` text; nothing changed or saved.
- After a change: save, run a control pass, `async_write_ha_state()`.

- [ ] **Step 1: failing tests** (build the thermostat the way `tests/test_services.py` / existing climate tests do)

```python
async def test_set_preset_creates_preset_and_feature():   # zone with no presets: set eco 18.5 -> "eco" in preset_modes, PRESET_MODE in supported_features, attrs["eco_temp"] == 18.5, store has it
async def test_active_preset_follows_new_temperature():   # in comfort 21 -> set comfort 20.5 -> target 20.5
async def test_clearing_active_preset_restores_saved_target():  # target 20, enter eco 18, clear eco -> preset none, target 20, "eco" not in preset_modes
async def test_clearing_last_preset_removes_feature()
async def test_out_of_range_rejected_and_nothing_saved():  # 30 with max 25 -> raises, store untouched
async def test_stored_value_wins_over_yaml_on_startup():   # YAML eco 17, store eco 18.5 -> 18.5
async def test_stored_null_clears_yaml_preset_on_startup()
async def test_invalid_stored_value_ignored_with_warning(caplog):  # store eco 30, max 25 -> YAML value used, warning, setup completes
async def test_vacation_on_does_not_move_target()
```

- [ ] **Step 2:** `pytest tests/test_zone_settings_services.py -v` → FAIL
- [ ] **Step 3:** implement; add `set_preset_temperature` to `services.yaml` (target: this integration's climate entities; `preset` select of the seven; `temperature` number 5–35 step 0.5, optional, "leave empty to remove the preset")
- [ ] **Step 4:** `pytest tests/test_zone_settings_services.py tests/test_services.py -v` → PASS; `pytest -q` → no new failures
- [ ] **Step 5:** commit `feat(settings): set preset temperatures per zone at runtime`

---

### Task 3: Night setback and reset services

**Files:**
- Modify: `climate_settings_services.py`, `climate_setup.py`, `services.yaml`
- Test: `tests/test_zone_settings_services.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces on the mixin:
  - `async_set_night_setback(self, start: str | None = None, delta: float | None = None, recovery_deadline: str | None = None) -> None`
  - `async_reset_zone_settings(self) -> None`
  - `night_setback_editable -> bool` — `self._night_setback_config is not None and self._night_setback_config.get("end") is None`
  - `self._yaml_night_setback: dict[str, Any]` (copy of `start`, `delta`, `recovery_deadline` taken before overlay)
  - `async_apply_stored_zone_settings` also overlays stored setback values, only when `night_setback_editable`.
- Mutate `self._night_setback_config` in place (the calculator holds the same dict). Do not replace the dict.
- Not editable → reject: `"Night setback is not set up for this zone"` / `"This zone uses a fixed night setback end time; change it in the configuration"`.
- Reset: clear store entry, put `_yaml_presets` back through `TemperatureManager.async_set_preset_temperature`, put `_yaml_night_setback` back in the dict, control pass, write state.
- Service schemas: `set_night_setback` `{vol.Optional("start"): cv.string, vol.Optional("delta"): vol.Coerce(float), vol.Optional("recovery_deadline"): cv.string}`; `reset_zone_settings` `{}`. (`cv.*` is a MagicMock under conftest; real checking is `validate_night_setback`.)

- [ ] **Step 1: failing tests**

```python
async def test_set_night_setback_changes_config_in_place():   # delta 1 -> 2: calculator's dict is the same object, ["delta"] == 2.0, stored
async def test_new_depth_used_at_next_evaluation_while_active():  # inside the night window: calculate_night_setback_adjustment -> target - 2.0
async def test_partial_update_keeps_other_fields()
async def test_rejected_without_night_setback()
async def test_rejected_with_fixed_end()
async def test_invalid_values_rejected_and_nothing_saved():   # delta 6, start "25:00"
async def test_stored_setback_wins_over_yaml_on_startup()
async def test_reset_restores_yaml_presets_and_setback_and_empties_store()
```

- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement; `services.yaml` entries for both (reset description says it drops every change made from the card for the zone)
- [ ] **Step 4:** `pytest tests/test_zone_settings_services.py tests/test_night_setback.py -v` → PASS; `pytest -q`
- [ ] **Step 5:** commit `feat(settings): edit night setback per zone and reset to config`

---

### Task 4: `settings` attribute group and vacation state

**Files:**
- Create: `managers/settings_attributes.py`
- Modify: `managers/state_attributes.py` (one `attrs.update(...)` in `build_state_attributes`), `adaptive/vacation.py`, `climate.py` (subscribe in `async_added_to_hass`), `docs/subsystems/state-attributes.md`
- Test: `tests/test_settings_attributes.py`

**Interfaces:**
- Consumes: `night_setback_editable`, `_zone_settings_key`, store `get_zone`.
- Produces: `build_settings_attributes(thermostat) -> dict[str, Any]` returning

```python
{"settings": {
    "changed": bool,            # store has an entry for this zone
    "vacation": bool,           # hass.data[DOMAIN]["vacation_mode"].enabled, False when absent
    "night_setback": {          # key omitted when the zone has no night setback
        "start": str, "delta": float, "recovery_deadline": str | None, "editable": bool,
    },
}}
```

- Vacation refresh: `VacationMode.async_enable` / `async_disable` send dispatcher signal `adaptive_climate_vacation_changed` after `_enabled` flips; each thermostat connects it to `async_write_ha_state` and unsubscribes on removal (`async_on_remove`).

- [ ] **Step 1: failing tests**

```python
def test_settings_group_for_zone_with_setback():   # {"changed": False, "vacation": False, "night_setback": {"start": "21:00", "delta": 1, "recovery_deadline": "9:00", "editable": True}}
def test_no_night_setback_key_without_setback()
def test_fixed_end_is_not_editable()
def test_changed_true_after_store_entry()
def test_vacation_true_when_enabled()
async def test_vacation_toggle_sends_signal():      # patch async_dispatcher_send in vacation.py; called once on enable and once on disable
```

- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement; document the `settings` group in `docs/subsystems/state-attributes.md`
- [ ] **Step 4:** `pytest tests/test_settings_attributes.py tests/test_vacation_mode.py -v` → PASS; `pytest -q`
- [ ] **Step 5:** commit `feat(settings): publish zone settings and vacation state`

---

### Task 5: Serve both cards

**Files:**
- Modify: `frontend_card.py`, `tests/test_frontend_card.py`
- Create: `frontend/adaptive-climate-control-card.js` (placeholder defining the element with a "not built yet" body; Task 6 replaces it)

**Interfaces — Produces:**
- `CARD_FILES: tuple[str, ...] = ("adaptive-climate-learning-card.js", "adaptive-climate-control-card.js")`
- `card_url(filename: str) -> str` → `/adaptive_climate/<filename>`
- `card_module_url(filename: str, content: bytes) -> str` → `card_url(filename) + "?v=" + sha1[:8]`
- `async_register_card(hass)` unchanged name; an unreadable file skips that card only, with a warning naming it. Remove `CARD_URL`; update its users.

- [ ] **Step 1: tests** — update existing; add:

```python
async def test_registers_both_cards():      # two static paths, add_extra_js_url called twice, URLs start with card_url(f) + "?v="
async def test_one_unreadable_file_still_registers_the_other(caplog)
```

- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement
- [ ] **Step 4:** `pytest tests/test_frontend_card.py -v` → PASS
- [ ] **Step 5:** commit `feat(frontend): serve the control card next to the learning card`

---

### Task 6: The card

**Files:**
- Replace: `frontend/adaptive-climate-control-card.js`
- Create: `frontend/control-harness.html`
- Modify: `README.md` (card section after "Learning card"), create `docs/subsystems/zone-settings.md` (services, precedence, reset)

**Interfaces — Consumes** (entity state):
`state`, `attributes.friendly_name`, `temperature`, `current_temperature`, `hvac_action`, `min_temp`, `max_temp`, `target_temp_step`, `preset_mode`, `preset_modes`, `<preset>_temp`, `settings.{changed,vacation,night_setback}`.
Services: `climate.set_temperature`, `climate.set_preset_mode`, `climate.turn_on`, `climate.turn_off`, `adaptive_climate.set_vacation_mode {enabled}`, `adaptive_climate.set_preset_temperature {entity_id, preset, temperature}` (`temperature: null` clears), `adaptive_climate.set_night_setback {entity_id, start?, delta?, recovery_deadline?}`, `adaptive_climate.reset_zone_settings {entity_id}`.

**Requirements:**
- Custom element with `setConfig` (`title?`, `entities?` as strings or `{entity}`), `set hass`, `getCardSize`, `getStubConfig`, `window.customCards` entry (name "Adaptive Climate control", description one line).
- Discovery without `entities`: every `climate.*` whose attributes have a `settings` object. Sorted by name.
- Look: reference mockup version B (`.superpowers/brainstorm/76262-1794752966/content/control-examples.html`), colours from HA theme variables as the learning card does; no hard-coded palette.
- Row: name; `<current>° now` plus `, heating` when `hvac_action === 'heating'` or `, off` when state is `off`; stepper; preset `<select>` (omitted when `preset_modes` has only `none`); on/off switch (`role="switch"`, `aria-checked`).
- Header: "All rooms" `<select>` of presets any shown room has, applied to rooms that have it; vacation switch from `settings.vacation` of the first zone.
- One room open at a time, toggled by a button on the row (`aria-expanded`).
- Opened room:
  - Night setback, `editable`: "starts at" `<input type="time">`, "lowers by" stepper 0.5–5 step 0.5, "warm again by" `<input type="time">`. Start not `HH:MM` → shown as text with a "set a time" button.
  - `night_setback` absent → `Night setback is not set up for this room in the config.` Not editable → values as text + `set in the config`.
  - Preset temperatures: slots eco, comfort, sleep, away plus any other preset with a value; empty slot shows `not set`; stepper within `min_temp`..`max_temp`; a clear button per filled slot.
  - `Back to config` only when `settings.changed`; `confirm()` first.
- Steppers: number updates at once; service call 600 ms after the last press.
- Failed call: old value back, error message shown in the row (`role="alert"`).
- Zone `unavailable`: name + `unavailable`, controls disabled.
- No zones: `No Adaptive Climate zones found.`
- Do not rebuild the DOM while a field inside the card has focus or a stepper call is pending (a state push mid-edit must not drop the user's input).
- Every control has an accessible name including the room; visible `:focus-visible`.

- [ ] **Step 1:** write `control-harness.html`: loads the card with a fake `hass` (six zones: normal, heating, off, unavailable, no setback, fixed-end setback; one with `settings.changed`), records `callService` calls, runs checks and prints PASS/FAIL per check, as `frontend/harness.html` does. Checks:
  - rows rendered = zones; unavailable row has disabled controls
  - three fast + presses → exactly one `climate.set_temperature` with target + 1.5 after 700 ms
  - preset select → `climate.set_preset_mode`; switch → `climate.turn_off`
  - "All rooms: eco" calls only zones whose `preset_modes` include eco
  - filling an empty slot → `set_preset_temperature` with that preset; clear → `temperature: null`
  - setback time change → `set_night_setback` with only that field
  - fixed-end and no-setback rooms show text, no inputs
  - `Back to config` absent unless `changed`; present → `reset_zone_settings` after confirm
  - rejected `callService` → value restored and alert text present
  - state push while an input has focus keeps the input's value
  - zero zones → empty-state text
- [ ] **Step 2:** open the harness in a browser (file URL) → checks FAIL against the placeholder
- [ ] **Step 3:** implement the card
- [ ] **Step 4:** harness → all PASS; `pytest tests/test_frontend_card.py -v` → PASS
- [ ] **Step 5:** README section + `docs/subsystems/zone-settings.md`
- [ ] **Step 6:** commit `feat(frontend): add control card`

---

## Unresolved questions

- Wiki: update it as part of this, or skip as with the learning card?
- Deploy to the box after merge, as last time, or wait for your word?
