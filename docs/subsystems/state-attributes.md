# State Attributes

Exposed via `extra_state_attributes`. Structure: flat restoration fields + grouped objects for diagnostics.

**Full structure:**
```python
{
    # Flat restoration (required for RestoreEntity)
    "integral": 5.2,
    "pid_history": [...],
    "outdoor_temp_lagged": 8.5,
    "cycle_count": 52,              # or {"heater": 42, "cooler": 10}
    "control_output": 65.3,

    # Preset temps (standard HA)
    "away_temp": 16.0,
    "eco_temp": 18.0,
    "boost_temp": 22.0,
    "comfort_temp": 20.0,
    "home_temp": 20.0,
    "sleep_temp": 18.0,
    "activity_temp": 21.0,

    # Grouped objects
    "status": {
        "activity": "heating",      # idle | heating | cooling | settling
        "overrides": [              # ordered by priority, first = in control
            {"type": "contact_open", "sensors": [...], "since": "..."},
            {"type": "night_setback", "delta": -2.0, "ends_at": "07:00"}
        ]
    },
    "learning": {
        "status": "stable",         # idle | collecting | stable | tuned | optimized
        "confidence": 45,           # 0-100%
        "cycles": 12,               # learned profile keys below are omitted when unknown
        "heating_type": "radiator",
        "area_m2": 18.0,
        "heating_rate": 0.91,
        "heating_rate_expected": 1.2,
        "overshoot": 0.2,
        "overshoot_limit": 0.2,
        "settling_time": 30.0,
        "settling_time_limit": 45.0
    },
    "settings": {
        "changed": false,           # true when the zone has saved runtime edits
        "vacation": false,          # vacation mode active
        "night_setback": {          # omitted when the zone has no night setback
            "start": "21:00", "delta": 1.0, "recovery_deadline": "9:00",
            "editable": true        # false with a fixed end time
        }
    },
    "debug": {...}                  # only when debug: true
}
```

**Override types and fields:**
- `contact_open` - `sensors` (list of entity IDs), `since` (ISO8601 timestamp)
- `humidity` - `state` (paused|stabilizing), `resume_at` (ISO8601), `peak` (% humidity, debug only)
- `open_window` - `since` (ISO8601), `resume_at` (ISO8601)
- `preheating` - `target_time` (ISO8601), `started_at` (ISO8601), `target_delta` (°C)
- `night_setback` - `delta` (°C), `ends_at` (ISO8601), `limited_to` (°C, when learning gate active)
- `learning_grace` - `until` (ISO8601 timestamp)

**Learning profile keys** (merged into `learning`, each omitted when unknown):

| Key | Unit | Meaning |
|-----|------|---------|
| `cycles` | count | Stored heating cycles (always present) |
| `heating_type` | - | Configured heating type |
| `area_m2` | m² | Configured zone area |
| `heating_rate` | °C/h | Mean learned heat-up rate (non-stalled sessions, needs ≥ 3) |
| `heating_rate_expected` | °C/h | Physics-based baseline for this zone |
| `overshoot` | °C | Mean overshoot over the last 20 cycles |
| `overshoot_limit` | °C | Convergence limit for this heating type |
| `settling_time` | min | Mean settling time over all stored cycles |
| `settling_time_limit` | min | Convergence limit for this heating type |

**Learning status states:**
- `idle` - Learning paused (contact_open, humidity_spike, or learning_grace active)
- `collecting` - Gathering data (< 6 cycles OR confidence below tier 1 threshold)
- `stable` - Basic convergence (confidence ≥ tier 1 threshold AND < tier 2)
- `tuned` - System well-tuned, night setback enabled (confidence ≥ tier 2 threshold AND < 95%)
- `optimized` - Very high confidence (confidence ≥ 95%)

**Confidence tier thresholds (scaled by heating type):**

| Heating Type | Tier 1 (stable) | Tier 2 (tuned) | Tier 3 (optimized) |
|--------------|-----------------|----------------|-------------------|
| floor_hydronic | 32% | 56% | 95% |
| radiator | 36% | 63% | 95% |
| convector | 40% | 70% | 95% |
| forced_air | 44% | 77% | 95% |

*Base values: Tier 1=40%, Tier 2=70%, Tier 3=95%. Scaling: floor 0.8x, radiator 0.9x, convector 1.0x, forced_air 1.1x (capped at 95%)*

**Debug groups** (when `debug: true` in domain config):
- `pwm` - `duty_accumulator_pct` (% of threshold)
- `cycle` - `state` (idle/heating/settling), `cycles_collected`, `cycles_required`
- `preheat` - `heating_rate_learned` (°C/h), `observation_count`
- `humidity` - `state` (normal/paused/stabilizing), `peak` (% humidity)
- `undershoot` - `thermal_debt` (°C·min), `consecutive_failures`, `ki_boost_applied`
- `ke` - `observations`, `current_ke`
- `pid` - `p_term`, `i_term`, `d_term`, `e_term`, `f_term` (all % control output)

