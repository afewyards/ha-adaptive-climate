# Heat from below: per-cycle tracking

## Intent

Rooms on the upper floors overshoot by about 1°. The owner suspects heat from
the floor below. Stored cycles cannot confirm it: they carry no timestamps and
nothing about other zones. Record, per cycle, whether a zone on a lower floor
was heating, and show on the learning card how overshoots line up with it.

This is measurement only. After a week or two the card answers the question.

## Goals

- Each new heating cycle records `heat_below`: the fraction (0 to 1, 2 dp) of
  the cycle, from its start until its metrics are finalised, during which at
  least one other zone on the floor below was actively heating.
- `heat_below` is `None` when it cannot be known: the zone's floor is unknown,
  no other zone sits on a lower floor, or the cycle predates this change.
- Floors come from Home Assistant (entity, area, floor level) through the
  existing `helpers/registry.py::discover_zone_floors`. "The floor below" is
  the nearest lower level that has a zone. Counting every lower floor would
  read "heating" on nearly every top-floor cycle and tell nothing.
- The `learning` attribute gains `below`, over the last 20 cycles that have a
  `heat_below` value:
  `{"over": n, "over_with": n, "rest": n, "rest_with": n}` where `over` counts
  cycles that failed on overshoot (the shared `cycle_failures` rule), `rest`
  the others, and `_with` those with `heat_below` of 0.25 or more. Left out
  when no cycle has a value.
- Card, opened room, one line under the facts line, only when `below.over` is
  at least 1:
  "7 of 9 overshoots came while a room below was heating, against 3 of 11
  other cycles." When `rest` is 0 the part from ", against" on is dropped.
  Singular for a total of 1 ("1 of 1 overshoot", "0 of 1 other cycle").

## Non-goals

- Changing how a cycle is judged, weighted or learned from.
- Configuring or changing thermal groups.
- Sun, cooking or other heat sources.
- Which room is above which; floors only.

## Anti-goals

- No `STORAGE_VERSION` or learner `format_version` bump: the field is an
  optional key, absent means `None`.
- No effect on control, confidence or tuning.
- No per-cycle lists in entity attributes.
- A lookup failure (registry, coordinator, a zone unloading) must never break
  a cycle or a state write; the value becomes `None`.

## Approaches

1. **A field on the cycle, sampled while the cycle runs.** Picked: the cycle
   tracker already samples during a cycle, and the value stays attached to
   the cycle it describes.
2. Query the HA recorder when a cycle ends. No sampling code, but async
   database reads in the cycle path and a dependency on recorder retention.
3. Timestamps on cycles, correlate later. Most flexible, but a store format
   change and the correlation still needs the other zones' history.

## Left to the builder

- Where the sample is taken, how another zone's "actively heating" is read
  (prefer what the coordinator already knows over state strings), names.

## Calls made by the author, not the owner

- Only the nearest lower floor with zones counts.
- 0.25 of the cycle as the threshold for "a room below was heating".
- The line shows only for rooms with at least one overshoot.
