# Ki boost and overshoot: judge only what the controller caused

## Intent

Every gain change in nine months has been an undershoot Ki boost, and the
bedroom sits at Ki 97.8 against a physics value of 6.8. The boost fires in
situations more Ki cannot fix, and a second boost path works from a biased
number. Overshoot, now the main failure, counts readings the controller did
not cause. Fix the inputs before changing any tuning rule.

## Findings this rests on

- The undershoot detector is fed the base target (`climate_control.py:178`),
  while the PID uses the effective target (night setback, contact clamp,
  cooling clamp; `managers/control_output.py:190`). A bedroom with its
  windows open or in setback reads as "undershooting" for hours.
- No boost path looks at whether the output is already at its maximum.
- The 3× cap is measured against a `physics_init`/`physics_reset` entry in a
  10-entry gain history; once that entry is pushed out only the cumulative
  multiplier is left, and that is reset by other events.
- `_check_physics_rate_and_boost_ki` (`climate_cycle_handlers.py:149`) has no
  cooldown, is not clamped, and compares a measured heating rate that reads
  low against a formula.
- Overshoot is the highest reading from the first crossing to the end of the
  cycle (up to 4 h of settling). `on_heater_stopped` and the 45-minute window
  are never wired. A cycle that starts above target counts its starting
  excess.
- Stored cycles (2026-11-15): no overshooting cycle started above target, so
  that fix is for correctness and changes nothing today. Whether late peaks
  explain the kitchen and study is unknown; cycles carry no timing.

## Goals

1. The undershoot detector gets the effective target, the same value the PID
   controls to.
2. While the output is saturated (at or above 95% of the output maximum) the
   real-time detector does not add time or debt. Decay and the reset above
   target still run.
3. A cycle whose output was saturated for at least half of its samples is
   not a chronic-approach failure. A saturated cycle that reaches target
   cleanly still clears the failure streak, as any clean cycle does. A cycle
   that crossed target at all (it has an overshoot value) is never a
   chronic-approach failure, so ignoring a late peak cannot create one.
4. The 3× cap is measured against the physics Ki computed fresh
   (`managers/pid_tuning.py::calculate_physics_gains`, as `gains_physics` on
   the learning attribute already does). The cumulative-multiplier gate
   stays. When physics cannot be computed the cumulative gate is the only cap,
   as today.
5. The heating-rate boost path is removed: `_check_physics_rate_and_boost_ki`
   and its two calls, plus whatever only it used
   (`check_physics_rate_underperformance`, the history baseline lookup if it
   has no caller left). The dead `check_rate_based_undershoot` /
   `apply_rate_adjustment` and their duty helper go too. Heating-rate
   sessions and the `heating_rate` value on the card stay.
6. Heating overshoot is measured from `max(target, cycle start temperature)`.
   Cooling cycles are judged as before.
7. After the last valve close, peak tracking ends once the temperature has
   fallen 0.2° below the running peak. Higher readings after that are
   ignored, with one info log line giving the ignored excess. With no known
   valve close time, behaviour is as today. The unused 45-minute window code
   is removed.

## Non-goals

- Lowering any Ki that is already boosted. Existing gains stay.
- Auto-tune gate, overshoot tuning rules, confidence and night setback rules.
- Supply temperature.
- Sun or weather input to the disturbance detector.

## Anti-goals

- No `STORAGE_VERSION` or learner format bump. A new per-cycle value, if one
  is needed, is an optional key.
- No boost becomes easier than today: every change either removes a path or
  adds a condition.
- `managers/cycle_tracker.py` is already over the 800-line limit. It must not
  grow; move per-cycle sampling out if goal 3 needs room.

## Approaches

1. **Fix the inputs to the existing detector and judge.** Picked: each change
   is small, removes a wrong signal, and leaves the rules themselves alone.
2. Read the supply sensor and heat source state to gate boosts. Covers "source
   off" directly, but a saturated output already covers it and needs no
   sensor.
3. Replace the Ki boost with a fault notice ("room cannot reach target").
   Better in the long run, larger, and a change of behaviour the owner has not
   asked for.

## Tests and docs

- Detector: setback or contact clamp active and room between the two targets
  adds no debt; saturated output adds no debt but decays; a saturated cycle
  neither counts nor clears the streak; cap holds at 3× fresh physics with no
  physics entry in history.
- Overshoot: start above target; late second peak after a 0.2° fall is
  ignored; single slow coast after valve close is still counted in full;
  missing valve close time gives today's result.
- Tests for the removed path are removed, not skipped.
- `docs/subsystems/` page covering undershoot detection and cycle metrics.

## Calls made by the author, not the owner

- 95% as "saturated", half the samples for a cycle.
- 0.2° fall as the end of the controller's own peak (sensors step in 0.1°).
- Cap against fresh physics. For the ground floor this reads 2.2× where the
  old history entry read 3.2×; the cumulative gate still blocks it at 3.0.

## Left to the builder

- Where saturation is sampled per cycle, names, how the fresh physics Ki
  reaches the detector.
