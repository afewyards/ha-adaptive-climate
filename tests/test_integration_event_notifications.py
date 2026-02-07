"""Integration tests for event-driven notifications."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from custom_components.adaptive_climate.managers.notification_manager import (
    NotificationManager,
)
from custom_components.adaptive_climate.managers.learning_milestone import (
    LearningMilestoneTracker,
)
from custom_components.adaptive_climate.managers.comfort_degradation import (
    ComfortDegradationDetector,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def notification_manager(mock_hass):
    return NotificationManager(
        hass=mock_hass,
        notify_service="mobile_app_phone",
        persistent_notification=True,
    )


@pytest.mark.asyncio
async def test_learning_milestone_fires_notification(notification_manager, mock_hass):
    """Learning tier change triggers notification through full stack."""
    tracker = LearningMilestoneTracker(
        zone_id="living_room",
        zone_name="Living Room",
        notification_manager=notification_manager,
    )

    # Initialize with collecting
    await tracker.async_check_milestone("collecting", 25)
    mock_hass.services.async_call.reset_mock()

    # Upgrade to stable
    result = await tracker.async_check_milestone("stable", 40)
    assert result is True
    assert mock_hass.services.async_call.call_count == 2  # iOS + persistent

    # Verify notification content
    ios_call = mock_hass.services.async_call.call_args_list[0]
    assert "Living Room" in ios_call[0][2]["message"]
    assert "stable" in ios_call[0][2]["message"]

    persistent_call = mock_hass.services.async_call.call_args_list[1]
    assert "convergence" in persistent_call[0][2]["message"].lower()


@pytest.mark.asyncio
async def test_comfort_drop_fires_notification(notification_manager, mock_hass):
    """Comfort degradation triggers notification through full stack."""
    detector = ComfortDegradationDetector(
        zone_id="office",
        zone_name="Office",
    )

    # Build up 24h average at ~82
    for _ in range(50):
        detector.record_score(82.0)

    # Drop to 60
    triggered = detector.check_degradation(60.0)
    assert triggered is True

    # Verify context
    ctx = detector.build_context(contact_pauses=3, humidity_pauses=0)
    assert "3 contact" in ctx

    # Send via notification manager
    avg = detector.rolling_average
    result = await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort Alert",
        ios_message=f"Office comfort dropped to 60% (avg was {avg:.0f}%)",
        persistent_message=f"Office comfort dropped to 60% (avg was {avg:.0f}%). {ctx}",
        cooldown_hours=24,
    )
    assert result is True


@pytest.mark.asyncio
async def test_comfort_and_learning_independent(notification_manager, mock_hass):
    """Both notification types can fire for same zone without interfering."""
    tracker = LearningMilestoneTracker(
        zone_id="office", zone_name="Office",
        notification_manager=notification_manager,
    )
    detector = ComfortDegradationDetector(
        zone_id="office", zone_name="Office",
    )

    # Learning milestone
    await tracker.async_check_milestone("collecting", 20)
    result1 = await tracker.async_check_milestone("stable", 40)
    assert result1 is True

    # Comfort degradation (different notification_id)
    for _ in range(50):
        detector.record_score(85.0)
    triggered = detector.check_degradation(60.0)
    assert triggered is True

    result2 = await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort Alert",
        ios_message="Office comfort dropped",
        cooldown_hours=24,
    )
    assert result2 is True

    # Both sent (2 calls from milestone, 2 from comfort = 4)
    assert mock_hass.services.async_call.call_count == 4


@pytest.mark.asyncio
async def test_cooldown_across_events(notification_manager, mock_hass):
    """Comfort alert cooldown doesn't affect learning milestone."""
    # Send comfort alert with cooldown
    await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort", ios_message="M", cooldown_hours=24,
    )

    # Comfort alert again — should be blocked
    result = await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort", ios_message="M", cooldown_hours=24,
    )
    assert result is False

    # Learning milestone — different ID, should work
    result = await notification_manager.async_send(
        notification_id="learning_milestone_office",
        title="Learning", ios_message="M",
    )
    assert result is True


@pytest.mark.asyncio
async def test_learning_milestone_upgrade_path(notification_manager, mock_hass):
    """Learning status progresses through tiers and fires notifications."""
    tracker = LearningMilestoneTracker(
        zone_id="bedroom",
        zone_name="Bedroom",
        notification_manager=notification_manager,
    )

    # Start at collecting
    result = await tracker.async_check_milestone("collecting", 15)
    assert result is False  # No notification on first check
    mock_hass.services.async_call.reset_mock()

    # Upgrade to stable
    result = await tracker.async_check_milestone("stable", 40)
    assert result is True
    assert mock_hass.services.async_call.call_count == 2
    mock_hass.services.async_call.reset_mock()

    # Upgrade to tuned
    result = await tracker.async_check_milestone("tuned", 65)
    assert result is True
    assert mock_hass.services.async_call.call_count == 2
    mock_hass.services.async_call.reset_mock()

    # Upgrade to optimized
    result = await tracker.async_check_milestone("optimized", 95)
    assert result is True
    assert mock_hass.services.async_call.call_count == 2


@pytest.mark.asyncio
async def test_learning_milestone_downgrade(notification_manager, mock_hass):
    """Learning status downgrade also triggers notification."""
    tracker = LearningMilestoneTracker(
        zone_id="kitchen",
        zone_name="Kitchen",
        notification_manager=notification_manager,
    )

    # Start at tuned
    await tracker.async_check_milestone("tuned", 65)
    mock_hass.services.async_call.reset_mock()

    # Downgrade to collecting (e.g., after rollback)
    result = await tracker.async_check_milestone("collecting", 20)
    assert result is True
    assert mock_hass.services.async_call.call_count == 2

    # Verify message contains "dropped to"
    ios_call = mock_hass.services.async_call.call_args_list[0]
    assert "dropped to" in ios_call[0][2]["message"]
    assert "collecting" in ios_call[0][2]["message"]


@pytest.mark.asyncio
async def test_learning_milestone_no_notification_for_idle(notification_manager, mock_hass):
    """Idle transitions don't trigger notifications."""
    tracker = LearningMilestoneTracker(
        zone_id="bathroom",
        zone_name="Bathroom",
        notification_manager=notification_manager,
    )

    # Start at collecting
    await tracker.async_check_milestone("collecting", 25)
    mock_hass.services.async_call.reset_mock()

    # Transition to idle
    result = await tracker.async_check_milestone("idle", 0)
    assert result is False
    assert mock_hass.services.async_call.call_count == 0

    # Transition from idle to collecting
    result = await tracker.async_check_milestone("collecting", 10)
    assert result is False
    assert mock_hass.services.async_call.call_count == 0


@pytest.mark.asyncio
async def test_comfort_degradation_absolute_threshold(notification_manager, mock_hass):
    """Comfort detector triggers on absolute threshold regardless of average."""
    detector = ComfortDegradationDetector(
        zone_id="living_room",
        zone_name="Living Room",
    )

    # Build up average at 75
    for _ in range(50):
        detector.record_score(75.0)

    # Drop to 64 (< 65 threshold)
    triggered = detector.check_degradation(64.0)
    assert triggered is True


@pytest.mark.asyncio
async def test_comfort_degradation_relative_drop(notification_manager, mock_hass):
    """Comfort detector triggers on significant drop from rolling average."""
    detector = ComfortDegradationDetector(
        zone_id="office",
        zone_name="Office",
    )

    # Build up average at 85
    for _ in range(50):
        detector.record_score(85.0)

    # Drop to 69 (85 - 16 = 69, exceeds 15 point threshold)
    triggered = detector.check_degradation(69.0)
    assert triggered is True

    # 70 should trigger (85 - 70 = 15, at threshold, >= comparison)
    detector2 = ComfortDegradationDetector(
        zone_id="office2",
        zone_name="Office 2",
    )
    for _ in range(50):
        detector2.record_score(85.0)
    triggered2 = detector2.check_degradation(70.0)
    assert triggered2 is True

    # 71 should not trigger (85 - 71 = 14, below threshold)
    detector3 = ComfortDegradationDetector(
        zone_id="office3",
        zone_name="Office 3",
    )
    for _ in range(50):
        detector3.record_score(85.0)
    triggered3 = detector3.check_degradation(71.0)
    assert triggered3 is False


@pytest.mark.asyncio
async def test_comfort_degradation_insufficient_data(notification_manager, mock_hass):
    """Comfort detector doesn't trigger with insufficient data."""
    detector = ComfortDegradationDetector(
        zone_id="bedroom",
        zone_name="Bedroom",
    )

    # Only 5 samples (need 12)
    for _ in range(5):
        detector.record_score(85.0)

    # Even a big drop shouldn't trigger
    triggered = detector.check_degradation(50.0)
    assert triggered is False

    # Rolling average should be None
    assert detector.rolling_average is None


@pytest.mark.asyncio
async def test_comfort_degradation_context_building(notification_manager, mock_hass):
    """Comfort detector builds useful context strings."""
    detector = ComfortDegradationDetector(
        zone_id="kitchen",
        zone_name="Kitchen",
    )

    # No pauses
    ctx = detector.build_context(contact_pauses=0, humidity_pauses=0)
    assert ctx == ""

    # Only contact pauses
    ctx = detector.build_context(contact_pauses=3, humidity_pauses=0)
    assert "3 contact" in ctx
    assert "pauses" in ctx

    # Only humidity pauses
    ctx = detector.build_context(contact_pauses=0, humidity_pauses=5)
    assert "5 humidity" in ctx
    assert "pauses" in ctx

    # Both types
    ctx = detector.build_context(contact_pauses=2, humidity_pauses=4)
    assert "2 contact" in ctx
    assert "4 humidity" in ctx
    assert "·" in ctx  # Separator


@pytest.mark.asyncio
async def test_notification_manager_fallback_message(notification_manager, mock_hass):
    """Persistent notification falls back to iOS message if not provided."""
    result = await notification_manager.async_send(
        notification_id="test_notification",
        title="Test",
        ios_message="Short message",
    )
    assert result is True

    # Both calls should use the same message
    ios_call = mock_hass.services.async_call.call_args_list[0]
    persistent_call = mock_hass.services.async_call.call_args_list[1]

    assert ios_call[0][2]["message"] == "Short message"
    assert persistent_call[0][2]["message"] == "Short message"


@pytest.mark.asyncio
async def test_notification_manager_separate_messages(notification_manager, mock_hass):
    """Persistent notification can have different message than iOS."""
    result = await notification_manager.async_send(
        notification_id="test_notification",
        title="Test",
        ios_message="Short",
        persistent_message="Long detailed message with markdown",
    )
    assert result is True

    ios_call = mock_hass.services.async_call.call_args_list[0]
    persistent_call = mock_hass.services.async_call.call_args_list[1]

    assert ios_call[0][2]["message"] == "Short"
    assert persistent_call[0][2]["message"] == "Long detailed message with markdown"
