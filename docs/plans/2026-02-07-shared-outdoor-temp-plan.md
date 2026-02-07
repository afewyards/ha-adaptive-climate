# Shared Outdoor Temperature Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move outdoor temp EMA filter from per-zone PID to coordinator so all zones sharing a weather entity see the same `outdoor_temp_lagged`.

**Architecture:** Coordinator owns single EMA filter with tau from `house_energy_rating`. Zones read shared lagged value. PID no longer filters outdoor temp. Zones with entity-level `outdoor_sensor` unchanged.

**Tech Stack:** Python, Home Assistant, pytest

**Baseline:** 2786 passed, 11 skipped, 0 failures

**Worktree:** `/Users/kleist/Sites/ha-adaptive-climate/.worktrees/shared-outdoor-temp` (branch: `feature/shared-outdoor-temp`)

---

### Task 1: Add EMA filter to coordinator

**Files:**
- Modify: `custom_components/adaptive_climate/coordinator.py`
- Test: `tests/test_coordinator.py`

**Step 1: Write failing tests**

Add to `tests/test_coordinator.py`:

```python
# --- Shared outdoor temp EMA tests ---

ENERGY_RATING_TAU = {
    "A++++": 10.0, "A+++": 8.0, "A++": 6.0, "A+": 5.0,
    "A": 4.0, "B": 3.0, "C": 2.5, "D": 2.0,
}

@pytest.mark.asyncio
async def test_outdoor_temp_lagged_defaults_to_none(hass):
    """outdoor_temp_lagged is None before any weather update."""
    coordinator = AdaptiveThermostatCoordinator(hass)
    assert coordinator.outdoor_temp_lagged is None

@pytest.mark.asyncio
async def test_outdoor_temp_lagged_initialized_on_first_reading(hass):
    """First weather reading sets outdoor_temp_lagged directly (no warmup)."""
    hass.data[DOMAIN] = {"weather_entity": "weather.home", "house_energy_rating": "B"}
    coordinator = AdaptiveThermostatCoordinator(hass)
    coordinator.update_outdoor_temp_lagged(5.0, dt_seconds=0)
    assert coordinator.outdoor_temp_lagged == 5.0

@pytest.mark.asyncio
async def test_outdoor_temp_lagged_ema_filter(hass):
    """EMA filter smooths outdoor temp with tau from house_energy_rating."""
    hass.data[DOMAIN] = {"weather_entity": "weather.home", "house_energy_rating": "A"}
    coordinator = AdaptiveThermostatCoordinator(hass)
    # tau = 4.0h for rating "A"
    coordinator.update_outdoor_temp_lagged(10.0, dt_seconds=0)  # init
    assert coordinator.outdoor_temp_lagged == 10.0

    # After 1 hour, temp jumps to 20°C
    # alpha = 3600 / (4.0 * 3600) = 0.25
    # lagged = 0.25 * 20 + 0.75 * 10 = 12.5
    coordinator.update_outdoor_temp_lagged(20.0, dt_seconds=3600)
    assert abs(coordinator.outdoor_temp_lagged - 12.5) < 0.01

@pytest.mark.asyncio
async def test_outdoor_temp_lagged_default_tau_without_rating(hass):
    """Uses default tau=4.0h when no house_energy_rating configured."""
    hass.data[DOMAIN] = {"weather_entity": "weather.home"}
    coordinator = AdaptiveThermostatCoordinator(hass)
    assert coordinator.outdoor_temp_tau == 4.0

@pytest.mark.asyncio
async def test_outdoor_temp_lagged_tau_from_rating(hass):
    """Tau derived from house_energy_rating."""
    hass.data[DOMAIN] = {"weather_entity": "weather.home", "house_energy_rating": "B"}
    coordinator = AdaptiveThermostatCoordinator(hass)
    assert coordinator.outdoor_temp_tau == 3.0
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_coordinator.py -k "outdoor_temp_lagged" -v`
Expected: FAIL — `outdoor_temp_lagged` property doesn't exist on coordinator

**Step 3: Implement**

In `coordinator.py`, add to `__init__`:
```python
# Shared outdoor temperature EMA filter
self._outdoor_temp_lagged: float | None = None
self._outdoor_temp_tau = self._resolve_outdoor_temp_tau()
self._last_outdoor_temp_update: float | None = None  # monotonic timestamp
```

Add methods:
```python
def _resolve_outdoor_temp_tau(self) -> float:
    """Get outdoor temp EMA tau from house_energy_rating, default 4.0h."""
    rating = self.hass.data.get(DOMAIN, {}).get("house_energy_rating")
    if not rating:
        return 4.0
    rating_map = {
        "A++++": 10.0, "A+++": 8.0, "A++": 6.0, "A+": 5.0,
        "A": 4.0, "B": 3.0, "C": 2.5, "D": 2.0,
    }
    return rating_map.get(rating.upper(), 4.0)

@property
def outdoor_temp_lagged(self) -> float | None:
    """Get the shared EMA-filtered outdoor temperature."""
    return self._outdoor_temp_lagged

@property
def outdoor_temp_tau(self) -> float:
    """Get the outdoor temp EMA time constant in hours."""
    return self._outdoor_temp_tau

def update_outdoor_temp_lagged(self, temp: float, dt_seconds: float) -> None:
    """Update the shared outdoor temp EMA filter.

    Args:
        temp: Current outdoor temperature in °C.
        dt_seconds: Time since last update in seconds.
    """
    if self._outdoor_temp_lagged is None or dt_seconds <= 0:
        self._outdoor_temp_lagged = temp
    else:
        alpha = dt_seconds / (self._outdoor_temp_tau * 3600.0)
        alpha = max(0.0, min(1.0, alpha))
        self._outdoor_temp_lagged = alpha * temp + (1.0 - alpha) * self._outdoor_temp_lagged
```

Update `_setup_outdoor_temp_listener` to also call the EMA update (add `import time` at top if not present):
```python
def _setup_outdoor_temp_listener(self) -> None:
    """Set up listener for outdoor temperature changes."""
    weather_entity_id = self.weather_entity
    if not weather_entity_id:
        _LOGGER.debug("No weather entity configured")
        return

    @callback
    def _async_outdoor_temp_changed(event: Event) -> None:
        """Handle outdoor temperature change."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        temp_attr = new_state.attributes.get("temperature")
        if temp_attr is None:
            return
        try:
            temp = float(temp_attr)
        except (ValueError, TypeError):
            return
        now = time.monotonic()
        dt_seconds = (now - self._last_outdoor_temp_update) if self._last_outdoor_temp_update else 0
        self.update_outdoor_temp_lagged(temp, dt_seconds)
        self._last_outdoor_temp_update = now
        # Auto mode switching (existing)
        if self._auto_mode_switching:
            self.hass.async_create_task(self._async_evaluate_auto_mode())

    self._outdoor_temp_unsub = async_track_state_change_event(
        self.hass, weather_entity_id, _async_outdoor_temp_changed,
    )
    _LOGGER.debug("Tracking outdoor temp from %s (tau=%.1fh)", weather_entity_id, self._outdoor_temp_tau)
```

**Important:** The weather listener must now always be set up when a weather entity exists (not just when auto mode switching is enabled). Move `_setup_outdoor_temp_listener()` call out of the auto mode switching `if` block — call it unconditionally after `__init__` sets up `_outdoor_temp_tau`.

Also initialize from current weather state on startup:
```python
# In __init__, after _setup_outdoor_temp_listener():
initial_temp = self.outdoor_temp
if initial_temp is not None:
    self._outdoor_temp_lagged = initial_temp
    self._last_outdoor_temp_update = time.monotonic()
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_coordinator.py -k "outdoor_temp_lagged" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/coordinator.py tests/test_coordinator.py
git commit -m "feat: add shared outdoor temp EMA filter to coordinator"
```

---

### Task 2: Remove EMA filter from PID controller

**Files:**
- Modify: `custom_components/adaptive_climate/pid_controller/__init__.py`
- Modify: `tests/test_pid_controller.py`

**Step 1: Update tests**

In `tests/test_pid_controller.py`:
- **`test_outdoor_temp_ema_filter`** (~line 681): Rewrite to verify PID uses `ext_temp` directly for `_dext` calculation without filtering. Pass ext_temp=10, verify `_dext = setpoint - 10` (no lag).
- **`test_outdoor_temp_lag_initialization`** (~line 736): Remove — PID no longer initializes lagged temp.
- **`test_outdoor_temp_lag_reset_on_clear_samples`** (~line 760): Remove — PID no longer owns lagged temp.
- **`test_outdoor_temp_lag_state_persistence`** (~line 778): Remove — persistence moves to coordinator.

Add replacement test:
```python
def test_outdoor_temp_passed_directly_to_dext():
    """PID uses ext_temp directly for _dext calculation (no EMA filter)."""
    pid = PID(kp=1.0, ki=0.01, kd=10.0, ke=0.5,
              out_min=0, out_max=100, sampling_period=0)
    # First call with ext_temp=10, setpoint=20
    pid.calc(19.0, 20.0, 100, 0, ext_temp=10.0)
    assert pid._dext == 20.0 - 10.0  # = 10.0

    # Second call with ext_temp jumps to 15 — no filtering, immediate
    pid.calc(19.0, 20.0, 200, 100, ext_temp=15.0)
    assert pid._dext == 20.0 - 15.0  # = 5.0
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pid_controller.py -k "outdoor_temp" -v`
Expected: New test fails (PID still filters), old tests may fail if removed

**Step 3: Implement**

In `pid_controller/__init__.py`:

Remove from `__init__`:
- `self._outdoor_temp_lag_tau = outdoor_temp_lag_tau` (line 125)
- `self._outdoor_temp_lagged = None` (line 126)

Remove `outdoor_temp_lag_tau` parameter from `__init__` signature.

Remove the `outdoor_temp_lagged` property and setter (lines 236-243).
Remove the `outdoor_temp_lag_tau` property (lines 245-248).

Replace EMA filter block (lines 565-580) with:
```python
# Use outdoor temperature directly for external compensation
# (EMA filtering is handled by the coordinator at house level)
if ext_temp is not None:
    self._dext = set_point - ext_temp
else:
    self._dext = 0
```

Also remove `outdoor_temp_lag_tau` from `clear_samples()` if it resets `_outdoor_temp_lagged` there.

**Step 4: Run tests**

Run: `python -m pytest tests/test_pid_controller.py -v`
Expected: PASS (updated tests pass, removed tests gone)

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/pid_controller/__init__.py tests/test_pid_controller.py
git commit -m "refactor: remove per-zone EMA filter from PID controller"
```

---

### Task 3: Wire zones to coordinator's lagged temp

**Files:**
- Modify: `custom_components/adaptive_climate/climate.py` (remove `_outdoor_temp_lag_tau`, stop passing to PID)
- Modify: `custom_components/adaptive_climate/managers/control_output.py` (pass coordinator lagged temp)
- Test: `tests/test_coordinator.py` (integration test: two zones same lagged value)

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_all_zones_share_outdoor_temp_lagged(hass):
    """All zones using same weather entity see identical outdoor_temp_lagged."""
    hass.data[DOMAIN] = {"weather_entity": "weather.home", "house_energy_rating": "B"}
    coordinator = AdaptiveThermostatCoordinator(hass)
    coordinator.update_outdoor_temp_lagged(8.0, dt_seconds=0)

    # Both zones read from coordinator — same value
    assert coordinator.outdoor_temp_lagged == 8.0

    coordinator.update_outdoor_temp_lagged(12.0, dt_seconds=1800)
    # alpha = 1800 / (3.0 * 3600) = 0.1667
    expected = 0.1667 * 12.0 + (1 - 0.1667) * 8.0
    assert abs(coordinator.outdoor_temp_lagged - expected) < 0.01
```

**Step 2: Run test — should pass already (coordinator done in Task 1)**

**Step 3: Implement wiring**

In `climate.py`:
- Remove `self._outdoor_temp_lag_tau` initialization (~line 356-375)
- Remove `outdoor_temp_lag_tau=self._outdoor_temp_lag_tau` from PID constructor call (~line 428)

In `managers/control_output.py` (~line 193):
- Change `ext_temp = self._thermostat_state._ext_temp` to use coordinator's lagged value for zones using the shared weather entity:
```python
# Use coordinator's shared lagged outdoor temp if available,
# otherwise fall back to zone's own ext_temp (for outdoor_sensor zones)
coordinator = self._thermostat_state._coordinator
if coordinator and coordinator.outdoor_temp_lagged is not None and not self._thermostat_state._ext_sensor_entity_id:
    ext_temp = coordinator.outdoor_temp_lagged
else:
    ext_temp = self._thermostat_state._ext_temp
```

**Step 4: Run full test suite**

Run: `python -m pytest -v`
Expected: PASS (some tests may need fixture updates — see Task 5)

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/climate.py custom_components/adaptive_climate/managers/control_output.py
git commit -m "refactor: wire zones to coordinator's shared outdoor temp"
```

---

### Task 4: Update state attributes and remove restoration

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py` (line 55)
- Modify: `custom_components/adaptive_climate/managers/state_restorer.py` (lines 162-167)

**Step 1: Write failing test**

Add test verifying `outdoor_temp_lagged` attr comes from coordinator:

```python
# In appropriate test file
def test_state_attributes_outdoor_temp_from_coordinator():
    """outdoor_temp_lagged attribute reads from coordinator, not PID."""
    # Mock thermostat with coordinator that has outdoor_temp_lagged=8.5
    # Verify build_state_attributes returns {"outdoor_temp_lagged": 8.5}
```

**Step 2: Implement**

In `state_attributes.py` (line 55), change:
```python
"outdoor_temp_lagged": thermostat._pid_controller.outdoor_temp_lagged,
```
to:
```python
"outdoor_temp_lagged": (
    thermostat._coordinator.outdoor_temp_lagged
    if thermostat._coordinator
    else thermostat._ext_temp
),
```

In `state_restorer.py`, remove lines 162-167 (the outdoor_temp_lagged restoration block).

**Step 3: Run tests**

Run: `python -m pytest -v`
Expected: PASS

**Step 4: Commit**

```bash
git add custom_components/adaptive_climate/managers/state_attributes.py custom_components/adaptive_climate/managers/state_restorer.py
git commit -m "refactor: outdoor_temp_lagged from coordinator, remove per-zone restoration"
```

---

### Task 5: Fix remaining test failures

**Files:**
- Various test files that reference `outdoor_temp_lagged` on PID or pass `outdoor_temp_lag_tau` to PID constructor

**Step 1: Run full suite, collect failures**

Run: `python -m pytest -v 2>&1 | grep FAILED`

**Step 2: Fix each failure**

Likely fixes:
- Tests constructing PID with `outdoor_temp_lag_tau=` kwarg → remove it
- Tests checking `pid.outdoor_temp_lagged` → check coordinator instead or remove
- Tests mocking `_outdoor_temp_lag_tau` on climate entity → remove

**Step 3: Run full suite**

Run: `python -m pytest -v`
Expected: 2786+ passed, 0 failures (test count may decrease slightly from removed tests)

**Step 4: Commit**

```bash
git add -u
git commit -m "test: fix outdoor temp tests for shared coordinator model"
```

---

### Task 6: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update docs**

Remove references to per-zone `outdoor_temp_lag_tau`. Note in architecture that coordinator owns outdoor temp EMA filter.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for shared outdoor temp"
```
