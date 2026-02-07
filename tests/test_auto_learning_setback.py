"""Tests for auto-learning setback."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from homeassistant.util import dt as dt_util
from custom_components.adaptive_climate.managers.night_setback_manager import (
    NightSetbackManager,
)
from custom_components.adaptive_climate.const import (
    HeatingType,
    AUTO_LEARNING_SETBACK_DELTA,
    AUTO_LEARNING_SETBACK_WINDOW_START,
    AUTO_LEARNING_SETBACK_WINDOW_END,
    AUTO_LEARNING_SETBACK_TRIGGER_DAYS,
    AUTO_LEARNING_SETBACK_COOLDOWN_DAYS,
)


def create_mock_hass():
    """Create a mock Home Assistant instance."""
    hass = Mock()
    hass.data = {}
    return hass


def create_night_setback_manager(
    has_configured_setback=False,
    learning_status="collecting",
    auto_learning_enabled=True,
):
    """Create a NightSetbackManager for testing."""
    hass = create_mock_hass()

    get_target_temp = Mock(return_value=20.0)
    get_current_temp = Mock(return_value=19.5)
    get_learning_status = Mock(return_value=learning_status)
    get_allowed_setback_delta = Mock(return_value=None)

    # Configure night setback if requested
    night_setback = None
    night_setback_config = None
    if has_configured_setback:
        night_setback_config = {
            "start_time": "22:00",
            "end_time": "06:00",
            "delta": 2.0,
        }

    manager = NightSetbackManager(
        hass=hass,
        entity_id="climate.test_zone",
        night_setback=night_setback,
        night_setback_config=night_setback_config,
        solar_recovery=None,
        window_orientation=None,
        get_target_temp=get_target_temp,
        get_current_temp=get_current_temp,
        get_learning_status=get_learning_status,
        get_allowed_setback_delta=get_allowed_setback_delta,
    )

    # Set auto_learning_enabled flag if needed
    if auto_learning_enabled:
        manager._auto_learning_enabled = True
    else:
        manager._auto_learning_enabled = False

    return manager


class TestAutoLearningSetbackTrigger:
    """Test auto-learning setback trigger conditions."""

    def test_triggers_after_7_days_at_cap(self):
        """Auto-setback triggers after 7 days stuck at maintenance cap."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Set days at maintenance cap to 7
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Should trigger
        assert manager.should_apply_auto_learning_setback() is True

    def test_no_trigger_before_7_days(self):
        """No trigger before 7 days."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Set days at maintenance cap to 6
        manager._days_at_maintenance_cap = 6
        manager._last_auto_setback = None

        # Should not trigger
        assert manager.should_apply_auto_learning_setback() is False

    def test_no_trigger_if_night_setback_configured(self):
        """No trigger if user has night setback configured."""
        manager = create_night_setback_manager(
            has_configured_setback=True,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Set days at maintenance cap to 7
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Should not trigger because setback is configured
        assert manager.should_apply_auto_learning_setback() is False

    def test_no_trigger_if_already_tuned(self):
        """No trigger if already at tuned status."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="tuned",
            auto_learning_enabled=True,
        )

        # Set days at maintenance cap to 7
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Should not trigger because already tuned
        assert manager.should_apply_auto_learning_setback() is False

    def test_no_trigger_if_optimized(self):
        """No trigger if already at optimized status."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="optimized",
            auto_learning_enabled=True,
        )

        # Set days at maintenance cap to 7
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Should not trigger because already optimized
        assert manager.should_apply_auto_learning_setback() is False

    def test_no_trigger_if_disabled(self):
        """No trigger if auto-learning is disabled."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=False,
        )

        # Set days at maintenance cap to 7
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Should not trigger because disabled
        assert manager.should_apply_auto_learning_setback() is False


class TestAutoLearningSetbackWindow:
    """Test auto-learning setback time window."""

    @patch("homeassistant.util.dt.utcnow")
    def test_applies_in_3_to_5_am_window(self, mock_utcnow):
        """Setback applies during 3-5am window."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Enable auto-learning setback
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Mock time to 4am
        mock_time = datetime(2024, 1, 15, 4, 0, 0)
        mock_utcnow.return_value = mock_time

        # Calculate setback
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(mock_time)

        # Should apply 0.5°C setback
        assert in_night_period is True
        assert effective_target == 19.5  # 20.0 - 0.5
        assert info.get("night_setback_delta") == 0.5

    @patch("homeassistant.util.dt.utcnow")
    def test_not_active_at_2_am(self, mock_utcnow):
        """No setback at 2am (before window)."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Enable auto-learning setback
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Mock time to 2am
        mock_time = datetime(2024, 1, 15, 2, 0, 0)
        mock_utcnow.return_value = mock_time

        # Calculate setback
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(mock_time)

        # Should not apply setback
        assert in_night_period is False
        assert effective_target == 20.0

    @patch("homeassistant.util.dt.utcnow")
    def test_not_active_at_6_am(self, mock_utcnow):
        """No setback at 6am (after window)."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Enable auto-learning setback
        manager._days_at_maintenance_cap = 7
        manager._last_auto_setback = None

        # Mock time to 6am
        mock_time = datetime(2024, 1, 15, 6, 0, 0)
        mock_utcnow.return_value = mock_time

        # Calculate setback
        effective_target, in_night_period, info = manager.calculate_night_setback_adjustment(mock_time)

        # Should not apply setback
        assert in_night_period is False
        assert effective_target == 20.0


class TestAutoLearningSetbackCooldown:
    """Test auto-learning setback cooldown."""

    def test_cooldown_prevents_repeated_setback(self):
        """Can't trigger again within 7 days."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Set days at maintenance cap to 7
        manager._days_at_maintenance_cap = 7

        # Set last auto setback to 5 days ago
        manager._last_auto_setback = datetime.utcnow() - timedelta(days=5)

        # Should not trigger
        assert manager.should_apply_auto_learning_setback() is False

    def test_triggers_after_cooldown(self):
        """Triggers again after cooldown expires."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Set days at maintenance cap to 7
        manager._days_at_maintenance_cap = 7

        # Set last auto setback to 8 days ago
        manager._last_auto_setback = datetime.utcnow() - timedelta(days=8)

        # Should trigger
        assert manager.should_apply_auto_learning_setback() is True


class TestAutoLearningSetbackDaysTracking:
    """Test days at maintenance cap tracking."""

    def test_update_days_at_cap_increments(self):
        """Updating days at cap increments the counter."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Initialize to 0
        manager._days_at_maintenance_cap = 0

        # Update with at_cap=True
        manager.update_days_at_maintenance_cap(at_cap=True)

        # Should increment
        assert manager._days_at_maintenance_cap == 1

    def test_update_days_at_cap_resets_when_not_at_cap(self):
        """Resets counter when not at cap."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Set to 5 days
        manager._days_at_maintenance_cap = 5

        # Update with at_cap=False
        manager.update_days_at_maintenance_cap(at_cap=False)

        # Should reset to 0
        assert manager._days_at_maintenance_cap == 0

    def test_multiple_updates_accumulate(self):
        """Multiple updates accumulate correctly."""
        manager = create_night_setback_manager(
            has_configured_setback=False,
            learning_status="stable",
            auto_learning_enabled=True,
        )

        # Initialize to 0
        manager._days_at_maintenance_cap = 0

        # Update 7 times
        for _ in range(7):
            manager.update_days_at_maintenance_cap(at_cap=True)

        # Should be at 7
        assert manager._days_at_maintenance_cap == 7
