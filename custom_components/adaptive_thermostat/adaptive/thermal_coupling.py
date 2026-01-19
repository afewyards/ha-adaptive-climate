"""Thermal coupling learning for multi-zone heat transfer prediction.

This module provides automatic learning of thermal coupling coefficients between
zones, enabling feedforward compensation to reduce overshoot when neighboring
zones are heating.
"""

from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Dict, Any, List, Optional, Tuple

from ..const import (
    CONF_FLOORPLAN,
    CONF_SEED_COEFFICIENTS,
    CONF_STAIRWELL_ZONES,
    DEFAULT_SEED_COEFFICIENTS,
)


@dataclass
class CouplingObservation:
    """A single observation of heat transfer between two zones.

    Records the temperature changes in source and target zones during a
    heating event, along with environmental conditions.
    """

    timestamp: datetime
    source_zone: str          # Entity ID of the zone that was heating
    target_zone: str          # Entity ID of the zone being observed
    source_temp_start: float  # Source zone temp at observation start (°C)
    source_temp_end: float    # Source zone temp at observation end (°C)
    target_temp_start: float  # Target zone temp at observation start (°C)
    target_temp_end: float    # Target zone temp at observation end (°C)
    outdoor_temp_start: float # Outdoor temp at observation start (°C)
    outdoor_temp_end: float   # Outdoor temp at observation end (°C)
    duration_minutes: float   # Duration of observation in minutes

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to dictionary for persistence."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "source_zone": self.source_zone,
            "target_zone": self.target_zone,
            "source_temp_start": self.source_temp_start,
            "source_temp_end": self.source_temp_end,
            "target_temp_start": self.target_temp_start,
            "target_temp_end": self.target_temp_end,
            "outdoor_temp_start": self.outdoor_temp_start,
            "outdoor_temp_end": self.outdoor_temp_end,
            "duration_minutes": self.duration_minutes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CouplingObservation":
        """Create observation from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source_zone=data["source_zone"],
            target_zone=data["target_zone"],
            source_temp_start=data["source_temp_start"],
            source_temp_end=data["source_temp_end"],
            target_temp_start=data["target_temp_start"],
            target_temp_end=data["target_temp_end"],
            outdoor_temp_start=data["outdoor_temp_start"],
            outdoor_temp_end=data["outdoor_temp_end"],
            duration_minutes=data["duration_minutes"],
        )


@dataclass
class CouplingCoefficient:
    """A learned thermal coupling coefficient between two zones.

    Represents the heat transfer rate from source to target zone,
    with confidence tracking for Bayesian blending with seed values.
    """

    source_zone: str              # Entity ID of the source zone
    target_zone: str              # Entity ID of the target zone
    coefficient: float            # Learned coupling coefficient (°C/hour per °C)
    confidence: float             # Confidence level (0-1)
    observation_count: int        # Number of observations used to calculate
    baseline_overshoot: Optional[float]  # Baseline overshoot before compensation (for validation)
    last_updated: datetime        # When the coefficient was last updated

    def to_dict(self) -> Dict[str, Any]:
        """Convert coefficient to dictionary for persistence."""
        return {
            "source_zone": self.source_zone,
            "target_zone": self.target_zone,
            "coefficient": self.coefficient,
            "confidence": self.confidence,
            "observation_count": self.observation_count,
            "baseline_overshoot": self.baseline_overshoot,
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CouplingCoefficient":
        """Create coefficient from dictionary."""
        return cls(
            source_zone=data["source_zone"],
            target_zone=data["target_zone"],
            coefficient=data["coefficient"],
            confidence=data["confidence"],
            observation_count=data["observation_count"],
            baseline_overshoot=data["baseline_overshoot"],
            last_updated=datetime.fromisoformat(data["last_updated"]),
        )


@dataclass
class ObservationContext:
    """Context for a pending thermal coupling observation.

    Captures the initial state when a zone starts heating, so we can
    calculate temperature deltas when it stops heating.
    """

    source_zone: str                         # Entity ID of the zone that started heating
    start_time: datetime                     # When the observation started
    source_temp_start: float                 # Source zone temp at start (°C)
    target_temps_start: Dict[str, float]     # All other zone temps at start {entity_id: temp}
    outdoor_temp_start: float                # Outdoor temp at start (°C)


def parse_floorplan(config: Dict[str, Any]) -> Dict[Tuple[str, str], float]:
    """Parse floorplan configuration and generate seed coefficients for zone pairs.

    Args:
        config: Configuration dict containing:
            - floorplan: List of floor definitions, each with:
                - floor: Floor number (int)
                - zones: List of zone entity IDs on this floor
                - open: Optional list of zones with open floor plan (high coupling)
            - stairwell_zones: Optional list of zones connected by stairwell
            - seed_coefficients: Optional dict to override default seed values

    Returns:
        Dict mapping (source_zone, target_zone) tuples to seed coefficient values.
        Coefficients represent expected heat transfer rate (°C/hour per °C source rise).
    """
    floorplan = config.get(CONF_FLOORPLAN, [])
    if not floorplan:
        return {}

    # Merge custom seed coefficients with defaults
    seed_values = {**DEFAULT_SEED_COEFFICIENTS}
    custom_seeds = config.get(CONF_SEED_COEFFICIENTS, {})
    seed_values.update(custom_seeds)

    stairwell_zones = set(config.get(CONF_STAIRWELL_ZONES, []))

    # Build floor index: zone -> floor number
    zone_to_floor: Dict[str, int] = {}
    floor_to_zones: Dict[int, List[str]] = {}
    floor_open_zones: Dict[int, set] = {}

    for floor_def in floorplan:
        floor_num = floor_def.get("floor", 0)
        zones = floor_def.get("zones", [])
        open_zones = set(floor_def.get("open", []))

        floor_to_zones[floor_num] = zones
        floor_open_zones[floor_num] = open_zones

        for zone in zones:
            zone_to_floor[zone] = floor_num

    seeds: Dict[Tuple[str, str], float] = {}

    # Generate same-floor pairs (including open floor plan)
    for floor_num, zones in floor_to_zones.items():
        open_zones = floor_open_zones.get(floor_num, set())

        for zone_a, zone_b in combinations(zones, 2):
            # Check if both zones are in open floor plan
            if zone_a in open_zones and zone_b in open_zones:
                seed_type = "open"
            else:
                seed_type = "same_floor"

            # Bidirectional: A->B and B->A
            seeds[(zone_a, zone_b)] = seed_values[seed_type]
            seeds[(zone_b, zone_a)] = seed_values[seed_type]

    # Generate vertical (cross-floor) pairs for adjacent floors
    sorted_floors = sorted(floor_to_zones.keys())

    for i in range(len(sorted_floors) - 1):
        lower_floor = sorted_floors[i]
        upper_floor = sorted_floors[i + 1]

        lower_zones = floor_to_zones[lower_floor]
        upper_zones = floor_to_zones[upper_floor]

        for lower_zone in lower_zones:
            for upper_zone in upper_zones:
                # Determine if this is a stairwell connection
                is_stairwell = lower_zone in stairwell_zones and upper_zone in stairwell_zones

                # Lower -> Upper: heat rises (up or stairwell_up)
                if is_stairwell:
                    seeds[(lower_zone, upper_zone)] = seed_values["stairwell_up"]
                    seeds[(upper_zone, lower_zone)] = seed_values["stairwell_down"]
                else:
                    seeds[(lower_zone, upper_zone)] = seed_values["up"]
                    seeds[(upper_zone, lower_zone)] = seed_values["down"]

    return seeds
