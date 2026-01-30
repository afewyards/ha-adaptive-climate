"""Tests for PIDGainsManager - state management and history tracking."""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock
from homeassistant.components.climate import HVACMode

from custom_components.adaptive_thermostat.managers.pid_gains_manager import PIDGainsManager
from custom_components.adaptive_thermostat.const import (
    PIDChangeReason,
    PIDChangeActor,
    REASON_TO_ACTOR,
    PIDGains,
    PID_HISTORY_SIZE,
)


@pytest.fixture
def mock_pid_controller():
    """Create a mock PID controller."""
    pid = Mock()
    pid.set_pid_param = Mock()
    return pid


@pytest.fixture
def initial_heating_gains():
    """Create initial heating gains for testing."""
    return PIDGains(kp=1.0, ki=0.1, kd=10.0, ke=0.0)


@pytest.fixture
def initial_cooling_gains():
    """Create initial cooling gains for testing."""
    return PIDGains(kp=1.5, ki=0.15, kd=12.0, ke=0.0)


@pytest.fixture
def manager(mock_pid_controller, initial_heating_gains):
    """Create a PIDGainsManager instance for testing."""
    return PIDGainsManager(mock_pid_controller, initial_heating_gains)


# =============================================================================
# Basic Operations
# =============================================================================

class TestPIDGainsManagerBasicOperations:
    """Tests for basic PIDGainsManager operations."""

    def test_set_gains_all_four_parameters(self, manager):
        """Test set_gains() with all four gains (kp, ki, kd, ke)."""
        manager.set_gains(
            reason=PIDChangeReason.PHYSICS_RESET,
            kp=2.0,
            ki=0.2,
            kd=15.0,
            ke=0.5,
        )

        gains = manager.get_gains()
        assert gains.kp == 2.0
        assert gains.ki == 0.2
        assert gains.kd == 15.0
        assert gains.ke == 0.5

    def test_get_gains_returns_current_gains(self, manager):
        """Test get_gains() returns current PIDGains object."""
        gains = manager.get_gains()

        assert isinstance(gains, PIDGains)
        assert gains.kp == 1.0
        assert gains.ki == 0.1
        assert gains.kd == 10.0
        assert gains.ke == 0.0

    def test_snapshot_recorded_on_set_gains(self, manager):
        """Test snapshot recording: history entry created with timestamp, gains, reason, actor."""
        manager.set_gains(
            reason=PIDChangeReason.PHYSICS_RESET,
            kp=2.0,
            ki=0.2,
            kd=15.0,
            ke=0.5,
        )

        history = manager.get_history(mode=HVACMode.HEAT)

        assert len(history) == 1
        snapshot = history[0]
        assert "timestamp" in snapshot
        assert "kp" in snapshot
        assert "ki" in snapshot
        assert "kd" in snapshot
        assert "ke" in snapshot
        assert "reason" in snapshot
        assert "actor" in snapshot
        assert snapshot["kp"] == 2.0
        assert snapshot["ki"] == 0.2
        assert snapshot["kd"] == 15.0
        assert snapshot["ke"] == 0.5
        assert snapshot["reason"] == PIDChangeReason.PHYSICS_RESET.value
        assert snapshot["actor"] == PIDChangeActor.USER.value

    def test_snapshot_timestamp_is_iso_format(self, manager):
        """Test snapshot timestamp is in ISO 8601 format."""
        manager.set_gains(reason=PIDChangeReason.AUTO_APPLY, kp=1.5)
        history = manager.get_history(mode=HVACMode.HEAT)

        snapshot = history[0]
        timestamp_str = snapshot["timestamp"]
        # Should be parseable as ISO format
        assert isinstance(timestamp_str, str)
        assert "T" in timestamp_str  # ISO 8601 format has T separator
        # Should be parseable
        parsed = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        assert parsed is not None

    def test_snapshot_with_optional_metrics(self, manager):
        """Test snapshot includes optional metrics when provided."""
        metrics = {"overshoot": 0.5, "settling_time": 120, "confidence": 0.85}
        manager.set_gains(reason=PIDChangeReason.AUTO_APPLY, kp=1.5, metrics=metrics)

        history = manager.get_history(mode=HVACMode.HEAT)
        snapshot = history[0]
        assert "metrics" in snapshot
        assert snapshot["metrics"]["overshoot"] == 0.5
        assert snapshot["metrics"]["settling_time"] == 120
        assert snapshot["metrics"]["confidence"] == 0.85


# =============================================================================
# Mode-Specific Gains
# =============================================================================

class TestPIDGainsManagerModeSpecificGains:
    """Tests for mode-specific gain storage and switching."""

    def test_manager_stores_separate_heating_and_cooling_gains(
        self, mock_pid_controller, initial_heating_gains, initial_cooling_gains
    ):
        """Test manager stores separate _heating_gains and _cooling_gains."""
        manager = PIDGainsManager(
            pid_controller=mock_pid_controller,
            initial_heating_gains=initial_heating_gains,
            initial_cooling_gains=initial_cooling_gains,
        )

        assert hasattr(manager, '_heating_gains')
        assert hasattr(manager, '_cooling_gains')
        assert manager._heating_gains.kp == 1.0
        assert manager._cooling_gains.kp == 1.5

    def test_set_gains_heat_mode_updates_heating_gains_only(
        self, mock_pid_controller, initial_heating_gains, initial_cooling_gains
    ):
        """Test set_gains(mode=HVACMode.HEAT) updates heating gains only."""
        manager = PIDGainsManager(
            pid_controller=mock_pid_controller,
            initial_heating_gains=initial_heating_gains,
            initial_cooling_gains=initial_cooling_gains,
        )

        manager.set_gains(reason=PIDChangeReason.AUTO_APPLY, kp=2.0, mode=HVACMode.HEAT)

        heating_gains = manager.get_gains(mode=HVACMode.HEAT)
        cooling_gains = manager.get_gains(mode=HVACMode.COOL)
        assert heating_gains.kp == 2.0  # Changed
        assert cooling_gains.kp == 1.5  # Unchanged

    def test_history_keyed_by_mode(
        self, mock_pid_controller, initial_heating_gains, initial_cooling_gains
    ):
        """Test history is keyed by mode."""
        manager = PIDGainsManager(
            pid_controller=mock_pid_controller,
            initial_heating_gains=initial_heating_gains,
            initial_cooling_gains=initial_cooling_gains,
        )

        manager.set_gains(reason=PIDChangeReason.AUTO_APPLY, kp=1.5, mode=HVACMode.HEAT)
        manager.set_gains(reason=PIDChangeReason.AUTO_APPLY, kp=2.0, mode=HVACMode.COOL)

        heating_history = manager.get_history(mode=HVACMode.HEAT)
        cooling_history = manager.get_history(mode=HVACMode.COOL)

        assert len(heating_history) == 1
        assert len(cooling_history) == 1
        assert heating_history[0]["kp"] == 1.5
        assert cooling_history[0]["kp"] == 2.0


# =============================================================================
# Partial Updates
# =============================================================================

class TestPIDGainsManagerPartialUpdates:
    """Tests for partial PID gain updates."""

    def test_partial_update_kp_only(self, manager):
        """Test set_gains(kp=...) only updates kp, leaves ki/kd/ke unchanged."""
        manager.set_gains(reason=PIDChangeReason.AUTO_APPLY, kp=2.5)
        gains = manager.get_gains()

        assert gains.kp == 2.5  # Updated
        assert gains.ki == 0.1  # Unchanged
        assert gains.kd == 10.0  # Unchanged
        assert gains.ke == 0.0  # Unchanged

    def test_partial_update_ki_only(self, manager):
        """Test set_gains(ki=...) only updates ki."""
        manager.set_gains(reason=PIDChangeReason.UNDERSHOOT_BOOST, ki=0.3)
        gains = manager.get_gains()

        assert gains.kp == 1.0  # Unchanged
        assert gains.ki == 0.3  # Updated
        assert gains.kd == 10.0  # Unchanged
        assert gains.ke == 0.0  # Unchanged

    def test_partial_update_snapshot_contains_all_gains(self, manager):
        """Test partial update snapshot contains ALL current gains, not just changed ones."""
        manager.set_gains(reason=PIDChangeReason.AUTO_APPLY, kp=2.0)
        history = manager.get_history()

        snapshot = history[0]
        assert snapshot["kp"] == 2.0  # Updated value
        assert snapshot["ki"] == 0.1  # Unchanged, but included
        assert snapshot["kd"] == 10.0  # Unchanged, but included
        assert snapshot["ke"] == 0.0  # Unchanged, but included


# =============================================================================
# State Restoration
# =============================================================================

class TestRestoreFromState:
    """Tests for restore_from_state() method."""

    def test_restore_from_state_full(self, manager):
        """restore_from_state should restore gains and history."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.8,
            "ki": 0.015,
            "kd": 12.0,
            "ke": 0.6,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.5,
                    "reason": "physics_init",
                    "actor": "system",
                },
                {
                    "timestamp": "2024-01-15T11:00:00",
                    "kp": 1.8,
                    "ki": 0.015,
                    "kd": 12.0,
                    "ke": 0.6,
                    "reason": "adaptive_apply",
                    "actor": "user",
                },
            ],
        }

        manager.restore_from_state(old_state)

        gains = manager.get_gains(HVACMode.HEAT)
        assert gains.kp == 1.8
        assert gains.ki == 0.015
        assert gains.kd == 12.0
        assert gains.ke == 0.6

        history = manager.get_history(HVACMode.HEAT)
        # Should have 2 old entries (no RESTORE added since gains match last entry)
        assert len(history) == 2

    def test_restore_from_state_applies_to_pid_controller(self, mock_pid_controller, initial_heating_gains):
        """Restored gains should be applied to PIDController."""
        manager = PIDGainsManager(mock_pid_controller, initial_heating_gains)

        old_state = Mock()
        old_state.attributes = {"kp": 2.0, "ki": 0.02, "kd": 15.0, "ke": 0.7}

        manager.restore_from_state(old_state)

        # Verify PIDController received the gains (called during init + restore)
        assert mock_pid_controller.set_pid_param.called
        # Check that kp=2.0 was set
        calls = [str(call) for call in mock_pid_controller.set_pid_param.call_args_list]
        assert any('2.0' in call for call in calls)

    def test_restore_with_none_state(self, manager):
        """restore_from_state with None should be a no-op."""
        manager.restore_from_state(None)
        gains = manager.get_gains()
        assert gains is not None

    def test_restore_with_empty_attributes(self, manager):
        """restore_from_state should handle missing attributes gracefully."""
        old_state = Mock()
        old_state.attributes = {}
        manager.restore_from_state(old_state)
        # Should not crash


# =============================================================================
# Backward Compatibility
# =============================================================================

class TestRestoreBackwardCompatibility:
    """Tests for backward compatibility with old state formats."""

    def test_restore_backward_compat_no_ke(self, manager):
        """Old state without ke should default ke to 0."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            # No "ke" attribute
        }

        manager.restore_from_state(old_state)

        gains = manager.get_gains()
        assert gains.ke == 0.0  # Default

    def test_restore_backward_compat_old_history_format(self, manager):
        """Old history format (flat list in attributes) should be handled."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            "ke": 0.5,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "reason": "physics_init",
                    # No ke, no actor
                }
            ],
        }

        manager.restore_from_state(old_state)

        history = manager.get_history(HVACMode.HEAT)
        assert len(history) >= 1
        # Old entries without ke should have ke defaulted
        assert history[0].get("ke", 0.0) == 0.0

    def test_restore_flat_history_format(self, manager):
        """Old flat pid_history list (not mode-keyed) should migrate to heating."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            "pid_history": [  # Flat list, not mode-keyed
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "reason": "physics_init",
                }
            ]
        }

        manager.restore_from_state(old_state)

        history = manager.get_history()
        assert len(history) >= 1


# =============================================================================
# History Serialization
# =============================================================================

class TestPIDHistorySerializationWithKe:
    """Tests for pid_history serialization including ke field."""

    def test_get_state_for_persistence_includes_gains(self, manager):
        """State for persistence should include current gains."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.5, ki=0.01, kd=10.0, ke=0.5)
        state = manager.get_state_for_persistence()

        assert "heating_gains" in state
        assert state["heating_gains"]["kp"] == 1.5
        assert state["heating_gains"]["ki"] == 0.01
        assert state["heating_gains"]["kd"] == 10.0
        assert state["heating_gains"]["ke"] == 0.5

    def test_get_state_for_persistence_includes_history(self, manager):
        """State for persistence should include full history."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.5, ki=0.01, kd=10.0, ke=0.5)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.8)

        state = manager.get_state_for_persistence()

        assert "pid_history" in state
        assert "heating" in state["pid_history"]
        assert len(state["pid_history"]["heating"]) == 2

    def test_history_entry_includes_ke(self, manager):
        """Each history entry should include ke field."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.5, ki=0.01, kd=10.0, ke=0.5)
        history = manager.get_history()
        assert "ke" in history[0]
        assert history[0]["ke"] == 0.5

    def test_cooling_gains_in_state(self, mock_pid_controller, initial_heating_gains, initial_cooling_gains):
        """Cooling gains should be in state when configured."""
        manager = PIDGainsManager(
            pid_controller=mock_pid_controller,
            initial_heating_gains=initial_heating_gains,
            initial_cooling_gains=initial_cooling_gains,
        )

        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.5, ke=0.5)

        state = manager.get_state_for_persistence()
        assert "cooling_gains" in state
        assert "ke" in state["cooling_gains"]


# =============================================================================
# PID Change Reasons
# =============================================================================

class TestPIDChangeReasons:
    """Tests for PID change reasons and actors."""

    def test_physics_reset_reason_enum_exists(self):
        """Test that PHYSICS_RESET reason exists in PIDChangeReason enum."""
        assert hasattr(PIDChangeReason, 'PHYSICS_RESET')
        assert PIDChangeReason.PHYSICS_RESET.value == "physics_reset"

    def test_physics_reset_maps_to_user_actor(self):
        """Test that PHYSICS_RESET reason maps to USER actor."""
        assert PIDChangeReason.PHYSICS_RESET in REASON_TO_ACTOR
        assert REASON_TO_ACTOR[PIDChangeReason.PHYSICS_RESET] == PIDChangeActor.USER

    def test_adaptive_apply_reason_enum_exists(self):
        """Test that ADAPTIVE_APPLY reason exists in PIDChangeReason enum."""
        assert hasattr(PIDChangeReason, 'ADAPTIVE_APPLY')
        assert PIDChangeReason.ADAPTIVE_APPLY.value == "adaptive_apply"

    def test_auto_apply_reason_enum_exists(self):
        """Test that AUTO_APPLY reason exists in PIDChangeReason enum."""
        assert hasattr(PIDChangeReason, 'AUTO_APPLY')
        assert PIDChangeReason.AUTO_APPLY.value == "auto_apply"

    def test_rollback_reason_enum_exists(self):
        """Test that ROLLBACK reason exists in PIDChangeReason enum."""
        assert hasattr(PIDChangeReason, 'ROLLBACK')
        assert PIDChangeReason.ROLLBACK.value == "rollback"


# =============================================================================
# History Size Limits
# =============================================================================

class TestHistorySizeLimits:
    """Tests for history size limiting."""

    def test_history_maintains_fifo_order(self, manager):
        """History should maintain FIFO order, evicting oldest when full."""
        # Add more than PID_HISTORY_SIZE entries
        for i in range(15):
            manager.set_gains(
                reason=PIDChangeReason.ADAPTIVE_APPLY,
                kp=1.5 + i * 0.1,
            )

        history = manager.get_history()
        # Should keep only last PID_HISTORY_SIZE entries
        assert len(history) <= PID_HISTORY_SIZE
        # Most recent should be at the end
        assert history[-1]["kp"] == pytest.approx(1.5 + 14 * 0.1, rel=1e-5)

    def test_history_size_limit_after_restore(self, manager):
        """After restore, history should be trimmed to PID_HISTORY_SIZE."""
        # Create old state with more than PID_HISTORY_SIZE entries
        old_history = [
            {
                "timestamp": f"2024-01-15T{i:02d}:00:00",
                "kp": 1.5 + i * 0.1,
                "ki": 0.01,
                "kd": 10.0,
                "ke": 0.5,
                "reason": "adaptive_apply",
            }
            for i in range(15)  # More than PID_HISTORY_SIZE (10)
        ]

        old_state = Mock()
        old_state.attributes = {
            "kp": 2.0,  # Ignored - history is authoritative
            "ki": 0.02,
            "kd": 12.0,
            "ke": 0.6,
            "pid_history": old_history,
        }

        manager.restore_from_state(old_state)

        history = manager.get_history(HVACMode.HEAT)
        # Should be trimmed to PID_HISTORY_SIZE (only 10 most recent entries kept)
        # No RESTORE entry added since gains match last history entry
        assert len(history) == PID_HISTORY_SIZE
        # Should keep the most recent ones (indices 5-14 from old history)
        # index 5 = kp 1.5 + 5 * 0.1 = 2.0
        assert history[0]["kp"] == pytest.approx(1.5 + 5 * 0.1, rel=1e-5)
        # Last entry should be index 14 = kp 1.5 + 14 * 0.1 = 2.9
        assert history[-1]["kp"] == pytest.approx(1.5 + 14 * 0.1, rel=1e-5)


# =============================================================================
# History Migration from AdaptiveLearner
# =============================================================================

class TestHistoryMigrationFromAdaptiveLearner:
    """Tests for migrating pid_history from AdaptiveLearner format."""

    def test_restore_mode_keyed_history_dict(self, manager):
        """New format: mode-keyed dict {"heating": [...], "cooling": [...]}."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            "ke": 0.5,
            "pid_history": {
                "heating": [
                    {
                        "timestamp": "2024-01-15T10:00:00",
                        "kp": 1.5,
                        "ki": 0.01,
                        "kd": 10.0,
                        "ke": 0.5,
                        "reason": "physics_init",
                        "actor": "system",
                    }
                ],
                "cooling": [
                    {
                        "timestamp": "2024-01-15T11:00:00",
                        "kp": 1.8,
                        "ki": 0.015,
                        "kd": 12.0,
                        "ke": 0.6,
                        "reason": "adaptive_apply",
                        "actor": "learner",
                    }
                ],
            },
        }

        manager.restore_from_state(old_state)

        heating_history = manager.get_history(HVACMode.HEAT)
        cooling_history = manager.get_history(HVACMode.COOL)

        # Should have 1 old + 1 restore in heating
        assert len(heating_history) >= 1
        # Should have 1 entry in cooling
        assert len(cooling_history) == 1
        assert cooling_history[0]["kp"] == 1.8

    def test_migrate_datetime_objects_to_iso_strings(self, manager):
        """Old entries with datetime objects should be converted to ISO strings."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            "pid_history": [
                {
                    "timestamp": datetime(2024, 1, 15, 10, 0, 0),  # datetime object
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "reason": "physics_init",
                }
            ],
        }

        manager.restore_from_state(old_state)

        history = manager.get_history()
        # First entry should have ISO string timestamp (not datetime object)
        timestamp = history[0]["timestamp"]
        assert isinstance(timestamp, str)
        assert "2024-01-15" in timestamp

    def test_migrate_missing_ke_field(self, manager):
        """Old entries without ke field should default to 0.0."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "reason": "physics_init",
                    # No ke field
                }
            ],
        }

        manager.restore_from_state(old_state)

        history = manager.get_history()
        # First entry should have ke defaulted to 0.0
        assert history[0]["ke"] == 0.0

    def test_migrate_missing_actor_field(self, manager):
        """Old entries without actor field should still work."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.5,
                    "reason": "physics_init",
                    # No actor field (old format)
                }
            ],
        }

        manager.restore_from_state(old_state)

        history = manager.get_history()
        # Entry may or may not have actor field - that's OK
        assert len(history) >= 1
        # Should preserve other fields
        assert history[0]["kp"] == 1.5
        assert history[0]["ke"] == 0.5

    def test_migrate_preserves_metrics_field(self, manager):
        """Migration should preserve optional metrics field."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.5,
            "ki": 0.01,
            "kd": 10.0,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.5,
                    "reason": "adaptive_apply",
                    "metrics": {
                        "overshoot": 0.5,
                        "settling_time": 120,
                    },
                }
            ],
        }

        manager.restore_from_state(old_state)

        history = manager.get_history()
        # First entry should preserve metrics
        assert "metrics" in history[0]
        assert history[0]["metrics"]["overshoot"] == 0.5
        assert history[0]["metrics"]["settling_time"] == 120


# =============================================================================
# Restore Deduplication
# =============================================================================

class TestRestoreDeduplication:
    """Tests for deduplicating RESTORE entries when gains unchanged."""

    def test_restore_unchanged_gains_no_new_entry(self, manager):
        """Restore with unchanged gains should NOT add a RESTORE entry."""
        # History with last entry matching gains we'll restore
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.8,
            "ki": 0.015,
            "kd": 12.0,
            "ke": 0.6,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.5,
                    "reason": "physics_init",
                    "actor": "system",
                },
                {
                    "timestamp": "2024-01-15T11:00:00",
                    "kp": 1.8,
                    "ki": 0.015,
                    "kd": 12.0,
                    "ke": 0.6,
                    "reason": "adaptive_apply",
                    "actor": "user",
                },
            ],
        }

        manager.restore_from_state(old_state)

        # Should have exactly 2 entries (no RESTORE added)
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 2
        # Last entry should still be adaptive_apply, not restore
        assert history[-1]["reason"] == "adaptive_apply"

    def test_restore_with_history_uses_history_values(self, manager):
        """Restore with history should use last history entry, not top-level attrs.

        Even when top-level attrs differ from history, history is authoritative.
        No RESTORE entry added since gains match last history entry.
        """
        old_state = Mock()
        old_state.attributes = {
            "kp": 2.0,  # Different from last history entry (ignored)
            "ki": 0.02,
            "kd": 15.0,
            "ke": 0.7,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.5,
                    "reason": "physics_init",
                    "actor": "system",
                },
            ],
        }

        manager.restore_from_state(old_state)

        # Gains should match last history entry
        gains = manager.get_gains(HVACMode.HEAT)
        assert gains.kp == 1.5
        assert gains.ki == 0.01
        assert gains.kd == 10.0
        assert gains.ke == 0.5

        # Should have only 1 entry (no RESTORE added since gains match history)
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 1
        assert history[0]["reason"] == "physics_init"

    def test_restore_empty_history_adds_restore_entry(self, manager):
        """Restore with empty history should add RESTORE entry."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.8,
            "ki": 0.015,
            "kd": 12.0,
            "ke": 0.6,
            "pid_history": [],  # Empty history
        }

        manager.restore_from_state(old_state)

        history = manager.get_history(HVACMode.HEAT)
        # Should have 1 RESTORE entry
        assert len(history) == 1
        assert history[0]["reason"] == "restore"

    def test_restore_preserves_history_order(self, manager):
        """Restore should preserve old entries in correct order."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.8,
            "ki": 0.015,
            "kd": 12.0,
            "ke": 0.6,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T09:00:00",
                    "kp": 1.0,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.0,
                    "reason": "physics_init",
                },
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.5,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.5,
                    "reason": "ke_learning",
                },
                {
                    "timestamp": "2024-01-15T11:00:00",
                    "kp": 1.8,
                    "ki": 0.015,
                    "kd": 12.0,
                    "ke": 0.6,
                    "reason": "adaptive_apply",
                },
            ],
        }

        manager.restore_from_state(old_state)

        history = manager.get_history(HVACMode.HEAT)
        # Should have exactly 3 entries (gains match last, no RESTORE added)
        assert len(history) == 3
        assert history[0]["reason"] == "physics_init"
        assert history[1]["reason"] == "ke_learning"
        assert history[2]["reason"] == "adaptive_apply"

    def test_multiple_restarts_no_duplicate_entries(self, manager):
        """Multiple restarts with same gains should not accumulate RESTORE entries."""
        old_state = Mock()
        old_state.attributes = {
            "kp": 1.8,
            "ki": 0.015,
            "kd": 12.0,
            "ke": 0.6,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T11:00:00",
                    "kp": 1.8,
                    "ki": 0.015,
                    "kd": 12.0,
                    "ke": 0.6,
                    "reason": "adaptive_apply",
                },
            ],
        }

        # Simulate multiple restarts
        manager.restore_from_state(old_state)
        manager.restore_from_state(old_state)
        manager.restore_from_state(old_state)

        history = manager.get_history(HVACMode.HEAT)
        # Should still have just 1 entry
        assert len(history) == 1
        assert history[0]["reason"] == "adaptive_apply"

    def test_restore_with_precision_mismatch_no_duplicate(self, manager):
        """Restore should handle float precision differences from JSON serialization.

        Uses 2 decimal place rounding to match HA state serialization.
        """
        old_state = Mock()
        old_state.attributes = {
            # Full precision values (as stored in state attributes)
            # These round to: kp=1.84, ki=5.33, ke=0.21
            "kp": 1.8368,
            "ki": 5.3306,
            "kd": 0.6,
            "ke": 0.2073,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T11:00:00",
                    # Rounded values (as stored in history by HA)
                    "kp": 1.84,
                    "ki": 5.33,
                    "kd": 0.6,
                    "ke": 0.21,
                    "reason": "adaptive_apply",
                },
            ],
        }

        manager.restore_from_state(old_state)

        history = manager.get_history(HVACMode.HEAT)
        # Should NOT add a RESTORE entry since gains match when rounded to 2 decimals
        assert len(history) == 1
        assert history[0]["reason"] == "adaptive_apply"

    def test_restore_uses_last_history_entry_not_top_level_attrs(self, manager):
        """Restore should use gains from last history entry, ignoring top-level attrs.

        This ensures the actual learned/tuned values from history are restored,
        not potentially stale top-level attributes.
        """
        old_state = Mock()
        old_state.attributes = {
            # Top-level attrs have DIFFERENT values (these should be IGNORED)
            "kp": 999.0,
            "ki": 999.0,
            "kd": 999.0,
            "ke": 999.0,
            "pid_history": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "kp": 1.0,
                    "ki": 0.01,
                    "kd": 10.0,
                    "ke": 0.0,
                    "reason": "physics_init",
                },
                {
                    "timestamp": "2024-01-15T11:00:00",
                    # These are the values that SHOULD be restored
                    "kp": 2.5,
                    "ki": 0.025,
                    "kd": 18.0,
                    "ke": 0.8,
                    "reason": "adaptive_apply",
                },
            ],
        }

        manager.restore_from_state(old_state)

        # Gains should match LAST history entry, NOT top-level attrs
        gains = manager.get_gains(HVACMode.HEAT)
        assert gains.kp == 2.5
        assert gains.ki == 0.025
        assert gains.kd == 18.0
        assert gains.ke == 0.8

        # History should have 2 entries (no RESTORE added since gains match last entry)
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 2


# =============================================================================
# History Deletion
# =============================================================================

class TestHistoryDeletion:
    """Tests for delete_history_entries() method."""

    def test_delete_single_entry_from_middle(self, manager):
        """Test deleting a single entry from the middle of history."""
        # Build a history with 3 entries
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=2.0)

        # Delete middle entry (index 1)
        deleted_count = manager.delete_history_entries([1], mode=HVACMode.HEAT)

        assert deleted_count == 1
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 2
        assert history[0]["kp"] == 1.0
        assert history[1]["kp"] == 2.0

    def test_delete_multiple_entries(self, manager):
        """Test deleting multiple entries."""
        # Build a history with 5 entries
        for i in range(5):
            manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=1.0 + i * 0.5)

        # Delete indices 1 and 3
        deleted_count = manager.delete_history_entries([1, 3], mode=HVACMode.HEAT)

        assert deleted_count == 2
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 3
        assert history[0]["kp"] == 1.0  # index 0
        assert history[1]["kp"] == 2.0  # index 2 (was 2 before deletion)
        assert history[2]["kp"] == 3.0  # index 4 (was 4 before deletion)

    def test_delete_with_invalid_index_raises_value_error(self, manager):
        """Test that invalid index raises ValueError."""
        # Build a history with 3 entries
        for i in range(3):
            manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=1.0 + i * 0.5)

        # Try to delete out-of-range index
        with pytest.raises(ValueError, match="Invalid history index"):
            manager.delete_history_entries([5], mode=HVACMode.HEAT)

        # History should be unchanged
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 3

    def test_delete_negative_index_raises_value_error(self, manager):
        """Test that negative index raises ValueError."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0)

        with pytest.raises(ValueError, match="Invalid history index"):
            manager.delete_history_entries([-1], mode=HVACMode.HEAT)

    def test_delete_from_empty_history_returns_zero(self, manager):
        """Test deleting from empty history returns 0."""
        # No entries in history
        deleted_count = manager.delete_history_entries([0], mode=HVACMode.HEAT)

        assert deleted_count == 0

    def test_delete_preserves_remaining_entries_order(self, manager):
        """Test that deletion preserves the order of remaining entries."""
        # Build a history with entries that have different reasons
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=2.0)
        manager.set_gains(PIDChangeReason.ROLLBACK, kp=1.8)

        # Delete indices 0 and 2
        manager.delete_history_entries([0, 2], mode=HVACMode.HEAT)

        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 2
        assert history[0]["reason"] == "adaptive_apply"
        assert history[0]["kp"] == 1.5
        assert history[1]["reason"] == "rollback"
        assert history[1]["kp"] == 1.8

    def test_delete_all_entries(self, manager):
        """Test deleting all entries."""
        # Build a history with 3 entries
        for i in range(3):
            manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=1.0 + i * 0.5)

        # Delete all indices
        deleted_count = manager.delete_history_entries([0, 1, 2], mode=HVACMode.HEAT)

        assert deleted_count == 3
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 0

    def test_delete_mode_specific_history(
        self, mock_pid_controller, initial_heating_gains, initial_cooling_gains
    ):
        """Test that deletion only affects the specified mode."""
        manager = PIDGainsManager(
            pid_controller=mock_pid_controller,
            initial_heating_gains=initial_heating_gains,
            initial_cooling_gains=initial_cooling_gains,
        )

        # Add entries to both modes
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, mode=HVACMode.HEAT)
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=2.0, mode=HVACMode.COOL)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=1.5, mode=HVACMode.HEAT)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=2.5, mode=HVACMode.COOL)

        # Delete from heating mode only
        deleted_count = manager.delete_history_entries([0], mode=HVACMode.HEAT)

        assert deleted_count == 1
        heating_history = manager.get_history(HVACMode.HEAT)
        cooling_history = manager.get_history(HVACMode.COOL)

        assert len(heating_history) == 1
        assert len(cooling_history) == 2  # Unchanged
        assert heating_history[0]["kp"] == 1.5
        assert cooling_history[0]["kp"] == 2.0

    def test_delete_partial_failure_no_deletion(self, manager):
        """Test that if any index is invalid, no deletions occur."""
        # Build a history with 3 entries
        for i in range(3):
            manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=1.0 + i * 0.5)

        # Try to delete with one valid and one invalid index
        with pytest.raises(ValueError, match="Invalid history index"):
            manager.delete_history_entries([1, 10], mode=HVACMode.HEAT)

        # History should be completely unchanged
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 3

    def test_delete_empty_indices_list(self, manager):
        """Test that empty indices list returns 0 without errors."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0)

        deleted_count = manager.delete_history_entries([], mode=HVACMode.HEAT)

        assert deleted_count == 0
        history = manager.get_history(HVACMode.HEAT)
        assert len(history) == 1


# =============================================================================
# History Restore
# =============================================================================

class TestRestoreFromHistory:
    """Tests for restore_from_history() method."""

    def test_restore_from_valid_index(self, manager):
        """Test restoring gains from a valid history index."""
        # Build a history with 3 entries
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, ki=0.01, kd=10.0, ke=0.0)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5, ki=0.015, kd=12.0, ke=0.5)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=2.0, ki=0.02, kd=15.0, ke=0.7)

        # Restore from index 1 (middle entry)
        entry = manager.restore_from_history(1, mode=HVACMode.HEAT)

        assert entry is not None
        assert entry["kp"] == 1.5
        assert entry["ki"] == 0.015
        assert entry["kd"] == 12.0
        assert entry["ke"] == 0.5

        # Verify gains were applied
        gains = manager.get_gains(HVACMode.HEAT)
        assert gains.kp == 1.5
        assert gains.ki == 0.015
        assert gains.kd == 12.0
        assert gains.ke == 0.5

    def test_restore_creates_new_history_entry(self, manager):
        """Test that restore creates a new history entry with HISTORY_RESTORE reason."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, ki=0.01, kd=10.0, ke=0.0)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5, ki=0.015, kd=12.0, ke=0.5)

        # Restore from index 0
        manager.restore_from_history(0, mode=HVACMode.HEAT)

        history = manager.get_history(HVACMode.HEAT)
        # Should have 2 old + 1 new HISTORY_RESTORE entry
        assert len(history) == 3
        assert history[-1]["reason"] == PIDChangeReason.HISTORY_RESTORE.value
        assert history[-1]["actor"] == PIDChangeActor.USER.value
        assert history[-1]["kp"] == 1.0
        assert history[-1]["ki"] == 0.01
        assert history[-1]["kd"] == 10.0
        assert history[-1]["ke"] == 0.0

    def test_restore_from_oldest_entry(self, manager):
        """Test restoring from index 0 (oldest entry)."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, ki=0.01, kd=10.0, ke=0.0)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5, ki=0.015, kd=12.0, ke=0.5)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=2.0, ki=0.02, kd=15.0, ke=0.7)

        entry = manager.restore_from_history(0, mode=HVACMode.HEAT)

        assert entry["kp"] == 1.0
        gains = manager.get_gains(HVACMode.HEAT)
        assert gains.kp == 1.0

    def test_restore_from_newest_entry(self, manager):
        """Test restoring from the newest entry (last index)."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, ki=0.01, kd=10.0, ke=0.0)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5, ki=0.015, kd=12.0, ke=0.5)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=2.0, ki=0.02, kd=15.0, ke=0.7)

        # Restore from index 2 (last entry)
        entry = manager.restore_from_history(2, mode=HVACMode.HEAT)

        assert entry["kp"] == 2.0
        gains = manager.get_gains(HVACMode.HEAT)
        assert gains.kp == 2.0

    def test_restore_from_invalid_index_raises_value_error(self, manager):
        """Test that invalid index raises ValueError."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, ki=0.01, kd=10.0, ke=0.0)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5, ki=0.015, kd=12.0, ke=0.5)

        # Try to restore from out-of-range index
        with pytest.raises(ValueError, match="Invalid history index"):
            manager.restore_from_history(5, mode=HVACMode.HEAT)

        # Gains should be unchanged
        gains = manager.get_gains(HVACMode.HEAT)
        assert gains.kp == 1.5

    def test_restore_from_negative_index_raises_value_error(self, manager):
        """Test that negative index raises ValueError."""
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, ki=0.01, kd=10.0, ke=0.0)

        with pytest.raises(ValueError, match="Invalid history index"):
            manager.restore_from_history(-1, mode=HVACMode.HEAT)

    def test_restore_from_empty_history_raises_value_error(self, manager):
        """Test that restoring from empty history raises ValueError."""
        # No entries in history
        with pytest.raises(ValueError, match="Invalid history index"):
            manager.restore_from_history(0, mode=HVACMode.HEAT)

    def test_restore_mode_specific(
        self, mock_pid_controller, initial_heating_gains, initial_cooling_gains
    ):
        """Test that restore only affects the specified mode."""
        manager = PIDGainsManager(
            pid_controller=mock_pid_controller,
            initial_heating_gains=initial_heating_gains,
            initial_cooling_gains=initial_cooling_gains,
        )

        # Add entries to both modes
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.0, mode=HVACMode.HEAT)
        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=2.0, mode=HVACMode.COOL)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=1.5, mode=HVACMode.HEAT)
        manager.set_gains(PIDChangeReason.AUTO_APPLY, kp=2.5, mode=HVACMode.COOL)

        # Restore heating from index 0
        manager.restore_from_history(0, mode=HVACMode.HEAT)

        heating_gains = manager.get_gains(HVACMode.HEAT)
        cooling_gains = manager.get_gains(HVACMode.COOL)

        assert heating_gains.kp == 1.0  # Restored
        assert cooling_gains.kp == 2.5  # Unchanged

    def test_restore_returns_correct_entry(self, manager):
        """Test that restore returns the exact entry that was restored."""
        manager.set_gains(
            PIDChangeReason.PHYSICS_INIT,
            kp=1.0,
            ki=0.01,
            kd=10.0,
            ke=0.0,
            metrics={"test": "value"}
        )
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=1.5, ki=0.015, kd=12.0, ke=0.5)

        entry = manager.restore_from_history(0, mode=HVACMode.HEAT)

        # Entry should match the stored history entry
        history = manager.get_history(HVACMode.HEAT)
        # First entry should be physics_init (the one we restored from)
        assert entry["timestamp"] == history[0]["timestamp"]
        assert entry["kp"] == history[0]["kp"]
        assert entry["ki"] == history[0]["ki"]
        assert entry["kd"] == history[0]["kd"]
        assert entry["ke"] == history[0]["ke"]
        assert entry["reason"] == history[0]["reason"]
        assert entry.get("metrics") == history[0].get("metrics")

    def test_restore_applied_to_pid_controller(self, mock_pid_controller, initial_heating_gains):
        """Test that restored gains are applied to PIDController."""
        manager = PIDGainsManager(mock_pid_controller, initial_heating_gains)

        manager.set_gains(PIDChangeReason.PHYSICS_INIT, kp=1.5, ki=0.015, kd=12.0, ke=0.5)
        manager.set_gains(PIDChangeReason.ADAPTIVE_APPLY, kp=2.0, ki=0.02, kd=15.0, ke=0.7)

        # Clear mock calls
        mock_pid_controller.set_pid_param.reset_mock()

        # Restore from index 0
        manager.restore_from_history(0, mode=HVACMode.HEAT)

        # Verify PIDController received the restored gains
        assert mock_pid_controller.set_pid_param.called
        calls = mock_pid_controller.set_pid_param.call_args_list
        # Should have 4 calls (kp, ki, kd, ke)
        assert len(calls) >= 4
