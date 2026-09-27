# Pump Flow Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Request flow (L/min) from one system pump = sum of design flows of zones whose valves are open and fully actuated; replace manifold `loops`/`flow_per_loop` with explicit per-zone `{entity, flow}`.

**Architecture:** Plain-voluptuous validators in `manifold_config.py`. `ManifoldRegistry` switches to per-zone flow (transport delay = pipe_volume / Σ active flows). New `PumpFlowController` (owned by coordinator, fed from `update_zone_demand`) tracks per-zone valve maturity, coalesces changes, applies write policy via pure helpers in `pump_flow_math.py`. Diagnostic sensor polls `controller.diagnostics()`.

**Tech Stack:** Python 3.11, Home Assistant custom component, voluptuous, pytest + pytest-asyncio (strict mode), ruff, pyright strict.

**Spec:** `docs/superpowers/specs/2026-09-27-pump-flow-control-design.md`

## Global Constraints

- ruff line-length 120; pyright strict on `custom_components/` (tests excluded).
- `X | None`, never `Optional`. `time.monotonic()` for durations, `dt_util.utcnow()` for wall clock; never `datetime.now()` / `time.time()`.
- No `assert` in production code. `@callback` only on sync functions. `# type: ignore[code]` only, never bare.
- Comments only where code can't explain itself. No ticket IDs / change history in comments.
- `*Controller` = actuates hardware. Service calls via `hass.services.async_call` only.
- Tests: `cv.*` is a MagicMock (tests/conftest.py) → new validators use plain voluptuous only. Every async test needs `@pytest.mark.asyncio` (no auto mode). Patch timers at the consuming module (`custom_components.adaptive_climate.managers.pump_flow_controller.async_call_later`).
- Pre-existing files already over the 800-line cap: `coordinator.py` (1276), `climate.py` (1265), `__init__.py` (1176), `const.py` (1216). Keep additions minimal; no refactors here. New files stay < 800.
- Spec values: coalescing window 30 s (never extended); `min_write_interval` default 300 s; startup delay 30 s after `EVENT_HOMEASSISTANT_STARTED`; re-evaluate every 300 s; write errors + entity-limit warnings logged ≤ once/hour; `flow` > 0; `min_flow` > 0; `min_write_interval` ≥ 0.
- Commits: Angular convention, stage files by name only. Never stage `CLAUDE.md`, `docs/subsystems/*` (other than the new `pump-flow-control.md`) or `docs/architecture/modules.md` — untracked user WIP (see Unresolved Q1).
- Run tests with `pytest` from repo root.

## Review Focus

- Valve closes before its actuation time elapses (short PWM cycle) → its flow never counts, no write → Task 4 `test_valve_closing_before_actuation_never_counts`.
- HEAT→COOL switch while the valve stays open → timing not restarted → Task 4 `test_reopen_report_keeps_original_timing`.
- Pump entity state `unavailable`/`unknown` → overwritten, no crash → Task 4 `test_non_numeric_target_state_is_overwritten`.
- Pump entity `min` > 0 and no zones open → writes that minimum right away (idle), no endless deferral → Task 4 `test_idle_with_entity_minimum_writes_that_minimum_at_once`.
- Open valve on a zone not in any manifold → ignored, no KeyError → Task 4 `test_zone_without_manifold_flow_is_ignored`; zone registered without `climate_entity_id` → slug fallback → Task 5 `test_missing_zone_data_falls_back_to_slug_entity_and_zero_actuation`.

## File Map

| File | Change |
|------|--------|
| `custom_components/adaptive_climate/const.py` | + manifold zone keys, pump consts; − `CONF_LOOPS`, `DEFAULT_LOOPS`, `CONF_FLOW_PER_LOOP`, `DEFAULT_FLOW_PER_LOOP` |
| `custom_components/adaptive_climate/manifold_config.py` | **new** — validators |
| `custom_components/adaptive_climate/adaptive/manifold_registry.py` | flow model + `manifolds_from_config` |
| `custom_components/adaptive_climate/__init__.py` | schema wiring, registry build |
| `custom_components/adaptive_climate/climate_setup.py` | `loops` → migration error; `zone_data["valve_actuation_time"]` |
| `custom_components/adaptive_climate/thermostat_config.py` | − `loops` |
| `custom_components/adaptive_climate/climate.py` | − `_loops`, `loops` property, `update_zone_loops` call |
| `custom_components/adaptive_climate/climate_init.py` | − `zone_loops=1` |
| `custom_components/adaptive_climate/coordinator.py` | − loops; + pump controller wiring |
| `custom_components/adaptive_climate/managers/pump_flow_math.py` | **new** — pure helpers |
| `custom_components/adaptive_climate/managers/pump_flow_controller.py` | **new** — controller |
| `custom_components/adaptive_climate/sensors/pump_flow.py` | **new** — diagnostic sensor |
| `custom_components/adaptive_climate/sensor.py` | register sensor |
| tests | new: `test_manifold_config.py`, `test_pump_flow_math.py`, `test_pump_flow_controller.py`, `test_pump_flow_wiring.py`, `test_pump_flow_sensor.py`; rewrite parts of `test_manifold_registry.py`, `test_coordinator.py`; delete `test_loops_config.py`, `TestManifoldConfigSchema` in `test_init.py`; preamble line in `test_central_controller.py` |
| `docs/subsystems/pump-flow-control.md` | **new** |

---

### Task 1: Constants + config validators

**Files:**
- Modify: `custom_components/adaptive_climate/const.py` (manifold block, `# Manifold transport delay`, ~line 1082)
- Create: `custom_components/adaptive_climate/manifold_config.py`
- Test: `tests/test_manifold_config.py`

**Interfaces:**
- Produces (const): `CONF_MANIFOLD_ZONE_ENTITY = "entity"`, `CONF_MANIFOLD_ZONE_FLOW = "flow"`, `CONF_PUMP_FLOW_CONTROL = "pump_flow_control"`, `CONF_PUMP_FLOW_TARGET_ENTITY = "target_entity"`, `CONF_PUMP_FLOW_MIN_FLOW = "min_flow"`, `CONF_PUMP_FLOW_MIN_WRITE_INTERVAL = "min_write_interval"`, `DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL = 300`, `PUMP_FLOW_DEBOUNCE_SECONDS = 30`, `PUMP_FLOW_STARTUP_DELAY_SECONDS = 30`, `PUMP_FLOW_UPDATE_INTERVAL_SECONDS = 300`, `PUMP_FLOW_WARN_INTERVAL_SECONDS = 3600`, `PUMP_FLOW_DEFAULT_STEP = 0.1`, `PUMP_FLOW_BINDING_ENTITY_LIMIT = "entity_limit"`.
- Produces (manifold_config): `MANIFOLD_SCHEMA`, `PUMP_FLOW_CONTROL_SCHEMA`, `validate_manifolds_and_pump_flow(config: dict[str, Any]) -> dict[str, Any]`, `removed_key(message: str) -> Callable[[Any], Any]`, `REMOVED_LOOPS_KEY = "loops"`, `REMOVED_FLOW_PER_LOOP_KEY = "flow_per_loop"`, `LOOPS_REMOVED_MESSAGE`, `FLOW_PER_LOOP_REMOVED_MESSAGE`, `ZONE_STRING_REMOVED_MESSAGE`.

- [ ] **Step 1: Write the failing tests** — `tests/test_manifold_config.py`

```python
"""Tests for manifold and pump flow config validators."""

import pytest
import voluptuous as vol

from custom_components.adaptive_climate.manifold_config import (
    LOOPS_REMOVED_MESSAGE,
    MANIFOLD_SCHEMA,
    PUMP_FLOW_CONTROL_SCHEMA,
    removed_key,
    validate_manifolds_and_pump_flow,
)


def manifold(**overrides):
    config = {
        "name": "Ground",
        "pipe_volume": 12,
        "zones": [{"entity": "climate.living", "flow": 4}, {"entity": "climate.kitchen", "flow": 3.5}],
    }
    config.update(overrides)
    return config


class TestManifoldSchema:
    def test_valid_manifold_is_normalised(self):
        result = MANIFOLD_SCHEMA(manifold())
        assert result["pipe_volume"] == 12.0
        assert result["zones"] == [
            {"entity": "climate.living", "flow": 4.0},
            {"entity": "climate.kitchen", "flow": 3.5},
        ]

    def test_plain_string_zone_fails_with_migration_message(self):
        with pytest.raises(vol.Invalid, match="plain entity ids in manifold zones were removed"):
            MANIFOLD_SCHEMA(manifold(zones=["climate.living"]))

    def test_flow_per_loop_fails_with_migration_message(self):
        with pytest.raises(vol.Invalid, match="'flow_per_loop' was removed"):
            MANIFOLD_SCHEMA(manifold(flow_per_loop=2.0))

    def test_missing_flow_is_rejected(self):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(zones=[{"entity": "climate.living"}]))

    @pytest.mark.parametrize("flow", [0, -1.5])
    def test_non_positive_flow_is_rejected(self, flow):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(zones=[{"entity": "climate.living", "flow": flow}]))

    def test_non_climate_zone_entity_is_rejected(self):
        with pytest.raises(vol.Invalid, match="climate"):
            MANIFOLD_SCHEMA(manifold(zones=[{"entity": "switch.living_valve", "flow": 4}]))

    def test_empty_zone_list_is_rejected(self):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(zones=[]))

    def test_small_pipe_volume_is_rejected(self):
        with pytest.raises(vol.Invalid):
            MANIFOLD_SCHEMA(manifold(pipe_volume=0.05))


class TestPumpFlowControlSchema:
    def test_minimal_config_gets_default_write_interval_and_no_floor(self):
        result = PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "number.heatpump_flow_setpoint"})
        assert result == {"target_entity": "number.heatpump_flow_setpoint", "min_write_interval": 300}

    def test_full_config(self):
        result = PUMP_FLOW_CONTROL_SCHEMA(
            {"target_entity": "input_number.pump_flow", "min_flow": "12", "min_write_interval": 0}
        )
        assert result == {"target_entity": "input_number.pump_flow", "min_flow": 12.0, "min_write_interval": 0}

    def test_target_must_be_a_number_entity(self):
        with pytest.raises(vol.Invalid):
            PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "sensor.pump_flow"})

    def test_min_flow_must_be_positive(self):
        with pytest.raises(vol.Invalid):
            PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "number.pump_flow", "min_flow": 0})

    def test_negative_write_interval_is_rejected(self):
        with pytest.raises(vol.Invalid):
            PUMP_FLOW_CONTROL_SCHEMA({"target_entity": "number.pump_flow", "min_write_interval": -1})


class TestCrossValidation:
    def test_zone_in_two_manifolds_is_rejected(self):
        config = {
            "manifolds": [
                MANIFOLD_SCHEMA(manifold()),
                MANIFOLD_SCHEMA(manifold(name="First", zones=[{"entity": "climate.living", "flow": 2}])),
            ]
        }
        with pytest.raises(vol.Invalid, match="one manifold only"):
            validate_manifolds_and_pump_flow(config)

    def test_pump_flow_control_requires_manifolds(self):
        with pytest.raises(vol.Invalid, match="requires manifolds"):
            validate_manifolds_and_pump_flow({"pump_flow_control": {"target_entity": "number.pump_flow"}})

    def test_valid_combination_passes_through(self):
        config = {
            "manifolds": [MANIFOLD_SCHEMA(manifold())],
            "pump_flow_control": {"target_entity": "number.pump_flow", "min_write_interval": 300},
        }
        assert validate_manifolds_and_pump_flow(config) is config

    def test_no_manifolds_and_no_pump_passes(self):
        assert validate_manifolds_and_pump_flow({}) == {}


def test_removed_key_always_fails_with_its_message():
    schema = vol.Schema({vol.Optional("loops"): removed_key(LOOPS_REMOVED_MESSAGE)})
    with pytest.raises(vol.Invalid, match="'loops' was removed"):
        schema({"loops": 2})
    assert schema({}) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_manifold_config.py -v`
Expected: FAIL — `ModuleNotFoundError: ... manifold_config`

- [ ] **Step 3: Add constants** — in `const.py` directly below `DEFAULT_FLOW_PER_LOOP = 2.0` (the loop constants are deleted in Task 2):

```python
CONF_MANIFOLD_ZONE_ENTITY = "entity"
CONF_MANIFOLD_ZONE_FLOW = "flow"

# Pump flow control
CONF_PUMP_FLOW_CONTROL = "pump_flow_control"
CONF_PUMP_FLOW_TARGET_ENTITY = "target_entity"
CONF_PUMP_FLOW_MIN_FLOW = "min_flow"
CONF_PUMP_FLOW_MIN_WRITE_INTERVAL = "min_write_interval"
DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL = 300  # seconds a decrease must persist before it is written
PUMP_FLOW_DEBOUNCE_SECONDS = 30
PUMP_FLOW_STARTUP_DELAY_SECONDS = 30
PUMP_FLOW_UPDATE_INTERVAL_SECONDS = 300
PUMP_FLOW_WARN_INTERVAL_SECONDS = 3600
PUMP_FLOW_DEFAULT_STEP = 0.1  # L/min, when the target entity exposes no step
PUMP_FLOW_BINDING_ENTITY_LIMIT = "entity_limit"
```

- [ ] **Step 4: Create `manifold_config.py`**

```python
"""Voluptuous validators for manifold and pump flow configuration.

Plain voluptuous only (no ``cv.*``), so tests exercise these exact validators.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

import voluptuous as vol

from .const import (
    CONF_MANIFOLD_ZONE_ENTITY,
    CONF_MANIFOLD_ZONE_FLOW,
    CONF_MANIFOLDS,
    CONF_PIPE_VOLUME,
    CONF_PUMP_FLOW_CONTROL,
    CONF_PUMP_FLOW_MIN_FLOW,
    CONF_PUMP_FLOW_MIN_WRITE_INTERVAL,
    CONF_PUMP_FLOW_TARGET_ENTITY,
    DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL,
)

REMOVED_LOOPS_KEY = "loops"
REMOVED_FLOW_PER_LOOP_KEY = "flow_per_loop"

_MIGRATION_HINT = "list each zone on its manifold as {entity: climate.<zone>, flow: <L/min>}"
LOOPS_REMOVED_MESSAGE = f"'loops' was removed; {_MIGRATION_HINT}"
FLOW_PER_LOOP_REMOVED_MESSAGE = f"'flow_per_loop' was removed; {_MIGRATION_HINT}"
ZONE_STRING_REMOVED_MESSAGE = f"plain entity ids in manifold zones were removed; {_MIGRATION_HINT}"

_CLIMATE_ENTITY_RE = re.compile(r"^climate\.[a-z0-9_]+$")
_NUMBER_ENTITY_RE = re.compile(r"^(number|input_number)\.[a-z0-9_]+$")


def removed_key(message: str) -> Callable[[Any], Any]:
    """Return a validator that rejects a removed config key with a migration message."""

    def _reject(_value: Any) -> Any:
        raise vol.Invalid(message)

    return _reject


def _entity_id(pattern: re.Pattern[str], description: str) -> Callable[[Any], str]:
    def _validate(value: Any) -> str:
        if not isinstance(value, str):
            raise vol.Invalid(f"expected {description} entity id")
        normalized = value.strip().lower()
        if not pattern.match(normalized):
            raise vol.Invalid(f"expected {description} entity id, got '{value}'")
        return normalized

    return _validate


_climate_entity_id = _entity_id(_CLIMATE_ENTITY_RE, "a climate.*")
_number_entity_id = _entity_id(_NUMBER_ENTITY_RE, "a number.* or input_number.*")


def _non_empty_string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise vol.Invalid("expected a non-empty string")
    return value


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


_POSITIVE_FLOAT = vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False))

_MANIFOLD_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MANIFOLD_ZONE_ENTITY): _climate_entity_id,
        vol.Required(CONF_MANIFOLD_ZONE_FLOW): _POSITIVE_FLOAT,
    }
)


def _manifold_zone(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        raise vol.Invalid(f"{ZONE_STRING_REMOVED_MESSAGE} (got '{value}')")
    return _MANIFOLD_ZONE_SCHEMA(value)


MANIFOLD_SCHEMA = vol.Schema(
    {
        vol.Required("name"): _non_empty_string,
        vol.Required("zones"): vol.All(_ensure_list, [_manifold_zone], vol.Length(min=1)),
        vol.Required(CONF_PIPE_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
        vol.Optional(REMOVED_FLOW_PER_LOOP_KEY): removed_key(FLOW_PER_LOOP_REMOVED_MESSAGE),
    }
)

PUMP_FLOW_CONTROL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PUMP_FLOW_TARGET_ENTITY): _number_entity_id,
        vol.Optional(CONF_PUMP_FLOW_MIN_FLOW): _POSITIVE_FLOAT,
        vol.Optional(CONF_PUMP_FLOW_MIN_WRITE_INTERVAL, default=DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
    }
)


def validate_manifolds_and_pump_flow(config: dict[str, Any]) -> dict[str, Any]:
    """Reject a zone listed on two manifolds, and pump flow control without manifolds."""
    owner: dict[str, str] = {}
    for manifold in config.get(CONF_MANIFOLDS) or []:
        for zone in manifold["zones"]:
            entity_id = zone[CONF_MANIFOLD_ZONE_ENTITY]
            if entity_id in owner:
                raise vol.Invalid(
                    f"{entity_id} is listed in manifold '{owner[entity_id]}' and '{manifold['name']}'; "
                    "a zone may belong to one manifold only"
                )
            owner[entity_id] = manifold["name"]
    if config.get(CONF_PUMP_FLOW_CONTROL) and not owner:
        raise vol.Invalid("pump_flow_control requires manifolds with per-zone flow")
    return config
```

- [ ] **Step 5: Run tests** — `pytest tests/test_manifold_config.py -v` → PASS. Then `ruff check custom_components/adaptive_climate/manifold_config.py tests/test_manifold_config.py && ruff format custom_components/adaptive_climate/manifold_config.py tests/test_manifold_config.py && pyright custom_components/adaptive_climate/manifold_config.py` → clean.

- [ ] **Step 6: Commit**

```bash
git add custom_components/adaptive_climate/const.py custom_components/adaptive_climate/manifold_config.py tests/test_manifold_config.py
git commit -m "feat(manifold): add per-zone flow and pump flow config validators"
```

---

### Task 2: Replace loops model with per-zone flow

**Files:**
- Modify: `adaptive/manifold_registry.py`, `__init__.py`, `climate_setup.py`, `thermostat_config.py`, `climate.py`, `climate_init.py`, `coordinator.py`, `const.py` (all under `custom_components/adaptive_climate/`)
- Test: `tests/test_manifold_registry.py`, `tests/test_coordinator.py`, `tests/test_manifold_config.py`; delete `tests/test_loops_config.py`; edit `tests/test_init.py`

**Interfaces:**
- Consumes: Task 1 `MANIFOLD_SCHEMA`, `validate_manifolds_and_pump_flow`, `removed_key`, `REMOVED_LOOPS_KEY`, `LOOPS_REMOVED_MESSAGE`, `CONF_MANIFOLD_ZONE_ENTITY`, `CONF_MANIFOLD_ZONE_FLOW`.
- Produces: `Manifold(name: str, pipe_volume: float, zones: dict[str, float])`; `manifolds_from_config(manifolds_config: list[dict[str, Any]]) -> list[Manifold]`; `ManifoldRegistry.get_transport_delay(zone_id: str, active_zones: set[str]) -> float`; `ManifoldRegistry.get_worst_case_transport_delay(zone_id: str) -> float`; `coordinator.get_worst_case_transport_delay_for_zone(zone_id: str) -> float`. Removed: `coordinator.update_zone_loops`, `coordinator._zone_loops`, `AdaptiveThermostatConfig.loops`, `AdaptiveThermostat.loops`.

- [ ] **Step 1: Rewrite registry tests** in `tests/test_manifold_registry.py`:

  1. Imports → add `manifolds_from_config`:
     ```python
     from custom_components.adaptive_climate.adaptive.manifold_registry import (
         Manifold,
         ManifoldRegistry,
         manifolds_from_config,
     )
     ```
  2. **Replace** classes `TestManifoldDataclass`, `TestTransportDelayColdManifold`, `TestMultipleZonesActiveSameManifold`, `TestWorstCaseTransportDelay` with:

```python
def ground_floor() -> ManifoldRegistry:
    return ManifoldRegistry(
        [
            Manifold(
                name="Ground",
                pipe_volume=20.0,
                zones={"climate.living": 4.0, "climate.kitchen": 3.5, "climate.hall": 2.5},
            ),
            Manifold(name="First", pipe_volume=10.0, zones={"climate.bed": 2.0}),
        ]
    )


class TestManifoldDataclass:
    def test_manifold_holds_per_zone_flow(self):
        manifold = Manifold(
            name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0, "climate.bedroom_2nd": 3.0}
        )
        assert manifold.name == "2nd Floor"
        assert manifold.pipe_volume == 20.0
        assert manifold.zones == {"climate.bathroom_2nd": 2.0, "climate.bedroom_2nd": 3.0}


class TestManifoldsFromConfig:
    def test_builds_manifolds_from_validated_config(self):
        config = [
            {
                "name": "Ground",
                "pipe_volume": 12,
                "zones": [{"entity": "climate.living", "flow": 4}, {"entity": "climate.kitchen", "flow": 3.5}],
            },
            {"name": "First", "pipe_volume": 8.0, "zones": [{"entity": "climate.bed", "flow": 2.0}]},
        ]
        manifolds = manifolds_from_config(config)
        assert [m.name for m in manifolds] == ["Ground", "First"]
        assert manifolds[0].pipe_volume == 12.0
        assert manifolds[0].zones == {"climate.living": 4.0, "climate.kitchen": 3.5}
        assert manifolds[1].zones == {"climate.bed": 2.0}

    def test_empty_config_builds_nothing(self):
        assert manifolds_from_config([]) == []


class TestTransportDelayColdManifold:
    def test_single_active_zone_uses_its_flow(self):
        assert ground_floor().get_transport_delay("climate.living", {"climate.living"}) == pytest.approx(5.0)

    def test_active_zones_on_the_manifold_add_their_flows(self):
        delay = ground_floor().get_transport_delay(
            "climate.living", {"climate.living", "climate.kitchen", "climate.hall"}
        )
        assert delay == pytest.approx(2.0)  # 20 / (4 + 3.5 + 2.5)

    def test_only_active_zones_count(self):
        delay = ground_floor().get_transport_delay("climate.living", {"climate.living", "climate.kitchen"})
        assert delay == pytest.approx(20.0 / 7.5)

    def test_active_zone_on_another_manifold_is_not_counted(self):
        delay = ground_floor().get_transport_delay("climate.living", {"climate.living", "climate.bed"})
        assert delay == pytest.approx(5.0)

    def test_no_active_zone_on_the_manifold_means_no_delay(self):
        assert ground_floor().get_transport_delay("climate.living", {"climate.bed"}) == 0.0

    def test_empty_active_set_means_no_delay(self):
        assert ground_floor().get_transport_delay("climate.living", set()) == 0.0


class TestWorstCaseTransportDelay:
    def test_worst_case_is_the_zone_alone(self):
        assert ground_floor().get_worst_case_transport_delay("climate.hall") == pytest.approx(8.0)  # 20 / 2.5

    def test_worst_case_unknown_zone_is_zero(self):
        assert ground_floor().get_worst_case_transport_delay("climate.garage") == 0.0

    def test_worst_case_empty_registry_is_zero(self):
        assert ManifoldRegistry([]).get_worst_case_transport_delay("climate.living") == 0.0

    def test_worst_case_is_never_shorter_than_the_live_delay(self):
        registry = ground_floor()
        live = registry.get_transport_delay("climate.living", {"climate.living", "climate.kitchen"})
        assert registry.get_worst_case_transport_delay("climate.living") >= live
```

  3. **Keep** `TestManifoldRegistryLookup`, `TestTransportDelayWarmManifold`, `TestTransportDelayUnknownZone`, `TestMarkManifoldActiveProductionScenario`, `TestManifoldPersistence`, applying these mechanical rules:
     - **R1** `Manifold(name=N, zones=[a, b], pipe_volume=V, flow_per_loop=F)` → `Manifold(name=N, pipe_volume=V, zones={a: F, b: F})`. Missing `flow_per_loop` → use `2.0`.
     - **R2** `active_zones = {"climate.x": n, ...}` → `{"climate.x", ...}` (set of keys). Where any `n > 1`, recompute the expected delay as `pipe_volume / Σ flow of the listed zones on that manifold` and update the assertion and its comment.
     - **R3** Assertions comparing `manifold.zones` to a list → compare `list(manifold.zones)` to that list.

- [ ] **Step 2: Update coordinator tests** in `tests/test_coordinator.py`:
  - Delete `test_update_zone_loops` and `test_update_zone_loops_unregistered_zone`.
  - `test_get_transport_delay_for_zone_no_active_zones`: `assert call_args[0][1] == {}` → `assert call_args[0][1] == set()`.
  - `test_get_transport_delay_converts_slug_to_entity_id`: delete the two `coord.update_zone_loops(...)` lines and the comment above them; replace any `active_zones[...] == N` assertion with `assert "climate.<zone>" in active_zones` for the same zone; docstring: drop the `_zone_loops` mention.
  - `test_get_worst_case_transport_delay_for_zone_with_registry`: call `coord.get_worst_case_transport_delay_for_zone("climate.bathroom_2nd")`, comment `# 20L / 4 L/min = 5 min`, `mock_registry.get_worst_case_transport_delay.assert_called_once_with("climate.bathroom_2nd")`.
  - `test_get_worst_case_transport_delay_for_zone_no_registry` / `_unknown_zone`: drop the `zone_loops=1` argument; `_unknown_zone` asserts `assert_called_once_with("climate.unknown_zone")`.
  - Delete `test_get_worst_case_transport_delay_for_zone_default_loops`.

- [ ] **Step 3: Remove obsolete tests**
  - `git rm tests/test_loops_config.py`
  - `tests/test_init.py`: delete class `TestManifoldConfigSchema` (it re-declares a local schema; superseded by `tests/test_manifold_config.py`), then remove any import of `CONF_FLOW_PER_LOOP` left in that file (`grep -n "FLOW_PER_LOOP\|flow_per_loop" tests/test_init.py` → no hits).
  - Append to `tests/test_manifold_config.py` (put `from pathlib import Path` in the file's import block):

```python
PACKAGE = Path(__file__).parent.parent / "custom_components" / "adaptive_climate"


def test_domain_schema_uses_the_shared_manifold_validators():
    source = (PACKAGE / "__init__.py").read_text()
    assert "from .manifold_config import" in source
    assert "validate_manifolds_and_pump_flow" in source


def test_entity_schema_rejects_loops_with_migration_message():
    source = (PACKAGE / "climate_setup.py").read_text()
    assert "vol.Optional(REMOVED_LOOPS_KEY): removed_key(LOOPS_REMOVED_MESSAGE)" in source
```

- [ ] **Step 4: Run to verify failure**

Run: `pytest tests/test_manifold_registry.py tests/test_coordinator.py tests/test_manifold_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'manifolds_from_config'` and wiring assertions.

- [ ] **Step 5: Rewrite `adaptive/manifold_registry.py`** — module docstring formula, dataclass, builder, delay methods (keep `__init__`, `get_manifold_for_zone`, `mark_manifold_active`, `get_state_for_persistence`, `restore_state` unchanged):

```python
"""Manifold transport delay tracking for hydronic heating systems.

When a manifold has been inactive for >5 minutes, water in the pipes cools.
The first heating cycle must fill the manifold with hot water before zones
receive heat, introducing a transport delay.

Formula:
    delay = pipe_volume / Σ flow of active zones on the manifold

Once the manifold is warm (active within 5 minutes), delay is zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import (
    CONF_MANIFOLD_ZONE_ENTITY,
    CONF_MANIFOLD_ZONE_FLOW,
    CONF_PIPE_VOLUME,
    MANIFOLD_COOLDOWN_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Manifold:
    """A hydraulic manifold serving one or more zones.

    Attributes:
        name: Unique identifier for the manifold (e.g., "2nd Floor")
        pipe_volume: Pipe volume in liters from heat source to manifold
        zones: Zone entity_id -> design flow in L/min
    """

    name: str
    pipe_volume: float
    zones: dict[str, float]


def manifolds_from_config(manifolds_config: list[dict[str, Any]]) -> list[Manifold]:
    """Build manifolds from validated ``manifolds:`` config entries."""
    return [
        Manifold(
            name=m["name"],
            pipe_volume=float(m[CONF_PIPE_VOLUME]),
            zones={z[CONF_MANIFOLD_ZONE_ENTITY]: float(z[CONF_MANIFOLD_ZONE_FLOW]) for z in m["zones"]},
        )
        for m in manifolds_config
    ]
```

  Replace `get_transport_delay` and `get_worst_case_transport_delay`:

```python
    def get_transport_delay(self, zone_id: str, active_zones: set[str]) -> float:
        """Calculate transport delay for a zone in minutes.

        Args:
            zone_id: Zone entity_id requesting delay calculation
            active_zones: Entity_ids of zones currently drawing water

        Returns:
            Transport delay in minutes. 0 when the zone is unknown, the manifold
            was active within the cooldown, or no active zone is on the manifold.
        """
        manifold = self.get_manifold_for_zone(zone_id)
        if not manifold:
            return 0.0

        if manifold.name in self._last_active_time:
            time_since_active = dt_util.utcnow() - self._last_active_time[manifold.name]
            if time_since_active < timedelta(minutes=MANIFOLD_COOLDOWN_MINUTES):
                return 0.0

        total_flow = sum(flow for zone, flow in manifold.zones.items() if zone in active_zones)
        if total_flow <= 0:
            return 0.0
        return manifold.pipe_volume / total_flow

    def get_worst_case_transport_delay(self, zone_id: str) -> float:
        """Transport delay in minutes with only this zone drawing water, or 0.0 if not on a manifold."""
        manifold = self.get_manifold_for_zone(zone_id)
        if not manifold:
            return 0.0
        return manifold.pipe_volume / manifold.zones[zone_id]
```

- [ ] **Step 6: `__init__.py`**
  - `.const` import: remove `CONF_FLOW_PER_LOOP`, `DEFAULT_FLOW_PER_LOOP`, `CONF_PIPE_VOLUME`.
  - Delete the whole `# Manifold schema` / `MANIFOLD_SCHEMA = vol.Schema(...)` block inside `if HAS_HOMEASSISTANT:` and the `MANIFOLD_SCHEMA = None` line in the `else:` stub block. Add a module-level import right after the `from .const import (...)` block (unconditional — an import plus a `None` stub would be a pyright redeclaration):
    ```python
    from .manifold_config import MANIFOLD_SCHEMA, validate_manifolds_and_pump_flow
    ```
  - `CONFIG_SCHEMA`: after `validate_water_temp_control,` add `validate_manifolds_and_pump_flow,`.
  - Registry block in `async_setup`: import `from .adaptive.manifold_registry import ManifoldRegistry, manifolds_from_config` and replace the `manifolds = [Manifold(...) for m in manifolds_config]` comprehension with `manifolds = manifolds_from_config(manifolds_config)`.

- [ ] **Step 7: Entity side**
  - `climate_setup.py`: after `from . import const` add `from .manifold_config import LOOPS_REMOVED_MESSAGE, REMOVED_LOOPS_KEY, removed_key`. Replace the `vol.Optional(const.CONF_LOOPS, default=const.DEFAULT_LOOPS): ...` line (under `# Manifold integration`) with `vol.Optional(REMOVED_LOOPS_KEY): removed_key(LOOPS_REMOVED_MESSAGE),`. Delete `loops=config.get(const.CONF_LOOPS, const.DEFAULT_LOOPS),` in the `AdaptiveThermostatConfig(...)` call.
  - `thermostat_config.py`: delete `loops: int = DEFAULT_LOOPS` and the `DEFAULT_LOOPS` import.
  - `climate.py`: delete `self._loops = config.loops` (~line 203) and the `loops` property (~line 1101). In the manifold registration block (~line 551) delete the `update_zone_loops` call and its comment; log line becomes `_LOGGER.info("%s: Registered with manifold registry", self.entity_id)`.
  - `climate_init.py` (~line 156): `coordinator.get_worst_case_transport_delay_for_zone(thermostat.entity_id)` (drop `zone_loops=1` + comment).

- [ ] **Step 8: `coordinator.py`**
  - Delete `self._zone_loops: dict[str, int] = {}`, method `update_zone_loops`, and the `_zone_loops` cleanup in `unregister_zone`.
  - Replace body of `get_transport_delay_for_zone` after the registry-None guard:
    ```python
        # demand_states is keyed by zone slug; the registry is keyed by climate entity_id
        active_zones = {
            f"climate.{zone_slug}"
            for zone_slug, demand_state in self._demand_states.items()
            if demand_state.get("demand") and demand_state.get("mode") == "heat"
        }
        return self._manifold_registry.get_transport_delay(zone_id, active_zones)
    ```
  - `get_worst_case_transport_delay_for_zone(self, zone_id: str) -> float`: drop `zone_loops` param + its docstring line; `return self._manifold_registry.get_worst_case_transport_delay(zone_id)`.

- [ ] **Step 9: `const.py`** — delete `CONF_FLOW_PER_LOOP`, `CONF_LOOPS`, `DEFAULT_LOOPS`, `DEFAULT_FLOW_PER_LOOP`.

- [ ] **Step 10: Verify**
  - `grep -rnE "flow_per_loop|FLOW_PER_LOOP|CONF_LOOPS|DEFAULT_LOOPS|zone_loops|update_zone_loops|\._loops\b" custom_components tests` → only hits in `manifold_config.py` / `tests/test_manifold_config.py`.
  - `pytest` (full suite) → PASS. `ruff check custom_components/ tests/ && ruff format custom_components/ tests/ && pyright custom_components/adaptive_climate/` → clean.

- [ ] **Step 11: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/manifold_registry.py custom_components/adaptive_climate/__init__.py \
  custom_components/adaptive_climate/climate_setup.py custom_components/adaptive_climate/thermostat_config.py \
  custom_components/adaptive_climate/climate.py custom_components/adaptive_climate/climate_init.py \
  custom_components/adaptive_climate/coordinator.py custom_components/adaptive_climate/const.py \
  tests/test_manifold_registry.py tests/test_coordinator.py tests/test_manifold_config.py tests/test_init.py
git commit -m "feat(manifold): replace loops model with per-zone flow

Manifold zones are now {entity, flow} entries and transport delay divides
pipe volume by the summed flow of active zones.

BREAKING CHANGE: entity-level 'loops', manifold 'flow_per_loop' and plain
entity-id zone entries are rejected; list zones as {entity, flow}."
```
(`test_loops_config.py` deletion is already staged by `git rm`.)

---

### Task 3: Pure pump flow helpers

**Files:**
- Create: `custom_components/adaptive_climate/managers/pump_flow_math.py`
- Test: `tests/test_pump_flow_math.py`

**Interfaces:**
- Consumes: `PUMP_FLOW_DEFAULT_STEP` (Task 1); `coerce_float` from `managers/water_temp_writer.py`.
- Produces: `FLOW_EPSILON: float`; `target_flow(counted_flows: Iterable[float], min_flow: float | None) -> float`; `round_up(value: float, step: float) -> float`; `flow_limits(state: Any) -> tuple[float, float, float]`; `parse_current(state: Any) -> float | None`; `WriteDecision(write: bool, decrease_pending_since: float | None, recheck_in: float | None)`; `decide_write(final: float, current: float | None, *, idle: bool, pending_since: float | None, now: float, min_write_interval: float) -> WriteDecision`.

- [ ] **Step 1: Write the failing tests** — `tests/test_pump_flow_math.py`

```python
"""Tests for pure pump flow helpers."""

import math
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_climate.managers.pump_flow_math import (
    decide_write,
    flow_limits,
    parse_current,
    round_up,
    target_flow,
)


def state(value="0", **attributes):
    s = MagicMock()
    s.state = value
    s.attributes = attributes
    return s


class TestTargetFlow:
    def test_nothing_open_is_zero(self):
        assert target_flow([], None) == 0.0

    def test_nothing_open_ignores_min_flow(self):
        assert target_flow([], 12.0) == 0.0

    def test_sum_of_counted_zones(self):
        assert target_flow([4.0, 3.5], None) == pytest.approx(7.5)

    def test_min_flow_floors_a_small_sum(self):
        assert target_flow([4.0], 12.0) == 12.0

    def test_sum_above_min_flow_is_kept(self):
        assert target_flow([8.0, 6.0], 12.0) == pytest.approx(14.0)


class TestRoundUp:
    def test_rounds_up_to_step(self):
        assert round_up(3.5, 1.0) == 4.0

    def test_exact_multiple_unchanged(self):
        assert round_up(7.5, 0.5) == 7.5

    def test_float_noise_does_not_bump_a_step(self):
        assert round_up(0.1 + 0.2, 0.1) == pytest.approx(0.3)

    def test_non_positive_step_rounds_to_millis(self):
        assert round_up(3.14159, 0) == 3.142


class TestFlowLimits:
    def test_reads_entity_attributes(self):
        assert flow_limits(state(min=5, max=40, step=0.5)) == (5.0, 40.0, 0.5)

    def test_missing_attributes_fall_back(self):
        minimum, maximum, step = flow_limits(state())
        assert (minimum, step) == (0.0, 0.1)
        assert math.isinf(maximum)

    def test_swapped_min_max_are_normalised(self):
        assert flow_limits(state(min=40, max=5, step=1)) == (5.0, 40.0, 1.0)

    def test_non_positive_step_falls_back(self):
        assert flow_limits(state(step=0))[2] == 0.1


class TestParseCurrent:
    def test_numeric_state(self):
        assert parse_current(state("7.5")) == 7.5

    @pytest.mark.parametrize("raw", ["unknown", "unavailable", ""])
    def test_non_numeric_state_is_none(self, raw):
        assert parse_current(state(raw)) is None


class TestDecideWrite:
    @staticmethod
    def decide(final, current, *, idle=False, pending_since=None, now=1000.0, interval=300.0):
        return decide_write(
            final, current, idle=idle, pending_since=pending_since, now=now, min_write_interval=interval
        )

    def test_unknown_current_value_writes(self):
        d = self.decide(4.0, None)
        assert (d.write, d.decrease_pending_since, d.recheck_in) == (True, None, None)

    def test_increase_writes_now(self):
        assert self.decide(7.5, 4.0).write

    def test_equal_is_a_no_op_and_clears_pending(self):
        d = self.decide(4.0, 4.0, pending_since=900.0)
        assert (d.write, d.decrease_pending_since, d.recheck_in) == (False, None, None)

    def test_first_decrease_starts_pending(self):
        d = self.decide(4.0, 7.5)
        assert (d.write, d.decrease_pending_since, d.recheck_in) == (False, 1000.0, 300.0)

    def test_decrease_still_pending_keeps_its_start(self):
        d = self.decide(4.0, 7.5, pending_since=900.0)
        assert not d.write
        assert d.decrease_pending_since == 900.0
        assert d.recheck_in == pytest.approx(200.0)

    def test_decrease_written_once_interval_elapsed(self):
        d = self.decide(4.0, 7.5, pending_since=700.0)
        assert (d.write, d.decrease_pending_since) == (True, None)

    def test_increase_clears_pending(self):
        d = self.decide(9.0, 7.5, pending_since=900.0)
        assert (d.write, d.decrease_pending_since) == (True, None)

    def test_idle_decrease_writes_now(self):
        assert self.decide(0.0, 7.5, idle=True).write

    def test_idle_at_current_value_is_a_no_op(self):
        assert not self.decide(5.0, 5.0, idle=True).write

    def test_zero_interval_writes_decrease_now(self):
        assert self.decide(4.0, 7.5, interval=0.0).write
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_pump_flow_math.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement** — `managers/pump_flow_math.py`

```python
"""Pure pump flow helpers: target, rounding, entity limits and the write decision."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from typing import Any

from ..const import PUMP_FLOW_DEFAULT_STEP
from .water_temp_writer import coerce_float

FLOW_EPSILON = 1e-6


def target_flow(counted_flows: Iterable[float], min_flow: float | None) -> float:
    """Return 0 when nothing counts, else the summed flow floored at ``min_flow``."""
    total = sum(counted_flows)
    if total <= 0:
        return 0.0
    if min_flow is None:
        return total
    return max(total, min_flow)


def round_up(value: float, step: float) -> float:
    """Round up to the entity step so the pump never gets less than requested."""
    if step <= 0:
        return round(value, 3)
    return round(math.ceil(value / step - 1e-9) * step, 3)


def flow_limits(state: Any) -> tuple[float, float, float]:
    """Return the target entity's (min, max, step); missing attributes fall back to (0, inf, default step)."""
    attributes = getattr(state, "attributes", None) or {}
    step = coerce_float(attributes.get("step"), PUMP_FLOW_DEFAULT_STEP)
    if step <= 0:
        step = PUMP_FLOW_DEFAULT_STEP
    minimum = coerce_float(attributes.get("min"), 0.0)
    maximum = coerce_float(attributes.get("max"), math.inf)
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return minimum, maximum, step


def parse_current(state: Any) -> float | None:
    """Return the entity's numeric value, or None when unknown, unavailable or non-numeric."""
    try:
        return float(state.state)
    except (AttributeError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class WriteDecision:
    """Outcome of comparing the new target with the entity's current value."""

    write: bool
    decrease_pending_since: float | None
    recheck_in: float | None


def decide_write(
    final: float,
    current: float | None,
    *,
    idle: bool,
    pending_since: float | None,
    now: float,
    min_write_interval: float,
) -> WriteDecision:
    """Increases and the drop to idle write now; other decreases must persist ``min_write_interval``."""
    if current is None:
        return WriteDecision(True, None, None)
    if abs(final - current) < FLOW_EPSILON:
        return WriteDecision(False, None, None)
    if idle or final > current:
        return WriteDecision(True, None, None)
    since = now if pending_since is None else pending_since
    waited = now - since
    if waited >= min_write_interval:
        return WriteDecision(True, None, None)
    return WriteDecision(False, since, min_write_interval - waited)
```

- [ ] **Step 4: Run tests** — `pytest tests/test_pump_flow_math.py -v` → PASS; ruff check/format + `pyright custom_components/adaptive_climate/managers/pump_flow_math.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/pump_flow_math.py tests/test_pump_flow_math.py
git commit -m "feat(pump): add pure pump flow helpers"
```

---

### Task 4: PumpFlowController

**Files:**
- Create: `custom_components/adaptive_climate/managers/pump_flow_controller.py`
- Test: `tests/test_pump_flow_controller.py`

**Interfaces:**
- Consumes: Task 3 helpers; Task 1 consts; `HeaterServiceCaller.get_number_entity_domain(entity_id) -> str` (static).
- Produces: `PumpFlowController(hass, config: dict[str, Any], zone_flows: dict[str, float], monotonic: Callable[[], float] = time.monotonic)`; `PumpFlowController.from_config(hass, config, manifolds_config: list[dict[str, Any]]) -> PumpFlowController`; `on_zone_demand(zone_entity_id: str, is_open: bool, valve_actuation_time: float) -> None`; `diagnostics() -> dict[str, Any]` with keys `requested`, `open_zones`, `pending_zones`, `raw_sum`, `min_flow_applied`, `decrease_pending_since`, `binding_constraint`, `last_write`; `async_start() -> None`; `async_cleanup() -> None`. Test hooks: `_async_startup(_now)`, `_async_timer_tick(_now)`, `_zone_flows`.

- [ ] **Step 1: Write the failing tests** — `tests/test_pump_flow_controller.py`

```python
"""Tests for PumpFlowController."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_climate.managers import pump_flow_controller as pump_module
from custom_components.adaptive_climate.managers.pump_flow_controller import PumpFlowController

TARGET = "number.heatpump_flow_setpoint"
LIVING = "climate.living"
KITCHEN = "climate.kitchen"
ZONE_FLOWS = {LIVING: 4.0, KITCHEN: 3.5}


class FakeScheduler:
    """Deterministic monotonic clock plus an ``async_call_later`` stand-in."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._timers: list[dict] = []

    def monotonic(self) -> float:
        return self.now

    def call_later(self, _hass, delay, action):
        timer = {"due": self.now + delay, "action": action, "cancelled": False}
        self._timers.append(timer)

        def cancel() -> None:
            timer["cancelled"] = True

        return cancel

    def active(self) -> list[dict]:
        return [t for t in self._timers if not t["cancelled"]]

    async def advance(self, seconds: float) -> None:
        """Move time forward, firing every timer that comes due, earliest first."""
        end = self.now + seconds
        while True:
            due = [t for t in self.active() if t["due"] <= end]
            if not due:
                break
            timer = min(due, key=lambda t: t["due"])
            timer["cancelled"] = True
            self.now = max(self.now, timer["due"])
            await timer["action"](None)
        self.now = end


def number_state(value, *, step=0.5, minimum=0.0, maximum=60.0):
    state = MagicMock()
    state.state = str(value)
    state.attributes = {"step": step, "min": minimum, "max": maximum}
    return state


def build(monkeypatch, *, initial=0.0, min_flow=None, min_write_interval=300, limits=None, target_state=True):
    scheduler = FakeScheduler()
    monkeypatch.setattr(pump_module, "async_call_later", scheduler.call_later)
    limits = limits or {}
    states = {TARGET: number_state(initial, **limits)} if target_state else {}

    async def set_value(_domain, _service, data, blocking=False):
        states[data["entity_id"]] = number_state(data["value"], **limits)

    hass = MagicMock()
    hass.states.get = states.get
    hass.services.async_call = AsyncMock(side_effect=set_value)

    config = {"target_entity": TARGET, "min_write_interval": min_write_interval}
    if min_flow is not None:
        config["min_flow"] = min_flow
    controller = PumpFlowController(hass, config, dict(ZONE_FLOWS), monotonic=scheduler.monotonic)
    return controller, hass, states, scheduler


def written(hass) -> list[float]:
    return [call.args[2]["value"] for call in hass.services.async_call.call_args_list]


async def start(controller) -> None:
    await controller._async_startup(None)


class TestValveTiming:
    @pytest.mark.asyncio
    async def test_zone_flow_waits_for_valve_actuation(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(119)
        assert written(hass) == []

        await sched.advance(1 + 30)  # counts at 120 s, window closes at 150 s
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_valve_closing_before_actuation_never_counts(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(60)
        controller.on_zone_demand(LIVING, False, 0.0)
        await sched.advance(600)

        assert written(hass) == []
        assert controller.diagnostics()["pending_zones"] == {}

    @pytest.mark.asyncio
    async def test_reopen_report_keeps_original_timing(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(60)
        controller.on_zone_demand(LIVING, True, 120.0)  # HEAT -> COOL with the valve still open
        await sched.advance(60 + 30)

        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_zone_without_manifold_flow_is_ignored(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand("climate.garage", True, 0.0)
        await sched.advance(600)

        assert written(hass) == []
        assert sched.active() == []

    @pytest.mark.asyncio
    async def test_diagnostics_report_seconds_until_a_zone_counts(self, monkeypatch):
        controller, _hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 120.0)
        await sched.advance(20)

        diagnostics = controller.diagnostics()
        assert diagnostics["pending_zones"] == {LIVING: 100.0}
        assert diagnostics["open_zones"] == []


class TestWritePolicy:
    @pytest.mark.asyncio
    async def test_increase_waits_for_the_coalescing_window(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(29)
        assert written(hass) == []
        await sched.advance(1)
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_window_is_not_extended_by_later_changes(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(20)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(10)

        assert written(hass) == [7.5]

    @pytest.mark.asyncio
    async def test_decrease_waits_for_min_write_interval(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)
        assert written(hass) == []

        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(30 + 299)
        assert written(hass) == []
        assert controller.diagnostics()["raw_sum"] == pytest.approx(4.0)
        assert controller.diagnostics()["decrease_pending_since"] is not None

        await sched.advance(1)
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_pending_decrease_is_dropped_when_target_recovers(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)

        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(100)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(600)

        assert written(hass) == []
        assert controller.diagnostics()["decrease_pending_since"] is None

    @pytest.mark.asyncio
    async def test_drop_to_idle_is_written_after_the_window(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)

        controller.on_zone_demand(LIVING, False, 0.0)
        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(30)

        assert written(hass) == [0.0]

    @pytest.mark.asyncio
    async def test_zero_min_write_interval_writes_decreases_at_once(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, initial=7.5, min_write_interval=0)
        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await start(controller)

        controller.on_zone_demand(KITCHEN, False, 0.0)
        await sched.advance(30)

        assert written(hass) == [4.0]


class TestTarget:
    @pytest.mark.asyncio
    async def test_min_flow_floors_a_small_sum(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, min_flow=12.0)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(30)

        assert written(hass) == [12.0]
        assert controller.diagnostics()["min_flow_applied"] is True

    @pytest.mark.asyncio
    async def test_rounds_up_to_the_entity_step(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, limits={"step": 1.0})
        await start(controller)

        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(30)

        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_entity_maximum_clamps_and_is_flagged(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch, limits={"maximum": 5.0})
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        controller.on_zone_demand(KITCHEN, True, 0.0)
        await sched.advance(30)

        assert written(hass) == [5.0]
        assert controller.diagnostics()["binding_constraint"] == "entity_limit"

    @pytest.mark.asyncio
    async def test_idle_with_entity_minimum_writes_that_minimum_at_once(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch, initial=8.0, limits={"minimum": 5.0})

        await start(controller)

        assert written(hass) == [5.0]
        assert controller.diagnostics()["binding_constraint"] == "entity_limit"


class TestTargetEntityState:
    @pytest.mark.asyncio
    async def test_missing_target_state_skips_then_retries_on_the_interval(self, monkeypatch):
        controller, hass, states, sched = build(monkeypatch, target_state=False)
        await start(controller)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(30)
        assert written(hass) == []

        states[TARGET] = number_state(0.0)
        await controller._async_timer_tick(None)
        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_non_numeric_target_state_is_overwritten(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch, initial="unavailable")
        controller.on_zone_demand(LIVING, True, 0.0)

        await start(controller)

        assert written(hass) == [4.0]

    @pytest.mark.asyncio
    async def test_manual_lowering_is_reverted_on_the_next_interval(self, monkeypatch):
        controller, hass, states, _sched = build(monkeypatch)
        controller.on_zone_demand(LIVING, True, 0.0)
        await start(controller)

        states[TARGET] = number_state(2.0)
        await controller._async_timer_tick(None)

        assert written(hass) == [4.0, 4.0]

    @pytest.mark.asyncio
    async def test_manual_raise_is_reverted_after_min_write_interval(self, monkeypatch):
        controller, hass, states, sched = build(monkeypatch)
        controller.on_zone_demand(LIVING, True, 0.0)
        await start(controller)

        states[TARGET] = number_state(9.0)
        await controller._async_timer_tick(None)
        assert written(hass) == [4.0]

        await sched.advance(300)
        assert written(hass) == [4.0, 4.0]


class TestStartup:
    @pytest.mark.asyncio
    async def test_no_writes_before_startup(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)

        controller.on_zone_demand(LIVING, True, 0.0)
        await sched.advance(600)

        assert written(hass) == []

    @pytest.mark.asyncio
    async def test_valves_open_at_startup_count_immediately(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch)
        controller.on_zone_demand(LIVING, True, 120.0)

        await start(controller)

        assert written(hass) == [4.0]
        assert controller.diagnostics()["pending_zones"] == {}


class TestRobustness:
    @pytest.mark.asyncio
    async def test_write_failure_does_not_raise(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch)
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("boom"))
        controller.on_zone_demand(LIVING, True, 0.0)

        await start(controller)

        assert controller.diagnostics()["last_write"] is None


class TestConstruction:
    def test_from_config_maps_every_manifold_zone_to_its_flow(self):
        manifolds = [
            {
                "name": "Ground",
                "pipe_volume": 12.0,
                "zones": [{"entity": LIVING, "flow": 4.0}, {"entity": KITCHEN, "flow": 3.5}],
            },
            {"name": "First", "pipe_volume": 8.0, "zones": [{"entity": "climate.bed", "flow": 2.0}]},
        ]

        controller = PumpFlowController.from_config(MagicMock(), {"target_entity": TARGET}, manifolds)

        assert controller._zone_flows == {LIVING: 4.0, KITCHEN: 3.5, "climate.bed": 2.0}


class TestTimers:
    def test_start_registers_a_started_listener_and_an_interval(self, monkeypatch):
        controller, hass, _states, _sched = build(monkeypatch)
        tracked = {}

        def fake_interval(_hass, action, interval):
            tracked["interval"] = interval
            return lambda: None

        monkeypatch.setattr(pump_module, "async_track_time_interval", fake_interval)
        hass.bus.async_listen_once = MagicMock(return_value=lambda: None)

        controller.async_start()

        assert tracked["interval"] == timedelta(seconds=300)
        hass.bus.async_listen_once.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_cancels_every_handle(self, monkeypatch):
        controller, hass, _states, sched = build(monkeypatch)
        cancelled = []
        monkeypatch.setattr(
            pump_module, "async_track_time_interval", lambda *_args: lambda: cancelled.append("interval")
        )
        hass.bus.async_listen_once = MagicMock(return_value=lambda: cancelled.append("once"))
        controller.async_start()
        await start(controller)
        controller.on_zone_demand(LIVING, True, 120.0)

        controller.async_cleanup()

        assert sorted(cancelled) == ["interval", "once"]
        assert sched.active() == []
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_pump_flow_controller.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement** — `managers/pump_flow_controller.py`

```python
"""Requests the summed design flow of open, fully actuated zones from one system pump."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_MANIFOLD_ZONE_ENTITY,
    CONF_MANIFOLD_ZONE_FLOW,
    CONF_PUMP_FLOW_MIN_FLOW,
    CONF_PUMP_FLOW_MIN_WRITE_INTERVAL,
    CONF_PUMP_FLOW_TARGET_ENTITY,
    DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL,
    PUMP_FLOW_BINDING_ENTITY_LIMIT,
    PUMP_FLOW_DEBOUNCE_SECONDS,
    PUMP_FLOW_STARTUP_DELAY_SECONDS,
    PUMP_FLOW_UPDATE_INTERVAL_SECONDS,
    PUMP_FLOW_WARN_INTERVAL_SECONDS,
)
from .heater_service_caller import HeaterServiceCaller
from .pump_flow_math import FLOW_EPSILON, decide_write, flow_limits, parse_current, round_up, target_flow

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _cancel(unsub: CALLBACK_TYPE | None) -> None:
    if unsub is not None:
        unsub()


class PumpFlowController:
    """Writes the summed flow of open, fully actuated zones to one pump setpoint entity.

    A zone's flow joins the sum ``valve_actuation_time`` seconds after its valve
    opens and leaves it the moment the valve closes. Increases and the drop to
    idle are written after a short coalescing window; other decreases must
    persist for ``min_write_interval`` first.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        zone_flows: dict[str, float],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hass = hass
        self._target_entity: str = config[CONF_PUMP_FLOW_TARGET_ENTITY]
        min_flow = config.get(CONF_PUMP_FLOW_MIN_FLOW)
        self._min_flow: float | None = None if min_flow is None else float(min_flow)
        self._min_write_interval = float(
            config.get(CONF_PUMP_FLOW_MIN_WRITE_INTERVAL, DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL)
        )
        self._zone_flows = dict(zone_flows)
        self._monotonic = monotonic

        self._counts_from: dict[str, float] = {}
        self._started = False
        self._decrease_pending_since: float | None = None
        self._requested: float | None = None
        self._raw_sum = 0.0
        self._binding: str | None = None
        self._last_write_at: datetime | None = None
        self._last_error_at: float | None = None
        self._last_limit_warn_at: float | None = None

        self._started_unsub: CALLBACK_TYPE | None = None
        self._startup_unsub: CALLBACK_TYPE | None = None
        self._interval_unsub: CALLBACK_TYPE | None = None
        self._window_unsub: CALLBACK_TYPE | None = None
        self._maturity_unsub: CALLBACK_TYPE | None = None
        self._decrease_unsub: CALLBACK_TYPE | None = None

    @classmethod
    def from_config(
        cls,
        hass: HomeAssistant,
        config: dict[str, Any],
        manifolds_config: list[dict[str, Any]],
    ) -> PumpFlowController:
        """Build the controller from validated ``pump_flow_control`` and ``manifolds`` config."""
        zone_flows = {
            zone[CONF_MANIFOLD_ZONE_ENTITY]: float(zone[CONF_MANIFOLD_ZONE_FLOW])
            for manifold in manifolds_config
            for zone in manifold["zones"]
        }
        return cls(hass, config, zone_flows)

    # ── demand intake ────────────────────────────────────────────────────────

    def on_zone_demand(self, zone_entity_id: str, is_open: bool, valve_actuation_time: float) -> None:
        """Record a zone valve opening or closing.

        Re-reporting an already open valve keeps its original timing, so an HVAC
        mode change while the valve stays open does not restart the wait.
        """
        if zone_entity_id not in self._zone_flows:
            return
        if is_open:
            if zone_entity_id in self._counts_from:
                return
            self._counts_from[zone_entity_id] = self._monotonic() + max(0.0, valve_actuation_time)
        elif self._counts_from.pop(zone_entity_id, None) is None:
            return
        self._schedule_maturity()
        self._request_evaluation()

    # ── scheduling ───────────────────────────────────────────────────────────

    def _schedule_maturity(self) -> None:
        _cancel(self._maturity_unsub)
        self._maturity_unsub = None
        now = self._monotonic()
        pending = [counts_from for counts_from in self._counts_from.values() if counts_from > now]
        if pending:
            self._maturity_unsub = async_call_later(self.hass, min(pending) - now, self._async_on_maturity)

    def _request_evaluation(self) -> None:
        if not self._started or self._window_unsub is not None:
            return
        self._window_unsub = async_call_later(self.hass, PUMP_FLOW_DEBOUNCE_SECONDS, self._async_on_window)

    async def _async_on_maturity(self, _now: datetime | None) -> None:
        self._maturity_unsub = None
        self._schedule_maturity()
        self._request_evaluation()

    async def _async_on_window(self, _now: datetime | None) -> None:
        self._window_unsub = None
        await self._async_run()

    async def _async_on_decrease_due(self, _now: datetime | None) -> None:
        self._decrease_unsub = None
        await self._async_run()

    # ── evaluation ───────────────────────────────────────────────────────────

    async def _async_run(self) -> None:
        try:
            await self._async_evaluate()
        except Exception:  # broad: one bad cycle must not kill the timers
            _LOGGER.exception("Pump flow control: evaluation failed")

    async def _async_evaluate(self) -> None:
        if not self._started:
            return
        now = self._monotonic()
        counted = [self._zone_flows[zone] for zone, counts_from in self._counts_from.items() if counts_from <= now]
        self._raw_sum = sum(counted)
        target = target_flow(counted, self._min_flow)

        state = self.hass.states.get(self._target_entity)
        if state is None:
            _LOGGER.debug("Pump flow control: %s has no state yet, skipping write", self._target_entity)
            return

        minimum, maximum, step = flow_limits(state)
        rounded = round_up(target, step)
        final = min(max(rounded, minimum), maximum)
        self._requested = final
        self._binding = None
        if abs(final - rounded) >= FLOW_EPSILON:
            self._binding = PUMP_FLOW_BINDING_ENTITY_LIMIT
            self._warn_entity_limited(rounded, final, now)

        decision = decide_write(
            final,
            parse_current(state),
            idle=not counted,
            pending_since=self._decrease_pending_since,
            now=now,
            min_write_interval=self._min_write_interval,
        )
        self._decrease_pending_since = decision.decrease_pending_since
        _cancel(self._decrease_unsub)
        self._decrease_unsub = None
        if decision.recheck_in is not None:
            self._decrease_unsub = async_call_later(self.hass, decision.recheck_in, self._async_on_decrease_due)
        if decision.write:
            await self._async_write(final, now)

    async def _async_write(self, value: float, now: float) -> None:
        domain = HeaterServiceCaller.get_number_entity_domain(self._target_entity)
        try:
            await self.hass.services.async_call(
                domain,
                "set_value",
                {"entity_id": self._target_entity, "value": value},
                blocking=False,
            )
        except ServiceNotFound as err:
            self._log_write_error(now, "service '%s.set_value' not found: %s", domain, err)
            return
        except HomeAssistantError as err:
            self._log_write_error(now, "Home Assistant error: %s", err)
            return
        except Exception as err:  # broad: a failed write must not kill the timers
            self._log_write_error(now, "unexpected error: %s", err)
            return
        self._last_error_at = None
        self._last_write_at = dt_util.utcnow()
        _LOGGER.debug("Pump flow control: wrote %.2f L/min to %s", value, self._target_entity)

    def _log_write_error(self, now: float, message: str, *args: Any) -> None:
        if self._last_error_at is not None and now - self._last_error_at < PUMP_FLOW_WARN_INTERVAL_SECONDS:
            return
        self._last_error_at = now
        _LOGGER.error("Pump flow control: failed to write %s — " + message, self._target_entity, *args)

    def _warn_entity_limited(self, desired: float, actual: float, now: float) -> None:
        last = self._last_limit_warn_at
        if last is not None and now - last < PUMP_FLOW_WARN_INTERVAL_SECONDS:
            return
        self._last_limit_warn_at = now
        _LOGGER.warning(
            "Pump flow control: %s cannot take %.2f L/min (entity limit clamps to %.2f L/min)",
            self._target_entity,
            desired,
            actual,
        )

    # ── diagnostics ──────────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        """Return the current request and the state behind it, for the diagnostic sensor."""
        now = self._monotonic()
        pending_since: str | None = None
        if self._decrease_pending_since is not None:
            waited = timedelta(seconds=now - self._decrease_pending_since)
            pending_since = (dt_util.utcnow() - waited).isoformat()
        return {
            "requested": self._requested,
            "open_zones": sorted(zone for zone, counts_from in self._counts_from.items() if counts_from <= now),
            "pending_zones": {
                zone: round(counts_from - now, 1)
                for zone, counts_from in sorted(self._counts_from.items())
                if counts_from > now
            },
            "raw_sum": round(self._raw_sum, 3),
            "min_flow_applied": self._min_flow is not None and 0 < self._raw_sum < self._min_flow,
            "decrease_pending_since": pending_since,
            "binding_constraint": self._binding,
            "last_write": None if self._last_write_at is None else self._last_write_at.isoformat(),
        }

    # ── lifecycle ────────────────────────────────────────────────────────────

    def async_start(self) -> None:
        """Register the startup evaluation and the periodic re-evaluation (sync, for ``coordinator.__init__``)."""
        self._started_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._async_on_ha_started)
        self._interval_unsub = async_track_time_interval(
            self.hass,
            self._async_timer_tick,
            timedelta(seconds=PUMP_FLOW_UPDATE_INTERVAL_SECONDS),
        )

    def async_cleanup(self) -> None:
        """Cancel every listener and timer. Called from coordinator cleanup."""
        for unsub in (
            self._started_unsub,
            self._startup_unsub,
            self._interval_unsub,
            self._window_unsub,
            self._maturity_unsub,
            self._decrease_unsub,
        ):
            _cancel(unsub)
        self._started_unsub = None
        self._startup_unsub = None
        self._interval_unsub = None
        self._window_unsub = None
        self._maturity_unsub = None
        self._decrease_unsub = None

    async def _async_on_ha_started(self, _event: Any) -> None:
        self._started_unsub = None
        self._startup_unsub = async_call_later(self.hass, PUMP_FLOW_STARTUP_DELAY_SECONDS, self._async_startup)

    async def _async_startup(self, _now: datetime | None) -> None:
        """Start writing. Valves reported open by now were open before the restart, so they count at once."""
        self._startup_unsub = None
        now = self._monotonic()
        self._counts_from = {zone: min(counts_from, now) for zone, counts_from in self._counts_from.items()}
        self._schedule_maturity()
        self._started = True
        await self._async_run()

    async def _async_timer_tick(self, _now: datetime | None) -> None:
        await self._async_run()
```

- [ ] **Step 4: Run tests** — `pytest tests/test_pump_flow_controller.py tests/test_pump_flow_math.py -v` → PASS. ruff check/format; `pyright custom_components/adaptive_climate/managers/pump_flow_controller.py` → clean (fix HA-boundary complaints with `# type: ignore[<code>]` + reason, never bare).

- [ ] **Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/pump_flow_controller.py tests/test_pump_flow_controller.py
git commit -m "feat(pump): add pump flow controller"
```

---

### Task 5: Wire into coordinator, zone data and domain schema

**Files:**
- Modify: `custom_components/adaptive_climate/coordinator.py`, `custom_components/adaptive_climate/climate_setup.py` (`zone_data` dict in `async_setup_platform`), `custom_components/adaptive_climate/__init__.py`
- Test: `tests/test_coordinator.py`, `tests/test_central_controller.py` (preamble), `tests/test_pump_flow_wiring.py`

**Interfaces:**
- Consumes: `PumpFlowController.from_config`, `.on_zone_demand`, `.async_start`, `.async_cleanup` (Task 4); `PUMP_FLOW_CONTROL_SCHEMA` (Task 1); `CONF_PUMP_FLOW_CONTROL`, `CONF_MANIFOLDS`.
- Produces: `coordinator.pump_flow_controller -> PumpFlowController | None`; `zone_data["valve_actuation_time"]: float` (seconds).

- [ ] **Step 1: Test preambles** — in `tests/test_coordinator.py` and `tests/test_central_controller.py`, directly under `sys.modules["managers.water_temp_controller"] = Mock()` add:

```python
sys.modules["managers.pump_flow_controller"] = Mock()
```

- [ ] **Step 2: Write the failing tests** — append to `tests/test_coordinator.py`:

```python
class TestPumpFlowWiring:
    @staticmethod
    def coord_with_pump(hass):
        coord = coordinator.AdaptiveThermostatCoordinator(hass)
        coord._pump_flow_controller = MagicMock()
        return coord

    def test_demand_change_notifies_pump_with_entity_and_actuation_time(self, hass):
        coord = self.coord_with_pump(hass)
        coord.register_zone("living", {"climate_entity_id": "climate.living", "valve_actuation_time": 120.0})

        coord.update_zone_demand("living", True, "heat")

        coord._pump_flow_controller.on_zone_demand.assert_called_once_with("climate.living", True, 120.0)

    def test_unchanged_demand_does_not_notify_again(self, hass):
        coord = self.coord_with_pump(hass)
        coord.register_zone("living", {"climate_entity_id": "climate.living", "valve_actuation_time": 120.0})

        coord.update_zone_demand("living", True, "heat")
        coord.update_zone_demand("living", True, "heat")

        assert coord._pump_flow_controller.on_zone_demand.call_count == 1

    def test_missing_zone_data_falls_back_to_slug_entity_and_zero_actuation(self, hass):
        coord = self.coord_with_pump(hass)
        coord.register_zone("garage", {"name": "Garage"})

        coord.update_zone_demand("garage", True, "heat")

        coord._pump_flow_controller.on_zone_demand.assert_called_once_with("climate.garage", True, 0.0)

    def test_unregister_reports_the_zone_closed(self, hass):
        coord = self.coord_with_pump(hass)
        coord.register_zone("living", {"climate_entity_id": "climate.living", "valve_actuation_time": 120.0})
        coord.update_zone_demand("living", True, "heat")
        coord._pump_flow_controller.reset_mock()

        coord.unregister_zone("living")

        coord._pump_flow_controller.on_zone_demand.assert_called_once_with("climate.living", False, 0.0)

    @pytest.mark.asyncio
    async def test_cleanup_releases_the_pump_controller(self, hass):
        coord = self.coord_with_pump(hass)
        pump = coord._pump_flow_controller

        await coord.async_cleanup()

        pump.async_cleanup.assert_called_once()
        assert coord.pump_flow_controller is None

    def test_setup_builds_the_controller_from_config(self, hass, monkeypatch):
        fake = MagicMock()
        monkeypatch.setattr(coordinator, "PumpFlowController", fake)
        manifolds = [{"name": "Ground", "pipe_volume": 12.0, "zones": [{"entity": "climate.living", "flow": 4.0}]}]
        pump_config = {"target_entity": "number.pump_flow", "min_write_interval": 300}

        coord = coordinator.AdaptiveThermostatCoordinator(
            hass, {"manifolds": manifolds, "pump_flow_control": pump_config}
        )

        fake.from_config.assert_called_once_with(hass, pump_config, manifolds)
        fake.from_config.return_value.async_start.assert_called_once()
        assert coord.pump_flow_controller is fake.from_config.return_value

    def test_no_controller_without_config(self, hass):
        assert coordinator.AdaptiveThermostatCoordinator(hass).pump_flow_controller is None
```

  Create `tests/test_pump_flow_wiring.py`:

```python
"""Source guards for pump flow wiring that the mocked HA test harness can't exercise."""

from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "custom_components" / "adaptive_climate"


def test_zone_data_carries_valve_actuation_time():
    source = (PACKAGE / "climate_setup.py").read_text()
    assert '"valve_actuation_time": valve_actuation_seconds,' in source


def test_domain_schema_accepts_pump_flow_control():
    source = (PACKAGE / "__init__.py").read_text()
    assert "vol.Optional(CONF_PUMP_FLOW_CONTROL): PUMP_FLOW_CONTROL_SCHEMA" in source
```

- [ ] **Step 3: Run to verify failure** — `pytest "tests/test_coordinator.py::TestPumpFlowWiring" tests/test_pump_flow_wiring.py -v` → FAIL (`pump_flow_controller` attribute / source guards).

- [ ] **Step 4: Coordinator**
  - Imports — try branch: add `CONF_MANIFOLDS,` and `CONF_PUMP_FLOW_CONTROL,` to the `.const` import and `from .managers.pump_flow_controller import PumpFlowController`; except branch: same names in `from const import (...)` and `from managers.pump_flow_controller import PumpFlowController  # type: ignore[no-redef]`.
  - `__init__`, right after `self._setup_water_temp_control()`:
    ```python
        # Pump flow control (if configured)
        self._pump_flow_controller: PumpFlowController | None = None
        self._setup_pump_flow_control()
    ```
  - Next to `_setup_water_temp_control` / `water_temp_controller`:
    ```python
    def _setup_pump_flow_control(self) -> None:
        """Construct the pump flow controller when configured."""
        pump_config = self._config.get(CONF_PUMP_FLOW_CONTROL)
        if not pump_config:
            return
        self._pump_flow_controller = PumpFlowController.from_config(
            self.hass, pump_config, self._config.get(CONF_MANIFOLDS) or []
        )
        self._pump_flow_controller.async_start()
        _LOGGER.info("Pump flow control enabled")

    @property
    def pump_flow_controller(self) -> PumpFlowController | None:
        """Return the pump flow controller, or None if not configured."""
        return self._pump_flow_controller
    ```
  - `update_zone_demand`, inside `if old_state != new_state:` directly after the `_LOGGER.info("Demand changed ...")` call:
    ```python
                if self._pump_flow_controller is not None:
                    zone_data = self._zones.get(zone_id, {})
                    self._pump_flow_controller.on_zone_demand(
                        zone_data.get("climate_entity_id", f"climate.{zone_id}"),
                        has_demand,
                        float(zone_data.get("valve_actuation_time", 0.0)),
                    )
    ```
  - `unregister_zone`: before `del self._zones[zone_id]` add `zone_entity_id = self._zones[zone_id].get("climate_entity_id", f"climate.{zone_id}")`; after the `_demand_states` deletion add:
    ```python
        if self._pump_flow_controller is not None:
            self._pump_flow_controller.on_zone_demand(zone_entity_id, False, 0.0)
    ```
  - `async_cleanup`, after the water temp block:
    ```python
        if self._pump_flow_controller is not None:
            self._pump_flow_controller.async_cleanup()
            self._pump_flow_controller = None
            _LOGGER.debug("Cleaned up pump flow controller")
    ```

- [ ] **Step 5: `climate_setup.py`** — in the `zone_data = {...}` literal add after `"pwm_seconds": ...,`:
  ```python
            "valve_actuation_time": valve_actuation_seconds,
  ```

- [ ] **Step 6: `__init__.py`**
  - `.const` import: add `CONF_PUMP_FLOW_CONTROL`.
  - Extend the Task 2 import: `from .manifold_config import MANIFOLD_SCHEMA, PUMP_FLOW_CONTROL_SCHEMA, validate_manifolds_and_pump_flow`.
  - Domain schema, after `vol.Optional(CONF_WATER_TEMP_CONTROL): WATER_TEMP_CONTROL_SCHEMA,`:
    ```python
                        # System pump flow setpoint from open zones' manifold flows
                        vol.Optional(CONF_PUMP_FLOW_CONTROL): PUMP_FLOW_CONTROL_SCHEMA,
    ```

- [ ] **Step 7: Run** — full `pytest` → PASS; `ruff check custom_components/ tests/ && ruff format custom_components/ tests/ && pyright custom_components/adaptive_climate/` → clean.

- [ ] **Step 8: Commit**

```bash
git add custom_components/adaptive_climate/coordinator.py custom_components/adaptive_climate/climate_setup.py \
  custom_components/adaptive_climate/__init__.py tests/test_coordinator.py tests/test_central_controller.py \
  tests/test_pump_flow_wiring.py
git commit -m "feat(pump): wire pump flow control into coordinator and config"
```

---

### Task 6: Diagnostic sensor

**Files:**
- Create: `custom_components/adaptive_climate/sensors/pump_flow.py`
- Modify: `custom_components/adaptive_climate/sensor.py` (import next to `WaterTempSupplySensor`; creation right after the water temp sensor block inside the `system_sensors_created` guard)
- Test: `tests/test_pump_flow_sensor.py`

**Interfaces:**
- Consumes: `coordinator.pump_flow_controller`, `PumpFlowController.diagnostics()` (Task 4/5).
- Produces: `PumpFlowTargetSensor(hass)`, unique_id `pump_flow_target`, name `Pump Flow Target`.

- [ ] **Step 1: Write the failing tests** — `tests/test_pump_flow_sensor.py`

```python
"""Tests for the system-wide pump flow diagnostic sensor."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# conftest.py doesn't register these two modules; inject before importing the sensor.
if "homeassistant.components.sensor" not in sys.modules:
    _mock_sensor_mod = MagicMock()
    _mock_sensor_mod.SensorEntity = type("SensorEntity", (), {})
    _mock_sensor_mod.SensorDeviceClass = MagicMock()
    _mock_sensor_mod.SensorStateClass = MagicMock()
    sys.modules["homeassistant.components.sensor"] = _mock_sensor_mod

if "homeassistant.helpers.entity" not in sys.modules:
    _mock_entity_mod = MagicMock()
    _mock_entity_mod.EntityCategory = MagicMock()
    sys.modules["homeassistant.helpers.entity"] = _mock_entity_mod

from custom_components.adaptive_climate.sensors.pump_flow import PumpFlowTargetSensor

DIAGNOSTICS = {
    "requested": 7.5,
    "open_zones": ["climate.kitchen", "climate.living"],
    "pending_zones": {"climate.hall": 42.0},
    "raw_sum": 7.5,
    "min_flow_applied": False,
    "decrease_pending_since": None,
    "binding_constraint": None,
    "last_write": "2026-09-27T10:00:00+00:00",
}


def make_hass(controller=None):
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.pump_flow_controller = controller
    hass.data = {"adaptive_climate": {"coordinator": coordinator}}
    return hass


def make_controller(diagnostics=None):
    controller = MagicMock()
    controller.diagnostics = MagicMock(return_value=diagnostics or DIAGNOSTICS)
    return controller


@pytest.mark.asyncio
async def test_state_is_the_requested_flow():
    sensor = PumpFlowTargetSensor(make_hass(make_controller()))

    await sensor.async_update()

    assert sensor.native_value == pytest.approx(7.5)
    assert sensor.available is True


@pytest.mark.asyncio
async def test_attributes_expose_the_diagnostic_breakdown():
    sensor = PumpFlowTargetSensor(make_hass(make_controller()))

    await sensor.async_update()

    assert sensor.extra_state_attributes == {key: value for key, value in DIAGNOSTICS.items() if key != "requested"}


@pytest.mark.asyncio
async def test_sensor_is_unavailable_without_a_controller():
    sensor = PumpFlowTargetSensor(make_hass(None))

    await sensor.async_update()

    assert sensor.native_value is None
    assert sensor.available is False


@pytest.mark.asyncio
async def test_a_failing_diagnostics_call_does_not_raise():
    controller = MagicMock()
    controller.diagnostics = MagicMock(side_effect=RuntimeError("boom"))
    sensor = PumpFlowTargetSensor(make_hass(controller))

    await sensor.async_update()

    assert sensor.available is False


def test_sensor_identity_is_stable():
    sensor = PumpFlowTargetSensor(make_hass(make_controller()))

    assert sensor.unique_id == "pump_flow_target"
    assert sensor.name == "Pump Flow Target"


def test_setup_creates_the_sensor_only_when_pump_control_is_configured():
    import inspect

    from custom_components.adaptive_climate import sensor as sensor_module

    source = inspect.getsource(sensor_module)
    assert "PumpFlowTargetSensor(hass)" in source
    assert 'getattr(coordinator, "pump_flow_controller", None) is not None' in source
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_pump_flow_sensor.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement** — `sensors/pump_flow.py`

```python
"""System-wide diagnostic sensor for pump flow control."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_ATTRIBUTE_KEYS = (
    "open_zones",
    "pending_zones",
    "raw_sum",
    "min_flow_applied",
    "decrease_pending_since",
    "binding_constraint",
    "last_write",
)


class PumpFlowTargetSensor(SensorEntity):
    """Reports the flow currently requested from the system pump."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the sensor.

        Args:
            hass: Home Assistant instance.
        """
        # Deferred for the same reason as WaterTempSupplySensor: keeps sensor.py importable in tests.
        from homeassistant.helpers.entity import EntityCategory

        self.hass = hass
        self._attr_name = "Pump Flow Target"
        self._attr_unique_id = "pump_flow_target"
        self._attr_icon = "mdi:pump"
        self._attr_should_poll = False
        self._attr_native_unit_of_measurement = UnitOfVolumeFlowRate.LITERS_PER_MINUTE
        self._attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_available = False
        self._state: float | None = None
        self._attributes: dict[str, Any] = {}

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this entity."""
        return self._attr_unique_id

    @property
    def name(self) -> str:
        """Return the name of this entity."""
        return self._attr_name

    @property
    def _controller(self) -> Any | None:
        from ..const import DOMAIN

        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator is None:
            return None
        return getattr(coordinator, "pump_flow_controller", None)

    @property
    def native_value(self) -> float | None:
        """Return the requested flow in L/min."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the diagnostic breakdown."""
        return self._attributes

    @property
    def available(self) -> bool:
        """Return whether the sensor currently has a usable reading."""
        return self._attr_available

    def _set_unavailable(self) -> None:
        self._state = None
        self._attributes = {}
        self._attr_available = False

    async def async_update(self) -> None:
        """Refresh state from the controller's diagnostics payload."""
        controller = self._controller
        if controller is None:
            self._set_unavailable()
            return
        try:
            diagnostics = controller.diagnostics()
        except Exception:  # broad: one bad sensor must not block the others
            _LOGGER.exception("Pump flow diagnostics failed")
            self._set_unavailable()
            return
        self._state = diagnostics.get("requested")
        self._attributes = {key: diagnostics.get(key) for key in _ATTRIBUTE_KEYS}
        self._attr_available = True
```

  `sensor.py`: add `from .sensors.pump_flow import PumpFlowTargetSensor` next to the `WaterTempSupplySensor` import; right after the water temp sensor creation block add:

```python
        # Create the pump flow diagnostic sensor when pump flow control is configured
        if coordinator is not None and getattr(coordinator, "pump_flow_controller", None) is not None:
            sensors.append(PumpFlowTargetSensor(hass))
            _LOGGER.info("PumpFlowTargetSensor created")
```

- [ ] **Step 4: Run** — `pytest tests/test_pump_flow_sensor.py tests/test_water_temp_sensor.py -v` → PASS; ruff; `pyright custom_components/adaptive_climate/sensors/pump_flow.py custom_components/adaptive_climate/sensor.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add custom_components/adaptive_climate/sensors/pump_flow.py custom_components/adaptive_climate/sensor.py tests/test_pump_flow_sensor.py
git commit -m "feat(pump): add pump flow target diagnostic sensor"
```

---

### Task 7: Docs

**Files:**
- Create: `docs/subsystems/pump-flow-control.md`
- Modify (untracked user WIP — edit, do **not** stage unless Unresolved Q1 says so): `docs/subsystems/multi-zone.md`, `docs/subsystems/valve-actuation-time.md`, `docs/architecture/modules.md`

- [ ] **Step 1: Create `docs/subsystems/pump-flow-control.md`**

````markdown
# Pump Flow Control

Requests flow (L/min) from one system pump: the summed design flow of zones whose valves are open and fully actuated.

**Configuration (domain-level):**
```yaml
adaptive_climate:
  manifolds:
    - name: Ground floor
      pipe_volume: 12
      zones:
        - entity: climate.living
          flow: 4.0
        - entity: climate.kitchen
          flow: 3.5
  pump_flow_control:
    target_entity: number.heatpump_flow_setpoint   # number.* or input_number.*
    min_flow: 12.0            # optional; floor for any non-zero sum (e.g. heat pump minimum flow)
    min_write_interval: 300   # optional; seconds a decrease must persist before it is written
```
Requires `manifolds`. A zone belongs to one manifold only. Zones not on a manifold never contribute.

**Zone on:** the zone's valve is open (same signal as the central controller), heat or cool. Proportional (pwm = 0) valves count their full flow above 0 %.

**Timing:**
- Valve opens → its flow joins the sum after the zone's `valve_actuation_time` (entity config or heating-type default). Re-reporting an open valve (e.g. HEAT→COOL) keeps the original timing.
- Valve closes → flow leaves the sum immediately.
- Changes coalesce in a 30 s window (starts at the first change, never extended).

**Target:** no counted zones → 0; else `max(sum, min_flow)`. Rounded up to the entity `step`, clamped to its min/max (clamp → rate-limited warning, `binding_constraint: entity_limit`). An entity min above 0 means idle writes that minimum.

**Write policy** (against the entity's current value): increase → write; drop to idle → write; equal → nothing; other decreases → written once they persist `min_write_interval`, dropped if the target recovers. Non-numeric entity state (`unknown`, `unavailable`) → write. No state → skip, retried every 5 min. Write errors log at most once per hour.

**Lifecycle:** writes start 30 s after HA start; valves open at that moment count immediately. Re-evaluates every 5 min, re-asserting after a pump reset or manual change (a manual raise counts as a decrease and is reverted after `min_write_interval`).

**Sensor:** `sensor.pump_flow_target` (diagnostic, L/min, refreshed every 5 min). Attributes: `open_zones`, `pending_zones` (seconds until counted), `raw_sum`, `min_flow_applied`, `decrease_pending_since`, `binding_constraint`, `last_write`.

**Code:** `managers/pump_flow_controller.py` (timers, writes), `managers/pump_flow_math.py` (pure target / rounding / write decision), `manifold_config.py` (validators), `sensors/pump_flow.py`.
````

- [ ] **Step 2: Update WIP docs** (edit in place, leave unstaged)
  - `docs/subsystems/multi-zone.md` — replace the `- **Manifolds:** ...` line with:
    ```markdown
    - **Manifolds:** Zones listed as `{entity, flow}` (L/min). Transport delay = `pipe_volume / Σ flow of heat-demand zones on the manifold`; preheat worst case = `pipe_volume / zone flow`. Feeds [pump flow control](pump-flow-control.md).
    - **Migration:** entity `loops`, manifold `flow_per_loop` and plain-string zones were removed and fail validation — use `zones: [{entity: climate.x, flow: <L/min>}]`.
    ```
  - `docs/subsystems/valve-actuation-time.md` — under **Timing behavior:** append `- Pump flow control: a zone's flow joins the requested pump flow only after this time`.
  - `docs/architecture/modules.md` — Core Modules table, after the `sensors/water_temp.py` row:
    ```markdown
    | `manifold_config.py` | Manifold / pump flow config validators (plain voluptuous) |
    | `sensors/pump_flow.py` | Pump flow target diagnostic sensor |
    ```
    Managers table, after the `water_temp_interlocks.py` row:
    ```markdown
    | `PumpFlowController` | System pump flow setpoint from open, fully actuated zones |
    | `pump_flow_math.py` | Pure pump-flow helpers: target, round-up, limits, write decision |
    ```

- [ ] **Step 3: Commit** (new file only)

```bash
git add docs/subsystems/pump-flow-control.md
git commit -m "docs(pump): document pump flow control"
```

---

### Task 8: Final verification

- [ ] **Step 1:** `pytest` → all pass (paste summary line).
- [ ] **Step 2:** `ruff check custom_components/ tests/` and `ruff format --check custom_components/ tests/` → clean.
- [ ] **Step 3:** `pyright custom_components/adaptive_climate/` → 0 errors.
- [ ] **Step 4:** `grep -rnE "flow_per_loop|FLOW_PER_LOOP|CONF_LOOPS|DEFAULT_LOOPS|zone_loops|update_zone_loops" custom_components tests` → only `manifold_config.py` / `tests/test_manifold_config.py`.
- [ ] **Step 5:** `wc -l` on the four new source files → each < 800.
- [ ] **Step 6:** `git status` → only `CLAUDE.md`, `docs/subsystems/*` WIP and `docs/architecture/modules.md` remain uncommitted.

---

## Unresolved Questions

1. `docs/subsystems/*.md` and `docs/architecture/modules.md` are untracked (your WIP from the CLAUDE.md split). Commit the Task 7 edits to them on this branch, or leave them uncommitted?
2. GitHub wiki (`afewyards/ha-adaptive-climate.wiki`): update + push after merge? It's outward-facing, so I'll ask before pushing.
3. The sensor refreshes on the shared 5-min system-sensor timer, so `pending_zones` can be up to 5 min stale. OK, or should the controller push an update after each evaluation?
