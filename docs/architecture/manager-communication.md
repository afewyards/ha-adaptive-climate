# Manager Communication Patterns

## Overview

Adaptive Climate uses four distinct communication patterns between the thermostat entity and its managers. Each pattern serves a specific purpose and should be used in the appropriate context.

## The Four Patterns

### 1. Notifications (Events via CycleEventDispatcher)

**Purpose:** One-way, fire-and-forget notifications about state changes.

**When to use:**
- Manager needs to know something happened but doesn't need to respond
- Multiple managers need to be notified of the same event
- Event source doesn't care who's listening or what they do with the event
- Decoupling is important (source doesn't know about listeners)

**How it works:**
- Event source emits typed event objects (dataclasses) via `CycleEventDispatcher`
- Listeners subscribe to event types
- Events flow one direction only (source → listeners)
- No return values, no blocking

**Example from codebase:**
```python
# HeaterController emits heating events
self._dispatcher.emit(
    HeatingStartedEvent(
        hvac_mode=hvac_mode,
        timestamp=dt_util.utcnow()
    )
)

# CycleTrackerManager subscribes to events
dispatcher.subscribe(
    CycleEventType.HEATING_STARTED,
    self._on_heating_started
)
```

**Event types:**
- `CYCLE_STARTED` / `CYCLE_ENDED` - Full heating cycle lifecycle
- `HEATING_STARTED` / `HEATING_ENDED` - Device on/off transitions
- `SETTLING_STARTED` - Settling phase begins
- `SETPOINT_CHANGED` - Target temperature changed
- `MODE_CHANGED` - HVAC mode switched
- `CONTACT_PAUSE` / `CONTACT_RESUME` - Contact sensor state
- `TEMPERATURE_UPDATE` - Current temperature reading

**Module:** `managers/events.py`

---

### 2. Actions (Callbacks)

**Purpose:** Manager needs to trigger an async action on the thermostat.

**When to use:**
- Manager needs thermostat to DO something (not just read state)
- Action is async and may involve I/O (service calls, device control)
- Manager doesn't own the action logic, just triggers it
- Single recipient (thermostat) performs the action

**How it works:**
- Manager receives callback during initialization: `Callable[[], Awaitable[None]]`
- Manager calls callback when action needed: `await self._callback()`
- Thermostat implements the actual action

**Example from codebase:**
```python
# CycleTrackerManager receives callbacks for actions
def __init__(
    self,
    # ... other params ...
    on_validation_failed: Callable[[], Awaitable[None]] | None = None,
    on_auto_apply_check: Callable[[], Awaitable[None]] | None = None,
):
    self._on_validation_failed = on_validation_failed
    self._on_auto_apply_check = on_auto_apply_check

# Manager triggers action
if validation_failed:
    if self._on_validation_failed:
        await self._on_validation_failed()  # Thermostat performs rollback

# Thermostat provides implementation
cycle_tracker = CycleTrackerManager(
    # ...
    on_validation_failed=self._async_handle_validation_failure,
    on_auto_apply_check=self._async_check_auto_apply,
)
```

**Common action callbacks:**
- `on_validation_failed` - Trigger PID rollback
- `on_auto_apply_check` - Check if auto-apply conditions met
- `_async_control_output` - Trigger PID calculation and device control
- `async_schedule_update_ha_state` - Force state refresh in HA

**Type signature:** `Callable[[], Awaitable[None]]` (no parameters, async, no return)

---

### 3. Queries (Sub-protocols)

**Purpose:** Manager needs to read thermostat state synchronously.

**When to use:**
- Manager needs to read multiple related properties
- Access pattern is synchronous (no `await` needed)
- Manager should be decoupled from full thermostat implementation
- Type safety is important

**How it works:**
- Define a Protocol (structural type) describing required properties
- Manager accepts protocol instance during initialization
- Manager reads properties directly: `self._state.current_temperature`
- Thermostat satisfies protocol by implementing required properties

**Example from codebase:**
```python
# Define sub-protocol for temperature state
@runtime_checkable
class TemperatureState(Protocol):
    """Temperature-related state that managers need."""

    @property
    def current_temperature(self) -> float | None: ...

    @property
    def target_temperature(self) -> float | None: ...

    @property
    def _ext_temp(self) -> float | None: ...

# Manager uses protocol
class TemperatureManager:
    def __init__(self, state: TemperatureState):
        self._state = state

    def get_error(self) -> float:
        return self._state.target_temperature - self._state.current_temperature

# Thermostat satisfies protocol automatically
class AdaptiveThermostat(ClimateEntity):
    @property
    def current_temperature(self) -> float | None:
        return self._current_temp
    # ... other properties ...
```

**Main protocol:** `ThermostatState` (`protocols.py`)

**Sub-protocols (implemented):**
- `TemperatureState` - Current/target/outdoor temps, tolerances
- `PIDState` - PID gains (kp/ki/kd/ke), control output, component terms (P/I/D/E)
- `HVACState` - HVAC mode, heating type, device active state
- `KeManagerState` - Combines TemperatureState + PIDState + HVACState for outdoor compensation
- `PIDTuningManagerState` - Extends ThermostatState with physical properties for tuning

**Protocol hierarchy:**
- Base protocols: `TemperatureState`, `PIDState`, `HVACState` (minimal, focused)
- Composite protocols: `KeManagerState` (inherits from three base protocols)
- Full protocol: `ThermostatState` (inherits from base protocols, adds full state)
- Specialized protocols: `PIDTuningManagerState` (extends ThermostatState with tuning properties)

**Benefits:**
- Type checking catches interface mismatches
- Managers only see properties they need
- Easy to test (mock the protocol)
- Documents dependencies explicitly

---

### 4. Hot Path (Direct Reference)

**Purpose:** Performance-critical code that needs immediate access.

**When to use:**
- Called frequently (every control cycle)
- Performance is critical
- Overhead of callbacks/events unacceptable
- Tight coupling is acceptable for performance

**How it works:**
- Manager receives direct object reference during initialization
- Manager calls methods directly on the object
- No indirection, no async overhead

**Example from codebase:**
```python
# PIDController stored as direct reference for hot path
class AdaptiveThermostat:
    def __init__(self, **kwargs):
        self._pid_controller = pid_controller.PIDController(
            kp=kp, ki=ki, kd=kd, ke=ke,
            output_min=output_min,
            output_max=output_max,
        )

    async def _async_control_heating(self):
        # Direct call in hot path - no protocol, no callback
        control_output = self._pid_controller.update(
            setpoint=setpoint,
            current_value=current_temp,
            dt=dt,
            outdoor_temp=outdoor_temp,
            wind_speed=wind_speed,
        )
```

**Current hot path components:**
- `PIDController` - Called every control cycle
- `PWMController` - Called during device actuation

**Trade-offs:**
- ✅ Maximum performance
- ✅ Zero indirection overhead
- ❌ Tight coupling
- ❌ Harder to test in isolation
- ❌ Breaking encapsulation

**Use sparingly:** Only for truly performance-critical code.

---

## Decision Tree

Use this flowchart to choose the right pattern:

```
┌─────────────────────────────────────┐
│ Manager needs to communicate with   │
│ thermostat...                       │
└─────────────┬───────────────────────┘
              │
              ▼
      Is it fire-and-forget?
      (No response needed)
              │
        Yes   │   No
      ┌───────┴───────┐
      │               │
      ▼               ▼
  EVENTS          Does manager need
                  thermostat to DO
                  something async?
                      │
                Yes   │   No
              ┌───────┴───────┐
              │               │
              ▼               ▼
          CALLBACK        Is it called
                          frequently in
                          hot path?
                              │
                        Yes   │   No
                      ┌───────┴───────┐
                      │               │
                      ▼               ▼
                  DIRECT          PROTOCOL
                  REFERENCE       (Query)
```

**Quick reference:**
- **Fire-and-forget notification?** → Events
- **Trigger async action?** → Callback
- **Performance-critical (hot path)?** → Direct reference
- **Read state synchronously?** → Protocol

---

## Examples from Codebase

### Events: HeaterController → CycleEventDispatcher

**Pattern:** Notifications

**Why:** HeaterController needs to notify other components (CycleTrackerManager, AdaptiveLearner) about heating state changes without knowing who's listening or coupling to their implementation.

**Code:**
```python
# HeaterController emits events when device state changes
self._dispatcher.emit(
    HeatingStartedEvent(
        hvac_mode=hvac_mode,
        timestamp=dt_util.utcnow()
    )
)

# CycleTrackerManager subscribes to events
dispatcher.subscribe(
    CycleEventType.HEATING_STARTED,
    self._on_heating_started
)
```

**Location:** `managers/heater_controller.py`, `managers/cycle_tracker.py`, `managers/events.py`

---

### Callbacks: HeaterController Actions

**Pattern:** Actions (Callbacks)

**Why:** HeaterController needs to trigger PID recalculation after clamping output without owning the PID logic.

**Code:**
```python
# HeaterController receives callback
def __init__(
    self,
    # ...
    on_control_output: Callable[[], Awaitable[None]] | None = None,
):
    self._on_control_output = on_control_output

# Trigger thermostat action
async def _async_control_valve(self, control_output: float):
    # ... clamp output ...
    if output_clamped and self._on_control_output:
        await self._on_control_output()

# Thermostat provides implementation
heater_controller = HeaterController(
    # ...
    on_control_output=self._async_control_output,
)
```

**Location:** `managers/heater_controller.py`

---

### Protocol: Managers Receiving ThermostatState

**Pattern:** Queries (Sub-protocol)

**Why:** Managers need to read thermostat state without tight coupling to implementation.

**Code:**
```python
# Protocol definition
@runtime_checkable
class ThermostatState(Protocol):
    @property
    def current_temperature(self) -> float | None: ...

    @property
    def target_temperature(self) -> float | None: ...

    @property
    def hvac_mode(self) -> HVACMode: ...

# Manager uses protocol
class SomeManager:
    def __init__(self, state: ThermostatState):
        self._state = state

    def calculate(self) -> float:
        return self._state.target_temperature - self._state.current_temperature

# Thermostat satisfies protocol
class AdaptiveThermostat(ClimateEntity):
    # Automatically satisfies ThermostatState by having matching properties
    @property
    def current_temperature(self) -> float | None:
        return self._current_temp
```

**Location:** `protocols.py`, used throughout managers

---

### Direct Reference: PIDController in Hot Path

**Pattern:** Direct reference

**Why:** PID calculation happens every control cycle (frequent), performance is critical.

**Code:**
```python
# Store direct reference
self._pid_controller = pid_controller.PIDController(
    kp=kp, ki=ki, kd=kd, ke=ke,
    output_min=output_min,
    output_max=output_max,
)

# Call directly in hot path
control_output = self._pid_controller.update(
    setpoint=setpoint,
    current_value=current_temp,
    dt=dt,
    outdoor_temp=outdoor_temp,
    wind_speed=wind_speed,
)
```

**Location:** `climate.py` → `pid_controller/__init__.py`

---

## Checklist for New Managers

When creating a new manager, follow these guidelines:

### 1. Choose the Right Pattern(s)

- [ ] Identify what the manager needs from the thermostat
- [ ] Use decision tree to select pattern(s)
- [ ] Justify if using direct reference (must be hot path)
- [ ] Prefer sub-protocols over full `ThermostatState`

### 2. Define Dependencies Explicitly

- [ ] Document which pattern(s) the manager uses in docstring
- [ ] Use type hints for all parameters (protocols, callbacks, etc.)
- [ ] Avoid mixing patterns inappropriately
- [ ] Keep initialization signature clean

### 3. Maintain Separation of Concerns

- [ ] Events for notifications only (no return values)
- [ ] Callbacks only for async actions (no synchronous work)
- [ ] Protocols only for reading state (no mutations)
- [ ] Direct refs only for performance-critical paths

### 4. Test Appropriately

- [ ] Mock protocols in unit tests
- [ ] Verify callbacks are called correctly
- [ ] Test event emission and subscription
- [ ] Don't test through direct references (use integration tests)

### 5. Document Communication Flow

- [ ] Add docstring describing manager's communication needs
- [ ] Document what events are emitted (if any)
- [ ] Document what callbacks are triggered (if any)
- [ ] Update this document if introducing new patterns

---

## Anti-Patterns to Avoid

### ❌ Don't Mix Events and Callbacks for Same Purpose

```python
# BAD: Using both event and callback for same notification
self._dispatcher.emit(ValidationFailedEvent())
await self._on_validation_failed()

# GOOD: Choose one
self._dispatcher.emit(ValidationFailedEvent())
# OR
await self._on_validation_failed()
```

### ❌ Don't Use Direct Reference When Protocol Suffices

```python
# BAD: Direct reference for non-hot-path code
class SomeManager:
    def __init__(self, thermostat: AdaptiveThermostat):
        self._thermostat = thermostat

# GOOD: Use protocol
class SomeManager:
    def __init__(self, state: SomeManagerState):
        self._state = state
```

### ❌ Don't Pass Too Many Individual Callbacks

```python
# BAD: Callback explosion
def __init__(
    self,
    get_temp: Callable[[], float],
    get_target: Callable[[], float],
    get_mode: Callable[[], str],
    get_outdoor: Callable[[], float],
    # ... 10 more callbacks ...
):

# GOOD: Use protocol for state queries
def __init__(self, state: TemperatureState):
```

### ❌ Don't Make Protocols Too Large

```python
# BAD: Protocol exposes everything
class ManagerState(Protocol):
    # 50 properties that manager doesn't need

# GOOD: Minimal sub-protocol
class ManagerState(Protocol):
    @property
    def temperature(self) -> float: ...

    @property
    def target(self) -> float: ...
```

### ❌ Don't Block in Event Handlers

```python
# BAD: Async work in event handler
async def on_event(self, event):
    await self.do_expensive_work()  # Blocks event dispatch

# GOOD: Schedule work separately
def on_event(self, event):
    self._pending_work = event
    # Actual work happens in separate async method
```

---

## Pattern Evolution

The codebase is currently transitioning from:
- **Individual callbacks** (`get_temp`, `get_target`, etc.) → **Sub-protocols** (`TemperatureState`, `PIDState`)
- **Full `ThermostatState`** → **Minimal sub-protocols**

**Migration strategy:**
1. Define sub-protocol for manager's needs
2. Update manager to accept protocol instead of callbacks
3. Ensure thermostat satisfies protocol
4. Remove old callback parameters
5. Update tests to mock protocol

**Benefits of sub-protocols:**
- Fewer initialization parameters
- Type-safe property access
- Self-documenting dependencies
- Easier to test

---

## Summary

| Pattern | Use Case | Async? | Direction | Coupling |
|---------|----------|--------|-----------|----------|
| **Events** | Fire-and-forget notifications | No | One-way | Loose |
| **Callbacks** | Trigger async actions | Yes | One-way | Medium |
| **Protocols** | Read state synchronously | No | One-way | Loose |
| **Direct Ref** | Performance-critical hot path | Either | Both ways | Tight |

**Default choice:** Start with protocols for state access, events for notifications, and callbacks only when async actions are needed. Reserve direct references for proven hot paths.

---

## Manager Pattern Reference

Complete mapping of all managers to their communication patterns:

| Manager | Events (Listen) | Events (Emit) | Callbacks | Protocol | Direct Ref |
|---------|----------------|---------------|-----------|----------|------------|
| `HeaterController` | - | ✅ HEATING_* | ✅ on_control_output | - | PIDController, PWMController |
| `PWMController` | - | - | ✅ set_heater | - | - |
| `CycleTrackerManager` | ✅ HEATING_*, TEMP_* | ✅ CYCLE_*, SETTLING_* | ✅ validation_failed, auto_apply | - | - |
| `CycleMetricsRecorder` | ✅ CYCLE_ENDED | - | - | - | - |
| `TemperatureManager` | ✅ TEMP_UPDATE | ✅ TEMP_UPDATE | - | ✅ TemperatureState | - |
| `KeManager` | - | - | ✅ set_ke, async_control | ✅ KeManagerState | - |
| `PIDTuningManager` | - | - | - | ✅ PIDTuningManagerState | - |
| `NightSetbackManager` | - | ✅ SETPOINT_CHANGED | ✅ schedule_update | - | - |
| `NightSetbackCalculator` | - | - | - | - | - |
| `LearningGateManager` | - | - | - | - | - |
| `SetpointBoostManager` | ✅ SETPOINT_CHANGED | - | - | - | PIDController |
| `PIDGainsManager` | - | - | - | - | PIDController |
| `AutoModeSwitchingManager` | - | - | ✅ async_set_hvac_mode | - | - |
| `StatusManager` | - | - | - | - | Multiple managers |

**Notes:**
- Controllers (HeaterController, PWMController) use direct references for hot-path performance
- CycleTrackerManager is event-heavy: listens to device state, emits cycle lifecycle
- TemperatureManager bridges sensor updates to event system
- KeManager and PIDTuningManager use specialized protocols (KeManagerState, PIDTuningManagerState)
- StatusManager aggregates state from multiple managers (direct access for efficiency)
