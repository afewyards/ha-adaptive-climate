# Floor Construction Impact on PID

> This content is for the [PID Control](https://github.com/afewyards/ha-adaptive-thermostat/wiki/PID-Control) wiki page.

## How Floor Construction Affects PID Tuning

For `floor_hydronic` systems, the `floor_construction` parameter adjusts the thermal time constant (tau) based on actual floor mass. This directly influences the physics-based PID initialization.

### Effects on PID Parameters

| Floor Characteristic | Effect on Tau | PID Impact |
|---------------------|---------------|------------|
| **Heavier floors** (thick screed, tile) | Higher tau | More conservative Kp/Ki, higher Kd |
| **Lighter floors** (thin screed, carpet) | Lower tau | More responsive Kp/Ki, lower Kd |
| **Tight pipe spacing** (100mm) | Lower tau | Faster response, more aggressive tuning |
| **Wide pipe spacing** (300mm) | Higher tau | Slower response, more conservative tuning |

### Tau Modifier Calculation

The tau modifier adjusts the base thermal time constant based on actual floor mass relative to a reference configuration:

```
tau_modifier = (floor_thermal_mass / reference_mass) / pipe_spacing_efficiency
```

Where:
- `floor_thermal_mass = sum(thickness_m x area_m2 x density x specific_heat)` for all layers
- `reference_mass` = thermal mass of 50mm cement screed
- `pipe_spacing_efficiency` = efficiency factor from pipe spacing (0.68 to 0.92)

### Reference Configuration

The baseline (tau_modifier = 1.0) is:
- 50mm cement screed
- 150mm pipe spacing

### Example Calculations

**Heavy floor (tile + thick screed):**
- 10mm ceramic tile + 70mm cement screed at 150mm spacing
- Total mass ~1.4x reference mass
- tau_modifier = 1.4 / 0.87 = 1.61
- Result: 61% slower response, more conservative PID

**Light floor (carpet + thin screed):**
- 8mm carpet + 35mm lightweight screed at 100mm spacing
- Total mass ~0.4x reference mass
- tau_modifier = 0.4 / 0.92 = 0.43
- Result: 57% faster response, more aggressive PID

### Practical Impact

| Tau Modifier | System Response | PID Behavior |
|--------------|-----------------|--------------|
| < 0.8 | Fast (light floor) | Higher Kp, faster integral action |
| 0.8 - 1.2 | Normal | Baseline tuning |
| 1.2 - 1.6 | Slow (heavy floor) | Lower Kp, slower integral action, higher Kd |
| > 1.6 | Very slow | Very conservative, prioritizes stability |

### Why This Matters

Without floor construction data, the system uses default assumptions. Specifying actual construction:

1. **Prevents overshoot** in heavy floors (thick stone/tile) by using conservative initial tuning
2. **Improves responsiveness** in light floors (carpet/thin screed) by using more aggressive tuning
3. **Reduces learning time** by starting closer to optimal PID values
4. **Accounts for pipe spacing** which affects heat distribution uniformity
