# Shared Outdoor Temperature

## Problem

All zones using the same weather entity show different `outdoor_temp_lagged` values. Each zone runs its own EMA filter with a zone-specific tau (`2 * thermal_time_constant`), causing drift — especially during rapid weather changes.

## Solution

Move outdoor temp EMA filter from per-zone PID to coordinator. Single shared value, single house-level tau.

```
Weather entity → Coordinator (EMA filter) → All zones read shared lagged value
```

### Tau Source

Derived from `house_energy_rating` using existing rating map in `physics.py`:
- A++++ = 10.0h, A+++ = 8.0h, A++ = 6.0h, A+ = 5.0h, A = 4.0h, B = 3.0h, C = 2.5h, D = 2.0h
- Default (no rating configured): 4.0h

### Why Not Per-Zone?

Per-zone sensitivity to outdoor temp is already modeled by **Ke** (outdoor compensation gain), which accounts for thermal properties, window area, glazing. Per-zone tau was double-modeling.

House-level tau answers: "how fast does the building envelope transfer outdoor conditions inward?" — a property of insulation, not individual zones.

### Persistence

None. On HA restart, initialize from current weather temp. EMA converges quickly; system is already warming up.

### Zones With `outdoor_sensor`

Unchanged. Dedicated sensors bypass the shared weather path — their raw value is passed directly to PID (no EMA lag).

## Files

| File | Change |
|------|--------|
| `coordinator.py` | Add `_outdoor_temp_lagged`, `_outdoor_temp_tau`, EMA update in weather listener |
| `pid_controller/__init__.py` | Remove EMA filter (~lines 566-576), use `ext_temp` as-is |
| `climate.py` | Remove `_outdoor_temp_lag_tau`, read lagged temp from coordinator |
| `managers/control_output.py` | Pass coordinator's lagged temp to PID |
| `managers/state_restorer.py` | Remove `outdoor_temp_lagged` restoration |
| `managers/state_attributes.py` | Read from coordinator instead of PID |
