"""Tests for night setback learning gate feature.

Tests verify that night setback is suppressed until learning reaches "tuned" status,
ensuring PID system has collected enough data before applying temperature reductions.

IMPORTANT: These tests follow TDD approach. Most tests will FAIL until the implementation
is complete in tasks #4, #5, and #6. The tests verify:
1. Night setback suppressed when status is "idle", "collecting", or "stable"
2. Night setback active when status is "tuned" or "optimized"
3. Status dict contains "suppressed_reason": "learning" when applicable
4. Heating-type-specific confidence thresholds work correctly
5. Transitions between states enable/disable night setback appropriately

Current implementation status: Tests written ✓, Implementation pending
"""
import pytest
from datetime import datetime, time
from unittest.mock import Mock, MagicMock, patch
from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
from custom_components.adaptive_climate.adaptive.night_setback import NightSetback
from custom_components.adaptive_climate.const import HeatingType


class MockHomeAssistant:
    """Mock Home Assistant instance for testing."""

    def __init__(self):
        self.data = {}


class TestNightSetbackLearningGate:
    """Test night setback suppression based on learning status."""

    def _create_manager(
        self,
        hass=None,
        night_setback_delta=2.0,
        get_learning_status=None,
    ):
        """Create a NightSetbackManager for testing.

        Args:
            hass: Optional Home Assistant instance
            night_setback_delta: Temperature setback delta
            get_learning_status: Optional callback that returns learning status

        Returns:
            NightSetbackManager instance
        """
        if hass is None:
            hass = MockHomeAssistant()

        night_setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=night_setback_delta,
        )

        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test_zone",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 19.0,
        )

        # Inject the learning status callback if provided
        if get_learning_status:
            manager._calculator._get_learning_status = get_learning_status

        return manager

    def test_night_setback_suppressed_when_collecting(self):
        """Test night setback is suppressed when learning status is 'collecting'."""
        # Mock learning status callback that returns "collecting"
        get_learning_status = Mock(return_value="collecting")

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_learning_status=get_learning_status,
        )

        # During night period (23:00)
        current = datetime(2024, 1, 15, 23, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should return original temperature without adjustment
        assert effective_target == 20.0  # No setback applied
        assert in_night_period is True  # Still in night period
        assert info.get("suppressed_reason") == "learning"  # Suppressed due to learning

    def test_night_setback_suppressed_when_idle(self):
        """Test night setback is suppressed when learning status is 'idle'."""
        # Mock learning status callback that returns "idle"
        get_learning_status = Mock(return_value="idle")

        manager = self._create_manager(
            night_setback_delta=2.5,
            get_learning_status=get_learning_status,
        )

        # During night period (02:00)
        current = datetime(2024, 1, 15, 2, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should return original temperature without adjustment
        assert effective_target == 20.0  # No setback applied
        assert in_night_period is True  # Still in night period
        assert info.get("suppressed_reason") == "learning"  # Suppressed due to learning

    def test_night_setback_suppressed_when_stable(self):
        """Test night setback is suppressed when learning status is 'stable' (not tuned yet)."""
        # Mock learning status callback that returns "stable"
        get_learning_status = Mock(return_value="stable")

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_learning_status=get_learning_status,
        )

        # During night period (23:00)
        current = datetime(2024, 1, 15, 23, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should NOT apply setback - stable is not tuned yet
        assert effective_target == 20.0  # No setback applied
        assert in_night_period is True
        assert info.get("suppressed_reason") == "learning"  # Suppressed due to learning

    def test_night_setback_active_when_tuned(self):
        """Test night setback is active when learning status is 'tuned'."""
        # Mock learning status callback that returns "tuned"
        get_learning_status = Mock(return_value="tuned")

        manager = self._create_manager(
            night_setback_delta=3.0,
            get_learning_status=get_learning_status,
        )

        # During night period (01:00)
        current = datetime(2024, 1, 15, 1, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply the setback delta
        assert effective_target == 17.0  # 20.0 - 3.0
        assert in_night_period is True
        assert "suppressed_reason" not in info  # Not suppressed

    def test_night_setback_active_when_optimized(self):
        """Test night setback is active when learning status is 'optimized'."""
        # Mock learning status callback that returns "optimized"
        get_learning_status = Mock(return_value="optimized")

        manager = self._create_manager(
            night_setback_delta=2.5,
            get_learning_status=get_learning_status,
        )

        # During night period (04:00)
        current = datetime(2024, 1, 15, 4, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply the setback delta
        assert effective_target == 17.5  # 20.0 - 2.5
        assert in_night_period is True
        assert "suppressed_reason" not in info  # Not suppressed

    def test_night_setback_during_day_not_applied_regardless_of_status(self):
        """Test night setback is not applied during day regardless of learning status."""
        # Test with "collecting" status
        get_learning_status_collecting = Mock(return_value="collecting")
        manager = self._create_manager(
            night_setback_delta=2.0,
            get_learning_status=get_learning_status_collecting,
        )

        # During day (10:00)
        current = datetime(2024, 1, 15, 10, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Not in night period, so no setback
        assert effective_target == 20.0
        assert in_night_period is False
        assert "suppressed_reason" not in info

    def test_status_shows_suppressed_reason_when_applicable(self):
        """Test status dict contains suppressed_reason when night setback is suppressed."""
        # Mock learning status callback that returns "collecting"
        get_learning_status = Mock(return_value="collecting")

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_learning_status=get_learning_status,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            _, _, info = manager.calculate_night_setback_adjustment(current)

        # Verify suppressed_reason is present
        assert "suppressed_reason" in info
        assert info["suppressed_reason"] == "learning"

    def test_status_no_suppressed_reason_when_active(self):
        """Test status dict does not contain suppressed_reason when night setback is active."""
        # Mock learning status callback that returns "stable"
        get_learning_status = Mock(return_value="stable")

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_learning_status=get_learning_status,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            _, _, info = manager.calculate_night_setback_adjustment(current)

        # Verify suppressed_reason is NOT present
        assert "suppressed_reason" not in info

    def test_restored_zone_with_tuned_confidence_enables_night_setback_immediately(self):
        """Test restored zone with tuned confidence enables night setback without fresh cycles."""
        # Mock learning status callback that returns "tuned" (simulating restored state)
        get_learning_status = Mock(return_value="tuned")

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_learning_status=get_learning_status,
        )

        # During night period, immediately after restoration
        current = datetime(2024, 1, 15, 23, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply setback immediately since learning status is "tuned"
        assert effective_target == 18.0  # 20.0 - 2.0
        assert in_night_period is True
        assert "suppressed_reason" not in info

    def test_no_callback_defaults_to_allowing_night_setback(self):
        """Test that without a learning status callback, night setback is allowed (backward compat)."""
        # Create manager without learning status callback
        manager = self._create_manager(
            night_setback_delta=2.0,
            get_learning_status=None,  # No callback
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply setback (backward compatibility)
        assert effective_target == 18.0  # 20.0 - 2.0
        assert in_night_period is True
        assert "suppressed_reason" not in info


class TestNightSetbackLearningGateHeatingTypeScaling:
    """Test heating-type-specific confidence thresholds for night setback gate."""

    @pytest.mark.parametrize("heating_type,cycles,confidence,expected_status", [
        # Floor hydronic: tier_1=32%, tier_2=56%, tier_3=95%
        (HeatingType.FLOOR_HYDRONIC, 6, 0.31, "collecting"),  # Below tier 1
        (HeatingType.FLOOR_HYDRONIC, 6, 0.32, "stable"),      # At tier 1
        (HeatingType.FLOOR_HYDRONIC, 6, 0.40, "stable"),      # Between tier 1 and 2
        (HeatingType.FLOOR_HYDRONIC, 6, 0.56, "tuned"),       # At tier 2
        (HeatingType.FLOOR_HYDRONIC, 6, 0.70, "tuned"),       # Between tier 2 and 3
        (HeatingType.FLOOR_HYDRONIC, 6, 0.95, "optimized"),   # At tier 3

        # Radiator: tier_1=36%, tier_2=63%, tier_3=95%
        (HeatingType.RADIATOR, 6, 0.35, "collecting"),        # Below tier 1
        (HeatingType.RADIATOR, 6, 0.36, "stable"),            # At tier 1
        (HeatingType.RADIATOR, 6, 0.50, "stable"),            # Between tier 1 and 2
        (HeatingType.RADIATOR, 6, 0.63, "tuned"),             # At tier 2
        (HeatingType.RADIATOR, 6, 0.80, "tuned"),             # Between tier 2 and 3
        (HeatingType.RADIATOR, 6, 0.95, "optimized"),         # At tier 3

        # Convector: tier_1=40%, tier_2=70%, tier_3=95%
        (HeatingType.CONVECTOR, 6, 0.39, "collecting"),       # Below tier 1
        (HeatingType.CONVECTOR, 6, 0.40, "stable"),           # At tier 1
        (HeatingType.CONVECTOR, 6, 0.60, "stable"),           # Between tier 1 and 2
        (HeatingType.CONVECTOR, 6, 0.70, "tuned"),            # At tier 2
        (HeatingType.CONVECTOR, 6, 0.85, "tuned"),            # Between tier 2 and 3
        (HeatingType.CONVECTOR, 6, 0.95, "optimized"),        # At tier 3

        # Forced air: tier_1=44%, tier_2=77%, tier_3=95%
        (HeatingType.FORCED_AIR, 6, 0.43, "collecting"),      # Below tier 1
        (HeatingType.FORCED_AIR, 6, 0.44, "stable"),          # At tier 1
        (HeatingType.FORCED_AIR, 6, 0.60, "stable"),          # Between tier 1 and 2
        (HeatingType.FORCED_AIR, 6, 0.77, "tuned"),           # At tier 2
        (HeatingType.FORCED_AIR, 6, 0.90, "tuned"),           # Between tier 2 and 3
        (HeatingType.FORCED_AIR, 6, 0.95, "optimized"),       # At tier 3
    ])
    def test_heating_type_confidence_thresholds(
        self, heating_type, cycles, confidence, expected_status
    ):
        """Test that heating-type-specific confidence thresholds work correctly.

        This test verifies that the learning status computation uses the correct
        confidence threshold for each heating type when determining if learning
        has reached "stable" status.
        """
        from custom_components.adaptive_climate.managers.state_attributes import _compute_learning_status

        # Compute learning status with the given parameters
        status = _compute_learning_status(
            cycle_count=cycles,
            convergence_confidence=confidence,
            heating_type=heating_type,
            is_paused=False,
        )

        assert status == expected_status

    def test_floor_hydronic_reaches_tuned_at_56_percent(self):
        """Test floor_hydronic reaches 'tuned' at 56% confidence (tier_2 * 0.8)."""
        from custom_components.adaptive_climate.managers.state_attributes import _compute_learning_status

        heating_type = HeatingType.FLOOR_HYDRONIC
        cycles = 8  # Sufficient cycles

        # Just below tier 2 - should be "stable"
        status_below = _compute_learning_status(
            cycle_count=cycles,
            convergence_confidence=0.55,
            heating_type=heating_type,
            is_paused=False,
        )
        assert status_below == "stable"

        # At tier 2 - should be "tuned"
        status_at = _compute_learning_status(
            cycle_count=cycles,
            convergence_confidence=0.56,
            heating_type=heating_type,
            is_paused=False,
        )
        assert status_at == "tuned"

    def test_forced_air_reaches_tuned_at_77_percent(self):
        """Test forced_air reaches 'tuned' at 77% confidence (tier_2 * 1.1)."""
        from custom_components.adaptive_climate.managers.state_attributes import _compute_learning_status

        heating_type = HeatingType.FORCED_AIR
        cycles = 6  # Sufficient cycles

        # Just below tier 2 - should be "stable"
        status_below = _compute_learning_status(
            cycle_count=cycles,
            convergence_confidence=0.76,
            heating_type=heating_type,
            is_paused=False,
        )
        assert status_below == "stable"

        # At tier 2 - should be "tuned"
        status_at = _compute_learning_status(
            cycle_count=cycles,
            convergence_confidence=0.77,
            heating_type=heating_type,
            is_paused=False,
        )
        assert status_at == "tuned"

    def test_insufficient_cycles_prevents_stable_status(self):
        """Test that insufficient cycles prevents 'stable' status even with high confidence."""
        from custom_components.adaptive_climate.managers.state_attributes import _compute_learning_status
        from custom_components.adaptive_climate.const import MIN_CYCLES_FOR_LEARNING

        # High confidence but insufficient cycles
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING - 1,  # One cycle short
            convergence_confidence=0.90,  # High confidence
            heating_type=HeatingType.CONVECTOR,
            is_paused=False,
        )

        assert status == "collecting"  # Still collecting, not stable

    def test_paused_conditions_force_idle_status(self):
        """Test that any pause condition forces 'idle' status regardless of confidence."""
        from custom_components.adaptive_climate.managers.state_attributes import _compute_learning_status

        # High confidence and sufficient cycles, but paused
        status = _compute_learning_status(
            cycle_count=10,
            convergence_confidence=0.90,
            heating_type=HeatingType.RADIATOR,
            is_paused=True,  # Paused due to contact_open, humidity_spike, or learning_grace
        )

        assert status == "idle"  # Forced to idle when paused


class TestNightSetbackLearningGateEdgeCases:
    """Test edge cases and boundary conditions for night setback learning gate."""

    def test_transition_from_collecting_to_tuned_enables_setback(self):
        """Test that transitioning from 'collecting' to 'tuned' enables night setback."""
        # Start with "collecting" status
        learning_status = "collecting"
        get_learning_status = Mock(return_value=learning_status)

        hass = MockHomeAssistant()
        night_setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
        )

        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test_zone",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 19.0,
        )
        manager._calculator._get_learning_status = get_learning_status

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        # First call - should suppress setback
        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target_1, _, info_1 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_1 == 20.0  # No setback
        assert info_1.get("suppressed_reason") == "learning"

        # Transition to "tuned"
        get_learning_status.return_value = "tuned"

        # Second call - should now apply setback
        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target_2, _, info_2 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_2 == 18.0  # Setback applied
        assert "suppressed_reason" not in info_2

    def test_transition_from_stable_to_tuned_enables_setback(self):
        """Test that transitioning from 'stable' to 'tuned' enables night setback."""
        # Start with "stable" status (suppresses setback)
        learning_status = "stable"
        get_learning_status = Mock(return_value=learning_status)

        hass = MockHomeAssistant()
        night_setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
        )

        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test_zone",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 19.0,
        )
        manager._calculator._get_learning_status = get_learning_status

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        # First call - should suppress setback (stable is not tuned)
        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target_1, _, info_1 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_1 == 20.0  # No setback
        assert info_1.get("suppressed_reason") == "learning"

        # Transition to "tuned"
        get_learning_status.return_value = "tuned"

        # Second call - should now apply setback
        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target_2, _, info_2 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_2 == 18.0  # Setback applied
        assert "suppressed_reason" not in info_2

    def test_transition_from_tuned_to_idle_suppresses_setback(self):
        """Test that transitioning from 'tuned' to 'idle' suppresses night setback."""
        # Start with "tuned" status
        learning_status = "tuned"
        get_learning_status = Mock(return_value=learning_status)

        hass = MockHomeAssistant()
        night_setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
        )

        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test_zone",
            night_setback=night_setback,
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 19.0,
        )
        manager._calculator._get_learning_status = get_learning_status

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        # First call - should apply setback
        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target_1, _, info_1 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_1 == 18.0  # Setback applied
        assert "suppressed_reason" not in info_1

        # Transition to "idle" (e.g., window opened)
        get_learning_status.return_value = "idle"

        # Second call - should now suppress setback
        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target_2, _, info_2 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_2 == 20.0  # No setback
        assert info_2.get("suppressed_reason") == "learning"

    def test_night_setback_not_configured_no_suppression(self):
        """Test that when night setback is not configured, no suppression occurs."""
        hass = MockHomeAssistant()

        # Create manager without night setback configuration
        manager = NightSetbackManager(
            hass=hass,
            entity_id="climate.test_zone",
            night_setback=None,  # Not configured
            night_setback_config=None,
            solar_recovery=None,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 19.0,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch('homeassistant.util.dt.utcnow', return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # No night setback configured, so just return target temp
        assert effective_target == 20.0
        assert in_night_period is False
        assert "suppressed_reason" not in info

    def test_multiple_learning_status_values(self):
        """Test all possible learning status values and their effect on night setback."""
        statuses_allow_setback = ["tuned", "optimized"]
        statuses_suppress_setback = ["idle", "collecting", "stable"]

        for status in statuses_allow_setback:
            get_learning_status = Mock(return_value=status)

            hass = MockHomeAssistant()
            night_setback = NightSetback(
                start_time="22:00",
                end_time="06:00",
                setback_delta=2.0,
            )

            manager = NightSetbackManager(
                hass=hass,
                entity_id="climate.test_zone",
                night_setback=night_setback,
                night_setback_config=None,
                solar_recovery=None,
                window_orientation=None,
                get_target_temp=lambda: 20.0,
                get_current_temp=lambda: 19.0,
            )
            manager._calculator._get_learning_status = get_learning_status

            current = datetime(2024, 1, 15, 23, 0)

            with patch('homeassistant.util.dt.utcnow', return_value=current):
                effective_target, _, info = manager.calculate_night_setback_adjustment(current)

            assert effective_target == 18.0, f"Status '{status}' should allow setback"
            assert "suppressed_reason" not in info, f"Status '{status}' should not be suppressed"

        for status in statuses_suppress_setback:
            get_learning_status = Mock(return_value=status)

            hass = MockHomeAssistant()
            night_setback = NightSetback(
                start_time="22:00",
                end_time="06:00",
                setback_delta=2.0,
            )

            manager = NightSetbackManager(
                hass=hass,
                entity_id="climate.test_zone",
                night_setback=night_setback,
                night_setback_config=None,
                solar_recovery=None,
                window_orientation=None,
                get_target_temp=lambda: 20.0,
                get_current_temp=lambda: 19.0,
            )
            manager._calculator._get_learning_status = get_learning_status

            current = datetime(2024, 1, 15, 23, 0)

            with patch('homeassistant.util.dt.utcnow', return_value=current):
                effective_target, _, info = manager.calculate_night_setback_adjustment(current)

            assert effective_target == 20.0, f"Status '{status}' should suppress setback"
            assert info.get("suppressed_reason") == "learning", f"Status '{status}' should have suppressed_reason"
