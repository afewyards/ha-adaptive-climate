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
