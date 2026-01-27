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
| `climate_setup.py` | Platform schema, setup, PWM validation |
| `climate_init.py` | Manager initialization factory |
| `coordinator.py` | Zone registry, CentralController, ModeSync, thermal groups |
| `pid_controller/__init__.py` | PID with P, I, D, E (outdoor), F (feedforward) terms |
| `adaptive/learning.py` | Cycle analysis, rule-based PID adjustments |
| `adaptive/validation.py` | ValidationManager for auto-apply safety checks |
| `adaptive/confidence.py` | ConfidenceTracker for convergence confidence |
| `adaptive/learner_serialization.py` | Serialization/deserialization for AdaptiveLearner |
| `adaptive/physics.py` | Thermal time constant, Ziegler-Nichols init |
| `adaptive/floor_physics.py` | Floor thermal properties, slab calculations |
| `sensor.py` | Performance/learning sensors |

### Managers (`managers/`)

`HeaterController` (PWM/valve), `PWMController` (duty accumulation, PWM switching), `CycleTrackerManager` (IDLE→HEATING→SETTLING), `CycleMetricsRecorder` (cycle validation, metrics), `TemperatureManager`, `KeManager` (outdoor comp), `NightSetbackManager`, `NightSetbackCalculator` (preheat timing)

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

### Open Window Detection

Algorithmic detection of open windows based on rapid temperature drops (Danfoss Ally algorithm). Module: `adaptive/open_window_detection.py` (`OpenWindowDetector`).

**Configuration:**
- **Domain:** `open_window_detection:` block with `temp_drop` (°C), `detection_window` (min), `pause_duration` (min), `cooldown` (min), `action` (pause/notify)
- **Entity:** Can disable with `open_window_detection: false`
- **Precedence:** Contact sensors > entity config > domain config > defaults

**Detection algorithm:** Ring buffer tracks temps over `detection_window`. Triggers when `oldest - current >= temp_drop`.

**Integration:**
- `_async_update_temp` feeds detector
- `_async_control_heating` checks pause state
- Suppression on setpoint decrease and night setback transitions

**Pause attribute:** See consolidated `pause` attribute below.

### Humidity-Based Steam Detection

Detects shower steam in bathrooms, pauses heating during shower, resumes after stabilization. Module: `adaptive/humidity_detector.py` (`HumidityDetector`).

**Configuration (entity-level only):**
```yaml
climate:
  - platform: adaptive_thermostat
    humidity_sensor: sensor.bathroom_humidity
    humidity_spike_threshold: 15       # % rise to trigger (default 15)
    humidity_absolute_max: 80          # % absolute cap (default 80)
    humidity_detection_window: 300     # seconds for rate calc (default 300)
    humidity_stabilization_delay: 300  # seconds after drop (default 300)
```

**Detection algorithm:** Ring buffer tracks humidity over `detection_window`. Triggers when spike ≥ `humidity_spike_threshold`% OR absolute ≥ `humidity_absolute_max`%. Exit when humidity < 70% AND dropped >10% from peak.

**State machine:**
```
NORMAL ──(spike)──> PAUSED ──(drop)──> STABILIZING ──(delay)──> NORMAL
                    (off, decay)       (off)
```

**Integration:**
- `_async_control_heating` checks pause state before PID
- Integral decay: ~10%/min during pause (prevents stale buildup)
- Preheat interaction: No preheat starts while paused
- Safety: 60 min max pause duration

**Pause attribute:** See consolidated `pause` attribute below.

### Predictive Pre-Heating

Learns heating rate to start early and reach target AT scheduled time. Based on Netatmo Auto-Adapt / Tado Early Start. Module: `adaptive/preheat.py` (`PreheatLearner`).

**Configuration:**
```yaml
night_setback:
  recovery_deadline: "07:00"
  preheat_enabled: true       # Enable predictive pre-heating
  max_preheat_hours: 3.0      # Optional, defaults per heating_type
```

**Learning:**
- Bins observations by delta (0-2, 2-4, 4-6, 6+°C) and outdoor temp (cold/mild/moderate)
- Requires 3+ observations for learned rate
- Fallback: Uses `HEATING_TYPE_PREHEAT_CONFIG` rates until sufficient data

**State attributes (debug only):** `preheat_active`, `preheat_scheduled_start`, `preheat_estimated_duration_min`, `preheat_learning_confidence`, `preheat_heating_rate_learned`, `preheat_observation_count`

### State Attributes

Exposed via `extra_state_attributes`. Minimized for clarity - only restoration + critical diagnostics.

**Core attributes:**
```python
{
    # Preset temps (standard)
    "away_temp", "eco_temp", "boost_temp", "comfort_temp",
    "home_temp", "sleep_temp", "activity_temp",

    # Restoration (PID state)
    "kp", "ki", "kd", "ke", "integral",
    "pid_mode", "outdoor_temp_lagged",
    "heater_cycle_count", "cooler_cycle_count",

    # Critical diagnostics
    "control_output",        # Current PID output %
    "duty_accumulator_pct",  # PWM accumulation as % of threshold

    # Learning status
    "learning_status",           # "collecting" | "ready" | "active" | "converged"
    "cycles_collected",          # Count of complete cycles
    "convergence_confidence_pct", # 0-100%
    "pid_history",               # List of PID adjustments (when non-empty)

    # Consolidated pause (contact sensors + humidity detection)
    "pause": {
        "active": bool,          # True when heating is paused
        "reason": str | None,    # "contact" | "humidity" | None
        "resume_in": int         # Seconds until resume (optional, only when countdown active)
    }
}
```

**Priority:** Contact sensor pause > humidity detection pause (if both active, contact shown)

**Debug-only attributes** (require `debug: true` in domain config):
- `current_cycle_state` - Cycle tracker state (idle/heating/settling)
- `cycles_required_for_learning` - Minimum cycles needed (constant: 6)
- `preheat_*` - Preheat learning/scheduling details
- `humidity_detection_state`, `humidity_resume_in` - Detailed humidity state

### Persistence

`LearningDataStore` - zone-keyed JSON (v4), 30s debounce, auto-migrations.

## Caveats

- **Service calls:** Use `hass.services.async_call()` - never manipulate states directly
- **PWM vs Valve:** `pwm > 0` = on/off cycling; `pwm = 0` = direct 0-100%
- **State persistence:** RestoreEntity - PID integral and gains persist
- **HAOS sensors:** Only update on change; large `sensor_dt` normal when stable

## Tests

`test_pid_controller.py`, `test_physics.py`, `test_learning.py`, `test_cycle_tracker.py`, `test_integration_cycle_learning.py`, `test_coordinator.py`, `test_central_controller.py`, `test_thermal_groups.py`, `test_night_setback.py`, `test_contact_sensors.py`, `test_preheat_learner.py`, `test_humidity_detector.py`
