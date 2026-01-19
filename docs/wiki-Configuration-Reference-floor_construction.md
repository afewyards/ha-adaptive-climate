# Floor Construction Configuration

> This content is for the [Configuration Reference](https://github.com/afewyards/ha-adaptive-thermostat/wiki/Configuration-Reference) wiki page.

## Parameter: `floor_construction` (optional)

For `floor_hydronic` systems only. Specifies floor construction to calculate accurate thermal time constant.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pipe_spacing_mm` | int | No | 150 | Pipe spacing: 100, 150, 200, or 300 mm |
| `layers` | list | Yes | - | List of floor layers (top to bottom) |

### Layer Properties

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `type` | string | Yes | `top_floor` or `screed` |
| `material` | string | Yes | Material name from library |
| `thickness_mm` | int | Yes | Layer thickness in mm |

### Top Floor Materials

| Material | Conductivity W/(m*K) | Density kg/m3 | Specific Heat J/(kg*K) |
|----------|---------------------|---------------|------------------------|
| `ceramic_tile` | 1.3 | 2300 | 840 |
| `porcelain` | 1.5 | 2400 | 880 |
| `natural_stone` | 2.8 | 2700 | 900 |
| `terrazzo` | 1.8 | 2200 | 850 |
| `polished_concrete` | 1.4 | 2100 | 880 |
| `hardwood` | 0.15 | 700 | 1600 |
| `engineered_wood` | 0.13 | 650 | 1600 |
| `laminate` | 0.17 | 800 | 1500 |
| `vinyl` | 0.19 | 1200 | 1400 |
| `carpet` | 0.06 | 200 | 1300 |
| `cork` | 0.04 | 200 | 1800 |

### Screed Materials

| Material | Conductivity W/(m*K) | Density kg/m3 | Specific Heat J/(kg*K) |
|----------|---------------------|---------------|------------------------|
| `cement` | 1.4 | 2100 | 840 |
| `anhydrite` | 1.2 | 2000 | 1000 |
| `lightweight` | 0.47 | 1000 | 1000 |
| `mastic_asphalt` | 0.7 | 2100 | 920 |
| `synthetic` | 0.3 | 1200 | 1200 |
| `self_leveling` | 1.3 | 1900 | 900 |
| `dry_screed` | 0.2 | 800 | 1000 |

### Pipe Spacing Efficiency

| Spacing (mm) | Efficiency | Notes |
|--------------|-----------|-------|
| 100 | 0.92 | Tight spacing - best heat distribution |
| 150 | 0.87 | Standard spacing (default) |
| 200 | 0.80 | Wide spacing - moderate efficiency |
| 300 | 0.68 | Very wide spacing - reduced efficiency |

### Validation Rules

1. **Layer Order:** Top floor layers must precede screed layers
2. **Thickness Ranges:**
   - Top floor: 5-25 mm
   - Screed: 30-80 mm
3. **Material Types:** Must exist in the material libraries above
4. **Pipe Spacing:** One of 100, 150, 200, 300 mm (defaults to 150 if not specified)

### Example Configuration

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

### Custom Materials

Override thermal properties for unlisted materials:

```yaml
floor_construction:
  layers:
    - type: screed
      material: custom_mix
      thickness_mm: 60
      conductivity: 1.1    # W/(m*K)
      density: 1850        # kg/m3
      specific_heat: 950   # J/(kg*K)
```

When custom properties are provided, they override any library values for that material name.
