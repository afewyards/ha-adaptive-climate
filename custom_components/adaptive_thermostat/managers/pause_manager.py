"""Pause manager for adaptive thermostat.

Aggregates multiple pause mechanisms (contact sensors, humidity detection, open window detection)
and provides a unified interface for checking pause state and retrieving pause information.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..adaptive.contact_sensors import ContactAction, ContactSensorHandler
    from ..adaptive.humidity_detector import HumidityDetector


class PauseInfo(TypedDict, total=False):
    """Pause information dictionary.

    Attributes:
        active: True when heating is paused
        reason: "contact" | "humidity" | None
        resume_in: Seconds until resume (optional, only when countdown active)
    """
    active: bool
    reason: str | None
    resume_in: int


class PauseManager:
    """Manages pause state across multiple pause mechanisms.

    Aggregates contact sensors, humidity detection, and open window detection
    into a single unified pause interface. Priority order (highest first):
    1. Contact sensors
    2. Humidity detection
    3. Open window detection (not yet implemented)
    """

    def __init__(
        self,
        contact_sensor_handler: ContactSensorHandler | None = None,
        humidity_detector: HumidityDetector | None = None,
    ):
        """Initialize pause manager.

        Args:
            contact_sensor_handler: Contact sensor handler instance (optional)
            humidity_detector: Humidity detector instance (optional)
        """
        self._contact_sensor_handler = contact_sensor_handler
        self._humidity_detector = humidity_detector
        # Placeholder for future open window detection
        # self._open_window_detector = None

    def is_paused(self) -> bool:
        """Check if heating should be paused.

        Returns:
            True if any pause mechanism is active
        """
        # Check contact sensors (highest priority)
        if self._contact_sensor_handler and self._contact_sensor_handler.should_take_action():
            from ..adaptive.contact_sensors import ContactAction
            action = self._contact_sensor_handler.get_action()
            if action == ContactAction.PAUSE:
                return True

        # Check humidity detection
        if self._humidity_detector and self._humidity_detector.should_pause():
            return True

        # Future: check open window detection
        # if self._open_window_detector and self._open_window_detector.should_pause():
        #     return True

        return False

    def get_pause_info(self) -> PauseInfo:
        """Get consolidated pause information.

        Returns the pause state with reason and optional resume countdown.
        Priority: contact > humidity > open_window (if multiple active, highest priority shown)

        Special case: If contact sensors are open but not yet triggering pause
        (during delay countdown), returns resume_in showing time until pause activates.

        Returns:
            Dictionary with active, reason, and optional resume_in fields
        """
        # Check contact sensors (highest priority)
        if self._contact_sensor_handler:
            is_open = self._contact_sensor_handler.is_any_contact_open()
            is_paused = self._contact_sensor_handler.should_take_action()

            if is_paused:
                from ..adaptive.contact_sensors import ContactAction
                action = self._contact_sensor_handler.get_action()
                if action == ContactAction.PAUSE:
                    pause_info: PauseInfo = {
                        "active": True,
                        "reason": "contact",
                    }
                    return pause_info
            elif is_open:
                # Contact is open but not paused yet (in delay countdown)
                time_until_action = self._contact_sensor_handler.get_time_until_action()
                if time_until_action is not None and time_until_action > 0:
                    pause_info: PauseInfo = {
                        "active": False,
                        "reason": None,
                        "resume_in": time_until_action,
                    }
                    return pause_info

        # Check humidity detection
        if self._humidity_detector and self._humidity_detector.should_pause():
            pause_info: PauseInfo = {
                "active": True,
                "reason": "humidity",
            }
            # Add resume countdown if in stabilizing state
            resume_in = self._humidity_detector.get_time_until_resume()
            if resume_in is not None:
                pause_info["resume_in"] = resume_in
            return pause_info

        # Future: check open window detection
        # if self._open_window_detector and self._open_window_detector.should_pause():
        #     pause_info: PauseInfo = {
        #         "active": True,
        #         "reason": "open_window",
        #     }
        #     resume_in = self._open_window_detector.get_time_until_resume()
        #     if resume_in is not None:
        #         pause_info["resume_in"] = resume_in
        #     return pause_info

        # No pause active
        return {
            "active": False,
            "reason": None,
        }
