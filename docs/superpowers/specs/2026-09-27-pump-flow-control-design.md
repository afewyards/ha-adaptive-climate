# Pump Flow Control — Design

## Goal

Drive one system pump's requested flow (L/min) from the zones whose valves are
actually open. Each manifold declares per-zone flow; the pump setpoint is the
sum of open zones' flows, applied only after each zone's valve has finished
opening.

## Decisions

| Topic | Decision |
|-------|----------|
| Pump topology | One system pump, one `number`/`input_number` target entity |
| Zone flow source | Explicit `{entity, flow}` per manifold zone, required |
| Old model | `loops` + `flow_per_loop` removed; `flow` is the single source (pump + transport delay) |
| "Zone on" | Valve open (`_is_device_active`), heat or cool; proportional valves count full flow when >0% |
| Valve timing | Zone flow counts only after its `valve_actuation_time` since open; removed immediately on close |
| Low/idle | Optional `min_flow` floors any non-zero sum; zero open zones → 0 |
| Write policy | 30 s coalescing window; increases immediate; decreases after `min_write_interval`; idle 0 immediate |
| Placement | New `PumpFlowController`, owned by coordinator |

## 1. Config

```yaml
adaptive_climate:
  manifolds:
    - name: Ground floor
      pipe_volume: 12
      zones:
        - entity: climate.living
          flow: 4.0            # L/min, required, > 0
        - entity: climate.kitchen
          flow: 3.5
  pump_flow_control:
    target_entity: number.heatpump_flow_setpoint   # required
    min_flow: 12.0             # optional, L/min floor for any non-zero sum
    min_write_interval: 300    # optional, s; decreases must persist this long
```

Validation:
- Zone entry: `entity` (climate domain) + `flow` (float > 0).
- Removed keys raise `vol.Invalid` with a migration message (not the generic
  extra-keys error): string zone entries, manifold `flow_per_loop`, entity
  `loops`. Local validators — `cv.*` is a MagicMock in tests.
- A zone may appear in at most one manifold (registry currently overwrites silently).
- `pump_flow_control` requires `manifolds`.
- `min_flow` > 0; `min_write_interval` ≥ 0.

Constants (`const.py`): `CONF_PUMP_FLOW_CONTROL`, `CONF_PUMP_FLOW_TARGET_ENTITY`,
`CONF_PUMP_FLOW_MIN_FLOW`, `CONF_PUMP_FLOW_MIN_WRITE_INTERVAL`,
`CONF_MANIFOLD_ZONE_ENTITY`, `CONF_MANIFOLD_ZONE_FLOW`,
`DEFAULT_PUMP_FLOW_MIN_WRITE_INTERVAL = 300`, `PUMP_FLOW_DEBOUNCE_SECONDS = 30`.
Delete `CONF_LOOPS`, `DEFAULT_LOOPS`, `CONF_FLOW_PER_LOOP`, `DEFAULT_FLOW_PER_LOOP`.

## 2. PumpFlowController

Files:
- `manifold_config.py` — plain-voluptuous validators (manifold zones, pump block,
  removed-key migration errors, cross checks), shared by domain + entity schemas.
- `managers/pump_flow_controller.py` — timers, demand intake, writes.
- `managers/pump_flow_math.py` — pure: target, round-up, write decision.
- `sensors/pump_flow.py` — diagnostic sensor.

Inputs:
- `coordinator.update_zone_demand()` already detects changes; on change it also
  calls `pump.on_zone_demand(climate_entity_id, open, valve_actuation_time)`
  (heat or cool; OFF mode reports demand False). Zone unregister reports closed.
- Zone flows come from the validated `manifolds` config
  (`PumpFlowController.from_config`); the registry is attached to the
  coordinator lazily by the first zone entity, too late for construction.
- `zone_data` gains `valve_actuation_time` (the value `climate_setup` already
  resolves: per-zone config or heating-type default).

Per-zone timing:
- Open → `opened_at[zone] = time.monotonic()`. Close → drop entry (flow leaves the sum at once).
- Zone counts when `monotonic() - opened_at >= valve_actuation_time`.
- One `async_call_later` scheduled for the next pending zone to mature.

Evaluation (coalescing window: first trigger starts 30 s, later triggers join, never extend):
1. `sum` = flows of counted zones. Target = 0 if none, else `max(sum, min_flow)`.
2. Round up to entity `step`; clamp to entity min/max. Clamp changed value →
   rate-limited warning, `binding_constraint: entity_limit`.
3. Compare to the target entity's current state:
   - higher, or target 0 → write now;
   - equal → no-op, clear pending decrease;
   - lower (non-zero) → set `decrease_pending_since`, re-evaluate after
     `min_write_interval`; still lower then → write current target; recovered
     meanwhile → pending cleared.

Writes: `number.set_value` / `input_number.set_value`, domain resolved as
`WaterTempController` does (`HeaterServiceCaller.get_number_entity_domain`).
Errors logged at most once per hour. Target entity without state → skip, retry
on next tick.

Lifecycle:
- Demand is tracked from construction; no writes before start.
- Start: `EVENT_HOMEASSISTANT_STARTED` + 30 s. Every zone open at that moment is
  marked matured, whatever its recorded `opened_at` (valves restored as open
  were already open before the restart).
- 5-min interval re-evaluates against the entity's current state (reasserts
  after pump reset or manual change).
- Zone unregister → drop from tracking. Coordinator `async_cleanup` cancels all timers.

Sensor `sensor.pump_flow_target` (L/min, device class `volume_flow_rate`,
diagnostic): state = requested flow. Attributes: `open_zones`, `pending_zones`
(seconds to maturity), `raw_sum`, `min_flow_applied`, `decrease_pending_since`,
`binding_constraint`, `last_write`.

## 3. Manifold registry + transport delay

`adaptive/manifold_registry.py`:
- `Manifold(name, pipe_volume, zones: dict[str, float])`; `flow_per_loop` removed.
- New `manifolds_from_config(config) -> list[Manifold]` builder.
- `get_transport_delay(zone_id, active_zones: set[str])` =
  `pipe_volume / Σ flow of active zones on that manifold`.
- `get_worst_case_transport_delay(zone_id)` = `pipe_volume / zone flow`
  (`zone_loops` param removed).
- Cooldown (warm within 5 min → 0) unchanged.

Remove `loops`:
- `coordinator.py`: drop `_zone_loops`, `update_zone_loops()`;
  `get_transport_delay_for_zone` passes the set of heat-demand zones
  (transport delay stays heat-only, as today).
- `climate.py`: drop `update_zone_loops` call.
- `climate_init.py`: drop `zone_loops=1` argument.
- `climate_setup.py`: `CONF_LOOPS` schema entry → migration-error validator; drop `loops=`.
- `thermostat_config.py`: drop `loops` field.
- `__init__.py`: build `Manifold` from `{entity, flow}` entries.

Transport delays change to reflect configured flows instead of the 2.0 L/min/loop
default — intended.

Persistence: manifold `last_active` keyed by name, unaffected. No `STORAGE_VERSION` bump.

## 4. Docs + testing

Docs:
- New `docs/subsystems/pump-flow-control.md`.
- `docs/subsystems/multi-zone.md`: manifold line → `{entity, flow}`, flow-sum delay.
- `docs/subsystems/valve-actuation-time.md`: pump flow also waits on it.
- `docs/architecture/modules.md`: new modules.
- Migration note (string zones / `flow_per_loop` / `loops` → `{entity, flow}`).
- Mirror to GitHub wiki.

Tests (TDD):
- `test_pump_flow_math.py`: idle 0, `min_flow` floor, step round-up, entity clamp,
  write decision (increase / equal / pending decrease / recovery / idle).
- `test_pump_flow_controller.py` (fake clock + `async_call_later`): actuation
  maturity, immediate removal on close, window coalescing without extension,
  delayed decrease + recovery, immediate idle 0, stateless target skip+retry,
  startup seeding, 5-min reassert after manual change, unregister cleanup,
  timer cleanup.
- Schema: valid `{entity, flow}`; migration errors for string zones /
  `flow_per_loop` / `loops`; duplicate zone across manifolds;
  `pump_flow_control` without `manifolds`; absent `min_flow` → no floor.
- `test_manifold_registry.py`: rewrite for flow-sum delays.
- Coordinator: `update_zone_demand` notifies the pump controller only on change.
- Update existing tests using `loops` / `flow_per_loop`.
- Gate: `pytest`, `ruff check`, `ruff format`, `pyright`.

## Out of scope

- Per-manifold pumps.
- Scaling flow by proportional valve opening %.
- Persisting pump state across restarts.
