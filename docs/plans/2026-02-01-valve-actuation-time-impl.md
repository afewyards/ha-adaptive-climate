# Valve Actuation Time Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add configurable valve actuation delay with PWM timing compensation and committed heat tracking.

**Architecture:** Entity-level config with heating-type defaults. New HeatPipeline class tracks in-flight heat. PWMController adjusts timing. CycleTracker uses actual heat delivery window. Learning splits controllable vs committed overshoot.

**Tech Stack:** Home Assistant custom component, Python 3.11+, pytest

**Design doc:** `docs/plans/2026-02-01-valve-actuation-time-design.md`

---

## Task 1: Add Constants and Config Schema

**Files:**
- Modify: `custom_components/adaptive_climate/const.py`
- Modify: `custom_components/adaptive_climate/climate_setup.py`
- Test: `tests/test_climate_setup.py`

**Step 1: Add valve actuation defaults to const.py**

In `const.py`, after `HEATING_TYPE_CHARACTERISTICS` dict (~line 264), add:

```python
CONF_VALVE_ACTUATION_TIME = "valve_actuation_time"

# Valve actuation time defaults by heating type (seconds)
HEATING_TYPE_VALVE_DEFAULTS: dict[HeatingType, int] = {
    HeatingType.FLOOR_HYDRONIC: 120,
    HeatingType.RADIATOR: 90,
    HeatingType.CONVECTOR: 0,
    HeatingType.FORCED_AIR: 30,
}
```

**Step 2: Add schema entry in climate_setup.py**

In `PLATFORM_SCHEMA` (~line 76, near other optional configs):

```python
vol.Optional(const.CONF_VALVE_ACTUATION_TIME): vol.All(
    cv.time_period, cv.positive_timedelta
),
```

**Step 3: Pass config to thermostat parameters**

In `async_setup_platform()`, parameters dict (~line 290):

```python
# Get valve actuation time from config or heating type default
valve_actuation_config = config.get(const.CONF_VALVE_ACTUATION_TIME)
if valve_actuation_config is not None:
    valve_actuation_seconds = valve_actuation_config.total_seconds()
else:
    valve_actuation_seconds = const.HEATING_TYPE_VALVE_DEFAULTS.get(
        heating_type, 0
    )

parameters["valve_actuation_time"] = valve_actuation_seconds
```

**Step 4: Write test for config parsing**

```python
# tests/test_climate_setup.py

async def test_valve_actuation_time_from_config(hass):
    """Test valve_actuation_time parsed from entity config."""
    config = {
        "platform": "adaptive_climate",
        "name": "Test",
        "heater": "switch.heater",
        "target_sensor": "sensor.temp",
        "valve_actuation_time": {"seconds": 150},
    }
    # Validate schema accepts it
    validated = PLATFORM_SCHEMA({"climate": [config]})
    assert validated["climate"][0]["valve_actuation_time"].total_seconds() == 150


async def test_valve_actuation_time_defaults_by_heating_type(hass):
    """Test valve_actuation_time defaults from heating type."""
    from custom_components.adaptive_climate.const import (
        HEATING_TYPE_VALVE_DEFAULTS,
        HeatingType,
    )

    assert HEATING_TYPE_VALVE_DEFAULTS[HeatingType.FLOOR_HYDRONIC] == 120
    assert HEATING_TYPE_VALVE_DEFAULTS[HeatingType.RADIATOR] == 90
    assert HEATING_TYPE_VALVE_DEFAULTS[HeatingType.CONVECTOR] == 0
    assert HEATING_TYPE_VALVE_DEFAULTS[HeatingType.FORCED_AIR] == 30
```

**Step 5: Run tests**

```bash
pytest tests/test_climate_setup.py -v -k valve_actuation
```

**Step 6: Commit**

```bash
git add custom_components/adaptive_climate/const.py \
        custom_components/adaptive_climate/climate_setup.py \
        tests/test_climate_setup.py
git commit -m "feat: add valve_actuation_time config with heating type defaults"
```

---

## Task 2: Create HeatPipeline Class

**Files:**
- Create: `custom_components/adaptive_climate/managers/heat_pipeline.py`
- Test: `tests/managers/test_heat_pipeline.py`

**Step 1: Write failing tests for HeatPipeline**

```python
# tests/managers/test_heat_pipeline.py

import pytest
from custom_components.adaptive_climate.managers.heat_pipeline import HeatPipeline


class TestHeatPipeline:
    """Tests for committed heat tracking."""

    def test_no_committed_heat_when_valve_closed(self):
        """No committed heat when valve never opened."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        assert pipeline.committed_heat_remaining(now=1000.0) == 0.0

    def test_committed_heat_while_valve_opening(self):
        """Committed heat accumulates as valve opens."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)

        # 60s in: pipe filling, 60s of heat in transit
        assert pipeline.committed_heat_remaining(now=60.0) == 60.0

        # 120s in: valve fully open, still only 120s in pipe (< transport_delay)
        assert pipeline.committed_heat_remaining(now=120.0) == 120.0

    def test_committed_heat_caps_at_transport_delay(self):
        """Committed heat maxes at transport_delay."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)

        # 1000s in: pipe full, capped at transport_delay
        assert pipeline.committed_heat_remaining(now=1000.0) == 600.0

    def test_committed_heat_drains_after_close(self):
        """Committed heat drains after valve closes."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        pipeline.valve_closed(at=700.0)  # Half-close point

        # Immediately after close: full transport_delay in pipe
        assert pipeline.committed_heat_remaining(now=700.0) == 600.0

        # 300s after close: half drained
        assert pipeline.committed_heat_remaining(now=1000.0) == 300.0

        # 600s after close: fully drained
        assert pipeline.committed_heat_remaining(now=1300.0) == 0.0

    def test_committed_heat_no_negative(self):
        """Committed heat never goes negative."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        pipeline.valve_closed(at=100.0)

        # Way after close
        assert pipeline.committed_heat_remaining(now=10000.0) == 0.0

    def test_valve_open_duration_calculation(self):
        """Calculate how long to keep valve open for target duty."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)

        # 50% duty, 900s period, no committed heat
        duration = pipeline.calculate_valve_open_duration(
            requested_duty=0.5,
            pwm_period=900.0,
            committed=0.0,
        )
        # Need 450s heat + 60s half-valve = 510s
        assert duration == 510.0

    def test_valve_open_duration_with_committed(self):
        """Committed heat reduces needed valve open time."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)

        # 50% duty, 900s period, 200s already committed
        duration = pipeline.calculate_valve_open_duration(
            requested_duty=0.5,
            pwm_period=900.0,
            committed=200.0,
        )
        # Need (450-200)=250s heat + 60s half-valve = 310s
        assert duration == 310.0

    def test_valve_open_duration_zero_when_committed_exceeds(self):
        """No valve open needed if committed heat exceeds request."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)

        duration = pipeline.calculate_valve_open_duration(
            requested_duty=0.3,
            pwm_period=900.0,
            committed=400.0,  # More than 270s needed
        )
        assert duration == 0.0

    def test_reset_clears_state(self):
        """Reset clears valve timing state."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        pipeline.reset()

        assert pipeline.committed_heat_remaining(now=100.0) == 0.0
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/managers/test_heat_pipeline.py -v
```

Expected: ModuleNotFoundError

**Step 3: Implement HeatPipeline**

```python
# custom_components/adaptive_climate/managers/heat_pipeline.py

"""Track committed heat in hydronic pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HeatPipeline:
    """Track heat in-flight through manifold pipes.

    Attributes:
        transport_delay: Time for water to travel manifold to zone (seconds)
        valve_time: Time for valve to fully open/close (seconds)
    """

    transport_delay: float
    valve_time: float
    _valve_opened_at: float | None = field(default=None, repr=False)
    _valve_closed_at: float | None = field(default=None, repr=False)

    def valve_opened(self, at: float) -> None:
        """Record valve open time (monotonic)."""
        self._valve_opened_at = at
        self._valve_closed_at = None

    def valve_closed(self, at: float) -> None:
        """Record valve half-close time (monotonic)."""
        self._valve_closed_at = at

    def reset(self) -> None:
        """Clear valve timing state."""
        self._valve_opened_at = None
        self._valve_closed_at = None

    def committed_heat_remaining(self, now: float) -> float:
        """Calculate seconds of heat still in-flight.

        Args:
            now: Current monotonic time

        Returns:
            Seconds of heat delivery remaining in pipes
        """
        if self._valve_opened_at is None:
            return 0.0

        if self._valve_closed_at is None:
            # Valve still open - pipe filling or full
            time_open = now - self._valve_opened_at
            return min(time_open, self.transport_delay)

        # Valve closed - pipe draining
        time_since_close = now - self._valve_closed_at
        remaining = self.transport_delay - time_since_close
        return max(0.0, remaining)

    def calculate_valve_open_duration(
        self,
        requested_duty: float,
        pwm_period: float,
        committed: float,
    ) -> float:
        """Calculate how long to keep valve open this cycle.

        Args:
            requested_duty: Target duty cycle 0.0-1.0
            pwm_period: PWM period in seconds
            committed: Seconds of heat already in-flight

        Returns:
            Seconds to keep valve open (0 if committed exceeds need)
        """
        desired_heat = requested_duty * pwm_period
        needed_heat = desired_heat - committed

        if needed_heat <= 0:
            return 0.0

        # Add half valve time for close delay
        return needed_heat + (self.valve_time / 2)
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/managers/test_heat_pipeline.py -v
```

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/heat_pipeline.py \
        tests/managers/test_heat_pipeline.py
git commit -m "feat: add HeatPipeline for committed heat tracking"
```

---

## Task 3: Integrate HeatPipeline with PWMController

**Files:**
- Modify: `custom_components/adaptive_climate/managers/pwm_controller.py`
- Test: `tests/managers/test_pwm_controller.py`

**Step 1: Write failing test for valve timing in PWM**

```python
# Add to tests/managers/test_pwm_controller.py

async def test_pwm_adjusts_for_valve_actuation_time(hass, mock_thermostat):
    """PWM timing accounts for valve actuation delay."""
    controller = PWMController(
        thermostat=mock_thermostat,
        pwm=900,  # 15 min
        difference=100,
        min_on_cycle_duration=0,
        min_off_cycle_duration=0,
        valve_actuation_time=120,  # 2 min valve
    )

    # 50% duty should produce extended on-time to account for valve
    # Desired heat: 450s, plus half-valve-time: 450 + 60 = 510s on-command
    time_on = controller.calculate_adjusted_on_time(
        control_output=50,
        difference=100,
    )

    assert time_on == 510


async def test_pwm_close_command_timing(hass, mock_thermostat):
    """Valve close command sent early to account for half-valve delay."""
    controller = PWMController(
        thermostat=mock_thermostat,
        pwm=900,
        difference=100,
        min_on_cycle_duration=0,
        min_off_cycle_duration=0,
        valve_actuation_time=120,
    )

    # Close should happen at: on_time - half_valve_time before heat delivery should end
    close_offset = controller.get_close_command_offset()
    assert close_offset == 60  # half of 120s
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/managers/test_pwm_controller.py -v -k valve_actuation
```

**Step 3: Add valve_actuation_time to PWMController**

In `pwm_controller.py`, modify `__init__` (~line 45):

```python
def __init__(
    self,
    thermostat: ThermostatState,
    pwm: int,
    difference: float,
    min_on_cycle_duration: float,
    min_off_cycle_duration: float,
    valve_actuation_time: float = 0.0,  # NEW
) -> None:
    self._thermostat = thermostat
    self._pwm = pwm
    self._difference = difference
    self._min_on_cycle_duration = min_on_cycle_duration
    self._min_off_cycle_duration = min_off_cycle_duration
    self._valve_actuation_time = valve_actuation_time  # NEW
    # ... rest unchanged
```

Add new methods:

```python
def calculate_adjusted_on_time(
    self,
    control_output: float,
    difference: float,
) -> float:
    """Calculate valve-on duration accounting for actuation delay.

    Args:
        control_output: PID output (0-100 typically)
        difference: Output range (max - min)

    Returns:
        Seconds to keep valve commanded open
    """
    if difference == 0:
        return 0.0

    # Base heat delivery time from duty cycle
    duty = control_output / difference
    heat_duration = self._pwm * duty

    # Add half valve time to account for close delay
    return heat_duration + (self._valve_actuation_time / 2)

def get_close_command_offset(self) -> float:
    """Get offset in seconds to send close command early.

    Returns:
        Seconds before desired heat-end to send close command
    """
    return self._valve_actuation_time / 2
```

**Step 4: Modify async_pwm_switch to use adjusted timing**

In `async_pwm_switch()` (~line 162), replace:

```python
time_on = self._pwm * (control_output / difference)
```

With:

```python
time_on = self.calculate_adjusted_on_time(control_output, difference)
```

**Step 5: Run tests**

```bash
pytest tests/managers/test_pwm_controller.py -v
```

**Step 6: Commit**

```bash
git add custom_components/adaptive_climate/managers/pwm_controller.py \
        tests/managers/test_pwm_controller.py
git commit -m "feat: integrate valve actuation time into PWM timing"
```

---

## Task 4: Update Demand Signaling Timing

**Files:**
- Modify: `custom_components/adaptive_climate/managers/heater_controller.py`
- Test: `tests/managers/test_heater_controller.py`

**Step 1: Write failing test for delayed demand signaling**

```python
# Add to tests/managers/test_heater_controller.py

async def test_demand_signaled_after_valve_opens(hass, mock_thermostat):
    """Demand signal waits for valve to fully open."""
    controller = HeaterController(
        thermostat=mock_thermostat,
        # ... other params
        valve_actuation_time=120,
    )

    events = []

    @callback
    def capture_event(event):
        events.append(event)

    async_dispatcher_connect(hass, SIGNAL_HEATING_STARTED, capture_event)

    # Turn on heater
    await controller.async_turn_on(HVACMode.HEAT)

    # Immediately: valve commanded, but no heating event yet
    assert len(events) == 0

    # After valve_actuation_time: heating event fires
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=120))
    await hass.async_block_till_done()

    assert len(events) == 1
    assert isinstance(events[0], HeatingStartedEvent)


async def test_demand_removed_at_half_valve_close(hass, mock_thermostat):
    """Demand removed when valve half-closed."""
    controller = HeaterController(
        thermostat=mock_thermostat,
        valve_actuation_time=120,
    )

    events = []

    @callback
    def capture_event(event):
        events.append(event)

    async_dispatcher_connect(hass, SIGNAL_HEATING_ENDED, capture_event)

    # Start heating (skip to after valve open)
    await controller.async_turn_on(HVACMode.HEAT)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=120))
    await hass.async_block_till_done()

    # Turn off heater
    await controller.async_turn_off(HVACMode.HEAT)

    # Immediately: no event yet
    heating_ended_events = [e for e in events if isinstance(e, HeatingEndedEvent)]
    assert len(heating_ended_events) == 0

    # After half valve time: heating ended event fires
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()

    heating_ended_events = [e for e in events if isinstance(e, HeatingEndedEvent)]
    assert len(heating_ended_events) == 1
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/managers/test_heater_controller.py -v -k demand
```

**Step 3: Implement delayed demand signaling**

In `heater_controller.py`, add valve timing:

```python
def __init__(
    self,
    # ... existing params
    valve_actuation_time: float = 0.0,  # NEW
) -> None:
    # ... existing init
    self._valve_actuation_time = valve_actuation_time
    self._valve_open_timer: asyncio.TimerHandle | None = None
    self._valve_close_timer: asyncio.TimerHandle | None = None
```

Modify `async_turn_on()`:

```python
async def async_turn_on(self, hvac_mode: HVACMode) -> None:
    """Turn on heater with valve delay for demand signaling."""
    # Command valve/heater immediately
    await self._async_set_heater_state(True, hvac_mode)

    if self._valve_actuation_time > 0:
        # Schedule demand signal after valve opens
        self._valve_open_timer = async_call_later(
            self._thermostat.hass,
            self._valve_actuation_time,
            self._async_signal_heating_started,
        )
    else:
        # No valve delay, signal immediately
        self._async_signal_heating_started()
```

Modify `async_turn_off()`:

```python
async def async_turn_off(self, hvac_mode: HVACMode) -> None:
    """Turn off heater with half-valve delay for demand removal."""
    # Command valve/heater immediately
    await self._async_set_heater_state(False, hvac_mode)

    # Cancel any pending open timer
    if self._valve_open_timer:
        self._valve_open_timer.cancel()
        self._valve_open_timer = None

    half_valve = self._valve_actuation_time / 2
    if half_valve > 0:
        # Schedule demand removal after half-close
        self._valve_close_timer = async_call_later(
            self._thermostat.hass,
            half_valve,
            self._async_signal_heating_ended,
        )
    else:
        self._async_signal_heating_ended()
```

**Step 4: Run tests**

```bash
pytest tests/managers/test_heater_controller.py -v
```

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/heater_controller.py \
        tests/managers/test_heater_controller.py
git commit -m "feat: delay demand signaling for valve actuation time"
```

---

## Task 5: Adjust Cycle Tracking Start/Stop

**Files:**
- Modify: `custom_components/adaptive_climate/managers/cycle_tracker.py`
- Test: `tests/managers/test_cycle_tracker.py`

**Step 1: Write failing test for adjusted cycle timing**

```python
# Add to tests/managers/test_cycle_tracker.py

async def test_cycle_start_uses_actual_heat_delivery(hass, mock_thermostat):
    """Cycle tracking starts when heat actually arrives, not at valve command."""
    tracker = CycleTrackerManager(
        thermostat=mock_thermostat,
        valve_actuation_time=120,
        transport_delay=300,
    )

    # Fire heating started event (valve fully open)
    await tracker._on_heating_started(HeatingStartedEvent(...))

    # Cycle should start tracking after transport delay
    assert tracker.get_heat_arrival_offset() == 300  # transport only, valve already open
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/managers/test_cycle_tracker.py -v -k actual_heat
```

**Step 3: Implement adjusted tracking**

In `cycle_tracker.py`, add transport awareness to `_on_heating_started()`:

```python
def _on_heating_started(self, event: HeatingStartedEvent) -> None:
    """Handle heating started - valve is now fully open."""
    # Record device on time (valve open, water flowing)
    self._device_on_time = time.monotonic()

    # Heat arrives after transport delay
    if self._transport_delay > 0:
        self._heat_arrival_time = self._device_on_time + self._transport_delay
    else:
        self._heat_arrival_time = self._device_on_time
```

**Step 4: Run tests**

```bash
pytest tests/managers/test_cycle_tracker.py -v
```

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/cycle_tracker.py \
        tests/managers/test_cycle_tracker.py
git commit -m "feat: adjust cycle tracking for transport delay"
```

---

## Task 6: Split Overshoot into Controllable vs Committed

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/cycle_analysis.py`
- Modify: `custom_components/adaptive_climate/adaptive/learning.py`
- Test: `tests/adaptive/test_cycle_analysis.py`

**Step 1: Write failing test for overshoot components**

```python
# Add to tests/adaptive/test_cycle_analysis.py

def test_calculate_overshoot_components():
    """Split overshoot into controllable and committed portions."""
    from custom_components.adaptive_climate.adaptive.cycle_analysis import (
        calculate_overshoot_components,
    )

    # Peak 0.5°C above setpoint, 2 min of committed heat, heating at 0.1°C/min
    controllable, committed = calculate_overshoot_components(
        peak_temp=21.5,
        setpoint=21.0,
        committed_heat_seconds=120,
        heating_rate=0.1 / 60,  # °C per second
    )

    # Committed: 120s × 0.1/60 = 0.2°C
    # Controllable: 0.5 - 0.2 = 0.3°C
    assert committed == pytest.approx(0.2, abs=0.01)
    assert controllable == pytest.approx(0.3, abs=0.01)


def test_calculate_overshoot_components_all_committed():
    """When all overshoot is from committed heat."""
    controllable, committed = calculate_overshoot_components(
        peak_temp=21.2,
        setpoint=21.0,
        committed_heat_seconds=300,
        heating_rate=0.1 / 60,
    )

    # Committed would be 0.5°C but total is only 0.2°C
    assert committed == pytest.approx(0.2, abs=0.01)
    assert controllable == 0.0
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/adaptive/test_cycle_analysis.py -v -k overshoot_components
```

**Step 3: Implement overshoot split**

```python
# In adaptive/cycle_analysis.py

def calculate_overshoot_components(
    peak_temp: float,
    setpoint: float,
    committed_heat_seconds: float,
    heating_rate: float,
) -> tuple[float, float]:
    """Split overshoot into controllable and committed portions.

    Args:
        peak_temp: Maximum temperature reached
        setpoint: Target temperature
        committed_heat_seconds: Heat in-flight when setpoint reached
        heating_rate: Temperature rise rate in °C/second

    Returns:
        Tuple of (controllable_overshoot, committed_overshoot) in °C
    """
    total_overshoot = max(0.0, peak_temp - setpoint)

    if total_overshoot == 0.0:
        return 0.0, 0.0

    committed_overshoot = min(
        committed_heat_seconds * heating_rate,
        total_overshoot,
    )
    controllable_overshoot = total_overshoot - committed_overshoot

    return controllable_overshoot, committed_overshoot
```

**Step 4: Update learning.py to use controllable overshoot only**

In `adaptive/learning.py`, modify the overshoot rule evaluation to use only controllable portion.

**Step 5: Run tests**

```bash
pytest tests/adaptive/test_cycle_analysis.py -v
pytest tests/adaptive/test_learning.py -v
```

**Step 6: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/cycle_analysis.py \
        custom_components/adaptive_climate/adaptive/learning.py \
        tests/adaptive/test_cycle_analysis.py
git commit -m "feat: split overshoot into controllable vs committed"
```

---

## Task 7: Add Rolling Window Heating Rate for Slow Systems

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/physics.py`
- Test: `tests/adaptive/test_physics.py`

**Step 1: Write failing test for rolling window heating rate**

```python
# Add to tests/adaptive/test_physics.py

def test_rolling_window_heating_rate():
    """Calculate heating rate from rolling window of observations."""
    from custom_components.adaptive_climate.adaptive.physics import (
        RollingWindowHeatingRate,
    )

    tracker = RollingWindowHeatingRate(window_seconds=7200)  # 2 hours

    # Add observations: (timestamp, temp_delta, heat_delivered_seconds)
    tracker.add_observation(0, temp_delta=0.1, heat_seconds=60)
    tracker.add_observation(900, temp_delta=0.15, heat_seconds=90)
    tracker.add_observation(1800, temp_delta=0.12, heat_seconds=72)

    # Total: 0.37°C from 222 seconds of heat
    rate = tracker.get_heating_rate()
    assert rate == pytest.approx(0.37 / 222, rel=0.01)  # °C per second


def test_rolling_window_expires_old_observations():
    """Old observations outside window are dropped."""
    tracker = RollingWindowHeatingRate(window_seconds=3600)  # 1 hour

    tracker.add_observation(0, temp_delta=1.0, heat_seconds=600)
    tracker.add_observation(3700, temp_delta=0.1, heat_seconds=60)  # Outside window

    rate = tracker.get_heating_rate()
    # Only second observation counts
    assert rate == pytest.approx(0.1 / 60, rel=0.01)
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/adaptive/test_physics.py -v -k rolling_window
```

**Step 3: Implement RollingWindowHeatingRate**

```python
# In adaptive/physics.py

from collections import deque
from dataclasses import dataclass


@dataclass
class HeatingObservation:
    timestamp: float
    temp_delta: float
    heat_seconds: float


class RollingWindowHeatingRate:
    """Track heating rate over rolling time window.

    For slow systems (floor, radiator) where per-cycle rise time
    isn't meaningful due to thermal lag.
    """

    def __init__(self, window_seconds: float) -> None:
        self._window = window_seconds
        self._observations: deque[HeatingObservation] = deque()

    def add_observation(
        self,
        timestamp: float,
        temp_delta: float,
        heat_seconds: float,
    ) -> None:
        """Add a heating observation."""
        self._observations.append(HeatingObservation(
            timestamp=timestamp,
            temp_delta=temp_delta,
            heat_seconds=heat_seconds,
        ))
        self._prune_old(timestamp)

    def _prune_old(self, now: float) -> None:
        """Remove observations outside window."""
        cutoff = now - self._window
        while self._observations and self._observations[0].timestamp < cutoff:
            self._observations.popleft()

    def get_heating_rate(self) -> float | None:
        """Get heating rate in °C per second.

        Returns None if insufficient observations.
        """
        if not self._observations:
            return None

        total_temp = sum(o.temp_delta for o in self._observations)
        total_heat = sum(o.heat_seconds for o in self._observations)

        if total_heat == 0:
            return None

        return total_temp / total_heat
```

**Step 4: Run tests**

```bash
pytest tests/adaptive/test_physics.py -v
```

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/physics.py \
        tests/adaptive/test_physics.py
git commit -m "feat: add rolling window heating rate for slow systems"
```

---

## Task 8: Wire Up Configuration Through Climate Entity

**Files:**
- Modify: `custom_components/adaptive_climate/climate.py`
- Modify: `custom_components/adaptive_climate/climate_init.py`
- Test: `tests/test_integration_valve_timing.py`

**Step 1: Write integration test**

```python
# tests/test_integration_valve_timing.py

async def test_valve_timing_end_to_end(hass):
    """Integration test: valve timing flows through entire system."""
    # Setup thermostat with valve_actuation_time
    config = {
        "platform": "adaptive_climate",
        "name": "Test Floor",
        "heater": "switch.floor_valve",
        "target_sensor": "sensor.temp",
        "heating_type": "floor_hydronic",
        "valve_actuation_time": {"seconds": 150},
    }

    await setup_thermostat(hass, config)

    thermostat = hass.data[DOMAIN]["entities"]["test_floor"]

    # Verify valve time passed to controllers
    assert thermostat._heater_controller._valve_actuation_time == 150
    assert thermostat._pwm_controller._valve_actuation_time == 150

    # Verify heat pipeline created
    assert thermostat._heat_pipeline is not None
    assert thermostat._heat_pipeline.valve_time == 150
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_integration_valve_timing.py -v
```

**Step 3: Update climate.py and climate_init.py**

In `climate_init.py`, pass valve_actuation_time to managers:

```python
def create_managers(params: dict) -> dict:
    valve_time = params.get("valve_actuation_time", 0.0)
    transport_delay = params.get("transport_delay", 0.0)

    heat_pipeline = HeatPipeline(
        transport_delay=transport_delay,
        valve_time=valve_time,
    ) if valve_time > 0 or transport_delay > 0 else None

    heater_controller = HeaterController(
        # ... existing params
        valve_actuation_time=valve_time,
    )

    pwm_controller = PWMController(
        # ... existing params
        valve_actuation_time=valve_time,
    )

    return {
        "heat_pipeline": heat_pipeline,
        "heater_controller": heater_controller,
        "pwm_controller": pwm_controller,
        # ... other managers
    }
```

**Step 4: Run tests**

```bash
pytest tests/test_integration_valve_timing.py -v
pytest tests/ -v  # Full test suite
```

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/climate.py \
        custom_components/adaptive_climate/climate_init.py \
        tests/test_integration_valve_timing.py
git commit -m "feat: wire valve_actuation_time through climate entity"
```

---

## Task 9: Update Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add valve_actuation_time to CLAUDE.md**

Add new section after Manifolds:

```markdown
### Valve Actuation Time

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
- Demand ON: when valve fully open (not at command)
- Demand OFF: when valve half-closed
- PWM duty adjusted to account for valve travel

**Committed heat tracking:**
When transport delay exists, tracks in-flight heat and subtracts from next cycle's duty calculation.
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add valve_actuation_time to CLAUDE.md"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Constants + config schema | const.py, climate_setup.py |
| 2 | HeatPipeline class | managers/heat_pipeline.py |
| 3 | PWM timing integration | managers/pwm_controller.py |
| 4 | Demand signaling timing | managers/heater_controller.py |
| 5 | Cycle tracking adjustment | managers/cycle_tracker.py |
| 6 | Overshoot split | adaptive/cycle_analysis.py, learning.py |
| 7 | Rolling window heating rate | adaptive/physics.py |
| 8 | Wire through climate entity | climate.py, climate_init.py |
| 9 | Documentation | CLAUDE.md |

**Dependencies:**
- Task 2 (HeatPipeline) before Task 3, 4, 8
- Task 1 (config) before Task 8
- Tasks 3-7 can run in parallel after Task 2
