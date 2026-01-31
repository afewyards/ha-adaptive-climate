"""Status manager for adaptive thermostat.

Aggregates multiple status mechanisms (pause via contact sensors, humidity detection,
night setback adjustments, and learning grace periods) and provides a unified interface
for checking status state and retrieving detailed status information.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

from typing_extensions import NotRequired

from homeassistant.util import dt as dt_util

from ..const import ThermostatCondition, ThermostatState

if TYPE_CHECKING:
    from ..adaptive.contact_sensors import ContactAction, ContactSensorHandler
    from ..adaptive.humidity_detector import HumidityDetector
    from .night_setback_manager import NightSetbackManager


class StatusInfo(TypedDict):
    """Status attribute structure for thermostat entity.

    Minimal (default):
        state: Current operational state
        conditions: List of active conditions (always present, may be empty)
        resume_at: ISO8601 timestamp when pause ends (optional)
        setback_delta: Temperature adjustment in °C during night_setback (optional)
        setback_end: ISO8601 timestamp when night period ends (optional)

    Rich (debug mode):
        humidity_peak: Peak humidity % when humidity_spike active (optional)
        open_sensors: List of contact sensor entity IDs that triggered (optional)
    """
    state: str
    conditions: list[str]
    resume_at: NotRequired[str]
    setback_delta: NotRequired[float]
    setback_end: NotRequired[str]
    # Debug fields
    humidity_peak: NotRequired[float]
    open_sensors: NotRequired[list[str]]


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
        debug: bool = False,
    ):
        """Initialize status manager.

        Args:
            contact_sensor_handler: Contact sensor handler instance (optional)
            humidity_detector: Humidity detector instance (optional)
            debug: If True, include debug fields in status output
        """
        self._contact_sensor_handler = contact_sensor_handler
        self._humidity_detector = humidity_detector
        self._night_setback_controller: NightSetbackManager | None = None
        self._debug = debug

    def set_night_setback_controller(self, controller: NightSetbackManager | None):
        """Set night setback controller (late binding).

        Args:
            controller: Night setback manager instance
        """
        self._night_setback_controller = controller

    def build_status(
        self,
        *,
        # State derivation inputs
        hvac_mode: str,
        heater_on: bool = False,
        cooler_on: bool = False,
        is_paused: bool = False,
        preheat_active: bool = False,
        cycle_state: str | None = None,
        # Condition inputs
        night_setback_active: bool = False,
        open_window_detected: bool = False,
        humidity_spike_active: bool = False,
        contact_open: bool = False,
        learning_grace_active: bool = False,
        # Optional values for status info
        resume_in_seconds: int | None = None,
        setback_delta: float | None = None,
        setback_end_time: str | None = None,  # "HH:MM" format
        # Debug values (only used when debug=True)
        humidity_peak: float | None = None,
        open_sensors: list[str] | None = None,
    ) -> StatusInfo:
        """Build complete status attribute.

        Returns:
            StatusInfo dict with state, conditions, and optional fields
        """
        state = derive_state(
            hvac_mode=hvac_mode,
            heater_on=heater_on,
            cooler_on=cooler_on,
            is_paused=is_paused,
            preheat_active=preheat_active,
            cycle_state=cycle_state,
        )

        conditions = build_conditions(
            night_setback_active=night_setback_active,
            open_window_detected=open_window_detected,
            humidity_spike_active=humidity_spike_active,
            contact_open=contact_open,
            learning_grace_active=learning_grace_active,
        )

        result: StatusInfo = {
            "state": state.value,
            "conditions": conditions,
        }

        # Add optional fields if present
        resume_at = calculate_resume_at(resume_in_seconds)
        if resume_at:
            result["resume_at"] = resume_at

        if setback_delta is not None:
            result["setback_delta"] = setback_delta

        setback_end = convert_setback_end(setback_end_time)
        if setback_end:
            result["setback_end"] = setback_end

        # Debug fields only when debug mode enabled
        if self._debug:
            if humidity_peak is not None:
                result["humidity_peak"] = humidity_peak
            if open_sensors is not None and len(open_sensors) > 0:
                result["open_sensors"] = open_sensors

        return result

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


def format_iso8601(dt: datetime) -> str:
    """Format datetime as ISO8601 string with UTC offset.

    Args:
        dt: Datetime to format

    Returns:
        ISO8601 formatted string (e.g., "2024-01-15T10:30:00+00:00")
    """
    return dt.isoformat()


def calculate_resume_at(resume_in_seconds: int | None) -> str | None:
    """Calculate ISO8601 timestamp for when pause ends.

    Args:
        resume_in_seconds: Seconds until resume, or None if not paused

    Returns:
        ISO8601 string of resume time, or None if not applicable
    """
    if resume_in_seconds is None or resume_in_seconds <= 0:
        return None

    resume_time = dt_util.utcnow() + timedelta(seconds=resume_in_seconds)
    return format_iso8601(resume_time)


def convert_setback_end(end_time: str | None, now: datetime | None = None) -> str | None:
    """Convert "HH:MM" setback end time to ISO8601.

    Args:
        end_time: Time in "HH:MM" format, or None
        now: Current time (for testing), defaults to utcnow()

    Returns:
        ISO8601 string for today or tomorrow (if time already passed), or None
    """
    if end_time is None:
        return None

    if now is None:
        now = dt_util.now()

    try:
        hour, minute = map(int, end_time.split(":"))
        end_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If time already passed today, use tomorrow
        if end_dt <= now:
            end_dt = end_dt + timedelta(days=1)

        return format_iso8601(end_dt)
    except (ValueError, AttributeError):
        return None


def build_conditions(
    *,
    night_setback_active: bool = False,
    open_window_detected: bool = False,
    humidity_spike_active: bool = False,
    contact_open: bool = False,
    learning_grace_active: bool = False,
) -> list[str]:
    """Build list of active conditions.

    Args:
        night_setback_active: Night setback period is active
        open_window_detected: Algorithmic open window detection triggered
        humidity_spike_active: Humidity spike (shower steam) detected
        contact_open: Contact sensor (window/door) is open
        learning_grace_active: Learning grace period after transition

    Returns:
        List of active condition string values (e.g., ["night_setback", "contact_open"])
        Order: contact_open, humidity_spike, open_window, night_setback, learning_grace
    """
    conditions: list[str] = []

    # Priority order: contact_open, humidity_spike, open_window, night_setback, learning_grace
    if contact_open:
        conditions.append(ThermostatCondition.CONTACT_OPEN.value)

    if humidity_spike_active:
        conditions.append(ThermostatCondition.HUMIDITY_SPIKE.value)

    if open_window_detected:
        conditions.append(ThermostatCondition.OPEN_WINDOW.value)

    if night_setback_active:
        conditions.append(ThermostatCondition.NIGHT_SETBACK.value)

    if learning_grace_active:
        conditions.append(ThermostatCondition.LEARNING_GRACE.value)

    return conditions


def derive_state(
    *,
    hvac_mode: str,
    heater_on: bool = False,
    cooler_on: bool = False,
    is_paused: bool = False,
    preheat_active: bool = False,
    cycle_state: str | None = None,
) -> ThermostatState:
    """Derive operational state from thermostat conditions.

    Priority order:
    1. HVAC off → idle
    2. Any pause → paused
    3. Preheat active → preheating
    4. Cycle settling → settling
    5. Heater/cooler on → heating/cooling
    6. Default → idle

    Args:
        hvac_mode: Current HVAC mode ("off", "heat", "cool", etc.)
        heater_on: Whether heater is currently active
        cooler_on: Whether cooler is currently active
        is_paused: Whether heating/cooling is paused by any condition
        preheat_active: Whether predictive preheat is running
        cycle_state: Cycle tracker state ("idle", "heating", "settling", etc.)

    Returns:
        ThermostatState enum value
    """
    # 1. HVAC off
    if hvac_mode == "off":
        return ThermostatState.IDLE

    # 2. Any pause condition blocks heating/cooling
    if is_paused:
        return ThermostatState.PAUSED

    # 3. Preheat active
    if preheat_active:
        return ThermostatState.PREHEATING

    # 4. Cycle settling
    if cycle_state == "settling":
        return ThermostatState.SETTLING

    # 5. Active heating/cooling
    if heater_on:
        return ThermostatState.HEATING

    if cooler_on:
        return ThermostatState.COOLING

    # 6. Default
    return ThermostatState.IDLE
