# State Attributes Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure state attributes into clear groups: flat restoration, `status` (activity + overrides), `learning`, `debug`.

**Architecture:** Refactor `state_attributes.py` and `status_manager.py` to produce new structure. StatusManager builds overrides array ordered by priority. Debug object groups attributes by feature.

**Tech Stack:** Python, Home Assistant, pytest

---

## Task 1: Add Override TypedDict and OverrideType Enum

**Files:**
- Modify: `custom_components/adaptive_climate/const.py`

**Step 1: Write failing test**

```python
# tests/test_state_attributes.py - add at top of file after imports

def test_override_type_enum_exists():
    """Override types should be defined as enum."""
    from custom_components.adaptive_climate.const import OverrideType

    assert OverrideType.CONTACT_OPEN.value == "contact_open"
    assert OverrideType.HUMIDITY.value == "humidity"
    assert OverrideType.OPEN_WINDOW.value == "open_window"
    assert OverrideType.PREHEATING.value == "preheating"
    assert OverrideType.NIGHT_SETBACK.value == "night_setback"
    assert OverrideType.LEARNING_GRACE.value == "learning_grace"
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_override_type_enum_exists -v`
Expected: FAIL with ImportError

**Step 3: Write implementation**

Add to `const.py` after `ThermostatCondition`:

```python
class OverrideType(StrEnum):
    """Override types for status attribute."""
    CONTACT_OPEN = "contact_open"
    HUMIDITY = "humidity"
    OPEN_WINDOW = "open_window"
    PREHEATING = "preheating"
    NIGHT_SETBACK = "night_setback"
    LEARNING_GRACE = "learning_grace"


# Override priority order (highest first)
OVERRIDE_PRIORITY = [
    OverrideType.CONTACT_OPEN,
    OverrideType.HUMIDITY,
    OverrideType.OPEN_WINDOW,
    OverrideType.PREHEATING,
    OverrideType.NIGHT_SETBACK,
    OverrideType.LEARNING_GRACE,
]
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_override_type_enum_exists -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/const.py tests/test_state_attributes.py
git commit -m "feat: add OverrideType enum and priority order"
```

---

## Task 2: Create Override Builder Functions

**Files:**
- Modify: `custom_components/adaptive_climate/managers/status_manager.py`

**Step 1: Write failing test**

```python
# tests/test_status_manager.py - add new test

def test_build_contact_open_override():
    """Contact open override should have correct structure."""
    from custom_components.adaptive_climate.managers.status_manager import build_override
    from custom_components.adaptive_climate.const import OverrideType

    override = build_override(
        OverrideType.CONTACT_OPEN,
        sensors=["binary_sensor.window_1", "binary_sensor.door_1"],
        since="2024-01-15T10:30:00+00:00",
    )

    assert override["type"] == "contact_open"
    assert override["sensors"] == ["binary_sensor.window_1", "binary_sensor.door_1"]
    assert override["since"] == "2024-01-15T10:30:00+00:00"


def test_build_night_setback_override():
    """Night setback override should have correct structure."""
    from custom_components.adaptive_climate.managers.status_manager import build_override
    from custom_components.adaptive_climate.const import OverrideType

    override = build_override(
        OverrideType.NIGHT_SETBACK,
        delta=-2.0,
        ends_at="07:00",
        limited_to=1.0,
    )

    assert override["type"] == "night_setback"
    assert override["delta"] == -2.0
    assert override["ends_at"] == "07:00"
    assert override["limited_to"] == 1.0


def test_build_night_setback_override_without_limited():
    """Night setback override without limited_to should omit field."""
    from custom_components.adaptive_climate.managers.status_manager import build_override
    from custom_components.adaptive_climate.const import OverrideType

    override = build_override(
        OverrideType.NIGHT_SETBACK,
        delta=-2.0,
        ends_at="07:00",
    )

    assert "limited_to" not in override


def test_build_humidity_override():
    """Humidity override should have state and resume_at."""
    from custom_components.adaptive_climate.managers.status_manager import build_override
    from custom_components.adaptive_climate.const import OverrideType

    override = build_override(
        OverrideType.HUMIDITY,
        state="paused",
        resume_at="2024-01-15T10:45:00+00:00",
    )

    assert override["type"] == "humidity"
    assert override["state"] == "paused"
    assert override["resume_at"] == "2024-01-15T10:45:00+00:00"


def test_build_preheating_override():
    """Preheating override should have target_time, started_at, target_delta."""
    from custom_components.adaptive_climate.managers.status_manager import build_override
    from custom_components.adaptive_climate.const import OverrideType

    override = build_override(
        OverrideType.PREHEATING,
        target_time="07:00",
        started_at="2024-01-15T05:30:00+00:00",
        target_delta=2.0,
    )

    assert override["type"] == "preheating"
    assert override["target_time"] == "07:00"
    assert override["started_at"] == "2024-01-15T05:30:00+00:00"
    assert override["target_delta"] == 2.0


def test_build_open_window_override():
    """Open window override should have since and resume_at."""
    from custom_components.adaptive_climate.managers.status_manager import build_override
    from custom_components.adaptive_climate.const import OverrideType

    override = build_override(
        OverrideType.OPEN_WINDOW,
        since="2024-01-15T10:30:00+00:00",
        resume_at="2024-01-15T10:45:00+00:00",
    )

    assert override["type"] == "open_window"
    assert override["since"] == "2024-01-15T10:30:00+00:00"
    assert override["resume_at"] == "2024-01-15T10:45:00+00:00"


def test_build_learning_grace_override():
    """Learning grace override should have until."""
    from custom_components.adaptive_climate.managers.status_manager import build_override
    from custom_components.adaptive_climate.const import OverrideType

    override = build_override(
        OverrideType.LEARNING_GRACE,
        until="2024-01-15T11:00:00+00:00",
    )

    assert override["type"] == "learning_grace"
    assert override["until"] == "2024-01-15T11:00:00+00:00"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py::test_build_contact_open_override tests/test_status_manager.py::test_build_night_setback_override -v`
Expected: FAIL with ImportError

**Step 3: Write implementation**

Add to `status_manager.py`:

```python
from ..const import OverrideType


def build_override(override_type: OverrideType, **kwargs) -> dict[str, Any]:
    """Build an override dict with type and provided fields.

    Args:
        override_type: The type of override
        **kwargs: Fields specific to this override type

    Returns:
        Dict with "type" and all non-None kwargs
    """
    result: dict[str, Any] = {"type": override_type.value}
    for key, value in kwargs.items():
        if value is not None:
            result[key] = value
    return result
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py -k "test_build_" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/status_manager.py tests/test_status_manager.py
git commit -m "feat: add build_override function for override dicts"
```

---

## Task 3: Create Overrides List Builder

**Files:**
- Modify: `custom_components/adaptive_climate/managers/status_manager.py`

**Step 1: Write failing test**

```python
# tests/test_status_manager.py - add new tests

def test_build_overrides_empty_when_no_conditions():
    """Overrides should be empty list when no conditions active."""
    from custom_components.adaptive_climate.managers.status_manager import build_overrides

    overrides = build_overrides()
    assert overrides == []


def test_build_overrides_single_contact_open():
    """Single contact_open override should be in list."""
    from custom_components.adaptive_climate.managers.status_manager import build_overrides

    overrides = build_overrides(
        contact_open=True,
        contact_sensors=["binary_sensor.window"],
        contact_since="2024-01-15T10:30:00+00:00",
    )

    assert len(overrides) == 1
    assert overrides[0]["type"] == "contact_open"
    assert overrides[0]["sensors"] == ["binary_sensor.window"]


def test_build_overrides_priority_order():
    """Multiple overrides should be in priority order."""
    from custom_components.adaptive_climate.managers.status_manager import build_overrides

    overrides = build_overrides(
        contact_open=True,
        contact_sensors=["binary_sensor.window"],
        contact_since="2024-01-15T10:30:00+00:00",
        night_setback_active=True,
        night_setback_delta=-2.0,
        night_setback_ends_at="07:00",
        learning_grace_active=True,
        learning_grace_until="2024-01-15T11:00:00+00:00",
    )

    # Priority: contact_open > night_setback > learning_grace
    assert len(overrides) == 3
    assert overrides[0]["type"] == "contact_open"
    assert overrides[1]["type"] == "night_setback"
    assert overrides[2]["type"] == "learning_grace"


def test_build_overrides_preheating_before_night_setback():
    """Preheating should come before night_setback in priority."""
    from custom_components.adaptive_climate.managers.status_manager import build_overrides

    overrides = build_overrides(
        preheating_active=True,
        preheating_target_time="07:00",
        preheating_started_at="2024-01-15T05:30:00+00:00",
        preheating_target_delta=2.0,
        night_setback_active=True,
        night_setback_delta=-2.0,
        night_setback_ends_at="07:00",
    )

    assert len(overrides) == 2
    assert overrides[0]["type"] == "preheating"
    assert overrides[1]["type"] == "night_setback"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py::test_build_overrides_empty_when_no_conditions -v`
Expected: FAIL with ImportError

**Step 3: Write implementation**

Add to `status_manager.py`:

```python
def build_overrides(
    *,
    # Contact open
    contact_open: bool = False,
    contact_sensors: list[str] | None = None,
    contact_since: str | None = None,
    # Humidity
    humidity_active: bool = False,
    humidity_state: str | None = None,
    humidity_resume_at: str | None = None,
    # Open window
    open_window_active: bool = False,
    open_window_since: str | None = None,
    open_window_resume_at: str | None = None,
    # Preheating
    preheating_active: bool = False,
    preheating_target_time: str | None = None,
    preheating_started_at: str | None = None,
    preheating_target_delta: float | None = None,
    # Night setback
    night_setback_active: bool = False,
    night_setback_delta: float | None = None,
    night_setback_ends_at: str | None = None,
    night_setback_limited_to: float | None = None,
    # Learning grace
    learning_grace_active: bool = False,
    learning_grace_until: str | None = None,
) -> list[dict[str, Any]]:
    """Build priority-ordered list of active overrides.

    Priority order (highest first):
    1. contact_open
    2. humidity
    3. open_window
    4. preheating
    5. night_setback
    6. learning_grace

    Returns:
        List of override dicts, ordered by priority
    """
    overrides: list[dict[str, Any]] = []

    # 1. Contact open (highest priority)
    if contact_open:
        overrides.append(build_override(
            OverrideType.CONTACT_OPEN,
            sensors=contact_sensors,
            since=contact_since,
        ))

    # 2. Humidity
    if humidity_active:
        overrides.append(build_override(
            OverrideType.HUMIDITY,
            state=humidity_state,
            resume_at=humidity_resume_at,
        ))

    # 3. Open window
    if open_window_active:
        overrides.append(build_override(
            OverrideType.OPEN_WINDOW,
            since=open_window_since,
            resume_at=open_window_resume_at,
        ))

    # 4. Preheating
    if preheating_active:
        overrides.append(build_override(
            OverrideType.PREHEATING,
            target_time=preheating_target_time,
            started_at=preheating_started_at,
            target_delta=preheating_target_delta,
        ))

    # 5. Night setback
    if night_setback_active:
        overrides.append(build_override(
            OverrideType.NIGHT_SETBACK,
            delta=night_setback_delta,
            ends_at=night_setback_ends_at,
            limited_to=night_setback_limited_to,
        ))

    # 6. Learning grace (lowest priority)
    if learning_grace_active:
        overrides.append(build_override(
            OverrideType.LEARNING_GRACE,
            until=learning_grace_until,
        ))

    return overrides
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py -k "test_build_overrides" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/status_manager.py tests/test_status_manager.py
git commit -m "feat: add build_overrides function for priority-ordered override list"
```

---

## Task 4: Refactor StatusManager.build_status to Use New Structure

**Files:**
- Modify: `custom_components/adaptive_climate/managers/status_manager.py`

**Step 1: Write failing test**

```python
# tests/test_status_manager.py - add new test

def test_build_status_new_structure():
    """build_status should return activity + overrides structure."""
    from custom_components.adaptive_climate.managers.status_manager import StatusManager

    manager = StatusManager()
    status = manager.build_status(
        hvac_mode="heat",
        heater_on=True,
        contact_open=True,
        contact_sensors=["binary_sensor.window"],
        contact_since="2024-01-15T10:30:00+00:00",
    )

    # New structure
    assert "activity" in status
    assert "overrides" in status
    assert status["activity"] == "heating"
    assert len(status["overrides"]) == 1
    assert status["overrides"][0]["type"] == "contact_open"

    # Old fields should not be present
    assert "state" not in status
    assert "conditions" not in status
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py::test_build_status_new_structure -v`
Expected: FAIL (structure mismatch)

**Step 3: Write implementation**

Update `StatusManager.build_status` and `StatusInfo` TypedDict:

```python
class StatusInfo(TypedDict):
    """Status attribute structure for thermostat entity.

    New structure (v2):
        activity: Current activity (idle|heating|cooling|settling)
        overrides: Priority-ordered list of active overrides
    """
    activity: str
    overrides: list[dict[str, Any]]
```

Update `build_status` method signature and implementation to use `build_overrides` and return new structure.

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py::test_build_status_new_structure -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/status_manager.py tests/test_status_manager.py
git commit -m "refactor: StatusManager.build_status returns activity + overrides"
```

---

## Task 5: Update derive_state to Remove PAUSED/PREHEATING

**Files:**
- Modify: `custom_components/adaptive_climate/managers/status_manager.py`
- Modify: `custom_components/adaptive_climate/const.py`

**Step 1: Write failing test**

```python
# tests/test_status_manager.py - update existing test

def test_derive_state_no_paused_state():
    """derive_state should not return PAUSED - that's now an override."""
    from custom_components.adaptive_climate.managers.status_manager import derive_state
    from custom_components.adaptive_climate.const import ThermostatState

    # Even when paused, activity should reflect what it would be doing
    state = derive_state(
        hvac_mode="heat",
        heater_on=True,
        is_paused=True,  # This should no longer affect activity
    )

    # Activity is heating (the override handles pause)
    assert state == ThermostatState.HEATING


def test_derive_state_no_preheating_state():
    """derive_state should not return PREHEATING - that's now an override."""
    from custom_components.adaptive_climate.managers.status_manager import derive_state
    from custom_components.adaptive_climate.const import ThermostatState

    state = derive_state(
        hvac_mode="heat",
        heater_on=True,
        preheat_active=True,  # This should no longer affect activity
    )

    # Activity is heating (preheating is an override)
    assert state == ThermostatState.HEATING
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py::test_derive_state_no_paused_state -v`
Expected: FAIL (returns PAUSED)

**Step 3: Write implementation**

Update `derive_state` to remove `is_paused` and `preheat_active` checks. Update `ThermostatState` enum to remove `PAUSED` and `PREHEATING`.

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_status_manager.py::test_derive_state_no_paused_state tests/test_status_manager.py::test_derive_state_no_preheating_state -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/status_manager.py custom_components/adaptive_climate/const.py tests/test_status_manager.py
git commit -m "refactor: remove PAUSED/PREHEATING from ThermostatState - now overrides"
```

---

## Task 6: Add Learning Object Builder

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py`

**Step 1: Write failing test**

```python
# tests/test_state_attributes.py - add new test

def test_build_learning_object():
    """Learning object should have status and confidence."""
    from custom_components.adaptive_climate.managers.state_attributes import build_learning_object

    learning = build_learning_object(
        status="stable",
        confidence=45,
    )

    assert learning == {"status": "stable", "confidence": 45}
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_build_learning_object -v`
Expected: FAIL with ImportError

**Step 3: Write implementation**

Add to `state_attributes.py`:

```python
def build_learning_object(status: str, confidence: int) -> dict[str, Any]:
    """Build learning status object.

    Args:
        status: Learning status ("idle"|"collecting"|"stable"|"tuned"|"optimized")
        confidence: Convergence confidence 0-100%

    Returns:
        Dict with status and confidence
    """
    return {"status": status, "confidence": confidence}
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_build_learning_object -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/state_attributes.py tests/test_state_attributes.py
git commit -m "feat: add build_learning_object function"
```

---

## Task 7: Add Debug Object Builder

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py`

**Step 1: Write failing test**

```python
# tests/test_state_attributes.py - add new test

def test_build_debug_object_pwm_group():
    """Debug object should have pwm group."""
    from custom_components.adaptive_climate.managers.state_attributes import build_debug_object

    debug = build_debug_object(
        pwm_duty_accumulator_pct=45.2,
    )

    assert "pwm" in debug
    assert debug["pwm"]["duty_accumulator_pct"] == 45.2


def test_build_debug_object_cycle_group():
    """Debug object should have cycle group."""
    from custom_components.adaptive_climate.managers.state_attributes import build_debug_object

    debug = build_debug_object(
        cycle_state="heating",
        cycle_cycles_collected=4,
        cycle_cycles_required=6,
    )

    assert "cycle" in debug
    assert debug["cycle"]["state"] == "heating"
    assert debug["cycle"]["cycles_collected"] == 4
    assert debug["cycle"]["cycles_required"] == 6


def test_build_debug_object_omits_empty_groups():
    """Debug object should omit groups with no data."""
    from custom_components.adaptive_climate.managers.state_attributes import build_debug_object

    debug = build_debug_object(
        pwm_duty_accumulator_pct=45.2,
        # No cycle data
    )

    assert "pwm" in debug
    assert "cycle" not in debug


def test_build_debug_object_all_groups():
    """Debug object should include all configured groups."""
    from custom_components.adaptive_climate.managers.state_attributes import build_debug_object

    debug = build_debug_object(
        pwm_duty_accumulator_pct=45.2,
        cycle_state="heating",
        cycle_cycles_collected=4,
        cycle_cycles_required=6,
        preheat_heating_rate_learned=0.5,
        preheat_observation_count=3,
        humidity_state="normal",
        humidity_peak=85.2,
        undershoot_thermal_debt=12.5,
        undershoot_consecutive_failures=2,
        undershoot_ki_boost_applied=1.2,
        ke_observations=15,
        ke_current_ke=0.5,
        pid_p_term=1.2,
        pid_i_term=3.5,
        pid_d_term=-0.3,
        pid_e_term=0.8,
        pid_f_term=0.0,
    )

    assert "pwm" in debug
    assert "cycle" in debug
    assert "preheat" in debug
    assert "humidity" in debug
    assert "undershoot" in debug
    assert "ke" in debug
    assert "pid" in debug
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_build_debug_object_pwm_group -v`
Expected: FAIL with ImportError

**Step 3: Write implementation**

Add to `state_attributes.py`:

```python
def build_debug_object(**kwargs) -> dict[str, Any]:
    """Build debug object grouped by feature.

    Groups:
    - pwm: duty_accumulator_pct
    - cycle: state, cycles_collected, cycles_required
    - preheat: heating_rate_learned, observation_count
    - humidity: state, peak
    - undershoot: thermal_debt, consecutive_failures, ki_boost_applied
    - ke: observations, current_ke
    - pid: p_term, i_term, d_term, e_term, f_term

    Args:
        **kwargs: Prefixed args like pwm_duty_accumulator_pct, cycle_state, etc.

    Returns:
        Dict with feature groups, empty groups omitted
    """
    debug: dict[str, Any] = {}

    # PWM group
    pwm = {}
    if kwargs.get("pwm_duty_accumulator_pct") is not None:
        pwm["duty_accumulator_pct"] = kwargs["pwm_duty_accumulator_pct"]
    if pwm:
        debug["pwm"] = pwm

    # Cycle group
    cycle = {}
    if kwargs.get("cycle_state") is not None:
        cycle["state"] = kwargs["cycle_state"]
    if kwargs.get("cycle_cycles_collected") is not None:
        cycle["cycles_collected"] = kwargs["cycle_cycles_collected"]
    if kwargs.get("cycle_cycles_required") is not None:
        cycle["cycles_required"] = kwargs["cycle_cycles_required"]
    if cycle:
        debug["cycle"] = cycle

    # Preheat group
    preheat = {}
    if kwargs.get("preheat_heating_rate_learned") is not None:
        preheat["heating_rate_learned"] = kwargs["preheat_heating_rate_learned"]
    if kwargs.get("preheat_observation_count") is not None:
        preheat["observation_count"] = kwargs["preheat_observation_count"]
    if preheat:
        debug["preheat"] = preheat

    # Humidity group
    humidity = {}
    if kwargs.get("humidity_state") is not None:
        humidity["state"] = kwargs["humidity_state"]
    if kwargs.get("humidity_peak") is not None:
        humidity["peak"] = kwargs["humidity_peak"]
    if humidity:
        debug["humidity"] = humidity

    # Undershoot group
    undershoot = {}
    if kwargs.get("undershoot_thermal_debt") is not None:
        undershoot["thermal_debt"] = kwargs["undershoot_thermal_debt"]
    if kwargs.get("undershoot_consecutive_failures") is not None:
        undershoot["consecutive_failures"] = kwargs["undershoot_consecutive_failures"]
    if kwargs.get("undershoot_ki_boost_applied") is not None:
        undershoot["ki_boost_applied"] = kwargs["undershoot_ki_boost_applied"]
    if undershoot:
        debug["undershoot"] = undershoot

    # Ke group
    ke = {}
    if kwargs.get("ke_observations") is not None:
        ke["observations"] = kwargs["ke_observations"]
    if kwargs.get("ke_current_ke") is not None:
        ke["current_ke"] = kwargs["ke_current_ke"]
    if ke:
        debug["ke"] = ke

    # PID group
    pid = {}
    if kwargs.get("pid_p_term") is not None:
        pid["p_term"] = kwargs["pid_p_term"]
    if kwargs.get("pid_i_term") is not None:
        pid["i_term"] = kwargs["pid_i_term"]
    if kwargs.get("pid_d_term") is not None:
        pid["d_term"] = kwargs["pid_d_term"]
    if kwargs.get("pid_e_term") is not None:
        pid["e_term"] = kwargs["pid_e_term"]
    if kwargs.get("pid_f_term") is not None:
        pid["f_term"] = kwargs["pid_f_term"]
    if pid:
        debug["pid"] = pid

    return debug
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py -k "test_build_debug_object" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/state_attributes.py tests/test_state_attributes.py
git commit -m "feat: add build_debug_object function with feature grouping"
```

---

## Task 8: Add Unified cycle_count Field

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py`

**Step 1: Write failing test**

```python
# tests/test_state_attributes.py - add new test

def test_build_cycle_count_heater_cooler():
    """cycle_count should be object when heater/cooler configured."""
    from custom_components.adaptive_climate.managers.state_attributes import build_cycle_count

    result = build_cycle_count(
        heater_count=42,
        cooler_count=10,
        is_demand_switch=False,
    )

    assert result == {"heater": 42, "cooler": 10}


def test_build_cycle_count_demand_switch():
    """cycle_count should be int when demand_switch configured."""
    from custom_components.adaptive_climate.managers.state_attributes import build_cycle_count

    result = build_cycle_count(
        heater_count=52,
        cooler_count=0,
        is_demand_switch=True,
    )

    assert result == 52
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_build_cycle_count_heater_cooler -v`
Expected: FAIL with ImportError

**Step 3: Write implementation**

Add to `state_attributes.py`:

```python
def build_cycle_count(
    heater_count: int,
    cooler_count: int,
    is_demand_switch: bool,
) -> int | dict[str, int]:
    """Build cycle_count field based on configuration.

    Args:
        heater_count: Number of heater cycles
        cooler_count: Number of cooler cycles
        is_demand_switch: True if using demand_switch (single actuator)

    Returns:
        Single int for demand_switch, dict for heater/cooler
    """
    if is_demand_switch:
        return heater_count  # demand_switch only uses heater_count internally
    return {"heater": heater_count, "cooler": cooler_count}
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py -k "test_build_cycle_count" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/state_attributes.py tests/test_state_attributes.py
git commit -m "feat: add build_cycle_count for unified cycle count field"
```

---

## Task 9: Refactor build_state_attributes to New Structure

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py`

**Step 1: Write failing test**

```python
# tests/test_state_attributes.py - add integration test

def test_build_state_attributes_new_structure(mock_thermostat):
    """build_state_attributes should return new grouped structure."""
    from custom_components.adaptive_climate.managers.state_attributes import build_state_attributes

    attrs = build_state_attributes(mock_thermostat)

    # Flat restoration fields
    assert "integral" in attrs
    assert "pid_history" in attrs
    assert "outdoor_temp_lagged" in attrs
    assert "cycle_count" in attrs
    assert "control_output" in attrs

    # Grouped objects
    assert "status" in attrs
    assert "activity" in attrs["status"]
    assert "overrides" in attrs["status"]

    assert "learning" in attrs
    assert "status" in attrs["learning"]
    assert "confidence" in attrs["learning"]

    # Old fields should not be present
    assert "heater_cycle_count" not in attrs
    assert "cooler_cycle_count" not in attrs
    assert "learning_status" not in attrs
    assert "state" not in attrs.get("status", {})
    assert "conditions" not in attrs.get("status", {})
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_build_state_attributes_new_structure -v`
Expected: FAIL (old structure returned)

**Step 3: Write implementation**

Refactor `build_state_attributes` to produce new structure using the builder functions created in previous tasks.

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py::test_build_state_attributes_new_structure -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/state_attributes.py tests/test_state_attributes.py
git commit -m "refactor: build_state_attributes uses new grouped structure"
```

---

## Task 10: Update _build_status_attribute for New Structure

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py`

**Step 1: Refactor _build_status_attribute**

Update to collect override data and call `StatusManager.build_status` with new signature.

**Step 2: Run all status tests**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py tests/test_status_manager.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add custom_components/adaptive_climate/managers/state_attributes.py
git commit -m "refactor: _build_status_attribute uses overrides structure"
```

---

## Task 11: Update StateRestorer for New Structure

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_restorer.py`

**Step 1: Write failing test**

```python
# tests/test_state_restorer.py - add new test

def test_restore_cycle_count_from_new_structure(mock_thermostat, mock_state):
    """StateRestorer should handle new cycle_count structure."""
    from custom_components.adaptive_climate.managers.state_restorer import StateRestorer

    # New structure with dict cycle_count
    mock_state.attributes = {
        "cycle_count": {"heater": 42, "cooler": 10},
        "integral": 5.0,
    }

    restorer = StateRestorer(mock_thermostat)
    restorer.restore(mock_state)

    assert mock_thermostat._heater_controller.heater_cycle_count == 42
    assert mock_thermostat._heater_controller.cooler_cycle_count == 10


def test_restore_cycle_count_from_int(mock_thermostat, mock_state):
    """StateRestorer should handle int cycle_count (demand_switch)."""
    from custom_components.adaptive_climate.managers.state_restorer import StateRestorer

    mock_state.attributes = {
        "cycle_count": 52,
        "integral": 5.0,
    }

    restorer = StateRestorer(mock_thermostat)
    restorer.restore(mock_state)

    assert mock_thermostat._heater_controller.heater_cycle_count == 52
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_restorer.py::test_restore_cycle_count_from_new_structure -v`
Expected: FAIL

**Step 3: Write implementation**

Update `StateRestorer._restore_pid_values` to handle both old (`heater_cycle_count`) and new (`cycle_count`) formats.

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_restorer.py -k "cycle_count" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/state_restorer.py tests/test_state_restorer.py
git commit -m "feat: StateRestorer handles new cycle_count structure"
```

---

## Task 12: Update Existing Tests

**Files:**
- Modify: `tests/test_state_attributes.py`
- Modify: `tests/test_status_manager.py`

**Step 1: Update all tests referencing old structure**

Search for tests using `state`, `conditions`, `heater_cycle_count`, `cooler_cycle_count`, `learning_status` and update to new structure.

**Step 2: Run full test suite**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest tests/test_state_attributes.py tests/test_status_manager.py tests/test_state_restorer.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/
git commit -m "test: update tests for new state attributes structure"
```

---

## Task 13: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update State Attributes section**

Replace old structure documentation with new structure.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for new state attributes structure"
```

---

## Task 14: Run Full Test Suite

**Step 1: Run all tests**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/state-attrs-refactor && pytest --tb=short`
Expected: All tests pass

**Step 2: Fix any failures**

Address any remaining test failures from other test files that depend on state attributes.

**Step 3: Final commit**

```bash
git add -A
git commit -m "fix: address remaining test failures from state attributes refactor"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Add OverrideType enum |
| 2 | Create build_override function |
| 3 | Create build_overrides function |
| 4 | Refactor StatusManager.build_status |
| 5 | Update derive_state (remove PAUSED/PREHEATING) |
| 6 | Add build_learning_object |
| 7 | Add build_debug_object |
| 8 | Add build_cycle_count |
| 9 | Refactor build_state_attributes |
| 10 | Update _build_status_attribute |
| 11 | Update StateRestorer |
| 12 | Update existing tests |
| 13 | Update CLAUDE.md |
| 14 | Run full test suite |
