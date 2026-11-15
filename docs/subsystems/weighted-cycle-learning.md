# Weighted Cycle Learning

Cycles are weighted by difficulty to prevent premature "tuned" status from maintenance-only operation.

**Cycle classification:**
- Recovery: starting delta ≥ threshold (0.5-0.8°C depending on type/status)
- Maintenance: starting delta < threshold

**Weight formula:**
```
weight = (base × delta_multiplier × outcome_factor) + bonuses
```

- Base: 1.0 (recovery) or 0.3 (maintenance)
- Delta multiplier: 1.0 + (delta - threshold) × 0.5, capped at 2.0
- Outcome factor: 1.0 (clean), 0.7 (overshoot), 0.5 (undershoot)
- Bonuses: +0.15 (duty >60%), +0.15 (outdoor <5°C), +0.2 (night setback recovery)

**Confidence caps by heating type:**
| Type | Maintenance Cap | Heating Rate Cap |
|------|-----------------|------------------|
| floor_hydronic | 25% | 30% |
| radiator | 30% | 20% |
| convector | 35% | 10% |
| forced_air | 35% | 5% |

**Recovery cycle requirements:**
- Tier progression requires recovery cycles, not just confidence %
- floor_hydronic: 12 for stable, 20 for tuned
- radiator: 8 for stable, 15 for tuned
- convector: 6 for stable, 12 for tuned
- forced_air: 6 for stable, 10 for tuned

**Auto-learning setback:**
- Triggers after 7 days stuck at maintenance cap
- Applies 0.5°C setback during 3-5am window
- 7-day cooldown between activations
- Disabled if user has night setback configured
- Domain config: `auto_learning_setback: true` (default)

**Extended settling windows:**
| Type | Settling Window |
|------|-----------------|
| floor_hydronic | 60 min |
| radiator | 30 min |
| convector | 15 min |
| forced_air | 10 min |
