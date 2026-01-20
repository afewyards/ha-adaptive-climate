"""Tests for cycle tracker manager."""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from custom_components.adaptive_thermostat.managers.cycle_tracker import (
    CycleState,
    CycleTrackerManager,
)


@pytest.fixture
def mock_async_call_later():
    """Mock async_call_later from homeassistant.helpers.event."""
    with patch(
        "custom_components.adaptive_thermostat.managers.cycle_tracker.async_call_later"
    ) as mock:
        mock.return_value = MagicMock()  # Returns cancel handle
        yield mock


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
    tracker = CycleTrackerManager(
        hass=mock_hass,
        zone_id="test_zone",
        adaptive_learner=mock_adaptive_learner,
        get_target_temp=mock_callbacks["get_target_temp"],
        get_current_temp=mock_callbacks["get_current_temp"],
        get_hvac_mode=mock_callbacks["get_hvac_mode"],
        get_in_grace_period=mock_callbacks["get_in_grace_period"],
    )
    # Mark restoration complete for most tests (except restoration tests)
    tracker.set_restoration_complete()
    return tracker


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
        import sys

        # Get the mocked async_call_later from conftest
        mock_async_call_later = sys.modules["homeassistant.helpers.event"].async_call_later
        mock_async_call_later.reset_mock()

        # First start heating
        cycle_tracker.on_heating_started(datetime.now())
        assert cycle_tracker.state == CycleState.HEATING

        # Then stop heating
        cycle_tracker.on_heating_stopped(datetime.now())

        assert cycle_tracker.state == CycleState.SETTLING
        # Verify timeout was scheduled
        mock_async_call_later.assert_called_once()
        call_args = mock_async_call_later.call_args
        assert call_args[0][1] == 120 * 60  # 120 minutes in seconds (2nd arg after hass)

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
        import sys

        # Get the mocked async_call_later from conftest
        mock_async_call_later = sys.modules["homeassistant.helpers.event"].async_call_later
        mock_async_call_later.reset_mock()

        # Start heating and stop
        cycle_tracker.on_heating_started(datetime.now())
        cycle_tracker.on_heating_stopped(datetime.now())

        # Verify timeout was scheduled
        mock_async_call_later.assert_called_once()
        call_args = mock_async_call_later.call_args
        assert call_args[0][1] == 120 * 60  # 120 minutes in seconds (2nd arg after hass)

        # Get the timeout callback (3rd arg) - it's now an async function passed directly
        timeout_callback = call_args[0][2]
        # Await the async callback directly (it expects a datetime arg)
        await timeout_callback(datetime.now())

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


class TestCycleTrackerTemperatureUpdates:
    """Test temperature update integration with control loop."""

    @pytest.mark.asyncio
    async def test_temperature_updates_during_heating(self, cycle_tracker):
        """Test that temperature samples are collected during heating cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Simulate temperature updates during heating (as would happen in control loop)
        temp_time_1 = datetime(2025, 1, 14, 10, 0, 30)
        await cycle_tracker.update_temperature(temp_time_1, 18.5)

        temp_time_2 = datetime(2025, 1, 14, 10, 1, 0)
        await cycle_tracker.update_temperature(temp_time_2, 19.0)

        temp_time_3 = datetime(2025, 1, 14, 10, 1, 30)
        await cycle_tracker.update_temperature(temp_time_3, 19.5)

        # Verify temperature samples were collected
        assert len(cycle_tracker.temperature_history) == 3
        assert cycle_tracker.temperature_history[0] == (temp_time_1, 18.5)
        assert cycle_tracker.temperature_history[1] == (temp_time_2, 19.0)
        assert cycle_tracker.temperature_history[2] == (temp_time_3, 19.5)


class TestCycleTrackerEdgeCases:
    """Test edge case handling for cycle tracker."""

    def test_setpoint_change_aborts_cycle(self, cycle_tracker):
        """Test that setpoint change during active cycle aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 1, 0), 19.0),
        ]

        # Change setpoint mid-cycle
        cycle_tracker.on_setpoint_changed(20.0, 22.0)

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker.cycle_start_time is None
        assert cycle_tracker._cycle_target_temp is None

    def test_setpoint_change_during_settling_aborts_cycle(self, cycle_tracker):
        """Test that setpoint change during settling aborts the cycle."""
        # Start and stop heating to enter settling state
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 15, 0))
        assert cycle_tracker.state == CycleState.SETTLING

        # Add temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 15, 30), 20.0),
        ]

        # Change setpoint during settling
        cycle_tracker.on_setpoint_changed(20.0, 22.0)

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker._settling_timeout_handle is None

    def test_setpoint_change_in_idle_no_effect(self, cycle_tracker):
        """Test that setpoint change in IDLE state has no effect."""
        # Ensure in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Change setpoint while in IDLE
        cycle_tracker.on_setpoint_changed(20.0, 22.0)

        # Verify state unchanged
        assert cycle_tracker.state == CycleState.IDLE

    def test_contact_sensor_aborts_cycle(self, cycle_tracker):
        """Test that contact sensor pause during active cycle aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 1, 0), 19.0),
        ]

        # Trigger contact sensor pause (window/door opened)
        cycle_tracker.on_contact_sensor_pause()

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker.cycle_start_time is None
        assert cycle_tracker._cycle_target_temp is None

    def test_contact_sensor_pause_in_idle_no_effect(self, cycle_tracker):
        """Test that contact sensor pause in IDLE state has no effect."""
        # Ensure in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Trigger contact sensor pause while in IDLE
        cycle_tracker.on_contact_sensor_pause()

        # Verify state unchanged
        assert cycle_tracker.state == CycleState.IDLE

    def test_mode_change_aborts_cycle(self, cycle_tracker):
        """Test that mode change from HEAT to OFF/COOL aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 1, 0), 19.0),
        ]

        # Change mode from HEAT to OFF
        cycle_tracker.on_mode_changed("heat", "off")

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker.cycle_start_time is None
        assert cycle_tracker._cycle_target_temp is None

    def test_mode_change_to_cool_aborts_cycle(self, cycle_tracker):
        """Test that mode change from HEAT to COOL aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
        ]

        # Change mode from HEAT to COOL
        cycle_tracker.on_mode_changed("heat", "cool")

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0

    def test_mode_change_heat_to_heat_no_effect(self, cycle_tracker):
        """Test that mode change from HEAT to HEAT has no effect."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
        ]

        # Change mode from HEAT to HEAT (no actual change)
        cycle_tracker.on_mode_changed("heat", "heat")

        # Verify cycle continues (state unchanged)
        assert cycle_tracker.state == CycleState.HEATING
        assert len(cycle_tracker.temperature_history) == 1

    def test_mode_change_in_idle_no_effect(self, cycle_tracker):
        """Test that mode change in IDLE state has no effect."""
        # Ensure in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Change mode while in IDLE
        cycle_tracker.on_mode_changed("heat", "off")

        # Verify state unchanged
        assert cycle_tracker.state == CycleState.IDLE


class TestSetpointChangeWithDeviceActive:
    """Tests for setpoint change behavior when heater is active."""

    def test_setpoint_change_while_heater_active_continues_tracking(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test that setpoint change while heater is active continues tracking."""
        # Create tracker with get_is_device_active returning True
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=Mock(return_value=True),
        )

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        tracker.on_heating_started(start_time)
        assert tracker.state == CycleState.HEATING

        # Add temperature sample to history
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 0, 30), 18.5))

        # Change setpoint while heater is active
        tracker.on_setpoint_changed(20.0, 22.0)

        # Assert state is still HEATING (not aborted)
        assert tracker.state == CycleState.HEATING

        # Assert _cycle_target_temp updated to new value
        assert tracker._cycle_target_temp == 22.0

        # Assert interruption was recorded
        assert len(tracker._interruption_history) == 1
        assert tracker._interruption_history[0][1] == "setpoint_minor"

        # Assert temperature_history is preserved
        assert len(tracker.temperature_history) == 1
        assert tracker.temperature_history[0] == (datetime(2025, 1, 14, 10, 0, 30), 18.5)

    def test_setpoint_change_while_heater_inactive_aborts_cycle(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test that setpoint change while heater is inactive aborts the cycle."""
        # Create tracker with get_is_device_active returning False
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=Mock(return_value=False),
        )

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        tracker.on_heating_started(start_time)
        assert tracker.state == CycleState.HEATING

        # Add temperature sample to history
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 0, 30), 18.5))

        # Change setpoint while heater is inactive
        tracker.on_setpoint_changed(20.0, 22.0)

        # Assert state is IDLE (aborted)
        assert tracker.state == CycleState.IDLE

        # Assert temperature_history is empty (cleared)
        assert len(tracker.temperature_history) == 0

    def test_setpoint_change_without_callback_aborts_cycle(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test backward compatibility: setpoint change aborts cycle when callback not provided."""
        # Create tracker WITHOUT get_is_device_active parameter (legacy behavior)
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            # NOTE: get_is_device_active intentionally not provided
        )

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        tracker.on_heating_started(start_time)
        assert tracker.state == CycleState.HEATING

        # Add temperature sample to history
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 0, 30), 18.5))

        # Change setpoint - should abort cycle (legacy behavior)
        tracker.on_setpoint_changed(20.0, 22.0)

        # Assert state is IDLE (aborted - legacy behavior preserved)
        assert tracker.state == CycleState.IDLE

        # Assert temperature_history is empty (cleared)
        assert len(tracker.temperature_history) == 0

    def test_multiple_setpoint_changes_while_heater_active(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test that multiple setpoint changes while heater is active are tracked."""
        # Create tracker with get_is_device_active returning True
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=Mock(return_value=True),
        )

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        tracker.on_heating_started(start_time)
        assert tracker.state == CycleState.HEATING

        # First setpoint change
        tracker.on_setpoint_changed(20.0, 22.0)

        # Second setpoint change
        tracker.on_setpoint_changed(22.0, 21.0)

        # Third setpoint change
        tracker.on_setpoint_changed(21.0, 23.0)

        # Assert state is still HEATING
        assert tracker.state == CycleState.HEATING

        # Assert _cycle_target_temp is the latest value
        assert tracker._cycle_target_temp == 23.0

        # Assert 3 interruptions were recorded
        assert len(tracker._interruption_history) == 3


class TestResetCycleState:
    """Tests for _reset_cycle_state helper method."""

    def test_reset_cycle_state_clears_all(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test that _reset_cycle_state clears all state variables."""
        # Create tracker
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=Mock(return_value=True),
        )

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        tracker.on_heating_started(start_time)
        assert tracker.state == CycleState.HEATING

        # Add items to _interruption_history
        tracker._interruption_history.append((datetime(2025, 1, 14, 10, 5, 0), "setpoint_minor"))
        tracker._interruption_history.append((datetime(2025, 1, 14, 10, 10, 0), "setpoint_minor"))

        # Add items to _temperature_history
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 0, 30), 18.5))
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 1, 0), 19.0))
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 1, 30), 19.5))

        # Call _reset_cycle_state()
        tracker._reset_cycle_state()

        # Assert _state == CycleState.IDLE
        assert tracker._state == CycleState.IDLE

        # Assert _interruption_history is empty
        assert len(tracker._interruption_history) == 0

        # Assert _temperature_history is empty
        assert len(tracker._temperature_history) == 0

        # Assert _cycle_start_time is None
        assert tracker._cycle_start_time is None

        # Assert _cycle_target_temp is None
        assert tracker._cycle_target_temp is None


class TestCycleTrackerMADSettling:
    """Test MAD-based settling detection."""

    def test_settling_detection_with_noise(self, cycle_tracker):
        """Test settling detection is robust to sensor noise using MAD."""
        # Start heating, then stop
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 15, 0))

        # Add 10 temperature samples with ±0.2°C noise (simulating sensor jitter)
        # Centered around 20.0°C target
        noisy_temps = [20.0, 20.1, 19.9, 20.0, 20.2, 19.8, 20.1, 19.9, 20.0, 20.1]
        for i, temp in enumerate(noisy_temps):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 10, 15 + i, 0), temp)
            )

        # Calculate expected MAD
        # Median of [20.0, 20.1, 19.9, 20.0, 20.2, 19.8, 20.1, 19.9, 20.0, 20.1] = 20.0
        # Deviations: [0.0, 0.1, 0.1, 0.0, 0.2, 0.2, 0.1, 0.1, 0.0, 0.1]
        # MAD = median of deviations = 0.1
        mad = cycle_tracker._calculate_mad(noisy_temps)
        assert 0.09 <= mad <= 0.11  # Allow small floating point tolerance

        # MAD threshold is 0.05, so 0.1 > 0.05 => should not settle yet
        assert not cycle_tracker._is_settling_complete()

        # Now add samples with less noise (within threshold)
        stable_temps = [20.0, 20.01, 19.99, 20.0, 20.02, 19.98, 20.01, 19.99, 20.0, 20.01]
        cycle_tracker._temperature_history.clear()
        for i, temp in enumerate(stable_temps):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 10, 25 + i, 0), temp)
            )

        # MAD should now be < 0.05
        mad = cycle_tracker._calculate_mad(stable_temps)
        assert mad < 0.05

        # Should detect settling
        assert cycle_tracker._is_settling_complete()

    def test_settling_mad_vs_variance(self, cycle_tracker):
        """Test MAD is more robust than variance to outliers."""
        # Start heating, then stop
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 15, 0))

        # Add 9 stable samples + 1 outlier
        # Stable temps around 20.0°C, one reading at 21.0°C (outlier)
        temps_with_outlier = [20.0, 20.01, 19.99, 20.0, 21.0, 20.01, 19.99, 20.0, 20.01, 19.99]

        for i, temp in enumerate(temps_with_outlier):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 10, 15 + i, 0), temp)
            )

        # Calculate MAD (robust to outlier)
        mad = cycle_tracker._calculate_mad(temps_with_outlier)

        # Calculate variance (NOT robust to outlier)
        mean_temp = sum(temps_with_outlier) / len(temps_with_outlier)
        variance = sum((t - mean_temp) ** 2 for t in temps_with_outlier) / len(temps_with_outlier)

        # Variance should be high due to outlier (old threshold: 0.01)
        # MAD should be low (new threshold: 0.05)
        assert variance > 0.01  # Would fail old variance check
        assert mad < 0.05  # Should pass new MAD check

        # MAD-based detection should handle this gracefully
        # (though in this case, one outlier might still exceed threshold depending on exact values)

    def test_settling_detection_outlier_robust(self, cycle_tracker):
        """Test settling detection handles single outlier correctly."""
        # Start heating, then stop
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        cycle_tracker.on_heating_stopped(datetime(2025, 1, 14, 10, 15, 0))

        # Add 9 very stable samples + 1 moderate outlier
        temps = [20.0, 20.0, 20.0, 20.0, 20.15, 20.0, 20.0, 20.0, 20.0, 20.0]

        for i, temp in enumerate(temps):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 10, 15 + i, 0), temp)
            )

        # Calculate MAD
        # Median = 20.0
        # Deviations: [0.0, 0.0, 0.0, 0.0, 0.15, 0.0, 0.0, 0.0, 0.0, 0.0]
        # MAD = median([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.15]) = 0.0
        mad = cycle_tracker._calculate_mad(temps)
        assert mad == 0.0  # MAD ignores single outlier when most values are identical

        # Should detect settling (MAD < 0.05)
        assert cycle_tracker._is_settling_complete()

    def test_calculate_mad_basic(self, cycle_tracker):
        """Test MAD calculation with known values."""
        # Test case 1: All identical values
        mad = cycle_tracker._calculate_mad([5.0, 5.0, 5.0, 5.0, 5.0])
        assert mad == 0.0

        # Test case 2: Simple values with known MAD
        # Values: [1, 2, 3, 4, 5]
        # Median = 3
        # Deviations: [2, 1, 0, 1, 2]
        # MAD = median([0, 1, 1, 2, 2]) = 1
        mad = cycle_tracker._calculate_mad([1.0, 2.0, 3.0, 4.0, 5.0])
        assert mad == 1.0

        # Test case 3: Even number of values
        # Values: [1, 2, 3, 4]
        # Median = 2.5
        # Deviations: [1.5, 0.5, 0.5, 1.5]
        # MAD = median([0.5, 0.5, 1.5, 1.5]) = 1.0
        mad = cycle_tracker._calculate_mad([1.0, 2.0, 3.0, 4.0])
        assert mad == 1.0

        # Test case 4: Empty list
        mad = cycle_tracker._calculate_mad([])
        assert mad == 0.0


def test_cycle_tracker_module_exists():
    """Marker test to verify cycle tracker module exists."""
    assert CycleState is not None
    assert CycleTrackerManager is not None


class TestCycleTrackerSettlingTimeout:
    """Tests for dynamic settling timeout based on thermal mass."""

    def test_settling_timeout_floor_hydronic(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test settling timeout calculation for floor hydronic (high thermal mass).

        Floor hydronic systems have tau=8h, should calculate timeout of 240 min (capped at max).
        Formula: timeout = max(60, min(240, tau * 30)) = max(60, min(240, 8*30)) = 240 min
        """
        # Create cycle tracker with floor hydronic thermal time constant
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            thermal_time_constant=8.0,  # 8 hours (floor hydronic)
            **mock_callbacks,
        )

        # Verify timeout is capped at maximum (240 minutes)
        assert cycle_tracker._max_settling_time_minutes == 240
        assert "calculated" in cycle_tracker._settling_timeout_source
        assert "tau=8.0h" in cycle_tracker._settling_timeout_source

    def test_settling_timeout_forced_air(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test settling timeout calculation for forced air (low thermal mass).

        Forced air systems have tau=2h, should calculate timeout of 60 min (capped at min).
        Formula: timeout = max(60, min(240, tau * 30)) = max(60, min(240, 2*30)) = 60 min
        """
        # Create cycle tracker with forced air thermal time constant
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            thermal_time_constant=2.0,  # 2 hours (forced air)
            **mock_callbacks,
        )

        # Verify timeout is capped at minimum (60 minutes)
        assert cycle_tracker._max_settling_time_minutes == 60
        assert "calculated" in cycle_tracker._settling_timeout_source
        assert "tau=2.0h" in cycle_tracker._settling_timeout_source

    def test_settling_timeout_radiator(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test settling timeout calculation for radiator (medium thermal mass).

        Radiator systems have tau=4h, should calculate timeout of 120 min.
        Formula: timeout = max(60, min(240, tau * 30)) = max(60, min(240, 4*30)) = 120 min
        """
        # Create cycle tracker with radiator thermal time constant
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            thermal_time_constant=4.0,  # 4 hours (radiator)
            **mock_callbacks,
        )

        # Verify timeout is calculated correctly (not capped)
        assert cycle_tracker._max_settling_time_minutes == 120
        assert "calculated" in cycle_tracker._settling_timeout_source
        assert "tau=4.0h" in cycle_tracker._settling_timeout_source

    def test_settling_timeout_override(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test settling timeout with explicit override."""
        # Create cycle tracker with explicit override
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            thermal_time_constant=8.0,  # Would calculate 240 min
            settling_timeout_minutes=90,  # Override to 90 min
            **mock_callbacks,
        )

        # Verify override takes precedence
        assert cycle_tracker._max_settling_time_minutes == 90
        assert cycle_tracker._settling_timeout_source == "override"

    def test_settling_timeout_default_fallback(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test settling timeout falls back to default when tau not provided."""
        # Create cycle tracker without tau or override
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            # No thermal_time_constant or settling_timeout_minutes
            **mock_callbacks,
        )

        # Verify default timeout (120 minutes)
        assert cycle_tracker._max_settling_time_minutes == 120
        assert cycle_tracker._settling_timeout_source == "default"


class TestCycleTrackerStateAccess:
    """Tests for state access methods."""

    def test_get_state_name_idle(self, cycle_tracker):
        """Test get_state_name returns 'idle' in IDLE state."""
        assert cycle_tracker.state == CycleState.IDLE
        assert cycle_tracker.get_state_name() == "idle"

    def test_get_state_name_heating(self, cycle_tracker):
        """Test get_state_name returns 'heating' in HEATING state."""
        cycle_tracker.on_heating_started(datetime.now())
        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.get_state_name() == "heating"

    def test_get_state_name_settling(self, cycle_tracker):
        """Test get_state_name returns 'settling' in SETTLING state."""
        cycle_tracker.on_heating_started(datetime.now())
        cycle_tracker.on_heating_stopped(datetime.now())
        assert cycle_tracker.state == CycleState.SETTLING
        assert cycle_tracker.get_state_name() == "settling"

    def test_get_state_name_cooling(self, cycle_tracker):
        """Test get_state_name returns 'cooling' in COOLING state."""
        cycle_tracker.on_cooling_started(datetime.now())
        assert cycle_tracker.state == CycleState.COOLING
        assert cycle_tracker.get_state_name() == "cooling"

    def test_get_last_interruption_reason_none_initially(self, cycle_tracker):
        """Test get_last_interruption_reason returns None with no interruptions."""
        assert cycle_tracker.get_last_interruption_reason() is None

    def test_get_last_interruption_reason_setpoint_major(self, cycle_tracker, mock_callbacks):
        """Test interruption reason for major setpoint change."""
        # Start a cycle
        cycle_tracker.on_heating_started(datetime.now())

        # Trigger major setpoint change (device inactive)
        mock_callbacks["get_target_temp"].return_value = 22.0
        cycle_tracker.on_setpoint_changed(20.0, 22.0)  # >0.5°C change

        # Should map to "setpoint_change"
        assert cycle_tracker.get_last_interruption_reason() == "setpoint_change"

    def test_get_last_interruption_reason_setpoint_minor(self, cycle_tracker, mock_callbacks):
        """Test interruption reason for minor setpoint change."""
        # Start a cycle
        cycle_tracker.on_heating_started(datetime.now())

        # Create mock for is_device_active
        mock_is_device_active = Mock(return_value=True)
        cycle_tracker._get_is_device_active = mock_is_device_active

        # Trigger minor setpoint change (device active)
        mock_callbacks["get_target_temp"].return_value = 20.3
        cycle_tracker.on_setpoint_changed(20.0, 20.3)  # <0.5°C change

        # Should map to "setpoint_change"
        assert cycle_tracker.get_last_interruption_reason() == "setpoint_change"

    def test_get_last_interruption_reason_mode_change(self, cycle_tracker, mock_callbacks):
        """Test interruption reason for mode change."""
        # Start a cycle
        cycle_tracker.on_heating_started(datetime.now())

        # Change mode
        mock_callbacks["get_hvac_mode"].return_value = "off"
        cycle_tracker.on_mode_changed("heat", "off")

        # Should map to "mode_change"
        assert cycle_tracker.get_last_interruption_reason() == "mode_change"

    def test_get_last_interruption_reason_contact_sensor(self, cycle_tracker):
        """Test interruption reason for contact sensor."""
        # Start a cycle
        cycle_tracker.on_heating_started(datetime.now())

        # Trigger contact sensor pause
        cycle_tracker.on_contact_sensor_pause()

        # Should map to "contact_sensor"
        assert cycle_tracker.get_last_interruption_reason() == "contact_sensor"

    def test_get_last_interruption_reason_clears_on_reset(self, cycle_tracker):
        """Test interruption reason is cleared when cycle resets."""
        # Start a cycle and interrupt it
        cycle_tracker.on_heating_started(datetime.now())
        cycle_tracker.on_contact_sensor_pause()

        assert cycle_tracker.get_last_interruption_reason() == "contact_sensor"

        # After reset, interruption history should be cleared
        # (This happens automatically in _reset_cycle_state which is called on interruption)
        assert cycle_tracker.state == CycleState.IDLE

        # After starting a new cycle, no interruptions yet
        cycle_tracker.on_heating_started(datetime.now())
        assert cycle_tracker.get_last_interruption_reason() is None

    def test_get_last_interruption_reason_returns_most_recent(self, cycle_tracker):
        """Test that most recent interruption is returned."""
        # Start a cycle
        cycle_tracker.on_heating_started(datetime.now())

        # First interruption (but continue tracking)
        mock_is_device_active = Mock(return_value=True)
        cycle_tracker._get_is_device_active = mock_is_device_active
        cycle_tracker.on_setpoint_changed(20.0, 20.3)  # Minor change, continues

        assert cycle_tracker.get_last_interruption_reason() == "setpoint_change"

        # Second interruption
        cycle_tracker.on_mode_changed("heat", "off")  # This aborts

        # Should return the most recent one
        assert cycle_tracker.get_last_interruption_reason() == "mode_change"


class TestCycleTrackerSettlingTimeoutFinalization:
    """Tests for settling timeout cycle finalization."""

    @pytest.mark.asyncio
    async def test_settling_timeout_records_metrics(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test settling timeout finalizes cycle and records metrics.

        This tests the fix for the bug where settling timeout would discard
        the cycle instead of recording metrics (the TODO comment that said
        '_finalize_cycle() will be implemented in feature 2.3').
        """
        import sys
        from datetime import timedelta

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            **mock_callbacks,
        )
        # Mark restoration complete to allow temperature updates
        cycle_tracker.set_restoration_complete()

        # Get the mocked async_call_later from conftest
        mock_async_call_later = sys.modules["homeassistant.helpers.event"].async_call_later
        mock_async_call_later.reset_mock()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle 6 minutes ago (> 5 min minimum duration)
        start_time = datetime.now() - timedelta(minutes=6)
        cycle_tracker.on_heating_started(start_time)

        # Add temperature samples during heating (rising)
        for i in range(5):
            await cycle_tracker.update_temperature(
                start_time + timedelta(minutes=i), 18.0 + i * 0.5
            )

        # Stop heating
        stop_time = start_time + timedelta(minutes=5)
        cycle_tracker.on_heating_stopped(stop_time)

        # Add settling samples with HIGH overshoot (far from target)
        # This simulates the GF bug: temperature stays high and doesn't settle
        for i in range(5):
            await cycle_tracker.update_temperature(
                stop_time + timedelta(minutes=i), 21.5  # 1.5°C above target
            )

        # Verify still in settling (too far from 0.5°C requirement)
        assert cycle_tracker.state == CycleState.SETTLING

        # Verify timeout was scheduled
        mock_async_call_later.assert_called_once()

        # Get the timeout callback and await it directly (async function)
        timeout_callback = mock_async_call_later.call_args[0][2]
        await timeout_callback(datetime.now())

        # State should transition to IDLE
        assert cycle_tracker.state == CycleState.IDLE

        # CRITICAL: Verify metrics were recorded (this is the fix!)
        mock_adaptive_learner.add_cycle_metrics.assert_called_once()

        # Verify the recorded metrics include overshoot
        recorded_metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert recorded_metrics.overshoot is not None
        assert recorded_metrics.overshoot > 1.0  # Should capture the 1.5°C overshoot


class TestCycleTrackerRestoration:
    """Tests for restoration gating functionality."""

    @pytest.mark.asyncio
    async def test_cycle_tracker_ignores_updates_before_restoration(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test temperature updates are ignored when restoration not complete."""
        # Create tracker without calling set_restoration_complete()
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
        )

        # Verify initial state
        assert cycle_tracker._restoration_complete is False
        assert cycle_tracker.state == CycleState.IDLE

        # Start heating cycle
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        assert cycle_tracker.state == CycleState.HEATING

        # Try to update temperature while restoration incomplete
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 0, 30), 18.5)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 1, 0), 19.0)

        # Temperature history should be empty (updates ignored)
        assert len(cycle_tracker.temperature_history) == 0

        # State should still be HEATING (state machine transitions still work)
        assert cycle_tracker.state == CycleState.HEATING

    @pytest.mark.asyncio
    async def test_cycle_tracker_processes_after_restoration(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test normal behavior after restoration complete."""
        # Create tracker without calling set_restoration_complete()
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
        )

        # Mark restoration complete
        cycle_tracker.set_restoration_complete()
        assert cycle_tracker._restoration_complete is True

        # Start heating cycle
        cycle_tracker.on_heating_started(datetime(2025, 1, 14, 10, 0, 0))
        assert cycle_tracker.state == CycleState.HEATING

        # Update temperature
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 0, 30), 18.5)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 1, 0), 19.0)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 1, 30), 19.5)

        # Temperature history should contain samples (normal behavior)
        assert len(cycle_tracker.temperature_history) == 3
        assert cycle_tracker.temperature_history[0] == (datetime(2025, 1, 14, 10, 0, 30), 18.5)
        assert cycle_tracker.temperature_history[1] == (datetime(2025, 1, 14, 10, 1, 0), 19.0)
        assert cycle_tracker.temperature_history[2] == (datetime(2025, 1, 14, 10, 1, 30), 19.5)


class TestCycleTrackerFinalizeSave:
    """Tests for cycle finalization save functionality."""

    @pytest.mark.asyncio
    async def test_finalize_cycle_schedules_save(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test that finalize_cycle calls schedule_zone_save after adding metrics."""
        # Create mock learning store
        mock_learning_store = MagicMock()
        mock_learning_store.update_zone_data = MagicMock()
        mock_learning_store.schedule_zone_save = MagicMock()

        # Setup hass.data with DOMAIN and learning_store
        mock_hass.data = {
            "adaptive_thermostat": {
                "learning_store": mock_learning_store,
            }
        }

        # Setup adaptive_learner to return dict
        mock_adaptive_learner.to_dict = MagicMock(return_value={
            "cycle_history": [],
            "last_adjustment_time": None,
            "consecutive_converged_cycles": 0,
            "pid_converged_for_ke": False,
            "auto_apply_count": 0,
        })
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle 6 minutes ago (> 5 min minimum duration)
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Add temperature samples during heating
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Finalize cycle
        await cycle_tracker._finalize_cycle()

        # Verify metrics were recorded
        mock_adaptive_learner.add_cycle_metrics.assert_called_once()

        # Verify schedule_zone_save was called
        mock_learning_store.schedule_zone_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_cycle_passes_adaptive_data(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test that finalize_cycle passes adaptive_learner.to_dict() to save."""
        # Create mock learning store
        mock_learning_store = MagicMock()
        mock_learning_store.update_zone_data = MagicMock()
        mock_learning_store.schedule_zone_save = MagicMock()

        # Setup hass.data with DOMAIN and learning_store
        mock_hass.data = {
            "adaptive_thermostat": {
                "learning_store": mock_learning_store,
            }
        }

        # Setup adaptive_learner to return specific dict
        expected_adaptive_data = {
            "cycle_history": [{"overshoot": 0.5, "undershoot": 0.1}],
            "last_adjustment_time": "2025-01-14T10:00:00",
            "consecutive_converged_cycles": 3,
            "pid_converged_for_ke": True,
            "auto_apply_count": 1,
        }
        mock_adaptive_learner.to_dict = MagicMock(return_value=expected_adaptive_data)
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Add temperature samples
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Finalize cycle
        await cycle_tracker._finalize_cycle()

        # Verify update_zone_data was called with the correct adaptive data
        mock_learning_store.update_zone_data.assert_called_once_with(
            zone_id="test_zone",
            adaptive_data=expected_adaptive_data,
        )

    @pytest.mark.asyncio
    async def test_finalize_cycle_no_store_gracefully_skips(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
    ):
        """Test that finalize_cycle gracefully handles missing learning store."""
        # Setup hass.data WITHOUT learning_store
        mock_hass.data = {}

        mock_adaptive_learner.to_dict = MagicMock(return_value={})
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Add temperature samples
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Finalize cycle should not raise exception
        await cycle_tracker._finalize_cycle()

        # Verify metrics were still recorded
        mock_adaptive_learner.add_cycle_metrics.assert_called_once()

        # Verify state is IDLE
        assert cycle_tracker.state == CycleState.IDLE
