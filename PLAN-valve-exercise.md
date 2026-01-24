# Valve Exercise Feature

Periodic valve cycling to prevent seizing during idle periods (summer). Based on Danfoss.

## New Files

**`managers/valve_exercise.py`** - `ValveExerciseManager` class

## Configuration (`const.py`)

```python
CONF_VALVE_EXERCISE = "valve_exercise"
CONF_VALVE_EXERCISE_ENABLED = "enabled"
CONF_VALVE_EXERCISE_FREQUENCY = "frequency"  # "weekly" | "monthly"
CONF_VALVE_EXERCISE_DAY = "day"              # 0-6 (Mon-Sun) or 1-31
CONF_VALVE_EXERCISE_TIME = "time"            # "03:00"
CONF_VALVE_EXERCISE_DURATION = "duration"    # seconds per position (default: min_cycle / 2)
```

## Config Schema (`climate.py` ~line 150)

```yaml
valve_exercise:
  enabled: true
  frequency: weekly
  day: 6  # Sunday
  time: "03:00"
  # duration defaults to min_cycle/2 if not specified
```

Validation: Only valid when `pwm: 0` (valve mode, not on/off PWM)

## Changes

| File | Change |
|------|--------|
| `const.py` | Add CONF_VALVE_EXERCISE_* constants |
| `climate.py` | Schema, init manager, schedule via `async_track_time_change` |
| `managers/heater_controller.py` | Add `async_exercise_valve(suppress_demand)`: 100% → wait → 0% → wait → restore, skips demand update |
| `managers/__init__.py` | Export ValveExerciseManager |
| `adaptive/persistence.py` | Store `last_exercise`, `exercise_count` |
| `managers/state_attributes.py` | Add `valve_exercise_last_run`, `valve_exercise_next_run` |

## Algorithm

```python
async def _async_valve_exercise_callback(self, _now):
    if self._is_heating or self._contact_paused:
        return  # Skip, try next interval

    # Exercise with suppress_demand=True to avoid triggering main heater
    await self._heater_controller.async_exercise_valve(suppress_demand=True)
    # Set to 100%, wait min_cycle/2, set to 0%, wait min_cycle/2, restore
```

## Scheduling

- Per-entity via `async_track_time_change(hour=H, minute=M)`
- Weekday/day-of-month check in callback
- Multi-zone: stagger by zone_index * 5min offset

## Edge Cases

| Case | Handling |
|------|----------|
| Actively heating | Skip, next interval |
| HVAC OFF | Still exercise (critical for summer) |
| Contact open | Skip |
| PWM mode | Feature disabled (validate in config) |
| Multi-zone | Stagger 5min apart |
| Main heater | Demand suppressed during exercise |

## Service (optional)

`adaptive_thermostat.exercise_valve` - manual trigger

## State Attributes

- `valve_exercise_enabled`
- `valve_exercise_last_run`
- `valve_exercise_next_run`
- `valve_exercise_in_progress`

## Decisions

| Question | Decision |
|----------|----------|
| Exercise when HVAC OFF | **Yes** - critical for summer when heating idle for months |
| Duration per position | **`min_cycle / 2`** - half the configured valve timing |
| Restore after exercise | **Previous position** - restore to pre-exercise state |
| Main heater trigger | **No** - exercise must NOT report demand to CentralController |
