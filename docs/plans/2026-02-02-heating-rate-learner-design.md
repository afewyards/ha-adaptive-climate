# HeatingRateLearner Design

## Problem

Two separate learning mechanisms exist:
- **PreheatLearner**: learns heating rate for preheat timing, isolated from main learner
- **CycleMetrics**: tracks rise_time per cycle, doesn't compute reusable rates

Real-world observation: GF zone had 18 cycles over 8 hours with only 0.4°C rise (0.05°C/h). Per-cycle learning misses this session-level pattern.

## Solution

Unified `HeatingRateLearner` inside `AdaptiveLearner` that:
1. Learns from every cycle AND multi-cycle recovery sessions
2. Provides heating rate for preheat timing (replaces PreheatLearner)
3. Enhances undershoot detection with rate comparison

## Learning Modes

| Mode | Granularity | Best For |
|------|-------------|----------|
| Per-cycle | Single cycle rise_time → rate | Fast systems (convector, forced_air) |
| Session | Multi-cycle climb → rate | Slow systems (floor, radiator) |

## Binning

Same as current PreheatLearner:
- **Delta bins**: 0-2°C, 2-4°C, 4-6°C, 6+°C
- **Outdoor bins**: cold (<5°C), mild (5-15°C), moderate (>15°C)
- 12 bins total, max 20 observations per bin

## Session Tracking

### State Machine

```
IDLE ──(temp < setpoint - threshold)──> TRACKING
                                            │
            ┌───────────────────────────────┤
            │                               │
            ▼                               ▼
    (reached setpoint)              (stalled 3 cycles)
            │                               │
            ▼                               ▼
      SUCCESS                          STALLED
            │                               │
            └───────────> BANK <────────────┘
                     (if valid)
```

### Session Data

```python
@dataclass
class RecoverySession:
    start_temp: float
    start_time: datetime
    target_setpoint: float
    outdoor_temp: float
    cycles_in_session: int
    cycle_duties: list[float]  # for avg duty calculation
    last_progress_cycle: int   # for stall detection
```

### Session End Conditions

| Condition | Result | Action |
|-----------|--------|--------|
| Reached setpoint (within threshold) | Success | Bank observation |
| Stalled within threshold | Success | Bank observation |
| Stalled outside threshold | Stalled | Bank observation, increment stall counter |
| Override triggered (contact, humidity) | Discarded | Don't bank, reset session |
| Duration < minimum | Discarded | Don't bank |

### Stall Detection

No progress (temp rise < 0.1°C) for 3 consecutive cycles → session ends as stalled.

### Minimum Session Duration

| Heating Type | Min Duration |
|--------------|--------------|
| floor_hydronic | 60 min |
| radiator | 30 min |
| convector | 15 min |
| forced_air | 10 min |

## Observation Structure

```python
@dataclass
class HeatingRateObservation:
    rate: float           # °C/hour
    duration_min: float   # session/cycle duration
    source: str           # "cycle" or "session"
    stalled: bool         # True if ended without reaching setpoint
    timestamp: datetime
```

## Undershoot Detection Integration

### New Mode: Session Rate Comparison

Supplements existing thermal debt and consecutive failure detection.

**Trigger conditions (all must be true):**
- Current rate < 60% of expected rate
- 2 consecutive stalled sessions
- Average duty < 85% (system has headroom)
- ≥5 observations in bin (sufficient data)

**Action:** Ki boost × 1.20

### Stall Counter Reset

Reset on:
- Successful session (reached setpoint)
- Outdoor temp change > 5°C
- Setpoint change > 1°C

### Capacity Warning

If rate < 60% expected BUT duty ≥ 85%:
- Log capacity warning
- Fire event for user notification
- No Ki boost (system maxed out)

### Detection Summary

| Mode | Trigger | Ki Action |
|------|---------|-----------|
| Thermal debt | debt > fixed threshold | boost × 1.20 |
| Consecutive failures | N cycles, rise_time=None | boost × 1.25 |
| Session rate | <60% expected, 2 stalls, duty <85% | boost × 1.20 |

## Preheat Integration

### Query Interface

```python
def get_heating_rate(
    self,
    delta: float,
    outdoor_temp: float
) -> tuple[float, str]:
    """
    Returns (rate_c_per_hour, source)
    source: "learned_session", "learned_cycle", "fallback"
    """
```

### Priority

1. Session observations in matching bin (≥3 observations)
2. Per-cycle observations in matching bin (≥3 observations)
3. Adjacent bin interpolation
4. Fallback to `HEATING_TYPE_PREHEAT_CONFIG`

## Module Structure

### New File: `adaptive/heating_rate_learner.py`

```python
class HeatingRateLearner:
    def __init__(self, heating_type: HeatingType)

    # Session tracking
    def start_session(self, temp: float, setpoint: float, outdoor: float) -> None
    def update_session(self, temp: float, cycle_duty: float) -> None
    def end_session(self, reason: str) -> HeatingRateObservation | None

    # Per-cycle learning
    def add_cycle_observation(self, metrics: CycleMetrics, outdoor: float) -> None

    # Query interface
    def get_heating_rate(self, delta: float, outdoor: float) -> tuple[float, str]
    def get_expected_vs_actual(self, delta: float, outdoor: float, current_rate: float) -> float | None

    # Serialization
    def to_dict(self) -> dict
    @classmethod
    def from_dict(cls, data: dict, heating_type: HeatingType) -> "HeatingRateLearner"
```

### Files to Modify

| File | Changes |
|------|---------|
| `adaptive/heating_rate_learner.py` | New file |
| `adaptive/learning.py` | Own HeatingRateLearner, integrate in add_cycle_metrics |
| `adaptive/learner_serialization.py` | v8 format, migrate PreheatLearner data |
| `adaptive/preheat.py` | Delegate to HeatingRateLearner, deprecate internal learning |
| `adaptive/undershoot_detector.py` | Add rate comparison mode |
| `climate.py` | Session lifecycle hooks (start/end/discard) |
| `managers/night_setback.py` | Use new query interface |

## Persistence

### v8 Format

```python
{
    "version": 8,
    # ... existing fields ...
    "heating_rate_learner": {
        "bins": {
            "delta_0_2_cold": [
                {"rate": 0.15, "duration_min": 180, "source": "session",
                 "stalled": false, "timestamp": "..."},
            ],
            # ... 12 bins total
        },
        "stall_counter": 0,
        "last_stall_outdoor": null,
        "last_stall_setpoint": null
    }
}
```

### Migration

On v7 load:
1. Migrate PreheatLearner observations into HeatingRateLearner with `source="cycle"`
2. Delete standalone PreheatLearner data
