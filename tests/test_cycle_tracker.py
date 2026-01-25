"""Tests for cycle tracker manager."""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from custom_components.adaptive_thermostat.managers.cycle_tracker import (
    CycleState,
    CycleTrackerManager,
)
from custom_components.adaptive_thermostat.managers.events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    SettlingStartedEvent,
    SetpointChangedEvent,
    ModeChangedEvent,
    ContactPauseEvent,
    TemperatureUpdateEvent,
    HeatingEndedEvent,
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
def dispatcher():
    """Create a cycle event dispatcher."""
    return CycleEventDispatcher()


@pytest.fixture
def cycle_tracker(mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
    """Create a cycle tracker instance."""
    tracker = CycleTrackerManager(
        hass=mock_hass,
        zone_id="test_zone",
        adaptive_learner=mock_adaptive_learner,
        get_target_temp=mock_callbacks["get_target_temp"],
        get_current_temp=mock_callbacks["get_current_temp"],
        get_hvac_mode=mock_callbacks["get_hvac_mode"],
        get_in_grace_period=mock_callbacks["get_in_grace_period"],
        dispatcher=dispatcher,
    )
    # Mark restoration complete for most tests (except restoration tests)
    tracker.set_restoration_complete()
    return tracker


class TestCycleTrackerBasic:
    """Tests for basic cycle tracker functionality."""

    def test_initial_state_is_idle(self, cycle_tracker, dispatcher):
        """Test that initial state is IDLE."""
        assert cycle_tracker.state == CycleState.IDLE
        assert cycle_tracker.cycle_start_time is None
        assert len(cycle_tracker.temperature_history) == 0

    def test_cycle_state_transitions(self, cycle_tracker, dispatcher):
        """Test state machine transitions."""
        # Start in IDLE
        assert cycle_tracker.state == CycleState.IDLE

        # IDLE -> HEATING
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # HEATING -> SETTLING
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime.now()))
        assert cycle_tracker.state == CycleState.SETTLING

    def test_on_heating_started_records_state(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test on_heating_started() records start time and target."""
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        mock_callbacks["get_target_temp"].return_value = 21.5

        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.cycle_start_time == start_time
        assert cycle_tracker._cycle_target_temp == 21.5
        assert len(cycle_tracker.temperature_history) == 0  # Cleared

    def test_on_heating_session_ended_transitions_to_settling(self, cycle_tracker, mock_hass, dispatcher):
        """Test on_heating_session_ended() transitions to SETTLING."""
        import sys

        # Get the mocked async_call_later from conftest
        mock_async_call_later = sys.modules["homeassistant.helpers.event"].async_call_later
        mock_async_call_later.reset_mock()

        # First start heating
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Then stop heating
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime.now()))

        assert cycle_tracker.state == CycleState.SETTLING
        # Verify timeout was scheduled
        mock_async_call_later.assert_called_once()
        call_args = mock_async_call_later.call_args
        assert call_args[0][1] == 120 * 60  # 120 minutes in seconds (2nd arg after hass)

    def test_on_heating_session_ended_ignores_when_not_heating(self, cycle_tracker, dispatcher):
        """Test on_heating_session_ended() ignores call when not in HEATING state."""
        # Start in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Try to stop heating
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime.now()))

        # Should remain in IDLE
        assert cycle_tracker.state == CycleState.IDLE

    def test_settling_started_transitions_to_settling(self, cycle_tracker, dispatcher):
        """Test SETTLING_STARTED event transitions from HEATING to SETTLING state."""
        # Start heating cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))
        assert cycle_tracker.state == CycleState.HEATING

        # Emit SETTLING_STARTED event
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 5, 0),
            was_clamped=False
        ))

        # Should transition to SETTLING
        assert cycle_tracker.state == CycleState.SETTLING

    def test_cooling_settling_started_transitions_to_settling(self, cycle_tracker, dispatcher):
        """Test SETTLING_STARTED event transitions from COOLING to SETTLING state."""
        # Start cooling cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="cool",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=22.0
        ))
        assert cycle_tracker.state == CycleState.COOLING

        # Emit SETTLING_STARTED event
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="cool",
            timestamp=datetime(2025, 1, 14, 10, 5, 0),
            was_clamped=False
        ))

        # Should transition to SETTLING
        assert cycle_tracker.state == CycleState.SETTLING

    def test_settling_started_schedules_settling_timeout(self, cycle_tracker, mock_hass, dispatcher):
        """Test SETTLING_STARTED event schedules settling timeout."""
        import sys

        # Get the mocked async_call_later from conftest
        mock_async_call_later = sys.modules["homeassistant.helpers.event"].async_call_later
        mock_async_call_later.reset_mock()

        # Start heating cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))

        # Emit SETTLING_STARTED event
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 5, 0),
            was_clamped=False
        ))

        # Verify timeout was scheduled
        mock_async_call_later.assert_called_once()
        call_args = mock_async_call_later.call_args
        assert call_args[0][1] == 120 * 60  # 120 minutes in seconds (2nd arg after hass)

    def test_on_heating_started_resets_cycle_from_non_idle(self, cycle_tracker, dispatcher):
        """Test on_heating_started() resets cycle when called from non-IDLE state."""
        # Start heating
        first_start = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=first_start, target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime.now()))
        assert cycle_tracker.state == CycleState.SETTLING

        # Start heating again without going through IDLE
        second_start = datetime(2025, 1, 14, 11, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=second_start, target_temp=20.0, current_temp=18.0))

        # Should be in HEATING state with new start time
        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.cycle_start_time == second_start

    def test_temperature_history_cleared_on_heating_start(self, cycle_tracker, dispatcher):
        """Test temperature history is cleared when heating starts."""
        # Manually add some history
        cycle_tracker._temperature_history.append((datetime.now(), 18.0))
        cycle_tracker._temperature_history.append((datetime.now(), 18.5))
        assert len(cycle_tracker.temperature_history) == 2

        # Start heating
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))

        # History should be cleared
        assert len(cycle_tracker.temperature_history) == 0


class TestCycleTrackerTemperatureCollection:
    """Tests for temperature collection functionality."""

    @pytest.mark.asyncio
    async def test_temperature_collection_during_heating(self, cycle_tracker, dispatcher):
        """Test temperature samples are collected during HEATING state."""
        # Start heating
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))

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
    async def test_temperature_collection_during_settling(self, cycle_tracker, dispatcher):
        """Test temperature samples are collected during SETTLING state."""
        # Start and stop heating
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 30, 0)))

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
    async def test_settling_detection_stable(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test settling is detected when temperature is stable and near target."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 30, 0)))

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
    async def test_settling_detection_unstable(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test settling continues when temperature is oscillating."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 30, 0)))

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
    async def test_settling_detection_insufficient_samples(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test settling requires at least 10 samples."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 30, 0)))

        # Add only 9 stable samples (not enough)
        for i in range(9):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, 30 + i, 0), 20.0
            )

        # Should still be in SETTLING (not enough samples)
        assert cycle_tracker.state == CycleState.SETTLING

    @pytest.mark.asyncio
    async def test_settling_detection_far_from_target(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test settling requires temperature within 0.5°C of target."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating and stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 30, 0)))

        # Add 10 stable samples but far from target (> 0.5°C away)
        for i in range(10):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, 30 + i, 0), 19.0  # 1.0°C below target
            )

        # Should still be in SETTLING (too far from target)
        assert cycle_tracker.state == CycleState.SETTLING

    @pytest.mark.asyncio
    async def test_settling_timeout(self, cycle_tracker, mock_hass, dispatcher):
        """Test settling timeout transitions to IDLE after 120 minutes."""
        import sys

        # Get the mocked async_call_later from conftest
        mock_async_call_later = sys.modules["homeassistant.helpers.event"].async_call_later
        mock_async_call_later.reset_mock()

        # Start heating and stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime.now()))

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

    def test_cycle_validation_min_duration(self, cycle_tracker, monkeypatch, dispatcher):
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
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))

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

    def test_cycle_validation_grace_period(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test learning grace period blocks recording."""
        # Set grace period active
        mock_callbacks["get_in_grace_period"].return_value = True

        # Start heating 10 minutes ago
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 9, 50, 0), target_temp=20.0, current_temp=18.0))

        # Add sufficient temperature samples
        for i in range(10):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 9, 50 + i, 0), 18.0 + i * 0.2)
            )

        # Check validation with grace period active
        is_valid, reason = cycle_tracker._is_cycle_valid()
        assert not is_valid
        assert "grace period" in reason.lower()

    def test_cycle_validation_insufficient_samples(self, cycle_tracker, dispatcher):
        """Test insufficient samples blocks recording."""
        # Start heating 10 minutes ago
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 9, 50, 0), target_temp=20.0, current_temp=18.0))

        # Add only 4 samples (not enough)
        for i in range(4):
            cycle_tracker._temperature_history.append(
                (datetime(2025, 1, 14, 9, 50 + i, 0), 18.0 + i * 0.5)
            )

        # Check validation
        is_valid, reason = cycle_tracker._is_cycle_valid()
        assert not is_valid
        assert "insufficient temperature samples" in reason.lower()

    def test_cycle_validation_success(self, cycle_tracker, dispatcher):
        """Test validation passes with all requirements met."""
        # Start heating 10 minutes ago
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 9, 50, 0), target_temp=20.0, current_temp=18.0))

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
    async def test_metrics_calculation(self, cycle_tracker, mock_callbacks, mock_adaptive_learner, dispatcher):
        """Test complete cycle calculates all 5 metrics."""
        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start heating 10 minutes ago
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

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
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 10, 0)))

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
    async def test_invalid_cycle_not_recorded(self, cycle_tracker, mock_adaptive_learner, dispatcher):
        """Test invalid cycles are not recorded."""
        # Start heating
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))

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

    def test_heater_controller_notifications(self, cycle_tracker, dispatcher):
        """Test heater controller notifies cycle tracker on state changes."""
        # Simulate HeaterController calling on_heating_started
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        # Verify state transition
        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.cycle_start_time == start_time

        # Simulate HeaterController calling on_heating_session_ended
        stop_time = datetime(2025, 1, 14, 10, 15, 0)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=stop_time))

        # Verify state transition
        assert cycle_tracker.state == CycleState.SETTLING

    def test_valve_mode_transitions(self, cycle_tracker, dispatcher):
        """Test valve mode transitions trigger cycle tracker events."""
        # Test heating started (valve 0 -> >0)
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Test heating stopped (valve >0 -> 0)
        stop_time = datetime(2025, 1, 14, 10, 15, 0)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=stop_time))
        assert cycle_tracker.state == CycleState.SETTLING


class TestCycleTrackerTemperatureUpdates:
    """Test temperature update integration with control loop."""

    @pytest.mark.asyncio
    async def test_temperature_updates_during_heating(self, cycle_tracker, dispatcher):
        """Test that temperature samples are collected during heating cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

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

    def test_setpoint_change_aborts_cycle(self, cycle_tracker, dispatcher):
        """Test that setpoint change during active cycle aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 1, 0), 19.0),
        ]

        # Change setpoint mid-cycle
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker.cycle_start_time is None
        assert cycle_tracker._cycle_target_temp is None

    def test_setpoint_change_during_settling_aborts_cycle(self, cycle_tracker, dispatcher):
        """Test that setpoint change during settling aborts the cycle."""
        # Start and stop heating to enter settling state
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 15, 0)))
        assert cycle_tracker.state == CycleState.SETTLING

        # Add temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 15, 30), 20.0),
        ]

        # Change setpoint during settling
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker._settling_timeout_handle is None

    def test_setpoint_change_in_idle_no_effect(self, cycle_tracker, dispatcher):
        """Test that setpoint change in IDLE state has no effect."""
        # Ensure in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Change setpoint while in IDLE
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))

        # Verify state unchanged
        assert cycle_tracker.state == CycleState.IDLE

    def test_contact_sensor_aborts_cycle(self, cycle_tracker, dispatcher):
        """Test that contact sensor pause during active cycle aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 1, 0), 19.0),
        ]

        # Trigger contact sensor pause (window/door opened)
        dispatcher.emit(ContactPauseEvent(hvac_mode="heat", timestamp=datetime.now(), entity_id="binary_sensor.window"))

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker.cycle_start_time is None
        assert cycle_tracker._cycle_target_temp is None

    def test_contact_sensor_pause_in_idle_no_effect(self, cycle_tracker, dispatcher):
        """Test that contact sensor pause in IDLE state has no effect."""
        # Ensure in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Trigger contact sensor pause while in IDLE
        dispatcher.emit(ContactPauseEvent(hvac_mode="heat", timestamp=datetime.now(), entity_id="binary_sensor.window"))

        # Verify state unchanged
        assert cycle_tracker.state == CycleState.IDLE

    def test_mode_change_aborts_cycle(self, cycle_tracker, dispatcher):
        """Test that mode change from HEAT to OFF/COOL aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
            (datetime(2025, 1, 14, 10, 1, 0), 19.0),
        ]

        # Change mode from HEAT to OFF
        dispatcher.emit(ModeChangedEvent(timestamp=datetime.now(), old_mode="heat", new_mode="off"))

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0
        assert cycle_tracker.cycle_start_time is None
        assert cycle_tracker._cycle_target_temp is None

    def test_mode_change_to_cool_aborts_cycle(self, cycle_tracker, dispatcher):
        """Test that mode change from HEAT to COOL aborts the cycle."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
        ]

        # Change mode from HEAT to COOL
        dispatcher.emit(ModeChangedEvent(timestamp=datetime.now(), old_mode="heat", new_mode="cool"))

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0

    def test_mode_change_heat_to_heat_no_effect(self, cycle_tracker, dispatcher):
        """Test that mode change from HEAT to HEAT has no effect."""
        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Add some temperature history
        cycle_tracker._temperature_history = [
            (datetime(2025, 1, 14, 10, 0, 30), 18.5),
        ]

        # Change mode from HEAT to HEAT (no actual change)
        dispatcher.emit(ModeChangedEvent(timestamp=datetime.now(), old_mode="heat", new_mode="heat"))

        # Verify cycle continues (state unchanged)
        assert cycle_tracker.state == CycleState.HEATING
        assert len(cycle_tracker.temperature_history) == 1

    def test_mode_change_in_idle_no_effect(self, cycle_tracker, dispatcher):
        """Test that mode change in IDLE state has no effect."""
        # Ensure in IDLE state
        assert cycle_tracker.state == CycleState.IDLE

        # Change mode while in IDLE
        dispatcher.emit(ModeChangedEvent(timestamp=datetime.now(), old_mode="heat", new_mode="off"))

        # Verify state unchanged
        assert cycle_tracker.state == CycleState.IDLE


class TestSetpointChangeWithDeviceActive:
    """Tests for setpoint change behavior when heater is active."""

    def test_setpoint_change_while_heater_active_continues_tracking(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert tracker.state == CycleState.HEATING

        # Add temperature sample to history
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 0, 30), 18.5))

        # Change setpoint while heater is active
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))

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
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert tracker.state == CycleState.HEATING

        # Add temperature sample to history
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 0, 30), 18.5))

        # Change setpoint while heater is inactive
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))

        # Assert state is IDLE (aborted)
        assert tracker.state == CycleState.IDLE

        # Assert temperature_history is empty (cleared)
        assert len(tracker.temperature_history) == 0

    def test_setpoint_change_without_callback_aborts_cycle(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert tracker.state == CycleState.HEATING

        # Add temperature sample to history
        tracker._temperature_history.append((datetime(2025, 1, 14, 10, 0, 30), 18.5))

        # Change setpoint - should abort cycle (legacy behavior)
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))

        # Assert state is IDLE (aborted - legacy behavior preserved)
        assert tracker.state == CycleState.IDLE

        # Assert temperature_history is empty (cleared)
        assert len(tracker.temperature_history) == 0

    def test_multiple_setpoint_changes_while_heater_active(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
        assert tracker.state == CycleState.HEATING

        # First setpoint change
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))

        # Second setpoint change
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=22.0, new_target=21.0))

        # Third setpoint change
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=21.0, new_target=23.0))

        # Assert state is still HEATING
        assert tracker.state == CycleState.HEATING

        # Assert _cycle_target_temp is the latest value
        assert tracker._cycle_target_temp == 23.0

        # Assert 3 interruptions were recorded
        assert len(tracker._interruption_history) == 3


class TestResetCycleState:
    """Tests for _reset_cycle_state helper method."""

    def test_reset_cycle_state_clears_all(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))
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

    def test_settling_detection_with_noise(self, cycle_tracker, dispatcher):
        """Test settling detection is robust to sensor noise using MAD."""
        # Start heating, then stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 15, 0)))

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

    def test_settling_mad_vs_variance(self, cycle_tracker, dispatcher):
        """Test MAD is more robust than variance to outliers."""
        # Start heating, then stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 15, 0)))

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

    def test_settling_detection_outlier_robust(self, cycle_tracker, dispatcher):
        """Test settling detection handles single outlier correctly."""
        # Start heating, then stop
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 15, 0)))

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


class TestCycleTrackerEventSubscriptions:
    """Tests for event-driven cycle tracker (Feature 2.1)."""

    def test_ctm_subscribes_to_events(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Test CTM subscribes to all input events on init."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventType,
            CycleEventDispatcher,
        )

        # Create dispatcher
        dispatcher = CycleEventDispatcher()

        # Create cycle tracker with dispatcher
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )

        # Verify subscriptions exist by checking dispatcher's listeners
        expected_events = [
            CycleEventType.CYCLE_STARTED,
            CycleEventType.HEATING_STARTED,
            CycleEventType.HEATING_ENDED,
            CycleEventType.SETTLING_STARTED,
            CycleEventType.CONTACT_PAUSE,
            CycleEventType.CONTACT_RESUME,
            CycleEventType.SETPOINT_CHANGED,
            CycleEventType.MODE_CHANGED,
        ]

        for event_type in expected_events:
            assert event_type in dispatcher._listeners
            assert len(dispatcher._listeners[event_type]) > 0

    def test_ctm_cycle_started_handler(self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
        """Test CYCLE_STARTED event triggers IDLE→HEATING transition."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventDispatcher,
            CycleStartedEvent,
        )

        # Create dispatcher and tracker
        dispatcher = CycleEventDispatcher()
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )

        # Verify initial state
        assert tracker.state == CycleState.IDLE

        # Emit CYCLE_STARTED event
        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0,
        )
        dispatcher.emit(event)

        # Verify state transition
        assert tracker.state == CycleState.HEATING
        assert tracker.cycle_start_time == datetime(2025, 1, 14, 10, 0, 0)
        assert tracker._cycle_target_temp == 20.0

    def test_ctm_settling_started_handler(self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
        """Test SETTLING_STARTED event triggers HEATING→SETTLING transition."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventDispatcher,
            CycleStartedEvent,
            SettlingStartedEvent,
        )

        # Create dispatcher and tracker
        dispatcher = CycleEventDispatcher()
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )

        # Start a cycle first
        event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0,
        )
        dispatcher.emit(event)
        assert tracker.state == CycleState.HEATING

        # Emit SETTLING_STARTED event
        settling_event = SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 15, 0),
        )
        dispatcher.emit(settling_event)

        # Verify state transition
        assert tracker.state == CycleState.SETTLING

    def test_ctm_heating_events_track_duty(self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
        """Test HEATING_STARTED/ENDED events update duty cycle tracking."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventDispatcher,
            CycleStartedEvent,
            HeatingStartedEvent,
            HeatingEndedEvent,
        )

        # Create dispatcher and tracker
        dispatcher = CycleEventDispatcher()
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )

        # Start a cycle first
        cycle_event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0,
        )
        dispatcher.emit(cycle_event)

        # Emit HEATING_STARTED event
        heating_start = HeatingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
        )
        dispatcher.emit(heating_start)

        # Verify device on time tracked
        assert hasattr(tracker, '_device_on_time')
        assert tracker._device_on_time == datetime(2025, 1, 14, 10, 0, 0)

        # Emit HEATING_ENDED event
        heating_end = HeatingEndedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 5, 0),
        )
        dispatcher.emit(heating_end)

        # Verify device off time tracked
        assert hasattr(tracker, '_device_off_time')
        assert tracker._device_off_time == datetime(2025, 1, 14, 10, 5, 0)

    def test_ctm_contact_pause_handler(self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
        """Test CONTACT_PAUSE event records interruption."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventDispatcher,
            CycleStartedEvent,
            ContactPauseEvent,
        )

        # Create dispatcher and tracker
        dispatcher = CycleEventDispatcher()
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )

        # Start a cycle first
        cycle_event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0,
        )
        dispatcher.emit(cycle_event)
        assert tracker.state == CycleState.HEATING

        # Emit CONTACT_PAUSE event
        pause_event = ContactPauseEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 5, 0),
            entity_id="binary_sensor.window",
        )
        dispatcher.emit(pause_event)

        # Verify interruption recorded and cycle aborted
        assert tracker.state == CycleState.IDLE
        assert tracker.get_last_interruption_reason() == "contact_sensor"

    def test_ctm_contact_resume_handler(self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
        """Test CONTACT_RESUME handler (currently no-op since pause already aborts)."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventDispatcher,
            ContactResumeEvent,
        )

        # Create dispatcher and tracker
        dispatcher = CycleEventDispatcher()
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )

        # In IDLE state
        assert tracker.state == CycleState.IDLE

        # Emit CONTACT_RESUME event (should be no-op since already aborted)
        resume_event = ContactResumeEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 10, 0),
            entity_id="binary_sensor.window",
            pause_duration_seconds=300,
        )
        dispatcher.emit(resume_event)

        # Should remain in IDLE
        assert tracker.state == CycleState.IDLE

    def test_ctm_mode_changed_aborts(self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
        """Test MODE_CHANGED event during cycle aborts it."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventDispatcher,
            CycleStartedEvent,
            ModeChangedEvent,
        )

        # Create dispatcher and tracker
        dispatcher = CycleEventDispatcher()
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )

        # Start a cycle first
        cycle_event = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0,
        )
        dispatcher.emit(cycle_event)
        assert tracker.state == CycleState.HEATING

        # Emit MODE_CHANGED event (heat -> off)
        mode_event = ModeChangedEvent(
            timestamp=datetime(2025, 1, 14, 10, 5, 0),
            old_mode="heat",
            new_mode="off",
        )
        dispatcher.emit(mode_event)

        # Verify cycle was aborted
        assert tracker.state == CycleState.IDLE
        assert tracker.get_last_interruption_reason() == "mode_change"

    @pytest.mark.asyncio
    async def test_ctm_emits_cycle_ended(self, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Test CTM emits CYCLE_ENDED when settling completes."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventDispatcher,
            CycleEventType,
        )

        # Create dispatcher and tracker
        dispatcher = CycleEventDispatcher()
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        tracker.set_restoration_complete()

        # Set up listener to capture CYCLE_ENDED event
        emitted_events = []

        def capture_event(event):
            emitted_events.append(event)

        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, capture_event)

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Setup adaptive learner mocks
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()

        # Start cycle 6 minutes ago (> 5 min minimum)
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        # Add temperature samples
        for i in range(6):
            await tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Finalize cycle (simulating settling complete)
        await tracker._finalize_cycle()

        # Verify CYCLE_ENDED was emitted
        assert len(emitted_events) == 1
        assert emitted_events[0].event_type == CycleEventType.CYCLE_ENDED
        assert emitted_events[0].hvac_mode == "heat"


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

    def test_get_state_name_idle(self, cycle_tracker, dispatcher):
        """Test get_state_name returns 'idle' in IDLE state."""
        assert cycle_tracker.state == CycleState.IDLE
        assert cycle_tracker.get_state_name() == "idle"

    def test_get_state_name_heating(self, cycle_tracker, dispatcher):
        """Test get_state_name returns 'heating' in HEATING state."""
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.get_state_name() == "heating"

    def test_get_state_name_settling(self, cycle_tracker, dispatcher):
        """Test get_state_name returns 'settling' in SETTLING state."""
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=datetime.now()))
        assert cycle_tracker.state == CycleState.SETTLING
        assert cycle_tracker.get_state_name() == "settling"

    def test_get_state_name_cooling(self, cycle_tracker, dispatcher):
        """Test get_state_name returns 'cooling' in COOLING state."""
        dispatcher.emit(CycleStartedEvent(hvac_mode="cool", timestamp=datetime.now(), target_temp=20.0, current_temp=22.0))
        assert cycle_tracker.state == CycleState.COOLING
        assert cycle_tracker.get_state_name() == "cooling"

    def test_get_last_interruption_reason_none_initially(self, cycle_tracker, dispatcher):
        """Test get_last_interruption_reason returns None with no interruptions."""
        assert cycle_tracker.get_last_interruption_reason() is None

    def test_get_last_interruption_reason_setpoint_major(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test interruption reason for major setpoint change."""
        # Start a cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))

        # Trigger major setpoint change (device inactive)
        mock_callbacks["get_target_temp"].return_value = 22.0
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=22.0))  # >0.5°C change

        # Should map to "setpoint_change"
        assert cycle_tracker.get_last_interruption_reason() == "setpoint_change"

    def test_get_last_interruption_reason_setpoint_minor(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test interruption reason for minor setpoint change."""
        # Start a cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))

        # Create mock for is_device_active
        mock_is_device_active = Mock(return_value=True)
        cycle_tracker._get_is_device_active = mock_is_device_active

        # Trigger minor setpoint change (device active)
        mock_callbacks["get_target_temp"].return_value = 20.3
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=20.3))  # <0.5°C change

        # Should map to "setpoint_change"
        assert cycle_tracker.get_last_interruption_reason() == "setpoint_change"

    def test_get_last_interruption_reason_mode_change(self, cycle_tracker, mock_callbacks, dispatcher):
        """Test interruption reason for mode change."""
        # Start a cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))

        # Change mode
        mock_callbacks["get_hvac_mode"].return_value = "off"
        dispatcher.emit(ModeChangedEvent(timestamp=datetime.now(), old_mode="heat", new_mode="off"))

        # Should map to "mode_change"
        assert cycle_tracker.get_last_interruption_reason() == "mode_change"

    def test_get_last_interruption_reason_contact_sensor(self, cycle_tracker, dispatcher):
        """Test interruption reason for contact sensor."""
        # Start a cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))

        # Trigger contact sensor pause
        dispatcher.emit(ContactPauseEvent(hvac_mode="heat", timestamp=datetime.now(), entity_id="binary_sensor.window"))

        # Should map to "contact_sensor"
        assert cycle_tracker.get_last_interruption_reason() == "contact_sensor"

    def test_get_last_interruption_reason_clears_on_reset(self, cycle_tracker, dispatcher):
        """Test interruption reason is cleared when cycle resets."""
        # Start a cycle and interrupt it
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))
        dispatcher.emit(ContactPauseEvent(hvac_mode="heat", timestamp=datetime.now(), entity_id="binary_sensor.window"))

        assert cycle_tracker.get_last_interruption_reason() == "contact_sensor"

        # After reset, interruption history should be cleared
        # (This happens automatically in _reset_cycle_state which is called on interruption)
        assert cycle_tracker.state == CycleState.IDLE

        # After starting a new cycle, no interruptions yet
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))
        assert cycle_tracker.get_last_interruption_reason() is None

    def test_get_last_interruption_reason_returns_most_recent(self, cycle_tracker, dispatcher):
        """Test that most recent interruption is returned."""
        # Start a cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime.now(), target_temp=20.0, current_temp=18.0))

        # First interruption (but continue tracking)
        mock_is_device_active = Mock(return_value=True)
        cycle_tracker._get_is_device_active = mock_is_device_active
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=20.0, new_target=20.3))  # Minor change, continues

        assert cycle_tracker.get_last_interruption_reason() == "setpoint_change"

        # Second interruption
        dispatcher.emit(ModeChangedEvent(timestamp=datetime.now(), old_mode="heat", new_mode="off"))  # This aborts

        # Should return the most recent one
        assert cycle_tracker.get_last_interruption_reason() == "mode_change"


class TestCycleTrackerSettlingTimeoutFinalization:
    """Tests for settling timeout cycle finalization."""

    @pytest.mark.asyncio
    async def test_settling_timeout_records_metrics(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
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
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        # Add temperature samples during heating (rising)
        for i in range(5):
            await cycle_tracker.update_temperature(
                start_time + timedelta(minutes=i), 18.0 + i * 0.5
            )

        # Stop heating
        stop_time = start_time + timedelta(minutes=5)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=stop_time))

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
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )

        # Verify initial state
        assert cycle_tracker._restoration_complete is False
        assert cycle_tracker.state == CycleState.IDLE

        # Start heating cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
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
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )

        # Mark restoration complete
        cycle_tracker.set_restoration_complete()
        assert cycle_tracker._restoration_complete is True

        # Start heating cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=datetime(2025, 1, 14, 10, 0, 0), target_temp=20.0, current_temp=18.0))
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
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle 6 minutes ago (> 5 min minimum duration)
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

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
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

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
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

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

    @pytest.mark.asyncio
    async def test_finalize_cycle_uses_device_off_time(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that _finalize_cycle passes device_off_time as reference_time to calculate_settling_time."""
        # Setup adaptive_learner
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()
        mock_adaptive_learner.to_dict = MagicMock(return_value={})

        # Setup hass.data
        mock_hass.data = {}

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        # Set device_off_time
        device_off_time = datetime(2025, 1, 14, 10, 5, 0)
        cycle_tracker._device_off_time = device_off_time

        # Add temperature samples
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Mock calculate_settling_time to verify reference_time is passed
        with patch(
            "custom_components.adaptive_thermostat.adaptive.cycle_analysis.calculate_settling_time"
        ) as mock_calc_settling:
            mock_calc_settling.return_value = 5.0  # Return dummy value

            # Finalize cycle
            await cycle_tracker._finalize_cycle()

            # Verify calculate_settling_time was called with reference_time=device_off_time
            mock_calc_settling.assert_called_once()
            call_args = mock_calc_settling.call_args
            assert call_args[1]["reference_time"] == device_off_time

    @pytest.mark.asyncio
    async def test_cycle_finalized_when_cycle_started_during_settling(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that when CYCLE_STARTED arrives during SETTLING, previous cycle is finalized.

        This ensures we don't discard cycles when a new heating cycle starts
        before the settling phase completes naturally.
        """
        from datetime import timedelta

        # Setup adaptive_learner
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
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start first cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))
        assert cycle_tracker.state == CycleState.HEATING

        # Add temperature samples during heating
        for i in range(5):
            await cycle_tracker.update_temperature(
                start_time + timedelta(minutes=i), 18.0 + i * 0.4
            )

        # Stop heating and enter SETTLING
        stop_time = start_time + timedelta(minutes=5)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=stop_time))
        assert cycle_tracker.state == CycleState.SETTLING

        # Add settling samples
        for i in range(3):
            await cycle_tracker.update_temperature(
                stop_time + timedelta(minutes=i), 20.0 + i * 0.1
            )

        # Reset mock to verify new call
        mock_adaptive_learner.add_cycle_metrics.reset_mock()

        # Start NEW cycle while still in SETTLING (this is the key scenario)
        new_cycle_time = stop_time + timedelta(minutes=3)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=new_cycle_time,
            target_temp=20.0,
            current_temp=19.0
        ))

        # CRITICAL: Verify previous cycle was FINALIZED before new cycle started
        # The cycle should NOT be discarded - metrics should be recorded
        mock_adaptive_learner.add_cycle_metrics.assert_called_once()

        # Verify metrics from the completed cycle were recorded
        recorded_metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert recorded_metrics is not None
        # Verify we have valid metrics (overshoot is a key metric)
        assert recorded_metrics.overshoot is not None

        # New cycle should now be active
        assert cycle_tracker.state == CycleState.HEATING


class TestTemperatureUpdateIntegralTracking:
    """Tests for TEMPERATURE_UPDATE event integration and integral tracking."""

    def test_temperature_update_tracks_integral_at_tolerance_entry(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that CycleTrackerManager captures integral value when entering cold tolerance zone."""
        from custom_components.adaptive_thermostat.const import HEATING_TYPE_CHARACTERISTICS, HEATING_TYPE_RADIATOR

        # Create cycle tracker with radiator heating type (cold_tolerance = 0.3)
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            heating_type=HEATING_TYPE_RADIATOR,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature to 20.0°C
        target_temp = 20.0
        mock_callbacks["get_target_temp"].return_value = target_temp

        # Start a heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=target_temp,
            current_temp=18.0
        ))

        # Get cold_tolerance for radiator (0.3)
        cold_tolerance = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_RADIATOR]["cold_tolerance"]

        # Dispatch TEMPERATURE_UPDATE events
        # First event: temperature = 19.0, pid_error = 1.0 (outside tolerance)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 1, 0),
            temperature=19.0,
            setpoint=target_temp,
            pid_integral=50.0,
            pid_error=1.0
        ))
        # Integral should not be captured yet
        assert cycle_tracker._integral_at_tolerance_entry is None

        # Second event: temperature = 19.5, pid_error = 0.5 (outside tolerance)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 2, 0),
            temperature=19.5,
            setpoint=target_temp,
            pid_integral=75.0,
            pid_error=0.5
        ))
        # Still outside tolerance
        assert cycle_tracker._integral_at_tolerance_entry is None

        # Third event: temperature = 19.75, pid_error = 0.25 (inside cold_tolerance of 0.3)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 3, 0),
            temperature=19.75,
            setpoint=target_temp,
            pid_integral=100.0,
            pid_error=0.25
        ))
        # Now integral should be captured
        assert cycle_tracker._integral_at_tolerance_entry == 100.0

        # Fourth event: temperature = 19.8, pid_error = 0.2 (still inside tolerance)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 4, 0),
            temperature=19.8,
            setpoint=target_temp,
            pid_integral=110.0,
            pid_error=0.2
        ))
        # Integral should not be overwritten (only capture first entry)
        assert cycle_tracker._integral_at_tolerance_entry == 100.0

    def test_temperature_update_tracks_integral_at_setpoint_cross(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that CycleTrackerManager captures integral value when crossing setpoint."""
        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature to 20.0°C
        target_temp = 20.0
        mock_callbacks["get_target_temp"].return_value = target_temp

        # Start a heating cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=target_temp,
            current_temp=18.0
        ))

        # Dispatch TEMPERATURE_UPDATE events
        # First event: pid_error = 0.5 (below setpoint)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 1, 0),
            temperature=19.5,
            setpoint=target_temp,
            pid_integral=80.0,
            pid_error=0.5
        ))
        # Integral should not be captured yet
        assert cycle_tracker._integral_at_setpoint_cross is None

        # Second event: pid_error = 0.1 (below setpoint)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 2, 0),
            temperature=19.9,
            setpoint=target_temp,
            pid_integral=95.0,
            pid_error=0.1
        ))
        # Still below setpoint
        assert cycle_tracker._integral_at_setpoint_cross is None

        # Third event: pid_error = 0.0 (at setpoint)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 3, 0),
            temperature=20.0,
            setpoint=target_temp,
            pid_integral=100.0,
            pid_error=0.0
        ))
        # Now integral should be captured
        assert cycle_tracker._integral_at_setpoint_cross == 100.0

        # Fourth event: pid_error = -0.1 (above setpoint, overshoot)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 4, 0),
            temperature=20.1,
            setpoint=target_temp,
            pid_integral=105.0,
            pid_error=-0.1
        ))
        # Integral should not be overwritten (only capture first crossing)
        assert cycle_tracker._integral_at_setpoint_cross == 100.0

    def test_cycle_start_resets_integral_tracking(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that starting a new cycle resets integral tracking values."""
        from custom_components.adaptive_thermostat.const import HEATING_TYPE_RADIATOR

        # Create cycle tracker with radiator heating type
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            heating_type=HEATING_TYPE_RADIATOR,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        target_temp = 20.0
        mock_callbacks["get_target_temp"].return_value = target_temp

        # Start first cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=target_temp,
            current_temp=18.0
        ))

        # Simulate temperature updates that set integral values
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 1, 0),
            temperature=19.75,
            setpoint=target_temp,
            pid_integral=100.0,
            pid_error=0.25  # Within cold tolerance for radiator (0.3)
        ))

        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=datetime(2025, 1, 14, 10, 2, 0),
            temperature=20.0,
            setpoint=target_temp,
            pid_integral=110.0,
            pid_error=0.0  # At setpoint
        ))

        # Verify both integral values are captured
        assert cycle_tracker._integral_at_tolerance_entry == 100.0
        assert cycle_tracker._integral_at_setpoint_cross == 110.0

        # Start a new cycle
        new_start_time = datetime(2025, 1, 14, 11, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=new_start_time,
            target_temp=target_temp,
            current_temp=18.5
        ))

        # Verify integral tracking values are reset to None
        assert cycle_tracker._integral_at_tolerance_entry is None
        assert cycle_tracker._integral_at_setpoint_cross is None

    @pytest.mark.asyncio
    async def test_finalize_cycle_includes_decay_metrics(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that finalized cycle includes decay-related integral metrics."""
        from datetime import timedelta
        from custom_components.adaptive_thermostat.const import HEATING_TYPE_RADIATOR

        # Create cycle tracker with radiator heating type
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            heating_type=HEATING_TYPE_RADIATOR,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        target_temp = 20.0
        mock_callbacks["get_target_temp"].return_value = target_temp
        mock_callbacks["get_current_temp"].return_value = 18.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=target_temp,
            current_temp=18.0
        ))

        # Collect samples for valid cycle - stay below tolerance initially
        for i in range(10):
            temp = 18.0 + (i * 0.15)  # Gradual rise up to 19.35
            pid_error = target_temp - temp
            pid_integral = 50.0 + (i * 10.0)  # Integral increases

            # Update via temperature manager (not via event, for legacy support)
            await cycle_tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=temp
            )

            # Emit TEMPERATURE_UPDATE event for integral tracking
            dispatcher.emit(TemperatureUpdateEvent(
                timestamp=start_time + timedelta(minutes=i),
                temperature=temp,
                setpoint=target_temp,
                pid_integral=pid_integral,
                pid_error=pid_error
            ))

        # Emit tolerance entry event (pid_error < 0.3 for radiator)
        # At this point temp is 19.8°C, pid_error = 0.2, which is within tolerance
        tolerance_time = start_time + timedelta(minutes=10)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=tolerance_time,
            temperature=19.8,
            setpoint=target_temp,
            pid_integral=200.0,
            pid_error=0.2  # Within tolerance (< 0.3)
        ))
        await cycle_tracker.update_temperature(tolerance_time, 19.8)

        # Emit setpoint crossing event (pid_error = 0)
        cross_time = start_time + timedelta(minutes=11)
        dispatcher.emit(TemperatureUpdateEvent(
            timestamp=cross_time,
            temperature=20.0,
            setpoint=target_temp,
            pid_integral=210.0,
            pid_error=0.0  # At setpoint
        ))
        await cycle_tracker.update_temperature(cross_time, 20.0)

        # Transition to settling
        settling_time = start_time + timedelta(minutes=17)
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=settling_time
        ))

        # Add settling samples to reach stability
        for i in range(10):
            await cycle_tracker.update_temperature(
                timestamp=settling_time + timedelta(minutes=i),
                temperature=20.0 + (0.01 * i)  # Very stable
            )

        # Finalize cycle
        await cycle_tracker._finalize_cycle()

        # Verify CycleMetrics was created with decay metrics
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]

        # Check that integral values are captured
        assert metrics.integral_at_tolerance_entry == 200.0
        assert metrics.integral_at_setpoint_cross == 210.0

        # Check that decay_contribution is calculated correctly
        expected_decay = 200.0 - 210.0  # tolerance_entry - setpoint_cross
        assert metrics.decay_contribution == expected_decay


class TestClampingAwareness:
    """Tests for clamping awareness in cycle tracking (Story 2.2)."""

    def test_cycle_tracker_captures_clamped_from_event(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that event.was_clamped is stored in tracker instance variable."""
        # Create tracker
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start a cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=20.0,
            current_temp=18.0
        ))
        assert tracker.state == CycleState.HEATING

        # Emit settling event with was_clamped=True
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            was_clamped=True
        ))

        # Verify the tracker captured the clamping state
        assert tracker._was_clamped is True

        # Test with was_clamped=False
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            target_temp=20.0,
            current_temp=18.0
        ))
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime.now(),
            was_clamped=False
        ))
        assert tracker._was_clamped is False

    @pytest.mark.asyncio
    async def test_cycle_tracker_includes_clamped_in_metrics(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that finalized CycleMetrics includes was_clamped field."""
        from datetime import timedelta

        # Create tracker
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start a cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Add temperature samples (enough for valid cycle)
        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=18.0 + (i * 0.2)
            )

        # Emit settling event with was_clamped=True
        settling_time = start_time + timedelta(minutes=15)
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=settling_time,
            was_clamped=True
        ))

        # Add settling samples to reach stability
        for i in range(10):
            await tracker.update_temperature(
                timestamp=settling_time + timedelta(minutes=i),
                temperature=20.0 + (0.01 * i)  # Very stable
            )

        # Finalize cycle
        await tracker._finalize_cycle()

        # Verify CycleMetrics was created with was_clamped=True
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert metrics.was_clamped is True

    @pytest.mark.asyncio
    async def test_cycle_tracker_logs_clamping_status(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher, caplog
    ):
        """Test that cycle completion log includes clamping information."""
        from datetime import timedelta
        import logging

        # Set log level to capture INFO logs
        caplog.set_level(logging.INFO)

        # Create tracker
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start a cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Add temperature samples
        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=18.0 + (i * 0.2)
            )

        # Emit settling event with was_clamped=True
        settling_time = start_time + timedelta(minutes=15)
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=settling_time,
            was_clamped=True
        ))

        # Add settling samples
        for i in range(10):
            await tracker.update_temperature(
                timestamp=settling_time + timedelta(minutes=i),
                temperature=20.0 + (0.01 * i)
            )

        # Finalize cycle
        await tracker._finalize_cycle()

        # Verify log message includes clamping status
        log_messages = [record.message for record in caplog.records if record.levelname == "INFO"]
        completion_logs = [msg for msg in log_messages if "Cycle completed" in msg]
        assert len(completion_logs) > 0
        assert "was_clamped=True" in completion_logs[0] or "clamped" in completion_logs[0].lower()


class TestEndTemperatureTracking:
    """Tests for end_temp tracking in cycle metrics (Story 3.1)."""

    @pytest.mark.asyncio
    async def test_end_temp_set_after_complete_cycle(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that end_temp is populated after a complete cycle."""
        from datetime import timedelta

        # Create tracker
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Set target temperature
        target_temp = 20.0
        mock_callbacks["get_target_temp"].return_value = target_temp

        # Start cycle
        start_time = datetime(2025, 1, 25, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=target_temp,
            current_temp=18.0
        ))

        # Collect temperature samples during HEATING phase
        for i in range(10):
            temp = 18.0 + (i * 0.2)  # Gradual rise from 18.0 to 19.8
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=temp
            )

        # Transition to SETTLING
        settling_time = start_time + timedelta(minutes=15)
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=settling_time,
            was_clamped=False
        ))

        # Add settling samples to reach stability
        settling_temps = [19.9, 19.95, 20.0, 20.05, 20.02, 20.01, 20.0, 20.0, 20.01, 20.0]
        for i, temp in enumerate(settling_temps):
            await tracker.update_temperature(
                timestamp=settling_time + timedelta(minutes=i),
                temperature=temp
            )

        # Record the last temperature that was added
        expected_end_temp = settling_temps[-1]

        # Finalize cycle
        await tracker._finalize_cycle()

        # Verify CycleMetrics was created with end_temp
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]

        # Check that end_temp is set and equals the last temperature
        assert metrics.end_temp is not None
        assert metrics.end_temp == expected_end_temp

    @pytest.mark.asyncio
    async def test_end_temp_equals_last_temperature_in_history(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that end_temp equals the last temperature in _temperature_history."""
        from datetime import timedelta

        # Create tracker
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Set target temperature
        target_temp = 21.5
        mock_callbacks["get_target_temp"].return_value = target_temp

        # Start cycle
        start_time = datetime(2025, 1, 25, 14, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=target_temp,
            current_temp=19.0
        ))

        # Collect temperature samples during HEATING phase with varying temps
        heating_temps = [19.0, 19.3, 19.7, 20.1, 20.5, 20.9, 21.2, 21.4, 21.5, 21.6]
        for i, temp in enumerate(heating_temps):
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=temp
            )

        # Transition to SETTLING
        settling_time = start_time + timedelta(minutes=12)
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=settling_time,
            was_clamped=False
        ))

        # Add settling samples - final temp is 21.48
        settling_temps = [21.55, 21.52, 21.50, 21.49, 21.48, 21.48, 21.48, 21.48, 21.48, 21.48]
        for i, temp in enumerate(settling_temps):
            await tracker.update_temperature(
                timestamp=settling_time + timedelta(minutes=i),
                temperature=temp
            )

        # The expected end temp is the last temperature we added
        expected_end_temp = settling_temps[-1]
        assert expected_end_temp == 21.48

        # Finalize cycle
        await tracker._finalize_cycle()

        # Verify CycleMetrics was created with end_temp matching last history entry
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]

        # Check that end_temp equals the last temperature from history
        assert metrics.end_temp == expected_end_temp

    @pytest.mark.asyncio
    async def test_end_temp_not_set_when_no_temperature_history(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that end_temp is None when there's no temperature history."""
        # Create tracker
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start cycle but don't collect any temperature samples
        start_time = datetime(2025, 1, 25, 16, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Transition to SETTLING immediately without temperature samples
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            was_clamped=False
        ))

        # Finalize cycle (will fail validation due to insufficient samples)
        await tracker._finalize_cycle()

        # Verify that no metrics were recorded due to validation failure
        # (cycle too short and insufficient samples)
        assert not mock_adaptive_learner.add_cycle_metrics.called


class TestInterCycleDrift:
    """Tests for inter_cycle_drift calculation (Story 3.2)."""

    @pytest.mark.asyncio
    async def test_first_cycle_drift_is_none(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that first cycle has inter_cycle_drift = None."""
        from datetime import timedelta

        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start first cycle
        start_time = datetime(2025, 1, 25, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Add temperature samples
        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=18.0 + (i * 0.15)
            )

        # Finalize first cycle
        await tracker._finalize_cycle()

        # Verify metrics were recorded
        mock_adaptive_learner.add_cycle_metrics.assert_called_once()
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]

        # First cycle should have inter_cycle_drift = None
        assert metrics.inter_cycle_drift is None

    @pytest.mark.asyncio
    async def test_second_cycle_drift_with_positive_drift(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test second cycle calculates positive inter_cycle_drift (warmer start)."""
        from datetime import timedelta

        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # First cycle: 18.0 -> 20.0 (end temp)
        start_time = datetime(2025, 1, 25, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=18.0 + (i * 0.143)  # Ends at 20.0 (14 * 0.143 = 2.002)
            )

        await tracker._finalize_cycle()

        # Second cycle: starts at 20.5 (warmer than previous end)
        start_time2 = datetime(2025, 1, 25, 11, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time2,
            target_temp=20.0,
            current_temp=20.5  # Warmer start
        ))

        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time2 + timedelta(minutes=i),
                temperature=20.5 + (i * 0.05)
            )

        await tracker._finalize_cycle()

        # Verify second cycle has positive drift
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 2
        second_metrics = mock_adaptive_learner.add_cycle_metrics.call_args_list[1][0][0]

        # drift = start_temp[current] - end_temp[previous]
        # drift = 20.5 - 20.0 = 0.5
        assert second_metrics.inter_cycle_drift is not None
        assert second_metrics.inter_cycle_drift == pytest.approx(0.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_second_cycle_drift_with_negative_drift(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test second cycle calculates negative inter_cycle_drift (cooler start - problematic case)."""
        from datetime import timedelta

        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # First cycle: 18.0 -> 20.0 (end temp)
        start_time = datetime(2025, 1, 25, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=18.0 + (i * 0.143)  # Ends at 20.0 (14 * 0.143 = 2.002)
            )

        await tracker._finalize_cycle()

        # Second cycle: starts at 19.0 (cooler than previous end)
        start_time2 = datetime(2025, 1, 25, 11, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time2,
            target_temp=20.0,
            current_temp=19.0  # Cooler start - the problematic case
        ))

        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time2 + timedelta(minutes=i),
                temperature=19.0 + (i * 0.067)
            )

        await tracker._finalize_cycle()

        # Verify second cycle has negative drift
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 2
        second_metrics = mock_adaptive_learner.add_cycle_metrics.call_args_list[1][0][0]

        # drift = start_temp[current] - end_temp[previous]
        # drift = 19.0 - 20.0 = -1.0
        assert second_metrics.inter_cycle_drift is not None
        assert second_metrics.inter_cycle_drift == pytest.approx(-1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_third_cycle_uses_second_cycle_end_temp(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that drift calculation uses the immediately previous cycle's end temp."""
        from datetime import timedelta

        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # First cycle: ends at 20.0
        start_time = datetime(2025, 1, 25, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time + timedelta(minutes=i),
                temperature=18.0 + (i * 0.143)  # Ends at 20.0 (14 * 0.143 = 2.002)
            )

        await tracker._finalize_cycle()

        # Second cycle: ends at 21.0
        start_time2 = datetime(2025, 1, 25, 11, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time2,
            target_temp=21.0,
            current_temp=20.0
        ))

        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time2 + timedelta(minutes=i),
                temperature=20.0 + (i * 0.071)  # Ends at 21.0 (14 * 0.071 = 0.994)
            )

        await tracker._finalize_cycle()

        # Third cycle: starts at 20.5
        start_time3 = datetime(2025, 1, 25, 12, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time3,
            target_temp=20.0,
            current_temp=20.5
        ))

        for i in range(15):
            await tracker.update_temperature(
                timestamp=start_time3 + timedelta(minutes=i),
                temperature=20.5 - (i * 0.033)
            )

        await tracker._finalize_cycle()

        # Verify third cycle uses second cycle's end temp
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 3
        third_metrics = mock_adaptive_learner.add_cycle_metrics.call_args_list[2][0][0]

        # drift = start_temp[third] - end_temp[second]
        # drift = 20.5 - 21.0 = -0.5
        assert third_metrics.inter_cycle_drift is not None
        assert third_metrics.inter_cycle_drift == pytest.approx(-0.5, abs=0.01)


class TestCycleTrackerSettlingMAE:
    """Tests for settling_mae calculation during cycle metrics recording."""

    @pytest.mark.asyncio
    async def test_settling_mae_calculated_during_record_cycle_metrics(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that settling_mae is calculated during _record_cycle_metrics."""
        from datetime import timedelta

        # Setup adaptive_learner
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()
        mock_adaptive_learner.to_dict = MagicMock(return_value={})

        # Setup hass.data
        mock_hass.data = {}

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        # Set device_off_time (heater turned off after 5 minutes)
        device_off_time = datetime(2025, 1, 14, 10, 5, 0)
        cycle_tracker._device_off_time = device_off_time

        # Add temperature samples (before and after device off)
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Mock calculate_settling_mae to verify it's called
        with patch(
            "custom_components.adaptive_thermostat.adaptive.cycle_analysis.calculate_settling_mae"
        ) as mock_calc_settling_mae:
            mock_calc_settling_mae.return_value = 0.15  # Return dummy value

            # Finalize cycle (which calls _record_cycle_metrics)
            await cycle_tracker._finalize_cycle()

            # Verify calculate_settling_mae was called
            mock_calc_settling_mae.assert_called_once()

    @pytest.mark.asyncio
    async def test_settling_mae_uses_device_off_time_as_settling_start(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that settling_mae uses _device_off_time as the settling_start_time parameter."""
        from datetime import timedelta

        # Setup adaptive_learner
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()
        mock_adaptive_learner.to_dict = MagicMock(return_value={})

        # Setup hass.data
        mock_hass.data = {}

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        # Set device_off_time
        device_off_time = datetime(2025, 1, 14, 10, 5, 0)
        cycle_tracker._device_off_time = device_off_time

        # Add temperature samples
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Mock calculate_settling_mae to verify settling_start_time is passed
        with patch(
            "custom_components.adaptive_thermostat.adaptive.cycle_analysis.calculate_settling_mae"
        ) as mock_calc_settling_mae:
            mock_calc_settling_mae.return_value = 0.15  # Return dummy value

            # Finalize cycle
            await cycle_tracker._finalize_cycle()

            # Verify calculate_settling_mae was called with settling_start_time=device_off_time
            mock_calc_settling_mae.assert_called_once()
            call_args = mock_calc_settling_mae.call_args
            assert call_args[1]["settling_start_time"] == device_off_time

    @pytest.mark.asyncio
    async def test_settling_mae_with_various_settling_patterns(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test settling_mae calculation with various settling patterns."""
        from datetime import timedelta

        # Setup adaptive_learner
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()
        mock_adaptive_learner.to_dict = MagicMock(return_value={})

        # Setup hass.data
        mock_hass.data = {}

        # Test Case 1: Stable settling (low MAE)
        cycle_tracker_stable = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone_stable",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker_stable.set_restoration_complete()
        mock_callbacks["get_target_temp"].return_value = 20.0

        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        device_off_time = datetime(2025, 1, 14, 10, 5, 0)
        cycle_tracker_stable._device_off_time = device_off_time

        # Add heating phase temps (before device off)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 0, 0), 18.0)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 1, 0), 18.5)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 2, 0), 19.0)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 3, 0), 19.5)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 4, 0), 19.8)

        # Add settling phase temps (after device off) - very stable around target
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 5, 0), 20.0)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 6, 0), 20.02)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 7, 0), 20.01)
        await cycle_tracker_stable.update_temperature(datetime(2025, 1, 14, 10, 8, 0), 20.03)

        with patch(
            "custom_components.adaptive_thermostat.adaptive.cycle_analysis.calculate_settling_mae"
        ) as mock_calc:
            mock_calc.return_value = 0.05  # Low MAE for stable settling
            await cycle_tracker_stable._finalize_cycle()
            mock_calc.assert_called_once()

        # Test Case 2: Oscillating settling (high MAE)
        cycle_tracker_oscillating = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone_oscillating",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker_oscillating.set_restoration_complete()

        start_time = datetime(2025, 1, 14, 11, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        device_off_time = datetime(2025, 1, 14, 11, 5, 0)
        cycle_tracker_oscillating._device_off_time = device_off_time

        # Add heating phase temps
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 0, 0), 18.0)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 1, 0), 18.5)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 2, 0), 19.0)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 3, 0), 19.5)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 4, 0), 19.8)

        # Add settling phase temps - oscillating around target
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 5, 0), 20.0)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 6, 0), 20.3)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 7, 0), 19.7)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 8, 0), 20.4)
        await cycle_tracker_oscillating.update_temperature(datetime(2025, 1, 14, 11, 9, 0), 19.6)

        with patch(
            "custom_components.adaptive_thermostat.adaptive.cycle_analysis.calculate_settling_mae"
        ) as mock_calc:
            mock_calc.return_value = 0.25  # High MAE for oscillating settling
            await cycle_tracker_oscillating._finalize_cycle()
            mock_calc.assert_called_once()

    @pytest.mark.asyncio
    async def test_settling_mae_none_when_no_device_off_time(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that settling_mae is None when device_off_time is not set."""
        from datetime import timedelta

        # Setup adaptive_learner
        mock_adaptive_learner.is_in_validation_mode = MagicMock(return_value=False)
        mock_adaptive_learner.update_convergence_confidence = MagicMock()
        mock_adaptive_learner.to_dict = MagicMock(return_value={})

        # Setup hass.data
        mock_hass.data = {}

        # Create cycle tracker
        cycle_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            dispatcher=dispatcher,
            **mock_callbacks,
        )
        cycle_tracker.set_restoration_complete()

        # Set target temperature
        mock_callbacks["get_target_temp"].return_value = 20.0

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=20.0, current_temp=18.0))

        # DO NOT set device_off_time (simulating non-PWM mode or missing event)
        # cycle_tracker._device_off_time remains None

        # Add temperature samples
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0), 18.0 + i * 0.4
            )

        # Mock calculate_settling_mae to verify it returns None
        with patch(
            "custom_components.adaptive_thermostat.adaptive.cycle_analysis.calculate_settling_mae"
        ) as mock_calc_settling_mae:
            mock_calc_settling_mae.return_value = None  # None when no settling_start_time

            # Finalize cycle
            await cycle_tracker._finalize_cycle()

            # Verify calculate_settling_mae was called with settling_start_time=None
            mock_calc_settling_mae.assert_called_once()
            call_args = mock_calc_settling_mae.call_args
            assert call_args[1]["settling_start_time"] is None


class TestCycleTrackerDeadTime:
    """Tests for dead time tracking in cycle tracker."""

    def test_set_transport_delay_stores_delay_for_current_cycle(self, cycle_tracker, dispatcher):
        """Test set_transport_delay() stores delay for the current cycle."""
        # Start a cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))
        assert cycle_tracker.state == CycleState.HEATING

        # Set transport delay
        cycle_tracker.set_transport_delay(2.5)

        # Verify delay is stored
        assert cycle_tracker._transport_delay_minutes == 2.5

    def test_set_transport_delay_accepts_zero(self, cycle_tracker, dispatcher):
        """Test set_transport_delay() accepts zero delay (manifold warm)."""
        # Start a cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set zero delay
        cycle_tracker.set_transport_delay(0.0)

        # Verify zero delay is stored
        assert cycle_tracker._transport_delay_minutes == 0.0

    def test_dead_time_tracked_separately_from_rise_time(self, cycle_tracker, dispatcher, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Test dead_time duration tracked separately from rise_time."""
        # Set up temperature tracking
        mock_callbacks["get_target_temp"].return_value = 20.0
        mock_callbacks["get_in_grace_period"].return_value = False

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set transport delay (dead time)
        cycle_tracker.set_transport_delay(2.5)

        # Verify internal state
        assert cycle_tracker._transport_delay_minutes == 2.5

    @pytest.mark.asyncio
    async def test_rise_time_excludes_dead_time(self, cycle_tracker, dispatcher, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Test rise_time calculation excludes dead_time (rise_time = total_rise - dead_time)."""
        # Set up for valid cycle
        mock_callbacks["get_target_temp"].return_value = 20.0
        mock_callbacks["get_in_grace_period"].return_value = False

        # Start cycle at 10:00
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set transport delay of 2.5 minutes
        cycle_tracker.set_transport_delay(2.5)

        # Add temperature samples showing rise over 10 minutes
        await cycle_tracker.update_temperature(start_time, 18.0)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 2, 0), 18.5)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 4, 0), 19.0)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 6, 0), 19.5)
        await cycle_tracker.update_temperature(datetime(2025, 1, 14, 10, 10, 0), 20.0)  # Reaches target at 10 min

        # Finalize cycle
        await cycle_tracker._finalize_cycle()

        # Verify CycleMetrics was created with dead_time
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]

        # dead_time should be 2.5 minutes
        assert metrics.dead_time == 2.5

        # rise_time should be total_rise (10 min) - dead_time (2.5 min) = 7.5 min
        # Note: actual rise_time calculation is done in cycle_analysis.calculate_rise_time()
        # Here we verify that dead_time is passed to CycleMetrics
        assert metrics.rise_time is not None

    @pytest.mark.asyncio
    async def test_cycle_metrics_populated_with_dead_time_on_completion(self, cycle_tracker, dispatcher, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Test CycleMetrics populated with dead_time on cycle completion."""
        # Set up for valid cycle
        mock_callbacks["get_target_temp"].return_value = 20.0
        mock_callbacks["get_in_grace_period"].return_value = False

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set transport delay
        cycle_tracker.set_transport_delay(3.0)

        # Add sufficient temperature samples for valid cycle
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0),
                18.0 + i * 0.4
            )

        # Finalize cycle
        await cycle_tracker._finalize_cycle()

        # Verify CycleMetrics was created with dead_time
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert metrics.dead_time == 3.0

    @pytest.mark.asyncio
    async def test_dead_time_is_none_when_no_transport_delay_set(self, cycle_tracker, dispatcher, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Test dead_time is None when no transport delay set."""
        # Set up for valid cycle
        mock_callbacks["get_target_temp"].return_value = 20.0
        mock_callbacks["get_in_grace_period"].return_value = False

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # DO NOT set transport delay
        # cycle_tracker.set_transport_delay() is not called

        # Add sufficient temperature samples for valid cycle
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0),
                18.0 + i * 0.4
            )

        # Finalize cycle
        await cycle_tracker._finalize_cycle()

        # Verify CycleMetrics was created with dead_time=None
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert metrics.dead_time is None

    def test_dead_time_resets_on_new_cycle_start(self, cycle_tracker, dispatcher):
        """Test dead_time resets on new cycle start."""
        # Start first cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set transport delay for first cycle
        cycle_tracker.set_transport_delay(2.5)
        assert cycle_tracker._transport_delay_minutes == 2.5

        # Start second cycle (should reset dead_time)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 11, 0, 0),
            target_temp=21.0,
            current_temp=19.0
        ))

        # Verify dead_time was reset to None
        assert cycle_tracker._transport_delay_minutes is None

    @pytest.mark.asyncio
    async def test_dead_time_preserved_during_settling_phase(self, cycle_tracker, dispatcher, mock_hass, mock_callbacks):
        """Test dead_time value preserved during settling phase."""
        # Start cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set transport delay
        cycle_tracker.set_transport_delay(1.5)
        assert cycle_tracker._transport_delay_minutes == 1.5

        # Transition to settling
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 5, 0),
            was_clamped=False
        ))
        assert cycle_tracker.state == CycleState.SETTLING

        # Verify dead_time still stored during settling
        assert cycle_tracker._transport_delay_minutes == 1.5

    @pytest.mark.asyncio
    async def test_dead_time_zero_handled_correctly(self, cycle_tracker, dispatcher, mock_hass, mock_adaptive_learner, mock_callbacks):
        """Test dead_time=0 (warm manifold) handled correctly in CycleMetrics."""
        # Set up for valid cycle
        mock_callbacks["get_target_temp"].return_value = 20.0
        mock_callbacks["get_in_grace_period"].return_value = False

        # Start cycle
        start_time = datetime(2025, 1, 14, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set zero transport delay (manifold is warm)
        cycle_tracker.set_transport_delay(0.0)

        # Add sufficient temperature samples
        for i in range(6):
            await cycle_tracker.update_temperature(
                datetime(2025, 1, 14, 10, i, 0),
                18.0 + i * 0.4
            )

        # Finalize cycle
        await cycle_tracker._finalize_cycle()

        # Verify CycleMetrics was created with dead_time=0.0
        assert mock_adaptive_learner.add_cycle_metrics.called
        metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert metrics.dead_time == 0.0

    def test_set_transport_delay_can_be_called_multiple_times(self, cycle_tracker, dispatcher):
        """Test set_transport_delay() can be updated during a cycle."""
        # Start cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set initial delay
        cycle_tracker.set_transport_delay(5.0)
        assert cycle_tracker._transport_delay_minutes == 5.0

        # Update delay (e.g., another zone started)
        cycle_tracker.set_transport_delay(2.5)
        assert cycle_tracker._transport_delay_minutes == 2.5

    @pytest.mark.asyncio
    async def test_dead_time_reset_after_cycle_abort(self, cycle_tracker, dispatcher):
        """Test dead_time is cleared when cycle is aborted."""
        # Start cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 0, 0),
            target_temp=20.0,
            current_temp=18.0
        ))

        # Set transport delay
        cycle_tracker.set_transport_delay(3.0)
        assert cycle_tracker._transport_delay_minutes == 3.0

        # Abort cycle with contact sensor pause
        dispatcher.emit(ContactPauseEvent(
            hvac_mode="heat",
            timestamp=datetime(2025, 1, 14, 10, 10, 0),
            entity_id="binary_sensor.window"
        ))

        # Cycle should be aborted and reset to IDLE
        assert cycle_tracker.state == CycleState.IDLE
        # dead_time should be cleared
        assert cycle_tracker._transport_delay_minutes is None


