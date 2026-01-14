"""Tests for cycle tracker manager."""

from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest

from custom_components.adaptive_thermostat.managers.cycle_tracker import (
    CycleState,
    CycleTrackerManager,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.async_call_later = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


@pytest.fixture
def mock_adaptive_learner():
    """Create a mock adaptive learner."""
    learner = MagicMock()
    learner.add_cycle_metrics = MagicMock()
    learner.update_convergence_tracking = MagicMock()
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


@pytest.fixture
def cycle_tracker(mock_hass, mock_adaptive_learner, mock_callbacks):
    """Create a cycle tracker instance."""
    return CycleTrackerManager(
        hass=mock_hass,
        zone_id="test_zone",
        adaptive_learner=mock_adaptive_learner,
        get_target_temp=mock_callbacks["get_target_temp"],
        get_current_temp=mock_callbacks["get_current_temp"],
        get_hvac_mode=mock_callbacks["get_hvac_mode"],
        get_in_grace_period=mock_callbacks["get_in_grace_period"],
    )


class TestCycleTrackerBasic:
    """Tests for basic cycle tracker functionality."""

    def test_initial_state_is_idle(self, cycle_tracker):
        """Test that initial state is IDLE."""
        assert cycle_tracker.state == CycleState.IDLE
        assert cycle_tracker.cycle_start_time is None
        assert len(cycle_tracker.temperature_history) == 0

    def test_cycle_state_transitions(self, cycle_tracker):
        """Test state machine transitions."""
        # Start in IDLE
        assert cycle_tracker.state == CycleState.IDLE

        # IDLE -> HEATING
        cycle_tracker.on_heating_started(datetime.now())
        assert cycle_tracker.state == CycleState.HEATING

        # HEATING -> SETTLING
        cycle_tracker.on_heating_stopped(datetime.now())
        assert cycle_tracker.state == CycleState.SETTLING

    def test_on_heating_started_records_state(self, cycle_tracker, mock_callbacks):
        """Test on_heating_started() records start time and target."""
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        mock_callbacks["get_target_temp"].return_value = 21.5

        cycle_tracker.on_heating_started(start_time)

        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.cycle_start_time == start_time
        assert cycle_tracker._cycle_target_temp == 21.5
        assert len(cycle_tracker.temperature_history) == 0  # Cleared

    def test_on_heating_stopped_transitions_to_settling(self, cycle_tracker, mock_hass):
        """Test on_heating_stopped() transitions to SETTLING."""
        # First start heating
        cycle_tracker.on_heating_started(datetime.now())
        assert cycle_tracker.state == CycleState.HEATING

        # Then stop heating
        cycle_tracker.on_heating_stopped(datetime.now())

        assert cycle_tracker.state == CycleState.SETTLING
        # Verify timeout was scheduled
        mock_hass.async_call_later.assert_called_once()
        call_args = mock_hass.async_call_later.call_args
        assert call_args[0][0] == 120 * 60  # 120 minutes in seconds

    def test_on_heating_stopped_ignores_when_not_heating(self, cycle_tracker):
        """Test on_heating_stopped() ignores call when not in HEATING state."""
        # Start in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Try to stop heating
        cycle_tracker.on_heating_stopped(datetime.now())

        # Should remain in IDLE
        assert cycle_tracker.state == CycleState.IDLE

    def test_on_heating_started_resets_cycle_from_non_idle(self, cycle_tracker):
        """Test on_heating_started() resets cycle when called from non-IDLE state."""
        # Start heating
        first_start = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(first_start)
        cycle_tracker.on_heating_stopped(datetime.now())
        assert cycle_tracker.state == CycleState.SETTLING

        # Start heating again without going through IDLE
        second_start = datetime(2025, 1, 14, 11, 0, 0)
        cycle_tracker.on_heating_started(second_start)

        # Should be in HEATING state with new start time
        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.cycle_start_time == second_start

    def test_temperature_history_cleared_on_heating_start(self, cycle_tracker):
        """Test temperature history is cleared when heating starts."""
        # Manually add some history
        cycle_tracker._temperature_history.append((datetime.now(), 18.0))
        cycle_tracker._temperature_history.append((datetime.now(), 18.5))
        assert len(cycle_tracker.temperature_history) == 2

        # Start heating
        cycle_tracker.on_heating_started(datetime.now())

        # History should be cleared
        assert len(cycle_tracker.temperature_history) == 0


def test_cycle_tracker_module_exists():
    """Marker test to verify cycle tracker module exists."""
    assert CycleState is not None
    assert CycleTrackerManager is not None
