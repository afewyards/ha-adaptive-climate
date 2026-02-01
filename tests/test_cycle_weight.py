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
