# Learning Subsystem Simplification — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify the adaptive learning subsystem by removing dead code, consolidating heating-type config, and decomposing the AdaptiveLearner god class into 3 focused managers.

**Architecture:** HeatingTypeProfile frozen dataclass replaces 25+ scattered per-type config dicts. AdaptiveLearner (1,659 lines) decomposes into PIDTuningManager (cycle history + rule evaluation), ConfidenceManager (confidence + status + Ke gate), ValidationSafetyManager (auto-apply + validation). AdaptiveLearner becomes ~200-line thin orchestrator. Serialization bumps to v11 with v10 backward compat.

**Tech Stack:** Python 3.12, pytest, Home Assistant core types, dataclasses

**Base path:** `custom_components/adaptive_climate` (all paths relative unless noted)

---

## Phase 1: HeatingTypeProfile

### Task 1: Create HeatingTypeProfile dataclass + tests

**Files:**
- Create: `adaptive/heating_type_profile.py`
- Create: `tests/test_heating_type_profile.py`

**Step 1: Write failing tests**

```python
# tests/test_heating_type_profile.py
import pytest
from custom_components.adaptive_climate.adaptive.heating_type_profile import (
    HeatingTypeProfile,
)
from custom_components.adaptive_climate.const import HeatingType


class TestHeatingTypeProfile:
    def test_for_type_returns_profile(self):
        p = HeatingTypeProfile.for_type(HeatingType.FLOOR_HYDRONIC)
        assert isinstance(p, HeatingTypeProfile)
        assert p.heating_type == HeatingType.FLOOR_HYDRONIC

    def test_all_types_have_profiles(self):
        for ht in HeatingType:
            p = HeatingTypeProfile.for_type(ht)
            assert p.heating_type == ht

    def test_frozen(self):
        p = HeatingTypeProfile.for_type(HeatingType.RADIATOR)
        with pytest.raises(AttributeError):
            p.pid_modifier = 999

    def test_floor_hydronic_values_match_const(self):
        """Verify profile values match current scattered constants."""
        from custom_components.adaptive_climate.const import (
            HEATING_TYPE_CHARACTERISTICS,
            HEATING_TYPE_CONVERGENCE_THRESHOLDS,
            HEATING_TYPE_PREHEAT_CONFIG,
            HEATING_TYPE_VALVE_DEFAULTS,
            HEATING_TYPE_BOOST_FACTORS,
            HEATING_TYPE_CONFIDENCE_SCALE,
            UNDERSHOOT_THRESHOLDS,
            SETTLING_WINDOW_MINUTES,
            MAINTENANCE_CONFIDENCE_CAP,
            HEATING_RATE_CONFIDENCE_CAP,
            RECOVERY_CYCLES_FOR_TIER1,
            RECOVERY_CYCLES_FOR_TIER2,
            RECOVERY_THRESHOLD_COLLECTING,
            CLAMPED_OVERSHOOT_MULTIPLIER,
        )

        ht = HeatingType.FLOOR_HYDRONIC
        p = HeatingTypeProfile.for_type(ht)
        chars = HEATING_TYPE_CHARACTERISTICS[ht]

        assert p.pid_modifier == chars["pid_modifier"]
        assert p.pwm_seconds == chars["pwm_period"]
        assert p.settling_window_minutes == SETTLING_WINDOW_MINUTES[ht]
        assert p.tier1_threshold == pytest.approx(0.4 * HEATING_TYPE_CONFIDENCE_SCALE[ht])
        assert p.tier2_threshold == pytest.approx(0.7 * HEATING_TYPE_CONFIDENCE_SCALE[ht])
        assert p.maintenance_confidence_cap == MAINTENANCE_CONFIDENCE_CAP[ht]
        assert p.heating_rate_confidence_cap == HEATING_RATE_CONFIDENCE_CAP[ht]
        assert p.recovery_cycles_stable == RECOVERY_CYCLES_FOR_TIER1[ht]
        assert p.recovery_cycles_tuned == RECOVERY_CYCLES_FOR_TIER2[ht]
        assert p.default_valve_actuation_seconds == HEATING_TYPE_VALVE_DEFAULTS[ht]
        assert p.recovery_delta_threshold_collecting == RECOVERY_THRESHOLD_COLLECTING[ht]
        assert p.clamped_overshoot_multiplier == CLAMPED_OVERSHOOT_MULTIPLIER[ht]

        conv = HEATING_TYPE_CONVERGENCE_THRESHOLDS[ht]
        assert p.settling_mae_threshold == conv["settling_mae"]
        assert p.drift_threshold == conv["inter_cycle_drift"]
        assert p.convergence_undershoot_threshold == conv["undershoot"]

        us = UNDERSHOOT_THRESHOLDS[ht]
        assert p.undershoot_min_cycles == us["min_consecutive_cycles"]
        assert p.ki_multiplier == us["ki_multiplier"]
        assert p.thermal_debt_threshold == us["debt_threshold"]
        assert p.undershoot_cooldown_hours == us["cooldown_hours"]

        pre = HEATING_TYPE_PREHEAT_CONFIG[ht]
        assert p.preheat_fallback_rate == pre["fallback_rate"]
        assert p.cold_soak_margin == pre["cold_soak_margin"]
        assert p.max_preheat_hours == pre["max_hours"]

        boost = HEATING_TYPE_BOOST_FACTORS[ht]
        assert p.setpoint_boost_factor == boost[0]
        assert p.setpoint_decay_rate == boost[1]

    def test_unknown_type_raises(self):
        with pytest.raises(KeyError):
            HeatingTypeProfile.for_type("nonexistent")
```

**Step 2: Run tests, verify failure**

Run: `pytest tests/test_heating_type_profile.py -v`
Expected: ImportError (module doesn't exist)

**Step 3: Implement HeatingTypeProfile**

Create `adaptive/heating_type_profile.py`. Populate all fields by reading current values from the scattered const.py dicts. Use `HEATING_TYPE_CHARACTERISTICS`, `HEATING_TYPE_CONVERGENCE_THRESHOLDS`, `UNDERSHOOT_THRESHOLDS`, `HEATING_TYPE_PREHEAT_CONFIG`, `HEATING_TYPE_VALVE_DEFAULTS`, `HEATING_TYPE_BOOST_FACTORS`, `HEATING_TYPE_CONFIDENCE_SCALE`, `SETTLING_WINDOW_MINUTES`, `MAINTENANCE_CONFIDENCE_CAP`, `HEATING_RATE_CONFIDENCE_CAP`, `RECOVERY_CYCLES_FOR_TIER1`, `RECOVERY_CYCLES_FOR_TIER2`, `RECOVERY_THRESHOLD_COLLECTING`, `RECOVERY_THRESHOLD_STABLE`, `CLAMPED_OVERSHOOT_MULTIPLIER`, `HEATING_TYPE_INTEGRAL_DECAY`, `HEATING_TYPE_EXP_DECAY_TAU`, `AUTO_APPLY_THRESHOLDS`.

Key: include ALL fields from the design doc plus these extras found during research:
- `clamped_overshoot_multiplier` (from `CLAMPED_OVERSHOOT_MULTIPLIER`)
- `integral_decay_multiplier` (from `HEATING_TYPE_INTEGRAL_DECAY`)
- `exp_decay_tau` (from `HEATING_TYPE_EXP_DECAY_TAU`)
- `auto_apply_min_cycles`, `auto_apply_cooldown_hours`, `auto_apply_cooldown_cycles` (from `AUTO_APPLY_THRESHOLDS`)
- `recovery_delta_threshold_collecting` (from `RECOVERY_THRESHOLD_COLLECTING`)
- `recovery_delta_threshold_stable` (from `RECOVERY_THRESHOLD_STABLE`)
- `convergence_undershoot_threshold` (from convergence thresholds, distinct from undershoot detector threshold)
- `min_session_duration_min` (from `heating_rate_learner.py:MIN_SESSION_DURATION`)
- `fallback_heating_rate` (from `heating_rate_learner.py:FALLBACK_RATES`)
- `undershoot_min_duration_min` (from `UNDERSHOOT_THRESHOLDS[ht]["min_cycle_duration"]`)
- `undershoot_detection_threshold` (from `UNDERSHOOT_THRESHOLDS[ht]["undershoot_threshold"]`)
- `time_threshold_hours` (from `UNDERSHOOT_THRESHOLDS[ht]["time_threshold_hours"]`)

Compute tier thresholds: `tier1 = 0.4 * scale`, `tier2 = 0.7 * scale`, `tier3 = min(0.95, 0.95 * scale)`.

**Step 4: Run tests, verify pass**

Run: `pytest tests/test_heating_type_profile.py -v`
Expected: All PASS

**Step 5: Run full suite to verify no regressions**

Run: `pytest --tb=short -q`
Expected: All existing tests pass (new module, no consumers yet)

**Step 6: Commit**

```
feat(learning): add HeatingTypeProfile dataclass

Single source of truth for all per-heating-type constants.
```

---

### Task 2: Migrate adaptive/ consumers to HeatingTypeProfile (batch 1)

**Files:**
- Modify: `adaptive/undershoot_detector.py` — replace `UNDERSHOOT_THRESHOLDS[ht]` lookups with `profile.field`
- Modify: `adaptive/confidence_contribution.py` — replace `MAINTENANCE_CONFIDENCE_CAP[ht]`, `HEATING_RATE_CONFIDENCE_CAP[ht]`, `RECOVERY_CYCLES_FOR_TIER1[ht]`, `RECOVERY_CYCLES_FOR_TIER2[ht]`
- Modify: `adaptive/cycle_weight.py` — replace `RECOVERY_THRESHOLD_COLLECTING[ht]`, `RECOVERY_THRESHOLD_STABLE[ht]`
- Modify: `adaptive/preheat.py` — replace `HEATING_TYPE_PREHEAT_CONFIG[ht]`
- Modify: `adaptive/heating_rate_learner.py` — replace `MIN_SESSION_DURATION`, `FALLBACK_RATES`

**Step 1: For each file**, replace dict lookups with `HeatingTypeProfile.for_type(self._heating_type).field_name`. Add import. Remove unused const imports.

Pattern — before:
```python
from ..const import UNDERSHOOT_THRESHOLDS
thresholds = UNDERSHOOT_THRESHOLDS[self._heating_type]
cooldown = thresholds["cooldown_hours"]
```

After:
```python
from .heating_type_profile import HeatingTypeProfile
profile = HeatingTypeProfile.for_type(self._heating_type)
cooldown = profile.undershoot_cooldown_hours
```

Cache `profile` as `self._profile` in `__init__` where the class stores `self._heating_type`.

**Step 2: Run affected tests**

Run: `pytest tests/test_undershoot_detector.py tests/test_confidence_contribution.py tests/test_cycle_weight.py tests/test_preheat_learner.py tests/test_integration_undershoot.py -v`
Expected: All PASS

**Step 3: Run full suite**

Run: `pytest --tb=short -q`
Expected: All PASS

**Step 4: Commit**

```
refactor(learning): migrate adaptive/ to HeatingTypeProfile (batch 1)

undershoot_detector, confidence_contribution, cycle_weight,
preheat, heating_rate_learner now use profile instead of dicts.
```

---

### Task 3: Migrate managers/ + other consumers to HeatingTypeProfile (batch 2)

**Files:**
- Modify: `managers/cycle_metrics.py` — replace `SETTLING_WINDOW_MINUTES[ht]`
- Modify: `managers/setpoint_boost.py` — replace `HEATING_TYPE_BOOST_FACTORS[ht]`
- Modify: `managers/learning_gate.py` — replace `HEATING_TYPE_CONFIDENCE_SCALE[ht]` and tier computation
- Modify: `adaptive/learning.py` — replace `CLAMPED_OVERSHOOT_MULTIPLIER[ht]`, convergence threshold lookups
- Modify: `managers/auto_apply.py` — replace `AUTO_APPLY_THRESHOLDS[ht]`, confidence scale lookups

**Step 1: Same pattern as Task 2.** Replace dict lookups → profile field access.

For `learning_gate.py` and `auto_apply.py`, the tier computation (`base_threshold * confidence_scale`) is replaced by `profile.tier1_threshold`, `profile.tier2_threshold` etc. — pre-computed.

**Step 2: Run affected tests**

Run: `pytest tests/test_learning.py tests/test_learning_gate_manager.py tests/test_auto_apply.py tests/test_integration_auto_apply.py tests/test_cycle_metrics.py tests/test_setpoint_boost.py -v --tb=short`
Expected: All PASS

**Step 3: Run full suite**

Run: `pytest --tb=short -q`

**Step 4: Commit**

```
refactor(learning): migrate managers/ to HeatingTypeProfile (batch 2)

cycle_metrics, setpoint_boost, learning_gate, auto_apply,
and learning.py now use profile instead of dicts.
```

---

### Task 4: Migrate remaining consumers + delete old dicts

**Files:**
- Modify: `climate.py` — replace `HEATING_TYPE_INTEGRAL_DECAY[ht]`
- Modify: `pid_controller/__init__.py` — replace `HEATING_TYPE_EXP_DECAY_TAU`, fallback dicts
- Modify: `adaptive/physics.py` — replace `EXPECTED_HEATING_RATES` type-keyed lookups where applicable
- Modify: `const.py` — delete all migrated dicts (keep `HEATING_TYPE_CHARACTERISTICS` temporarily if `climate_setup.py` still reads it for schema validation; only delete dicts that have zero remaining consumers)

**Step 1:** Migrate remaining lookups. For const.py, grep each dict name. If zero consumers remain (after tasks 2-3), delete the dict. If consumers remain outside adaptive_climate (e.g., tests importing from const), update those imports too.

**Note:** `HEATING_TYPE_CHARACTERISTICS` may still be needed by `climate_setup.py` for config schema validation (pwm_period, min_cycle_time). If so, keep it but mark with `# TODO: migrate to HeatingTypeProfile`. Don't force it — schema validation has different lifecycle.

**Step 2: Run full suite**

Run: `pytest --tb=short -q`

**Step 3: Commit**

```
refactor(learning): complete HeatingTypeProfile migration, delete old dicts

Remaining consumers migrated. Unused per-type dicts removed from const.py.
```

---

## Phase 2: Dead Code Removal

### Task 5: Delete dead heating rate code

**Files:**
- Delete: `adaptive/thermal_rates.py` (entire file — `ThermalRateLearner`, dead)
- Modify: `adaptive/physics.py` — delete `RollingWindowHeatingRate` class
- Modify: `adaptive/night_setback.py` — remove `thermal_rate_learner` param from `NightSetback.__init__`, remove `_get_heating_rate()` ThermalRateLearner fallback path
- Modify: `adaptive/preheat.py` — make `heating_rate_learner` required param (not optional), delete internal `_observations` dict, `add_observation()`, `_expire_old_observations()`, `get_delta_bin()`, `get_outdoor_bin()`, `get_learned_rate()`, `get_rate_consistency_score()` (internal bin methods only — keep delegating methods). Delete `to_dict()`/`from_dict()` observation parts.

**Step 1:** Delete `thermal_rates.py`. Remove imports from `__init__.py` if any.

**Step 2:** In `physics.py`, find `RollingWindowHeatingRate` class, delete it entirely.

**Step 3:** In `night_setback.py`, remove `thermal_rate_learner` from `__init__` signature and body. Simplify `_get_heating_rate()` to only use hardcoded fallback rates (or wire to `HeatingRateLearner` via a callback — check current fallback path).

**Step 4:** In `preheat.py`, make `heating_rate_learner` required. Delete all internal observation storage code. Keep `estimate_time_to_target()` (which delegates to `heating_rate_learner.get_heating_rate()`), `cold_soak_margin`, `max_hours`, `fallback_rate` config.

**Step 5: Run tests**

Run: `pytest tests/test_preheat_learner.py tests/test_night_setback.py tests/test_integration_night_setback_lifecycle.py tests/test_integration_heating_rate.py -v --tb=short`
Expected: Some tests may fail if they instantiate `PreheatLearner` without `heating_rate_learner` — fix those by providing a mock. Delete tests that test the removed internal bin methods.

**Step 6: Run full suite**

Run: `pytest --tb=short -q`

**Step 7: Commit**

```
refactor(learning): delete dead heating rate code

Remove ThermalRateLearner, RollingWindowHeatingRate,
PreheatLearner internal bins (delegates to HeatingRateLearner).
```

---

### Task 6: Delete dead Ki boost + confidence code

**Files:**
- Modify: `adaptive/heating_rate_learner.py` — delete `should_boost_ki()`, `check_physics_underperformance()`, `_stall_counter` tracking, `last_stall_*` fields. Keep session management + bin storage + `get_heating_rate()`.
- Modify: `adaptive/undershoot_detector.py` — delete `check_rate_based_undershoot()` method
- Modify: `adaptive/confidence.py` — delete `update_convergence_confidence()` (unused, real impl is in learning.py)
- Modify: `climate.py` — delete `_check_physics_rate_and_boost_ki()` method and its call sites (~lines 1462-1510), delete `check_physics_rate_underperformance()` call in `_handle_heating_rate_session_end()`
- Modify: `adaptive/learning.py` — delete `check_physics_rate_underperformance()` delegator

**Step 1:** In `heating_rate_learner.py`, delete `should_boost_ki()`, `check_physics_underperformance()`, `_stall_counter`, `_last_stall_outdoor`, `_last_stall_setpoint`, `_consecutive_stalls`. Remove from `to_dict()`/`from_dict()`. Keep `_active_session`, `_bins`, `start_session()`, `end_session()`, `add_observation()`, `get_heating_rate()`.

**Step 2:** In `undershoot_detector.py`, delete `check_rate_based_undershoot()`.

**Step 3:** In `confidence.py`, delete the `update_convergence_confidence()` method (the one that is never called — verify by grepping).

**Step 4:** In `climate.py`, delete `_check_physics_rate_and_boost_ki()` and remove its calls from `_handle_heating_rate_session_end()`.

**Step 5:** In `learning.py`, delete `check_physics_rate_underperformance()` delegator.

**Step 6: Run tests**

Run: `pytest tests/test_undershoot_detector.py tests/test_integration_undershoot.py tests/test_learning.py tests/test_integration_heating_rate.py -v --tb=short`
Expected: Some tests for `check_physics_underperformance` will fail — delete those tests (they test removed functionality).

**Step 7: Run full suite**

Run: `pytest --tb=short -q`

**Step 8: Commit**

```
refactor(learning): delete dead Ki boost path + unused confidence method

Remove physics rate Ki boost (path 3), HeatingRateLearner stall
tracking, UndershootDetector.check_rate_based_undershoot(),
ConfidenceTracker.update_convergence_confidence() (unused).
```

---

## Phase 3: Shared Utilities

### Task 7: Extract Pearson correlation + MAD to robust_stats.py

**Files:**
- Modify: `adaptive/robust_stats.py` — add `pearson_correlation()` function
- Modify: `adaptive/pid_rules.py` — replace inline Pearson with import from `robust_stats`
- Modify: `adaptive/ke_learning.py` — replace inline Pearson with import from `robust_stats`
- Modify: `managers/cycle_metrics.py` — replace inline MAD calculation with import from `robust_stats`
- Test: `tests/test_robust_stats.py` (create if not exists, or add to existing)

**Step 1: Write tests for `pearson_correlation()`**

```python
def test_pearson_perfect_positive():
    assert pearson_correlation([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)

def test_pearson_perfect_negative():
    assert pearson_correlation([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)

def test_pearson_no_correlation():
    assert abs(pearson_correlation([1, 2, 3, 4], [1, 3, 2, 4])) < 0.9

def test_pearson_too_few_points():
    assert pearson_correlation([1], [1]) is None
```

**Step 2:** Copy the better of the two Pearson implementations into `robust_stats.py`. Add MAD if not already there.

**Step 3:** Replace duplicates in `pid_rules.py`, `ke_learning.py`, `cycle_metrics.py` with imports.

**Step 4: Run affected tests**

Run: `pytest tests/test_robust_stats.py tests/test_pid_rules.py tests/test_learning.py tests/test_cycle_metrics.py -v --tb=short`

**Step 5: Run full suite**

Run: `pytest --tb=short -q`

**Step 6: Commit**

```
refactor(learning): extract Pearson correlation + MAD to robust_stats

Eliminates duplicate implementations in pid_rules, ke_learning,
and cycle_metrics.
```

---

## Phase 4: ConfidenceManager

### Task 8: Create ConfidenceManager + tests

**Files:**
- Create: `adaptive/confidence_manager.py`
- Create: `tests/test_confidence_manager.py`

**Step 1: Write failing tests**

Test the public interface from the design:
```python
class TestConfidenceManager:
    def test_initial_confidence_is_zero(self):
        mgr = ConfidenceManager(heating_type=HeatingType.CONVECTOR)
        assert mgr.get_confidence(HVACMode.HEAT) == 0.0

    def test_update_increases_confidence(self):
        mgr = ConfidenceManager(heating_type=HeatingType.CONVECTOR)
        metrics = make_good_cycle_metrics()
        mgr.update(weight=1.0, cycle_metrics=metrics, mode=HVACMode.HEAT)
        assert mgr.get_confidence(HVACMode.HEAT) > 0.0

    def test_get_learning_status_idle_when_suppressed(self):
        mgr = ConfidenceManager(heating_type=HeatingType.CONVECTOR)
        assert mgr.get_learning_status(HVACMode.HEAT, cycle_count=0) == "idle"

    def test_get_learning_status_collecting(self):
        mgr = ConfidenceManager(heating_type=HeatingType.CONVECTOR)
        assert mgr.get_learning_status(HVACMode.HEAT, cycle_count=3) == "collecting"

    def test_get_learning_status_stable(self):
        mgr = ConfidenceManager(heating_type=HeatingType.CONVECTOR)
        mgr._set_confidence(HVACMode.HEAT, 0.45)  # above tier1
        status = mgr.get_learning_status(HVACMode.HEAT, cycle_count=10)
        assert status == "stable"

    def test_apply_decay(self):
        mgr = ConfidenceManager(heating_type=HeatingType.CONVECTOR)
        mgr._set_confidence(HVACMode.HEAT, 0.5)
        mgr.apply_decay(0.9)
        assert mgr.get_confidence(HVACMode.HEAT) == pytest.approx(0.45)

    def test_ke_convergence_tracking(self):
        mgr = ConfidenceManager(heating_type=HeatingType.CONVECTOR)
        assert not mgr.is_converged_for_ke()
        for _ in range(5):
            mgr.update_ke_convergence(converged=True)
        assert mgr.is_converged_for_ke()

    def test_compute_learning_status_module_function(self):
        from custom_components.adaptive_climate.adaptive.confidence_manager import (
            compute_learning_status,
        )
        status = compute_learning_status(
            confidence=0.5, cycle_count=10, heating_type=HeatingType.CONVECTOR
        )
        assert status == "stable"
```

**Step 2: Run, verify failure**

Run: `pytest tests/test_confidence_manager.py -v`
Expected: ImportError

**Step 3: Implement ConfidenceManager**

Create `adaptive/confidence_manager.py`. This absorbs:
- `ConfidenceTracker` data storage (heating/cooling confidence floats)
- `update_convergence_confidence()` logic from `learning.py:1052-1196`
- `ConfidenceContributionTracker` (import + own instance)
- Ke convergence gate (`_consecutive_converged_cycles`, `_pid_converged_for_ke`, 5-cycle threshold)
- `compute_learning_status()` as module-level function using `HeatingTypeProfile` tier thresholds
- `get_learning_rate_multiplier()` from `learning.py:1237`
- `apply_confidence_decay()` from `learning.py:1228`

Key: use `HeatingTypeProfile` for tier thresholds instead of recomputing from `HEATING_TYPE_CONFIDENCE_SCALE`.

Interface:
```python
class ConfidenceManager:
    def __init__(self, heating_type: HeatingType): ...
    def update(self, weight: float, cycle_metrics: CycleMetrics, mode: HVACMode) -> str: ...
    def get_confidence(self, mode: HVACMode) -> float: ...
    def get_learning_status(self, mode: HVACMode, cycle_count: int) -> str: ...
    def get_learning_rate_multiplier(self, mode: HVACMode) -> float: ...
    def apply_decay(self, factor: float) -> None: ...
    def is_converged_for_ke(self) -> bool: ...
    def update_ke_convergence(self, converged: bool) -> None: ...
    def reset_ke_convergence(self) -> None: ...
    # Contribution tracking (delegated)
    def get_contribution_tracker(self) -> ConfidenceContributionTracker: ...
    # Serialization
    def to_dict(self, mode: HVACMode) -> dict: ...
    def restore_from_dict(self, data: dict, mode: HVACMode) -> None: ...

def compute_learning_status(confidence: float, cycle_count: int, heating_type: HeatingType) -> str: ...
```

**Step 4: Run tests, verify pass**

Run: `pytest tests/test_confidence_manager.py -v`

**Step 5: Run full suite** (no consumers yet, should be clean)

Run: `pytest --tb=short -q`

**Step 6: Commit**

```
feat(learning): add ConfidenceManager

Consolidates confidence tracking, learning status computation,
contribution caps, and Ke convergence gate into one manager.
```

---

### Task 9: Wire ConfidenceManager into AdaptiveLearner

**Files:**
- Modify: `adaptive/learning.py` — replace inline confidence logic with `ConfidenceManager` delegation
- Modify: `managers/learning_gate.py` — import `compute_learning_status` from `confidence_manager` instead of recomputing
- Modify: `managers/auto_apply.py` — import `compute_learning_status` from `confidence_manager` instead of recomputing

**Step 1:** In `learning.py`:
- Replace `self._confidence = ConfidenceTracker(...)` with `self._confidence_manager = ConfidenceManager(heating_type)`
- Delete `update_convergence_confidence()` method (144 lines), delegate to `self._confidence_manager.update()`
- Delete `get_learning_rate_multiplier()`, delegate to `self._confidence_manager`
- Delete `apply_confidence_decay()`, delegate to `self._confidence_manager`
- Replace `_consecutive_converged_cycles` and `_pid_converged_for_ke` with `self._confidence_manager` methods
- Keep `AdaptiveLearner` public API signatures unchanged — they now delegate

**Step 2:** In `learning_gate.py`, replace inline tier computation:
```python
# Before:
scale = HEATING_TYPE_CONFIDENCE_SCALE.get(ht, 1.0)
tier2 = 0.7 * scale
if confidence >= tier2: status = "tuned"

# After:
from ..adaptive.confidence_manager import compute_learning_status
status = compute_learning_status(confidence, cycle_count, heating_type)
```

**Step 3:** In `auto_apply.py`, same replacement for learning status computation.

**Step 4: Run affected tests**

Run: `pytest tests/test_learning.py tests/test_learning_gate_manager.py tests/test_auto_apply.py tests/test_integration_auto_apply.py tests/test_confidence_tiers.py -v --tb=short`

**Step 5: Run full suite**

Run: `pytest --tb=short -q`

**Step 6: Commit**

```
refactor(learning): wire ConfidenceManager into AdaptiveLearner

Confidence, learning status, Ke gate now single source of truth.
Deduplicates status computation in learning_gate + auto_apply.
```

---

## Phase 5: PIDTuningManager

### Task 10: Create PIDTuningManager + AveragedCycleMetrics + tests

**Files:**
- Create: `adaptive/pid_tuning_manager.py`
- Create: `tests/test_pid_tuning_manager_new.py` (avoid collision with existing `test_pid_tuning_manager.py` which tests the protocol)

**Step 1: Write failing tests**

```python
class TestPIDTuningManager:
    def test_add_cycle_stores_in_history(self):
        mgr = PIDTuningManager(heating_type=HeatingType.CONVECTOR)
        mgr.add_cycle(HVACMode.HEAT, make_cycle_metrics())
        assert mgr.get_cycle_count(HVACMode.HEAT) == 1

    def test_calculate_adjustment_returns_none_with_few_cycles(self):
        mgr = PIDTuningManager(heating_type=HeatingType.CONVECTOR)
        result = mgr.calculate_adjustment(HVACMode.HEAT, current_kp=1.0, current_ki=0.01, current_kd=10.0)
        assert result is None

    def test_averaged_cycle_metrics_dataclass(self):
        m = AveragedCycleMetrics(overshoot=0.3, undershoot=0.1, settling_time=5.0, ...)
        assert m.overshoot == 0.3

    def test_cycle_history_eviction(self):
        mgr = PIDTuningManager(heating_type=HeatingType.CONVECTOR, max_history=5)
        for i in range(10):
            mgr.add_cycle(HVACMode.HEAT, make_cycle_metrics())
        assert mgr.get_cycle_count(HVACMode.HEAT) == 10  # count tracks all, history capped
```

**Step 2: Run, verify failure**

**Step 3: Implement PIDTuningManager**

Create `adaptive/pid_tuning_manager.py`. This absorbs from `learning.py`:
- Cycle history storage (`_heating_cycle_history`, `_cooling_cycle_history`, `_max_history`)
- `add_cycle_metrics()` — the cycle storage part (not confidence/undershoot updates)
- `calculate_pid_adjustment()` — decomposed into pipeline:
  1. `_select_recent_undisturbed(mode, count)` — filter + slice
  2. `_compute_averaged_metrics(cycles) -> AveragedCycleMetrics` — the 118-line block
  3. `_check_rate_limit()` — time + cycle count gate
  4. `_check_convergence(averaged)` — convergence detection
  5. `_evaluate_rules(averaged, gains, learning_rate, pwm)` — rule eval + conflict resolution + scaling + clamp
- `_rule_state_tracker`, `_convergence_thresholds`, `_rule_thresholds`
- `_last_adjustment_time`, `_cycles_since_last_adjustment`
- `clear_history()`, `get_cycle_count()`

```python
@dataclass
class AveragedCycleMetrics:
    overshoot: float
    undershoot: float
    settling_time: float
    oscillations: float
    rise_time: float | None
    inter_cycle_drift: float
    settling_mae: float
    decay_contribution: float

class PIDTuningManager:
    def __init__(self, heating_type: HeatingType, max_history: int = MAX_CYCLE_HISTORY): ...
    def add_cycle(self, mode: HVACMode, metrics: CycleMetrics) -> None: ...
    def calculate_adjustment(self, mode, current_kp, current_ki, current_kd, pwm_seconds=None, learning_rate=1.0) -> dict | None: ...
    def get_cycle_count(self, mode: HVACMode) -> int: ...
    def get_cycle_history(self, mode: HVACMode) -> list[CycleMetrics]: ...
    def clear_history(self) -> None: ...
    # Serialization
    def to_dict(self) -> dict: ...
    def restore_from_dict(self, data: dict) -> None: ...
```

**Step 4: Run tests, verify pass**

Run: `pytest tests/test_pid_tuning_manager_new.py -v`

**Step 5: Run full suite**

Run: `pytest --tb=short -q`

**Step 6: Commit**

```
feat(learning): add PIDTuningManager + AveragedCycleMetrics

Cycle history, metric averaging, rule evaluation in one manager.
calculate_pid_adjustment decomposed into 5-step pipeline.
```

---

### Task 11: Wire PIDTuningManager into AdaptiveLearner

**Files:**
- Modify: `adaptive/learning.py` — replace inline cycle history + rule evaluation with `PIDTuningManager` delegation
- Modify: `adaptive/learner_serialization.py` — update serialization to use PIDTuningManager

**Step 1:** In `learning.py`:
- Replace `self._heating_cycle_history`, `self._cooling_cycle_history`, `_max_history` with `self._tuning = PIDTuningManager(heating_type, max_history)`
- Delete `calculate_pid_adjustment()` (334 lines!), replace with delegation:
  ```python
  def calculate_pid_adjustment(self, current_kp, current_ki, current_kd, pwm_seconds=None):
      mode = self._get_current_mode()
      lr = self._confidence_manager.get_learning_rate_multiplier(mode)
      return self._tuning.calculate_adjustment(mode, current_kp, current_ki, current_kd, pwm_seconds, lr)
  ```
- Delete `_check_rate_limit()`, `_check_convergence()` (moved to PIDTuningManager)
- Delete `_rule_state_tracker`, `_convergence_thresholds`, `_rule_thresholds` init
- `add_cycle_metrics()` now calls `self._tuning.add_cycle(mode, metrics)` for storage
- `clear_history()` delegates to `self._tuning.clear_history()`
- `cycle_history` property delegates to `self._tuning.get_cycle_history(mode)`

**Step 2:** Update `learner_serialization.py` to serialize/restore via `PIDTuningManager.to_dict()`/`restore_from_dict()`.

**Step 3: Run affected tests**

Run: `pytest tests/test_learning.py tests/test_pid_rules.py tests/test_integration_auto_apply.py tests/test_integration_adaptive_flow.py tests/test_integration_cycle_learning.py -v --tb=short`
Expected: All PASS (behavioral equivalence)

**Step 4: Run full suite**

Run: `pytest --tb=short -q`

**Step 5: Commit**

```
refactor(learning): wire PIDTuningManager into AdaptiveLearner

calculate_pid_adjustment (334 lines) replaced with 3-line delegation.
Cycle history ownership moved to PIDTuningManager.
```

---

## Phase 6: ValidationSafetyManager

### Task 12: Create ValidationSafetyManager by merging existing managers

**Files:**
- Create: `adaptive/validation_safety_manager.py`
- Modify: `managers/auto_apply.py` — keep as thin adapter or merge into new class
- Modify: `adaptive/validation.py` — merge into new class
- Test: `tests/test_validation_safety_manager.py`

**Step 1: Write failing tests**

```python
class TestValidationSafetyManager:
    def test_should_auto_apply_false_when_collecting(self):
        mgr = ValidationSafetyManager(heating_type=HeatingType.CONVECTOR)
        assert not mgr.should_auto_apply(confidence=0.2, status="collecting", adjustment={})

    def test_should_auto_apply_true_when_tuned_first_time(self):
        mgr = ValidationSafetyManager(heating_type=HeatingType.CONVECTOR)
        assert mgr.should_auto_apply(confidence=0.75, status="tuned", adjustment={"kp": 0.1})

    def test_validation_mode_lifecycle(self):
        mgr = ValidationSafetyManager(heating_type=HeatingType.CONVECTOR)
        mgr.start_validation(baseline_overshoot=0.3)
        assert mgr.is_in_validation()
        result = mgr.add_validation_cycle(make_good_metrics())
        # ... test full lifecycle
```

**Step 2: Run, verify failure**

**Step 3: Implement** by merging `ValidationManager` + `AutoApplyManager` logic into one class. Both currently coordinate on "should we apply this adjustment?" — combine their gates.

**Step 4: Run tests**

Run: `pytest tests/test_validation_safety_manager.py tests/test_auto_apply.py tests/test_integration_auto_apply.py -v --tb=short`

**Step 5: Run full suite**

Run: `pytest --tb=short -q`

**Step 6: Commit**

```
feat(learning): add ValidationSafetyManager

Merges ValidationManager + AutoApplyManager into unified
auto-apply gating and validation lifecycle.
```

---

### Task 13: Wire ValidationSafetyManager into AdaptiveLearner

**Files:**
- Modify: `adaptive/learning.py` — replace `_validation` and `_auto_apply` with `_validation_safety`
- Modify: `managers/pid_tuning.py` (the existing one in managers/) — update to use new interface

**Step 1:** In `learning.py`:
- Replace `self._validation = ValidationManager(...)` and `self._auto_apply = AutoApplyManager(...)` with `self._validation_safety = ValidationSafetyManager(heating_type)`
- Delete all 9 thin delegator methods for validation/auto-apply
- Update `add_cycle_metrics()` validation path to use `self._validation_safety`

**Step 2:** Update `managers/pid_tuning.py` (the existing service-facing manager) to use new `ValidationSafetyManager` interface.

**Step 3: Run tests**

Run: `pytest tests/test_integration_auto_apply.py tests/test_auto_apply.py tests/test_learning.py -v --tb=short`

**Step 4: Run full suite**

Run: `pytest --tb=short -q`

**Step 5: Commit**

```
refactor(learning): wire ValidationSafetyManager into AdaptiveLearner

9 thin delegator methods removed. Validation + auto-apply unified.
```

---

## Phase 7: Slim AdaptiveLearner

### Task 14: Remove remaining thin delegators, finalize orchestrator

**Files:**
- Modify: `adaptive/learning.py` — delete remaining delegator methods, slim to ~200 lines
- Modify: `managers/state_attributes.py` — update to access sub-managers directly via `adaptive_learner.confidence_manager`, `adaptive_learner.tuning`, etc.
- Modify: `sensors/performance.py` — update `cycle_history` access
- Modify: `services/__init__.py` and `services/scheduled.py` — update method calls if needed
- Modify: `managers/cycle_metrics.py` — update learner method calls

**Step 1:** Expose sub-managers as properties on AdaptiveLearner:
```python
@property
def confidence_manager(self) -> ConfidenceManager: return self._confidence_manager

@property
def tuning(self) -> PIDTuningManager: return self._tuning

@property
def validation(self) -> ValidationSafetyManager: return self._validation_safety

@property
def undershoot_detector(self) -> UndershootDetector: return self._undershoot_detector

@property
def heating_rate_learner(self) -> HeatingRateLearner: return self._heating_rate_learner
```

**Step 2:** Update callers that used thin delegators. For each deleted method, the caller either:
- Calls the sub-manager directly: `learner.confidence_manager.get_confidence(mode)` instead of `learner.get_convergence_confidence(mode)`
- Or the method stays on AdaptiveLearner as a true orchestration method (not a thin wrapper)

Keep on AdaptiveLearner (true orchestration, touches multiple sub-managers):
- `add_cycle_metrics()` — updates tuning + confidence + undershoot
- `to_dict()` / `restore_from_dict()` — coordinates all sub-managers
- `clear_history()` — resets all sub-managers

Remove from AdaptiveLearner (now accessed via sub-manager properties):
- `get_convergence_confidence()` → `learner.confidence_manager.get_confidence()`
- `get_cycle_count()` → `learner.tuning.get_cycle_count()`
- `get_learning_rate_multiplier()` → `learner.confidence_manager.get_learning_rate_multiplier()`
- `is_pid_converged_for_ke()` → `learner.confidence_manager.is_converged_for_ke()`
- All validation/auto-apply delegators → `learner.validation.*`
- etc.

**Step 3:** Update all callers in `state_attributes.py`, `performance.py`, `services/`, `managers/cycle_metrics.py`, `climate.py`, `climate_control.py`.

**Note:** This touches many files. Split into sub-commits if the diff is large. The key principle: if a caller only needs one sub-manager, access it directly. If a caller needs orchestration across managers, use the orchestrator method.

**Step 4: Run tests**

Run: `pytest tests/test_learning.py tests/test_cycle_tracker.py tests/test_integration_cycle_learning.py tests/test_integration_auto_apply.py tests/test_integration_adaptive_flow.py -v --tb=short`

**Step 5: Run full suite**

Run: `pytest --tb=short -q`

**Step 6: Verify AdaptiveLearner is now ~200 lines**

Count: `wc -l custom_components/adaptive_climate/adaptive/learning.py`
Expected: ~150-250 lines

**Step 7: Commit**

```
refactor(learning): slim AdaptiveLearner to thin orchestrator

~1,400 lines removed. Callers access sub-managers via properties.
Orchestrator retains add_cycle_metrics, serialization, clear_history.
```

---

## Phase 8: Serialization v11

### Task 15: Bump serialization to v11 with v10 backward compat

**Files:**
- Modify: `adaptive/learner_serialization.py` — v11 format + v10 reader
- Modify: `adaptive/learning.py` — update `to_dict()` and `restore_from_dict()` for new structure
- Test: `tests/test_integration_persistence.py` — add v10→v11 migration test

**Step 1: Write failing test**

```python
def test_v10_data_restores_in_v11():
    """v10 serialized data should restore correctly in v11 code."""
    v10_data = {
        "format_version": 10,
        "heating": {"cycle_history": [...], "auto_apply_count": 3, "convergence_confidence": 0.65},
        "cooling": {"cycle_history": [], "auto_apply_count": 0, "convergence_confidence": 0.0},
        "last_adjustment_time": None,
        "consecutive_converged_cycles": 4,
        "pid_converged_for_ke": True,
        "undershoot_detector": {"cumulative_ki_multiplier": 1.2, ...},
        "contribution_tracker": {...},
        "heating_rate_learner": {...},
    }
    learner = AdaptiveLearner(heating_type=HeatingType.CONVECTOR)
    learner.restore_from_dict(v10_data)
    assert learner.confidence_manager.get_confidence(HVACMode.HEAT) == pytest.approx(0.65)
    assert learner.tuning.get_cycle_count(HVACMode.HEAT) == len(v10_data["heating"]["cycle_history"])
```

**Step 2: Run, verify failure** (v10 restore may already work or may need migration)

**Step 3: Implement v11 format**

v11 structure (serialized by new managers):
```python
{
    "format_version": 11,
    "tuning": {
        "heating_cycle_history": [...],
        "cooling_cycle_history": [...],
        "last_adjustment_time": str | None,
    },
    "confidence": {
        "heating_confidence": float,
        "cooling_confidence": float,
        "heating_auto_apply_count": int,
        "cooling_auto_apply_count": int,
        "consecutive_converged_cycles": int,
        "pid_converged_for_ke": bool,
        "contribution_tracker": {...},
    },
    "undershoot_detector": {...},  # same as v10
    "heating_rate_learner": {...},  # same as v10
}
```

Add `_restore_v10(data)` function that maps v10 keys → v11 structure before restoring.

In `restore_learner_from_dict()`:
```python
version = data.get("format_version", 0)
if version == 10:
    data = _migrate_v10_to_v11(data)
elif version != 11:
    return _default_learner_state()
```

**Step 4: Run persistence tests**

Run: `pytest tests/test_integration_persistence.py -v`

**Step 5: Run full suite**

Run: `pytest --tb=short -q`

**Step 6: Commit**

```
feat(learning): bump serialization to v11 with v10 compat

New format groups state by manager ownership. v10 data auto-migrates.
```

---

## Phase 9: Cleanup + Delete Old Modules

### Task 16: Delete replaced modules, update imports

**Files:**
- Delete: `adaptive/validation.py` (merged into `validation_safety_manager.py`)
- Modify: `adaptive/confidence.py` — either delete entirely (replaced by `confidence_manager.py`) or keep as pure data types only
- Clean up: any remaining unused imports in const.py, learning.py
- Verify: `adaptive/__init__.py` exports if any

**Step 1:** Grep for all imports of deleted modules. Update or remove.

**Step 2: Run full suite**

Run: `pytest --tb=short -q`

**Step 3: Run linting + type checking**

Run: `ruff check custom_components/ tests/ && pyright custom_components/adaptive_climate/`

**Step 4: Commit**

```
chore(learning): delete replaced modules, clean imports

Remove validation.py (merged), unused confidence.py code.
```

---

## Summary

| Phase | Tasks | Key outcome |
|-------|-------|-------------|
| 1. HeatingTypeProfile | 1-4 | Single source of truth for 25+ per-type dicts |
| 2. Dead code | 5-6 | ~1,500 lines deleted (dead learners, Ki boost, bins) |
| 3. Utilities | 7 | Deduplicated Pearson + MAD |
| 4. ConfidenceManager | 8-9 | Confidence + status + Ke gate consolidated |
| 5. PIDTuningManager | 10-11 | Cycle history + rule eval extracted, 334-line method decomposed |
| 6. ValidationSafety | 12-13 | ValidationManager + AutoApplyManager merged |
| 7. Slim learner | 14 | AdaptiveLearner → ~200-line orchestrator |
| 8. Serialization | 15 | v11 format, v10 backward compat |
| 9. Cleanup | 16 | Delete replaced modules |

**Total: 16 tasks, ~2,000 lines deleted, ~1,500 restructured.**

**Test strategy:** Every task runs affected tests + full suite. Existing tests are the primary safety net (behavioral equivalence). New tests only for new public interfaces (HeatingTypeProfile, ConfidenceManager, PIDTuningManager, ValidationSafetyManager).

**Risk mitigation:** Each phase is independently committable. If any phase causes issues, the previous phases still provide value. Phase 1 (HeatingTypeProfile) and Phase 2 (dead code) are zero-risk and deliver immediate simplification.
