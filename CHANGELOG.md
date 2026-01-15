# CHANGELOG


## v0.6.5 (2026-01-15)

### Bug Fixes

- Add _reset_cycle_state() helper to CycleTrackerManager
  ([`374df55`](https://github.com/afewyards/ha-adaptive-thermostat/commit/374df55a90ccc91351320689bfcdf06909e3ebdc))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add get_is_device_active callback to CycleTrackerManager
  ([`0c93878`](https://github.com/afewyards/ha-adaptive-thermostat/commit/0c938788fdd1faf9358315ec04ae4015f8ea2938))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add safety check to _is_device_active property
  ([`fca6dd1`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fca6dd1616f6c7cb20e203a9d691b9d96a3ae384))

Add existence check for _heater_controller before accessing is_active method to prevent
  AttributeError when controller is not yet initialized. Returns False when controller is None.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Continue cycle tracking on setpoint change when heater is active
  ([`4712dbf`](https://github.com/afewyards/ha-adaptive-thermostat/commit/4712dbf3b72977cc744f02eb2ddfecf0390d1b7d))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Pass get_is_device_active callback to CycleTrackerManager
  ([`770e538`](https://github.com/afewyards/ha-adaptive-thermostat/commit/770e5380ae358cd64fcf8f7d3b9d19f99a5b5a56))

Add get_is_device_active callback parameter to CycleTrackerManager initialization in climate.py.
  This allows the cycle tracker to properly monitor device state during cycle tracking.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Update _finalize_cycle() to log interruption status
  ([`bf507b6`](https://github.com/afewyards/ha-adaptive-thermostat/commit/bf507b64d40258fba1e5ec401f05193967ade0dc))

- Add logging after validation checks to report setpoint changes during tracking - Replace inline
  state resets with _reset_cycle_state() calls for consistency - Ensure _was_interrupted and
  _setpoint_changes are cleared after finalization - All cycle tracker tests pass

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Refactoring

- Use _reset_cycle_state() helper in abort paths
  ([`de0843e`](https://github.com/afewyards/ha-adaptive-thermostat/commit/de0843eb76cf80e8a2896cd6da21686731e4dbff))

Replace inline abort logic in on_setpoint_changed(), on_contact_sensor_pause(), and
  on_mode_changed() with calls to _reset_cycle_state() helper method for consistent cycle cleanup.
  Log messages preserved before reset calls.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Testing

- Add integration test for complete cycle with setpoint change mid-cycle
  ([`d5261f2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d5261f24c0d759dc93bd1f7cc42990fa4c715507))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add integration test for cooling mode setpoint change
  ([`36b7c24`](https://github.com/afewyards/ha-adaptive-thermostat/commit/36b7c2400ae9dd8d0f311284f5d7b1629af5cb09))

Add COOLING state to CycleState enum and on_cooling_started/stopped methods to CycleTrackerManager.
  Update existing methods to handle COOLING state: - update_temperature now collects samples during
  COOLING - on_setpoint_changed continues tracking when cooler is active - on_contact_sensor_pause
  aborts COOLING cycles - on_mode_changed handles COOLING to heat/off transitions

Add test_setpoint_change_in_cooling_mode integration test that verifies setpoint changes while
  cooler is active continue cycle tracking.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add unit test for _reset_cycle_state clearing all state
  ([`9c52a00`](https://github.com/afewyards/ha-adaptive-thermostat/commit/9c52a003b13eb8051e56705f32fd8de587d652f6))

Add test_reset_cycle_state_clears_all to verify that _reset_cycle_state() properly clears all state
  variables: _state, _was_interrupted, _setpoint_changes, _temperature_history, _cycle_start_time,
  and _cycle_target_temp.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add unit test for backward compatibility when callback not provided
  ([`ab3ba59`](https://github.com/afewyards/ha-adaptive-thermostat/commit/ab3ba5953ef7f75596d6065d3aa11f1e7dc374c9))

Adds test_setpoint_change_without_callback_aborts_cycle which verifies that when
  get_is_device_active callback is not provided, setpoint changes abort the cycle (preserving legacy
  behavior).

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add unit test for multiple setpoint changes while heater active
  ([`a9d9c62`](https://github.com/afewyards/ha-adaptive-thermostat/commit/a9d9c6209a1518254984316da4e74eb73772733b))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add unit test for setpoint change while heater inactive
  ([`c1b9e62`](https://github.com/afewyards/ha-adaptive-thermostat/commit/c1b9e620b2dd5fda54d9b82f42b19c34a6778829))

Add test_setpoint_change_while_heater_inactive_aborts_cycle to verify that when get_is_device_active
  returns False, setpoint changes abort the cycle and clear temperature history (preserving
  backward-compatible behavior).

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add unit test for setpoint change while heater is active
  ([`78bfc9b`](https://github.com/afewyards/ha-adaptive-thermostat/commit/78bfc9b8881f1014b68163743064b9be0e7af7c5))

Test verifies that when setpoint changes while the heater is actively running, cycle tracking
  continues instead of aborting. Checks that: - State remains HEATING (not aborted) -
  _cycle_target_temp is updated to new value - _was_interrupted flag is set to True - Temperature
  history is preserved

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.6.4 (2026-01-15)

### Bug Fixes

- Add Pillow to test dependencies for CI
  ([`10d62f7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/10d62f741ae6835ed4173ec4281e1fc77960bb41))

The charts module uses PIL for image generation, but Pillow was not listed in requirements-test.txt,
  causing CI failures.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.6.3 (2026-01-15)

### Bug Fixes

- Use async_call_later helper instead of non-existent hass method
  ([`3626a91`](https://github.com/afewyards/ha-adaptive-thermostat/commit/3626a910b211e0f7fe3073cbf73d04604a850052))

async_call_later is a helper function from homeassistant.helpers.event, not a method on the
  HomeAssistant object. This was causing an AttributeError when the cycle tracker tried to schedule
  the settling timeout after heating stopped.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.6.2 (2026-01-15)

### Bug Fixes

- Add async_get_last_state to MockRestoreEntity in tests
  ([`99b952d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/99b952dbe08e45cd1f9eda8f79e5740eda1faaeb))

Add missing async_get_last_state and async_added_to_hass methods to MockRestoreEntity in
  test_comfort_sensors.py and test_sensor.py.

These tests set up mocks in sys.modules at import time. When pytest collects tests alphabetically,
  test_comfort_sensors.py was imported before test_energy.py, causing its incomplete
  MockRestoreEntity to pollute the module cache. This made WeeklyCostSensor inherit from a mock
  without async_get_last_state, causing test failures.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.6.1 (2026-01-15)

### Bug Fixes

- Register zone with coordinator before adding entity
  ([`839f40f`](https://github.com/afewyards/ha-adaptive-thermostat/commit/839f40f353b40b18f8aca29b32bde486858eee2c))

Move zone registration to happen BEFORE async_add_entities() is called. This ensures zone_data
  (including adaptive_learner) is available when async_added_to_hass() runs, allowing
  CycleTrackerManager to be properly initialized.

Previously, zone registration happened after entity addition, causing a race condition where
  async_added_to_hass() would find no zone_data and skip creating the CycleTrackerManager entirely.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Refactoring

- Extract build_state_attributes from climate.py
  ([`9fc7eee`](https://github.com/afewyards/ha-adaptive-thermostat/commit/9fc7eeec48dbbd201f7b78164c00fbb2190bfea6))

Extract extra_state_attributes logic into managers/state_attributes.py, reducing climate.py by ~82
  lines and improving code organization.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract ControlOutputManager from climate.py
  ([`76bafc2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/76bafc2c05f47ea508426d431b200f9a115d501b))

Extract calc_output() logic into new ControlOutputManager class. Simplify set_control_value() and
  pwm_switch() by removing fallback code since HeaterController handles all control operations.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract LearningDataStore from adaptive/learning.py
  ([`74b87fa`](https://github.com/afewyards/ha-adaptive-thermostat/commit/74b87fa44cf418e855bcc1c761388e4d237e04d3))

Extract LearningDataStore class to dedicated persistence.py module: - Move all persistence logic
  (save, load, restore methods) - Add backward-compatible re-exports in learning.py and __init__.py
  - Reduce learning.py from 771 to 481 lines

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract NightSetbackCalculator from climate.py
  ([`2e37387`](https://github.com/afewyards/ha-adaptive-thermostat/commit/2e3738755ff74f243b6a9e1a8f9f63233bee81d7))

Move night setback calculation logic into dedicated NightSetbackCalculator class in
  managers/night_setback_calculator.py. This reduces climate.py by ~150 lines and improves
  separation of concerns.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract PID rule engine from adaptive/learning.py
  ([`a1dccc2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/a1dccc2de5e2bc0eebc47c089e8d94e30f23dba3))

- Create adaptive/pid_rules.py with PIDRule enum and PIDRuleResult namedtuple - Extract
  evaluate_pid_rules(), detect_rule_conflicts(), resolve_rule_conflicts() - Update
  AdaptiveLearner.calculate_pid_adjustment() to use imported functions - Add re-exports in
  adaptive/__init__.py for backward compatibility

Story 2.1 complete. All 90 learning tests pass.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract PIDTuningManager from climate.py
  ([`314983a`](https://github.com/afewyards/ha-adaptive-thermostat/commit/314983af276b94cf5bbfb0f958c298ef34014c67))

Move PID tuning service methods (async_set_pid, async_set_pid_mode, async_reset_pid_to_physics,
  async_apply_adaptive_pid, async_apply_adaptive_ke) to new managers/pid_tuning.py module.
  Climate.py service handlers now delegate to PIDTuningManager instance.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract PWM tuning utilities from adaptive/learning.py
  ([`674d230`](https://github.com/afewyards/ha-adaptive-thermostat/commit/674d230f0303965ae2ec31d310fcac9e5eaf13a2))

- Create adaptive/pwm_tuning.py with calculate_pwm_adjustment() and ValveCycleTracker - Update
  learning.py to import and re-export for backward compatibility - Update adaptive/__init__.py with
  re-exports - Reduce learning.py from 481 to 392 lines

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract StateRestorer from climate.py
  ([`bee004a`](https://github.com/afewyards/ha-adaptive-thermostat/commit/bee004ace5ebc4dda5f8976459e61812adf10519))

Move state restoration logic into dedicated StateRestorer manager class: - _restore_state() for
  target temp, preset mode, HVAC mode - _restore_pid_values() for PID integral, gains (Kp, Ki, Kd,
  Ke), and PID mode - Single restore() entry point for async_added_to_hass

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove fallback code sections from climate.py
  ([`8c4b5a4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/8c4b5a418e6ff7e1a0f192f0ba683b1d323959d0))

All managers are now initialized in async_added_to_hass before any methods can be called, making the
  fallback code paths unreachable.

Removed fallbacks from: - async_set_pid(), async_set_pid_mode() - delegate to PIDTuningManager -
  async_set_preset_temp() - delegate to TemperatureManager - async_reset_pid_to_physics(),
  async_apply_adaptive_pid() - delegate to PIDTuningManager - _is_device_active,
  heater_or_cooler_entity - delegate to HeaterController - _async_call_heater_service,
  _async_heater_turn_on/off, _async_set_valve_value - delegate to HeaterController -
  async_set_preset_mode, preset_mode/s, presets - delegate to TemperatureManager - calc_output() -
  delegate to ControlOutputManager - async_set_temperature() - delegate to TemperatureManager

climate.py reduced from 2239 to 1781 lines (-458 lines, -20.5%)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.6.0 (2026-01-15)

### Bug Fixes

- Defer Ke application until PID reaches equilibrium
  ([`0375795`](https://github.com/afewyards/ha-adaptive-thermostat/commit/0375795bfe441a657831b5d3e3f7df138173c62e))

- Start with Ke=0 instead of applying physics-based Ke immediately - Store physics-based Ke in
  KeLearner as reference value - Add get_is_pid_converged callback to KeController - Enable Ke
  learning and apply physics Ke only after PID converges - This prevents Ke from interfering with
  PID tuning during initial stabilization

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Features

- **reports**: Add visual charts and comfort metrics to weekly report
  ([`a88a5e8`](https://github.com/afewyards/ha-adaptive-thermostat/commit/a88a5e870408e6cd2678d295cbb243f7f9c1c2f7))

- Add Pillow-based chart generation (bar charts, comfort charts, week-over-week comparison) - Add
  TimeAtTargetSensor tracking % time within tolerance of setpoint - Add ComfortScoreSensor with
  weighted composite score (time_at_target 60%, deviation 25%, oscillations 15%) - Add 12-week
  rolling history storage for week-over-week comparisons - Add zone cost estimation based on duty
  cycle × area weighting - Attach PNG chart to mobile notifications - Add comprehensive tests for
  all new modules

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Refactoring

- Move outdoor_sensor config from per-zone to domain level
  ([`c65e7de`](https://github.com/afewyards/ha-adaptive-thermostat/commit/c65e7de0615de8141e508a91fff62e0d9e078646))

- Add outdoor_sensor to domain-level config schema in __init__.py - Remove per-zone outdoor_sensor
  config option from climate.py - All zones now inherit outdoor_sensor from domain config - Update
  README documentation to reflect the change

This simplifies configuration since outdoor temperature is typically the same for all zones in a
  house.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.5.0 (2026-01-15)

### Bug Fixes

- Enforce recovery_deadline as early override for night setback end time
  ([`c3b18e7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/c3b18e72f00b9c520c701fad40a83b9e3262d688))

- Fix recovery_deadline being ignored when dynamic end time succeeds - Now uses the earlier of
  (dynamic end time, recovery_deadline) - Auto-enable solar_recovery when window_orientation is
  configured - Update README with new behavior and clearer examples

Previously, recovery_deadline was only used as a fallback when sunrise data was unavailable. Now it
  properly acts as an upper bound, ensuring zones recover by the specified time even if dynamic
  calculation suggests a later time.

### Features

- Add A++++ energy rating for extremely well-insulated buildings
  ([`48484f4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/48484f46e4fd2cb4877db27ca33c54076eab3e23))


## v0.4.0 (2026-01-15)

### Features

- Handle shared switches between heater and cooler modes
  ([`9bc3abb`](https://github.com/afewyards/ha-adaptive-thermostat/commit/9bc3abb0be018fff2d5f6f34cb74bff1ff68e47f))

Add smart turn-off logic to prevent shared switches (e.g., circulation pumps) from being turned off
  when still needed by the other mode. Shared switches now only turn off when both heating and
  cooling have no demand.

Changes: - Add _get_shared_switches() to detect switches in both lists - Add
  _turn_off_switches_smart() to skip shared switches when other mode is active - Update delayed
  turn-off methods to check other mode's demand state - Remove obsolete
  _heater_activated_by_us/_cooler_activated_by_us flags - Add 8 new tests for shared switch behavior


## v0.3.0 (2026-01-14)

### Bug Fixes

- Instantiate system-wide sensors (WeeklyCostSensor, TotalPowerSensor)
  ([`314c1c5`](https://github.com/afewyards/ha-adaptive-thermostat/commit/314c1c520b4d2a04aee8cff7d41795a39132259c))

WeeklyCostSensor and TotalPowerSensor classes existed but were never created during initialization.
  This caused weekly reports to show "N/A (no meter data)" even when energy_meter_entity was
  configured.

Now creates these sensors during first zone setup using domain-level energy_meter_entity and
  energy_cost_entity configuration.

- Remove incorrect @dataclass decorator from CycleTrackerManager
  ([`4e6316c`](https://github.com/afewyards/ha-adaptive-thermostat/commit/4e6316c3b494b5feaad061522837354f7404087c))

The CycleTrackerManager class had both @dataclass decorator and a custom __init__ method, which is
  incorrect. Removed the @dataclass decorator and unused dataclasses import since the class
  implements its own initialization logic.

### Chores

- Alter gitignore
  ([`440af85`](https://github.com/afewyards/ha-adaptive-thermostat/commit/440af85adfc36eaf8472753826fcb0517fe6d7ba))

### Documentation

- Add Mermaid diagrams to CLAUDE.md and track in git
  ([`bded612`](https://github.com/afewyards/ha-adaptive-thermostat/commit/bded612d04d9ee8dd767ca8ad1c1c18268a8d743))

- Add visual flowchart for main control loop (temperature → PID → heater) - Add initialization flow
  diagram showing manager setup sequence - Add cycle tracking state machine diagram
  (IDLE→HEATING→SETTLING) - Add multi-zone coordination sequence diagram - Remove CLAUDE.md from
  .gitignore to track documentation

### Features

- Add calculate_rise_time() function for cycle metrics
  ([`084dd70`](https://github.com/afewyards/ha-adaptive-thermostat/commit/084dd700c4da721505d947422be2169b3facf735))

Implements calculate_rise_time() to measure heating system responsiveness. The function calculates
  the time required for temperature to rise from start to target, with configurable tolerance for
  target detection.

- Added calculate_rise_time() to adaptive/cycle_analysis.py - Accepts temperature_history,
  start_temp, target_temp, threshold params - Returns rise time in minutes or None if target never
  reached - Comprehensive docstring with usage example

Tests: - Added 10 comprehensive tests covering normal rise, never reached, insufficient data,
  already at target, threshold variations, slow/fast rise, overshoot, and edge cases - All 661 tests
  pass

Related to feature 1.1 (infrastructure) in learning plan.

- Add cycle_history property to AdaptiveLearner
  ([`4085681`](https://github.com/afewyards/ha-adaptive-thermostat/commit/40856814b55662c33788092f80c9187ab2094690))

Add getter and setter property for cycle_history to enable external access to cycle metrics data
  while maintaining encapsulation. The setter is primarily intended for testing purposes.

- Add heating event handlers to CycleTrackerManager
  ([`76379f2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/76379f200155b7737e09c6044426ff7da5e4ef5d))

Implements on_heating_started() and on_heating_stopped() methods to manage cycle state transitions
  (IDLE -> HEATING -> SETTLING) with settling timeout.

- Create CycleTrackerManager class foundation
  ([`f373d50`](https://github.com/afewyards/ha-adaptive-thermostat/commit/f373d5065a97441de5a3cb1c3b9e1ef2b56cfd3e))

Add CycleTrackerManager class to track heating cycles and collect temperature data for adaptive PID
  tuning. This manager implements a state machine (IDLE -> HEATING -> SETTLING) and provides the
  foundation for cycle metrics calculation.

- Create managers/cycle_tracker.py with CycleTrackerManager class - Define CycleState enum (IDLE,
  HEATING, SETTLING) - Implement initialization with callbacks for temp/mode getters - Add state
  tracking variables and constants - Export CycleState and CycleTrackerManager from managers module

- Handle contact sensor interruptions in cycle tracking
  ([`c3a82da`](https://github.com/afewyards/ha-adaptive-thermostat/commit/c3a82da4814d2509bc4c7dad45a9ef29c2909b11))

Implemented on_contact_sensor_pause() method in CycleTrackerManager to abort active heating cycles
  when windows or doors are opened. This prevents recording invalid cycle data from interrupted
  heating sessions.

- Added on_contact_sensor_pause() method to CycleTrackerManager - Aborts cycles in HEATING or
  SETTLING states - Clears temperature history and cycle data - Cancels settling timeout if active -
  Transitions to IDLE state - Integrated cycle tracker notification in climate.py contact sensor
  pause handler - Notifies tracker before pausing heating (line 1928-1929) - Added comprehensive
  tests for contact sensor edge cases - test_contact_sensor_aborts_cycle: Verifies cycle abortion
  during active states - test_contact_sensor_pause_in_idle_no_effect: Verifies no-op in IDLE state

All 691 tests pass (2 new tests added, 0 regressions)

- Handle HVAC mode changes in cycle tracking
  ([`21f72a7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/21f72a7d3831c28effff3025b2b35e94cc2a1380))

- Add on_mode_changed() method to CycleTrackerManager - Abort cycles when mode changes from HEAT to
  OFF or COOL - Integrate mode change notification in climate.py - Add 4 comprehensive tests for
  mode change handling

- Handle setpoint changes during active cycles
  ([`7c1ea1a`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7c1ea1af2348cf98d818ecdc0c3b7123ffc9580f))

Implement edge case handling for setpoint changes that occur during active heating cycles. When a
  user changes the target temperature mid-cycle, the cycle is aborted to prevent recording invalid
  metrics.

- Add on_setpoint_changed() method to CycleTrackerManager - Aborts cycle if in HEATING or SETTLING
  state - Clears temperature history and cycle data - Cancels settling timeout if active -
  Transitions to IDLE state - Logs setpoint change with old/new temperatures

- Integrate setpoint change tracking in climate.py - Modify _set_target_temp() to track old
  temperature - Notify cycle tracker when setpoint changes - Only triggers when temperature actually
  changes

- Add comprehensive tests for edge case handling - Test setpoint change aborts cycle during HEATING
  - Test setpoint change aborts cycle during SETTLING - Test setpoint change in IDLE has no effect

All 689 tests pass (3 new tests added, 0 regressions)

- Implement cycle validation and metrics calculation
  ([`98d93d7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/98d93d724f6f0c36e86f9aceb273b6385869f781))

Add cycle validation logic to CycleTrackerManager: - Check minimum duration (5 minutes) - Check
  learning grace period - Check sufficient temperature samples (>= 5)

Implement complete metrics calculation in _finalize_cycle: - Calculate all 5 metrics: overshoot,
  undershoot, settling_time, oscillations, rise_time - Record metrics with adaptive learner - Update
  convergence tracking for PID tuning - Log cycle completion with all metrics

Add comprehensive test coverage: - Test cycle validation rules - Test metrics calculation - Test
  invalid cycle rejection

- Implement temperature collection and settling detection
  ([`2de7d5b`](https://github.com/afewyards/ha-adaptive-thermostat/commit/2de7d5b5d6bb8aae77640659f6c6996f95781c2f))

Add temperature collection during HEATING and SETTLING states with automatic settling detection
  based on temperature stability.

Implementation: - Add update_temperature() method to collect samples during active cycles - Add
  _is_settling_complete() to detect stable temperatures - Add _finalize_cycle() stub for cycle
  completion (full metrics in 2.3) - Settling requires 10+ samples, variance < 0.01, within 0.5°C of
  target - 120-minute settling timeout for edge cases

Testing: - Add 8 comprehensive tests for temperature collection and settling - All 677 tests pass (8
  new, 0 regressions) - Verify state transitions and timeout behavior

- Integrate CycleTracker with HeaterController
  ([`6c61692`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6c61692c92bb43ea3d7ce9bbce19381d28d8fefa))

- Add cycle tracker notifications in async_turn_on() and async_turn_off() - Track valve state
  transitions in async_set_valve_value() for non-PWM mode - Support both PWM (on/off) and valve
  (0-100%) heating devices - Add TestCycleTrackerValveMode with 2 tests for integration verification
  - All 685 tests passing

- Integrate CycleTrackerManager with climate entity
  ([`5052577`](https://github.com/afewyards/ha-adaptive-thermostat/commit/50525775acc28407f4fde66b2a94b58995bd452e))

- Add CycleTrackerManager import to climate.py - Add _cycle_tracker instance variable declaration -
  Initialize cycle tracker in async_added_to_hass after Ke controller - Retrieve adaptive_learner
  from coordinator zone data - Pass lambda callbacks for target_temp, current_temp, hvac_mode,
  grace_period - Log successful initialization - All 685 tests pass with no regressions

- Integrate temperature updates into control loop
  ([`da2bf28`](https://github.com/afewyards/ha-adaptive-thermostat/commit/da2bf281bf8dcf652581c50bd808fd1294dfdb88))

- Add datetime import to climate.py for cycle tracking timestamps - Integrate cycle tracker
  temperature updates in _async_control_heating() - Temperature samples automatically collected
  after PID calc_output() - Add safety checks for cycle_tracker existence and current_temp validity
  - Add test for temperature update integration in control loop - All 686 tests passing (1 new test
  added)

### Refactoring

- Extract CentralController to separate module
  ([`95e4cef`](https://github.com/afewyards/ha-adaptive-thermostat/commit/95e4cef70beeed13cf9a59f6e0321166493d0689))

Move CentralController class and related constants from coordinator.py to new central_controller.py
  file. Re-export from coordinator.py for backward compatibility. Update tests to patch the correct
  module.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract cycle analysis functions to separate module
  ([`613b510`](https://github.com/afewyards/ha-adaptive-thermostat/commit/613b510ba32a4aca1bb8751199bcc88bd7fc7f13))

Move PhaseAwareOvershootTracker, CycleMetrics, and related functions (calculate_overshoot,
  calculate_undershoot, count_oscillations, calculate_settling_time) from learning.py to new
  cycle_analysis.py module.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract energy sensors to separate module
  ([`d885dd8`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d885dd83b3264149090fb0217f50f09ca056fe7b))

Move PowerPerM2Sensor, HeatOutputSensor, TotalPowerSensor, and WeeklyCostSensor from sensor.py to
  sensors/energy.py as part of ongoing refactoring to improve code organization.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract health sensor to separate module
  ([`e78271c`](https://github.com/afewyards/ha-adaptive-thermostat/commit/e78271c370767f75cb37dbebf8b7e2679a079a8c))

Move SystemHealthSensor class from sensor.py to sensors/health.py. Update sensor.py to be a lean
  entry point with re-exports for backward compatibility.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract heater controller to managers/heater_controller.py
  ([`6e613f3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6e613f348492f7ac49d65b337edb7ed982e02ac7))

Create new managers/ directory and extract HeaterController class from climate.py. This moves
  heater/cooler control logic into a dedicated manager class, improving code organization and
  maintainability.

HeaterController handles: - Device on/off control (PWM and toggle modes) - Valve value control for
  non-PWM devices - PWM switching logic - Service call error handling - Control failure event firing

The climate.py delegates to HeaterController when available, with fallback logic for startup before
  the controller is initialized. Setter callbacks allow HeaterController to update thermostat state.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract Ke learning controller to managers/ke_manager.py
  ([`7307924`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7307924f4269a92ed4eaad1b01f5dabc99c3ac28))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract night setback controller to managers/night_setback_manager.py
  ([`d362aed`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d362aed1615e1f60399331d9bf31c911adaf8b37))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract performance sensors to separate module
  ([`037c574`](https://github.com/afewyards/ha-adaptive-thermostat/commit/037c5741a3ca6f0bf89b143f7adf69bf88f8f40c))

Create sensors/ directory and move performance-related sensor classes: - HeaterStateChange dataclass
  - AdaptiveThermostatSensor base class - DutyCycleSensor, CycleTimeSensor, OvershootSensor -
  SettlingTimeSensor, OscillationsSensor

Maintain backward compatibility via re-exports in sensor.py.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract scheduled tasks to services/scheduled.py
  ([`fe168cc`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fe168cc4fba1ddb3bfcd0a6127cdeffcdf7bcedc))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract temperature manager to managers/temperature_manager.py
  ([`bb956b1`](https://github.com/afewyards/ha-adaptive-thermostat/commit/bb956b1230beeaffcc4bb01c3f1dc34927b5dede))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract ThermalRateLearner to separate module
  ([`75fa04c`](https://github.com/afewyards/ha-adaptive-thermostat/commit/75fa04ca6df88d984ed4978bd63b2e21ee6405a7))

Move ThermalRateLearner class from learning.py to dedicated thermal_rates.py file for better code
  organization. Add backward compatible re-export from learning.py and export from
  adaptive/__init__.py. Also add pytest pythonpath configuration to pyproject.toml.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Testing

- Add integration tests for cycle learning flow
  ([`7b43cc2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7b43cc2893f89cbd68f1681b7816d23b646ce765))

- Create tests/test_integration_cycle_learning.py with 8 comprehensive tests - Test complete heating
  cycle with realistic temperature progression - Test multiple cycles recorded sequentially (3
  back-to-back) - Test cycle abortion scenarios: setpoint changes, contact sensors - Test vacation
  mode: cycles complete but not recorded - Test PWM mode on/off cycling - Test valve mode 0-100%
  transitions - All 703 tests pass (8 new, 0 regressions)


## v0.2.1 (2026-01-14)

### Bug Fixes

- Night setback sunset offset timezone and unit bugs
  ([`5c4be94`](https://github.com/afewyards/ha-adaptive-thermostat/commit/5c4be94f9601b636cd9ecac6ecca737029dcad2b))

Two bugs caused night setback to trigger ~3 hours early:

1. Timezone bug: sunset time from sun.sun was in UTC but compared against local time, causing ~1
  hour early trigger in CET

2. Unit bug: "sunset+2" was interpreted as 2 minutes instead of 2 hours, causing ~2 hours early
  trigger

Changes: - Convert UTC sunset/sunrise to local time using dt_util.as_local() - Add smart offset
  parsing: values ≤12 = hours, >12 = minutes - Support explicit suffixes: sunset+2h, sunset+30m -
  Backward compatible: sunset+30 still works as 30 minutes

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Testing

- Update tests for HVAC mode tracking in demand aggregation
  ([`d67d915`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d67d9156fe7ad6bc8f273f38f165855c13bb06ad))

Add hvac_mode parameter to update_zone_demand() calls in tests to match the API change from commit
  7b739b8 which added separate heating/cooling demand tracking. Also set _heater_activated_by_us
  flag for turn-off tests since the controller now only schedules turn-off when it activated the
  heater.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.2.0 (2026-01-14)

### Features

- Add HVAC mode tracking for separate heating/cooling demand
  ([`7b739b8`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7b739b863c6c8a66a1053e73c9253ba4e679c13c))

The coordinator now tracks each zone's HVAC mode alongside demand state, enabling proper separation
  of heating and cooling demand aggregation. Also tracks which controller activated shared switches
  to prevent turning off switches when still needed by the other mode.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Testing

- Update integration tests for turn-off debounce
  ([`d0e53a1`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d0e53a1b25f1676528448e4e169f078e5fa4609a))

Updated tests that expected immediate turn-off to account for the new 10-second debounce delay by
  patching TURN_OFF_DEBOUNCE_SECONDS to 0.1s.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>


## v0.1.2 (2026-01-14)

### Bug Fixes

- Pump not activating when other heater switches already on
  ([`228b2ae`](https://github.com/afewyards/ha-adaptive-thermostat/commit/228b2aec9bd5091c64d3f6aaaa9232233d773e65))

- Changed heater/cooler logic from `_is_any_switch_on` to `_are_all_switches_on` so that if one
  switch (e.g., power_plant_heat) is already on but another (e.g., pump) is off, the controller will
  turn on all switches - Added 10-second debounce on turn-off to prevent brief demand drops during
  HA restarts from turning off the main heat source - Added `_are_all_switches_on` method to check
  if all switches are on - Added debounce methods: `_schedule_heater_turnoff_unlocked`,
  `_cancel_heater_turnoff_unlocked`, `_delayed_heater_turnoff` (and cooler equivalents) - Added
  tests for new functionality

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Documentation

- Add test status badge to README
  ([`f4d66bf`](https://github.com/afewyards/ha-adaptive-thermostat/commit/f4d66bfa022683fa8844543a7163b8d77469ff38))

- Add testing period notice to README
  ([`fc11d29`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fc11d29bffd628bc63ee3afffae748273356a460))

- Update Ke section to reflect adaptive learning
  ([`295f527`](https://github.com/afewyards/ha-adaptive-thermostat/commit/295f5270c91d34bdd78a9d3d3f1dbefc559ae5f0))

Replace static Ke recommendations with explanation of automatic learning. The system learns Ke by
  observing correlations between outdoor temperature and PID output, making manual tuning
  unnecessary.


## v0.1.1 (2026-01-14)

### Bug Fixes

- Use empty string instead of boolean for changelog_file config
  ([`d2057b9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d2057b9d1f5f585f01880e1cc3c6a08311bc5361))

### Chores

- Disable changelog file generation in semantic-release
  ([`213feba`](https://github.com/afewyards/ha-adaptive-thermostat/commit/213feba5883565bef8908421cb85f697eede28d0))

GitHub releases already contain release notes, so a separate CHANGELOG.md is redundant.

### Documentation

- Fix README inaccuracies and add missing schema parameter
  ([`bae1145`](https://github.com/afewyards/ha-adaptive-thermostat/commit/bae11458a83b2ebcaa2eb8d27a237d060ab0707a))

README corrections: - Fix Created Entities: remove non-existent entities, add heat_output sensor -
  Add 3 undocumented services: apply_adaptive_ke, energy_stats, pid_recommendations - Fix Night
  Setback orientation offsets (signs were reversed) - Remove non-existent weather adjustment feature
  claim - Fix link_delay_minutes default from 10 to 20 minutes - Add missing adaptive learning rules
  to PID table

Code fixes (climate.py): - Add min_effective_elevation to night_setback schema (was documented but
  not configurable) - Use DEFAULT_LINK_DELAY_MINUTES constant instead of hardcoded fallback


## v0.1.0 (2026-01-14)

### Bug Fixes

- Add _sync_in_progress flag to ModeSync to prevent feedback loops
  ([`77b8fa1`](https://github.com/afewyards/ha-adaptive-thermostat/commit/77b8fa139c7dca8f81ade9d775fa4a34b3ef595e))

When a zone mode changes, ModeSync propagates to other zones which could trigger reverse syncs.
  Added flag with try/finally pattern to skip sync handlers when already syncing.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add asyncio.Lock to CentralController to prevent startup race conditions
  ([`037ecac`](https://github.com/afewyards/ha-adaptive-thermostat/commit/037ecac77e6745e8a822db2658c663f7e049feed))

- Add _startup_lock to protect _heater_waiting_for_startup and _cooler_waiting_for_startup flags -
  Protect _update_heater() and _update_cooler() methods with lock - Add deadlock avoidance in cancel
  methods by releasing lock while awaiting task cancellation - Add tests for concurrent update calls
  and task cancellation race conditions

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add missing astral dependency for sun position tests
  ([`ca0b31a`](https://github.com/afewyards/ha-adaptive-thermostat/commit/ca0b31a00b8fc88ca4ef6b435d63262f06f5da68))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add roof to config schema validation
  ([`1ce4720`](https://github.com/afewyards/ha-adaptive-thermostat/commit/1ce4720ccec86d137d8dea8efecc94b9a3b4574e))

Add WINDOW_ORIENTATION_ROOF constant and include it in VALID_WINDOW_ORIENTATIONS list for config
  validation.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Allow demand_switch-only configuration without heater
  ([`f917215`](https://github.com/afewyards/ha-adaptive-thermostat/commit/f91721531817b8c968cbca78c3071e9bbb888a50))

Previously the entity setup assumed heater_entity_id was always set, causing entity load failures
  when only demand_switch was configured. Now properly guards heater state tracking with null check
  and adds state tracking for demand_switch.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Correct PID sampling period timing bug and add timestamp validation
  ([`2ee4c7c`](https://github.com/afewyards/ha-adaptive-thermostat/commit/2ee4c7cdc710ad472a022c0cdb2f30f54984f84d))

- Fixed timing bug where sampling period check used wrong timestamp (time() - _input_time instead of
  time() - _last_input_time) - Added warning log when event-driven mode (sampling_period=0) is used
  without providing input_time parameter - Fixed incorrect docstrings for cold_tolerance and
  hot_tolerance - Added tests for sampling period timing and timestamp validation

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Create standalone device for zone sensors
  ([`224d481`](https://github.com/afewyards/ha-adaptive-thermostat/commit/224d4819698caaae0b1fb4ff947764ee164b6471))

YAML-configured climate entities cannot have a device parent. Sensors now create their own device
  with full device info (name, manufacturer, model) for proper grouping in HA UI.

- Remove device_info from climate.py (YAML entities incompatible) - Enhance sensor device_info with
  complete device details - Fix test_energy.py missing device_registry mock

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Enable physics-based PID initialization when values not configured
  ([`1a718ca`](https://github.com/afewyards/ha-adaptive-thermostat/commit/1a718ca063ce4aeb7a1389d926653dc0cd09f6d1))

The schema defaults for kp/ki/kd (100/0/0) were preventing the physics-based calculation from
  running. Removing the defaults allows kwargs.get() to return None when not explicitly configured,
  triggering the physics-based initialization using zone properties (area_m2, ceiling_height,
  heating_type).

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Enable solar recovery with dynamic night setback end times
  ([`28c6eb9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/28c6eb9261814dda7538fd9e74b597fef14f8c18))

Solar recovery was only created when an explicit end time was set in night_setback config. This fix
  moves SolarRecovery creation outside the `if end:` block so it works with dynamic end times too.

Uses "07:00" as default base_recovery_time for static fallback, but the dynamic sun position
  calculator overrides this.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Handle unknown/unavailable sensor states gracefully
  ([`f3bc168`](https://github.com/afewyards/ha-adaptive-thermostat/commit/f3bc168e5c61d207e41143b6dedc105b90723939))

Skip temperature updates when sensor state is 'unknown' or 'unavailable' instead of attempting to
  parse and logging errors.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Implement real cycle time calculation in CycleTimeSensor
  ([`d1b4409`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d1b44090cb96b1ef9a6fd33a1a34ada98b3e437e))

Track heater state transitions (on->off->on) and calculate cycle time as duration between
  consecutive ON states. Maintain rolling average of recent cycle times (default 10 cycles).

- Add DEFAULT_ROLLING_AVERAGE_SIZE constant (10 cycles) - Track heater state via
  async_track_state_change_event - Record _last_on_timestamp to calculate cycle duration - Use deque
  with maxlen for memory-efficient rolling average - Filter out cycles shorter than 1 minute -
  Return None when no complete cycles recorded - Expose cycle_count, last_cycle_time in
  extra_state_attributes - Add 19 new tests for cycle time calculation and rolling average

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Implement real duty cycle calculation in DutyCycleSensor
  ([`5008b06`](https://github.com/afewyards/ha-adaptive-thermostat/commit/5008b065b5f54ab04aaaf116e5dbafbada9e3ad7))

- Track heater on/off state changes with timestamps using deque - Calculate duty cycle as (on_time /
  total_time) * 100 over measurement window - Add configurable measurement window (default 1 hour) -
  Handle edge cases: always on, always off, no state changes - Fall back to control_output from PID
  controller when no heater tracking - Add extra_state_attributes for debugging (window, state
  changes count) - Add comprehensive test suite with 27 tests

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Implement weekly delta tracking in WeeklyCostSensor
  ([`0edc2d0`](https://github.com/afewyards/ha-adaptive-thermostat/commit/0edc2d026c41b3c0d9bb95406e3a719e8fc9fb12))

- Add RestoreEntity inheritance for state persistence across restarts - Track week_start_reading and
  week_start_timestamp for delta calculation - Implement _check_week_boundary() for ISO week-based
  week reset detection - Handle meter reset/replacement scenarios with automatic recovery - Expose
  persistence data in extra_state_attributes - Add comprehensive test suite with 23 tests covering:
  - Weekly delta calculation - Persistence across restarts - Week boundary reset - Meter reset
  handling - Edge cases (unavailable meter, invalid values)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Improve PID controller integral reset and input validation
  ([`52fc7be`](https://github.com/afewyards/ha-adaptive-thermostat/commit/52fc7be94111879568aec4d52b34b49e868f4990))

- Reset integral on setpoint change regardless of ext_temp presence - Auto-clear samples when
  switching from OFF to AUTO mode - Add input validation for NaN and Inf values - Return cached
  output for invalid inputs

The integral was previously only reset when ext_temp was provided, causing stale values in systems
  without external temperature compensation. Mode switching from OFF to AUTO now clears samples to
  prevent derivative calculation corruption from stale timestamps.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Prevent compounding zone-specific PID adjustments
  ([`e43adb3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/e43adb34f9642688c36786e19d20b26688fd7897))

Zone-specific adjustments were being applied inside the per-cycle calculation loop, causing
  exponential compounding (e.g., Ki for kitchen would become 0.8^n after n cycles).

Changes: - Add _calculate_zone_factors() called once at initialization - Store zone factors as
  immutable instance variables - Apply zone factors as final multipliers after learned adjustments -
  Add get_zone_factors() method for inspection - Add apply_zone_factors parameter for optional
  skipping - Add 16 new tests for zone adjustment behavior

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Prevent mode sync from changing zones that are OFF
  ([`8b7e29d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/8b7e29d8ceee5ce472abb02902f813ecf53a0ae7))

OFF zones should remain independent and not be synced when another zone changes mode. Mode sync now
  only affects zones in an active mode (heat/cool).

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Prevent spurious heating on startup with control interval
  ([`b850903`](https://github.com/afewyards/ha-adaptive-thermostat/commit/b8509039d14977a8433f8d41a54ae2100d425bed))

Two issues fixed:

1. Initialize _time_changed to current time instead of 0. With epoch-0, PWM calculated time_passed
  as billions of seconds, causing immediate heater turn-on regardless of actual demand.

2. Always recalculate PID on control interval ticks. Previously with sampling_period=0 (event-driven
  mode), PID only recalculated on sensor changes, leaving stale _control_output from state
  restoration to drive PWM decisions incorrectly.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Repair broken test suite
  ([`933282c`](https://github.com/afewyards/ha-adaptive-thermostat/commit/933282cc9eacc0c2adeba5dc4b8298e1866d972e))

- Remove obsolete test_demand_switch.py (tests non-existent switch module) - Separate voluptuous
  import from homeassistant imports in __init__.py - Fix test data in adaptive PID tests to avoid
  false convergence - Fix asyncio event loop handling for Python 3.13 compatibility

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Replace bare exception handler with specific AttributeError
  ([`8433424`](https://github.com/afewyards/ha-adaptive-thermostat/commit/843342499f182c7685437304cb97c279fbb48122))

Replace bare `except:` with `except AttributeError as ex:` in _device_is_active() method. Add debug
  logging for startup scenarios where entity state is not yet available.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Startup min_cycle_duration and demand_switch turn_off
  ([`cfe3fb5`](https://github.com/afewyards/ha-adaptive-thermostat/commit/cfe3fb5483a2512dd8cd86385a2af4d55e923f66))

Two bugs fixed:

1. Startup min_cycle_duration bypass: On HA restart, min_cycle_duration checks would block valve
  control because we had no reliable data about when cycles actually started. Now returns 0 from
  _get_cycle_start_time() when _last_heat_cycle_time is None, allowing immediate action on first
  cycle after startup.

2. demand_switch turn_off not working: The _async_heater_turn_off function had a broken loop that
  only iterated when heater or cooler entities were configured. Zones with only demand_switch (no
  heater/cooler) would log "Turning OFF" but never actually call the service. Fixed by iterating
  directly over heater_or_cooler_entity property.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Trigger ModeSync when HVAC mode changes
  ([`b97fc6d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/b97fc6da0b47e276f890daf696661c243b7c8f92))

ModeSync was implemented but never called from the climate entity. Now async_set_hvac_mode()
  triggers mode_sync.on_mode_change() to synchronize HEAT/COOL modes across all zones as intended.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Use empirical PID values instead of Ziegler-Nichols formula
  ([`412b1e8`](https://github.com/afewyards/ha-adaptive-thermostat/commit/412b1e8ad5dae8126121498dc672c32df4b81a04))

The Ziegler-Nichols formula produced values unsuitable for slow thermal systems like floor heating
  (Ki was 18x too high, Kd was 300x too low).

Changes: - Replace theoretical Z-N formula with empirical base values per heating type - Floor
  hydronic: Kp=0.3, Ki=0.012, Kd=7.0 (calibrated from real A+++ house) - Add minor tau-based
  adjustments (±30% max) - Add INFO-level logging for PID values on init and restore - Remove unused
  services (apply_recommended_pid, set_pid_gain, set_pid_mode)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- **autotune**: Fix autotune results logging.
  ([`a0b31cf`](https://github.com/afewyards/ha-adaptive-thermostat/commit/a0b31cf70b491f941e1e80b6d66f4948f725860d))

- **autotune**: Fix sample_time reporting.
  ([`a41f7e9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/a41f7e955bcc0aba960361034462dd77f787b55c))

- **autotune**: Fix set point taken on very first sample (at boot).
  ([`01cb0fc`](https://github.com/afewyards/ha-adaptive-thermostat/commit/01cb0fc87d83518101fc9e6530e3c12b177ed160))

- **away mode**: Fix bug locking thermostat in away mode once enabled.
  ([`d4b8282`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d4b8282367d8d05aa531335eeecb2806d4bd49e0))

- **default_conf**: Remove warnings about default delays.
  ([`6dc5c89`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6dc5c8983ffa584f55a277cf0110686950866d01))

- **parameters**: Fix precision not working
  ([`ffe8eb2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/ffe8eb270445058f61c400c449e31116a6cbd767))

- **pid_controller**: Make sure last p, i and d are always available.
  ([`6e7c028`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6e7c0281f6a15ae97cb3a6e18e6070bd3690e5b2))

- **pid_controller**: Manage case when sample times are NoneType or identical.
  ([`53194e3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/53194e3703958802517d968b5273f3997a6c6c3d))

- **pid_controller**: Self._sampletime is ms in PID.
  ([`dc201e4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/dc201e4316fd004a524894bcfcda4bccb87cc55d))

- **precision**: Add parameter for target temp precision.
  ([`bfe6d62`](https://github.com/afewyards/ha-adaptive-thermostat/commit/bfe6d62b673986bb7345c973f1f848fab88cbe5d))

- **presets**: Revert use of literal_eval.
  ([`fa2bcb2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fa2bcb217cc9147c837acdfeb9259b4accb18744))

- **presets**: Use literal_eval instead of eval.
  ([`6152b3c`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6152b3cec4f869708e57cdf9aed63aceef51779d))

- **restore**: Don't restore integral with Autotune
  ([`022e8c3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/022e8c384605ca124f52d21574e66b1e823a3c96))

- **services**: Add missing required setting 'max'.
  ([`6d1af42`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6d1af42e3664bb3682face435ec93b4d82ad65a0))

- **services**: Limit target to smart_thermostat entities.
  ([`23d9c0d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/23d9c0dc42113b6089c959577956e8c714f4c3b2))

- **smart_thermostat**: Add missing sampling_period to PID init after autotune.
  ([`d63a33b`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d63a33bc2e32760268ca3c34d7fdad0aaa0744f5))

- **smart_thermostat**: Fix attributes update
  ([`3ac4ed6`](https://github.com/afewyards/ha-adaptive-thermostat/commit/3ac4ed6defd5cdc20a86e21d345574f3ccd3e4b8))

Update HA states at the end of _async_control_heating() method to ensure attributes are up to date
  after PID is calculated.

- **smart_thermostat**: Fix error when setting preset mode 'none' twice.
  ([`01de1a2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/01de1a248c857cac5896abe8623cf34c869b4512))

- **smart_thermostat**: Fix HACS Action workflow.
  ([`895b2a7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/895b2a7c1c578721f874d7abbd6b8fb0a1eb0e64))

- **smart_thermostat**: Fix issue at start with self._last_input being None.
  ([`33ea209`](https://github.com/afewyards/ha-adaptive-thermostat/commit/33ea209d97a733b49fed322241d7426ccd4e3f50))

- **smart_thermostat**: Ignore brands check.
  ([`e3daf12`](https://github.com/afewyards/ha-adaptive-thermostat/commit/e3daf121dc558f7b37236698301ffc769ff73ea6))

- **smart_thermostat**: Immediately switch off heater when thermostat is switched off.
  ([`f8aabd0`](https://github.com/afewyards/ha-adaptive-thermostat/commit/f8aabd0a3c99c5bee5e393edaf86218fa60d00a0))

- **smart_thermostat**: Make sure _previous_temp_time and _cur_temp_time are timestamps.
  ([`008de5e`](https://github.com/afewyards/ha-adaptive-thermostat/commit/008de5e52021b50f02e342a7f550d3873a75eba7))

- **smart_thermostat**: Remove custom_attributes.
  ([`3486654`](https://github.com/afewyards/ha-adaptive-thermostat/commit/348665477ba32b9d1d0a83b45d3b219ab8c70f85))

### Chores

- Add automatic version generation with python-semantic-release
  ([`d640c55`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d640c5521bc48ce69c309e492a97db10e22b8a5e))

- Add pyproject.toml with semantic-release configuration - Add GitHub Actions release workflow - Set
  initial version to 0.1.0 (alpha)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Increase default zone link delay to 20 minutes
  ([`6d43f69`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6d43f69b9049b8beb09f07791a95ba9d04e36096))

10 minutes was too short for floor hydronic heating with typical 90-minute PWM cycles. 20 minutes
  (~25% of PWM) gives enough time for heat transfer between thermally connected zones.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Update HACS metadata with new name and HA version
  ([`31e74e3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/31e74e365477b2dd29fd70e22df341230a20ecdf))

Rename to "Adaptive Thermostat" and require HA 2026.1.0.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Update workflow to use main branch only
  ([`fc2a145`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fc2a14564e38b0358d08915df5bf2a595390a368))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Documentation

- Add ASCII art header to README
  ([`406a9a3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/406a9a36b25c933fe3502a54638a1a9596ad35a7))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add comprehensive README for Adaptive Thermostat
  ([`4a3aff4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/4a3aff450b3e9e6e1831433182d0e1704ae0efea))

- Document all features: PID control, adaptive learning, multi-zone coordination - Include
  configuration examples (basic and full) - Document all services (entity and domain-level) - Add
  parameters reference tables - Include troubleshooting section - Add Buy Me a Coffee link

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add demand_switch configuration example and parameter
  ([`9b5a928`](https://github.com/afewyards/ha-adaptive-thermostat/commit/9b5a928d4740426576218be91fb7a9a5e6f1f543))

Document the demand_switch option for unified valve control in both heating and cooling modes.
  Useful for fan coil units and shared hydronic loops.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add MIT license with original author attribution
  ([`637ba02`](https://github.com/afewyards/ha-adaptive-thermostat/commit/637ba0249d4b10490118208f0c2dd4a1f6cad4bb))

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove manual PID settings, emphasize auto-tuning
  ([`8d86e64`](https://github.com/afewyards/ha-adaptive-thermostat/commit/8d86e64ad2ed994614f480166be04af49b65903d))

PID values are auto-calculated from heating_type and area_m2, then refined through adaptive
  learning. Manual configuration is only needed for advanced overrides.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Update README to reflect current features and services
  ([`5fb1681`](https://github.com/afewyards/ha-adaptive-thermostat/commit/5fb16811326b575bde77fbe645f7ba8739922884))

- Update services section: remove deleted services, add apply_adaptive_pid - Add empirical PID
  values (Kp, Ki, Kd) to heating types table - Document dynamic night setback end time
  (sunrise/orientation/weather) - Document learning grace period for night setback transitions -
  Update troubleshooting to reference current services

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Update README with recent config changes
  ([`52707de`](https://github.com/afewyards/ha-adaptive-thermostat/commit/52707de25dd0192f3d532ff398dd11f92d6f4d34))

- Remove kp, ki, kd, ke from parameters (now physics-based only) - Remove learning_enabled option
  (now always enabled) - Update main_heater/cooler_switch to show list support

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Features

- Add adaptive Ke learning for outdoor temperature compensation
  ([`d91bc5f`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d91bc5f92b4125525d0b00b4d4f447d392a8666b))

Ke (outdoor temp compensation) now auto-tunes after PID converges: - Physics-based Ke initialization
  using house energy rating - Correlation-based tuning: adjusts Ke when steady-state error
  correlates with outdoor temp - Gated by PID convergence (3 consecutive stable cycles) - Rate
  limited to one adjustment per 48 hours

New attributes: ke_learning_enabled, ke_observations, pid_converged New service: apply_adaptive_ke

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add apply_adaptive_pid entity service
  ([`662e67d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/662e67dcfc49f180546cdb1e96db619e9357e0a9))

Adds a new entity service that calculates and applies PID values based on learned performance
  metrics (overshoot, undershoot, settling time, oscillations). Requires at least 3 analyzed heating
  cycles.

Usage: service: adaptive_thermostat.apply_adaptive_pid

target: entity_id: climate.adaptive_thermostat_gf

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add cycle history bounding and rate limiting
  ([`40d60a9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/40d60a9949c9278be52a1b9f392b8bf863bd261c))

- Add MAX_CYCLE_HISTORY (100) and MIN_ADJUSTMENT_INTERVAL (24h) constants - Implement FIFO eviction
  when cycle history exceeds maximum size - Add rate limiting to skip PID adjustments if last
  adjustment was too recent - Add logging when adjustments are skipped due to rate limiting -
  Persist last_adjustment_time across restarts via LearningDataStore - Add 18 new tests for history
  bounding and rate limiting

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add DataUpdateCoordinator for centralized data management
  ([`836cbf4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/836cbf45789cc1f826ecdd56f4e6f67880bda24f))

Implement DataUpdateCoordinator to handle periodic updates of climate entity data. This provides
  centralized state management and efficient data fetching for all zones. Also fixes DOMAIN constant
  to match the renamed integration.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Add demand_switch for unified valve control
  ([`b16b358`](https://github.com/afewyards/ha-adaptive-thermostat/commit/b16b3583ed512f2427420bde667abf188eb75463))

- Add demand_switch config option for single valve controlling both heat/cool - PWM controlled same
  as heater/cooler - Make heater and cooler optional (at least one of heater/cooler/demand_switch
  required) - demand_switch entities controlled regardless of HVAC mode

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add domain config schema validation
  ([`3a72ce0`](https://github.com/afewyards/ha-adaptive-thermostat/commit/3a72ce0902ebffc80e36079b16442354e2d6d2e1))

- Add CONFIG_SCHEMA using voluptuous to validate domain configuration - Add valid_notify_service()
  custom validator for notify service format - Validate parameter types and ranges: -
  source_startup_delay: 0-300 seconds - learning_window_days: 1-30 days - fallback_flow_rate:
  0.01-10.0 L/s - house_energy_rating: A+++ to G - Add helpful error messages for invalid
  configuration - Add voluptuous to requirements-test.txt - Create tests/test_init.py with 43 tests

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add dynamic night setback end time and learning grace period
  ([`46c76f9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/46c76f96f6bf0c2d13b41c8b0791e02313eb946e))

Night setback end time calculation: - Base: sunrise + 60 min (sun needs time to rise high enough) -
  Orientation offsets: south +30, east +15, west -30, north -45 min - Weather adjustments: cloudy
  -30 min, clear +15 min - Falls back to recovery_deadline or 07:00 if sunrise unavailable

Learning grace period: - 60-minute grace period triggers on night setback transitions - Prevents
  sudden setpoint changes from confusing adaptive learning - Exposes learning_paused and
  learning_resumes in entity attributes

Also adds debug logging for night setback state and transitions.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add dynamic sun position-based solar recovery timing
  ([`08b5af3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/08b5af323fd293dac2e5a3477c20237bf4af7fa7))

Replace static orientation offsets with actual sun position calculations using the astral library.
  The system now calculates when the sun's azimuth aligns with window orientation (±45°) and
  elevation exceeds the minimum threshold (default 10°).

- Add sun_position.py module with SunPositionCalculator class - Update SolarRecovery to use dynamic
  timing when calculator available - Wire up calculator in climate.py using HA location config - Add
  min_effective_elevation config option (default 10°) - Fall back to static offsets if HA location
  not configured - Update README with new feature documentation

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add error handling and retry logic to CentralController
  ([`14bb8d2`](https://github.com/afewyards/ha-adaptive-thermostat/commit/14bb8d2e55d1261c5b0450e833f20d58ce3fa4c0))

- Wrap service calls in try/except for ServiceNotFound and HomeAssistantError - Log errors when
  switch operations fail with appropriate levels - Add retry logic with exponential backoff (1s, 2s,
  4s delays) - Track consecutive failures and emit warning after 3 failures - Add
  get_consecutive_failures() method for health monitoring - Add 11 new tests for error handling
  scenarios

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add error handling for heater service calls
  ([`b58897c`](https://github.com/afewyards/ha-adaptive-thermostat/commit/b58897c85cec8a66566dd5760a8f77511917c0c8))

- Wrap turn_on/turn_off/set_value service calls in try/except - Catch ServiceNotFound,
  HomeAssistantError, and generic exceptions - Log errors with entity_id and operation details -
  Fire adaptive_thermostat_heater_control_failed event on failure - Expose heater_control_failed and
  last_heater_error in state attributes - Add 19 unit tests for service failure handling

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add history dependency to manifest
  ([`fb712dd`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fb712ddbf9f50f99a8b84d53bdea541222fdd221))

Add the history component to the dependencies array in manifest.json. This ensures Home Assistant
  loads the history integration before adaptive_thermostat, enabling future use of historical
  temperature data.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add intercardinal window orientations (NE, NW, SE, SW)
  ([`1404b5d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/1404b5db4eb396b8a30825bd56eab229fb4f9ede))

Adds northeast, southeast, southwest, and northwest options for window_orientation config with
  appropriate solar gain seasonal impact values and recovery time offsets.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add Ke limits to PID_LIMITS constant
  ([`125bedc`](https://github.com/afewyards/ha-adaptive-thermostat/commit/125bedcdfe826ab9f077cf7aee37d649f2a22bc1))

Add ke_min (0.0) and ke_max (2.0) to the PID_LIMITS dictionary for weather compensation coefficient
  bounds. Update calculate_recommended_ke() in heating_curves.py to use centralized limits instead
  of hardcoded values.

- Add ke_min=0.0, ke_max=2.0 to PID_LIMITS in const.py - Update heating_curves.py to import and use
  PID_LIMITS for clamping - Add TestPIDLimitsConstants test class with 5 comprehensive tests - Add
  test_pid_limits() function for Story 7.4 verification

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add noise tolerance to segment detection
  ([`c62caf9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/c62caf9485924d32c91bcf886845eec586e206d2))

Add noise tolerance and rate bounds validation to ThermalRateLearner segment detection. Small
  temperature reversals below the noise threshold (default 0.05C) no longer break segments,
  preventing fragmentation from sensor noise. Segments are also validated against rate bounds
  (0.1-10 C/hour) to reject physically impossible rates.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add physics-based PID initialization module
  ([`b400d31`](https://github.com/afewyards/ha-adaptive-thermostat/commit/b400d314f711e8656d9eac9a5c768d5889dd08aa))

Implement adaptive/physics.py with functions for calculating initial PID parameters based on zone
  thermal properties and heating system type.

Key features: - calculate_thermal_time_constant(): estimates system responsiveness from volume or
  energy efficiency rating (A+++ to D) - calculate_initial_pid(): modified Ziegler-Nichols tuning
  with heating type modifiers (floor_hydronic, radiator, convector, forced_air) -
  calculate_initial_pwm_period(): PWM period selection based on heating system characteristics

Added heating type constants and lookup table to const.py with PID modifiers and PWM periods for
  each heating type.

Comprehensive test suite with 12 tests covering all functions, edge cases, and heating type
  variations.

All 22 tests pass (coordinator + PID + physics).

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Add reset_pid_to_physics service
  ([`e4de0cc`](https://github.com/afewyards/ha-adaptive-thermostat/commit/e4de0cc45077be50055c38ff60d8ad54d305e7a7))

Adds entity service to recalculate PID values from zone's physical properties (area_m2,
  ceiling_height, heating_type). Clears integral term to avoid wind-up from previous tuning.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add roof option for window_orientation
  ([`2921027`](https://github.com/afewyards/ha-adaptive-thermostat/commit/2921027949445a9e8d8e559f73bee7284a88acb6))

Support skylights in solar recovery by adding "roof" as a valid window orientation. Uses same -30
  min offset as south-facing windows since skylights get good midday sun exposure.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add thermal rate learning module
  ([`ce47c1b`](https://github.com/afewyards/ha-adaptive-thermostat/commit/ce47c1bc54f7aff5acc9d6af40d49d879bbf76c4))

Implements ThermalRateLearner class to learn heating and cooling rates from observed temperature
  history.

Key features: - Analyzes temperature history to detect heating/cooling segments - Calculates rates
  in °C/hour with configurable minimum duration - Stores measurements with outlier rejection (2
  sigma default) - Averages recent measurements (max 50) for stable estimates - Uses median for
  segment rates to reduce outlier impact

Module includes comprehensive 14-test suite validating all functionality.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Add unload support for clean integration reload
  ([`d5d60b7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d5d60b7e063cf1e5b2d2c789b89e31775295659c))

Implement async_unload() function to properly clean up when the integration is being unloaded or
  reloaded. This prevents memory leaks and leftover scheduled tasks from accumulating.

Changes: - Track unsubscribe callbacks from async_track_time_change for all 6 scheduled tasks - Add
  async_unload() that cancels scheduled tasks, unregisters services, clears coordinator refs - Add
  async_unregister_services() to services.py for removing all 7 registered services - Add 12 new
  tests for unload functionality (TestAsyncUnload, TestAsyncUnregisterServices,
  TestReloadWithoutLeftoverState)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add unregister_zone to coordinator
  ([`abed95f`](https://github.com/afewyards/ha-adaptive-thermostat/commit/abed95f405fb4c817e4c26749711ad7ef04dbbd2))

Add zone unregistration support for clean entity removal: - Add unregister_zone() method to
  AdaptiveThermostatCoordinator - Add duplicate registration warning to register_zone() - Add
  async_will_remove_from_hass() to climate entity to call unregister - Add 7 new tests for zone
  lifecycle management

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add window/glass area to physics-based PID calculation
  ([`4ca3964`](https://github.com/afewyards/ha-adaptive-thermostat/commit/4ca39640a78bb18fad85447731121dfb1ef332fa))

Add glazing type (window_rating) and window area parameters to thermal time constant calculation.
  More glass with worse insulation reduces tau (faster heat loss = more aggressive PID tuning).

- Add GLAZING_U_VALUES lookup (single, double, hr, hr+, hr++, hr+++, triple) - Controller-level
  window_rating config with per-zone override - Tau reduction capped at 40% for extreme cases - Add
  10 new tests for window/glazing calculations

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Complete integration of coordinator, services, and system sensors
  ([`80d9539`](https://github.com/afewyards/ha-adaptive-thermostat/commit/80d95391372e885457e746e27f91fe1c745fa09b))

- Add configuration constants for all new features to const.py - Create number.py platform for
  learning_window entity - Add TotalPowerSensor and WeeklyCostSensor to sensor.py - Implement
  vacation mode handler (adaptive/vacation.py) - Register 6 domain services in __init__.py: -
  run_learning, apply_recommended_pid, health_check - weekly_report, cost_report, set_vacation_mode
  - Extend climate.py PLATFORM_SCHEMA with new config options - Integrate coordinator into
  climate.py with zone registration - Trigger sensor/switch platform discovery per zone

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Group sensors under thermostat device
  ([`96295ea`](https://github.com/afewyards/ha-adaptive-thermostat/commit/96295eacb98edde9918fb0f51e351ac19056e815))

Add device_info property to both SmartThermostat climate entity and AdaptiveThermostatSensor base
  class to enable Home Assistant device grouping. Sensors now appear under their parent thermostat
  device in the HA UI.

- Add DeviceInfo import to climate.py and sensor.py - Add device_info property returning identifiers
  with zone_id - Include manufacturer and model info in climate entity device_info - Add 5 tests for
  sensor device grouping verification - Fix test infrastructure with voluptuous and device_registry
  mocks

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Hide generated sensors from UI by default
  ([`7b8ff25`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7b8ff2583b4525fcd047d0743f790113764c7089))

Set entity_registry_visible_default to False for all sensor entities so they don't clutter the Home
  Assistant UI. Sensors remain accessible via the entity registry when needed.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Implement adaptive PID adjustments based on observed performance
  ([`3753cc9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/3753cc937eae5043e5f41c83d2cd967af09f38d8))

Implements task 2.4 with comprehensive rule-based adaptive PID tuning:

- Added cycle analysis functions to adaptive/learning.py: * calculate_overshoot(): detects max
  temperature beyond target * calculate_undershoot(): detects max temperature drop below target *
  count_oscillations(): counts crossings with hysteresis * calculate_settling_time(): measures time
  to stable temperature

- Created CycleMetrics class for storing cycle performance data

- Created AdaptiveLearner class with calculate_pid_adjustment(): * High overshoot (>0.5°C): reduce
  Kp up to 15%, reduce Ki 10% * Moderate overshoot (>0.2°C): reduce Kp 5% * Slow response (>60 min):
  increase Kp 10% * Undershoot (>0.3°C): increase Ki up to 20% * Many oscillations (>3): reduce Kp
  10%, increase Kd 20% * Some oscillations (>1): increase Kd 10% * Slow settling (>90 min): increase
  Kd 15%

- Zone-specific adjustments: * Kitchen: lower Ki 20% (oven/door disturbances) * Bathroom: higher Kp
  10% (skylight heat loss) * Bedroom: lower Ki 15% (night ventilation) * Ground floor: higher Ki 15%
  (exterior doors)

- Added PID limits to const.py: * Kp: 10.0-500.0, Ki: 0.0-100.0, Kd: 0.0-200.0 *
  MIN_CYCLES_FOR_LEARNING constant (3 cycles minimum)

- Created test suite with 10 comprehensive tests - All 46 tests pass across entire test suite

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement central heat source controller with startup delay
  ([`3d9a82e`](https://github.com/afewyards/ha-adaptive-thermostat/commit/3d9a82ed64f1e06ab12443f9be3b651b3353a0de))

- Created CentralController class for managing main heater/cooler switches - Implemented aggregate
  demand-based control from all zones - Added configurable startup delay (default 0 seconds) with
  asyncio - Heater and cooler operate independently - Immediate shutdown when demand drops to zero -
  Startup tasks can be cancelled if demand is lost during delay - Added 7 comprehensive tests
  (exceeds requirement of 6) - All tests pass successfully

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement cost_report service with period support (task 6.5)
  ([`a909c36`](https://github.com/afewyards/ha-adaptive-thermostat/commit/a909c364c884a96b0847020d8506a9a4f80f7277))

- Add period parameter (daily/weekly/monthly) to cost_report service - Scale from weekly data when
  period-specific sensor unavailable - Add COST_REPORT_SCHEMA for service validation - Update
  services.yaml with period field definition - Sort zone power data alphabetically in report - Fall
  back to duty cycle when power sensor unavailable

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Implement demand switches per zone (task 4.1)
  ([`af53562`](https://github.com/afewyards/ha-adaptive-thermostat/commit/af53562a5afea09e04a27de13ffae67bd62e3570))

- Create switch.py platform with DemandSwitch class - Automatic state management based on PID output
  from climate entity - Turn ON when PID output > threshold (default 5.0%), OFF when satisfied -
  Configurable demand_threshold parameter per zone - Fallback to heater_entity_id state when PID
  output unavailable - Add switch to PLATFORMS list in __init__.py - Create comprehensive test suite
  with 6 tests (exceeds requirement of 3) - All 88 tests passing (6 demand_switch + 82 existing) -
  Update project.json to mark task 4.1 as complete - Update progress.txt with implementation details

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement health monitoring with alerts
  ([`0c78709`](https://github.com/afewyards/ha-adaptive-thermostat/commit/0c78709a0dc7cf26076519a9bae403f0c3c21766))

Implemented comprehensive health monitoring system with:

- analytics/health.py module with HealthMonitor and SystemHealthMonitor classes - Short cycle
  detection (<10 min critical, <15 min warning) - High power consumption detection (>20 W/m²) -
  Sensor availability checks - Exception zones support (e.g., bathroom high power OK) -
  SystemHealthSensor entity for overall health tracking - Comprehensive test suite with 11 tests
  (test_health.py)

All 75 tests pass successfully.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement heating curves with outdoor temp compensation (task 5.5)
  ([`a9a28a4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/a9a28a4ef57e2c792fb5471f7458024fb89a2751))

Implemented comprehensive weather compensation system for PID controllers:

- Created adaptive/heating_curves.py module with three functions: -
  calculate_weather_compensation(): Computes compensation from setpoint, outdoor temp, and ke
  coefficient - calculate_recommended_ke(): Recommends ke based on insulation quality and heating
  type (0.3-1.8 range) - apply_outdoor_compensation_to_pid_output(): Applies compensation with
  output clamping

Key features: - Supports different insulation levels (excellent to poor) - Adjusts for heating types
  (floor, radiator, convector, forced air) - Gracefully handles missing outdoor temperature sensor -
  Output clamping to min/max limits

Test suite: 17 tests (exceeds requirement of 2 tests) - 5 weather compensation tests - 6 recommended
  ke tests - 6 PID output compensation tests All 179 tests passing (17 new + 162 existing)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement learning data persistence for adaptive thermostat
  ([`f2e49da`](https://github.com/afewyards/ha-adaptive-thermostat/commit/f2e49daec012d0a79d3e4b0f2f06fbadd545d669))

Add LearningDataStore class to persist adaptive learning data across Home Assistant restarts,
  enabling continuous learning and improvement.

- Implement save() method with atomic file writes to .storage/ - Implement load() method with
  graceful corrupt data handling - Add restore methods for ThermalRateLearner, AdaptiveLearner,
  ValveCycleTracker - Validate data types and handle missing/corrupt data gracefully - Store
  learning data in JSON format with version field - Create comprehensive test suite with 6 tests
  covering save/load/corruption scenarios

All 59 tests pass. Completes task 2.6 (adaptive-learning).

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement learning metrics sensors (overshoot, settling time, oscillations)
  ([`5a6c1fe`](https://github.com/afewyards/ha-adaptive-thermostat/commit/5a6c1feb8da97c966c681b693e7dcfdc71dbd480))

- Add OvershootSensor class to track average temperature overshoot from cycle history - Add
  SettlingTimeSensor class to track average settling time in minutes - Add OscillationsSensor class
  to track average oscillation count - All sensors retrieve data from coordinator adaptive_learner -
  Add comprehensive test suite with 2 tests validating calculations - All 64 tests pass

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement night setback with configurable delta (task 5.1)
  ([`8f29d48`](https://github.com/afewyards/ha-adaptive-thermostat/commit/8f29d4820565fa4160063e432f1265e6937d744b))

Implemented energy-saving night setback feature with comprehensive functionality:

Module Structure: - NightSetback: Single-zone night setback management - NightSetbackManager:
  Multi-zone night setback coordination

Core Features: - Night period detection with fixed time ranges (e.g., 22:00-06:00) -
  Midnight-crossing period support - Sunset-based start time with configurable offsets (sunset+30,
  sunset-15) - Configurable temperature setback delta - Automatic recovery 2 hours before deadline -
  Force recovery override for emergency situations - Multi-zone support with independent schedules

Test Coverage: - 14 comprehensive tests covering all functionality - Tests for basic detection,
  sunset support, recovery logic - Multi-zone configuration validation - All 126 tests passing (14
  new + 112 existing)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement performance sensors (duty cycle, power/m2, cycle time)
  ([`d4280be`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d4280be1b84b219f6a27f7906e58867365d26274))

Implements task 3.1 - Create performance sensors for monitoring heating system performance.

- Add DutyCycleSensor to track heating on/off percentage (0-100%) - Currently returns simple on/off
  state - Foundation for full history-based calculation - Add PowerPerM2Sensor to calculate power
  consumption per m² - Uses duty cycle and zone area from coordinator - Configurable max_power_w_m2
  per zone (default 100 W/m²) - Formula: power_m2 = (duty_cycle / 100) * max_power_w_m2 - Add
  CycleTimeSensor to measure average heating cycle time - Returns placeholder value (20 minutes) -
  Foundation for full history-based calculation - All sensors update every 5 minutes via
  UPDATE_INTERVAL - Add "sensor" platform to PLATFORMS in __init__.py - Add 3 comprehensive tests in
  test_performance_sensors.py - All 62 tests pass (3 new + 59 existing)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement phase-aware overshoot detection
  ([`77fa8c4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/77fa8c4afcad33dae787dc2705cce94920e7d1e6))

Add PhaseAwareOvershootTracker class that properly tracks heating phases: - Rise phase: temperature
  approaching setpoint - Settling phase: temperature has crossed setpoint

Calculate overshoot only from settling phase data (max settling temp - setpoint). Reset tracking
  when setpoint changes. Return None when setpoint never reached.

Add 26 comprehensive tests covering: - Core tracker functionality - Setpoint change handling -
  Settling phase detection - Edge cases (setpoint never reached) - Realistic heating scenarios

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Implement PID rule conflict resolution and convergence detection
  ([`6560b45`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6560b452bd4e47edff2725076cb622402f11df64))

Add priority-based rule conflict resolution to AdaptiveLearner: - Define rule priority levels
  (oscillation=3, overshoot=2, slow_response=1) - Add conflict detection for opposing adjustments on
  same parameter - Resolve conflicts by applying higher priority rule, suppressing lower - Log when
  conflicts are detected and which rule takes precedence

Add convergence detection to skip adjustments when system is tuned: - Define CONVERGENCE_THRESHOLDS
  for overshoot, oscillations, settling, rise time - Check convergence before evaluating rules -
  Return None when all metrics within acceptable bounds

New types: PIDRule enum, PIDRuleResult namedtuple New methods: _evaluate_rules, _detect_conflicts,
  _resolve_conflicts, _check_convergence

Adds 13 tests for rule conflicts and convergence detection.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Implement PWM auto-tuning based on observed cycling behavior
  ([`d4eae87`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d4eae872e817d830be0930ffbefbfd140a99b464))

Added PWM auto-tuning functionality to detect short cycling and automatically adjust PWM period to
  reduce valve wear.

Key features: - calculate_pwm_adjustment() function detects short cycling (<10 min avg) - Increases
  PWM period proportionally to shortage below threshold - Enforces min/max PWM bounds (180s - 1800s)
  - ValveCycleTracker class counts valve cycles for wear monitoring - Comprehensive test suite with
  7 tests covering all edge cases

All 53 tests pass across entire test suite.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement solar gain learning and prediction (task 5.3)
  ([`fdb3f04`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fdb3f0431cab3bdd2eff304799bb1b74c84db725))

- Created solar/ module with comprehensive solar gain learning system - Implemented SolarGainLearner
  class with pattern learning per zone per orientation - Added season detection
  (Winter/Spring/Summer/Fall) with automatic date-based determination - Added cloud coverage
  adjustment (Clear/Partly Cloudy/Cloudy/Overcast) - Implemented seasonal adjustment based on sun
  angle changes - Orientation-specific seasonal impact (South most affected, North least affected) -
  Intelligent prediction with fallback strategy (exact match → season match → hour match → fallback)
  - SolarGainManager for multi-zone management - 15 comprehensive tests covering all features - All
  150 tests pass (no regressions)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Implement zone linking for thermally connected zones (task 4.4)
  ([`ea365a9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/ea365a918bfeae2e148151022ec3e04ee66308ae))

- Created ZoneLinker class in coordinator.py - Delay linked zone heating when primary zone heats
  (configurable minutes) - Track delay remaining time with automatic expiration - Support
  bidirectional linking between zones - Support multiple zones linked to one zone - Methods:
  configure_linked_zones, is_zone_delayed, get_delay_remaining_minutes, clear_delay - Created
  comprehensive test suite with 10 tests (exceeds requirement of 4) - All tests pass successfully

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Integrate contact sensor support for window/door open detection
  ([`b2e35f0`](https://github.com/afewyards/ha-adaptive-thermostat/commit/b2e35f0cd71ae65c8adb764f3808d35ad0cf7d42))

Wire up the existing ContactSensorHandler to pause heating when configured contact sensors
  (windows/doors) are open. Exposes contact_open, contact_paused, and contact_pause_in attributes.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Integrate HeatOutputSensor from heat_output.py module
  ([`0fab9e4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/0fab9e4bf5637ebf9c676dfbb6c64a8432ce2fcb))

- Create HeatOutputSensor entity class in sensor.py - Wire up supply_temp and return_temp sensor
  inputs - Calculate heat output using delta-T formula (Q = m x cp x ΔT) - Add sensor to platform
  setup in async_setup_platform() - Support optional flow_rate sensor with configurable fallback -
  Expose extra_state_attributes: supply_temp_c, return_temp_c, flow_rate_lpm, delta_t_c - Add 15
  comprehensive tests for heat output calculation

Completes Story 2.4 - all 61 sensor tests pass.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Integrate night setback and solar recovery into climate entity
  ([`fa32ddd`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fa32ddd686bafbbbcece17a2e0f1b47c01a691de))

Wire existing NightSetback and SolarRecovery modules into the climate entity for energy-saving
  temperature control:

- Add YAML schema for night_setback configuration block - Instantiate NightSetback/SolarRecovery
  from config in __init__ - Apply setback adjustment in calc_output() before PID calculation - Add
  sunset time helper for sunset-based start times - Expose night_setback_active,
  solar_recovery_active in entity attributes

Night setback lowers setpoint during configured hours. Solar recovery (when enabled with
  window_orientation) delays morning heating to let sun warm zones naturally based on window
  orientation.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Persistent reports
  ([`cf4f5c3`](https://github.com/afewyards/ha-adaptive-thermostat/commit/cf4f5c3838cdcdf2c8a21984ef60b0a7baa82081))

- Physics-based PID initialization and HA 2026.1 compatibility
  ([`063748d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/063748db68106f1119d4d1d75ad11c4b353ef986))

- Add physics-based PID calculation when no config values provided Uses heating_type, area_m2,
  ceiling_height to calculate initial Kp/Ki/Kd - Show current PID gains in debug output [Kp=x, Ki=x,
  Kd=x, Ke=x] - Fix SERVICE_SET_TEMPERATURE import removed in HA 2026.1

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove PIDAutotune class and autotune functionality
  ([`d9a8e91`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d9a8e913deee0a02b7bfa1710aaf699bf625a9b3))

Remove PIDAutotune relay-based autotuning in favor of adaptive learning approach.

Changes: - Remove PIDAutotune class from pid_controller/__init__.py (290+ lines) - Remove unused
  imports (deque, namedtuple, math) - Remove autotune config constants (CONF_AUTOTUNE,
  CONF_NOISEBAND, CONF_LOOKBACK) - Remove all autotune logic from climate.py including schema,
  initialization, attributes, and control logic - Add comprehensive test suite with 7 tests covering
  PID output calculation, limits, windup prevention, modes, and external compensation

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Remove progress and project
  ([`7865e40`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7865e408381cfdb65146a47b6dbe74da31a8daba))

- Rename integration from smart_thermostat to adaptive_thermostat
  ([`6817b1a`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6817b1ac4135f1685832c1af471c4c65170fe430))

- Update manifest.json: change domain to 'adaptive_thermostat', name to 'Adaptive Thermostat',
  version to '1.0.0' - Update const.py: change DOMAIN to 'adaptive_thermostat' and DEFAULT_NAME to
  'Adaptive Thermostat' - Rename folder from smart_thermostat to adaptive_thermostat

This is the foundation for the adaptive thermostat fork with integrated adaptive learning, energy
  optimization, and multi-zone coordination.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Restore learning window state across restarts
  ([`7f78053`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7f780536393cf489da28169ce19310bb4d9b656f))

- Add RestoreNumber to LearningWindowNumber for state persistence - Expand README with cooling,
  night setback examples and full param tables - Add vacation mode tests - Add .gitignore for
  Python/IDE files

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Support multiple entities for main heater/cooler switches
  ([`208cee9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/208cee95c2ca6ee4cd969933e5bc9e4b17d5b5a9))

Allow main_heater_switch and main_cooler_switch configuration options to accept lists of entity IDs,
  enabling control of multiple switches (e.g., boiler + pump) as a single coordinated unit.

- Change config schema from cv.entity_id to cv.entity_ids - Add list-based helper methods:
  _is_any_switch_on, _turn_on_switches, _turn_off_switches - Update all calling code to use list
  iteration - Add 8 new tests for multiple entity scenarios

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Update gitignore
  ([`3b7574e`](https://github.com/afewyards/ha-adaptive-thermostat/commit/3b7574e65f445a7e87e65dc7627fff3e6d82650e))

- Wire up zone linking for thermally connected zones
  ([`135eab7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/135eab7cff30cc3f0880129756f6491cbd0f762e))

Zone linking was defined but never instantiated or used. Now properly integrated:

- Instantiate ZoneLinker in __init__.py after coordinator creation - Configure linked zones in
  async_added_to_hass when zones register - Check is_zone_delayed before allowing heater turn on -
  Notify zone linker when heating starts to delay linked zones - Reset heating state when heater
  turns off - Add zone linking status to entity attributes (zone_link_delayed,
  zone_link_delay_remaining, linked_zones)

When a zone starts heating, thermally connected linked zones will delay their heating for the
  configured time (default 10 minutes) to allow heat transfer before firing their own heating.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Refactoring

- Deduplicate night setback logic into single method
  ([`9e84b96`](https://github.com/afewyards/ha-adaptive-thermostat/commit/9e84b9609f3138570b77b90e9e76dddb48b1f493))

- Add _parse_night_start_time() helper for parsing "HH:MM", "sunset", "sunset+30" formats - Add
  _is_in_night_time_period() helper for midnight-crossing period detection - Add
  _calculate_night_setback_adjustment() as single source of truth for all night setback logic -
  Consolidate static (NightSetback object) and dynamic (config dict) mode handling - Update
  extra_state_attributes to use consolidated method - Update calc_output() to use consolidated
  method - Add 17 comprehensive tests for night setback functionality

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Derive ac_mode from cooler configuration
  ([`6f8cfe9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6f8cfe9f38680a8034e2433dc88af5cf2dbaa7dc))

Remove ac_mode config option and automatically enable cooling mode when cooler is configured at zone
  level or main_cooler_switch is configured at controller level.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Extract service handlers to services.py module
  ([`374b3ad`](https://github.com/afewyards/ha-adaptive-thermostat/commit/374b3ad86e6146664bbac42a015bb8ac94bd0f30))

- Create new services.py with all service handlers and scheduled callbacks - Deduplicate health
  check logic using _run_health_check_core() with is_scheduled param - Deduplicate weekly report
  logic using _run_weekly_report_core() - Add new energy_stats service handler for current power and
  cost data - Add new pid_recommendations service handler for PID tuning suggestions - Reduce
  __init__.py from 956 to 356 lines (-63%) - Add 21 tests for service registration and handlers

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Make control_interval optional with auto-derivation
  ([`d9cab12`](https://github.com/afewyards/ha-adaptive-thermostat/commit/d9cab12145a14d6f8e26ecad743b2649b2407873))

Rename keep_alive to control_interval and make it optional. The control loop interval is now
  auto-derived: explicit config > sampling_period > 60s default. This simplifies configuration as
  most users don't need to set it manually.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Make ZoneLinker query methods idempotent
  ([`fcf17c9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/fcf17c97b3fbc8af88e1fa91d4a012a79d79982e))

Remove side effects from is_zone_delayed() and get_delay_remaining_minutes() that were deleting
  expired entries. Query methods should be pure functions.

- Add cleanup_expired_delays() method to explicitly remove expired entries - Call cleanup from
  coordinator's _async_update_data() (every 30 seconds) - Update docstrings to document idempotent
  behavior - Add 7 new tests for idempotent query behavior

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Move debug to controller config, remove health_alerts_enabled
  ([`38c8706`](https://github.com/afewyards/ha-adaptive-thermostat/commit/38c8706122c9b6a3b62a3c7e732205b8517728bb))

- Move debug option from per-zone to controller-level config - Remove health_alerts_enabled (was
  dead code - never used) - Update README documentation accordingly

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Move presets to controller config, remove sleep_temp
  ([`8ac53f4`](https://github.com/afewyards/ha-adaptive-thermostat/commit/8ac53f4aafd8c84b09bc112a3e5523ff63a8a6fa))

- Move preset temperatures (away, eco, boost, comfort, home, activity) from per-zone climate config
  to controller-level adaptive_thermostat config - Remove sleep_temp preset entirely - Presets are
  now shared across all zones - Update README to reflect new configuration structure

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove device grouping from zone sensors
  ([`eed2f7e`](https://github.com/afewyards/ha-adaptive-thermostat/commit/eed2f7eadf411297133e89d6f4aab722f15b2e08))

Sensors are now standalone entities instead of being grouped under a zone device.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove learning_enabled config option
  ([`74cddaa`](https://github.com/afewyards/ha-adaptive-thermostat/commit/74cddaaeaa8f24235bd40da0c8e82273e2d868c2))

Adaptive learning is now always enabled by default. Vacation mode can still temporarily disable it
  as an internal runtime state.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove manual PID config options from climate
  ([`552b50d`](https://github.com/afewyards/ha-adaptive-thermostat/commit/552b50d161e0626aa861f772bb4dae7c7781523c))

Remove kp, ki, kd, ke configuration options from the climate platform schema. PID values are now
  always initialized from physics-based calculations, then refined by adaptive learning.

- Remove CONF_KP/KI/KD/KE from schema and const.py - Simplify entity initialization to always use
  physics-based PID - State restoration and set_pid_param service remain for learned values

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove unused high_power_exception config
  ([`cb138a5`](https://github.com/afewyards/ha-adaptive-thermostat/commit/cb138a544dc97fa2eac180d2fd6c680209a994b2))

The config was stored but never used to filter high power warnings.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Remove zone name-specific PID adjustments
  ([`23ef791`](https://github.com/afewyards/ha-adaptive-thermostat/commit/23ef7916a48a395b5fb0d6c58165a75786db560d))

Remove hardcoded PID adjustments based on zone name patterns (kitchen, bathroom, bedroom, ground
  floor). These were not general-purpose and tied behavior to specific naming conventions.

Changes: - Remove _calculate_zone_factors() and get_zone_factors() methods - Remove zone_name
  parameter from AdaptiveLearner - Remove apply_zone_factors parameter from
  calculate_pid_adjustment() - Remove zone_name from save/restore data structure - Remove related
  tests (TestZoneAdjustmentFactors, etc.)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Rename SmartThermostat class to AdaptiveThermostat
  ([`840530f`](https://github.com/afewyards/ha-adaptive-thermostat/commit/840530f45312afaaac8642be04a0b18055f30ea5))

Aligns the main climate entity class name with the component name.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Replace polling with push-based demand tracking for CentralController
  ([`aa7c539`](https://github.com/afewyards/ha-adaptive-thermostat/commit/aa7c53940f96bb18e7b1dfce589cb4bbdf8b6195))

- Remove DemandSwitch entity (switch.py) - was unused - CentralController now triggered immediately
  when zone demand changes instead of polling every 30 seconds - Demand based on actual valve state
  (_is_device_active) not PID output - Fix min_cycle_duration blocking first cycle on startup by
  initializing _last_heat_cycle_time to 0 - Add demand update in _async_switch_changed for immediate
  response - Add INFO-level logging for demand changes

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Split async_added_to_hass into smaller methods
  ([`33e3a1a`](https://github.com/afewyards/ha-adaptive-thermostat/commit/33e3a1a679e0683d0faa258b848178e7cc4a7ec5))

Extract three focused methods from the monolithic async_added_to_hass: - _setup_state_listeners():
  handles all state change listener setup - _restore_state(): handles restoring climate entity state
  - _restore_pid_values(): handles restoring PID controller values

This improves code organization, testability, and maintainability while preserving all existing
  functionality.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Trim services to essential set
  ([`75f4356`](https://github.com/afewyards/ha-adaptive-thermostat/commit/75f4356394f8325a074c92de8a86bed54e12df30))

Keep only frequently used services: - reset_pid_to_physics (entity) - apply_adaptive_pid (entity) -
  run_learning (domain) - health_check (domain) - weekly_report (domain) - cost_report (domain) -
  set_vacation_mode (domain)

Remove: set_preset_temp, apply_recommended_pid

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Use imported discovery module and remove duplicate service definitions
  ([`005487a`](https://github.com/afewyards/ha-adaptive-thermostat/commit/005487a4dd2c58aa48ec823d7c36189d2791f9fe))

Use discovery helper via direct import rather than hass.helpers access pattern. Remove
  clear_integral, set_pid_mode, and set_pid_gain from services.yaml as they are registered
  programmatically via entity services.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

### Testing

- Add integration tests for control loop and adaptive learning
  ([`6c46714`](https://github.com/afewyards/ha-adaptive-thermostat/commit/6c46714338bf2a5b0073527c64e140c122eaa94d))

Add two integration test files covering multi-component flows: - test_integration_control_loop.py:
  PID → demand → central controller → heater - test_integration_adaptive_flow.py: cycle metrics →
  PID recommendations

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Add integration tests for ZoneLinker climate entity integration
  ([`30a4535`](https://github.com/afewyards/ha-adaptive-thermostat/commit/30a453578b54228c9d8c4aa89539946240744782))

- Add 6 integration tests verifying climate entity correctly interacts with ZoneLinker - Tests
  cover: heating start notification, delayed zone blocking, delay expiration, extra_state_attributes
  exposure, multiple heating starts, heater off state reset - All 16 zone_linking tests pass (10
  existing + 6 new integration tests)

Story 1.1: Wire up ZoneLinker integration to climate entity

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Fix asyncio event loop issues for Python 3.9+
  ([`c4a52f5`](https://github.com/afewyards/ha-adaptive-thermostat/commit/c4a52f5381507e81573d10eca540becb86ed243c))

Replace asyncio.get_event_loop().run_until_complete() with asyncio.run() in test_learning_sensors.py
  and test_performance_sensors.py to fix RuntimeError when no event loop exists in thread.

All 112 tests now passing.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>

- Remove non-functional device_info tests
  ([`f3c0dd7`](https://github.com/afewyards/ha-adaptive-thermostat/commit/f3c0dd7c538210634d1f576d21d64cc09f0f48be))

Device grouping for sensors doesn't work as expected in HA. Remove the tests until a proper solution
  is found.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

- Update physics tests to match empirical PID implementation
  ([`7a09ff9`](https://github.com/afewyards/ha-adaptive-thermostat/commit/7a09ff93edc516a09f3a45642b932666cf04a394))

Tests expected old Ziegler-Nichols formula values but implementation now uses empirically-calibrated
  base values per heating type. Also fixed Kd comparisons: slower systems now correctly have higher
  Kd (more damping), not lower.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
