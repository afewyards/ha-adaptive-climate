"""Thermal coupling learning for multi-zone heat transfer prediction.

This module provides automatic learning of thermal coupling coefficients between
zones, enabling feedforward compensation to reduce overshoot when neighboring
zones are heating.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

try:
    from ..const import (
        CONF_OPEN_ZONES,
        CONF_SEED_COEFFICIENTS,
        CONF_STAIRWELL_ZONES,
        COUPLING_CONFIDENCE_MAX,
        COUPLING_CONFIDENCE_THRESHOLD,
        COUPLING_MAX_COEFFICIENT,
        COUPLING_MAX_OUTDOOR_CHANGE,
        COUPLING_MIN_DURATION_MINUTES,
        COUPLING_MIN_OBSERVATIONS,
        COUPLING_MIN_SOURCE_RISE,
        COUPLING_SEED_WEIGHT,
        COUPLING_VALIDATION_CYCLES,
        COUPLING_VALIDATION_DEGRADATION,
        DEFAULT_SEED_COEFFICIENTS,
    )
except ImportError:
    from const import (
        CONF_OPEN_ZONES,
        CONF_SEED_COEFFICIENTS,
        CONF_STAIRWELL_ZONES,
        COUPLING_CONFIDENCE_MAX,
        COUPLING_CONFIDENCE_THRESHOLD,
        COUPLING_MAX_COEFFICIENT,
        COUPLING_MAX_OUTDOOR_CHANGE,
        COUPLING_MIN_DURATION_MINUTES,
        COUPLING_MIN_OBSERVATIONS,
        COUPLING_MIN_SOURCE_RISE,
        COUPLING_SEED_WEIGHT,
        COUPLING_VALIDATION_CYCLES,
        COUPLING_VALIDATION_DEGRADATION,
        DEFAULT_SEED_COEFFICIENTS,
    )

# Legacy constant for backward compatibility during migration
CONF_FLOORPLAN = "floorplan"


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
    validation_cycles: int = 0    # Number of cycles in validation window after coefficient change

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
            "validation_cycles": self.validation_cycles,
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
            validation_cycles=data.get("validation_cycles", 0),
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


def graduated_confidence(confidence: float) -> float:
    """Calculate graduated scaling factor based on confidence level.

    Scales the effect of coupling compensation based on how confident we are
    in the learned coefficient. Low confidence means no compensation applied.
    High confidence means full compensation applied. In between, we ramp up linearly.

    Args:
        confidence: Confidence level (0-1) from learned coefficient.

    Returns:
        Scaling factor (0-1) where:
        - 0.0 if confidence < COUPLING_CONFIDENCE_THRESHOLD (0.3)
        - 1.0 if confidence >= COUPLING_CONFIDENCE_MAX (0.5)
        - Linear interpolation between threshold and max

    Examples:
        >>> graduated_confidence(0.2)
        0.0
        >>> graduated_confidence(0.4)
        0.5
        >>> graduated_confidence(0.6)
        1.0
    """
    # Below threshold: no effect
    if confidence < COUPLING_CONFIDENCE_THRESHOLD:
        return 0.0

    # Above max: full effect
    if confidence >= COUPLING_CONFIDENCE_MAX:
        return 1.0

    # Linear ramp between threshold and max
    # Formula: (confidence - min) / (max - min)
    return (confidence - COUPLING_CONFIDENCE_THRESHOLD) / (
        COUPLING_CONFIDENCE_MAX - COUPLING_CONFIDENCE_THRESHOLD
    )


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


def _calculate_transfer_rate(observation: CouplingObservation) -> float:
    """Calculate the heat transfer rate from an observation.

    The transfer rate represents how much the target zone warms per degree
    of source zone warming, normalized by time.

    Formula: target_delta / (source_delta * hours)

    Args:
        observation: The CouplingObservation to calculate from.

    Returns:
        Transfer rate in °C/hour per °C source rise.
        Returns 0.0 if source delta is zero (to avoid division by zero).
    """
    source_delta = observation.source_temp_end - observation.source_temp_start
    target_delta = observation.target_temp_end - observation.target_temp_start

    # Guard against division by zero
    if source_delta == 0:
        return 0.0

    hours = observation.duration_minutes / 60.0

    # Guard against zero duration
    if hours == 0:
        return 0.0

    return target_delta / (source_delta * hours)


def build_seeds_from_discovered_floors(
    zone_floors: Dict[str, Optional[int]],
    open_zones: List[str],
    stairwell_zones: List[str],
    seed_coefficients: Optional[Dict[str, float]] = None
) -> Dict[Tuple[str, str], float]:
    """Build seed coefficients from auto-discovered zone floor assignments.

    Args:
        zone_floors: Dict mapping zone entity IDs to floor levels (int) or None.
            Zones with None floor are excluded from coupling pairs.
        open_zones: List of zone entity IDs that are in open floor plans.
            Open coefficient applies only to zones on the same floor.
        stairwell_zones: List of zone entity IDs connected by stairwells.
            Stairwell coefficients apply to vertical relationships.
        seed_coefficients: Optional dict to override default seed values.

    Returns:
        Dict mapping (source_zone, target_zone) tuples to seed coefficient values.
        Coefficients represent expected heat transfer rate (°C/hour per °C source rise).
    """
    # Merge custom seed coefficients with defaults
    seed_values = {**DEFAULT_SEED_COEFFICIENTS}
    if seed_coefficients:
        seed_values.update(seed_coefficients)

    open_zones_set = set(open_zones)
    stairwell_zones_set = set(stairwell_zones)

    # Build floor index: floor -> zones list (exclude zones with None floor)
    floor_to_zones: Dict[int, List[str]] = {}
    for zone, floor_level in zone_floors.items():
        if floor_level is None:
            continue
        if floor_level not in floor_to_zones:
            floor_to_zones[floor_level] = []
        floor_to_zones[floor_level].append(zone)

    seeds: Dict[Tuple[str, str], float] = {}

    # Generate same-floor pairs
    for floor_num, zones in floor_to_zones.items():
        for zone_a, zone_b in combinations(zones, 2):
            # Check if both zones are in open floor plan AND on same floor
            if zone_a in open_zones_set and zone_b in open_zones_set:
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
                is_stairwell = lower_zone in stairwell_zones_set and upper_zone in stairwell_zones_set

                # Lower -> Upper: heat rises (up or stairwell_up)
                if is_stairwell:
                    seeds[(lower_zone, upper_zone)] = seed_values["stairwell_up"]
                    seeds[(upper_zone, lower_zone)] = seed_values["stairwell_down"]
                else:
                    seeds[(lower_zone, upper_zone)] = seed_values["up"]
                    seeds[(upper_zone, lower_zone)] = seed_values["down"]

    return seeds


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

    def __init__(self, hass: Optional["HomeAssistant"] = None) -> None:
        """Initialize the thermal coupling learner with empty state.

        Args:
            hass: Optional Home Assistant instance for registry access during auto-discovery.
        """
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

        # Home Assistant instance for registry access
        self._hass = hass

    @property
    def _async_lock(self) -> asyncio.Lock:
        """Get the asyncio lock, creating it if needed.

        Lazy initialization avoids issues with event loop availability
        at construction time in Python 3.9.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def initialize_seeds(
        self,
        floorplan_config: Dict[str, Any],
        zone_entity_ids: Optional[List[str]] = None
    ) -> None:
        """Initialize seed coefficients from floorplan configuration or auto-discovery.

        Seeds provide the Bayesian prior for coefficient estimation. They are
        blended with observed data using COUPLING_SEED_WEIGHT pseudo-observations.

        If a legacy floorplan is provided, it takes precedence. Otherwise, if zone_entity_ids
        are provided and hass is available, auto-discovery will be attempted.

        Args:
            floorplan_config: Configuration dict containing floorplan, stairwell_zones,
                and optional seed_coefficients override.
            zone_entity_ids: Optional list of zone entity IDs for auto-discovery.
        """
        import logging
        _LOGGER = logging.getLogger(__name__)

        # Check for legacy floorplan config (takes precedence)
        floorplan = floorplan_config.get(CONF_FLOORPLAN)
        if floorplan:
            # Use legacy parse_floorplan for backward compatibility
            self._seeds = parse_floorplan(floorplan_config)
            return

        # Attempt auto-discovery if hass and zone_entity_ids provided
        if self._hass is not None and zone_entity_ids:
            try:
                from ..helpers.registry import discover_zone_floors
            except ImportError:
                try:
                    from helpers.registry import discover_zone_floors
                except ImportError:
                    _LOGGER.warning(
                        "Floor auto-discovery unavailable: helpers.registry module not found"
                    )
                    return

            # Discover floor assignments from registries
            zone_floors = discover_zone_floors(self._hass, zone_entity_ids)

            # Log warnings for zones without floor assignment
            for zone_id, floor in zone_floors.items():
                if floor is None:
                    _LOGGER.warning(
                        "Zone %s has no floor assignment in area/floor registry. "
                        "Thermal coupling seeds will not be auto-generated for this zone.",
                        zone_id
                    )

            # Extract configuration for seed generation
            open_zones = floorplan_config.get(CONF_OPEN_ZONES, [])
            stairwell_zones = floorplan_config.get(CONF_STAIRWELL_ZONES, [])
            seed_coefficients = floorplan_config.get(CONF_SEED_COEFFICIENTS)

            # Build seeds from discovered floors
            self._seeds = build_seeds_from_discovered_floors(
                zone_floors=zone_floors,
                open_zones=open_zones,
                stairwell_zones=stairwell_zones,
                seed_coefficients=seed_coefficients
            )

            _LOGGER.info(
                "Auto-discovered floor assignments for %d zones, generated %d coupling seeds",
                sum(1 for f in zone_floors.values() if f is not None),
                len(self._seeds)
            )

    def get_pending_observation_count(self) -> int:
        """Get the number of pending observations (zones currently being observed).

        Returns:
            Count of active observation contexts.
        """
        return len(self._pending)

    def get_learner_state(self) -> str:
        """Get the current learning state based on observation and coefficient data.

        Returns:
            One of:
            - "learning": Actively collecting observations (has pending or < 3 obs per pair)
            - "validating": Has learned coefficients but still gathering validation data
            - "stable": Has confident coefficients with sufficient observations

        The state is determined by analyzing the overall health of learned coefficients.
        """
        # Use existing module-level imports for constants
        # COUPLING_CONFIDENCE_THRESHOLD and COUPLING_CONFIDENCE_MAX imported at top of file

        # If no coefficients learned yet, we're still learning
        if not self.coefficients:
            return "learning"

        # Count coefficients in different confidence ranges
        low_confidence = 0
        medium_confidence = 0
        high_confidence = 0

        for coef in self.coefficients.values():
            if coef.confidence < COUPLING_CONFIDENCE_THRESHOLD:
                low_confidence += 1
            elif coef.confidence < COUPLING_CONFIDENCE_MAX:
                medium_confidence += 1
            else:
                high_confidence += 1

        total = len(self.coefficients)

        # If any coefficients have low confidence, we're still learning
        if low_confidence > 0:
            return "learning"

        # If majority are high confidence, we're stable
        if high_confidence > total / 2:
            return "stable"

        # Otherwise we're validating
        return "validating"

    def get_coefficients_for_zone(self, target_zone: str) -> Dict[str, float]:
        """Get all coupling coefficients where the given zone is the target.

        Args:
            target_zone: Entity ID of the zone to get coefficients for.

        Returns:
            Dict mapping source zone entity IDs to coefficient values.
            Includes both learned and seed-based coefficients.
        """
        result: Dict[str, float] = {}

        # Get learned coefficients
        for (source, target), coef in self.coefficients.items():
            if target == target_zone:
                result[source] = coef.coefficient

        # Fill in seeds for any pairs not already covered
        for (source, target), seed_value in self._seeds.items():
            if target == target_zone and source not in result:
                result[source] = seed_value

        return result

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
                validation_cycles=0,
            )

        # No data available for this pair
        return None

    def calculate_coefficient(
        self, source_zone: str, target_zone: str
    ) -> Optional[CouplingCoefficient]:
        """Calculate the coupling coefficient for a zone pair from observations.

        Uses Bayesian blending to combine seed coefficients (if available) with
        observed transfer rates. The formula is:

            blended = (seed * SEED_WEIGHT + obs_mean * obs_count) / (SEED_WEIGHT + obs_count)

        Confidence is calculated from observation count and penalized by variance.

        Args:
            source_zone: Entity ID of the source zone (the one heating)
            target_zone: Entity ID of the target zone (receiving heat transfer)

        Returns:
            CouplingCoefficient if observations exist, None otherwise.
            Use get_coefficient() to fall back to seed-only values.
        """
        pair = (source_zone, target_zone)

        # Need observations to calculate
        if pair not in self.observations or not self.observations[pair]:
            return None

        observations = self.observations[pair]
        obs_count = len(observations)

        # Calculate transfer rates for all observations
        transfer_rates = [_calculate_transfer_rate(obs) for obs in observations]

        # Filter out zero rates (from invalid observations)
        transfer_rates = [r for r in transfer_rates if r > 0]
        if not transfer_rates:
            return None

        obs_count = len(transfer_rates)
        obs_mean = sum(transfer_rates) / obs_count

        # Check for seed coefficient
        seed = self._seeds.get(pair)

        if seed is not None:
            # Bayesian blend: (seed * weight + obs_mean * count) / (weight + count)
            coefficient = (seed * COUPLING_SEED_WEIGHT + obs_mean * obs_count) / (
                COUPLING_SEED_WEIGHT + obs_count
            )
        else:
            # No seed: pure observation average
            coefficient = obs_mean

        # Cap coefficient at maximum
        coefficient = min(coefficient, COUPLING_MAX_COEFFICIENT)

        # Calculate confidence
        # Base confidence from observation count (asymptotic approach to 1.0)
        # Using formula: count / (count + k) where k controls the rate
        # With k=COUPLING_MIN_OBSERVATIONS, we reach 0.5 at MIN_OBSERVATIONS
        base_confidence = obs_count / (obs_count + COUPLING_MIN_OBSERVATIONS)

        # Reduce confidence based on variance
        if obs_count >= 2:
            mean_rate = sum(transfer_rates) / len(transfer_rates)
            variance = sum((r - mean_rate) ** 2 for r in transfer_rates) / len(
                transfer_rates
            )
            std_dev = variance**0.5

            # Normalize variance penalty by mean (coefficient of variation)
            # High CV = high uncertainty
            if mean_rate > 0:
                cv = std_dev / mean_rate
                # CV of 0.5 (50% std dev) reduces confidence by ~25%
                # CV of 1.0 (100% std dev) reduces confidence by ~50%
                variance_penalty = min(cv * 0.5, 0.5)
                confidence = base_confidence * (1 - variance_penalty)
            else:
                confidence = base_confidence
        else:
            # Single observation: can't calculate variance, use base confidence
            confidence = base_confidence

        # Clamp confidence between threshold and max
        confidence = max(0.0, min(confidence, COUPLING_CONFIDENCE_MAX))

        return CouplingCoefficient(
            source_zone=source_zone,
            target_zone=target_zone,
            coefficient=coefficient,
            confidence=confidence,
            observation_count=obs_count,
            baseline_overshoot=None,
            last_updated=datetime.now(),
            validation_cycles=0,
        )

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

    def record_baseline_overshoot(
        self, pair: Tuple[str, str], overshoot: float
    ) -> None:
        """Record the baseline overshoot for a coefficient before validation.

        This is called when coupling compensation is first applied to a zone pair,
        recording the current overshoot to compare against during validation.

        Args:
            pair: (source_zone, target_zone) tuple
            overshoot: The baseline overshoot value (°C)
        """
        if pair not in self.coefficients:
            return

        coef = self.coefficients[pair]
        # Create a new CouplingCoefficient with updated baseline
        self.coefficients[pair] = CouplingCoefficient(
            source_zone=coef.source_zone,
            target_zone=coef.target_zone,
            coefficient=coef.coefficient,
            confidence=coef.confidence,
            observation_count=coef.observation_count,
            baseline_overshoot=overshoot,
            last_updated=coef.last_updated,
            validation_cycles=0,  # Reset validation cycles when baseline is set
        )

    def add_validation_cycle(
        self, pair: Tuple[str, str], overshoot: float
    ) -> None:
        """Add a validation cycle for a coefficient.

        Increments the validation cycle count for tracking coefficient performance
        after it was changed. Call this after each heating cycle when validation
        is in progress.

        Args:
            pair: (source_zone, target_zone) tuple
            overshoot: The overshoot observed in this cycle (°C)
        """
        if pair not in self.coefficients:
            return

        coef = self.coefficients[pair]
        # Increment validation cycles
        self.coefficients[pair] = CouplingCoefficient(
            source_zone=coef.source_zone,
            target_zone=coef.target_zone,
            coefficient=coef.coefficient,
            confidence=coef.confidence,
            observation_count=coef.observation_count,
            baseline_overshoot=coef.baseline_overshoot,
            last_updated=coef.last_updated,
            validation_cycles=coef.validation_cycles + 1,
        )

    def check_validation(
        self, pair: Tuple[str, str], current_overshoot: float
    ) -> Optional[str]:
        """Check validation status and potentially trigger rollback.

        Compares current overshoot to baseline overshoot. If overshoot has
        increased by more than COUPLING_VALIDATION_DEGRADATION (30%), the
        coefficient is halved (rolled back).

        Args:
            pair: (source_zone, target_zone) tuple
            current_overshoot: The current overshoot value (°C)

        Returns:
            - "rollback" if coefficient was rolled back due to degradation
            - "success" if validation completed successfully
            - None if still collecting cycles or no validation needed
        """
        import logging
        _LOGGER = logging.getLogger(__name__)

        if pair not in self.coefficients:
            return None

        coef = self.coefficients[pair]

        # Skip if no baseline recorded (not in validation mode)
        if coef.baseline_overshoot is None:
            return None

        # Check if overshoot has degraded beyond threshold
        threshold = coef.baseline_overshoot * (1 + COUPLING_VALIDATION_DEGRADATION)

        if current_overshoot > threshold:
            # Rollback: halve the coefficient
            old_coefficient = coef.coefficient
            new_coefficient = coef.coefficient / 2

            _LOGGER.warning(
                "Coupling coefficient rollback triggered for %s -> %s: "
                "overshoot %.3f°C exceeds threshold %.3f°C (baseline %.3f°C). "
                "Coefficient reduced from %.3f to %.3f",
                pair[0], pair[1],
                current_overshoot, threshold, coef.baseline_overshoot,
                old_coefficient, new_coefficient,
            )

            # Create new coefficient with halved value and reset validation
            self.coefficients[pair] = CouplingCoefficient(
                source_zone=coef.source_zone,
                target_zone=coef.target_zone,
                coefficient=new_coefficient,
                confidence=coef.confidence,
                observation_count=coef.observation_count,
                baseline_overshoot=None,  # Clear baseline
                last_updated=datetime.now(),
                validation_cycles=0,  # Reset cycles
            )
            return "rollback"

        # Increment validation cycles
        new_cycles = coef.validation_cycles + 1

        # Check if validation window complete
        if new_cycles >= COUPLING_VALIDATION_CYCLES:
            # Validation successful
            _LOGGER.info(
                "Coupling coefficient validation successful for %s -> %s: "
                "average overshoot %.3f°C within threshold (baseline %.3f°C)",
                pair[0], pair[1], current_overshoot, coef.baseline_overshoot,
            )

            # Clear validation state
            self.coefficients[pair] = CouplingCoefficient(
                source_zone=coef.source_zone,
                target_zone=coef.target_zone,
                coefficient=coef.coefficient,
                confidence=coef.confidence,
                observation_count=coef.observation_count,
                baseline_overshoot=None,  # Clear baseline
                last_updated=coef.last_updated,
                validation_cycles=0,  # Reset cycles
            )
            return "success"

        # Still collecting cycles
        self.coefficients[pair] = CouplingCoefficient(
            source_zone=coef.source_zone,
            target_zone=coef.target_zone,
            coefficient=coef.coefficient,
            confidence=coef.confidence,
            observation_count=coef.observation_count,
            baseline_overshoot=coef.baseline_overshoot,
            last_updated=coef.last_updated,
            validation_cycles=new_cycles,
        )
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the learner state to a dictionary for persistence.

        The dictionary format uses pipe-separated zone pairs as keys for
        observations, coefficients, and seeds to enable JSON serialization.

        Returns:
            Dict containing:
                - observations: Dict of zone pair -> list of observation dicts
                - coefficients: Dict of zone pair -> coefficient dict
                - seeds: Dict of zone pair -> seed value
        """
        # Serialize observations: (source, target) tuple -> "source|target" string key
        observations_dict: Dict[str, List[Dict[str, Any]]] = {}
        for (source, target), obs_list in self.observations.items():
            key = f"{source}|{target}"
            observations_dict[key] = [obs.to_dict() for obs in obs_list]

        # Serialize coefficients
        coefficients_dict: Dict[str, Dict[str, Any]] = {}
        for (source, target), coef in self.coefficients.items():
            key = f"{source}|{target}"
            coefficients_dict[key] = coef.to_dict()

        # Serialize seeds
        seeds_dict: Dict[str, float] = {}
        for (source, target), seed_value in self._seeds.items():
            key = f"{source}|{target}"
            seeds_dict[key] = seed_value

        return {
            "observations": observations_dict,
            "coefficients": coefficients_dict,
            "seeds": seeds_dict,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ThermalCouplingLearner":
        """Restore learner state from a dictionary.

        Implements error recovery per item - invalid observations or coefficients
        are skipped with a warning logged, allowing partial restoration.

        Args:
            data: Dict with observations, coefficients, and seeds keys.

        Returns:
            ThermalCouplingLearner with restored state.
        """
        import logging

        _LOGGER = logging.getLogger(__name__)

        learner = cls()

        # Restore seeds first (simple float values, unlikely to fail)
        seeds_data = data.get("seeds", {})
        for key, seed_value in seeds_data.items():
            try:
                parts = key.split("|")
                if len(parts) == 2:
                    pair = (parts[0], parts[1])
                    learner._seeds[pair] = seed_value
            except Exception as exc:
                _LOGGER.warning("Failed to restore seed %s: %s", key, exc)

        # Restore observations with error recovery
        observations_data = data.get("observations", {})
        for key, obs_list_data in observations_data.items():
            try:
                parts = key.split("|")
                if len(parts) != 2:
                    continue
                pair = (parts[0], parts[1])

                observations: List[CouplingObservation] = []
                for obs_data in obs_list_data:
                    try:
                        obs = CouplingObservation.from_dict(obs_data)
                        observations.append(obs)
                    except Exception as exc:
                        _LOGGER.warning(
                            "Failed to restore observation for %s: %s", key, exc
                        )

                if observations:
                    learner.observations[pair] = observations
            except Exception as exc:
                _LOGGER.warning("Failed to restore observations for %s: %s", key, exc)

        # Restore coefficients with error recovery
        coefficients_data = data.get("coefficients", {})
        for key, coef_data in coefficients_data.items():
            try:
                parts = key.split("|")
                if len(parts) != 2:
                    continue
                pair = (parts[0], parts[1])

                coef = CouplingCoefficient.from_dict(coef_data)
                learner.coefficients[pair] = coef
            except Exception as exc:
                _LOGGER.warning("Failed to restore coefficient for %s: %s", key, exc)

        return learner
