"""Tests for PauseManager."""
import pytest
from unittest.mock import MagicMock
from custom_components.adaptive_thermostat.managers.pause_manager import PauseManager
from custom_components.adaptive_thermostat.adaptive.contact_sensors import ContactAction


class TestPauseManagerBasic:
    """Test basic PauseManager functionality."""

    def test_no_detectors_not_paused(self):
        """Test that with no detectors, pause is not active."""
        manager = PauseManager()
        assert manager.is_paused() is False

        pause_info = manager.get_pause_info()
        assert pause_info["active"] is False
        assert pause_info["reason"] is None

    def test_contact_sensor_pause(self):
        """Test contact sensor pause detection."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE

        manager = PauseManager(contact_sensor_handler=contact_handler)

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "contact"

    def test_contact_sensor_frost_protection_not_pause(self):
        """Test contact sensor frost protection doesn't trigger pause."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.FROST_PROTECTION

        manager = PauseManager(contact_sensor_handler=contact_handler)

        # Frost protection is not a pause
        assert manager.is_paused() is False
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is False

    def test_humidity_pause(self):
        """Test humidity detector pause detection."""
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_time_until_resume.return_value = None

        manager = PauseManager(humidity_detector=humidity_detector)

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"

    def test_humidity_pause_with_countdown(self):
        """Test humidity pause with resume countdown."""
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_time_until_resume.return_value = 120

        manager = PauseManager(humidity_detector=humidity_detector)

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"
        assert pause_info["resume_in"] == 120


class TestPauseManagerPriority:
    """Test priority handling when multiple detectors are active."""

    def test_contact_priority_over_humidity(self):
        """Test that contact sensor pause takes priority over humidity."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE
        contact_handler.is_any_contact_open.return_value = True

        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True

        manager = PauseManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "contact"  # Contact has priority

    def test_humidity_when_no_contact_pause(self):
        """Test humidity pause is reported when contact isn't pausing."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = False
        contact_handler.is_any_contact_open.return_value = False

        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_time_until_resume.return_value = 60

        manager = PauseManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        assert manager.is_paused() is True
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is True
        assert pause_info["reason"] == "humidity"
        assert pause_info["resume_in"] == 60


class TestPauseManagerContactDelay:
    """Test contact sensor delay countdown handling."""

    def test_contact_open_but_not_paused_shows_countdown(self):
        """Test that contact open but not yet paused shows countdown."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = 180

        manager = PauseManager(contact_sensor_handler=contact_handler)

        assert manager.is_paused() is False
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is False
        assert pause_info["reason"] is None
        assert pause_info["resume_in"] == 180

    def test_contact_delay_zero_not_shown(self):
        """Test that zero or None countdown isn't included."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = 0

        manager = PauseManager(contact_sensor_handler=contact_handler)

        pause_info = manager.get_pause_info()
        assert "resume_in" not in pause_info

    def test_contact_closed_no_info(self):
        """Test that closed contact returns no pause info."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = False
        contact_handler.should_take_action.return_value = False

        manager = PauseManager(contact_sensor_handler=contact_handler)

        assert manager.is_paused() is False
        pause_info = manager.get_pause_info()
        assert pause_info["active"] is False
        assert pause_info["reason"] is None
        assert "resume_in" not in pause_info


class TestPauseManagerAggregation:
    """Test that PauseManager properly aggregates multiple sources."""

    def test_only_queries_contact_when_it_pauses(self):
        """Test that humidity isn't checked when contact is pausing."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE

        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True

        manager = PauseManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        manager.get_pause_info()

        # Humidity should not be checked when contact is pausing
        humidity_detector.should_pause.assert_not_called()

    def test_checks_humidity_when_contact_delay(self):
        """Test humidity is checked when contact is in delay period."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = 60

        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = False

        manager = PauseManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        # Contact delay takes priority, so humidity shouldn't be checked
        pause_info = manager.get_pause_info()
        assert pause_info["reason"] is None
        assert pause_info["resume_in"] == 60

        # Humidity should not be checked when contact delay is active
        humidity_detector.should_pause.assert_not_called()
