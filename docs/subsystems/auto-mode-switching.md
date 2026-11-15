# Auto Mode Switching

House-wide automatic HVAC mode switching based on outdoor temp vs zone setpoints, with seasonal locking and forecast lookahead. Module: `managers/auto_mode_switching.py` (`AutoModeSwitchingManager`).

**Configuration (domain-level):**
```yaml
adaptive_climate:
  weather_entity: weather.home
  auto_mode_switching:
    enabled: true
    threshold: 2.0              # °C delta from median setpoint (default: 2.0)
    min_switch_interval: 3600   # seconds between switches (default: 1 hour)
    forecast_hours: 6           # lookahead for proactive switching (default: 6)
    season_thresholds:
      winter_below: 12.0        # forecast median < this = winter
      summer_above: 18.0        # forecast median > this = summer
```

**Logic:**
- Outdoor temp < median setpoint - threshold → HEAT
- Outdoor temp > median setpoint + threshold → COOL
- In between → stay in current mode (hysteresis)

**Season locking:**
- Winter (forecast median < 12°C) → only HEAT allowed
- Summer (forecast median > 18°C) → only COOL allowed
- Shoulder (12-18°C) → both allowed

**Forecast override:** When in hysteresis zone, checks forecast for next N hours to proactively switch if weather is trending past threshold.

**Integration:** Coordinator initializes manager, listens to outdoor temp changes from weather entity, and propagates mode to all non-OFF zones via service calls.
