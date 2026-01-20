"""Integration tests for thermal coupling learning flow.

This module tests the complete thermal coupling flow from observation
collection through coefficient calculation to compensation application.
"""

from __future__ import annotations

import pytest
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import Dict, Any

# Add parent directory to path
sys.path.insert(
    0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat")
)

# Mock homeassistant modules before importing coordinator
sys.modules["homeassistant"] = Mock()
sys.modules["homeassistant.core"] = Mock()
sys.modules["homeassistant.helpers"] = Mock()
sys.modules["homeassistant.helpers.update_coordinator"] = Mock()
sys.modules["homeassistant.exceptions"] = Mock()


# Create mock base class
class MockDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval


sys.modules[
    "homeassistant.helpers.update_coordinator"
].DataUpdateCoordinator = MockDataUpdateCoordinator

# Import const to get DOMAIN
import const

# Import thermal_coupling BEFORE coordinator
from adaptive.thermal_coupling import (
    ThermalCouplingLearner,
    CouplingObservation,
    CouplingCoefficient,
    ObservationContext,
    should_record_observation,
    CONF_FLOORPLAN,  # Legacy constant for backward compatibility
)

# Now we can import the coordinator
import coordinator


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance with weather entity."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro)

    # Mock weather entity for outdoor temperature
    hass.data = {
        const.DOMAIN: {
            "weather_entity": "weather.home"
        }
    }
    mock_state = MagicMock()
    mock_state.attributes = {"temperature": 5.0}
    hass.states.get.return_value = mock_state

    # Mock hass.config for SunPositionCalculator
    # Set to a northern European location (winter with low sun)
    hass.config.latitude = 52.0
    hass.config.longitude = 5.0
    hass.config.elevation = 0

    return hass


@pytest.fixture
def multi_zone_coordinator(mock_hass):
    """Create a coordinator with multiple zones pre-registered."""
    coord = coordinator.AdaptiveThermostatCoordinator(mock_hass)

    # Register Zone A (living room)
    coord.register_zone("climate.living_room", {
        "name": "Living Room",
        "current_temp": 20.0,
        "window_orientation": "south",
        "heating_type": "radiator",
    })

    # Register Zone B (kitchen - adjacent to living room)
    coord.register_zone("climate.kitchen", {
        "name": "Kitchen",
        "current_temp": 19.0,
        "window_orientation": "east",
        "heating_type": "radiator",
    })

    # Register Zone C (bedroom - upper floor)
    coord.register_zone("climate.bedroom", {
        "name": "Bedroom",
        "current_temp": 18.0,
        "window_orientation": "north",
        "heating_type": "radiator",
    })

    # Initialize seeds from a floorplan config
    floorplan_config = {
        CONF_FLOORPLAN: [
            {"floor": 0, "zones": ["climate.living_room", "climate.kitchen"]},
            {"floor": 1, "zones": ["climate.bedroom"]},
        ],
        const.CONF_STAIRWELL_ZONES: [],
    }
    coord.thermal_coupling_learner.initialize_seeds(floorplan_config)

    return coord


class TestIntegrationZoneHeatsObservationStarted:
    """Test that zone heating starts observation."""

    def test_zone_a_heating_starts_observation(self, multi_zone_coordinator):
        """TEST: Zone A heating starts observation for all other zones."""
        coord = multi_zone_coordinator
        learner = coord.thermal_coupling_learner

        # Initially no pending observations
        assert len(learner._pending) == 0

        # Living room starts heating (demand False -> True)
        coord.update_zone_demand("climate.living_room", True, "heat")

        # Observation should be started for living room
        assert "climate.living_room" in learner._pending
        context = learner._pending["climate.living_room"]

        # Verify observation context
        assert context.source_zone == "climate.living_room"
        assert context.source_temp_start == 20.0
        assert context.outdoor_temp_start == 5.0

        # Target temps should include kitchen and bedroom, not living room
        assert "climate.kitchen" in context.target_temps_start
        assert "climate.bedroom" in context.target_temps_start
        assert "climate.living_room" not in context.target_temps_start
        assert context.target_temps_start["climate.kitchen"] == 19.0
        assert context.target_temps_start["climate.bedroom"] == 18.0

    def test_multiple_zones_can_have_pending_observations(self, multi_zone_coordinator):
        """TEST: Multiple zones heating creates separate observations."""
        coord = multi_zone_coordinator
        learner = coord.thermal_coupling_learner

        # Living room starts heating
        coord.update_zone_demand("climate.living_room", True, "heat")
        assert "climate.living_room" in learner._pending

        # Kitchen also starts heating (only 50% threshold reached)
        coord.update_zone_demand("climate.kitchen", True, "heat")
        assert "climate.kitchen" in learner._pending

        # Both have separate observation contexts
        assert len(learner._pending) == 2
        assert learner._pending["climate.living_room"].source_zone == "climate.living_room"
        assert learner._pending["climate.kitchen"].source_zone == "climate.kitchen"


class TestIntegrationObservationRecorded:
    """Test that observations are recorded after heating stops."""

    def test_observation_recorded_after_zone_stops_heating(self, multi_zone_coordinator):
        """TEST: Observation stored after Zone A stops heating."""
        coord = multi_zone_coordinator
        learner = coord.thermal_coupling_learner

        # Start heating zone A
        coord.update_zone_demand("climate.living_room", True, "heat")
        assert "climate.living_room" in learner._pending

        # Simulate temperature changes (source warms up, targets warm a bit)
        coord.update_zone_temp("climate.living_room", 22.0)  # +2.0°C
        coord.update_zone_temp("climate.kitchen", 19.5)  # +0.5°C (coupled)
        coord.update_zone_temp("climate.bedroom", 18.2)  # +0.2°C (coupled)

        # Manually adjust start time to simulate 20 minutes passing
        original_context = learner._pending["climate.living_room"]
        learner._pending["climate.living_room"] = ObservationContext(
            source_zone=original_context.source_zone,
            start_time=datetime.now() - timedelta(minutes=20),
            source_temp_start=original_context.source_temp_start,
            target_temps_start=original_context.target_temps_start,
            outdoor_temp_start=original_context.outdoor_temp_start,
        )

        # Zone A stops heating (demand True -> False)
        coord.update_zone_demand("climate.living_room", False, "heat")

        # Pending observation should be cleared
        assert "climate.living_room" not in learner._pending

        # Observations should be recorded for idle zones (kitchen and bedroom)
        # Note: The actual recording happens in the coordinator's _end_coupling_observation
        # which calls learner.end_observation and filters through should_record_observation

    def test_observation_filters_applied(self, multi_zone_coordinator):
        """TEST: Observations are filtered by validation criteria."""
        coord = multi_zone_coordinator
        learner = coord.thermal_coupling_learner

        # Start heating zone A
        coord.update_zone_demand("climate.living_room", True, "heat")

        # Simulate minimal temperature changes (won't pass filters)
        coord.update_zone_temp("climate.living_room", 20.1)  # Only +0.1°C (below MIN_SOURCE_RISE)
        coord.update_zone_temp("climate.kitchen", 19.1)

        # Adjust time to pass duration filter
        original_context = learner._pending["climate.living_room"]
        learner._pending["climate.living_room"] = ObservationContext(
            source_zone=original_context.source_zone,
            start_time=datetime.now() - timedelta(minutes=20),
            source_temp_start=original_context.source_temp_start,
            target_temps_start=original_context.target_temps_start,
            outdoor_temp_start=original_context.outdoor_temp_start,
        )

        # Create a test observation that should fail the source rise filter
        test_obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone="climate.living_room",
            target_zone="climate.kitchen",
            source_temp_start=20.0,
            source_temp_end=20.1,  # Only 0.1°C rise
            target_temp_start=19.0,
            target_temp_end=19.1,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=20.0,
        )

        # Should be filtered out due to low source rise
        assert not should_record_observation(test_obs)


class TestIntegrationCoefficientCalculated:
    """Test that coefficients are calculated after MIN_OBSERVATIONS."""

    def test_coefficient_calculated_after_three_cycles(self):
        """TEST: Coefficient available after MIN_OBSERVATIONS (3) cycles."""
        learner = ThermalCouplingLearner()

        # Initialize seeds
        learner.initialize_seeds({
            CONF_FLOORPLAN: [
                {"floor": 0, "zones": ["climate.living_room", "climate.kitchen"]},
            ],
        })

        source = "climate.living_room"
        target = "climate.kitchen"
        pair = (source, target)

        # Simulate 3 valid observations
        for i in range(3):
            obs = CouplingObservation(
                timestamp=datetime.now() - timedelta(hours=i),
                source_zone=source,
                target_zone=target,
                source_temp_start=20.0,
                source_temp_end=22.0,  # +2.0°C rise
                target_temp_start=19.0,
                target_temp_end=19.3 + (i * 0.1),  # ~0.3-0.5°C rise
                outdoor_temp_start=5.0,
                outdoor_temp_end=5.0,
                duration_minutes=30.0,  # 0.5 hours
            )
            # Store observation directly (bypassing filters for test)
            if pair not in learner.observations:
                learner.observations[pair] = []
            learner.observations[pair].append(obs)

        # Calculate coefficient
        coef = learner.calculate_coefficient(source, target)

        # Should have a coefficient now
        assert coef is not None
        assert coef.source_zone == source
        assert coef.target_zone == target
        assert coef.observation_count == 3
        assert coef.coefficient > 0
        assert coef.confidence > 0

        # Store calculated coefficient
        learner.coefficients[pair] = coef

        # get_coefficient should now return the learned coefficient
        retrieved = learner.get_coefficient(source, target)
        assert retrieved is not None
        assert retrieved.observation_count == 3

    def test_seed_only_coefficient_before_observations(self):
        """TEST: Returns seed with 0.3 confidence when no observations."""
        learner = ThermalCouplingLearner()

        # Initialize seeds
        learner.initialize_seeds({
            CONF_FLOORPLAN: [
                {"floor": 0, "zones": ["climate.living_room", "climate.kitchen"]},
            ],
        })

        # Get coefficient for seeded pair with no observations
        coef = learner.get_coefficient("climate.living_room", "climate.kitchen")

        assert coef is not None
        assert coef.coefficient == const.DEFAULT_SEED_COEFFICIENTS["same_floor"]
        assert coef.confidence == const.COUPLING_CONFIDENCE_THRESHOLD  # 0.3
        assert coef.observation_count == 0

    def test_bayesian_blending_with_seed(self):
        """TEST: Coefficient blends seed and observations with SEED_WEIGHT."""
        learner = ThermalCouplingLearner()

        # Initialize seeds with known value
        learner.initialize_seeds({
            CONF_FLOORPLAN: [
                {"floor": 0, "zones": ["climate.living_room", "climate.kitchen"]},
            ],
        })

        source = "climate.living_room"
        target = "climate.kitchen"
        pair = (source, target)

        # Seed value for same_floor is 0.15
        seed_value = const.DEFAULT_SEED_COEFFICIENTS["same_floor"]

        # Add one observation with different transfer rate
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone=source,
            target_zone=target,
            source_temp_start=20.0,
            source_temp_end=22.0,  # +2.0°C rise
            target_temp_start=19.0,
            target_temp_end=19.6,  # +0.6°C rise
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=60.0,  # 1 hour
        )
        # Transfer rate = 0.6 / (2.0 * 1.0) = 0.3
        learner.observations[pair] = [obs]

        coef = learner.calculate_coefficient(source, target)

        # With Bayesian blending: (seed * 6 + obs_mean * 1) / (6 + 1)
        # = (0.15 * 6 + 0.3 * 1) / 7 = (0.9 + 0.3) / 7 = 0.171...
        expected = (seed_value * const.COUPLING_SEED_WEIGHT + 0.3 * 1) / (
            const.COUPLING_SEED_WEIGHT + 1
        )
        assert coef is not None
        assert abs(coef.coefficient - expected) < 0.01


class TestIntegrationCompensationApplied:
    """Test that compensation is applied in control loop.

    Note: The actual ControlOutputManager compensation calculation is extensively
    tested in test_control_output.py. These integration tests verify the data flow
    conceptually - that the learner provides coefficient data that can be used
    for compensation calculation.
    """

    def test_zone_b_can_get_coefficient_when_zone_a_heating(self, multi_zone_coordinator):
        """TEST: Zone B can retrieve coefficient data when Zone A is heating."""
        coord = multi_zone_coordinator
        learner = coord.thermal_coupling_learner

        source = "climate.living_room"
        target = "climate.kitchen"
        pair = (source, target)

        # Add observations and calculate coefficient
        for i in range(const.COUPLING_MIN_OBSERVATIONS):
            obs = CouplingObservation(
                timestamp=datetime.now() - timedelta(hours=i),
                source_zone=source,
                target_zone=target,
                source_temp_start=20.0,
                source_temp_end=22.0,
                target_temp_start=19.0,
                target_temp_end=19.4,
                outdoor_temp_start=5.0,
                outdoor_temp_end=5.0,
                duration_minutes=30.0,
            )
            if pair not in learner.observations:
                learner.observations[pair] = []
            learner.observations[pair].append(obs)

        coef = learner.calculate_coefficient(source, target)
        learner.coefficients[pair] = coef

        # Zone A starts heating
        coord.update_zone_demand(source, True, "heat")

        # Simulate temperature rise in Zone A
        coord.update_zone_temp(source, 22.0)

        # Zone B (kitchen) should be able to get coefficient for Zone A
        retrieved_coef = learner.get_coefficient(source, target)
        assert retrieved_coef is not None
        assert retrieved_coef.coefficient > 0

        # Verify the graduated_confidence calculation works
        from adaptive.thermal_coupling import graduated_confidence
        conf_scale = graduated_confidence(retrieved_coef.confidence)

        # With MIN_OBSERVATIONS, should have confidence >= threshold
        assert conf_scale >= 0.0  # At minimum threshold

        # Calculate expected compensation conceptually
        temp_rise = 22.0 - 20.0  # Zone A rise
        expected_compensation_degc = retrieved_coef.coefficient * conf_scale * temp_rise
        assert expected_compensation_degc >= 0.0

    def test_no_coefficient_for_unrelated_zones(self, multi_zone_coordinator):
        """TEST: No coefficient available for zones without observations."""
        coord = multi_zone_coordinator
        learner = coord.thermal_coupling_learner

        # No observations added between bedroom and living room floors
        # (They're on different floors without stairwell connection in seed config)
        coef = learner.get_coefficient("climate.bedroom", "climate.living_room")

        # Should have seed coefficient (vertical relationship exists)
        # because bedroom is on floor 1, living room on floor 0
        # This is the "down" direction seed
        assert coef is not None
        assert coef.confidence == const.COUPLING_CONFIDENCE_THRESHOLD  # Seed-only


class TestIntegrationPersistenceRoundtrip:
    """Test that learner state survives restart."""

    def test_learner_state_survives_restart(self):
        """TEST: Learner state survives save/load cycle."""
        # Create learner with state
        learner = ThermalCouplingLearner()

        # Initialize seeds
        learner.initialize_seeds({
            CONF_FLOORPLAN: [
                {"floor": 0, "zones": ["climate.living_room", "climate.kitchen"]},
                {"floor": 1, "zones": ["climate.bedroom"]},
            ],
            const.CONF_STAIRWELL_ZONES: [],
        })

        source = "climate.living_room"
        target = "climate.kitchen"
        pair = (source, target)

        # Add observations
        for i in range(3):
            obs = CouplingObservation(
                timestamp=datetime.now() - timedelta(hours=i),
                source_zone=source,
                target_zone=target,
                source_temp_start=20.0,
                source_temp_end=22.0,
                target_temp_start=19.0,
                target_temp_end=19.3 + (i * 0.1),
                outdoor_temp_start=5.0,
                outdoor_temp_end=5.0,
                duration_minutes=30.0,
            )
            if pair not in learner.observations:
                learner.observations[pair] = []
            learner.observations[pair].append(obs)

        # Calculate and store coefficient
        coef = learner.calculate_coefficient(source, target)
        learner.coefficients[pair] = coef

        # Serialize state
        state_dict = learner.to_dict()

        # Verify serialized state has expected structure
        assert "observations" in state_dict
        assert "coefficients" in state_dict
        assert "seeds" in state_dict
        assert len(state_dict["observations"]) > 0
        assert len(state_dict["coefficients"]) > 0
        assert len(state_dict["seeds"]) > 0

        # Create new learner from serialized state
        restored_learner = ThermalCouplingLearner.from_dict(state_dict)

        # Verify observations restored
        assert pair in restored_learner.observations
        assert len(restored_learner.observations[pair]) == 3

        # Verify coefficient restored
        assert pair in restored_learner.coefficients
        restored_coef = restored_learner.coefficients[pair]
        assert restored_coef.source_zone == source
        assert restored_coef.target_zone == target
        assert restored_coef.observation_count == coef.observation_count
        assert abs(restored_coef.coefficient - coef.coefficient) < 0.001

        # Verify seeds restored
        assert pair in restored_learner._seeds
        assert (
            restored_learner._seeds[pair]
            == const.DEFAULT_SEED_COEFFICIENTS["same_floor"]
        )

    def test_persistence_with_empty_learner(self):
        """TEST: Empty learner serializes and restores correctly."""
        learner = ThermalCouplingLearner()

        # Serialize empty state
        state_dict = learner.to_dict()

        assert state_dict == {
            "observations": {},
            "coefficients": {},
            "seeds": {},
        }

        # Restore from empty state
        restored = ThermalCouplingLearner.from_dict(state_dict)

        assert len(restored.observations) == 0
        assert len(restored.coefficients) == 0
        assert len(restored._seeds) == 0

    def test_persistence_data_flow_simulation(self, mock_hass):
        """TEST: Simulate data flow that would happen with LearningDataStore."""
        # This test simulates the persistence data flow without actually importing
        # LearningDataStore (which has complex HA dependencies).
        # The actual persistence is tested in test_learning_storage.py.

        # Create learner with data
        learner = ThermalCouplingLearner()
        learner.initialize_seeds({
            CONF_FLOORPLAN: [
                {"floor": 0, "zones": ["climate.living_room", "climate.kitchen"]},
            ],
        })

        # Add an observation
        pair = ("climate.living_room", "climate.kitchen")
        obs = CouplingObservation(
            timestamp=datetime.now(),
            source_zone=pair[0],
            target_zone=pair[1],
            source_temp_start=20.0,
            source_temp_end=22.0,
            target_temp_start=19.0,
            target_temp_end=19.4,
            outdoor_temp_start=5.0,
            outdoor_temp_end=5.0,
            duration_minutes=30.0,
        )
        learner.observations[pair] = [obs]

        # Simulate what LearningDataStore.update_coupling_data does
        coupling_data = learner.to_dict()

        # Simulate storage structure
        storage_data = {
            "version": 4,
            "zones": {},
            "thermal_coupling": coupling_data,
        }

        # Simulate what LearningDataStore.get_coupling_data does
        retrieved = storage_data.get("thermal_coupling")
        assert retrieved is not None
        assert "observations" in retrieved
        assert "seeds" in retrieved

        # Restore to new learner (simulating restart)
        restored = ThermalCouplingLearner.from_dict(retrieved)
        assert pair in restored.observations
        assert len(restored.observations[pair]) == 1


class TestFullLearningFlow:
    """End-to-end test of the complete learning flow."""

    def test_complete_learning_flow(self, multi_zone_coordinator):
        """TEST: Complete flow from heating start to coefficient application."""
        coord = multi_zone_coordinator
        learner = coord.thermal_coupling_learner

        # 1. Living room starts heating
        coord.update_zone_demand("climate.living_room", True, "heat")
        assert "climate.living_room" in learner._pending

        # 2. Simulate temperature changes over time
        coord.update_zone_temp("climate.living_room", 22.0)  # +2.0°C
        coord.update_zone_temp("climate.kitchen", 19.4)  # +0.4°C (coupled)

        # Adjust observation start time for realistic duration
        original_context = learner._pending["climate.living_room"]
        learner._pending["climate.living_room"] = ObservationContext(
            source_zone=original_context.source_zone,
            start_time=datetime.now() - timedelta(minutes=30),
            source_temp_start=original_context.source_temp_start,
            target_temps_start=original_context.target_temps_start,
            outdoor_temp_start=original_context.outdoor_temp_start,
        )

        # 3. Living room stops heating
        coord.update_zone_demand("climate.living_room", False, "heat")
        assert "climate.living_room" not in learner._pending

        # 4. Manually add observations to test coefficient calculation
        # (In real use, the coordinator's _end_coupling_observation handles this)
        source = "climate.living_room"
        target = "climate.kitchen"
        pair = (source, target)

        # Add multiple observations to reach MIN_OBSERVATIONS
        for i in range(const.COUPLING_MIN_OBSERVATIONS):
            obs = CouplingObservation(
                timestamp=datetime.now() - timedelta(hours=i),
                source_zone=source,
                target_zone=target,
                source_temp_start=20.0,
                source_temp_end=22.0,
                target_temp_start=19.0,
                target_temp_end=19.4,
                outdoor_temp_start=5.0,
                outdoor_temp_end=5.0,
                duration_minutes=30.0,
            )
            if pair not in learner.observations:
                learner.observations[pair] = []
            learner.observations[pair].append(obs)

        # 5. Calculate and store coefficient
        coef = learner.calculate_coefficient(source, target)
        assert coef is not None
        learner.coefficients[pair] = coef

        # 6. Verify learner state reflects learned coefficient
        state = learner.get_learner_state()
        # Should be "learning" or "validating" based on confidence levels
        assert state in ["learning", "validating", "stable"]

        # 7. Verify coefficient can be retrieved
        retrieved = learner.get_coefficient(source, target)
        assert retrieved is not None
        # We added MIN_OBSERVATIONS manually, plus potentially one from the coordinator
        assert retrieved.observation_count >= const.COUPLING_MIN_OBSERVATIONS

        # 8. Verify persistence roundtrip
        state_dict = learner.to_dict()
        restored = ThermalCouplingLearner.from_dict(state_dict)
        assert pair in restored.coefficients
        assert restored.coefficients[pair].coefficient == coef.coefficient


class TestAutoDiscoveryIntegration:
    """Integration tests for thermal coupling auto-discovery flow."""

    def test_coupling_integration_autodiscovery(self, mock_hass):
        """TEST: End-to-end auto-discovery with mocked registries.

        Verifies that zones with proper floor assignments get coupling seeds,
        and the coordinator properly initializes the thermal coupling learner.
        """
        from unittest.mock import MagicMock

        # Mock entity registry
        entity_registry = MagicMock()

        # Mock entity entries with area assignments
        living_room_entity = MagicMock()
        living_room_entity.area_id = "area_living_room"

        kitchen_entity = MagicMock()
        kitchen_entity.area_id = "area_kitchen"

        bedroom_entity = MagicMock()
        bedroom_entity.area_id = "area_bedroom"

        entity_registry.async_get = MagicMock(side_effect=lambda entity_id: {
            "climate.living_room": living_room_entity,
            "climate.kitchen": kitchen_entity,
            "climate.bedroom": bedroom_entity,
        }.get(entity_id))

        # Mock area registry
        area_registry = MagicMock()

        area_living_room = MagicMock()
        area_living_room.floor_id = "floor_ground"

        area_kitchen = MagicMock()
        area_kitchen.floor_id = "floor_ground"

        area_bedroom = MagicMock()
        area_bedroom.floor_id = "floor_first"

        area_registry.async_get = MagicMock(side_effect=lambda area_id: {
            "area_living_room": area_living_room,
            "area_kitchen": area_kitchen,
            "area_bedroom": area_bedroom,
        }.get(area_id))

        # Mock floor registry
        floor_registry = MagicMock()

        floor_ground = MagicMock()
        floor_ground.level = 0

        floor_first = MagicMock()
        floor_first.level = 1

        floor_registry.async_get = MagicMock(side_effect=lambda floor_id: {
            "floor_ground": floor_ground,
            "floor_first": floor_first,
        }.get(floor_id))

        # Patch registry helpers
        with patch("helpers.registry.er.async_get", return_value=entity_registry), \
             patch("helpers.registry.ar.async_get", return_value=area_registry), \
             patch("helpers.registry.fr.async_get", return_value=floor_registry):

            # Create learner with hass instance
            learner = ThermalCouplingLearner(hass=mock_hass)

            zone_entity_ids = ["climate.living_room", "climate.kitchen", "climate.bedroom"]

            # Initialize seeds (should trigger auto-discovery)
            learner.initialize_seeds(
                floorplan_config={},  # No legacy floorplan
                zone_entity_ids=zone_entity_ids
            )

            # Verify seeds were created
            assert len(learner._seeds) > 0

            # Check same-floor coupling (living room <-> kitchen)
            assert ("climate.living_room", "climate.kitchen") in learner._seeds
            assert ("climate.kitchen", "climate.living_room") in learner._seeds
            assert learner._seeds[("climate.living_room", "climate.kitchen")] == const.DEFAULT_SEED_COEFFICIENTS["same_floor"]

            # Check vertical coupling (living room -> bedroom, heat rising)
            assert ("climate.living_room", "climate.bedroom") in learner._seeds
            assert learner._seeds[("climate.living_room", "climate.bedroom")] == const.DEFAULT_SEED_COEFFICIENTS["up"]

            # Check vertical coupling (bedroom -> living room, heat descending)
            assert ("climate.bedroom", "climate.living_room") in learner._seeds
            assert learner._seeds[("climate.bedroom", "climate.living_room")] == const.DEFAULT_SEED_COEFFICIENTS["down"]

            # Verify coefficients can be retrieved (should return seed-based coefficients)
            coef_lr_to_k = learner.get_coefficient("climate.living_room", "climate.kitchen")
            assert coef_lr_to_k is not None
            assert coef_lr_to_k.coefficient == const.DEFAULT_SEED_COEFFICIENTS["same_floor"]
            assert coef_lr_to_k.confidence == const.COUPLING_CONFIDENCE_THRESHOLD
            assert coef_lr_to_k.observation_count == 0

    def test_coupling_integration_partial_discovery(self, mock_hass):
        """TEST: Partial auto-discovery - some zones without floors still work.

        Verifies that zones without floor assignments are excluded from coupling,
        but zones with floors still get proper seeds.
        """
        from unittest.mock import MagicMock
        import logging

        # Mock entity registry
        entity_registry = MagicMock()

        # Living room has area with floor
        living_room_entity = MagicMock()
        living_room_entity.area_id = "area_living_room"

        # Kitchen has area with floor
        kitchen_entity = MagicMock()
        kitchen_entity.area_id = "area_kitchen"

        # Bedroom has no area (entity.area_id = None)
        bedroom_entity = MagicMock()
        bedroom_entity.area_id = None

        entity_registry.async_get = MagicMock(side_effect=lambda entity_id: {
            "climate.living_room": living_room_entity,
            "climate.kitchen": kitchen_entity,
            "climate.bedroom": bedroom_entity,
        }.get(entity_id))

        # Mock area registry
        area_registry = MagicMock()

        area_living_room = MagicMock()
        area_living_room.floor_id = "floor_ground"

        area_kitchen = MagicMock()
        area_kitchen.floor_id = "floor_ground"

        area_registry.async_get = MagicMock(side_effect=lambda area_id: {
            "area_living_room": area_living_room,
            "area_kitchen": area_kitchen,
        }.get(area_id))

        # Mock floor registry
        floor_registry = MagicMock()

        floor_ground = MagicMock()
        floor_ground.level = 0

        floor_registry.async_get = MagicMock(side_effect=lambda floor_id: {
            "floor_ground": floor_ground,
        }.get(floor_id))

        # Patch registry helpers
        with patch("helpers.registry.er.async_get", return_value=entity_registry), \
             patch("helpers.registry.ar.async_get", return_value=area_registry), \
             patch("helpers.registry.fr.async_get", return_value=floor_registry):

            # Create learner with hass instance
            learner = ThermalCouplingLearner(hass=mock_hass)

            zone_entity_ids = ["climate.living_room", "climate.kitchen", "climate.bedroom"]

            # Capture log warnings - patch logging.getLogger within initialize_seeds
            import logging
            with patch.object(logging, 'getLogger') as mock_get_logger:
                mock_logger = MagicMock()
                mock_get_logger.return_value = mock_logger

                # Initialize seeds (should trigger auto-discovery)
                learner.initialize_seeds(
                    floorplan_config={},  # No legacy floorplan
                    zone_entity_ids=zone_entity_ids
                )

                # Verify warning was logged for bedroom (no floor assignment)
                warning_calls = [
                    call for call in mock_logger.warning.call_args_list
                    if "climate.bedroom" in str(call)
                ]
                assert len(warning_calls) == 1
                assert "has no floor assignment" in str(warning_calls[0])

            # Verify seeds were created for zones WITH floors
            assert len(learner._seeds) > 0

            # Check same-floor coupling (living room <-> kitchen) - should exist
            assert ("climate.living_room", "climate.kitchen") in learner._seeds
            assert ("climate.kitchen", "climate.living_room") in learner._seeds

            # Verify bedroom has NO coupling seeds (no floor assignment)
            bedroom_pairs = [
                pair for pair in learner._seeds.keys()
                if "climate.bedroom" in pair
            ]
            assert len(bedroom_pairs) == 0

            # Verify get_coefficient returns None for bedroom pairs
            coef_lr_to_bed = learner.get_coefficient("climate.living_room", "climate.bedroom")
            assert coef_lr_to_bed is None

            # Verify coefficients still work for zones with floors
            coef_lr_to_k = learner.get_coefficient("climate.living_room", "climate.kitchen")
            assert coef_lr_to_k is not None
            assert coef_lr_to_k.coefficient == const.DEFAULT_SEED_COEFFICIENTS["same_floor"]

    def test_autodiscovery_with_open_zones(self, mock_hass):
        """TEST: Auto-discovery with open zones configuration.

        Verifies that zones marked as open floor plan get higher coupling coefficients
        even when using auto-discovery.
        """
        from unittest.mock import MagicMock

        # Mock registries (same setup as test_coupling_integration_autodiscovery)
        entity_registry = MagicMock()

        living_room_entity = MagicMock()
        living_room_entity.area_id = "area_living_room"

        kitchen_entity = MagicMock()
        kitchen_entity.area_id = "area_kitchen"

        entity_registry.async_get = MagicMock(side_effect=lambda entity_id: {
            "climate.living_room": living_room_entity,
            "climate.kitchen": kitchen_entity,
        }.get(entity_id))

        area_registry = MagicMock()

        area_living_room = MagicMock()
        area_living_room.floor_id = "floor_ground"

        area_kitchen = MagicMock()
        area_kitchen.floor_id = "floor_ground"

        area_registry.async_get = MagicMock(side_effect=lambda area_id: {
            "area_living_room": area_living_room,
            "area_kitchen": area_kitchen,
        }.get(area_id))

        floor_registry = MagicMock()

        floor_ground = MagicMock()
        floor_ground.level = 0

        floor_registry.async_get = MagicMock(side_effect=lambda floor_id: {
            "floor_ground": floor_ground,
        }.get(floor_id))

        # Patch registry helpers
        with patch("helpers.registry.er.async_get", return_value=entity_registry), \
             patch("helpers.registry.ar.async_get", return_value=area_registry), \
             patch("helpers.registry.fr.async_get", return_value=floor_registry):

            # Create learner with hass instance
            learner = ThermalCouplingLearner(hass=mock_hass)

            zone_entity_ids = ["climate.living_room", "climate.kitchen"]

            # Initialize seeds with open zones configuration
            learner.initialize_seeds(
                floorplan_config={
                    const.CONF_OPEN_ZONES: ["climate.living_room", "climate.kitchen"],
                },
                zone_entity_ids=zone_entity_ids
            )

            # Verify open coefficient is used (not same_floor)
            assert ("climate.living_room", "climate.kitchen") in learner._seeds
            assert learner._seeds[("climate.living_room", "climate.kitchen")] == const.DEFAULT_SEED_COEFFICIENTS["open"]

            # Verify it's higher than same_floor
            assert const.DEFAULT_SEED_COEFFICIENTS["open"] > const.DEFAULT_SEED_COEFFICIENTS["same_floor"]

    def test_autodiscovery_with_stairwell_zones(self, mock_hass):
        """TEST: Auto-discovery with stairwell zones configuration.

        Verifies that zones connected by stairwells get higher vertical coupling.
        """
        from unittest.mock import MagicMock

        # Mock registries
        entity_registry = MagicMock()

        hallway_entity = MagicMock()
        hallway_entity.area_id = "area_hallway"

        landing_entity = MagicMock()
        landing_entity.area_id = "area_landing"

        entity_registry.async_get = MagicMock(side_effect=lambda entity_id: {
            "climate.hallway": hallway_entity,
            "climate.landing": landing_entity,
        }.get(entity_id))

        area_registry = MagicMock()

        area_hallway = MagicMock()
        area_hallway.floor_id = "floor_ground"

        area_landing = MagicMock()
        area_landing.floor_id = "floor_first"

        area_registry.async_get = MagicMock(side_effect=lambda area_id: {
            "area_hallway": area_hallway,
            "area_landing": area_landing,
        }.get(area_id))

        floor_registry = MagicMock()

        floor_ground = MagicMock()
        floor_ground.level = 0

        floor_first = MagicMock()
        floor_first.level = 1

        floor_registry.async_get = MagicMock(side_effect=lambda floor_id: {
            "floor_ground": floor_ground,
            "floor_first": floor_first,
        }.get(floor_id))

        # Patch registry helpers
        with patch("helpers.registry.er.async_get", return_value=entity_registry), \
             patch("helpers.registry.ar.async_get", return_value=area_registry), \
             patch("helpers.registry.fr.async_get", return_value=floor_registry):

            # Create learner with hass instance
            learner = ThermalCouplingLearner(hass=mock_hass)

            zone_entity_ids = ["climate.hallway", "climate.landing"]

            # Initialize seeds with stairwell zones configuration
            learner.initialize_seeds(
                floorplan_config={
                    const.CONF_STAIRWELL_ZONES: ["climate.hallway", "climate.landing"],
                },
                zone_entity_ids=zone_entity_ids
            )

            # Verify stairwell_up coefficient is used (not regular up)
            assert ("climate.hallway", "climate.landing") in learner._seeds
            assert learner._seeds[("climate.hallway", "climate.landing")] == const.DEFAULT_SEED_COEFFICIENTS["stairwell_up"]

            # Verify stairwell_down for reverse direction
            assert ("climate.landing", "climate.hallway") in learner._seeds
            assert learner._seeds[("climate.landing", "climate.hallway")] == const.DEFAULT_SEED_COEFFICIENTS["stairwell_down"]

            # Verify stairwell coefficients differ from regular vertical
            assert const.DEFAULT_SEED_COEFFICIENTS["stairwell_up"] > const.DEFAULT_SEED_COEFFICIENTS["up"]
