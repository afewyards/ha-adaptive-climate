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
    return CycleTrackerManager(
        hass=mock_hass,
        zone_id="bedroom",
        adaptive_learner=mock_adaptive_learner,
        get_target_temp=mock_callbacks["get_target_temp"],
        get_current_temp=mock_callbacks["get_current_temp"],
        get_hvac_mode=mock_callbacks["get_hvac_mode"],
        get_in_grace_period=mock_callbacks["get_in_grace_period"],
    )


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
        cycle_tracker.on_heating_stopped(stop_time)

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
            cycle_tracker.on_heating_stopped(stop_time)

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
        tracker.on_heating_stopped(current_time)

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
        cycle_tracker.on_heating_stopped(current_time)

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
        cycle_tracker.on_heating_stopped(current_time)

        assert cycle_tracker.state == CycleState.SETTLING

        # Add settling samples
        for _ in range(10):
            await cycle_tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Verify cycle recorded
        assert cycle_tracker.state == CycleState.IDLE
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
