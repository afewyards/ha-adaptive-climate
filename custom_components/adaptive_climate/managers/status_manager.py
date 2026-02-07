"""Status manager for adaptive thermostat.

Aggregates multiple status mechanisms (pause via contact sensors, humidity detection,
night setback adjustments, and learning grace periods) and provides a unified interface
for checking status state and retrieving detailed status information.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, TypedDict

from typing_extensions import NotRequired

from homeassistant.util import dt as dt_util

from ..const import OverrideType, ThermostatCondition, ThermostatState

if TYPE_CHECKING:
    from ..adaptive.contact_sensors import ContactAction, ContactSensorHandler
    from ..adaptive.humidity_detector import HumidityDetector
    from .night_setback_manager import NightSetbackManager


class StatusInfo(TypedDict):
    """Status attribute structure for thermostat entity.

    New structure (v2):
        activity: Current activity (idle|heating|cooling|settling)
        overrides: Priority-ordered list of active overrides
    """

    activity: str
    overrides: list[dict[str, Any]]


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
        # Contact open override
        contact_open: bool = False,
        contact_sensors: list[str] | None = None,
        contact_since: str | None = None,
        # Humidity override
        humidity_active: bool = False,
        humidity_state: str | None = None,
        humidity_resume_at: str | None = None,
        # Open window override
        open_window_active: bool = False,
        open_window_since: str | None = None,
        open_window_resume_at: str | None = None,
        # Preheating override
        preheating_active: bool = False,
        preheating_target_time: str | None = None,
        preheating_started_at: str | None = None,
        preheating_target_delta: float | None = None,
        # Night setback override
        night_setback_active: bool = False,
        night_setback_delta: float | None = None,
        night_setback_ends_at: str | None = None,
        night_setback_limited_to: float | None = None,
        # Learning grace override
        learning_grace_active: bool = False,
        learning_grace_until: str | None = None,
    ) -> StatusInfo:
        """Build complete status attribute.

        Returns:
            StatusInfo dict with activity and overrides
        """
        activity = derive_state(
            hvac_mode=hvac_mode,
            heater_on=heater_on,
            cooler_on=cooler_on,
            preheat_active=preheat_active,
            cycle_state=cycle_state,
        )

        overrides = build_overrides(
            contact_open=contact_open,
            contact_sensors=contact_sensors,
            contact_since=contact_since,
            humidity_active=humidity_active,
            humidity_state=humidity_state,
            humidity_resume_at=humidity_resume_at,
            open_window_active=open_window_active,
            open_window_since=open_window_since,
            open_window_resume_at=open_window_resume_at,
            preheating_active=preheating_active,
            preheating_target_time=preheating_target_time,
            preheating_started_at=preheating_started_at,
            preheating_target_delta=preheating_target_delta,
            night_setback_active=night_setback_active,
            night_setback_delta=night_setback_delta,
            night_setback_ends_at=night_setback_ends_at,
            night_setback_limited_to=night_setback_limited_to,
            learning_grace_active=learning_grace_active,
            learning_grace_until=learning_grace_until,
        )

        return {
            "activity": activity,
            "overrides": overrides,
        }

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
        return bool(self._humidity_detector and self._humidity_detector.should_pause())


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
    preheat_active: bool = False,
    cycle_state: str | None = None,
) -> str:
    """Derive operational activity from thermostat conditions.

    Args:
        hvac_mode: Current HVAC mode ("off", "heat", "cool", etc.)
        heater_on: Whether heater is currently active
        cooler_on: Whether cooler is currently active
        preheat_active: Whether preheating is currently active
        cycle_state: Cycle tracker state ("idle", "heating", "settling", etc.)

    Returns:
        Activity string (idle, heating, cooling, settling, preheating)
    """
    # 1. HVAC off
    if hvac_mode == "off":
        return ThermostatState.IDLE.value

    # 2. Preheating (highest priority for activity display)
    if preheat_active:
        return "preheating"

    # 3. Cycle settling
    if cycle_state == "settling":
        return ThermostatState.SETTLING.value

    # 4. Active heating/cooling
    if heater_on:
        return ThermostatState.HEATING.value

    if cooler_on:
        return ThermostatState.COOLING.value

    # 5. Default
    return ThermostatState.IDLE.value


def build_override(override_type: OverrideType, **kwargs) -> dict[str, Any]:
    """Build an override dict with type and provided fields.

    Args:
        override_type: The type of override
        **kwargs: Fields specific to this override type

    Returns:
        Dict with "type" and all non-None kwargs
    """
    result: dict[str, Any] = {"type": override_type.value}
    for key, value in kwargs.items():
        if value is not None:
            result[key] = value
    return result


def build_overrides(
    *,
    # Contact open
    contact_open: bool = False,
    contact_sensors: list[str] | None = None,
    contact_since: str | None = None,
    # Humidity
    humidity_active: bool = False,
    humidity_state: str | None = None,
    humidity_resume_at: str | None = None,
    # Open window
    open_window_active: bool = False,
    open_window_since: str | None = None,
    open_window_resume_at: str | None = None,
    # Preheating
    preheating_active: bool = False,
    preheating_target_time: str | None = None,
    preheating_started_at: str | None = None,
    preheating_target_delta: float | None = None,
    # Night setback
    night_setback_active: bool = False,
    night_setback_delta: float | None = None,
    night_setback_ends_at: str | None = None,
    night_setback_limited_to: float | None = None,
    # Learning grace
    learning_grace_active: bool = False,
    learning_grace_until: str | None = None,
) -> list[dict[str, Any]]:
    """Build priority-ordered list of active overrides.

    Priority order (highest first):
    1. contact_open
    2. humidity
    3. open_window
    4. preheating
    5. night_setback
    6. learning_grace

    Returns:
        List of override dicts, ordered by priority
    """
    overrides: list[dict[str, Any]] = []

    # 1. Contact open (highest priority)
    if contact_open:
        overrides.append(
            build_override(
                OverrideType.CONTACT_OPEN,
                sensors=contact_sensors,
                since=contact_since,
            )
        )

    # 2. Humidity
    if humidity_active:
        overrides.append(
            build_override(
                OverrideType.HUMIDITY,
                state=humidity_state,
                resume_at=humidity_resume_at,
            )
        )

    # 3. Open window
    if open_window_active:
        overrides.append(
            build_override(
                OverrideType.OPEN_WINDOW,
                since=open_window_since,
                resume_at=open_window_resume_at,
            )
        )

    # 4. Preheating
    if preheating_active:
        overrides.append(
            build_override(
                OverrideType.PREHEATING,
                target_time=preheating_target_time,
                started_at=preheating_started_at,
                target_delta=preheating_target_delta,
            )
        )

    # 5. Night setback
    if night_setback_active:
        overrides.append(
            build_override(
                OverrideType.NIGHT_SETBACK,
                delta=night_setback_delta,
                ends_at=night_setback_ends_at,
                limited_to=night_setback_limited_to,
            )
        )

    # 6. Learning grace (lowest priority)
    if learning_grace_active:
        overrides.append(
            build_override(
                OverrideType.LEARNING_GRACE,
                until=learning_grace_until,
            )
        )

    return overrides
