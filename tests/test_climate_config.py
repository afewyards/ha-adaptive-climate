"""Tests for thermal coupling configuration parsing in climate.py (Story 7.1)."""
import pytest

# Try to import voluptuous, skip tests if not available
try:
    import voluptuous as vol
    HAS_VOLUPTUOUS = True
except ImportError:
    HAS_VOLUPTUOUS = False
    vol = None

pytestmark = pytest.mark.skipif(not HAS_VOLUPTUOUS, reason="voluptuous not installed")

# Import constants directly (doesn't require homeassistant)
from custom_components.adaptive_thermostat.const import (
    CONF_THERMAL_COUPLING,
    CONF_STAIRWELL_ZONES,
    CONF_SEED_COEFFICIENTS,
    DEFAULT_SEED_COEFFICIENTS,
)

# Legacy constant for backward compatibility during migration to auto-discovery
CONF_FLOORPLAN = "floorplan"


# =============================================================================
# Helper to create thermal coupling schema for testing
# =============================================================================

def create_thermal_coupling_schema():
    """Create the thermal_coupling schema matching climate.py PLATFORM_SCHEMA."""
    # Mock cv.entity_ids for testing
    def mock_entity_ids(value):
        """Mock entity_ids validator."""
        if not isinstance(value, list):
            raise vol.Invalid("entity_ids must be a list")
        for entity_id in value:
            if not isinstance(entity_id, str) or "." not in entity_id:
                raise vol.Invalid(f"Invalid entity_id: {entity_id}")
        return value

    # Mock cv.ensure_list for testing
    def mock_ensure_list(value):
        """Mock ensure_list validator."""
        if isinstance(value, list):
            return value
        return [value]

    return vol.Schema({
        vol.Optional('enabled', default=True): vol.Coerce(bool),
        vol.Optional(CONF_FLOORPLAN): vol.All(
            mock_ensure_list,
            [vol.Schema({
                vol.Required('floor'): vol.Coerce(int),
                vol.Required('zones'): mock_entity_ids,
                vol.Optional('open'): mock_entity_ids,
            })]
        ),
        vol.Optional(CONF_STAIRWELL_ZONES): mock_entity_ids,
        vol.Optional(CONF_SEED_COEFFICIENTS): vol.Schema({
            vol.Optional('same_floor'): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional('up'): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional('down'): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional('open'): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional('stairwell_up'): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional('stairwell_down'): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        }),
    })


# =============================================================================
# Thermal Coupling Configuration Tests (Story 7.1)
# =============================================================================


def test_config_thermal_coupling_enabled():
    """Test that thermal_coupling.enabled flag is parsed correctly (default true)."""
    schema = create_thermal_coupling_schema()

    # Test with enabled=True
    config_enabled = {"enabled": True}
    validated = schema(config_enabled)
    assert validated["enabled"] is True

    # Test with enabled=False
    config_disabled = {"enabled": False}
    validated = schema(config_disabled)
    assert validated["enabled"] is False

    # Test default (should be True)
    config_default = {}
    validated = schema(config_default)
    assert validated["enabled"] is True


def test_config_floorplan_parsed():
    """Test that floorplan with floors and zones is parsed correctly."""
    schema = create_thermal_coupling_schema()

    config = {
        CONF_FLOORPLAN: [
            {
                "floor": 0,
                "zones": ["climate.living_room", "climate.kitchen"],
            },
            {
                "floor": 1,
                "zones": ["climate.bedroom", "climate.bathroom"],
                "open": ["climate.bedroom", "climate.bathroom"],
            }
        ]
    }

    validated = schema(config)
    floorplan = validated[CONF_FLOORPLAN]

    # Verify floor 0
    assert floorplan[0]["floor"] == 0
    assert floorplan[0]["zones"] == ["climate.living_room", "climate.kitchen"]
    assert "open" not in floorplan[0]

    # Verify floor 1
    assert floorplan[1]["floor"] == 1
    assert floorplan[1]["zones"] == ["climate.bedroom", "climate.bathroom"]
    assert floorplan[1]["open"] == ["climate.bedroom", "climate.bathroom"]


def test_config_stairwell_zones_parsed():
    """Test that stairwell_zones list is parsed correctly."""
    schema = create_thermal_coupling_schema()

    config = {
        CONF_STAIRWELL_ZONES: ["climate.stairwell", "climate.hallway"]
    }

    validated = schema(config)
    stairwell_zones = validated[CONF_STAIRWELL_ZONES]

    assert stairwell_zones == ["climate.stairwell", "climate.hallway"]


def test_config_seed_coefficients_override():
    """Test that custom seed coefficients override defaults."""
    schema = create_thermal_coupling_schema()

    # Test with custom seed coefficients
    config = {
        CONF_SEED_COEFFICIENTS: {
            "same_floor": 0.20,
            "up": 0.50,
            "down": 0.05,
            "open": 0.70,
            "stairwell_up": 0.55,
            "stairwell_down": 0.08,
        }
    }

    validated = schema(config)
    seeds = validated[CONF_SEED_COEFFICIENTS]

    # Verify custom values
    assert seeds["same_floor"] == 0.20
    assert seeds["up"] == 0.50
    assert seeds["down"] == 0.05
    assert seeds["open"] == 0.70
    assert seeds["stairwell_up"] == 0.55
    assert seeds["stairwell_down"] == 0.08

    # Test partial override (schema only validates provided values)
    config_partial = {
        CONF_SEED_COEFFICIENTS: {
            "same_floor": 0.25,
            "up": 0.45,
        }
    }

    validated = schema(config_partial)
    seeds = validated[CONF_SEED_COEFFICIENTS]

    # Verify custom values are used
    assert seeds["same_floor"] == 0.25
    assert seeds["up"] == 0.45
    # Note: Other values won't have defaults applied by schema - that's done at runtime


def test_config_seed_coefficients_range_validation():
    """Test that seed coefficients are validated to be in range 0-1."""
    schema = create_thermal_coupling_schema()

    # Test with invalid value > 1.0
    config_high = {
        CONF_SEED_COEFFICIENTS: {
            "same_floor": 1.5,  # Invalid - too high
        }
    }

    with pytest.raises(vol.Invalid):
        schema(config_high)

    # Test with invalid value < 0.0
    config_low = {
        CONF_SEED_COEFFICIENTS: {
            "down": -0.1,  # Invalid - negative
        }
    }

    with pytest.raises(vol.Invalid):
        schema(config_low)


def test_config_floorplan_invalid_entity_id():
    """Test that invalid entity IDs in floorplan are rejected."""
    schema = create_thermal_coupling_schema()

    # Test with invalid entity_id (no domain)
    config_invalid = {
        CONF_FLOORPLAN: [
            {
                "floor": 0,
                "zones": ["invalid_entity"],  # Missing domain
            }
        ]
    }

    with pytest.raises(vol.Invalid):
        schema(config_invalid)


def test_config_complete_thermal_coupling():
    """Test a complete thermal coupling configuration."""
    schema = create_thermal_coupling_schema()

    config = {
        "enabled": True,
        CONF_FLOORPLAN: [
            {
                "floor": 0,
                "zones": ["climate.living_room", "climate.kitchen"],
                "open": ["climate.living_room", "climate.kitchen"],
            },
            {
                "floor": 1,
                "zones": ["climate.bedroom", "climate.bathroom"],
            }
        ],
        CONF_STAIRWELL_ZONES: ["climate.stairwell"],
        CONF_SEED_COEFFICIENTS: {
            "same_floor": 0.15,
            "up": 0.40,
            "down": 0.10,
            "open": 0.60,
            "stairwell_up": 0.45,
            "stairwell_down": 0.10,
        }
    }

    validated = schema(config)

    # Verify all components
    assert validated["enabled"] is True
    assert len(validated[CONF_FLOORPLAN]) == 2
    assert validated[CONF_STAIRWELL_ZONES] == ["climate.stairwell"]
    assert validated[CONF_SEED_COEFFICIENTS]["same_floor"] == 0.15


# =============================================================================
# Coupling Learner Initialization Tests (Story 7.2)
# =============================================================================


def test_climate_init_coupling_learner():
    """Test that coupling learner is created during climate setup.

    The ThermalCouplingLearner should be created and have proper initial state.
    This test verifies the learner can be created independently and has
    the expected empty state for fresh initialization.
    """
    from custom_components.adaptive_thermostat.adaptive.thermal_coupling import ThermalCouplingLearner

    # Create learner (simulating what coordinator does)
    learner = ThermalCouplingLearner()

    # Verify learner has expected initial empty state
    assert learner is not None
    assert isinstance(learner, ThermalCouplingLearner)
    assert learner.observations == {}
    assert learner.coefficients == {}
    assert learner._seeds == {}

    # Verify learner has required methods
    assert hasattr(learner, 'get_coefficient')
    assert hasattr(learner, 'calculate_coefficient')
    assert hasattr(learner, 'initialize_seeds')
    assert hasattr(learner, 'start_observation')
    assert hasattr(learner, 'end_observation')
    assert hasattr(learner, 'to_dict')
    assert hasattr(learner, 'from_dict')


def test_climate_restore_coupling_data():
    """Test that coupling data is restored from persistence on startup.

    When the LearningDataStore contains coupling data, it should be loaded
    into the ThermalCouplingLearner via the from_dict method.
    """
    from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
        ThermalCouplingLearner,
        CouplingObservation,
        CouplingCoefficient,
    )
    from custom_components.adaptive_thermostat.adaptive.persistence import LearningDataStore
    from datetime import datetime
    from unittest.mock import Mock, AsyncMock, patch

    # Create test coupling data to restore
    stored_coupling_data = {
        "observations": {
            "climate.zone_a|climate.zone_b": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "source_zone": "climate.zone_a",
                    "target_zone": "climate.zone_b",
                    "source_temp_start": 18.0,
                    "source_temp_end": 21.0,
                    "target_temp_start": 17.0,
                    "target_temp_end": 17.5,
                    "outdoor_temp_start": 5.0,
                    "outdoor_temp_end": 6.0,
                    "duration_minutes": 30.0,
                }
            ]
        },
        "coefficients": {
            "climate.zone_a|climate.zone_b": {
                "source_zone": "climate.zone_a",
                "target_zone": "climate.zone_b",
                "coefficient": 0.18,
                "confidence": 0.35,
                "observation_count": 3,
                "baseline_overshoot": None,
                "last_updated": "2024-01-15T11:00:00",
            }
        },
        "seeds": {
            "climate.zone_a|climate.zone_b": 0.15,
        }
    }

    # Restore learner from dict
    learner = ThermalCouplingLearner.from_dict(stored_coupling_data)

    # Verify observations were restored
    assert ("climate.zone_a", "climate.zone_b") in learner.observations
    obs_list = learner.observations[("climate.zone_a", "climate.zone_b")]
    assert len(obs_list) == 1
    assert obs_list[0].source_zone == "climate.zone_a"
    assert obs_list[0].target_zone == "climate.zone_b"
    assert obs_list[0].source_temp_start == 18.0

    # Verify coefficients were restored
    assert ("climate.zone_a", "climate.zone_b") in learner.coefficients
    coef = learner.coefficients[("climate.zone_a", "climate.zone_b")]
    assert coef.coefficient == 0.18
    assert coef.confidence == 0.35
    assert coef.observation_count == 3

    # Verify seeds were restored
    assert ("climate.zone_a", "climate.zone_b") in learner._seeds
    assert learner._seeds[("climate.zone_a", "climate.zone_b")] == 0.15


def test_climate_seeds_from_floorplan():
    """Test that seeds are initialized from floorplan config.

    When a floorplan configuration is provided, the ThermalCouplingLearner
    should generate seed coefficients for zone pairs based on their
    spatial relationships (same floor, up, down, open, stairwell).
    """
    from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
        ThermalCouplingLearner,
        parse_floorplan,
        CONF_FLOORPLAN,  # Legacy constant from thermal_coupling module
    )
    from custom_components.adaptive_thermostat.const import (
        CONF_STAIRWELL_ZONES,
        CONF_SEED_COEFFICIENTS,
        DEFAULT_SEED_COEFFICIENTS,
    )

    # Create floorplan config
    floorplan_config = {
        CONF_FLOORPLAN: [
            {
                "floor": 0,
                "zones": ["climate.living_room", "climate.kitchen"],
                "open": ["climate.living_room", "climate.kitchen"],
            },
            {
                "floor": 1,
                "zones": ["climate.bedroom", "climate.bathroom"],
            }
        ],
        CONF_STAIRWELL_ZONES: ["climate.living_room", "climate.bedroom"],
    }

    # Create learner and initialize seeds
    learner = ThermalCouplingLearner()
    learner.initialize_seeds(floorplan_config)

    # Verify same-floor open plan seeds (living room <-> kitchen)
    # Both zones are in open floor plan, so should use "open" seed
    assert ("climate.living_room", "climate.kitchen") in learner._seeds
    assert learner._seeds[("climate.living_room", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["open"]
    assert learner._seeds[("climate.kitchen", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["open"]

    # Verify floor 1 same-floor seeds (bedroom <-> bathroom)
    assert ("climate.bedroom", "climate.bathroom") in learner._seeds
    assert learner._seeds[("climate.bedroom", "climate.bathroom")] == DEFAULT_SEED_COEFFICIENTS["same_floor"]

    # Verify stairwell vertical seeds (living_room -> bedroom = stairwell_up)
    assert ("climate.living_room", "climate.bedroom") in learner._seeds
    assert learner._seeds[("climate.living_room", "climate.bedroom")] == DEFAULT_SEED_COEFFICIENTS["stairwell_up"]

    # Verify stairwell vertical seeds (bedroom -> living_room = stairwell_down)
    assert learner._seeds[("climate.bedroom", "climate.living_room")] == DEFAULT_SEED_COEFFICIENTS["stairwell_down"]

    # Verify regular vertical seeds (kitchen -> bathroom = up, not stairwell)
    assert ("climate.kitchen", "climate.bathroom") in learner._seeds
    assert learner._seeds[("climate.kitchen", "climate.bathroom")] == DEFAULT_SEED_COEFFICIENTS["up"]
    assert learner._seeds[("climate.bathroom", "climate.kitchen")] == DEFAULT_SEED_COEFFICIENTS["down"]

    # Verify get_coefficient returns seed-based coefficient for seed-only pairs
    coef = learner.get_coefficient("climate.living_room", "climate.kitchen")
    assert coef is not None
    assert coef.coefficient == DEFAULT_SEED_COEFFICIENTS["open"]
    assert coef.confidence == 0.3  # COUPLING_CONFIDENCE_THRESHOLD for seed-only
    assert coef.observation_count == 0


# =============================================================================
# Coupling Attribute Tests (Story 7.3)
# =============================================================================


def test_attr_coupling_coefficients():
    """Test coupling_coefficients attribute returns dict of zone coefficients."""
    from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
        ThermalCouplingLearner,
        CouplingCoefficient,
    )
    from datetime import datetime

    learner = ThermalCouplingLearner()

    # Initialize seeds
    learner._seeds[("climate.zone_a", "climate.zone_b")] = 0.15
    learner._seeds[("climate.zone_c", "climate.zone_b")] = 0.20

    # Add a learned coefficient (should override seed)
    learner.coefficients[("climate.zone_a", "climate.zone_b")] = CouplingCoefficient(
        source_zone="climate.zone_a",
        target_zone="climate.zone_b",
        coefficient=0.18,
        confidence=0.45,
        observation_count=5,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )

    # Get coefficients for zone_b
    coefficients = learner.get_coefficients_for_zone("climate.zone_b")

    # Should have both sources, with learned coefficient overriding seed
    assert "climate.zone_a" in coefficients
    assert coefficients["climate.zone_a"] == 0.18  # Learned value
    assert "climate.zone_c" in coefficients
    assert coefficients["climate.zone_c"] == 0.20  # Seed value


def test_attr_coupling_compensation():
    """Test coupling_compensation attribute returns current degC compensation."""
    from unittest.mock import Mock, MagicMock
    from custom_components.adaptive_thermostat.managers.control_output import ControlOutputManager

    # Create a mock thermostat
    thermostat = Mock()
    thermostat.entity_id = "climate.test"
    thermostat._hvac_mode = "heat"
    thermostat.hass = Mock()
    thermostat.hass.data = {}

    # Create mock callbacks
    mock_callbacks = {
        'get_current_temp': Mock(return_value=20.0),
        'get_ext_temp': Mock(return_value=5.0),
        'get_wind_speed': Mock(return_value=0.0),
        'get_previous_temp_time': Mock(return_value=None),
        'set_previous_temp_time': Mock(),
        'get_cur_temp_time': Mock(return_value=None),
        'set_cur_temp_time': Mock(),
        'get_output_precision': Mock(return_value=1),
        'calculate_night_setback_adjustment': Mock(return_value=(20.0, None, None)),
        'set_control_output': Mock(),
        'set_p': Mock(),
        'set_i': Mock(),
        'set_d': Mock(),
        'set_e': Mock(),
        'set_dt': Mock(),
        'get_kp': Mock(return_value=100.0),
        'get_ki': Mock(return_value=0.1),
        'get_kd': Mock(return_value=10.0),
        'get_ke': Mock(return_value=0.5),
    }

    manager = ControlOutputManager(
        thermostat=thermostat,
        pid_controller=Mock(),
        heater_controller=None,
        **mock_callbacks,
    )

    # Initially compensation should be 0
    assert manager.coupling_compensation_degc == 0.0

    # Manually set the values (simulating what happens in _calculate_coupling_compensation)
    manager._last_coupling_compensation_degc = 0.5
    manager._last_coupling_compensation_power = 50.0

    # Verify properties return correct values
    assert manager.coupling_compensation_degc == 0.5
    assert manager.coupling_compensation_power == 50.0


def test_attr_coupling_compensation_power():
    """Test coupling_compensation_power attribute returns current power% reduction."""
    from unittest.mock import Mock
    from custom_components.adaptive_thermostat.managers.control_output import ControlOutputManager

    thermostat = Mock()
    thermostat.entity_id = "climate.test"
    thermostat._hvac_mode = "heat"
    thermostat.hass = Mock()
    thermostat.hass.data = {}

    mock_callbacks = {
        'get_current_temp': Mock(return_value=20.0),
        'get_ext_temp': Mock(return_value=5.0),
        'get_wind_speed': Mock(return_value=0.0),
        'get_previous_temp_time': Mock(return_value=None),
        'set_previous_temp_time': Mock(),
        'get_cur_temp_time': Mock(return_value=None),
        'set_cur_temp_time': Mock(),
        'get_output_precision': Mock(return_value=1),
        'calculate_night_setback_adjustment': Mock(return_value=(20.0, None, None)),
        'set_control_output': Mock(),
        'set_p': Mock(),
        'set_i': Mock(),
        'set_d': Mock(),
        'set_e': Mock(),
        'set_dt': Mock(),
        'get_kp': Mock(return_value=100.0),
        'get_ki': Mock(return_value=0.1),
        'get_kd': Mock(return_value=10.0),
        'get_ke': Mock(return_value=0.5),
    }

    manager = ControlOutputManager(
        thermostat=thermostat,
        pid_controller=Mock(),
        heater_controller=None,
        **mock_callbacks,
    )

    # Set compensation values
    manager._last_coupling_compensation_degc = 0.8  # 0.8°C
    manager._last_coupling_compensation_power = 80.0  # 80% with Kp=100

    # Verify power property
    assert manager.coupling_compensation_power == 80.0


def test_attr_coupling_observations_pending():
    """Test coupling_observations_pending returns count of active observations."""
    from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
        ThermalCouplingLearner,
        ObservationContext,
    )
    from datetime import datetime

    learner = ThermalCouplingLearner()

    # Initially no pending observations
    assert learner.get_pending_observation_count() == 0

    # Start an observation
    learner.start_observation(
        source_zone="climate.zone_a",
        all_zone_temps={"climate.zone_a": 18.0, "climate.zone_b": 17.0},
        outdoor_temp=5.0,
    )

    # Should have 1 pending
    assert learner.get_pending_observation_count() == 1

    # Start another observation
    learner.start_observation(
        source_zone="climate.zone_c",
        all_zone_temps={"climate.zone_a": 18.0, "climate.zone_b": 17.0, "climate.zone_c": 19.0},
        outdoor_temp=5.0,
    )

    # Should have 2 pending
    assert learner.get_pending_observation_count() == 2

    # End one observation
    learner.end_observation(
        source_zone="climate.zone_a",
        current_temps={"climate.zone_a": 21.0, "climate.zone_b": 17.5},
        outdoor_temp=6.0,
        idle_zones={"climate.zone_b"},
    )

    # Should have 1 pending
    assert learner.get_pending_observation_count() == 1


def test_attr_coupling_learner_state():
    """Test coupling_learner_state returns learning/validating/stable."""
    from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
        ThermalCouplingLearner,
        CouplingCoefficient,
    )
    from datetime import datetime
    from custom_components.adaptive_thermostat.const import (
        COUPLING_CONFIDENCE_THRESHOLD,
        COUPLING_CONFIDENCE_MAX,
    )

    learner = ThermalCouplingLearner()

    # Initially should be "learning" (no coefficients)
    assert learner.get_learner_state() == "learning"

    # Add a low-confidence coefficient - still "learning"
    learner.coefficients[("climate.zone_a", "climate.zone_b")] = CouplingCoefficient(
        source_zone="climate.zone_a",
        target_zone="climate.zone_b",
        coefficient=0.15,
        confidence=0.2,  # Below COUPLING_CONFIDENCE_THRESHOLD (0.3)
        observation_count=1,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )
    assert learner.get_learner_state() == "learning"

    # Replace with medium confidence - should be "validating"
    learner.coefficients[("climate.zone_a", "climate.zone_b")] = CouplingCoefficient(
        source_zone="climate.zone_a",
        target_zone="climate.zone_b",
        coefficient=0.15,
        confidence=0.4,  # Between threshold (0.3) and max (0.5)
        observation_count=5,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )
    assert learner.get_learner_state() == "validating"

    # Add high confidence coefficient - should be "stable" (majority > 0.5)
    learner.coefficients[("climate.zone_a", "climate.zone_b")] = CouplingCoefficient(
        source_zone="climate.zone_a",
        target_zone="climate.zone_b",
        coefficient=0.15,
        confidence=0.55,  # Above COUPLING_CONFIDENCE_MAX (0.5)
        observation_count=10,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )
    assert learner.get_learner_state() == "stable"


def test_attr_coupling_learner_state_multiple_coefficients():
    """Test learner state with multiple coefficients at different confidence levels."""
    from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
        ThermalCouplingLearner,
        CouplingCoefficient,
    )
    from datetime import datetime

    learner = ThermalCouplingLearner()

    # Add one low confidence - should be "learning"
    learner.coefficients[("climate.a", "climate.b")] = CouplingCoefficient(
        source_zone="climate.a",
        target_zone="climate.b",
        coefficient=0.15,
        confidence=0.25,
        observation_count=2,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )
    assert learner.get_learner_state() == "learning"

    # Replace with high confidence
    learner.coefficients[("climate.a", "climate.b")] = CouplingCoefficient(
        source_zone="climate.a",
        target_zone="climate.b",
        coefficient=0.15,
        confidence=0.55,
        observation_count=10,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )

    # Add another at medium confidence
    learner.coefficients[("climate.c", "climate.d")] = CouplingCoefficient(
        source_zone="climate.c",
        target_zone="climate.d",
        coefficient=0.20,
        confidence=0.40,
        observation_count=5,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )

    # 1 high (0.55), 1 medium (0.40) - majority NOT high, so "validating"
    assert learner.get_learner_state() == "validating"

    # Add two more high confidence
    learner.coefficients[("climate.e", "climate.f")] = CouplingCoefficient(
        source_zone="climate.e",
        target_zone="climate.f",
        coefficient=0.18,
        confidence=0.60,
        observation_count=15,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )
    learner.coefficients[("climate.g", "climate.h")] = CouplingCoefficient(
        source_zone="climate.g",
        target_zone="climate.h",
        coefficient=0.22,
        confidence=0.55,
        observation_count=12,
        baseline_overshoot=None,
        last_updated=datetime.now(),
    )

    # Now 3 high, 1 medium - majority high, so "stable"
    assert learner.get_learner_state() == "stable"
