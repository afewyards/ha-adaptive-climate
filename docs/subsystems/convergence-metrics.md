# Convergence Metrics

All must pass for steady-state: `inter_cycle_drift`, `settling_mae`, `undershoot`. Thresholds scale by heating type (floor=0.3°C → forced_air=0.15°C).

`undershoot` is how far below target the room was at or after its highest reading in the cycle. Readings before that peak are ignored, so a cold start does not count once the room has warmed up; a cycle that never rises keeps the gap it started with. A dip before the peak (a maintenance cycle that sags during floor dead time, then recovers past target) is not counted as undershoot either. Overshoot, controllable overshoot, undershoot, starting delta and inter-cycle drift are rounded to 6 decimals when measured. Stored learner data from before this (format_version 11) is migrated once: heating cycles whose undershoot reaches or exceeds their starting delta get `undershoot = None` (unknown), because that value was the cold start (plus any dead-time sag below it), not the end of the cycle.
