# Adaptive Learning Subsystem Analysis

## Overview

The adaptive learning subsystem is the intelligence layer of the Adaptive Climate integration. It observes heating cycle performance, computes metrics, adjusts PID gains through rule-based evaluation, and tracks confidence in the tuning. The subsystem spans 20+ modules across `adaptive/` (core algorithms) and `managers/` (orchestration), totaling approximately 10,500 lines of learning-related code.

The architecture follows a pipeline pattern: temperature readings flow through cycle tracking into metric computation, rule evaluation, gain adjustment, and confidence tracking. However, this clean pipeline is complicated by extensive cross-cutting concerns (learning suppression, graduated night setback, undershoot detection) and significant code duplication across module boundaries.

## Module Catalog

### Core Learning Modules (`adaptive/`)

---

#### `adaptive/learning.py` -- AdaptiveLearner (1,659 lines)

**Key class:** `AdaptiveLearner` -- Central orchestrator for all adaptive learning.

**Responsibilities:**
- Owns and coordinates sub-components: `ConfidenceTracker`, `ValidationManager`, `AutoApplyManager`, `UndershootDetector`, `CycleWeightCalculator`, `ConfidenceContributionTracker`, `HeatingRateLearner`
- Maintains cycle history (heating and cooling separately)
- Computes PID adjustments via `calculate_pid_adjustment()` (334-line method, lines 514-847)
- Updates convergence confidence via `update_convergence_confidence()` (144-line method, lines 1052-1196)
- Exposes learning status (idle/collecting/stable/tuned/optimized)

**Dependencies:** `confidence.py`, `validation.py`, `auto_apply.py`, `undershoot_detector.py`, `cycle_weight.py`, `confidence_contribution.py`, `heating_rate_learner.py`, `pid_rules.py`, `robust_stats.py`, `learner_serialization.py`, `cycle_analysis.py`

**Persistence:** Full state via `learner_serialization.py` (format_version 12; v12 drops stored heating undershoot that reached or exceeded the cycle's starting gap and rounds stored overshoot/undershoot/starting_delta). Includes cycle histories, auto-apply counts, convergence confidence, undershoot detector state, contribution tracker state, heating rate learner state.

**Issues:**
- God class with 63 methods and 27+ attributes
- Lines 195-315: ~30 backward-compatibility property aliases exist solely for tests accessing private internals
- `update_convergence_confidence()` (144 lines) writes `ConfidenceTracker` values directly with its own weighted learning implementation
- `calculate_pid_adjustment()` is a 334-line monolith that could delegate more to `pid_rules.py`

---

#### `adaptive/cycle_analysis.py` -- CycleMetrics (707 lines)

**Key classes:**
- `CycleMetrics` -- 20+ field dataclass capturing cycle performance (overshoot, undershoot, settling time, rise time, oscillations, settling MAE, duty cycle, etc.)
- `PhaseAwareOvershootTracker` -- Rise-to-settling phase tracking with time-windowed peak detection
- `InterruptionClassifier` -- Classifies events that invalidate cycles (setpoint changes, mode changes, contact sensor events)

**Responsibilities:** Pure computation of cycle performance metrics from temperature history and heater activity periods.

**Dependencies:** None (pure functions and dataclasses)

**Persistence:** Serialized via `learner_serialization.py` (`serialize_cycle()` / `_deserialize_cycle()`)

---

#### `adaptive/pid_rules.py` -- Rule Engine (488 lines)

**Key abstractions:**
- `PIDRule` enum: 9 rules with priority levels (oscillation=3, overshoot=2, slow_response=1)
- `RuleStateTracker` -- Hysteresis tracking to prevent rule oscillation
- `evaluate_pid_rules()` -- 239-line function evaluating all rules against averaged metrics

**Responsibilities:** Determines which PID gain adjustments should be made based on observed cycle performance patterns (oscillation, overshoot, undershoot, slow response, drift, etc.).

**Dependencies:** None (pure logic, receives metrics as arguments)

**Persistence:** None (stateless evaluation, though `RuleStateTracker` maintains in-memory hysteresis state)

**Issues:**
- Contains `calculate_pearson_correlation()` (line ~450) -- **duplicated** in `ke_learning.py`

---

#### `adaptive/confidence.py` -- ConfidenceTracker (246 lines)

**Key class:** `ConfidenceTracker` -- Per-mode (heating/cooling) convergence confidence tracking.

**Responsibilities:**
- Tracks confidence 0.0-1.0 per HVAC mode
- Provides `get_learning_rate_multiplier()` (linear interpolation 2.0x at low confidence to 0.5x at high)
- Provides `apply_confidence_decay()` (2% daily decay)

**Dependencies:** None

**Persistence:** Confidence values serialized as part of learner state

**Issues:**
- The tracker is reduced to a data holder that the learner reads/writes directly

---

#### `adaptive/confidence_contribution.py` -- ConfidenceContributionTracker (241 lines)

**Key class:** `ConfidenceContributionTracker` -- Caps maintenance and heating-rate contributions to prevent premature "tuned" status.

**Responsibilities:**
- `apply_maintenance_gain()` -- Diminishing returns past cap threshold (25-35% depending on heating type)
- `apply_heating_rate_gain()` -- Hard cap (5-30% depending on heating type)
- `can_reach_tier()` -- Recovery cycle count gating for tier progression

**Dependencies:** `const.py` (heating type constants)

**Persistence:** Serialized via `to_dict()` / `from_dict()`, embedded in learner state

**Issues:** Lines 58-93 contain backward-compatible property aliases for tests

---

#### `adaptive/cycle_weight.py` -- CycleWeightCalculator (127 lines)

**Key class:** `CycleWeightCalculator` -- Classifies cycles as recovery vs. maintenance and calculates learning weight.

**Key abstraction:** `CycleOutcome` enum (CLEAN, OVERSHOOT, UNDERSHOOT)

**Formula:** `weight = (base x delta_multiplier x outcome_factor) + bonuses`
- Base: 1.0 (recovery) or 0.3 (maintenance)
- Delta multiplier: `1.0 + (delta - threshold) x 0.5`, capped at 2.0
- Outcome factor: 1.0 (clean), 0.7 (overshoot), 0.5 (undershoot)
- Bonuses: +0.15 (high duty), +0.15 (cold outdoor), +0.2 (night setback recovery)

**Dependencies:** `const.py` (heating type thresholds)

**Persistence:** None (stateless calculator)

---

#### `adaptive/validation.py` -- ValidationManager (396 lines)

**Key class:** `ValidationManager` -- Post-auto-apply monitoring for performance degradation with rollback capability.

**Responsibilities:**
- `start_validation_mode()` / `add_validation_cycle()` -- Monitors N cycles after auto-apply for degradation
- `check_auto_apply_limits()` -- 4 safety checks (lifetime count, seasonal count, drift from baseline, seasonal shift cooldown)
- `check_seasonal_shift()` -- Detects 10C outdoor temp shifts via outdoor temp history
- `set_physics_baseline()` / `calculate_drift_from_baseline()` -- Tracks max % drift across Kp/Ki/Kd from initial physics values

**Dependencies:** `const.py`

**Persistence:** In-memory state (validation mode, seasonal tracking, physics baseline). Serialized as part of learner state.

---

#### `adaptive/auto_apply.py` -- AutoApplyManager (250 lines)

**Key class:** `AutoApplyManager` -- Orchestrates auto-apply safety gates for automatic PID gain updates.

**Responsibilities:**
- `check_auto_apply_safety_gates()` -- 3 checks: validation mode active, apply limits, seasonal shift. Returns min_cycles (doubled after the first apply) and cooldown hours/cycles, which `calculate_pid_adjustment()` enforces. Learning status is not a gate.

**Dependencies:** `validation.py`, `const.py`

**Persistence:** None (delegates to ValidationManager)


---

#### `adaptive/undershoot_detector.py` -- UndershootDetector (516 lines)

**Key class:** `UndershootDetector` -- Unified detector with two detection modes.

**Modes:**
1. **Real-time thermal debt:** Accumulates `time x temp_deficit` below setpoint. Triggers when debt exceeds threshold scaled by thermal mass (floor: 150 C-min, convector: 60 C-min)
2. **Cycle-based consecutive failures:** Tracks consecutive cycles failing to reach setpoint (rise_time=None + undershoot >= threshold). Resets on success.

**Behavior:** When triggered, applies Ki boost (1.20-1.35x depending on type). Cumulative multiplier capped at 2.0x. Cooldown scales with thermal mass (floor: 24h, forced_air: 3h).

**Dependencies:** `const.py`

**Persistence:** Serialized via `to_dict()` / `from_dict()`, embedded in learner state (v10 format)

---

#### `adaptive/heating_rate_learner.py` -- HeatingRateLearner (547 lines)

**Key classes:**
- `HeatingRateLearner` -- Binned heating rate observations (12 bins: 4 delta ranges x 3 outdoor temp ranges)
- `RecoverySession` -- Multi-cycle session tracking with stall detection

**Responsibilities:**
- `get_heating_rate()` -- Returns learned or fallback rate with source attribution

**Dependencies:** `const.py` (heating type rates, bin thresholds)

**Persistence:** Serialized via `to_dict()` / `from_dict()`, embedded in learner state

Measurement only: heating-rate sessions do not boost Ki.

---

#### `adaptive/ke_learning.py` -- KeLearner (458 lines)

**Key classes:**
- `KeLearner` -- Learns outdoor temperature compensation coefficient (Ke)
- `KeObservation` dataclass -- timestamp, outdoor_temp, pid_output, indoor_temp, target_temp

**Responsibilities:**
- Collects observations when PID is at steady state
- `calculate_ke_adjustment()` -- Pearson correlation analysis between outdoor temp and PID output
- Rate-limited adjustments (configurable interval between adjustments)

**Dependencies:** None (self-contained)

**Persistence:** Serialized via `to_dict()` / `from_dict()`, stored separately (not in learner state -- stored via `persistence.py` ke key)

**Issues:** Contains `_calculate_pearson_correlation()` using `statistics.stdev` -- **duplicated** with the version in `pid_rules.py` which uses manual standard deviation calculation

---

#### `adaptive/robust_stats.py` -- Robust Statistics (185 lines)

**Key functions:**
- `robust_average()` -- MAD-based outlier rejection with safety limits
- `detect_outliers_modified_zscore()` -- Modified Z-score with 0.6745 constant
- `calculate_median()` / `calculate_mad()` -- Basic robust statistics

**Dependencies:** None (pure math)

**Persistence:** None (stateless)

**Issues:** `calculate_mad()` is **duplicated** as `_calculate_mad()` in `managers/cycle_metrics.py`

---

#### `adaptive/learner_serialization.py` -- Serialization (292 lines)

**Key functions:**
- `learner_to_dict()` -- Serializes all learner state to v10 format dict
- `restore_learner_from_dict()` -- Deserializes v10 format (rejects older formats, returns defaults)
- `serialize_cycle()` / `_deserialize_cycle()` -- CycleMetrics to/from dict

**Dependencies:** All sub-components that have `to_dict()` / `from_dict()` methods

**Persistence:** This IS the persistence layer for `AdaptiveLearner`

---

#### `adaptive/persistence.py` -- LearningDataStore (307 lines)

**Key class:** `LearningDataStore` -- Zone-keyed JSON persistence via HA Store API.

**Responsibilities:**
- `async_save_zone()` -- Saves adaptive, ke, preheat data per zone
- `schedule_zone_save()` -- 30-second debounced save
- `update_zone_data()` -- In-memory update without disk write
- Also handles manifold state persistence

**Dependencies:** Home Assistant Store API

**Persistence:** This is the disk persistence coordinator. Saves to `.storage/adaptive_climate.learning`

---

#### `adaptive/physics.py` -- Physics-Based Initialization (842 lines)

**Key functions:**
- `calculate_thermal_time_constant()` -- Tau from volume/energy_rating with window/floor adjustments
- `calculate_initial_pid()` -- Multi-point reference profile interpolation with power/supply temp scaling
- `calculate_initial_ke()` -- Physics-based Ke from energy rating, windows, heating type
- `calculate_expected_heating_rate()` -- Physics baseline for rate comparison
- `RollingWindowHeatingRate` class -- Rolling window heating rate tracker

**Dependencies:** `const.py`, `floor_physics.py`

**Persistence:** None (pure calculation, results stored via `PIDGainsManager`)

**Issues:** Contains 6+ heating-type configuration dicts (reference_profiles, pwm_periods, heating_type_factors, EXPECTED_HEATING_RATES, etc.)

---

### Peripheral Adaptive Modules

These modules participate in learning primarily as inputs (providing data) or gates (suppressing learning).

#### `adaptive/thermal_rates.py` -- ThermalRateLearner (407 lines)

**Key class:** `ThermalRateLearner` -- Learns heating and cooling rates from temperature history segments.

**Role in learning:** Provides heating/cooling rate estimates used by `NightSetback` for recovery timing. Independent from `HeatingRateLearner` (different approach: segment-based vs. binned observations).

**Persistence:** None (in-memory deques, no serialization)

**Issues:** **Overlaps** with `HeatingRateLearner` in purpose (both learn heating rates) but uses different methodology. `ThermalRateLearner` does segment-based analysis of raw temperature history; `HeatingRateLearner` does binned observation tracking from cycle metrics. Both serve different consumers but the overlap in domain is confusing.

---

#### `adaptive/preheat.py` -- PreheatLearner (388 lines)

**Key class:** `PreheatLearner` -- Binned heating rate observations for preheat time estimation.

**Role in learning:** Can delegate to `HeatingRateLearner` for rate data, or maintain its own binned observations. Used by `NightSetback.should_start_recovery()` for predictive pre-heating.

**Persistence:** Serialized via `to_dict()` / `from_dict()`, stored via `persistence.py` (preheat key)

**Issues:** Maintains its own 12-bin observation system (4 delta bins x 3 outdoor bins) that **overlaps** with `HeatingRateLearner`'s identical binning scheme. When `HeatingRateLearner` is provided, it delegates to it -- but both classes exist and maintain similar data structures.

---

#### `adaptive/night_setback.py` -- NightSetback (390 lines)

**Key classes:**
- `NightSetback` -- Zone night setback data model with time parsing, heating rate estimation, recovery scheduling
- `NightSetbackManager` -- Multi-zone night setback manager (note: different from `managers/night_setback_manager.py`)

**Role in learning:** Consumer of learned heating rates (from `ThermalRateLearner` or `PreheatLearner`) for recovery timing. Also contains its own hardcoded heating type rate and cold-soak margin dicts (lines 126, 152).

**Persistence:** None (configuration only)

**Issues:**
- Contains hardcoded heating-type rate dicts that duplicate values found in other modules
- Two classes named `NightSetbackManager` exist: one in `adaptive/night_setback.py` (multi-zone data model) and one in `managers/night_setback_manager.py` (HA integration orchestration)

---

#### `adaptive/contact_sensors.py` -- ContactSensorHandler (335 lines)

**Key classes:** `ContactSensorHandler`, `ContactSensorManager`

**Role in learning:** Learning suppression gate. When any contact is open, learning is suppressed via `LearningGateManager.is_learning_suppressed()`.

**Persistence:** None (runtime state only)

---

#### `adaptive/humidity_detector.py` -- HumidityDetector (220 lines)

**Key class:** `HumidityDetector` -- State machine (NORMAL -> PAUSED -> STABILIZING -> NORMAL)

**Role in learning:** Learning suppression gate. When humidity spike detected, learning is suppressed via `LearningGateManager.is_learning_suppressed()`.

**Persistence:** None (runtime state only)

---

#### `adaptive/disturbance_detector.py` -- DisturbanceDetector (240 lines)

**Key class:** `DisturbanceDetector` -- Detects solar gain, wind loss, outdoor temp swings, occupancy effects.

**Role in learning:** Cycle invalidation. Flags cycles with environmental disturbances for exclusion from learning.

**Persistence:** None (stateless detection)

---

#### `adaptive/floor_physics.py` -- Floor Physics (253 lines)

**Key functions:** `validate_floor_construction()`, `calculate_floor_thermal_properties()`

**Role in learning:** Provides tau_modifier for floor heating physics initialization. Feeds into `physics.py` calculations.

**Persistence:** None (pure calculation)

---

#### `adaptive/sun_position.py` -- SunPositionCalculator (234 lines)

**Role in learning:** None directly. Used by night setback for solar recovery timing.

---

#### `adaptive/pwm_tuning.py` -- PWM Tuning (97 lines)

**Key function:** `calculate_pwm_adjustment()` -- Detects short cycling and recommends PWM period increase.
**Key class:** `ValveCycleTracker` -- Counts valve open/close cycles for wear monitoring.

**Role in learning:** Peripheral. Analyzes cycle times for PWM optimization, independent of PID learning.

---

### Learning-Related Managers (`managers/`)

---

#### `managers/cycle_tracker.py` -- CycleTrackerManager (773 lines)

**Key class:** `CycleTrackerManager` -- IDLE -> HEATING -> SETTLING state machine.

**Responsibilities:**
- Tracks heating cycle lifecycle: start, heating phase, settling phase, completion
- Subscribes to 9 event types from `CycleEventDispatcher`
- Creates `CycleMetricsRecorder` internally for metrics calculation
- `_is_settling_complete()` -- MAD-based stability detection for settling
- `_finalize_cycle()` -- Delegates to `CycleMetricsRecorder.record_cycle_metrics()`

**Dependencies:** `managers/events.py` (CycleEventDispatcher), `managers/cycle_metrics.py` (CycleMetricsRecorder)

**Persistence:** Cycle count exposed via state attributes

**Issues:** God class with 38 methods and 43 attributes. 161-line `__init__`.

---

#### `managers/cycle_metrics.py` -- CycleMetricsRecorder (646 lines)

**Key class:** `CycleMetricsRecorder` -- Validates completed cycles, computes all metrics, and feeds results to learner.

**Responsibilities:**
- `record_cycle_metrics()` (244-line method, lines 349-593) -- Computes all cycle metrics, validates cycle, records with learner
- Calls `adaptive_learner.add_cycle_metrics()`, `update_convergence_tracking()`, `update_convergence_confidence()`
- Triggers validation mode checks and auto-apply callbacks
- Schedules debounced learning save via `LearningDataStore`

**Dependencies:** `adaptive/learning.py` (AdaptiveLearner), `adaptive/cycle_analysis.py` (CycleMetrics), `adaptive/persistence.py` (LearningDataStore)

**Persistence:** Triggers persistence through LearningDataStore

**Issues:**
- Contains `_calculate_mad()` that **duplicates** `adaptive/robust_stats.py:calculate_mad()`
- `record_cycle_metrics()` at 244 lines is a complexity hotspot

---

#### `managers/pid_gains_manager.py` -- PIDGainsManager (404 lines)

**Key class:** `PIDGainsManager` -- Centralized PID gain mutations with automatic history recording.

**Responsibilities:**
- Single entry point for ALL gain changes (physics init, adaptive apply, rollback, undershoot boost, etc.)
- Owns `_heating_gains` and `_cooling_gains` `PIDGains` objects
- Auto-records snapshots with timestamp, reason (`PIDChangeReason` enum), actor, and optional metrics
- Syncs gains to PIDController after every change
- Handles state restoration with backward compatibility

**Dependencies:** `pid_controller/__init__.py` (PIDController, PIDGains), `const.py` (PIDChangeReason)

**Persistence:** PID history serialized via `restore_from_state()` / state attributes. Mode-keyed history: `{"heating": [...], "cooling": [...]}`

---

#### `managers/pid_tuning.py` -- PIDTuningManager (493 lines)

**Key class:** `PIDTuningManager` -- Service call handlers for PID operations.

**Responsibilities:**
- `async_reset_pid_to_physics()` -- Recalculates gains from thermal properties
- `async_apply_adaptive_pid()` -- Manual apply of learned gains
- `async_auto_apply_adaptive_pid()` -- Auto-apply with safety gates + validation mode start
- `async_rollback_pid()` -- Rollback to previous gains from history
- `async_clear_learning()` -- Clears all learning data and resets

**Dependencies:** `adaptive/physics.py`, `adaptive/learning.py`, `managers/pid_gains_manager.py`

**Persistence:** Delegates to PIDGainsManager

---

#### `managers/ke_manager.py` -- KeManager (377 lines)

**Key class:** `KeManager` -- Manages outdoor temperature compensation (Ke) learning lifecycle.

**Responsibilities:**
- `is_at_steady_state()` -- Tolerance-based steady state detection for observation recording
- `maybe_record_observation()` -- PID convergence gating, steady state check, rate limiting
- `async_apply_adaptive_ke()` -- Applies learned Ke via PIDGainsManager

**Dependencies:** `adaptive/ke_learning.py` (KeLearner), `managers/pid_gains_manager.py` (PIDGainsManager)

**Persistence:** Delegates to KeLearner's `to_dict()` / `from_dict()`, stored via `persistence.py`

---

#### `managers/learning_gate.py` -- LearningGateManager (148 lines)

**Key class:** `LearningGateManager` -- Learning suppression and graduated night setback delta.

**Responsibilities:**
- `is_learning_suppressed()` -- Returns True when learning should be paused (contact open, humidity spike, learning grace period)
- `get_allowed_delta()` -- Returns max allowed night setback delta based on learning progress (0.0/0.5/1.0/None)

**Dependencies:** `adaptive/contact_sensors.py`, `adaptive/humidity_detector.py`, `managers/night_setback_manager.py`, `adaptive/learning.py` (via callback)

**Persistence:** None (queries other components)

**Issues:**
- Computes confidence tier thresholds (`_scaled_tier_1`, `_scaled_tier_2`, `_tier_3`) with the same scaling logic as `auto_apply.py:_compute_learning_status()` -- **duplicated** threshold computation

---

#### `managers/night_setback_manager.py` -- NightSetbackManager (450 lines)

**Key class:** `NightSetbackManager` -- Night setback orchestration with HA integration.

**Responsibilities:**
- Delegates calculation to `NightSetbackCalculator`
- Manages learning grace period (60 min after night setback transitions)
- Applies graduated setback delta from `LearningGateManager`
- Implements auto-learning setback (0.5C setback during 3-5am after 7 days stuck at maintenance cap)
- Tracks transitions (started/ended) for consuming code

**Dependencies:** `adaptive/night_setback.py`, `managers/night_setback_calculator.py`, `managers/learning_gate.py` (via callback)

**Persistence:** Grace period and auto-learning state via `restore_state()`

---

## Data Flow

### Primary Learning Pipeline

```
Temperature Sensor
       |
       v
TemperatureManager (tracks current temp, history)
       |
       v
PIDController.calc() (produces control_output, p/i/d/e/f terms)
       |
       v
HeaterController (actuates heater on/off)
       |
       v
CycleEventDispatcher (emits DEVICE_STATE_CHANGED events)
       |
       v
CycleTrackerManager (IDLE -> HEATING -> SETTLING state machine)
       |
       | [cycle complete]
       v
CycleMetricsRecorder.record_cycle_metrics()
       |
       | [validates cycle, computes CycleMetrics]
       v
AdaptiveLearner.add_cycle_metrics()
       |
       +-- CycleWeightCalculator.calculate_weight()
       |     (classify recovery/maintenance, compute weight)
       |
       +-- update_convergence_confidence()
       |     |
       |     +-- ConfidenceContributionTracker
       |     |     (apply maintenance/heating-rate caps)
       |     |
       |     +-- ConfidenceTracker (store confidence value)
       |
       +-- UndershootDetector.add_cycle_metrics()
       |     (track consecutive failures, rate comparison)
       |
       +-- HeatingRateLearner.add_observation()
             (bin heating rate by delta x outdoor temp)
```

### PID Adjustment Flow

```
AdaptiveLearner.calculate_pid_adjustment()
       |
       | [called periodically or on cycle complete]
       v
Average recent cycle metrics (overshoot, undershoot, drift, etc.)
       |
       v
robust_average() (MAD-based outlier rejection)
       |
       v
Check convergence (all metrics within thresholds?)
       |
       v
evaluate_pid_rules() [pid_rules.py]
       |
       | [returns list of (PIDRule, adjustments)]
       v
resolve_rule_conflicts() (priority-based, highest wins)
       |
       v
Apply learning_rate_multiplier (from ConfidenceTracker; capped at 1.0 for auto-apply)
       |
       v
[auto-apply only] clip each gain to the physics baseline ± 50%; skip if nothing changes
       |
       v
Return PIDChangeSet {delta_kp, delta_ki, delta_kd}
       |
       v
[Caller applies via PIDGainsManager.set_gains()]
```

### Auto-Apply Flow

```
CycleMetricsRecorder.record_cycle_metrics()
       |
       | [after recording, checks auto-apply conditions]
       v
AutoApplyManager.check_auto_apply_safety_gates()
       |
       +-- ValidationManager.is_in_validation_mode()
       |
       +-- ValidationManager.check_auto_apply_limits()
       |     (lifetime cap, seasonal cap, drift from baseline)
       |
       +-- ValidationManager.check_seasonal_shift()
       |
       v [gates pass -> min_cycles, cooldown hours/cycles]
PIDTuningManager.async_auto_apply_adaptive_pid()
       |
       v
AdaptiveLearner.calculate_pid_adjustment()
       |     (physics baseline required, cooldown rate limit, min_cycles of history,
       |      learning rate <= 1.0, gains clipped to drift limit)
       |
       v
PIDGainsManager.set_gains(reason=AUTO_APPLY)   [integral kept]
       |
       v
AdaptiveLearner.clear_history(mode) + mark_adjustment_applied()
       |     (only the applied mode; the other mode keeps its learning)
       v
ValidationManager.start_validation_mode()
       | [monitor next N cycles; fail if avg overshoot > max(baseline × 1.3, overshoot_max)]
       | [rollback: restore previous gains, integral kept, clear_history(mode), restart cooldown]
```

### Ke Learning Flow (Parallel Path)

```
PIDController.calc() [at steady state]
       |
       v
KeManager.maybe_record_observation()
       |
       | [checks: PID converged, steady state, rate limit]
       v
KeLearner.add_observation()
       |
       | [enough observations accumulated]
       v
KeLearner.calculate_ke_adjustment()
       | (Pearson correlation analysis)
       v
KeManager.async_apply_adaptive_ke()
       |
       v
PIDGainsManager.set_gains(reason=KE_LEARNING, ke=new_ke)
```

### Undershoot Detection Flow (Parallel Path)

```
_async_control_heating() [every PID update]
       |
       v
UndershootDetector.update_real_time(current_temp, setpoint)
       | [accumulates thermal debt when below setpoint]
       |
       +-- should_adjust_ki()? [checks: debt > threshold, cooldown, cap]
       |     |
       |     v [yes]
       |   AdaptiveLearner notifies caller
       |     |
       |     v
       |   PIDGainsManager.set_gains(reason=UNDERSHOOT_BOOST, ki=boosted)
       |
CycleMetricsRecorder [on cycle complete]
       |
       v
UndershootDetector.add_cycle_metrics()
       | [tracks consecutive failures]
       |
       +-- should_adjust_ki()? [checks: N consecutive failures]
             |
             v [yes]
           Same boost path as above
```

### Learning Suppression Flow

```
LearningGateManager.is_learning_suppressed()
       |
       +-- ContactSensorHandler.is_any_contact_open()? -> suppress
       +-- HumidityDetector.should_pause()? -> suppress
       +-- NightSetbackManager.in_learning_grace_period? -> suppress
       |
       v [if suppressed]
CycleMetricsRecorder skips learning data recording

LearningGateManager.get_allowed_delta()
       |
       +-- ConfidenceTracker confidence + cycle count
       |     -> 0.0 (suppress) / 0.5 / 1.0 / None (unlimited)
       |
       v
NightSetbackManager applies graduated delta
```

## Cross-Cutting Concerns

### 1. Duplicated Code

| Duplication | Location A | Location B | Impact |
|------------|-----------|-----------|--------|
| `calculate_pearson_correlation()` | `pid_rules.py:~450` | `ke_learning.py:~350` | Different implementations (manual vs statistics.stdev) |
| `calculate_mad()` / `_calculate_mad()` | `robust_stats.py:~140` | `cycle_metrics.py:~600` | Identical logic, should import from robust_stats |
| Heating rate learning | `thermal_rates.py:ThermalRateLearner` | `heating_rate_learner.py:HeatingRateLearner` | Different approaches to same problem; `preheat.py` adds a third binned approach |
| Heating type rate dicts | `night_setback.py:126,152` | `const.py`, `physics.py`, `heating_rate_learner.py` | Hardcoded per-type values scattered across 16+ locations |

### 2. Complexity Hotspots

**Ranked by method length x cyclomatic complexity:**

1. **`AdaptiveLearner.calculate_pid_adjustment()`** (334 lines, `learning.py:514-847`) -- Averages metrics, applies outlier rejection, checks convergence, evaluates rules, resolves conflicts, applies scaling. Could delegate the metric averaging and convergence checking to separate functions.

2. **`CycleMetricsRecorder.record_cycle_metrics()`** (244 lines, `cycle_metrics.py:349-593`) -- Validates cycle, computes all metrics, records with learner, triggers callbacks. Orchestration complexity: calls into 6+ different sub-systems.

3. **`evaluate_pid_rules()`** (239 lines, `pid_rules.py:162-400`) -- Evaluates 9 rules sequentially with complex conditional logic for each. Each rule has different threshold checks and adjustment formulas.

4. **`AdaptiveLearner.update_convergence_confidence()`** (144 lines, `learning.py:1052-1196`) -- Weighted confidence update with cycle classification, contribution caps, tier gating. This logic should live in `confidence.py` or `confidence_contribution.py`.

5. **`NightSetbackManager.calculate_night_setback_adjustment()`** (151 lines, `night_setback_manager.py:222-380`) -- Branching logic for auto-learning setback, graduated delta, old vs new callback paths, transition detection.

6. **`CycleTrackerManager.__init__()`** (161 lines, `cycle_tracker.py:57-218`) -- Initializes 43 attributes. Many could be grouped into dataclasses.

### 3. Unclear Responsibility Boundaries

**Confidence management is split three ways:**
- `ConfidenceTracker` (`confidence.py`) -- Holds per-mode values
- `AdaptiveLearner` (`learning.py:1052-1196`) -- Actually computes and updates confidence with weighted logic
- `ConfidenceContributionTracker` (`confidence_contribution.py`) -- Applies caps to confidence gains

The split creates a situation where `ConfidenceTracker` appears to be the authority on confidence, but `AdaptiveLearner` writes directly to the stored values.

**Heating rate learning exists in three forms:**
- `ThermalRateLearner` (`thermal_rates.py`) -- Segment-based rate learning from temperature history
- `HeatingRateLearner` (`heating_rate_learner.py`) -- Binned observation tracking from cycle metrics
- `PreheatLearner` (`preheat.py`) -- Has its own binned observations OR delegates to HeatingRateLearner

Each serves a slightly different consumer, but the domain overlap is significant and confusing.

**Night setback has naming confusion:**
- `NightSetback` class in `adaptive/night_setback.py` -- Data model
- `NightSetbackManager` class in `adaptive/night_setback.py` -- Multi-zone data model manager
- `NightSetbackManager` class in `managers/night_setback_manager.py` -- HA integration orchestration
- `NightSetbackCalculator` class in `managers/night_setback_calculator.py` -- Calculation logic

Two classes with the same name (`NightSetbackManager`) exist in different modules with different responsibilities.

### 4. Backward-Compatibility Burden

`AdaptiveLearner` has ~30 property aliases (lines 195-315) that expose private attribute internals through public properties. These exist solely for tests that directly access internal state. Examples:

- `_cycle_history` -> property alias
- `_heating_convergence_confidence` -> property alias
- `_auto_apply_count` -> property alias

`ConfidenceContributionTracker` has ~8 similar aliases (lines 58-93).

This pattern makes the internal structure of these classes effectively frozen, since changing the internal representation would break all test aliases. The correct fix is to update tests to use public API methods rather than accessing internals.

### 5. Ki Boost: Multiple Independent Paths

Ki (integral gain) can be boosted through two mechanisms, both in `UndershootDetector` and sharing its cooldown and caps:

1. **UndershootDetector real-time mode** -- Thermal debt accumulation triggers Ki boost
2. **UndershootDetector cycle mode** -- Consecutive approach failures trigger Ki boost

Each boost calls `mark_adjustment_applied()`, so the auto-apply cooldown restarts and an auto-apply cannot multiply Ki again on the same cycles.

### 6. Dead or Near-Dead Code

- `DisturbanceDetector` (`disturbance_detector.py`) -- Referenced in CLAUDE.md but no evidence of actual integration into the cycle recording pipeline (no imports found in `cycle_metrics.py` or `cycle_tracker.py`)
- `ThermalRateLearner` (`thermal_rates.py`) -- Unclear if actively used; `HeatingRateLearner` and `PreheatLearner` serve the same consumers with more sophisticated approaches

## Potential Simplifications

### High Impact

1. **Move `update_convergence_confidence()` to `ConfidenceTracker`** -- The 144-line method in `AdaptiveLearner` should be the implementation inside `ConfidenceTracker`, with the learner calling through rather than writing its values directly.

2. **Consolidate heating rate learning** -- Choose one binned approach (`HeatingRateLearner`) and have `PreheatLearner` always delegate to it. Evaluate whether `ThermalRateLearner` is still needed.

3. **Extract a shared `pearson_correlation()` utility** -- Used by both `pid_rules.py` and `ke_learning.py` with different implementations.

5. **Import `calculate_mad()` from `robust_stats.py`** -- Remove the duplicate in `cycle_metrics.py`.

### Medium Impact

6. **Coordinate Ki boost paths** -- Unify the three Ki boost mechanisms under `UndershootDetector` with a shared cumulative cap.

7. **Remove backward-compatibility aliases** -- Update tests to use public API methods instead of internal attribute aliases.

8. **Break up `calculate_pid_adjustment()`** -- Extract metric averaging, convergence checking, and scaling into separate functions. The 334-line method should be 3-4 composed steps.

9. **Break up `record_cycle_metrics()`** -- The 244-line orchestration method should be decomposed into phases: validate, compute, record, notify.

### Lower Impact

10. **Resolve NightSetbackManager naming** -- Rename the `adaptive/night_setback.py` version to avoid confusion with the manager in `managers/`.

11. **Audit DisturbanceDetector integration** -- Either integrate it into the cycle recording pipeline or remove it.

12. **Consolidate heating-type dicts** -- Part of the broader `HeatingTypeProfile` consolidation identified in `codebase-overview.md` (Priority 2).

---

## AdaptiveLearner Method Groups

`AdaptiveLearner` (`learning.py`) has 38 callable methods/properties and 27+ instance variables. They form seven coherent groups.

### Group 1 -- Cycle ingestion and history management

| Method | Line | Notes |
|---|---|---|
| `add_cycle_metrics()` | 323 | Appends, fires undershoot side-effect, FIFO eviction |
| `get_cycle_count()` | 394 | `len()` wrapper |
| `cycle_history` / `_cycle_history` property pair | 185-204 | Backward-compat aliases for tests |

**State owned:** `_heating_cycle_history`, `_cooling_cycle_history`, `_max_history`, `_cycles_since_last_adjustment` (shared with Group 2).

**Dependencies:** calls `_undershoot_detector.add_cycle()` (Group 5 side-effect embedded here).

---

### Group 2 -- PID adjustment calculation and rate-limiting

| Method | Line | Notes |
|---|---|---|
| `calculate_pid_adjustment()` | 514 | 334-line monolith -- see pipeline analysis below |
| `_check_rate_limit()` | 460 | Hybrid time+cycle gate |
| `_check_convergence()` | 411 | 7-metric threshold check |
| `get_last_adjustment_time()` | 849 | Trivial getter |

**State owned:** `_last_adjustment_time`, `_cycles_since_last_adjustment`, `_rule_state_tracker`, `_convergence_thresholds`, `_rule_thresholds`, `_heating_type`.

**Dependencies:** reads Group 3 for `get_learning_rate_multiplier()`; delegates to Group 4 for auto-apply gates; calls `pid_rules.py` functions.

---

### Group 3 -- Convergence confidence and learning rate

| Method | Line | Notes |
|---|---|---|
| `update_convergence_confidence()` | 1052 | 144-line weighted update -- writes `ConfidenceTracker` values directly |
| `get_convergence_confidence()` | 1030 | Thin delegator |
| `get_auto_apply_count()` | 1041 | Thin delegator |
| `apply_confidence_decay()` | 1228 | Thin delegator |
| `get_learning_rate_multiplier()` | 1237 | Thin delegator |
| `can_reach_learning_tier()` | 1510 | Thin delegator to `_contribution_tracker` |
| Property aliases: `_heating_convergence_confidence`, `_cooling_convergence_confidence`, etc. | 207-265 | 6 aliases, exist only for tests |

**State owned (via delegates):** `_confidence: ConfidenceTracker`, `_weight_calculator: CycleWeightCalculator`, `_contribution_tracker: ConfidenceContributionTracker`.

**Key issue:** `update_convergence_confidence()` writes directly to `self._confidence._heating/cooling_convergence_confidence` (private attribute bypass), making `ConfidenceTracker` a passive data bag rather than an authority.

---

### Group 4 -- Validation and auto-apply safety gates

| Method | Line | Notes |
|---|---|---|
| `set_physics_baseline()` | 908 | Thin delegator |
| `calculate_drift_from_baseline()` | 922 | Thin delegator |
| `check_performance_degradation()` | 1197 | Thin delegator |
| `check_seasonal_shift()` | 1214 | Thin delegator |
| `start_validation_mode()` | 1264 | Thin delegator |
| `add_validation_cycle()` | 1276 | Thin delegator |
| `is_in_validation_mode()` | 1292 | Thin delegator |
| `check_auto_apply_limits()` | 1300 | Thin delegator |
| `record_seasonal_shift()` | 1333 | Thin delegator |
| Property aliases: `_validation_mode`, `_last_seasonal_check`, etc. | 267-322 | 9 aliases, exist only for tests |

**State owned (via delegates):** `_validation: ValidationManager`, `_auto_apply: AutoApplyManager`.

**Dependencies:** `_auto_apply.check_auto_apply_safety_gates()` receives `_confidence` and `_contribution_tracker` from Group 3 at call time.

---

### Group 5 -- Undershoot detection and Ke-convergence gate

| Method | Line | Notes |
|---|---|---|
| `update_undershoot_detector()` | 1343 | Thin delegator, called per PID tick |
| `check_undershoot_adjustment()` | 1363 | Reads PID history, calls `should_adjust_ki()`, applies multiplier, decreases confidence (writes Group 3 state directly) |
| `undershoot_detector` property | 1501 | Exposes detector for serialization |
| `update_convergence_tracking()` | 944 | Tracks consecutive converged cycles for Ke-readiness |
| `is_pid_converged_for_ke()` | 997 | Trivial boolean getter |
| `get_consecutive_converged_cycles()` | 1006 | Trivial int getter |
| `reset_ke_convergence()` | 1014 | Resets Ke-readiness gate |

**State owned:** `_undershoot_detector: UndershootDetector`, `_heating_rate_learner: HeatingRateLearner`, `_consecutive_converged_cycles`, `_pid_converged_for_ke`.

**Key issue:** `check_undershoot_adjustment()` writes directly to `self._confidence._heating/cooling_convergence_confidence` -- a second confidence bypass (same problem as Group 3).

---

### Group 6 -- Heating rate learning

| Method | Line | Notes |
|---|---|---|
| `get_heating_rate()` | 1250 | Thin delegator to `_heating_rate_learner` |

**State owned (via delegate):** `_heating_rate_learner: HeatingRateLearner`.

---

### Group 7 -- Lifecycle: reset, serialization, restoration

| Method | Line | Notes |
|---|---|---|
| `clear_history()` | 858 | Full reset; re-creates contribution tracker and heating rate learner (no reset methods on those) |
| `get_previous_pid()` | 896 | Deprecated, always returns None |
| `to_dict()` | 1522 | Thin delegator to `learner_serialization.learner_to_dict()` |
| `restore_from_dict()` | 1554 | Delegates to serialization, then manually applies each restored field |
| `_perform_historic_scan()` | 1620 | Re-feeds cycle history to undershoot detector on restore |

---

### Thin delegators that could be eliminated

The following 21 methods have bodies that are a single `return self._x.y(args)` call with no logic. If callers held direct references to the sub-managers, these wrappers would not need to exist:

`get_cycle_count()`, `get_last_adjustment_time()`, `apply_confidence_decay()`, `get_learning_rate_multiplier()`, `get_convergence_confidence()`, `get_auto_apply_count()`, `can_reach_learning_tier()`, `set_physics_baseline()`, `calculate_drift_from_baseline()`, `check_performance_degradation()`, `check_seasonal_shift()`, `start_validation_mode()`, `add_validation_cycle()`, `is_in_validation_mode()`, `check_auto_apply_limits()`, `record_seasonal_shift()`, `get_heating_rate()`, `update_undershoot_detector()`, `is_pid_converged_for_ke()`, `get_consecutive_converged_cycles()`, `get_previous_pid()` (deprecated, remove entirely).

---

## Proposed Manager Decomposition (4-5 managers replacing AdaptiveLearner)

### Manager 1 -- `CycleHistoryManager`

Owns cycle storage, FIFO eviction, disturbed-cycle filtering, and metric averaging. Answers: "given the history, what are the robust-averaged metrics for the last N undisturbed cycles?"

**State:** `_heating_cycle_history`, `_cooling_cycle_history`, `_max_history`.

**Methods absorbed from AdaptiveLearner:** `add_cycle_metrics()` (minus undershoot side-effect), `get_cycle_count()`, the `recent_cycles` slicing (lines 612-625), and the full metric-averaging block (lines 627-728).

**New type introduced:**
```python
@dataclass
class AveragedCycleMetrics:
    avg_overshoot: float
    avg_undershoot: float
    avg_oscillations: float
    avg_rise_time: float
    avg_settling_time: float
    avg_inter_cycle_drift: float
    avg_settling_mae: float
    avg_decay_contribution: float | None
    avg_integral_at_tolerance: float | None
    outdoor_temp_values: list[float]
    rise_time_values: list[float]
```

---

### Manager 2 -- `PIDRuleEvaluator`

Owns the rule evaluation pipeline: convergence check, rate limiting, rule firing, PWM filter, conflict resolution, learning-rate scaling, gain clamping.

**State:** `_last_adjustment_time`, `_cycles_since_last_adjustment`, `_rule_state_tracker`, `_convergence_thresholds`, `_rule_thresholds`, `_heating_type`.

**Methods absorbed:** `_check_convergence()`, `_check_rate_limit()`, `get_last_adjustment_time()`, and the core body of `calculate_pid_adjustment()` (Steps 5-6 of the pipeline).

**Single public method:**
```python
def evaluate(
    self,
    metrics: AveragedCycleMetrics,
    current_gains: PIDGains,
    learning_rate: float,
    pwm_seconds: float,
) -> dict[str, float] | None
```

---

### Manager 3 -- `ConvergenceConfidenceManager`

Consolidates the three-way confidence split: `ConfidenceTracker` (data holder), `AdaptiveLearner.update_convergence_confidence()` (bypasses tracker), `ConfidenceContributionTracker` (caps).

**State:** all of `ConfidenceTracker`, all of `ConfidenceContributionTracker`, `_consecutive_converged_cycles`, `_pid_converged_for_ke`.

**Methods absorbed:** `update_convergence_confidence()` (144 lines, moves here as the canonical implementation -- `ConfidenceTracker.update_convergence_confidence()` is dead code and gets removed), `apply_confidence_decay()`, `get_convergence_confidence()`, `get_learning_rate_multiplier()`, `can_reach_learning_tier()`, `update_convergence_tracking()`, `is_pid_converged_for_ke()`, `get_consecutive_converged_cycles()`, `reset_ke_convergence()`.

**Key fix:** eliminates the private attribute bypass; all confidence writes go through a single public method.

---

### Manager 4 -- `ValidationSafetyManager`

Merges `ValidationManager` and `AutoApplyManager` into one. Both exist to answer "should auto-apply be blocked?" -- the split between them is artificial.

**State:** all of `ValidationManager`, absorbs `AutoApplyManager`.

**Key fix:** exposes `compute_learning_status(cycle_count, confidence, mode) -> LearningStatus` as a module-level function shared with `LearningGateManager`. This eliminates the learning-status duplication identified in the Cross-Cutting Concerns section.

**Methods absorbed:** `set_physics_baseline()`, `calculate_drift_from_baseline()`, `check_performance_degradation()`, `check_seasonal_shift()`, `start_validation_mode()`, `add_validation_cycle()`, `is_in_validation_mode()`, `check_auto_apply_limits()`, `record_seasonal_shift()`.

---

### Manager 5 -- `UndershootKiManager`

Owns the Ki-boost path (real-time and cycle modes) under one shared cumulative cap.

**State:** `UndershootDetector` (both modes).

**Methods absorbed:** `update_undershoot_detector()`, `check_undershoot_adjustment()`, `undershoot_detector` property (for serialization).

**Key fix:** `check_undershoot_adjustment()` currently writes directly to `_confidence` (confidence bypass). In this decomposition, it returns a `KiBoostResult` and the caller decrements confidence via `ConvergenceConfidenceManager`. No cross-manager state writes.

**Single interface:**
```python
def check_and_apply(
    self,
    temp: float,
    setpoint: float,
    dt_seconds: float,
    cold_tolerance: float,
    cycles_completed: int,
    current_ki: float,
    pid_history: list[dict],
    mode: HVACMode,
) -> KiBoostResult | None
```

---

## `calculate_pid_adjustment()` Pipeline Decomposition

The 334-line method (lines 514-847) is a linear sequence of 6 discrete steps. None of the steps share mutable state with each other -- they pass results forward as local variables, making extraction straightforward.

```
Step 1: Mode context setup          (566-574,  ~9 lines)
Step 2: Auto-apply safety gate      (576-598, ~22 lines)  -- only when check_auto_apply=True
Step 3: Rate limit + data guard     (600-625, ~25 lines)  -- 3 sequential None-returns
Step 4: Metric averaging            (627-744, ~118 lines) -- prime extraction target
Step 5: Convergence check           (746-757, ~11 lines)  -- already a method
Step 6: Rule eval + apply + clamp   (759-847, ~88 lines)
```

**Step 4 detail -- Metric averaging (lines 627-744):**
Eight robust-averaged scalars computed independently. Overshoot is special: it has two sub-paths (`controllable_overshoot` attribute preferred over `overshoot` for backward compat, then clamped-cycle amplification). The other seven metrics are structurally identical. Extracting this as `_compute_averaged_metrics(recent_cycles) -> AveragedCycleMetrics` removes 118 lines from the parent and creates a separately testable unit.

**Step 6 detail -- Rule eval + apply + clamp (lines 759-847):**
Three composable sub-steps:
- `_evaluate_and_filter_rules(averaged, pwm_seconds) -> list[PIDRuleResult] | None` -- calls `evaluate_pid_rules()`, applies PWM filter, calls `detect/resolve_rule_conflicts()`
- `_apply_rules_with_scaling(rule_results, current_gains, learning_rate) -> tuple[float, float, float]` -- the multiplicative application loop (lines 815-832)
- Gain clamping (lines 834-837) -- 3 lines, stays at the call site or moves into `_apply_rules_with_scaling`

**After extraction, the method body becomes ~25 lines:**

```python
def calculate_pid_adjustment(self, ...) -> dict[str, float] | None:
    mode = mode or get_hvac_heat_mode()
    cycle_history, convergence_confidence = self._select_mode_state(mode)

    if check_auto_apply:
        gates_passed, min_interval_hours, min_adjustment_cycles, min_cycles = (
            self._auto_apply.check_auto_apply_safety_gates(...)
        )
        if not gates_passed:
            return None

    if self._check_rate_limit(min_interval_hours, min_adjustment_cycles):
        return None

    recent_cycles = self._select_recent_undisturbed(cycle_history, min_cycles)
    if recent_cycles is None:
        return None

    averaged = self._compute_averaged_metrics(recent_cycles)

    if self._check_convergence(**averaged.convergence_kwargs()):
        return None

    rule_results = self._evaluate_and_filter_rules(averaged, pwm_seconds)
    if not rule_results:
        return None

    learning_rate = self.get_learning_rate_multiplier(convergence_confidence)
    new_kp, new_ki, new_kd = self._apply_rules_with_scaling(
        rule_results, (current_kp, current_ki, current_kd), learning_rate
    )

    self._last_adjustment_time = dt_util.utcnow()
    self._cycles_since_last_adjustment = 0
    return {"kp": new_kp, "ki": new_ki, "kd": new_kd}
```

Each extracted helper is independently testable. `AveragedCycleMetrics` becomes a first-class type usable by debug sensors or logging without re-running the full pipeline.
