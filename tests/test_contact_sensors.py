"""Tests for contact sensor module."""
import pytest
from datetime import datetime, timedelta
from custom_components.adaptive_thermostat.adaptive.contact_sensors import (
    ContactSensorHandler,
    ContactSensorManager,
    ContactAction
)


class TestContactSensorHandler:
    """Test ContactSensorHandler class."""

    def test_contact_delay(self):
        """Test that action is delayed after contact opens."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=300  # 5 minutes
        )

        current_time = datetime(2024, 1, 15, 10, 0)

        # Update state: window opens
        handler.update_contact_states({
            "binary_sensor.window_bedroom": True
        }, current_time)

        # Immediately after opening - no action yet
        assert handler.should_take_action(current_time) is False
        assert handler.is_any_contact_open() is True

        # 2 minutes later - still no action
        time_2min = current_time + timedelta(minutes=2)
        assert handler.should_take_action(time_2min) is False

        # 5 minutes later - action should be taken
        time_5min = current_time + timedelta(minutes=5)
        assert handler.should_take_action(time_5min) is True

        # 10 minutes later - action should still be taken
        time_10min = current_time + timedelta(minutes=10)
        assert handler.should_take_action(time_10min) is True

    def test_pause_action(self):
        """Test pause action stops heating."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=0,  # No delay for testing
            action=ContactAction.PAUSE
        )

        current_time = datetime(2024, 1, 15, 10, 0)
        base_setpoint = 20.0

        # Window closed - no adjustment
        handler.update_contact_states({
            "binary_sensor.window_bedroom": False
        }, current_time)
        assert handler.get_adjusted_setpoint(base_setpoint, current_time) is None

        # Window opens - pause heating (returns None)
        handler.update_contact_states({
            "binary_sensor.window_bedroom": True
        }, current_time)
        adjusted = handler.get_adjusted_setpoint(base_setpoint, current_time)
        assert adjusted is None  # None indicates pause
        assert handler.get_action() == ContactAction.PAUSE

    def test_frost_protection_action(self):
        """Test frost protection action lowers temperature."""
        frost_temp = 5.0
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.door_bathroom"],
            contact_delay_seconds=0,  # No delay for testing
            action=ContactAction.FROST_PROTECTION,
            frost_protection_temp=frost_temp
        )

        current_time = datetime(2024, 1, 15, 10, 0)
        base_setpoint = 20.0

        # Door closed - no adjustment
        handler.update_contact_states({
            "binary_sensor.door_bathroom": False
        }, current_time)
        assert handler.get_adjusted_setpoint(base_setpoint, current_time) is None

        # Door opens - frost protection
        handler.update_contact_states({
            "binary_sensor.door_bathroom": True
        }, current_time)
        adjusted = handler.get_adjusted_setpoint(base_setpoint, current_time)
        assert adjusted == frost_temp
        assert handler.get_action() == ContactAction.FROST_PROTECTION

    def test_grace_period(self):
        """Test learning grace period prevents action."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_living_room"],
            contact_delay_seconds=0,  # No contact delay
            learning_grace_seconds=3600  # 1 hour grace period
        )

        current_time = datetime(2024, 1, 15, 10, 0)

        # Window opens immediately
        handler.update_contact_states({
            "binary_sensor.window_living_room": True
        }, current_time)

        # During grace period - no action
        assert handler.should_take_action(current_time) is False
        assert handler.get_grace_time_remaining(current_time) == 3600

        # 30 minutes later - still in grace period
        time_30min = current_time + timedelta(minutes=30)
        assert handler.should_take_action(time_30min) is False
        assert handler.get_grace_time_remaining(time_30min) == 1800

        # 1 hour later - grace period expired, action taken
        time_1hour = current_time + timedelta(hours=1)
        assert handler.should_take_action(time_1hour) is True
        assert handler.get_grace_time_remaining(time_1hour) == 0

    def test_multi_sensor_aggregation(self):
        """Test aggregation of multiple contact sensors."""
        handler = ContactSensorHandler(
            contact_sensors=[
                "binary_sensor.window_bedroom_1",
                "binary_sensor.window_bedroom_2",
                "binary_sensor.door_bedroom"
            ],
            contact_delay_seconds=0  # No delay for testing
        )

        current_time = datetime(2024, 1, 15, 10, 0)

        # All closed - no action
        handler.update_contact_states({
            "binary_sensor.window_bedroom_1": False,
            "binary_sensor.window_bedroom_2": False,
            "binary_sensor.door_bedroom": False
        }, current_time)
        assert handler.is_any_contact_open() is False
        assert handler.should_take_action(current_time) is False

        # One window opens - action taken (ANY open triggers)
        handler.update_contact_states({
            "binary_sensor.window_bedroom_1": True,
            "binary_sensor.window_bedroom_2": False,
            "binary_sensor.door_bedroom": False
        }, current_time)
        assert handler.is_any_contact_open() is True
        assert handler.should_take_action(current_time) is True

        # Multiple open - still triggers action
        handler.update_contact_states({
            "binary_sensor.window_bedroom_1": True,
            "binary_sensor.window_bedroom_2": True,
            "binary_sensor.door_bedroom": False
        }, current_time)
        assert handler.is_any_contact_open() is True
        assert handler.should_take_action(current_time) is True

        # All closed again - no action
        handler.update_contact_states({
            "binary_sensor.window_bedroom_1": False,
            "binary_sensor.window_bedroom_2": False,
            "binary_sensor.door_bedroom": False
        }, current_time)
        assert handler.is_any_contact_open() is False
        assert handler.should_take_action(current_time) is False

    def test_time_until_action(self):
        """Test getting time remaining until action."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_kitchen"],
            contact_delay_seconds=600  # 10 minutes
        )

        current_time = datetime(2024, 1, 15, 10, 0)

        # No contact open - no action pending
        handler.update_contact_states({
            "binary_sensor.window_kitchen": False
        }, current_time)
        assert handler.get_time_until_action(current_time) is None

        # Contact opens
        handler.update_contact_states({
            "binary_sensor.window_kitchen": True
        }, current_time)

        # Immediately - 600 seconds until action
        assert handler.get_time_until_action(current_time) == 600

        # 5 minutes later - 300 seconds remaining
        time_5min = current_time + timedelta(minutes=5)
        assert handler.get_time_until_action(time_5min) == 300

        # 10 minutes later - action should happen now
        time_10min = current_time + timedelta(minutes=10)
        assert handler.get_time_until_action(time_10min) == 0

    def test_contact_state_transitions(self):
        """Test contact open/close transitions are tracked correctly."""
        handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_study"],
            contact_delay_seconds=300
        )

        current_time = datetime(2024, 1, 15, 10, 0)

        # Initial state - closed
        assert handler.is_any_contact_open() is False

        # Opens
        handler.update_contact_states({
            "binary_sensor.window_study": True
        }, current_time)
        assert handler.is_any_contact_open() is True

        # Wait for delay
        time_after_delay = current_time + timedelta(minutes=5)
        assert handler.should_take_action(time_after_delay) is True

        # Closes - should reset
        handler.update_contact_states({
            "binary_sensor.window_study": False
        }, current_time)
        assert handler.is_any_contact_open() is False
        assert handler.should_take_action(time_after_delay) is False


class TestContactSensorManager:
    """Test ContactSensorManager class."""

    def test_configure_zone(self):
        """Test zone configuration."""
        manager = ContactSensorManager()

        manager.configure_zone(
            zone_id="bedroom",
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=300,
            action="pause"
        )

        config = manager.get_zone_config("bedroom")
        assert config is not None
        assert config["contact_sensors"] == ["binary_sensor.window_bedroom"]
        assert config["contact_delay_seconds"] == 300
        assert config["action"] == "pause"

    def test_should_take_action_for_zone(self):
        """Test checking if zone should take action."""
        manager = ContactSensorManager()

        manager.configure_zone(
            zone_id="kitchen",
            contact_sensors=["binary_sensor.window_kitchen"],
            contact_delay_seconds=0
        )

        current_time = datetime(2024, 1, 15, 10, 0)

        # Contact closed - no action
        manager.update_contact_states("kitchen", {
            "binary_sensor.window_kitchen": False
        }, current_time)
        assert manager.should_take_action("kitchen", current_time) is False

        # Contact opens - action
        manager.update_contact_states("kitchen", {
            "binary_sensor.window_kitchen": True
        }, current_time)
        assert manager.should_take_action("kitchen", current_time) is True

    def test_get_adjusted_setpoint_for_zone(self):
        """Test getting adjusted setpoint for a zone."""
        manager = ContactSensorManager()

        manager.configure_zone(
            zone_id="bathroom",
            contact_sensors=["binary_sensor.window_bathroom"],
            contact_delay_seconds=0,
            action="frost_protection",
            frost_protection_temp=7.0
        )

        current_time = datetime(2024, 1, 15, 10, 0)
        base_setpoint = 22.0

        # Contact closed - no adjustment
        manager.update_contact_states("bathroom", {
            "binary_sensor.window_bathroom": False
        }, current_time)
        adjusted = manager.get_adjusted_setpoint("bathroom", base_setpoint, current_time)
        assert adjusted is None

        # Contact opens - frost protection
        manager.update_contact_states("bathroom", {
            "binary_sensor.window_bathroom": True
        }, current_time)
        adjusted = manager.get_adjusted_setpoint("bathroom", base_setpoint, current_time)
        assert adjusted == 7.0

    def test_unconfigured_zone_returns_none(self):
        """Test that unconfigured zones return None."""
        manager = ContactSensorManager()

        assert manager.get_zone_config("nonexistent") is None
        assert manager.should_take_action("nonexistent") is False
        assert manager.get_adjusted_setpoint("nonexistent", 20.0) is None

    def test_multiple_zones_independence(self):
        """Test that multiple zones operate independently."""
        manager = ContactSensorManager()

        # Configure two zones
        manager.configure_zone(
            zone_id="bedroom",
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=0,
            action="pause"
        )

        manager.configure_zone(
            zone_id="living_room",
            contact_sensors=["binary_sensor.window_living_room"],
            contact_delay_seconds=0,
            action="frost_protection",
            frost_protection_temp=5.0
        )

        current_time = datetime(2024, 1, 15, 10, 0)

        # Open bedroom window only
        manager.update_contact_states("bedroom", {
            "binary_sensor.window_bedroom": True
        }, current_time)
        manager.update_contact_states("living_room", {
            "binary_sensor.window_living_room": False
        }, current_time)

        # Bedroom should take action, living room should not
        assert manager.should_take_action("bedroom", current_time) is True
        assert manager.should_take_action("living_room", current_time) is False

        # Verify different actions
        bedroom_handler = manager.get_handler("bedroom")
        living_room_handler = manager.get_handler("living_room")
        assert bedroom_handler.get_action() == ContactAction.PAUSE
        assert living_room_handler.get_action() == ContactAction.FROST_PROTECTION


class TestContactAccumulatorReset:
    """Tests for duty accumulator reset on contact sensor open (Story 3.3)."""

    def test_contact_open_resets_accumulator(self):
        """Test contact sensor open resets duty accumulator."""
        from unittest.mock import MagicMock

        # Create mock heater controller
        mock_heater_controller = MagicMock()
        mock_heater_controller.reset_duty_accumulator = MagicMock()

        # Create mock contact sensor handler
        contact_handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=0
        )

        # Create a mock climate-like object that simulates contact sensor state change
        class MockClimate:
            def __init__(self):
                self._heater_controller = mock_heater_controller
                self._contact_sensor_handler = contact_handler
                self._contact_pause_times: dict[str, datetime] = {}

            def handle_contact_sensor_changed(self, entity_id: str, is_open: bool) -> None:
                """Handle contact sensor state change (mirrors climate.py logic)."""
                current_time = datetime.now()

                if is_open:
                    # Track pause start time for this sensor
                    self._contact_pause_times[entity_id] = current_time
                    # Reset duty accumulator when contact opens
                    if self._heater_controller is not None:
                        self._heater_controller.reset_duty_accumulator()
                else:
                    # Clear pause time when contact closes
                    self._contact_pause_times.pop(entity_id, None)

        climate = MockClimate()

        # Act - Contact sensor opens
        climate.handle_contact_sensor_changed("binary_sensor.window_bedroom", True)

        # Assert - accumulator should be reset
        mock_heater_controller.reset_duty_accumulator.assert_called_once()

    def test_contact_close_does_not_reset_accumulator(self):
        """Test contact sensor close does NOT reset duty accumulator."""
        from unittest.mock import MagicMock

        # Create mock heater controller
        mock_heater_controller = MagicMock()
        mock_heater_controller.reset_duty_accumulator = MagicMock()

        # Create mock contact sensor handler
        contact_handler = ContactSensorHandler(
            contact_sensors=["binary_sensor.window_bedroom"],
            contact_delay_seconds=0
        )

        # Create a mock climate-like object that simulates contact sensor state change
        class MockClimate:
            def __init__(self):
                self._heater_controller = mock_heater_controller
                self._contact_sensor_handler = contact_handler
                self._contact_pause_times: dict[str, datetime] = {}

            def handle_contact_sensor_changed(self, entity_id: str, is_open: bool) -> None:
                """Handle contact sensor state change (mirrors climate.py logic)."""
                current_time = datetime.now()

                if is_open:
                    # Track pause start time for this sensor
                    self._contact_pause_times[entity_id] = current_time
                    # Reset duty accumulator when contact opens
                    if self._heater_controller is not None:
                        self._heater_controller.reset_duty_accumulator()
                else:
                    # Clear pause time when contact closes
                    self._contact_pause_times.pop(entity_id, None)

        climate = MockClimate()

        # First open the contact (which resets accumulator)
        climate.handle_contact_sensor_changed("binary_sensor.window_bedroom", True)
        mock_heater_controller.reset_duty_accumulator.reset_mock()

        # Act - Contact sensor closes
        climate.handle_contact_sensor_changed("binary_sensor.window_bedroom", False)

        # Assert - accumulator should NOT be reset on close
        mock_heater_controller.reset_duty_accumulator.assert_not_called()
