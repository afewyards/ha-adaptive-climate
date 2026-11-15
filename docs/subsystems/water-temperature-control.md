# Water Temperature Control

Drives the heat pump's supply water temperature setpoints. Cooling is computed
from the worst-case indoor dew point; heating pushes a configured target. Both
ramp gradually when a mode resumes after a long idle period.

**Configuration (domain-level):**
```yaml
adaptive_climate:
  supply_temp_sensor: sensor.heatpump_supply_temp  # existing domain-level key; heating ramp seeding reads it (see Ramps)
  water_temp_control:
    idle_days: 7                    # ramp restarts after this many days inactive
    min_write_interval: 1800        # s between unsafe-direction writes
    condensation_sensor: binary_sensor.manifold_condensation
    cooling:
      target_entity: number.heatpump_cool_supply
      min_supply_temp: 18.0
      dew_point_margin: 2.0
      fallback_humidity: 65
      ramp_start: 22.0
      ramp_rate: 1.0
      extra_sensors:
        - humidity: sensor.manifold_rh
          temperature: sensor.manifold_temp
    heating:
      target_entity: number.heatpump_heat_supply
      target: 35.0                  # Range(20, 45); defaults to supply_temperature
      ramp_start: 25.0
      ramp_rate: 2.0
```

**Entity-level:** `exclude_from_dew_point: true` omits a zone's humidity from the scan.

**Warnings:**
- The heating half **overrides a heat pump's own weather-compensation curve** — only configure it if the pump runs a fixed setpoint.
- `supply_temperature` also feeds physics-based PID init; changing it changes both.
- Insulated supply pipework and manifold are a prerequisite for radiant cooling near the dew point.

**Dew point sources:** every COOL-mode zone with a `humidity_sensor` (minus
`exclude_from_dew_point` zones and zones whose HumidityDetector is
paused/stabilizing), plus configured `extra_sensors` pairs. A registered zone
whose HVAC mode can't be resolved (climate entity not yet loaded, renamed,
unavailable) still contributes a conservative blind reading instead of being
silently dropped (`managers/water_temp_blind_zones.py`). Worst (highest) dew
point wins. RH is smoothed with a 20-min EMA per source. Implausible RH falls
back to `fallback_humidity`; implausible/missing temperature drops the source;
stale readings use `max(last_ema, fallback)`. With no real reading anywhere the
system is blind and floors at `max(min_supply_temp, 20.0)`.

**Targets:** `cooling = max(dew_point + margin, min_supply_temp)`,
`heating = target`. While a ramp is active the ramp bound applies instead.

**Ramps:** start when a mode becomes active after ≥ `idle_days` inactive. Heating
seeds from `max(ramp_start, measured supply water temp)` — the domain-level
`supply_temp_sensor` (config validation rejects it being the same entity as
either mode's `target_entity`), used only when configured, parses as a float,
reads within 5–70 °C, has a `device_class` of `temperature` or none and a
Celsius `unit_of_measurement` or none (catches a °F sensor or an unrelated
DHW-loop sensor with a plausible-but-wrong value), isn't stale, and isn't
frozen. Staleness checks `state.last_reported` first (HA advances it on
every poll, unlike `last_updated`, which only advances on a value change)
and falls back to `last_updated`; no usable timestamp at all counts as
stale. `WATER_TEMP_STALE_MINUTES` = 60 min. Separately, the frozen check
reads `state.last_changed` (advances only when the *value* itself changes):
a reading whose value hasn't moved in `WATER_TEMP_SEED_SUPPLY_FROZEN_MINUTES`
(4 h) is rejected even if still polling — supply water is a continuously
varying analog signal, so a bit-identical value for that long means a dead
sensor or a dead pump, not a genuinely steady loop. This frozen check applies
only to the heating ramp seed, not to dew-point humidity source scanning.
Missing, stale, frozen, implausible, wrong device_class/unit, or not
configured falls back to the plain configured `ramp_start`, logging a
WARNING at ramp start naming the specific cause (a merely-cold measurement
is normal and stays silent). The target setpoint entity is never read for
seeding — it sits idle for ≥ `idle_days` before a ramp starts, so its value
is stale by definition and proves nothing about the water. Cooling always
uses the configured `ramp_start`. Heating and cooling track idle/ramp state
independently.

**Write policy:** round to the entity's `step` toward the safe side (cooling up,
heating down), clamp to the entity's min/max, compare the post-clamp value.
Safe-direction changes write immediately; unsafe-direction changes must persist
for `min_write_interval`. No write while the target entity has no state (the
5-min timer retries). An entity min/max clamp past the mode's safe bound sets
`binding_constraint: entity_limit` and logs a rate-limited warning. Mode
deactivation parks at `ramp_start`. Pure rounding/clamping/direction-safety
helpers live in `managers/water_temp_writer.py`; ramp math and the
measured-supply seed live in `managers/water_temp_ramp.py`.

**Interlocks (cooling):** the condensation sensor being ON, or any COOL zone
reporting `open_window` / `contact_open`, forces an immediate park — never below
the current dew target (`max(ramp_start, dew_target)`) — and holds for 30
minutes after the condition clears.

**Learning protection:** `coordinator.water_temp_learning_gate(mode)` is true
while a ramp is active and for one 60-min settling window after any write of
≥ 1.0 °C. While true, affected zones suppress cycle recording and undershoot
detection, surfaced as the existing `learning_grace` override.

**Cooling clamp:** `coordinator.min_cooling_target` reads the controller's live
effective supply temp (+ `cooling_supply_margin`), falling back to the static
`cooling_supply_temp` before the first computation. Setup logs a warning
(`check_cooling_supply_conflict`) when a configured static `cooling_supply_temp`
disagrees with `water_temp_control.cooling.min_supply_temp`.

**Persistence:** top-level `water_temp_state` key in the learning store. ISO
timestamps for last-active and ramp-start per mode, plus the last value
written per entity. `STORAGE_VERSION` 6 migrates older stores by clearing the
entire persisted heating ramp block (`last_active`, `ramp_started`,
`ramp_start_value`) — under the pre-fix build `ramp_start_value` could hold
the *setpoint* entity's value, which is no longer a trusted seed. Clearing
`last_active` too (not just the seed) makes heating look long-idle on
restore, so a ramp in flight across the upgrade restarts and re-seeds from a
live measurement instead of continuing from the stale seed. Cooling's block
is untouched.

**Downgrading is not supported.** `LearningDataStore._validate_data` rejects
any `version` outside `1..STORAGE_VERSION`, so a v6 store loaded by a build
older than this one falls outside its own `1..5` range, fails validation,
and is silently replaced with an empty `{"version": 5, "zones": {}}` — the
next save overwrites the file, permanently losing all learning data. Do not
roll back to a pre-v6 release once any zone has loaded on this version.

**Diagnostic sensor:** `sensor.water_supply_temperature_target` — state is the
effective supply temp; attributes are `mode`, `dew_point`, `binding_constraint`
(`dew_point` / `min_supply` / `ramp` / `target` / `interlock` / `blind` / `entity_limit`),
`ramp_active`, `days_remaining`, `worst_source`.
