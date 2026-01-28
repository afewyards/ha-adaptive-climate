"""Tests for StatusManager."""
import pytest
from unittest.mock import MagicMock
from custom_components.adaptive_thermostat.managers.status_manager import StatusManager
from custom_components.adaptive_thermostat.adaptive.contact_sensors import ContactAction


class TestStatusManagerBasic:
    """Test basic StatusManager functionality."""

    def test_no_detectors_not_paused(self):
        """Test that with no detectors, pause is not active."""
        manager = StatusManager()
        assert manager.is_paused() is False

        status_info = manager.get_status_info()
        assert status_info["active"] is False
        assert status_info["reason"] is None

    def test_contact_sensor_pause(self):
        """Test contact sensor pause detection."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE

        manager = StatusManager(contact_sensor_handler=contact_handler)

        assert manager.is_paused() is True
        status_info = manager.get_status_info()
        assert status_info["active"] is True
        assert status_info["reason"] == "contact"

    def test_contact_sensor_frost_protection_not_pause(self):
        """Test contact sensor frost protection doesn't trigger pause."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.FROST_PROTECTION

        manager = StatusManager(contact_sensor_handler=contact_handler)

        # Frost protection is not a pause
        assert manager.is_paused() is False
        status_info = manager.get_status_info()
        assert status_info["active"] is False

    def test_humidity_pause(self):
        """Test humidity detector pause detection."""
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_time_until_resume.return_value = None

        manager = StatusManager(humidity_detector=humidity_detector)

        assert manager.is_paused() is True
        status_info = manager.get_status_info()
        assert status_info["active"] is True
        assert status_info["reason"] == "humidity"

    def test_humidity_pause_with_countdown(self):
        """Test humidity pause with resume countdown."""
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_time_until_resume.return_value = 120

        manager = StatusManager(humidity_detector=humidity_detector)

        assert manager.is_paused() is True
        status_info = manager.get_status_info()
        assert status_info["active"] is True
        assert status_info["reason"] == "humidity"
        assert status_info["resume_in"] == 120


class TestStatusManagerPriority:
    """Test priority handling when multiple detectors are active."""

    def test_contact_priority_over_humidity(self):
        """Test that contact sensor pause takes priority over humidity."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE
        contact_handler.is_any_contact_open.return_value = True

        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True

        manager = StatusManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        assert manager.is_paused() is True
        status_info = manager.get_status_info()
        assert status_info["active"] is True
        assert status_info["reason"] == "contact"  # Contact has priority

    def test_humidity_when_no_contact_pause(self):
        """Test humidity pause is reported when contact isn't pausing."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = False
        contact_handler.is_any_contact_open.return_value = False

        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_time_until_resume.return_value = 60

        manager = StatusManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        assert manager.is_paused() is True
        status_info = manager.get_status_info()
        assert status_info["active"] is True
        assert status_info["reason"] == "humidity"
        assert status_info["resume_in"] == 60


class TestStatusManagerContactDelay:
    """Test contact sensor delay countdown handling."""

    def test_contact_open_but_not_paused_shows_countdown(self):
        """Test that contact open but not yet paused shows countdown."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = 180

        manager = StatusManager(contact_sensor_handler=contact_handler)

        assert manager.is_paused() is False
        status_info = manager.get_status_info()
        assert status_info["active"] is False
        assert status_info["reason"] is None
        assert status_info["resume_in"] == 180

    def test_contact_delay_zero_not_shown(self):
        """Test that zero or None countdown isn't included."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = 0

        manager = StatusManager(contact_sensor_handler=contact_handler)

        status_info = manager.get_status_info()
        assert "resume_in" not in status_info

    def test_contact_closed_no_info(self):
        """Test that closed contact returns no pause info."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = False
        contact_handler.should_take_action.return_value = False

        manager = StatusManager(contact_sensor_handler=contact_handler)

        assert manager.is_paused() is False
        status_info = manager.get_status_info()
        assert status_info["active"] is False
        assert status_info["reason"] is None
        assert "resume_in" not in status_info


class TestStatusManagerAggregation:
    """Test that StatusManager properly aggregates multiple sources."""

    def test_only_queries_contact_when_it_pauses(self):
        """Test that humidity isn't checked when contact is pausing."""
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE

        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True

        manager = StatusManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        manager.get_status_info()

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

        manager = StatusManager(
            contact_sensor_handler=contact_handler,
            humidity_detector=humidity_detector,
        )

        # Contact delay takes priority, so humidity shouldn't be checked
        status_info = manager.get_status_info()
        assert status_info["reason"] is None
        assert status_info["resume_in"] == 60

        # Humidity should not be checked when contact delay is active
        humidity_detector.should_pause.assert_not_called()


class TestStatusManagerNightSetback:
    """Test night setback integration in StatusManager."""

    def _make_night_setback_controller(self, in_night=True, delta=-2.0, end="07:00",
                                        grace_period=False, grace_until=None):
        ctrl = MagicMock()
        info = {
            "night_setback_active": in_night,
            "night_setback_delta": delta,
            "night_setback_end": end,
        }
        ctrl.calculate_night_setback_adjustment.return_value = (20.0 if in_night else 22.0, in_night, info)
        ctrl.in_learning_grace_period = grace_period
        ctrl.learning_grace_until = grace_until
        return ctrl

    def test_night_setback_active_shown_in_status(self):
        """Night setback active → reason=night_setback with delta/end."""
        manager = StatusManager()
        manager.set_night_setback_controller(self._make_night_setback_controller())

        info = manager.get_status_info()
        assert info["active"] is True
        assert info["reason"] == "night_setback"
        assert info["delta"] == -2.0
        assert info["end"] == "07:00"
        assert "learning_paused" not in info

    def test_night_setback_inactive_no_status(self):
        """Night setback inactive → default status."""
        manager = StatusManager()
        manager.set_night_setback_controller(
            self._make_night_setback_controller(in_night=False)
        )

        info = manager.get_status_info()
        assert info["active"] is False
        assert info["reason"] is None

    def test_night_setback_with_learning_grace(self):
        """Night setback active + learning grace → learning_paused fields."""
        from datetime import datetime
        grace_until = datetime(2024, 1, 1, 8, 0, 0)
        manager = StatusManager()
        manager.set_night_setback_controller(
            self._make_night_setback_controller(grace_period=True, grace_until=grace_until)
        )

        info = manager.get_status_info()
        assert info["active"] is True
        assert info["reason"] == "night_setback"
        assert info["learning_paused"] is True
        assert info["learning_resumes"] == "08:00"

    def test_learning_grace_without_night_setback(self):
        """Learning grace active but not in night period → learning_paused only."""
        from datetime import datetime
        grace_until = datetime(2024, 1, 1, 8, 0, 0)
        manager = StatusManager()
        manager.set_night_setback_controller(
            self._make_night_setback_controller(in_night=False, grace_period=True, grace_until=grace_until)
        )

        info = manager.get_status_info()
        assert info["active"] is False
        assert info["reason"] is None
        assert info["learning_paused"] is True
        assert info["learning_resumes"] == "08:00"

    def test_contact_priority_over_night_setback(self):
        """Contact pause takes priority over night setback."""
        contact_handler = MagicMock()
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE
        contact_handler.is_any_contact_open.return_value = True

        manager = StatusManager(contact_sensor_handler=contact_handler)
        manager.set_night_setback_controller(self._make_night_setback_controller())

        info = manager.get_status_info()
        assert info["active"] is True
        assert info["reason"] == "contact"

    def test_humidity_priority_over_night_setback(self):
        """Humidity pause takes priority over night setback."""
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_time_until_resume.return_value = None

        manager = StatusManager(humidity_detector=humidity_detector)
        manager.set_night_setback_controller(self._make_night_setback_controller())

        info = manager.get_status_info()
        assert info["active"] is True
        assert info["reason"] == "humidity"
