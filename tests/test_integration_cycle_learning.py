"""Integration tests for cycle learning flow.

This module tests the complete cycle learning flow, simulating realistic
heating scenarios and verifying that cycles are properly tracked and recorded.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.adaptive_thermostat.managers.cycle_tracker import (
    CycleState,
    CycleTrackerManager,
)
from custom_components.adaptive_thermostat.managers.events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    SettlingStartedEvent,
    SetpointChangedEvent,
    ContactPauseEvent,
)
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro)

    # Mock async_call_later to return a cancel handle
    def mock_call_later(delay, callback):
        # Return a callable that acts as cancel handle
        return MagicMock()

    hass.async_call_later = MagicMock(side_effect=mock_call_later)
    return hass


@pytest.fixture
def dispatcher():
    """Create a cycle event dispatcher."""
    return CycleEventDispatcher()


@pytest.fixture
def mock_adaptive_learner():
    """Create a mock adaptive learner."""
    learner = MagicMock()
    learner.add_cycle_metrics = MagicMock()
    learner.update_convergence_tracking = MagicMock()
    learner.get_cycle_count = MagicMock(return_value=0)
    return learner


@pytest.fixture
def mock_callbacks():
    """Create mock getter callbacks."""
    return {
        "get_target_temp": MagicMock(return_value=21.0),
        "get_current_temp": MagicMock(return_value=19.0),
        "get_hvac_mode": MagicMock(return_value="heat"),
        "get_in_grace_period": MagicMock(return_value=False),
    }


@pytest.fixture
def cycle_tracker(mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
    """Create a CycleTrackerManager instance with mocks."""
    tracker = CycleTrackerManager(
        hass=mock_hass,
        zone_id="bedroom",
        adaptive_learner=mock_adaptive_learner,
        get_target_temp=mock_callbacks["get_target_temp"],
        get_current_temp=mock_callbacks["get_current_temp"],
        get_hvac_mode=mock_callbacks["get_hvac_mode"],
        get_in_grace_period=mock_callbacks["get_in_grace_period"],
        dispatcher=dispatcher,
    )
    # Mark restoration complete for testing
    tracker.set_restoration_complete()
    return tracker


class TestCompleteHeatingCycle:
    """Test complete heating cycle from start to finish."""

    @pytest.mark.asyncio
    async def test_complete_heating_cycle(
        self, cycle_tracker, mock_adaptive_learner, dispatcher
    ):
        """Test complete heating cycle with realistic temperature progression."""
        # Start heating at 19.0°C, target 21.0°C
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        assert cycle_tracker.state == CycleState.HEATING

        # Simulate temperature rising over 30 minutes (every 30 seconds)
        current_time = start_time
        temperatures = [
            19.0, 19.1, 19.3, 19.5, 19.7, 20.0, 20.3, 20.6, 20.9, 21.1,  # First 5 minutes
            21.2, 21.2, 21.3, 21.2, 21.2, 21.1, 21.1, 21.0, 21.0, 21.0,  # Next 5 minutes
            21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0,  # Next 5 minutes
            21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0,  # Next 5 minutes
            21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0,  # Next 5 minutes
            21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0,  # Final 5 minutes
        ]

        for temp in temperatures:
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Stop heating after 30 minutes
        stop_time = start_time + timedelta(minutes=30)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=stop_time))

        assert cycle_tracker.state == CycleState.SETTLING

        # Continue collecting temperature during settling (10 more samples)
        settling_temps = [21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0]
        for temp in settling_temps:
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Settling should complete (stable temperature)
        assert cycle_tracker.state == CycleState.IDLE

        # Verify metrics were recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1
        assert mock_adaptive_learner.update_convergence_tracking.call_count == 1

        # Verify metrics object
        recorded_metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert isinstance(recorded_metrics, CycleMetrics)
        assert recorded_metrics.overshoot is not None
        assert recorded_metrics.rise_time is not None


class TestMultipleCyclesInSequence:
    """Test multiple heating cycles recorded in sequence."""

    @pytest.mark.asyncio
    async def test_multiple_cycles_in_sequence(
        self, cycle_tracker, mock_adaptive_learner, dispatcher
    ):
        """Test that 3 complete cycles are all recorded."""
        for cycle_num in range(3):
            # Start heating
            start_time = datetime(2024, 1, 1, 10 + cycle_num * 2, 0, 0)
            dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

            # Collect temperature samples (20 samples = 10 minutes)
            current_time = start_time
            for i in range(20):
                temp = 19.0 + min(i * 0.15, 2.0)  # Rise to 21.0°C
                await cycle_tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)

            # Stop heating
            stop_time = current_time
            dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=stop_time))

            # Add settling samples (10 samples at stable temp)
            for _ in range(10):
                await cycle_tracker.update_temperature(current_time, 21.0)
                current_time += timedelta(seconds=30)

            # Should be idle after settling
            assert cycle_tracker.state == CycleState.IDLE

        # Verify all 3 cycles were recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 3
        assert mock_adaptive_learner.update_convergence_tracking.call_count == 3


class TestCycleAbortedBySetpointChange:
    """Test cycle abortion due to setpoint changes."""

    @pytest.mark.asyncio
    async def test_cycle_aborted_by_setpoint_change(
        self, cycle_tracker, mock_adaptive_learner, dispatcher
    ):
        """Test that setpoint change mid-cycle aborts and doesn't record."""
        # Start heating
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        # Collect some temperature samples
        current_time = start_time
        for i in range(10):
            temp = 19.0 + i * 0.2
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change setpoint mid-cycle
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=21.0, new_target=22.0))

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0

        # No metrics should be recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0


class TestCycleAbortedByContactSensor:
    """Test cycle abortion due to contact sensor interruption."""

    @pytest.mark.asyncio
    async def test_cycle_aborted_by_contact_sensor(
        self, cycle_tracker, mock_adaptive_learner, dispatcher
    ):
        """Test that contact sensor pause aborts cycle and doesn't record."""
        # Start heating
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        # Collect some temperature samples
        current_time = start_time
        for i in range(10):
            temp = 19.0 + i * 0.2
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Contact sensor triggers pause (window opened)
        dispatcher.emit(ContactPauseEvent(hvac_mode="heat", timestamp=datetime.now(), entity_id="binary_sensor.window"))

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0

        # No metrics should be recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0


class TestCycleDuringVacationMode:
    """Test cycle recording blocked during vacation mode."""

    @pytest.mark.asyncio
    async def test_cycle_during_vacation_mode(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that cycles during vacation mode are not recorded."""
        # Set grace period to True (simulates vacation mode blocking)
        mock_callbacks["get_in_grace_period"].return_value = True

        # Create tracker with vacation mode active
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="bedroom",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Complete a full cycle
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        # Collect temperature samples (20 samples = 10 minutes)
        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Stop heating
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

        # Add settling samples
        for _ in range(10):
            await tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Cycle should complete but not be recorded (validation failed)
        assert tracker.state == CycleState.IDLE
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0


class TestPWMModeCycleTracking:
    """Test cycle tracking with PWM mode (on/off cycling)."""

    @pytest.mark.asyncio
    async def test_pwm_mode_cycle_tracking(
        self, cycle_tracker, mock_adaptive_learner, dispatcher
    ):
        """Test that PWM on/off cycling is tracked correctly."""
        # Simulate PWM cycle: heater turns on and off multiple times
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        current_time = start_time

        # First PWM on cycle
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=current_time, target_temp=21.0, current_temp=19.0))

        # Collect temps during first on period (10 minutes = 20 samples)
        for i in range(20):
            temp = 19.0 + min(i * 0.1, 2.0)
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # PWM turns off (heating stops)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

        # Continue collecting during settling with stable temperature (10 samples)
        for _ in range(10):
            await cycle_tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Verify cycle completes
        assert cycle_tracker.state == CycleState.IDLE
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1


class TestValveModeCycleTracking:
    """Test cycle tracking with valve mode (0-100% positioning)."""

    @pytest.mark.asyncio
    async def test_valve_mode_cycle_tracking(
        self, cycle_tracker, mock_adaptive_learner, dispatcher
    ):
        """Test that valve transitions (0 to >0) are tracked as cycles."""
        # Valve opens to 50% (tracked as heating started)
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        assert cycle_tracker.state == CycleState.HEATING

        # Collect temperature samples
        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Valve closes to 0% (tracked as heating stopped)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

        assert cycle_tracker.state == CycleState.SETTLING

        # Add settling samples
        for _ in range(10):
            await cycle_tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Verify cycle recorded
        assert cycle_tracker.state == CycleState.IDLE
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1


class TestCycleResumedAfterSetpointChange:
    """Test complete cycle with setpoint change mid-cycle."""

    @pytest.mark.asyncio
    async def test_cycle_tracked_despite_setpoint_change(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that cycle completes when setpoint changes while heater is active."""
        # Create mock for get_is_device_active returning True
        mock_is_device_active = MagicMock(return_value=True)

        # Create tracker with get_is_device_active callback
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="bedroom",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=mock_is_device_active,
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        assert tracker.state == CycleState.HEATING

        # Collect 5 temperature samples
        current_time = start_time
        for i in range(5):
            temp = 19.0 + i * 0.2
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change setpoint mid-cycle (heater still active)
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=21.0, new_target=22.0))

        # Assert state is still HEATING and cycle was interrupted (interruption_history not empty)
        assert tracker.state == CycleState.HEATING
        assert len(tracker._interruption_history) > 0

        # Collect 15 more temperature samples
        for i in range(15):
            temp = 20.0 + min(i * 0.1, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change get_is_device_active mock to return False
        mock_is_device_active.return_value = False

        # Stop heating
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

        # Assert state transitions to SETTLING
        assert tracker.state == CycleState.SETTLING

        # Add 10 settling samples at stable temperature
        for _ in range(10):
            await tracker.update_temperature(current_time, 22.0)
            current_time += timedelta(seconds=30)

        # Assert state becomes IDLE and add_cycle_metrics was called
        assert tracker.state == CycleState.IDLE
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1


class TestSetpointChangeInCoolingMode:
    """Test setpoint change behavior in cooling mode."""

    @pytest.mark.asyncio
    async def test_setpoint_change_in_cooling_mode(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that setpoint change while cooler is active continues tracking."""
        # Set HVAC mode to 'cool'
        mock_callbacks["get_hvac_mode"].return_value = "cool"
        mock_callbacks["get_target_temp"].return_value = 24.0
        mock_callbacks["get_current_temp"].return_value = 26.0

        # Create mock for get_is_device_active returning True (cooler active)
        mock_is_device_active = MagicMock(return_value=True)

        # Create tracker with cooling mode and get_is_device_active callback
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="bedroom",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=mock_is_device_active,
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start cooling cycle
        start_time = datetime(2024, 7, 15, 14, 0, 0)  # Summer afternoon
        dispatcher.emit(CycleStartedEvent(hvac_mode="cool", timestamp=start_time, target_temp=20.0, current_temp=22.0))

        assert tracker.state == CycleState.COOLING

        # Collect 5 temperature samples (cooling down from 26°C)
        current_time = start_time
        for i in range(5):
            temp = 26.0 - i * 0.2  # Temperature dropping
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change setpoint mid-cycle while cooler is active (e.g., user wants it colder)
        dispatcher.emit(SetpointChangedEvent(hvac_mode="heat", timestamp=datetime.now(), old_target=24.0, new_target=23.0))

        # Assert state is still COOLING (same behavior as heating)
        assert tracker.state == CycleState.COOLING
        assert len(tracker._interruption_history) > 0
        assert tracker._cycle_target_temp == 23.0

        # Verify temperature history is preserved
        assert len(tracker.temperature_history) == 5

        # Collect 15 more temperature samples
        for i in range(15):
            temp = 25.0 - min(i * 0.1, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change get_is_device_active mock to return False
        mock_is_device_active.return_value = False

        # Stop cooling
        dispatcher.emit(SettlingStartedEvent(hvac_mode="cool", timestamp=current_time))

        # Assert state transitions to SETTLING
        assert tracker.state == CycleState.SETTLING

        # Add 10 settling samples at stable temperature
        for _ in range(10):
            await tracker.update_temperature(current_time, 23.0)
            current_time += timedelta(seconds=30)

        # Assert state becomes IDLE and add_cycle_metrics was called
        assert tracker.state == CycleState.IDLE
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1


# Marker test for module existence
def test_integration_cycle_learning_module_exists(dispatcher):
    """Marker test to verify module can be imported."""
    from custom_components.adaptive_thermostat.managers.cycle_tracker import (
        CycleTrackerManager,
        CycleState,
    )
    assert CycleTrackerManager is not None
    assert CycleState is not None


class TestPWMSessionTracking:
    """Test PWM session tracking end-to-end."""

    @pytest.mark.asyncio
    async def test_pwm_cycle_completes_without_settling_interruption(
        self, cycle_tracker, mock_adaptive_learner, dispatcher
    ):
        """Test multiple PWM pulses produce single HEATING→SETTLING transition.

        Simulates PWM mode where heater turns on/off multiple times
        while control_output stays >0. Verifies only ONE cycle is tracked
        (session-level, not pulse-level).
        """
        # Start heating session at 19.0°C, target 21.0°C
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        current_time = start_time

        # Session starts: control_output 0→50
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))
        assert cycle_tracker.state == CycleState.HEATING

        # Simulate 30 minutes of PWM cycling
        # Each "pulse" represents a PWM on/off cycle, but session stays active
        # because control_output remains >0 throughout
        temperatures = [
            # First 10 minutes: temperature rising (20 samples)
            19.0, 19.1, 19.2, 19.4, 19.6, 19.8, 20.0, 20.2, 20.5, 20.7,
            20.9, 21.0, 21.1, 21.2, 21.2, 21.3, 21.3, 21.2, 21.2, 21.1,
            # Next 10 minutes: temperature stabilizing near setpoint
            21.1, 21.0, 21.0, 21.1, 21.0, 21.0, 21.1, 21.0, 21.0, 21.0,
            # Final 10 minutes: stable at setpoint
            21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0,
            21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0,
        ]

        # Collect temperature samples during PWM cycling
        # NOTE: In PWM mode, the HeaterController turns the heater on/off
        # internally via async_turn_on/async_turn_off, but these do NOT
        # call on_heating_started/on_heating_session_ended because the session
        # (control_output >0) remains active. Only async_set_control_value
        # detects session boundaries.
        for temp in temperatures:
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Verify still in HEATING state (no false transitions)
        assert cycle_tracker.state == CycleState.HEATING

        # Session ends: control_output 50→0
        stop_time = start_time + timedelta(minutes=30)
        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=stop_time))

        # Verify transition to SETTLING (first and only time)
        assert cycle_tracker.state == CycleState.SETTLING

        # Add settling samples (10 samples = 5 minutes)
        settling_temps = [21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0, 21.0]
        for temp in settling_temps:
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Verify settling completes (stable temperature)
        assert cycle_tracker.state == CycleState.IDLE

        # Verify metrics were recorded exactly ONCE (session-level cycle, not pulse-level)
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1
        assert mock_adaptive_learner.update_convergence_tracking.call_count == 1

        # Verify metrics object has valid data
        recorded_metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert isinstance(recorded_metrics, CycleMetrics)
        assert recorded_metrics.overshoot is not None
        assert recorded_metrics.rise_time is not None


class TestPersistenceRoundtrip:
    """Integration tests for persistence round-trip."""

    def test_adaptive_learner_persistence_roundtrip(self):
        """Test AdaptiveLearner data persists and restores correctly.

        Creates a learner, adds cycles, saves to dict, creates new learner,
        restores from dict, and verifies all cycles and state match.
        """
        from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics

        # Create learner and add cycles
        original_learner = AdaptiveLearner(heating_type="floor_hydronic")

        # Add 5 cycles with varying metrics
        cycles_data = [
            {"overshoot": 0.3, "undershoot": 0.1, "settling_time": 45.0, "oscillations": 1, "rise_time": 30.0},
            {"overshoot": 0.25, "undershoot": 0.15, "settling_time": 42.0, "oscillations": 0, "rise_time": 28.0},
            {"overshoot": 0.2, "undershoot": 0.1, "settling_time": 40.0, "oscillations": 0, "rise_time": 25.0},
            {"overshoot": 0.15, "undershoot": 0.05, "settling_time": 38.0, "oscillations": 0, "rise_time": 22.0},
            {"overshoot": 0.1, "undershoot": 0.0, "settling_time": 35.0, "oscillations": 0, "rise_time": 20.0},
        ]

        for cycle_data in cycles_data:
            metrics = CycleMetrics(**cycle_data)
            original_learner.add_cycle_metrics(metrics)
            original_learner.update_convergence_tracking(metrics)

        # Set some convergence state
        original_learner._consecutive_converged_cycles = 3
        original_learner._pid_converged_for_ke = True
        original_learner._auto_apply_count = 2

        # Serialize to dict
        serialized = original_learner.to_dict()

        # Verify serialization contains expected data
        assert "cycle_history" in serialized
        assert len(serialized["cycle_history"]) == 5
        assert serialized["consecutive_converged_cycles"] == 3
        assert serialized["pid_converged_for_ke"] is True
        assert serialized["auto_apply_count"] == 2

        # Create new learner and restore from dict
        restored_learner = AdaptiveLearner(heating_type="floor_hydronic")
        restored_learner.restore_from_dict(serialized)

        # Verify cycle count matches
        assert restored_learner.get_cycle_count() == original_learner.get_cycle_count()
        assert restored_learner.get_cycle_count() == 5

        # Verify each cycle's metrics match
        original_history = original_learner._cycle_history
        restored_history = restored_learner._cycle_history

        for i, (orig, restored) in enumerate(zip(original_history, restored_history)):
            assert orig.overshoot == restored.overshoot, f"Cycle {i} overshoot mismatch"
            assert orig.undershoot == restored.undershoot, f"Cycle {i} undershoot mismatch"
            assert orig.settling_time == restored.settling_time, f"Cycle {i} settling_time mismatch"
            assert orig.oscillations == restored.oscillations, f"Cycle {i} oscillations mismatch"
            assert orig.rise_time == restored.rise_time, f"Cycle {i} rise_time mismatch"

        # Verify convergence state matches
        assert restored_learner._consecutive_converged_cycles == original_learner._consecutive_converged_cycles
        assert restored_learner._pid_converged_for_ke == original_learner._pid_converged_for_ke
        assert restored_learner._auto_apply_count == original_learner._auto_apply_count

    def test_ke_learner_persistence_roundtrip(self):
        """Test KeLearner observations persist and restore correctly."""
        from custom_components.adaptive_thermostat.adaptive.ke_learning import KeLearner, KeObservation

        # Create learner and add observations
        original_learner = KeLearner(initial_ke=0.5)

        # Enable learning
        original_learner.enable()

        # Add observations using proper KeObservation objects
        original_learner._observations = [
            KeObservation(timestamp=datetime.now(), outdoor_temp=5.0, pid_output=50.0,
                         indoor_temp=21.0, target_temp=21.0),
            KeObservation(timestamp=datetime.now(), outdoor_temp=0.0, pid_output=55.0,
                         indoor_temp=20.5, target_temp=21.0),
            KeObservation(timestamp=datetime.now(), outdoor_temp=-5.0, pid_output=60.0,
                         indoor_temp=20.0, target_temp=21.0),
        ]
        original_learner._current_ke = 0.65

        # Serialize to dict
        serialized = original_learner.to_dict()

        # Verify serialization
        assert "current_ke" in serialized
        assert serialized["current_ke"] == 0.65
        assert "observations" in serialized
        assert len(serialized["observations"]) == 3
        assert serialized["enabled"] is True

        # Create new learner from dict
        restored_learner = KeLearner.from_dict(serialized)

        # Verify Ke value matches
        assert restored_learner.current_ke == original_learner.current_ke

        # Verify observation count matches
        assert restored_learner.observation_count == original_learner.observation_count

        # Verify enabled state matches
        assert restored_learner.enabled == original_learner.enabled

    def test_full_persistence_roundtrip_with_store(self):
        """Test complete persistence flow using LearningDataStore."""
        from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_thermostat.adaptive.persistence import LearningDataStore
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics
        from custom_components.adaptive_thermostat.adaptive.ke_learning import KeLearner, KeObservation
        from unittest.mock import MagicMock

        # Create mock hass for the store
        mock_hass = MagicMock()
        store = LearningDataStore(mock_hass)

        # Create and populate AdaptiveLearner
        adaptive_learner = AdaptiveLearner(heating_type="radiator")
        for i in range(3):
            metrics = CycleMetrics(
                overshoot=0.2 + i * 0.05,
                undershoot=0.1,
                settling_time=40.0 - i * 2,
                oscillations=max(0, 2 - i),
                rise_time=25.0 - i,
            )
            adaptive_learner.add_cycle_metrics(metrics)
        adaptive_learner._consecutive_converged_cycles = 2
        adaptive_learner._pid_converged_for_ke = True

        # Create and populate KeLearner
        ke_learner = KeLearner(initial_ke=0.4)
        ke_learner.enable()
        ke_learner._current_ke = 0.55
        # Add some observations for the count
        ke_learner._observations = [
            KeObservation(timestamp=datetime.now(), outdoor_temp=i * 2.0, pid_output=50.0,
                         indoor_temp=20.0, target_temp=21.0)
            for i in range(5)
        ]

        # Update store with zone data
        zone_id = "living_room"
        store.update_zone_data(
            zone_id=zone_id,
            adaptive_data=adaptive_learner.to_dict(),
            ke_data=ke_learner.to_dict(),
        )

        # Verify data is in store
        zone_data = store.get_zone_data(zone_id)
        assert zone_data is not None
        assert "adaptive_learner" in zone_data
        assert "ke_learner" in zone_data

        # Create new learners and restore from store
        restored_adaptive = AdaptiveLearner(heating_type="radiator")
        restored_adaptive.restore_from_dict(zone_data["adaptive_learner"])

        restored_ke = KeLearner.from_dict(zone_data["ke_learner"])

        # Verify AdaptiveLearner restoration
        assert restored_adaptive.get_cycle_count() == 3
        assert restored_adaptive._consecutive_converged_cycles == 2
        assert restored_adaptive._pid_converged_for_ke is True

        # Verify cycle metrics
        original_cycles = adaptive_learner._cycle_history
        restored_cycles = restored_adaptive._cycle_history
        for i, (orig, restored) in enumerate(zip(original_cycles, restored_cycles)):
            assert abs(orig.overshoot - restored.overshoot) < 0.001, f"Cycle {i} overshoot mismatch"
            assert abs(orig.settling_time - restored.settling_time) < 0.001, f"Cycle {i} settling_time mismatch"

        # Verify KeLearner restoration
        assert restored_ke.current_ke == 0.55
        assert restored_ke.observation_count == 5
        assert restored_ke.enabled is True


class TestEventDrivenCycleFlow:
    """Integration tests for event-driven cycle tracking flow."""

    @pytest.fixture
    def dispatcher(self):
        """Create a CycleEventDispatcher for testing."""
        from custom_components.adaptive_thermostat.managers.events import CycleEventDispatcher
        return CycleEventDispatcher()

    @pytest.fixture
    def event_tracker_with_dispatcher(self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher):
        """Create a CycleTrackerManager with event dispatcher."""
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="living_room",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()
        return tracker

    @pytest.mark.asyncio
    async def test_full_cycle_event_flow(
        self, event_tracker_with_dispatcher, dispatcher, mock_adaptive_learner
    ):
        """Test complete heating cycle with event sequence.

        Verifies: CYCLE_STARTED → HEATING_STARTED → HEATING_ENDED → SETTLING_STARTED → CYCLE_ENDED
        """
        from custom_components.adaptive_thermostat.managers.events import (
            CycleEventType,
            CycleStartedEvent,
            HeatingStartedEvent,
            HeatingEndedEvent,
            SettlingStartedEvent,
        )

        tracker = event_tracker_with_dispatcher

        # Track emitted events
        emitted_events = []

        def track_event(event):
            emitted_events.append(event)

        # Subscribe to CYCLE_ENDED to capture it
        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, track_event)

        # Start heating cycle via event
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_started = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(cycle_started)

        assert tracker.state == CycleState.HEATING

        # Emit HEATING_STARTED (device turns on)
        heating_started = HeatingStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
        )
        dispatcher.emit(heating_started)

        # Simulate temperature rising over time
        current_time = start_time
        temperatures = [19.0 + i * 0.15 for i in range(20)]  # Rise to ~22°C
        for temp in temperatures:
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Emit HEATING_ENDED (device turns off)
        heating_ended = HeatingEndedEvent(
            hvac_mode="heat",
            timestamp=current_time,
        )
        dispatcher.emit(heating_ended)

        # Emit SETTLING_STARTED (demand dropped to 0)
        settling_started = SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=current_time,
        )
        dispatcher.emit(settling_started)

        assert tracker.state == CycleState.SETTLING

        # Add settling samples at stable temperature
        for _ in range(10):
            await tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Cycle should complete and transition to IDLE
        assert tracker.state == CycleState.IDLE

        # Verify metrics were recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1

        # Verify CYCLE_ENDED event was emitted
        assert len(emitted_events) == 1
        assert emitted_events[0].event_type == CycleEventType.CYCLE_ENDED
        assert emitted_events[0].hvac_mode == "heat"
        assert emitted_events[0].metrics is not None

    @pytest.mark.asyncio
    async def test_mode_change_aborts_cycle_via_event(
        self, event_tracker_with_dispatcher, dispatcher, mock_adaptive_learner
    ):
        """Test that MODE_CHANGED event during heating aborts cycle."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleStartedEvent,
            ModeChangedEvent,
        )

        tracker = event_tracker_with_dispatcher

        # Start heating cycle via event
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_started = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(cycle_started)

        assert tracker.state == CycleState.HEATING

        # Collect some temperature samples
        current_time = start_time
        for i in range(10):
            temp = 19.0 + i * 0.2
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Emit MODE_CHANGED event (heat → off)
        mode_changed = ModeChangedEvent(
            timestamp=current_time,
            old_mode="heat",
            new_mode="off",
        )
        dispatcher.emit(mode_changed)

        # Verify cycle was aborted
        assert tracker.state == CycleState.IDLE
        assert len(tracker.temperature_history) == 0

        # No metrics should be recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0

    @pytest.mark.asyncio
    async def test_contact_pause_resume_flow_via_events(
        self, event_tracker_with_dispatcher, dispatcher, mock_adaptive_learner
    ):
        """Test CONTACT_PAUSE and CONTACT_RESUME event handling."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleStartedEvent,
            ContactPauseEvent,
            ContactResumeEvent,
        )

        tracker = event_tracker_with_dispatcher

        # Start heating cycle via event
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_started = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(cycle_started)

        assert tracker.state == CycleState.HEATING

        # Collect some temperature samples
        current_time = start_time
        for i in range(10):
            temp = 19.0 + i * 0.2
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Emit CONTACT_PAUSE event (window opened)
        contact_pause = ContactPauseEvent(
            hvac_mode="heat",
            timestamp=current_time,
            entity_id="binary_sensor.window_living_room",
        )
        dispatcher.emit(contact_pause)

        # Verify cycle was aborted
        assert tracker.state == CycleState.IDLE
        assert len(tracker.temperature_history) == 0

        # Emit CONTACT_RESUME event (window closed)
        # This should be a no-op since cycle was already aborted
        resume_time = current_time + timedelta(minutes=10)
        contact_resume = ContactResumeEvent(
            hvac_mode="heat",
            timestamp=resume_time,
            entity_id="binary_sensor.window_living_room",
            pause_duration_seconds=600.0,
        )
        dispatcher.emit(contact_resume)

        # State should still be IDLE
        assert tracker.state == CycleState.IDLE

        # No metrics should be recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0

    @pytest.mark.asyncio
    async def test_full_cycle_decay_tracking(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test full heating cycle with decay tracking via TemperatureUpdateEvent.

        Simulates a complete heating cycle with PID controller tracking and verifies:
        - integral_at_tolerance_entry captured when pid_error < cold_tolerance
        - integral_at_setpoint_cross captured when pid_error <= 0
        - decay_contribution calculated correctly (entry - cross)
        - CycleMetrics has all decay fields populated
        """
        from custom_components.adaptive_thermostat.managers.events import (
            CycleStartedEvent,
            SettlingStartedEvent,
            TemperatureUpdateEvent,
        )

        # Create tracker with radiator heating type (cold_tolerance=0.3°C)
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            dispatcher=dispatcher,
            heating_type="radiator",  # cold_tolerance=0.3°C
        )
        tracker.set_restoration_complete()

        # Start heating cycle at 19.0°C, target 21.0°C
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_started = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(cycle_started)

        assert tracker.state == CycleState.HEATING

        # Simulate temperature rising with PID integral building up
        # For radiator heating type, cold_tolerance = 0.3°C
        current_time = start_time
        current_temp = 19.0
        pid_integral = 0.0

        # Phase 1: Temperature rises from 19.0 to 20.7 (just before tolerance entry)
        # PID integral builds up as temperature chases setpoint
        for i in range(10):
            current_temp = 19.0 + i * 0.17  # 19.0, 19.17, 19.34, ..., 20.53
            pid_error = 21.0 - current_temp  # Starts at 2.0, decreases
            pid_integral += pid_error * 0.1  # Accumulate integral term

            # Update temperature for cycle tracking
            await tracker.update_temperature(current_time, current_temp)

            # Emit TemperatureUpdateEvent for integral tracking
            temp_event = TemperatureUpdateEvent(
                timestamp=current_time,
                temperature=current_temp,
                setpoint=21.0,
                pid_integral=pid_integral,
                pid_error=pid_error,
            )
            dispatcher.emit(temp_event)

            current_time += timedelta(seconds=30)

        # Phase 2: Temperature enters cold tolerance zone (20.7 to 21.0)
        # This is where integral_at_tolerance_entry should be captured
        # cold_tolerance = 0.3, so tolerance entry occurs when pid_error < 0.3
        integral_at_entry = None
        for i in range(3):
            current_temp = 20.7 + i * 0.1  # 20.7, 20.8, 20.9
            pid_error = 21.0 - current_temp  # 0.3, 0.2, 0.1 (< 0.3 triggers capture)
            pid_integral += pid_error * 0.1

            # Capture integral at first entry into tolerance zone
            if integral_at_entry is None and pid_error < 0.3:
                integral_at_entry = pid_integral

            await tracker.update_temperature(current_time, current_temp)

            temp_event = TemperatureUpdateEvent(
                timestamp=current_time,
                temperature=current_temp,
                setpoint=21.0,
                pid_integral=pid_integral,
                pid_error=pid_error,
            )
            dispatcher.emit(temp_event)

            current_time += timedelta(seconds=30)

        # Phase 3: Temperature crosses setpoint (21.0 to 21.2)
        # This is where integral_at_setpoint_cross should be captured
        integral_at_cross = None
        for i in range(3):
            current_temp = 21.0 + i * 0.1  # 21.0, 21.1, 21.2
            pid_error = 21.0 - current_temp  # 0.0, -0.1, -0.2 (<= 0 triggers capture)
            pid_integral += pid_error * 0.1

            # Capture integral at setpoint crossing
            if integral_at_cross is None and pid_error <= 0.0:
                integral_at_cross = pid_integral

            await tracker.update_temperature(current_time, current_temp)

            temp_event = TemperatureUpdateEvent(
                timestamp=current_time,
                temperature=current_temp,
                setpoint=21.0,
                pid_integral=pid_integral,
                pid_error=pid_error,
            )
            dispatcher.emit(temp_event)

            current_time += timedelta(seconds=30)

        # Phase 4: Temperature overshoots slightly and settles
        for i in range(10):
            current_temp = 21.2 - i * 0.02  # Gradually settle to 21.0
            pid_error = 21.0 - current_temp
            pid_integral += pid_error * 0.1

            await tracker.update_temperature(current_time, current_temp)

            temp_event = TemperatureUpdateEvent(
                timestamp=current_time,
                temperature=current_temp,
                setpoint=21.0,
                pid_integral=pid_integral,
                pid_error=pid_error,
            )
            dispatcher.emit(temp_event)

            current_time += timedelta(seconds=30)

        # Stop heating and enter settling phase
        settling_started = SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=current_time,
        )
        dispatcher.emit(settling_started)

        assert tracker.state == CycleState.SETTLING

        # Add settling samples at stable temperature
        for _ in range(10):
            await tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Cycle should complete and transition to IDLE
        assert tracker.state == CycleState.IDLE

        # Verify metrics were recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1

        # Verify CycleMetrics has decay fields populated
        recorded_metrics = mock_adaptive_learner.add_cycle_metrics.call_args[0][0]
        assert isinstance(recorded_metrics, CycleMetrics)

        # Verify integral values were captured
        assert recorded_metrics.integral_at_tolerance_entry is not None
        assert recorded_metrics.integral_at_setpoint_cross is not None
        assert recorded_metrics.decay_contribution is not None

        # Calculate expected decay contribution
        expected_decay = integral_at_entry - integral_at_cross

        # Verify integral values match what we captured
        assert abs(recorded_metrics.integral_at_tolerance_entry - integral_at_entry) < 0.01
        assert abs(recorded_metrics.integral_at_setpoint_cross - integral_at_cross) < 0.01

        # Verify decay_contribution is calculated correctly (entry - cross)
        # Note: decay_contribution may be negative if integral continues accumulating
        # during the approach to setpoint (which happens with I-only control or
        # when P term isn't strong enough to reduce error quickly)
        assert abs(recorded_metrics.decay_contribution - expected_decay) < 0.01

    @pytest.mark.asyncio
    async def test_setpoint_change_during_cycle_via_event(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test SETPOINT_CHANGED event during cycle handled via event."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleStartedEvent,
            SetpointChangedEvent,
            SettlingStartedEvent,
        )

        # Create mock for get_is_device_active returning True (heater active)
        mock_is_device_active = MagicMock(return_value=True)

        # Create tracker with get_is_device_active callback and dispatcher
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="bedroom",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=mock_is_device_active,
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating cycle via event
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_started = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(cycle_started)

        assert tracker.state == CycleState.HEATING

        # Collect 5 temperature samples
        current_time = start_time
        for i in range(5):
            temp = 19.0 + i * 0.2
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Emit SETPOINT_CHANGED event (heater still active = minor change)
        setpoint_changed = SetpointChangedEvent(
            hvac_mode="heat",
            timestamp=current_time,
            old_target=21.0,
            new_target=22.0,
        )
        dispatcher.emit(setpoint_changed)

        # Cycle should continue (device is active, so minor change)
        assert tracker.state == CycleState.HEATING
        assert len(tracker._interruption_history) > 0  # Interruption recorded
        assert tracker._cycle_target_temp == 22.0  # Target updated

        # Collect 15 more temperature samples
        for i in range(15):
            temp = 20.0 + min(i * 0.1, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change mock to device inactive for settling
        mock_is_device_active.return_value = False

        # Emit SETTLING_STARTED
        settling_started = SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=current_time,
        )
        dispatcher.emit(settling_started)

        assert tracker.state == CycleState.SETTLING

        # Add 10 settling samples
        for _ in range(10):
            await tracker.update_temperature(current_time, 22.0)
            current_time += timedelta(seconds=30)

        # Cycle should complete
        assert tracker.state == CycleState.IDLE
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 1

    @pytest.mark.asyncio
    async def test_setpoint_major_change_aborts_via_event(
        self, mock_hass, mock_adaptive_learner, mock_callbacks, dispatcher
    ):
        """Test that major SETPOINT_CHANGED event (device inactive) aborts cycle."""
        from custom_components.adaptive_thermostat.managers.events import (
            CycleStartedEvent,
            SetpointChangedEvent,
        )

        # Create mock for get_is_device_active returning False (heater inactive)
        mock_is_device_active = MagicMock(return_value=False)

        # Create tracker with get_is_device_active callback and dispatcher
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="bedroom",
            adaptive_learner=mock_adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            get_is_device_active=mock_is_device_active,
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Start heating cycle via event
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_started = CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=21.0,
            current_temp=19.0,
        )
        dispatcher.emit(cycle_started)

        assert tracker.state == CycleState.HEATING

        # Collect some temperature samples
        current_time = start_time
        for i in range(10):
            temp = 19.0 + i * 0.2
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Emit SETPOINT_CHANGED event with >0.5°C change and device inactive
        setpoint_changed = SetpointChangedEvent(
            hvac_mode="heat",
            timestamp=current_time,
            old_target=21.0,
            new_target=23.0,  # 2°C change = major
        )
        dispatcher.emit(setpoint_changed)

        # Cycle should be aborted (device inactive + major setpoint change)
        assert tracker.state == CycleState.IDLE
        assert len(tracker.temperature_history) == 0

        # No metrics should be recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0


class TestDecayAwareKiAdjustment:
    """Integration test for decay-aware Ki adjustment (Story 8.2)."""

    def test_decay_aware_ki_adjustment(self):
        """Test that decay-aware learning adjusts Ki correctly based on decay_ratio.

        Tests the UNDERSHOOT rule's decay-aware Ki scaling:
        - decay_ratio = 0.0 (no decay) -> full Ki increase
        - decay_ratio = 1.0 (all decay) -> no Ki increase
        - decay_ratio = 0.5 (partial decay) -> 50% Ki increase
        """
        from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics

        # Create learner with radiator heating type
        # Radiator thresholds: undershoot=0.375°C, overshoot_max=0.25, min_cycles=6
        learner = AdaptiveLearner(heating_type="radiator")

        # Test case 1: decay_ratio = 0.0 (no decay contribution)
        # Expected: Full Ki increase (up to 100% for avg_undershoot=0.5°C)
        # Note: Must exceed convergence thresholds and have 6 cycles minimum
        # Design metrics to ONLY trigger UNDERSHOOT rule:
        # - overshoot < 0.25 (below moderate_overshoot threshold)
        # - settling_time < 45 (below slow_settling threshold)
        # - rise_time > 60 (above convergence threshold to prevent skip, but use 70 to avoid SLOW_RESPONSE at 80)
        # - oscillations < 1 (below some_oscillations threshold)
        # - undershoot > 0.375 (above undershoot threshold)
        learner.clear_history()
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.2,  # Below moderate_overshoot threshold (0.25)
                undershoot=0.5,  # Triggers UNDERSHOOT rule (>0.375°C)
                settling_time=40.0,  # Below slow_settling threshold (45 min)
                oscillations=0,
                rise_time=70.0,  # Above convergence (60) but below SLOW_RESPONSE (80)
                integral_at_tolerance_entry=100.0,  # Entry integral
                integral_at_setpoint_cross=100.0,   # Cross integral (no decay)
                decay_contribution=0.0,  # decay_ratio = 0.0 / 100.0 = 0.0
            )
            learner.add_cycle_metrics(metrics)

        # Calculate expected Ki adjustment for decay_ratio = 0.0
        # increase = min(1.0, avg_undershoot * 2.0) = min(1.0, 0.5 * 2.0) = 1.0
        # scaled_increase = increase * (1.0 - decay_ratio) = 1.0 * (1.0 - 0.0) = 1.0
        # ki_factor = 1.0 + scaled_increase = 2.0 (base factor before learning rate)
        # Learning rate multiplier = 2.0x (confidence=0.0)
        # Scaled ki_factor = 1.0 + (2.0 - 1.0) * 2.0 = 3.0
        # Final Ki = 0.001 * 3.0 = 0.003
        adjustment = learner.calculate_pid_adjustment(
            current_kp=5.0, current_ki=0.001, current_kd=300.0
        )

        assert adjustment is not None, "Expected adjustment for decay_ratio=0.0"
        expected_ki = 0.001 * 3.0  # Full Ki increase with 2.0x learning rate
        assert abs(adjustment["ki"] - expected_ki) < 0.0001, (
            f"decay_ratio=0.0: Expected Ki={expected_ki:.4f}, got {adjustment['ki']:.4f}"
        )

        # Test case 2: decay_ratio = 1.0 (all integral from decay)
        # Expected: No Ki increase (scaled_increase = 0)
        learner.clear_history()
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.2,  # Below moderate_overshoot threshold
                undershoot=0.5,  # Triggers UNDERSHOOT rule
                settling_time=40.0,  # Below slow_settling threshold
                oscillations=0,
                rise_time=70.0,  # Above convergence (60) but below SLOW_RESPONSE (80)
                integral_at_tolerance_entry=100.0,  # Entry integral
                integral_at_setpoint_cross=0.0,     # Cross integral (full decay)
                decay_contribution=100.0,  # decay_ratio = 100.0 / 100.0 = 1.0
            )
            learner.add_cycle_metrics(metrics)

        # Calculate expected Ki adjustment for decay_ratio = 1.0
        # increase = min(1.0, avg_undershoot * 2.0) = 1.0
        # scaled_increase = increase * (1.0 - decay_ratio) = 1.0 * (1.0 - 1.0) = 0.0
        # ki_factor = 1.0 + scaled_increase = 1.0 (base factor, no increase)
        # Learning rate multiplier applied: 1.0 + (1.0 - 1.0) * 2.0 = 1.0
        # Final Ki = 0.001 * 1.0 = 0.001 (no change)
        adjustment = learner.calculate_pid_adjustment(
            current_kp=5.0, current_ki=0.001, current_kd=300.0
        )

        assert adjustment is not None, "Expected adjustment for decay_ratio=1.0"
        expected_ki = 0.001 * 1.0  # No Ki increase
        assert abs(adjustment["ki"] - expected_ki) < 0.0001, (
            f"decay_ratio=1.0: Expected Ki={expected_ki:.4f}, got {adjustment['ki']:.4f}"
        )

        # Test case 3: decay_ratio = 0.5 (partial decay)
        # Expected: 50% of full Ki increase
        learner.clear_history()
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.2,  # Below moderate_overshoot threshold
                undershoot=0.5,  # Triggers UNDERSHOOT rule
                settling_time=40.0,  # Below slow_settling threshold
                oscillations=0,
                rise_time=70.0,  # Above convergence (60) but below SLOW_RESPONSE (80)
                integral_at_tolerance_entry=100.0,  # Entry integral
                integral_at_setpoint_cross=50.0,    # Cross integral (partial decay)
                decay_contribution=50.0,  # decay_ratio = 50.0 / 100.0 = 0.5
            )
            learner.add_cycle_metrics(metrics)

        # Calculate expected Ki adjustment for decay_ratio = 0.5
        # increase = min(1.0, avg_undershoot * 2.0) = 1.0
        # scaled_increase = increase * (1.0 - decay_ratio) = 1.0 * (1.0 - 0.5) = 0.5
        # ki_factor = 1.0 + scaled_increase = 1.5 (base factor, 50% increase)
        # Learning rate multiplier applied: 1.0 + (1.5 - 1.0) * 2.0 = 2.0
        # Final Ki = 0.001 * 2.0 = 0.002
        adjustment = learner.calculate_pid_adjustment(
            current_kp=5.0, current_ki=0.001, current_kd=300.0
        )

        assert adjustment is not None, "Expected adjustment for decay_ratio=0.5"
        expected_ki = 0.001 * 2.0  # 50% base increase * 2.0x learning rate
        assert abs(adjustment["ki"] - expected_ki) < 0.0001, (
            f"decay_ratio=0.5: Expected Ki={expected_ki:.4f}, got {adjustment['ki']:.4f}"
        )

        # Test case 4: decay_ratio = 0.2 (low decay, 80% Ki increase expected)
        learner.clear_history()
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.2,  # Below moderate_overshoot threshold
                undershoot=0.5,
                settling_time=40.0,  # Below slow_settling threshold
                oscillations=0,
                rise_time=70.0,  # Above convergence (60) but below SLOW_RESPONSE (80)
                integral_at_tolerance_entry=100.0,
                integral_at_setpoint_cross=80.0,
                decay_contribution=20.0,  # decay_ratio = 20.0 / 100.0 = 0.2
            )
            learner.add_cycle_metrics(metrics)

        # Calculate expected Ki adjustment for decay_ratio = 0.2
        # increase = min(1.0, avg_undershoot * 2.0) = 1.0
        # scaled_increase = increase * (1.0 - decay_ratio) = 1.0 * (1.0 - 0.2) = 0.8
        # ki_factor = 1.0 + scaled_increase = 1.8 (base factor, 80% increase)
        # Learning rate multiplier applied: 1.0 + (1.8 - 1.0) * 2.0 = 2.6
        # Final Ki = 0.001 * 2.6 = 0.0026
        adjustment = learner.calculate_pid_adjustment(
            current_kp=5.0, current_ki=0.001, current_kd=300.0
        )

        assert adjustment is not None, "Expected adjustment for decay_ratio=0.2"
        expected_ki = 0.001 * 2.6  # 80% base increase * 2.0x learning rate
        assert abs(adjustment["ki"] - expected_ki) < 0.0001, (
            f"decay_ratio=0.2: Expected Ki={expected_ki:.4f}, got {adjustment['ki']:.4f}"
        )

        # Test case 5: decay_ratio = 0.8 (high decay, 20% Ki increase expected)
        learner.clear_history()
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.2,  # Below moderate_overshoot threshold
                undershoot=0.5,
                settling_time=40.0,  # Below slow_settling threshold
                oscillations=0,
                rise_time=70.0,  # Above convergence (60) but below SLOW_RESPONSE (80)
                integral_at_tolerance_entry=100.0,
                integral_at_setpoint_cross=20.0,
                decay_contribution=80.0,  # decay_ratio = 80.0 / 100.0 = 0.8
            )
            learner.add_cycle_metrics(metrics)

        # Calculate expected Ki adjustment for decay_ratio = 0.8
        # increase = min(1.0, avg_undershoot * 2.0) = 1.0
        # scaled_increase = increase * (1.0 - decay_ratio) = 1.0 * (1.0 - 0.8) = 0.2
        # ki_factor = 1.0 + scaled_increase = 1.2 (base factor, 20% increase)
        # Learning rate multiplier applied: 1.0 + (1.2 - 1.0) * 2.0 = 1.4
        # Final Ki = 0.001 * 1.4 = 0.0014
        adjustment = learner.calculate_pid_adjustment(
            current_kp=5.0, current_ki=0.001, current_kd=300.0
        )

        assert adjustment is not None, "Expected adjustment for decay_ratio=0.8"
        expected_ki = 0.001 * 1.4  # 20% base increase * 2.0x learning rate
        assert abs(adjustment["ki"] - expected_ki) < 0.0001, (
            f"decay_ratio=0.8: Expected Ki={expected_ki:.4f}, got {adjustment['ki']:.4f}"
        )

        # Test case 6: Verify no adjustment when decay metrics are missing (backward compatibility)
        learner.clear_history()
        for _ in range(6):
            metrics = CycleMetrics(
                overshoot=0.2,  # Below moderate_overshoot threshold
                undershoot=0.5,  # Triggers UNDERSHOOT rule
                settling_time=40.0,  # Below slow_settling threshold
                oscillations=0,
                rise_time=70.0,  # Above convergence (60) but below SLOW_RESPONSE (80)
                integral_at_tolerance_entry=None,  # No decay data
                integral_at_setpoint_cross=None,
                decay_contribution=None,
            )
            learner.add_cycle_metrics(metrics)

        # Without decay metrics, should get full Ki increase (decay_ratio=0.0 default)
        # Same as test case 1: full increase with 2.0x learning rate = 3.0x
        adjustment = learner.calculate_pid_adjustment(
            current_kp=5.0, current_ki=0.001, current_kd=300.0
        )

        assert adjustment is not None, "Expected adjustment when decay metrics missing"
        expected_ki = 0.001 * 3.0  # Full 100% base increase * 2.0x learning rate (backward compatible)
        assert abs(adjustment["ki"] - expected_ki) < 0.0001, (
            f"No decay metrics: Expected Ki={expected_ki:.4f}, got {adjustment['ki']:.4f}"
        )
