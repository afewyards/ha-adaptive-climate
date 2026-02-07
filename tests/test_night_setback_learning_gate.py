"""Tests for graduated night setback learning gate callback interface.

Tests verify the new callback interface that returns allowed_delta (float | None)
instead of learning status string. This allows graduated setback based on both
learning status AND cycle count.

IMPORTANT: These tests follow TDD approach and will FAIL until implementation is complete.

The graduated setback progression:
- idle: 0°C (no setback allowed)
- collecting + cycles < 3: 0°C (insufficient data)
- collecting + cycles >= 3: 0.5°C (half degree allowed)
- stable: 1.0°C (one degree allowed)
- tuned/optimized: None (unlimited/full setback allowed)

Interface expectation:
    def get_allowed_setback_delta() -> float | None:
        '''Returns max allowed setback delta, or None for unlimited.'''
        ...
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch
from custom_components.adaptive_climate.managers.night_setback_manager import NightSetbackManager
from custom_components.adaptive_climate.adaptive.night_setback import NightSetback


class MockHomeAssistant:
    """Mock Home Assistant instance for testing."""

    def __init__(self):
        self.data = {}


class TestGraduatedSetbackCallbackInterface:
    """Test the new callback interface that returns allowed_delta instead of boolean."""

    def _create_manager(
        self,
        hass=None,
        night_setback_delta=2.0,
        get_allowed_setback_delta=None,
    ):
        """Create a NightSetbackManager for testing.

        Args:
            hass: Optional Home Assistant instance
            night_setback_delta: Temperature setback delta
            get_allowed_setback_delta: Optional callback that returns allowed delta

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
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        return manager

    def test_callback_returns_zero_when_idle(self):
        """Test callback returns allowed_delta = 0 when status is 'idle'."""
        # Mock callback that returns 0 (idle status)
        get_allowed_setback_delta = Mock(return_value=0.0)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period (23:00)
        current = datetime(2024, 1, 15, 23, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should return original temperature without adjustment
        assert effective_target == 20.0  # No setback applied
        assert in_night_period is True  # Still in night period
        assert info.get("suppressed_reason") == "learning"  # Suppressed due to learning

    def test_callback_returns_zero_when_collecting_few_cycles(self):
        """Test callback returns allowed_delta = 0 when status is 'collecting' with < 3 cycles."""
        # Mock callback that returns 0 (collecting with insufficient cycles)
        get_allowed_setback_delta = Mock(return_value=0.0)

        manager = self._create_manager(
            night_setback_delta=2.5,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period (02:00)
        current = datetime(2024, 1, 15, 2, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should return original temperature without adjustment
        assert effective_target == 20.0  # No setback applied
        assert in_night_period is True  # Still in night period
        assert info.get("suppressed_reason") == "learning"  # Suppressed due to learning

    def test_callback_returns_half_degree_when_collecting_enough_cycles(self):
        """Test callback returns allowed_delta = 0.5 when status is 'collecting' with >= 3 cycles."""
        # Mock callback that returns 0.5 (collecting with sufficient cycles)
        get_allowed_setback_delta = Mock(return_value=0.5)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period (23:00)
        current = datetime(2024, 1, 15, 23, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply 0.5°C setback (capped by allowed_delta)
        assert effective_target == 19.5  # 20.0 - 0.5
        assert in_night_period is True
        assert info.get("suppressed_reason") == "limited"  # Capped by learning gate
        assert info.get("night_setback_delta") == 0.5
        assert info.get("effective_delta") == 0.5  # Partial setback applied

    def test_callback_returns_one_degree_when_stable(self):
        """Test callback returns allowed_delta = 1.0 when status is 'stable'."""
        # Mock callback that returns 1.0 (stable status)
        get_allowed_setback_delta = Mock(return_value=1.0)

        manager = self._create_manager(
            night_setback_delta=2.5,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period (01:00)
        current = datetime(2024, 1, 15, 1, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply 1.0°C setback (capped by allowed_delta)
        assert effective_target == 19.0  # 20.0 - 1.0
        assert in_night_period is True
        assert info.get("suppressed_reason") == "limited"  # Capped by learning gate
        assert info.get("night_setback_delta") == 1.0
        assert info.get("effective_delta") == 1.0  # Partial setback applied

    def test_callback_returns_full_when_tuned(self):
        """Test callback returns allowed_delta = None when status is 'tuned'."""
        # Mock callback that returns None (tuned status - unlimited)
        get_allowed_setback_delta = Mock(return_value=None)

        manager = self._create_manager(
            night_setback_delta=3.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period (04:00)
        current = datetime(2024, 1, 15, 4, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply the full setback delta
        assert effective_target == 17.0  # 20.0 - 3.0 (full setback)
        assert in_night_period is True
        assert "suppressed_reason" not in info  # Not suppressed
        assert info.get("night_setback_delta") == 3.0
        assert info.get("effective_delta") == 3.0  # Full setback applied

    def test_callback_returns_full_when_optimized(self):
        """Test callback returns allowed_delta = None when status is 'optimized'."""
        # Mock callback that returns None (optimized status - unlimited)
        get_allowed_setback_delta = Mock(return_value=None)

        manager = self._create_manager(
            night_setback_delta=2.5,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period (05:00)
        current = datetime(2024, 1, 15, 5, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply the full setback delta
        assert effective_target == 17.5  # 20.0 - 2.5 (full setback)
        assert in_night_period is True
        assert "suppressed_reason" not in info  # Not suppressed
        assert info.get("night_setback_delta") == 2.5
        assert info.get("effective_delta") == 2.5  # Full setback applied

    def test_callback_capping_when_configured_delta_smaller(self):
        """Test that even with allowed_delta > configured delta, only configured delta is applied."""
        # Mock callback that returns 3.0 (more than configured)
        get_allowed_setback_delta = Mock(return_value=3.0)

        manager = self._create_manager(
            night_setback_delta=1.5,  # Smaller configured delta
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period (23:00)
        current = datetime(2024, 1, 15, 23, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply only the configured delta (capped by config)
        assert effective_target == 18.5  # 20.0 - 1.5 (config limit)
        assert in_night_period is True
        assert "suppressed_reason" not in info  # Not capped - allowed_delta >= configured_delta
        assert info.get("night_setback_delta") == 1.5
        assert info.get("effective_delta") == 1.5  # Capped by config

    def test_transition_from_zero_to_half_degree(self):
        """Test transitioning from 0°C to 0.5°C allowed delta."""
        # Start with 0.0 (insufficient cycles)
        allowed_delta = 0.0
        get_allowed_setback_delta = Mock(return_value=allowed_delta)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        # First call - should suppress setback
        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target_1, _, info_1 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_1 == 20.0  # No setback
        assert info_1.get("suppressed_reason") == "learning"

        # Transition to 0.5
        get_allowed_setback_delta.return_value = 0.5

        # Second call - should now apply 0.5°C setback
        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target_2, _, info_2 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_2 == 19.5  # 0.5°C setback
        assert info_2.get("suppressed_reason") == "limited"  # Capped by learning gate
        assert info_2.get("night_setback_delta") == 0.5
        assert info_2.get("effective_delta") == 0.5

    def test_transition_from_half_degree_to_full(self):
        """Test transitioning from 0.5°C to full (None) allowed delta."""
        # Start with 0.5 (collecting with cycles)
        allowed_delta = 0.5
        get_allowed_setback_delta = Mock(return_value=allowed_delta)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        # First call - should apply 0.5°C setback
        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target_1, _, info_1 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_1 == 19.5  # 0.5°C setback
        assert info_1.get("night_setback_delta") == 0.5
        assert info_1.get("effective_delta") == 0.5
        assert info_1.get("suppressed_reason") == "limited"  # Capped

        # Transition to None (tuned)
        get_allowed_setback_delta.return_value = None

        # Second call - should now apply full setback
        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target_2, _, info_2 = manager.calculate_night_setback_adjustment(current)

        assert effective_target_2 == 18.0  # 2.0°C setback (full)
        assert "suppressed_reason" not in info_2
        assert info_2.get("night_setback_delta") == 2.0
        assert info_2.get("effective_delta") == 2.0

    def test_no_callback_defaults_to_full_setback(self):
        """Test that without a callback, full setback is allowed (backward compat)."""
        # Create manager without callback
        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=None,  # No callback
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply full setback (backward compatibility)
        assert effective_target == 18.0  # 20.0 - 2.0
        assert in_night_period is True
        assert "suppressed_reason" not in info

    def test_during_day_not_applied_regardless_of_allowed_delta(self):
        """Test that setback is not applied during day regardless of allowed delta."""
        # Mock callback returning 0.5
        get_allowed_setback_delta = Mock(return_value=0.5)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During day (10:00)
        current = datetime(2024, 1, 15, 10, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Not in night period, so no setback
        assert effective_target == 20.0
        assert in_night_period is False
        assert "suppressed_reason" not in info


class TestGraduatedSetbackEdgeCases:
    """Test edge cases for graduated setback."""

    def _create_manager(
        self,
        hass=None,
        night_setback_delta=2.0,
        get_allowed_setback_delta=None,
    ):
        """Create a NightSetbackManager for testing."""
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
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        return manager

    def test_very_small_allowed_delta(self):
        """Test that very small allowed delta (e.g., 0.1°C) is respected."""
        # Mock callback that returns 0.1
        get_allowed_setback_delta = Mock(return_value=0.1)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply only 0.1°C setback
        assert effective_target == 19.9  # 20.0 - 0.1
        assert in_night_period is True
        assert info.get("night_setback_delta") == 0.1
        assert info.get("effective_delta") == 0.1
        assert info.get("suppressed_reason") == "limited"  # Capped by learning gate

    def test_exact_match_allowed_delta_equals_config(self):
        """Test when allowed delta exactly matches configured delta."""
        # Mock callback that returns exact match
        get_allowed_setback_delta = Mock(return_value=2.0)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Should apply full 2.0°C setback
        assert effective_target == 18.0  # 20.0 - 2.0
        assert in_night_period is True
        assert info.get("night_setback_delta") == 2.0
        assert info.get("effective_delta") == 2.0
        assert "suppressed_reason" not in info  # Not capped - allowed_delta >= configured_delta

    def test_negative_allowed_delta_treated_as_unlimited(self):
        """Test that negative allowed delta falls through to unlimited (full setback)."""
        # Mock callback that returns negative (invalid - treated as None)
        get_allowed_setback_delta = Mock(return_value=-0.5)

        manager = self._create_manager(
            night_setback_delta=2.0,
            get_allowed_setback_delta=get_allowed_setback_delta,
        )

        # During night period
        current = datetime(2024, 1, 15, 23, 0)

        with patch("homeassistant.util.dt.utcnow", return_value=current):
            effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(current)

        # Negative falls through to None case (unlimited) - applies full setback
        assert effective_target == 18.0  # Full setback: 20.0 - 2.0
        assert in_night_period is True
        assert "suppressed_reason" not in info  # Not suppressed
        assert info.get("night_setback_delta") == 2.0
        assert info.get("effective_delta") == 2.0
