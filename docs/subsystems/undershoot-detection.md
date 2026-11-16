# Undershoot Detection

Unified detector with dual modes: real-time thermal debt tracking and cycle-based consecutive failure detection. Adjusts Ki when system persistently underperforms. Module: `adaptive/undershoot_detector.py` (`UndershootDetector`).

Both modes judge only what more Ki could fix.

**Real-time mode:** Tracks accumulated time × temp deficit below setpoint (thermal debt). Triggers when debt exceeds threshold scaled by thermal mass. The setpoint is the effective target the PID controls to (night setback, contact-sensor clamp, cooling clamp applied), not the base target, so a room in setback or with a window open is not "undershooting". While the output is saturated (at or above 95% of the PID output maximum, heat mode only) no time or debt is added; the exponential decay and the full reset above target still run.

**Cycle mode:** Detects consecutive cycles that fail to reach setpoint (`rise_time = None`, `overshoot = None`, `undershoot >= threshold`, `duration >= min_duration`). Resets counter on successful cycles. Cycle `undershoot` is measured from the cycle's highest reading onward (see convergence-metrics), so a cycle that rose but stopped short counts the gap left after its best point, not the gap it started with. A cycle that crossed target at all has an overshoot value and is never a failure, so ignoring a late peak (convergence-metrics) cannot create one. A cycle whose output was saturated for at least half of its samples is not a failure either; if it reached target cleanly it still clears the streak like any clean cycle.

Each cycle stores `output_saturated`: the share (0-1) of its temperature samples taken with the output saturated. It is `null` for cycles from before this field; those are judged as before.

**Thresholds by heating type:**
| Type | min_cycles | undershoot | min_duration | ki_multiplier | thermal_debt_threshold |
|------|------------|------------|--------------|---------------|------------------------|
| floor_hydronic | 4 | 0.4°C | 60 min | 1.20 | 150.0°C·min |
| radiator | 3 | 0.35°C | 30 min | 1.25 | 90.0°C·min |
| convector | 3 | 0.30°C | 20 min | 1.30 | 60.0°C·min |
| forced_air | 2 | 0.25°C | 10 min | 1.35 | 30.0°C·min |

**Behavior:**
- Applies Ki boost when pattern detected (respects cooldown and cumulative cap)
- Cooldown scales with thermal mass (floor: 24h, radiator: 12h, convector: 6h, forced_air: 3h)
- Cumulative multiplier capped at 3.0x to prevent runaway integral gain
- Ki is also capped at 3.0x the physics Ki: computed fresh from the zone config (the same value as `gains_physics` on the learning attribute) and, while the gain history still holds one, the last `physics_init`/`physics_reset` entry; the lower baseline wins. Each boost is clamped by both the physics ratio and the cumulative multiplier. Without a room size there is no physics Ki and the cumulative multiplier is the only cap
- Decreases convergence confidence on adjustment (similar to poor cycles)
- Restarts the auto-apply cooldown (`mark_adjustment_applied`), so the next cycle end cannot auto-apply another Ki increase on the same cycles
- No boost while an auto-apply is in validation, so a rollback restores the gains from before the apply
- Heating-rate sessions are measurement only (the card's `heating_rate`); they no longer boost Ki

**Integration:**
- Detector initialized in `AdaptiveLearner.__init__`
- Real-time updates via `update_realtime` (called from `_async_control_heating` with the effective target and `_output_saturated`)
- Cycle updates via `add_cycle_metrics`; saturation is sampled per cycle in `managers/cycle_samples.py`
- Serialized/restored via `learner_serialization.py` (format_version 12). Cycles with `undershoot = None` never count as failures
