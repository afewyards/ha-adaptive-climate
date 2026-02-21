# Learning Subsystem Simplification

**Date:** 2026-02-21
**Status:** Approved

## Problem

The adaptive learning subsystem spans ~10,500 lines across 20+ modules with:
- `AdaptiveLearner` god class (1,659 lines, 63 methods)
- 6 concrete duplications across modules
- 3 uncoordinated Ki boost paths
- 3 heating rate learners (2 dead/redundant)
- 16+ scattered heating-type config dicts
- Learning status computed in 4 separate locations

## Design

### 1. HeatingTypeProfile (Foundation)

Frozen `@dataclass` in `adaptive/heating_type_profile.py`. Single source of truth for ALL per-heating-type constants currently scattered across 16+ dicts.

Fields: `pid_modifier`, `pwm_seconds`, `settling_mae_threshold`, `drift_threshold`, `undershoot_threshold`, `settling_window_minutes`, `tier1_threshold`, `tier2_threshold`, `tier3_threshold`, `maintenance_confidence_cap`, `heating_rate_confidence_cap`, `recovery_cycles_stable`, `recovery_cycles_tuned`, `undershoot_min_cycles`, `undershoot_min_duration_min`, `ki_multiplier`, `thermal_debt_threshold`, `undershoot_cooldown_hours`, `preheat_fallback_rate`, `cold_soak_margin`, `max_preheat_hours`, `default_valve_actuation_seconds`, `setpoint_boost_factor`, `setpoint_decay_rate`, `recovery_delta_threshold`.

Registry: `PROFILES: dict[HeatingType, HeatingTypeProfile]` at module level. Access via `HeatingTypeProfile.for_type(ht)`.

All modules currently using per-type dicts get migrated to `profile.field_name`.

### 2. Dead Code Removal

| Target | Location | Reason |
|--------|----------|--------|
| `ThermalRateLearner` | `adaptive/thermal_rates.py` | Never injected in production |
| `RollingWindowHeatingRate` | `adaptive/physics.py` | Never instantiated |
| `HeatingRateLearner.should_boost_ki()` | `adaptive/heating_rate_learner.py` | Caller has zero callers |
| `UndershootDetector.check_rate_based_undershoot()` | `adaptive/undershoot_detector.py` | Zero callers |
| `ConfidenceTracker.update_convergence_confidence()` | `adaptive/confidence.py` | Real version in learning.py |
| Physics Ki boost path (path 3) | `climate.py:1462-1510` | Redundant with UndershootDetector paths 1+2 |
| `HeatingRateLearner.check_physics_underperformance()` | `adaptive/heating_rate_learner.py` | Only caller was path 3 |
| `PreheatLearner._observations` + internal bin logic | `adaptive/preheat.py` | Delegates to HeatingRateLearner in prod |
| `NightSetback._get_heating_rate()` ThermalRateLearner path | `adaptive/night_setback.py` | Dead path |
| 21 thin delegators on `AdaptiveLearner` | `adaptive/learning.py` | Callers reference sub-managers |
| Duplicate Pearson correlation | `adaptive/ke_learning.py` | Use shared utility |
| Duplicate MAD calculation | `managers/cycle_metrics.py` | Use robust_stats.py |

### 3. AdaptiveLearner Decomposition (3 Managers + Orchestrator)

#### PIDTuningManager
**Owns:** cycle history storage (heating/cooling), metric averaging, rule evaluation, rate limiting, convergence thresholds.

**Absorbs from AdaptiveLearner:**
- `_heating_cycle_history`, `_cooling_cycle_history`, `_max_history`
- `_rule_state_tracker`, `_convergence_thresholds`, `_rule_thresholds`
- `_last_adjustment_time`, `_cycles_since_last_adjustment`
- `add_cycle_metrics()` (cycle storage part)
- `calculate_pid_adjustment()` full pipeline
- `_check_rate_limit()`, `_check_convergence()`

**Key type:** `AveragedCycleMetrics` dataclass — extracted from the 118-line averaging block.

**Interface:**
```python
add_cycle(mode, metrics) -> None
calculate_adjustment(mode, gains, learning_rate, pwm_seconds) -> PIDAdjustment | None
get_cycle_count(mode) -> int
```

#### ConfidenceManager
**Absorbs:**
- `ConfidenceTracker` (data holder)
- `update_convergence_confidence()` from learning.py (144-line real implementation)
- `ConfidenceContributionTracker` (caps)
- Ke convergence gate (`_consecutive_converged_cycles`, `_pid_converged_for_ke`)
- Learning status computation (currently duplicated in auto_apply.py and learning_gate.py)

**Interface:**
```python
update(cycle_weight, cycle_metrics, mode) -> LearningStatus
get_confidence() -> float
get_learning_status() -> LearningStatus  # single source of truth
apply_decay(factor) -> None
is_converged_for_ke() -> bool
update_ke_convergence(converged: bool) -> None
```

`compute_learning_status()` exposed as module-level function for import by learning_gate.py.

#### ValidationSafetyManager
**Merges:** `ValidationManager` + `AutoApplyManager`.

**Interface:**
```python
should_auto_apply(confidence, status, adjustment) -> bool
record_auto_apply(gains_before, gains_after) -> None
check_degradation(current_metrics, baseline) -> bool
start_validation(baseline_gains) -> None
add_validation_cycle(metrics) -> None
is_in_validation() -> bool
```

#### AdaptiveLearner (Thin Orchestrator, ~200 lines)
**Owns references to:** PIDTuningManager, ConfidenceManager, ValidationSafetyManager, UndershootDetector, HeatingRateLearner, PreheatLearner.

**Callers in climate.py/climate_control.py keep calling AdaptiveLearner** — its methods become 1-3 line delegations:

```python
def add_cycle_metrics(self, mode, metrics):
    self._tuning.add_cycle(mode, metrics)
    weight = self._weight_calculator.calculate(metrics, ...)
    self._confidence.update(weight, metrics, mode)
    self._undershoot_detector.add_cycle(metrics)

def calculate_pid_adjustment(self, mode, gains, pwm):
    lr = self._confidence.get_learning_rate_multiplier()
    return self._tuning.calculate_adjustment(mode, gains, lr, pwm)
```

### 4. Shared Utilities

- Extend `robust_stats.py` with `pearson_correlation()` — replace duplicates in `pid_rules.py` and `ke_learning.py`
- MAD calculation: `cycle_metrics.py` imports from `robust_stats.py` instead of reimplementing

### 5. Migration Strategy

Each step independently testable against existing test suite:

1. **HeatingTypeProfile** — no behavioral change, config consolidation only
2. **Dead code removal** — delete dead/redundant code identified above
3. **Shared utilities** — extract Pearson correlation and MAD
4. **ConfidenceManager** — move logic, update imports, deduplicate learning status
5. **PIDTuningManager** — move cycle history + rule evaluation
6. **ValidationSafetyManager** — merge existing ValidationManager + AutoApplyManager
7. **Slim AdaptiveLearner** — wire orchestrator to new managers
8. **Serialization v11** — bump format with backward-compat v10 reader

### 6. Unresolved Questions

None — all design decisions resolved during brainstorming.

## Expected Outcome

- ~2,000 lines deleted (dead code + duplication)
- ~1,500 lines restructured across new managers
- `AdaptiveLearner` shrinks from 1,659 to ~200 lines
- Single source of truth for: heating type config, learning status, confidence, Ki boosting
- `calculate_pid_adjustment()` shrinks from 334 to ~25 lines (pipeline of composed steps)
- All existing tests continue to pass (behavioral equivalence)
