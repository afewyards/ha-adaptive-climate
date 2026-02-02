# Fix Weighted Learning System

## Problem

Learning is broken in 3 ways, causing GF zone to have:
- 0/47 cycles with rise_time recorded
- 0 recovery cycles counted
- 0% heating_rate_contribution
- Stuck at "collecting" despite 60% confidence

## Root Causes

### 1. rise_time threshold too tight
`calculate_rise_time` uses hardcoded 0.05°C threshold, but floor_hydronic convergence tolerance is 0.3°C. Cycles within tolerance are marked as "never reached setpoint".

### 2. starting_delta never set
`starting_delta` is not passed to `CycleMetrics` constructor. Since it's always `None`:
- Recovery cycles never counted (`add_recovery_cycle` skipped)
- Weighted learning disabled (weight defaults to 1.0)
- Cycle classification (recovery vs maintenance) broken

### 3. apply_heating_rate_gain never called
Method defined but never invoked. `heating_rate_contribution` always 0%.

## Solution

### Fix 1: rise_time threshold (managers/cycle_metrics.py)

Pass heating-type-specific threshold to `calculate_rise_time`:

```python
from ..const import get_convergence_thresholds

convergence = get_convergence_thresholds(self._heating_type)
rise_threshold = convergence.get("undershoot_max", 0.05)

rise_time = calculate_rise_time(
    temperature_history,
    start_temp,
    target_temp,
    threshold=rise_threshold,
    skip_seconds=transport_delay_seconds
)
```

### Fix 2: starting_delta (managers/cycle_metrics.py)

Calculate and pass to CycleMetrics:

```python
start_temp = temperature_history[0][1]
starting_delta = target_temp - start_temp

metrics = CycleMetrics(
    # ... existing fields ...
    starting_delta=starting_delta,
)
```

### Fix 3: heating_rate_gain (adaptive/learning.py)

Call when cycle has valid rise_time:

```python
# After determining actual_gain...
if metrics.rise_time is not None:
    self._contribution_tracker.apply_heating_rate_gain(actual_gain)
```

### Fix 4: Persistence (adaptive/learner_serialization.py)

Add `starting_delta` to serialization:

```python
# _serialize_cycle
"starting_delta": cycle.starting_delta,

# _deserialize_cycle
starting_delta=cycle_dict.get("starting_delta"),
```

## Files Changed

1. `custom_components/adaptive_climate/managers/cycle_metrics.py`
2. `custom_components/adaptive_climate/adaptive/learning.py`
3. `custom_components/adaptive_climate/adaptive/learner_serialization.py`

## Testing

- Existing tests in `test_cycle_weight.py`, `test_confidence_contribution.py`
- Add tests for rise_time with heating-type thresholds
- Add tests for starting_delta flow through to weighted learning
- Add integration test verifying recovery cycles are counted
