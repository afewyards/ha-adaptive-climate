# PID Auto-Tuning (`adaptive/learning.py`)

Rules adjust Kp/Ki/Kd based on overshoot, undershoot, drift, oscillations. `auto_apply_pid: true` applies changes automatically using unified learning status tiers:
- **First auto-apply:** Requires "tuned" status (tier 2 confidence threshold)
- **Subsequent auto-applies:** Require "optimized" status (tier 3 confidence threshold)
- Includes safety limits and auto-rollback on degradation
