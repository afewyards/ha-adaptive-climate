# Automatic PID Application

By default (`auto_apply_pid: true`), PID parameters are automatically applied when the system reaches sufficient confidence in its recommendations. This feature eliminates the need to manually trigger `apply_adaptive_pid` while including safety measures to prevent runaway tuning.

## How It Works

### Learning Phase
1. System collects heating cycle metrics (overshoot, settling time, oscillations)
2. `AdaptiveLearner` calculates convergence confidence based on metric stability
3. Confidence increases as patterns become consistent across cycles

### Auto-Apply Trigger
When confidence reaches heating-type-specific thresholds:
1. Safety limits are checked (seasonal, lifetime, drift)
2. Current PID values are recorded to history
3. New PID values are applied
4. Learning history is cleared
5. Validation mode begins

### Validation Window
After auto-apply, the system enters a 5-cycle validation window:
- Monitors overshoot compared to baseline (average of last 6 cycles before apply)
- If overshoot degrades >30%, automatically rolls back to previous PID
- On success, validation mode exits and normal learning resumes

## Safety Limits

| Limit | Value | Description |
|-------|-------|-------------|
| **Seasonal** | 5 per 90 days | Maximum auto-applies per heating season |
| **Lifetime** | 20 total | Requires manual review after extensive tuning |
| **Drift** | 50% max | PID values can't exceed 1.5× physics baseline |
| **Seasonal Shift** | 7 days | Blocked after large outdoor temperature shift |

## Heating-Type Thresholds

Slow systems (high thermal mass) require higher confidence because mistakes are costly to recover from.

| Heating Type | First Apply | Subsequent | Min Cycles | Cooldown |
|--------------|-------------|------------|------------|----------|
| `floor_hydronic` | 80% | 90% | 8 cycles | 96h / 15 cycles |
| `radiator` | 70% | 85% | 7 cycles | 72h / 12 cycles |
| `convector` | 60% | 80% | 6 cycles | 48h / 10 cycles |
| `forced_air` | 60% | 80% | 6 cycles | 36h / 8 cycles |

- **First Apply**: Required confidence for first-ever auto-apply (no history)
- **Subsequent**: Required confidence after previous auto-applies (higher bar)
- **Min Cycles**: Minimum learning cycles before auto-apply can trigger
- **Cooldown**: Minimum time OR cycles between auto-applies (whichever is longer)

## Configuration

### Enabling (Default)
```yaml
climate:
  - platform: adaptive_thermostat
    name: Living Room
    heater: switch.heating_living
    target_sensor: sensor.temp_living
    heating_type: radiator
    area_m2: 20
    # auto_apply_pid: true  # Default, no need to specify
```

### Disabling
```yaml
climate:
  - platform: adaptive_thermostat
    name: Living Room
    heater: switch.heating_living
    target_sensor: sensor.temp_living
    heating_type: radiator
    area_m2: 20
    auto_apply_pid: false  # Manual application only
```

With auto-apply disabled, use `adaptive_thermostat.apply_adaptive_pid` service to manually apply learned values when ready.

## Monitoring

### Entity Attributes

| Attribute | Description |
|-----------|-------------|
| `auto_apply_pid_enabled` | Whether auto-apply is enabled for this zone |
| `auto_apply_count` | Number of times PID has been auto-applied |
| `validation_mode` | `true` when validating newly applied PID |
| `pid_history` | Last 3 PID configurations with timestamps |

### Example Dashboard Card

```yaml
type: entities
title: Auto-Apply Status
entities:
  - entity: climate.living_room
    type: attribute
    attribute: auto_apply_pid_enabled
    name: Auto-Apply Enabled
  - entity: climate.living_room
    type: attribute
    attribute: auto_apply_count
    name: Auto-Apply Count
  - entity: climate.living_room
    type: attribute
    attribute: validation_mode
    name: Validation Mode
  - entity: climate.living_room
    type: attribute
    attribute: convergence_confidence_pct
    name: Confidence
```

## Manual Rollback

If auto-applied values aren't performing well, use the rollback service:

```yaml
service: adaptive_thermostat.rollback_pid
target:
  entity_id: climate.living_room
```

This:
- Reverts PID to the previous configuration from history
- Clears learning history
- Exits validation mode
- Sends a notification confirming rollback

## Notifications

The system sends persistent notifications for:
- **Auto-Apply Success**: Shows old and new PID values, validation cycle count
- **Validation Failure**: Indicates automatic rollback occurred
- **Manual Rollback**: Confirms rollback was successful

## Troubleshooting

### Auto-Apply Not Triggering

1. Check `convergence_confidence_pct` attribute - must reach threshold
2. Verify `auto_apply_pid_enabled` is `true`
3. Check if safety limits are hit:
   - `auto_apply_count` near 5 (seasonal) or 20 (lifetime)
   - Recent seasonal shift may have triggered blocking period
4. Ensure minimum cycles collected (6-8 depending on heating type)

### Frequent Rollbacks

If validation frequently fails:
1. Check for external disturbances (windows opening, drafts)
2. Verify `heating_type` matches actual system
3. Consider disabling auto-apply and using manual service

### Seeing PID History

The `pid_history` attribute shows recent PID configurations:
```json
[
  {"timestamp": "2026-01-19T14:30:00", "kp": 85.2, "ki": 0.012, "kd": 45.1, "reason": "auto_apply"},
  {"timestamp": "2026-01-18T09:15:00", "kp": 92.0, "ki": 0.010, "kd": 50.0, "reason": "physics_reset"}
]
```

Reasons include: `auto_apply`, `manual_apply`, `physics_reset`, `rollback`
