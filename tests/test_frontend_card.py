"""Tests for serving and registering the learning card."""

from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.adaptive_climate import frontend_card
from custom_components.adaptive_climate.frontend_card import CARD_URL, async_register_card, card_module_url


def _hass(components=("frontend",)):
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *a: f(*a))
    hass.http.async_register_static_paths = AsyncMock()
    hass.config.components = set(components)
    return hass


@pytest.mark.asyncio
async def test_registers_static_path_and_module_url():
    hass = _hass()
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    hass.http.async_register_static_paths.assert_awaited_once()
    add.assert_called_once()
    assert add.call_args.args[0] is hass
    assert add.call_args.args[1].startswith(CARD_URL + "?v=")


@pytest.mark.asyncio
async def test_without_frontend_only_serves_file():
    hass = _hass(components=())
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    hass.http.async_register_static_paths.assert_awaited_once()
    add.assert_not_called()


@pytest.mark.asyncio
async def test_missing_file_logs_and_returns(monkeypatch, caplog):
    hass = _hass()
    monkeypatch.setattr(frontend_card, "_CARD_PATH", Path("/nonexistent/missing.js"))
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    hass.http.async_register_static_paths.assert_not_awaited()
    add.assert_not_called()
    assert "learning card" in caplog.text


@pytest.mark.asyncio
async def test_no_http_component_returns():
    hass = _hass()
    hass.http = None
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    add.assert_not_called()


def test_module_url_changes_with_content():
    assert card_module_url(b"one") != card_module_url(b"two")
    assert card_module_url(b"one") == card_module_url(b"one")
