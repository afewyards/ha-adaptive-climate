# Valve Actuation Time

Configurable delay for motorized valve travel time. Affects PWM timing compensation and cycle tracking.

**Configuration (entity-level):**
```yaml
climate:
  - platform: adaptive_climate
    valve_actuation_time: 150  # seconds, optional
```

**Defaults by heating type:**
| Type | Default |
|------|---------|
| floor_hydronic | 120s |
| radiator | 90s |
| convector | 0s |
| forced_air | 30s |

**Timing behavior:**
- Demand ON: signaled when valve fully open (not at command)
- Demand OFF: signaled when valve half-closed
- PWM duty adjusted to account for valve travel
- Pump flow control: a zone's flow joins the requested pump flow only after this time

**Committed heat tracking:**
When transport delay exists, tracks in-flight heat and subtracts from next cycle's duty calculation. Learning splits overshoot into controllable vs committed portions.

**Command verification:**
After each switch turn_on/turn_off, the controller re-checks the switch state 20s later and re-sends the command (up to 2 retries, then logs an error) if it did not take effect, covering lost Zigbee commands.
