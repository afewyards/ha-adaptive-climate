# Multi-PWM Cycle Aggregation for Floor Hydronic

**Date:** 2026-02-19
**Status:** Approved

## Problem

Each PWM off-period emits SETTLING_STARTED (v0.28 fix in `heater_controller.py`), creating one learning cycle per PWM pulse (~15min for floor_hydronic). Floor hydronic thermal lag is 1-3+ hours, so cycles finalize before the floor delivers heat. Result: `rise_time=None` on every cycle, confidence stuck at zero despite system at setpoint.

**Root cause:** Two contradictory designs in `heater_controller.py`:
- **v0.21** (`async_set_control_value`): Session boundary — SETTLING_STARTED when `control_output` drops to 0. Groups PWM pulses.
- **v0.28** (`async_turn_off`): PWM-off emission — SETTLING_STARTED on every heater turn-off. Added to fix maintenance cycles that never see demand=0, but destroys grouping.

## Changes

### 1. Remove v0.28 SETTLING_STARTED from `async_turn_off()` (heater_controller.py)

Delete the SETTLING_STARTED emission from `async_turn_off()` (lines ~745-754). PWM off-periods no longer terminate learning cycles.

### 2. Debounce demand→0 transition (heater_controller.py)

Replace immediate SETTLING_STARTED in `async_set_control_value()` with debounced version:
- demand → 0: start debounce timer (`2 × pwm_period`)
- demand returns > 0 before timer: cancel timer, cycle continues
- timer expires: emit SETTLING_STARTED

Handles: PWM off-periods (demand stays >0), brief demand dips between consecutive sessions (debounced), true end of heating (timer fires).

Debounce values (auto from PWM period):

| Type | PWM period | Debounce (2×PWM) |
|------|-----------|------------------|
| floor_hydronic | 15 min | 30 min |
| radiator | 10 min | 20 min |
| convector | 5 min | 10 min |
| forced_air | 3 min | 6 min |

Custom PWM periods scale automatically (e.g. 40min → 80min debounce).

### 3. Low-output maintenance timeout (heater_controller.py)

For maintenance cycles where output hovers at 2-5% and never reaches 0: if `control_output` below `min_output_threshold` (2%) for `2 × pwm_period`, emit SETTLING_STARTED. Replaces v0.28 behavior for the case it was designed to fix.

### 4. Relax rise_time evaluation for recovery cycles (adaptive/learning.py)

Change rise_time check for recovery cycles from:
```python
rise_time is not None and rise_time <= rise_time_max
```
to:
```python
rise_time is not None  # just require system reached setpoint
```

Maintenance cycles keep existing behavior (`rise_time=None` acceptable).

## Unchanged

- Settling detection logic (MAD < 0.05, within 0.5C of target)
- Settling timeout (`max(60, min(240, tau * 30))`)
- Other convergence thresholds (overshoot, undershoot, oscillations, settling_time)
- Cycle metrics calculation
- Confidence weighting, contribution tracker, tier gates

## Files affected

- `managers/heater_controller.py` — remove v0.28 emission, add debounce timer, add low-output timeout
- `adaptive/learning.py` — relax rise_time check for recovery cycles
- `const.py` — add MIN_OUTPUT_THRESHOLD constant if needed
- Tests for cycle tracker, heater controller, learning confidence evaluation
