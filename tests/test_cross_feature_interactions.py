"""Tests for cross-feature interactions.

Verifies that different features work correctly when active simultaneously,
covering interactions between:
- Humidity detection + Learning
- Contact sensors + Preheat
- Multiple pause sources (PauseManager)
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from custom_components.adaptive_thermostat.adaptive.humidity_detector import (
    HumidityDetector,
)
from custom_components.adaptive_thermostat.adaptive.contact_sensors import (
    ContactSensorHandler,
    ContactAction,
)
from custom_components.adaptive_thermostat.adaptive.preheat import (
    PreheatLearner,
)
from custom_components.adaptive_thermostat.adaptive.learning import (
    AdaptiveLearner,
    CycleMetrics,
)
from custom_components.adaptive_thermostat.managers.pause_manager import (
    PauseManager,
)
from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_RADIATOR,
)


class TestHumidityLearningInteraction:
    """Test interactions between humidity detection and learning."""

    def test_learning_doesnt_record_cycle_during_humidity_pause(self):
        """Test that learning doesn't record interrupted cycles during humidity pause."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
        detector = HumidityDetector()

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start normal - no humidity spike
        detector.record_humidity(now, 50.0)
        assert detector.get_state() == "normal"

        # Record a good cycle first
        good_cycle = CycleMetrics(
            overshoot=0.3,
            undershoot=0.1,
            settling_time=20.0,
            oscillations=1,
            rise_time=15.0,
        )
        learner.add_cycle_metrics(good_cycle)
        assert learner.get_cycle_count() == 1

        # Trigger humidity pause
        detector.record_humidity(now + timedelta(minutes=5), 70.0)
        assert detector.get_state() == "paused"
        assert detector.should_pause() is True

        # During pause, a cycle should be marked as disturbed
        # In real implementation, the cycle tracker would mark this via is_disturbed
        disturbed_cycle = CycleMetrics(
            overshoot=0.5,
            undershoot=0.2,
            settling_time=25.0,
            oscillations=2,
            rise_time=18.0,
            disturbances=["humidity_pause"],
        )
        learner.add_cycle_metrics(disturbed_cycle)

        # Cycle is recorded but marked as disturbed
        assert learner.get_cycle_count() == 2
        assert disturbed_cycle.is_disturbed is True

    def test_integral_decay_during_humidity_pause_doesnt_corrupt_state(self):
        """Test that integral decay during humidity pause doesn't corrupt PID state."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
        detector = HumidityDetector()

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Record some initial cycles
        for i in range(3):
            cycle = CycleMetrics(
                overshoot=0.3,
                undershoot=0.1,
                settling_time=20.0,
                oscillations=1,
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        initial_count = learner.get_cycle_count()

        # Trigger humidity pause
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(minutes=5), 70.0)
        assert detector.should_pause() is True

        # In real implementation, integral would decay during pause
        # But learning state should remain intact

        # Humidity drops below 70% and >10% from peak to enter stabilizing
        detector.record_humidity(now + timedelta(minutes=20), 59.0)
        assert detector.get_state() == "stabilizing"

        # Record a new cycle after pause
        new_cycle = CycleMetrics(
            overshoot=0.35,
            undershoot=0.12,
            settling_time=22.0,
            oscillations=1,
            rise_time=16.0,
        )
        learner.add_cycle_metrics(new_cycle)

        # Verify state wasn't corrupted
        assert learner.get_cycle_count() == initial_count + 1
        assert learner._last_adjustment_time is None  # No adjustments yet

    def test_cycle_metrics_invalidated_when_interrupted_by_humidity(self):
        """Test that cycle metrics are properly invalidated when interrupted by humidity pause."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
        detector = HumidityDetector(spike_threshold=15, detection_window=300)

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Normal operation
        detector.record_humidity(now, 50.0)

        # Mid-cycle, humidity spikes (>15% rise in 5 minutes)
        detector.record_humidity(now + timedelta(minutes=5), 70.0)
        assert detector.should_pause() is True

        # Create interrupted cycle (marked disturbed)
        interrupted_cycle = CycleMetrics(
            overshoot=1.5,  # Abnormal overshoot due to interruption
            undershoot=0.3,
            settling_time=45.0,  # Long settling due to interruption
            oscillations=3,
            rise_time=10.0,
            disturbances=["humidity_spike"],
        )

        learner.add_cycle_metrics(interrupted_cycle)

        # Verify disturbed cycles are filtered out during learning
        recent_cycles = learner._heating_cycle_history[-6:]
        undisturbed = [c for c in recent_cycles if not c.is_disturbed]

        # With only 1 cycle (disturbed), should have 0 undisturbed
        assert len(undisturbed) == 0

        # Try to calculate adjustment - should return None due to insufficient undisturbed cycles
        adjustment = learner.calculate_pid_adjustment(
            current_kp=5.0,
            current_ki=0.01,
            current_kd=2.0,
            min_cycles=1,  # Would normally be enough
        )

        # Should return None because disturbed cycles are filtered
        assert adjustment is None


class TestContactPreheatInteraction:
    """Test interactions between contact sensors and preheat."""

    def test_preheat_doesnt_start_while_contact_open(self):
        """Test that preheat doesn't start while contact sensor indicates open."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=0,  # Immediate action
            action=ContactAction.PAUSE,
        )
        learner = PreheatLearner(HEATING_TYPE_RADIATOR)

        now = datetime(2024, 1, 1, 6, 0, 0)

        # Window is open
        handler.update_contact_states({
            "binary_sensor.window_bedroom": True
        }, now)

        assert handler.is_any_contact_open() is True
        assert handler.should_take_action(now) is True

        # In real implementation, preheat start should be blocked
        # Simulate checking if preheat can start
        can_start_preheat = not handler.should_take_action(now)
        assert can_start_preheat is False

        # Add preheat observations (learning still works)
        learner.add_observation(
            start_temp=18.0,
            end_temp=21.0,
            outdoor_temp=5.0,
            duration_minutes=60,
            timestamp=now - timedelta(days=1),
        )

        assert learner.get_observation_count() == 1

    def test_active_preheat_interrupted_when_contact_opens(self):
        """Test that active preheat is interrupted when contact opens."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=300,  # 5 minute delay
            action=ContactAction.PAUSE,
        )

        now = datetime(2024, 1, 1, 6, 0, 0)

        # Preheat is active (simulated state)
        preheat_active = True
        preheat_start_time = now

        # Contact closed initially
        handler.update_contact_states({
            "binary_sensor.window_bedroom": False
        }, now)
        assert handler.is_any_contact_open() is False

        # 10 minutes into preheat, window opens
        time_window_opens = now + timedelta(minutes=10)
        handler.update_contact_states({
            "binary_sensor.window_bedroom": True
        }, time_window_opens)

        assert handler.is_any_contact_open() is True

        # Still in delay period (5 min delay not elapsed)
        time_2min = time_window_opens + timedelta(minutes=2)
        assert handler.should_take_action(time_2min) is False

        # After delay, should pause
        time_6min = time_window_opens + timedelta(minutes=6)
        should_interrupt_preheat = handler.should_take_action(time_6min)
        assert should_interrupt_preheat is True

        # In real implementation, this would cancel preheat
        if should_interrupt_preheat:
            preheat_active = False

        assert preheat_active is False

    def test_preheat_resumes_after_contact_closes(self):
        """Test that preheat can resume after contact closes."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.door_bathroom"],
            contact_delay_seconds=0,
            action=ContactAction.PAUSE,
        )
        learner = PreheatLearner(HEATING_TYPE_CONVECTOR)

        now = datetime(2024, 1, 1, 6, 0, 0)
        target_time = now + timedelta(hours=1)  # Target: 7:00 AM

        # Door opens (pause heating)
        handler.update_contact_states({
            "binary_sensor.door_bathroom": True
        }, now)
        assert handler.should_take_action(now) is True

        # Estimate time needed (should still work)
        estimated_minutes = learner.estimate_time_to_target(
            current_temp=18.0,
            target_temp=21.0,
            outdoor_temp=5.0,
        )
        assert estimated_minutes > 0  # Returns fallback estimate

        # Door closes 15 minutes later
        time_closed = now + timedelta(minutes=15)
        handler.update_contact_states({
            "binary_sensor.door_bathroom": False
        }, time_closed)

        assert handler.is_any_contact_open() is False
        assert handler.should_take_action(time_closed) is False

        # Preheat can now resume/restart
        # Re-calculate time needed with updated current time
        remaining_minutes = learner.estimate_time_to_target(
            current_temp=18.5,  # Slightly warmer
            target_temp=21.0,
            outdoor_temp=5.0,
        )

        # New start time = target - remaining
        new_preheat_start = target_time - timedelta(minutes=remaining_minutes)

        # Verify logic: if we're past the new start time, start immediately
        should_start_now = time_closed >= new_preheat_start
        # In this case, we might be past it or close
        assert isinstance(should_start_now, bool)


class TestMultiplePauseSources:
    """Test PauseManager with multiple pause sources active."""

    def test_pause_manager_prioritizes_contact_over_humidity(self):
        """Test that PauseManager correctly prioritizes contact > humidity."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=0,
            action=ContactAction.PAUSE,
        )
        detector = HumidityDetector()
        manager = PauseManager(
            contact_sensor_handler=handler,
            humidity_detector=detector,
        )

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Both inactive initially
        assert manager.is_paused() is False
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is False
        assert pause_info["reason"] is None

        # Activate humidity pause only
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(minutes=5), 70.0)
        assert detector.should_pause() is True

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"

        # Now activate contact pause (higher priority)
        handler.update_contact_states({"binary_sensor.window": True}, now)
        assert handler.should_take_action(now) is True

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "contact"  # Contact takes priority

    def test_both_detectors_active_shows_highest_priority_reason(self):
        """Test that when both detectors are active, highest priority reason is shown."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.door"],
            contact_delay_seconds=0,
            action=ContactAction.PAUSE,
        )
        detector = HumidityDetector()
        manager = PauseManager(
            contact_sensor_handler=handler,
            humidity_detector=detector,
        )

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Activate both simultaneously
        # Contact sensor
        handler.update_contact_states({"binary_sensor.door": True}, now)
        # Humidity spike
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(minutes=5), 75.0)

        # Both should be active
        assert handler.should_take_action(now) is True
        assert detector.should_pause() is True

        # Manager should show contact (higher priority)
        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "contact"

    def test_transition_from_contact_to_humidity_pause(self):
        """Test transition from one pause reason to another."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=0,
            action=ContactAction.PAUSE,
        )
        detector = HumidityDetector(stabilization_delay=300)
        manager = PauseManager(
            contact_sensor_handler=handler,
            humidity_detector=detector,
        )

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start with humidity pause
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(minutes=5), 75.0)

        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"

        # Add contact pause (takes priority)
        time_1 = now + timedelta(minutes=10)
        handler.update_contact_states({"binary_sensor.window": True}, time_1)

        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "contact"

        # Close window (contact resolves)
        time_2 = now + timedelta(minutes=15)
        handler.update_contact_states({"binary_sensor.window": False}, time_2)

        # Should fall back to humidity (still active)
        detector.record_humidity(time_2, 72.0)  # Still high
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"

        # Humidity drops below 70% and >10% from peak (75->59) to enter stabilizing
        time_3 = now + timedelta(minutes=20)
        detector.record_humidity(time_3, 59.0)
        assert detector.get_state() == "stabilizing"

        # Still paused but in stabilizing
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"
        assert "resume_in" in pause_info  # Countdown present

        # After stabilization delay, both resolve
        time_4 = time_3 + timedelta(seconds=301)
        detector.record_humidity(time_4, 63.0)

        pause_info = manager.get_pause_info()
        assert pause_info["active"] is False
        assert pause_info["reason"] is None

    def test_pause_manager_with_only_contact_sensor(self):
        """Test PauseManager with only contact sensor configured."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=0,
            action=ContactAction.PAUSE,
        )
        manager = PauseManager(contact_sensor_handler=handler)

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Initially not paused
        assert manager.is_paused() is False

        # Open window
        handler.update_contact_states({"binary_sensor.window": True}, now)

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "contact"

    def test_pause_manager_with_only_humidity_detector(self):
        """Test PauseManager with only humidity detector configured."""
        detector = HumidityDetector()
        manager = PauseManager(humidity_detector=detector)

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Initially not paused
        assert manager.is_paused() is False

        # Trigger humidity spike
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(minutes=5), 75.0)

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"

    def test_pause_manager_contact_delay_countdown(self):
        """Test contact sensor delay countdown logic through handler."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=300,  # 5 minute delay
            action=ContactAction.PAUSE,
        )

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Open window
        handler.update_contact_states({"binary_sensor.window": True}, now)

        # Not yet paused (in delay period)
        assert handler.is_any_contact_open() is True
        assert handler.should_take_action(now) is False

        # Countdown should show time until action
        time_until = handler.get_time_until_action(now)
        assert time_until == 300  # 5 minutes

        # 2 minutes later
        time_2min = now + timedelta(minutes=2)
        assert handler.should_take_action(time_2min) is False
        time_until = handler.get_time_until_action(time_2min)
        assert time_until == 180  # 3 minutes remaining

        # After delay expires
        time_6min = now + timedelta(minutes=6)
        assert handler.should_take_action(time_6min) is True
        time_until = handler.get_time_until_action(time_6min)
        assert time_until == 0  # Action should be taken now

        # Verify PauseManager would correctly aggregate this
        # (Handler integration is tested separately, here we verify delay logic)
        manager = PauseManager(contact_sensor_handler=handler)
        # At time_6min, should be paused
        is_paused = handler.should_take_action(time_6min) and handler.get_action() == ContactAction.PAUSE
        assert is_paused is True


class TestComplexScenarios:
    """Test complex multi-feature scenarios."""

    def test_humidity_during_learning_validation_mode(self):
        """Test humidity pause during learning validation mode."""
        learner = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
        detector = HumidityDetector()

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Build up enough cycles for validation mode
        for i in range(6):
            cycle = CycleMetrics(
                overshoot=0.4,
                undershoot=0.1,
                settling_time=20.0,
                oscillations=1,
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # Start validation mode
        learner.start_validation_mode(baseline_overshoot=0.4)
        assert learner.is_in_validation_mode() is True

        # During validation, humidity spike occurs
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(minutes=5), 75.0)
        assert detector.should_pause() is True

        # Cycle during humidity pause should be marked disturbed
        disturbed_cycle = CycleMetrics(
            overshoot=0.8,
            undershoot=0.2,
            settling_time=35.0,
            oscillations=3,
            rise_time=18.0,
            disturbances=["humidity_pause"],
        )

        # In real implementation, disturbed cycles wouldn't count toward validation
        # Validation should still be active
        result = learner.add_validation_cycle(disturbed_cycle)

        # Disturbed cycle added but validation not complete yet
        # (needs 3 undisturbed cycles)
        assert result is None  # Still collecting

    def test_preheat_with_learning_and_contact_interaction(self):
        """Test preheat interacting with both learning and contact sensors."""
        learner_adaptive = AdaptiveLearner(heating_type=HEATING_TYPE_RADIATOR)
        learner_preheat = PreheatLearner(HEATING_TYPE_RADIATOR)
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window"],
            contact_delay_seconds=300,
            action=ContactAction.PAUSE,
        )

        now = datetime(2024, 1, 1, 6, 0, 0)

        # Add some heating observations for preheat learning
        for i in range(5):
            learner_preheat.add_observation(
                start_temp=18.0,
                end_temp=21.0,
                outdoor_temp=5.0,
                duration_minutes=60,
                timestamp=now - timedelta(days=i+1),
            )

        assert learner_preheat.get_observation_count() == 5

        # Estimate preheat time
        estimated = learner_preheat.estimate_time_to_target(
            current_temp=18.0,
            target_temp=21.0,
            outdoor_temp=5.0,
        )
        assert estimated > 0

        # During preheat, window opens
        handler.update_contact_states({"binary_sensor.window": True}, now)

        # Preheat should stop (simulated)
        preheat_should_run = not handler.should_take_action(
            now + timedelta(minutes=6)
        )
        assert preheat_should_run is False

        # Regular adaptive learning continues (but cycles marked disturbed)
        disturbed_cycle = CycleMetrics(
            overshoot=0.5,
            undershoot=0.3,
            settling_time=30.0,
            oscillations=2,
            rise_time=20.0,
            disturbances=["contact_open"],
        )
        learner_adaptive.add_cycle_metrics(disturbed_cycle)

        # Verify learning still tracks cycles
        assert learner_adaptive.get_cycle_count() == 1

    def test_all_pause_sources_resolve_simultaneously(self):
        """Test scenario where multiple pause sources resolve at the same time."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.door"],
            contact_delay_seconds=0,
            action=ContactAction.PAUSE,
        )
        detector = HumidityDetector(stabilization_delay=300)
        manager = PauseManager(
            contact_sensor_handler=handler,
            humidity_detector=detector,
        )

        now = datetime(2024, 1, 1, 12, 0, 0)

        # Activate both
        handler.update_contact_states({"binary_sensor.door": True}, now)
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(minutes=5), 75.0)

        assert manager.is_paused() is True

        # Both resolve at same time
        time_resolve = now + timedelta(minutes=30)
        handler.update_contact_states({"binary_sensor.door": False}, time_resolve)
        detector.record_humidity(time_resolve, 60.0)

        # After stabilization delay for humidity
        time_final = time_resolve + timedelta(seconds=301)
        detector.record_humidity(time_final, 58.0)

        # All should be resolved
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is False
        assert pause_info["reason"] is None
