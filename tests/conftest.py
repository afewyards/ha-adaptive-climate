"""Test configuration and fixtures for adaptive thermostat tests."""

import sys
from unittest.mock import MagicMock

# Mock homeassistant modules before any test imports
mock_ha = MagicMock()
mock_ha.helpers = MagicMock()
mock_ha.helpers.event = MagicMock()
mock_ha.helpers.event.async_call_later = MagicMock(return_value=MagicMock())

sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha.helpers
sys.modules["homeassistant.helpers.event"] = mock_ha.helpers.event
