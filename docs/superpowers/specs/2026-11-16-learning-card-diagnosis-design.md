# Learning card: diagnosis view

## Intent

The learning card shows the outcome per room but not why. Finding out why a
room stayed in "collecting" took SSH and a replay script. The card should let
the owner see, per room: what is wrong, how the recent cycles were judged,
what the gains are and how they changed, and what the tuner is waiting for.

The first attempt put all of that on the row and was hard to scan. The agreed
layout is the opposite: a closed room is one sentence, and the detail is one
click away.

Reference mockup (approved): `.superpowers/brainstorm/learning-card-diagnosis.html`.
Layout, wording and order are as in the mockup. Its fonts and colours are
stand-ins; the card keeps using HA theme variables as today.

## Goals

Closed room, three things:

- one sentence, first match wins:
  1. tune check running: "Checking a tune from 9 Nov" (date of the newest
     auto-tune in the gain history)
  2. the worst off measurement: "Ends 0.4° below target" (undershoot),
     "Overshoots by 0.39°", "Heats at 61% of the expected rate",
     "Takes 140 min to settle"
  3. no measurements yet: "Still collecting"
  4. otherwise "On track"
  A room with an off measurement gets the amber dot, also in case 1.
- a strip of up to 20 ticks, oldest first: tall neutral = good cycle, short
  amber = poor cycle. Hidden from screen readers; the facts line carries it.
- confidence %.

Header: title and "N of M rooms need attention" (rooms with an off
measurement), or "All rooms on track".

Opened room (one open at a time, the row is a button with `aria-expanded`):

1. Facts line: status, cycle count, "X of the last N were good", and the most
   common failure in words ("13 ended too low", "11 overshot",
   "2 settled too slowly", "3 swung too much", "2 rose too slowly").
2. Four measurement rows with limit tick, off ones first: undershoot,
   heats at, overshoot, settles in. Same off rules as today; undershoot is off
   above its limit.
3. Gains on one line: Kp, Ki, Kd. A gain that differs from its physics value
   by 1.5× or more (either way) gets the flag "3.2× physics".
4. Timeline: a "next" entry, then the gain changes newest first. Three shown,
   the rest behind "Show N older changes". An entry shows only the gains that
   changed against the entry before it; the oldest entry lists Kp, Ki, Kd.
   Entries that change no gain are skipped. Reasons in plain words:
   `undershoot_ki_boost` "undershoot boost", `auto_apply` "auto-tune",
   `rollback` "rolled back", `physics_reset` "reset to physics",
   `physics_init` "set from physics", `restore` "restored at start",
   `manual_apply` "applied by hand", anything else with underscores as spaces.
5. Holds line, only the parts that apply: "Ki boost is at its cap.",
   "No Ki boost while the check runs.", "Night setback held at 0.5° until 32%."
   (a limit of 0: "Night setback off for now.")

"next" wording by tune state:

| state | text |
|---|---|
| ready | Tune check at the next cycle |
| waiting, cycles left | Tune check after 10 more cycles |
| waiting, cycles and time left | Tune check after 1 more cycle, not before 40 h |
| waiting, only time left | Tune check in 14 h |
| checking | Tune check running, 2 of 5 cycles |
| blocked | No tuning: <reason in plain words> |
| off | No tuning: <reason> |

Layout: narrow as in the mockup (name over sentence, strip, %); from 520 px
container width one line per closed room. Readable from 330 px.

## Non-goals

- Actions from the card (apply, rollback, reset).
- More than the 10 gain changes the thermostat already keeps.
- Trends over time, cooling mode, Ke history as its own view.
- Changing any limit, gate or learning rule.

## Anti-goals

- No per-cycle lists of numbers in attributes (recorder size).
- No new entities, no store change, no `STORAGE_VERSION` or learner format bump.
- No side effects from building the attribute: reading the tune state must
  not record a seasonal shift, start validation or touch counters.
- No build step or dependency for the card.

## Constraints

- `CLAUDE.md` rules (pyright strict, ruff, 800 lines, no `assert`).
  `managers/state_attributes.py` is over the limit: new code goes in
  `managers/learning_profile.py` or a module beside it.
- The learner's "physics baseline" is re-anchored to the current gains at
  every restart, so it is not the physics value. `gains_physics` below is
  what a reset to physics would set now.
- The Ki boost factor is published today only under `debug`.
- Card tests are the self-checking harness page
  `frontend/harness.html`; the pure functions exported on
  `window.AdaptiveClimateLearningCard` are what it checks.

## Approaches

1. **Extend the `learning` attribute, card reads it plus `pid_history`.**
   Picked: same path as the existing card, nothing new to subscribe to.
2. Websocket command with full cycle data. Room for history, but most code
   and polling; not needed for 20 verdicts.
3. Card recomputes verdicts from raw cycles. Would put per-cycle numbers in
   the recorder and duplicate the rules in JavaScript.

## Design

### Backend: additions to `learning` (heating mode)

All optional; a key is left out when unknown. Existing keys unchanged.

| key | type | meaning |
|---|---|---|
| `undershoot` | float, 2 dp | mean undershoot of the last 20 cycles that have one |
| `undershoot_limit` | float | the zone's `undershoot_max` |
| `recent` | str | one char per cycle, last 20, oldest first: `g` good, `p` poor |
| `recent_fails` | dict[str, int] | poor cycles in `recent` by cause; keys `undershoot`, `overshoot`, `settling`, `oscillation`, `rise_time`; a cycle counts once per cause it failed; zero counts left out |
| `gains` | `{kp, ki, kd}` | gains in use now |
| `gains_physics` | `{kp, ki, kd}` | what a reset to physics would set; left out when physics cannot be computed |
| `ki_boost_capped` | `true` | only when the undershoot Ki multiplier is at its cap |
| `tune` | dict | see below |
| `setback_limit` | float | night setback depth learning allows now, only when it is below the configured depth and not caused by a window or humidity pause |
| `setback_until` | int | confidence % at which the limit next relaxes; with `setback_limit` only |

`tune`:

- `state`: `ready`, `waiting`, `checking`, `blocked`, `off`
- `waiting`: `cycles_left` (int, the larger of cycles still needed to reach
  the minimum and cycles left in the cooldown) and/or `hours_left` (int,
  rounded up); zero values left out
- `checking`: `done`, `of` (validation cycles)
- `blocked`: `reason`, one of `lifetime_limit`, `seasonal_limit`,
  `drift_limit`, `seasonal_shift`
- `off`: `reason`, one of `disabled`, `no_room_size`, `no_baseline`
- `ready` and `waiting` may carry `last`: the skip reason of the most recent
  check. The card words `converged`, `no_rules_triggered`, `no_change` and
  `insufficient_usable_data` as a "Last check: ..." line and hides the rest.
- `tune` is left out in cooling mode. A seasonal shift is only reported once
  recorded, so a room shown as ready can still be blocked at its next cycle.

Good/poor uses the same rule as confidence. The rule in
`adaptive/learning_coordinator.py::update_convergence_confidence` moves into
one pure function that returns the list of failed causes; both callers use it.
For the strip, "recovery" is decided with today's confidence, so an old cycle
can show differently from how it was counted then. Documented, accepted.

### Frontend

`frontend/adaptive-climate-learning-card.js` is reworked to the mockup. Pure
functions (sentence, facts line, timeline entries, tune text, holds) are
exported on `window.AdaptiveClimateLearningCard` and covered in
`frontend/harness.html`. A zone without the new keys (older backend) still
renders: sentence from the old keys, no strip, no timeline "next".

### Tests and docs

- Python: each new key present and absent, `recent`/`recent_fails` against
  hand-made cycles, each `tune` state, no side effects on the validation
  manager, the shared good/poor function giving the same confidence results
  as before (existing tests stay green).
- Card: harness cases for every sentence branch, each tune text, timeline
  diffing and skipping, older-backend fallback.
- Docs: README card section, `docs/subsystems/state-attributes.md`.

## Calls made by the author, not the owner

- Sentence priority and wording; "needs attention" counts off measurements only.
- 1.5× as the threshold for the physics flag.
- Three log entries before "Show older".
- Strip verdicts recomputed with today's rules.
- `gains_physics` computed fresh instead of using the learner's baseline.

## Left to the builder

- Function and module names, how the tune state is derived from the existing
  gate code, DOM and CSS details.
