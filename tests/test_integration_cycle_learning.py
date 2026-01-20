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
def cycle_tracker(mock_hass, mock_adaptive_learner, mock_callbacks):
    """Create a CycleTrackerManager instance with mocks."""
    tracker = CycleTrackerManager(
        hass=mock_hass,
        zone_id="bedroom",
        adaptive_learner=mock_adaptive_learner,
        get_target_temp=mock_callbacks["get_target_temp"],
        get_current_temp=mock_callbacks["get_current_temp"],
        get_hvac_mode=mock_callbacks["get_hvac_mode"],
        get_in_grace_period=mock_callbacks["get_in_grace_period"],
    )
    # Mark restoration complete for testing
    tracker.set_restoration_complete()
    return tracker


class TestCompleteHeatingCycle:
    """Test complete heating cycle from start to finish."""

    @pytest.mark.asyncio
    async def test_complete_heating_cycle(
        self, cycle_tracker, mock_adaptive_learner
    ):
        """Test complete heating cycle with realistic temperature progression."""
        # Start heating at 19.0°C, target 21.0°C
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

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
        cycle_tracker.on_heating_session_ended(stop_time)

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
        self, cycle_tracker, mock_adaptive_learner
    ):
        """Test that 3 complete cycles are all recorded."""
        for cycle_num in range(3):
            # Start heating
            start_time = datetime(2024, 1, 1, 10 + cycle_num * 2, 0, 0)
            cycle_tracker.on_heating_started(start_time)

            # Collect temperature samples (20 samples = 10 minutes)
            current_time = start_time
            for i in range(20):
                temp = 19.0 + min(i * 0.15, 2.0)  # Rise to 21.0°C
                await cycle_tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)

            # Stop heating
            stop_time = current_time
            cycle_tracker.on_heating_session_ended(stop_time)

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
        self, cycle_tracker, mock_adaptive_learner
    ):
        """Test that setpoint change mid-cycle aborts and doesn't record."""
        # Start heating
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Collect some temperature samples
        current_time = start_time
        for i in range(10):
            temp = 19.0 + i * 0.2
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change setpoint mid-cycle
        cycle_tracker.on_setpoint_changed(21.0, 22.0)

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0

        # No metrics should be recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0


class TestCycleAbortedByContactSensor:
    """Test cycle abortion due to contact sensor interruption."""

    @pytest.mark.asyncio
    async def test_cycle_aborted_by_contact_sensor(
        self, cycle_tracker, mock_adaptive_learner
    ):
        """Test that contact sensor pause aborts cycle and doesn't record."""
        # Start heating
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        # Collect some temperature samples
        current_time = start_time
        for i in range(10):
            temp = 19.0 + i * 0.2
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Contact sensor triggers pause (window opened)
        cycle_tracker.on_contact_sensor_pause()

        # Verify cycle was aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert len(cycle_tracker.temperature_history) == 0

        # No metrics should be recorded
        assert mock_adaptive_learner.add_cycle_metrics.call_count == 0


class TestCycleDuringVacationMode:
    """Test cycle recording blocked during vacation mode."""

    @pytest.mark.asyncio
    async def test_cycle_during_vacation_mode(
        self, mock_hass, mock_adaptive_learner, mock_callbacks
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
        )
        tracker.set_restoration_complete()

        # Complete a full cycle
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        tracker.on_heating_started(start_time)

        # Collect temperature samples (20 samples = 10 minutes)
        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Stop heating
        tracker.on_heating_session_ended(current_time)

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
        self, cycle_tracker, mock_adaptive_learner
    ):
        """Test that PWM on/off cycling is tracked correctly."""
        # Simulate PWM cycle: heater turns on and off multiple times
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        current_time = start_time

        # First PWM on cycle
        cycle_tracker.on_heating_started(current_time)

        # Collect temps during first on period (10 minutes = 20 samples)
        for i in range(20):
            temp = 19.0 + min(i * 0.1, 2.0)
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # PWM turns off (heating stops)
        cycle_tracker.on_heating_session_ended(current_time)

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
        self, cycle_tracker, mock_adaptive_learner
    ):
        """Test that valve transitions (0 to >0) are tracked as cycles."""
        # Valve opens to 50% (tracked as heating started)
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        cycle_tracker.on_heating_started(start_time)

        assert cycle_tracker.state == CycleState.HEATING

        # Collect temperature samples
        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await cycle_tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Valve closes to 0% (tracked as heating stopped)
        cycle_tracker.on_heating_session_ended(current_time)

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
        self, mock_hass, mock_adaptive_learner, mock_callbacks
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
        )
        tracker.set_restoration_complete()

        # Start heating
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        tracker.on_heating_started(start_time)

        assert tracker.state == CycleState.HEATING

        # Collect 5 temperature samples
        current_time = start_time
        for i in range(5):
            temp = 19.0 + i * 0.2
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change setpoint mid-cycle (heater still active)
        tracker.on_setpoint_changed(21.0, 22.0)

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
        tracker.on_heating_session_ended(current_time)

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
        self, mock_hass, mock_adaptive_learner, mock_callbacks
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
        )
        tracker.set_restoration_complete()

        # Start cooling cycle
        start_time = datetime(2024, 7, 15, 14, 0, 0)  # Summer afternoon
        tracker.on_cooling_started(start_time)

        assert tracker.state == CycleState.COOLING

        # Collect 5 temperature samples (cooling down from 26°C)
        current_time = start_time
        for i in range(5):
            temp = 26.0 - i * 0.2  # Temperature dropping
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        # Change setpoint mid-cycle while cooler is active (e.g., user wants it colder)
        tracker.on_setpoint_changed(24.0, 23.0)

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
        tracker.on_cooling_session_ended(current_time)

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
def test_integration_cycle_learning_module_exists():
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
        self, cycle_tracker, mock_adaptive_learner
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
        cycle_tracker.on_heating_started(start_time)
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
        cycle_tracker.on_heating_session_ended(stop_time)

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
