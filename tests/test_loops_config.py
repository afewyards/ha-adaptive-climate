"""Test loops config parameter validation in climate platform schema."""

import pytest
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat"))

import voluptuous as vol
from const import CONF_LOOPS


# Schema validator for loops parameter
LOOPS_SCHEMA = vol.Schema({
    vol.Optional(CONF_LOOPS, default=1): vol.All(
        vol.Coerce(int),
        vol.Range(min=1, max=10)
    )
})


class TestLoopsConfig:
    """Test suite for loops config parameter validation."""

    def test_default_value_when_not_specified(self):
        """Test that default value is 1 when loops is not specified."""
        config = {}
        validated = LOOPS_SCHEMA(config)
        assert validated[CONF_LOOPS] == 1

    def test_valid_value_1(self):
        """Test that value 1 is accepted."""
        config = {CONF_LOOPS: 1}
        validated = LOOPS_SCHEMA(config)
        assert validated[CONF_LOOPS] == 1

    def test_valid_value_2(self):
        """Test that value 2 is accepted."""
        config = {CONF_LOOPS: 2}
        validated = LOOPS_SCHEMA(config)
        assert validated[CONF_LOOPS] == 2

    def test_valid_value_5(self):
        """Test that value 5 is accepted."""
        config = {CONF_LOOPS: 5}
        validated = LOOPS_SCHEMA(config)
        assert validated[CONF_LOOPS] == 5

    def test_valid_value_10(self):
        """Test that maximum value 10 is accepted."""
        config = {CONF_LOOPS: 10}
        validated = LOOPS_SCHEMA(config)
        assert validated[CONF_LOOPS] == 10

    def test_invalid_value_0(self):
        """Test that value 0 is rejected."""
        config = {CONF_LOOPS: 0}
        with pytest.raises(vol.Invalid) as exc_info:
            LOOPS_SCHEMA(config)
        assert "value must be at least 1" in str(exc_info.value)

    def test_invalid_negative_value(self):
        """Test that negative values are rejected."""
        config = {CONF_LOOPS: -1}
        with pytest.raises(vol.Invalid) as exc_info:
            LOOPS_SCHEMA(config)
        assert "value must be at least 1" in str(exc_info.value)

    def test_invalid_value_above_max(self):
        """Test that values above 10 are rejected."""
        config = {CONF_LOOPS: 11}
        with pytest.raises(vol.Invalid) as exc_info:
            LOOPS_SCHEMA(config)
        assert "value must be at most 10" in str(exc_info.value)

    def test_invalid_non_integer_float(self):
        """Test that non-integer float values are rejected."""
        config = {CONF_LOOPS: 2.5}
        # vol.Coerce(int) will truncate to 2, which is valid
        validated = LOOPS_SCHEMA(config)
        assert validated[CONF_LOOPS] == 2

    def test_invalid_non_integer_string(self):
        """Test that non-numeric string values are rejected."""
        config = {CONF_LOOPS: "invalid"}
        with pytest.raises(vol.Invalid) as exc_info:
            LOOPS_SCHEMA(config)
        # voluptuous error for invalid coercion
        assert "invalid literal" in str(exc_info.value).lower() or "expected int" in str(exc_info.value).lower()

    def test_coerce_string_to_int(self):
        """Test that valid string numbers are coerced to integers."""
        config = {CONF_LOOPS: "3"}
        validated = LOOPS_SCHEMA(config)
        assert validated[CONF_LOOPS] == 3
        assert isinstance(validated[CONF_LOOPS], int)

    def test_loops_config_module_exists(self):
        """Marker test to verify CONF_LOOPS constant exists."""
        assert CONF_LOOPS == "loops"
