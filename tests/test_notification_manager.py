"""Tests for NotificationManager."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest

from custom_components.adaptive_climate.managers.notification_manager import (
    NotificationManager,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def manager(mock_hass):
    return NotificationManager(
        hass=mock_hass,
        notify_service="mobile_app_phone",
        persistent_notification=True,
    )


@pytest.fixture
def manager_no_persistent(mock_hass):
    return NotificationManager(
        hass=mock_hass,
        notify_service="mobile_app_phone",
        persistent_notification=False,
    )


@pytest.mark.asyncio
async def test_send_ios_and_persistent(manager, mock_hass):
    """Both iOS and persistent notification dispatched."""
    result = await manager.async_send(
        notification_id="test_1",
        title="Test Title",
        ios_message="Short message",
        persistent_message="Detailed message",
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 2
    ios_call = mock_hass.services.async_call.call_args_list[0]
    assert ios_call[0][0] == "notify"
    assert ios_call[0][1] == "mobile_app_phone"
    assert ios_call[0][2]["title"] == "Test Title"
    assert ios_call[0][2]["message"] == "Short message"
    persistent_call = mock_hass.services.async_call.call_args_list[1]
    assert persistent_call[0][0] == "persistent_notification"
    assert persistent_call[0][2]["message"] == "Detailed message"


@pytest.mark.asyncio
async def test_send_ios_only(manager_no_persistent, mock_hass):
    """Only iOS notification when persistent_notification=False."""
    result = await manager_no_persistent.async_send(
        notification_id="test_1",
        title="Test Title",
        ios_message="Short message",
        persistent_message="Detailed message",
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 1
    ios_call = mock_hass.services.async_call.call_args_list[0]
    assert ios_call[0][0] == "notify"


@pytest.mark.asyncio
async def test_persistent_uses_ios_message_as_fallback(manager, mock_hass):
    """When no persistent_message given, uses ios_message for persistent too."""
    await manager.async_send(
        notification_id="test_1",
        title="Test Title",
        ios_message="Short message",
    )
    assert mock_hass.services.async_call.call_count == 2
    persistent_call = mock_hass.services.async_call.call_args_list[1]
    assert persistent_call[0][2]["message"] == "Short message"


@pytest.mark.asyncio
async def test_cooldown_suppresses(manager, mock_hass):
    """Second call within cooldown returns False."""
    await manager.async_send(
        notification_id="cd_test", title="T", ios_message="M", cooldown_hours=1.0,
    )
    assert mock_hass.services.async_call.call_count == 2
    result = await manager.async_send(
        notification_id="cd_test", title="T", ios_message="M", cooldown_hours=1.0,
    )
    assert result is False
    assert mock_hass.services.async_call.call_count == 2


@pytest.mark.asyncio
async def test_cooldown_expires(manager, mock_hass):
    """Call after cooldown succeeds."""
    await manager.async_send(
        notification_id="cd_test", title="T", ios_message="M", cooldown_hours=1.0,
    )
    manager._cooldowns["cd_test"] = datetime.now() - timedelta(hours=2)
    result = await manager.async_send(
        notification_id="cd_test", title="T", ios_message="M", cooldown_hours=1.0,
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 4


@pytest.mark.asyncio
async def test_different_ids_independent(manager, mock_hass):
    """Cooldowns are per notification_id."""
    await manager.async_send(
        notification_id="id_a", title="T", ios_message="M", cooldown_hours=1.0,
    )
    result = await manager.async_send(
        notification_id="id_b", title="T", ios_message="M", cooldown_hours=1.0,
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 4


@pytest.mark.asyncio
async def test_no_notify_service(mock_hass):
    """Gracefully handles missing notify service."""
    manager = NotificationManager(
        hass=mock_hass, notify_service=None, persistent_notification=True,
    )
    result = await manager.async_send(
        notification_id="test", title="T", ios_message="M",
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 1  # only persistent


@pytest.mark.asyncio
async def test_zero_cooldown_never_suppresses(manager, mock_hass):
    """Zero cooldown (default) never suppresses."""
    for _ in range(3):
        result = await manager.async_send(
            notification_id="test", title="T", ios_message="M", cooldown_hours=0,
        )
        assert result is True
    assert mock_hass.services.async_call.call_count == 6
