"""Serve the cards and add them to the Home Assistant frontend."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILES = ("adaptive-climate-learning-card.js", "adaptive-climate-control-card.js")
_FRONTEND_DIR = Path(__file__).parent / "frontend"


def card_url(filename: str) -> str:
    return f"/{DOMAIN}/{filename}"


def card_module_url(filename: str, content: bytes) -> str:
    # Boxes updated by copying files keep the manifest version, so the file hash busts the browser cache.
    return f"{card_url(filename)}?v={hashlib.sha1(content).hexdigest()[:8]}"


async def async_register_card(hass: HomeAssistant) -> None:
    if getattr(hass, "http", None) is None:
        return
    for filename in CARD_FILES:
        path = _FRONTEND_DIR / filename
        try:
            content = await hass.async_add_executor_job(path.read_bytes)
        except OSError as err:
            _LOGGER.warning("%s not registered, unreadable: %s", filename, err)
            continue
        await hass.http.async_register_static_paths([StaticPathConfig(card_url(filename), str(path))])
        if "frontend" in hass.config.components:
            add_extra_js_url(hass, card_module_url(filename, content))
