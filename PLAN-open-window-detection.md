# Open Window Detection (Algorithmic)

Detect sudden temp drops to pause heating without physical contact sensors. Based on Danfoss Ally.

## New Files

**`adaptive/open_window_detection.py`** - `OpenWindowDetector` class

## Configuration (`const.py`)

```python
CONF_OPEN_WINDOW_DETECTION = "open_window_detection"
CONF_OWD_TEMP_DROP = "temp_drop"              # default 0.5 C
CONF_OWD_DETECTION_WINDOW = "detection_window" # default 180 sec (3 min)
CONF_OWD_PAUSE_DURATION = "pause_duration"     # default 1800 sec (30 min)
CONF_OWD_COOLDOWN = "cooldown"                 # default 2700 sec (45 min)
CONF_OWD_ACTION = "action"                     # default "pause" | "frost_protection"
```

## Config Schema

**Domain level (`__init__.py`)** - defaults, all optional:
```yaml
adaptive_thermostat:
  open_window_detection:
    temp_drop: 0.5
    detection_window: 180
    pause_duration: 1800
    cooldown: 2700
    action: pause
```

**Entity level (`climate.py`)** - override/disable:
```yaml
climate:
  - platform: adaptive_thermostat
    open_window_detection: false  # Disable for this zone
    # OR
    open_window_detection:
      temp_drop: 0.8  # Override threshold for drafty room
```

## Precedence

1. Entity has `contact_sensors:` → OWD inactive (physical sensors win)
2. Entity sets `open_window_detection: false` → OWD disabled
3. Otherwise → OWD enabled with entity overrides > domain defaults > built-in defaults

## OpenWindowDetector Class

```python
class OpenWindowDetector:
    _temp_history: deque[(datetime, float)]  # Ring buffer
    _detection_triggered: bool
    _pause_start_time: datetime | None
    _last_detection_time: datetime | None
    _suppressed_until: datetime | None

    record_temperature(timestamp, temp)  # Feed temp updates
    check_for_drop() -> bool             # oldest - current >= threshold?
    trigger_detection()                   # Start pause
    should_pause(now) -> bool            # In pause period?
    pause_just_expired() -> bool         # Emit resume event once
    suppress_detection(duration)          # Suppress after setpoint change
    get_action() -> ContactAction
```

## Detection Algorithm

```
1. Maintain ring buffer of (timestamp, temp) for detection_window
2. Prune entries older than detection_window
3. If (oldest_temp - current_temp) >= temp_drop:
   - Not in cooldown
   - Not suppressed
   → Trigger detection, pause for pause_duration
```

## Changes

| File | Location | Change |
|------|----------|--------|
| `const.py` | ~line 419 | Add CONF_OWD_* constants + defaults |
| `__init__.py` | schema | Add domain-level `open_window_detection` block |
| `climate.py` | schema | Add entity-level `open_window_detection` (bool or dict) |
| `climate.py` | `__init__` | Merge domain+entity config, init detector if enabled AND no contact_sensors |
| `climate.py` | `_async_update_temp` | Feed temp to detector |
| `climate.py` | `async_set_temperature` | Suppress detection for 5min |
| `climate.py` | `async_set_hvac_mode` | Clear detector on OFF |
| `climate.py` | `_async_control_heating` | Check detector, apply pause |
| `state_attributes.py` | | Add `open_window_detection_active` |

## Integration Point (`_async_control_heating` ~line 2037)

```python
# After contact sensor check
if self._open_window_detector:
    if self._open_window_detector.check_for_drop():
        self._open_window_detector.trigger_detection()
        _LOGGER.info("Open window detected - pausing %d min", pause_duration // 60)
        self._cycle_dispatcher.emit(ContactPauseEvent(...))

    if self._open_window_detector.should_pause(datetime.now()):
        await self._async_heater_turn_off(force=True)
        return
    elif self._open_window_detector.pause_just_expired():
        self._cycle_dispatcher.emit(ContactResumeEvent(...))
        _LOGGER.info("Open window pause expired - resuming")
```

## Edge Cases

| Case | Handling |
|------|----------|
| Setpoint change down | Suppress 5min, clear history |
| HVAC OFF | Clear detector state |
| Rapid re-triggers | 45min cooldown |
| Sensor noise | 3min window smooths spikes |
| Recovery | Auto-resume after 30min |
| Night setback transition | Suppress during transitions |

## State Attributes

- `open_window_detection_active`
- `open_window_cooldown_remaining`

## Decisions

1. **Auto-extend pause if temp keeps dropping?** No - cooldown handles re-detection
2. **Emit `ContactResumeEvent` when pause expires?** Yes - consistency with contact sensors
3. **Create diagnostic sensor entity?** No - state attributes sufficient
