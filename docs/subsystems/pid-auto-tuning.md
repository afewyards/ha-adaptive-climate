# PID Auto-Tuning (`adaptive/learning.py`)

Rules adjust Kp/Ki/Kd based on overshoot, undershoot, drift, oscillations. `auto_apply_pid: true` applies changes automatically. Learning status is not a gate: confidence only rises once the gains are good, so waiting for "tuned" would lock tuning out. Gates that apply:
- Not in validation mode, safety limits (lifetime, seasonal, drift), no seasonal shift block
- A physics baseline is set (zones without `area_m2` get none, so they never auto-apply; manual apply still works)
- `min_cycles` of history per heating type (doubled after the first apply; history is cleared on every apply)
- Cooldown hours and cooldown cycles since the last adjustment. Undershoot, chronic-approach and physics-rate Ki boosts count as adjustments, so an auto-apply cannot stack on a boost. No Ki boost runs during validation, so one cannot stack on an apply either
- Auto-rollback on degradation during validation: after 5 cycles, it fails when the average overshoot is above both baseline × 1.3 (`VALIDATION_DEGRADATION_THRESHOLD`) and the heating type's `overshoot_max`, so normal overshoot after a baseline with none does not roll back

**Step size:** the confidence learning-rate multiplier (up to 2.0x at low confidence) is capped at 1.0 for auto-apply, so one apply moves the gains by the plain rule factor (undershoot 0.5°C: Ki ×2). Manual recommendations keep the multiplier. Each auto-applied gain is clipped to within 50% (`MAX_CUMULATIVE_DRIFT_PCT`) of its physics baseline (floor at baseline, undershoot 0.5°C: Ki ×1.5, not ×2). If the clipped gains equal the current ones, the apply is skipped (`no_change`). Manual recommendations are not clipped.

**On apply:** the PID integral is kept (it is stored in output units, so a new Ki only changes how it grows from here). Only the applied mode's cycle history, confidence and contribution are cleared; the other mode keeps its learning. The cleared history does not switch night setback off (see night-setback-learning-gate).

**Integral-decay safety net:** the PID controller decays a wound-up integral near setpoint until the zone is auto-tuned. "Auto-tuned" follows learning status, not the apply count: the controller sees the auto-apply count only while status is `tuned` or `optimized`, else 0. It is synced on startup, after each apply (status is `collecting` again, so the net is on) and every control loop, so the net comes back when confidence drops.

**On rollback:** the previous gains are restored and the integral is kept (zeroing it would drop the output by the whole heat demand). The mode's cycle history (cycles run under the rolled-back gains) is cleared and the cooldown restarts.
