"""Cycle metrics recording for adaptive learning.

This module provides the CycleMetricsRecorder class which validates cycles,
calculates metrics, and records them with the adaptive learner.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from ..adaptive.learning import AdaptiveLearner
    from ..adaptive.cycle_analysis import CycleMetrics
    from .events import CycleEventDispatcher

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class CycleMetricsRecorder:
    """Records and validates cycle metrics for adaptive learning.

    This class is responsible for:
    1. Validating cycles meet minimum requirements
    2. Calculating all cycle metrics (overshoot, undershoot, settling time, etc.)
    3. Recording metrics with the adaptive learner
    4. Handling validation mode and rollbacks
    5. Managing integral decay tracking
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_id: str,
        adaptive_learner: AdaptiveLearner,
        get_target_temp: Callable[[], float | None],
        get_current_temp: Callable[[], float | None],
        get_hvac_mode: Callable[[], str],
        get_in_grace_period: Callable[[], bool],
        min_cycle_duration_minutes: int,
        get_outdoor_temp: Callable[[], float | None] | None = None,
        on_validation_failed: Callable[[], Awaitable[None]] | None = None,
        on_auto_apply_check: Callable[[], Awaitable[None]] | None = None,
        dispatcher: "CycleEventDispatcher | None" = None,
        cold_tolerance: float | None = None,
    ) -> None:
        """Initialize the cycle metrics recorder.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            adaptive_learner: Adaptive learning instance for storing cycle metrics
            get_target_temp: Callback to get current target temperature
            get_current_temp: Callback to get current temperature
            get_hvac_mode: Callback to get current HVAC mode
            get_in_grace_period: Callback to check if in learning grace period
            min_cycle_duration_minutes: Minimum cycle duration for validation
            get_outdoor_temp: Callback to get outdoor temperature (optional)
            on_validation_failed: Async callback for validation failure (triggers rollback)
            on_auto_apply_check: Async callback for auto-apply check after cycle completion
            dispatcher: Optional CycleEventDispatcher for event-driven operation
            cold_tolerance: Cold tolerance threshold for integral tracking
        """
        self._hass = hass
        self._zone_id = zone_id
        self._adaptive_learner = adaptive_learner
        self._get_target_temp = get_target_temp
        self._get_current_temp = get_current_temp
        self._get_hvac_mode = get_hvac_mode
        self._get_in_grace_period = get_in_grace_period
        self._get_outdoor_temp = get_outdoor_temp
        self._on_validation_failed = on_validation_failed
        self._on_auto_apply_check = on_auto_apply_check
        self._dispatcher = dispatcher
        self._min_cycle_duration_minutes = min_cycle_duration_minutes
        self._cold_tolerance = cold_tolerance

        # Metrics tracking state
        self._interruption_history: list[tuple[datetime, str]] = []
        self._was_clamped: bool = False
        self._device_on_time: datetime | None = None
        self._device_off_time: datetime | None = None
        self._integral_at_tolerance_entry: float | None = None
        self._integral_at_setpoint_cross: float | None = None
        self._prev_cycle_end_temp: float | None = None
        self._transport_delay_minutes: float | None = None

        # Logging
        self._logger = logging.getLogger(f"{__name__}.{zone_id}")

    def reset_cycle_metrics(self) -> None:
        """Reset all metrics tracking state for a new cycle."""
        self._interruption_history.clear()
        self._was_clamped = False
        self._integral_at_tolerance_entry = None
        self._integral_at_setpoint_cross = None
        self._transport_delay_minutes = None

    def add_interruption(self, timestamp: datetime, interruption_type: str) -> None:
        """Record an interruption in the current cycle.

        Args:
            timestamp: When the interruption occurred
            interruption_type: Type of interruption (from InterruptionType enum value)
        """
        self._interruption_history.append((timestamp, interruption_type))

    def set_clamped(self, was_clamped: bool) -> None:
        """Set whether the PID was clamped during the current cycle.

        Args:
            was_clamped: True if PID was clamped
        """
        self._was_clamped = was_clamped

    def set_device_on_time(self, timestamp: datetime) -> None:
        """Set when the heating/cooling device turned on.

        Args:
            timestamp: Device on time
        """
        self._device_on_time = timestamp

    def set_device_off_time(self, timestamp: datetime) -> None:
        """Set when the heating/cooling device turned off.

        Args:
            timestamp: Device off time
        """
        self._device_off_time = timestamp

    def track_integral_at_tolerance(self, pid_error: float, pid_integral: float) -> None:
        """Track integral value when temperature enters cold tolerance zone.

        Only captures the first time per cycle when pid_error < cold_tolerance.

        Args:
            pid_error: Current PID error (target - current)
            pid_integral: Current integral value
        """
        if self._integral_at_tolerance_entry is None and self._cold_tolerance is not None:
            if pid_error < self._cold_tolerance:
                self._integral_at_tolerance_entry = pid_integral
                self._logger.debug(
                    "Captured integral at tolerance entry: %.2f (pid_error=%.2f < cold_tolerance=%.2f)",
                    pid_integral,
                    pid_error,
                    self._cold_tolerance,
                )

    def track_integral_at_setpoint(self, pid_error: float, pid_integral: float) -> None:
        """Track integral value when temperature crosses setpoint.

        Only captures the first time per cycle when pid_error <= 0.

        Args:
            pid_error: Current PID error (target - current)
            pid_integral: Current integral value
        """
        if self._integral_at_setpoint_cross is None:
            if pid_error <= 0.0:
                self._integral_at_setpoint_cross = pid_integral
                self._logger.debug(
                    "Captured integral at setpoint cross: %.2f (pid_error=%.2f)",
                    pid_integral,
                    pid_error,
                )

    def set_transport_delay(self, minutes: float) -> None:
        """Set transport delay for current cycle.

        Args:
            minutes: Transport delay in minutes (can be 0 for warm manifold)
        """
        self._transport_delay_minutes = minutes
        self._logger.debug(
            "Transport delay set to %.1f minutes for current cycle",
            minutes
        )

    def _calculate_mad(self, values: list[float]) -> float:
        """Calculate Median Absolute Deviation (MAD) for robust variability measure.

        MAD is more robust to outliers than standard deviation/variance.
        Formula: median(|values - median(values)|)

        Args:
            values: List of numeric values

        Returns:
            Median absolute deviation
        """
        if not values:
            return 0.0

        # Calculate median
        sorted_values = sorted(values)
        n = len(sorted_values)
        if n % 2 == 0:
            median = (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2
        else:
            median = sorted_values[n // 2]

        # Calculate absolute deviations
        abs_deviations = [abs(v - median) for v in values]

        # Return median of absolute deviations
        sorted_devs = sorted(abs_deviations)
        n = len(sorted_devs)
        if n % 2 == 0:
            mad = (sorted_devs[n // 2 - 1] + sorted_devs[n // 2]) / 2
        else:
            mad = sorted_devs[n // 2]

        return mad

    def _calculate_decay_metrics(self) -> tuple[float | None, float | None, float | None]:
        """Calculate decay-related integral metrics.

        Computes the decay contribution as the difference between integral values
        at tolerance entry and setpoint crossing.

        Returns:
            Tuple of (integral_at_tolerance_entry, integral_at_setpoint_cross, decay_contribution)
            decay_contribution is None if either integral value is missing
        """
        integral_at_tolerance = self._integral_at_tolerance_entry
        integral_at_setpoint = self._integral_at_setpoint_cross

        # Calculate decay contribution only if both values are captured
        decay_contribution = None
        if integral_at_tolerance is not None and integral_at_setpoint is not None:
            decay_contribution = integral_at_tolerance - integral_at_setpoint

        return integral_at_tolerance, integral_at_setpoint, decay_contribution

    def _is_cycle_valid(
        self,
        cycle_start_time: datetime | None,
        temperature_history: list[tuple[datetime, float]],
        current_time: datetime | None = None,
    ) -> tuple[bool, str]:
        """Check if the current cycle is valid for recording.

        A cycle is valid if:
        1. Duration >= minimum cycle duration (5 minutes)
        2. Not in learning grace period
        3. Learning is enabled (not in vacation mode)
        4. Sufficient temperature samples (>= 5)

        Args:
            cycle_start_time: When the cycle started
            temperature_history: List of (timestamp, temperature) samples
            current_time: Current time for duration calculation (defaults to dt_util.utcnow())

        Returns:
            Tuple of (is_valid, reason_string)
        """
        # Check minimum duration
        if cycle_start_time is None:
            return False, "No cycle start time recorded"

        if current_time is None:
            current_time = dt_util.utcnow()
        duration_minutes = (current_time - cycle_start_time).total_seconds() / 60
        if duration_minutes < self._min_cycle_duration_minutes:
            return False, f"Cycle too short ({duration_minutes:.1f} min < {self._min_cycle_duration_minutes} min)"

        # Check learning grace period
        if self._get_in_grace_period():
            return False, "In learning grace period"

        # Check vacation mode (learning_enabled)
        # Note: This is checked via zone_data["learning_enabled"] which is set to False in vacation mode
        # For now, we'll skip this check as it requires coordination with climate entity
        # The get_in_grace_period callback handles the grace period, and vacation mode
        # should be checked at a higher level before calling cycle tracking methods

        # Check sufficient temperature samples
        if len(temperature_history) < 5:
            return False, f"Insufficient temperature samples ({len(temperature_history)} < 5)"

        return True, "Valid"

    def _schedule_learning_save(self) -> None:
        """Schedule a debounced save of learning data to storage.

        Gets the learning store from hass.data and triggers a delayed save
        with the current adaptive learner data. This ensures cycle metrics
        are persisted after finalization without blocking on disk I/O.
        """
        from ..const import DOMAIN

        # Get learning store from hass.data
        learning_store = self._hass.data.get(DOMAIN, {}).get("learning_store")
        if learning_store is None:
            self._logger.debug("No learning store available, skipping save")
            return

        # Update zone data in memory with current adaptive learner state
        adaptive_data = self._adaptive_learner.to_dict()
        learning_store.update_zone_data(
            zone_id=self._zone_id,
            adaptive_data=adaptive_data,
        )

        # Schedule debounced save (30s delay)
        learning_store.schedule_zone_save()

        self._logger.debug(
            "Scheduled learning data save for zone %s after cycle finalization",
            self._zone_id,
        )

    def record_cycle_metrics(
        self,
        cycle_start_time: datetime | None,
        cycle_target_temp: float | None,
        cycle_state_value: str,
        temperature_history: list[tuple[datetime, float]],
        outdoor_temp_history: list[tuple[datetime, float]],
    ) -> None:
        """Record metrics for the current cycle without resetting state.

        This is a synchronous helper that validates the cycle and records metrics
        without transitioning state. Used when a new cycle interrupts the settling phase.

        Args:
            cycle_start_time: When the cycle started
            cycle_target_temp: Target temperature for the cycle
            cycle_state_value: Current cycle state as string ("heating", "cooling", "settling")
            temperature_history: List of (timestamp, temperature) samples
            outdoor_temp_history: List of (timestamp, outdoor_temp) samples
        """
        # Validate cycle
        is_valid, reason = self._is_cycle_valid(cycle_start_time, temperature_history)
        if not is_valid:
            self._logger.info("Cycle not recorded: %s", reason)
            return

        # Log interruption status if cycle was interrupted
        if len(self._interruption_history) > 0:
            self._logger.info(
                "Cycle had %d interruptions during tracking",
                len(self._interruption_history),
            )

        # Import cycle analysis functions
        from ..adaptive.cycle_analysis import (
            CycleMetrics,
            calculate_overshoot,
            calculate_undershoot,
            calculate_settling_time,
            count_oscillations,
            calculate_rise_time,
            calculate_settling_mae,
        )
        from ..adaptive.disturbance_detector import DisturbanceDetector

        # Get target temperature
        target_temp = cycle_target_temp
        if target_temp is None:
            self._logger.warning("No target temperature recorded, cannot calculate metrics")
            return

        # Get start temperature (first reading in history)
        if len(temperature_history) < 1:
            self._logger.warning("No temperature history, cannot calculate metrics")
            return

        start_temp = temperature_history[0][1]

        # Calculate all 5 metrics
        overshoot = calculate_overshoot(temperature_history, target_temp)
        undershoot = calculate_undershoot(temperature_history, target_temp)
        settling_time = calculate_settling_time(temperature_history, target_temp, reference_time=self._device_off_time)
        oscillations = count_oscillations(temperature_history, target_temp)
        rise_time = calculate_rise_time(temperature_history, start_temp, target_temp)

        # Detect disturbances (requires environmental sensor data - not yet wired up)
        # For now, heater_active_periods is estimated from cycle start/stop times
        heater_active_periods = []
        if cycle_start_time:
            # Estimate heater was active from cycle start to first settling temp
            heating_end = cycle_start_time
            if len(temperature_history) > 0:
                # Assume heating stopped sometime during the cycle
                heating_end = temperature_history[len(temperature_history) // 2][0]
            heater_active_periods.append((cycle_start_time, heating_end))

        detector = DisturbanceDetector()
        disturbances = detector.detect_disturbances(
            temperature_history=temperature_history,
            heater_active_periods=heater_active_periods,
            outdoor_temps=None,  # TODO: Wire up outdoor sensor data
            solar_values=None,   # TODO: Wire up solar sensor data
            wind_speeds=None,    # TODO: Wire up wind sensor data
        )

        # Calculate outdoor temperature average if available
        outdoor_temp_avg = None
        if len(outdoor_temp_history) > 0:
            outdoor_temp_avg = sum(temp for _, temp in outdoor_temp_history) / len(outdoor_temp_history)

        # Calculate decay metrics
        integral_at_tolerance, integral_at_setpoint, decay_contribution = self._calculate_decay_metrics()

        # Calculate end_temp from last temperature in history
        end_temp = None
        if len(temperature_history) > 0:
            end_temp = temperature_history[-1][1]

        # Calculate inter_cycle_drift if we have previous cycle end temp
        inter_cycle_drift = None
        if self._prev_cycle_end_temp is not None and start_temp is not None:
            inter_cycle_drift = start_temp - self._prev_cycle_end_temp

        # Calculate settling_mae
        settling_mae = calculate_settling_mae(
            temperature_history=temperature_history,
            target_temp=target_temp,
            settling_start_time=self._device_off_time,
        )

        # Calculate dead_time from transport delay if set
        dead_time = self._transport_delay_minutes

        # Determine mode from current cycle state
        mode = None
        if cycle_state_value in ("heating", "settling"):
            # Check if we were in a heating cycle
            hvac_mode = self._get_hvac_mode()
            if hvac_mode == "heat":
                mode = "heating"
            elif hvac_mode == "cool":
                mode = "cooling"
        elif cycle_state_value == "cooling":
            mode = "cooling"

        # Create CycleMetrics object with interruption history
        metrics = CycleMetrics(
            overshoot=overshoot,
            undershoot=undershoot,
            settling_time=settling_time,
            oscillations=oscillations,
            rise_time=rise_time,
            disturbances=disturbances,
            interruption_history=self._interruption_history.copy(),
            outdoor_temp_avg=outdoor_temp_avg,
            integral_at_tolerance_entry=integral_at_tolerance,
            integral_at_setpoint_cross=integral_at_setpoint,
            decay_contribution=decay_contribution,
            was_clamped=self._was_clamped,
            end_temp=end_temp,
            settling_mae=settling_mae,
            inter_cycle_drift=inter_cycle_drift,
            dead_time=dead_time,
            mode=mode,
        )

        # Record metrics with adaptive learner
        self._adaptive_learner.add_cycle_metrics(metrics)
        self._adaptive_learner.update_convergence_tracking(metrics)
        self._adaptive_learner.update_convergence_confidence(metrics)

        # Check if we're in validation mode and handle validation
        if self._adaptive_learner.is_in_validation_mode():
            validation_result = self._adaptive_learner.add_validation_cycle(metrics)

            if validation_result == 'rollback':
                # Validation failed - call rollback callback if available
                if self._on_validation_failed is not None:
                    self._logger.warning("Validation failed, triggering rollback callback")
                    # Schedule callback as async task
                    self._hass.async_create_task(self._on_validation_failed())
                else:
                    self._logger.warning(
                        "Validation failed but no rollback callback configured"
                    )
            elif validation_result == 'success':
                self._logger.info(
                    "Validation completed successfully - PID changes verified"
                )

        # Log cycle completion with all metrics
        disturbance_str = f", disturbances={disturbances}" if disturbances else ""
        clamped_str = f", was_clamped={self._was_clamped}"
        self._logger.info(
            "Cycle completed - overshoot=%.2f°C, undershoot=%.2f°C, "
            "settling_time=%.1f min, oscillations=%d, rise_time=%.1f min%s%s",
            overshoot or 0.0,
            undershoot or 0.0,
            settling_time or 0.0,
            oscillations,
            rise_time or 0.0,
            disturbance_str,
            clamped_str,
        )

        # Trigger auto-apply check if callback configured (and not in validation mode)
        if self._on_auto_apply_check is not None and not self._adaptive_learner.is_in_validation_mode():
            self._hass.async_create_task(self._on_auto_apply_check())

        # Schedule debounced save of learning data
        self._schedule_learning_save()

        # Emit CYCLE_ENDED event if dispatcher is configured
        if self._dispatcher is not None:
            from .events import CycleEndedEvent

            # Get HVAC mode from callback
            hvac_mode = self._get_hvac_mode()

            # Compute duration for preheat observation recording
            duration_minutes = None
            if cycle_start_time is not None:
                duration_minutes = (dt_util.utcnow() - cycle_start_time).total_seconds() / 60

            # Create metrics dict from the CycleMetrics object
            metrics_dict = {
                "overshoot": metrics.overshoot,
                "undershoot": metrics.undershoot,
                "settling_time": metrics.settling_time,
                "oscillations": metrics.oscillations,
                "rise_time": metrics.rise_time,
                "disturbances": metrics.disturbances,
                "outdoor_temp_avg": metrics.outdoor_temp_avg,
                "start_temp": start_temp,
                "end_temp": end_temp,
                "duration_minutes": duration_minutes,
                "interrupted": metrics.was_interrupted,
            }

            cycle_ended_event = CycleEndedEvent(
                hvac_mode=hvac_mode,
                timestamp=dt_util.utcnow(),
                metrics=metrics_dict,
            )
            self._dispatcher.emit(cycle_ended_event)

        # Store end_temp for next cycle's inter_cycle_drift calculation
        if end_temp is not None:
            self._prev_cycle_end_temp = end_temp
