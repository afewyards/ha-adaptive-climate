[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

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
    heating_type: radiator
    area_m2: 20
    min_temp: 7
    max_temp: 28
    target_temp: 20
    keep_alive:
      seconds: 60
```

PID values are automatically calculated from `heating_type` and `area_m2`, then refined through adaptive learning. No manual tuning required.

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

    # Zone properties for physics-based PID initialization
    heating_type: floor_hydronic  # floor_hydronic, radiator, convector, forced_air
    area_m2: 28
    ceiling_height: 2.5
    window_area_m2: 4.0
    window_orientation: south  # north, east, south, west, roof

    # Adaptive learning
    learning_enabled: true

    # Zone linking (for thermally connected zones)
    linked_zones:
      - climate.kitchen
      - climate.hallway
    link_delay_minutes: 20

    # Contact sensors (pause heating when windows/doors open)
    contact_sensors:
      - binary_sensor.gf_window
      - binary_sensor.gf_door
    contact_action: pause  # pause, frost_protection, none
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
    activity_temp: 20
```

### Cooling/AC Example
```yaml
climate:
  - platform: adaptive_thermostat
    name: Living Room AC
    heater: switch.heating_living
    cooler: switch.ac_living
    ac_mode: true
    target_sensor: sensor.temp_living
    min_temp: 16
    max_temp: 30
    target_temp: 22
    hot_tolerance: 0.5
    cold_tolerance: 0.5
    keep_alive:
      seconds: 60
```

### Valve Control Example
For systems with a single valve that controls flow regardless of heating or cooling mode (e.g., fan coil units, zone valves on a shared hydronic loop):

```yaml
climate:
  - platform: adaptive_thermostat
    name: Fan Coil Unit
    demand_switch: switch.fcunit_valve
    target_sensor: sensor.temp_fcunit
    ac_mode: true
    pwm: 0  # Direct valve control (0-100%)
    min_temp: 16
    max_temp: 28
    target_temp: 21
    keep_alive:
      seconds: 60
```

Unlike `heater` (active only in heat mode) or `cooler` (active only in cool mode), `demand_switch` is controlled in both modes—useful when the same valve regulates flow from a shared hot/cold source.

### Night Setback Example
```yaml
climate:
  - platform: adaptive_thermostat
    name: Bedroom
    heater: switch.heating_bedroom
    target_sensor: sensor.temp_bedroom
    window_orientation: south  # Required for dynamic end time
    keep_alive:
      seconds: 60

    # Night setback configuration
    night_setback:
      start: "22:00"          # Or "sunset+30" for 30 min after sunset
      delta: 2.0              # Reduce by 2°C at night
      # end: "06:30"          # Optional - if omitted, calculated dynamically
      solar_recovery: true    # Delay morning heating to let sun warm zone
      recovery_deadline: "08:00"  # Hard deadline for recovery
```

When `end` is omitted, the end time is calculated dynamically based on:
- **Sunrise** + 60 minutes base offset
- **Window orientation**: south +30min, east +15min, west -30min, north -45min
- **Weather**: cloudy -30min, clear +15min

This allows south-facing rooms to benefit from solar gain while north-facing rooms start heating earlier.

### System Configuration
```yaml
adaptive_thermostat:
  # House properties (for physics-based initialization)
  house_energy_rating: A+++  # A+++ to G

  # Learning
  learning_window_days: 7  # Adjustable via number entity

  # Weather for solar gain prediction
  weather_entity: weather.home

  # Heat output sensors (optional - for detailed analytics)
  supply_temp_sensor: sensor.heating_supply
  return_temp_sensor: sensor.heating_return
  flow_rate_sensor: sensor.heating_flow
  volume_meter_entity: sensor.heating_volume_m3
  fallback_flow_rate: 0.5  # L/min fallback when meter unavailable

  # Energy tracking (optional)
  energy_meter_entity: sensor.heating_energy
  energy_cost_entity: input_number.energy_price

  # Central heat source control
  main_heater_switch: switch.boiler
  main_cooler_switch: switch.ac_compressor
  source_startup_delay: 30  # Seconds delay before firing heat source

  # Mode synchronization across zones
  sync_modes: true  # Sync HEAT/COOL mode changes across zones

  # Notifications for health alerts and reports
  notify_service: notify.mobile_app
```

## Heating Types

The `heating_type` parameter determines PID values using empirical base values calibrated from real-world HVAC systems:

| Type | Description | Kp | Ki | Kd | PWM Period |
|------|-------------|-----|-------|-----|------------|
| `floor_hydronic` | Underfloor water heating | 0.3 | 0.012 | 7.0 | 15 min |
| `radiator` | Traditional radiators | 0.5 | 0.02 | 5.0 | 10 min |
| `convector` | Convection heaters | 0.8 | 0.04 | 3.0 | 5 min |
| `forced_air` | Forced air / HVAC | 1.2 | 0.08 | 2.0 | 3 min |

Values are adjusted ±30% based on thermal time constant (zone volume and window area). Floor heating needs very low Ki (to avoid integral wind-up) and high Kd (to dampen slow oscillations).

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

**Reset PID to physics defaults:** `adaptive_thermostat.reset_pid_to_physics`
```yaml
service: adaptive_thermostat.reset_pid_to_physics
target:
  entity_id: climate.living_room
```
Recalculates PID values from the zone's physical properties (`area_m2`, `ceiling_height`, `heating_type`) using empirical values calibrated for real-world HVAC systems. Clears the integral term to avoid wind-up.

**Apply adaptive PID:** `adaptive_thermostat.apply_adaptive_pid`
```yaml
service: adaptive_thermostat.apply_adaptive_pid
target:
  entity_id: climate.living_room
```
Calculates and applies PID values based on learned performance metrics (overshoot, undershoot, settling time, oscillations). Requires at least 3 analyzed heating cycles.

### Domain Services (system-wide)

**Run adaptive learning:** `adaptive_thermostat.run_learning`
```yaml
service: adaptive_thermostat.run_learning
```
Triggers adaptive learning analysis for all zones.

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
data:
  period: weekly  # daily, weekly, or monthly
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
Automatically lowers temperature during sleeping hours. Features include:
- **Sunset-relative start**: Use "sunset+30" to start 30 minutes after sunset
- **Dynamic end time**: When `end` is omitted, calculates optimal wake time from sunrise, window orientation, and weather
- **Solar recovery**: Delays morning heating to let the sun warm south-facing zones naturally
- **Learning grace period**: Excludes night setback transitions from adaptive learning to avoid confusion

### Solar Gain Prediction
Learns solar heating patterns per zone based on:
- Window orientation (south-facing gets more sun)
- Season (summer vs winter intensity)
- Cloud coverage forecast

### Contact Sensors
Pauses heating or switches to frost protection when windows/doors are open:
- `pause` - Stops heating completely
- `frost_protection` - Maintains minimum safe temperature (5°C)
- `none` - No action (useful if you only want logging)

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
- Configured via `main_heater_switch`, `main_cooler_switch`, and `source_startup_delay`

### Mode Synchronization
When one zone switches to HEAT or COOL mode, other zones follow (OFF mode remains independent).
- Enable with `sync_modes: true` in system configuration
- Prevents conflicting heat/cool modes across zones

### Zone Demand Tracking
Each zone creates a demand switch (`switch.{zone}_demand`) that indicates heating demand:
- ON when PID output exceeds threshold
- Used by central controller to aggregate demand
- Can also be used in custom automations

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
| `heater` | No* | - | Switch/valve entity (or list) for heating mode |
| `cooler` | No | - | Switch/valve entity (or list) for cooling mode |
| `demand_switch` | No* | - | Switch/valve entity (or list) controlled in both modes |
| `invert_heater` | No | false | Invert heater on/off polarity |
| `target_sensor` | Yes | - | Temperature sensor entity |
| `outdoor_sensor` | No | - | Outdoor temperature sensor for Ke |
| `min_temp` | No | 7 | Minimum setpoint temperature |
| `max_temp` | No | 35 | Maximum setpoint temperature |
| `target_temp` | No | 20 | Initial target temperature |
| `ac_mode` | No | false | Enable cooling mode support |

\* At least one of `heater`, `cooler`, or `demand_switch` is required.

### Timing & Cycle Parameters
| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `keep_alive` | Yes | - | Interval to refresh heater state |
| `pwm` | No | 00:15:00 | PWM period (set to 0 for valves) |
| `min_cycle_duration` | No | 00:00:00 | Minimum heater on-time |
| `min_off_cycle_duration` | No | - | Minimum heater off-time |
| `min_cycle_duration_pid_off` | No | - | Min on-time when PID disabled |
| `min_off_cycle_duration_pid_off` | No | - | Min off-time when PID disabled |
| `sampling_period` | No | 00:00:00 | PID calculation interval (0 = on sensor change) |

### Temperature Tolerance Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `hot_tolerance` | 0.3 | Tolerance above setpoint before cooling (°C) |
| `cold_tolerance` | 0.3 | Tolerance below setpoint before heating (°C) |
| `precision` | - | Display precision (tenths, halves, whole) |
| `target_temp_step` | - | Setpoint adjustment step size |

### PID Parameters (Optional - Auto-calculated)
PID values are automatically calculated from `heating_type` and zone properties. Only specify these to override the physics-based initialization.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `kp` | Auto | Proportional gain (10-500) |
| `ki` | Auto | Integral gain (0-100) |
| `kd` | Auto | Derivative gain (0-200) |
| `ke` | 0 | Outdoor temperature compensation gain |

### Output Control Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `output_min` | 0 | Minimum PID output value |
| `output_max` | 100 | Maximum PID output value |
| `out_clamp_low` | 0 | Lower output clamp limit |
| `out_clamp_high` | 100 | Upper output clamp limit |
| `output_precision` | 1 | Decimal places for output value |

### Safety Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `sensor_stall` | 06:00:00 | Timeout before declaring sensor stalled |
| `output_safety` | 5.0 | Safety output when sensor stalls (%) |
| `force_off_state` | true | Force heater off when HVAC mode is OFF |

### Adaptive Learning Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `heating_type` | - | System type: floor_hydronic, radiator, convector, forced_air |
| `area_m2` | - | Zone floor area in m² |
| `ceiling_height` | 2.5 | Ceiling height in meters |
| `window_area_m2` | - | Total window area in m² |
| `window_orientation` | - | Primary window direction (north, east, south, west, roof) |
| `learning_enabled` | true | Enable adaptive learning |

### Preset Mode Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `away_temp` | - | Away mode temperature |
| `eco_temp` | - | Eco mode temperature |
| `boost_temp` | - | Boost mode temperature |
| `comfort_temp` | - | Comfort mode temperature |
| `home_temp` | - | Home mode temperature |
| `sleep_temp` | - | Sleep mode temperature |
| `activity_temp` | - | Activity mode temperature |
| `preset_sync_mode` | none | Sync setpoint to preset ("sync" or "none") |
| `boost_pid_off` | false | Disable PID control in boost mode |

### Night Setback Parameters
Configure as a nested block under `night_setback`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `start` | - | Start time ("22:00" or "sunset+30") |
| `end` | dynamic | End time ("06:30") - if omitted, calculated from sunrise/orientation/weather |
| `delta` | 2.0 | Temperature reduction at night (°C) |
| `solar_recovery` | false | Delay morning heating to let sun warm the zone |
| `recovery_deadline` | - | Hard deadline for active heating recovery ("08:00") |

### Zone Coordination Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `linked_zones` | - | List of thermally connected zone entity IDs |
| `link_delay_minutes` | 20 | Delay (minutes) before linked zone starts heating |
| `contact_sensors` | - | List of window/door sensor entity IDs |
| `contact_action` | pause | Action when open: pause, frost_protection, none |
| `contact_delay` | 120 | Seconds before taking action |

### Health Monitoring Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `health_alerts_enabled` | true | Enable health monitoring alerts |
| `high_power_exception` | false | Exclude zone from high power warnings |

### Debug Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `debug` | false | Expose debug attributes on entity |

### System Configuration Parameters
These are configured under the `adaptive_thermostat:` domain block, not per-zone.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `house_energy_rating` | - | Building energy rating (A+++ to G) for physics init |
| `learning_window_days` | 7 | Days of data for adaptive learning |
| `weather_entity` | - | Weather entity for solar gain prediction |
| `main_heater_switch` | - | Main boiler/heat pump switch entity |
| `main_cooler_switch` | - | Main AC/chiller switch entity |
| `source_startup_delay` | 30 | Seconds to wait before activating heat source |
| `sync_modes` | true | Synchronize HEAT/COOL modes across zones |
| `notify_service` | - | Notification service for alerts/reports |
| `energy_meter_entity` | - | Energy meter sensor for cost tracking |
| `energy_cost_entity` | - | Energy price input for cost calculation |
| `supply_temp_sensor` | - | Supply water temperature sensor |
| `return_temp_sensor` | - | Return water temperature sensor |
| `flow_rate_sensor` | - | Flow rate sensor (L/min) |
| `volume_meter_entity` | - | Volume meter for flow rate calculation |
| `fallback_flow_rate` | 0.5 | Fallback flow rate (L/min) when meter unavailable |

## Troubleshooting

### Let Adaptive Learning Do the Work
The thermostat automatically learns and adjusts PID parameters. Give it time:
- Initial values come from empirical base values (calibrated per heating_type)
- Values are adjusted based on zone volume and window area
- After 3+ heating cycles, use `adaptive_thermostat.apply_adaptive_pid` to apply learned adjustments
- Use `adaptive_thermostat.reset_pid_to_physics` to reset to initial values if needed

### Common Issues
- **Slow response**: Check `heating_type` is correct, or wait for adaptive learning
- **Oscillations**: Usually resolves after adaptive learning detects the pattern
- **Never reaches setpoint**: Ensure `area_m2` is accurate
- **Overshoots then settles**: Normal initially, adaptive learning will reduce Kp

### Health Warnings
- **Critical cycle time (<10 min)**: System cycling too fast, check PID tuning
- **Warning cycle time (<15 min)**: Consider increasing PWM period
- **High power (>20 W/m²)**: May indicate heat loss issues

## Credits

This integration is a fork of [HASmartThermostat](https://github.com/ScratMan/HASmartThermostat) by ScratMan, with extensive enhancements for adaptive learning and multi-zone coordination.

The PID controller is based on the work from:
- [Smart Thermostat PID](https://github.com/aendle/custom_components)
- [pid-autotune](https://github.com/hirschmann/pid-autotune)
