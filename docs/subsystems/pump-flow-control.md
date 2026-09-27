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
