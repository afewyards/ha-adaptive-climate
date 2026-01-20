"""Tests for thermal coupling learning."""

import pytest
from datetime import datetime

from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
    CouplingObservation,
    CouplingCoefficient,
    ObservationContext,
    parse_floorplan,
    build_seeds_from_discovered_floors,
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
# Build Seeds From Discovered Floors Tests
# ============================================================================


class TestBuildSeedsFromDiscoveredFloors:
    """Tests for the build_seeds_from_discovered_floors function."""

    def test_build_seeds_same_floor(self):
        """Zones on same floor get same_floor coefficient (0.15)."""
        zone_floors = {
            "climate.living_room": 1,
            "climate.kitchen": 1,
            "climate.dining": 1,
        }
        open_zones = []
        stairwell_zones = []

        seeds = build_seeds_from_discovered_floors(zone_floors, open_zones, stairwell_zones)

        # Each zone pair should have same_floor seed (bidirectional)
        assert seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.kitchen", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.living_room", "climate.dining")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.dining", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.kitchen", "climate.dining")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.dining", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

    def test_build_seeds_adjacent_floors(self):
        """Zones on adjacent floors get up/down coefficients."""
        zone_floors = {
            "climate.garage": 0,
            "climate.living_room": 1,
            "climate.bedroom": 2,
        }
        open_zones = []
        stairwell_zones = []

        seeds = build_seeds_from_discovered_floors(zone_floors, open_zones, stairwell_zones)

        # Floor 0 -> Floor 1: up (heat rises)
        assert seeds[("climate.garage", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["up"]
        # Floor 1 -> Floor 0: down
        assert seeds[("climate.living_room", "climate.garage")] == DEFAULT_SEED_COEFFICIENTS["down"]

        # Floor 1 -> Floor 2: up
        assert seeds[("climate.living_room", "climate.bedroom")] == DEFAULT_SEED_COEFFICIENTS["up"]
        # Floor 2 -> Floor 1: down
        assert seeds[("climate.bedroom", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["down"]

        # Non-adjacent floors should not have entries
        assert ("climate.garage", "climate.bedroom") not in seeds
        assert ("climate.bedroom", "climate.garage") not in seeds

    def test_build_seeds_open_same_floor(self):
        """Open zones on same floor get open coefficient (0.60)."""
        zone_floors = {
            "climate.living_room": 1,
            "climate.kitchen": 1,
            "climate.hallway": 1,
        }
        open_zones = ["climate.living_room", "climate.kitchen"]
        stairwell_zones = []

        seeds = build_seeds_from_discovered_floors(zone_floors, open_zones, stairwell_zones)

        # Open zones get open seed (bidirectional)
        assert seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["open"]
        assert seeds[("climate.kitchen", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["open"]

        # Non-open zone pairs on same floor get same_floor seed
        assert seeds[("climate.living_room", "climate.hallway")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.hallway", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.kitchen", "climate.hallway")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.hallway", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

    def test_build_seeds_open_different_floors(self):
        """Open zones on different floors get normal up/down."""
        zone_floors = {
            "climate.living_room": 1,
            "climate.kitchen": 2,
        }
        open_zones = ["climate.living_room", "climate.kitchen"]
        stairwell_zones = []

        seeds = build_seeds_from_discovered_floors(zone_floors, open_zones, stairwell_zones)

        # Different floors: should get up/down, NOT open coefficient
        assert seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["up"]
        assert seeds[("climate.kitchen", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["down"]

    def test_build_seeds_stairwell(self):
        """Stairwell zones get stairwell_up/stairwell_down coefficients."""
        zone_floors = {
            "climate.hallway_ground": 0,
            "climate.hallway_first": 1,
            "climate.hallway_second": 2,
        }
        open_zones = []
        stairwell_zones = [
            "climate.hallway_ground",
            "climate.hallway_first",
            "climate.hallway_second",
        ]

        seeds = build_seeds_from_discovered_floors(zone_floors, open_zones, stairwell_zones)

        # Stairwell vertical: upward gets stairwell_up
        assert seeds[("climate.hallway_ground", "climate.hallway_first")] == DEFAULT_SEED_COEFFICIENTS["stairwell_up"]
        assert seeds[("climate.hallway_first", "climate.hallway_second")] == DEFAULT_SEED_COEFFICIENTS["stairwell_up"]

        # Stairwell vertical: downward gets stairwell_down
        assert seeds[("climate.hallway_first", "climate.hallway_ground")] == DEFAULT_SEED_COEFFICIENTS["stairwell_down"]
        assert seeds[("climate.hallway_second", "climate.hallway_first")] == DEFAULT_SEED_COEFFICIENTS["stairwell_down"]

    def test_build_seeds_excludes_none_floors(self):
        """Zones with None floor are excluded from pairs."""
        zone_floors = {
            "climate.living_room": 1,
            "climate.kitchen": 1,
            "climate.unassigned": None,
        }
        open_zones = []
        stairwell_zones = []

        seeds = build_seeds_from_discovered_floors(zone_floors, open_zones, stairwell_zones)

        # Only living_room and kitchen should have a pairing
        assert seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert seeds[("climate.kitchen", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

        # Unassigned zone should not be in any pairs
        assert all("climate.unassigned" not in pair for pair in seeds.keys())


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


# ============================================================================
# Observation Filtering Tests
# ============================================================================


class TestObservationFiltering:
    """Tests for observation filtering logic."""

    def test_filter_skip_short_duration(self):
        """Test observations < 15 min are filtered out."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        # Observation with short duration (10 min < 15 min minimum)
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.0,
            target_temp_start=18.0,
            target_temp_end=18.5,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=10.0,  # Too short!
        )

        assert should_record_observation(obs) is False

    def test_filter_skip_low_source_rise(self):
        """Test observations with source temp rise < 0.3C are filtered out."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        # Observation with low source temp rise (0.2C < 0.3C minimum)
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=20.0,
            source_temp_end=20.2,  # Only 0.2C rise
            target_temp_start=18.0,
            target_temp_end=18.1,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=30.0,
        )

        assert should_record_observation(obs) is False

    def test_filter_skip_target_warmer(self):
        """Test observations are filtered if target was warmer than source at start."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        # Target was warmer than source at start - no meaningful coupling
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=18.0,
            source_temp_end=21.0,
            target_temp_start=19.0,  # Warmer than source start!
            target_temp_end=19.5,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=30.0,
        )

        assert should_record_observation(obs) is False

    def test_filter_skip_outdoor_change(self):
        """Test observations are filtered if outdoor temp changed > 3C."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        # Large outdoor temp change indicates external factors
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=22.0,
            target_temp_start=18.0,
            target_temp_end=19.0,
            outdoor_temp_start=5.0,
            outdoor_temp_end=9.0,  # 4C change > 3C max
            duration_minutes=30.0,
        )

        assert should_record_observation(obs) is False

    def test_filter_skip_target_dropped(self):
        """Test observations are filtered if target temp dropped."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        # Target temp dropped - can't learn coupling from negative delta
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=22.0,
            target_temp_start=18.0,
            target_temp_end=17.5,  # Temp dropped!
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=30.0,
        )

        assert should_record_observation(obs) is False

    def test_filter_pass_valid_observation(self):
        """Test valid observations pass all filters."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        # Valid observation meeting all criteria
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=22.0,      # 3C rise > 0.3C min
            target_temp_start=18.0,     # Cooler than source
            target_temp_end=19.0,       # Increased (positive delta)
            outdoor_temp_start=5.0,
            outdoor_temp_end=6.0,       # Only 1C change < 3C max
            duration_minutes=30.0,      # > 15 min
        )

        assert should_record_observation(obs) is True

    def test_filter_outdoor_negative_change(self):
        """Test outdoor temp drop > 3C also triggers filter."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        # Outdoor temp dropped significantly
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=22.0,
            target_temp_start=18.0,
            target_temp_end=19.0,
            outdoor_temp_start=10.0,
            outdoor_temp_end=5.0,  # -5C change, abs() > 3C max
            duration_minutes=30.0,
        )

        assert should_record_observation(obs) is False

    def test_filter_boundary_duration(self):
        """Test exactly 15 min duration passes."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=22.0,
            target_temp_start=18.0,
            target_temp_end=19.0,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=15.0,  # Exactly at boundary
        )

        assert should_record_observation(obs) is True

    def test_filter_boundary_source_rise(self):
        """Test exactly 0.3C source rise passes."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=19.3,  # Exactly 0.3C rise
            target_temp_start=18.0,
            target_temp_end=18.1,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=30.0,
        )

        assert should_record_observation(obs) is True

    def test_filter_boundary_outdoor_change(self):
        """Test exactly 3C outdoor change passes."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            should_record_observation,
        )

        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=22.0,
            target_temp_start=18.0,
            target_temp_end=19.0,
            outdoor_temp_start=5.0,
            outdoor_temp_end=8.0,  # Exactly 3C change
            duration_minutes=30.0,
        )

        assert should_record_observation(obs) is True


# ============================================================================
# Transfer Rate and Coefficient Calculation Tests
# ============================================================================


class TestTransferRateCalculation:
    """Tests for transfer rate calculation from observations."""

    def test_calc_transfer_rate_basic(self):
        """Test transfer rate computes target_delta / (source_delta * hours)."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            _calculate_transfer_rate,
        )

        # 60 min observation: source rose 2°C, target rose 0.5°C
        # Rate = 0.5 / (2 * 1.0) = 0.25 °C/hour per °C source
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.0,      # 2°C rise
            target_temp_start=18.0,
            target_temp_end=18.5,      # 0.5°C rise
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,     # 1 hour
        )

        rate = _calculate_transfer_rate(obs)
        assert abs(rate - 0.25) < 0.001

    def test_calc_transfer_rate_shorter_duration(self):
        """Test transfer rate with 30 minute observation."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            _calculate_transfer_rate,
        )

        # 30 min observation: source rose 3°C, target rose 0.3°C
        # Rate = 0.3 / (3 * 0.5) = 0.2 °C/hour per °C source
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=18.0,
            source_temp_end=21.0,      # 3°C rise
            target_temp_start=17.0,
            target_temp_end=17.3,      # 0.3°C rise
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=30.0,     # 0.5 hours
        )

        rate = _calculate_transfer_rate(obs)
        assert abs(rate - 0.2) < 0.001

    def test_calc_transfer_rate_zero_source_delta(self):
        """Test transfer rate returns 0 when source delta is zero."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingObservation,
            _calculate_transfer_rate,
        )

        # Zero source delta - should return 0 to avoid division by zero
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=20.0,
            source_temp_end=20.0,      # No change
            target_temp_start=18.0,
            target_temp_end=18.5,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,
        )

        rate = _calculate_transfer_rate(obs)
        assert rate == 0.0


class TestCoefficientCalculation:
    """Tests for Bayesian coefficient calculation."""

    def test_calc_coefficient_no_seed_single_observation(self):
        """Test coefficient calculation with one observation, no seed."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )

        learner = ThermalCouplingLearner()

        # Add single observation with known transfer rate
        # 60 min, source +2°C, target +0.4°C -> rate = 0.4 / (2 * 1) = 0.2
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.0,
            target_temp_start=18.0,
            target_temp_end=18.4,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,
        )

        pair = ("climate.living_room", "climate.kitchen")
        learner.observations[pair] = [obs]

        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        # No seed: just the observation average (0.2)
        assert coef is not None
        assert abs(coef.coefficient - 0.2) < 0.01
        assert coef.observation_count == 1

    def test_calc_coefficient_no_seed_multiple_observations(self):
        """Test coefficient is average of transfer rates without seed."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )

        learner = ThermalCouplingLearner()

        # Three observations with rates: 0.2, 0.3, 0.4
        # Average = (0.2 + 0.3 + 0.4) / 3 = 0.3
        observations = []
        for target_rise, rate in [(0.4, 0.2), (0.6, 0.3), (0.8, 0.4)]:
            # Each: 60 min, source +2°C
            obs = CouplingObservation(
                timestamp=datetime.now(),
                source_zone="climate.living_room",
                target_zone="climate.kitchen",
                source_temp_start=19.0,
                source_temp_end=21.0,
                target_temp_start=18.0,
                target_temp_end=18.0 + target_rise,
                outdoor_temp_start=5.0,
                outdoor_temp_end=5.0,
                duration_minutes=60.0,
            )
            observations.append(obs)

        pair = ("climate.living_room", "climate.kitchen")
        learner.observations[pair] = observations

        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        assert coef is not None
        assert abs(coef.coefficient - 0.3) < 0.01
        assert coef.observation_count == 3

    def test_calc_coefficient_with_seed_bayesian_blend(self):
        """Test Bayesian blend: (seed*6 + obs*count) / (6+count)."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )
        from custom_components.adaptive_thermostat.const import COUPLING_SEED_WEIGHT

        learner = ThermalCouplingLearner()

        # Set up seed for the pair
        pair = ("climate.living_room", "climate.kitchen")
        learner._seeds[pair] = 0.15  # same_floor seed

        # Add 3 observations with rate = 0.3 each
        # Bayesian: (0.15*6 + 0.3*3) / (6+3) = (0.9 + 0.9) / 9 = 0.2
        observations = []
        for _ in range(3):
            obs = CouplingObservation(
                timestamp=datetime.now(),
                source_zone="climate.living_room",
                target_zone="climate.kitchen",
                source_temp_start=19.0,
                source_temp_end=21.0,
                target_temp_start=18.0,
                target_temp_end=18.6,  # +0.6°C / (2°C * 1h) = 0.3 rate
                outdoor_temp_start=5.0,
                outdoor_temp_end=5.0,
                duration_minutes=60.0,
            )
            observations.append(obs)

        learner.observations[pair] = observations

        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        # Bayesian blend: (0.15*6 + 0.3*3) / (6+3) = 1.8/9 = 0.2
        assert coef is not None
        assert abs(coef.coefficient - 0.2) < 0.01

    def test_calc_confidence_base_from_count(self):
        """Test confidence scales with observation count."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )
        from custom_components.adaptive_thermostat.const import COUPLING_MIN_OBSERVATIONS

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Consistent observations (low variance) - confidence should grow with count
        observations = []
        for i in range(5):
            obs = CouplingObservation(
                timestamp=datetime.now(),
                source_zone="climate.living_room",
                target_zone="climate.kitchen",
                source_temp_start=19.0,
                source_temp_end=21.0,
                target_temp_start=18.0,
                target_temp_end=18.4,  # Consistent rate
                outdoor_temp_start=5.0,
                outdoor_temp_end=5.0,
                duration_minutes=60.0,
            )
            observations.append(obs)

        learner.observations[pair] = observations

        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        assert coef is not None
        # With 5 consistent observations and no seed, confidence should be > 0.3
        assert coef.confidence > 0.3
        assert coef.observation_count == 5

    def test_calc_confidence_reduced_by_variance(self):
        """Test confidence is reduced when observations have high variance."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # High variance observations with vastly different rates
        # Rate 1: 0.1, Rate 2: 0.5 -> high variance
        obs1 = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.0,
            target_temp_start=18.0,
            target_temp_end=18.2,  # 0.2 / 2 = 0.1 rate
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,
        )
        obs2 = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.0,
            target_temp_start=18.0,
            target_temp_end=19.0,  # 1.0 / 2 = 0.5 rate
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,
        )

        learner.observations[pair] = [obs1, obs2]

        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        assert coef is not None
        # High variance should reduce confidence
        assert coef.confidence < 0.3  # Below threshold due to variance

    def test_coefficient_capped_at_max(self):
        """Test coefficient is capped at MAX_COEFFICIENT=0.5."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )
        from custom_components.adaptive_thermostat.const import COUPLING_MAX_COEFFICIENT

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Very high transfer rate observation (unrealistic but tests capping)
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=20.0,      # +1°C source
            target_temp_start=18.0,
            target_temp_end=19.0,      # +1°C target in 1 hour = rate 1.0
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,
        )

        learner.observations[pair] = [obs]

        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        assert coef is not None
        # Coefficient should be capped at 0.5
        assert coef.coefficient == COUPLING_MAX_COEFFICIENT
        assert coef.coefficient == 0.5

    def test_calc_coefficient_no_observations_returns_none(self):
        """Test calculate_coefficient returns None when no observations exist."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        # No observations, no seeds
        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        assert coef is None

    def test_calc_coefficient_seed_only_returns_seed(self):
        """Test calculate_coefficient falls back to get_coefficient for seed-only."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        # Set up seed but no observations
        pair = ("climate.living_room", "climate.kitchen")
        learner._seeds[pair] = 0.15

        # calculate_coefficient with no observations but seed should return None
        # (calculation requires observations; get_coefficient handles seed fallback)
        coef = learner.calculate_coefficient("climate.living_room", "climate.kitchen")

        assert coef is None  # Need observations to calculate


# ============================================================================
# Graduated Confidence Function Tests
# ============================================================================


class TestGraduatedConfidence:
    """Tests for the graduated_confidence scaling function."""

    def test_graduated_confidence_below_threshold(self):
        """Test confidence < 0.3 returns 0."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            graduated_confidence,
        )

        # Below threshold: should return 0 (no effect)
        assert graduated_confidence(0.0) == 0.0
        assert graduated_confidence(0.1) == 0.0
        assert graduated_confidence(0.2) == 0.0
        assert graduated_confidence(0.29) == 0.0

    def test_graduated_confidence_above_max(self):
        """Test confidence >= 0.5 returns 1.0."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            graduated_confidence,
        )

        # At or above max: should return 1.0 (full effect)
        assert graduated_confidence(0.5) == 1.0
        assert graduated_confidence(0.6) == 1.0
        assert graduated_confidence(0.8) == 1.0
        assert graduated_confidence(1.0) == 1.0

    def test_graduated_confidence_linear_ramp(self):
        """Test linear interpolation between 0.3 and 0.5."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            graduated_confidence,
        )

        # At threshold (0.3): should return 0.0
        assert abs(graduated_confidence(0.3) - 0.0) < 0.001

        # Midpoint (0.4): should return 0.5
        # Linear formula: (0.4 - 0.3) / (0.5 - 0.3) = 0.1 / 0.2 = 0.5
        assert abs(graduated_confidence(0.4) - 0.5) < 0.001

        # At 0.35: should return 0.25
        # (0.35 - 0.3) / (0.5 - 0.3) = 0.05 / 0.2 = 0.25
        assert abs(graduated_confidence(0.35) - 0.25) < 0.001

        # At 0.45: should return 0.75
        # (0.45 - 0.3) / (0.5 - 0.3) = 0.15 / 0.2 = 0.75
        assert abs(graduated_confidence(0.45) - 0.75) < 0.001

        # Just below max (0.49): should be close to 1.0
        # (0.49 - 0.3) / (0.5 - 0.3) = 0.19 / 0.2 = 0.95
        assert abs(graduated_confidence(0.49) - 0.95) < 0.001


# ============================================================================
# Learner Serialization Tests
# ============================================================================


class TestLearnerSerialization:
    """Tests for ThermalCouplingLearner serialization/deserialization."""

    def test_learner_to_dict_empty(self):
        """Test to_dict on empty learner returns valid structure."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()
        data = learner.to_dict()

        assert "observations" in data
        assert "coefficients" in data
        assert "seeds" in data
        assert data["observations"] == {}
        assert data["coefficients"] == {}
        assert data["seeds"] == {}

    def test_learner_to_dict_with_observations(self):
        """Test to_dict serializes observations correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )

        learner = ThermalCouplingLearner()

        # Add observations
        obs = CouplingObservation(
            timestamp=datetime(2024, 1, 15, 12, 30, 0),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.0,
            target_temp_start=18.0,
            target_temp_end=18.5,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,
        )
        pair = ("climate.living_room", "climate.kitchen")
        learner.observations[pair] = [obs]

        data = learner.to_dict()

        # Observations should be keyed by "source_zone|target_zone"
        obs_key = "climate.living_room|climate.kitchen"
        assert obs_key in data["observations"]
        assert len(data["observations"][obs_key]) == 1
        assert data["observations"][obs_key][0]["timestamp"] == "2024-01-15T12:30:00"

    def test_learner_to_dict_with_coefficients(self):
        """Test to_dict serializes coefficients correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()

        # Add coefficient
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.25,
            confidence=0.7,
            observation_count=5,
            baseline_overshoot=0.3,
            last_updated=datetime(2024, 1, 15, 14, 0, 0),
        )
        pair = ("climate.living_room", "climate.kitchen")
        learner.coefficients[pair] = coef

        data = learner.to_dict()

        # Coefficients should be keyed by "source_zone|target_zone"
        coef_key = "climate.living_room|climate.kitchen"
        assert coef_key in data["coefficients"]
        assert data["coefficients"][coef_key]["coefficient"] == 0.25
        assert data["coefficients"][coef_key]["confidence"] == 0.7

    def test_learner_to_dict_with_seeds(self):
        """Test to_dict serializes seeds correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        # Initialize seeds
        learner.initialize_seeds({
            "floorplan": [
                {"floor": 1, "zones": ["climate.living_room", "climate.kitchen"]}
            ]
        })

        data = learner.to_dict()

        # Seeds should be serialized with pipe-separated keys
        assert "climate.living_room|climate.kitchen" in data["seeds"]
        assert "climate.kitchen|climate.living_room" in data["seeds"]

    def test_learner_from_dict_empty(self):
        """Test from_dict restores empty learner."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        data = {
            "observations": {},
            "coefficients": {},
            "seeds": {},
        }

        learner = ThermalCouplingLearner.from_dict(data)

        assert learner.observations == {}
        assert learner.coefficients == {}
        assert learner._seeds == {}

    def test_learner_from_dict_with_observations(self):
        """Test from_dict restores observations correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
        )

        data = {
            "observations": {
                "climate.living_room|climate.kitchen": [
                    {
                        "timestamp": "2024-01-15T12:30:00",
                        "source_zone": "climate.living_room",
                        "target_zone": "climate.kitchen",
                        "source_temp_start": 19.0,
                        "source_temp_end": 21.0,
                        "target_temp_start": 18.0,
                        "target_temp_end": 18.5,
                        "outdoor_temp_start": 5.0,
                        "outdoor_temp_end": 5.0,
                        "duration_minutes": 60.0,
                    }
                ]
            },
            "coefficients": {},
            "seeds": {},
        }

        learner = ThermalCouplingLearner.from_dict(data)

        pair = ("climate.living_room", "climate.kitchen")
        assert pair in learner.observations
        assert len(learner.observations[pair]) == 1
        obs = learner.observations[pair][0]
        assert isinstance(obs, CouplingObservation)
        assert obs.timestamp == datetime(2024, 1, 15, 12, 30, 0)
        assert obs.source_temp_end == 21.0

    def test_learner_from_dict_with_coefficients(self):
        """Test from_dict restores coefficients correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        data = {
            "observations": {},
            "coefficients": {
                "climate.living_room|climate.kitchen": {
                    "source_zone": "climate.living_room",
                    "target_zone": "climate.kitchen",
                    "coefficient": 0.25,
                    "confidence": 0.7,
                    "observation_count": 5,
                    "baseline_overshoot": 0.3,
                    "last_updated": "2024-01-15T14:00:00",
                }
            },
            "seeds": {},
        }

        learner = ThermalCouplingLearner.from_dict(data)

        pair = ("climate.living_room", "climate.kitchen")
        assert pair in learner.coefficients
        coef = learner.coefficients[pair]
        assert isinstance(coef, CouplingCoefficient)
        assert coef.coefficient == 0.25
        assert coef.confidence == 0.7

    def test_learner_from_dict_with_seeds(self):
        """Test from_dict restores seeds correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        data = {
            "observations": {},
            "coefficients": {},
            "seeds": {
                "climate.living_room|climate.kitchen": 0.15,
                "climate.kitchen|climate.living_room": 0.15,
            },
        }

        learner = ThermalCouplingLearner.from_dict(data)

        assert learner._seeds[("climate.living_room", "climate.kitchen")] == 0.15
        assert learner._seeds[("climate.kitchen", "climate.living_room")] == 0.15

    def test_learner_from_dict_error_recovery(self):
        """Test from_dict skips invalid items and continues."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        data = {
            "observations": {
                # Valid observation
                "climate.living_room|climate.kitchen": [
                    {
                        "timestamp": "2024-01-15T12:30:00",
                        "source_zone": "climate.living_room",
                        "target_zone": "climate.kitchen",
                        "source_temp_start": 19.0,
                        "source_temp_end": 21.0,
                        "target_temp_start": 18.0,
                        "target_temp_end": 18.5,
                        "outdoor_temp_start": 5.0,
                        "outdoor_temp_end": 5.0,
                        "duration_minutes": 60.0,
                    }
                ],
                # Invalid observation (missing timestamp)
                "climate.bedroom|climate.bathroom": [
                    {
                        "source_zone": "climate.bedroom",
                        "target_zone": "climate.bathroom",
                    }
                ],
            },
            "coefficients": {
                # Valid coefficient
                "climate.living_room|climate.kitchen": {
                    "source_zone": "climate.living_room",
                    "target_zone": "climate.kitchen",
                    "coefficient": 0.25,
                    "confidence": 0.7,
                    "observation_count": 5,
                    "baseline_overshoot": None,
                    "last_updated": "2024-01-15T14:00:00",
                },
                # Invalid coefficient (missing fields)
                "climate.bad|climate.data": {
                    "coefficient": 0.1,
                },
            },
            "seeds": {
                "climate.a|climate.b": 0.15,
            },
        }

        # Should not raise, should skip invalid items
        learner = ThermalCouplingLearner.from_dict(data)

        # Valid items should be restored
        assert ("climate.living_room", "climate.kitchen") in learner.observations
        assert ("climate.living_room", "climate.kitchen") in learner.coefficients
        assert ("climate.a", "climate.b") in learner._seeds

        # Invalid items should be skipped
        assert ("climate.bedroom", "climate.bathroom") not in learner.observations
        assert ("climate.bad", "climate.data") not in learner.coefficients

    def test_learner_roundtrip(self):
        """Test to_dict then from_dict preserves state."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingObservation,
            CouplingCoefficient,
        )

        # Create learner with full state
        original = ThermalCouplingLearner()

        # Add seeds
        original.initialize_seeds({
            "floorplan": [
                {"floor": 1, "zones": ["climate.living_room", "climate.kitchen"]}
            ]
        })

        # Add observation
        obs = CouplingObservation(
            timestamp=datetime(2024, 1, 15, 12, 30, 0),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=19.0,
            source_temp_end=21.0,
            target_temp_start=18.0,
            target_temp_end=18.5,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,
        )
        pair = ("climate.living_room", "climate.kitchen")
        original.observations[pair] = [obs]

        # Add coefficient
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.25,
            confidence=0.7,
            observation_count=5,
            baseline_overshoot=0.3,
            last_updated=datetime(2024, 1, 15, 14, 0, 0),
        )
        original.coefficients[pair] = coef

        # Roundtrip
        data = original.to_dict()
        restored = ThermalCouplingLearner.from_dict(data)

        # Verify seeds
        assert restored._seeds == original._seeds

        # Verify observations
        assert list(restored.observations.keys()) == list(original.observations.keys())
        orig_obs = original.observations[pair][0]
        rest_obs = restored.observations[pair][0]
        assert rest_obs.timestamp == orig_obs.timestamp
        assert rest_obs.source_temp_end == orig_obs.source_temp_end
        assert rest_obs.target_temp_end == orig_obs.target_temp_end

        # Verify coefficients
        assert list(restored.coefficients.keys()) == list(original.coefficients.keys())
        orig_coef = original.coefficients[pair]
        rest_coef = restored.coefficients[pair]
        assert rest_coef.coefficient == orig_coef.coefficient
        assert rest_coef.confidence == orig_coef.confidence
        assert rest_coef.observation_count == orig_coef.observation_count
        assert rest_coef.baseline_overshoot == orig_coef.baseline_overshoot
        assert rest_coef.last_updated == orig_coef.last_updated


# ============================================================================
# Validation and Rollback Tests
# ============================================================================


class TestCoefficientValidation:
    """Tests for coefficient validation and rollback logic."""

    def test_validation_tracks_baseline(self):
        """Test baseline_overshoot is stored when compensation first applied."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Add a coefficient with no baseline yet
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.25,
            confidence=0.5,
            observation_count=5,
            baseline_overshoot=None,
            validation_cycles=0,
            last_updated=datetime.now(),
        )
        learner.coefficients[pair] = coef

        # Record baseline overshoot
        learner.record_baseline_overshoot(pair, overshoot=0.3)

        # Baseline should now be stored
        assert learner.coefficients[pair].baseline_overshoot == 0.3
        assert learner.coefficients[pair].validation_cycles == 0

    def test_validation_counts_cycles(self):
        """Test validation_cycles increments after coefficient change."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Set up coefficient with baseline recorded
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.25,
            confidence=0.5,
            observation_count=5,
            baseline_overshoot=0.3,
            validation_cycles=0,
            last_updated=datetime.now(),
        )
        learner.coefficients[pair] = coef

        # Add validation cycle
        learner.add_validation_cycle(pair, overshoot=0.28)

        # Validation cycles should increment
        assert learner.coefficients[pair].validation_cycles == 1

        # Add more cycles
        learner.add_validation_cycle(pair, overshoot=0.31)
        learner.add_validation_cycle(pair, overshoot=0.29)

        assert learner.coefficients[pair].validation_cycles == 3

    def test_validation_rollback_triggered(self):
        """Test coefficient is halved if overshoot increased >30%."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Set up coefficient with baseline
        original_coefficient = 0.30
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=original_coefficient,
            confidence=0.5,
            observation_count=5,
            baseline_overshoot=0.3,  # Baseline overshoot
            validation_cycles=0,
            last_updated=datetime.now(),
        )
        learner.coefficients[pair] = coef

        # Check validation with much higher overshoot (> 30% increase)
        # Baseline = 0.3, threshold = 0.3 * 1.3 = 0.39
        # Overshoot = 0.45 > 0.39, should trigger rollback
        result = learner.check_validation(pair, current_overshoot=0.45)

        assert result == "rollback"
        # Coefficient should be halved
        assert learner.coefficients[pair].coefficient == original_coefficient / 2
        # Baseline should be cleared
        assert learner.coefficients[pair].baseline_overshoot is None
        # Validation cycles should be reset
        assert learner.coefficients[pair].validation_cycles == 0

    def test_validation_rollback_logs_warning(self, caplog):
        """Test warning is logged when rollback occurs."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )
        import logging

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Set up coefficient with baseline
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.30,
            confidence=0.5,
            observation_count=5,
            baseline_overshoot=0.3,
            validation_cycles=0,
            last_updated=datetime.now(),
        )
        learner.coefficients[pair] = coef

        # Check validation with high overshoot (triggers rollback)
        with caplog.at_level(logging.WARNING):
            result = learner.check_validation(pair, current_overshoot=0.50)

        assert result == "rollback"
        # Verify warning was logged
        assert "rollback" in caplog.text.lower()
        assert "climate.living_room" in caplog.text
        assert "climate.kitchen" in caplog.text

    def test_validation_success_no_rollback(self):
        """Test validation succeeds when overshoot stays within threshold."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Set up coefficient with baseline
        original_coefficient = 0.30
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=original_coefficient,
            confidence=0.5,
            observation_count=5,
            baseline_overshoot=0.3,
            validation_cycles=4,  # Already at 4 cycles
            last_updated=datetime.now(),
        )
        learner.coefficients[pair] = coef

        # Check validation with similar overshoot (< 30% increase)
        # Baseline = 0.3, threshold = 0.3 * 1.3 = 0.39
        # Overshoot = 0.35 < 0.39, should succeed
        result = learner.check_validation(pair, current_overshoot=0.35)

        assert result == "success"
        # Coefficient should remain unchanged
        assert learner.coefficients[pair].coefficient == original_coefficient
        # Baseline should be cleared (validation complete)
        assert learner.coefficients[pair].baseline_overshoot is None
        # Validation cycles should be reset
        assert learner.coefficients[pair].validation_cycles == 0

    def test_validation_continues_below_cycle_count(self):
        """Test validation continues when not enough cycles collected."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Set up coefficient with baseline
        original_coefficient = 0.30
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=original_coefficient,
            confidence=0.5,
            observation_count=5,
            baseline_overshoot=0.3,
            validation_cycles=2,  # Only 2 cycles
            last_updated=datetime.now(),
        )
        learner.coefficients[pair] = coef

        # Check validation - should continue collecting
        result = learner.check_validation(pair, current_overshoot=0.32)

        assert result is None  # Still collecting
        # Coefficient unchanged
        assert learner.coefficients[pair].coefficient == original_coefficient
        # Baseline remains
        assert learner.coefficients[pair].baseline_overshoot == 0.3
        # Validation cycles incremented
        assert learner.coefficients[pair].validation_cycles == 3

    def test_validation_no_baseline_skips(self):
        """Test validation is skipped when no baseline is recorded."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
            CouplingCoefficient,
        )

        learner = ThermalCouplingLearner()
        pair = ("climate.living_room", "climate.kitchen")

        # Set up coefficient without baseline
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.30,
            confidence=0.5,
            observation_count=5,
            baseline_overshoot=None,  # No baseline
            validation_cycles=0,
            last_updated=datetime.now(),
        )
        learner.coefficients[pair] = coef

        # Check validation - should skip
        result = learner.check_validation(pair, current_overshoot=0.50)

        assert result is None  # No validation needed

    def test_validation_no_coefficient_returns_none(self):
        """Test check_validation returns None for unknown pair."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            ThermalCouplingLearner,
        )

        learner = ThermalCouplingLearner()

        # Check validation for unknown pair
        result = learner.check_validation(
            ("climate.unknown", "climate.other"),
            current_overshoot=0.5
        )

        assert result is None

    def test_coefficient_validation_cycles_serialization(self):
        """Test validation_cycles is serialized and deserialized correctly."""
        from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
            CouplingCoefficient,
        )

        # Create coefficient with validation_cycles
        coef = CouplingCoefficient(
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            coefficient=0.25,
            confidence=0.7,
            observation_count=5,
            baseline_overshoot=0.3,
            validation_cycles=3,
            last_updated=datetime(2024, 1, 15, 12, 30, 0),
        )

        # Serialize
        data = coef.to_dict()
        assert data["validation_cycles"] == 3

        # Deserialize
        restored = CouplingCoefficient.from_dict(data)
        assert restored.validation_cycles == 3
