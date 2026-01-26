# Humidity-Based Steam Detection for Bathrooms

## Problem
Shower steam causes temp sensor spikes → thermostat stops heating → room cools after shower

## Solution: Humidity Spike Detector

Detect shower via humidity rate-of-change, pause heating during shower, resume normal PID after stabilization.

### Detection Logic
- **Trigger**: Humidity rises >15% in 5 minutes OR absolute >80%
- Entity-level config only (like contact_sensors)

### Behavior
1. **Shower detected** → Pause heating (turn off heater), start integral decay
2. **During pause** → Decay PID integral ~10%/min (prevents stale buildup)
3. **Shower ends** → Wait 5 min stabilization (sensor condensation evaporates)
4. **After stabilization** → Resume normal PID with decayed integral

### Exit Condition
- Humidity <70% AND dropped >10% from peak → start stabilization timer

### State Machine
```
NORMAL ──(humidity spike)──> PAUSED ──(humidity drops)──> STABILIZING ──(5min)──> NORMAL
                            (heater off)                   (heater off)
```

---

## Implementation

### Files to Modify
1. `const.py` - config constants
2. `climate.py` - sensor tracking, callbacks, control loop
3. `managers/state_attributes.py` - state exposure
4. New: `adaptive/humidity_detector.py` - detector class (follows contact_sensors pattern)
5. New: `tests/test_humidity_detector.py`

### Config Schema
```yaml
climate:
  - platform: adaptive_thermostat
    humidity_sensor: sensor.bathroom_humidity
    humidity_spike_threshold: 15       # % rise to trigger (default 15)
    humidity_absolute_max: 80          # % absolute cap (default 80)
    humidity_detection_window: 300     # seconds for rate calc (default 300)
    humidity_stabilization_delay: 300  # seconds after humidity drops (default 300)
```

### HumidityDetector Class
```python
class HumidityDetector:
    _humidity_history: deque[(datetime, float)]  # ring buffer
    _state: Literal["normal", "paused", "stabilizing"]
    _peak_humidity: float
    _stabilization_start: datetime | None

    def record_humidity(ts, humidity) → None
    def get_state() → str
    def should_pause() → bool
    def get_time_until_resume() → int | None
```

### Integration in climate.py
```python
# In _async_control_heating, before contact sensor check:
if self._humidity_detector and self._humidity_detector.should_pause():
    # Decay integral while paused (~10%/min = 0.17%/sec)
    elapsed = time.time() - self._last_control_time
    decay_factor = 0.9 ** (elapsed / 60)  # 10% decay per minute
    self._pid_controller.decay_integral(decay_factor)

    await self._async_heater_turn_off()
    return  # Skip PID calculation
```

### PID Controller Addition
Add `decay_integral(factor)` method to PIDController:
```python
def decay_integral(self, factor: float) -> None:
    """Decay integral term by factor (0-1). Used during humidity pause."""
    self._integral *= factor
```

### State Attributes
- `humidity_detection_state`: "normal" | "paused" | "stabilizing"
- `humidity_resume_in`: seconds until normal (when stabilizing)

### Edge Cases
- **Max pause duration**: 60 min cap, then resume with warning log (stuck sensor protection)
- **Back-to-back showers**: Reset stabilization timer if humidity spikes again
- **Night setback interaction**: Don't start preheat while paused

---

## Verification
1. Run `pytest tests/test_humidity_detector.py`
2. Deploy to HAOS, configure bathroom thermostat with humidity sensor
3. Test: trigger humidity spike, verify heater pauses
4. Test: humidity drops, verify 5min stabilization then resume
5. Check state attributes in HA developer tools

---

## Tasks

1. TEST: HumidityDetector spike detection
2. IMPL: `const.py` constants + `adaptive/humidity_detector.py` class
3. TEST: HumidityDetector stabilization + resume
4. IMPL: `decay_integral()` method in PIDController
5. IMPL: Config schema + init in `climate.py`
6. TEST: Integration with climate entity (including integral decay)
7. IMPL: State change listener + control loop integration
8. IMPL: State attributes
9. VERIFY: Full pytest suite passes
10. DOCS: Update wiki
