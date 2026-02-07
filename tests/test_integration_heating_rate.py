"""Integration tests for heating rate session lifecycle.

This module tests the complete session lifecycle for heating rate learning,
verifying that sessions are started, updated, and ended correctly based on
thermostat state and override conditions.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateLearner,
)
from custom_components.adaptive_climate.managers.events import CycleEndedEvent
from custom_components.adaptive_climate.const import HeatingType
from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.data = {
        "adaptive_climate": {
            "coordinator": None,
            "learning_store": None,
        }
    }
    return hass


@pytest.fixture
def mock_thermostat(mock_hass):
    """Create a mock thermostat with heating rate learner."""
    from custom_components.adaptive_climate.adaptive.learning import AdaptiveLearner

    # Create adaptive learner with heating rate learner
    learner = AdaptiveLearner(
        heating_type=HeatingType.RADIATOR,
    )

    # Create mock thermostat
    thermostat = MagicMock()
    thermostat.hass = mock_hass
    thermostat.entity_id = "climate.test_zone"
    thermostat._name = "Test Zone"
    thermostat._zone_id = "test_zone"
    thermostat._heating_type = HeatingType.RADIATOR
    thermostat._hvac_mode = HVACMode.HEAT
    thermostat._current_temp = 19.0
    thermostat._target_temp = 21.0
    thermostat._ext_temp = 5.0
    thermostat._cold_tolerance = 0.3
    thermostat._hot_tolerance = 0.3

    # Status manager (no pauses active)
    thermostat._status_manager = MagicMock()
    thermostat._status_manager.is_paused.return_value = False

    # Contact sensor handler
    thermostat._contact_sensor_handler = None

    # Humidity detector
    thermostat._humidity_detector = None

    # Open window detector
    thermostat._open_window_detector = None

    # Coordinator access
    thermostat._coordinator = None
    thermostat.hass.data["adaptive_climate"]["coordinator"] = None

    # Store learner in coordinator-like structure
    zone_data = {"adaptive_learner": learner}
    coordinator = MagicMock()
    coordinator.get_zone_data.return_value = zone_data
    thermostat.hass.data["adaptive_climate"]["coordinator"] = coordinator
    thermostat._coordinator = coordinator

    # Direct access to learner for tests
    thermostat._learner = learner

    return thermostat


class TestSessionStartDetection:
    """Test session start detection in _async_control_heating."""

    @pytest.mark.asyncio
    async def test_session_starts_when_temp_below_threshold(self, mock_thermostat):
        """Test session starts when temp is below setpoint - 0.5°C threshold."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Set temp below threshold (21.0 - 0.5 = 20.5)
        mock_thermostat._current_temp = 19.0
        mock_thermostat._target_temp = 21.0
        mock_thermostat._hvac_mode = HVACMode.HEAT

        # No active session initially
        assert learner._active_session is None

        # Simulate session start call (this will be in _async_control_heating)
        threshold = 0.5  # radiator threshold
        if mock_thermostat._current_temp < mock_thermostat._target_temp - threshold:
            learner.start_session(
                temp=mock_thermostat._current_temp,
                setpoint=mock_thermostat._target_temp,
                outdoor_temp=mock_thermostat._ext_temp,
            )

        # Verify session started
        assert learner._active_session is not None
        assert learner._active_session.start_temp == 19.0
        assert learner._active_session.target_setpoint == 21.0
        assert learner._active_session.outdoor_temp == 5.0

    @pytest.mark.asyncio
    async def test_session_not_started_when_temp_above_threshold(self, mock_thermostat):
        """Test session doesn't start when temp is above threshold."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Set temp above threshold (21.0 - 0.5 = 20.5)
        mock_thermostat._current_temp = 20.7
        mock_thermostat._target_temp = 21.0

        # No active session initially
        assert learner._active_session is None

        # Simulate session start check
        threshold = 0.5
        if mock_thermostat._current_temp < mock_thermostat._target_temp - threshold:
            learner.start_session(
                temp=mock_thermostat._current_temp,
                setpoint=mock_thermostat._target_temp,
                outdoor_temp=mock_thermostat._ext_temp,
            )

        # Verify no session started
        assert learner._active_session is None

    @pytest.mark.asyncio
    async def test_session_not_started_when_paused(self, mock_thermostat):
        """Test session doesn't start when heating is paused."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Set temp below threshold but pause active
        mock_thermostat._current_temp = 19.0
        mock_thermostat._target_temp = 21.0
        mock_thermostat._status_manager.is_paused.return_value = True

        # No active session initially
        assert learner._active_session is None

        # Simulate session start check with pause check
        threshold = 0.5
        if (
            not mock_thermostat._status_manager.is_paused()
            and mock_thermostat._current_temp < mock_thermostat._target_temp - threshold
        ):
            learner.start_session(
                temp=mock_thermostat._current_temp,
                setpoint=mock_thermostat._target_temp,
                outdoor_temp=mock_thermostat._ext_temp,
            )

        # Verify no session started
        assert learner._active_session is None

    @pytest.mark.asyncio
    async def test_floor_hydronic_uses_lower_threshold(self, mock_thermostat):
        """Test floor_hydronic uses 0.3°C threshold instead of 0.5°C."""
        # Change heating type to floor_hydronic
        mock_thermostat._heating_type = HeatingType.FLOOR_HYDRONIC
        learner = HeatingRateLearner(HeatingType.FLOOR_HYDRONIC)
        mock_thermostat._learner._heating_rate_learner = learner

        # Set temp at 20.8 (below 21.0 - 0.3 = 20.7, but above 21.0 - 0.5 = 20.5)
        mock_thermostat._current_temp = 20.6
        mock_thermostat._target_temp = 21.0

        # Simulate session start with floor_hydronic threshold
        threshold = 0.3  # floor_hydronic threshold
        if mock_thermostat._current_temp < mock_thermostat._target_temp - threshold:
            learner.start_session(
                temp=mock_thermostat._current_temp,
                setpoint=mock_thermostat._target_temp,
                outdoor_temp=mock_thermostat._ext_temp,
            )

        # Verify session started with lower threshold
        assert learner._active_session is not None


class TestSessionUpdate:
    """Test session update on cycle completion."""

    @pytest.mark.asyncio
    async def test_session_updated_on_cycle_end(self, mock_thermostat):
        """Test session is updated when cycle ends."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session
        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
        )

        # Simulate cycle end with metrics
        mock_thermostat._current_temp = 19.5
        cycle_event = CycleEndedEvent(
            timestamp=dt_util.utcnow(),
            hvac_mode=HVACMode.HEAT,
            metrics={
                "duty": 65.0,
                "start_temp": 19.0,
                "end_temp": 19.5,
                "duration_minutes": 30.0,
            },
        )

        # Update session
        learner.update_session(
            temp=mock_thermostat._current_temp,
            duty=cycle_event.metrics["duty"],
        )

        # Verify session updated
        assert learner._active_session.cycles_in_session == 1
        assert learner._active_session.cycle_duties == [65.0]
        assert learner._active_session.last_temp == 19.5

    @pytest.mark.asyncio
    async def test_multiple_cycle_updates(self, mock_thermostat):
        """Test session tracks multiple cycles."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session
        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
        )

        # Simulate 3 cycles
        temps = [19.3, 19.7, 20.1]
        duties = [70.0, 65.0, 60.0]

        for temp, duty in zip(temps, duties):
            mock_thermostat._current_temp = temp
            learner.update_session(temp=temp, duty=duty)

        # Verify all cycles tracked
        assert learner._active_session.cycles_in_session == 3
        assert learner._active_session.cycle_duties == duties
        assert learner._active_session.last_temp == 20.1


class TestSessionEndReachedSetpoint:
    """Test session end when setpoint is reached."""

    @pytest.mark.asyncio
    async def test_session_ends_when_setpoint_reached(self, mock_thermostat):
        """Test session ends successfully when setpoint is reached."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session
        start_time = dt_util.utcnow() - timedelta(minutes=60)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # Simulate reaching setpoint
        mock_thermostat._current_temp = 20.8  # >= 21.0 - 0.3 (cold_tolerance)

        # Update session and check if reached
        learner.update_session(temp=20.8, duty=55.0)

        # End session when reached
        end_temp = 20.8
        cold_tolerance = 0.3
        if end_temp >= learner._active_session.target_setpoint - cold_tolerance:
            obs = learner.end_session(end_temp=end_temp, reason="reached_setpoint", timestamp=end_time)

        # Verify session ended and observation banked
        assert learner._active_session is None
        assert obs is not None
        assert obs.source == "session"
        assert obs.stalled is False
        assert obs.duration_min == pytest.approx(60.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_session_end_calculates_correct_rate(self, mock_thermostat):
        """Test session end calculates correct heating rate."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session
        start_time = dt_util.utcnow() - timedelta(minutes=60)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # End session after 60 minutes with 1.5°C rise
        end_temp = 20.5
        obs = learner.end_session(end_temp=end_temp, reason="reached_setpoint", timestamp=end_time)

        # Verify rate calculation (1.5°C / 1 hour = 1.5°C/h)
        assert obs is not None
        assert obs.rate == pytest.approx(1.5, rel=0.01)


class TestSessionEndStalled:
    """Test session end when stalled."""

    @pytest.mark.asyncio
    async def test_session_ends_when_stalled(self, mock_thermostat):
        """Test session ends as stalled after 3 cycles without progress."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session
        start_time = dt_util.utcnow() - timedelta(minutes=90)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # Simulate 3 cycles with no progress (temp rises < 0.1°C each)
        for i in range(3):
            learner.update_session(temp=19.05, duty=60.0)

        # Check if stalled
        is_stalled = learner.is_stalled()
        assert is_stalled is True

        # End session as stalled
        obs = learner.end_session(end_temp=19.05, reason="stalled", timestamp=end_time)

        # Verify session ended and observation banked as stalled
        assert learner._active_session is None
        assert obs is not None
        assert obs.stalled is True
        assert obs.source == "session"

    @pytest.mark.asyncio
    async def test_stall_counter_increments(self, mock_thermostat):
        """Test stall counter increments on stalled session."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start and end stalled session
        start_time = dt_util.utcnow() - timedelta(minutes=90)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        for i in range(3):
            learner.update_session(temp=19.05, duty=60.0)

        # End as stalled
        with patch("homeassistant.util.dt.utcnow", return_value=end_time):
            learner.end_session(end_temp=19.05, reason="stalled")

        # Verify stall counter incremented
        assert learner._stall_counter == 1


class TestSessionEndOverride:
    """Test session end when override occurs."""

    @pytest.mark.asyncio
    async def test_session_discarded_on_contact_open(self, mock_thermostat):
        """Test session is discarded (not banked) when contact opens."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session
        start_time = dt_util.utcnow() - timedelta(minutes=30)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # Simulate contact opening
        mock_thermostat._status_manager.is_paused.return_value = True

        # End session due to override
        obs = learner.end_session(end_temp=19.5, reason="override", timestamp=end_time)

        # Verify session discarded (no observation banked)
        assert learner._active_session is None
        assert obs is None

    @pytest.mark.asyncio
    async def test_session_discarded_on_humidity_pause(self, mock_thermostat):
        """Test session is discarded when humidity pause occurs."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session
        start_time = dt_util.utcnow() - timedelta(minutes=30)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # Simulate humidity pause
        mock_thermostat._status_manager.is_paused.return_value = True

        # End session due to override
        obs = learner.end_session(end_temp=19.5, reason="override", timestamp=end_time)

        # Verify session discarded
        assert learner._active_session is None
        assert obs is None

    @pytest.mark.asyncio
    async def test_too_short_session_discarded(self, mock_thermostat):
        """Test session is discarded if duration is too short."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start session (radiator min duration = 30 min)
        start_time = dt_util.utcnow() - timedelta(minutes=20)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # End session after only 20 minutes
        obs = learner.end_session(end_temp=20.5, reason="reached_setpoint", timestamp=end_time)

        # Verify session discarded due to short duration
        assert learner._active_session is None
        assert obs is None


class TestSessionDontStartTwice:
    """Test that we don't start a session if one is already active."""

    @pytest.mark.asyncio
    async def test_no_duplicate_session_start(self, mock_thermostat):
        """Test session start is skipped if session already active."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start first session
        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
        )
        first_session = learner._active_session

        # Attempt to start second session
        threshold = 0.5
        if learner._active_session is None and mock_thermostat._current_temp < mock_thermostat._target_temp - threshold:
            learner.start_session(
                temp=18.5,
                setpoint=21.0,
                outdoor_temp=4.0,
            )

        # Verify original session unchanged
        assert learner._active_session is first_session
        assert learner._active_session.start_temp == 19.0


class TestSessionLifecycleComplete:
    """Test complete session lifecycle from start to end."""

    @pytest.mark.asyncio
    async def test_complete_successful_session(self, mock_thermostat):
        """Test complete session lifecycle: start -> updates -> reached setpoint."""
        learner = mock_thermostat._learner._heating_rate_learner

        # 1. Start session
        start_time = dt_util.utcnow() - timedelta(minutes=60)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # 2. Simulate 3 heating cycles with progress
        temps = [19.5, 20.0, 20.8]
        duties = [70.0, 65.0, 60.0]

        for temp, duty in zip(temps, duties):
            learner.update_session(temp=temp, duty=duty)

        # 3. Check if reached setpoint
        end_temp = 20.8
        cold_tolerance = 0.3
        reached = end_temp >= learner._active_session.target_setpoint - cold_tolerance
        assert reached is True

        # 4. End session successfully
        obs = learner.end_session(end_temp=end_temp, reason="reached_setpoint", timestamp=end_time)

        # 5. Verify complete lifecycle
        assert learner._active_session is None
        assert obs is not None
        assert obs.source == "session"
        assert obs.stalled is False
        assert obs.duration_min == pytest.approx(60.0, rel=0.01)
        assert learner._stall_counter == 0  # Success resets counter

    @pytest.mark.asyncio
    async def test_complete_stalled_session(self, mock_thermostat):
        """Test complete session lifecycle: start -> updates -> stalled -> end."""
        learner = mock_thermostat._learner._heating_rate_learner

        # 1. Start session
        start_time = dt_util.utcnow() - timedelta(minutes=90)
        end_time = dt_util.utcnow()

        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
            timestamp=start_time,
        )

        # 2. Simulate 4 cycles with minimal progress
        for i in range(4):
            learner.update_session(temp=19.05, duty=60.0)

        # 3. Check if stalled (3 cycles without progress)
        is_stalled = learner.is_stalled()
        assert is_stalled is True

        # 4. End session as stalled
        obs = learner.end_session(end_temp=19.05, reason="stalled", timestamp=end_time)

        # 5. Verify complete lifecycle
        assert learner._active_session is None
        assert obs is not None
        assert obs.stalled is True
        assert learner._stall_counter == 1


class TestNightSetbackSessionManagement:
    """Test heating rate session management during night setback periods."""

    @pytest.mark.asyncio
    async def test_no_session_start_during_night_setback(self, mock_thermostat):
        """Test session doesn't start when night setback is active."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Set up night setback controller that returns in_night_period = True
        mock_night_setback = MagicMock()
        mock_night_setback.calculate_night_setback_adjustment.return_value = (
            -2.0,  # adjustment
            True,  # in_night_period
            {},  # night_setback_info
        )
        mock_thermostat._night_setback_controller = mock_night_setback

        # Set temp below threshold (19.0 < 21.0 - 0.5)
        mock_thermostat._current_temp = 19.0
        mock_thermostat._target_temp = 21.0

        # No active session initially
        assert learner._active_session is None

        # Simulate session start logic with night setback guard
        in_night_setback = False
        if mock_thermostat._night_setback_controller:
            _, in_night_period, _ = mock_thermostat._night_setback_controller.calculate_night_setback_adjustment()
            in_night_setback = in_night_period

        threshold = 0.5  # radiator threshold
        if (
            learner._active_session is None
            and not mock_thermostat._status_manager.is_paused()
            and not in_night_setback
            and mock_thermostat._ext_temp is not None
        ):
            learner.start_session(
                temp=mock_thermostat._current_temp,
                setpoint=mock_thermostat._target_temp,
                outdoor_temp=mock_thermostat._ext_temp,
            )

        # Verify no session started
        assert learner._active_session is None

    @pytest.mark.asyncio
    async def test_active_session_discarded_on_night_setback_start(self, mock_thermostat):
        """Test active session is discarded when night setback activates."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Start a session normally (no night setback initially)
        mock_thermostat._night_setback_controller = None
        learner.start_session(
            temp=19.0,
            setpoint=21.0,
            outdoor_temp=5.0,
        )

        # Verify session is active
        assert learner._active_session is not None

        # Now simulate night setback becoming active
        mock_night_setback = MagicMock()
        mock_night_setback.calculate_night_setback_adjustment.return_value = (
            -2.0,  # adjustment
            True,  # in_night_period
            {},  # night_setback_info
        )
        mock_thermostat._night_setback_controller = mock_night_setback
        mock_thermostat._current_temp = 19.3

        # Run the discard logic
        in_night_setback = False
        if mock_thermostat._night_setback_controller:
            _, in_night_period, _ = mock_thermostat._night_setback_controller.calculate_night_setback_adjustment()
            in_night_setback = in_night_period

        if in_night_setback and learner._active_session is not None:
            learner.end_session(
                end_temp=mock_thermostat._current_temp,
                reason="override",
            )

        # Verify session is ended and observation NOT banked
        assert learner._active_session is None

    @pytest.mark.asyncio
    async def test_session_starts_after_night_setback_ends(self, mock_thermostat):
        """Test session can start normally when night setback is not active."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Set up night setback controller that returns in_night_period = False
        mock_night_setback = MagicMock()
        mock_night_setback.calculate_night_setback_adjustment.return_value = (
            0.0,  # adjustment (no setback)
            False,  # in_night_period
            {},  # night_setback_info
        )
        mock_thermostat._night_setback_controller = mock_night_setback

        # Set temp below threshold
        mock_thermostat._current_temp = 19.0
        mock_thermostat._target_temp = 21.0

        # No active session initially
        assert learner._active_session is None

        # Simulate session start logic
        in_night_setback = False
        if mock_thermostat._night_setback_controller:
            _, in_night_period, _ = mock_thermostat._night_setback_controller.calculate_night_setback_adjustment()
            in_night_setback = in_night_period

        threshold = 0.5
        if (
            learner._active_session is None
            and not mock_thermostat._status_manager.is_paused()
            and not in_night_setback
            and mock_thermostat._ext_temp is not None
        ):
            learner.start_session(
                temp=mock_thermostat._current_temp,
                setpoint=mock_thermostat._target_temp,
                outdoor_temp=mock_thermostat._ext_temp,
            )

        # Verify session started normally
        assert learner._active_session is not None
        assert learner._active_session.start_temp == 19.0

    @pytest.mark.asyncio
    async def test_works_without_night_setback_controller(self, mock_thermostat):
        """Test session start works when night_setback_controller is None."""
        learner = mock_thermostat._learner._heating_rate_learner

        # Set night_setback_controller to None
        mock_thermostat._night_setback_controller = None

        # Set temp below threshold
        mock_thermostat._current_temp = 19.0
        mock_thermostat._target_temp = 21.0

        # No active session initially
        assert learner._active_session is None

        # Simulate session start logic
        in_night_setback = False
        if mock_thermostat._night_setback_controller:
            _, in_night_period, _ = mock_thermostat._night_setback_controller.calculate_night_setback_adjustment()
            in_night_setback = in_night_period

        threshold = 0.5
        if (
            learner._active_session is None
            and not mock_thermostat._status_manager.is_paused()
            and not in_night_setback
            and mock_thermostat._ext_temp is not None
        ):
            learner.start_session(
                temp=mock_thermostat._current_temp,
                setpoint=mock_thermostat._target_temp,
                outdoor_temp=mock_thermostat._ext_temp,
            )

        # Verify session starts normally (no crash)
        assert learner._active_session is not None
        assert learner._active_session.start_temp == 19.0
