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
        self._pid_controller.set_pid_param(kp=gains.kp, ki=gains.ki, kd=gains.kd, ke=gains.ke)

    def _gains_match_last_entry(self, gains: PIDGains, mode: HVACMode) -> bool:
        """Check if gains match the last history entry.

        Used to avoid duplicate RESTORE entries when gains unchanged.
        Rounds to 2 decimal places to match HA state serialization precision.
        """
        mode_key = self._mode_key(mode)
        history = self._pid_history.get(mode_key, [])
        if not history:
            return False
        last = history[-1]

        # Round to 2 decimals to match HA state serialization
        def r2(x: float) -> float:
            return round(x, 2)

        return (
            r2(gains.kp) == r2(last.get("kp", 0.0))
            and r2(gains.ki) == r2(last.get("ki", 0.0))
            and r2(gains.kd) == r2(last.get("kd", 0.0))
            and r2(gains.ke) == r2(last.get("ke", 0.0))
        )

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

        # Record snapshot only if gains differ from last entry (dedup)
        if not self._gains_match_last_entry(new_gains, resolved_mode):
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

        # 1. Restore history FIRST (before set_gains)
        # This allows set_gains to deduplicate if gains match last entry
        old_history = attrs.get("pid_history", [])
        if old_history:
            self._restore_history(old_history)

        # 2. Get gains from LAST history entry if available, else fall back to attrs
        mode_key = "heating"
        if self._pid_history[mode_key]:
            last_entry = self._pid_history[mode_key][-1]
            kp = last_entry.get("kp", self._heating_gains.kp)
            ki = last_entry.get("ki", self._heating_gains.ki)
            kd = last_entry.get("kd", self._heating_gains.kd)
            ke = last_entry.get("ke", 0.0)
        else:
            # Fallback to top-level attrs (backward compat)
            kp = attrs.get("kp", self._heating_gains.kp)
            ki = attrs.get("ki", self._heating_gains.ki)
            kd = attrs.get("kd", self._heating_gains.kd)
            ke = attrs.get("ke", 0.0)

        # 3. Set gains (will skip recording if unchanged from last entry)
        self.set_gains(
            PIDChangeReason.RESTORE,
            kp=kp,
            ki=ki,
            kd=kd,
            ke=ke,
            mode=HVACMode.HEAT,
        )

    def _restore_history(self, old_history: list[dict] | dict) -> None:
        """Restore history from old format, handling migration.

        Called BEFORE set_gains() during restore, so history is populated
        before deduplication check runs.
        """
        # Handle mode-keyed format: {"heating": [...], "cooling": [...]}
        if isinstance(old_history, dict):
            for mode_key in ["heating", "cooling"]:
                if mode_key in old_history:
                    entries = old_history[mode_key]
                    if isinstance(entries, list):
                        migrated = [self._migrate_history_entry(e) for e in entries]
                        self._pid_history[mode_key] = migrated
        # Handle flat list format (old AdaptiveLearner format) - migrate to heating
        elif isinstance(old_history, list):
            migrated = [self._migrate_history_entry(e) for e in old_history]
            self._pid_history["heating"] = migrated

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

    def delete_history_entries(self, indices: list[int], mode: HVACMode = HVACMode.HEAT) -> int:
        """Delete specific entries from PID history by index.

        Args:
            indices: List of 0-based indices to delete (0 = oldest entry).
            mode: Which mode's history to modify.

        Returns:
            Number of entries actually deleted.

        Raises:
            ValueError: If any index is out of range.
        """
        if not indices:
            return 0

        mode_key = self._mode_key(mode)
        history = self._pid_history[mode_key]

        if not history:
            return 0

        # Validate all indices before deleting any
        for idx in indices:
            if idx < 0 or idx >= len(history):
                raise ValueError(f"Invalid history index {idx} (history has {len(history)} entries)")

        # Delete in reverse order to preserve indices during deletion
        for idx in sorted(indices, reverse=True):
            del history[idx]

        return len(indices)

    def restore_from_history(self, index: int, mode: HVACMode = HVACMode.HEAT) -> dict:
        """Restore PID gains from a specific history entry.

        Args:
            index: 0-based index of history entry (0 = oldest).
            mode: Which mode's history to restore from.

        Returns:
            The restored history entry dict.

        Raises:
            ValueError: If index is out of range or history is empty.
        """
        mode_key = self._mode_key(mode)
        history = self._pid_history[mode_key]

        if not history:
            raise ValueError(f"Invalid history index {index} (history is empty)")

        if index < 0 or index >= len(history):
            raise ValueError(f"Invalid history index {index} (history has {len(history)} entries)")

        # Get the entry at the specified index
        entry = history[index]

        # Restore gains from the entry
        self.set_gains(
            reason=PIDChangeReason.HISTORY_RESTORE,
            kp=entry.get("kp"),
            ki=entry.get("ki"),
            kd=entry.get("kd"),
            ke=entry.get("ke"),
            mode=mode,
        )

        return entry

    def ensure_initial_history_recorded(self) -> None:
        """Ensure initial physics-calculated gains are recorded to history.

        Called after state restoration completes. If history is empty for the
        current mode, records the current gains with PHYSICS_INIT reason.

        This is idempotent - safe to call multiple times.
        """
        mode = self._resolve_mode(None)
        mode_key = self._mode_key(mode)

        # Only record if history is empty (fresh start, no saved state)
        if not self._pid_history[mode_key]:
            current_gains = self._get_gains_for_mode(mode)
            self._record_snapshot(
                current_gains,
                PIDChangeReason.PHYSICS_INIT,
                mode,
                metrics=None,
            )

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
