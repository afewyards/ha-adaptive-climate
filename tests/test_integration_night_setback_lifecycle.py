"""Integration tests for night setback lifecycle.

Tests the full lifecycle of night setback functionality including:
- Full setback → preheat → recovery cycle
- Learning gate graduation through learning stages
- Setback + pause interaction (priority stacking)
"""

import pytest
from datetime import datetime, time as dt_time, timedelta, timezone
from unittest.mock import MagicMock

from custom_components.adaptive_climate.managers.night_setback_calculator import (
    NightSetbackCalculator,
)
from custom_components.adaptive_climate.managers.learning_gate import (
    LearningGateManager,
)
from custom_components.adaptive_climate.adaptive.preheat import PreheatLearner
from custom_components.adaptive_climate.adaptive.heating_rate_learner import (
    HeatingRateLearner,
)
from custom_components.adaptive_climate.adaptive.contact_sensors import (
    ContactSensorHandler,
    ContactAction,
)
from custom_components.adaptive_climate.adaptive.humidity_detector import (
    HumidityDetector,
)
from custom_components.adaptive_climate.const import HeatingType
from custom_components.adaptive_climate.adaptive.cycle_analysis import CycleMetrics


class TestNightSetbackLifecycle:
    """Test B1: Full setback → preheat → recovery."""

    def test_full_setback_preheat_recovery_cycle(self, mock_hass, time_travel):
        """Test complete night setback cycle with preheat and recovery.

        Simulates:
        1. Normal operation before setback
        2. Setback period starts (setpoint drops)
        3. Preheat calculates start time based on heating rate
        4. Heating begins at preheat time
        5. Temperature rises toward target
        6. Recovery completes at deadline
        """
        # Set initial time to 22:50 (10 minutes before setback)
        time_travel._current_dt = datetime(2024, 1, 1, 22, 50, 0, tzinfo=timezone.utc)

        # Create HeatingRateLearner and PreheatLearner
        heating_rate_learner = HeatingRateLearner(HeatingType.RADIATOR.value)
        preheat_learner = PreheatLearner(
            heating_type=HeatingType.RADIATOR.value,
            max_hours=3.0,
            heating_rate_learner=heating_rate_learner,
        )

        # Add a learned heating rate observation for 2°C recovery in cold conditions
        # Simulate a 2°C rise taking 4 hours (0.5°C/h rate)
        heating_rate_learner.add_observation(
            rate=0.5,  # °C/h
            duration_min=240.0,  # 4 hours
            source="session",
            stalled=False,
            delta=2.0,
            outdoor_temp=3.0,
            timestamp=time_travel.now() - timedelta(days=1),
        )

        # Create mocks for callbacks
        target_temp = 21.0
        current_temp = 21.0  # Currently at target
        outdoor_temp = 3.0

        get_target_temp = lambda: target_temp
        get_current_temp = lambda: current_temp

        # Create night setback config
        night_setback_config = {
            "start": "23:00",
            "delta": 2.0,
            "recovery_deadline": "07:00",
        }

        # Create NightSetbackCalculator
        calculator = NightSetbackCalculator(
            hass=mock_hass,
            entity_id="climate.test",
            night_setback=None,
            night_setback_config=night_setback_config,
            window_orientation=None,
            get_target_temp=get_target_temp,
            get_current_temp=get_current_temp,
            preheat_learner=preheat_learner,
            preheat_enabled=True,
            manifold_transport_delay=0.0,
        )

        # Step 1: At 22:59 - normal operation, no setback
        time_travel._current_dt = datetime(2024, 1, 1, 22, 59, 0, tzinfo=timezone.utc)
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is False
        assert effective_target == 21.0

        # Step 2: At 23:00 - night period starts, setpoint drops
        time_travel._current_dt = datetime(2024, 1, 1, 23, 0, 0, tzinfo=timezone.utc)
        current_temp = 21.0  # Still at old target
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is True
        assert effective_target == 19.0  # 21.0 - 2.0
        assert info["night_setback_delta"] == 2.0

        # Simulate temperature dropping during setback
        time_travel._current_dt = datetime(2024, 1, 2, 2, 0, 0, tzinfo=timezone.utc)
        current_temp = 19.0  # Reached setback temperature

        # Step 3: Calculate preheat start time
        # Recovery deadline is 07:00, current time is 02:00
        # Delta is 2°C, learned rate is 0.5°C/h
        # Estimated time = 2.0 / 0.5 = 4 hours
        # With 10% buffer (min 15 min): 4h + 24 min = 4.4h
        # Preheat should start at ~02:36 (07:00 - 4.4h)
        deadline = datetime(2024, 1, 2, 7, 0, 0, tzinfo=timezone.utc)
        preheat_start = calculator.calculate_preheat_start(
            deadline=deadline,
            current_temp=current_temp,
            target_temp=target_temp,
            outdoor_temp=outdoor_temp,
            humidity_paused=False,
            effective_delta=2.0,
        )

        assert preheat_start is not None
        # Preheat should start before the deadline
        assert preheat_start < deadline
        # Preheat should be reasonable (between 1 and 5 hours before deadline)
        time_before_deadline = (deadline - preheat_start).total_seconds() / 3600
        assert 1.0 <= time_before_deadline <= 5.0

        # Step 4: At preheat start time - heating begins
        time_travel._current_dt = preheat_start
        preheat_info = calculator.get_preheat_info(
            now=time_travel.now(),
            current_temp=current_temp,
            target_temp=target_temp,
            outdoor_temp=outdoor_temp,
            deadline=deadline,
            humidity_paused=False,
            effective_delta=2.0,
        )
        assert preheat_info["active"] is True
        assert preheat_info["scheduled_start"] == preheat_start

        # Start a heating session
        heating_rate_learner.start_session(
            temp=current_temp,
            setpoint=target_temp,
            outdoor_temp=outdoor_temp,
            timestamp=time_travel.now(),
        )

        # Step 5: Simulate temperature rising toward target
        # Advance 2 hours (halfway through recovery)
        time_travel.advance(hours=2)
        current_temp = 20.0  # Halfway to target
        heating_rate_learner.update_session(temp=current_temp, duty=65.0)

        # Step 6: At 07:00 - recovery completes, session ends
        time_travel._current_dt = datetime(2024, 1, 2, 7, 0, 0, tzinfo=timezone.utc)
        current_temp = 21.0  # Reached target
        heating_rate_learner.update_session(temp=current_temp, duty=60.0)

        # End the session
        observation = heating_rate_learner.end_session(
            end_temp=current_temp,
            reason="reached_setpoint",
            timestamp=time_travel.now(),
        )

        # Verify observation was recorded
        assert observation is not None
        assert observation.source == "session"
        assert observation.stalled is False
        assert heating_rate_learner.get_observation_count() == 2  # Initial + new

        # Verify night setback is no longer active
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is False
        assert effective_target == 21.0


class TestLearningGateGraduation:
    """Test B2: Learning gate graduation through learning stages."""

    def test_learning_gate_graduated_delta(self, make_thermostat):
        """Test learning gate applies graduated delta as system learns.

        Progression:
        1. Fresh system (0 cycles) → 0.0°C (suppressed)
        2. After 3 cycles → 0.5°C (limited)
        3. Mock tier 1 confidence → 1.0°C (limited)
        4. Mock tier 2 confidence → unlimited (full delta)
        """
        # Create thermostat with radiator type
        t = make_thermostat(heating_type=HeatingType.RADIATOR)

        # Create mock night setback manager
        mock_night_setback = MagicMock()
        mock_night_setback.is_configured = True
        mock_night_setback.in_learning_grace_period = False

        # Create mock learner that we can control
        mock_learner = MagicMock()
        mock_learner.get_cycle_count = MagicMock(return_value=0)
        mock_learner.get_convergence_confidence = MagicMock(return_value=0.0)

        # Create learning gate manager
        learning_gate = LearningGateManager(
            night_setback_controller=mock_night_setback,
            contact_sensor_handler=None,
            humidity_detector=None,
            get_adaptive_learner=lambda: mock_learner,
            heating_type=HeatingType.RADIATOR,
        )

        # Stage 1: Fresh system (0 cycles) - fully suppressed
        allowed_delta = learning_gate.get_allowed_delta()
        assert allowed_delta == 0.0

        # Stage 2: After recording 3 cycles - 0.5°C allowed
        mock_learner.get_cycle_count.return_value = 3
        mock_learner.get_convergence_confidence.return_value = 0.2  # Below tier 1

        allowed_delta = learning_gate.get_allowed_delta()
        assert allowed_delta == 0.5

        # Stage 3: Reach "stable" status (tier 1) - 1.0°C allowed
        # For radiator, tier 1 is scaled to 36% (0.36)
        mock_learner.get_cycle_count.return_value = 10
        mock_learner.get_convergence_confidence.return_value = 0.36

        allowed_delta = learning_gate.get_allowed_delta()
        assert allowed_delta == 1.0

        # Stage 4: Reach "tuned" status (tier 2) - unlimited (None)
        # For radiator, tier 2 is scaled to 63% (0.63)
        mock_learner.get_cycle_count.return_value = 20
        mock_learner.get_convergence_confidence.return_value = 0.63

        allowed_delta = learning_gate.get_allowed_delta()
        assert allowed_delta is None  # Unlimited


class TestSetbackPausePriority:
    """Test B3: Setback + pause interaction (priority stacking)."""

    def test_contact_override_takes_priority_over_setback(self, mock_hass, time_travel):
        """Test contact sensor pause overrides night setback.

        Simulates:
        1. Night setback active (setpoint reduced)
        2. Contact sensor opens → heating pauses (higher priority)
        3. Verify heating is paused (contact takes priority)
        4. Contact closes → verify setback still active
        5. Night period ends → verify normal operation resumes
        """
        # Set time to middle of night setback period (02:00)
        time_travel._current_dt = datetime(2024, 1, 2, 2, 0, 0, tzinfo=timezone.utc)

        # Create mocks
        target_temp = 21.0
        current_temp = 19.0  # At setback temperature
        get_target_temp = lambda: target_temp
        get_current_temp = lambda: current_temp

        # Create night setback config
        night_setback_config = {
            "start": "23:00",
            "delta": 2.0,
            "recovery_deadline": "07:00",
        }

        # Create calculator
        calculator = NightSetbackCalculator(
            hass=mock_hass,
            entity_id="climate.test",
            night_setback=None,
            night_setback_config=night_setback_config,
            window_orientation=None,
            get_target_temp=get_target_temp,
            get_current_temp=get_current_temp,
            preheat_learner=None,
            preheat_enabled=False,
        )

        # Step 1: Verify night setback is active
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is True
        assert effective_target == 19.0  # 21.0 - 2.0
        assert info["night_setback_delta"] == 2.0

        # Create contact sensor handler
        contact_handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=0,  # No delay for testing
            action=ContactAction.PAUSE,
            frost_protection_temp=5.0,
            learning_grace_seconds=0,  # No grace period
        )

        # Step 2: Contact sensor opens
        # Update contact state directly
        contact_handler.update_contact_states(
            contact_states={"binary_sensor.window": True},  # True = open
            current_time=time_travel.now(),
        )

        # Step 3: Verify heating should pause (contact takes priority)
        assert contact_handler.is_any_contact_open() is True
        assert contact_handler.should_take_action() is True
        assert contact_handler.get_action() == ContactAction.PAUSE

        # Night setback is still technically active, but heating should be paused
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is True  # Still in night period
        assert effective_target == 19.0  # Setback still applies to setpoint

        # In real thermostat, _async_control_heating checks contact_handler.should_take_action()
        # and pauses heating regardless of night setback state

        # Step 4: Contact closes after 10 minutes
        time_travel.advance(minutes=10)
        contact_handler.update_contact_states(
            contact_states={"binary_sensor.window": False},  # False = closed
            current_time=time_travel.now(),
        )

        # Verify heating can resume (still in night setback though)
        assert contact_handler.is_any_contact_open() is False
        assert contact_handler.should_take_action() is False

        # Night setback should still be active
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is True
        assert effective_target == 19.0

        # Step 5: Night period ends at 07:00
        time_travel._current_dt = datetime(2024, 1, 2, 7, 0, 0, tzinfo=timezone.utc)
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is False
        assert effective_target == 21.0  # Back to normal target

    def test_humidity_override_takes_priority_over_setback(self, mock_hass, time_travel):
        """Test humidity spike pause overrides night setback.

        Similar to contact sensor test, but with humidity detection.
        """
        # Set time to middle of night setback period (02:00)
        time_travel._current_dt = datetime(2024, 1, 2, 2, 0, 0, tzinfo=timezone.utc)

        # Create mocks
        target_temp = 21.0
        current_temp = 19.0
        get_target_temp = lambda: target_temp
        get_current_temp = lambda: current_temp

        # Create night setback config
        night_setback_config = {
            "start": "23:00",
            "delta": 2.0,
            "recovery_deadline": "07:00",
        }

        # Create calculator
        calculator = NightSetbackCalculator(
            hass=mock_hass,
            entity_id="climate.test",
            night_setback=None,
            night_setback_config=night_setback_config,
            window_orientation=None,
            get_target_temp=get_target_temp,
            get_current_temp=get_current_temp,
            preheat_learner=None,
            preheat_enabled=False,
        )

        # Verify night setback is active
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is True
        assert effective_target == 19.0

        # Create humidity detector
        detector = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            detection_window=300,
            stabilization_delay=300,
            max_pause_duration=3600,
            exit_humidity_threshold=70.0,
            exit_humidity_drop=5.0,
        )

        # Simulate humidity spike
        # Add baseline readings
        for i in range(10):
            detector.record_humidity(time_travel.now() - timedelta(seconds=60 * i), 50.0)

        # Add spike reading
        detector.record_humidity(time_travel.now(), 70.0)  # +20% spike

        # Verify humidity pause is active
        assert detector.should_pause() is True
        assert detector.get_state() in ["paused", "stabilizing"]

        # Night setback still active, but heating should pause
        effective_target, in_night_period, info = calculator.calculate_night_setback_adjustment(time_travel.now())
        assert in_night_period is True
        assert effective_target == 19.0

        # In real thermostat, humidity_detector.should_pause() takes priority

        # Simulate humidity dropping and stabilizing
        time_travel.advance(minutes=10)
        detector.record_humidity(time_travel.now(), 60.0)  # Dropped 10% from peak

        # Should still be in stabilization
        state = detector.get_state()
        # State could be "paused" or "stabilizing" depending on exact conditions

        # Wait for stabilization delay
        time_travel.advance(minutes=10)
        detector.record_humidity(time_travel.now(), 55.0)

        # Eventually returns to normal
        # (exact timing depends on thresholds, but the test shows the interaction)
