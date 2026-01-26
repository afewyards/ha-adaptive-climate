# Plan: Minimize State Attributes

## Goal
Reduce state attribute clutter by removing purely informational attributes, consolidating pause indicators, and keeping only restoration + critical diagnostic attributes.

## Changes

### 1. Consolidate pause indicators into single `pause` attribute
**File:** `managers/state_attributes.py`

Replace `contact_paused`, `contact_open`, `contact_pause_in`, `humidity_paused`, `humidity_detection_state`, `humidity_resume_in` with:

```python
"pause": {
    "active": True,
    "reason": "contact" | "humidity" | None,
    "resume_in": 120  # seconds, optional
}
```

- Priority: contact > humidity (if both, show contact)
- `resume_in` only present when countdown active
- `pause: {"active": False, "reason": None}` when not paused

### 2. Keep critical diagnostics
- `control_output` - current PID %
- `duty_accumulator_pct` - PWM accumulation %
- `learning_status` - collecting/ready/active/converged
- `cycles_collected` - count
- `convergence_confidence_pct` - 0-100%

### 3. Remove non-critical attributes
| Attribute | Reason |
|-----------|--------|
| `duty_accumulator` | Keep only `_pct` version |
| `transport_delay` | Config-derived |
| `outdoor_temp_lag_tau` | Config value |
| `cycles_required_for_learning` | Constant (6) |
| `current_cycle_state` | Too granular |
| `last_cycle_interrupted` | Historical |
| `last_pid_adjustment` | Timestamp |
| `auto_apply_pid_enabled` | Config flag |
| `auto_apply_count` | Counter |
| `validation_mode` | Internal state |
| `ke_learning_enabled` | Config flag |
| `ke_observations` | Counter |
| `pid_converged` | Redundant w/ confidence |
| `consecutive_converged_cycles` | Internal |
| `heating_convergence_confidence` | Per-mode detail |
| `cooling_convergence_confidence` | Per-mode detail |
| `nights_until_end` | Calculated |
| `heater_control_failed` | Error flag |
| `last_heater_error` | Error msg |
| `learning_paused`, `learning_resumes` | Grace period |
| `night_setback_active`, `night_setback_delta`, `night_setback_end`, `night_setback_end_dynamic` | Informational |

### 4. Keep for restoration (unchanged)
- `kp`, `ki`, `kd`, `ke`, `pid_mode`, `pid_i`/`integral`
- `outdoor_temp_lagged`
- `heater_cycle_count`, `cooler_cycle_count`
- `pid_history`
- Preset temps (`away_temp`, `eco_temp`, etc.)

## Files to Modify

| File | Changes |
|------|---------|
| `managers/state_attributes.py` | Remove attrs, add `_build_pause_attribute()` |
| `tests/test_state_attributes.py` | Remove tests for deleted attrs, add pause tests |
| `tests/test_state_attributes_preheat.py` | May need minor updates |
| `CLAUDE.md` | Update State attributes sections |

## Tasks

1. **TEST:** Write tests for new `pause` attribute structure
2. **IMPL:** Add `_build_pause_attribute()` helper function
3. **TEST:** Update existing tests to expect `pause` instead of individual attrs
4. **IMPL:** Remove individual pause attributes, wire up consolidated `pause`
5. **IMPL:** Remove non-critical diagnostic attributes
6. **IMPL:** Remove helper functions: `_add_contact_sensor_attributes`, `_add_humidity_detector_attributes`, `_add_learning_grace_attributes`, `_add_ke_learning_attributes`, `_add_per_mode_convergence_attributes`, `_add_heater_failure_attributes`
7. **IMPL:** Simplify `_add_learning_status_attributes` to keep only: `learning_status`, `cycles_collected`, `convergence_confidence_pct`, `pid_history`
8. **VERIFY:** Run `pytest tests/test_state_attributes.py`
9. **VERIFY:** Run full `pytest`
10. **DOCS:** Update CLAUDE.md State attributes sections

## Final Attributes Structure

```python
{
    # Presets (standard)
    "away_temp", "eco_temp", "boost_temp", "comfort_temp",
    "home_temp", "sleep_temp", "activity_temp",

    # Restoration
    "ke", "pid_mode", "outdoor_temp_lagged",
    "heater_cycle_count", "cooler_cycle_count", "pid_history",

    # Critical diagnostics
    "control_output", "duty_accumulator_pct",
    "learning_status", "cycles_collected", "convergence_confidence_pct",

    # Consolidated pause
    "pause": {"active": bool, "reason": str|None, "resume_in": int|None},

    # Debug only
    "integral", "preheat_*"
}
```

## Verification
1. `pytest tests/test_state_attributes.py` - attribute tests pass
2. `pytest` - full suite passes
3. Manual: Check entity attributes in HA developer tools
