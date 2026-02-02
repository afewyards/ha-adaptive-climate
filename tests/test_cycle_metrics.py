"""Tests for cycle metrics recorder."""

from datetime import datetime
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

    def test_settling_start_includes_transport_delay(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Settling window starts after transport delay."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_transport",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )

        # Set transport delay (5 minutes)
        recorder.set_transport_delay(5.0)

        # Set device off time
        device_off_time = datetime(2025, 1, 15, 10, 30, 0)
        recorder.set_device_off_time(device_off_time)

        # Get settling start time (should account for transport delay)
        settling_start = recorder.get_settling_start_time()

        # Settling should start 5 minutes after device turned off (transport delay)
        expected_start = datetime(2025, 1, 15, 10, 35, 0)
        assert settling_start == expected_start

    def test_settling_start_includes_valve_actuation(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Settling window starts after valve actuation time."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_valve",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
            valve_actuation_time=120.0,  # 2 minutes in seconds
        )

        # Set device off time
        device_off_time = datetime(2025, 1, 15, 10, 30, 0)
        recorder.set_device_off_time(device_off_time)

        # Get settling start time (should account for half valve actuation time)
        settling_start = recorder.get_settling_start_time()

        # Settling should start 1 minute after device off (half of 2 min valve time)
        expected_start = datetime(2025, 1, 15, 10, 31, 0)
        assert settling_start == expected_start

    def test_settling_start_includes_both_delays(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Settling window starts after both valve actuation and transport delay."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_both",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
            valve_actuation_time=120.0,  # 2 minutes in seconds
        )

        # Set transport delay (5 minutes)
        recorder.set_transport_delay(5.0)

        # Set device off time
        device_off_time = datetime(2025, 1, 15, 10, 30, 0)
        recorder.set_device_off_time(device_off_time)

        # Get settling start time (should account for both delays)
        settling_start = recorder.get_settling_start_time()

        # Settling should start 6 minutes after device off:
        # - Half valve actuation: 1 min
        # - Transport delay: 5 min
        expected_start = datetime(2025, 1, 15, 10, 36, 0)
        assert settling_start == expected_start

    def test_settling_start_returns_device_off_when_no_delays(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Settling starts at device off time when no delays are present."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_no_delays",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.CONVECTOR,
        )

        # Set device off time
        device_off_time = datetime(2025, 1, 15, 10, 30, 0)
        recorder.set_device_off_time(device_off_time)

        # Get settling start time (should be same as device off time)
        settling_start = recorder.get_settling_start_time()

        assert settling_start == device_off_time

    def test_settling_start_returns_none_when_no_device_off(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Settling start returns None when device_off_time is not set."""
        recorder = CycleMetricsRecorder(
            hass=mock_hass,
            zone_id="test_no_off",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            min_cycle_duration_minutes=5,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )

        # Don't set device_off_time

        # Get settling start time (should return None)
        settling_start = recorder.get_settling_start_time()

        assert settling_start is None


class TestRiseTimeThreshold:
    """Test rise_time calculation uses heating-type-specific thresholds."""

    def test_rise_time_uses_floor_hydronic_threshold(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
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
        cycle_start = datetime(2025, 1, 15, 10, 0, 0)
        target_temp = 20.0
        start_temp = 18.0

        temperature_history = [
            (cycle_start, 18.0),
            (cycle_start + timedelta(minutes=10), 18.5),
            (cycle_start + timedelta(minutes=20), 19.0),
            (cycle_start + timedelta(minutes=30), 19.5),
            (cycle_start + timedelta(minutes=40), 19.75),  # Within 0.5°C of target
            (cycle_start + timedelta(minutes=50), 19.8),
        ]

        # Mock calculate_rise_time to capture the threshold parameter
        with patch('custom_components.adaptive_climate.adaptive.cycle_analysis.calculate_rise_time') as mock_calc:
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
            assert 'threshold' in call_kwargs
            assert call_kwargs['threshold'] == 0.5

    def test_rise_time_uses_forced_air_threshold(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
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
        cycle_start = datetime(2025, 1, 15, 10, 0, 0)
        target_temp = 20.0

        temperature_history = [
            (cycle_start, 18.0),
            (cycle_start + timedelta(minutes=5), 18.5),
            (cycle_start + timedelta(minutes=10), 19.0),
            (cycle_start + timedelta(minutes=15), 19.5),
            (cycle_start + timedelta(minutes=20), 19.9),  # Within 0.15°C of target
        ]

        # Mock calculate_rise_time to capture the threshold parameter
        with patch('custom_components.adaptive_climate.adaptive.cycle_analysis.calculate_rise_time') as mock_calc:
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
            assert 'threshold' in call_kwargs
            assert call_kwargs['threshold'] == 0.15

    def test_rise_time_defaults_to_0_2_when_no_cold_tolerance(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
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
        cycle_start = datetime(2025, 1, 15, 10, 0, 0)
        target_temp = 20.0

        temperature_history = [
            (cycle_start, 18.0),
            (cycle_start + timedelta(minutes=5), 18.5),
            (cycle_start + timedelta(minutes=10), 19.0),
            (cycle_start + timedelta(minutes=15), 19.5),
            (cycle_start + timedelta(minutes=20), 20.0),
        ]

        # Mock calculate_rise_time to capture the threshold parameter
        with patch('custom_components.adaptive_climate.adaptive.cycle_analysis.calculate_rise_time') as mock_calc:
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
            assert 'threshold' not in call_kwargs


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
        start_time = datetime(2025, 1, 15, 10, 0, 0)
        temperature_history = [
            (datetime(2025, 1, 15, 10, 0, 0), 18.0),
            (datetime(2025, 1, 15, 10, 5, 0), 18.5),
            (datetime(2025, 1, 15, 10, 10, 0), 19.0),
            (datetime(2025, 1, 15, 10, 15, 0), 19.5),
            (datetime(2025, 1, 15, 10, 20, 0), 20.0),
            (datetime(2025, 1, 15, 10, 25, 0), 20.2),
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

    def test_starting_delta_with_cooling_mode(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
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
        start_time = datetime(2025, 1, 15, 10, 0, 0)
        temperature_history = [
            (datetime(2025, 1, 15, 10, 0, 0), 22.0),
            (datetime(2025, 1, 15, 10, 5, 0), 21.5),
            (datetime(2025, 1, 15, 10, 10, 0), 21.0),
            (datetime(2025, 1, 15, 10, 15, 0), 20.5),
            (datetime(2025, 1, 15, 10, 20, 0), 20.0),
            (datetime(2025, 1, 15, 10, 25, 0), 19.8),
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
