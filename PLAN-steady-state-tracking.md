# Plan: Add Steady-State Tracking Metrics

## Problem
System marks PID as "converged" despite 0.7°C persistent undershoot. Each cycle looks fine individually, but room cools between cycles (IDLE state not captured).

## Solution
Add two new metrics:
1. **Inter-cycle drift**: `start_temp[n] - end_temp[n-1]` - catches insufficient Ki
2. **Settling MAE**: Mean absolute error during settling phase - catches poor within-cycle control

## Files to Modify

| File | Changes |
|------|---------|
| `adaptive/cycle_analysis.py` | Add `end_temp`, `settling_mae`, `inter_cycle_drift` to CycleMetrics; add `calculate_settling_mae()` |
| `managers/cycle_tracker.py` | Track `_prev_cycle_end_temp`; calculate drift and MAE in `_record_cycle_metrics()` |
| `const.py` | Add thresholds: `inter_cycle_drift_max`, `settling_mae_max` per heating type |
| `adaptive/learning.py` | Add metrics to `_check_convergence()`; extract/average in `calculate_pid_adjustment()` |
| `adaptive/pid_rules.py` | Add `INTER_CYCLE_DRIFT` rule: negative drift → increase Ki 15% |

## Implementation Steps

### 1. CycleMetrics (cycle_analysis.py)
```python
# Add to __init__:
end_temp: Optional[float] = None,
settling_mae: Optional[float] = None,
inter_cycle_drift: Optional[float] = None,
```

### 2. calculate_settling_mae() (cycle_analysis.py)
```python
def calculate_settling_mae(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    settling_start_time: Optional[datetime] = None,
) -> Optional[float]:
    """MAE during settling phase (after heater off)."""
    # Filter to settling phase only
    # Return mean(|temp - target|)
```

### 3. Cycle Tracker (cycle_tracker.py)
- Add `_prev_cycle_end_temp: Optional[float] = None` instance var
- In `_record_cycle_metrics()`:
  - `end_temp = self._temperature_history[-1][1]`
  - `settling_mae = calculate_settling_mae(..., settling_start_time=self._device_off_time)`
  - `drift = start_temp - self._prev_cycle_end_temp` if prev exists
  - Store `self._prev_cycle_end_temp = end_temp` after recording
- On init: restore `_prev_cycle_end_temp` from last cycle in history (if exists)

### 4. Thresholds (const.py)
```python
HEATING_TYPE_CONVERGENCE_THRESHOLDS = {
    HEATING_TYPE_FLOOR_HYDRONIC: {
        # existing...
        "inter_cycle_drift_max": 0.3,  # °C, absolute value
        "settling_mae_max": 0.3,
    },
    HEATING_TYPE_RADIATOR: {
        "inter_cycle_drift_max": 0.25,
        "settling_mae_max": 0.25,
    },
    # etc for other types
}
```

### 5. Convergence Check (learning.py)
```python
def _check_convergence(
    self,
    avg_overshoot: float,
    avg_oscillations: float,
    avg_settling_time: float,
    avg_rise_time: float,
    avg_inter_cycle_drift: float,  # NEW
    avg_settling_mae: float,       # NEW
) -> bool:
    is_converged = (
        # existing checks...
        and abs(avg_inter_cycle_drift) <= self._convergence_thresholds["inter_cycle_drift_max"]
        and avg_settling_mae <= self._convergence_thresholds["settling_mae_max"]
    )
```

### 6. PID Rule (pid_rules.py)
```python
class PIDRule(Enum):
    # existing...
    INTER_CYCLE_DRIFT = ("inter_cycle_drift", RULE_PRIORITY_SLOW_RESPONSE)

# In evaluate_pid_rules():
if avg_inter_cycle_drift < -thresholds["inter_cycle_drift_max"]:
    results.append(PIDRuleResult(
        rule=PIDRule.INTER_CYCLE_DRIFT,
        kp_factor=1.0,
        ki_factor=1.15,  # Increase Ki 15%
        kd_factor=1.0,
        reason=f"Inter-cycle drift {avg_inter_cycle_drift:.2f}°C indicates Ki too low"
    ))
```

## Tests

| Test File | Test Cases |
|-----------|------------|
| `test_cycle_analysis.py` | `calculate_settling_mae()` with various settling patterns |
| `test_cycle_tracker.py` | Inter-cycle drift calculation across consecutive cycles |
| `test_learning.py` | Convergence fails when drift exceeds threshold |
| `test_pid_rules.py` | INTER_CYCLE_DRIFT rule fires and adjusts Ki |

## Verification
1. Run `pytest tests/` - all pass
2. Deploy to HAOS bathroom thermostat
3. Monitor: after 2+ cycles, check `inter_cycle_drift` attribute
4. Verify: system no longer shows "converged" with persistent undershoot
