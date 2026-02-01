# State Attributes Refactor

## Problem

Current state attributes are:
1. **Cluttered** - Too many attributes exposed, hard to find what matters
2. **Inconsistent** - Some nested (`status`), some flat, no clear pattern
3. **Debug boundary unclear** - Hard to know what's debug-only vs always present

## Design

Separate attributes into clear top-level groups: flat restoration fields, `status`, `learning`, and `debug`.

### Top-level Structure

```python
{
    # Restoration (flat, required for RestoreEntity)
    "integral": 5.2,
    "pid_history": [...],
    "outdoor_temp_lagged": 8.5,
    "cycle_count": 52,              # or {"heater": 42, "cooler": 10}
    "control_output": 65.3,

    # Preset temps (standard HA)
    "away_temp": 16.0,
    "eco_temp": 18.0,
    "boost_temp": 22.0,
    "comfort_temp": 21.0,
    "home_temp": 20.0,
    "sleep_temp": 18.0,
    "activity_temp": 19.0,

    # Grouped objects
    "status": {...},
    "learning": {...},
    "debug": {...}                  # only when debug: true
}
```

#### `cycle_count` field

Adapts to configuration:
- **demand_switch**: `"cycle_count": 52`
- **heater/cooler**: `"cycle_count": {"heater": 42, "cooler": 10}`

### `status` Object

Operational state with priority-ordered overrides.

```python
"status": {
    "activity": "heating",          # idle | heating | cooling | settling
    "overrides": [...]              # ordered by priority, first = in control
}
```

#### Base activities

| Activity | Description |
|----------|-------------|
| `idle` | Not heating or cooling |
| `heating` | Actively heating |
| `cooling` | Actively cooling |
| `settling` | Post-heating stabilization period |

#### Override types

Overrides modify normal behavior. Array ordered by priority (first = in control). Empty array when no overrides active.

| Type | Fields | Description |
|------|--------|-------------|
| `contact_open` | `sensors`, `since` | Contact sensor triggered |
| `open_window` | `since`, `resume_at` | Algorithmic temp drop detection |
| `humidity` | `state`, `resume_at` | Shower/steam detection. State: `paused` \| `stabilizing` |
| `night_setback` | `delta`, `ends_at`, `limited_to` | Reduced setpoint period. `limited_to` optional (when learning gate constrains) |
| `preheating` | `target_time`, `started_at`, `target_delta` | Early heating to hit recovery deadline |
| `learning_grace` | `until` | Initial learning period |

#### Example

```python
"status": {
    "activity": "heating",
    "overrides": [
        {"type": "preheating", "target_time": "07:00", "started_at": "05:30", "target_delta": 2.0},
        {"type": "night_setback", "delta": -2.0, "ends_at": "07:00", "limited_to": 1.0}
    ]
}
```

### `learning` Object

Learning progress indicator.

```python
"learning": {
    "status": "stable",             # idle | collecting | stable | tuned | optimized
    "confidence": 45                # 0-100%
}
```

| Status | Description |
|--------|-------------|
| `idle` | Learning paused (disturbance active) |
| `collecting` | Gathering data |
| `stable` | Basic convergence achieved |
| `tuned` | Well-tuned, first auto-apply eligible |
| `optimized` | High confidence, subsequent auto-applies eligible |

### `debug` Object

Only present when `debug: true` in domain config. Each group only present when that feature is configured/relevant.

```python
"debug": {
    "pwm": {
        "duty_accumulator_pct": 45.2
    },
    "cycle": {
        "state": "heating",           # idle | heating | settling
        "cycles_collected": 4,
        "cycles_required": 6
    },
    "preheat": {
        "heating_rate_learned": 0.5,  # °C/hour
        "observation_count": 3
    },
    "humidity": {
        "state": "normal",            # normal | paused | stabilizing
        "peak": 85.2
    },
    "undershoot": {
        "thermal_debt": 12.5,         # °C·min accumulated
        "consecutive_failures": 2,
        "ki_boost_applied": 1.2       # multiplier, 1.0 = no boost
    },
    "ke": {
        "observations": 15,
        "current_ke": 0.5
    },
    "pid": {
        "p_term": 1.2,
        "i_term": 3.5,
        "d_term": -0.3,
        "e_term": 0.8,
        "f_term": 0.0
    }
}
```

## Migration

Breaking change to attribute structure. Existing automations/templates referencing old attributes will need updates.

Still in alpha - breaking changes acceptable. Document in release notes.

## Implementation Notes

- `StatusManager` builds the `status` object
- Each manager contributes its override when active
- Override priority order: contact_open > humidity_spike > open_window > preheating > night_setback > learning_grace
- Debug groups populated by respective managers only when debug enabled
