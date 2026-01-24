# Predictive Pre-Heating (Time-to-Temperature)

Learn heating rate, start early to reach target AT scheduled time. Based on Netatmo Auto-Adapt, Tado Early Start.

## Foundation (Already Exists)

- `ThermalRateLearner` in `adaptive/thermal_rates.py` - learns heating rates
- `NightSetback` with `recovery_deadline` support
- `HEATING_TYPE_CHARACTERISTICS` with thermal mass info
- Persistence layer

## New/Extended Files

**`adaptive/preheat.py`** (new) or extend `thermal_rates.py`:
- `PreheatLearner` - stores observations binned by delta and outdoor temp
- `estimate_time_to_target(current, target, outdoor)` → minutes

## Configuration (`const.py`)

```python
CONF_PREHEAT_ENABLED = "preheat_enabled"
CONF_MAX_PREHEAT_HOURS = "max_preheat_hours"

HEATING_TYPE_PREHEAT_CONFIG = {
    "floor_hydronic": {"max_hours": 8.0, "cold_soak_margin": 1.5, "fallback_rate": 0.5},
    "radiator":       {"max_hours": 4.0, "cold_soak_margin": 1.3, "fallback_rate": 1.2},
    "convector":      {"max_hours": 2.0, "cold_soak_margin": 1.2, "fallback_rate": 2.0},
    "forced_air":     {"max_hours": 1.5, "cold_soak_margin": 1.1, "fallback_rate": 4.0},
}
```

## Config Schema

```yaml
night_setback:
  start: "22:00"
  end: "sunrise"
  delta: 2.0
  recovery_deadline: "07:00"
  preheat_enabled: true       # NEW
  max_preheat_hours: 3.0      # NEW (default per heating_type)
```

## Learning Algorithm

```python
HeatingObservation = {
    start_temp, end_temp, outdoor_temp, duration_minutes, rate
}

# Store binned by:
# - Delta: 0-2C, 2-4C, 4-6C, 6+C
# - Outdoor: cold (<5C), mild (5-12C), moderate (>12C)

def estimate_time_to_target(current, target, outdoor):
    delta = target - current
    bin = get_delta_bin(delta)
    outdoor_bin = get_outdoor_bin(outdoor)

    rates = get_observations(bin, outdoor_bin)
    base_rate = median(rates[-10:]) if len(rates) >= 3 else FALLBACK_RATE

    # Cold soak margin (larger delta = more margin)
    margin = (1.0 + delta/10 * 0.3) * COLD_SOAK_MARGIN[heating_type]

    return min((delta / base_rate) * 60 * margin, max_hours * 60)
```

## Preheat Start Calculation

```python
def calculate_preheat_start(deadline, current_temp, target_temp, outdoor_temp):
    time_needed = estimate_time_to_target(current_temp, target_temp, outdoor_temp)
    buffer = max(time_needed * 0.1, 15)  # 10% or 15min
    total = min(time_needed + buffer, max_preheat_hours * 60)

    return deadline - timedelta(minutes=total)
```

## Changes

| File | Change |
|------|--------|
| `const.py` | Add CONF_PREHEAT_*, HEATING_TYPE_PREHEAT_CONFIG |
| `adaptive/thermal_rates.py` | Extend with delta-binned observations |
| `adaptive/night_setback.py` | Use PreheatLearner in `should_start_recovery()` |
| `managers/night_setback_calculator.py` | Calculate dynamic preheat start |
| `climate.py` | Init PreheatLearner, record observations after cycles |
| `adaptive/persistence.py` | Persist preheat observations |
| `state_attributes.py` | Add preheat state attrs |

## Integration with Night Setback

Current: Fixed recovery time or uses rough heating type estimate

New:
```python
# In NightSetbackCalculator
if recovery_deadline and preheat_enabled:
    preheat_start = preheat_learner.calculate_preheat_start(
        deadline=recovery_deadline,
        current_temp=current,
        target_temp=target,
        outdoor_temp=outdoor
    )
    if now >= preheat_start:
        return (full_setpoint, preheat_active=True)
```

## State Attributes

- `preheat_enabled`
- `preheat_active`
- `preheat_scheduled_start`
- `preheat_estimated_duration_min`
- `preheat_learning_confidence`
- `preheat_heating_rate_learned`
- `preheat_observation_count`

## Edge Cases

| Case | Handling |
|------|----------|
| No learned data | Use FALLBACK_RATE per heating_type |
| current >= target | No preheat needed |
| Very cold outdoor | Outdoor bin adjusts estimate |
| Old observations | 90-day rolling window |

## Decisions

| Question | Decision |
|----------|----------|
| Immediate vs gradual ramp | **Immediate** - jump to full target when preheat starts |
| Scope | **Night setback only** - preset schedules deferred to v2 |
| Min observations | **3** - uses fallback rate until then |
| Observation expiry | **90 days** - rolling window, no decay weighting |
