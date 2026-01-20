# CLAUDE.md

## Project Overview

Home Assistant custom component: PID thermostat with adaptive learning, multi-zone coordination, physics-based initialization. Fork of Smart Thermostat PID.

**Capabilities:** PID control (PWM on/off or 0-100% valve), adaptive PID tuning, physics-based initialization, multi-zone coordination, energy optimization (night setback, solar gain, contact sensors).

## Development Commands

```bash
pytest                                    # all tests
pytest tests/test_pid_controller.py       # specific file
pytest --cov=custom_components/adaptive_thermostat  # with coverage
```

**Install:** Copy `custom_components/adaptive_thermostat/` to HA's `config/custom_components/`, restart HA.

## Code Style

- **Max file length:** 800 lines - extract into modules when exceeded

## Architecture

### Core Modules

| Module | Purpose |
|--------|---------|
| `climate.py` | Main entity - orchestrates managers, presets, state |
| `coordinator.py` | Zone registry, CentralController, ModeSync, coupling triggers |
| `pid_controller/__init__.py` | PID with P, I, D, E (outdoor), F (feedforward) terms |
| `adaptive/learning.py` | Cycle analysis, rule-based PID adjustments |
| `adaptive/cycle_analysis.py` | Overshoot tracking, cycle metrics |
| `adaptive/physics.py` | Thermal time constant, Ziegler-Nichols PID init |
| `sensor.py` | Performance/learning sensors |
| `services.py` | Domain/entity services |

### Managers (`managers/`)

| Manager | Purpose |
|---------|---------|
| `HeaterController` | Device control, PWM cycling, valve positioning |
| `CycleTrackerManager` | State machine (IDLE→HEATING→SETTLING), metrics |
| `TemperatureManager` | Temperature state tracking |
| `KeManager` | Outdoor compensation tuning |
| `NightSetbackManager` | Night setback scheduling |

### Adaptive Features (`adaptive/`)

| Module | Purpose |
|--------|---------|
| `thermal_coupling.py` | Inter-zone heat transfer learning |
| `night_setback.py` | Scheduled temp reduction |
| `solar_recovery.py` | Delays heating when sun will warm zone |
| `contact_sensors.py` | Pauses on window/door open |
| `heating_curves.py` | Outdoor temp compensation |

### Data Flow

```
Temp Update → TemperatureManager → PID.calc_output → HeaterController
    → PWM (on/off) or Valve (0-100%) → CycleTrackerManager
    → Coordinator.update_demand → CentralController → Main heater
```

### Configuration

**Two levels:**
1. **Domain** (`adaptive_thermostat:`): house_energy_rating, main_heater/cooler, sync_modes, presets
2. **Entity** (`climate:` platform): heater, target_sensor, heating_type, area_m2, night_setback

**Key constants** in `const.py`: `HEATING_TYPE_CHARACTERISTICS`, `PID_LIMITS`, convergence thresholds.

## Key Technical Details

### Heating Types

| Type | PID Modifier | PWM Period |
|------|--------------|------------|
| `floor_hydronic` | 0.5x | 15 min |
| `radiator` | 0.7x | 10 min |
| `convector` | 1.0x | 5 min |
| `forced_air` | 1.3x | 3 min |

### Supply Temperature Scaling

Optional `supply_temperature` config adjusts PID for non-standard supply temps (e.g., heat pump at 35°C vs reference 45°C for floor heating → 1.67x scaling).

### Floor Construction

For `floor_hydronic`: optional `floor_construction` config with layers (top_floor material, screed material, thicknesses) and pipe_spacing. Calculates `tau_modifier` for thermal mass. Materials defined in `const.py`.

### PID Adjustment Rules

In `adaptive/learning.py`. Thresholds per heating type from `get_rule_thresholds()`:
- High overshoot → reduce Kp 15%
- Moderate overshoot → reduce Kp 5%
- Slow response → increase Kp 10%
- Undershoot → increase Ki 20%
- Oscillations → reduce Kp 10%, increase Kd 20%
- Slow settling → increase Kd 15%

### Auto-Apply PID

When `auto_apply_pid: true` (default), applies PID changes at confidence thresholds. Safety limits in `const.py`: `MAX_AUTO_APPLIES_PER_SEASON=5`, `MAX_CUMULATIVE_DRIFT_PCT=50%`, `VALIDATION_CYCLE_COUNT=5`. Auto-rollback if overshoot degrades >30%.

### Cycle Tracking

State machine: IDLE → HEATING → SETTLING → IDLE

**Interruptions** (`InterruptionClassifier`):
- Major setpoint change (>0.5°C, device inactive): abort
- Minor setpoint change: continue
- Incompatible mode change: abort
- Contact sensor open >5min: abort

### Multi-Zone Coordination

- **Mode sync:** HEAT/COOL propagates; OFF independent
- **Central controller:** Aggregates demand, 30s startup delay
- **Thermal coupling:** Learns inter-zone heat transfer, applies feedforward

### Thermal Coupling

Learns heat transfer between zones via observation. Requires HA floor/area config. Config options: `enabled`, `open` (open floor plan zones), `stairwell_zones`, `seed_coefficients`. Coefficients validated over 5 cycles, rollback if overshoot +30%.

### Persistence

`LearningDataStore` in `adaptive/persistence.py`. Zone-keyed JSON (v4 format). 30s debounce on saves. Migrations: v2→v3→v4 automatic.

## Important Caveats

- **Service calls:** Use `hass.services.async_call()` - never manipulate states directly
- **PWM vs Valve:** `pwm > 0` = on/off cycling; `pwm = 0` = direct 0-100%
- **State persistence:** RestoreEntity - PID integral and gains persist
- **Ke:** Start 0.3-0.6 for well-insulated buildings

## Test Organization

| Test File | Tests |
|-----------|-------|
| `test_pid_controller.py` | PID math |
| `test_physics.py` | Thermal calculations |
| `test_learning.py` | Adaptive tuning |
| `test_cycle_tracker.py` | Cycle state machine |
| `test_integration_cycle_learning.py` | E2E cycle/learning |
| `test_coordinator.py` | Multi-zone |
| `test_central_controller.py` | Main heat source |
| `test_thermal_coupling.py` | Coupling learner |
| `test_coupling_integration.py` | E2E coupling |
| `test_night_setback.py` | Schedule/sunrise |
| `test_solar_recovery.py` | Solar delay |
| `test_contact_sensors.py` | Window/door |
