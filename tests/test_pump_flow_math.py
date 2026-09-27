"""Tests for pure pump flow helpers."""

import math
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_climate.managers.pump_flow_math import (
    decide_write,
    flow_limits,
    parse_current,
    round_up,
    target_flow,
)


def state(value="0", **attributes):
    s = MagicMock()
    s.state = value
    s.attributes = attributes
    return s


class TestTargetFlow:
    def test_nothing_open_is_zero(self):
        assert target_flow([], None) == 0.0

    def test_nothing_open_ignores_min_flow(self):
        assert target_flow([], 12.0) == 0.0

    def test_sum_of_counted_zones(self):
        assert target_flow([4.0, 3.5], None) == pytest.approx(7.5)

    def test_min_flow_floors_a_small_sum(self):
        assert target_flow([4.0], 12.0) == 12.0

    def test_sum_above_min_flow_is_kept(self):
        assert target_flow([8.0, 6.0], 12.0) == pytest.approx(14.0)


class TestRoundUp:
    def test_rounds_up_to_step(self):
        assert round_up(3.5, 1.0) == 4.0

    def test_exact_multiple_unchanged(self):
        assert round_up(7.5, 0.5) == 7.5

    def test_float_noise_does_not_bump_a_step(self):
        assert round_up(0.1 + 0.2, 0.1) == pytest.approx(0.3)

    def test_non_positive_step_rounds_to_millis(self):
        assert round_up(3.14159, 0) == 3.142


class TestFlowLimits:
    def test_reads_entity_attributes(self):
        assert flow_limits(state(min=5, max=40, step=0.5)) == (5.0, 40.0, 0.5)

    def test_missing_attributes_fall_back(self):
        minimum, maximum, step = flow_limits(state())
        assert (minimum, step) == (0.0, 0.1)
        assert math.isinf(maximum)

    def test_swapped_min_max_are_normalised(self):
        assert flow_limits(state(min=40, max=5, step=1)) == (5.0, 40.0, 1.0)

    def test_non_positive_step_falls_back(self):
        assert flow_limits(state(step=0))[2] == 0.1


class TestParseCurrent:
    def test_numeric_state(self):
        assert parse_current(state("7.5")) == 7.5

    @pytest.mark.parametrize("raw", ["unknown", "unavailable", ""])
    def test_non_numeric_state_is_none(self, raw):
        assert parse_current(state(raw)) is None


class TestDecideWrite:
    @staticmethod
    def decide(final, current, *, idle=False, pending_since=None, now=1000.0, interval=300.0):
        return decide_write(
            final, current, idle=idle, pending_since=pending_since, now=now, min_write_interval=interval
        )

    def test_unknown_current_value_writes(self):
        d = self.decide(4.0, None)
        assert (d.write, d.decrease_pending_since, d.recheck_in) == (True, None, None)

    def test_increase_writes_now(self):
        assert self.decide(7.5, 4.0).write

    def test_equal_is_a_no_op_and_clears_pending(self):
        d = self.decide(4.0, 4.0, pending_since=900.0)
        assert (d.write, d.decrease_pending_since, d.recheck_in) == (False, None, None)

    def test_first_decrease_starts_pending(self):
        d = self.decide(4.0, 7.5)
        assert (d.write, d.decrease_pending_since, d.recheck_in) == (False, 1000.0, 300.0)

    def test_decrease_still_pending_keeps_its_start(self):
        d = self.decide(4.0, 7.5, pending_since=900.0)
        assert not d.write
        assert d.decrease_pending_since == 900.0
        assert d.recheck_in == pytest.approx(200.0)

    def test_decrease_written_once_interval_elapsed(self):
        d = self.decide(4.0, 7.5, pending_since=700.0)
        assert (d.write, d.decrease_pending_since) == (True, None)

    def test_increase_clears_pending(self):
        d = self.decide(9.0, 7.5, pending_since=900.0)
        assert (d.write, d.decrease_pending_since) == (True, None)

    def test_idle_decrease_writes_now(self):
        assert self.decide(0.0, 7.5, idle=True).write

    def test_idle_at_current_value_is_a_no_op(self):
        assert not self.decide(5.0, 5.0, idle=True).write

    def test_zero_interval_writes_decrease_now(self):
        assert self.decide(4.0, 7.5, interval=0.0).write
