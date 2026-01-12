[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

<a href="https://www.buymeacoffee.com/afewyards" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>

# Adaptive Thermostat
## Smart PID Thermostat with Adaptive Learning for Home Assistant

An advanced thermostat integration featuring PID control with automatic tuning, adaptive learning, multi-zone coordination, and energy optimization. Fork of [HASmartThermostat](https://github.com/ScratMan/HASmartThermostat) with extensive enhancements.

### Key Features

- **PID Control** - Accurate temperature control with proportional, integral, derivative, and outdoor compensation (Ke) terms
- **Adaptive Learning** - Automatically learns thermal characteristics and optimizes PID parameters
- **Physics-Based Initialization** - Initial PID values calculated from zone properties (no manual tuning required)
- **Multi-Zone Coordination** - Central heat source control, mode synchronization, and zone linking
- **Energy Optimization** - Night setback, solar gain prediction, contact sensors, heating curves
- **Analytics & Monitoring** - Performance sensors, health monitoring, energy tracking, weekly reports

## Installation

### Install using HACS (Recommended)
1. Go to HACS → Integrations → Three dots menu → Custom repositories
2. Add `https://github.com/afewyards/ha-adaptive-thermostat` as Integration
3. Click Install on the Adaptive Thermostat card
4. Restart Home Assistant

### Manual Installation
1. Copy the `custom_components/adaptive_thermostat` folder to your `<config>/custom_components/` directory
2. Restart Home Assistant

## Configuration

### Basic Example
```yaml
climate:
  - platform: adaptive_thermostat
    name: Living Room
    heater: switch.heating_living_room
    target_sensor: sensor.temp_living_room
    min_temp: 7
    max_temp: 28
    target_temp: 20
    keep_alive:
      seconds: 60
    kp: 50
    ki: 0.01
    kd: 2000
    pwm: 00:15:00
```

### Full Example with Adaptive Features
```yaml
climate:
  - platform: adaptive_thermostat
    name: Ground Floor
    heater: switch.heating_gf
    target_sensor: sensor.temp_gf
    outdoor_sensor: sensor.outdoor_temp
    min_temp: 7
    max_temp: 28
    target_temp: 20
    keep_alive:
      seconds: 60

    # PID settings (or let physics-based initialization handle it)
    kp: 50
    ki: 0.01
    kd: 2000
    ke: 0.6
    pwm: 00:15:00

    # Zone properties for physics-based tuning
    heating_type: floor_hydronic  # floor_hydronic, radiator, convector, forced_air
    area_m2: 28
    ceiling_height: 2.5
    window_area_m2: 4.0
    window_orientation: south  # north, east, south, west

    # Adaptive learning
    learning_enabled: true

    # Zone linking (for thermally connected zones)
    linked_zones:
      - climate.kitchen
      - climate.hallway
    link_delay_minutes: 10

    # Contact sensors (pause heating when windows/doors open)
    contact_sensors:
      - binary_sensor.gf_window
      - binary_sensor.gf_door
    contact_action: pause  # pause, frost_protection
    contact_delay: 120

    # Health monitoring
    health_alerts_enabled: true
    high_power_exception: false

    # Preset temperatures
    away_temp: 14
    eco_temp: 18
    boost_temp: 23
    comfort_temp: 21
    home_temp: 20
    sleep_temp: 18
```

### System Configuration
```yaml
adaptive_thermostat:
  # Central heat source control
  main_heater_switch: switch.boiler
  main_cooler_switch: switch.ac_compressor
  source_startup_delay: 30

  # Mode synchronization across zones
  sync_modes: true

  # Notifications
  notify_service: notify.mobile_app

  # Energy tracking (optional)
  energy_meter_entity: sensor.heating_energy
  energy_cost_entity: input_number.energy_price

  # Weather for solar gain prediction
  weather_entity: weather.home
```

## Heating Types

The `heating_type` parameter helps the system calculate appropriate PID values:

| Type | Description | Response | PWM Period |
|------|-------------|----------|------------|
| `floor_hydronic` | Underfloor water heating | Very slow | 15 min |
| `radiator` | Traditional radiators | Moderate | 10 min |
| `convector` | Convection heaters | Fast | 5 min |
| `forced_air` | Forced air / HVAC | Very fast | 3 min |

## Created Entities

### Per Zone
- `sensor.{zone}_duty_cycle` - Current heating duty cycle (%)
- `sensor.{zone}_power_m2` - Power consumption per m² (W/m²)
- `sensor.{zone}_cycle_time` - Average heating cycle time (min)
- `sensor.{zone}_overshoot` - Temperature overshoot (°C)
- `sensor.{zone}_settling_time` - Time to reach setpoint (min)
- `sensor.{zone}_oscillations` - Number of temperature oscillations
- `switch.{zone}_demand` - Zone heating demand indicator

### System
- `sensor.heating_system_health` - Overall system health (healthy/warning/critical)
- `sensor.heating_total_power` - Aggregated power across all zones (W)
- `sensor.heating_weekly_cost` - Weekly energy cost
- `number.adaptive_thermostat_learning_window` - Learning window in days (adjustable)

## Services

### Entity Services (target a specific thermostat)

**Set PID gains:** `adaptive_thermostat.set_pid_gain`
```yaml
service: adaptive_thermostat.set_pid_gain
data:
  kp: 50
  ki: 0.01
  kd: 2000
  ke: 0.6
target:
  entity_id: climate.living_room
```

**Set PID mode:** `adaptive_thermostat.set_pid_mode`
```yaml
service: adaptive_thermostat.set_pid_mode
data:
  mode: 'auto'  # or 'off' for hysteresis mode
target:
  entity_id: climate.living_room
```

**Set preset temperatures:** `adaptive_thermostat.set_preset_temp`
```yaml
service: adaptive_thermostat.set_preset_temp
data:
  away_temp: 14
  eco_temp: 18
  boost_temp: 23
target:
  entity_id: climate.living_room
```

**Clear integral:** `adaptive_thermostat.clear_integral`

### Domain Services (system-wide)

**Run adaptive learning:** `adaptive_thermostat.run_learning`
```yaml
service: adaptive_thermostat.run_learning
```

**Apply recommended PID:** `adaptive_thermostat.apply_recommended_pid`
```yaml
service: adaptive_thermostat.apply_recommended_pid
data:
  entity_id: climate.living_room
```

**Health check:** `adaptive_thermostat.health_check`
```yaml
service: adaptive_thermostat.health_check
```

**Weekly report:** `adaptive_thermostat.weekly_report`
```yaml
service: adaptive_thermostat.weekly_report
```

**Cost report:** `adaptive_thermostat.cost_report`
```yaml
service: adaptive_thermostat.cost_report
```

**Vacation mode:** `adaptive_thermostat.set_vacation_mode`
```yaml
service: adaptive_thermostat.set_vacation_mode
data:
  enabled: true
  target_temp: 12  # frost protection temperature
```

## Adaptive Learning

The thermostat automatically learns from heating cycles and adjusts PID parameters:

### What It Learns
- **Thermal rates** - Heating and cooling rates (°C/hour) for each zone
- **Overshoot patterns** - How much temperature exceeds setpoint
- **Settling behavior** - Time to stabilize and oscillation patterns

### PID Adjustment Rules
| Observation | Adjustment |
|-------------|------------|
| High overshoot (>0.5°C) | Reduce Kp by up to 15% |
| Slow response (>60 min) | Increase Kp by 10% |
| Undershoot (>0.3°C) | Increase Ki by up to 20% |
| Many oscillations (>3) | Reduce Kp, increase Kd |
| Slow settling (>90 min) | Increase Kd by 15% |

Learning requires a minimum of 3 heating cycles before making recommendations.

## Energy Optimization Features

### Night Setback
Automatically lowers temperature during sleeping hours with optional solar recovery in the morning.

### Solar Gain Prediction
Learns solar heating patterns per zone based on:
- Window orientation (south-facing gets more sun)
- Season (summer vs winter intensity)
- Cloud coverage forecast

### Contact Sensors
Pauses heating or switches to frost protection when windows/doors are open:
- `pause` - Stops heating completely
- `frost_protection` - Maintains minimum safe temperature (5°C)

### Heating Curves
Outdoor temperature compensation using the Ke parameter:
```
E = Ke × (target_temp - outdoor_temp)
```

Recommended Ke values by insulation:
- Excellent (A+++): 0.3
- Good (A/B): 0.5
- Average (C/D): 1.0
- Poor (E/F): 1.5

## Multi-Zone Coordination

### Central Heat Source Control
Aggregates demand from all zones to control the main boiler/heat pump:
- Startup delay allows valves to open before firing heat source
- Immediate off when no zone has demand

### Mode Synchronization
When one zone switches to HEAT or COOL mode, other zones follow (OFF mode remains independent).

### Zone Linking
For thermally connected zones (e.g., open floor plan):
- When one zone starts heating, linked zones delay their heating
- Prevents redundant heating of connected spaces
- Configurable delay (default 10 minutes)

## Parameters Reference

### Core Parameters
| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `name` | No | Adaptive Thermostat | Thermostat name |
| `heater` | Yes | - | Switch/valve entity to control |
| `cooler` | No | - | Cooling switch/valve entity |
| `target_sensor` | Yes | - | Temperature sensor entity |
| `outdoor_sensor` | No | - | Outdoor temperature sensor |
| `keep_alive` | Yes | - | PWM update interval |
| `pwm` | No | 00:15:00 | PWM period (0 for valves) |

### PID Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `kp` | 100 | Proportional gain |
| `ki` | 0 | Integral gain |
| `kd` | 0 | Derivative gain |
| `ke` | 0 | Outdoor compensation gain |

### Adaptive Learning Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `heating_type` | - | System type for physics-based init |
| `area_m2` | - | Zone floor area in m² |
| `ceiling_height` | 2.5 | Ceiling height in meters |
| `window_area_m2` | - | Total window area in m² |
| `window_orientation` | - | Primary window direction |
| `learning_enabled` | true | Enable adaptive learning |

### Zone Coordination Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `linked_zones` | - | List of thermally connected zones |
| `link_delay_minutes` | 10 | Delay before linked zone heats |
| `contact_sensors` | - | Window/door sensors |
| `contact_action` | pause | Action when contact open |
| `contact_delay` | 120 | Seconds before taking action |

### Health Monitoring Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `health_alerts_enabled` | true | Enable health monitoring |
| `high_power_exception` | false | Exclude from high power warnings |

## Troubleshooting

### PID Tuning Tips
- Start with low Kp to avoid oscillations
- Increase Kp for faster response if stable
- Use Kd to dampen overshoot
- Keep Ki small for heating systems (slow thermal mass)
- Let adaptive learning fine-tune over time

### Common Issues
- **Slow response**: Increase Kp or check sensor update rate
- **Oscillations**: Reduce Kp, increase Kd
- **Never reaches setpoint**: Increase Ki
- **Overshoots then settles**: Normal for PID, reduce Kp slightly

### Health Warnings
- **Critical cycle time (<10 min)**: System cycling too fast, check PID tuning
- **Warning cycle time (<15 min)**: Consider increasing PWM period
- **High power (>20 W/m²)**: May indicate heat loss issues

## Credits

This integration is a fork of [HASmartThermostat](https://github.com/ScratMan/HASmartThermostat) by ScratMan, with extensive enhancements for adaptive learning and multi-zone coordination.

The PID controller is based on the work from:
- [Smart Thermostat PID](https://github.com/aendle/custom_components)
- [pid-autotune](https://github.com/hirschmann/pid-autotune)
