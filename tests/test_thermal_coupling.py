"""Tests for thermal coupling learning."""

import pytest
from datetime import datetime

from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
    CouplingObservation,
    CouplingCoefficient,
    ObservationContext,
    parse_floorplan,
)
from custom_components.adaptive_thermostat.const import DEFAULT_SEED_COEFFICIENTS


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


# ============================================================================
# Floorplan Parser Tests
# ============================================================================


class TestParseFloorplan:
    """Tests for the floorplan parser function."""

    def test_parse_floorplan_same_floor(self):
        """Zones on same floor get same_floor seed for all pairs."""
        config = {
            "floorplan": [
                {
                    "floor": 1,
                    "zones": ["climate.living_room", "climate.kitchen", "climate.dining"],
                }
            ]
        }

        seeds = parse_floorplan(config)

        # Each zone pair should have same_floor seed (bidirectional)
        assert seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.kitchen", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.living_room", "climate.dining")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.dining", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.kitchen", "climate.dining")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.dining", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

    def test_parse_floorplan_vertical(self):
        """Floor N to N+1 gets 'up' seed, N+1 to N gets 'down' seed."""
        config = {
            "floorplan": [
                {"floor": 0, "zones": ["climate.garage"]},
                {"floor": 1, "zones": ["climate.living_room"]},
                {"floor": 2, "zones": ["climate.bedroom"]},
            ]
        }

        seeds = parse_floorplan(config)

        # Floor 0 -> Floor 1: up (heat rises from garage to living_room)
        assert seeds[("climate.garage", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["up"]
        # Floor 1 -> Floor 0: down
        assert seeds[("climate.living_room", "climate.garage")] == DEFAULT_SEED_COEFFICIENTS["down"]

        # Floor 1 -> Floor 2: up
        assert seeds[("climate.living_room", "climate.bedroom")] == DEFAULT_SEED_COEFFICIENTS["up"]
        # Floor 2 -> Floor 1: down
        assert seeds[("climate.bedroom", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["down"]

        # Non-adjacent floors should not have entries (floor 0 and floor 2)
        assert ("climate.garage", "climate.bedroom") not in seeds
        assert ("climate.bedroom", "climate.garage") not in seeds

    def test_parse_floorplan_open(self):
        """Zones listed in 'open' array get open seed (overrides same_floor)."""
        config = {
            "floorplan": [
                {
                    "floor": 1,
                    "zones": ["climate.living_room", "climate.kitchen", "climate.hallway"],
                    "open": ["climate.living_room", "climate.kitchen"],
                }
            ]
        }

        seeds = parse_floorplan(config)

        # Open zones get open seed (bidirectional)
        assert seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["open"]
        assert seeds[("climate.kitchen", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["open"]

        # Non-open zone pairs on same floor get same_floor seed
        assert seeds[("climate.living_room", "climate.hallway")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.hallway", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.kitchen", "climate.hallway")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.hallway", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

    def test_parse_floorplan_stairwell(self):
        """Stairwell_zones get stairwell_up/down seeds for vertical relationships."""
        config = {
            "floorplan": [
                {"floor": 0, "zones": ["climate.hallway_ground"]},
                {"floor": 1, "zones": ["climate.hallway_first"]},
                {"floor": 2, "zones": ["climate.hallway_second"]},
            ],
            "stairwell_zones": [
                "climate.hallway_ground",
                "climate.hallway_first",
                "climate.hallway_second",
            ],
        }

        seeds = parse_floorplan(config)

        # Stairwell vertical: upward gets stairwell_up
        assert seeds[("climate.hallway_ground", "climate.hallway_first")] == DEFAULT_SEED_COEFFICIENTS["stairwell_up"]
        assert seeds[("climate.hallway_first", "climate.hallway_second")] == DEFAULT_SEED_COEFFICIENTS["stairwell_up"]

        # Stairwell vertical: downward gets stairwell_down
        assert seeds[("climate.hallway_first", "climate.hallway_ground")] == DEFAULT_SEED_COEFFICIENTS["stairwell_down"]
        assert seeds[("climate.hallway_second", "climate.hallway_first")] == DEFAULT_SEED_COEFFICIENTS["stairwell_down"]

    def test_parse_floorplan_custom_seeds(self):
        """Override default seed values from config."""
        config = {
            "floorplan": [
                {
                    "floor": 1,
                    "zones": ["climate.living_room", "climate.kitchen"],
                }
            ],
            "seed_coefficients": {
                "same_floor": 0.20,  # Override default 0.15
            },
        }

        seeds = parse_floorplan(config)

        # Should use custom seed value
        assert seeds[("climate.living_room", "climate.kitchen")] == 0.20
        assert seeds[("climate.kitchen", "climate.living_room")] == 0.20

    def test_parse_floorplan_empty_config(self):
        """Empty or missing floorplan returns empty dict."""
        assert parse_floorplan({}) == {}
        assert parse_floorplan({"floorplan": []}) == {}

    def test_parse_floorplan_single_zone_floor(self):
        """Single zone on a floor creates no same_floor pairs."""
        config = {
            "floorplan": [
                {"floor": 1, "zones": ["climate.only_zone"]},
            ]
        }

        seeds = parse_floorplan(config)

        # No pairs should be generated for a single zone
        assert seeds == {}

    def test_parse_floorplan_mixed_scenario(self):
        """Complex scenario with multiple floors, open plan, and stairwell."""
        config = {
            "floorplan": [
                {"floor": 0, "zones": ["climate.garage", "climate.utility"]},
                {
                    "floor": 1,
                    "zones": ["climate.living_room", "climate.kitchen", "climate.hallway"],
                    "open": ["climate.living_room", "climate.kitchen"],
                },
                {"floor": 2, "zones": ["climate.bedroom", "climate.bathroom", "climate.landing"]},
            ],
            "stairwell_zones": ["climate.hallway", "climate.landing"],
        }

        seeds = parse_floorplan(config)

        # Floor 0: same_floor between garage and utility
        assert seeds[("climate.garage", "climate.utility")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

        # Floor 1: open between living_room and kitchen
        assert seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["open"]

        # Floor 1: same_floor for hallway with other zones
        assert seeds[("climate.hallway", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

        # Vertical: floor 0 to floor 1 (normal up/down)
        assert seeds[("climate.garage", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["up"]
        assert seeds[("climate.living_room", "climate.garage")] == DEFAULT_SEED_COEFFICIENTS["down"]

        # Stairwell: hallway (floor 1) to landing (floor 2)
        assert seeds[("climate.hallway", "climate.landing")] == DEFAULT_SEED_COEFFICIENTS["stairwell_up"]
        assert seeds[("climate.landing", "climate.hallway")] == DEFAULT_SEED_COEFFICIENTS["stairwell_down"]

        # Non-stairwell vertical: bedroom to hallway
        assert seeds[("climate.hallway", "climate.bedroom")] == DEFAULT_SEED_COEFFICIENTS["up"]
        assert seeds[("climate.bedroom", "climate.hallway")] == DEFAULT_SEED_COEFFICIENTS["down"]


# ============================================================================
# ThermalCouplingLearner Tests
# ============================================================================


class TestThermalCouplingLearner:
    """Tests for the ThermalCouplingLearner class."""

    def test_learner_initialization(self):
        """Test learner creates empty observations/coefficients dicts."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        # Should have empty dicts for observations and coefficients
        assert learner.observations == {}
        assert learner.coefficients == {}
        # Lock is lazy initialized, so _lock starts as None
        # The _async_lock property creates it on first access (in async context)
        assert learner._lock is None  # Not yet created
        # Note: _async_lock property tested in async context elsewhere

    def test_learner_seed_initialization(self):
        """Test learner loads seeds from floorplan config."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        floorplan_config = {
            "floorplan": [
                {
                    "floor": 1,
                    "zones": ["climate.living_room", "climate.kitchen"],
                }
            ]
        }

        learner.initialize_seeds(floorplan_config)

        # Should have seeds for the zone pairs
        assert ("climate.living_room", "climate.kitchen") in learner._seeds
        assert ("climate.kitchen", "climate.living_room") in learner._seeds
        # Seeds should match default same_floor value
        assert learner._seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

    def test_learner_get_coefficient_no_data(self):
        """Test get_coefficient returns None for unknown pair with no seed."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        # No seeds or observations for this pair
        result = learner.get_coefficient("climate.unknown_a", "climate.unknown_b")

        assert result is None

    def test_learner_get_coefficient_seed_only(self):
        """Test get_coefficient returns seed with 0.3 confidence when only seed available."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()

        floorplan_config = {
            "floorplan": [
                {
                    "floor": 1,
                    "zones": ["climate.living_room", "climate.kitchen"],
                }
            ]
        }

        learner.initialize_seeds(floorplan_config)

        result = learner.get_coefficient("climate.living_room", "climate.kitchen")

        # Should return a CouplingCoefficient based on seed
        assert result is not None
        assert isinstance(result, CouplingCoefficient)
        assert result.source_zone == "climate.living_room"
        assert result.target_zone == "climate.kitchen"
        assert result.coefficient == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        # Seed-only confidence should be COUPLING_CONFIDENCE_THRESHOLD (0.3)
        assert result.confidence == 0.3
        assert result.observation_count == 0


# ============================================================================
# Observation Start/End Lifecycle Tests
# ============================================================================


class TestObservationLifecycle:
    """Tests for observation start/end lifecycle methods."""

    def test_start_observation(self):
        """Test start_observation creates ObservationContext for source zone."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            ObservationContext,
        )

        learner = ThermalCouplingLearner()

        all_zone_temps = {
            "climate.living_room": 19.0,
            "climate.kitchen": 18.5,
            "climate.bedroom": 17.0,
        }
        outdoor_temp = 5.0

        learner.start_observation(
            source_zone="climate.living_room",
            all_zone_temps=all_zone_temps,
            outdoor_temp=outdoor_temp,
        )

        # Should have a pending observation for the source zone
        assert "climate.living_room" in learner._pending
        context = learner._pending["climate.living_room"]
        assert isinstance(context, ObservationContext)
        assert context.source_zone == "climate.living_room"
        assert context.source_temp_start == 19.0
        assert context.outdoor_temp_start == 5.0
        # Target temps should NOT include the source zone
        assert "climate.living_room" not in context.target_temps_start
        assert context.target_temps_start["climate.kitchen"] == 18.5
        assert context.target_temps_start["climate.bedroom"] == 17.0

    def test_start_observation_skips_if_pending(self):
        """Test start_observation does not create duplicate observations for same source."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )
        from datetime import datetime, timedelta

        learner = ThermalCouplingLearner()

        all_zone_temps = {
            "climate.living_room": 19.0,
            "climate.kitchen": 18.5,
        }

        # Start first observation
        learner.start_observation(
            source_zone="climate.living_room",
            all_zone_temps=all_zone_temps,
            outdoor_temp=5.0,
        )
        original_start_time = learner._pending["climate.living_room"].start_time

        # Try to start another observation for same zone
        learner.start_observation(
            source_zone="climate.living_room",
            all_zone_temps={"climate.living_room": 20.0, "climate.kitchen": 19.0},
            outdoor_temp=6.0,
        )

        # Should still have the original observation, not a new one
        assert learner._pending["climate.living_room"].start_time == original_start_time
        assert learner._pending["climate.living_room"].source_temp_start == 19.0

    def test_end_observation_creates_records(self):
        """Test end_observation creates CouplingObservation for each target zone."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )
        from datetime import datetime, timedelta
        from unittest.mock import patch

        learner = ThermalCouplingLearner()

        # Set up initial temps
        all_zone_temps_start = {
            "climate.living_room": 19.0,
            "climate.kitchen": 18.5,
            "climate.bedroom": 17.0,
        }

        # Manually create a pending observation with a known start time
        start_time = datetime.now() - timedelta(minutes=30)
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ObservationContext,
        )
        learner._pending["climate.living_room"] = ObservationContext(
            source_zone="climate.living_room",
            start_time=start_time,
            source_temp_start=19.0,
            target_temps_start={"climate.kitchen": 18.5, "climate.bedroom": 17.0},
            outdoor_temp_start=5.0,
        )

        # End observation with new temps
        current_temps = {
            "climate.living_room": 21.5,  # Source rose 2.5°C
            "climate.kitchen": 19.2,      # Target rose 0.7°C
            "climate.bedroom": 17.5,      # Target rose 0.5°C
        }
        idle_zones = {"climate.kitchen", "climate.bedroom"}

        observations = learner.end_observation(
            source_zone="climate.living_room",
            current_temps=current_temps,
            outdoor_temp=5.5,
            idle_zones=idle_zones,
        )

        # Should return observations for each target zone
        assert len(observations) == 2

        # Check kitchen observation
        kitchen_obs = next((o for o in observations if o.target_zone == "climate.kitchen"), None)
        assert kitchen_obs is not None
        assert kitchen_obs.source_zone == "climate.living_room"
        assert kitchen_obs.source_temp_start == 19.0
        assert kitchen_obs.source_temp_end == 21.5
        assert kitchen_obs.target_temp_start == 18.5
        assert kitchen_obs.target_temp_end == 19.2
        assert kitchen_obs.outdoor_temp_start == 5.0
        assert kitchen_obs.outdoor_temp_end == 5.5
        assert 29 <= kitchen_obs.duration_minutes <= 31  # ~30 minutes

        # Pending observation should be cleared
        assert "climate.living_room" not in learner._pending

    def test_end_observation_calculates_deltas(self):
        """Test end_observation computes source_temp_delta and target_temp_delta correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            ObservationContext,
        )
        from datetime import datetime, timedelta

        learner = ThermalCouplingLearner()

        start_time = datetime.now() - timedelta(minutes=45)
        learner._pending["climate.office"] = ObservationContext(
            source_zone="climate.office",
            start_time=start_time,
            source_temp_start=18.0,
            target_temps_start={"climate.hallway": 16.0},
            outdoor_temp_start=2.0,
        )

        observations = learner.end_observation(
            source_zone="climate.office",
            current_temps={"climate.office": 21.0, "climate.hallway": 17.2},
            outdoor_temp=2.5,
            idle_zones={"climate.hallway"},
        )

        assert len(observations) == 1
        obs = observations[0]

        # Verify deltas are correct
        source_delta = obs.source_temp_end - obs.source_temp_start
        target_delta = obs.target_temp_end - obs.target_temp_start

        assert source_delta == 3.0  # 21.0 - 18.0
        assert abs(target_delta - 1.2) < 0.001  # 17.2 - 16.0 (with floating point tolerance)

    def test_end_observation_no_pending(self):
        """Test end_observation returns empty list if no pending observation."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        observations = learner.end_observation(
            source_zone="climate.unknown",
            current_temps={"climate.unknown": 20.0},
            outdoor_temp=5.0,
            idle_zones=set(),
        )

        assert observations == []

    def test_end_observation_only_idle_zones(self):
        """Test end_observation only creates observations for zones that were idle."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            ObservationContext,
        )
        from datetime import datetime, timedelta

        learner = ThermalCouplingLearner()

        start_time = datetime.now() - timedelta(minutes=30)
        learner._pending["climate.living_room"] = ObservationContext(
            source_zone="climate.living_room",
            start_time=start_time,
            source_temp_start=19.0,
            target_temps_start={
                "climate.kitchen": 18.5,
                "climate.bedroom": 17.0,
                "climate.bathroom": 20.0,
            },
            outdoor_temp_start=5.0,
        )

        # Only kitchen was idle, bedroom and bathroom were also heating
        current_temps = {
            "climate.living_room": 21.5,
            "climate.kitchen": 19.2,
            "climate.bedroom": 19.0,
            "climate.bathroom": 22.0,
        }
        idle_zones = {"climate.kitchen"}  # Only kitchen was idle

        observations = learner.end_observation(
            source_zone="climate.living_room",
            current_temps=current_temps,
            outdoor_temp=5.5,
            idle_zones=idle_zones,
        )

        # Should only have observation for kitchen (the only idle zone)
        assert len(observations) == 1
        assert observations[0].target_zone == "climate.kitchen"
