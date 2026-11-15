# Predictive Pre-Heating

Learns heating rate to start early and reach target AT scheduled time. Based on Netatmo Auto-Adapt / Tado Early Start. Module: `adaptive/preheat.py` (`PreheatLearner`).

**Configuration:**
```yaml
night_setback:
  recovery_deadline: "07:00"
  preheat_enabled: true       # Enable predictive pre-heating
  max_preheat_hours: 3.0      # Optional, defaults per heating_type
```

**Learning:**
- Bins observations by delta (0-2, 2-4, 4-6, 6+°C) and outdoor temp (cold/mild/moderate)
- Requires 3+ observations for learned rate
- Fallback: Uses `HEATING_TYPE_PREHEAT_CONFIG` rates until sufficient data

**State attributes (debug only):** `preheat_active`, `preheat_scheduled_start`, `preheat_estimated_duration_min`, `preheat_learning_confidence`, `preheat_heating_rate_learned`, `preheat_observation_count`
