"""Static Thermal Groups for Adaptive Thermostat.

Provides declarative thermal group configuration for:
- Leader/follower coordination in open plan areas
- Cross-group heat transfer with delay modeling
- Static configuration without learning or Bayesian updates
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

try:
    from ..const import DOMAIN
except ImportError:
    from const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# Group type constants
GROUP_TYPE_OPEN_PLAN = "open_plan"

# Valid group types
VALID_GROUP_TYPES = [GROUP_TYPE_OPEN_PLAN]


@dataclass
class ThermalGroup:
    """Configuration for a thermal group.

    Attributes:
        name: Human-readable group name
        zones: List of zone IDs in this group
        group_type: Type of thermal relationship (open_plan)
        leader: Leader zone ID (for open_plan type)
        receives_from: Source group name for heat transfer
        transfer_factor: Fraction of source heat reaching this group (0.0-1.0)
        delay_minutes: Time delay for heat transfer propagation
    """
    name: str
    zones: list[str]
    group_type: str = GROUP_TYPE_OPEN_PLAN
    leader: str | None = None
    receives_from: str | None = None
    transfer_factor: float = 0.0
    delay_minutes: int = 0

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.group_type not in VALID_GROUP_TYPES:
            raise ValueError(
                f"Invalid group_type '{self.group_type}'. "
                f"Must be one of: {', '.join(VALID_GROUP_TYPES)}"
            )

        if not self.zones:
            raise ValueError(f"Group '{self.name}' must have at least one zone")

        if self.group_type == GROUP_TYPE_OPEN_PLAN:
            if self.leader is None:
                raise ValueError(
                    f"Group '{self.name}' type 'open_plan' requires a leader zone"
                )
            if self.leader not in self.zones:
                raise ValueError(
                    f"Leader '{self.leader}' must be in zones list for group '{self.name}'"
                )

        if self.receives_from is not None:
            if not (0.0 <= self.transfer_factor <= 1.0):
                raise ValueError(
                    f"transfer_factor must be between 0.0 and 1.0 for group '{self.name}', "
                    f"got {self.transfer_factor}"
                )
            if self.delay_minutes < 0:
                raise ValueError(
                    f"delay_minutes must be non-negative for group '{self.name}', "
                    f"got {self.delay_minutes}"
                )


@dataclass
class TransferHistory:
    """Tracks heat transfer history for delay modeling.

    Attributes:
        source_group: Source group name
        timestamp: When the heat was measured
        heat_output: Heat output percentage (0-100)
    """
    source_group: str
    timestamp: datetime
    heat_output: float


class ThermalGroupManager:
    """Manages thermal groups and coordinates zone behavior.

    Features:
    - Leader/follower setpoint tracking for open plan areas
    - Cross-group heat transfer with configurable delay
    - Feedforward compensation calculation
    """

    def __init__(self, hass: HomeAssistant, groups_config: list[dict[str, Any]]):
        """Initialize thermal group manager.

        Args:
            hass: Home Assistant instance
            groups_config: List of group configuration dictionaries
        """
        self.hass = hass
        self._groups: dict[str, ThermalGroup] = {}
        self._zone_to_group: dict[str, str] = {}
        self._transfer_history: dict[str, list[TransferHistory]] = {}

        # Parse and validate configuration
        for group_config in groups_config:
            self._add_group(group_config)

        # Validate cross-group references
        self._validate_cross_group_refs()

        _LOGGER.info(
            "ThermalGroupManager initialized with %d groups covering %d zones",
            len(self._groups),
            len(self._zone_to_group)
        )

    def _add_group(self, config: dict[str, Any]) -> None:
        """Add a thermal group from configuration.

        Args:
            config: Group configuration dictionary
        """
        try:
            group = ThermalGroup(
                name=config["name"],
                zones=config["zones"],
                group_type=config.get("type", GROUP_TYPE_OPEN_PLAN),
                leader=config.get("leader"),
                receives_from=config.get("receives_from"),
                transfer_factor=config.get("transfer_factor", 0.0),
                delay_minutes=config.get("delay_minutes", 0),
            )

            # Check for zone conflicts
            for zone_id in group.zones:
                if zone_id in self._zone_to_group:
                    existing_group = self._zone_to_group[zone_id]
                    raise ValueError(
                        f"Zone '{zone_id}' is already in group '{existing_group}', "
                        f"cannot add to group '{group.name}'"
                    )

            # Register group and zones
            self._groups[group.name] = group
            for zone_id in group.zones:
                self._zone_to_group[zone_id] = group.name

            # Initialize transfer history if receiving from another group
            if group.receives_from:
                self._transfer_history[group.name] = []

            _LOGGER.debug("Added thermal group: %s (type=%s, zones=%s)",
                         group.name, group.group_type, group.zones)

        except (KeyError, ValueError) as e:
            _LOGGER.error("Failed to add thermal group from config: %s", e)
            raise

    def _validate_cross_group_refs(self) -> None:
        """Validate that cross-group references are valid."""
        for group in self._groups.values():
            if group.receives_from:
                if group.receives_from not in self._groups:
                    raise ValueError(
                        f"Group '{group.name}' receives_from unknown group "
                        f"'{group.receives_from}'"
                    )
                # Prevent self-reference
                if group.receives_from == group.name:
                    raise ValueError(
                        f"Group '{group.name}' cannot receive from itself"
                    )

    def get_zone_group(self, zone_id: str) -> ThermalGroup | None:
        """Get the thermal group for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            ThermalGroup if zone is in a group, None otherwise
        """
        group_name = self._zone_to_group.get(zone_id)
        if group_name:
            return self._groups[group_name]
        return None

    def get_leader_zone(self, zone_id: str) -> str | None:
        """Get the leader zone for a follower zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Leader zone ID if this is a follower zone, None if leader or not in group
        """
        group = self.get_zone_group(zone_id)
        if group and group.leader and group.leader != zone_id:
            return group.leader
        return None

    def is_leader_zone(self, zone_id: str) -> bool:
        """Check if a zone is a leader in its group.

        Args:
            zone_id: Zone identifier

        Returns:
            True if zone is a leader
        """
        group = self.get_zone_group(zone_id)
        return group is not None and group.leader == zone_id

    def get_follower_zones(self, leader_zone_id: str) -> list[str]:
        """Get all follower zones for a leader.

        Args:
            leader_zone_id: Leader zone identifier

        Returns:
            List of follower zone IDs
        """
        group = self.get_zone_group(leader_zone_id)
        if not group or group.leader != leader_zone_id:
            return []

        return [z for z in group.zones if z != leader_zone_id]

    def record_heat_output(self, zone_id: str, heat_output: float) -> None:
        """Record heat output for a zone in a source group.

        This is used to build transfer history for delayed feedforward calculations.

        Args:
            zone_id: Zone identifier
            heat_output: Current heat output percentage (0-100)
        """
        # Find all groups that receive from this zone's group
        source_group_name = self._zone_to_group.get(zone_id)
        if not source_group_name:
            return

        # Find receiving groups
        for group in self._groups.values():
            if group.receives_from == source_group_name:
                # Add to history
                history_entry = TransferHistory(
                    source_group=source_group_name,
                    timestamp=dt_util.utcnow(),
                    heat_output=heat_output
                )
                self._transfer_history[group.name].append(history_entry)

                # Keep only last 2 hours of history
                cutoff_time = dt_util.utcnow() - timedelta(hours=2)
                self._transfer_history[group.name] = [
                    h for h in self._transfer_history[group.name]
                    if h.timestamp > cutoff_time
                ]

    def calculate_feedforward(self, zone_id: str) -> float:
        """Calculate feedforward compensation for a zone from thermal transfer.

        For zones in groups that receive heat from other groups, this calculates
        the expected heat contribution based on the source group's historical output
        and the configured transfer factor and delay.

        Args:
            zone_id: Zone identifier

        Returns:
            Feedforward percentage (0-100) to subtract from PID output
        """
        group = self.get_zone_group(zone_id)
        if not group or not group.receives_from:
            return 0.0

        # Get transfer history for this group
        history = self._transfer_history.get(group.name, [])
        if not history:
            return 0.0

        # Find heat output at delay_minutes ago
        target_time = dt_util.utcnow() - timedelta(minutes=group.delay_minutes)

        # Find closest historical entry
        closest_entry = None
        min_time_diff = timedelta(hours=999)

        for entry in history:
            time_diff = abs(entry.timestamp - target_time)
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                closest_entry = entry

        if closest_entry is None:
            return 0.0

        # Only use if within 5 minutes of target time
        if min_time_diff > timedelta(minutes=5):
            return 0.0

        # Calculate feedforward: source heat * transfer factor
        feedforward = closest_entry.heat_output * group.transfer_factor

        _LOGGER.debug(
            "Zone %s feedforward: %.1f%% (source=%s, factor=%.2f, delay=%dm)",
            zone_id, feedforward, group.receives_from, group.transfer_factor,
            group.delay_minutes
        )

        return feedforward

    def should_sync_setpoint(self, follower_zone_id: str, leader_setpoint: float) -> bool:
        """Check if a follower zone should sync to leader setpoint.

        Args:
            follower_zone_id: Follower zone identifier
            leader_setpoint: Leader's current setpoint

        Returns:
            True if follower should sync to leader setpoint
        """
        # Must be a follower zone in an open_plan group
        group = self.get_zone_group(follower_zone_id)
        if not group or group.leader == follower_zone_id:
            return False

        return group.group_type == GROUP_TYPE_OPEN_PLAN

    def get_group_status(self) -> dict[str, Any]:
        """Get status of all thermal groups for diagnostics.

        Returns:
            Dictionary with group status information
        """
        status = {
            "groups": {},
            "zone_assignments": self._zone_to_group.copy(),
        }

        for group_name, group in self._groups.items():
            status["groups"][group_name] = {
                "type": group.group_type,
                "zones": group.zones,
                "leader": group.leader,
                "receives_from": group.receives_from,
                "transfer_factor": group.transfer_factor,
                "delay_minutes": group.delay_minutes,
                "history_entries": len(self._transfer_history.get(group_name, [])),
            }

        return status


def validate_thermal_groups_config(config: list[dict[str, Any]]) -> None:
    """Validate thermal groups configuration without creating manager.

    This is used during config validation to catch errors early.

    Args:
        config: List of group configuration dictionaries

    Raises:
        ValueError: If configuration is invalid
    """
    if not isinstance(config, list):
        raise ValueError("thermal_groups must be a list")

    if not config:
        raise ValueError("thermal_groups cannot be empty")

    # Validate each group can be created
    groups_by_name = {}
    zones_seen = set()

    for i, group_config in enumerate(config):
        if not isinstance(group_config, dict):
            raise ValueError(f"Group {i} must be a dictionary")

        # Required fields
        if "name" not in group_config:
            raise ValueError(f"Group {i} missing required field 'name'")
        if "zones" not in group_config:
            raise ValueError(f"Group {i} missing required field 'zones'")

        name = group_config["name"]
        zones = group_config["zones"]

        # Check for duplicate group names
        if name in groups_by_name:
            raise ValueError(f"Duplicate group name: '{name}'")
        groups_by_name[name] = group_config

        # Check zones
        if not isinstance(zones, list):
            raise ValueError(f"Group '{name}' zones must be a list")
        if not zones:
            raise ValueError(f"Group '{name}' must have at least one zone")

        # Check for zone conflicts
        for zone in zones:
            if zone in zones_seen:
                raise ValueError(
                    f"Zone '{zone}' assigned to multiple groups"
                )
            zones_seen.add(zone)

        # Validate by creating ThermalGroup (will run __post_init__ validation)
        try:
            ThermalGroup(
                name=name,
                zones=zones,
                group_type=group_config.get("type", GROUP_TYPE_OPEN_PLAN),
                leader=group_config.get("leader"),
                receives_from=group_config.get("receives_from"),
                transfer_factor=group_config.get("transfer_factor", 0.0),
                delay_minutes=group_config.get("delay_minutes", 0),
            )
        except (ValueError, TypeError) as e:
            raise ValueError(f"Group '{name}' configuration error: {e}") from e

    # Validate cross-group references
    for group_config in config:
        receives_from = group_config.get("receives_from")
        if receives_from:
            if receives_from not in groups_by_name:
                raise ValueError(
                    f"Group '{group_config['name']}' receives_from unknown group "
                    f"'{receives_from}'"
                )
            if receives_from == group_config["name"]:
                raise ValueError(
                    f"Group '{group_config['name']}' cannot receive from itself"
                )
