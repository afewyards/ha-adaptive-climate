"""The undershoot detector judges only what more Ki could fix."""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_climate.adaptive.undershoot_detector import UndershootDetector
from custom_components.adaptive_climate.const import (
    DOMAIN,
    MIN_CYCLES_FOR_LEARNING,
    UNDERSHOOT_THRESHOLDS,
    HeatingType,
)
from custom_components.adaptive_climate.managers.cycle_tracker import CycleTrackerManager
from custom_components.adaptive_climate.managers.events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    SettlingStartedEvent,
)

FLOOR = HeatingType.FLOOR_HYDRONIC
HOUR = 3600.0
NOW = 100 * HOUR


class _Mode(str):
    """HVACMode members are plain strings under the test mocks; the loop reads .value."""

    @property
    def value(self) -> str:
        return str(self)


def loop_module():
    """Looked up late: other tests reload the integration's modules and swap HA mocks."""
    return importlib.import_module("custom_components.adaptive_climate.climate_control")


def hvac_mode(name: str):
    mode = getattr(loop_module().HVACMode, name)
    return _Mode(mode) if isinstance(mode, str) else mode


def failing_cycle(**overrides) -> CycleMetrics:
    """A long floor cycle that never reached the setpoint and stayed 0.6° short."""
    fields = {"undershoot": 0.6, "overshoot": None, "rise_time": None, "settling_time": 90.0, "starting_delta": 2.0}
    return CycleMetrics(**{**fields, **overrides})


def clean_cycle(**overrides) -> CycleMetrics:
    fields = {"undershoot": 0.1, "overshoot": 0.1, "rise_time": 60.0, "settling_time": 30.0, "starting_delta": 2.0}
    return CycleMetrics(**{**fields, **overrides})


def thermostat(learner: AdaptiveLearner, current: float, base: float, effective: float, saturated: bool = False):
    """Mock thermostat that reaches the undershoot block of the control loop."""
    coordinator = MagicMock()
    coordinator.get_zone_data.return_value = {"adaptive_learner": learner}
    t = MagicMock()
    t.hass.data = {DOMAIN: {"coordinator": coordinator}}
    t._temp_lock = asyncio.Lock()
    t._active = True
    t._hvac_mode = hvac_mode("HEAT")
    t._zone_id = "bedroom"
    t._current_temp = current
    t._target_temp = base
    t._calculate_night_setback_adjustment.return_value = (effective, True, {})
    t._output_saturated = saturated
    t._status_manager.is_paused.return_value = False
    t._sensor_stall = 0
    t.calc_output = AsyncMock()
    t.set_control_value = AsyncMock()
    t._cycle_dispatcher = None
    t._cycle_tracker = None
    t._night_setback_controller = None
    t.water_temp_learning_gate_active = False
    t._cold_tolerance = 0.3
    t._ext_temp = None
    t._area_m2 = None
    t._pid_controller.ki = 1.0
    t._gains_manager.get_history.return_value = []
    t._last_control_time = NOW - HOUR
    return t


def control_mixin():
    return loop_module().ClimateControlMixin


async def run_loop(t) -> None:
    """One control loop an hour after the last one."""
    mixin = control_mixin()
    with patch.dict(mixin._async_control_heating.__globals__, {"time": MagicMock(monotonic=lambda: NOW)}):
        await mixin._async_control_heating(t)


class TestEffectiveTarget:
    @pytest.mark.asyncio
    async def test_room_between_setback_and_base_target_adds_no_debt(self):
        learner = AdaptiveLearner(heating_type=FLOOR)
        await run_loop(thermostat(learner, current=19.5, base=21.0, effective=19.0))
        assert learner.undershoot_detector.thermal_debt == 0.0
        assert learner.undershoot_detector.time_below_target == 0.0

    @pytest.mark.asyncio
    async def test_room_below_the_effective_target_still_adds_debt(self):
        learner = AdaptiveLearner(heating_type=FLOOR)
        await run_loop(thermostat(learner, current=19.5, base=21.0, effective=21.0))
        assert learner.undershoot_detector.thermal_debt > 0.0

    @pytest.mark.asyncio
    async def test_saturated_output_adds_no_debt(self):
        learner = AdaptiveLearner(heating_type=FLOOR)
        await run_loop(thermostat(learner, current=19.5, base=21.0, effective=21.0, saturated=True))
        assert learner.undershoot_detector.thermal_debt == 0.0

    @pytest.mark.asyncio
    async def test_fresh_physics_ki_caps_the_boost(self):
        learner = AdaptiveLearner(heating_type=FLOOR)
        learner.undershoot_detector.time_below_target = 5 * HOUR
        t = thermostat(learner, current=19.0, base=21.0, effective=21.0)
        t._pid_controller.ki = 3.0
        physics = {"calculate_physics_gains": lambda _: (2.0, 50.0, 1.0, 10.0)}
        with patch.dict(control_mixin()._async_control_heating.__globals__, physics):
            await run_loop(t)
            t._gains_manager.set_gains.assert_not_called()

            t._pid_controller.ki = 2.0
            await run_loop(t)
        assert t._gains_manager.set_gains.call_args.kwargs["ki"] == pytest.approx(2.4)

    @pytest.mark.asyncio
    async def test_physics_error_still_actuates_with_the_cumulative_cap(self):
        def broken(_):
            raise ValueError("area_m2 is required when floor_construction is provided")

        learner = AdaptiveLearner(heating_type=FLOOR)
        learner.undershoot_detector.time_below_target = 5 * HOUR
        t = thermostat(learner, current=19.0, base=21.0, effective=21.0)
        t._pid_controller.ki = 2.0
        with patch.dict(control_mixin()._async_control_heating.__globals__, {"calculate_physics_gains": broken}):
            await run_loop(t)
        t.set_control_value.assert_awaited_once()
        assert t._gains_manager.set_gains.call_args.kwargs["ki"] == pytest.approx(2.4)


class TestOutputSaturated:
    @pytest.mark.parametrize(
        ("mode", "output", "expected"),
        [("HEAT", 95.0, True), ("HEAT", 94.9, False), ("COOL", 0.0, False)],
    )
    def test_95_percent_of_the_output_maximum(self, mode, output, expected):
        t = MagicMock(_hvac_mode=hvac_mode(mode), _control_output=output)
        t._pid_controller.out_max = 0.0 if mode == "COOL" else 100.0
        assert control_mixin()._output_saturated.fget(t) is expected


class TestRealtimeWhileSaturated:
    def test_decays_without_adding(self):
        detector = UndershootDetector(FLOOR)
        detector.update_realtime(18.0, 20.0, HOUR, 0.3)
        debt, below = detector.thermal_debt, detector.time_below_target
        detector.update_realtime(18.0, 20.0, HOUR, 0.3, saturated=True)
        assert 0.0 < detector.thermal_debt < debt
        assert 0.0 < detector.time_below_target < below

    def test_reset_above_target_still_runs(self):
        detector = UndershootDetector(FLOOR)
        detector.update_realtime(18.0, 20.0, HOUR, 0.3)
        detector.update_realtime(20.2, 20.0, 60.0, 0.3, saturated=True)
        assert detector.thermal_debt == 0.0
        assert detector.time_below_target == 0.0


class TestSaturatedCycle:
    def test_saturated_failing_cycle_neither_counts_nor_clears_the_streak(self):
        detector = UndershootDetector(FLOOR)
        detector.add_cycle(failing_cycle(), 90.0)
        detector.add_cycle(failing_cycle(output_saturated=0.5), 90.0)
        assert detector.consecutive_undershoot_cycles == 1

    def test_saturated_clean_cycle_still_clears_the_streak(self):
        detector = UndershootDetector(FLOOR)
        detector.add_cycle(failing_cycle(), 90.0)
        detector.add_cycle(clean_cycle(output_saturated=0.8), 90.0)
        assert detector.consecutive_undershoot_cycles == 0

    def test_less_than_half_saturated_still_counts(self):
        detector = UndershootDetector(FLOOR)
        detector.add_cycle(failing_cycle(output_saturated=0.49), 90.0)
        assert detector.consecutive_undershoot_cycles == 1


async def recorded_saturation(saturated_by_minute: list[bool]) -> float | None:
    learner = MagicMock()
    learner.is_in_validation_mode.return_value = False
    dispatcher = CycleEventDispatcher()
    samples = iter(saturated_by_minute)
    tracker = CycleTrackerManager(
        hass=MagicMock(),
        zone_id="up",
        adaptive_learner=learner,
        get_target_temp=lambda: 20.0,
        get_current_temp=lambda: 18.0,
        get_hvac_mode=lambda: "heat",
        get_in_grace_period=lambda: False,
        dispatcher=dispatcher,
        get_output_saturated=lambda: next(samples),
    )
    tracker.set_restoration_complete()
    start = datetime(2026, 1, 14, 6, 0)
    end = start + timedelta(minutes=len(saturated_by_minute))
    with patch("custom_components.adaptive_climate.managers.cycle_metrics.dt_util.utcnow", return_value=end):
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start, target_temp=20.0, current_temp=18.0))
        for minute in range(len(saturated_by_minute)):
            await tracker.update_temperature(start + timedelta(minutes=minute), 18.0 + minute * 0.2)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=end))
        await tracker._finalize_cycle()
    return learner.add_cycle_metrics.call_args[0][0].output_saturated


@pytest.mark.asyncio
async def test_cycle_records_the_share_of_saturated_samples():
    assert await recorded_saturation([True] * 6 + [False] * 4) == pytest.approx(0.6)


class TestKiCap:
    @staticmethod
    def boosting_learner() -> AdaptiveLearner:
        learner = AdaptiveLearner(heating_type=FLOOR)
        learner.undershoot_detector.time_below_target = 5 * HOUR
        return learner

    def test_holds_at_3x_fresh_physics_without_a_history_entry(self):
        history = [{"reason": "undershoot_ki_boost", "ki": 2.5, "timestamp": "2026-01-01T00:00:00+00:00"}]
        assert self.boosting_learner().check_undershoot_adjustment(0, 6.0, history) is not None
        assert self.boosting_learner().check_undershoot_adjustment(0, 6.0, history, physics_ki=2.0) is None

    def test_clamps_the_last_step_to_3x_fresh_physics(self):
        new_ki = self.boosting_learner().check_undershoot_adjustment(0, 5.5, [], physics_ki=2.0)
        assert new_ki == pytest.approx(6.0)

    def test_lower_of_fresh_and_history_physics_wins(self):
        history = [{"reason": "physics_init", "ki": 1.0, "timestamp": "2026-01-01T00:00:00+00:00"}]
        assert self.boosting_learner().check_undershoot_adjustment(0, 3.0, history, physics_ki=2.0) is None

    def test_cumulative_gate_still_applies_with_physics(self):
        learner = self.boosting_learner()
        learner.undershoot_detector.cumulative_ki_multiplier = 2.9
        new_ki = learner.check_undershoot_adjustment(0, 2.0, [], physics_ki=2.0)
        assert new_ki == pytest.approx(2.0 * 3.0 / 2.9)


def test_saturated_cycles_skip_cycle_mode_too():
    detector = UndershootDetector(FLOOR)
    needed = int(UNDERSHOOT_THRESHOLDS[FLOOR]["min_consecutive_cycles"])
    for _ in range(needed):
        detector.add_cycle(failing_cycle(output_saturated=1.0), 90.0)
    assert detector.should_adjust_ki(MIN_CYCLES_FOR_LEARNING) is False
