# Valve Actuation Time Design

## Overview

Add configurable valve actuation delay to account for physical valve travel time. Improves PWM timing accuracy and cycle tracking for hydronic systems.

## Configuration

Entity-level option with heating-type defaults:

```yaml
climate:
  - platform: adaptive_climate
    name: Living Room
    heating_type: floor_hydronic
    valve_actuation_time: 150  # seconds, optional override
```

### Defaults

| Heating Type | Default | Rationale |
|--------------|---------|-----------|
| floor_hydronic | 120s | Thermal actuators |
| radiator | 90s | Faster valve heads |
| convector | 0s | Direct electric |
| forced_air | 30s | Motorized dampers |

Single value for open and close (most valves symmetric).

## PWM Timing Compensation

### Timing Sequence

```
Valve OPEN cmd → [valve_time] → Valve open, demand ON
                               → [transport_delay] → Heat arrives

Valve CLOSE cmd → [valve_time/2] → Valve half-closed, demand OFF
                                  → [transport_delay] → Last heat delivered
```

### Example Timeline

15-min PWM, 50% duty (7.5 min heat), 120s valve, 90s transport:

```
00:00   Valve OPEN command
02:00   Valve open → demand ON
03:30   Hot water reaches zone
        ─── 7.5 min heat delivery ───
08:30   Valve CLOSE command
09:30   Valve half-closed → demand OFF
11:00   Last heat delivered
15:00   Next cycle
```

### Close Command Formula

```python
close_cmd = open_cmd + desired_heat_duration + (valve_time / 2)
```

### Why Half-Valve for Close

Flow diminishes progressively as valve closes. Half-closed = meaningful heat delivery cutoff.

## Heater Coordination

- Demand ON: when valve fully open (not at command)
- Demand OFF: when valve half-closed
- Multi-zone: if another zone heating, boiler already running

## Committed Heat Tracking

When transport delay exists, heat "in the pipes" must be tracked.

### State

```python
@dataclass
class HeatPipeline:
    valve_opened_at: float | None
    valve_closed_at: float | None
    transport_delay: float
```

### Calculation

```python
def committed_heat_remaining(self, now: float) -> float:
    if self.valve_opened_at is None:
        return 0.0

    if self.valve_closed_at is None:
        # Valve open - pipe filling or full
        time_open = now - self.valve_opened_at
        return min(time_open, self.transport_delay)

    # Valve closed - pipe draining
    time_since_close = now - self.valve_closed_at
    return max(0.0, self.transport_delay - time_since_close)
```

### Duty Adjustment

```python
def calculate_valve_open_duration(
    self,
    requested_duty: float,
    pwm_period: float,
    committed: float,
) -> float:
    desired_heat = requested_duty * pwm_period
    needed_heat = desired_heat - committed

    if needed_heat <= 0:
        return 0.0  # In-flight heat exceeds request

    return needed_heat + (self.valve_time / 2)
```

## Overlapping Cycles

When `valve_time + transport_delay` exceeds significant portion of PWM period, cycles overlap.

### Handling

1. **Valve already open** - skip open command, stays open
2. **Account for in-flight heat** - subtract from next cycle's duty
3. **Duty drops suddenly** - can't recall in-flight heat, may overshoot
4. **System OFF** - in-flight heat still arrives, track for metrics

### Edge Case Warning

Log warning if requested duty physically unachievable given delays.

## Learning Adjustments

### Overshoot Split

```python
def calculate_overshoot_components(
    self,
    peak_temp: float,
    setpoint: float,
    committed_at_setpoint: float,
    heating_rate: float,
) -> tuple[float, float]:
    total = peak_temp - setpoint
    committed = committed_at_setpoint * heating_rate
    controllable = total - committed
    return controllable, committed
```

Learning only adjusts PID for controllable overshoot.

### Heating Rate vs Rise Time

| Heating Type | τ | Per-Cycle Metrics | Approach |
|--------------|---|-------------------|----------|
| floor_hydronic | 30-60 min | Heat delivered only | Rolling window, physics model |
| radiator | 10-20 min | Limited | Rolling window |
| convector | 3-5 min | Yes | Traditional rise time |
| forced_air | 1-2 min | Yes | Traditional rise time |

### Physics Model for Slow Systems

Floor heating: 50+ min thermal time constant. Per-cycle learning doesn't work.

**Approach:**
1. Rolling window correlation (2-3× τ)
2. Track cumulative heat delivered vs temp change
3. Calibrate model parameters (τ, heat capacity), not PID per-cycle
4. Use preheat learner bins for scheduling

```python
heating_rate = sum(temp_changes[-window:]) / sum(heat_delivered[-window:])
```

**Rationale:** Professional BMS systems use models for slow systems. Feedforward more important than feedback. PID gains from physics, minimal per-cycle adjustment.

## Sensor Considerations

Air sensor only (current system). Full response chain:

```
Valve → Manifold → Slab → Floor surface → Room air → Sensor
```

Total lag 60-90+ min for floor heating. Reinforces physics-first approach.

**Future enhancement:** Optional `floor_sensor` for safety limits (18°C min, 29°C max per EN 1264).

## Implementation Notes

### New Config Option

Add to entity schema in `climate_setup.py`:
- `valve_actuation_time`: optional, seconds
- Default from `HEATING_TYPE_VALVE_DEFAULTS` dict

### Affected Modules

- `managers/pwm_controller.py` - timing compensation
- `managers/cycle_tracker_manager.py` - tracking start/stop adjustment
- `coordinator.py` - demand signaling timing
- `adaptive/learning.py` - overshoot split, heating rate metric
- `adaptive/physics.py` - rolling window heating rate for slow systems

### New Module

`managers/heat_pipeline.py` - committed heat tracking
