"""Tests for constants in const.py."""

from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_CHARACTERISTICS,
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
    VALID_HEATING_TYPES,
    INTEGRAL_DECAY_THRESHOLDS,
)


class TestHeatingTypeCharacteristics:
    """Test HEATING_TYPE_CHARACTERISTICS structure and values."""

    def test_heating_type_characteristics_has_decay_params(self):
        """Verify cold_tolerance, hot_tolerance, decay_exponent, max_settling_time exist for all heating types."""
        required_keys = [
            "cold_tolerance",
            "hot_tolerance",
            "decay_exponent",
            "max_settling_time",
        ]

        for heating_type in VALID_HEATING_TYPES:
            assert heating_type in HEATING_TYPE_CHARACTERISTICS, (
                f"Heating type {heating_type} missing from HEATING_TYPE_CHARACTERISTICS"
            )

            characteristics = HEATING_TYPE_CHARACTERISTICS[heating_type]

            for key in required_keys:
                assert key in characteristics, (
                    f"Heating type {heating_type} missing required key: {key}"
                )
                assert isinstance(characteristics[key], (int, float)), (
                    f"Heating type {heating_type} key {key} must be numeric, got {type(characteristics[key])}"
                )
                assert characteristics[key] > 0, (
                    f"Heating type {heating_type} key {key} must be positive, got {characteristics[key]}"
                )

    def test_floor_hydronic_decay_values(self):
        """Test floor_hydronic has correct tolerance and decay values."""
        chars = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FLOOR_HYDRONIC]

        assert chars["cold_tolerance"] == 0.5
        assert chars["hot_tolerance"] == 0.5
        assert chars["decay_exponent"] == 2.0
        assert chars["max_settling_time"] == 90

    def test_radiator_decay_values(self):
        """Test radiator has correct tolerance and decay values."""
        chars = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_RADIATOR]

        assert chars["cold_tolerance"] == 0.3
        assert chars["hot_tolerance"] == 0.3
        assert chars["decay_exponent"] == 1.0
        assert chars["max_settling_time"] == 45

    def test_convector_decay_values(self):
        """Test convector has correct tolerance and decay values."""
        chars = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_CONVECTOR]

        assert chars["cold_tolerance"] == 0.2
        assert chars["hot_tolerance"] == 0.2
        assert chars["decay_exponent"] == 1.0
        assert chars["max_settling_time"] == 30

    def test_forced_air_decay_values(self):
        """Test forced_air has correct tolerance and decay values."""
        chars = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FORCED_AIR]

        assert chars["cold_tolerance"] == 0.15
        assert chars["hot_tolerance"] == 0.15
        assert chars["decay_exponent"] == 0.5
        assert chars["max_settling_time"] == 15

    def test_decay_exponent_ordering(self):
        """Test that decay_exponent decreases with faster heating systems."""
        floor = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FLOOR_HYDRONIC]["decay_exponent"]
        radiator = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_RADIATOR]["decay_exponent"]
        convector = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_CONVECTOR]["decay_exponent"]
        forced_air = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FORCED_AIR]["decay_exponent"]

        # Slower systems (higher thermal mass) need higher decay exponents
        assert floor > radiator
        assert radiator >= convector
        assert convector > forced_air

    def test_tolerance_ordering(self):
        """Test that tolerance decreases with faster heating systems."""
        floor = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FLOOR_HYDRONIC]["cold_tolerance"]
        radiator = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_RADIATOR]["cold_tolerance"]
        convector = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_CONVECTOR]["cold_tolerance"]
        forced_air = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FORCED_AIR]["cold_tolerance"]

        # Slower systems get wider tolerance bands
        assert floor > radiator
        assert radiator > convector
        assert convector > forced_air

    def test_max_settling_time_ordering(self):
        """Test that max_settling_time decreases with faster heating systems."""
        floor = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FLOOR_HYDRONIC]["max_settling_time"]
        radiator = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_RADIATOR]["max_settling_time"]
        convector = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_CONVECTOR]["max_settling_time"]
        forced_air = HEATING_TYPE_CHARACTERISTICS[HEATING_TYPE_FORCED_AIR]["max_settling_time"]

        # Slower systems take longer to settle
        assert floor > radiator
        assert radiator > convector
        assert convector > forced_air


class TestIntegralDecayThresholds:
    """Test INTEGRAL_DECAY_THRESHOLDS structure and values."""

    def test_integral_decay_thresholds_exists(self):
        """Verify INTEGRAL_DECAY_THRESHOLDS dict exists."""
        assert INTEGRAL_DECAY_THRESHOLDS is not None
        assert isinstance(INTEGRAL_DECAY_THRESHOLDS, dict)

    def test_integral_decay_thresholds_has_all_heating_types(self):
        """Verify all heating types have integral decay threshold entries."""
        for heating_type in VALID_HEATING_TYPES:
            assert heating_type in INTEGRAL_DECAY_THRESHOLDS, (
                f"Heating type {heating_type} missing from INTEGRAL_DECAY_THRESHOLDS"
            )

    def test_integral_decay_thresholds_values_are_numeric(self):
        """Verify all threshold values are numeric and positive."""
        for heating_type, threshold in INTEGRAL_DECAY_THRESHOLDS.items():
            assert isinstance(threshold, (int, float)), (
                f"Threshold for {heating_type} must be numeric, got {type(threshold)}"
            )
            assert threshold > 0, (
                f"Threshold for {heating_type} must be positive, got {threshold}"
            )

    def test_integral_decay_threshold_values(self):
        """Verify specific threshold values match the specification."""
        assert INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC] == 35.0
        assert INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_RADIATOR] == 40.0
        assert INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_CONVECTOR] == 50.0
        assert INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_FORCED_AIR] == 60.0

    def test_integral_decay_threshold_ordering(self):
        """Test that thresholds increase with faster heating systems.

        Slower systems (high thermal mass) need lower thresholds to activate
        safety net earlier, preventing prolonged overshoot.
        """
        floor = INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC]
        radiator = INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_RADIATOR]
        convector = INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_CONVECTOR]
        forced_air = INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_FORCED_AIR]

        # Faster systems can tolerate higher thresholds before needing safety net
        assert floor < radiator
        assert radiator < convector
        assert convector < forced_air


# Marker test
def test_const_module_exists():
    """Marker test to ensure const module is importable."""
    from custom_components.adaptive_thermostat import const
    assert const is not None
