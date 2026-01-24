"""Tests for preheat configuration constants."""

import pytest

from custom_components.adaptive_thermostat.const import (
    CONF_PREHEAT_ENABLED,
    CONF_MAX_PREHEAT_HOURS,
    HEATING_TYPE_PREHEAT_CONFIG,
)


def test_preheat_config_keys_exist():
    """Test that preheat config key constants exist."""
    assert CONF_PREHEAT_ENABLED == "preheat_enabled"
    assert CONF_MAX_PREHEAT_HOURS == "max_preheat_hours"


def test_heating_type_preheat_config_has_all_types():
    """Test that HEATING_TYPE_PREHEAT_CONFIG has all 4 heating types."""
    expected_types = {"floor_hydronic", "radiator", "convector", "forced_air"}
    assert set(HEATING_TYPE_PREHEAT_CONFIG.keys()) == expected_types


def test_heating_type_preheat_config_has_required_keys():
    """Test that each heating type has required keys."""
    required_keys = {"max_hours", "cold_soak_margin", "fallback_rate"}

    for heating_type, config in HEATING_TYPE_PREHEAT_CONFIG.items():
        assert set(config.keys()) == required_keys, (
            f"{heating_type} missing required keys"
        )


def test_fallback_rate_values_are_positive():
    """Test that fallback_rate values are positive floats."""
    for heating_type, config in HEATING_TYPE_PREHEAT_CONFIG.items():
        fallback_rate = config["fallback_rate"]
        assert isinstance(fallback_rate, (int, float)), (
            f"{heating_type} fallback_rate is not numeric"
        )
        assert fallback_rate > 0, (
            f"{heating_type} fallback_rate is not positive"
        )


def test_max_hours_values_are_positive():
    """Test that max_hours values are positive floats."""
    for heating_type, config in HEATING_TYPE_PREHEAT_CONFIG.items():
        max_hours = config["max_hours"]
        assert isinstance(max_hours, (int, float)), (
            f"{heating_type} max_hours is not numeric"
        )
        assert max_hours > 0, (
            f"{heating_type} max_hours is not positive"
        )


def test_cold_soak_margin_values_are_positive():
    """Test that cold_soak_margin values are positive floats."""
    for heating_type, config in HEATING_TYPE_PREHEAT_CONFIG.items():
        cold_soak_margin = config["cold_soak_margin"]
        assert isinstance(cold_soak_margin, (int, float)), (
            f"{heating_type} cold_soak_margin is not numeric"
        )
        assert cold_soak_margin > 0, (
            f"{heating_type} cold_soak_margin is not positive"
        )
