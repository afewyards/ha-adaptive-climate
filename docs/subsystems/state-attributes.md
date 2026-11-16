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
        "status": "collecting",     # idle | collecting | stable | tuned | optimized
        "confidence": 30,           # 0-100%
        "cycles": 12,               # learned profile keys below are omitted when unknown
        "heating_type": "radiator",
        "area_m2": 18.0,
        "heating_rate": 0.91,
        "heating_rate_expected": 1.0,
        "overshoot": 0.05,
        "overshoot_limit": 0.25,
        "undershoot": 0.29,
        "undershoot_limit": 0.25,
        "settling_time": 30.0,
        "settling_time_limit": 90,
        "recent": "ggpgpppgpppp",   # g good / p poor, oldest first
        "recent_fails": {"undershoot": 8, "rise_time": 2},
        "below": {"over": 9, "over_with": 7, "rest": 11, "rest_with": 3},
        "gains": {"kp": 1.84, "ki": 8.2286, "kd": 0.63},
        "gains_physics": {"kp": 1.84, "ki": 5.7143, "kd": 0.63},
        "tune": {"state": "waiting", "cycles_left": 2, "last": "converged"},
        "setback_limit": 0.5,
        "setback_until": 36
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
| `undershoot` | °C | Mean undershoot over the last 20 cycles |
| `undershoot_limit` | °C | Convergence limit for this heating type |
| `settling_time` | min | Mean settling time over all stored cycles |
| `settling_time_limit` | min | Convergence limit for this heating type |
| `recent` | - | One char per cycle, last 20, oldest first: `g` good, `p` poor |
| `recent_fails` | count | Poor cycles in `recent` by cause (`undershoot`, `overshoot`, `settling`, `oscillation`, `rise_time`); a cycle counts once per failed cause; zero counts and an all-good strip leave it out |
| `below` | count | Over the last 20 cycles that have a `heat_below` value: `over` failed on overshoot, `rest` the others, `over_with` / `rest_with` those where a room on the floor below was heating for 0.25 of the cycle or more; omitted when no cycle has a value |
| `ki_boost_capped` | - | `true` when the cumulative undershoot Ki multiplier is at its 3.0× cap |
| `gains` | - | `{kp, ki, kd}` in use now |
| `gains_physics` | - | `{kp, ki, kd}` a `reset_pid_to_physics` would set now; omitted without `area_m2` |
| `tune` | - | What heating auto-tune is waiting for (below); omitted in cooling mode |
| `setback_limit` | °C | Night setback depth learning allows now, only when below the configured `delta`; omitted during a contact-sensor or humidity pause |
| `setback_until` | % | Confidence at which that limit next relaxes; omitted for a 0° hold, which lifts at 3 cycles |

Each heating cycle stores `heat_below`: the share of the cycle (0-1, 2 dp), from its start until its metrics are finalised, during which a room on the floor below was heating (coordinator demand in heat mode, sampled with each temperature update during the cycle). The floor below is the nearest lower floor level that has an Adaptive Climate zone; floors come from the entity's area and its floor level in Home Assistant. It is `null` when the zone's floor is unknown, no other zone sits lower, a lookup failed, or the cycle is older than this field. It is measurement only: it does not change how a cycle is judged or learned from.

Good and poor use the confidence rule. Whether a cycle counts as a recovery (which skips the rise-time check) is decided with today's confidence, so an old cycle can read differently from how it was counted then.

**`tune` states** (heating mode, read-only: building it never records a seasonal shift, starts validation or changes a counter):

| `state` | Extra keys | Meaning |
|---------|------------|---------|
| `ready` | `last` | The next cycle runs the auto-tune check |
| `waiting` | `cycles_left`, `hours_left`, `last` | Larger of cycles still missing for the minimum and cycles left in the cooldown; cooldown hours rounded up. Zero values are left out |
| `checking` | `done`, `of` | Validating the last auto-tune, `done` of `of` cycles |
| `blocked` | `reason` | `lifetime_limit`, `seasonal_limit`, `drift_limit` or `seasonal_shift` |
| `off` | `reason` | `disabled` (`auto_apply_pid: false`), `no_room_size` (no `area_m2`) or `no_baseline` (area set, but no physics baseline was recorded) |

`last` is why the most recent PID recommendation check gave no change, when there is one since the last restart: `gates_not_passed`, `rate_limited`, `insufficient_data`, `insufficient_usable_data`, `converged`, `no_rules_triggered` or `no_change`.

`ready` does not rule out a block: the seasonal shift check runs only at the auto-tune check itself (it records the shift), so a room shown as `ready` can still be blocked for 7 days at its next cycle.

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

