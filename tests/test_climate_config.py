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
    CONF_FLOORPLAN,
    CONF_STAIRWELL_ZONES,
    CONF_SEED_COEFFICIENTS,
    DEFAULT_SEED_COEFFICIENTS,
)


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
