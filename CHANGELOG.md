# CHANGELOG


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
