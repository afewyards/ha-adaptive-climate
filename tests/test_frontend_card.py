"""Tests for serving and registering the cards."""

from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.adaptive_climate import frontend_card
from custom_components.adaptive_climate.frontend_card import CARD_FILES, async_register_card, card_module_url, card_url


def _hass(components=("frontend",)):
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *a: f(*a))
    hass.http.async_register_static_paths = AsyncMock()
    hass.config.components = set(components)
    return hass


@pytest.mark.asyncio
async def test_registers_both_cards():
    hass = _hass()
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    assert hass.http.async_register_static_paths.await_count == 2
    assert add.call_count == 2
    for call, filename in zip(add.call_args_list, CARD_FILES):
        assert call.args[0] is hass
        assert call.args[1].startswith(card_url(filename) + "?v=")


@pytest.mark.asyncio
async def test_without_frontend_only_serves_files():
    hass = _hass(components=())
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    assert hass.http.async_register_static_paths.await_count == 2
    add.assert_not_called()


@pytest.mark.asyncio
async def test_missing_files_log_and_skip(monkeypatch, caplog):
    hass = _hass()
    monkeypatch.setattr(frontend_card, "_FRONTEND_DIR", Path("/nonexistent"))
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    hass.http.async_register_static_paths.assert_not_awaited()
    add.assert_not_called()
    assert all(f in caplog.text for f in CARD_FILES)


@pytest.mark.asyncio
async def test_one_unreadable_file_still_registers_the_other(monkeypatch, tmp_path, caplog):
    (tmp_path / CARD_FILES[1]).write_text("x")
    monkeypatch.setattr(frontend_card, "_FRONTEND_DIR", tmp_path)
    hass = _hass()
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    hass.http.async_register_static_paths.assert_awaited_once()
    add.assert_called_once()
    assert add.call_args.args[1].startswith(card_url(CARD_FILES[1]))
    assert CARD_FILES[0] in caplog.text


@pytest.mark.asyncio
async def test_no_http_component_returns():
    hass = _hass()
    hass.http = None
    with patch.object(frontend_card, "add_extra_js_url") as add:
        await async_register_card(hass)
    add.assert_not_called()


def test_module_url_changes_with_content():
    f = CARD_FILES[0]
    assert card_module_url(f, b"one") != card_module_url(f, b"two")
    assert card_module_url(f, b"one") == card_module_url(f, b"one")
