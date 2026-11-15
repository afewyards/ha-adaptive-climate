# Setpoint Feedforward

Detects setpoint changes and applies integral boost/decay to accelerate PID response. P-on-M eliminates setpoint kicks but causes sluggish response - this compensates by pre-loading the integral term.

**Configuration (entity-level):**
```yaml
climate:
  - platform: adaptive_climate
    setpoint_boost: true          # Enable setpoint feedforward (default: true)
    setpoint_boost_factor: 25.0   # Override default factor (optional)
    setpoint_debounce: 5          # Debounce window in seconds (default: 5)
```

**Behavior:**
- Debounces rapid clicks (5 clicks in 3s → single boost for accumulated delta)
- Boost on setpoint INCREASE: `boost = min(delta * factor, max(integral * 0.5, 15.0))`
- Decay on setpoint DECREASE: `integral *= max(0.3, 1.0 - abs(delta) * decay_rate)`

**Skip conditions:**
- `abs(delta) < 0.3°C` (too small)
- Night setback active (already reduced setpoint)

**Heating type factors:**
| Type | boost_factor | decay_rate |
|------|--------------|------------|
| floor_hydronic | 25.0 | 0.15 |
| radiator | 18.0 | 0.20 |
| convector | 12.0 | 0.25 |
| forced_air | 8.0 | 0.30 |

Module: `managers/setpoint_boost.py` (`SetpointBoostManager`).
