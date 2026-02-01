# Weighted Cycle Learning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent premature "tuned" status by weighting cycles by difficulty and requiring recovery cycle counts.

**Architecture:** Add cycle classification (maintenance vs recovery) with weighted confidence contribution. Cap passive sources (maintenance + heating rate). Track recovery cycles separately. Extended settling windows for slow systems.

**Tech Stack:** Python 3.11, pytest, Home Assistant custom component patterns.

**Worktree:** `/Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning`

**Design doc:** `docs/plans/2026-02-01-weighted-cycle-learning-design.md`

---

## Task 1: Add Constants for Weighted Learning

**Files:**
- Modify: `custom_components/adaptive_climate/const.py`
- Test: `tests/test_const.py` (create if needed)

**Step 1: Add recovery threshold constants**

Add after line 548 (after `HEATING_TYPE_CONFIDENCE_SCALE`):

```python
# Recovery thresholds by heating type and learning status
# Maps (heating_type, is_stable) -> threshold in °C
RECOVERY_THRESHOLD_COLLECTING = {
    HeatingType.FLOOR_HYDRONIC: 0.5,
    HeatingType.RADIATOR: 0.3,
    HeatingType.CONVECTOR: 0.3,
    HeatingType.FORCED_AIR: 0.3,
}

RECOVERY_THRESHOLD_STABLE = {
    HeatingType.FLOOR_HYDRONIC: 0.8,
    HeatingType.RADIATOR: 0.5,
    HeatingType.CONVECTOR: 0.3,
    HeatingType.FORCED_AIR: 0.3,
}

# Maintenance confidence caps by heating type
MAINTENANCE_CONFIDENCE_CAP = {
    HeatingType.FLOOR_HYDRONIC: 0.25,
    HeatingType.RADIATOR: 0.30,
    HeatingType.CONVECTOR: 0.35,
    HeatingType.FORCED_AIR: 0.35,
}

# Heating rate confidence caps by heating type
HEATING_RATE_CONFIDENCE_CAP = {
    HeatingType.FLOOR_HYDRONIC: 0.30,
    HeatingType.RADIATOR: 0.20,
    HeatingType.CONVECTOR: 0.10,
    HeatingType.FORCED_AIR: 0.05,
}

# Settling windows by heating type (minutes from heat delivery end)
SETTLING_WINDOW_MINUTES = {
    HeatingType.FLOOR_HYDRONIC: 60,
    HeatingType.RADIATOR: 30,
    HeatingType.CONVECTOR: 15,
    HeatingType.FORCED_AIR: 10,
}

# Recovery cycle requirements for tier progression
RECOVERY_CYCLES_FOR_TIER1 = {
    HeatingType.FLOOR_HYDRONIC: 12,
    HeatingType.RADIATOR: 8,
    HeatingType.CONVECTOR: 6,
    HeatingType.FORCED_AIR: 6,
}

RECOVERY_CYCLES_FOR_TIER2 = {
    HeatingType.FLOOR_HYDRONIC: 20,
    HeatingType.RADIATOR: 15,
    HeatingType.CONVECTOR: 12,
    HeatingType.FORCED_AIR: 10,
}

# Cycle weighting constants
MAINTENANCE_BASE_WEIGHT = 0.3
RECOVERY_BASE_WEIGHT = 1.0
DELTA_MULTIPLIER_SCALE = 0.5
DELTA_MULTIPLIER_CAP = 2.0
OUTCOME_FACTOR_CLEAN = 1.0
OUTCOME_FACTOR_OVERSHOOT = 0.7
OUTCOME_FACTOR_UNDERSHOOT = 0.5
DUTY_BONUS = 0.15
DUTY_BONUS_THRESHOLD = 0.60
OUTDOOR_BONUS = 0.15
OUTDOOR_BONUS_THRESHOLD = 5.0  # °C
NIGHT_SETBACK_BONUS = 0.2
MAINTENANCE_DIMINISHING_RATE = 0.1

# Auto-learning setback constants
AUTO_LEARNING_SETBACK_DELTA = 0.5  # °C
AUTO_LEARNING_SETBACK_WINDOW_START = 3  # hour (3am)
AUTO_LEARNING_SETBACK_WINDOW_END = 5  # hour (5am)
AUTO_LEARNING_SETBACK_TRIGGER_DAYS = 7
AUTO_LEARNING_SETBACK_COOLDOWN_DAYS = 7
```

**Step 2: Run linter**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && python -m py_compile custom_components/adaptive_climate/const.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add custom_components/adaptive_climate/const.py
git commit -m "feat: add weighted cycle learning constants"
```

---

## Task 2: Add Cycle Weight Calculator

**Files:**
- Create: `custom_components/adaptive_climate/adaptive/cycle_weight.py`
- Create: `tests/test_cycle_weight.py`

**Step 1: Write failing tests**

Create `tests/test_cycle_weight.py`:

```python
"""Tests for cycle weight calculation."""

import pytest
from custom_components.adaptive_climate.adaptive.cycle_weight import (
    CycleWeightCalculator,
    CycleOutcome,
)
from custom_components.adaptive_climate.const import HeatingType


class TestCycleClassification:
    """Test cycle classification (maintenance vs recovery)."""

    def test_maintenance_cycle_floor_collecting(self):
        """Cycle below threshold is maintenance."""
        calc = CycleWeightCalculator(HeatingType.FLOOR_HYDRONIC)
        assert calc.is_recovery_cycle(0.4, is_stable=False) is False  # < 0.5

    def test_recovery_cycle_floor_collecting(self):
        """Cycle at/above threshold is recovery."""
        calc = CycleWeightCalculator(HeatingType.FLOOR_HYDRONIC)
        assert calc.is_recovery_cycle(0.5, is_stable=False) is True

    def test_recovery_threshold_increases_at_stable(self):
        """Floor threshold increases from 0.5 to 0.8 at stable."""
        calc = CycleWeightCalculator(HeatingType.FLOOR_HYDRONIC)
        # 0.6 is recovery when collecting, maintenance when stable
        assert calc.is_recovery_cycle(0.6, is_stable=False) is True
        assert calc.is_recovery_cycle(0.6, is_stable=True) is False
        assert calc.is_recovery_cycle(0.8, is_stable=True) is True

    def test_forced_air_threshold_constant(self):
        """Forced air threshold stays at 0.3 regardless of status."""
        calc = CycleWeightCalculator(HeatingType.FORCED_AIR)
        assert calc.is_recovery_cycle(0.3, is_stable=False) is True
        assert calc.is_recovery_cycle(0.3, is_stable=True) is True
        assert calc.is_recovery_cycle(0.25, is_stable=False) is False


class TestCycleWeightCalculation:
    """Test cycle weight calculation."""

    def test_maintenance_cycle_base_weight(self):
        """Maintenance cycle gets 0.3 base weight."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.2,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
        )
        assert weight == pytest.approx(0.3, rel=0.01)

    def test_recovery_cycle_base_weight(self):
        """Recovery cycle gets 1.0 base weight."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.5,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
        )
        # base=1.0, delta_mult=1.0+(0.5-0.3)*0.5=1.1, outcome=1.0
        assert weight == pytest.approx(1.1, rel=0.01)

    def test_large_delta_increases_weight(self):
        """Larger starting delta increases weight."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=2.0,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
        )
        # base=1.0, delta_mult=1.0+(2.0-0.3)*0.5=1.85, outcome=1.0
        assert weight == pytest.approx(1.85, rel=0.01)

    def test_delta_multiplier_capped_at_2(self):
        """Delta multiplier caps at 2.0."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=5.0,  # Would give 1.0+(5.0-0.3)*0.5=3.35
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
        )
        # Capped: base=1.0, delta_mult=2.0, outcome=1.0
        assert weight == pytest.approx(2.0, rel=0.01)

    def test_overshoot_reduces_weight(self):
        """Overshoot outcome reduces weight by 0.7x."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.5,
            is_stable=False,
            outcome=CycleOutcome.OVERSHOOT,
        )
        # base=1.0, delta_mult=1.1, outcome=0.7 -> 1.0*1.1*0.7=0.77
        assert weight == pytest.approx(0.77, rel=0.01)

    def test_undershoot_reduces_weight_more(self):
        """Undershoot outcome reduces weight by 0.5x."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.5,
            is_stable=False,
            outcome=CycleOutcome.UNDERSHOOT,
        )
        # base=1.0, delta_mult=1.1, outcome=0.5 -> 1.0*1.1*0.5=0.55
        assert weight == pytest.approx(0.55, rel=0.01)


class TestBonuses:
    """Test bonus calculations."""

    def test_duty_bonus_above_threshold(self):
        """Effective duty >60% adds 0.15 bonus."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.5,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
            effective_duty=0.65,
        )
        # base*delta*outcome + duty_bonus = 1.1 + 0.15 = 1.25
        assert weight == pytest.approx(1.25, rel=0.01)

    def test_no_duty_bonus_below_threshold(self):
        """Effective duty <=60% adds no bonus."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.5,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
            effective_duty=0.55,
        )
        assert weight == pytest.approx(1.1, rel=0.01)

    def test_outdoor_bonus_below_threshold(self):
        """Outdoor temp <5°C adds 0.15 bonus."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.5,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
            outdoor_temp=3.0,
        )
        # 1.1 + 0.15 = 1.25
        assert weight == pytest.approx(1.25, rel=0.01)

    def test_night_setback_bonus(self):
        """Night setback recovery adds 0.2 bonus."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=0.5,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
            is_night_setback_recovery=True,
        )
        # 1.1 + 0.2 = 1.3
        assert weight == pytest.approx(1.3, rel=0.01)

    def test_all_bonuses_stack(self):
        """All bonuses stack additively."""
        calc = CycleWeightCalculator(HeatingType.CONVECTOR)
        weight = calc.calculate_weight(
            starting_delta=2.0,
            is_stable=False,
            outcome=CycleOutcome.CLEAN,
            effective_duty=0.70,
            outdoor_temp=0.0,
            is_night_setback_recovery=True,
        )
        # base=1.0, delta_mult=1.85, outcome=1.0 -> challenge=1.85
        # bonuses: 0.15 + 0.15 + 0.2 = 0.5
        # total: 1.85 + 0.5 = 2.35
        assert weight == pytest.approx(2.35, rel=0.01)
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_cycle_weight.py -v`
Expected: FAIL with import errors (module doesn't exist)

**Step 3: Implement cycle weight calculator**

Create `custom_components/adaptive_climate/adaptive/cycle_weight.py`:

```python
"""Cycle weight calculation for weighted learning."""

from enum import Enum

from ..const import (
    HeatingType,
    RECOVERY_THRESHOLD_COLLECTING,
    RECOVERY_THRESHOLD_STABLE,
    MAINTENANCE_BASE_WEIGHT,
    RECOVERY_BASE_WEIGHT,
    DELTA_MULTIPLIER_SCALE,
    DELTA_MULTIPLIER_CAP,
    OUTCOME_FACTOR_CLEAN,
    OUTCOME_FACTOR_OVERSHOOT,
    OUTCOME_FACTOR_UNDERSHOOT,
    DUTY_BONUS,
    DUTY_BONUS_THRESHOLD,
    OUTDOOR_BONUS,
    OUTDOOR_BONUS_THRESHOLD,
    NIGHT_SETBACK_BONUS,
)


class CycleOutcome(Enum):
    """Outcome of a heating/cooling cycle."""

    CLEAN = "clean"
    OVERSHOOT = "overshoot"
    UNDERSHOOT = "undershoot"


class CycleWeightCalculator:
    """Calculates weight for cycles based on difficulty and outcome."""

    def __init__(self, heating_type: HeatingType) -> None:
        """Initialize with heating type."""
        self._heating_type = heating_type

    def get_recovery_threshold(self, is_stable: bool) -> float:
        """Get recovery threshold based on learning status.

        Args:
            is_stable: True if learning status is "stable" or higher.

        Returns:
            Threshold in °C. Cycles starting >= this delta are recovery cycles.
        """
        if is_stable:
            return RECOVERY_THRESHOLD_STABLE[self._heating_type]
        return RECOVERY_THRESHOLD_COLLECTING[self._heating_type]

    def is_recovery_cycle(self, starting_delta: float, is_stable: bool) -> bool:
        """Determine if cycle is recovery (vs maintenance).

        Args:
            starting_delta: Temperature difference from setpoint at cycle start.
            is_stable: True if learning status is "stable" or higher.

        Returns:
            True if recovery cycle, False if maintenance cycle.
        """
        threshold = self.get_recovery_threshold(is_stable)
        return starting_delta >= threshold

    def calculate_weight(
        self,
        starting_delta: float,
        is_stable: bool,
        outcome: CycleOutcome,
        effective_duty: float | None = None,
        outdoor_temp: float | None = None,
        is_night_setback_recovery: bool = False,
    ) -> float:
        """Calculate cycle weight.

        Formula: (challenge × outcome_factor) + bonuses
        Where: challenge = base_weight × delta_multiplier

        Args:
            starting_delta: Temperature difference from setpoint at cycle start.
            is_stable: True if learning status is "stable" or higher.
            outcome: Cycle outcome (clean, overshoot, undershoot).
            effective_duty: Peak duty minus committed heat ratio (0-1).
            outdoor_temp: Outdoor temperature in °C.
            is_night_setback_recovery: True if recovering from night setback.

        Returns:
            Cycle weight (typically 0.3-2.5).
        """
        # Determine base weight
        is_recovery = self.is_recovery_cycle(starting_delta, is_stable)
        base_weight = RECOVERY_BASE_WEIGHT if is_recovery else MAINTENANCE_BASE_WEIGHT

        # Calculate delta multiplier (only for recovery cycles)
        if is_recovery:
            threshold = self.get_recovery_threshold(is_stable)
            delta_multiplier = 1.0 + (starting_delta - threshold) * DELTA_MULTIPLIER_SCALE
            delta_multiplier = min(delta_multiplier, DELTA_MULTIPLIER_CAP)
        else:
            delta_multiplier = 1.0

        # Get outcome factor
        outcome_factors = {
            CycleOutcome.CLEAN: OUTCOME_FACTOR_CLEAN,
            CycleOutcome.OVERSHOOT: OUTCOME_FACTOR_OVERSHOOT,
            CycleOutcome.UNDERSHOOT: OUTCOME_FACTOR_UNDERSHOOT,
        }
        outcome_factor = outcome_factors[outcome]

        # Calculate challenge
        challenge = base_weight * delta_multiplier * outcome_factor

        # Calculate bonuses
        bonuses = 0.0

        if effective_duty is not None and effective_duty > DUTY_BONUS_THRESHOLD:
            bonuses += DUTY_BONUS

        if outdoor_temp is not None and outdoor_temp < OUTDOOR_BONUS_THRESHOLD:
            bonuses += OUTDOOR_BONUS

        if is_night_setback_recovery:
            bonuses += NIGHT_SETBACK_BONUS

        return challenge + bonuses
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_cycle_weight.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/cycle_weight.py tests/test_cycle_weight.py
git commit -m "feat: add cycle weight calculator with tests"
```

---

## Task 3: Add Confidence Contribution Tracker

**Files:**
- Create: `custom_components/adaptive_climate/adaptive/confidence_contribution.py`
- Create: `tests/test_confidence_contribution.py`

**Step 1: Write failing tests**

Create `tests/test_confidence_contribution.py`:

```python
"""Tests for confidence contribution tracking."""

import pytest
from custom_components.adaptive_climate.adaptive.confidence_contribution import (
    ConfidenceContributionTracker,
)
from custom_components.adaptive_climate.const import HeatingType


class TestMaintenanceCap:
    """Test maintenance confidence capping."""

    def test_maintenance_below_cap(self):
        """Maintenance contribution below cap is fully applied."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        gain = tracker.apply_maintenance_gain(0.10)
        assert gain == pytest.approx(0.10, rel=0.01)
        assert tracker.maintenance_contribution == pytest.approx(0.10, rel=0.01)

    def test_maintenance_at_cap(self):
        """Maintenance at cap gets diminishing returns."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor cap is 25%
        tracker._maintenance_contribution = 0.25
        gain = tracker.apply_maintenance_gain(0.10)
        # 10% * 0.1 diminishing rate = 1%
        assert gain == pytest.approx(0.01, rel=0.01)

    def test_maintenance_crossing_cap(self):
        """Gain that crosses cap is split."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        tracker._maintenance_contribution = 0.20
        gain = tracker.apply_maintenance_gain(0.10)
        # 5% to reach cap + 5% * 0.1 = 5.5%
        assert gain == pytest.approx(0.055, rel=0.01)

    def test_different_caps_by_heating_type(self):
        """Different heating types have different caps."""
        floor = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        forced = ConfidenceContributionTracker(HeatingType.FORCED_AIR)

        # Floor cap is 25%, forced_air is 35%
        floor._maintenance_contribution = 0.30
        forced._maintenance_contribution = 0.30

        floor_gain = floor.apply_maintenance_gain(0.10)
        forced_gain = forced.apply_maintenance_gain(0.10)

        # Floor is over cap, forced is under
        assert floor_gain < forced_gain


class TestHeatingRateCap:
    """Test heating rate confidence capping."""

    def test_heating_rate_below_cap(self):
        """Heating rate below cap is fully applied."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        gain = tracker.apply_heating_rate_gain(0.15)
        assert gain == pytest.approx(0.15, rel=0.01)
        assert tracker.heating_rate_contribution == pytest.approx(0.15, rel=0.01)

    def test_heating_rate_capped(self):
        """Heating rate gain is capped at max."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor cap is 30%
        gain = tracker.apply_heating_rate_gain(0.50)
        assert gain == pytest.approx(0.30, rel=0.01)
        assert tracker.heating_rate_contribution == pytest.approx(0.30, rel=0.01)

    def test_forced_air_low_heating_rate_cap(self):
        """Forced air has low heating rate cap (5%)."""
        tracker = ConfidenceContributionTracker(HeatingType.FORCED_AIR)
        gain = tracker.apply_heating_rate_gain(0.20)
        assert gain == pytest.approx(0.05, rel=0.01)


class TestRecoveryCycles:
    """Test recovery cycle tracking."""

    def test_recovery_cycle_count(self):
        """Recovery cycles are counted separately."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        tracker.add_recovery_cycle()
        tracker.add_recovery_cycle()
        assert tracker.recovery_cycle_count == 2

    def test_can_reach_tier1(self):
        """Tier 1 requires enough recovery cycles."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor needs 12 recovery cycles for tier 1
        for _ in range(11):
            tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(1) is False

        tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(1) is True

    def test_can_reach_tier2(self):
        """Tier 2 requires more recovery cycles."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        # Floor needs 20 recovery cycles for tier 2
        for _ in range(19):
            tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(2) is False

        tracker.add_recovery_cycle()
        assert tracker.can_reach_tier(2) is True


class TestSerialization:
    """Test serialization/deserialization."""

    def test_to_dict(self):
        """Tracker serializes to dict."""
        tracker = ConfidenceContributionTracker(HeatingType.FLOOR_HYDRONIC)
        tracker._maintenance_contribution = 0.15
        tracker._heating_rate_contribution = 0.10
        tracker._recovery_cycle_count = 5

        data = tracker.to_dict()

        assert data["maintenance_contribution"] == pytest.approx(0.15, rel=0.01)
        assert data["heating_rate_contribution"] == pytest.approx(0.10, rel=0.01)
        assert data["recovery_cycle_count"] == 5

    def test_from_dict(self):
        """Tracker deserializes from dict."""
        data = {
            "maintenance_contribution": 0.20,
            "heating_rate_contribution": 0.12,
            "recovery_cycle_count": 8,
        }
        tracker = ConfidenceContributionTracker.from_dict(
            data, HeatingType.FLOOR_HYDRONIC
        )

        assert tracker.maintenance_contribution == pytest.approx(0.20, rel=0.01)
        assert tracker.heating_rate_contribution == pytest.approx(0.12, rel=0.01)
        assert tracker.recovery_cycle_count == 8

    def test_from_dict_missing_fields(self):
        """Missing fields default to zero."""
        tracker = ConfidenceContributionTracker.from_dict({}, HeatingType.FLOOR_HYDRONIC)

        assert tracker.maintenance_contribution == 0.0
        assert tracker.heating_rate_contribution == 0.0
        assert tracker.recovery_cycle_count == 0
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_confidence_contribution.py -v`
Expected: FAIL with import errors

**Step 3: Implement confidence contribution tracker**

Create `custom_components/adaptive_climate/adaptive/confidence_contribution.py`:

```python
"""Confidence contribution tracking for weighted learning."""

from __future__ import annotations

from ..const import (
    HeatingType,
    MAINTENANCE_CONFIDENCE_CAP,
    HEATING_RATE_CONFIDENCE_CAP,
    RECOVERY_CYCLES_FOR_TIER1,
    RECOVERY_CYCLES_FOR_TIER2,
    MAINTENANCE_DIMINISHING_RATE,
)


class ConfidenceContributionTracker:
    """Tracks confidence contributions from different sources with caps."""

    def __init__(self, heating_type: HeatingType) -> None:
        """Initialize tracker.

        Args:
            heating_type: The heating type for this zone.
        """
        self._heating_type = heating_type
        self._maintenance_contribution: float = 0.0
        self._heating_rate_contribution: float = 0.0
        self._recovery_cycle_count: int = 0

    @property
    def maintenance_contribution(self) -> float:
        """Current maintenance confidence contribution."""
        return self._maintenance_contribution

    @property
    def heating_rate_contribution(self) -> float:
        """Current heating rate confidence contribution."""
        return self._heating_rate_contribution

    @property
    def recovery_cycle_count(self) -> int:
        """Number of recovery cycles completed."""
        return self._recovery_cycle_count

    def apply_maintenance_gain(self, gain: float) -> float:
        """Apply maintenance confidence gain with cap and diminishing returns.

        Args:
            gain: Raw confidence gain to apply.

        Returns:
            Actual gain after cap and diminishing returns.
        """
        cap = MAINTENANCE_CONFIDENCE_CAP[self._heating_type]

        if self._maintenance_contribution >= cap:
            # Already at cap - diminishing returns
            actual_gain = gain * MAINTENANCE_DIMINISHING_RATE
            self._maintenance_contribution += actual_gain
            return actual_gain

        # Room under cap
        room = cap - self._maintenance_contribution
        if gain <= room:
            # Fully under cap
            self._maintenance_contribution += gain
            return gain

        # Crosses cap - split the gain
        under_cap_gain = room
        over_cap_gain = (gain - room) * MAINTENANCE_DIMINISHING_RATE
        actual_gain = under_cap_gain + over_cap_gain
        self._maintenance_contribution += actual_gain
        return actual_gain

    def apply_heating_rate_gain(self, gain: float) -> float:
        """Apply heating rate confidence gain with hard cap.

        Args:
            gain: Raw confidence gain to apply.

        Returns:
            Actual gain after cap.
        """
        cap = HEATING_RATE_CONFIDENCE_CAP[self._heating_type]
        room = cap - self._heating_rate_contribution

        if room <= 0:
            return 0.0

        actual_gain = min(gain, room)
        self._heating_rate_contribution += actual_gain
        return actual_gain

    def add_recovery_cycle(self) -> None:
        """Record a completed recovery cycle."""
        self._recovery_cycle_count += 1

    def can_reach_tier(self, tier: int) -> bool:
        """Check if tier can be reached based on recovery cycle count.

        Args:
            tier: Tier number (1 or 2).

        Returns:
            True if enough recovery cycles for tier.
        """
        if tier == 1:
            required = RECOVERY_CYCLES_FOR_TIER1[self._heating_type]
        elif tier == 2:
            required = RECOVERY_CYCLES_FOR_TIER2[self._heating_type]
        else:
            return True  # Tier 3+ has no cycle requirement

        return self._recovery_cycle_count >= required

    def to_dict(self) -> dict:
        """Serialize to dict for persistence.

        Returns:
            Dict with contribution values.
        """
        return {
            "maintenance_contribution": self._maintenance_contribution,
            "heating_rate_contribution": self._heating_rate_contribution,
            "recovery_cycle_count": self._recovery_cycle_count,
        }

    @classmethod
    def from_dict(
        cls, data: dict, heating_type: HeatingType
    ) -> ConfidenceContributionTracker:
        """Deserialize from dict.

        Args:
            data: Dict with contribution values (may be empty).
            heating_type: The heating type for this zone.

        Returns:
            Initialized tracker.
        """
        tracker = cls(heating_type)
        tracker._maintenance_contribution = data.get("maintenance_contribution", 0.0)
        tracker._heating_rate_contribution = data.get("heating_rate_contribution", 0.0)
        tracker._recovery_cycle_count = data.get("recovery_cycle_count", 0)
        return tracker
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_confidence_contribution.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/confidence_contribution.py tests/test_confidence_contribution.py
git commit -m "feat: add confidence contribution tracker with caps"
```

---

## Task 4: Add Heating Rate Consistency Score to PreheatLearner

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/preheat.py`
- Modify: `tests/test_preheat_learner.py`

**Step 1: Write failing test**

Add to `tests/test_preheat_learner.py`:

```python
class TestHeatingRateConsistency:
    """Test heating rate consistency scoring."""

    def test_no_observations_returns_zero(self):
        """No observations means zero consistency."""
        learner = PreheatLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        assert learner.get_rate_consistency_score() == 0.0

    def test_few_observations_low_score(self):
        """Few observations give low score."""
        learner = PreheatLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        # Add 3 observations
        for i in range(3):
            learner.add_observation(
                start_temp=18.0,
                end_temp=20.0,
                outdoor_temp=5.0,
                duration_minutes=60,
                timestamp=dt_util.utcnow(),
            )
        score = learner.get_rate_consistency_score()
        assert 0.0 < score < 0.5

    def test_many_consistent_observations_high_score(self):
        """Many consistent observations give high score."""
        learner = PreheatLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        # Add 15 consistent observations (same rate)
        for i in range(15):
            learner.add_observation(
                start_temp=18.0,
                end_temp=20.0,  # 2°C rise
                outdoor_temp=5.0,
                duration_minutes=60,  # 2°C/hr consistent
                timestamp=dt_util.utcnow(),
            )
        score = learner.get_rate_consistency_score()
        assert score >= 0.8

    def test_inconsistent_observations_lower_score(self):
        """Inconsistent observations reduce score."""
        learner = PreheatLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        # Add observations with varying rates
        for i, duration in enumerate([30, 60, 90, 45, 75, 120, 40, 80, 55, 65]):
            learner.add_observation(
                start_temp=18.0,
                end_temp=20.0,
                outdoor_temp=5.0,
                duration_minutes=duration,  # Varying rates
                timestamp=dt_util.utcnow(),
            )
        score = learner.get_rate_consistency_score()
        # Score should be lower due to inconsistency
        assert 0.3 < score < 0.7
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_preheat_learner.py::TestHeatingRateConsistency -v`
Expected: FAIL (method doesn't exist)

**Step 3: Implement get_rate_consistency_score**

Add to `custom_components/adaptive_climate/adaptive/preheat.py` in the `PreheatLearner` class:

```python
def get_rate_consistency_score(self) -> float:
    """Calculate heating rate consistency score for confidence contribution.

    Score based on:
    - Number of observations (more = higher base score)
    - Consistency of rates within bins (lower variance = higher score)

    Returns:
        Score from 0.0 to 1.0.
    """
    all_rates: list[float] = []
    for observations in self._observations.values():
        all_rates.extend(obs.rate for obs in observations)

    if not all_rates:
        return 0.0

    # Base score from observation count (caps at 1.0 with 10+ observations)
    count_score = min(len(all_rates) / 10.0, 1.0)

    if len(all_rates) < 3:
        # Not enough for consistency check
        return count_score * 0.3

    # Calculate coefficient of variation (lower = more consistent)
    import statistics
    mean_rate = statistics.mean(all_rates)
    if mean_rate == 0:
        return count_score * 0.5

    stdev = statistics.stdev(all_rates)
    cv = stdev / mean_rate  # Coefficient of variation

    # Convert CV to consistency score (CV of 0 = 1.0, CV of 1 = 0.0)
    consistency_score = max(0.0, 1.0 - cv)

    # Combine count and consistency (weighted average)
    return count_score * 0.4 + consistency_score * 0.6
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_preheat_learner.py::TestHeatingRateConsistency -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/preheat.py tests/test_preheat_learner.py
git commit -m "feat: add heating rate consistency score to PreheatLearner"
```

---

## Task 5: Update Serialization to v9 Format

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/learner_serialization.py`
- Modify: `tests/test_learner_serialization.py`

**Step 1: Write failing test**

Add to `tests/test_learner_serialization.py`:

```python
class TestV9Format:
    """Test v9 format with confidence contributions."""

    def test_serialize_includes_contributions(self):
        """V9 format includes contribution tracking."""
        learner = create_test_learner()  # Helper to create AdaptiveLearner
        learner._contribution_tracker._maintenance_contribution = 0.15
        learner._contribution_tracker._heating_rate_contribution = 0.10
        learner._contribution_tracker._recovery_cycle_count = 8

        data = learner_to_dict(learner)

        assert data["format_version"] == 9
        assert "contribution_tracker" in data
        assert data["contribution_tracker"]["maintenance_contribution"] == pytest.approx(0.15)
        assert data["contribution_tracker"]["heating_rate_contribution"] == pytest.approx(0.10)
        assert data["contribution_tracker"]["recovery_cycle_count"] == 8

    def test_deserialize_v9_restores_contributions(self):
        """V9 format restores contribution tracking."""
        data = {
            "format_version": 9,
            "contribution_tracker": {
                "maintenance_contribution": 0.20,
                "heating_rate_contribution": 0.12,
                "recovery_cycle_count": 5,
            },
            "heating": {"cycle_history": [], "auto_apply_count": 0, "convergence_confidence": 0.0},
            "cooling": {"cycle_history": [], "auto_apply_count": 0, "convergence_confidence": 0.0},
        }
        learner = create_test_learner()
        dict_to_learner(data, learner)

        assert learner._contribution_tracker.maintenance_contribution == pytest.approx(0.20)
        assert learner._contribution_tracker.heating_rate_contribution == pytest.approx(0.12)
        assert learner._contribution_tracker.recovery_cycle_count == 5

    def test_v8_migration_to_v9(self):
        """V8 format migrates to v9 with zero contributions."""
        data = {
            "format_version": 8,
            "heating": {"cycle_history": [], "auto_apply_count": 0, "convergence_confidence": 0.5},
            "cooling": {"cycle_history": [], "auto_apply_count": 0, "convergence_confidence": 0.0},
        }
        learner = create_test_learner()
        dict_to_learner(data, learner)

        # Contributions default to zero on migration
        assert learner._contribution_tracker.maintenance_contribution == 0.0
        assert learner._contribution_tracker.heating_rate_contribution == 0.0
        assert learner._contribution_tracker.recovery_cycle_count == 0
        # Existing confidence preserved
        assert learner._confidence_tracker.get_convergence_confidence(HVACMode.HEAT) == pytest.approx(0.5)
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_learner_serialization.py::TestV9Format -v`
Expected: FAIL

**Step 3: Update serialization**

Modify `custom_components/adaptive_climate/adaptive/learner_serialization.py`:

1. Update `CURRENT_FORMAT_VERSION = 9` (was 8)
2. In `learner_to_dict()`, add contribution tracker serialization
3. In `dict_to_learner()`, add contribution tracker deserialization with v8 migration

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_learner_serialization.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/learner_serialization.py tests/test_learner_serialization.py
git commit -m "feat: update serialization to v9 with contribution tracking"
```

---

## Task 6: Integrate Weighted Learning into AdaptiveLearner

**Files:**
- Modify: `custom_components/adaptive_climate/adaptive/learning.py`
- Modify: `tests/test_learning.py`

**Step 1: Write failing test**

Add to `tests/test_learning.py`:

```python
class TestWeightedCycleLearning:
    """Test weighted cycle learning integration."""

    def test_maintenance_cycle_weighted(self):
        """Maintenance cycles get reduced confidence gain."""
        learner = create_learner(HeatingType.CONVECTOR)
        initial_confidence = learner.get_convergence_confidence(HVACMode.HEAT)

        # Add maintenance cycle (delta < 0.3)
        metrics = create_good_metrics(starting_delta=0.1)
        learner.add_cycle_metrics(metrics, HVACMode.HEAT)

        gain = learner.get_convergence_confidence(HVACMode.HEAT) - initial_confidence
        # Maintenance weight is 0.3, so gain should be ~30% of normal
        assert gain < 0.05  # Much less than normal ~0.1

    def test_recovery_cycle_full_weight(self):
        """Recovery cycles get full confidence gain."""
        learner = create_learner(HeatingType.CONVECTOR)
        initial_confidence = learner.get_convergence_confidence(HVACMode.HEAT)

        # Add recovery cycle (delta >= 0.3)
        metrics = create_good_metrics(starting_delta=0.5)
        learner.add_cycle_metrics(metrics, HVACMode.HEAT)

        gain = learner.get_convergence_confidence(HVACMode.HEAT) - initial_confidence
        # Should be close to normal gain
        assert gain >= 0.08

    def test_recovery_cycle_counted(self):
        """Recovery cycles increment counter."""
        learner = create_learner(HeatingType.FLOOR_HYDRONIC)

        metrics = create_good_metrics(starting_delta=0.6)  # > 0.5 threshold for collecting
        learner.add_cycle_metrics(metrics, HVACMode.HEAT)

        assert learner._contribution_tracker.recovery_cycle_count == 1

    def test_tier_blocked_without_recovery_cycles(self):
        """Can't reach tier 1 without enough recovery cycles."""
        learner = create_learner(HeatingType.FLOOR_HYDRONIC)

        # Add maintenance cycles to build confidence
        for _ in range(20):
            metrics = create_good_metrics(starting_delta=0.1)
            learner.add_cycle_metrics(metrics, HVACMode.HEAT)

        # Confidence might be high enough but tier blocked
        assert not learner.can_reach_learning_tier(1, HVACMode.HEAT)

        # Add recovery cycles
        for _ in range(12):
            metrics = create_good_metrics(starting_delta=0.6)
            learner.add_cycle_metrics(metrics, HVACMode.HEAT)

        assert learner.can_reach_learning_tier(1, HVACMode.HEAT)
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_learning.py::TestWeightedCycleLearning -v`
Expected: FAIL

**Step 3: Implement weighted learning in AdaptiveLearner**

Modify `custom_components/adaptive_climate/adaptive/learning.py`:

1. Add `_contribution_tracker` and `_weight_calculator` to `__init__`
2. Modify `add_cycle_metrics()` to calculate weight and apply weighted gain
3. Add `can_reach_learning_tier()` method
4. Track starting_delta in cycle metrics (may need to add to CycleMetrics)

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_learning.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/adaptive/learning.py tests/test_learning.py
git commit -m "feat: integrate weighted learning into AdaptiveLearner"
```

---

## Task 7: Add Extended Settling Window

**Files:**
- Modify: `custom_components/adaptive_climate/managers/cycle_metrics.py`
- Modify: `tests/test_cycle_metrics.py`

**Step 1: Write failing test**

Add to `tests/test_cycle_metrics.py`:

```python
class TestExtendedSettlingWindow:
    """Test extended settling window for slow systems."""

    def test_settling_start_includes_actuation_and_transport(self):
        """Settling window starts after actuation + transport delay."""
        recorder = CycleMetricsRecorder(
            heating_type=HeatingType.FLOOR_HYDRONIC,
            # ... other params
        )
        recorder.set_transport_delay(5.0)  # 5 min transport
        recorder.set_valve_actuation_time(6.0)  # 6 min valve

        # Simulate cycle
        valve_close_time = dt_util.utcnow()
        recorder.set_device_off_time()

        settling_start = recorder.get_settling_start_time()
        expected = valve_close_time + timedelta(minutes=11)  # 5 + 6

        assert abs((settling_start - expected).total_seconds()) < 1

    def test_settling_window_by_heating_type(self):
        """Settling window varies by heating type."""
        floor = CycleMetricsRecorder(heating_type=HeatingType.FLOOR_HYDRONIC)
        forced = CycleMetricsRecorder(heating_type=HeatingType.FORCED_AIR)

        assert floor.get_settling_window_minutes() == 60
        assert forced.get_settling_window_minutes() == 10
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_cycle_metrics.py::TestExtendedSettlingWindow -v`
Expected: FAIL

**Step 3: Implement extended settling window**

Modify `custom_components/adaptive_climate/managers/cycle_metrics.py`:

1. Add `_valve_actuation_time` tracking
2. Add `get_settling_start_time()` method
3. Add `get_settling_window_minutes()` method using `SETTLING_WINDOW_MINUTES` constant
4. Update settling time calculation to use new timing

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_cycle_metrics.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/cycle_metrics.py tests/test_cycle_metrics.py
git commit -m "feat: add extended settling window for slow systems"
```

---

## Task 8: Add Auto-Learning Setback

**Files:**
- Modify: `custom_components/adaptive_climate/managers/night_setback_manager.py`
- Create: `tests/test_auto_learning_setback.py`

**Step 1: Write failing tests**

Create `tests/test_auto_learning_setback.py`:

```python
"""Tests for auto-learning setback."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from homeassistant.util import dt as dt_util
from custom_components.adaptive_climate.managers.night_setback_manager import (
    NightSetbackManager,
)
from custom_components.adaptive_climate.const import HeatingType


class TestAutoLearningSetbackTrigger:
    """Test auto-learning setback trigger conditions."""

    def test_triggers_after_7_days_at_cap(self):
        """Auto-setback triggers after 7 days stuck at maintenance cap."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 7
        manager._has_night_setback_configured = False
        manager._learning_status = "collecting"

        assert manager.should_apply_auto_learning_setback() is True

    def test_no_trigger_before_7_days(self):
        """No trigger before 7 days."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 6

        assert manager.should_apply_auto_learning_setback() is False

    def test_no_trigger_if_night_setback_configured(self):
        """No trigger if user has night setback configured."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 10
        manager._has_night_setback_configured = True

        assert manager.should_apply_auto_learning_setback() is False

    def test_no_trigger_if_already_tuned(self):
        """No trigger if already at tuned status."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 10
        manager._learning_status = "tuned"

        assert manager.should_apply_auto_learning_setback() is False

    def test_no_trigger_if_disabled(self):
        """No trigger if auto-learning disabled."""
        manager = create_manager(auto_learning_enabled=False)
        manager._days_at_maintenance_cap = 10

        assert manager.should_apply_auto_learning_setback() is False


class TestAutoLearningSetbackWindow:
    """Test auto-learning setback time window."""

    def test_applies_in_3_to_5_am_window(self):
        """Setback applies during 3-5am window."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 7

        with patch_time(hour=4):
            delta, active, _ = manager.calculate_night_setback_adjustment()
            assert active is True
            assert delta == pytest.approx(-0.5)

    def test_not_active_outside_window(self):
        """No setback outside 3-5am window."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 7

        with patch_time(hour=6):
            delta, active, _ = manager.calculate_night_setback_adjustment()
            assert active is False


class TestAutoLearningSetbackCooldown:
    """Test auto-learning setback cooldown."""

    def test_cooldown_prevents_repeated_setback(self):
        """Can't trigger again within 7 days."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 10
        manager._last_auto_setback = dt_util.utcnow() - timedelta(days=5)

        assert manager.should_apply_auto_learning_setback() is False

    def test_triggers_after_cooldown(self):
        """Triggers again after cooldown expires."""
        manager = create_manager(auto_learning_enabled=True)
        manager._days_at_maintenance_cap = 10
        manager._last_auto_setback = dt_util.utcnow() - timedelta(days=8)

        assert manager.should_apply_auto_learning_setback() is True
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_auto_learning_setback.py -v`
Expected: FAIL

**Step 3: Implement auto-learning setback**

Modify `custom_components/adaptive_climate/managers/night_setback_manager.py`:

1. Add tracking for `_days_at_maintenance_cap`, `_last_auto_setback`
2. Add `should_apply_auto_learning_setback()` method
3. Integrate into `calculate_night_setback_adjustment()`
4. Add state attribute reporting

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_auto_learning_setback.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/adaptive_climate/managers/night_setback_manager.py tests/test_auto_learning_setback.py
git commit -m "feat: add auto-learning setback for zones stuck at cap"
```

---

## Task 9: Integration Test

**Files:**
- Create: `tests/test_integration_weighted_learning.py`

**Step 1: Write integration test**

```python
"""Integration tests for weighted cycle learning."""

import pytest
from datetime import timedelta

from homeassistant.util import dt as dt_util
from custom_components.adaptive_climate.const import HeatingType, HVACMode


class TestWeightedLearningIntegration:
    """End-to-end tests for weighted learning flow."""

    @pytest.mark.asyncio
    async def test_floor_system_learning_progression(self):
        """Floor system progresses through learning stages correctly."""
        # Setup floor hydronic thermostat
        thermostat = await create_test_thermostat(HeatingType.FLOOR_HYDRONIC)

        # Phase 1: Maintenance cycles build limited confidence
        for _ in range(20):
            await simulate_maintenance_cycle(thermostat, delta=0.2)

        # Should be capped at ~25% maintenance contribution
        assert thermostat.learning_status == "collecting"
        confidence = thermostat.get_convergence_confidence()
        assert 0.20 < confidence < 0.30

        # Phase 2: Recovery cycles unlock tier 1
        for _ in range(12):
            await simulate_recovery_cycle(thermostat, delta=0.6)

        # Now should reach "stable"
        assert thermostat.learning_status == "stable"

        # Phase 3: More recovery to reach tuned
        for _ in range(8):
            await simulate_recovery_cycle(thermostat, delta=1.0)

        assert thermostat.learning_status == "tuned"

    @pytest.mark.asyncio
    async def test_auto_learning_setback_triggers(self):
        """Auto-learning setback triggers after 7 days at cap."""
        thermostat = await create_test_thermostat(HeatingType.FLOOR_HYDRONIC)

        # Fill maintenance cap
        for _ in range(30):
            await simulate_maintenance_cycle(thermostat, delta=0.1)

        # Simulate 7 days passing
        thermostat._contribution_tracker._days_at_cap = 7

        # At 4am, should apply auto-setback
        with patch_time(hour=4):
            setback = thermostat.get_night_setback_adjustment()
            assert setback.delta == pytest.approx(-0.5)
            assert setback.is_auto_learning is True
```

**Step 2: Run integration tests**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest tests/test_integration_weighted_learning.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add tests/test_integration_weighted_learning.py
git commit -m "test: add integration tests for weighted learning"
```

---

## Task 10: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add weighted learning section**

Add to CLAUDE.md under appropriate section:

```markdown
### Weighted Cycle Learning

Cycles are weighted by difficulty to prevent premature "tuned" status from maintenance-only operation.

**Cycle classification:**
- Recovery: starting delta ≥ threshold (0.5-0.8°C depending on type/status)
- Maintenance: starting delta < threshold

**Weight formula:**
```
weight = (base × delta_multiplier × outcome_factor) + bonuses
```

**Confidence caps by heating type:**
| Type | Maintenance Cap | Heating Rate Cap |
|------|-----------------|------------------|
| floor_hydronic | 25% | 30% |
| radiator | 30% | 20% |
| convector | 35% | 10% |
| forced_air | 35% | 5% |

**Recovery cycle requirements:**
- Tier progression requires recovery cycles, not just confidence %
- floor_hydronic: 12 for stable, 20 for tuned
- forced_air: 6 for stable, 10 for tuned

**Auto-learning setback:**
- Triggers after 7 days stuck at maintenance cap
- Applies 0.5°C setback during 3-5am
- Domain config: `auto_learning_setback: true` (default)
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add weighted cycle learning to CLAUDE.md"
```

---

## Task 11: Run Full Test Suite

**Step 1: Run all tests**

Run: `cd /Users/kleist/Sites/ha-adaptive-climate/.worktrees/weighted-cycle-learning && pytest --tb=short -q`
Expected: All tests PASS

**Step 2: If any failures, fix and recommit**

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add constants | `const.py` |
| 2 | Cycle weight calculator | `adaptive/cycle_weight.py` |
| 3 | Confidence contribution tracker | `adaptive/confidence_contribution.py` |
| 4 | Heating rate consistency score | `adaptive/preheat.py` |
| 5 | Serialization v9 | `adaptive/learner_serialization.py` |
| 6 | Integrate into AdaptiveLearner | `adaptive/learning.py` |
| 7 | Extended settling window | `managers/cycle_metrics.py` |
| 8 | Auto-learning setback | `managers/night_setback_manager.py` |
| 9 | Integration tests | `tests/test_integration_weighted_learning.py` |
| 10 | Documentation | `CLAUDE.md` |
| 11 | Final test run | - |
