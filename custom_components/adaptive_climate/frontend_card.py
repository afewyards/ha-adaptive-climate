"""Serve the learning card and add it to the Home Assistant frontend."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/{DOMAIN}/adaptive-climate-learning-card.js"
_CARD_PATH = Path(__file__).parent / "frontend" / "adaptive-climate-learning-card.js"


def card_module_url(content: bytes) -> str:
    # Boxes updated by copying files keep the manifest version, so the file hash busts the browser cache.
    return f"{CARD_URL}?v={hashlib.sha1(content).hexdigest()[:8]}"


async def async_register_card(hass: HomeAssistant) -> None:
    if getattr(hass, "http", None) is None:
        return
    try:
        content = await hass.async_add_executor_job(_CARD_PATH.read_bytes)
    except OSError as err:
        _LOGGER.warning("learning card not registered, %s unreadable: %s", _CARD_PATH.name, err)
        return
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(_CARD_PATH))])
    if "frontend" in hass.config.components:
        add_extra_js_url(hass, card_module_url(content))
