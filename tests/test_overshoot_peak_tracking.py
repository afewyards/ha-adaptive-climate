"""Overshoot counts only what the controller caused: the start excess and late peaks are left out."""

import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_climate.adaptive.cycle_analysis import calculate_overshoot
from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_climate.const import UNDERSHOOT_THRESHOLDS, HeatingType
from custom_components.adaptive_climate.managers.cycle_tracker import CycleTrackerManager
from custom_components.adaptive_climate.managers.events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    HeatingEndedEvent,
    HeatingStartedEvent,
    SettlingStartedEvent,
)

START = datetime(2026, 1, 14, 6, 0, 0)


def history(*temps):
    """One reading every 10 minutes from START."""
    return [(START + timedelta(minutes=10 * i), temp) for i, temp in enumerate(temps)]


def at(minute):
    return START + timedelta(minutes=minute)


# 20.0 → 21.3 coast peak, falls 0.2 by minute 60, then the sun lifts it to 21.8
LATE_PEAK = history(20.0, 20.6, 21.0, 21.2, 21.3, 21.2, 21.1, 21.0, 21.4, 21.8, 21.5)


class TestStartAboveTarget:
    def test_only_the_rise_above_the_start_counts(self):
        assert calculate_overshoot(history(21.3, 21.4, 21.5, 21.2), 21.0, start_temp=21.3) == pytest.approx(0.2)

    def test_falling_from_an_excess_start_is_no_overshoot(self):
        assert calculate_overshoot(history(21.4, 21.2, 21.0, 20.8), 21.0, start_temp=21.4) == 0.0

    def test_start_below_target_is_unchanged(self):
        temps = history(20.0, 20.8, 21.3, 21.1)
        assert calculate_overshoot(temps, 21.0, start_temp=20.0) == calculate_overshoot(temps, 21.0)

    def test_legacy_mode_uses_the_same_reference(self):
        assert calculate_overshoot(history(21.3, 21.5), 21.0, phase_aware=False, start_temp=21.3) == pytest.approx(0.2)


class TestPeakEndsAfterValveClose:
    def test_late_second_peak_after_a_fall_is_ignored(self, caplog):
        with caplog.at_level(logging.INFO):
            assert calculate_overshoot(LATE_PEAK, 21.0, valve_close_time=at(25)) == pytest.approx(0.3)
        lines = [r.getMessage() for r in caplog.records if "ignored a later peak" in r.getMessage()]
        assert lines == ["Overshoot: ignored a later peak 0.50°C above the controller's own after the valve closed"]

    def test_missing_valve_close_time_gives_todays_result(self, caplog):
        with caplog.at_level(logging.INFO):
            assert calculate_overshoot(LATE_PEAK, 21.0) == pytest.approx(0.8)
        assert not [r for r in caplog.records if "ignored a later peak" in r.getMessage()]

    def test_slow_coast_after_valve_close_is_counted_in_full(self):
        coast = history(20.0, 20.6, 21.0, 21.1, 21.2, 21.3, 21.4, 21.5, 21.5, 21.4, 21.4, 21.3)
        assert calculate_overshoot(coast, 21.0, valve_close_time=at(20)) == pytest.approx(0.5)

    def test_valve_close_after_the_last_sample_gives_todays_result(self):
        assert calculate_overshoot(LATE_PEAK, 21.0, valve_close_time=at(200)) == pytest.approx(0.8)

    def test_fall_is_measured_from_the_peak_since_the_close(self):
        # Pulse peak 21.4 while heating, valve closes in a dip, in-flight heat lifts it to 21.6
        pulsed = history(20.5, 21.0, 21.4, 21.2, 21.2, 21.4, 21.6, 21.4, 21.3)
        assert calculate_overshoot(pulsed, 21.0, valve_close_time=at(30)) == pytest.approx(0.6)

    def test_rise_less_than_the_fall_threshold_does_not_end_the_peak(self):
        wobble = history(20.5, 21.0, 21.3, 21.2, 21.4, 21.3)
        assert calculate_overshoot(wobble, 21.0, valve_close_time=at(20)) == pytest.approx(0.4)


async def recorded_cycle(
    temps, start_temp, on_minute=0, off_minute=None, stale_off=False, target=21.0, mode="heat", learner=None
):
    """Run one cycle through the tracker and return the CycleMetrics handed to the learner."""
    learner = learner or AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
    dispatcher = CycleEventDispatcher()
    tracker = CycleTrackerManager(
        hass=MagicMock(),
        zone_id="up",
        adaptive_learner=learner,
        get_target_temp=lambda: target,
        get_current_temp=lambda: start_temp,
        get_hvac_mode=lambda: mode,
        get_in_grace_period=lambda: False,
        dispatcher=dispatcher,
    )
    tracker.set_restoration_complete()
    end = START + timedelta(minutes=10 * len(temps))
    with (
        patch("custom_components.adaptive_climate.managers.cycle_metrics.dt_util.utcnow", return_value=end),
        patch.object(learner, "add_cycle_metrics", wraps=learner.add_cycle_metrics) as add_cycle_metrics,
    ):
        if stale_off:
            dispatcher.emit(HeatingEndedEvent(hvac_mode=mode, timestamp=START - timedelta(hours=3)))
        dispatcher.emit(CycleStartedEvent(hvac_mode=mode, timestamp=START, target_temp=target, current_temp=start_temp))
        if on_minute is not None:
            dispatcher.emit(HeatingStartedEvent(hvac_mode=mode, timestamp=at(on_minute)))
        if off_minute is not None:
            dispatcher.emit(HeatingEndedEvent(hvac_mode=mode, timestamp=at(off_minute)))
        for timestamp, temp in history(*temps):
            await tracker.update_temperature(timestamp, temp)
        dispatcher.emit(SettlingStartedEvent(hvac_mode=mode, timestamp=end))
        await tracker._finalize_cycle()
    add_cycle_metrics.assert_called_once()
    return add_cycle_metrics.call_args[0][0]


async def recorded_overshoot(temps, start_temp, **kwargs):
    return (await recorded_cycle(temps, start_temp, **kwargs)).overshoot


LATE_TEMPS = [temp for _, temp in LATE_PEAK]


class TestRecordedCycle:
    @pytest.mark.asyncio
    async def test_valve_close_ends_the_peak(self):
        assert await recorded_overshoot(LATE_TEMPS, 20.0, off_minute=25) == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_no_valve_close_keeps_the_late_peak(self):
        assert await recorded_overshoot(LATE_TEMPS, 20.0) == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_close_from_before_the_cycle_is_ignored(self):
        assert await recorded_overshoot(LATE_TEMPS, 20.0, on_minute=None, stale_off=True) == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_reopen_after_the_close_keeps_the_late_peak(self):
        assert await recorded_overshoot(LATE_TEMPS, 20.0, on_minute=40, off_minute=25) == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_start_above_target_is_measured_from_the_start(self):
        assert await recorded_overshoot([21.3, 21.4, 21.5, 21.2, 21.1, 21.0], 21.3) == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_ignored_late_peak_does_not_make_an_approach_failure(self):
        # Start 0.1 short (no rise time), reach target as the valve closes, fall to 19.8, sun to 20.3, cool to 19.5
        learner = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        temps = [19.9, 20.0, 19.8, 20.3, 19.5]
        metrics = await recorded_cycle(temps, 19.9, off_minute=10, target=20.0, learner=learner)

        assert metrics.overshoot == 0.0
        assert metrics.rise_time is None
        assert metrics.settling_time is None
        assert metrics.undershoot >= UNDERSHOOT_THRESHOLDS[HeatingType.FLOOR_HYDRONIC]["undershoot_threshold"]
        assert learner.undershoot_detector.consecutive_undershoot_cycles == 0

    @pytest.mark.asyncio
    async def test_cooling_overshoot_is_unchanged(self):
        temps = [21.3, 21.4, 21.5, 21.2, 21.0, 20.8, 21.6, 21.1]
        cooled = await recorded_overshoot(temps, 21.3, off_minute=15, mode="cool")
        assert cooled == calculate_overshoot(history(*temps), 21.0)
        assert cooled == pytest.approx(0.6)
