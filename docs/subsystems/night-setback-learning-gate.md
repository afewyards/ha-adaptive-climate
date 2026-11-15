# Night Setback Learning Gate

Night setback delta is graduated based on learning progress, allowing partial setback early to accelerate learning while protecting convergence metrics.

**Graduated Delta:**
- `cycles < 3` and no auto-apply yet, or "idle": fully suppressed (0°C applied)
- `cycles >= 3` (or at least one auto-apply) + "collecting": 0.5°C allowed. An auto-apply clears the cycle history, so the auto-apply count keeps setback on afterwards
- "stable": 1.0°C allowed
- "tuned"/"optimized": full configured delta

**Status attribute:**
- `suppressed_reason: "learning"` when fully suppressed (0°C)
- `suppressed_reason: "limited"` when capped below configured delta
- `allowed_setback: X.X` shows effective delta when limited

**Preheat scaling:**
- Preheat timing scales with effective delta (not configured delta)
- Early setback cycles provide preheat observations that accelerate learning

**Rationale:** Graduated approach balances two goals: (1) protect initial PID learning from large setpoint swings, (2) gather early setback/preheat data to accelerate convergence. Partial setback is safe once basic cycle metrics stabilize.
