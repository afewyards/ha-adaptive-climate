# Learning Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Lovelace card with the integration showing, per zone, learned heat-up rate, overshoot and settling time against expected/limit, plus confidence.

**Architecture:** Backend adds 9 small keys to each climate entity's existing `learning` attribute via one new pure-function module. A new registration module serves one plain JS file and adds it to the frontend. Card reads climate states only; no services, no websocket.

**Tech Stack:** Python (HA custom component, pytest with mocked HA via `tests/conftest.py`), one vanilla JS custom element, no build.

**Spec:** `docs/superpowers/specs/2026-11-15-learning-card-design.md`

## Global Constraints

- Work on branch `feat/learning-card`, not `main`. Commits via the `committing` skill (Angular convention).
- No build step, bundler, framework, npm dependency. One JS file.
- No change to stored data, `STORAGE_VERSION`, existing attribute meanings, controller behaviour or thresholds. No new entities.
- Pyright strict, ruff (line 120), `X | None`, no `assert` in production code, max 800 lines per Python file. `__init__.py` (1158) and `managers/state_attributes.py` (833) are already over: add call sites only, new code in new modules.
- New `learning` keys: omitted when unknown. Never `None`, never `0.0` as a stand-in.
- Rounding: `heating_rate`, `heating_rate_expected`, `overshoot`, `overshoot_limit` 2 decimals; `settling_time`, `settling_time_limit` 1 decimal.
- Card thresholds: rate off below 75% of expected; overshoot/settling off above limit. Scales: rate 0–2 °C/h, overshoot 0–0.5 °C, settling 0–120 min, clamp beyond.
- Colours only from HA theme variables: `--primary-text-color`, `--secondary-text-color`, `--divider-color`, `--warning-color`, `--primary-color` (tick), `--ha-card-background`.
- Tests: `cv.*` validators are MagicMocks via conftest; pin mock attributes explicitly.
- No comments narrating changes, no ticket refs in code.

## Review Focus

1. Cycles restored from older storage have `overshoot`/`settling_time` = `None` → means skip `None`; all `None` → key omitted, no crash. (Task 1 test)
2. Zone with `heating_type` `None` or unknown → limits and expected rate fall back as `get_convergence_thresholds` / `calculate_expected_heating_rate` already do; no exception. (Task 1 test)
3. Friendly name or attribute containing `<`, `&`, quotes → rendered as text, never as HTML. (Task 3 harness case)
4. `entities:` lists an entity that is missing, `unavailable`, or has no `learning` attribute → row shows the entity id and "Not available", other rows unaffected. (Task 3 harness case)
5. `heating_rate_expected` or a limit is `0` or absent → no tick, bar not marked off, no division by zero. (Task 3 harness case)

---

### Task 1: Publish learned profile in `learning` attribute

**Files:**
- Create: `custom_components/adaptive_climate/managers/learning_profile.py`
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py` (extract mean from `check_physics_underperformance`, ~line 437)
- Modify: `custom_components/adaptive_climate/adaptive/learning.py` (delegate, next to `check_physics_rate_underperformance` ~line 557)
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py:266` (one call)
- Modify: `docs/subsystems/state-attributes.md` (`learning` block + key table)
- Test: `tests/test_learning_profile.py`, `tests/test_state_attributes.py`

**Interfaces:**
- Produces: `learning` attribute keys `cycles:int`, `heating_type:str`, `area_m2:float`, `heating_rate`, `heating_rate_expected`, `overshoot`, `overshoot_limit`, `settling_time`, `settling_time_limit` (floats). Task 3 reads exactly these names.
- Produces:
  ```python
  # heating_rate_learner.py
  def get_mean_session_rate(self) -> float | None  # mean of non-stalled "session" obs; None if < MIN_OBSERVATIONS_FOR_RATE or mean <= 0
  # learning.py (AdaptiveLearner)
  def get_mean_heating_rate(self) -> float | None
  # managers/learning_profile.py
  OVERSHOOT_WINDOW = 20
  def build_learning_profile(
      learner: AdaptiveLearner,
      heating_type: str | None,
      area_m2: float | None,
      tau: float | None,
      max_power_w: float | None,
      supply_temperature: float | None,
  ) -> dict[str, Any]
  ```

- [ ] **Step 1: Failing tests, `tests/test_learning_profile.py`.** Use a stub learner (`SimpleNamespace(cycle_history=[...], get_cycle_count=lambda: n, get_mean_heating_rate=lambda: r)`), cycles as `SimpleNamespace(overshoot=..., settling_time=...)`.

  ```python
  def test_full_profile():
      p = build_learning_profile(stub(cycles=[c(0.1, 20), c(0.3, 40)], rate=0.914), "radiator", 18.0, None, None, None)
      th = get_convergence_thresholds("radiator")
      assert p == {
          "cycles": 2, "heating_type": "radiator", "area_m2": 18.0,
          "heating_rate": 0.91,
          "heating_rate_expected": round(calculate_expected_heating_rate("radiator")["baseline"], 2),
          "overshoot": 0.2, "overshoot_limit": th["overshoot_max"],
          "settling_time": 30.0, "settling_time_limit": th["settling_time_max"],
      }

  def test_no_cycles_omits_measured_keys():
      p = build_learning_profile(stub(cycles=[], rate=None), "radiator", None, None, None, None)
      assert p["cycles"] == 0
      assert not {"heating_rate", "overshoot", "settling_time", "area_m2"} & p.keys()
      assert {"heating_rate_expected", "overshoot_limit", "settling_time_limit"} <= p.keys()

  def test_none_metrics_are_skipped():
      p = build_learning_profile(stub(cycles=[c(None, None), c(0.4, None)], rate=None), "radiator", None, None, None, None)
      assert p["overshoot"] == 0.4 and "settling_time" not in p

  def test_overshoot_uses_last_20_cycles():
      p = build_learning_profile(stub(cycles=[c(1.0, 10)] * 5 + [c(0.2, 10)] * 20, rate=None), "radiator", None, None, None, None)
      assert p["overshoot"] == 0.2 and p["settling_time"] == 10.0

  def test_unknown_heating_type_does_not_raise():
      none = build_learning_profile(stub(cycles=[], rate=None), None, None, None, None, None)
      odd = build_learning_profile(stub(cycles=[], rate=None), "nonsense", None, None, None, None)
      assert "heating_type" not in none and odd["heating_type"] == "nonsense"
      assert "overshoot_limit" in none and "overshoot_limit" in odd
  ```
  Plus in the existing heating-rate learner test file: `get_mean_session_rate()` returns `None` with 2 session obs, the mean with 3, ignores stalled and `source="cycle"` obs; `check_physics_underperformance` results unchanged (existing tests stay green).
  Plus in `tests/test_state_attributes.py`: with a learner present, `attrs["learning"]` still has `status`/`confidence` and now `cycles`; with no coordinator, `attrs["learning"] == {"status": "idle", "confidence": 0}` unchanged.

- [ ] **Step 2: Run, expect fail.** `pytest tests/test_learning_profile.py -v` → ImportError.

- [ ] **Step 3: Implement.**
  - `get_mean_session_rate`: move the observation gathering + mean out of `check_physics_underperformance`, which then calls it. Behaviour of that method unchanged.
  - `build_learning_profile`: `heating_type` key only when not `None`; `area_m2` only when truthy. `overshoot` = mean of non-`None` `overshoot` in `learner.cycle_history[-OVERSHOOT_WINDOW:]`; `settling_time` = mean of non-`None` over all `cycle_history`. Limits from `get_convergence_thresholds(heating_type)`; expected from `calculate_expected_heating_rate(heating_type, tau, area_m2, max_power_w, supply_temperature)["baseline"]`, omitted if `<= 0`.
  - `state_attributes.py` after line 266: `attrs["learning"].update(build_learning_profile(adaptive_learner, heating_type, getattr(thermostat, "_area_m2", None), getattr(thermostat, "_thermal_time_constant", None), getattr(thermostat, "_max_power_w", None), getattr(thermostat, "_supply_temperature", None)))` (same four sources as `climate_cycle_handlers.py:158`). Idle early-return paths untouched.

- [ ] **Step 4: Verify.** `pytest tests/test_learning_profile.py tests/test_state_attributes.py tests/test_state_attributes_preheat.py -v` pass; `pytest` full pass; `ruff check custom_components/ tests/`; `pyright custom_components/adaptive_climate/` clean.

- [ ] **Step 5: Docs.** `docs/subsystems/state-attributes.md`: add the nine keys with unit + meaning, note "omitted when unknown".

- [ ] **Step 6: Commit.** `feat(learning): publish learned heat-up, overshoot and settling in learning attribute`

---

### Task 2: Serve and register the card

**Files:**
- Create: `custom_components/adaptive_climate/frontend_card.py`
- Create: `custom_components/adaptive_climate/frontend/adaptive-climate-learning-card.js` (placeholder: one comment line; Task 3 fills it)
- Modify: `custom_components/adaptive_climate/__init__.py` (`async_setup`, after `hass.data.setdefault(DOMAIN, {})` ~line 730: `await async_register_card(hass)`)
- Modify: `custom_components/adaptive_climate/manifest.json` (`"after_dependencies": ["frontend", "http"]`)
- Modify: `tests/conftest.py` only if `homeassistant.components.frontend` / `homeassistant.components.http` are not already mocked
- Test: `tests/test_frontend_card.py`

**Interfaces:**
- Produces:
  ```python
  CARD_URL = f"/{DOMAIN}/adaptive-climate-learning-card.js"
  _CARD_PATH: Path  # frontend/adaptive-climate-learning-card.js next to the module
  def card_module_url(content: bytes) -> str          # f"{CARD_URL}?v={sha1(content)[:8]}"
  async def async_register_card(hass: HomeAssistant) -> None
  ```
  Reference implementation to mirror: `/Users/kleist/Sites/x1-smartcharge/custom_components/anker_x1_smartgrid/__init__.py:44-62`.

- [ ] **Step 1: Failing tests.** `hass = MagicMock()`, `hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *a: f(*a))`, `hass.http.async_register_static_paths = AsyncMock()`, `hass.config.components = {"frontend"}`; patch `frontend_card.add_extra_js_url`.

  ```python
  async def test_registers_static_path_and_module_url():
      # async_register_static_paths awaited once; add_extra_js_url called once with (hass, url)
      # url.startswith(CARD_URL + "?v=")
  async def test_without_frontend_only_serves_file():
      # components = set() → static path registered, add_extra_js_url not called
  async def test_missing_file_logs_and_returns(monkeypatch, caplog):
      # _CARD_PATH → missing.js → no raise, nothing registered, "learning card" in caplog.text
  async def test_no_http_component_returns():
      # hass.http = None → no raise, add_extra_js_url not called
  def test_module_url_changes_with_content():
      # card_module_url(b"one") != card_module_url(b"two"); stable for same bytes
  ```

- [ ] **Step 2: Run, expect fail.** `pytest tests/test_frontend_card.py -v` → ImportError.
- [ ] **Step 3: Implement** `frontend_card.py` per interface; warning text `"learning card not registered, %s unreadable: %s"`. Add the call in `async_setup` and the manifest key.
- [ ] **Step 4: Verify.** `pytest tests/test_frontend_card.py tests/test_init.py -v`, full `pytest`, ruff, pyright.
- [ ] **Step 5: Commit.** `feat(frontend): serve and register the learning card`

---

### Task 3: The card

**Files:**
- Modify: `custom_components/adaptive_climate/frontend/adaptive-climate-learning-card.js`
- Create: `custom_components/adaptive_climate/frontend/harness.html`
- Modify: `README.md` (short "Learning card" section: what it shows, `type: custom:adaptive-climate-learning-card`, optional `title`, `entities`)

**Interfaces:**
- Consumes: Task 1 `learning` keys; standard `friendly_name`.
- Produces: custom element `adaptive-climate-learning-card`; config `{ title?: string, entities?: string[] }`; default title `What the system knows`. Pure helpers exposed for the harness on `window.AdaptiveClimateLearningCard = { zoneProfile, sortZones }`.

**Behaviour the code must implement** (visual reference, minus the "moves after" bar: `.superpowers/brainstorm/29215-1794737275/content/card-final.html`):
- Discovery when `entities` absent: every `climate.*` state whose `attributes.learning` is an object with a `confidence` key.
- `zoneProfile(entityId, stateObj)` → `{ id, name, meta, confidence, status, cycles, available, bars, reasons }`. `bars` in order: `heats at` (`heating_rate` vs `heating_rate_expected`, unit `°/h`, also shows `NN%` of expected), `overshoot` (vs `overshoot_limit`, `°`), `settles in` (vs `settling_time_limit`, `min`). Each bar: `{ label, text, frac, tick, off }`; `frac`/`tick` are 0–1 on the fixed scale, clamped; `tick` null when no positive reference; `off` false when value or reference missing.
- `meta`: `"<heating type with _ as space>, <area> m²"`, parts omitted when absent.
- Reason lines, exact copy: `Heats at {pct}% of the expected rate.` / `Overshoots by {v}°, limit {lim}°.` / `Takes {v} min to settle, limit {lim} min.`
- No measured value on any bar → no bars, line `Still collecting`. Entity missing/unavailable/no `learning` (only possible via `entities`) → entity id + `Not available`.
- No zones → `No Adaptive Climate zones found.`
- `sortZones`: more off bars first, then confidence ascending, then name. Explicit `entities` order wins over sorting.
- Missing value: `–` and empty track.
- Header right side: total cycles across shown zones, `NNN cycles`.
- Footer legend: `learned`, `expected, or the limit`.
- Layout: container query (or `ResizeObserver` fallback) at 560 px card width: below, header line then three bars in a row; above, name / bars / confidence on one line.
- All dynamic text via `textContent` or an escape helper; no raw interpolation into `innerHTML`.
- Re-render only if a watched entity's state object identity changed or config changed.
- `getCardSize()` → `1 + zones`; `getStubConfig()` → `{}`; guard `customElements.define` and `window.customCards` push against double load.

- [ ] **Step 1: Harness first.** `harness.html` loads the card script, mounts four instances at 400 px and 760 px with a fake `hass` (dark and light theme variables set on wrappers): (a) five zones mixing good, rate-off (0.9 vs 1.5), overshoot-off, collecting (confidence only), and one with `heating_rate_expected: 0`; (b) `entities` with one missing id and one `unavailable`; (c) no zones; (d) friendly name `<img src=x onerror=alert(1)> & "Kitchen"`. A `<script>` block runs `console.assert` checks and writes `PASS`/`FAIL n` into `#selfcheck`:
  - `zoneProfile` of the rate-off zone: `bars[0].off === true`, `reasons[0] === "Heats at 60% of the expected rate."`
  - zone with expected `0`: `bars[0].tick === null && bars[0].off === false`
  - value above scale: `frac === 1`
  - `sortZones`: off zones before good; among good, lower confidence first
  - case (d): `card.shadowRoot.querySelector("img") === null`
  - case (b): rendered text contains `Not available`
- [ ] **Step 2: Implement the card** until the harness shows `PASS`.
- [ ] **Step 3: Verify.** `node --check custom_components/adaptive_climate/frontend/adaptive-climate-learning-card.js` exits 0. Open `harness.html` in a browser: `#selfcheck` reads `PASS`, no console errors, both widths and both themes legible, nothing overflows at 330 px. `pytest tests/test_frontend_card.py` still passes (file exists, hash changes).
- [ ] **Step 4: README section.**
- [ ] **Step 5: Commit.** `feat(frontend): add learning card`

---

## After the tasks

- Whole-branch `code-review` agent before merge.
- Deploy to HAOS per memory note (scp to `/config/custom_components/`), restart, hard-refresh, add the card, check against live zones.

## Unresolved questions

1. Sensor fixes from earlier (duty cycle, cycle time, wear, power/m²) still uncommitted on `main`. Commit them as their own change first, before branching?
2. Wiki: `CLAUDE.md` says update the GitHub wiki alongside docs. Is there a local wiki checkout, or do you update it yourself?
3. Card visible to all dashboards as soon as the integration loads, no opt-out. Fine, or want a domain config flag?
