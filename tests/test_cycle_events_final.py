"""Tests for final cycle event refactor - verifying no legacy code remains.

This module tests that:
1. CycleTrackerManager works purely through events
2. HeaterController has no direct _cycle_tracker references
3. climate.py uses only events for cycle communication
"""

from __future__ import annotations

import pytest
import ast
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

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
)
from homeassistant.util import dt as dt_util


@pytest.fixture(autouse=True)
def mock_dt_util():
    """Mock dt_util.utcnow() to return a fixed datetime for duration calculations."""
    # Set a far-future datetime to ensure all cycle durations are valid
    fixed_now = datetime(2024, 12, 31, 23, 59, 59)
    with patch('custom_components.adaptive_thermostat.managers.cycle_metrics.dt_util.utcnow', return_value=fixed_now):
        yield


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro)

    # Mock async_call_later to return a cancel handle
    def mock_call_later(hass_instance, delay, callback):
        return MagicMock()

    # Patch async_call_later at module level
    import homeassistant.helpers.event
    original_call_later = homeassistant.helpers.event.async_call_later
    homeassistant.helpers.event.async_call_later = mock_call_later

    yield hass

    # Restore original
    homeassistant.helpers.event.async_call_later = original_call_later


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
def dispatcher():
    """Create a cycle event dispatcher."""
    return CycleEventDispatcher()


@pytest.fixture
def cycle_tracker(mock_hass, mock_adaptive_learner, dispatcher):
    """Create a CycleTrackerManager instance with event dispatcher."""
    tracker = CycleTrackerManager(
        hass=mock_hass,
        zone_id="test_zone",
        adaptive_learner=mock_adaptive_learner,
        get_target_temp=lambda: 21.0,
        get_current_temp=lambda: 20.0,
        get_hvac_mode=lambda: "heat",
        get_in_grace_period=lambda: False,
        get_is_device_active=lambda: False,
        dispatcher=dispatcher,
    )
    tracker.set_restoration_complete()
    return tracker


class TestCycleTrackerEventOnly:
    """Test that CycleTrackerManager works purely through events."""

    def test_cycle_started_event(self, cycle_tracker, dispatcher):
        """Test CYCLE_STARTED event triggers cycle start."""
        # Emit CYCLE_STARTED event
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            target_temp=21.0,
            current_temp=19.0,
        ))

        # Verify cycle state changed to HEATING
        assert cycle_tracker.state == CycleState.HEATING
        assert cycle_tracker.cycle_start_time is not None

    def test_settling_started_event(self, cycle_tracker, dispatcher):
        """Test SETTLING_STARTED event triggers settling."""
        # Start a cycle first
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            target_temp=21.0,
            current_temp=19.0,
        ))

        # Emit SETTLING_STARTED event
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
        ))

        # Verify cycle state changed to SETTLING
        assert cycle_tracker.state == CycleState.SETTLING

    def test_setpoint_changed_event(self, cycle_tracker, dispatcher):
        """Test SETPOINT_CHANGED event is handled."""
        # Start a cycle first
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            target_temp=21.0,
            current_temp=19.0,
        ))

        initial_state = cycle_tracker.state

        # Emit minor setpoint change (should continue)
        dispatcher.emit(SetpointChangedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            old_target=21.0,
            new_target=21.3,
        ))

        # Cycle should still be active (minor change)
        assert cycle_tracker.state == initial_state

    def test_mode_changed_event(self, cycle_tracker, dispatcher):
        """Test MODE_CHANGED event is handled."""
        # Start a cycle first
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            target_temp=21.0,
            current_temp=19.0,
        ))

        # Emit mode change event (incompatible)
        dispatcher.emit(ModeChangedEvent(
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            old_mode="heat",
            new_mode="off",
        ))

        # Cycle should be aborted
        assert cycle_tracker.state == CycleState.IDLE

    def test_contact_pause_event(self, cycle_tracker, dispatcher):
        """Test CONTACT_PAUSE event is handled."""
        # Start a cycle first
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            target_temp=21.0,
            current_temp=19.0,
        ))

        # Emit contact pause event
        dispatcher.emit(ContactPauseEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            entity_id="binary_sensor.window",
        ))

        # Cycle should be aborted
        assert cycle_tracker.state == CycleState.IDLE


class TestNoLegacyCode:
    """Test that no legacy code remains in the codebase."""

    def test_heater_controller_no_cycle_tracker_refs(self):
        """Verify HeaterController has no direct _cycle_tracker references."""
        # Read HeaterController source
        hc_path = Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat" / "managers" / "heater_controller.py"
        source = hc_path.read_text()

        # Check for _cycle_tracker references
        assert "_cycle_tracker" not in source, (
            "HeaterController should not have direct _cycle_tracker references"
        )

    def test_climate_no_legacy_cycle_calls(self):
        """Verify climate.py uses only events for cycle communication."""
        # Read climate.py source
        climate_path = Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat" / "climate.py"
        source = climate_path.read_text()

        # Parse the AST to find method calls
        tree = ast.parse(source)

        # Look for deprecated method calls
        deprecated_methods = [
            "on_heating_started",
            "on_heating_session_ended",
            "on_cooling_started",
            "on_cooling_session_ended",
            "on_setpoint_changed",
            "on_mode_changed",
            "on_contact_sensor_pause",
        ]

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in deprecated_methods:
                    # Check if this is actually a call to the deprecated method
                    # (ignore definitions and docstrings)
                    if isinstance(node.ctx, ast.Load):
                        pytest.fail(
                            f"Found deprecated method call: {node.attr} in climate.py"
                        )

    def test_cycle_tracker_no_public_deprecated_methods(self):
        """Verify deprecated methods are removed from CycleTrackerManager."""
        # Check that deprecated methods don't exist
        tracker = CycleTrackerManager.__dict__

        deprecated_methods = [
            "on_heating_started",
            "on_heating_session_ended",
            "on_cooling_started",
            "on_cooling_session_ended",
            "on_setpoint_changed",
            "on_mode_changed",
            "on_contact_sensor_pause",
        ]

        for method_name in deprecated_methods:
            assert method_name not in tracker, (
                f"Deprecated method {method_name} should be removed from CycleTrackerManager"
            )


class TestCycleEventIntegration:
    """Integration tests for complete event flow."""

    @pytest.mark.asyncio
    async def test_complete_cycle_via_events(self, cycle_tracker, dispatcher, mock_adaptive_learner):
        """Test a complete heating cycle using only events."""
        # Start cycle
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
            target_temp=21.0,
            current_temp=19.0,
        ))

        assert cycle_tracker.state == CycleState.HEATING

        # Simulate temperature rise
        for i in range(20):
            temp = 19.0 + (i * 0.1)
            await cycle_tracker.update_temperature(start_time, temp)

        # End heating session
        dispatcher.emit(SettlingStartedEvent(
            hvac_mode="heat",
            timestamp=start_time,
        ))

        assert cycle_tracker.state == CycleState.SETTLING

    @pytest.mark.asyncio
    async def test_cycle_interruption_via_events(self, cycle_tracker, dispatcher):
        """Test cycle interruption through events."""
        # Start cycle
        dispatcher.emit(CycleStartedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            target_temp=21.0,
            current_temp=19.0,
        ))

        assert cycle_tracker.state == CycleState.HEATING

        # Interrupt with contact sensor
        dispatcher.emit(ContactPauseEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            entity_id="binary_sensor.window",
        ))

        # Cycle should be aborted
        assert cycle_tracker.state == CycleState.IDLE
        assert cycle_tracker.get_last_interruption_reason() == "contact_sensor"
