# Undershoot Detection

Unified detector with dual modes: real-time thermal debt tracking and cycle-based consecutive failure detection. Adjusts Ki when system persistently underperforms. Module: `adaptive/undershoot_detector.py` (`UndershootDetector`).

**Real-time mode:** Tracks accumulated time × temp deficit below setpoint (thermal debt). Triggers when debt exceeds threshold scaled by thermal mass.

**Cycle mode:** Detects consecutive cycles that fail to reach setpoint (`rise_time = None`, `undershoot >= threshold`, `duration >= min_duration`). Resets counter on successful cycles.

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
- Decreases convergence confidence on adjustment (similar to poor cycles)

**Integration:**
- Detector initialized in `AdaptiveLearner.__init__`
- Real-time updates via `update_real_time` (called from `_async_control_heating`)
- Cycle updates via `add_cycle_metrics`
- Serialized/restored via `learner_serialization.py` (v10 format)
