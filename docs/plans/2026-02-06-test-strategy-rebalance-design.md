# Test Strategy Rebalance: Integration-First Testing

## Problem

2803 tests, 94% unit / 6% integration. Mock-heavy tests in `test_climate.py` (99 mocks, 38 fragile tests) and `test_cycle_tracker.py` (62 mocks, ~10 fragile) break on harmless refactors, give false confidence. Critical workflows (persistence round-trip, multi-zone, night setback lifecycle) have zero integration coverage.

## Goals

- Rewrite `test_climate.py` around observable behavior, not private attributes
- Trim fragile implementation-detail tests from `test_cycle_tracker.py`
- Add 12 integration tests covering real user workflows
- Net result: ~2740 tests (was 2803), 12% integration (was 6%), dramatically better refactoring resilience

## Principles

- Test what user/system observes, not internal wiring
- No `_private` attribute assertions — use public properties and `extra_state_attributes`
- No `mock.call_count` — assert outcomes
- Integration tests use real component instances, mock only HA infrastructure (`hass`, entity registry)
- Each integration test tells a story: setup -> action -> observable result

---

## Phase 1a: Shared Test Infrastructure

New fixtures in `tests/conftest.py`:

### `make_thermostat` factory

Creates **real instances** of AdaptiveLearner, PIDController, CycleTracker, managers. Only `hass` is mocked. Key difference from current tests — actual component graph, not mock web.

```python
@pytest.fixture
def make_thermostat(mock_hass):
    """Create real AdaptiveLearner + managers with only hass mocked."""
    def factory(heating_type=HeatingType.RADIATOR, **overrides):
        ...
    return factory
```

### `mock_hass` (shared)

Minimal HA mock: states, services, event bus. Replaces per-file duplicates (~60-80% of setup code currently repeated).

### `time_travel` helper

Patches `dt_util.utcnow()` and `time.monotonic()` consistently. Critical for integration tests spanning minutes/hours of simulated time.

---

## Phase 1b: New Integration Tests (12 tests, 4 files)

### File A: `test_integration_control_loop.py`

**A1: Full PID feedback loop**
- Components: PID -> HeaterController -> CycleTracker -> Learning
- Scenario: Temp sensor updates -> PID computes output -> heater toggles -> cycle completes -> learning records metrics
- Assert: cycle count increments, learning status progresses

**A2: Transport delay propagation**
- Components: Coordinator -> Manifold -> PWM -> CycleMetrics
- Scenario: Configure manifold with pipe delay -> verify PWM duty adjustment accounts for committed heat -> cycle metrics split controllable vs committed overshoot

**A3: Setpoint change response**
- Components: SetpointBoost -> PID integral -> HeaterController
- Scenario: Change setpoint +2C -> integral gets boost -> heater responds within 1 cycle. Change -2C -> integral decays.

### File B: `test_integration_night_setback_lifecycle.py`

**B1: Full setback -> preheat -> recovery**
- Components: NightSetbackManager -> LearningGate -> PreheatLearner -> HeatingRateLearner
- Scenario: Night period starts -> setpoint drops by graduated delta -> preheat calculates start time -> heating starts early -> recovery session tracked -> observation banked

**B2: Learning gate graduation**
- Components: LearningGate -> AdaptiveLearner -> NightSetbackManager
- Scenario: Fresh system: delta suppressed to 0C. After 3 cycles: 0.5C. After stable: 1.0C. After tuned: full delta. Assert `status.overrides` shows correct `limited_to` at each stage.

**B3: Setback + pause interaction**
- Components: NightSetback -> ContactSensor -> StatusManager
- Scenario: Night setback active, contact opens -> heating pauses (priority) -> contact closes -> setback still active -> recovery resumes correctly

### File C: `test_integration_persistence.py`

**C1: Full save -> restore round-trip**
- Components: LearningDataStore -> AdaptiveLearner -> StateRestorer -> PIDGainsManager
- Scenario: Build up learning state (10 cycles, confidence 40%, custom gains, 3 heating rate observations) -> serialize -> create fresh learner -> restore -> assert all state matches: gains, confidence, cycle count, heating rate bins

**C2: Restore with version migration**
- Components: LearningDataStore -> learner_serialization
- Scenario: Craft v7 serialization payload -> restore into current version -> verify undershoot detector fields migrated, no data loss on older fields

**C3: Restore degraded data gracefully**
- Components: LearningDataStore -> AdaptiveLearner
- Scenario: Feed corrupted/partial JSON -> learner initializes to safe defaults without crash. Assert logging warns.

### File D: `test_integration_overrides.py`

**D1: Override priority stacking**
- Components: StatusManager -> ContactSensor -> HumidityDetector -> NightSetback
- Scenario: Activate night setback -> contact opens -> humidity spikes -> verify `status.overrides` ordered by priority. Remove contact -> humidity becomes top. Clear all -> normal operation.

**D2: Contact pause + learning resilience**
- Components: ContactSensor -> StatusManager -> AdaptiveLearner -> HeatingRateLearner
- Scenario: Mid-cycle contact opens -> learning status "idle" -> active heating rate session discarded -> contact closes -> new session starts -> previous cycle metrics NOT banked (tainted)

**D3: Humidity pause + integral decay**
- Components: HumidityDetector -> PIDController -> StatusManager
- Scenario: Shower detected -> heating pauses -> integral decays ~10%/min -> shower ends -> stabilization delay -> heating resumes with reduced integral -> PID recovers without overshoot

---

## Phase 2a: Rewrite `test_climate.py`

**Current:** 90 tests, 3,406 lines, 99 mock calls. 38 fragile tests.

### Delete (38 tests)

| Class | Count | Reason |
|-------|-------|--------|
| `TestSetupStateListeners` | 5 | Asserts listener registration call counts |
| `TestClimateDispatcherIntegration` | 14 | Asserts `._cycle_dispatcher is dispatcher` |
| `TestClimateNoDirectCTMCalls` | 8 | Asserts what *shouldn't* be called |
| Transport delay wiring | 6 | Covered by integration test A2 |
| Remaining wiring assertions | 5 | Pure mock plumbing |

### Rewrite (52 tests) — new structure by feature

```
TestHeaterControl          - turn on/off, error handling, recovery
TestPresetModes            - away/eco/boost/comfort mode switching
TestTemperatureTracking    - sensor updates, tolerance, filtering
TestStateRestoration       - RestoreEntity round-trip via public attributes
TestNightSetback           - setback activation, delta, learning gate
TestServiceCalls           - set_pid_gains, reset_learning, apply_learned
TestStateAttributes        - extra_state_attributes structure validation
TestHVACModes              - heat/cool/off transitions, mode constraints
```

### Key assertion pattern change

```python
# BEFORE (fragile):
assert thermostat._heater_control_failed is True
assert mock_hass.services.async_call.call_count == 2

# AFTER (behavioral):
assert thermostat.hvac_action == "idle"
assert thermostat.extra_state_attributes["status"]["activity"] == "idle"
```

---

## Phase 2b: Trim `test_cycle_tracker.py`

**Current:** 69 tests, 4,034 lines. 85% already behavioral (good).

### Delete (~10 tests)

- Mock callback assertion tests (4) — `learning_callback.call_count == 1`
- HA scheduling mock tests (3) — `async_track_time_interval` called
- Internal timestamp assertions (3) — `_cycle_start_time` values

### Keep (59 tests)

- State machine transitions (IDLE->HEATING->SETTLING) via `state` property
- Settling algorithm (MAD, outlier filtering) — pure logic, no mocks
- Temperature history tracking — behavioral
- Cycle completion events — assert event contents

**Result:** 69 -> ~59 tests, ~3,400 lines.

---

## Phase 3: Verify & Clean Up

- Run full test suite, confirm green
- Remove any orphaned fixtures/helpers
- Verify no coverage regression on critical paths

---

## Execution Order & Risk

| Phase | Work | Risk | Depends on |
|-------|------|------|------------|
| 1a | Shared fixtures in conftest.py | Low | - |
| 1b | 12 integration tests (Files A-D) | Low | 1a |
| 2a | Rewrite test_climate.py | Medium | 1b (safety net) |
| 2b | Trim test_cycle_tracker.py | Low | 1b |
| 3 | Verify full suite, clean up | Low | 2a, 2b |

Phase 1 first — integration tests act as safety net before deleting unit tests. If integration test fails after deleting a unit test, we know we lost real coverage.

**Rollback:** Each phase is a separate commit. Phase 2a is riskiest — can revert and add targeted integration tests instead.

## Expected Outcome

| Metric | Before | After |
|--------|--------|-------|
| Total tests | 2803 | ~2740 |
| Unit / Integration | 94% / 6% | 88% / 12% |
| Mock calls in test_climate.py | 99 | ~15 |
| Private attribute assertions | 30+ | 0 |
| Critical workflow coverage | 6 flows | 18 flows |
| Refactoring resilience | Low | High |
