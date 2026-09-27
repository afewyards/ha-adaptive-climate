"""Manifold transport delay tracking for hydronic heating systems.

When a manifold has been inactive for >5 minutes, water in the pipes cools.
The first heating cycle must fill the manifold with hot water before zones
receive heat, introducing a transport delay.

Formula:
    delay = pipe_volume / Σ flow of active zones on the manifold

Once the manifold is warm (active within 5 minutes), delay is zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import (
    CONF_MANIFOLD_ZONE_ENTITY,
    CONF_MANIFOLD_ZONE_FLOW,
    CONF_PIPE_VOLUME,
    MANIFOLD_COOLDOWN_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Manifold:
    """A hydraulic manifold serving one or more zones.

    Attributes:
        name: Unique identifier for the manifold (e.g., "2nd Floor")
        pipe_volume: Pipe volume in liters from heat source to manifold
        zones: Zone entity_id -> design flow in L/min
    """

    name: str
    pipe_volume: float
    zones: dict[str, float]


def manifolds_from_config(manifolds_config: list[dict[str, Any]]) -> list[Manifold]:
    """Build manifolds from validated ``manifolds:`` config entries."""
    return [
        Manifold(
            name=m["name"],
            pipe_volume=float(m[CONF_PIPE_VOLUME]),
            zones={z[CONF_MANIFOLD_ZONE_ENTITY]: float(z[CONF_MANIFOLD_ZONE_FLOW]) for z in m["zones"]},
        )
        for m in manifolds_config
    ]


class ManifoldRegistry:
    """Registry for tracking manifolds and calculating transport delays.

    Tracks when each manifold was last active to determine if pipes are warm
    (active within cooldown period) or cold (need filling time).
    """

    def __init__(self, manifolds: list[Manifold]) -> None:
        """Initialize registry with manifold definitions.

        Args:
            manifolds: List of Manifold objects to register
        """
        self._manifolds = manifolds

        # Build zone-to-manifold lookup map
        self._zone_to_manifold: dict[str, Manifold] = {}
        for manifold in manifolds:
            for zone_id in manifold.zones:
                self._zone_to_manifold[zone_id] = manifold

        # Track last active time per manifold name
        self._last_active_time: dict[str, datetime] = {}

    def get_manifold_for_zone(self, zone_id: str) -> Manifold | None:
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

    def get_transport_delay(self, zone_id: str, active_zones: set[str]) -> float:
        """Calculate transport delay for a zone in minutes.

        Args:
            zone_id: Zone entity_id requesting delay calculation
            active_zones: Entity_ids of zones currently drawing water

        Returns:
            Transport delay in minutes. 0 when the zone is unknown, the manifold
            was active within the cooldown, or no active zone is on the manifold.
        """
        manifold = self.get_manifold_for_zone(zone_id)
        if not manifold:
            return 0.0

        if manifold.name in self._last_active_time:
            time_since_active = dt_util.utcnow() - self._last_active_time[manifold.name]
            if time_since_active < timedelta(minutes=MANIFOLD_COOLDOWN_MINUTES):
                return 0.0

        total_flow = sum(flow for zone, flow in manifold.zones.items() if zone in active_zones)
        if total_flow <= 0:
            return 0.0
        return manifold.pipe_volume / total_flow

    def get_worst_case_transport_delay(self, zone_id: str) -> float:
        """Transport delay in minutes with only this zone drawing water, or 0.0 if not on a manifold."""
        manifold = self.get_manifold_for_zone(zone_id)
        if not manifold:
            return 0.0
        return manifold.pipe_volume / manifold.zones[zone_id]

    def get_state_for_persistence(self) -> dict[str, str]:
        """Return last_active_time as ISO strings for storage.

        Returns:
            Dict mapping manifold names to ISO datetime strings.
        """
        return {name: ts.isoformat() for name, ts in self._last_active_time.items() if ts is not None}

    def restore_state(self, state: dict[str, str]) -> None:
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
