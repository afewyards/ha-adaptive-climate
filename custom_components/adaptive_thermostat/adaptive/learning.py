"""Thermal rate learning and adaptive PID adjustments for Adaptive Thermostat."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import statistics
import logging
import json
import os

from ..const import (
    PID_LIMITS,
    MIN_CYCLES_FOR_LEARNING,
    MAX_CYCLE_HISTORY,
    MIN_ADJUSTMENT_INTERVAL,
    CONVERGENCE_THRESHOLDS,
    MIN_CONVERGENCE_CYCLES_FOR_KE,
)

# Import PID rule engine components
from .pid_rules import (
    PIDRule,
    PIDRuleResult,
    evaluate_pid_rules,
    detect_rule_conflicts,
    resolve_rule_conflicts,
)

# Import and re-export ThermalRateLearner for backward compatibility
from .thermal_rates import ThermalRateLearner

# Import and re-export cycle analysis components for backward compatibility
from .cycle_analysis import (
    PhaseAwareOvershootTracker,
    CycleMetrics,
    calculate_overshoot,
    calculate_undershoot,
    count_oscillations,
    calculate_settling_time,
)

_LOGGER = logging.getLogger(__name__)


# Adaptive learning (CycleMetrics imported from cycle_analysis)


class AdaptiveLearner:
    """Adaptive PID tuning based on observed heating cycle performance."""

    def __init__(self, max_history: int = MAX_CYCLE_HISTORY):
        """
        Initialize the AdaptiveLearner.

        Args:
            max_history: Maximum number of cycles to retain in history (FIFO eviction)
        """
        self._cycle_history: List[CycleMetrics] = []
        self._max_history = max_history
        self._last_adjustment_time: Optional[datetime] = None
        # Convergence tracking for Ke learning activation
        self._consecutive_converged_cycles: int = 0
        self._pid_converged_for_ke: bool = False

    @property
    def cycle_history(self) -> List[CycleMetrics]:
        """Return cycle history for external access."""
        return self._cycle_history

    @cycle_history.setter
    def cycle_history(self, value: List[CycleMetrics]) -> None:
        """Set cycle history (primarily for testing)."""
        self._cycle_history = value

    def add_cycle_metrics(self, metrics: CycleMetrics) -> None:
        """
        Add a cycle's performance metrics to history.

        Implements FIFO eviction when history exceeds max_history.

        Args:
            metrics: CycleMetrics object with performance data
        """
        self._cycle_history.append(metrics)

        # FIFO eviction: remove oldest entries when exceeding max history
        if len(self._cycle_history) > self._max_history:
            evicted_count = len(self._cycle_history) - self._max_history
            self._cycle_history = self._cycle_history[-self._max_history:]
            _LOGGER.debug(
                f"Cycle history exceeded max ({self._max_history}), "
                f"evicted {evicted_count} oldest entries"
            )

    def get_cycle_count(self) -> int:
        """
        Get number of stored cycle metrics.

        Returns:
            Number of cycles in history
        """
        return len(self._cycle_history)

    def _check_convergence(
        self,
        avg_overshoot: float,
        avg_oscillations: float,
        avg_settling_time: float,
        avg_rise_time: float,
    ) -> bool:
        """
        Check if the system has converged (is well-tuned).

        Convergence occurs when ALL performance metrics are within acceptable bounds
        defined by CONVERGENCE_THRESHOLDS.

        Args:
            avg_overshoot: Average overshoot in °C
            avg_oscillations: Average number of oscillations
            avg_settling_time: Average settling time in minutes
            avg_rise_time: Average rise time in minutes

        Returns:
            True if converged, False otherwise
        """
        is_converged = (
            avg_overshoot <= CONVERGENCE_THRESHOLDS["overshoot_max"] and
            avg_oscillations <= CONVERGENCE_THRESHOLDS["oscillations_max"] and
            avg_settling_time <= CONVERGENCE_THRESHOLDS["settling_time_max"] and
            avg_rise_time <= CONVERGENCE_THRESHOLDS["rise_time_max"]
        )

        if is_converged:
            _LOGGER.info(
                f"PID convergence detected - system tuned: "
                f"overshoot={avg_overshoot:.2f}°C, oscillations={avg_oscillations:.1f}, "
                f"settling={avg_settling_time:.1f}min, rise={avg_rise_time:.1f}min"
            )

        return is_converged

    def _check_rate_limit(
        self,
        min_interval_hours: int = MIN_ADJUSTMENT_INTERVAL,
    ) -> bool:
        """
        Check if enough time has passed since the last PID adjustment.

        Args:
            min_interval_hours: Minimum hours between adjustments

        Returns:
            True if rate limited (adjustment should be skipped), False if OK to adjust
        """
        if self._last_adjustment_time is None:
            return False

        time_since_last = datetime.now() - self._last_adjustment_time
        min_interval = timedelta(hours=min_interval_hours)

        if time_since_last < min_interval:
            hours_remaining = (min_interval - time_since_last).total_seconds() / 3600
            _LOGGER.info(
                f"PID adjustment rate limited: last adjustment was "
                f"{time_since_last.total_seconds() / 3600:.1f}h ago, "
                f"minimum interval is {min_interval_hours}h "
                f"({hours_remaining:.1f}h remaining)"
            )
            return True

        return False

    def calculate_pid_adjustment(
        self,
        current_kp: float,
        current_ki: float,
        current_kd: float,
        min_cycles: int = MIN_CYCLES_FOR_LEARNING,
        min_interval_hours: int = MIN_ADJUSTMENT_INTERVAL,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate PID adjustments based on observed cycle performance.

        Implements priority-based rule conflict resolution:
        - Priority 3 (highest): Oscillation rules (safety)
        - Priority 2: Overshoot rules (stability)
        - Priority 1 (lowest): Response time rules (performance)

        When rules conflict (e.g., overshoot says decrease Kp, slow response says increase),
        the higher priority rule wins.

        Also detects convergence (system is well-tuned) and skips adjustments.
        Rate limiting prevents adjustments too frequently.

        Args:
            current_kp: Current proportional gain
            current_ki: Current integral gain
            current_kd: Current derivative gain
            min_cycles: Minimum cycles required before making recommendations
            min_interval_hours: Minimum hours between adjustments (rate limiting)

        Returns:
            Dictionary with recommended kp, ki, kd values, or None if insufficient data,
            system is converged, or rate limited
        """
        # Check rate limiting first
        if self._check_rate_limit(min_interval_hours):
            return None

        if len(self._cycle_history) < min_cycles:
            _LOGGER.debug(
                f"Insufficient cycles for learning: {len(self._cycle_history)} < {min_cycles}"
            )
            return None

        # Calculate average metrics from recent cycles
        recent_cycles = self._cycle_history[-min_cycles:]

        avg_overshoot = statistics.mean(
            [c.overshoot for c in recent_cycles if c.overshoot is not None]
        ) if any(c.overshoot is not None for c in recent_cycles) else 0.0

        avg_undershoot = statistics.mean(
            [c.undershoot for c in recent_cycles if c.undershoot is not None]
        ) if any(c.undershoot is not None for c in recent_cycles) else 0.0

        avg_settling_time = statistics.mean(
            [c.settling_time for c in recent_cycles if c.settling_time is not None]
        ) if any(c.settling_time is not None for c in recent_cycles) else 0.0

        avg_oscillations = statistics.mean([c.oscillations for c in recent_cycles])

        avg_rise_time = statistics.mean(
            [c.rise_time for c in recent_cycles if c.rise_time is not None]
        ) if any(c.rise_time is not None for c in recent_cycles) else 0.0

        # Check for convergence - skip adjustments if system is tuned
        if self._check_convergence(
            avg_overshoot, avg_oscillations, avg_settling_time, avg_rise_time
        ):
            _LOGGER.info("Skipping PID adjustment - system has converged")
            return None

        # Evaluate all applicable rules
        rule_results = evaluate_pid_rules(
            avg_overshoot, avg_undershoot, avg_oscillations,
            avg_rise_time, avg_settling_time
        )

        if not rule_results:
            _LOGGER.debug("No PID rules triggered - metrics within acceptable ranges")
            return None

        # Detect and resolve conflicts
        conflicts = detect_rule_conflicts(rule_results)
        if conflicts:
            _LOGGER.info(f"Detected {len(conflicts)} PID rule conflict(s)")
            rule_results = resolve_rule_conflicts(rule_results, conflicts)

        # Apply resolved rules
        new_kp = current_kp
        new_ki = current_ki
        new_kd = current_kd

        for result in rule_results:
            if result.kp_factor != 1.0:
                _LOGGER.info(f"{result.reason}: Kp *= {result.kp_factor:.2f}")
            if result.ki_factor != 1.0:
                _LOGGER.info(f"{result.reason}: Ki *= {result.ki_factor:.2f}")
            if result.kd_factor != 1.0:
                _LOGGER.info(f"{result.reason}: Kd *= {result.kd_factor:.2f}")

            new_kp *= result.kp_factor
            new_ki *= result.ki_factor
            new_kd *= result.kd_factor

        # Enforce PID limits
        new_kp = max(PID_LIMITS["kp_min"], min(PID_LIMITS["kp_max"], new_kp))
        new_ki = max(PID_LIMITS["ki_min"], min(PID_LIMITS["ki_max"], new_ki))
        new_kd = max(PID_LIMITS["kd_min"], min(PID_LIMITS["kd_max"], new_kd))

        # Record adjustment time for rate limiting
        self._last_adjustment_time = datetime.now()

        return {
            "kp": new_kp,
            "ki": new_ki,
            "kd": new_kd,
        }

    def get_last_adjustment_time(self) -> Optional[datetime]:
        """
        Get the timestamp of the last PID adjustment.

        Returns:
            datetime of last adjustment, or None if no adjustment has been made
        """
        return self._last_adjustment_time

    def clear_history(self) -> None:
        """Clear all stored cycle metrics and reset last adjustment time."""
        self._cycle_history.clear()
        self._last_adjustment_time = None

    def update_convergence_tracking(self, metrics: CycleMetrics) -> bool:
        """Update convergence tracking based on latest cycle metrics.

        Tracks consecutive converged cycles to determine when PID is stable
        enough for Ke learning to begin.

        Args:
            metrics: The latest cycle metrics to evaluate

        Returns:
            True if PID is now converged for Ke learning, False otherwise
        """
        # Check if this cycle meets convergence thresholds
        overshoot = metrics.overshoot if metrics.overshoot is not None else 0.0
        oscillations = metrics.oscillations
        settling_time = metrics.settling_time if metrics.settling_time is not None else 0.0
        rise_time = metrics.rise_time if metrics.rise_time is not None else 0.0

        is_cycle_converged = (
            overshoot <= CONVERGENCE_THRESHOLDS["overshoot_max"] and
            oscillations <= CONVERGENCE_THRESHOLDS["oscillations_max"] and
            settling_time <= CONVERGENCE_THRESHOLDS["settling_time_max"] and
            rise_time <= CONVERGENCE_THRESHOLDS["rise_time_max"]
        )

        if is_cycle_converged:
            self._consecutive_converged_cycles += 1
            _LOGGER.debug(
                "Convergence tracking: cycle converged (%d consecutive)",
                self._consecutive_converged_cycles,
            )

            # Check if we've reached the threshold for Ke learning
            if (self._consecutive_converged_cycles >= MIN_CONVERGENCE_CYCLES_FOR_KE and
                    not self._pid_converged_for_ke):
                self._pid_converged_for_ke = True
                _LOGGER.info(
                    "PID converged for Ke learning after %d consecutive cycles",
                    self._consecutive_converged_cycles,
                )
        else:
            # Reset consecutive counter on non-converged cycle
            if self._consecutive_converged_cycles > 0:
                _LOGGER.debug(
                    "Convergence tracking: cycle not converged, resetting "
                    "counter (was %d)",
                    self._consecutive_converged_cycles,
                )
            self._consecutive_converged_cycles = 0
            self._pid_converged_for_ke = False

        return self._pid_converged_for_ke

    def is_pid_converged_for_ke(self) -> bool:
        """Check if PID has converged sufficiently for Ke learning.

        Returns:
            True if PID has had MIN_CONVERGENCE_CYCLES_FOR_KE consecutive
            converged cycles, False otherwise
        """
        return self._pid_converged_for_ke

    def get_consecutive_converged_cycles(self) -> int:
        """Get the number of consecutive converged cycles.

        Returns:
            Number of consecutive cycles meeting convergence thresholds
        """
        return self._consecutive_converged_cycles

    def reset_ke_convergence(self) -> None:
        """Reset Ke convergence tracking.

        Call this when PID values are changed or Ke learning needs to restart.
        """
        old_converged = self._pid_converged_for_ke
        old_count = self._consecutive_converged_cycles
        self._consecutive_converged_cycles = 0
        self._pid_converged_for_ke = False
        if old_converged or old_count > 0:
            _LOGGER.info(
                "Ke convergence reset (was: converged=%s, consecutive=%d)",
                old_converged,
                old_count,
            )


# PWM auto-tuning


def calculate_pwm_adjustment(
    cycle_times: List[float],
    current_pwm_period: float,
    min_pwm_period: float = 180.0,
    max_pwm_period: float = 1800.0,
    short_cycle_threshold: float = 10.0,
) -> Optional[float]:
    """
    Calculate PWM period adjustment based on observed cycling behavior.

    Detects short cycling and increases PWM period to reduce valve wear.
    Short cycling occurs when the heating switches on/off too frequently.

    Args:
        cycle_times: List of cycle times in minutes
        current_pwm_period: Current PWM period in seconds
        min_pwm_period: Minimum allowed PWM period in seconds (default 180s = 3 min)
        max_pwm_period: Maximum allowed PWM period in seconds (default 1800s = 30 min)
        short_cycle_threshold: Threshold for short cycling in minutes (default 10 min)

    Returns:
        Recommended PWM period in seconds, or None if insufficient data
    """
    if not cycle_times or len(cycle_times) < 3:
        return None

    # Calculate average cycle time
    avg_cycle_time = statistics.mean(cycle_times)

    # Check for short cycling
    if avg_cycle_time < short_cycle_threshold:
        # Increase PWM period to reduce cycling
        # Increase by 20% for each minute below threshold
        shortage = short_cycle_threshold - avg_cycle_time
        increase_factor = 1.0 + (shortage / short_cycle_threshold) * 0.2
        new_pwm_period = current_pwm_period * increase_factor

        # Enforce bounds
        new_pwm_period = max(min_pwm_period, min(max_pwm_period, new_pwm_period))

        _LOGGER.info(
            f"Short cycling detected (avg {avg_cycle_time:.1f} min): "
            f"increasing PWM period from {current_pwm_period:.0f}s to {new_pwm_period:.0f}s"
        )

        return new_pwm_period

    # If cycles are acceptable, keep current PWM period
    return current_pwm_period


class ValveCycleTracker:
    """Track valve cycles for wear monitoring."""

    def __init__(self):
        """Initialize the ValveCycleTracker."""
        self._cycle_count: int = 0
        self._last_state: Optional[bool] = None

    def update(self, valve_open: bool) -> None:
        """
        Update valve state and count cycles.

        A cycle is counted each time the valve transitions from closed to open.

        Args:
            valve_open: Current valve state (True = open, False = closed)
        """
        if self._last_state is not None and not self._last_state and valve_open:
            # Transition from closed to open
            self._cycle_count += 1

        self._last_state = valve_open

    def get_cycle_count(self) -> int:
        """
        Get total number of valve cycles.

        Returns:
            Total cycle count
        """
        return self._cycle_count

    def reset(self) -> None:
        """Reset cycle counter."""
        self._cycle_count = 0
        self._last_state = None


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
        thermal_learner: Optional[ThermalRateLearner] = None,
        adaptive_learner: Optional[AdaptiveLearner] = None,
        valve_tracker: Optional[ValveCycleTracker] = None,
        ke_learner: Optional[Any] = None,  # KeLearner, imported dynamically to avoid circular import
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

    def restore_thermal_learner(self, data: Dict[str, Any]) -> Optional[ThermalRateLearner]:
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

    def restore_adaptive_learner(self, data: Dict[str, Any]) -> Optional[AdaptiveLearner]:
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
            adaptive_data = data["adaptive_learner"]
            max_history = adaptive_data.get("max_history", MAX_CYCLE_HISTORY)
            learner = AdaptiveLearner(max_history=max_history)

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

    def restore_valve_tracker(self, data: Dict[str, Any]) -> Optional[ValveCycleTracker]:
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
