"""Thermal coupling learning for multi-zone heat transfer prediction.

This module provides automatic learning of thermal coupling coefficients between
zones, enabling feedforward compensation to reduce overshoot when neighboring
zones are heating.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Dict, Any, List, Optional, Tuple

from ..const import (
    CONF_FLOORPLAN,
    CONF_SEED_COEFFICIENTS,
    CONF_STAIRWELL_ZONES,
    COUPLING_CONFIDENCE_THRESHOLD,
    COUPLING_MAX_OUTDOOR_CHANGE,
    COUPLING_MIN_DURATION_MINUTES,
    COUPLING_MIN_SOURCE_RISE,
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


def should_record_observation(observation: CouplingObservation) -> bool:
    """Determine if an observation should be recorded for learning.

    Filters out observations that are unlikely to provide useful coupling data
    due to noise, external factors, or invalid conditions.

    Args:
        observation: The CouplingObservation to evaluate.

    Returns:
        True if the observation should be recorded, False otherwise.

    Filtering criteria:
        1. Duration must be >= COUPLING_MIN_DURATION_MINUTES (15 min)
        2. Source temp rise must be >= COUPLING_MIN_SOURCE_RISE (0.3°C)
        3. Target must have been cooler than source at start
        4. Outdoor temp change must be <= COUPLING_MAX_OUTDOOR_CHANGE (3°C)
        5. Target temp must not have dropped (delta >= 0)
    """
    # Filter 1: Skip short duration observations
    if observation.duration_minutes < COUPLING_MIN_DURATION_MINUTES:
        return False

    # Filter 2: Skip if source temp rise is too low
    source_delta = observation.source_temp_end - observation.source_temp_start
    if source_delta < COUPLING_MIN_SOURCE_RISE:
        return False

    # Filter 3: Skip if target was warmer than source at start
    # Heat flows from hot to cold, so target should be cooler
    if observation.target_temp_start >= observation.source_temp_start:
        return False

    # Filter 4: Skip if outdoor temp changed too much
    # Large outdoor changes indicate external factors affecting temps
    outdoor_delta = abs(observation.outdoor_temp_end - observation.outdoor_temp_start)
    if outdoor_delta > COUPLING_MAX_OUTDOOR_CHANGE:
        return False

    # Filter 5: Skip if target temp dropped
    # Negative delta means something else caused cooling, not coupling
    target_delta = observation.target_temp_end - observation.target_temp_start
    if target_delta < 0:
        return False

    return True


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


class ThermalCouplingLearner:
    """Learns thermal coupling coefficients between zones from heating observations.

    This class tracks heating events and temperature changes across zones to learn
    how heat transfers between adjacent spaces. Learned coefficients enable
    feedforward compensation to reduce overshoot when neighboring zones are heating.

    The learner uses Bayesian blending to combine seed coefficients (from floorplan
    configuration) with observed data, gradually increasing confidence as more
    observations are collected.
    """

    def __init__(self) -> None:
        """Initialize the thermal coupling learner with empty state."""
        # Dict mapping (source_zone, target_zone) -> list of CouplingObservation
        self.observations: Dict[Tuple[str, str], List[CouplingObservation]] = {}

        # Dict mapping (source_zone, target_zone) -> CouplingCoefficient
        self.coefficients: Dict[Tuple[str, str], CouplingCoefficient] = {}

        # Seed coefficients from floorplan (used as Bayesian prior)
        self._seeds: Dict[Tuple[str, str], float] = {}

        # Pending observations (zones currently heating)
        self._pending: Dict[str, ObservationContext] = {}

        # Lock for thread-safe access to shared state (lazy initialized)
        self._lock: Optional[asyncio.Lock] = None

    @property
    def _async_lock(self) -> asyncio.Lock:
        """Get the asyncio lock, creating it if needed.

        Lazy initialization avoids issues with event loop availability
        at construction time in Python 3.9.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def initialize_seeds(self, floorplan_config: Dict[str, Any]) -> None:
        """Initialize seed coefficients from floorplan configuration.

        Seeds provide the Bayesian prior for coefficient estimation. They are
        blended with observed data using COUPLING_SEED_WEIGHT pseudo-observations.

        Args:
            floorplan_config: Configuration dict containing floorplan, stairwell_zones,
                and optional seed_coefficients override.
        """
        self._seeds = parse_floorplan(floorplan_config)

    def get_coefficient(
        self, source_zone: str, target_zone: str
    ) -> Optional[CouplingCoefficient]:
        """Get the coupling coefficient for a zone pair.

        Returns the learned coefficient if available, falls back to seed coefficient
        if no observations exist, or returns None if no data is available for this pair.

        Args:
            source_zone: Entity ID of the source zone (the one heating)
            target_zone: Entity ID of the target zone (receiving heat transfer)

        Returns:
            CouplingCoefficient if data is available, None otherwise.
            Seed-only coefficients have confidence=0.3 (COUPLING_CONFIDENCE_THRESHOLD).
        """
        pair = (source_zone, target_zone)

        # Check if we have a learned coefficient
        if pair in self.coefficients:
            return self.coefficients[pair]

        # Fall back to seed coefficient if available
        if pair in self._seeds:
            return CouplingCoefficient(
                source_zone=source_zone,
                target_zone=target_zone,
                coefficient=self._seeds[pair],
                confidence=COUPLING_CONFIDENCE_THRESHOLD,  # 0.3 for seed-only
                observation_count=0,
                baseline_overshoot=None,
                last_updated=datetime.now(),
            )

        # No data available for this pair
        return None

    def start_observation(
        self,
        source_zone: str,
        all_zone_temps: Dict[str, float],
        outdoor_temp: float,
    ) -> None:
        """Start an observation when a zone begins heating.

        Creates an ObservationContext that captures the initial state of all zones
        so we can calculate temperature deltas when the source zone stops heating.

        If an observation is already pending for this source zone, this call is
        ignored to prevent duplicate observations.

        Args:
            source_zone: Entity ID of the zone that started heating
            all_zone_temps: Current temperatures of all zones {entity_id: temp}
            outdoor_temp: Current outdoor temperature (°C)
        """
        # Skip if observation already pending for this zone
        if source_zone in self._pending:
            return

        # Extract source temp and build target temps dict (excluding source)
        source_temp = all_zone_temps.get(source_zone, 0.0)
        target_temps = {
            zone: temp
            for zone, temp in all_zone_temps.items()
            if zone != source_zone
        }

        # Create observation context
        self._pending[source_zone] = ObservationContext(
            source_zone=source_zone,
            start_time=datetime.now(),
            source_temp_start=source_temp,
            target_temps_start=target_temps,
            outdoor_temp_start=outdoor_temp,
        )

    def end_observation(
        self,
        source_zone: str,
        current_temps: Dict[str, float],
        outdoor_temp: float,
        idle_zones: set,
    ) -> List[CouplingObservation]:
        """End an observation when a zone stops heating.

        Creates CouplingObservation records for each target zone that was idle
        (not heating) during the observation period.

        Args:
            source_zone: Entity ID of the zone that stopped heating
            current_temps: Current temperatures of all zones {entity_id: temp}
            outdoor_temp: Current outdoor temperature (°C)
            idle_zones: Set of zone entity IDs that were idle during the observation

        Returns:
            List of CouplingObservation records created for idle target zones.
            Returns empty list if no pending observation exists for source_zone.
        """
        # Check if we have a pending observation for this zone
        if source_zone not in self._pending:
            return []

        context = self._pending.pop(source_zone)
        now = datetime.now()

        # Calculate duration in minutes
        duration_minutes = (now - context.start_time).total_seconds() / 60.0

        # Get current source temp
        source_temp_end = current_temps.get(source_zone, context.source_temp_start)

        observations: List[CouplingObservation] = []

        # Create observation for each target zone that was idle
        for target_zone, target_temp_start in context.target_temps_start.items():
            # Skip if target wasn't idle (was also heating)
            if target_zone not in idle_zones:
                continue

            # Skip if we don't have current temp for target
            if target_zone not in current_temps:
                continue

            target_temp_end = current_temps[target_zone]

            observation = CouplingObservation(
                timestamp=now,
                source_zone=source_zone,
                target_zone=target_zone,
                source_temp_start=context.source_temp_start,
                source_temp_end=source_temp_end,
                target_temp_start=target_temp_start,
                target_temp_end=target_temp_end,
                outdoor_temp_start=context.outdoor_temp_start,
                outdoor_temp_end=outdoor_temp,
                duration_minutes=duration_minutes,
            )
            observations.append(observation)

        return observations
