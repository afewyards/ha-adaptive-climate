# Module map

## Core Modules

| Module | Purpose |
|--------|---------|
| `climate.py` | Main entity - orchestrates managers, presets, state |
| `climate_setup.py` | Platform schema, setup, PWM validation |
| `climate_init.py` | Manager initialization factory |
| `coordinator.py` | Zone registry, CentralController, ModeSync, thermal groups, shared outdoor temp EMA |
| `pid_controller/__init__.py` | PID with P, I, D, E (outdoor), F (feedforward) terms |
| `adaptive/learning.py` | Cycle analysis, rule-based PID adjustments |
| `adaptive/validation.py` | ValidationManager for auto-apply safety checks |
| `adaptive/confidence.py` | ConfidenceTracker for convergence confidence |
| `adaptive/cycle_weight.py` | Cycle classification and weight calculation |
| `adaptive/confidence_contribution.py` | Confidence contribution tracking with caps |
| `adaptive/learner_serialization.py` | Serialization/deserialization for AdaptiveLearner |
| `adaptive/undershoot_detector.py` | Real-time thermal debt + cycle-based undershoot detection |
| `adaptive/physics.py` | Thermal time constant, Ziegler-Nichols init |
| `adaptive/floor_physics.py` | Floor thermal properties, slab calculations |
| `sensor.py` | Performance/learning sensors |
| `helpers/dew_point.py` | Pure Magnus-Tetens dew point calculation |
| `sensors/water_temp.py` | Water supply temperature diagnostic sensor |
| `manifold_config.py` | Manifold / pump flow config validators (plain voluptuous) |
| `sensors/pump_flow.py` | Pump flow target diagnostic sensor |

## Managers (`managers/`)

| Manager | Purpose |
|---------|---------|
| `HeaterController` | PWM/valve actuation |
| `PWMController` | Duty accumulation, PWM switching |
| `CycleTrackerManager` | IDLE→HEATING→SETTLING state machine |
| `cycle_samples.py` | Per-cycle outdoor, heat-below and output-saturation samples |
| `CycleMetricsRecorder` | Cycle validation, metrics |
| `TemperatureManager` | Temperature tracking |
| `KeManager` | Outdoor compensation |
| `NightSetbackManager` | Night setback orchestration |
| `NightSetbackCalculator` | Preheat timing |
| `NightSetbackLearningGate` | Suppresses night setback until tuned |
| `pid_gains_manager.py` | Centralized PID gain mutations, auto-history |
| `WaterTempController` | Supply water temp targets, ramps, write policy, interlocks |
| `DewPointScanner` | Worst-case indoor dew point across zones and extra sensors |
| `water_temp_writer.py` | Pure write-policy helpers: rounding, clamping, direction safety |
| `water_temp_ramp.py` | Ramp math, measured-supply-temp seeding, mode-key normalization |
| `water_temp_blind_zones.py` | Blind-reading fallback for zones with unresolvable HVAC mode |
| `water_temp_interlocks.py` | Cooling interlock state machine (condensation/open-window park) |
| `PumpFlowController` | System pump flow setpoint from open, fully actuated zones |
| `pump_flow_math.py` | Pure pump-flow helpers: target, round-up, limits, write decision |
