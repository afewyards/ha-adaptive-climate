"""Persistence layer for adaptive learning data."""

from __future__ import annotations

import asyncio
import copy
from typing import Any
import logging

from homeassistant.util import dt as dt_util


_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "adaptive_climate_learning"
STORAGE_VERSION = 5
SAVE_DELAY_SECONDS = 30


def _migrate_store(data: dict, from_version: int) -> dict:
    """Migrate store data from an older version to STORAGE_VERSION.

    For versions 1-4, the outer structure {"version": N, "zones": {...}} is the same.
    The inner zone data (learner serialization format) is handled separately by
    learner_serialization.py. We only need to bump the outer version number here.

    Returns a copy of data with version bumped to STORAGE_VERSION.
    Returns data unchanged if from_version >= STORAGE_VERSION.
    """
    if from_version >= STORAGE_VERSION:
        return data

    _LOGGER.warning(
        "Migrating store from v%s to v%s",
        from_version,
        STORAGE_VERSION,
    )
    migrated = dict(data)
    migrated["version"] = STORAGE_VERSION
    return migrated


def _create_store(hass, version: int, key: str):
    """Create a Store instance."""
    from homeassistant.helpers.storage import Store

    return Store(hass, version, key)


class LearningDataStore:
    """Persist learning data across Home Assistant restarts."""

    def __init__(self, hass):
        """
        Initialize the LearningDataStore.

        Args:
            hass: HomeAssistant instance
        """
        self.hass = hass
        self._store = None
        self._data = {"version": STORAGE_VERSION, "zones": {}}
        self._save_lock = None  # Lazily initialized in async context

    def _validate_data(self, data: Any) -> bool:
        """
        Validate loaded data structure.

        Args:
            data: Data loaded from storage

        Returns:
            True if data is valid, False otherwise
        """
        # Check that data is a dict
        if not isinstance(data, dict):
            _LOGGER.warning(f"Invalid data structure: expected dict, got {type(data).__name__}")
            return False

        # Check required keys exist
        if "version" not in data:
            _LOGGER.warning("Invalid data structure: missing 'version' key")
            return False

        if "zones" not in data:
            _LOGGER.warning("Invalid data structure: missing 'zones' key")
            return False

        # Validate version is an integer
        if not isinstance(data["version"], int):
            _LOGGER.warning(f"Invalid data structure: version must be int, got {type(data['version']).__name__}")
            return False

        # Check version is within supported range (1-5)
        if data["version"] < 1 or data["version"] > STORAGE_VERSION:
            _LOGGER.warning(
                f"Invalid data structure: version {data['version']} outside supported range (1-{STORAGE_VERSION})"
            )
            return False

        # Validate zones is a dict
        if not isinstance(data["zones"], dict):
            _LOGGER.warning(f"Invalid data structure: zones must be dict, got {type(data['zones']).__name__}")
            return False

        # Validate each zone has a dict value
        for zone_id, zone_data in data["zones"].items():
            if not isinstance(zone_data, dict):
                _LOGGER.warning(
                    f"Invalid data structure: zone '{zone_id}' data must be dict, got {type(zone_data).__name__}"
                )
                return False

        return True

    async def async_load(self) -> dict[str, Any]:
        """
        Load learning data from HA Store.

        Returns:
            Dictionary with learning data in v5 format (zone-keyed)
        """
        if self.hass is None:
            raise RuntimeError("async_load requires HomeAssistant instance")

        # Lazily initialize lock in async context
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        if self._store is None:
            self._store = _create_store(self.hass, STORAGE_VERSION, STORAGE_KEY)

        data = await self._store.async_load()

        if data is None:
            # No existing data - return default structure
            self._data = {"version": STORAGE_VERSION, "zones": {}}
            return self._data

        # Validate loaded data
        if not self._validate_data(data):
            _LOGGER.warning("Persisted learning data failed validation, using default structure")
            self._data = {"version": STORAGE_VERSION, "zones": {}}
            return self._data

        # Migrate older store versions to current
        if data["version"] < STORAGE_VERSION:
            data = _migrate_store(data, data["version"])

        self._data = data
        return data

    def get_zone_data(self, zone_id: str) -> dict[str, Any] | None:
        """
        Get learning data for a specific zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Zone data dictionary or None if zone doesn't exist
        """
        return self._data["zones"].get(zone_id)

    async def async_save_zone(
        self,
        zone_id: str,
        adaptive_data: dict[str, Any] | None = None,
        ke_data: dict[str, Any] | None = None,
        preheat_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Save learning data for a specific zone.

        Args:
            zone_id: Zone identifier
            adaptive_data: AdaptiveLearner data dictionary
            ke_data: KeLearner data dictionary
            preheat_data: PreheatLearner data dictionary
        """
        if self.hass is None:
            raise RuntimeError("async_save_zone requires HomeAssistant instance")

        if self._store is None:
            raise RuntimeError("Store not initialized - call async_load() first")

        # Lazily initialize lock if needed (should be done by async_load, but safety check)
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        async with self._save_lock:
            # Ensure zone exists in data structure
            if zone_id not in self._data["zones"]:
                self._data["zones"][zone_id] = {}

            zone_data = self._data["zones"][zone_id]

            # Update adaptive learner data
            if adaptive_data is not None:
                zone_data["adaptive_learner"] = adaptive_data

            # Update Ke learner data
            if ke_data is not None:
                zone_data["ke_learner"] = ke_data

            # Update preheat learner data
            if preheat_data is not None:
                zone_data["preheat_learner"] = preheat_data

            # Update timestamp
            zone_data["last_updated"] = dt_util.utcnow().isoformat()

            # Save to disk
            await self._store.async_save(self._data)

            _LOGGER.debug(
                f"Saved learning data for zone '{zone_id}': "
                f"adaptive={adaptive_data is not None}, ke={ke_data is not None}, "
                f"preheat={preheat_data is not None}"
            )

    def load_pwm_coordination(self) -> Any:
        """Read additive restart journals from the already loaded HA Store."""
        return copy.deepcopy(self._data.get("pwm_coordination", {}))

    async def async_save_pwm_coordination(self, name: str, state: dict[str, Any]) -> None:
        """Durably journal one group without replacing learner/other group data."""
        if self._store is None or self._save_lock is None:
            raise RuntimeError("Store not initialized - call async_load() first")
        async with self._save_lock:
            journals = self._data.setdefault("pwm_coordination", {})
            if not isinstance(journals, dict):
                raise ValueError("Invalid PWM restart storage")
            journals[name] = copy.deepcopy(state)
            await self._store.async_save(self._data)

    def schedule_zone_save(self) -> None:
        """
        Schedule a delayed save operation.

        Uses HA Store's async_delay_save() to debounce frequent save operations.
        The save will be executed after SAVE_DELAY_SECONDS (30s) unless another
        schedule_zone_save() call resets the timer.
        """
        if self.hass is None:
            raise RuntimeError("schedule_zone_save requires HomeAssistant instance")

        if self._store is None:
            raise RuntimeError("Store not initialized - call async_load() first")

        # Schedule delayed save with 30-second delay
        # The Store helper handles debouncing - multiple calls within the delay
        # period will reset the timer, ensuring only one save occurs.
        # Late binding is intentional: the lambda captures self._data by reference so
        # that if update_zone_data() is called again before the timer fires, the most
        # recent data is written (not a stale snapshot).
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY_SECONDS)

        _LOGGER.debug(f"Scheduled zone save with {SAVE_DELAY_SECONDS}s delay")

    def update_zone_data(
        self,
        zone_id: str,
        adaptive_data: dict[str, Any] | None = None,
        ke_data: dict[str, Any] | None = None,
        preheat_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Update zone data in memory without triggering immediate save.

        This method updates the internal data structure but does not persist
        to disk. Call schedule_zone_save() after to trigger a debounced save.

        Args:
            zone_id: Zone identifier
            adaptive_data: AdaptiveLearner data dictionary (optional)
            ke_data: KeLearner data dictionary (optional)
            preheat_data: PreheatLearner data dictionary (optional)
        """
        # Ensure zone exists in data structure
        if zone_id not in self._data["zones"]:
            self._data["zones"][zone_id] = {}

        zone_data = self._data["zones"][zone_id]

        # Update adaptive learner data
        if adaptive_data is not None:
            zone_data["adaptive_learner"] = adaptive_data

        # Update Ke learner data
        if ke_data is not None:
            zone_data["ke_learner"] = ke_data

        # Update preheat learner data
        if preheat_data is not None:
            zone_data["preheat_learner"] = preheat_data

        # Update timestamp
        zone_data["last_updated"] = dt_util.utcnow().isoformat()

        _LOGGER.debug(
            f"Updated zone data for '{zone_id}' in memory: "
            f"adaptive={adaptive_data is not None}, ke={ke_data is not None}, "
            f"preheat={preheat_data is not None}"
        )

    async def async_load_manifold_state(self) -> dict[str, str] | None:
        """
        Load manifold state from HA Store.

        Returns:
            Dictionary mapping manifold names to ISO datetime strings, or None if not found.
        """
        if self.hass is None:
            raise RuntimeError("async_load_manifold_state requires HomeAssistant instance")

        if self._store is None:
            raise RuntimeError("Store not initialized - call async_load() first")

        # Manifold state is stored at top level in the data structure
        manifold_state = self._data.get("manifold_state")
        if manifold_state is None:
            _LOGGER.debug("No manifold state found in storage")
            return None

        _LOGGER.info("Loaded manifold state: %d manifolds", len(manifold_state))
        return manifold_state

    async def async_save_manifold_state(self, state: dict[str, str]) -> None:
        """
        Save manifold state to HA Store.

        Args:
            state: Dict mapping manifold names to ISO datetime strings.
        """
        if self.hass is None:
            raise RuntimeError("async_save_manifold_state requires HomeAssistant instance")

        if self._store is None:
            raise RuntimeError("Store not initialized - call async_load() first")

        # Lazily initialize lock if needed
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        async with self._save_lock:
            # Store manifold state at top level
            self._data["manifold_state"] = state

            # Save to disk
            await self._store.async_save(self._data)

            _LOGGER.debug("Saved manifold state: %d manifolds", len(state))

    async def async_load_water_temp_state(self) -> dict[str, Any] | None:
        """
        Load water temperature control state from HA Store.

        Returns:
            Dict with per-mode ramp state and last-written values, or None if
            no state has been persisted yet.
        """
        if self.hass is None:
            raise RuntimeError("async_load_water_temp_state requires HomeAssistant instance")

        if self._store is None:
            raise RuntimeError("Store not initialized - call async_load() first")

        # Water temp state is stored at top level, mirroring manifold_state.
        # This is additive: _validate_data only requires version + zones, so no
        # STORAGE_VERSION bump is needed.
        water_temp_state = self._data.get("water_temp_state")
        if water_temp_state is None:
            _LOGGER.debug("No water temperature state found in storage")
            return None

        _LOGGER.info("Loaded water temperature control state")
        # Return a deep copy - callers must never be able to mutate our
        # internal _data by mutating the dict they got back from load().
        return copy.deepcopy(water_temp_state)

    async def async_save_water_temp_state(self, state: dict[str, Any]) -> None:
        """
        Save water temperature control state to HA Store.

        Args:
            state: Dict with per-mode ramp state (ISO timestamps) and the last
                value written to each target entity.
        """
        if self.hass is None:
            raise RuntimeError("async_save_water_temp_state requires HomeAssistant instance")

        if self._store is None:
            raise RuntimeError("Store not initialized - call async_load() first")

        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        async with self._save_lock:
            # Store a deep copy - a debounced zone-save (schedule_zone_save)
            # serializes self._data later on its own timer, and the caller may
            # keep mutating `state` (e.g. an in-progress ramp record) after
            # this call returns. Without a copy that later save could persist
            # a torn/partially-mutated record.
            self._data["water_temp_state"] = copy.deepcopy(state)
            await self._store.async_save(self._data)
            _LOGGER.debug("Saved water temperature control state")
