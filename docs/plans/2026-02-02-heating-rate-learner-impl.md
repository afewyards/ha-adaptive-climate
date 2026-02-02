# HeatingRateLearner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unified heating rate learner that tracks session-level recovery rates, replaces PreheatLearner's internal learning, and enhances undershoot detection.

**Architecture:** HeatingRateLearner lives inside AdaptiveLearner, tracks multi-cycle recovery sessions, bins observations by delta/outdoor temp. Preheat timing and undershoot detection both query the same learned rates.

**Tech Stack:** Python, Home Assistant, pytest, dataclasses

**Design Doc:** `docs/plans/2026-02-02-heating-rate-learner-design.md`

---

## Task 1: HeatingRateLearner Core - Data Models

**Files:**
- Create: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for observation dataclass**

```python
# tests/test_heating_rate_learner.py
"""Tests for HeatingRateLearner."""
import pytest
from datetime import datetime, timezone

from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateObservation,
    RecoverySession,
)


def test_heating_rate_observation_creation():
    """Test observation dataclass stores all fields."""
    obs = HeatingRateObservation(
        rate=0.15,
        duration_min=180.0,
        source="session",
        stalled=False,
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
    )
    assert obs.rate == 0.15
    assert obs.duration_min == 180.0
    assert obs.source == "session"
    assert obs.stalled is False


def test_recovery_session_creation():
    """Test session dataclass stores tracking state."""
    session = RecoverySession(
        start_temp=18.0,
        start_time=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
        target_setpoint=21.0,
        outdoor_temp=5.0,
    )
    assert session.start_temp == 18.0
    assert session.target_setpoint == 21.0
    assert session.cycles_in_session == 0
    assert session.cycle_duties == []
    assert session.last_progress_cycle == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py -v`
Expected: FAIL with "ModuleNotFoundError" or "cannot import name"

**Step 3: Write minimal implementation**

```python
# custom_components/adaptive_climate/adaptive/heating_rate_learner.py
"""Unified heating rate learning from cycle and session data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class HeatingRateObservation:
    """Single heating rate observation."""

    rate: float  # degrees C per hour
    duration_min: float  # session/cycle duration in minutes
    source: str  # "cycle" or "session"
    stalled: bool  # True if ended without reaching setpoint
    timestamp: datetime


@dataclass
class RecoverySession:
    """Tracks an active recovery session spanning multiple cycles."""

    start_temp: float
    start_time: datetime
    target_setpoint: float
    outdoor_temp: float  # snapshot at session start
    cycles_in_session: int = 0
    cycle_duties: list[float] = field(default_factory=list)
    last_progress_cycle: int = 0
    last_temp: float | None = None  # for progress detection
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/heating_rate_learner.py tests/test_heating_rate_learner.py
git commit -m "feat(heating-rate): add observation and session dataclasses"
```

---

## Task 2: HeatingRateLearner Core - Binning Logic

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for bin key calculation**

```python
# Add to tests/test_heating_rate_learner.py
from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateLearner,
)
from custom_components.adaptive_climate.const import HeatingType


class TestBinning:
    """Tests for observation binning."""

    def test_get_bin_key_delta_0_2_cold(self):
        """Test bin key for small delta, cold outdoor."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        key = learner._get_bin_key(delta=1.5, outdoor_temp=3.0)
        assert key == "delta_0_2_cold"

    def test_get_bin_key_delta_4_6_mild(self):
        """Test bin key for medium delta, mild outdoor."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        key = learner._get_bin_key(delta=5.0, outdoor_temp=10.0)
        assert key == "delta_4_6_mild"

    def test_get_bin_key_delta_6_plus_moderate(self):
        """Test bin key for large delta, warm outdoor."""
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        key = learner._get_bin_key(delta=8.0, outdoor_temp=18.0)
        assert key == "delta_6_plus_moderate"

    def test_all_12_bins_exist(self):
        """Test learner initializes all 12 bins."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        assert len(learner._bins) == 12
        assert "delta_0_2_cold" in learner._bins
        assert "delta_6_plus_moderate" in learner._bins
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestBinning -v`
Expected: FAIL with "cannot import name 'HeatingRateLearner'"

**Step 3: Write minimal implementation**

```python
# Add to heating_rate_learner.py after dataclasses

# Bin boundaries
DELTA_BINS = [(0, 2), (2, 4), (4, 6), (6, float("inf"))]
DELTA_BIN_NAMES = ["delta_0_2", "delta_2_4", "delta_4_6", "delta_6_plus"]
OUTDOOR_BINS = [(-float("inf"), 5), (5, 15), (15, float("inf"))]
OUTDOOR_BIN_NAMES = ["cold", "mild", "moderate"]


class HeatingRateLearner:
    """Unified heating rate learning from cycle and session data."""

    MAX_OBSERVATIONS_PER_BIN = 20

    def __init__(self, heating_type: str) -> None:
        """Initialize learner.

        Args:
            heating_type: HeatingType enum value (e.g., "floor_hydronic")
        """
        self._heating_type = heating_type
        self._bins: dict[str, list[HeatingRateObservation]] = {}
        self._active_session: RecoverySession | None = None
        self._stall_counter: int = 0
        self._last_stall_outdoor: float | None = None
        self._last_stall_setpoint: float | None = None

        # Initialize all 12 bins
        for delta_name in DELTA_BIN_NAMES:
            for outdoor_name in OUTDOOR_BIN_NAMES:
                key = f"{delta_name}_{outdoor_name}"
                self._bins[key] = []

    def _get_bin_key(self, delta: float, outdoor_temp: float) -> str:
        """Get bin key for given delta and outdoor temp."""
        # Find delta bin
        delta_name = DELTA_BIN_NAMES[-1]  # default to largest
        for i, (low, high) in enumerate(DELTA_BINS):
            if low <= delta < high:
                delta_name = DELTA_BIN_NAMES[i]
                break

        # Find outdoor bin
        outdoor_name = OUTDOOR_BIN_NAMES[-1]  # default to warmest
        for i, (low, high) in enumerate(OUTDOOR_BINS):
            if low <= outdoor_temp < high:
                outdoor_name = OUTDOOR_BIN_NAMES[i]
                break

        return f"{delta_name}_{outdoor_name}"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestBinning -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add binning logic with 12 bins"
```

---

## Task 3: HeatingRateLearner Core - Add Observation

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for adding observations**

```python
# Add to tests/test_heating_rate_learner.py
class TestAddObservation:
    """Tests for adding observations to bins."""

    def test_add_observation_to_correct_bin(self):
        """Test observation lands in correct bin."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        learner.add_observation(
            rate=0.5,
            duration_min=60.0,
            source="session",
            stalled=False,
            delta=3.0,
            outdoor_temp=8.0,
        )
        assert len(learner._bins["delta_2_4_mild"]) == 1
        assert learner._bins["delta_2_4_mild"][0].rate == 0.5

    def test_max_observations_per_bin(self):
        """Test bin is capped at MAX_OBSERVATIONS_PER_BIN."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        for i in range(25):
            learner.add_observation(
                rate=0.1 * i,
                duration_min=60.0,
                source="session",
                stalled=False,
                delta=1.0,
                outdoor_temp=3.0,
            )
        assert len(learner._bins["delta_0_2_cold"]) == 20
        # Oldest should be dropped, newest kept
        assert learner._bins["delta_0_2_cold"][-1].rate == pytest.approx(2.4)

    def test_get_observation_count(self):
        """Test total observation count across all bins."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        learner.add_observation(rate=0.5, duration_min=60, source="session", stalled=False, delta=1.0, outdoor_temp=3.0)
        learner.add_observation(rate=0.6, duration_min=60, source="session", stalled=False, delta=3.0, outdoor_temp=10.0)
        assert learner.get_observation_count() == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestAddObservation -v`
Expected: FAIL with "has no attribute 'add_observation'"

**Step 3: Write minimal implementation**

```python
# Add to HeatingRateLearner class
    def add_observation(
        self,
        rate: float,
        duration_min: float,
        source: str,
        stalled: bool,
        delta: float,
        outdoor_temp: float,
        timestamp: datetime | None = None,
    ) -> None:
        """Add a heating rate observation to the appropriate bin.

        Args:
            rate: Heating rate in degrees C per hour
            duration_min: Duration of session/cycle in minutes
            source: "session" or "cycle"
            stalled: True if session ended without reaching setpoint
            delta: Temperature delta (setpoint - start_temp)
            outdoor_temp: Outdoor temperature at session start
            timestamp: Observation timestamp (defaults to now)
        """
        from homeassistant.util import dt as dt_util

        if timestamp is None:
            timestamp = dt_util.utcnow()

        obs = HeatingRateObservation(
            rate=rate,
            duration_min=duration_min,
            source=source,
            stalled=stalled,
            timestamp=timestamp,
        )

        bin_key = self._get_bin_key(delta, outdoor_temp)
        self._bins[bin_key].append(obs)

        # Cap at max observations, keep newest
        if len(self._bins[bin_key]) > self.MAX_OBSERVATIONS_PER_BIN:
            self._bins[bin_key] = self._bins[bin_key][-self.MAX_OBSERVATIONS_PER_BIN:]

    def get_observation_count(self) -> int:
        """Get total observation count across all bins."""
        return sum(len(obs_list) for obs_list in self._bins.values())
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestAddObservation -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add observation storage with bin capping"
```

---

## Task 4: HeatingRateLearner Core - Query Interface

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for rate query**

```python
# Add to tests/test_heating_rate_learner.py
class TestGetHeatingRate:
    """Tests for querying learned heating rate."""

    def test_get_rate_from_session_observations(self):
        """Test returns average rate from session observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        # Add 3 session observations
        for rate in [0.4, 0.5, 0.6]:
            learner.add_observation(rate=rate, duration_min=60, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.5, outdoor_temp=10.0)
        assert rate == pytest.approx(0.5)
        assert source == "learned_session"

    def test_get_rate_prefers_session_over_cycle(self):
        """Test session observations preferred over cycle."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        # Add cycle observations
        for rate in [0.3, 0.3, 0.3]:
            learner.add_observation(rate=rate, duration_min=30, source="cycle", stalled=False, delta=3.0, outdoor_temp=8.0)
        # Add session observations
        for rate in [0.5, 0.5, 0.5]:
            learner.add_observation(rate=rate, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert rate == pytest.approx(0.5)
        assert source == "learned_session"

    def test_get_rate_falls_back_to_cycle(self):
        """Test falls back to cycle when <3 session observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        # Add only 2 session observations (not enough)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)
        # Add 3 cycle observations
        for rate in [0.3, 0.3, 0.3]:
            learner.add_observation(rate=rate, duration_min=30, source="cycle", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert rate == pytest.approx(0.3)
        assert source == "learned_cycle"

    def test_get_rate_returns_fallback(self):
        """Test returns fallback when insufficient data."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert source == "fallback"
        assert rate > 0  # Should return some fallback rate

    def test_min_observations_for_learned_rate(self):
        """Test requires 3 observations for learned rate."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)
        learner.add_observation(rate=0.5, duration_min=90, source="session", stalled=False, delta=3.0, outdoor_temp=8.0)

        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert source == "fallback"  # Only 2 observations, not enough
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestGetHeatingRate -v`
Expected: FAIL with "has no attribute 'get_heating_rate'"

**Step 3: Write minimal implementation**

```python
# Add to HeatingRateLearner class

    # Fallback rates by heating type (degrees C per hour)
    FALLBACK_RATES: dict[str, float] = {
        "floor_hydronic": 0.15,
        "radiator": 0.3,
        "convector": 0.6,
        "forced_air": 1.0,
    }

    MIN_OBSERVATIONS_FOR_RATE = 3

    def get_heating_rate(
        self, delta: float, outdoor_temp: float
    ) -> tuple[float, str]:
        """Get heating rate for given conditions.

        Args:
            delta: Temperature delta (setpoint - current_temp)
            outdoor_temp: Current outdoor temperature

        Returns:
            Tuple of (rate in degrees C/hour, source string)
            source: "learned_session", "learned_cycle", or "fallback"
        """
        bin_key = self._get_bin_key(delta, outdoor_temp)
        observations = self._bins[bin_key]

        # Try session observations first (≥3 required)
        session_obs = [o for o in observations if o.source == "session"]
        if len(session_obs) >= self.MIN_OBSERVATIONS_FOR_RATE:
            avg_rate = sum(o.rate for o in session_obs) / len(session_obs)
            return (avg_rate, "learned_session")

        # Try cycle observations (≥3 required)
        cycle_obs = [o for o in observations if o.source == "cycle"]
        if len(cycle_obs) >= self.MIN_OBSERVATIONS_FOR_RATE:
            avg_rate = sum(o.rate for o in cycle_obs) / len(cycle_obs)
            return (avg_rate, "learned_cycle")

        # Fallback to heating type default
        fallback = self.FALLBACK_RATES.get(self._heating_type, 0.3)
        return (fallback, "fallback")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestGetHeatingRate -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add rate query with session/cycle/fallback priority"
```

---

## Task 5: Session Tracking - Start/End Session

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for session lifecycle**

```python
# Add to tests/test_heating_rate_learner.py
from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    MIN_SESSION_DURATION,
)


class TestSessionTracking:
    """Tests for recovery session tracking."""

    def test_start_session_creates_active_session(self):
        """Test start_session creates tracking state."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(
            temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now
        )

        assert learner._active_session is not None
        assert learner._active_session.start_temp == 18.0
        assert learner._active_session.target_setpoint == 21.0
        assert learner._active_session.outdoor_temp == 5.0

    def test_end_session_success_banks_observation(self):
        """Test successful session banks rate observation."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 10, 45, tzinfo=timezone.utc)  # 45 min

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(
            end_temp=20.8, reason="reached_setpoint", timestamp=end
        )

        assert obs is not None
        # Rate = (20.8 - 18.0) / (45/60) = 2.8 / 0.75 = 3.73 C/h
        assert obs.rate == pytest.approx(3.73, rel=0.01)
        assert obs.stalled is False
        assert learner._active_session is None
        assert learner.get_observation_count() == 1

    def test_end_session_stalled_banks_observation(self):
        """Test stalled session banks observation with stalled=True."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)  # 60 min

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=19.5, reason="stalled", timestamp=end)

        assert obs is not None
        assert obs.stalled is True
        # Rate = (19.5 - 18.0) / 1.0 = 1.5 C/h
        assert obs.rate == pytest.approx(1.5)

    def test_end_session_too_short_discards(self):
        """Test session shorter than minimum is discarded."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 10, 15, tzinfo=timezone.utc)  # 15 min (radiator min is 30)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=19.0, reason="reached_setpoint", timestamp=end)

        assert obs is None  # Discarded
        assert learner.get_observation_count() == 0

    def test_end_session_override_discards(self):
        """Test session interrupted by override is discarded."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        obs = learner.end_session(end_temp=19.0, reason="override", timestamp=end)

        assert obs is None
        assert learner.get_observation_count() == 0

    def test_min_session_duration_by_heating_type(self):
        """Test minimum duration varies by heating type."""
        assert MIN_SESSION_DURATION["floor_hydronic"] == 60
        assert MIN_SESSION_DURATION["radiator"] == 30
        assert MIN_SESSION_DURATION["convector"] == 15
        assert MIN_SESSION_DURATION["forced_air"] == 10
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestSessionTracking -v`
Expected: FAIL with "cannot import name 'MIN_SESSION_DURATION'" or "has no attribute 'start_session'"

**Step 3: Write minimal implementation**

```python
# Add constants at module level
MIN_SESSION_DURATION: dict[str, int] = {
    "floor_hydronic": 60,
    "radiator": 30,
    "convector": 15,
    "forced_air": 10,
}

# Add to HeatingRateLearner class
    def start_session(
        self,
        temp: float,
        setpoint: float,
        outdoor_temp: float,
        timestamp: datetime | None = None,
    ) -> None:
        """Start tracking a recovery session.

        Args:
            temp: Current temperature at session start
            setpoint: Target setpoint
            outdoor_temp: Outdoor temperature (snapshot for binning)
            timestamp: Session start time (defaults to now)
        """
        from homeassistant.util import dt as dt_util

        if timestamp is None:
            timestamp = dt_util.utcnow()

        self._active_session = RecoverySession(
            start_temp=temp,
            start_time=timestamp,
            target_setpoint=setpoint,
            outdoor_temp=outdoor_temp,
        )
        self._active_session.last_temp = temp

    def end_session(
        self,
        end_temp: float,
        reason: str,
        timestamp: datetime | None = None,
    ) -> HeatingRateObservation | None:
        """End the current recovery session.

        Args:
            end_temp: Temperature at session end
            reason: Why session ended - "reached_setpoint", "stalled", "override"
            timestamp: Session end time (defaults to now)

        Returns:
            HeatingRateObservation if banked, None if discarded
        """
        from homeassistant.util import dt as dt_util

        if self._active_session is None:
            return None

        if timestamp is None:
            timestamp = dt_util.utcnow()

        session = self._active_session
        self._active_session = None

        # Discard if override interrupted
        if reason == "override":
            return None

        # Calculate duration
        duration_min = (timestamp - session.start_time).total_seconds() / 60.0

        # Discard if too short
        min_duration = MIN_SESSION_DURATION.get(self._heating_type, 30)
        if duration_min < min_duration:
            return None

        # Calculate rate
        temp_rise = end_temp - session.start_temp
        duration_hours = duration_min / 60.0
        rate = temp_rise / duration_hours if duration_hours > 0 else 0.0

        # Determine if stalled
        stalled = reason == "stalled"

        # Bank observation
        delta = session.target_setpoint - session.start_temp
        self.add_observation(
            rate=rate,
            duration_min=duration_min,
            source="session",
            stalled=stalled,
            delta=delta,
            outdoor_temp=session.outdoor_temp,
            timestamp=timestamp,
        )

        return self._bins[self._get_bin_key(delta, session.outdoor_temp)][-1]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestSessionTracking -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add session start/end with min duration check"
```

---

## Task 6: Session Tracking - Update & Stall Detection

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for session updates**

```python
# Add to tests/test_heating_rate_learner.py
class TestSessionUpdates:
    """Tests for session progress tracking."""

    def test_update_session_tracks_progress(self):
        """Test update_session records cycle data."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.5, duty=0.75)

        assert learner._active_session.cycles_in_session == 1
        assert learner._active_session.cycle_duties == [0.75]
        assert learner._active_session.last_temp == 18.5
        assert learner._active_session.last_progress_cycle == 1

    def test_update_session_detects_no_progress(self):
        """Test stall detection when temp doesn't rise."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.05, duty=0.75)  # <0.1 rise = no progress
        learner.update_session(temp=18.08, duty=0.75)  # still no progress
        learner.update_session(temp=18.09, duty=0.75)  # still no progress

        # last_progress_cycle should still be 0 (no progress recorded)
        assert learner._active_session.last_progress_cycle == 0
        assert learner._active_session.cycles_in_session == 3

    def test_is_stalled_after_3_no_progress_cycles(self):
        """Test is_stalled returns True after 3 cycles without progress."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        assert learner.is_stalled() is False

        learner.update_session(temp=18.05, duty=0.75)
        assert learner.is_stalled() is False

        learner.update_session(temp=18.08, duty=0.75)
        assert learner.is_stalled() is False

        learner.update_session(temp=18.09, duty=0.75)
        assert learner.is_stalled() is True  # 3 cycles with no progress

    def test_progress_resets_stall_detection(self):
        """Test making progress resets the stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.05, duty=0.75)  # no progress
        learner.update_session(temp=18.08, duty=0.75)  # no progress
        learner.update_session(temp=18.3, duty=0.75)   # progress! (0.22 rise)

        assert learner._active_session.last_progress_cycle == 3
        assert learner.is_stalled() is False

    def test_get_avg_session_duty(self):
        """Test calculating average duty for session."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=now)
        learner.update_session(temp=18.5, duty=0.70)
        learner.update_session(temp=19.0, duty=0.80)
        learner.update_session(temp=19.5, duty=0.90)

        assert learner.get_avg_session_duty() == pytest.approx(0.80)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestSessionUpdates -v`
Expected: FAIL with "has no attribute 'update_session'"

**Step 3: Write minimal implementation**

```python
# Add to HeatingRateLearner class
    PROGRESS_THRESHOLD = 0.1  # Minimum temp rise to count as progress
    STALL_CYCLES = 3  # Cycles without progress to declare stall

    def update_session(self, temp: float, duty: float) -> None:
        """Update session with cycle completion data.

        Args:
            temp: Current temperature after cycle
            duty: Duty cycle (0.0-1.0) during this cycle
        """
        if self._active_session is None:
            return

        session = self._active_session
        session.cycles_in_session += 1
        session.cycle_duties.append(duty)

        # Check for progress
        if session.last_temp is not None:
            temp_rise = temp - session.last_temp
            if temp_rise >= self.PROGRESS_THRESHOLD:
                session.last_progress_cycle = session.cycles_in_session

        session.last_temp = temp

    def is_stalled(self) -> bool:
        """Check if current session is stalled (no progress for 3 cycles)."""
        if self._active_session is None:
            return False

        session = self._active_session
        cycles_since_progress = session.cycles_in_session - session.last_progress_cycle
        return cycles_since_progress >= self.STALL_CYCLES

    def get_avg_session_duty(self) -> float | None:
        """Get average duty for current session."""
        if self._active_session is None or not self._active_session.cycle_duties:
            return None
        return sum(self._active_session.cycle_duties) / len(self._active_session.cycle_duties)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestSessionUpdates -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add session update with stall detection"
```

---

## Task 7: Stall Counter & Ki Boost Trigger

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for stall counter**

```python
# Add to tests/test_heating_rate_learner.py
class TestStallCounter:
    """Tests for consecutive stall tracking and Ki boost trigger."""

    def test_stall_increments_counter(self):
        """Test stalled session increments stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner._stall_counter == 1

    def test_success_resets_counter(self):
        """Test successful session resets stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        # First session stalls
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)
        assert learner._stall_counter == 1

        # Second session succeeds
        start2 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start2)
        learner.end_session(end_temp=20.8, reason="reached_setpoint", timestamp=end2)

        assert learner._stall_counter == 0

    def test_outdoor_change_resets_counter(self):
        """Test significant outdoor temp change resets stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        # First stall at outdoor=5
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)
        assert learner._stall_counter == 1

        # Second stall at outdoor=-2 (>5 degree change)
        start2 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=-2.0, timestamp=start2)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end2)

        # Counter reset due to outdoor change, then incremented
        assert learner._stall_counter == 1

    def test_setpoint_change_resets_counter(self):
        """Test significant setpoint change resets stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        # First stall at setpoint=21
        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        # Second stall at setpoint=23 (>1 degree change)
        start2 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        learner.start_session(temp=18.0, setpoint=23.0, outdoor_temp=5.0, timestamp=start2)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end2)

        assert learner._stall_counter == 1  # Reset then incremented

    def test_should_boost_ki_after_2_stalls_with_low_duty(self):
        """Test Ki boost triggered after 2 consecutive stalls with headroom."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for i in range(2):
            start = datetime(2026, 1, 15, 10 + i * 2, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 15, 11 + i * 2, 0, tzinfo=timezone.utc)
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
            learner.update_session(temp=18.5, duty=0.60)  # Low duty
            learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner.should_boost_ki() is True

    def test_no_boost_when_high_duty(self):
        """Test no Ki boost when duty is high (capacity limited)."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for i in range(2):
            start = datetime(2026, 1, 15, 10 + i * 2, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 15, 11 + i * 2, 0, tzinfo=timezone.utc)
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
            learner.update_session(temp=18.5, duty=0.90)  # High duty
            learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner.should_boost_ki() is False  # Capacity limited

    def test_acknowledge_boost_resets_counter(self):
        """Test acknowledging Ki boost resets the stall counter."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for i in range(2):
            start = datetime(2026, 1, 15, 10 + i * 2, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 15, 11 + i * 2, 0, tzinfo=timezone.utc)
            learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
            learner.update_session(temp=18.5, duty=0.60)
            learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        assert learner.should_boost_ki() is True
        learner.acknowledge_ki_boost()
        assert learner._stall_counter == 0
        assert learner.should_boost_ki() is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestStallCounter -v`
Expected: FAIL with "has no attribute 'should_boost_ki'"

**Step 3: Write minimal implementation**

```python
# Add to HeatingRateLearner class
    OUTDOOR_RESET_THRESHOLD = 5.0  # degrees C change to reset counter
    SETPOINT_RESET_THRESHOLD = 1.0  # degrees C change to reset counter
    DUTY_CAPACITY_THRESHOLD = 0.85  # above this = capacity limited
    STALLS_FOR_BOOST = 2  # consecutive stalls before Ki boost

    def _check_reset_conditions(self, outdoor_temp: float, setpoint: float) -> None:
        """Check if conditions changed enough to reset stall counter."""
        if self._last_stall_outdoor is not None:
            if abs(outdoor_temp - self._last_stall_outdoor) > self.OUTDOOR_RESET_THRESHOLD:
                self._stall_counter = 0

        if self._last_stall_setpoint is not None:
            if abs(setpoint - self._last_stall_setpoint) > self.SETPOINT_RESET_THRESHOLD:
                self._stall_counter = 0

    def _record_stall(self, outdoor_temp: float, setpoint: float) -> None:
        """Record a stall and update tracking."""
        self._check_reset_conditions(outdoor_temp, setpoint)
        self._stall_counter += 1
        self._last_stall_outdoor = outdoor_temp
        self._last_stall_setpoint = setpoint

    def _record_success(self) -> None:
        """Record a successful session."""
        self._stall_counter = 0
        self._last_stall_outdoor = None
        self._last_stall_setpoint = None

    def should_boost_ki(self) -> bool:
        """Check if Ki should be boosted based on stall pattern.

        Returns True if:
        - 2+ consecutive stalls
        - Average session duty < 85% (system has headroom)
        """
        if self._stall_counter < self.STALLS_FOR_BOOST:
            return False

        # Check if we have duty data from last session
        # This is set during end_session before clearing active_session
        if not hasattr(self, "_last_session_avg_duty"):
            return False

        return self._last_session_avg_duty < self.DUTY_CAPACITY_THRESHOLD

    def acknowledge_ki_boost(self) -> None:
        """Acknowledge that Ki boost was applied, reset counter."""
        self._stall_counter = 0
```

**Step 3b: Update end_session to use these methods**

```python
# Modify end_session method - replace the stalled handling section:
    def end_session(
        self,
        end_temp: float,
        reason: str,
        timestamp: datetime | None = None,
    ) -> HeatingRateObservation | None:
        """End the current recovery session."""
        from homeassistant.util import dt as dt_util

        if self._active_session is None:
            return None

        if timestamp is None:
            timestamp = dt_util.utcnow()

        session = self._active_session
        self._active_session = None

        # Store avg duty before clearing session
        if session.cycle_duties:
            self._last_session_avg_duty = sum(session.cycle_duties) / len(session.cycle_duties)
        else:
            self._last_session_avg_duty = 0.0

        # Discard if override interrupted
        if reason == "override":
            return None

        # Calculate duration
        duration_min = (timestamp - session.start_time).total_seconds() / 60.0

        # Discard if too short
        min_duration = MIN_SESSION_DURATION.get(self._heating_type, 30)
        if duration_min < min_duration:
            return None

        # Calculate rate
        temp_rise = end_temp - session.start_temp
        duration_hours = duration_min / 60.0
        rate = temp_rise / duration_hours if duration_hours > 0 else 0.0

        # Determine if stalled and update counter
        stalled = reason == "stalled"
        if stalled:
            self._record_stall(session.outdoor_temp, session.target_setpoint)
        else:
            self._record_success()

        # Bank observation
        delta = session.target_setpoint - session.start_temp
        self.add_observation(
            rate=rate,
            duration_min=duration_min,
            source="session",
            stalled=stalled,
            delta=delta,
            outdoor_temp=session.outdoor_temp,
            timestamp=timestamp,
        )

        return self._bins[self._get_bin_key(delta, session.outdoor_temp)][-1]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestStallCounter -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add stall counter with Ki boost trigger"
```

---

## Task 8: Rate Comparison for Undershoot Detection

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for rate comparison**

```python
# Add to tests/test_heating_rate_learner.py
class TestRateComparison:
    """Tests for comparing current rate against learned rate."""

    def test_get_rate_ratio_with_sufficient_data(self):
        """Test rate ratio calculation with enough observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Add 5 session observations (enough for comparison)
        for _ in range(5):
            learner.add_observation(
                rate=1.0, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        # Current rate is 0.4, expected is 1.0 -> ratio = 0.4
        ratio = learner.get_rate_ratio(
            current_rate=0.4, delta=3.0, outdoor_temp=8.0
        )
        assert ratio == pytest.approx(0.4)

    def test_get_rate_ratio_insufficient_data(self):
        """Test rate ratio returns None with insufficient observations."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Only 3 observations (need 5 for comparison)
        for _ in range(3):
            learner.add_observation(
                rate=1.0, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        ratio = learner.get_rate_ratio(
            current_rate=0.4, delta=3.0, outdoor_temp=8.0
        )
        assert ratio is None

    def test_is_underperforming_at_60_percent(self):
        """Test underperforming detection at 60% threshold."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        for _ in range(5):
            learner.add_observation(
                rate=1.0, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        # 0.5 is 50% of expected 1.0 -> underperforming
        assert learner.is_underperforming(0.5, delta=3.0, outdoor_temp=8.0) is True

        # 0.7 is 70% of expected 1.0 -> not underperforming
        assert learner.is_underperforming(0.7, delta=3.0, outdoor_temp=8.0) is False

        # 0.6 is exactly 60% -> not underperforming (threshold is <60%)
        assert learner.is_underperforming(0.6, delta=3.0, outdoor_temp=8.0) is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestRateComparison -v`
Expected: FAIL with "has no attribute 'get_rate_ratio'"

**Step 3: Write minimal implementation**

```python
# Add to HeatingRateLearner class
    MIN_OBSERVATIONS_FOR_COMPARISON = 5
    UNDERPERFORMING_THRESHOLD = 0.6  # Below 60% of expected = underperforming

    def get_rate_ratio(
        self, current_rate: float, delta: float, outdoor_temp: float
    ) -> float | None:
        """Get ratio of current rate to expected rate.

        Args:
            current_rate: Current observed heating rate (C/h)
            delta: Temperature delta for binning
            outdoor_temp: Outdoor temp for binning

        Returns:
            Ratio (0.0-1.0+) if sufficient data, None otherwise
        """
        bin_key = self._get_bin_key(delta, outdoor_temp)
        observations = self._bins[bin_key]

        # Need minimum observations for reliable comparison
        session_obs = [o for o in observations if o.source == "session"]
        if len(session_obs) < self.MIN_OBSERVATIONS_FOR_COMPARISON:
            return None

        expected_rate = sum(o.rate for o in session_obs) / len(session_obs)
        if expected_rate <= 0:
            return None

        return current_rate / expected_rate

    def is_underperforming(
        self, current_rate: float, delta: float, outdoor_temp: float
    ) -> bool:
        """Check if current rate is significantly below expected.

        Returns True if rate < 60% of expected AND we have sufficient data.
        """
        ratio = self.get_rate_ratio(current_rate, delta, outdoor_temp)
        if ratio is None:
            return False
        return ratio < self.UNDERPERFORMING_THRESHOLD
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestRateComparison -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add rate comparison for undershoot detection"
```

---

## Task 9: Serialization - to_dict/from_dict

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/heating_rate_learner.py`
- Test: `tests/test_heating_rate_learner.py`

**Step 1: Write failing test for serialization**

```python
# Add to tests/test_heating_rate_learner.py
class TestSerialization:
    """Tests for HeatingRateLearner serialization."""

    def test_to_dict_includes_all_state(self):
        """Test to_dict captures complete state."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Add some observations
        learner.add_observation(
            rate=0.5, duration_min=60, source="session",
            stalled=False, delta=3.0, outdoor_temp=8.0
        )

        data = learner.to_dict()

        assert data["heating_type"] == "radiator"
        assert "bins" in data
        assert "delta_2_4_mild" in data["bins"]
        assert len(data["bins"]["delta_2_4_mild"]) == 1
        assert data["stall_counter"] == 0

    def test_from_dict_restores_state(self):
        """Test from_dict restores complete state."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)

        # Add observations and create state
        for _ in range(3):
            learner.add_observation(
                rate=0.5, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        # Serialize and restore
        data = learner.to_dict()
        restored = HeatingRateLearner.from_dict(data)

        assert restored._heating_type == learner._heating_type
        assert restored.get_observation_count() == learner.get_observation_count()
        assert len(restored._bins["delta_2_4_mild"]) == 3

    def test_observation_serialization(self):
        """Test observation round-trips correctly."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        ts = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

        learner.add_observation(
            rate=0.5, duration_min=60, source="session",
            stalled=True, delta=3.0, outdoor_temp=8.0, timestamp=ts
        )

        data = learner.to_dict()
        restored = HeatingRateLearner.from_dict(data)

        obs = restored._bins["delta_2_4_mild"][0]
        assert obs.rate == 0.5
        assert obs.duration_min == 60
        assert obs.source == "session"
        assert obs.stalled is True

    def test_stall_counter_persists(self):
        """Test stall counter survives serialization."""
        learner = HeatingRateLearner(HeatingType.RADIATOR)
        start = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)

        learner.start_session(temp=18.0, setpoint=21.0, outdoor_temp=5.0, timestamp=start)
        learner.end_session(end_temp=19.0, reason="stalled", timestamp=end)

        data = learner.to_dict()
        restored = HeatingRateLearner.from_dict(data)

        assert restored._stall_counter == 1
        assert restored._last_stall_outdoor == 5.0
        assert restored._last_stall_setpoint == 21.0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_heating_rate_learner.py::TestSerialization -v`
Expected: FAIL with "has no attribute 'to_dict'"

**Step 3: Write minimal implementation**

```python
# Add to HeatingRateLearner class
    def to_dict(self) -> dict:
        """Serialize learner state to dictionary."""
        bins_data = {}
        for key, observations in self._bins.items():
            bins_data[key] = [
                {
                    "rate": obs.rate,
                    "duration_min": obs.duration_min,
                    "source": obs.source,
                    "stalled": obs.stalled,
                    "timestamp": obs.timestamp.isoformat(),
                }
                for obs in observations
            ]

        return {
            "heating_type": self._heating_type,
            "bins": bins_data,
            "stall_counter": self._stall_counter,
            "last_stall_outdoor": self._last_stall_outdoor,
            "last_stall_setpoint": self._last_stall_setpoint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HeatingRateLearner":
        """Restore learner from serialized state."""
        from datetime import datetime

        heating_type = data.get("heating_type", "radiator")
        learner = cls(heating_type)

        # Restore bins
        bins_data = data.get("bins", {})
        for key, obs_list in bins_data.items():
            if key in learner._bins:
                learner._bins[key] = [
                    HeatingRateObservation(
                        rate=obs["rate"],
                        duration_min=obs["duration_min"],
                        source=obs["source"],
                        stalled=obs["stalled"],
                        timestamp=datetime.fromisoformat(obs["timestamp"]),
                    )
                    for obs in obs_list
                ]

        # Restore stall tracking
        learner._stall_counter = data.get("stall_counter", 0)
        learner._last_stall_outdoor = data.get("last_stall_outdoor")
        learner._last_stall_setpoint = data.get("last_stall_setpoint")

        return learner
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_heating_rate_learner.py::TestSerialization -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add serialization to_dict/from_dict"
```

---

## Task 10: Integrate into AdaptiveLearner

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/learning.py`
- Test: `tests/test_learning.py` (add integration test)

**Step 1: Write failing test for integration**

```python
# Add to tests/test_learning.py (find appropriate location)
from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateLearner,
)


class TestHeatingRateLearnerIntegration:
    """Tests for HeatingRateLearner integration with AdaptiveLearner."""

    def test_adaptive_learner_owns_heating_rate_learner(self):
        """Test AdaptiveLearner creates HeatingRateLearner."""
        learner = AdaptiveLearner(heating_type="radiator")
        assert hasattr(learner, "_heating_rate_learner")
        assert isinstance(learner._heating_rate_learner, HeatingRateLearner)

    def test_heating_rate_learner_uses_correct_type(self):
        """Test HeatingRateLearner uses same heating type."""
        learner = AdaptiveLearner(heating_type="floor_hydronic")
        assert learner._heating_rate_learner._heating_type == "floor_hydronic"

    def test_get_heating_rate_delegates(self):
        """Test AdaptiveLearner.get_heating_rate delegates to HeatingRateLearner."""
        learner = AdaptiveLearner(heating_type="radiator")

        # Add observations via heating_rate_learner
        for _ in range(3):
            learner._heating_rate_learner.add_observation(
                rate=0.5, duration_min=60, source="session",
                stalled=False, delta=3.0, outdoor_temp=8.0
            )

        rate, source = learner.get_heating_rate(delta=3.0, outdoor_temp=8.0)
        assert rate == pytest.approx(0.5)
        assert source == "learned_session"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_learning.py::TestHeatingRateLearnerIntegration -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# Modify custom_components/adaptive_climate/adaptive/learning.py

# Add import at top
from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateLearner,
)

# In AdaptiveLearner.__init__, after other manager initialization (around line 150):
        self._heating_rate_learner = HeatingRateLearner(self._heating_type)

# Add delegate method to AdaptiveLearner class:
    def get_heating_rate(
        self, delta: float, outdoor_temp: float
    ) -> tuple[float, str]:
        """Get learned heating rate for given conditions.

        Delegates to HeatingRateLearner.

        Args:
            delta: Temperature delta (setpoint - current)
            outdoor_temp: Current outdoor temperature

        Returns:
            Tuple of (rate in C/hour, source string)
        """
        return self._heating_rate_learner.get_heating_rate(delta, outdoor_temp)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_learning.py::TestHeatingRateLearnerIntegration -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): integrate HeatingRateLearner into AdaptiveLearner"
```

---

## Task 11: Update Serialization to v10

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/learner_serialization.py`
- Test: `tests/test_learner_serialization.py`

**Step 1: Write failing test for v10 migration**

```python
# Add to tests/test_learner_serialization.py
def test_v9_to_v10_migration():
    """Test v9 state migrates to v10 with heating_rate_learner."""
    v9_data = {
        "version": 9,
        "heating_type": "radiator",
        "cycle_history": [],
        "pid_history": [],
        "confidence_tracker": {"total_contribution": 0.0},
        "undershoot_detector": {"thermal_debt": 0.0},
        # No heating_rate_learner in v9
    }

    learner = restore_learner_from_dict(v9_data)

    assert hasattr(learner, "_heating_rate_learner")
    assert learner._heating_rate_learner is not None
    assert learner._heating_rate_learner._heating_type == "radiator"


def test_v10_round_trip():
    """Test v10 state serializes and restores correctly."""
    learner = AdaptiveLearner(heating_type="radiator")

    # Add heating rate data
    learner._heating_rate_learner.add_observation(
        rate=0.5, duration_min=60, source="session",
        stalled=False, delta=3.0, outdoor_temp=8.0
    )

    # Serialize
    data = serialize_learner_to_dict(learner)
    assert data["version"] == 10
    assert "heating_rate_learner" in data

    # Restore
    restored = restore_learner_from_dict(data)
    assert restored._heating_rate_learner.get_observation_count() == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_learner_serialization.py::test_v9_to_v10_migration tests/test_learner_serialization.py::test_v10_round_trip -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# Modify custom_components/adaptive_climate/adaptive/learner_serialization.py

# Update version constant
CURRENT_VERSION = 10  # was 9

# Add import
from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateLearner,
)

# In serialize_learner_to_dict function, add after undershoot_detector serialization:
    if hasattr(learner, "_heating_rate_learner") and learner._heating_rate_learner:
        data["heating_rate_learner"] = learner._heating_rate_learner.to_dict()

# In restore_learner_from_dict function, add restoration logic:
    # Restore heating_rate_learner (v10+)
    if "heating_rate_learner" in data:
        learner._heating_rate_learner = HeatingRateLearner.from_dict(
            data["heating_rate_learner"]
        )
    else:
        # v9 migration: create fresh learner
        learner._heating_rate_learner = HeatingRateLearner(heating_type)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_learner_serialization.py::test_v9_to_v10_migration tests/test_learner_serialization.py::test_v10_round_trip -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): add v10 serialization with migration from v9"
```

---

## Task 12: Update PreheatLearner to Delegate

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/preheat.py`
- Test: `tests/test_preheat_learner.py`

**Step 1: Write failing test for delegation**

```python
# Add to tests/test_preheat_learner.py
def test_preheat_learner_delegates_to_heating_rate_learner():
    """Test PreheatLearner uses HeatingRateLearner when provided."""
    from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
        HeatingRateLearner,
    )

    hr_learner = HeatingRateLearner("radiator")
    # Pre-populate with data
    for _ in range(3):
        hr_learner.add_observation(
            rate=0.6, duration_min=60, source="session",
            stalled=False, delta=3.0, outdoor_temp=8.0
        )

    preheat = PreheatLearner("radiator", heating_rate_learner=hr_learner)

    # estimate_time_to_target should use the learned rate
    time_min = preheat.estimate_time_to_target(
        current_temp=18.0, target_temp=21.0, outdoor_temp=8.0
    )

    # delta=3, rate=0.6 C/h -> time = 3/0.6 = 5 hours = 300 min
    assert time_min == pytest.approx(300, rel=0.1)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_preheat_learner.py::test_preheat_learner_delegates_to_heating_rate_learner -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# Modify custom_components/adaptive_climate/adaptive/preheat.py

# Add import
from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateLearner,
)

# Modify PreheatLearner.__init__ to accept optional HeatingRateLearner:
    def __init__(
        self,
        heating_type: str,
        heating_rate_learner: HeatingRateLearner | None = None,
    ) -> None:
        self._heating_type = heating_type
        self._heating_rate_learner = heating_rate_learner
        # ... existing initialization ...

# Modify estimate_time_to_target (or get_heating_rate if that's what it uses):
    def estimate_time_to_target(
        self, current_temp: float, target_temp: float, outdoor_temp: float
    ) -> float:
        """Estimate time in minutes to reach target temperature."""
        delta = target_temp - current_temp
        if delta <= 0:
            return 0.0

        # Delegate to HeatingRateLearner if available
        if self._heating_rate_learner is not None:
            rate, source = self._heating_rate_learner.get_heating_rate(
                delta, outdoor_temp
            )
        else:
            rate = self._get_heating_rate_internal(delta, outdoor_temp)

        if rate <= 0:
            return float("inf")

        hours = delta / rate
        return hours * 60  # Convert to minutes
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_preheat_learner.py::test_preheat_learner_delegates_to_heating_rate_learner -v`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(heating-rate): update PreheatLearner to delegate to HeatingRateLearner"
```

---

## Task 13: Add Session Lifecycle Hooks in climate.py

**Files:**
- Modify: `custom_components/adaptive_climate/climate.py`
- Test: `tests/test_integration_heating_rate.py` (new)

**Step 1: Write failing test for session lifecycle**

```python
# Create tests/test_integration_heating_rate.py
"""Integration tests for HeatingRateLearner session lifecycle."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


class TestHeatingRateSessionLifecycle:
    """Tests for session start/end in climate entity."""

    @pytest.fixture
    def climate_entity(self):
        """Create a mock climate entity with heating rate learner."""
        # This will need to be adapted based on actual test patterns
        # in the codebase
        pass

    def test_session_starts_on_recovery_detection(self):
        """Test session starts when entering recovery (temp < setpoint - threshold)."""
        # Placeholder - implement based on actual climate.py patterns
        pass

    def test_session_ends_on_setpoint_reached(self):
        """Test session ends when setpoint is reached."""
        pass

    def test_session_discarded_on_override(self):
        """Test session discarded when contact/humidity override occurs."""
        pass
```

**Note:** This task requires deeper integration with the existing climate.py patterns. The test structure should follow existing integration tests in the codebase. Review `tests/test_integration_cycle_learning.py` for patterns.

**Step 2-5:** Implementation depends on existing climate.py event handling patterns. Key integration points:

1. **Session start:** When `_async_control_heating` detects temp below setpoint threshold and HVAC mode is HEAT
2. **Session update:** After each cycle completes (in cycle end handler)
3. **Session end:** When setpoint reached, stalled, or override occurs
4. **Session discard:** On contact_open, humidity_spike, or other override events

**Commit after implementation:**

```bash
git add -A
git commit -m "feat(heating-rate): add session lifecycle hooks in climate entity"
```

---

## Task 14: Wire Up Undershoot Detector Rate Mode

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/undershoot_detector.py`
- Test: `tests/test_undershoot_detector.py`

**Step 1: Write failing test for rate-based detection**

```python
# Add to tests/test_undershoot_detector.py
def test_undershoot_detector_uses_heating_rate_learner():
    """Test UndershootDetector checks rate when HeatingRateLearner available."""
    from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
        HeatingRateLearner,
    )

    hr_learner = HeatingRateLearner("radiator")
    # Add enough observations for comparison
    for _ in range(5):
        hr_learner.add_observation(
            rate=1.0, duration_min=60, source="session",
            stalled=False, delta=3.0, outdoor_temp=8.0
        )

    detector = UndershootDetector(
        heating_type="radiator",
        heating_rate_learner=hr_learner,
    )

    # Current rate is 0.4 (40% of expected 1.0) - should flag underperforming
    is_under = detector.check_rate_underperforming(
        current_rate=0.4, delta=3.0, outdoor_temp=8.0
    )
    assert is_under is True
```

**Step 2-5:** Implement rate checking mode in UndershootDetector that queries HeatingRateLearner.

**Commit:**

```bash
git add -A
git commit -m "feat(heating-rate): add rate comparison mode to UndershootDetector"
```

---

## Task 15: Run Full Test Suite & Fix Issues

**Step 1: Run all tests**

```bash
pytest --tb=short -q
```

**Step 2: Fix any failures**

Address test failures one by one, committing each fix.

**Step 3: Run with coverage**

```bash
pytest --cov=custom_components/adaptive_climate/adaptive/heating_rate_learner --cov-report=term-missing
```

**Step 4: Final commit**

```bash
git add -A
git commit -m "test(heating-rate): ensure full test coverage"
```

---

## Summary

| Task | Description | Key Files |
|------|-------------|-----------|
| 1 | Data models | heating_rate_learner.py |
| 2 | Binning logic | heating_rate_learner.py |
| 3 | Add observation | heating_rate_learner.py |
| 4 | Query interface | heating_rate_learner.py |
| 5 | Session start/end | heating_rate_learner.py |
| 6 | Session update/stall | heating_rate_learner.py |
| 7 | Stall counter & Ki boost | heating_rate_learner.py |
| 8 | Rate comparison | heating_rate_learner.py |
| 9 | Serialization | heating_rate_learner.py |
| 10 | AdaptiveLearner integration | learning.py |
| 11 | v10 serialization | learner_serialization.py |
| 12 | PreheatLearner delegation | preheat.py |
| 13 | Climate lifecycle hooks | climate.py |
| 14 | UndershootDetector rate mode | undershoot_detector.py |
| 15 | Full test suite | all |
