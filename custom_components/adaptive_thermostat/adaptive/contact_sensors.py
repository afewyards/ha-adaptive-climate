"""Contact sensor handling for adaptive thermostat.

Pauses or adjusts heating when windows/doors are open to save energy,
with configurable delays, grace periods, and multiple action types.
"""
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any


class ContactAction(Enum):
    """Action to take when contact sensor opens."""
    PAUSE = "pause"  # Stop heating completely
    FROST_PROTECTION = "frost_protection"  # Lower to frost protection temp (e.g., 5°C)


class ContactSensorHandler:
    """Handles contact sensor (window/door) state for a zone.

    When contact sensors open, heating can be paused or lowered after a delay
    to avoid wasting energy. A learning grace period prevents actions during
    initial learning.
    """

    def __init__(
        self,
        contact_sensors: List[str],
        contact_delay_seconds: int = 300,  # 5 minutes default
        action: ContactAction = ContactAction.PAUSE,
        frost_protection_temp: float = 5.0,
        learning_grace_seconds: int = 0
    ):
        """Initialize contact sensor handler.

        Args:
            contact_sensors: List of contact sensor entity IDs
            contact_delay_seconds: Seconds to wait after opening before taking action
            action: Action to take (PAUSE or FROST_PROTECTION)
            frost_protection_temp: Temperature for frost protection mode (°C)
            learning_grace_seconds: Grace period after startup to allow learning (seconds)
        """
        self.contact_sensors = contact_sensors
        self.contact_delay_seconds = contact_delay_seconds
        self.action = action
        self.frost_protection_temp = frost_protection_temp
        self.learning_grace_seconds = learning_grace_seconds

        # Track when contacts opened
        self._contact_opened_at: Optional[datetime] = None
        self._any_contact_open = False

        # Track when handler was created (for grace period)
        self._created_at: Optional[datetime] = None

    def update_contact_states(
        self,
        contact_states: Dict[str, bool],
        current_time: Optional[datetime] = None
    ):
        """Update contact sensor states.

        Args:
            contact_states: Dictionary mapping sensor entity_id to state (True=open, False=closed)
            current_time: Current datetime (defaults to now, used for testing)
        """
        if current_time is None:
            current_time = datetime.now()

        # Initialize created_at on first update if not set
        if self._created_at is None:
            self._created_at = current_time

        # Aggregate: ANY sensor open means contact is open
        any_open = any(
            contact_states.get(sensor_id, False)
            for sensor_id in self.contact_sensors
        )

        # Track state changes
        if any_open and not self._any_contact_open:
            # Transition from closed to open
            self._contact_opened_at = current_time
            self._any_contact_open = True
        elif not any_open and self._any_contact_open:
            # Transition from open to closed
            self._contact_opened_at = None
            self._any_contact_open = False

    def is_any_contact_open(self) -> bool:
        """Check if any contact sensor is currently open.

        Returns:
            True if at least one contact sensor is open
        """
        return self._any_contact_open

    def should_take_action(self, current_time: Optional[datetime] = None) -> bool:
        """Check if action should be taken based on contact state and delay.

        Args:
            current_time: Current datetime (defaults to now)

        Returns:
            True if action should be taken (contact open + delay elapsed)
        """
        if current_time is None:
            current_time = datetime.now()

        # Check grace period (no action during initial learning)
        if self.learning_grace_seconds > 0 and self._created_at is not None:
            grace_end = self._created_at + timedelta(seconds=self.learning_grace_seconds)
            if current_time < grace_end:
                return False

        # No action if no contact is open
        if not self._any_contact_open or self._contact_opened_at is None:
            return False

        # Check if delay has elapsed
        delay_end = self._contact_opened_at + timedelta(seconds=self.contact_delay_seconds)
        return current_time >= delay_end

    def get_action(self) -> ContactAction:
        """Get the configured action type.

        Returns:
            ContactAction enum value
        """
        return self.action

    def get_adjusted_setpoint(
        self,
        base_setpoint: float,
        current_time: Optional[datetime] = None
    ) -> Optional[float]:
        """Get adjusted setpoint based on contact state.

        Args:
            base_setpoint: Normal temperature setpoint
            current_time: Current datetime (defaults to now)

        Returns:
            Adjusted setpoint, or None if no adjustment needed
        """
        if not self.should_take_action(current_time):
            return None

        if self.action == ContactAction.FROST_PROTECTION:
            return self.frost_protection_temp
        elif self.action == ContactAction.PAUSE:
            # For PAUSE action, return None to indicate heating should stop
            # (caller should interpret None as "pause heating")
            return None

        return None

    def get_time_until_action(self, current_time: Optional[datetime] = None) -> Optional[int]:
        """Get seconds until action will be taken.

        Args:
            current_time: Current datetime (defaults to now)

        Returns:
            Seconds until action, or None if no action pending
        """
        if current_time is None:
            current_time = datetime.now()

        if not self._any_contact_open or self._contact_opened_at is None:
            return None

        delay_end = self._contact_opened_at + timedelta(seconds=self.contact_delay_seconds)
        if current_time >= delay_end:
            return 0  # Action should be taken now

        return int((delay_end - current_time).total_seconds())

    def get_grace_time_remaining(self, current_time: Optional[datetime] = None) -> int:
        """Get seconds remaining in learning grace period.

        Args:
            current_time: Current datetime (defaults to now)

        Returns:
            Seconds remaining in grace period (0 if expired)
        """
        if current_time is None:
            current_time = datetime.now()

        if self.learning_grace_seconds <= 0 or self._created_at is None:
            return 0

        grace_end = self._created_at + timedelta(seconds=self.learning_grace_seconds)
        if current_time >= grace_end:
            return 0

        return int((grace_end - current_time).total_seconds())


class ContactSensorManager:
    """Manages contact sensor handlers for multiple zones."""

    def __init__(self):
        """Initialize contact sensor manager."""
        self._zone_handlers: Dict[str, ContactSensorHandler] = {}

    def configure_zone(
        self,
        zone_id: str,
        contact_sensors: List[str],
        contact_delay_seconds: int = 300,
        action: str = "pause",
        frost_protection_temp: float = 5.0,
        learning_grace_seconds: int = 0
    ):
        """Configure contact sensor handling for a zone.

        Args:
            zone_id: Zone identifier
            contact_sensors: List of contact sensor entity IDs
            contact_delay_seconds: Seconds to wait after opening before taking action
            action: Action type ("pause" or "frost_protection")
            frost_protection_temp: Temperature for frost protection mode (°C)
            learning_grace_seconds: Grace period after startup (seconds)
        """
        # Convert string action to enum
        action_enum = ContactAction.PAUSE if action.lower() == "pause" else ContactAction.FROST_PROTECTION

        self._zone_handlers[zone_id] = ContactSensorHandler(
            contact_sensors=contact_sensors,
            contact_delay_seconds=contact_delay_seconds,
            action=action_enum,
            frost_protection_temp=frost_protection_temp,
            learning_grace_seconds=learning_grace_seconds
        )

    def update_contact_states(
        self,
        zone_id: str,
        contact_states: Dict[str, bool],
        current_time: Optional[datetime] = None
    ):
        """Update contact sensor states for a zone.

        Args:
            zone_id: Zone identifier
            contact_states: Dictionary mapping sensor entity_id to state
            current_time: Current datetime (defaults to now, used for testing)
        """
        if zone_id not in self._zone_handlers:
            return

        handler = self._zone_handlers[zone_id]
        handler.update_contact_states(contact_states, current_time)

    def should_take_action(
        self,
        zone_id: str,
        current_time: Optional[datetime] = None
    ) -> bool:
        """Check if action should be taken for a zone.

        Args:
            zone_id: Zone identifier
            current_time: Current datetime (defaults to now)

        Returns:
            True if action should be taken
        """
        if zone_id not in self._zone_handlers:
            return False

        handler = self._zone_handlers[zone_id]
        return handler.should_take_action(current_time)

    def get_adjusted_setpoint(
        self,
        zone_id: str,
        base_setpoint: float,
        current_time: Optional[datetime] = None
    ) -> Optional[float]:
        """Get adjusted setpoint for a zone based on contact state.

        Args:
            zone_id: Zone identifier
            base_setpoint: Normal temperature setpoint
            current_time: Current datetime (defaults to now)

        Returns:
            Adjusted setpoint, or None if no adjustment or zone not configured
        """
        if zone_id not in self._zone_handlers:
            return None

        handler = self._zone_handlers[zone_id]
        return handler.get_adjusted_setpoint(base_setpoint, current_time)

    def get_handler(self, zone_id: str) -> Optional[ContactSensorHandler]:
        """Get contact sensor handler for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            ContactSensorHandler instance or None if not configured
        """
        return self._zone_handlers.get(zone_id)

    def get_zone_config(self, zone_id: str) -> Optional[Dict[str, Any]]:
        """Get contact sensor configuration for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Configuration dictionary or None if not configured
        """
        if zone_id not in self._zone_handlers:
            return None

        handler = self._zone_handlers[zone_id]
        return {
            "contact_sensors": handler.contact_sensors,
            "contact_delay_seconds": handler.contact_delay_seconds,
            "action": handler.action.value,
            "frost_protection_temp": handler.frost_protection_temp,
            "learning_grace_seconds": handler.learning_grace_seconds
        }
