# Manifold Transport Delay Feature

## Problem
Transport delay (dead time) from heat source to zone manifold causes PID integral windup. Delay varies by active loops:
- 1 loop @ 2 L/min, 20L pipe volume → 10 min delay
- 4 loops @ 8 L/min → 2.5 min delay
- Manifold recently active → 0 delay (pipes warm)

## Config

**Zone config** (loops per zone):
```yaml
climate:
  - platform: adaptive_thermostat
    name: "2nd Bathroom"
    heating_type: floor_hydronic
    loops: 2  # floor heating loops (default: 1)
```

**Domain config** (manifold topology):
```yaml
adaptive_thermostat:
  manifolds:
    - name: "2nd Floor"
      zones: [climate.bathroom_2nd, climate.bedroom_2nd]
      pipe_volume: 20  # liters to manifold
      flow_per_loop: 2  # L/min (default)
```

## Delay Calculation
```
total_flow = sum(zone.loops × flow_per_loop for zone in active_zones_on_manifold)
delay = pipe_volume / total_flow
```
Example: bathroom (2 loops) + bedroom (3 loops) active → 5 loops × 2 L/min = 10 L/min → 20L / 10 = 2 min

## Design

### New Module: `adaptive/manifold_registry.py`
- `Manifold` dataclass: name, zones, pipe_volume, flow_per_loop
- `ManifoldRegistry`:
  - Zone-to-manifold mapping
  - Track `last_active_time` per manifold
  - `get_transport_delay(zone_id, active_zones)` → minutes
    - If manifold active < 5 min ago → 0
    - Else → `pipe_volume / (active_loops × flow_per_loop)`

### Changes to Existing Files

| File | Changes |
|------|---------|
| `const.py` | Add CONF_MANIFOLDS, CONF_PIPE_VOLUME, CONF_FLOW_PER_LOOP, CONF_LOOPS, MANIFOLD_COOLDOWN_MINUTES, DEFAULT_LOOPS=1 |
| `__init__.py` | MANIFOLD_SCHEMA, create registry in async_setup, store in hass.data |
| `climate.py` | Add `loops` config param, register loops with coordinator, query delay, pass to PID, expose as attribute |
| `coordinator.py` | Store registry ref, track zone loops, expose get_transport_delay_for_zone() |
| `pid_controller/__init__.py` | Accept transport_delay param, reduce integral to 25% during dead time |
| `managers/cycle_tracker.py` | Track dead_time separately, exclude from rise_time |
| `adaptive/cycle_analysis.py` | Add dead_time field to CycleMetrics |

### PID Dead Time Logic
```python
if self._in_dead_time:
    # Accumulate integral at 25% rate during dead time
    self._integral += self._Ki * self._error * dt_hours * 0.25
else:
    self._integral += self._Ki * self._error * dt_hours
```

Dead time state:
- Starts when: zone turns on AND manifold was cold (no activity > 5 min)
- Ends when: elapsed time > calculated transport_delay
- Reset when: heater turns off

## Tasks

```
1. TEST+IMPL: Constants in const.py (CONF_LOOPS, CONF_MANIFOLDS, etc.)
2. TEST+IMPL: Manifold dataclass + ManifoldRegistry in adaptive/manifold_registry.py
3. TEST+IMPL: Manifold config schema + setup in __init__.py
4. TEST+IMPL: Zone loops config in climate.py schema
5. TEST+IMPL: Coordinator - store registry, track zone loops, get_transport_delay_for_zone()
6. TEST+IMPL: PID dead time - param, 25% integral rate, state tracking
7. TEST+IMPL: CycleMetrics dead_time field + cycle tracker integration
8. TEST+IMPL: Climate entity - register loops, query delay, pass to PID, expose attribute
9. VERIFY: Full test suite
10. DOCS: Update CLAUDE.md
```

## Verification
1. `pytest` - all tests pass
2. Deploy to HAOS, configure manifold for 2nd floor
3. Check `transport_delay` attribute on bathroom entity
4. Observe reduced integral windup when single zone starts cold
