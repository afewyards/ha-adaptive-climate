"""PID Gains Manager - centralized PID gain mutations with auto-history recording."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import HVACMode
from homeassistant.util import dt as dt_util

from ..const import (
    PID_HISTORY_SIZE,
    PIDChangeActor,
    PIDChangeReason,
    REASON_TO_ACTOR,
)

if TYPE_CHECKING:
    from ..pid_controller import PIDController, PIDGains


class PIDGainsManager:
    """Centralized manager for all PID gain mutations.

    Single entry point for kp/ki/kd/ke changes. Auto-records to pid_history.
    Owns _heating_gains and _cooling_gains PIDGains objects.

    Note: Integral stays in PIDController, not managed here.
    """

    def __init__(
        self,
        pid_controller: PIDController,
        initial_heating_gains: PIDGains,
        initial_cooling_gains: PIDGains | None = None,
        get_hvac_mode: Callable[[], HVACMode] | None = None,
    ) -> None:
        """Initialize the gains manager.

        Args:
            pid_controller: The PID controller to sync gains to.
            initial_heating_gains: Initial gains for heating mode.
            initial_cooling_gains: Optional initial gains for cooling mode.
            get_hvac_mode: Optional callback to get current HVAC mode.
        """
        self._pid_controller = pid_controller
        self._heating_gains = initial_heating_gains
        self._cooling_gains = initial_cooling_gains
        self._get_hvac_mode = get_hvac_mode

        # History keyed by mode: {"heating": [...], "cooling": [...]}
        self._pid_history: dict[str, list[dict[str, Any]]] = {
            "heating": [],
            "cooling": [],
        }

        # Sync initial gains to PID controller (no history recorded for init)
        self._sync_gains_to_controller(self._heating_gains)

    def _resolve_mode(self, mode: HVACMode | None) -> HVACMode:
        """Resolve mode, using callback if available, else default to HEAT."""
        if mode is not None:
            return mode
        if self._get_hvac_mode is not None:
            return self._get_hvac_mode()
        return HVACMode.HEAT

    def _mode_key(self, mode: HVACMode) -> str:
        """Convert HVACMode to history key."""
        return "cooling" if mode == HVACMode.COOL else "heating"

    def _get_gains_for_mode(self, mode: HVACMode) -> PIDGains:
        """Get gains object for mode."""
        if mode == HVACMode.COOL and self._cooling_gains is not None:
            return self._cooling_gains
        return self._heating_gains

    def _set_gains_for_mode(self, mode: HVACMode, gains: PIDGains) -> None:
        """Set gains object for mode."""
        if mode == HVACMode.COOL:
            self._cooling_gains = gains
        else:
            self._heating_gains = gains

    def _sync_gains_to_controller(self, gains: PIDGains) -> None:
        """Sync gains to the PID controller."""
        self._pid_controller.set_pid_param("kp", gains.kp)
        self._pid_controller.set_pid_param("ki", gains.ki)
        self._pid_controller.set_pid_param("kd", gains.kd)
        self._pid_controller.set_pid_param("ke", gains.ke)

    def set_gains(
        self,
        reason: PIDChangeReason,
        *,
        kp: float | None = None,
        ki: float | None = None,
        kd: float | None = None,
        ke: float | None = None,
        mode: HVACMode | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Set gains and auto-record snapshot.

        Args:
            reason: Why the gains are being changed.
            kp: New proportional gain (or None to keep current).
            ki: New integral gain (or None to keep current).
            kd: New derivative gain (or None to keep current).
            ke: New outdoor compensation gain (or None to keep current).
            mode: HVAC mode (defaults to current mode via callback).
            metrics: Optional metrics to include in snapshot.
        """
        resolved_mode = self._resolve_mode(mode)
        current_gains = self._get_gains_for_mode(resolved_mode)

        # Apply partial updates using dataclass replace
        new_gains = replace(
            current_gains,
            kp=kp if kp is not None else current_gains.kp,
            ki=ki if ki is not None else current_gains.ki,
            kd=kd if kd is not None else current_gains.kd,
            ke=ke if ke is not None else current_gains.ke,
        )

        # Update stored gains
        self._set_gains_for_mode(resolved_mode, new_gains)

        # Sync to PID controller
        self._sync_gains_to_controller(new_gains)

        # Record snapshot
        self._record_snapshot(new_gains, reason, resolved_mode, metrics)

    def _record_snapshot(
        self,
        gains: PIDGains,
        reason: PIDChangeReason,
        mode: HVACMode,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Record a snapshot to history."""
        actor = REASON_TO_ACTOR.get(reason, PIDChangeActor.SYSTEM)

        snapshot: dict[str, Any] = {
            "timestamp": dt_util.utcnow().isoformat(),
            "kp": gains.kp,
            "ki": gains.ki,
            "kd": gains.kd,
            "ke": gains.ke,
            "reason": reason.value,
            "actor": actor.value,
        }

        if metrics is not None:
            snapshot["metrics"] = metrics

        mode_key = self._mode_key(mode)
        self._pid_history[mode_key].append(snapshot)

        # Enforce history size limit (FIFO)
        if len(self._pid_history[mode_key]) > PID_HISTORY_SIZE:
            self._pid_history[mode_key] = self._pid_history[mode_key][-PID_HISTORY_SIZE:]

    def get_gains(self, mode: HVACMode | None = None) -> PIDGains:
        """Get current gains for mode.

        Args:
            mode: HVAC mode (defaults to current mode via callback).

        Returns:
            PIDGains object for the specified mode.
        """
        resolved_mode = self._resolve_mode(mode)
        return self._get_gains_for_mode(resolved_mode)

    def get_history(self, mode: HVACMode | None = None) -> list[dict[str, Any]]:
        """Get history for mode.

        Args:
            mode: HVAC mode (defaults to heating).

        Returns:
            List of history snapshots.
        """
        resolved_mode = self._resolve_mode(mode) if mode is not None else HVACMode.HEAT
        mode_key = self._mode_key(resolved_mode)
        return list(self._pid_history[mode_key])

    def restore_from_state(self, old_state: Any) -> None:
        """Restore gains and history from HA state restoration.

        Args:
            old_state: The old state object with attributes dict.
        """
        if old_state is None:
            return

        attrs = getattr(old_state, "attributes", {})
        if not attrs:
            return

        # Restore gains with defaults for missing fields
        kp = attrs.get("kp", self._heating_gains.kp)
        ki = attrs.get("ki", self._heating_gains.ki)
        kd = attrs.get("kd", self._heating_gains.kd)
        ke = attrs.get("ke", 0.0)  # Default ke to 0 for backward compat

        # Use set_gains with RESTORE reason
        self.set_gains(
            PIDChangeReason.RESTORE,
            kp=kp,
            ki=ki,
            kd=kd,
            ke=ke,
            mode=HVACMode.HEAT,
        )

        # Restore history (prepend to current, since set_gains just added one entry)
        old_history = attrs.get("pid_history", [])
        if old_history:
            self._restore_history(old_history)

    def _restore_history(self, old_history: list[dict] | dict) -> None:
        """Restore history from old format, handling migration."""
        # Handle mode-keyed format: {"heating": [...], "cooling": [...]}
        if isinstance(old_history, dict):
            for mode_key in ["heating", "cooling"]:
                if mode_key in old_history:
                    entries = old_history[mode_key]
                    if isinstance(entries, list):
                        # Prepend old history before the restore entry
                        migrated = [self._migrate_history_entry(e) for e in entries]
                        self._pid_history[mode_key] = migrated + self._pid_history[mode_key]
        # Handle flat list format (old AdaptiveLearner format) - migrate to heating
        elif isinstance(old_history, list):
            migrated = [self._migrate_history_entry(e) for e in old_history]
            self._pid_history["heating"] = migrated + self._pid_history["heating"]

        # Enforce size limits
        for mode_key in ["heating", "cooling"]:
            if len(self._pid_history[mode_key]) > PID_HISTORY_SIZE:
                self._pid_history[mode_key] = self._pid_history[mode_key][-PID_HISTORY_SIZE:]

    def _migrate_history_entry(self, entry: dict) -> dict:
        """Migrate a single history entry to new format."""
        if not isinstance(entry, dict):
            return {}

        # Handle datetime objects
        timestamp = entry.get("timestamp", "")
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()

        migrated = {
            "timestamp": timestamp,
            "kp": entry.get("kp", 0.0),
            "ki": entry.get("ki", 0.0),
            "kd": entry.get("kd", 0.0),
            "ke": entry.get("ke", 0.0),  # Default for old entries without ke
            "reason": entry.get("reason", "unknown"),
        }

        # Add actor if present, or leave it out (backward compat)
        if "actor" in entry:
            migrated["actor"] = entry["actor"]

        # Preserve metrics if present
        if "metrics" in entry:
            migrated["metrics"] = entry["metrics"]

        return migrated

    def get_state_for_persistence(self) -> dict[str, Any]:
        """Get state dict for persistence.

        Returns:
            Dict containing gains and history for state attributes.
        """
        state: dict[str, Any] = {
            "heating_gains": {
                "kp": self._heating_gains.kp,
                "ki": self._heating_gains.ki,
                "kd": self._heating_gains.kd,
                "ke": self._heating_gains.ke,
            },
            "pid_history": self._pid_history,
        }

        if self._cooling_gains is not None:
            state["cooling_gains"] = {
                "kp": self._cooling_gains.kp,
                "ki": self._cooling_gains.ki,
                "kd": self._cooling_gains.kd,
                "ke": self._cooling_gains.ke,
            }

        return state
