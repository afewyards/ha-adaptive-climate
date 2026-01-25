# CLAUDE.md

## Overview

HA custom component: PID thermostat with adaptive learning, multi-zone coordination, physics-based init. Fork of Smart Thermostat PID.

## Commands

```bash
pytest                                    # all tests
pytest tests/test_pid_controller.py       # specific file
pytest --cov=custom_components/adaptive_thermostat  # coverage
```

## Code Style

- **Max file length:** 800 lines - extract into modules when exceeded
- **Documentation:** Update GitHub wiki alongside any doc changes

## Architecture

### Core Modules

| Module | Purpose |
|--------|---------|
| `climate.py` | Main entity - orchestrates managers, presets, state |
| `coordinator.py` | Zone registry, CentralController, ModeSync, thermal groups |
| `pid_controller/__init__.py` | PID with P, I, D, E (outdoor), F (feedforward) terms |
| `adaptive/learning.py` | Cycle analysis, rule-based PID adjustments |
| `adaptive/physics.py` | Thermal time constant, Ziegler-Nichols init |
| `sensor.py` | Performance/learning sensors |

### Managers (`managers/`)

`HeaterController` (PWM/valve), `CycleTrackerManager` (IDLE→HEATING→SETTLING), `TemperatureManager`, `KeManager` (outdoor comp), `NightSetbackManager`

### Data Flow

```
Temp → TemperatureManager → PID → HeaterController → CycleTracker → Coordinator → CentralController
```

### Config Levels

1. **Domain** (`adaptive_thermostat:`): house_energy_rating, main_heater, sync_modes, presets, manifolds, thermal_groups
2. **Entity** (`climate:` platform): heater, target_sensor, heating_type, area_m2, night_setback

## Key Details

### Heating Types

| Type | PID Mod | PWM |
|------|---------|-----|
| `floor_hydronic` | 0.5x | 15m |
| `radiator` | 0.7x | 10m |
| `convector` | 1.0x | 5m |
| `forced_air` | 1.3x | 3m |

### Convergence Metrics

All must pass for steady-state: `inter_cycle_drift`, `settling_mae`, `undershoot`. Thresholds scale by heating type (floor=0.3°C → forced_air=0.15°C).

### PID Auto-Tuning (`adaptive/learning.py`)

Rules adjust Kp/Ki/Kd based on overshoot, undershoot, drift, oscillations. `auto_apply_pid: true` applies changes at confidence thresholds with safety limits and auto-rollback.

### Multi-Zone

- **Mode sync:** HEAT/COOL propagates; OFF independent
- **Central controller:** Aggregates demand, 30s startup delay
- **Thermal groups:** Leader zones + feedforward for heat transfer
- **Manifolds:** Transport delay = `pipe_volume / (active_loops × flow_per_loop)`

### Persistence

`LearningDataStore` - zone-keyed JSON (v4), 30s debounce, auto-migrations.

## Caveats

- **Service calls:** Use `hass.services.async_call()` - never manipulate states directly
- **PWM vs Valve:** `pwm > 0` = on/off cycling; `pwm = 0` = direct 0-100%
- **State persistence:** RestoreEntity - PID integral and gains persist
- **HAOS sensors:** Only update on change; large `sensor_dt` normal when stable

## Tests

`test_pid_controller.py`, `test_physics.py`, `test_learning.py`, `test_cycle_tracker.py`, `test_integration_cycle_learning.py`, `test_coordinator.py`, `test_central_controller.py`, `test_thermal_groups.py`, `test_night_setback.py`, `test_contact_sensors.py`
