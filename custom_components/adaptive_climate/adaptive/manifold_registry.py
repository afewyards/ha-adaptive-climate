"""Manifold transport delay tracking for hydronic heating systems.

This module tracks hydraulic manifolds and calculates transport delays for
heated water to reach zones. Transport delay depends on pipe volume, flow rate,
and number of active loops.

When a manifold has been inactive for >5 minutes, water in the pipes cools.
The first heating cycle must fill the manifold with hot water before zones
receive heat, introducing a transport delay.

Formula:
    delay = pipe_volume / (active_loops × flow_per_loop)

Once the manifold is warm (active within 5 minutes), delay is zero.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from homeassistant.util import dt as dt_util

from ..const import (
    MANIFOLD_COOLDOWN_MINUTES,
    DEFAULT_FLOW_PER_LOOP,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Manifold:
    """Represents a hydraulic manifold serving one or more zones.

    Attributes:
        name: Unique identifier for the manifold (e.g., "2nd Floor")
        zones: List of zone entity_ids served by this manifold
        pipe_volume: Total pipe volume in liters from heat source to manifold
        flow_per_loop: Flow rate per loop in L/min (default 2.0)
    """
    name: str
    zones: List[str]
    pipe_volume: float
    flow_per_loop: float = DEFAULT_FLOW_PER_LOOP


class ManifoldRegistry:
    """Registry for tracking manifolds and calculating transport delays.

    Tracks when each manifold was last active to determine if pipes are warm
    (active within cooldown period) or cold (need filling time).
    """

    def __init__(self, manifolds: List[Manifold]) -> None:
        """Initialize registry with manifold definitions.

        Args:
            manifolds: List of Manifold objects to register
        """
        self._manifolds = manifolds

        # Build zone-to-manifold lookup map
        self._zone_to_manifold: Dict[str, Manifold] = {}
        for manifold in manifolds:
            for zone_id in manifold.zones:
                self._zone_to_manifold[zone_id] = manifold

        # Track last active time per manifold name
        self._last_active_time: Dict[str, datetime] = {}

    def get_manifold_for_zone(self, zone_id: str) -> Optional[Manifold]:
        """Get the manifold serving a specific zone.

        Args:
            zone_id: Zone entity_id to lookup

        Returns:
            Manifold object if zone is registered, None otherwise
        """
        return self._zone_to_manifold.get(zone_id)

    def mark_manifold_active(self, zone_id: str) -> None:
        """Mark the manifold serving this zone as active (warm).

        Updates the last active timestamp for the manifold to now.
        Used when a zone starts heating to indicate manifold has warm water.

        Args:
            zone_id: Zone entity_id that is heating
        """
        manifold = self.get_manifold_for_zone(zone_id)
        if manifold:
            self._last_active_time[manifold.name] = dt_util.utcnow()

    def get_transport_delay(
        self,
        zone_id: str,
        active_zones: Dict[str, int]
    ) -> float:
        """Calculate transport delay for a zone in minutes.

        Transport delay is the time for hot water to travel from the heat source
        to the manifold. When the manifold is cold (inactive for >5 min), this
        represents the filling time. When warm, delay is zero.

        Args:
            zone_id: Zone entity_id requesting delay calculation
            active_zones: Dict of {zone_id: loop_count} for all active zones

        Returns:
            Transport delay in minutes. Returns 0 if:
                - Zone not in registry
                - Manifold was active within cooldown period
                - No active zones (no flow)
        """
        # Unknown zone - return 0 silently
        manifold = self.get_manifold_for_zone(zone_id)
        if not manifold:
            return 0.0

        # Check if manifold is warm (active within cooldown period)
        if manifold.name in self._last_active_time:
            time_since_active = dt_util.utcnow() - self._last_active_time[manifold.name]
            if time_since_active < timedelta(minutes=MANIFOLD_COOLDOWN_MINUTES):
                return 0.0

        # Calculate total active loops on this manifold
        total_active_loops = 0
        for zone_id_check, loop_count in active_zones.items():
            zone_manifold = self.get_manifold_for_zone(zone_id_check)
            if zone_manifold and zone_manifold.name == manifold.name:
                total_active_loops += loop_count

        # No active zones means no flow - return 0
        if total_active_loops == 0:
            return 0.0

        # Calculate delay: pipe_volume / (total_loops × flow_per_loop)
        total_flow_rate = total_active_loops * manifold.flow_per_loop
        delay = manifold.pipe_volume / total_flow_rate

        return delay

    def get_worst_case_transport_delay(self, zone_id: str, zone_loops: int = 1) -> float:
        """Get worst-case transport delay in minutes for a zone.

        Worst case = only this zone active, so delay = pipe_volume / (zone_loops × flow_per_loop).

        Args:
            zone_id: The zone entity_id
            zone_loops: Number of loops for this zone (defaults to 1 for worst case)

        Returns:
            Transport delay in minutes, or 0.0 if zone not in any manifold.
        """
        manifold = self.get_manifold_for_zone(zone_id)
        if not manifold:
            return 0.0
        return manifold.pipe_volume / (zone_loops * manifold.flow_per_loop)

    def get_state_for_persistence(self) -> Dict[str, str]:
        """Return last_active_time as ISO strings for storage.

        Returns:
            Dict mapping manifold names to ISO datetime strings.
        """
        return {
            name: ts.isoformat()
            for name, ts in self._last_active_time.items()
            if ts is not None
        }

    def restore_state(self, state: Dict[str, str]) -> None:
        """Restore last_active_time from stored ISO strings.

        Args:
            state: Dict mapping manifold names to ISO datetime strings.
        """
        for name, ts_str in state.items():
            try:
                parsed = dt_util.parse_datetime(ts_str)
                if parsed:
                    self._last_active_time[name] = parsed
            except (ValueError, TypeError):
                _LOGGER.warning("Failed to parse manifold timestamp for '%s': %s", name, ts_str)
