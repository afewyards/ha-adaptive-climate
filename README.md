[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
[![Tests](https://github.com/afewyards/ha-adaptive-thermostat/workflows/Tests/badge.svg)](https://github.com/afewyards/ha-adaptive-thermostat/actions/workflows/tests.yml)

<a href="https://www.buymeacoffee.com/afewyards" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>

```
 █████╗ ██████╗  █████╗ ██████╗ ████████╗██╗██╗   ██╗███████╗
██╔══██╗██╔══██╗██╔══██╗██╔══██╗╚══██╔══╝██║██║   ██║██╔════╝
███████║██║  ██║███████║██████╔╝   ██║   ██║██║   ██║█████╗
██╔══██║██║  ██║██╔══██║██╔═══╝    ██║   ██║╚██╗ ██╔╝██╔══╝
██║  ██║██████╔╝██║  ██║██║        ██║   ██║ ╚████╔╝ ███████╗
╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝        ╚═╝   ╚═╝  ╚═══╝  ╚══════╝
████████╗██╗  ██╗███████╗██████╗ ███╗   ███╗ ██████╗ ███████╗████████╗ █████╗ ████████╗
╚══██╔══╝██║  ██║██╔════╝██╔══██╗████╗ ████║██╔═══██╗██╔════╝╚══██╔══╝██╔══██╗╚══██╔══╝
   ██║   ███████║█████╗  ██████╔╝██╔████╔██║██║   ██║███████╗   ██║   ███████║   ██║
   ██║   ██╔══██║██╔══╝  ██╔══██╗██║╚██╔╝██║██║   ██║╚════██║   ██║   ██╔══██║   ██║
   ██║   ██║  ██║███████╗██║  ██║██║ ╚═╝ ██║╚██████╔╝███████║   ██║   ██║  ██║   ██║
   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝ ╚═════╝ ╚══════╝   ╚═╝   ╚═╝  ╚═╝   ╚═╝
```

# Adaptive Thermostat
## Smart PID Thermostat with Adaptive Learning for Home Assistant

An advanced thermostat integration featuring PID control with automatic tuning, adaptive learning, multi-zone coordination, and energy optimization. Fork of [HASmartThermostat](https://github.com/ScratMan/HASmartThermostat) with extensive enhancements.

> **Note: This project is currently in active testing and development.**
> While the core functionality is operational, adaptive learning features and multi-zone coordination are being refined based on real-world usage. Please report any issues or unexpected behavior via [GitHub Issues](https://github.com/afewyards/ha-adaptive-thermostat/issues).

### Key Features

- **PID Control** - Four-term PID controller (P+I+D+E) with proportional-on-measurement for smooth operation
- **Adaptive Learning** - Automatically learns thermal characteristics and optimizes PID parameters from real heating cycles, with automatic application and validation
- **Physics-Based Initialization** - Initial PID values calculated from zone properties using empirical HVAC data
- **Multi-Zone Coordination** - Central heat source control, mode synchronization, and thermal coupling for connected spaces
- **Energy Optimization** - Night setback with dynamic sunrise timing, solar gain prediction, contact sensors, outdoor compensation
- **Actuator Wear Tracking** - Cycle counting with predictive maintenance alerts
- **Comprehensive Monitoring** - Performance sensors, health warnings, energy tracking, and automated reports

## 📚 Full Documentation

**[Visit the Wiki](https://github.com/afewyards/ha-adaptive-thermostat/wiki)** for comprehensive documentation including:

- [Installation & Setup](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Installation-&-Setup) - Detailed installation guide and first-time configuration
- [Configuration Reference](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Configuration-Reference) - Complete parameter reference with 60+ parameters
- [PID Control](https://github.com/afewyards/ha-adaptive-thermostat/wiki/PID-Control) - How PID works, PWM vs valve control
- [Adaptive Learning](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Adaptive-Learning) - Cycle tracking, learning rules, Ke-First process
- [Multi-Zone Coordination](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Multi-Zone-Coordination) - Architecture and configuration
- [Energy Optimization](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Energy-Optimization) - Night setback, solar recovery, outdoor compensation
- [Troubleshooting](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Troubleshooting) - Diagnostic checklist and common issues

## Installation

### Install using HACS (Recommended)
1. Go to HACS → Integrations → Three dots menu → Custom repositories
2. Add `https://github.com/afewyards/ha-adaptive-thermostat` as Integration
3. Click Install on the Adaptive Thermostat card
4. Restart Home Assistant

### Manual Installation
1. Copy the `custom_components/adaptive_thermostat` folder to your `<config>/custom_components/` directory
2. Restart Home Assistant

## Quick Start

### Basic Configuration
```yaml
climate:
  - platform: adaptive_thermostat
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
adaptive_thermostat:
  house_energy_rating: A+++
  main_heater_switch: switch.boiler
  outdoor_sensor: sensor.outdoor_temp
  sync_modes: true

  # Thermal coupling learns heat transfer between zones
  thermal_coupling:
    floorplan:
      - floor: 0
        zones:
          - climate.ground_floor
          - climate.kitchen
        open:
          - climate.ground_floor
          - climate.kitchen

climate:
  - platform: adaptive_thermostat
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

  - platform: adaptive_thermostat
    name: Kitchen
    heater: switch.heating_kitchen
    target_sensor: sensor.temp_kitchen
    heating_type: radiator
    area_m2: 15
```

### Night Setback with Solar Recovery
```yaml
climate:
  - platform: adaptive_thermostat
    name: Bedroom
    heater: switch.heating_bedroom
    target_sensor: sensor.temp_bedroom
    heating_type: radiator
    area_m2: 16
    window_orientation: south

    night_setback:
      start: "22:00"
      delta: 2.0
      recovery_deadline: "08:00"
      solar_recovery: true
```

When `end` is omitted, wake time is calculated from sunrise, window orientation, and weather forecast. [Learn more →](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Energy-Optimization#night-setback)

### Cooling/AC Configuration
```yaml
climate:
  - platform: adaptive_thermostat
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
  - platform: adaptive_thermostat
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
  - platform: adaptive_thermostat
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

Specifying floor construction improves PID initialization by calculating thermal mass from actual materials. Heavier floors get more conservative tuning. [Learn more →](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Configuration-Reference#floor_construction)

### Actuator Wear Tracking
```yaml
climate:
  - platform: adaptive_thermostat
    name: Living Room
    heater: switch.heating_living
    target_sensor: sensor.temp_living
    heating_type: radiator
    area_m2: 20
    heater_rated_cycles: 100000  # Typical contactor lifetime
```

Tracks on→off cycles and fires maintenance alerts at 80% and 90% wear. [Learn more →](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Actuator-Wear-Tracking)

## Created Entities

### Per Zone
- `sensor.{zone}_duty_cycle` - Current heating duty cycle (%)
- `sensor.{zone}_power_m2` - Power consumption per m² (W/m²)
- `sensor.{zone}_cycle_time` - Average heating cycle time (min)
- `sensor.{zone}_overshoot` - Temperature overshoot (°C)
- `sensor.{zone}_settling_time` - Time to reach setpoint (min)
- `sensor.{zone}_oscillations` - Number of temperature oscillations
- `sensor.{zone}_heat_output` - Heat output (W) - requires supply/return temp sensors
- `sensor.{zone}_heater_wear` - Heater wear % (hidden, optional)
- `sensor.{zone}_cooler_wear` - Cooler wear % (hidden, optional)

### System
- `number.adaptive_thermostat_learning_window` - Learning window in days (adjustable)

## Services

### Entity Services
- `adaptive_thermostat.reset_pid_to_physics` - Reset PID to physics-based defaults
- `adaptive_thermostat.apply_adaptive_pid` - Apply learned PID adjustments (manual)
- `adaptive_thermostat.rollback_pid` - Revert to previous PID configuration
- `adaptive_thermostat.clear_learning` - Clear all learning data and reset to physics defaults
- `adaptive_thermostat.apply_adaptive_ke` - Apply outdoor compensation tuning

### Domain Services
- `adaptive_thermostat.run_learning` - Trigger learning analysis for all zones
- `adaptive_thermostat.health_check` - System health monitoring
- `adaptive_thermostat.weekly_report` - Generate performance report
- `adaptive_thermostat.cost_report` - Energy cost analysis (daily/weekly/monthly)
- `adaptive_thermostat.set_vacation_mode` - Enable frost protection mode
- `adaptive_thermostat.energy_stats` - Current energy statistics
- `adaptive_thermostat.pid_recommendations` - Preview recommended PID values

[Full service documentation →](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Services)

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
  - platform: adaptive_thermostat
    name: Living Room
    heater: switch.heating_living
    target_sensor: sensor.temp_living
    heating_type: radiator
    area_m2: 20
    auto_apply_pid: false  # Disable automatic application
```

With `auto_apply_pid: false`, use `adaptive_thermostat.apply_adaptive_pid` service to manually apply learned values.

### Entity Attributes
Monitor auto-apply status via entity attributes:
- `auto_apply_pid_enabled` - Whether auto-apply is enabled
- `auto_apply_count` - Number of times PID has been auto-applied
- `validation_mode` - Currently validating new PID values
- `pid_history` - Last 3 PID configurations (timestamp, values, reason)

## Troubleshooting

### Let Adaptive Learning Work
The thermostat automatically learns and adjusts. Give it time:
- Initial values come from physics-based calculations
- PID adjustments are auto-applied when confidence reaches heating-type threshold
- Manual application available via `adaptive_thermostat.apply_adaptive_pid` service

### Common Issues

**Slow response**
- Verify `heating_type` matches your actual system
- Check `area_m2` is accurate
- Wait for adaptive learning (3-7 days) or increase Kp manually

**Oscillations**
- Usually resolves after adaptive learning detects the pattern
- May indicate `heating_type` mismatch or very short PWM period

**Never reaches setpoint**
- Ensure `area_m2` is correct
- Check for high heat loss (poor insulation, open windows)
- Verify heater has sufficient capacity

**Temperature overshoots then settles**
- Normal behavior initially
- Adaptive learning will detect overshoot and reduce Kp automatically

### Health Warnings

Monitor sensor values for performance issues:
- **Critical cycle time (<10 min)**: System cycling too fast, check PID tuning
- **Warning cycle time (<15 min)**: Consider increasing PWM period
- **High power (>20 W/m²)**: May indicate heat loss or inefficient operation

[Full troubleshooting guide →](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Troubleshooting)

## Credits

This integration is a fork of [HASmartThermostat](https://github.com/ScratMan/HASmartThermostat) by ScratMan, with extensive enhancements for adaptive learning and multi-zone coordination.

The PID controller is based on the work from:
- [Smart Thermostat PID](https://github.com/aendle/custom_components)
- [pid-autotune](https://github.com/hirschmann/pid-autotune)
