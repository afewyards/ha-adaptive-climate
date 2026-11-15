# PID Gains Manager

Centralized manager for all PID gain mutations (kp/ki/kd/ke). Single entry point for gain changes with automatic history recording. Module: `managers/pid_gains_manager.py` (`PIDGainsManager`).

**Responsibilities:**
- Owns `_heating_gains` and `_cooling_gains` PIDGains objects
- Auto-records all changes to `pid_history` with timestamp, reason, actor
- Syncs gains to PIDController
- Handles state restoration with backward compatibility

**Interface:**
```python
# Set gains with automatic history recording
gains_manager.set_gains(
    PIDChangeReason.PHYSICS_RESET,  # Reason enum
    kp=1.5, ki=0.01, kd=10.0, ke=0.5,  # Partial updates allowed
    metrics={"undershoot": 0.3}  # Optional metrics
)

# Get current gains
gains = gains_manager.get_gains(HVACMode.HEAT)

# Get history
history = gains_manager.get_history(HVACMode.HEAT)
```

**PID Change Reasons:**
| Reason | Actor | Description |
|--------|-------|-------------|
| `PHYSICS_INIT` | system | Initial physics calculation |
| `PHYSICS_RESET` | user | Manual reset to physics |
| `ADAPTIVE_APPLY` | user | Manual apply learned gains |
| `AUTO_APPLY` | learning | Validation manager auto-apply |
| `ROLLBACK` | user | Rollback to previous gains |
| `KE_PHYSICS` | system | Ke enabled via physics |
| `KE_LEARNING` | learning | Ke adjusted via learning |
| `UNDERSHOOT_BOOST` | learning | Ki boost for undershoot (real-time or cycle-based) |
| `SERVICE_CALL` | user | Manual set via service |
| `RESTORE` | system | State restoration |

**History Format:**
```python
{
    "timestamp": "2024-01-15T10:30:00+00:00",
    "kp": 1.5, "ki": 0.01, "kd": 10.0, "ke": 0.5,
    "reason": "physics_reset",
    "actor": "user",
    "metrics": {...}  # Optional
}
```
