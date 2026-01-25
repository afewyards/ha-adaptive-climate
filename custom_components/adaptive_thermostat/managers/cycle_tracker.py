"""Cycle tracking manager for adaptive learning.

This module provides the CycleTrackerManager class which tracks heating cycles,
collects temperature data, and calculates metrics for adaptive PID tuning.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from ..adaptive.learning import AdaptiveLearner
    from .events import (
        CycleEventDispatcher,
        CycleStartedEvent,
        CycleEndedEvent,
        HeatingStartedEvent,
        HeatingEndedEvent,
        SettlingStartedEvent,
        SetpointChangedEvent,
        ModeChangedEvent,
        ContactPauseEvent,
        ContactResumeEvent,
        TemperatureUpdateEvent,
    )

_LOGGER = logging.getLogger(__name__)


class CycleState(Enum):
    """States for cycle tracking."""

    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"
    SETTLING = "settling"


class CycleTrackerManager:
    """Manages heating cycle tracking and metrics collection.

    This class tracks heating cycles through their lifecycle:
    1. IDLE -> HEATING when heating starts
    2. HEATING -> SETTLING when heating stops
    3. SETTLING -> IDLE when temperature stabilizes or timeout occurs

    During HEATING and SETTLING states, temperature samples are collected
    for cycle metrics calculation.
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
        get_is_device_active: Callable[[], bool] | None = None,
        thermal_time_constant: float | None = None,
        settling_timeout_minutes: int | None = None,
        get_outdoor_temp: Callable[[], float | None] | None = None,
        on_validation_failed: Callable[[], Awaitable[None]] | None = None,
        on_auto_apply_check: Callable[[], Awaitable[None]] | None = None,
        dispatcher: "CycleEventDispatcher | None" = None,
        heating_type: str | None = None,
    ) -> None:
        """Initialize the cycle tracker manager.

        Args:
            hass: Home Assistant instance
            zone_id: Unique identifier for the zone
            adaptive_learner: Adaptive learning instance for storing cycle metrics
            get_target_temp: Callback to get current target temperature
            get_current_temp: Callback to get current temperature
            get_hvac_mode: Callback to get current HVAC mode
            get_in_grace_period: Callback to check if in learning grace period
            get_is_device_active: Callback to check if heater/cooler is currently active
            thermal_time_constant: Building thermal time constant in hours (tau)
            settling_timeout_minutes: Optional override for settling timeout in minutes
            get_outdoor_temp: Callback to get outdoor temperature (optional)
            on_validation_failed: Async callback for validation failure (triggers rollback)
            on_auto_apply_check: Async callback for auto-apply check after cycle completion
            dispatcher: Optional CycleEventDispatcher for event-driven operation
            heating_type: Heating system type (for cold_tolerance lookup)
        """
        from ..const import (
            SETTLING_TIMEOUT_MULTIPLIER,
            SETTLING_TIMEOUT_MIN,
            SETTLING_TIMEOUT_MAX,
            HEATING_TYPE_CHARACTERISTICS,
        )

        self._hass = hass
        self._zone_id = zone_id
        self._adaptive_learner = adaptive_learner
        self._get_target_temp = get_target_temp
        self._get_current_temp = get_current_temp
        self._get_hvac_mode = get_hvac_mode
        self._get_in_grace_period = get_in_grace_period
        self._get_is_device_active = get_is_device_active
        self._get_outdoor_temp = get_outdoor_temp
        self._on_validation_failed = on_validation_failed
        self._on_auto_apply_check = on_auto_apply_check
        self._dispatcher = dispatcher

        # State tracking
        self._state: CycleState = CycleState.IDLE
        self._cycle_start_time: datetime | None = None
        self._cycle_target_temp: float | None = None
        self._temperature_history: list[tuple[datetime, float]] = []
        self._outdoor_temp_history: list[tuple[datetime, float]] = []
        self._settling_timeout_handle = None
        self._interruption_history: list[tuple[datetime, str]] = []
        self._last_interruption_reason: str | None = None  # Persists across cycle resets
        self._restoration_complete: bool = False  # Gate temperature updates until restoration done
        self._was_clamped: bool = False  # Tracks if PID was clamped during current cycle

        # Calculate dynamic settling timeout based on thermal mass
        self._settling_timeout_source = "default"
        if settling_timeout_minutes is not None:
            # Use explicit override
            self._max_settling_time_minutes = settling_timeout_minutes
            self._settling_timeout_source = "override"
        elif thermal_time_constant is not None:
            # Calculate from tau: timeout = max(60, min(240, tau * 30))
            calculated_timeout = thermal_time_constant * SETTLING_TIMEOUT_MULTIPLIER
            self._max_settling_time_minutes = int(
                max(SETTLING_TIMEOUT_MIN, min(SETTLING_TIMEOUT_MAX, calculated_timeout))
            )
            self._settling_timeout_source = f"calculated (tau={thermal_time_constant:.1f}h)"
        else:
            # Default fallback
            self._max_settling_time_minutes = 120
            self._settling_timeout_source = "default"

        self._min_cycle_duration_minutes = 5

        # Logging
        self._logger = logging.getLogger(f"{__name__}.{zone_id}")
        self._logger.info(
            "CycleTrackerManager initialized for zone %s: settling_timeout=%d min (%s)",
            zone_id,
            self._max_settling_time_minutes,
            self._settling_timeout_source,
        )

        # Event subscriptions
        self._unsubscribe_handles: list[Callable[[], None]] = []
        self._device_on_time: datetime | None = None
        self._device_off_time: datetime | None = None

        # Integral tracking for decay calculation
        self._integral_at_tolerance_entry: float | None = None
        self._integral_at_setpoint_cross: float | None = None
        # Initialize cold_tolerance from heating type characteristics
        if heating_type and heating_type in HEATING_TYPE_CHARACTERISTICS:
            self._cold_tolerance: float | None = HEATING_TYPE_CHARACTERISTICS[heating_type].get("cold_tolerance")
        else:
            self._cold_tolerance: float | None = None

        # Track previous cycle end temperature for inter-cycle drift calculation
        self._prev_cycle_end_temp: float | None = None

        # Subscribe to events if dispatcher provided
        if self._dispatcher is not None:
            from .events import CycleEventType

            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.CYCLE_STARTED, self._on_cycle_started)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.HEATING_STARTED, self._on_heating_started)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.HEATING_ENDED, self._on_heating_ended)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.SETTLING_STARTED, self._on_settling_started)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.CONTACT_PAUSE, self._on_contact_pause)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.CONTACT_RESUME, self._on_contact_resume)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.SETPOINT_CHANGED, self._on_setpoint_changed_event)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.MODE_CHANGED, self._on_mode_changed_event)
            )
            self._unsubscribe_handles.append(
                self._dispatcher.subscribe(CycleEventType.TEMPERATURE_UPDATE, self._on_temperature_update)
            )

    @property
    def state(self) -> CycleState:
        """Return current cycle state."""
        return self._state

    @property
    def cycle_start_time(self) -> datetime | None:
        """Return cycle start time."""
        return self._cycle_start_time

    @property
    def temperature_history(self) -> list[tuple[datetime, float]]:
        """Return temperature history."""
        return self._temperature_history.copy()

    def get_state_name(self) -> str:
        """Return current cycle state as lowercase string.

        Returns:
            Cycle state name: "idle", "heating", "cooling", or "settling"
        """
        return self._state.value

    def get_last_interruption_reason(self) -> str | None:
        """Return last interruption reason or None if no interruptions.

        Maps InterruptionType enum values to user-friendly strings:
        - "setpoint_major" or "setpoint_minor" -> "setpoint_change"
        - "mode_change" -> "mode_change"
        - "contact_sensor" -> "contact_sensor"
        - "timeout" -> None (treated as successful completion)

        Returns:
            Interruption reason string or None if no interruptions
        """
        return self._last_interruption_reason

    def set_restoration_complete(self) -> None:
        """Mark restoration as complete, allowing temperature updates to be processed.

        This method should be called after the thermostat has restored its state
        from storage to prevent collecting stale temperature samples during startup.
        """
        self._restoration_complete = True
        self._logger.debug("Restoration complete, temperature updates now enabled")

    def _on_cycle_started(self, event: "CycleStartedEvent") -> None:
        """Handle CYCLE_STARTED event.

        Args:
            event: CycleStartedEvent with hvac_mode, timestamp, target_temp, current_temp
        """
        # Determine cycle state based on HVAC mode
        if event.hvac_mode == "heat":
            new_state = CycleState.HEATING
        elif event.hvac_mode == "cool":
            new_state = CycleState.COOLING
        else:
            return

        # Clear integral tracking for new cycle (must happen before idempotent check)
        self._integral_at_tolerance_entry = None
        self._integral_at_setpoint_cross = None

        # Idempotent: ignore if already in the target state
        if self._state == new_state:
            return

        if self._state == CycleState.SETTLING:
            self._logger.info("Finalizing previous cycle before starting new one")
            # Record metrics synchronously before starting new cycle
            self._record_cycle_metrics()

        # Transition to new state
        self._state = new_state
        self._cycle_start_time = event.timestamp
        self._cycle_target_temp = self._get_target_temp()
        self._temperature_history.clear()
        self._outdoor_temp_history.clear()
        # Clear last interruption reason when starting a new cycle
        self._last_interruption_reason = None
        # Clear clamping state for new cycle
        self._was_clamped = False

        current_temp = self._get_current_temp()
        self._logger.info(
            "Cycle started: target=%.2f°C, current=%.2f°C",
            self._cycle_target_temp or 0.0,
            current_temp or 0.0,
        )

    def _on_settling_started(self, event: "SettlingStartedEvent") -> None:
        """Handle SETTLING_STARTED event.

        Args:
            event: SettlingStartedEvent with hvac_mode, timestamp, was_clamped
        """
        # Verify we're in the correct state for settling
        if event.hvac_mode == "heat" and self._state != CycleState.HEATING:
            self._logger.debug(
                "Session ended while in state %s, ignoring", self._state
            )
            return
        elif event.hvac_mode == "cool" and self._state != CycleState.COOLING:
            self._logger.debug(
                "Session ended while in state %s, ignoring", self._state
            )
            return

        # Capture clamping state from event
        self._was_clamped = event.was_clamped

        # Transition to SETTLING state
        self._state = CycleState.SETTLING

        # Schedule settling timeout
        self._schedule_settling_timeout()

        mode_str = "heating" if event.hvac_mode == "heat" else "cooling"
        self._logger.info(
            "%s session ended, monitoring settling (timeout in %d minutes)",
            mode_str.capitalize(),
            self._max_settling_time_minutes,
        )

    def _on_heating_started(self, event: "HeatingStartedEvent") -> None:
        """Handle HEATING_STARTED event for duty cycle tracking.

        Args:
            event: HeatingStartedEvent with hvac_mode, timestamp
        """
        # Track device on time for duty cycle calculation
        self._device_on_time = event.timestamp

    def _on_heating_ended(self, event: "HeatingEndedEvent") -> None:
        """Handle HEATING_ENDED event for duty cycle tracking.

        Args:
            event: HeatingEndedEvent with hvac_mode, timestamp
        """
        # Track device off time for duty cycle calculation
        self._device_off_time = event.timestamp

    def _on_contact_pause(self, event: "ContactPauseEvent") -> None:
        """Handle CONTACT_PAUSE event.

        Args:
            event: ContactPauseEvent with hvac_mode, timestamp, entity_id
        """
        from ..adaptive.cycle_analysis import InterruptionType

        # Use centralized interruption handler
        self._handle_interruption(
            InterruptionType.CONTACT_SENSOR.value,
            should_abort=True,
            reason="contact sensor pause (window/door opened)"
        )

    def _on_contact_resume(self, event: "ContactResumeEvent") -> None:
        """Handle CONTACT_RESUME event.

        Args:
            event: ContactResumeEvent with hvac_mode, timestamp, entity_id, pause_duration_seconds
        """
        # Currently a no-op since CONTACT_PAUSE already aborts the cycle
        # Future enhancement: could resume cycle if pause was brief
        pass

    def _on_setpoint_changed_event(self, event: "SetpointChangedEvent") -> None:
        """Handle SETPOINT_CHANGED event.

        Args:
            event: SetpointChangedEvent with hvac_mode, timestamp, old_target, new_target
        """
        from ..adaptive.cycle_analysis import InterruptionClassifier, InterruptionType

        # Only process if we're in an active cycle
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Check if device is currently active
        is_device_active = False
        if self._get_is_device_active is not None:
            is_device_active = self._get_is_device_active()

        # Classify the interruption
        interruption_type = InterruptionClassifier.classify_setpoint_change(
            event.old_target, event.new_target, is_device_active
        )

        # Determine action based on classification
        if interruption_type == InterruptionType.SETPOINT_MAJOR:
            # Major change, abort cycle
            reason = f"setpoint change: {event.old_target:.2f}°C -> {event.new_target:.2f}°C (device inactive)"
            self._handle_interruption(
                interruption_type.value,
                should_abort=True,
                reason=reason
            )
        else:
            # Minor change, continue tracking with new setpoint
            self._cycle_target_temp = event.new_target
            reason = f"setpoint change: {event.old_target:.2f}°C -> {event.new_target:.2f}°C (device active or minor)"
            self._handle_interruption(
                interruption_type.value,
                should_abort=False,
                reason=reason
            )

    def _on_mode_changed_event(self, event: "ModeChangedEvent") -> None:
        """Handle MODE_CHANGED event.

        Args:
            event: ModeChangedEvent with timestamp, old_mode, new_mode
        """
        from ..adaptive.cycle_analysis import InterruptionClassifier, InterruptionType

        # Only process if we're in an active cycle
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Map cycle state to string for classifier
        cycle_state_str = self._state.value  # "heating", "cooling", or "settling"

        # Classify the interruption
        interruption_type = InterruptionClassifier.classify_mode_change(
            event.old_mode, event.new_mode, cycle_state_str
        )

        if interruption_type is not None:
            # Incompatible mode change, abort cycle
            reason = f"mode change: {event.old_mode} -> {event.new_mode} (incompatible with {cycle_state_str})"
            self._handle_interruption(
                interruption_type.value,
                should_abort=True,
                reason=reason
            )

    def _on_temperature_update(self, event: "TemperatureUpdateEvent") -> None:
        """Handle TEMPERATURE_UPDATE event for integral tracking.

        Tracks integral values at two key points during heating:
        1. When temperature enters cold tolerance zone (pid_error < cold_tolerance)
        2. When temperature crosses setpoint (pid_error <= 0)

        Args:
            event: TemperatureUpdateEvent with timestamp, temperature, setpoint, pid_integral, pid_error
        """
        # Only track during active cycle (HEATING or SETTLING)
        if self._state not in (CycleState.HEATING, CycleState.SETTLING):
            return

        # Capture integral at tolerance entry (only once per cycle)
        if self._integral_at_tolerance_entry is None and self._cold_tolerance is not None:
            if event.pid_error < self._cold_tolerance:
                self._integral_at_tolerance_entry = event.pid_integral
                self._logger.debug(
                    "Captured integral at tolerance entry: %.2f (pid_error=%.2f < cold_tolerance=%.2f)",
                    event.pid_integral,
                    event.pid_error,
                    self._cold_tolerance,
                )

        # Capture integral at setpoint crossing (only once per cycle)
        if self._integral_at_setpoint_cross is None:
            if event.pid_error <= 0.0:
                self._integral_at_setpoint_cross = event.pid_integral
                self._logger.debug(
                    "Captured integral at setpoint cross: %.2f (pid_error=%.2f)",
                    event.pid_integral,
                    event.pid_error,
                )

    def cleanup(self) -> None:
        """Clean up event subscriptions and timers.

        This method should be called when the entity is being removed from
        Home Assistant to prevent memory leaks from orphaned subscriptions.
        """
        for unsub in self._unsubscribe_handles:
            unsub()
        self._unsubscribe_handles.clear()
        self._cancel_settling_timeout()
        self._logger.debug("Cleaned up %s subscriptions", self._zone_id)

    def _cancel_settling_timeout(self) -> None:
        """Cancel any active settling timeout."""
        if self._settling_timeout_handle is not None:
            self._settling_timeout_handle()
            self._settling_timeout_handle = None

    def _schedule_settling_timeout(self) -> None:
        """Schedule timeout for settling detection."""
        from homeassistant.helpers.event import async_call_later

        # Cancel existing timeout if any
        self._cancel_settling_timeout()

        # Schedule new timeout
        async def _settling_timeout(_: datetime) -> None:
            """Handle settling timeout."""
            self._logger.warning(
                "Settling timeout reached (%d minutes), finalizing cycle",
                self._max_settling_time_minutes,
            )
            # Clear handle before finalize to avoid double-cancel attempt
            self._settling_timeout_handle = None
            await self._finalize_cycle()

        # Store the cancel handle - pass async function directly, async_call_later handles it
        self._settling_timeout_handle = async_call_later(
            self._hass, self._max_settling_time_minutes * 60, _settling_timeout
        )

    async def update_temperature(self, timestamp: datetime, temperature: float) -> None:
        """Update temperature history and check for settling completion.

        Only collects temperature samples when in HEATING, COOLING, or SETTLING state.
        Checks for settling completion on each update during SETTLING state.

        Args:
            timestamp: Time of temperature reading
            temperature: Current temperature value
        """
        # Gate updates until restoration is complete
        if not self._restoration_complete:
            return

        # Only collect during active cycle tracking
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Append temperature sample
        self._temperature_history.append((timestamp, temperature))

        # Also track outdoor temperature if available
        if self._get_outdoor_temp is not None:
            outdoor_temp = self._get_outdoor_temp()
            if outdoor_temp is not None:
                self._outdoor_temp_history.append((timestamp, outdoor_temp))

        # Check for settling completion during SETTLING state
        if self._state == CycleState.SETTLING:
            if self._is_settling_complete():
                self._logger.info("Settling complete, finalizing cycle")
                await self._finalize_cycle()

    def _handle_interruption(
        self,
        interruption_type: str,
        should_abort: bool,
        reason: str
    ) -> None:
        """Centralized interruption handler.

        Args:
            interruption_type: Type of interruption (from InterruptionType enum value)
            should_abort: Whether to abort the cycle (True) or continue tracking (False)
            reason: Human-readable reason for logging
        """
        from ..adaptive.cycle_analysis import InterruptionType

        # Only process if we're in an active cycle
        if self._state not in (CycleState.HEATING, CycleState.COOLING, CycleState.SETTLING):
            return

        # Record interruption in history
        self._interruption_history.append((datetime.now(), interruption_type))

        # Map interruption type to user-friendly string for persistence
        if interruption_type in ("setpoint_major", "setpoint_minor"):
            self._last_interruption_reason = "setpoint_change"
        elif interruption_type == "mode_change":
            self._last_interruption_reason = "mode_change"
        elif interruption_type == "contact_sensor":
            self._last_interruption_reason = "contact_sensor"
        # timeout or other interruptions don't set a persistent reason

        if should_abort:
            # Abort the cycle
            self._logger.info("Cycle aborted: %s", reason)
            self._reset_cycle_state()
        else:
            # Continue tracking but mark as interrupted
            self._logger.info("Cycle interrupted (continuing): %s", reason)

    def _reset_cycle_state(self) -> None:
        """Reset cycle state to IDLE and clear all cycle data.

        This helper method provides consistent cleanup of cycle state,
        ensuring all state variables are properly reset.
        """
        # Clear temperature history
        self._temperature_history.clear()
        self._outdoor_temp_history.clear()

        # Reset cycle tracking variables
        self._cycle_start_time = None
        self._cycle_target_temp = None

        # Clear interruption history
        self._interruption_history.clear()

        # Clear integral tracking
        self._integral_at_tolerance_entry = None
        self._integral_at_setpoint_cross = None

        # Clear clamping state
        self._was_clamped = False

        # Set state to IDLE
        self._state = CycleState.IDLE

        # Cancel settling timeout if active
        self._cancel_settling_timeout()

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

    def _is_settling_complete(self) -> bool:
        """Check if temperature has settled after heating stopped.

        Settling is considered complete when:
        1. At least 10 samples (5 minutes at 30-second intervals) are collected
        2. MAD of last 10 samples < SETTLING_MAD_THRESHOLD (stable temperature)
        3. Current temperature is within 0.5°C of target

        Uses Median Absolute Deviation (MAD) instead of variance for robustness
        to outliers (e.g., brief sensor noise, single errant reading).

        Returns:
            True if settling is complete, False otherwise
        """
        from ..const import SETTLING_MAD_THRESHOLD

        # Need minimum 10 samples for settling detection
        if len(self._temperature_history) < 10:
            return False

        # Get last 10 temperature samples
        last_temps = [temp for _, temp in self._temperature_history[-10:]]

        # Calculate MAD (robust alternative to variance)
        mad = self._calculate_mad(last_temps)

        self._logger.debug(
            "Settling check: MAD=%.3f°C (threshold=%.3f°C)",
            mad,
            SETTLING_MAD_THRESHOLD,
        )

        # Check if MAD is below threshold (stable)
        if mad >= SETTLING_MAD_THRESHOLD:
            return False

        # Check if current temperature is within 0.5°C of target
        current_temp = last_temps[-1]
        target_temp = self._cycle_target_temp
        if target_temp is None:
            return False

        if abs(current_temp - target_temp) > 0.5:
            return False

        self._logger.debug("Temperature settled: MAD=%.3f°C", mad)
        return True

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

    def _is_cycle_valid(self) -> tuple[bool, str]:
        """Check if the current cycle is valid for recording.

        A cycle is valid if:
        1. Duration >= minimum cycle duration (5 minutes)
        2. Not in learning grace period
        3. Learning is enabled (not in vacation mode)
        4. Sufficient temperature samples (>= 5)

        Returns:
            Tuple of (is_valid, reason_string)
        """
        # Check minimum duration
        if self._cycle_start_time is None:
            return False, "No cycle start time recorded"

        duration_minutes = (datetime.now() - self._cycle_start_time).total_seconds() / 60
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
        if len(self._temperature_history) < 5:
            return False, f"Insufficient temperature samples ({len(self._temperature_history)} < 5)"

        return True, "Valid"

    def _record_cycle_metrics(self) -> None:
        """Record metrics for the current cycle without resetting state.

        This is a synchronous helper that validates the cycle and records metrics
        without transitioning state. Used when a new cycle interrupts the settling phase.
        """
        # Validate cycle
        is_valid, reason = self._is_cycle_valid()
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
        )
        from ..adaptive.disturbance_detector import DisturbanceDetector

        # Get target temperature
        target_temp = self._cycle_target_temp
        if target_temp is None:
            self._logger.warning("No target temperature recorded, cannot calculate metrics")
            return

        # Get start temperature (first reading in history)
        if len(self._temperature_history) < 1:
            self._logger.warning("No temperature history, cannot calculate metrics")
            return

        start_temp = self._temperature_history[0][1]

        # Calculate all 5 metrics
        overshoot = calculate_overshoot(self._temperature_history, target_temp)
        undershoot = calculate_undershoot(self._temperature_history, target_temp)
        settling_time = calculate_settling_time(self._temperature_history, target_temp, reference_time=self._device_off_time)
        oscillations = count_oscillations(self._temperature_history, target_temp)
        rise_time = calculate_rise_time(self._temperature_history, start_temp, target_temp)

        # Detect disturbances (requires environmental sensor data - not yet wired up)
        # For now, heater_active_periods is estimated from cycle start/stop times
        heater_active_periods = []
        if self._cycle_start_time:
            # Estimate heater was active from cycle start to first settling temp
            heating_end = self._cycle_start_time
            if len(self._temperature_history) > 0:
                # Assume heating stopped sometime during the cycle
                heating_end = self._temperature_history[len(self._temperature_history) // 2][0]
            heater_active_periods.append((self._cycle_start_time, heating_end))

        detector = DisturbanceDetector()
        disturbances = detector.detect_disturbances(
            temperature_history=self._temperature_history,
            heater_active_periods=heater_active_periods,
            outdoor_temps=None,  # TODO: Wire up outdoor sensor data
            solar_values=None,   # TODO: Wire up solar sensor data
            wind_speeds=None,    # TODO: Wire up wind sensor data
        )

        # Calculate outdoor temperature average if available
        outdoor_temp_avg = None
        if len(self._outdoor_temp_history) > 0:
            outdoor_temp_avg = sum(temp for _, temp in self._outdoor_temp_history) / len(self._outdoor_temp_history)

        # Calculate decay metrics
        integral_at_tolerance, integral_at_setpoint, decay_contribution = self._calculate_decay_metrics()

        # Calculate end_temp from last temperature in history
        end_temp = None
        if len(self._temperature_history) > 0:
            end_temp = self._temperature_history[-1][1]

        # Calculate inter_cycle_drift if we have previous cycle end temp
        inter_cycle_drift = None
        if self._prev_cycle_end_temp is not None and start_temp is not None:
            inter_cycle_drift = start_temp - self._prev_cycle_end_temp

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
            inter_cycle_drift=inter_cycle_drift,
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

            # Create metrics dict from the CycleMetrics object
            metrics_dict = {
                "overshoot": metrics.overshoot,
                "undershoot": metrics.undershoot,
                "settling_time": metrics.settling_time,
                "oscillations": metrics.oscillations,
                "rise_time": metrics.rise_time,
                "disturbances": metrics.disturbances,
                "outdoor_temp_avg": metrics.outdoor_temp_avg,
            }

            cycle_ended_event = CycleEndedEvent(
                hvac_mode=hvac_mode,
                timestamp=datetime.now(),
                metrics=metrics_dict,
            )
            self._dispatcher.emit(cycle_ended_event)

        # Store end_temp for next cycle's inter_cycle_drift calculation
        if end_temp is not None:
            self._prev_cycle_end_temp = end_temp

    async def _finalize_cycle(self) -> None:
        """Finalize cycle and record metrics.

        Validates the cycle, calculates metrics if valid, and records them
        with the adaptive learner. Transitions to IDLE state.
        """
        # Cancel settling timeout if active
        if self._settling_timeout_handle is not None:
            self._settling_timeout_handle()
            self._settling_timeout_handle = None

        # Record metrics using the synchronous helper
        self._record_cycle_metrics()

        # Reset cycle state (clears interruption flags and transitions to IDLE)
        self._reset_cycle_state()
