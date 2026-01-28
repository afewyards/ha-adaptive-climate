"""Status manager for adaptive thermostat.

Aggregates multiple status mechanisms (pause via contact sensors, humidity detection,
night setback adjustments, and learning grace periods) and provides a unified interface
for checking status state and retrieving detailed status information.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..adaptive.contact_sensors import ContactAction, ContactSensorHandler
    from ..adaptive.humidity_detector import HumidityDetector
    from .night_setback_manager import NightSetbackManager


class StatusInfo(TypedDict, total=False):
    """Status information dictionary.

    Attributes:
        active: True when heating is paused or night setback is active
        reason: "contact" | "humidity" | "night_setback" | None
        resume_in: Seconds until resume (optional, only when countdown active)
        delta: Night setback delta in °C (optional, night_setback only)
        end: Night setback end time "HH:MM" (optional, night_setback only)
        learning_paused: True when learning grace period is active (optional)
        learning_resumes: Learning grace period end time "HH:MM" (optional)
    """
    active: bool
    reason: str | None
    resume_in: int
    delta: float
    end: str
    learning_paused: bool
    learning_resumes: str


class StatusManager:
    """Manages status state across multiple mechanisms.

    Aggregates contact sensors, humidity detection, night setback, and learning grace periods
    into a single unified status interface. Priority order for pause (highest first):
    1. Contact sensors
    2. Humidity detection
    3. Night setback (adjusts setpoint, reported but not a pause)
    """

    def __init__(
        self,
        contact_sensor_handler: ContactSensorHandler | None = None,
        humidity_detector: HumidityDetector | None = None,
    ):
        """Initialize status manager.

        Args:
            contact_sensor_handler: Contact sensor handler instance (optional)
            humidity_detector: Humidity detector instance (optional)
        """
        self._contact_sensor_handler = contact_sensor_handler
        self._humidity_detector = humidity_detector
        self._night_setback_controller: NightSetbackManager | None = None
        # Cache for get_status_info with 30s TTL
        self._cached_status: StatusInfo | None = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 30.0

    def set_night_setback_controller(self, controller: NightSetbackManager | None):
        """Set night setback controller (late binding).

        Args:
            controller: Night setback manager instance
        """
        self._night_setback_controller = controller

    def is_paused(self) -> bool:
        """Check if heating should be paused.

        Note: This only checks contact sensors and humidity detection.
        Night setback is not considered a pause (it adjusts setpoint instead).

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

        return False

    def get_status_info(self) -> StatusInfo:
        """Get consolidated status information.

        Returns the status state with reason and optional additional fields.
        Priority: contact > humidity > night_setback (if multiple active, highest priority shown)

        Special cases:
        - If contact sensors are open but not yet triggering pause (during delay countdown),
          returns resume_in showing time until pause activates.
        - Night setback is reported when active, even though it's not a pause.
        - Learning grace period is reported via learning_paused field.

        Returns:
            Dictionary with active, reason, and optional additional fields
        """
        # Check cache validity (30s TTL) and basic state signature
        now = time.monotonic()
        cache_valid = (
            self._cached_status is not None
            and (now - self._cache_timestamp) < self._cache_ttl
            and self._check_state_signature_unchanged()
        )

        if cache_valid:
            return self._cached_status

        # Cache miss or expired - recalculate
        status = self._calculate_status_info()
        self._cached_status = status
        self._cache_timestamp = now
        self._update_state_signature()
        return status

    def _check_state_signature_unchanged(self) -> bool:
        """Check if state has changed since last cache update.

        Returns:
            True if state unchanged, False if state changed
        """
        # Quick state signature: check if pause sources are still in same state
        contact_paused = (
            self._contact_sensor_handler
            and self._contact_sensor_handler.should_take_action()
        )
        humidity_state = (
            self._humidity_detector.get_state()
            if self._humidity_detector
            else "normal"
        )

        return (
            getattr(self, '_last_contact_paused', None) == contact_paused
            and getattr(self, '_last_humidity_state', None) == humidity_state
        )

    def _update_state_signature(self) -> None:
        """Update state signature for cache validation."""
        self._last_contact_paused = (
            self._contact_sensor_handler
            and self._contact_sensor_handler.should_take_action()
        )
        self._last_humidity_state = (
            self._humidity_detector.get_state()
            if self._humidity_detector
            else "normal"
        )

    def _calculate_status_info(self) -> StatusInfo:
        """Calculate status information (uncached).

        Returns:
            Dictionary with active, reason, and optional additional fields
        """
        # Check contact sensors (highest priority)
        if self._contact_sensor_handler:
            is_open = self._contact_sensor_handler.is_any_contact_open()
            is_paused = self._contact_sensor_handler.should_take_action()

            if is_paused:
                from ..adaptive.contact_sensors import ContactAction
                action = self._contact_sensor_handler.get_action()
                if action == ContactAction.PAUSE:
                    pause_info: StatusInfo = {
                        "active": True,
                        "reason": "contact",
                    }
                    return pause_info
            elif is_open:
                # Contact is open but not paused yet (in delay countdown)
                time_until_action = self._contact_sensor_handler.get_time_until_action()
                if time_until_action is not None and time_until_action > 0:
                    pause_info: StatusInfo = {
                        "active": False,
                        "reason": None,
                        "resume_in": time_until_action,
                    }
                    return pause_info

        # Check humidity detection
        if self._humidity_detector and self._humidity_detector.should_pause():
            pause_info: StatusInfo = {
                "active": True,
                "reason": "humidity",
            }
            # Add resume countdown if in stabilizing state
            resume_in = self._humidity_detector.get_time_until_resume()
            if resume_in is not None:
                pause_info["resume_in"] = resume_in
            return pause_info

        # Check night setback (lowest priority — adjusts setpoint, not a pause)
        if self._night_setback_controller:
            _, in_night, info = self._night_setback_controller.calculate_night_setback_adjustment()
            if in_night:
                status: StatusInfo = {
                    "active": True,
                    "reason": "night_setback",
                }
                if "night_setback_delta" in info:
                    status["delta"] = info["night_setback_delta"]
                if "night_setback_end" in info:
                    status["end"] = info["night_setback_end"]
                # Check learning grace period
                if self._night_setback_controller.in_learning_grace_period:
                    status["learning_paused"] = True
                    grace_until = self._night_setback_controller.learning_grace_until
                    if grace_until:
                        status["learning_resumes"] = grace_until.strftime("%H:%M")
                return status
            # Not in night period but check learning grace
            if self._night_setback_controller.in_learning_grace_period:
                status: StatusInfo = {
                    "active": False,
                    "reason": None,
                    "learning_paused": True,
                }
                grace_until = self._night_setback_controller.learning_grace_until
                if grace_until:
                    status["learning_resumes"] = grace_until.strftime("%H:%M")
                return status

        # No pause active
        return {
            "active": False,
            "reason": None,
        }


# Backward compatibility alias
PauseManager = StatusManager
PauseInfo = StatusInfo
