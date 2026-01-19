"""Tests for thermal coupling learning."""

import pytest
from datetime import datetime

from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
    CouplingObservation,
)


# ============================================================================
# CouplingObservation Tests
# ============================================================================


class TestCouplingObservation:
    """Tests for the CouplingObservation dataclass."""

    def test_coupling_observation_creation(self):
        """Test basic observation creation with all fields."""
        now = datetime.now()
        obs = CouplingObservation(
            timestamp=now,
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.5,
            target_temp_start=18.5,
            target_temp_end=19.2,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.5,
            duration_minutes=45.0,
        )

        assert obs.timestamp == now
        assert obs.source_zone == "climate.living_room"
        assert obs.target_zone == "climate.kitchen"
        assert obs.source_temp_start == 19.0
        assert obs.source_temp_end == 21.5
        assert obs.target_temp_start == 18.5
        assert obs.target_temp_end == 19.2
        assert obs.outdoor_temp_start == 5.0
        assert obs.outdoor_temp_end == 5.5
        assert obs.duration_minutes == 45.0

    def test_coupling_observation_to_dict(self):
        """Test observation serialization - datetime as ISO string."""
        timestamp = datetime(2024, 1, 15, 12, 30, 0)
        obs = CouplingObservation(
            timestamp=timestamp,
            source_zone="climate.bedroom",
            target_zone="climate.bathroom",
            source_temp_start=20.0,
            source_temp_end=22.0,
            target_temp_start=19.0,
            target_temp_end=19.8,
            outdoor_temp_start=3.0,
            outdoor_temp_end=3.2,
            duration_minutes=60.0,
        )

        data = obs.to_dict()

        assert data["timestamp"] == "2024-01-15T12:30:00"
        assert data["source_zone"] == "climate.bedroom"
        assert data["target_zone"] == "climate.bathroom"
        assert data["source_temp_start"] == 20.0
        assert data["source_temp_end"] == 22.0
        assert data["target_temp_start"] == 19.0
        assert data["target_temp_end"] == 19.8
        assert data["outdoor_temp_start"] == 3.0
        assert data["outdoor_temp_end"] == 3.2
        assert data["duration_minutes"] == 60.0

    def test_coupling_observation_from_dict(self):
        """Test observation deserialization from dict."""
        data = {
            "timestamp": "2024-01-15T14:00:00",
            "source_zone": "climate.office",
            "target_zone": "climate.hallway",
            "source_temp_start": 18.0,
            "source_temp_end": 21.0,
            "target_temp_start": 17.5,
            "target_temp_end": 18.3,
            "outdoor_temp_start": 0.0,
            "outdoor_temp_end": 0.5,
            "duration_minutes": 90.0,
        }

        obs = CouplingObservation.from_dict(data)

        assert obs.timestamp == datetime(2024, 1, 15, 14, 0, 0)
        assert obs.source_zone == "climate.office"
        assert obs.target_zone == "climate.hallway"
        assert obs.source_temp_start == 18.0
        assert obs.source_temp_end == 21.0
        assert obs.target_temp_start == 17.5
        assert obs.target_temp_end == 18.3
        assert obs.outdoor_temp_start == 0.0
        assert obs.outdoor_temp_end == 0.5
        assert obs.duration_minutes == 90.0

    def test_coupling_observation_roundtrip(self):
        """Test serialization roundtrip preserves all data."""
        original = CouplingObservation(
            timestamp=datetime(2024, 2, 20, 8, 15, 30),
            source_zone="climate.zone_a",
            target_zone="climate.zone_b",
            source_temp_start=17.5,
            source_temp_end=20.5,
            target_temp_start=16.8,
            target_temp_end=17.6,
            outdoor_temp_start=-2.0,
            outdoor_temp_end=-1.5,
            duration_minutes=120.0,
        )

        # Convert to dict and back
        data = original.to_dict()
        restored = CouplingObservation.from_dict(data)

        assert restored.timestamp == original.timestamp
        assert restored.source_zone == original.source_zone
        assert restored.target_zone == original.target_zone
        assert restored.source_temp_start == original.source_temp_start
        assert restored.source_temp_end == original.source_temp_end
        assert restored.target_temp_start == original.target_temp_start
        assert restored.target_temp_end == original.target_temp_end
        assert restored.outdoor_temp_start == original.outdoor_temp_start
        assert restored.outdoor_temp_end == original.outdoor_temp_end
        assert restored.duration_minutes == original.duration_minutes
