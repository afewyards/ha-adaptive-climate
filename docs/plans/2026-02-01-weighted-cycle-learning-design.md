# Weighted Cycle Learning

## Problem

Zones reach "tuned" status prematurely by coasting at setpoint (maintenance cycles) without proving recovery capability. A floor hydronic system can show "tuned" in days without ever testing a real setback recovery.

**Root causes:**
- All cycles count equally regardless of difficulty
- Maintenance cycles (temp already at setpoint) inflate confidence
- Slow systems (floor) have blurry cycle boundaries due to thermal mass
- No incentive for system to seek learning opportunities

## Solution Overview

1. **Weight cycles by difficulty** — recovery cycles count more than maintenance
2. **Cap passive confidence** — maintenance + heating rate can't reach "tuned" alone
3. **Require recovery cycles** — tier progression needs recovery cycle counts, not just confidence %
4. **Scale by heating type** — floor uses heating rate as primary signal
5. **Auto-learning setback** — system proactively seeks learning when stuck

## Cycle Classification

### Recovery Threshold Scaling

Recovery threshold scales with learning status to avoid chicken-and-egg with `NightSetbackLearningGate`:

**Floor hydronic:**

| Learning Status | Recovery Threshold | Rationale |
|-----------------|-------------------|-----------|
| collecting | 0.5°C | Matches learning gate limit |
| stable+ | 0.8°C | Full threshold once basics proven |

**Radiator:**

| Learning Status | Recovery Threshold |
|-----------------|-------------------|
| collecting | 0.3°C |
| stable+ | 0.5°C |

**Convector / Forced air:** 0.3°C always (no scaling needed).

## Cycle Weighting Formula

Hybrid formula:

```
challenge = base_weight × delta_multiplier
cycle_weight = (challenge × outcome_factor) + bonuses
```

### Components

| Factor | Value |
|--------|-------|
| base_weight | 1.0 (recovery) or 0.3 (maintenance) |
| delta_multiplier | `1.0 + (delta - threshold) × 0.5`, capped at 2.0 |
| outcome_factor | 1.0 (clean), 0.7 (overshoot), 0.5 (undershoot) |
| duty_bonus | +0.15 if effective duty > 60% |
| outdoor_bonus | +0.15 if outdoor < 5°C |
| night_setback_bonus | +0.2 if recovery from night setback |

### Effective Duty

Subtract committed heat from peak duty:

```python
effective_duty = peak_duty - committed_heat_ratio
duty_bonus = 0.15 if effective_duty > 0.60 else 0.0
```

### Example Weights

| Scenario | Weight |
|----------|--------|
| Maintenance, clean | 0.3 |
| 1°C recovery (floor), clean | 1.1 |
| 2°C recovery, clean, cold day | 2.0 |
| 3°C night setback, overshoot | 1.75 |

## Confidence Sources by Heating Type

| Heating Type | Primary Signal | Maintenance Cap | Heating Rate Cap | Settling Window |
|--------------|----------------|-----------------|------------------|-----------------|
| floor_hydronic | Heating rate | 25% | 30% | 60 min |
| radiator | Both | 30% | 20% | 30 min |
| convector | Cycles | 35% | 10% | 15 min |
| forced_air | Cycles | 35% | 5% | 10 min |

### Maintenance Cap Behavior

After cap reached, diminishing returns at 10% rate:

```python
if maintenance_contribution < cap:
    confidence += gain
    maintenance_contribution += gain
else:
    confidence += gain * 0.1
    maintenance_contribution += gain * 0.1
```

### Heating Rate Contribution

For floor_hydronic and radiator, heating rate consistency from `PreheatLearner` contributes to confidence:

```python
if heating_type in (FLOOR_HYDRONIC, RADIATOR):
    rate_consistency = preheat_learner.get_rate_consistency_score()  # 0-1
    heating_rate_contribution = min(rate_consistency * cap, cap)
```

## Recovery Cycle Count Requirements

**Critical:** Tier progression requires RECOVERY cycles, not total cycles. Passive confidence (maintenance + heating rate) cannot bypass this.

| Heating Type | Recovery Cycles for Tier 1 (stable) | Recovery Cycles for Tier 2 (tuned) |
|--------------|-------------------------------------|-------------------------------------|
| floor_hydronic | 12 | 20 |
| radiator | 8 | 15 |
| convector | 6 | 12 |
| forced_air | 6 | 10 |

**Progression logic:**

```python
can_reach_stable = (
    confidence >= tier_1_threshold AND
    recovery_cycle_count >= required_recovery_cycles_tier_1
)
```

This ensures:
- Passive sources help build confidence but don't bypass recovery proof
- Must complete 12 recovery cycles at 0.5°C threshold to reach "stable" (floor)
- Then threshold increases to 0.8°C for remaining cycles to "tuned"

## Extended Settling Windows

Settling window starts after full heat delivery:

```python
settling_start = valve_close_time + valve_actuation_time + transport_delay
```

| Heating Type | Settling Window |
|--------------|-----------------|
| floor_hydronic | 60 min |
| radiator | 30 min |
| convector | 15 min |
| forced_air | 10 min |

This accounts for thermal mass continuing to radiate heat after valve closes.

## Auto-Learning Setback

If zone stuck at maintenance cap for 7+ days without reaching "tuned":

| Parameter | Value |
|-----------|-------|
| Trigger | 7 days at cap |
| Setback delta | 0.5°C |
| Window | 3:00-5:00am |
| Frequency | Max 1× per week |
| Continues until | Zone reaches "tuned" tier |
| Operation | Silent (visible in `status.overrides`) |

### Configuration

Domain-level, opt-out:

```yaml
adaptive_climate:
  auto_learning_setback: true  # default
```

### Skip Conditions

- Zone already has night setback configured
- Zone already at "tuned" or higher
- Last auto-setback was < 7 days ago

### State Attribute

```yaml
status:
  overrides:
    - type: auto_learning_setback
      delta: -0.5
      window: "03:00-05:00"
```

### Interaction with NightSetbackLearningGate

Auto-learning setback only activates when `NightSetbackLearningGate` would allow at least 0.5°C.

## Migration

- **Grandfather existing confidence** — current values preserved
- **New rules apply to future cycles only**
- **No retroactive recalculation**

### New Persisted Fields (v8 format)

```python
"maintenance_contribution": 0.0,
"heating_rate_contribution": 0.0,
"recovery_cycle_count": 0,
```

Missing fields default to 0.0 on restore.

## Files to Modify

| File | Changes |
|------|---------|
| `adaptive/confidence.py` | Weighted gain, caps, recovery cycle tracking |
| `adaptive/learning.py` | Cycle classification, weight computation, threshold scaling |
| `adaptive/preheat.py` | Expose `get_rate_consistency_score()` |
| `adaptive/learner_serialization.py` | v8 format with new fields |
| `managers/cycle_metrics.py` | Extended settling window timing |
| `const.py` | New constants (thresholds, caps, weights, cycle requirements) |
| `managers/state_attributes.py` | `auto_learning_setback` override type |
| `managers/night_setback_manager.py` | Auto-learning setback logic |

## Summary

| Parameter | Value |
|-----------|-------|
| Recovery threshold | Scaled by learning status (0.5→0.8°C floor, 0.3→0.5°C radiator) |
| Maintenance cap | 25-35% by type, then 10% rate |
| Heating rate cap | 5-30% by type |
| Night setback bonus | +0.2 |
| Duty bonus | +0.15 if effective duty > 60% |
| Outdoor bonus | +0.15 if outdoor < 5°C |
| Auto-setback trigger | 7 days at cap |
| Auto-setback delta | 0.5°C, 3-5am, 1×/week |
| Settling window | 10-60 min by type, from heat delivery end |
| Recovery cycles for tuned | 10-20 by type |
