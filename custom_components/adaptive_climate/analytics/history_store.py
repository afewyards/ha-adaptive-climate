"""Historical data storage for weekly reports.

Stores weekly snapshots for week-over-week comparisons.
Uses Home Assistant's storage helper for persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, TYPE_CHECKING
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "adaptive_climate_history"
STORAGE_VERSION = 2
MAX_WEEKS_TO_KEEP = 12


class _HistoryStoreBackend(Store):
    """Store subclass with v1→v2 migration support.

    v2 added optional per-zone fields (confidence, learning_status,
    recovery_cycles, humidity_pauses, contact_pauses). The data format
    is backward-compatible so migration is a no-op.
    """

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict,
    ) -> dict:
        """Migrate from older storage versions."""
        if old_major_version < 2:
            _LOGGER.info(
                "Migrating history store from v%d.%d to v%d",
                old_major_version,
                old_minor_version,
                STORAGE_VERSION,
            )
        return old_data


@dataclass
class ZoneSnapshot:
    """Snapshot of a single zone's weekly performance."""

    zone_id: str
    duty_cycle: float
    comfort_score: float | None
    time_at_target: float | None
    area_m2: float | None
    confidence: float | None = None
    learning_status: str | None = None
    recovery_cycles: int | None = None
    humidity_pauses: int | None = None
    contact_pauses: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ZoneSnapshot:
        """Create from dictionary."""
        return cls(
            zone_id=data["zone_id"],
            duty_cycle=data.get("duty_cycle", 0.0),
            comfort_score=data.get("comfort_score"),
            time_at_target=data.get("time_at_target"),
            area_m2=data.get("area_m2"),
            confidence=data.get("confidence"),
            learning_status=data.get("learning_status"),
            recovery_cycles=data.get("recovery_cycles"),
            humidity_pauses=data.get("humidity_pauses"),
            contact_pauses=data.get("contact_pauses"),
        )


@dataclass
class WeeklySnapshot:
    """Snapshot of a week's heating performance."""

    year: int
    week_number: int
    total_cost: float | None
    total_energy_kwh: float | None
    zones: dict[str, ZoneSnapshot]
    timestamp: str  # ISO format

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "year": self.year,
            "week_number": self.week_number,
            "total_cost": self.total_cost,
            "total_energy_kwh": self.total_energy_kwh,
            "zones": {k: v.to_dict() for k, v in self.zones.items()},
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeeklySnapshot:
        """Create from dictionary."""
        zones = {}
        for zone_id, zone_data in data.get("zones", {}).items():
            zones[zone_id] = ZoneSnapshot.from_dict(zone_data)

        return cls(
            year=data["year"],
            week_number=data["week_number"],
            total_cost=data.get("total_cost"),
            total_energy_kwh=data.get("total_energy_kwh"),
            zones=zones,
            timestamp=data.get("timestamp", dt_util.utcnow().isoformat()),
        )


class HistoryStore:
    """Manages persistent storage of weekly report history.

    Stores up to MAX_WEEKS_TO_KEEP weeks of data for comparisons.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the history store.

        Args:
            hass: Home Assistant instance
        """
        self.hass = hass
        self._store = None
        self._data: list[WeeklySnapshot] = []

    async def async_load(self) -> list[WeeklySnapshot]:
        """Load history from storage.

        Returns:
            List of weekly snapshots, newest first
        """
        if self._store is None:
            self._store = _HistoryStoreBackend(self.hass, STORAGE_VERSION, STORAGE_KEY)

        data = await self._store.async_load()
        if data is None:
            self._data = []
            return []

        snapshots = []
        for item in data.get("snapshots", []):
            try:
                snapshots.append(WeeklySnapshot.from_dict(item))
            except (KeyError, TypeError) as e:
                _LOGGER.warning("Failed to load snapshot: %s", e)

        # Sort by year/week descending (newest first)
        snapshots.sort(key=lambda s: (s.year, s.week_number), reverse=True)
        self._data = snapshots
        return snapshots

    async def async_save_snapshot(self, snapshot: WeeklySnapshot) -> None:
        """Save a weekly snapshot.

        Adds the snapshot and prunes old data beyond MAX_WEEKS_TO_KEEP.

        Args:
            snapshot: The weekly snapshot to save
        """
        if self._store is None:
            self._store = _HistoryStoreBackend(self.hass, STORAGE_VERSION, STORAGE_KEY)

        # Load existing if not already loaded
        if not self._data:
            await self.async_load()

        # Remove any existing snapshot for the same week
        self._data = [s for s in self._data if not (s.year == snapshot.year and s.week_number == snapshot.week_number)]

        # Add new snapshot
        self._data.append(snapshot)

        # Sort and prune
        self._data.sort(key=lambda s: (s.year, s.week_number), reverse=True)
        self._data = self._data[:MAX_WEEKS_TO_KEEP]

        # Save to storage
        await self._store.async_save({"snapshots": [s.to_dict() for s in self._data]})

        _LOGGER.debug(
            "Saved weekly snapshot for %d-W%02d, total stored: %d",
            snapshot.year,
            snapshot.week_number,
            len(self._data),
        )

    def get_previous_week(self) -> WeeklySnapshot | None:
        """Get the snapshot from the previous week.

        Returns:
            Previous week's snapshot or None if not available
        """
        now = dt_util.utcnow()
        current_year, current_week, _ = now.isocalendar()

        # Find the snapshot that's not the current week
        for snapshot in self._data:
            if snapshot.year == current_year and snapshot.week_number == current_week:
                continue
            # Return the first one that's not current week (they're sorted newest first)
            return snapshot

        return None

    def get_snapshot_for_week(self, year: int, week_number: int) -> WeeklySnapshot | None:
        """Get snapshot for a specific week.

        Args:
            year: ISO year
            week_number: ISO week number

        Returns:
            Snapshot for that week or None
        """
        for snapshot in self._data:
            if snapshot.year == year and snapshot.week_number == week_number:
                return snapshot
        return None

    def calculate_week_over_week(self, current: WeeklySnapshot) -> dict[str, float | None]:
        """Calculate week-over-week changes.

        Args:
            current: Current week's snapshot

        Returns:
            Dictionary with percentage changes:
            - cost_change_pct: Cost change percentage (negative = decrease)
            - energy_change_pct: Energy change percentage
        """
        previous = self.get_previous_week()
        if previous is None:
            return {
                "cost_change_pct": None,
                "energy_change_pct": None,
            }

        result = {}

        # Cost change
        if current.total_cost is not None and previous.total_cost is not None and previous.total_cost > 0:
            result["cost_change_pct"] = (current.total_cost - previous.total_cost) / previous.total_cost * 100
        else:
            result["cost_change_pct"] = None

        # Energy change
        if (
            current.total_energy_kwh is not None
            and previous.total_energy_kwh is not None
            and previous.total_energy_kwh > 0
        ):
            result["energy_change_pct"] = (
                (current.total_energy_kwh - previous.total_energy_kwh) / previous.total_energy_kwh * 100
            )
        else:
            result["energy_change_pct"] = None

        return result
