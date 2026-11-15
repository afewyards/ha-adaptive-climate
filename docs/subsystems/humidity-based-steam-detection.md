# Humidity-Based Steam Detection

Detects shower steam in bathrooms, pauses heating during shower, resumes after stabilization. Module: `adaptive/humidity_detector.py` (`HumidityDetector`).

**Configuration (entity-level only):**
```yaml
climate:
  - platform: adaptive_climate
    humidity_sensor: sensor.bathroom_humidity
    humidity_spike_threshold: 15       # % rise to trigger (default 15)
    humidity_absolute_max: 80          # % absolute cap (default 80)
    humidity_detection_window: 300     # seconds for rate calc (default 300)
    humidity_stabilization_delay: 300  # seconds after drop (default 300)
    humidity_exit_threshold: 70        # % absolute for exit (default 70)
    humidity_exit_drop: 5              # % drop from peak for exit (default 5)
```

**Detection algorithm:** Ring buffer tracks humidity over `detection_window`. Triggers when spike ≥ `humidity_spike_threshold`% OR absolute ≥ `humidity_absolute_max`%. Exit when humidity < `humidity_exit_threshold`% AND dropped ≥ `humidity_exit_drop`% from peak.

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

**Status attribute:** See consolidated `status` attribute below.
