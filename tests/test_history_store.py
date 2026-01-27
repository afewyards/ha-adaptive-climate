"""Tests for weekly report history storage."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.adaptive_thermostat.analytics.history_store import (
    HistoryStore,
    WeeklySnapshot,
    ZoneSnapshot,
    MAX_WEEKS_TO_KEEP,
)


def test_zone_snapshot_to_dict():
    """Test ZoneSnapshot serialization."""
    snapshot = ZoneSnapshot(
        zone_id="living_room",
        duty_cycle=45.5,
        comfort_score=85.0,
        time_at_target=78.5,
        area_m2=25.0,
    )

    data = snapshot.to_dict()

    assert data["zone_id"] == "living_room"
    assert data["duty_cycle"] == 45.5
    assert data["comfort_score"] == 85.0
    assert data["time_at_target"] == 78.5
    assert data["area_m2"] == 25.0


def test_zone_snapshot_from_dict():
    """Test ZoneSnapshot deserialization."""
    data = {
        "zone_id": "bedroom",
        "duty_cycle": 30.2,
        "comfort_score": 92.0,
        "time_at_target": 88.0,
        "area_m2": 15.0,
    }

    snapshot = ZoneSnapshot.from_dict(data)

    assert snapshot.zone_id == "bedroom"
    assert snapshot.duty_cycle == 30.2
    assert snapshot.comfort_score == 92.0
    assert snapshot.time_at_target == 88.0
    assert snapshot.area_m2 == 15.0


def test_zone_snapshot_from_dict_with_missing_fields():
    """Test ZoneSnapshot handles missing optional fields."""
    data = {
        "zone_id": "kitchen",
        "duty_cycle": 55.0,
    }

    snapshot = ZoneSnapshot.from_dict(data)

    assert snapshot.zone_id == "kitchen"
    assert snapshot.duty_cycle == 55.0
    assert snapshot.comfort_score is None
    assert snapshot.time_at_target is None
    assert snapshot.area_m2 is None


def test_weekly_snapshot_to_dict():
    """Test WeeklySnapshot serialization."""
    zones = {
        "living_room": ZoneSnapshot(
            zone_id="living_room",
            duty_cycle=45.5,
            comfort_score=85.0,
            time_at_target=78.5,
            area_m2=25.0,
        )
    }

    snapshot = WeeklySnapshot(
        year=2024,
        week_number=3,
        total_cost=42.30,
        total_energy_kwh=150.5,
        zones=zones,
        timestamp="2024-01-21T10:00:00",
    )

    data = snapshot.to_dict()

    assert data["year"] == 2024
    assert data["week_number"] == 3
    assert data["total_cost"] == 42.30
    assert data["total_energy_kwh"] == 150.5
    assert data["timestamp"] == "2024-01-21T10:00:00"
    assert "living_room" in data["zones"]
    assert data["zones"]["living_room"]["duty_cycle"] == 45.5


def test_weekly_snapshot_from_dict():
    """Test WeeklySnapshot deserialization."""
    data = {
        "year": 2024,
        "week_number": 3,
        "total_cost": 42.30,
        "total_energy_kwh": 150.5,
        "zones": {
            "living_room": {
                "zone_id": "living_room",
                "duty_cycle": 45.5,
                "comfort_score": 85.0,
                "time_at_target": 78.5,
                "area_m2": 25.0,
            }
        },
        "timestamp": "2024-01-21T10:00:00",
    }

    snapshot = WeeklySnapshot.from_dict(data)

    assert snapshot.year == 2024
    assert snapshot.week_number == 3
    assert snapshot.total_cost == 42.30
    assert snapshot.total_energy_kwh == 150.5
    assert "living_room" in snapshot.zones
    assert snapshot.zones["living_room"].duty_cycle == 45.5


@pytest.mark.asyncio
async def test_history_store_save_and_load():
    """Test saving and loading snapshots."""
    import sys

    # Mock homeassistant module
    mock_ha_storage = MagicMock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value=None)
    mock_store_instance.async_save = AsyncMock()
    mock_ha_storage.Store = MagicMock(return_value=mock_store_instance)

    sys.modules["homeassistant.helpers.storage"] = mock_ha_storage

    mock_hass = MagicMock()

    try:
        store = HistoryStore(mock_hass)

        # Load empty history
        snapshots = await store.async_load()
        assert snapshots == []

        # Create and save a snapshot
        zones = {
            "living_room": ZoneSnapshot(
                zone_id="living_room",
                duty_cycle=45.5,
                comfort_score=85.0,
                time_at_target=78.5,
                area_m2=25.0,
            )
        }
        snapshot = WeeklySnapshot(
            year=2024,
            week_number=3,
            total_cost=42.30,
            total_energy_kwh=150.5,
            zones=zones,
            timestamp="2024-01-21T10:00:00",
        )

        await store.async_save_snapshot(snapshot)

        # Verify save was called
        mock_store_instance.async_save.assert_called_once()
        saved_data = mock_store_instance.async_save.call_args[0][0]
        assert "snapshots" in saved_data
        assert len(saved_data["snapshots"]) == 1
    finally:
        # Cleanup mocked module
        if "homeassistant.helpers.storage" in sys.modules:
            del sys.modules["homeassistant.helpers.storage"]


@pytest.mark.asyncio
async def test_history_store_prunes_old_data():
    """Test that history store prunes data beyond MAX_WEEKS_TO_KEEP."""
    import sys

    # Create existing data with MAX_WEEKS_TO_KEEP + 1 snapshots
    existing_snapshots = []
    for week in range(1, MAX_WEEKS_TO_KEEP + 2):
        existing_snapshots.append({
            "year": 2024,
            "week_number": week,
            "total_cost": 40.0 + week,
            "total_energy_kwh": 140.0 + week,
            "zones": {},
            "timestamp": f"2024-01-{7 * week:02d}T10:00:00",
        })

    # Mock homeassistant module
    mock_ha_storage = MagicMock()
    mock_store_instance = AsyncMock()
    mock_store_instance.async_load = AsyncMock(return_value={"snapshots": existing_snapshots})
    mock_store_instance.async_save = AsyncMock()
    mock_ha_storage.Store = MagicMock(return_value=mock_store_instance)

    sys.modules["homeassistant.helpers.storage"] = mock_ha_storage

    mock_hass = MagicMock()

    try:
        store = HistoryStore(mock_hass)

        # Load and add new snapshot
        await store.async_load()

        # Add a new week
        new_snapshot = WeeklySnapshot(
            year=2024,
            week_number=MAX_WEEKS_TO_KEEP + 2,
            total_cost=50.0,
            total_energy_kwh=160.0,
            zones={},
            timestamp="2024-04-01T10:00:00",
        )
        await store.async_save_snapshot(new_snapshot)

        # Verify only MAX_WEEKS_TO_KEEP are saved
        saved_data = mock_store_instance.async_save.call_args[0][0]
        assert len(saved_data["snapshots"]) == MAX_WEEKS_TO_KEEP
    finally:
        # Cleanup mocked module
        if "homeassistant.helpers.storage" in sys.modules:
            del sys.modules["homeassistant.helpers.storage"]


def test_calculate_week_over_week():
    """Test week-over-week change calculation."""
    from unittest.mock import patch
    from homeassistant.util import dt as dt_util

    mock_hass = MagicMock()
    store = HistoryStore(mock_hass)

    # Get current ISO week info to properly set up test data
    now = dt_util.utcnow()
    current_year, current_week, _ = now.isocalendar()

    # Previous week is current_week - 1
    prev_week = current_week - 1 if current_week > 1 else 52
    prev_year = current_year if current_week > 1 else current_year - 1

    prev_snapshot = WeeklySnapshot(
        year=prev_year,
        week_number=prev_week,
        total_cost=50.0,
        total_energy_kwh=200.0,
        zones={},
        timestamp="2024-01-14T10:00:00",
    )
    current_snapshot = WeeklySnapshot(
        year=current_year,
        week_number=current_week,
        total_cost=45.0,
        total_energy_kwh=180.0,
        zones={},
        timestamp="2024-01-21T10:00:00",
    )

    # Set the internal data - newest first (sorted)
    store._data = [current_snapshot, prev_snapshot]

    changes = store.calculate_week_over_week(current_snapshot)

    # Cost decreased by 10% (50 -> 45)
    assert changes["cost_change_pct"] == -10.0
    # Energy decreased by 10% (200 -> 180)
    assert changes["energy_change_pct"] == -10.0


def test_calculate_week_over_week_no_previous():
    """Test week-over-week with no previous data."""
    from homeassistant.util import dt as dt_util

    mock_hass = MagicMock()
    store = HistoryStore(mock_hass)

    # Get current ISO week info
    now = dt_util.utcnow()
    current_year, current_week, _ = now.isocalendar()

    current_snapshot = WeeklySnapshot(
        year=current_year,
        week_number=current_week,
        total_cost=45.0,
        total_energy_kwh=180.0,
        zones={},
        timestamp="2024-01-21T10:00:00",
    )

    # Only current week in data - no previous week
    store._data = [current_snapshot]

    changes = store.calculate_week_over_week(current_snapshot)

    assert changes["cost_change_pct"] is None
    assert changes["energy_change_pct"] is None


def test_get_snapshot_for_week():
    """Test retrieving snapshot for a specific week."""
    mock_hass = MagicMock()
    store = HistoryStore(mock_hass)

    snapshot1 = WeeklySnapshot(
        year=2024,
        week_number=2,
        total_cost=50.0,
        total_energy_kwh=200.0,
        zones={},
        timestamp="2024-01-14T10:00:00",
    )
    snapshot2 = WeeklySnapshot(
        year=2024,
        week_number=3,
        total_cost=45.0,
        total_energy_kwh=180.0,
        zones={},
        timestamp="2024-01-21T10:00:00",
    )

    store._data = [snapshot2, snapshot1]

    # Find week 2
    found = store.get_snapshot_for_week(2024, 2)
    assert found is not None
    assert found.week_number == 2
    assert found.total_cost == 50.0

    # Non-existent week
    not_found = store.get_snapshot_for_week(2024, 10)
    assert not_found is None
