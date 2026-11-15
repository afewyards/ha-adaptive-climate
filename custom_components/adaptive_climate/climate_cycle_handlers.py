"""Cycle event handlers for adaptive thermostat.

This module contains handlers for heating cycle lifecycle events, physics-based
Ki boost logic, Ke/steady-state helpers, and manifold transport delay management.
"""

from __future__ import annotations

import logging

from homeassistant.core import callback

from . import DOMAIN
from .const import MAX_UNDERSHOOT_KI_MULTIPLIER, PIDChangeReason
from .managers.events import CycleEndedEvent, HeatingEndedEvent, HeatingStartedEvent

_LOGGER = logging.getLogger(__name__)


class ClimateCycleHandlersMixin:
    """Mixin providing cycle event handlers for AdaptiveThermostat.

    Encapsulates all heating-cycle lifecycle callbacks, physics-based Ki boost
    logic, Ke observation helpers, and manifold transport delay wiring.
    """

    def _handle_cycle_ended_for_preheat(self, event: CycleEndedEvent) -> None:
        """Handle CYCLE_ENDED event to record preheat observations.

        Called when a heating cycle completes. Records heating rate observation
        if cycle was successful (not interrupted) and outdoor temperature is available.

        Args:
            event: The CycleEndedEvent containing cycle metrics
        """
        if not self._preheat_learner:
            return

        # Only record if cycle completed successfully (not interrupted)
        if not event.metrics or event.metrics.get("interrupted"):
            return

        # Extract cycle data
        start_temp = event.metrics.get("start_temp")
        end_temp = event.metrics.get("end_temp")
        duration_minutes = event.metrics.get("duration_minutes")
        outdoor_temp = self._ext_temp

        # Record observation if we have all required data
        if start_temp and end_temp and duration_minutes and outdoor_temp is not None:
            self._preheat_learner.add_observation(
                start_temp=start_temp,
                end_temp=end_temp,
                outdoor_temp=outdoor_temp,
                duration_minutes=duration_minutes,
                timestamp=event.timestamp,
            )
            _LOGGER.debug(
                "%s: Recorded preheat observation (delta=%.1f°C, outdoor=%.1f°C, duration=%.0fmin)",
                self.entity_id,
                end_temp - start_temp,
                outdoor_temp,
                duration_minutes,
            )

            # Schedule persistence save
            coordinator = self._coordinator
            if coordinator and self._zone_id:
                zone_data = coordinator.get_zone_data(self._zone_id)
                if zone_data:
                    learning_store = self.hass.data.get(DOMAIN, {}).get("learning_store")
                    if learning_store:
                        learning_store.update_zone_data(
                            self._zone_id,
                            preheat_data=self._preheat_learner.to_dict(),
                        )
                        learning_store.schedule_zone_save()

    def _handle_cycle_ended_for_heating_rate(self, event: CycleEndedEvent) -> None:
        """Handle CYCLE_ENDED event to update heating rate session.

        Called when a heating cycle completes. Updates the active session with
        cycle data and checks for session end conditions (reached setpoint or stalled).

        Args:
            event: The CycleEndedEvent containing cycle metrics
        """
        coordinator = self._coordinator
        if not coordinator or not self._zone_id:
            return

        zone_data = coordinator.get_zone_data(self._zone_id)
        if not zone_data:
            return

        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            return

        heating_rate_learner = adaptive_learner._heating_rate_learner

        # Skip if no active session
        if not heating_rate_learner._active_session:
            return

        # Skip if cycle was interrupted
        if not event.metrics or event.metrics.get("interrupted"):
            return

        # Update session with cycle data
        duty = event.metrics.get("duty")
        if duty is not None and self._current_temp is not None:
            heating_rate_learner.update_session(
                temp=self._current_temp,
                duty=duty,
            )

        # Check for session end conditions
        if self._current_temp is not None and self._target_temp is not None:
            # Check if reached setpoint
            if self._current_temp >= float(self._target_temp) - float(self._cold_tolerance):
                obs = heating_rate_learner.end_session(
                    end_temp=self._current_temp,
                    reason="reached_setpoint",
                )
                if obs:
                    _LOGGER.info(
                        "%s: Heating rate session ended (reached setpoint): rate=%.2f°C/h, duration=%.0fmin",
                        self.entity_id,
                        obs.rate,
                        obs.duration_min,
                    )
                    self._check_physics_rate_and_boost_ki(adaptive_learner)
            # Check if stalled
            elif heating_rate_learner.is_stalled():
                obs = heating_rate_learner.end_session(
                    end_temp=self._current_temp,
                    reason="stalled",
                )
                if obs:
                    _LOGGER.warning(
                        "%s: Heating rate session ended (stalled): rate=%.2f°C/h, duration=%.0fmin",
                        self.entity_id,
                        obs.rate,
                        obs.duration_min,
                    )
                    self._check_physics_rate_and_boost_ki(adaptive_learner)

    def _check_physics_rate_and_boost_ki(self, adaptive_learner) -> None:
        """Check if learned heating rate is below physics expectations and boost Ki if needed.

        Called after heating rate sessions end. Compares learned rate against
        physics-predicted rate and applies Ki boost if significantly underperforming.

        Args:
            adaptive_learner: The AdaptiveLearner instance for this zone.
        """
        # A boost during validation would make rollback (history[-2]) restore the auto-applied gains
        if adaptive_learner.is_in_validation_mode():
            return

        result = adaptive_learner.check_physics_rate_underperformance(
            tau=getattr(self, "_thermal_time_constant", None),
            area_m2=self._area_m2,
            max_power_w=self._max_power_w,
            supply_temperature=self._supply_temperature,
        )

        if result is None:
            return  # Not enough data yet

        if not result.get("is_underperforming", False):
            return  # Performing adequately

        # Check if we've already boosted Ki to the cap relative to physics baseline
        from .adaptive.learning_adjustments import get_physics_baseline_ki_from_history

        old_ki = self._pid_controller.ki
        pid_history = self._gains_manager.get_history()
        physics_baseline_ki = get_physics_baseline_ki_from_history(pid_history)

        if physics_baseline_ki is not None and physics_baseline_ki > 0:
            actual_ratio = old_ki / physics_baseline_ki
            if actual_ratio >= MAX_UNDERSHOOT_KI_MULTIPLIER:
                _LOGGER.debug(
                    "%s: Physics rate check: underperforming but Ki already at cap (%.4f / %.4f = %.2fx >= max %.1fx)",
                    self.entity_id,
                    old_ki,
                    physics_baseline_ki,
                    actual_ratio,
                    MAX_UNDERSHOOT_KI_MULTIPLIER,
                )
                return
        else:
            # Fallback to cumulative multiplier cap if no physics baseline available
            cumulative = adaptive_learner.undershoot_detector.cumulative_ki_multiplier
            if cumulative >= MAX_UNDERSHOOT_KI_MULTIPLIER:
                _LOGGER.debug(
                    "%s: Physics rate check: underperforming but Ki already boosted %.1fx (max %.1fx)",
                    self.entity_id,
                    cumulative,
                    MAX_UNDERSHOOT_KI_MULTIPLIER,
                )
                return

        # Apply the suggested Ki boost
        suggested_boost = result.get("suggested_ki_boost", 1.2)
        new_ki = old_ki * suggested_boost

        # Scale integral to prevent output spike
        if old_ki > 0:
            scale_factor = old_ki / new_ki
            if self._gains_manager is not None:
                self._gains_manager.scale_integral(scale_factor, PIDChangeReason.UNDERSHOOT_BOOST)
            else:
                self._pid_controller.scale_integral(scale_factor)

        # Update Ki via PIDGainsManager
        self._gains_manager.set_gains(
            PIDChangeReason.UNDERSHOOT_BOOST,
            ki=new_ki,
            metrics={
                "reason": "physics_rate_underperformance",
                "learned_rate": result["learned_rate"],
                "expected_rate": result["expected_rate"],
                "ratio": result["ratio"],
                "observation_count": result["observation_count"],
            },
        )

        # Update cumulative multiplier in undershoot detector (kept for logging/debug only)
        adaptive_learner.undershoot_detector.cumulative_ki_multiplier *= suggested_boost
        # Restart the auto-apply cooldown so an auto-apply cannot stack on this boost
        adaptive_learner.mark_adjustment_applied()

        _LOGGER.warning(
            "%s: Physics rate check triggered Ki boost: learned=%.3f°C/h vs expected=%.3f°C/h "
            "(%.0f%%). Ki: %.5f -> %.5f (%.0f%% increase)",
            self.entity_id,
            result["learned_rate"],
            result["expected_rate"],
            result["ratio"] * 100,
            old_ki,
            new_ki,
            (suggested_boost - 1.0) * 100,
        )

        self.schedule_update_ha_state()

    async def _handle_validation_failure(self) -> None:
        """Handle validation failure by rolling back PID values.

        Called by CycleTrackerManager when validation detects performance degradation
        after an auto-apply. Triggers automatic rollback and notifies the user.
        """
        if not self._pid_tuning_manager:
            return

        success = await self._pid_tuning_manager.async_rollback_pid()

        if success:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": f"adaptive_climate_rollback_{self._zone_id}",
                    "title": f"⚠️ PID Rolled Back: {self._name}",
                    "message": (
                        "The auto-applied PID values caused performance degradation "
                        "(>30% worse overshoot). The system has automatically rolled "
                        "back to the previous configuration.\n\n"
                        "Learning will continue and may recommend new values "
                        "when confidence improves."
                    ),
                },
                blocking=False,
            )
            _LOGGER.warning(
                "%s: Validation failed - PID values rolled back automatically",
                self.entity_id,
            )
        else:
            _LOGGER.error(
                "%s: Validation failed but rollback failed - no previous PID history",
                self.entity_id,
            )

    def _is_at_steady_state(self) -> bool:
        """Check if the system is at steady state (maintaining target temperature).

        Delegates to KeManager for the actual implementation.

        Returns:
            True if at steady state, False otherwise
        """
        if self._ke_controller is not None:
            return self._ke_controller.is_at_steady_state()
        return False

    def _maybe_record_ke_observation(self) -> None:
        """Record a Ke observation if conditions are met.

        Delegates to KeManager for the actual implementation.
        """
        if self._ke_controller is not None:
            self._ke_controller.maybe_record_observation()

    def _query_and_mark_manifold(self, action: str = "heating") -> None:
        """Query transport delay from coordinator and mark manifold active.

        Args:
            action: "heating" or "cooling" for logging purposes.
        """
        coordinator = self._coordinator
        if coordinator and self._zone_id:
            # C02: coordinator returns minutes; store with explicit unit in field name.
            delay_minutes = coordinator.get_transport_delay_for_zone(self.entity_id)
            if delay_minutes is not None and delay_minutes > 0:
                self._transport_delay_minutes = delay_minutes
                # PID controller and cycle tracker both expect minutes.
                self._pid_controller.set_transport_delay(delay_minutes)
                if self._cycle_tracker:
                    self._cycle_tracker.set_transport_delay(delay_minutes)
                _LOGGER.debug("%s: Set transport delay %.1f min for %s start", self.entity_id, delay_minutes, action)

        manifold_registry = self.hass.data.get(DOMAIN, {}).get("manifold_registry")
        if manifold_registry:
            manifold_registry.mark_manifold_active(self.entity_id)

    @callback
    def _on_heating_started_event(self, event: HeatingStartedEvent) -> None:
        """Handle HEATING_STARTED event - query manifold transport delay."""
        action = "cooling" if event.hvac_mode == "cool" else "heating"
        self._query_and_mark_manifold(action)

    @callback
    def _on_heating_ended_event(self, event: HeatingEndedEvent) -> None:
        """Handle HEATING_ENDED event - reset transport delay."""
        if self._transport_delay_minutes is not None:
            self._pid_controller.reset_dead_time()
            self._transport_delay_minutes = None
            _LOGGER.debug("%s: Reset transport delay on heating stop", self.entity_id)
