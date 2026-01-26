# Humidity-Based Steam Detection

> This content is for the [Features](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Features) wiki page.

## Overview

Detects shower steam in bathroom zones using humidity rate-of-change analysis. Automatically pauses heating during showers and resumes control after humidity stabilizes, preventing false temperature readings from steam condensation.

**Use case:** Bathroom with floor hydronic heating + humidity sensor. Shower steam causes temperature sensor spike → thermostat stops heating → room gets cold after shower ends.

**Solution:** Pause heating during shower, decay PID integral to prevent overcorrection, resume normal control after 5-minute stabilization period.

## Configuration

Entity-level configuration only (similar to contact sensors):

```yaml
climate:
  - platform: adaptive_thermostat
    name: Bathroom
    heater: switch.bathroom_ufh
    target_sensor: sensor.bathroom_temp
    humidity_sensor: sensor.bathroom_humidity        # Required
    humidity_spike_threshold: 15                     # Optional, default 15
    humidity_absolute_max: 80                        # Optional, default 80
    humidity_detection_window: 300                   # Optional, default 300
    humidity_stabilization_delay: 300                # Optional, default 300
```

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `humidity_sensor` | entity_id | Yes | - | Humidity sensor entity (must report %) |
| `humidity_spike_threshold` | float | No | 15 | Humidity rise (%) to trigger detection |
| `humidity_absolute_max` | float | No | 80 | Absolute humidity (%) threshold |
| `humidity_detection_window` | int | No | 300 | Time window (seconds) for rate calculation |
| `humidity_stabilization_delay` | int | No | 300 | Wait time (seconds) after humidity drops |

## Detection Algorithm

Uses ring buffer to track humidity history over `humidity_detection_window`.

**Trigger conditions (OR):**
1. **Rate-of-change:** Humidity rises ≥ `humidity_spike_threshold`% within detection window
2. **Absolute threshold:** Humidity ≥ `humidity_absolute_max`%

**Exit conditions (AND):**
1. Humidity < 70%
2. Humidity dropped >10% from peak value

**Example:** With defaults (15%, 80%, 5 min window):
- Humidity 45% → 60% in 5 minutes = 15% rise → **PAUSED**
- OR humidity reaches 80% → **PAUSED**
- Humidity drops to 68% (peak was 80%, dropped 12%) → **STABILIZING**
- After 5 minutes → **NORMAL**

## State Machine

```
NORMAL
  ├─ (humidity spike detected) ─┐
  │                              │
  v                              │
PAUSED                           │
  ├─ Heater: OFF                 │
  ├─ Integral decay: ~10%/min    │
  ├─ Max duration: 60 min        │
  │                              │
  ├─ (humidity drops) ───────────┤
  │                              │
  v                              │
STABILIZING                      │
  ├─ Heater: OFF                 │
  ├─ Timer: stabilization_delay  │
  │                              │
  ├─ (timer expires) ────────────┤
  │                              │
  └─ (back to) ──────────────────┘
```

## Behavior Details

### During PAUSED State

1. **Heater control:** Turned off, PID calculation skipped
2. **Integral decay:** PID integral term decays at ~10%/min to prevent stale accumulation
   - Formula: `integral *= 0.9 ** (elapsed_min)`
   - Prevents overcorrection when heating resumes
3. **Peak tracking:** Records maximum humidity for exit condition
4. **Max pause:** Automatically exits after 60 minutes (stuck sensor protection)

### During STABILIZING State

1. **Heater control:** Remains off
2. **Timer:** Waits for `humidity_stabilization_delay` seconds
3. **Purpose:** Allows condensation to evaporate from temperature sensor
4. **Reset:** If humidity spikes again, returns to PAUSED (back-to-back showers)

### Interactions

**With preheat:**
- Preheat does NOT start if humidity detection is PAUSED or STABILIZING
- Prevents wasted heating cycles during showers

**With contact sensors:**
- Humidity detection is checked before contact sensors in control loop
- Both can independently pause heating

**With open window detection:**
- Independent systems, both can trigger simultaneously
- Humidity detection takes precedence in control flow

## State Attributes

Exposed in Home Assistant entity attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `humidity_detection_state` | string | Current state: `normal`, `paused`, `stabilizing` |
| `humidity_resume_in` | int\|None | Seconds until resume (when stabilizing), `None` otherwise |

### Example Usage in Automation

```yaml
automation:
  - alias: Notify on bathroom shower
    trigger:
      - platform: state
        entity_id: climate.bathroom
        attribute: humidity_detection_state
        to: "paused"
    action:
      - service: notify.mobile_app
        data:
          message: "Shower detected - heating paused"
```

## Validation Rules

1. `humidity_sensor` entity must exist and report numeric state
2. Thresholds must be > 0 and < 100
3. Time windows must be ≥ 60 seconds
4. Detection window must be ≥ stabilization delay (for data buffer)

## Troubleshooting

### Heater doesn't resume after shower

**Check:**
1. Developer Tools → States → Check `humidity_detection_state` attribute
2. If stuck in STABILIZING, humidity may not have dropped enough
3. Verify humidity sensor is working: `sensor.bathroom_humidity` should show < 70%

**Solution:** Adjust `humidity_stabilization_delay` or thresholds

### False triggers (not during shower)

**Check:**
1. Humidity sensor history in HA for spurious spikes
2. Sensitivity: 15% rise might be too low for your sensor

**Solution:** Increase `humidity_spike_threshold` (try 20-25%)

### Triggers too late (shower already started)

**Check:**
1. Detection window may be too long
2. Rate calculation needs faster response

**Solution:** Decrease `humidity_detection_window` (try 120-180 seconds)

### Resumes too early (sensor still wet)

**Check:**
1. Stabilization delay may be too short
2. Condensation evaporates slowly in your bathroom

**Solution:** Increase `humidity_stabilization_delay` (try 420-600 seconds)

## Advanced: Decay Rate Tuning

Default integral decay is 10%/min (0.9 ** minutes_elapsed). For systems with large integral buildup, adjust decay in code:

```python
# In climate.py _async_control_heating
decay_factor = 0.85 ** (elapsed / 60)  # 15% decay per minute (more aggressive)
```

Higher decay (0.85-0.80) = faster reset, lower overshoot risk
Lower decay (0.92-0.95) = slower reset, maintains warmth better

## Implementation Notes

**Module:** `adaptive/humidity_detector.py` (`HumidityDetector` class)

**Key methods:**
- `record_humidity(timestamp, value)` - Add observation to ring buffer
- `should_pause()` - Returns True if heating should be paused
- `get_state()` - Returns current state machine state
- `get_time_until_resume()` - Returns countdown for STABILIZING state

**Integration points:**
1. `climate.py` `__init__` - Initialize detector if `humidity_sensor` configured
2. `climate.py` `_async_humidity_sensor_changed` - Callback feeds detector
3. `climate.py` `_async_control_heating` - Check `should_pause()` before PID
4. `managers/state_attributes.py` - Expose state attributes

**Testing:** `tests/test_humidity_detector.py`
