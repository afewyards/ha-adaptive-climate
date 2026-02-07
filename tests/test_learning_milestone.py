"""Tests for learning milestone notifications."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.adaptive_climate.managers.learning_milestone import (
    LearningMilestoneTracker,
    TIER_CONTEXT,
)


@pytest.fixture
def mock_notification_manager():
    mgr = MagicMock()
    mgr.async_send = AsyncMock(return_value=True)
    return mgr


@pytest.fixture
def tracker(mock_notification_manager):
    return LearningMilestoneTracker(
        zone_id="living_room",
        zone_name="Living Room",
        notification_manager=mock_notification_manager,
    )


@pytest.mark.asyncio
async def test_notification_on_tier_upgrade(tracker, mock_notification_manager):
    """Collecting -> stable fires notification."""
    # Initialize
    await tracker.async_check_milestone("collecting", 25)
    mock_notification_manager.async_send.reset_mock()

    result = await tracker.async_check_milestone("stable", 40)
    assert result is True
    mock_notification_manager.async_send.assert_called_once()
    call_kwargs = mock_notification_manager.async_send.call_args[1]
    assert "stable" in call_kwargs["ios_message"].lower()
    assert "Living Room" in call_kwargs["ios_message"]
    assert "40%" in call_kwargs["ios_message"]


@pytest.mark.asyncio
async def test_notification_on_tier_downgrade(tracker, mock_notification_manager):
    """Rollback from tuned -> collecting fires notification."""
    await tracker.async_check_milestone("tuned", 70)
    mock_notification_manager.async_send.reset_mock()

    result = await tracker.async_check_milestone("collecting", 25)
    assert result is True
    call_kwargs = mock_notification_manager.async_send.call_args[1]
    assert "collecting" in call_kwargs["ios_message"].lower()


@pytest.mark.asyncio
async def test_no_notification_same_tier(tracker, mock_notification_manager):
    """Confidence change within same tier is silent."""
    await tracker.async_check_milestone("stable", 40)
    mock_notification_manager.async_send.reset_mock()

    result = await tracker.async_check_milestone("stable", 45)
    assert result is False
    mock_notification_manager.async_send.assert_not_called()


def test_context_string_per_tier():
    """Correct context string for each tier."""
    assert "convergence" in TIER_CONTEXT["stable"].lower()
    assert "night setback" in TIER_CONTEXT["tuned"].lower() or "auto-apply" in TIER_CONTEXT["tuned"].lower()
    assert "best performance" in TIER_CONTEXT["optimized"].lower() or "high confidence" in TIER_CONTEXT["optimized"].lower()
    assert "collecting" in TIER_CONTEXT


@pytest.mark.asyncio
async def test_initial_status_none(tracker, mock_notification_manager):
    """First check with idle status doesn't fire."""
    result = await tracker.async_check_milestone("idle", 0)
    assert result is False
    mock_notification_manager.async_send.assert_not_called()


@pytest.mark.asyncio
async def test_idle_transitions_silent(tracker, mock_notification_manager):
    """Transitions to/from idle (pause/unpause) are silent."""
    await tracker.async_check_milestone("stable", 40)
    mock_notification_manager.async_send.reset_mock()

    # stable -> idle (pause)
    result = await tracker.async_check_milestone("idle", 40)
    assert result is False

    # idle -> stable (unpause)
    result = await tracker.async_check_milestone("stable", 40)
    assert result is False


@pytest.mark.asyncio
async def test_upgrade_verb(tracker, mock_notification_manager):
    """Upgrade uses 'reached' verb."""
    await tracker.async_check_milestone("collecting", 25)
    mock_notification_manager.async_send.reset_mock()

    await tracker.async_check_milestone("stable", 40)
    call_kwargs = mock_notification_manager.async_send.call_args[1]
    assert "reached" in call_kwargs["ios_message"]


@pytest.mark.asyncio
async def test_downgrade_verb(tracker, mock_notification_manager):
    """Downgrade uses 'dropped to' verb."""
    await tracker.async_check_milestone("tuned", 70)
    mock_notification_manager.async_send.reset_mock()

    await tracker.async_check_milestone("collecting", 25)
    call_kwargs = mock_notification_manager.async_send.call_args[1]
    assert "dropped to" in call_kwargs["ios_message"]


@pytest.mark.asyncio
async def test_persistent_includes_context(tracker, mock_notification_manager):
    """Persistent message includes tier context."""
    await tracker.async_check_milestone("collecting", 25)
    mock_notification_manager.async_send.reset_mock()

    await tracker.async_check_milestone("tuned", 70)
    call_kwargs = mock_notification_manager.async_send.call_args[1]
    persistent = call_kwargs["persistent_message"]
    assert "night setback" in persistent.lower() or "auto-apply" in persistent.lower()


@pytest.mark.asyncio
async def test_no_notification_manager(mock_notification_manager):
    """Gracefully handles missing notification manager."""
    tracker = LearningMilestoneTracker(
        zone_id="test", zone_name="Test", notification_manager=None,
    )
    await tracker.async_check_milestone("collecting", 25)
    result = await tracker.async_check_milestone("stable", 40)
    assert result is False
