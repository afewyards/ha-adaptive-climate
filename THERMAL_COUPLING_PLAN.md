# Thermal Coupling Learning - Implementation Plan

## Goal
Remove `ZoneLinker` entirely. Replace with auto-discovered thermal coupling coefficients and feedforward compensation.

## New Module: `adaptive/thermal_coupling.py`

### Data Structures
```python
@dataclass
class CouplingObservation:
    timestamp: datetime
    source_zone: str           # Zone heating (directional: source→target)
    target_zone: str           # Zone idle, being observed
    source_temp_delta: float   # Source temp rise (°C)
    target_temp_delta: float   # Target temp change (°C, spillover)
    duration_minutes: float
    outdoor_temp_start: float | None
    outdoor_temp_end: float | None

@dataclass
class CouplingCoefficient:
    source_zone: str           # Directional: A→B is separate from B→A
    target_zone: str
    coefficient: float         # °C_target / (°C_source × hour)
    confidence: float          # 0.0-1.0
    observation_count: int
    last_updated: datetime
    baseline_overshoot: float | None  # Target zone overshoot before compensation
    validation_cycles: int     # Cycles since compensation enabled

@dataclass
class ObservationContext:
    """Tracks an in-progress observation window."""
    source_zone: str
    start_time: datetime
    source_temp_start: float
    target_temps_start: dict[str, float]  # zone_id → temp at start
    outdoor_temp_start: float | None
```

### ThermalCouplingLearner
```python
class ThermalCouplingLearner:
    _observations: dict[tuple[str,str], list[CouplingObservation]]  # (source,target) → obs
    _coefficients: dict[tuple[str,str], CouplingCoefficient]
    _pending: dict[str, ObservationContext]  # source_zone → active observation
    _seeds: dict[tuple[str,str], float]      # From floorplan config
    _lock: asyncio.Lock                       # Thread safety for concurrent access

    # Constants
    MIN_OBSERVATIONS = 3
    MAX_OBSERVATIONS_PER_PAIR = 50
    MIN_DURATION_MIN = 15
    MAX_DURATION_MIN = 120         # 2h max observation window
    MIN_SOURCE_TEMP_RISE = 0.3     # °C minimum to consider valid
    OUTDOOR_TEMP_CHANGE_MAX = 3.0  # Skip if outdoor changed more
    CONFIDENCE_THRESHOLD = 0.3
    MAX_COEFFICIENT = 0.5
    SEED_WEIGHT = 6
    VALIDATION_CYCLES = 5
    VALIDATION_DEGRADATION = 0.30  # 30% worse triggers rollback
```

### Seeded Estimates (Physics-Based)
If floorplan configured, system auto-generates seeds:
- **same_floor**: 0.15 initial (partitions reduce coupling)
- **up** (floor N → N+1): 0.40 initial (heat rises via convection)
- **down** (floor N → N-1): 0.10 initial (conduction through slab only)
- **open**: 0.60 initial (no walls, near-unified thermal mass)
- **stairwell_up**: 0.45 initial (chimney effect in open staircase)

All seeded pairs start with confidence 0.3, refined by observations.
Without floorplan: pure auto-discovery, 2-4 weeks to converge.

## Observation Collection

**Triggers** (coordinator tracks demand state):
- **Start**: Zone demand transitions to True, wait 5 min stabilization
- **End**: Zone demand transitions to False, OR 2h max window reached
- **Record**: All idle zones' temp change during window

**"Active heating zone"**: `coordinator.get_zone_data(zone_id)["has_demand"] == True`
PWM zones are "active" for entire demand period (not just ON portions).

**Detection mechanisms**:
- **Solar gain**: Use existing `SunPositionCalculator` - skip if sun elevation >15° AND any observed zone has south-facing window
- **Mass recovery**: Skip if >50% of registered zones have `has_demand=True` simultaneously
- **Outdoor temp**: Access via `coordinator.outdoor_temp` (from domain config `weather_entity`)

**Filtering** (all must pass):
- Skip if multiple zones heating (can't isolate single source)
- Skip high solar gain periods (sun elevation + window orientation)
- Require 15+ min duration
- Require source temp rise ≥0.3°C
- **Skip if target warmer than source** (heat can't flow uphill)
- **Skip if outdoor temp changed >3°C** (confounds heat loss calculation)
- **Skip if target temp dropped** (external factors dominating)
- **Skip during mass recovery** (>50% zones demanding = setback recovery)

## Coefficient Calculation

```
transfer_rate = target_temp_delta / (source_temp_delta × hours)
```

**Bayesian blend with seed** (seed treated as prior):
```python
SEED_WEIGHT = 6  # Seed counts as 6 virtual observations (gives 0.3 base confidence)

if has_seed:
    # Blend seed with observations - seed influence fades as data accumulates
    observed_avg = robust_average(transfer_rates)  # MAD outlier rejection
    coefficient = (seed * SEED_WEIGHT + observed_avg * obs_count) / (SEED_WEIGHT + obs_count)
else:
    # Pure auto-discovery - no seed
    coefficient = robust_average(transfer_rates)

# Confidence calculation
base_confidence = min(1.0, (SEED_WEIGHT + obs_count) / 20)
variance_penalty = 1 - normalized_variance  # High variance reduces confidence
confidence = base_confidence × variance_penalty
```

**Example progression** (seed=0.40, learned=0.35):
| Observations | Coefficient | Confidence | Compensation |
|--------------|-------------|------------|--------------|
| 0 (seed only) | 0.40 | 0.30 | 0% (at threshold) |
| 3 | 0.38 | 0.45 | 75% graduated |
| 6 | 0.37 | 0.60 | 100% |
| 14 | 0.36 | 1.00 | 100% |

Seed provides immediate baseline; real observations refine and increase confidence.

## Feedforward Compensation

**Integration point**: New `_feedforward` term in PID controller (separate from `_external`/Ke)

```python
# In ControlOutputManager: calculate compensation in °C
coupling_compensation_degC = sum(
    coeff × source_temp_delta × hours × graduated_confidence(confidence)
    for source_zone, state in coordinator.get_active_zones().items()
    if source_zone != self.zone_id
)
coupling_compensation_degC = min(coupling_compensation_degC, MAX_COMPENSATION[heating_type])

# Convert °C to power % using Kp as scaling factor
coupling_compensation_power = coupling_compensation_degC * self._pid.kp

# Apply in PID as feedforward term (reduces output when neighbors heat)
output = P + I + D + E - F  # where F = feedforward
# Update integral anti-windup: I_max = out_max - E - F
```

**PID controller changes**:
```python
def set_feedforward(self, ff: float) -> None:
    """Set feedforward compensation (positive = reduce output)."""
    self._feedforward = ff

# In calc():
self._output = self._proportional + self._integral + self._derivative + self._external - self._feedforward
```

**Graduated confidence ramp-in** (smoother than hard threshold):
```python
def graduated_confidence(confidence: float) -> float:
    if confidence < 0.3: return 0.0
    if confidence >= 0.5: return 1.0
    return (confidence - 0.3) / 0.2  # Linear ramp 0.3→0.5
```

**Max compensation by heating type** (in °C):
- `floor_hydronic`: 1.0°C (slow recovery)
- `radiator`: 1.2°C
- `convector`: 1.5°C
- `forced_air`: 2.0°C

**Cooling mode**: Disable coupling compensation for v1 (`hvac_mode == "cool"` → feedforward=0)

## Storage (Version 4)

Coordinator-level (not per-zone):
```json
{
  "version": 4,
  "zones": { ... },
  "thermal_coupling": {
    "observations": { "source_zone|target_zone": [...] },
    "coefficients": { "source_zone|target_zone": {...} }
  }
}
```

**Key format**: `"source_zone|target_zone"` is directional (A→B separate from B→A)

**Migration v3→v4**:
```python
def _migrate_v3_to_v4(self, v3_data: dict) -> dict:
    v4_data = v3_data.copy()
    v4_data["version"] = 4
    v4_data["thermal_coupling"] = {
        "observations": {},
        "coefficients": {}
    }
    return v4_data
```

Note: floorplan is config-only (not persisted), seeds are regenerated on startup.

## Remove ZoneLinker

Delete entirely from codebase:
- `ZoneLinker` class in `coordinator.py`
- `linked_zones` config option
- `zone_link_delayed`, `zone_link_delay_remaining` attributes
- Related tests in `test_zone_linking.py`

## Config

```yaml
adaptive_thermostat:
  thermal_coupling:
    enabled: true              # default true

    # Floorplan-based hints - system infers directions automatically
    floorplan:
      - floor: 0               # ground floor
        zones: [climate.hallway, climate.garage]
      - floor: 1               # first floor
        zones: [climate.living_room, climate.kitchen]
        open: [climate.living_room, climate.kitchen]  # no walls between these
      - floor: 2               # second floor
        zones: [climate.bedroom, climate.office, climate.bathroom]

    stairwell_zones: [climate.hallway, climate.living_room, climate.landing]

    # Optional: override default seed coefficients
    seed_coefficients:
      same_floor: 0.15         # default
      up: 0.40                 # default (heat rises)
      down: 0.10               # default (conduction only)
      open: 0.60               # default (open floor plan)
      stairwell_up: 0.45       # default (chimney effect)
```

**Auto-inferred coupling seeds** (from floorplan):
- Same floor → `same_floor` seed
- Floor N → Floor N+1 → `up` seed
- Floor N → Floor N-1 → `down` seed
- Listed in `open` together → `open` seed
- Listed in `stairwell_zones` → `stairwell_up` upward, `down` downward

## Validation & Rollback

When compensation is applied for a zone pair:
1. Track overshoot for 5 cycles with compensation enabled
2. Compare to baseline overshoot (before compensation)
3. If overshoot increased >30%, reduce coefficient by 50%
4. Log warning for user visibility

```python
if validation_overshoot > baseline_overshoot * 1.3:
    coefficient *= 0.5
    log_warning(f"Coupling {source}→{target} reduced due to increased overshoot")
```

## Design Decisions
- **Remove ZoneLinker** - no fallback, clean break
- **Single coefficient per pair** - no door state tracking (v1)
- **Floorplan-based config** - define floors once, system infers all directions
- **Pure auto-discovery** - works without any config, floorplan just accelerates
- **Feedforward in PID** - keeps displayed setpoint honest
- **Graduated confidence** - smooth ramp-in from 0.3→0.5
- **Per-heating-type caps** - slower systems get lower max compensation

## Files to Modify

| File | Changes |
|------|---------|
| `adaptive/thermal_coupling.py` | **NEW** - Learner, dataclasses, floorplan parser |
| `coordinator.py` | **DELETE** ZoneLinker, add ThermalCouplingLearner, observation triggers |
| `adaptive/persistence.py` | Version 4, coupling storage methods |
| `pid_controller/__init__.py` | Add feedforward term support |
| `managers/control_output.py` | Calculate and pass coupling compensation to PID |
| `managers/heater_controller.py` | Remove ZoneLinker calls |
| `climate.py` | Remove linked_zones, add floorplan config, init coupling learner |
| `const.py` | Remove CONF_LINKED_ZONES, add coupling constants, seed defaults |
| `test_zone_linking.py` | **DELETE** entire file |
| `test_thermal_coupling.py` | **NEW** - Tests for coupling learner |

## Entity Attributes
- `coupling_coefficients`: {zone: {coefficient, confidence, observation_count}}
- `coupling_compensation`: Current °C offset being applied
- `coupling_compensation_power`: Current power % reduction
- `coupling_observations_pending`: Number of active observation windows
- `coupling_learner_state`: "learning" | "validating" | "stable"

## Tests
- `test_thermal_coupling.py` - Dataclasses, learner logic
- `test_coupling_integration.py` - End-to-end with coordinator

## Verification
1. Add thermal coupling to 2-zone test setup
2. Simulate Zone A heating → observe Zone B temp collection
3. Verify coefficient calculation after MIN_OBSERVATIONS
4. Verify compensation reduces Zone B's effective setpoint
5. Run full test suite: `pytest tests/ -v`
