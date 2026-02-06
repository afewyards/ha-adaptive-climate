"""Tests for shared test fixtures in conftest.py."""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock


class TestMockHass:
    """Tests for the shared mock_hass fixture."""

    def test_mock_hass_has_states(self, mock_hass):
        assert hasattr(mock_hass, 'states')
        assert isinstance(mock_hass.states, MagicMock)

    def test_mock_hass_has_async_services(self, mock_hass):
        assert isinstance(mock_hass.services.async_call, AsyncMock)

    def test_mock_hass_has_event_bus(self, mock_hass):
        assert isinstance(mock_hass.bus.async_fire, AsyncMock)

    def test_mock_hass_has_data_structure(self, mock_hass):
        assert "adaptive_climate" in mock_hass.data
        ac_data = mock_hass.data["adaptive_climate"]
        assert "coordinator" in ac_data
        assert "learning_store" in ac_data

    def test_mock_hass_has_async_create_task(self, mock_hass):
        assert hasattr(mock_hass, 'async_create_task')
        assert isinstance(mock_hass.async_create_task, MagicMock)

    def test_mock_hass_has_async_call_later(self, mock_hass):
        """async_call_later should return a cancel callback."""
        assert hasattr(mock_hass, 'async_call_later')
        cancel = mock_hass.async_call_later(10, lambda: None)
        assert callable(cancel)


class TestTimeTravel:
    """Tests for the time_travel fixture."""

    def test_initial_time_is_deterministic(self, time_travel):
        """time_travel starts at a known fixed time."""
        assert isinstance(time_travel.now(), datetime)
        assert time_travel.now().tzinfo is not None  # timezone-aware

    def test_monotonic_returns_float(self, time_travel):
        assert isinstance(time_travel.monotonic(), float)

    def test_advance_minutes(self, time_travel):
        """Advancing minutes moves both utcnow and monotonic."""
        start_dt = time_travel.now()
        start_mono = time_travel.monotonic()

        time_travel.advance(minutes=30)

        assert time_travel.now() == start_dt + timedelta(minutes=30)
        assert time_travel.monotonic() == start_mono + 1800.0

    def test_advance_is_cumulative(self, time_travel):
        """Multiple advances accumulate."""
        start_dt = time_travel.now()

        time_travel.advance(minutes=10)
        time_travel.advance(minutes=20)

        assert time_travel.now() == start_dt + timedelta(minutes=30)

    def test_utcnow_patched(self, time_travel):
        """dt_util.utcnow() returns the controlled time."""
        from homeassistant.util import dt as dt_util

        start = time_travel.now()
        assert dt_util.utcnow() == start

        time_travel.advance(hours=1)
        assert dt_util.utcnow() == start + timedelta(hours=1)

    def test_monotonic_patched(self, time_travel):
        """time.monotonic() returns the controlled time."""
        import time

        start = time_travel.monotonic()
        assert time.monotonic() == start

        time_travel.advance(seconds=60)
        assert time.monotonic() == start + 60.0

    def test_advance_with_mixed_units(self, time_travel):
        """Advance accepts hours, minutes, and seconds together."""
        start_dt = time_travel.now()
        time_travel.advance(hours=1, minutes=30, seconds=45)
        expected = start_dt + timedelta(hours=1, minutes=30, seconds=45)
        assert time_travel.now() == expected
