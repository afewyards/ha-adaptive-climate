"""Persistence layer for adaptive learning data."""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import json
import logging
import os

from homeassistant.util import dt as dt_util

from ..const import MAX_CYCLE_HISTORY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store
    from .thermal_rates import ThermalRateLearner
    from .cycle_analysis import CycleMetrics

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "adaptive_thermostat_learning"
STORAGE_VERSION = 5
SAVE_DELAY_SECONDS = 30


def _create_store(hass, version: int, key: str):
    """Create a Store instance."""
    from homeassistant.helpers.storage import Store
    return Store(hass, version, key)


class LearningDataStore:
    """Persist learning data across Home Assistant restarts."""

    def __init__(self, hass_or_path):
        """
        Initialize the LearningDataStore.

        Args:
            hass_or_path: Either a HomeAssistant instance (new API) or a storage path string (legacy API)
        """
        # Support both new API (HomeAssistant instance) and legacy API (storage path)
        if isinstance(hass_or_path, str):
            # Legacy API - file I/O based (for backwards compatibility with existing tests)
            self.storage_path = hass_or_path
            self.storage_file = os.path.join(hass_or_path, "adaptive_thermostat_learning.json")
            self.hass = None
            self._store = None
            self._data = {"version": 5, "zones": {}}
            self._save_lock = None  # Legacy API doesn't need locks (synchronous)
        else:
            # New API - HA Store based
            self.hass = hass_or_path
            self.storage_path = None
            self.storage_file = None
            self._store = None
            self._data = {"version": 5, "zones": {}}
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
            _LOGGER.warning(
                f"Invalid data structure: version must be int, got {type(data['version']).__name__}"
            )
            return False

        # Check version is within supported range (1-5)
        if data["version"] < 1 or data["version"] > STORAGE_VERSION:
            _LOGGER.warning(
                f"Invalid data structure: version {data['version']} outside supported range (1-{STORAGE_VERSION})"
            )
            return False

        # Validate zones is a dict
        if not isinstance(data["zones"], dict):
            _LOGGER.warning(
                f"Invalid data structure: zones must be dict, got {type(data['zones']).__name__}"
            )
            return False

        # Validate each zone has a dict value
        for zone_id, zone_data in data["zones"].items():
            if not isinstance(zone_data, dict):
                _LOGGER.warning(
                    f"Invalid data structure: zone '{zone_id}' data must be dict, got {type(zone_data).__name__}"
                )
                return False

        return True

    async def async_load(self) -> Dict[str, Any]:
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
            self._data = {"version": 5, "zones": {}}
            return self._data

        # Validate loaded data
        if not self._validate_data(data):
            _LOGGER.warning(
                "Persisted learning data failed validation, using default structure"
            )
            self._data = {"version": 5, "zones": {}}
            return self._data

        self._data = data
        return data

    def get_zone_data(self, zone_id: str) -> Optional[Dict[str, Any]]:
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
        adaptive_data: Optional[Dict[str, Any]] = None,
        ke_data: Optional[Dict[str, Any]] = None,
        preheat_data: Optional[Dict[str, Any]] = None,
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
        # period will reset the timer, ensuring only one save occurs
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY_SECONDS)

        _LOGGER.debug(f"Scheduled zone save with {SAVE_DELAY_SECONDS}s delay")

    def update_zone_data(
        self,
        zone_id: str,
        adaptive_data: Optional[Dict[str, Any]] = None,
        ke_data: Optional[Dict[str, Any]] = None,
        preheat_data: Optional[Dict[str, Any]] = None,
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

    def load(self) -> Optional[Dict[str, Any]]:
        """
        Load learning data from storage.

        Returns:
            Dictionary with learning data, or None if file doesn't exist or is corrupt
        """
        if not os.path.exists(self.storage_file):
            _LOGGER.info(f"No existing learning data found at {self.storage_file}")
            return None

        try:
            with open(self.storage_file, "r") as f:
                data = json.load(f)

            # Validate version
            if "version" not in data:
                _LOGGER.warning("Learning data missing version field, treating as corrupt")
                return None

            _LOGGER.info(f"Learning data loaded from {self.storage_file}")
            return data

        except json.JSONDecodeError as e:
            _LOGGER.error(f"Corrupt learning data (invalid JSON): {e}")
            return None
        except Exception as e:
            _LOGGER.error(f"Failed to load learning data: {e}")
            return None

    def restore_thermal_learner(self, data: Dict[str, Any]) -> Optional["ThermalRateLearner"]:
        """
        Restore ThermalRateLearner from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored ThermalRateLearner instance, or None if data missing
        """
        if "thermal_learner" not in data:
            return None

        try:
            # Import here to avoid circular import
            from .thermal_rates import ThermalRateLearner

            thermal_data = data["thermal_learner"]
            learner = ThermalRateLearner(
                outlier_threshold=thermal_data.get("outlier_threshold", 2.0)
            )

            # Validate data types
            cooling_rates = thermal_data.get("cooling_rates", [])
            heating_rates = thermal_data.get("heating_rates", [])

            if not isinstance(cooling_rates, list):
                raise TypeError("cooling_rates must be a list")
            if not isinstance(heating_rates, list):
                raise TypeError("heating_rates must be a list")

            learner._cooling_rates = cooling_rates
            learner._heating_rates = heating_rates

            _LOGGER.info(
                f"Restored ThermalRateLearner: "
                f"{len(learner._cooling_rates)} cooling rates, "
                f"{len(learner._heating_rates)} heating rates"
            )
            return learner

        except Exception as e:
            _LOGGER.error(f"Failed to restore ThermalRateLearner: {e}")
            return None

    def restore_adaptive_learner(self, data: Dict[str, Any]) -> Optional[Any]:
        """
        Restore AdaptiveLearner from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored AdaptiveLearner instance, or None if data missing
        """
        if "adaptive_learner" not in data:
            return None

        try:
            # Import here to avoid circular import
            from .learning import AdaptiveLearner
            from .cycle_analysis import CycleMetrics

            adaptive_data = data["adaptive_learner"]
            max_history = adaptive_data.get("max_history", MAX_CYCLE_HISTORY)
            heating_type = adaptive_data.get("heating_type")
            learner = AdaptiveLearner(max_history=max_history, heating_type=heating_type)

            # Validate cycle history is a list
            cycle_history = adaptive_data.get("cycle_history", [])
            if not isinstance(cycle_history, list):
                raise TypeError("cycle_history must be a list")

            # Restore cycle history
            for cycle_data in cycle_history:
                if not isinstance(cycle_data, dict):
                    raise TypeError("cycle_data must be a dictionary")

                metrics = CycleMetrics(
                    overshoot=cycle_data.get("overshoot"),
                    undershoot=cycle_data.get("undershoot"),
                    settling_time=cycle_data.get("settling_time"),
                    oscillations=cycle_data.get("oscillations", 0),
                    rise_time=cycle_data.get("rise_time"),
                )
                learner.add_cycle_metrics(metrics)

            # Restore last adjustment time
            last_adj_time_str = adaptive_data.get("last_adjustment_time")
            if last_adj_time_str is not None:
                learner._last_adjustment_time = datetime.fromisoformat(last_adj_time_str)

            # Restore convergence tracking state (version 2+)
            learner._consecutive_converged_cycles = adaptive_data.get(
                "consecutive_converged_cycles", 0
            )
            learner._pid_converged_for_ke = adaptive_data.get(
                "pid_converged_for_ke", False
            )

            _LOGGER.info(
                f"Restored AdaptiveLearner: {learner.get_cycle_count()} cycles, "
                f"last adjustment: {learner._last_adjustment_time}, "
                f"converged_for_ke: {learner._pid_converged_for_ke}"
            )
            return learner

        except Exception as e:
            _LOGGER.error(f"Failed to restore AdaptiveLearner: {e}")
            return None

    def restore_valve_tracker(self, data: Dict[str, Any]) -> Optional[Any]:
        """
        Restore ValveCycleTracker from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored ValveCycleTracker instance, or None if data missing
        """
        if "valve_tracker" not in data:
            return None

        try:
            # Import here to avoid circular import
            from .learning import ValveCycleTracker

            valve_data = data["valve_tracker"]
            tracker = ValveCycleTracker()
            tracker._cycle_count = valve_data.get("cycle_count", 0)
            tracker._last_state = valve_data.get("last_state")

            _LOGGER.info(f"Restored ValveCycleTracker: {tracker._cycle_count} cycles")
            return tracker

        except Exception as e:
            _LOGGER.error(f"Failed to restore ValveCycleTracker: {e}")
            return None

    def restore_ke_learner(self, data: Dict[str, Any]) -> Optional[Any]:
        """
        Restore KeLearner from saved data.

        Args:
            data: Loaded data dictionary from load()

        Returns:
            Restored KeLearner instance, or None if data missing
        """
        if "ke_learner" not in data:
            return None

        try:
            # Import KeLearner here to avoid circular import
            from .ke_learning import KeLearner

            ke_data = data["ke_learner"]
            learner = KeLearner.from_dict(ke_data)

            _LOGGER.info(
                f"Restored KeLearner: ke={learner.current_ke:.2f}, "
                f"enabled={learner.enabled}, observations={learner.observation_count}"
            )
            return learner

        except Exception as e:
            _LOGGER.error(f"Failed to restore KeLearner: {e}")
            return None

    def restore_preheat_learner(self, data: Dict[str, Any]) -> Optional[Any]:
        """
        Restore PreheatLearner from saved data.

        Args:
            data: Loaded data dictionary from get_zone_data()

        Returns:
            Restored PreheatLearner instance, or None if data missing
        """
        if "preheat_learner" not in data:
            return None

        try:
            # Import PreheatLearner here to avoid circular import
            from .preheat import PreheatLearner

            preheat_data = data["preheat_learner"]
            learner = PreheatLearner.from_dict(preheat_data)

            _LOGGER.info(
                f"Restored PreheatLearner: heating_type={learner.heating_type}, "
                f"max_hours={learner.max_hours}, observations={learner.get_observation_count()}"
            )
            return learner

        except Exception as e:
            _LOGGER.error(f"Failed to restore PreheatLearner: {e}")
            return None

    async def async_load_manifold_state(self) -> Optional[Dict[str, str]]:
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

    async def async_save_manifold_state(self, state: Dict[str, str]) -> None:
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
