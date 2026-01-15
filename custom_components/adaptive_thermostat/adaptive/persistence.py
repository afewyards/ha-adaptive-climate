"""Persistence layer for adaptive learning data."""

from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import json
import logging
import os

from ..const import MAX_CYCLE_HISTORY

if TYPE_CHECKING:
    from .thermal_rates import ThermalRateLearner
    from .cycle_analysis import CycleMetrics

_LOGGER = logging.getLogger(__name__)


class LearningDataStore:
    """Persist learning data across Home Assistant restarts."""

    def __init__(self, storage_path: str):
        """
        Initialize the LearningDataStore.

        Args:
            storage_path: Path to storage directory (typically .storage/)
        """
        self.storage_path = storage_path
        self.storage_file = os.path.join(storage_path, "adaptive_thermostat_learning.json")

    def save(
        self,
        thermal_learner: Optional["ThermalRateLearner"] = None,
        adaptive_learner: Optional[Any] = None,  # AdaptiveLearner
        valve_tracker: Optional[Any] = None,  # ValveCycleTracker
        ke_learner: Optional[Any] = None,  # KeLearner
    ) -> bool:
        """
        Save learning data to storage.

        Args:
            thermal_learner: ThermalRateLearner instance to save
            adaptive_learner: AdaptiveLearner instance to save
            valve_tracker: ValveCycleTracker instance to save
            ke_learner: KeLearner instance to save

        Returns:
            True if save successful, False otherwise
        """
        try:
            # Ensure storage directory exists
            os.makedirs(self.storage_path, exist_ok=True)

            data: Dict[str, Any] = {
                "version": 2,  # Incremented for Ke learner support
                "last_updated": datetime.now().isoformat(),
            }

            # Save ThermalRateLearner data
            if thermal_learner is not None:
                data["thermal_learner"] = {
                    "cooling_rates": thermal_learner._cooling_rates,
                    "heating_rates": thermal_learner._heating_rates,
                    "outlier_threshold": thermal_learner.outlier_threshold,
                }

            # Save AdaptiveLearner data
            if adaptive_learner is not None:
                cycle_history = []
                for cycle in adaptive_learner._cycle_history:
                    cycle_history.append({
                        "overshoot": cycle.overshoot,
                        "undershoot": cycle.undershoot,
                        "settling_time": cycle.settling_time,
                        "oscillations": cycle.oscillations,
                        "rise_time": cycle.rise_time,
                    })

                data["adaptive_learner"] = {
                    "cycle_history": cycle_history,
                    "last_adjustment_time": (
                        adaptive_learner._last_adjustment_time.isoformat()
                        if adaptive_learner._last_adjustment_time is not None
                        else None
                    ),
                    "max_history": adaptive_learner._max_history,
                    "heating_type": adaptive_learner._heating_type,
                    # Include convergence tracking state
                    "consecutive_converged_cycles": adaptive_learner._consecutive_converged_cycles,
                    "pid_converged_for_ke": adaptive_learner._pid_converged_for_ke,
                }

            # Save ValveCycleTracker data
            if valve_tracker is not None:
                data["valve_tracker"] = {
                    "cycle_count": valve_tracker._cycle_count,
                    "last_state": valve_tracker._last_state,
                }

            # Save KeLearner data
            if ke_learner is not None:
                data["ke_learner"] = ke_learner.to_dict()

            # Write to file atomically
            temp_file = f"{self.storage_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)

            # Atomic rename
            os.replace(temp_file, self.storage_file)

            _LOGGER.info(f"Learning data saved to {self.storage_file}")
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to save learning data: {e}")
            return False

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
