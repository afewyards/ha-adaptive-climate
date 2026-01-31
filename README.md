[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
[![Tests](https://github.com/afewyards/ha-adaptive-climate/workflows/Tests/badge.svg)](https://github.com/afewyards/ha-adaptive-climate/actions/workflows/tests.yml)

<a href="https://www.buymeacoffee.com/afewyards" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>

```
 █████╗ ██████╗  █████╗ ██████╗ ████████╗██╗██╗   ██╗███████╗
██╔══██╗██╔══██╗██╔══██╗██╔══██╗╚══██╔══╝██║██║   ██║██╔════╝
███████║██║  ██║███████║██████╔╝   ██║   ██║██║   ██║█████╗
██╔══██║██║  ██║██╔══██║██╔═══╝    ██║   ██║╚██╗ ██╔╝██╔══╝
██║  ██║██████╔╝██║  ██║██║        ██║   ██║ ╚████╔╝ ███████╗
╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝        ╚═╝   ╚═╝  ╚═══╝  ╚══════╝
 ██████╗██╗     ██╗███╗   ███╗ █████╗ ████████╗███████╗
██╔════╝██║     ██║████╗ ████║██╔══██╗╚══██╔══╝██╔════╝
██║     ██║     ██║██╔████╔██║███████║   ██║   █████╗
██║     ██║     ██║██║╚██╔╝██║██╔══██║   ██║   ██╔══╝
╚██████╗███████╗██║██║ ╚═╝ ██║██║  ██║   ██║   ███████╗
 ╚═════╝╚══════╝╚═╝╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚══════╝
```

# Adaptive Climate
## Smart PID Climate Control with Adaptive Learning for Home Assistant

An advanced climate integration featuring PID control with automatic tuning, adaptive learning, multi-zone coordination, and energy optimization. Fork of [HASmartThermostat](https://github.com/ScratMan/HASmartThermostat) with extensive enhancements.

> **Note: This project is currently in active testing and development.**
> While the core functionality is operational, adaptive learning features and multi-zone coordination are being refined based on real-world usage. Please report any issues or unexpected behavior via [GitHub Issues](https://github.com/afewyards/ha-adaptive-climate/issues).

### Key Features

- **5-Term PID Control** - Advanced PID controller (P+I+D+E+F) with proportional-on-measurement, outdoor compensation, and thermal coupling feedforward
- **Adaptive Learning** - Automatically learns thermal characteristics, optimizes PID parameters, and detects chronic approach failures
- **Physics-Based Initialization** - Initial PID values calculated from zone properties, floor construction, and empirical HVAC data
- **Multi-Zone Coordination** - Central heat source control, mode synchronization, thermal groups, and automatic HVAC mode switching
- **Disturbance Handling** - Contact sensors, algorithmic open window detection, humidity-based steam detection (bathrooms)
- **Energy Optimization** - Night setback with predictive pre-heating, outdoor compensation (Ke), setpoint feedforward
- **Actuator Wear Tracking** - Cycle counting with predictive maintenance alerts
- **Comprehensive Monitoring** - Performance sensors, comfort scores, energy tracking, and automated reports

## 📚 Full Documentation

**[Visit the Wiki](https://github.com/afewyards/ha-adaptive-climate/wiki)** for comprehensive documentation including:

- [Installation & Setup](https://github.com/afewyards/ha-adaptive-climate/wiki/Installation-&-Setup) - Detailed installation guide and first-time configuration
- [Configuration Reference](https://github.com/afewyards/ha-adaptive-climate/wiki/Configuration-Reference) - Complete parameter reference
- [PID Control](https://github.com/afewyards/ha-adaptive-climate/wiki/PID-Control) - 5-term PID (P/I/D/E/F), PWM vs valve control
- [Adaptive Learning](https://github.com/afewyards/ha-adaptive-climate/wiki/Adaptive-Learning) - Cycle tracking, learning rules, chronic approach detection
- [Multi-Zone Coordination](https://github.com/afewyards/ha-adaptive-climate/wiki/Multi-Zone-Coordination) - Central controller, thermal groups, auto mode switching
- [Energy Optimization](https://github.com/afewyards/ha-adaptive-climate/wiki/Energy-Optimization) - Night setback, predictive preheat, outdoor compensation
- [Humidity Detection](https://github.com/afewyards/ha-adaptive-climate/wiki/Humidity-Detection) - Bathroom steam detection
- [Troubleshooting](https://github.com/afewyards/ha-adaptive-climate/wiki/Troubleshooting) - Diagnostic checklist and common issues

## Installation

### Install using HACS (Recommended)
1. Go to HACS → Integrations → Three dots menu → Custom repositories
2. Add `https://github.com/afewyards/ha-adaptive-climate` as Integration
3. Click Install on the Adaptive Climate card
4. Restart Home Assistant

### Manual Installation
1. Copy the `custom_components/adaptive_climate` folder to your `<config>/custom_components/` directory
2. Restart Home Assistant

## Migration from adaptive_thermostat

If you're upgrading from the `adaptive_thermostat` integration, see the [CHANGELOG](CHANGELOG.md) for migration instructions. The integration has been renamed to `adaptive_climate` to better reflect support for both heating and cooling.

## Companion Lovelace Card

For the best experience, use the [Adaptive Climate Card](https://github.com/afewyards/ha-adaptive-climate-card) — a custom Lovelace card designed specifically for this integration.

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/afewyards/ha-adaptive-climate-card)

## Quick Start

### Basic Configuration
```yaml
climate:
  - platform: adaptive_climate
    name: Living Room
    heater: switch.heating_living_room
    target_sensor: sensor.temp_living_room
    heating_type: radiator
    area_m2: 20
    min_temp: 7
    max_temp: 28
    target_temp: 20
```

PID values are automatically calculated from `heating_type` and `area_m2`, then refined through adaptive learning. No manual tuning required.

## Configuration Examples

### Multi-Zone with Central Heat Source
```yaml
adaptive_climate:
  house_energy_rating: A+++
  main_heater_switch: switch.boiler
  outdoor_sensor: sensor.outdoor_temp
  weather_entity: weather.home
  sync_modes: true

  # Automatic HVAC mode switching based on outdoor temp
  auto_mode_switching:
    enabled: true
    threshold: 2.0
    forecast_hours: 6

  # Thermal groups define how heat transfers between zones
  thermal_groups:
    - name: "Open Plan Ground Floor"
      zones: [climate.ground_floor, climate.kitchen]
      type: open_plan
      leader: climate.ground_floor

    - name: "Upstairs from Stairwell"
      zones: [climate.upstairs_landing, climate.master_bedroom]
      receives_from: "Open Plan Ground Floor"
      transfer_factor: 0.2
      delay_minutes: 15

climate:
  - platform: adaptive_climate
    name: Ground Floor
    heater: switch.heating_gf
    target_sensor: sensor.temp_gf
    heating_type: floor_hydronic
    area_m2: 28
    window_orientation: south
    floor_construction:
      pipe_spacing_mm: 150
      layers:
        - type: top_floor
          material: ceramic_tile
          thickness_mm: 10
        - type: screed
          material: cement
          thickness_mm: 50

  - platform: adaptive_climate
    name: Kitchen
    heater: switch.heating_kitchen
    target_sensor: sensor.temp_kitchen
    heating_type: radiator
    area_m2: 15
```

### Night Setback with Predictive Pre-Heating
```yaml
climate:
  - platform: adaptive_climate
    name: Bedroom
    heater: switch.heating_bedroom
    target_sensor: sensor.temp_bedroom
    heating_type: radiator
    area_m2: 16
    window_orientation: south

    night_setback:
      start: "22:00"
      delta: 2.0
      recovery_deadline: "07:00"
      preheat_enabled: true  # Learn heating rate and start early
```

With `preheat_enabled`, the system learns your heating rate and starts recovery early to reach target AT the deadline. [Learn more →](https://github.com/afewyards/ha-adaptive-climate/wiki/Energy-Optimization#predictive-pre-heating)

### Bathroom with Humidity Detection
```yaml
climate:
  - platform: adaptive_climate
    name: Bathroom
    heater: switch.heating_bathroom
    target_sensor: sensor.temp_bathroom
    humidity_sensor: sensor.humidity_bathroom
    heating_type: radiator
    area_m2: 6
    humidity_spike_threshold: 15  # Pause heating on 15% spike (shower)
```

Detects shower steam and pauses heating to prevent wasted energy. Resumes after humidity stabilizes. [Learn more →](https://github.com/afewyards/ha-adaptive-climate/wiki/Humidity-Detection)

### Cooling/AC Configuration
```yaml
climate:
  - platform: adaptive_climate
    name: Living Room AC
    heater: switch.heating_living
    cooler: switch.ac_living
    target_sensor: sensor.temp_living
    heating_type: forced_air
    area_m2: 25
    hot_tolerance: 0.5
    cold_tolerance: 0.5
```

### Valve Control (0-100%)
```yaml
climate:
  - platform: adaptive_climate
    name: Fan Coil Unit
    demand_switch: switch.fcunit_valve
    target_sensor: sensor.temp_fcunit
    pwm: 0  # Direct valve positioning
    heating_type: forced_air
    area_m2: 20
```

### Floor Construction (Underfloor Heating)
```yaml
climate:
  - platform: adaptive_climate
    name: Living Room UFH
    heater: switch.ufh_living
    target_sensor: sensor.temp_living
    heating_type: floor_hydronic
    area_m2: 32
    floor_construction:
      pipe_spacing_mm: 100
      layers:
        - type: top_floor
          material: hardwood
          thickness_mm: 16
        - type: screed
          material: anhydrite
          thickness_mm: 70
```

Specifying floor construction improves PID initialization by calculating thermal mass from actual materials. Heavier floors get more conservative tuning. [Learn more →](https://github.com/afewyards/ha-adaptive-climate/wiki/Configuration-Reference#floor_construction)

### Actuator Wear Tracking
```yaml
climate:
  - platform: adaptive_climate
    name: Living Room
    heater: switch.heating_living
    target_sensor: sensor.temp_living
    heating_type: radiator
    area_m2: 20
    heater_rated_cycles: 100000  # Typical contactor lifetime
```

Tracks on→off cycles and fires maintenance alerts at 80% and 90% wear. [Learn more →](https://github.com/afewyards/ha-adaptive-climate/wiki/Actuator-Wear-Tracking)

## Created Entities

### Per Zone
- `sensor.{zone}_duty_cycle` - Current heating duty cycle (%)
- `sensor.{zone}_power_m2` - Power consumption per m² (W/m²)
- `sensor.{zone}_cycle_time` - Average heating cycle time (min)
- `sensor.{zone}_overshoot` - Temperature overshoot (°C)
- `sensor.{zone}_settling_time` - Time to reach setpoint (min)
- `sensor.{zone}_oscillations` - Number of temperature oscillations
- `sensor.{zone}_heat_output` - Heat output (W) - requires supply/return temp sensors
- `sensor.{zone}_time_at_target` - Time at target temperature (%)
- `sensor.{zone}_comfort_score` - Composite comfort score (0-100)
- `sensor.{zone}_heater_wear` - Heater wear % (hidden, optional)
- `sensor.{zone}_cooler_wear` - Cooler wear % (hidden, optional)

### System
- `number.adaptive_climate_learning_window` - Learning window in days (adjustable)
- `sensor.adaptive_climate_total_power` - Total power across all zones
- `sensor.adaptive_climate_weekly_cost` - Weekly energy cost

## Services

### Entity Services (require `debug: true`)
- `adaptive_climate.reset_pid_to_physics` - Reset PID to physics-based defaults
- `adaptive_climate.apply_adaptive_pid` - Apply learned PID adjustments manually
- `adaptive_climate.apply_adaptive_ke` - Apply outdoor compensation tuning
- `adaptive_climate.rollback_pid` - Revert to previous PID configuration
- `adaptive_climate.clear_learning` - Clear all learning data and reset

### Entity Services (always available)
- `adaptive_climate.delete_pid_history` - Clear PID adjustment history
- `adaptive_climate.restore_pid_history` - Restore from saved history

### Domain Services
- `adaptive_climate.weekly_report` - Generate performance report
- `adaptive_climate.cost_report` - Energy cost analysis (daily/weekly/monthly)
- `adaptive_climate.set_vacation_mode` - Enable frost protection mode
- `adaptive_climate.run_learning` - Trigger learning analysis (debug only)
- `adaptive_climate.pid_recommendations` - Preview recommended PID values (debug only)

[Full service documentation →](https://github.com/afewyards/ha-adaptive-climate/wiki/Services)

## Automatic PID Tuning

By default, PID parameters are automatically applied when the system reaches sufficient confidence. This happens transparently with built-in safety measures.

### How It Works
1. **Learning** - System collects heating cycle metrics (overshoot, settling time, oscillations)
2. **Confidence** - Convergence confidence builds as patterns stabilize
3. **Auto-Apply** - When confidence reaches threshold, new PID values are applied automatically
4. **Validation** - 5-cycle validation window monitors performance
5. **Rollback** - If performance degrades >30%, automatically reverts to previous values

### Safety Limits
- **5 auto-applies per season** (90 days) - prevents runaway tuning
- **20 lifetime limit** - requires manual review after extensive tuning
- **50% max drift** - PID values can't drift too far from physics baseline
- **Seasonal blocking** - 7-day pause after large outdoor temperature shifts

### Heating-Type Thresholds

| Type | First Apply | Subsequent | Min Cycles | Cooldown |
|------|-------------|------------|------------|----------|
| `floor_hydronic` | 80% | 90% | 8 | 96h |
| `radiator` | 70% | 85% | 7 | 72h |
| `convector` | 60% | 80% | 6 | 48h |
| `forced_air` | 60% | 80% | 6 | 36h |

Slow systems (high thermal mass) require higher confidence because mistakes are costly to recover from.

### Disabling Auto-Apply
```yaml
climate:
  - platform: adaptive_climate
    name: Living Room
    heater: switch.heating_living
    target_sensor: sensor.temp_living
    heating_type: radiator
    area_m2: 20
    auto_apply_pid: false  # Disable automatic application
```

With `auto_apply_pid: false`, use `adaptive_climate.apply_adaptive_pid` service to manually apply learned values.

### Entity Attributes
Monitor status via entity attributes:
- `learning_status` - Current state: `collecting`, `stable`, or `optimized`
- `pid_history` - Last 10 PID changes with timestamp, reason, and actor
- `status` - Operational status with `state` and `conditions` (contact_open, humidity_spike, etc.)

Additional attributes available with `debug: true` in domain config:
- `convergence_confidence_pct` - Learning confidence (0-100%)
- `cycles_collected` - Number of complete heating cycles analyzed
- `duty_accumulator_pct` - PWM accumulation as % of threshold

## Troubleshooting

### Let Adaptive Learning Work
The thermostat automatically learns and adjusts. Give it time:
- Initial values come from physics-based calculations
- PID adjustments are auto-applied when confidence reaches heating-type threshold
- Manual application available via `adaptive_climate.apply_adaptive_pid` service

### Common Issues

**Slow response to setpoint changes**
- Setpoint feedforward (`setpoint_boost: true`) accelerates response
- Verify `heating_type` matches your actual system
- Wait for adaptive learning (3-7 days)

**Oscillations**
- Usually resolves after adaptive learning detects the pattern
- May indicate `heating_type` mismatch or very short PWM period

**Never reaches setpoint**
- Chronic approach detector automatically boosts Ki after repeated failures
- Ensure `area_m2` is correct
- Check for high heat loss (poor insulation, open windows)
- Verify heater has sufficient capacity

**Temperature overshoots then settles**
- Normal behavior initially
- Adaptive learning will detect overshoot and reduce Kp automatically

**Heating pauses unexpectedly**
- Check `status.conditions` attribute for: `contact_open`, `humidity_spike`, `open_window`
- Check `status.resume_at` for when heating will resume

### Learning Status

Monitor `learning_status` attribute:
- `collecting` - Gathering data (< 6 cycles or low confidence)
- `stable` - System converged, PID optimized
- `optimized` - Very high confidence (95%+)

[Full troubleshooting guide →](https://github.com/afewyards/ha-adaptive-climate/wiki/Troubleshooting)

## Credits

This integration is a fork of [HASmartThermostat](https://github.com/ScratMan/HASmartThermostat) by ScratMan, with extensive enhancements for adaptive learning and multi-zone coordination.

The PID controller is based on the work from:
- [Smart Thermostat PID](https://github.com/aendle/custom_components)
- [pid-autotune](https://github.com/hirschmann/pid-autotune)
