"""Tests for cycle metrics recorder."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock

import pytest

from custom_components.adaptive_climate.const import HeatingType
from custom_components.adaptive_climate.managers.cycle_metrics import CycleMetricsRecorder


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


@pytest.fixture
def mock_adaptive_learner():
    """Create a mock adaptive learner."""
    learner = MagicMock()
    learner.add_cycle_metrics = MagicMock()
    learner.update_convergence_tracking = MagicMock()
    learner.update_convergence_confidence = MagicMock()
    learner.is_in_validation_mode = MagicMock(return_value=False)
    return learner


@pytest.fixture
def mock_callbacks():
    """Create mock callback functions."""
    return {
        "get_target_temp": Mock(return_value=20.0),
        "get_current_temp": Mock(return_value=18.0),
        "get_hvac_mode": Mock(return_value="heat"),
        "get_in_grace_period": Mock(return_value=False),
    }


class TestExtendedSettlingWindow:
    """Test extended settling window for slow systems."""

    def test_settling_window_by_heating_type(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Settling window varies by heating type."""
        # Create recorder for floor_hydronic (slowest system)
        floor_recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_floor",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )

        # Create recorder for forced_air (fastest system)
        forced_recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_forced",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FORCED_AIR,
        )

        # Verify settling windows match const.py expectations
        assert floor_recorder.get_settling_window_minutes() == 60
        assert forced_recorder.get_settling_window_minutes() == 10

    def test_settling_window_defaults_to_30_when_no_heating_type(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Settling window defaults to 30 minutes when heating_type is None."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_no_type",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=None,
        )

        # Should default to 30 minutes (radiator-like default)
        assert recorder.get_settling_window_minutes() == 30


class TestRiseTimeThreshold:
    """Test rise_time calculation uses heating-type-specific thresholds."""

    def test_rise_time_uses_floor_hydronic_threshold(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Rise time calculation uses 0.5°C threshold for floor_hydronic."""
        from datetime import timedelta
        from unittest.mock import patch

        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_floor",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            cold_tolerance=0.5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )

        # Create temperature history reaching target within 0.5°C (but not 0.05°C)
        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        target_temp = 20.0

        temperature_history = [
            (cycle_start, 18.0),
            (cycle_start + timedelta(minutes=10), 18.5),
            (cycle_start + timedelta(minutes=20), 19.0),
            (cycle_start + timedelta(minutes=30), 19.5),
            (cycle_start + timedelta(minutes=40), 19.75),  # Within 0.5°C of target
            (cycle_start + timedelta(minutes=50), 19.8),
        ]

        # Mock calculate_rise_time to capture the threshold parameter
        with patch("custom_components.adaptive_climate.adaptive.cycle_analysis.calculate_rise_time") as mock_calc:
            mock_calc.return_value = 40.0  # 40 minutes to reach target

            # Record cycle metrics (this calls calculate_rise_time internally)
            recorder.record_cycle_metrics(
                cycle_start_time=cycle_start,
                cycle_target_temp=target_temp,
                cycle_state_value="settling",
                temperature_history=temperature_history,
                outdoor_temp_history=[],
            )

            # Verify calculate_rise_time was called with 0.5°C threshold (floor_hydronic)
            mock_calc.assert_called_once()
            call_kwargs = mock_calc.call_args[1]
            assert "threshold" in call_kwargs
            assert call_kwargs["threshold"] == 0.5

    def test_rise_time_uses_forced_air_threshold(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Rise time calculation uses 0.15°C threshold for forced_air."""
        from datetime import timedelta
        from unittest.mock import patch

        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_forced",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            cold_tolerance=0.15,
            heating_type=HeatingType.FORCED_AIR,
        )

        # Create temperature history
        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        target_temp = 20.0

        temperature_history = [
            (cycle_start, 18.0),
            (cycle_start + timedelta(minutes=5), 18.5),
            (cycle_start + timedelta(minutes=10), 19.0),
            (cycle_start + timedelta(minutes=15), 19.5),
            (cycle_start + timedelta(minutes=20), 19.9),  # Within 0.15°C of target
        ]

        # Mock calculate_rise_time to capture the threshold parameter
        with patch("custom_components.adaptive_climate.adaptive.cycle_analysis.calculate_rise_time") as mock_calc:
            mock_calc.return_value = 20.0  # 20 minutes to reach target

            # Record cycle metrics
            recorder.record_cycle_metrics(
                cycle_start_time=cycle_start,
                cycle_target_temp=target_temp,
                cycle_state_value="settling",
                temperature_history=temperature_history,
                outdoor_temp_history=[],
            )

            # Verify calculate_rise_time was called with 0.15°C threshold (forced_air)
            mock_calc.assert_called_once()
            call_kwargs = mock_calc.call_args[1]
            assert "threshold" in call_kwargs
            assert call_kwargs["threshold"] == 0.15

    def test_rise_time_defaults_to_0_2_when_no_cold_tolerance(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Rise time calculation defaults to 0.2°C threshold when cold_tolerance is None."""
        from datetime import timedelta
        from unittest.mock import patch

        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_no_type",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=None,
        )

        # Create temperature history (need at least 5 samples)
        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        target_temp = 20.0

        temperature_history = [
            (cycle_start, 18.0),
            (cycle_start + timedelta(minutes=5), 18.5),
            (cycle_start + timedelta(minutes=10), 19.0),
            (cycle_start + timedelta(minutes=15), 19.5),
            (cycle_start + timedelta(minutes=20), 20.0),
        ]

        # Mock calculate_rise_time to capture the threshold parameter
        with patch("custom_components.adaptive_climate.adaptive.cycle_analysis.calculate_rise_time") as mock_calc:
            mock_calc.return_value = 20.0

            # Record cycle metrics
            recorder.record_cycle_metrics(
                cycle_start_time=cycle_start,
                cycle_target_temp=target_temp,
                cycle_state_value="settling",
                temperature_history=temperature_history,
                outdoor_temp_history=[],
            )

            # Verify calculate_rise_time was called without threshold parameter
            # (will use default 0.2°C from calculate_rise_time function)
            mock_calc.assert_called_once()
            call_kwargs = mock_calc.call_args[1]
            assert "threshold" not in call_kwargs


class TestUndershootIgnoresColdStart:
    """Undershoot must not report the gap the cycle started with once the room warmed up."""

    @staticmethod
    def _recovery_history() -> list[tuple[datetime, float]]:
        return [
            (datetime(2025, 1, 15, 6, 0, tzinfo=timezone.utc), 18.0),
            (datetime(2025, 1, 15, 6, 30, tzinfo=timezone.utc), 18.8),
            (datetime(2025, 1, 15, 7, 0, tzinfo=timezone.utc), 19.6),
            (datetime(2025, 1, 15, 7, 30, tzinfo=timezone.utc), 20.1),
            (datetime(2025, 1, 15, 8, 0, tzinfo=timezone.utc), 20.2),
            (datetime(2025, 1, 15, 8, 30, tzinfo=timezone.utc), 19.9),
        ]

    def _record(self, recorder: CycleMetricsRecorder, learner: MagicMock):
        history = self._recovery_history()
        recorder.record_cycle_metrics(
            cycle_start_time=history[0][0],
            cycle_target_temp=20.0,
            cycle_state_value="settling",
            temperature_history=history,
            outdoor_temp_history=[],
        )
        return learner.add_cycle_metrics.call_args[0][0]

    def _recorder(self, mock_hass, mock_adaptive_learner, mock_callbacks) -> CycleMetricsRecorder:
        return CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_undershoot",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )

    def test_no_device_off_time(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        recorder = self._recorder(mock_hass, mock_adaptive_learner, mock_callbacks)

        metrics = self._record(recorder, mock_adaptive_learner)

        assert metrics.starting_delta == pytest.approx(2.0)
        assert metrics.undershoot == pytest.approx(0.1)

    def test_device_off_time_from_previous_cycle(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        recorder = self._recorder(mock_hass, mock_adaptive_learner, mock_callbacks)
        recorder.set_device_off_time(datetime(2025, 1, 14, 22, 0, tzinfo=timezone.utc))

        metrics = self._record(recorder, mock_adaptive_learner)

        assert metrics.undershoot == pytest.approx(0.1)

    def test_settling_window_after_last_sample(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        recorder = self._recorder(mock_hass, mock_adaptive_learner, mock_callbacks)
        recorder.set_device_off_time(datetime(2025, 1, 15, 8, 25, tzinfo=timezone.utc))
        recorder.set_transport_delay(15.0)

        metrics = self._record(recorder, mock_adaptive_learner)

        assert metrics.undershoot == pytest.approx(0.1)

    def test_metrics_free_of_float_noise(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """0.1° readings give overshoot, undershoot and starting_delta of exactly 0.3, not 0.3000000000000007."""
        recorder = self._recorder(mock_hass, mock_adaptive_learner, mock_callbacks)
        history = [
            (datetime(2025, 1, 15, 6, 0, tzinfo=timezone.utc), 20.7),
            (datetime(2025, 1, 15, 6, 30, tzinfo=timezone.utc), 21.1),
            (datetime(2025, 1, 15, 7, 0, tzinfo=timezone.utc), 21.3),
            (datetime(2025, 1, 15, 7, 30, tzinfo=timezone.utc), 20.9),
            (datetime(2025, 1, 15, 8, 0, tzinfo=timezone.utc), 20.7),
        ]
        recorder.record_cycle_metrics(
            cycle_start_time=history[0][0],
            cycle_target_temp=21.0,
            cycle_state_value="settling",
            temperature_history=history,
            outdoor_temp_history=[],
        )
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]

        assert (metrics.overshoot, metrics.undershoot, metrics.starting_delta) == (0.3, 0.3, 0.3)


class TestStartingDeltaCalculation:
    """Test starting_delta calculation for weighted learning."""

    def test_starting_delta_calculated_and_passed_to_cycle_metrics(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Starting delta is calculated from target_temp - start_temp and passed to CycleMetrics."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_starting_delta",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )

        # Create temperature history starting at 18.0°C
        start_time = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        temperature_history = [
            (datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc), 18.0),
            (datetime(2025, 1, 15, 10, 5, 0, tzinfo=timezone.utc), 18.5),
            (datetime(2025, 1, 15, 10, 10, 0, tzinfo=timezone.utc), 19.0),
            (datetime(2025, 1, 15, 10, 15, 0, tzinfo=timezone.utc), 19.5),
            (datetime(2025, 1, 15, 10, 20, 0, tzinfo=timezone.utc), 20.0),
            (datetime(2025, 1, 15, 10, 25, 0, tzinfo=timezone.utc), 20.2),
        ]

        # Target temp is 20.0 (from mock_callbacks)
        # Start temp is 18.0
        # Expected starting_delta = 20.0 - 18.0 = 2.0

        # Record cycle
        recorder.record_cycle_metrics(
            cycle_start_time=start_time,
            cycle_target_temp=20.0,
            cycle_state_value="heating",
            temperature_history=temperature_history,
            outdoor_temp_history=[],
        )

        # Verify add_cycle_metrics was called
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1

        # Extract the CycleMetrics object that was passed
        call_args = mock_adaptive_learner.add_cycle_metrics.call_args
        cycle_metrics = call_args[0][0]

        # Verify starting_delta is calculated correctly
        assert cycle_metrics.starting_delta == 2.0

    def test_inter_cycle_drift_rounded(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """20.7 - 21.0 must compare as -0.3 against the drift rule threshold."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_drift",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )
        recorder._prev_cycle_end_temp = 21.0
        start_time = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        temperature_history = [
            (datetime(2025, 1, 15, 10, minute, 0, tzinfo=timezone.utc), 20.7 + minute * 0.01)
            for minute in range(0, 30, 5)
        ]

        recorder.record_cycle_metrics(
            cycle_start_time=start_time,
            cycle_target_temp=21.0,
            cycle_state_value="heating",
            temperature_history=temperature_history,
            outdoor_temp_history=[],
        )

        assert mock_adaptive_learner.add_cycle_metrics.call_args[0][0].inter_cycle_drift == -0.3

    def test_starting_delta_with_cooling_mode(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Starting delta is calculated for cooling mode (temp - target)."""
        # Modify callbacks for cooling mode
        mock_callbacks["get_hvac_mode"].return_value = "cool"

        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_cooling_delta",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FORCED_AIR,
        )

        # Create temperature history starting at 22.0°C (above target)
        start_time = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        temperature_history = [
            (datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc), 22.0),
            (datetime(2025, 1, 15, 10, 5, 0, tzinfo=timezone.utc), 21.5),
            (datetime(2025, 1, 15, 10, 10, 0, tzinfo=timezone.utc), 21.0),
            (datetime(2025, 1, 15, 10, 15, 0, tzinfo=timezone.utc), 20.5),
            (datetime(2025, 1, 15, 10, 20, 0, tzinfo=timezone.utc), 20.0),
            (datetime(2025, 1, 15, 10, 25, 0, tzinfo=timezone.utc), 19.8),
        ]

        # Target temp is 20.0
        # Start temp is 22.0
        # Expected starting_delta = 20.0 - 22.0 = -2.0 (negative because cooling)

        # Record cycle
        recorder.record_cycle_metrics(
            cycle_start_time=start_time,
            cycle_target_temp=20.0,
            cycle_state_value="cooling",
            temperature_history=temperature_history,
            outdoor_temp_history=[],
        )

        # Verify add_cycle_metrics was called
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1

        # Extract the CycleMetrics object that was passed
        call_args = mock_adaptive_learner.add_cycle_metrics.call_args
        cycle_metrics = call_args[0][0]

        # Verify starting_delta is calculated correctly (target - actual = negative for cooling)
        assert cycle_metrics.starting_delta == -2.0


class TestCoolingCycleLearnerMode:
    """H08: add_cycle_metrics / update_convergence_confidence must receive mode kwarg."""

    def test_cooling_cycle_passes_cool_mode_to_learner(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """H08: a COOL cycle must pass mode='cool' to add_cycle_metrics, not default to HEAT."""
        mock_callbacks["get_hvac_mode"].return_value = "cool"

        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_h08_cooling",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FORCED_AIR,
        )

        # Capture cycle mode at start (simulates CycleTracker calling set_cycle_mode)
        recorder.set_cycle_mode("cool")

        start_time = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        # Need >= 5 samples to pass _is_cycle_valid
        temperature_history = [
            (datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc), 22.0),
            (datetime(2025, 1, 15, 10, 5, 0, tzinfo=timezone.utc), 21.5),
            (datetime(2025, 1, 15, 10, 10, 0, tzinfo=timezone.utc), 21.0),
            (datetime(2025, 1, 15, 10, 15, 0, tzinfo=timezone.utc), 20.5),
            (datetime(2025, 1, 15, 10, 20, 0, tzinfo=timezone.utc), 20.0),
        ]

        recorder.record_cycle_metrics(
            cycle_start_time=start_time,
            cycle_target_temp=20.0,
            cycle_state_value="cooling",
            temperature_history=temperature_history,
            outdoor_temp_history=[],
        )

        # add_cycle_metrics must be called with mode="cool" (not None / defaulting to HEAT)
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1
        add_call = mock_adaptive_learner.add_cycle_metrics.call_args
        assert add_call[1].get("mode") == "cool", (
            f"Expected mode='cool', got mode={add_call[1].get('mode')!r}. "
            "Cooling cycles were defaulting to HEAT because mode kwarg was omitted."
        )

        # update_convergence_confidence must also receive mode="cool"
        assert mock_adaptive_learner.update_convergence_confidence.call_count == 1
        conf_call = mock_adaptive_learner.update_convergence_confidence.call_args
        assert conf_call[1].get("mode") == "cool"

    def test_heating_cycle_passes_heat_mode_to_learner(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """H08 regression: heating cycles still pass mode='heat' (not None)."""
        mock_callbacks["get_hvac_mode"].return_value = "heat"

        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_h08_heating",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.CONVECTOR,
        )

        recorder.set_cycle_mode("heat")

        start_time = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        # Need 5+ samples for valid cycle
        temperature_history = [
            (datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc), 18.0),
            (datetime(2025, 1, 15, 10, 5, 0, tzinfo=timezone.utc), 18.5),
            (datetime(2025, 1, 15, 10, 10, 0, tzinfo=timezone.utc), 19.0),
            (datetime(2025, 1, 15, 10, 15, 0, tzinfo=timezone.utc), 19.5),
            (datetime(2025, 1, 15, 10, 20, 0, tzinfo=timezone.utc), 20.0),
            (datetime(2025, 1, 15, 10, 25, 0, tzinfo=timezone.utc), 20.0),
        ]

        recorder.record_cycle_metrics(
            cycle_start_time=start_time,
            cycle_target_temp=20.0,
            cycle_state_value="settling",
            temperature_history=temperature_history,
            outdoor_temp_history=[],
        )

        add_call = mock_adaptive_learner.add_cycle_metrics.call_args
        assert add_call is not None, "add_cycle_metrics was not called (cycle may have been invalid)"
        assert add_call[1].get("mode") == "heat"


class TestCycleEndedEventOrdering:
    """Test that CYCLE_ENDED event is emitted before learning save is scheduled (M08)."""

    def test_emit_before_save_scheduling(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """CYCLE_ENDED event must be emitted BEFORE _schedule_learning_save is called.

        Handlers that mutate the learner (e.g., heating-rate updaters) subscribe to
        CYCLE_ENDED and fire synchronously during emit().  If save is scheduled first,
        the snapshot is taken before those mutations, so learner state is saved stale.

        Fix: emit first, then schedule save.
        """
        from datetime import timedelta
        from unittest.mock import MagicMock

        from custom_components.adaptive_climate.const import DOMAIN
        from custom_components.adaptive_climate.managers.events import (
            CycleEventDispatcher,
            CycleEventType,
        )

        # Track call order using a shared list mutated by side-effects
        call_order: list[str] = []

        # Set up dispatcher with a subscriber that records when emit fires
        dispatcher = CycleEventDispatcher()
        dispatcher.subscribe(
            CycleEventType.CYCLE_ENDED,
            lambda _: call_order.append("emit"),
        )

        # Set up mock learning store whose update_zone_data records when save fires
        def _record_save(*args: object, **kwargs: object) -> None:
            del args, kwargs  # intentionally unused — side_effect just logs to call_order
            call_order.append("save")

        mock_learning_store = MagicMock()
        mock_learning_store.update_zone_data = MagicMock(side_effect=_record_save)
        mock_learning_store.schedule_zone_save = MagicMock()

        # Wire hass.data so _schedule_learning_save finds the store
        mock_hass.data = {DOMAIN: {"learning_store": mock_learning_store}}

        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_order",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
            dispatcher=dispatcher,
        )

        # Build a valid temperature history (6 samples, 25 min cycle)
        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        temperature_history = [(cycle_start + timedelta(minutes=i * 5), 18.0 + i * 0.4) for i in range(6)]

        # Act: record cycle metrics
        recorder.record_cycle_metrics(
            cycle_start_time=cycle_start,
            cycle_target_temp=20.0,
            cycle_state_value="settling",
            temperature_history=temperature_history,
            outdoor_temp_history=[],
        )

        # Assert ordering: emit must appear before save in the call log
        assert "emit" in call_order, "CYCLE_ENDED event was never emitted"
        assert "save" in call_order, "Learning save was never scheduled"
        emit_idx = call_order.index("emit")
        save_idx = call_order.index("save")
        assert emit_idx < save_idx, (
            f"Expected emit (idx={emit_idx}) before save (idx={save_idx}), got call order: {call_order}"
        )


class TestCycleEndedEventDuty:
    """H09 regression: CYCLE_ENDED metrics_dict must include 'duty' field.

    Root cause: the metrics_dict built for CycleEndedEvent omitted 'duty', so
    _handle_cycle_ended_for_heating_rate() always got duty=None and never called
    heating_rate_learner.update_session().
    """

    def _make_recorder_with_dispatcher(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Create a CycleMetricsRecorder wired to a real CycleEventDispatcher."""
        from custom_components.adaptive_climate.managers.events import CycleEventDispatcher

        dispatcher = CycleEventDispatcher()
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_duty",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.CONVECTOR,
            dispatcher=dispatcher,
        )
        return recorder, dispatcher

    def _make_temperature_history(self, cycle_start: datetime) -> list:
        from datetime import timedelta

        return [
            (cycle_start + timedelta(minutes=i * 5), 18.0 + i * 0.4)
            for i in range(6)  # 25-minute cycle, ends at 20°C
        ]

    def test_cycle_ended_event_contains_duty_field(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """CYCLE_ENDED event metrics must include 'duty' key."""
        from custom_components.adaptive_climate.managers.events import CycleEventType

        recorder, dispatcher = self._make_recorder_with_dispatcher(mock_hass, mock_adaptive_learner, mock_callbacks)

        # Simulate heater on for 10 of the 25-minute cycle
        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        from datetime import timedelta

        recorder.set_device_on_time(cycle_start)
        recorder.set_device_off_time(cycle_start + timedelta(minutes=10))

        emitted_events: list = []
        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, emitted_events.append)

        recorder.record_cycle_metrics(
            cycle_start_time=cycle_start,
            cycle_target_temp=20.0,
            cycle_state_value="settling",
            temperature_history=self._make_temperature_history(cycle_start),
            outdoor_temp_history=[],
        )

        assert len(emitted_events) == 1, "Expected one CYCLE_ENDED event"
        event = emitted_events[0]
        assert "duty" in event.metrics, (
            f"CYCLE_ENDED metrics must contain 'duty'. Got keys: {list(event.metrics.keys())}"
        )

    def test_cycle_ended_duty_is_fraction(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """CYCLE_ENDED duty must be a float in [0, 1]."""
        from custom_components.adaptive_climate.managers.events import CycleEventType
        from datetime import timedelta

        recorder, dispatcher = self._make_recorder_with_dispatcher(mock_hass, mock_adaptive_learner, mock_callbacks)

        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        # Heater on for 10 min of a 25-min cycle → duty ≈ 0.4
        recorder.set_device_on_time(cycle_start)
        recorder.set_device_off_time(cycle_start + timedelta(minutes=10))

        captured: list = []
        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, captured.append)

        recorder.record_cycle_metrics(
            cycle_start_time=cycle_start,
            cycle_target_temp=20.0,
            cycle_state_value="settling",
            temperature_history=self._make_temperature_history(cycle_start),
            outdoor_temp_history=[],
        )

        duty = captured[0].metrics.get("duty")
        assert duty is not None
        assert 0.0 <= duty <= 1.0, f"duty must be in [0, 1], got {duty}"

    def test_cycle_ended_duty_none_when_no_device_times(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """When device on/off times are not set, duty should be None (not crash)."""
        from custom_components.adaptive_climate.managers.events import CycleEventType

        recorder, dispatcher = self._make_recorder_with_dispatcher(mock_hass, mock_adaptive_learner, mock_callbacks)
        # No set_device_on_time / set_device_off_time calls

        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        captured: list = []
        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, captured.append)

        recorder.record_cycle_metrics(
            cycle_start_time=cycle_start,
            cycle_target_temp=20.0,
            cycle_state_value="settling",
            temperature_history=self._make_temperature_history(cycle_start),
            outdoor_temp_history=[],
        )

        # Event was still emitted, duty is None (acceptable fallback)
        assert len(captured) == 1
        assert "duty" in captured[0].metrics


class TestKeDataPersistedWithCycleSave:
    """M07: ke_data must be included in update_zone_data when saving after a cycle."""

    def _make_recorder(self, mock_hass, mock_adaptive_learner, mock_callbacks, ke_manager=None):
        """Build a CycleMetricsRecorder with an optional ke_manager."""
        return CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_ke_persist",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.RADIATOR,
            ke_manager=ke_manager,
        )

    def _run_one_cycle(self, recorder, mock_hass):
        """Wire a learning store and record a valid 6-sample cycle."""
        from datetime import timedelta
        from custom_components.adaptive_climate.const import DOMAIN

        mock_store = MagicMock()
        mock_store.schedule_zone_save = MagicMock()
        captured = {}
        mock_store.update_zone_data = MagicMock(side_effect=lambda **kw: captured.update(kw))
        mock_hass.data = {DOMAIN: {"learning_store": mock_store}}

        cycle_start = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        temperature_history = [(cycle_start + timedelta(minutes=i * 5), 18.0 + i * 0.4) for i in range(6)]
        recorder.record_cycle_metrics(
            cycle_start_time=cycle_start,
            cycle_target_temp=20.0,
            cycle_state_value="settling",
            temperature_history=temperature_history,
            outdoor_temp_history=[],
        )
        return captured, mock_store

    def test_ke_data_included_when_ke_manager_present(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """M07: update_zone_data must receive ke_data when ke_manager is set."""
        ke_manager = MagicMock()
        ke_manager.get_learner_dict.return_value = {"ke": 0.5, "observations": 3}

        recorder = self._make_recorder(mock_hass, mock_adaptive_learner, mock_callbacks, ke_manager)
        captured, _ = self._run_one_cycle(recorder, mock_hass)

        assert "ke_data" in captured, "ke_data key missing from update_zone_data call"
        assert captured["ke_data"] == {"ke": 0.5, "observations": 3}
        ke_manager.get_learner_dict.assert_called_once()

    def test_ke_data_none_when_no_ke_manager(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """M07: ke_data=None when no ke_manager provided (backwards-compat)."""
        recorder = self._make_recorder(mock_hass, mock_adaptive_learner, mock_callbacks, ke_manager=None)
        captured, _ = self._run_one_cycle(recorder, mock_hass)

        assert captured.get("ke_data") is None

    def test_ke_data_none_when_learner_dict_returns_none(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """M07: ke_data=None when get_learner_dict() returns None (no KeLearner)."""
        ke_manager = MagicMock()
        ke_manager.get_learner_dict.return_value = None

        recorder = self._make_recorder(mock_hass, mock_adaptive_learner, mock_callbacks, ke_manager)
        captured, _ = self._run_one_cycle(recorder, mock_hass)

        assert captured.get("ke_data") is None


class TestKeManagerGetLearnerDict:
    """M07: KeManager.get_learner_dict() must delegate to KeLearner.to_dict()."""

    def test_returns_learner_to_dict_when_present(self):
        """get_learner_dict() returns KeLearner.to_dict() result."""
        from custom_components.adaptive_climate.managers.ke_manager import KeManager

        ke_manager = MagicMock(spec=KeManager)
        ke_manager.get_learner_dict = KeManager.get_learner_dict.__get__(ke_manager)

        mock_learner = MagicMock()
        mock_learner.to_dict.return_value = {"ke": 0.4, "n": 5}
        ke_manager._ke_learner = mock_learner

        result = ke_manager.get_learner_dict()
        assert result == {"ke": 0.4, "n": 5}
        mock_learner.to_dict.assert_called_once()

    def test_returns_none_when_no_learner(self):
        """get_learner_dict() returns None when _ke_learner is None."""
        from custom_components.adaptive_climate.managers.ke_manager import KeManager

        ke_manager = MagicMock(spec=KeManager)
        ke_manager.get_learner_dict = KeManager.get_learner_dict.__get__(ke_manager)
        ke_manager._ke_learner = None

        result = ke_manager.get_learner_dict()
        assert result is None
