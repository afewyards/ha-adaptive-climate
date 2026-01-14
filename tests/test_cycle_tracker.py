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


class TestCycleTrackerTemperatureCollection:
    """Tests for temperature collection functionality."""

    @pytest.mark.asyncio
    async def test_temperature_collection_during_heating(self, cycle_tracker):
        """Test temperature samples are collected during HEATING state."""
        # Start heating
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))

        # Add temperature samples
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 0, 30), 18.5)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 1, 0), 19.0)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 1, 30), 19.5)

        # Verify samples were collected
        history = cycle_tracker.temperature_history
        assert len(history) == 3
        assert history[0] == (datetime(2025, 1, 14, 10, 0, 30), 18.5)
        assert history[1] == (datetime(2025, 1, 14, 10, 1, 0), 19.0)
        assert history[2] == (datetime(2025, 1, 14, 10, 1, 30), 19.5)

    @pytest.mark.asyncio
    async def test_temperature_collection_during_settling(self, cycle_tracker):
        """Test temperature samples are collected during SETTLING state."""
        # Start and stop heating
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 30, 0))

        # Add temperature samples
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 30, 30), 20.0)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 31, 0), 20.1)

        # Verify samples were collected
        history = cycle_tracker.temperature_history
        assert len(history) == 2
        assert history[0] == (datetime(2025, 1, 14, 10, 30, 30), 20.0)
        assert history[1] == (datetime(2025, 1, 14, 10, 31, 0), 20.1)

    @pytest.mark.asyncio
    async def test_temperature_not_collected_during_idle(self, cycle_tracker):
        """Test temperature samples are NOT collected during IDLE state."""
        # Ensure we're in IDLE
        assert cycle_tracker.state == CycleState.IDLE

        # Try to add temperature samples
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 0, 0), 18.0)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 1, 0), 18.5)

        # Verify no samples were collected
        assert len(cycle_tracker.temperature_history) == 0


class TestCycleTrackerSettling:
    """Tests for settling detection functionality."""

    @pytest.mark.asyncio
    async def test_settling_detection_stable(self, cycle_tracker, mock_callbacks):
        """Test settling is detected when temperature is stable and near target."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 30, 0))

        # Add 10 stable temperature samples near target (variance < 0.01, within 0.5°C)
        base_time = datetime(2025, 1, 14, 10, 30, 0)
        for i in range(10):
            temp = 20.0 + (i % 3) * 0.02  # Very small variations (20.0, 20.02, 20.04)
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, 30 + i, 0), temp
            )

        # Should have detected settling and transitioned to IDLE
        assert cycle_tracker.state == CycleState.IDLE

    @pytest.mark.asyncio
    async def test_settling_detection_unstable(self, cycle_tracker, mock_callbacks):
        """Test settling continues when temperature is oscillating."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 30, 0))

        # Add 10 unstable temperature samples (high variance)
        base_time = datetime(2025, 1, 14, 10, 30, 0)
        for i in range(10):
            temp = 20.0 + (i % 2) * 0.5  # Oscillating (20.0, 20.5, 20.0, 20.5, ...)
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, 30 + i, 0), temp
            )

        # Should still be in SETTLING (not stable enough)
        assert cycle_tracker.state == CycleState.SETTLING

    @pytest.mark.asyncio
    async def test_settling_detection_insufficient_samples(self, cycle_tracker, mock_callbacks):
        """Test settling requires at least 10 samples."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 30, 0))

        # Add only 9 stable samples (not enough)
        for i in range(9):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, 30 + i, 0), 20.0
            )

        # Should still be in SETTLING (not enough samples)
        assert cycle_tracker.state == CycleState.SETTLING

    @pytest.mark.asyncio
    async def test_settling_detection_far_from_target(self, cycle_tracker, mock_callbacks):
        """Test settling requires temperature within 0.5°C of target."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 30, 0))

        # Add 10 stable samples but far from target (> 0.5°C away)
        for i in range(10):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, 30 + i, 0), 19.0  # 1.0°C below target
            )

        # Should still be in SETTLING (too far from target)
        assert cycle_tracker.state == CycleState.SETTLING

    @pytest.mark.asyncio
    async def test_settling_timeout(self, cycle_tracker, mock_hass):
        """Test settling timeout transitions to IDLE after 120 minutes."""
        # Start heating and stop
        cycle_tracker.on_heating_started(datetime.now())
        cycle_tracker.on_heating_stopped(datetime.now())

        # Verify timeout was scheduled
        mock_hass.async_call_later.assert_called_once()
        call_args = mock_hass.async_call_later.call_args
        assert call_args[0][0] == 120 * 60  # 120 minutes in seconds

        # Get the timeout callback and execute the inner task
        timeout_callback = call_args[0][1]
        timeout_callback(None)  # This calls lambda which calls async_create_task

        # Verify that async_create_task was called with a coroutine
        mock_hass.async_create_task.assert_called_once()

        # Get the coroutine and await it
        task_arg = mock_hass.async_create_task.call_args[0][0]
        await task_arg

        # State should transition to IDLE
        assert cycle_tracker.state == CycleState.IDLE


class TestCycleTrackerValidation:
    """Tests for cycle validation functionality."""

    def test_cycle_validation_min_duration(self, cycle_tracker, monkeypatch):
        """Test minimum duration enforcement (reject < 5 min)."""
        # Mock datetime.now() to control the time
        fixed_time = datetime(2025, 1, 14, 10, 4, 30)

        class MockDateTime:
            @staticmethod
            def now():
                return fixed_time

        import custom_components.adaptive_thermostat.managers.cycle_tracker as cycle_tracker_module
        monkeypatch.setattr(cycle_tracker_module, 'datetime', MockDateTime)

        # Start heating
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))

        # Add some temperature samples
        cycle_tracker._temperature_history.append((datetime(2025, 1, 14, 10, 1, 0), 18.5))
        cycle_tracker._temperature_history.append((datetime(2025, 1, 14, 10, 2, 0), 19.0))
        cycle_tracker._temperature_history.append((datetime(2025, 1, 14, 10, 3, 0), 19.5))
        cycle_tracker._temperature_history.append((datetime(2025, 1, 14, 10, 4, 0), 20.0))
        cycle_tracker._temperature_history.append((datetime(2025, 1, 14, 10, 4, 30), 20.2))

        # Check validation after only 4.5 minutes
        is_valid, reason = cycle_tracker._is_cycle_valid()
        assert not is_valid
        assert "too short" in reason.lower()

    def test_cycle_validation_grace_period(self, cycle_tracker, mock_callbacks):
        """Test learning grace period blocks recording."""
        # Set grace period active
        mock_callbacks["get_in_grace_period"].return_value = True

        # Start heating 10 minutes ago
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 9, 50, 0))

        # Add sufficient temperature samples
        for i in range(10):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 9, 50 + i, 0), 18.0 + i * 0.2)
            )

        # Check validation with grace period active
        is_valid, reason = cycle_tracker._is_cycle_valid()
        assert not is_valid
        assert "grace period" in reason.lower()

    def test_cycle_validation_insufficient_samples(self, cycle_tracker):
        """Test insufficient samples blocks recording."""
        # Start heating 10 minutes ago
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 9, 50, 0))

        # Add only 4 samples (not enough)
        for i in range(4):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 9, 50 + i, 0), 18.0 + i * 0.5)
            )

        # Check validation
        is_valid, reason = cycle_tracker._is_cycle_valid()
        assert not is_valid
        assert "insufficient temperature samples" in reason.lower()

    def test_cycle_validation_success(self, cycle_tracker):
        """Test validation passes with all requirements met."""
        # Start heating 10 minutes ago
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 9, 50, 0))

        # Add sufficient temperature samples (>= 5)
        for i in range(10):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 9, 50 + i, 0), 18.0 + i * 0.2)
            )

        # Check validation
        is_valid, reason = cycle_tracker._is_cycle_valid()
        assert is_valid
        assert reason == "Valid"


class TestCycleTrackerMetrics:
    """Tests for cycle metrics calculation."""

    @pytest.mark.asyncio
    async def test_metrics_calculation(self, cycle_tracker, mock_callbacks, mock_adaptive_learner):
        """Test complete cycle calculates all 5 metrics."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating 10 minutes ago
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Simulate realistic heating cycle with overshoot and settling
        # Rise phase: 18.0 -> 20.5 (overshoot)
        temps = [
            (datetime(2025, 1, 14, 10, 0, 0), 18.0),   # start
            (datetime(2025, 1, 14, 10, 1, 0), 18.5),
            (datetime(2025, 1, 14, 10, 2, 0), 19.0),
            (datetime(2025, 1, 14, 10, 3, 0), 19.5),
            (datetime(2025, 1, 14, 10, 4, 0), 20.0),   # reach target
            (datetime(2025, 1, 14, 10, 5, 0), 20.5),   # overshoot
            (datetime(2025, 1, 14, 10, 6, 0), 20.3),   # settling
            (datetime(2025, 1, 14, 10, 7, 0), 20.1),
            (datetime(2025, 1, 14, 10, 8, 0), 20.0),
            (datetime(2025, 1, 14, 10, 9, 0), 19.9),
        ]

        for timestamp, temp in temps:
            await cycle_tracker.update_temperature(timestamp, temp)

        # Stop heating and transition to SETTLING
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 10, 0))

        # Call finalize (simulating settling complete or timeout)
        await cycle_tracker._finalize_cycle()

        # Verify metrics were calculated and passed to adaptive learner
        mock_adaptive_learner.add_cycle_metrics.assert_called_once()
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]

        # Verify all 5 metrics are present
        assert metrics.overshoot is not None
        assert metrics.undershoot is not None
        assert metrics.settling_time is not None
        assert metrics.oscillations >= 0
        assert metrics.rise_time is not None

        # Verify convergence tracking was updated
        mock_adaptive_learner.update_convergence_tracking.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_cycle_not_recorded(self, cycle_tracker, mock_adaptive_learner):
        """Test invalid cycles are not recorded."""
        # Start heating
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))

        # Add only 2 samples (insufficient)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 0, 30), 18.5)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 1, 0), 19.0)

        # Try to finalize
        await cycle_tracker._finalize_cycle()

        # Verify metrics were NOT passed to adaptive learner
        mock_adaptive_learner.add_cycle_metrics.assert_not_called()
        mock_adaptive_learner.update_convergence_tracking.assert_not_called()

        # Verify state transitioned to IDLE
        assert cycle_tracker.state == CycleState.IDLE


class TestCycleTrackerValveMode:
    """Tests for cycle tracker integration with valve mode."""

    def test_heater_controller_notifications(self, cycle_tracker):
        """Test heater controller notifies cycle tracker on state changes."""
        # Simulate HeaterController calling on_heating_started
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Verify state transition
        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.cycle_start_time == start_time

        # Simulate HeaterController calling on_heating_stopped
        stop_time = datetime(2025, 1, 14, 10, 15, 0)
        cycle_tracker.on_heating_stopped(stop_time)

        # Verify state transition
        assert cycle_tracker.state == CycleState.SETTLING

    def test_valve_mode_transitions(self, cycle_tracker):
        """Test valve mode transitions trigger cycle tracker events."""
        # Test heating started (valve 0 -> >0)
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)
        assert cycle_tracker.state == CycleState.HEATING

        # Test heating stopped (valve >0 -> 0)
        stop_time = datetime(2025, 1, 14, 10, 15, 0)
        cycle_tracker.on_heating_stopped(stop_time)
        assert cycle_tracker.state == CycleState.SETTLING


def test_cycle_tracker_module_exists():
    """Marker test to verify cycle tracker module exists."""
    assert CycleState is not None
    assert CycleTrackerManager is not None
