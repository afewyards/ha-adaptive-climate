"""Tests for thermal coupling learning."""

import pytest
from datetime import datetime

from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
    CouplingObservation,
    CouplingCoefficient,
    ObservationContext,
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


# ============================================================================
# CouplingCoefficient Tests
# ============================================================================


class TestCouplingCoefficient:
    """Tests for the CouplingCoefficient dataclass."""

    def test_coupling_coefficient_creation(self):
        """Test basic coefficient creation with all fields including baseline_overshoot."""
        now = datetime.now()
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.25,
            confidence=0.7,
            observation_count=5,
            baseline_overshoot=0.3,
            last_updated=now,
        )

        assert coef.source_zone == "climate.living_room"
        assert coef.target_zone == "climate.kitchen"
        assert coef.coefficient == 0.25
        assert coef.confidence == 0.7
        assert coef.observation_count == 5
        assert coef.baseline_overshoot == 0.3
        assert coef.last_updated == now

    def test_coupling_coefficient_optional_baseline(self):
        """Test coefficient creation with None baseline_overshoot."""
        coef = CouplingCoefficient(
            source_zone="climate.bedroom",
            target_zone="climate.bathroom",
            coefficient=0.15,
            confidence=0.5,
            observation_count=3,
            baseline_overshoot=None,
            last_updated=datetime.now(),
        )

        assert coef.baseline_overshoot is None

    def test_coupling_coefficient_to_dict(self):
        """Test coefficient serialization."""
        timestamp = datetime(2024, 1, 15, 12, 30, 0)
        coef = CouplingCoefficient(
            source_zone="climate.office",
            target_zone="climate.hallway",
            coefficient=0.35,
            confidence=0.85,
            observation_count=10,
            baseline_overshoot=0.25,
            last_updated=timestamp,
        )

        data = coef.to_dict()

        assert data["source_zone"] == "climate.office"
        assert data["target_zone"] == "climate.hallway"
        assert data["coefficient"] == 0.35
        assert data["confidence"] == 0.85
        assert data["observation_count"] == 10
        assert data["baseline_overshoot"] == 0.25
        assert data["last_updated"] == "2024-01-15T12:30:00"

    def test_coupling_coefficient_to_dict_none_baseline(self):
        """Test serialization handles None baseline_overshoot."""
        coef = CouplingCoefficient(
            source_zone="climate.zone_a",
            target_zone="climate.zone_b",
            coefficient=0.20,
            confidence=0.4,
            observation_count=2,
            baseline_overshoot=None,
            last_updated=datetime(2024, 2, 1, 10, 0, 0),
        )

        data = coef.to_dict()

        assert data["baseline_overshoot"] is None

    def test_coupling_coefficient_from_dict(self):
        """Test coefficient deserialization from dict."""
        data = {
            "source_zone": "climate.garage",
            "target_zone": "climate.mudroom",
            "coefficient": 0.18,
            "confidence": 0.65,
            "observation_count": 7,
            "baseline_overshoot": 0.4,
            "last_updated": "2024-03-10T14:45:00",
        }

        coef = CouplingCoefficient.from_dict(data)

        assert coef.source_zone == "climate.garage"
        assert coef.target_zone == "climate.mudroom"
        assert coef.coefficient == 0.18
        assert coef.confidence == 0.65
        assert coef.observation_count == 7
        assert coef.baseline_overshoot == 0.4
        assert coef.last_updated == datetime(2024, 3, 10, 14, 45, 0)

    def test_coupling_coefficient_from_dict_none_baseline(self):
        """Test deserialization handles None baseline_overshoot."""
        data = {
            "source_zone": "climate.attic",
            "target_zone": "climate.upstairs",
            "coefficient": 0.30,
            "confidence": 0.55,
            "observation_count": 4,
            "baseline_overshoot": None,
            "last_updated": "2024-04-20T08:00:00",
        }

        coef = CouplingCoefficient.from_dict(data)

        assert coef.baseline_overshoot is None

    def test_coupling_coefficient_roundtrip(self):
        """Test serialization roundtrip preserves all data."""
        original = CouplingCoefficient(
            source_zone="climate.master_bedroom",
            target_zone="climate.en_suite",
            coefficient=0.42,
            confidence=0.92,
            observation_count=15,
            baseline_overshoot=0.28,
            last_updated=datetime(2024, 5, 15, 16, 30, 45),
        )

        # Convert to dict and back
        data = original.to_dict()
        restored = CouplingCoefficient.from_dict(data)

        assert restored.source_zone == original.source_zone
        assert restored.target_zone == original.target_zone
        assert restored.coefficient == original.coefficient
        assert restored.confidence == original.confidence
        assert restored.observation_count == original.observation_count
        assert restored.baseline_overshoot == original.baseline_overshoot
        assert restored.last_updated == original.last_updated


# ============================================================================
# ObservationContext Tests
# ============================================================================


class TestObservationContext:
    """Tests for the ObservationContext dataclass."""

    def test_observation_context_creation(self):
        """Test context creation - tracks source zone and start temps."""
        start_time = datetime.now()
        target_temps = {
            "climate.kitchen": 18.5,
            "climate.bedroom": 17.0,
            "climate.bathroom": 19.0,
        }

        context = ObservationContext(
            source_zone="climate.living_room",
            start_time=start_time,
            source_temp_start=19.0,
            target_temps_start=target_temps,
            outdoor_temp_start=5.0,
        )

        assert context.source_zone == "climate.living_room"
        assert context.start_time == start_time
        assert context.source_temp_start == 19.0
        assert context.target_temps_start == target_temps
        assert context.target_temps_start["climate.kitchen"] == 18.5
        assert context.target_temps_start["climate.bedroom"] == 17.0
        assert context.target_temps_start["climate.bathroom"] == 19.0
        assert context.outdoor_temp_start == 5.0

    def test_observation_context_empty_targets(self):
        """Test context creation with no target zones."""
        context = ObservationContext(
            source_zone="climate.only_zone",
            start_time=datetime.now(),
            source_temp_start=20.0,
            target_temps_start={},
            outdoor_temp_start=0.0,
        )

        assert context.source_zone == "climate.only_zone"
        assert context.target_temps_start == {}
