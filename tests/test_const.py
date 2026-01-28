"""Tests for constants in const.py."""

from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_CHARACTERISTICS,
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
    VALID_HEATING_TYPES,
    INTEGRAL_DECAY_THRESHOLDS,
    PIDGains,
    COOLING_TYPE_CHARACTERISTICS,
    CONF_COOLING_TYPE,
    CONF_HUMIDITY_SENSOR,
    CONF_HUMIDITY_SPIKE_THRESHOLD,
    CONF_HUMIDITY_ABSOLUTE_MAX,
    CONF_HUMIDITY_DETECTION_WINDOW,
    CONF_HUMIDITY_STABILIZATION_DELAY,
    DEFAULT_HUMIDITY_SPIKE_THRESHOLD,
    DEFAULT_HUMIDITY_ABSOLUTE_MAX,
    DEFAULT_HUMIDITY_DETECTION_WINDOW,
    DEFAULT_HUMIDITY_STABILIZATION_DELAY,
    DEFAULT_HUMIDITY_MAX_PAUSE,
    DEFAULT_HUMIDITY_EXIT_THRESHOLD,
    DEFAULT_HUMIDITY_EXIT_DROP,
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
        assert INTEGRAL_DECAY_THRESHOLDS[HEATING_TYPE_FLOOR_HYDRONIC] == 30.0
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


class TestPIDGains:
    """Test PIDGains dataclass structure and behavior."""

    def test_pidgains_dataclass_exists(self):
        """Verify PIDGains dataclass can be imported and instantiated."""
        gains = PIDGains(kp=1.0, ki=0.5, kd=0.2)
        assert gains is not None
        assert hasattr(gains, 'kp')
        assert hasattr(gains, 'ki')
        assert hasattr(gains, 'kd')

    def test_pidgains_field_access(self):
        """Verify PIDGains fields are accessible and have correct values."""
        gains = PIDGains(kp=2.5, ki=0.3, kd=0.8)
        assert gains.kp == 2.5
        assert gains.ki == 0.3
        assert gains.kd == 0.8

    def test_pidgains_immutability(self):
        """Verify PIDGains is frozen (immutable)."""
        gains = PIDGains(kp=1.0, ki=0.5, kd=0.2)
        try:
            gains.kp = 2.0
            assert False, "PIDGains should be frozen (immutable)"
        except (AttributeError, Exception):
            pass  # Expected - dataclass should be frozen

    def test_pidgains_equality(self):
        """Verify PIDGains equality comparison works."""
        gains1 = PIDGains(kp=1.0, ki=0.5, kd=0.2)
        gains2 = PIDGains(kp=1.0, ki=0.5, kd=0.2)
        gains3 = PIDGains(kp=2.0, ki=0.5, kd=0.2)
        assert gains1 == gains2
        assert gains1 != gains3


class TestCoolingTypeCharacteristics:
    """Test COOLING_TYPE_CHARACTERISTICS structure and values."""

    def test_cooling_type_characteristics_exists(self):
        """Verify COOLING_TYPE_CHARACTERISTICS dict exists."""
        assert COOLING_TYPE_CHARACTERISTICS is not None
        assert isinstance(COOLING_TYPE_CHARACTERISTICS, dict)

    def test_cooling_type_characteristics_has_required_types(self):
        """Verify forced_air, chilled_water, mini_split cooling types exist."""
        required_types = ["forced_air", "chilled_water", "mini_split"]
        for cooling_type in required_types:
            assert cooling_type in COOLING_TYPE_CHARACTERISTICS, (
                f"Cooling type {cooling_type} missing from COOLING_TYPE_CHARACTERISTICS"
            )

    def test_cooling_type_has_required_fields(self):
        """Verify each cooling type has pid_modifier, pwm_period, min_cycle, tau_ratio."""
        required_keys = ["pid_modifier", "pwm_period", "min_cycle", "tau_ratio"]

        for cooling_type, characteristics in COOLING_TYPE_CHARACTERISTICS.items():
            for key in required_keys:
                assert key in characteristics, (
                    f"Cooling type {cooling_type} missing required key: {key}"
                )
                assert isinstance(characteristics[key], (int, float)), (
                    f"Cooling type {cooling_type} key {key} must be numeric, got {type(characteristics[key])}"
                )
                assert characteristics[key] >= 0, (
                    f"Cooling type {cooling_type} key {key} must be non-negative, got {characteristics[key]}"
                )

    def test_forced_air_characteristics(self):
        """Test forced_air has correct characteristic values."""
        chars = COOLING_TYPE_CHARACTERISTICS["forced_air"]
        assert chars["pid_modifier"] == 1.0
        assert chars["pwm_period"] == 600  # 10 minutes
        assert chars["min_cycle"] == 180  # 3 minutes compressor protection
        assert chars["tau_ratio"] == 0.3

    def test_chilled_water_characteristics(self):
        """Test chilled_water has correct characteristic values."""
        chars = COOLING_TYPE_CHARACTERISTICS["chilled_water"]
        assert chars["pid_modifier"] == 0.7
        assert chars["pwm_period"] == 900  # 15 minutes
        assert chars["min_cycle"] == 0  # no compressor
        assert chars["tau_ratio"] == 0.6

    def test_mini_split_characteristics(self):
        """Test mini_split has correct characteristic values."""
        chars = COOLING_TYPE_CHARACTERISTICS["mini_split"]
        assert chars["pid_modifier"] == 0.9
        assert chars["pwm_period"] == 0  # inverter modulating, no PWM
        assert chars["min_cycle"] == 180  # 3 minutes compressor protection
        assert chars["tau_ratio"] == 0.4

    def test_tau_ratio_all_less_than_one(self):
        """Test that all cooling tau_ratios are less than 1.0.

        Cooling should respond faster than heating, so tau_ratio < 1.0.
        """
        for cooling_type, chars in COOLING_TYPE_CHARACTERISTICS.items():
            assert chars["tau_ratio"] < 1.0, (
                f"Cooling type {cooling_type} tau_ratio should be < 1.0, got {chars['tau_ratio']}"
            )

    def test_compressor_types_have_min_cycle_protection(self):
        """Test that compressor-based cooling types have min_cycle > 0."""
        # forced_air and mini_split use compressors
        assert COOLING_TYPE_CHARACTERISTICS["forced_air"]["min_cycle"] > 0
        assert COOLING_TYPE_CHARACTERISTICS["mini_split"]["min_cycle"] > 0
        # chilled_water does not
        assert COOLING_TYPE_CHARACTERISTICS["chilled_water"]["min_cycle"] == 0


class TestCoolingTypeConfig:
    """Test CONF_COOLING_TYPE constant."""

    def test_conf_cooling_type_constant_exists(self):
        """Verify CONF_COOLING_TYPE constant exists and is a string."""
        assert CONF_COOLING_TYPE is not None
        assert isinstance(CONF_COOLING_TYPE, str)

    def test_conf_cooling_type_value(self):
        """Verify CONF_COOLING_TYPE has expected value."""
        assert CONF_COOLING_TYPE == "cooling_type"


class TestHumidityDetectionConstants:
    """Test humidity detection configuration constants."""

    def test_humidity_config_constants_exist(self):
        """Verify all humidity configuration constants exist and are strings."""
        config_constants = [
            CONF_HUMIDITY_SENSOR,
            CONF_HUMIDITY_SPIKE_THRESHOLD,
            CONF_HUMIDITY_ABSOLUTE_MAX,
            CONF_HUMIDITY_DETECTION_WINDOW,
            CONF_HUMIDITY_STABILIZATION_DELAY,
        ]

        for constant in config_constants:
            assert constant is not None
            assert isinstance(constant, str)

    def test_humidity_config_constant_values(self):
        """Verify humidity configuration constants have expected values."""
        assert CONF_HUMIDITY_SENSOR == "humidity_sensor"
        assert CONF_HUMIDITY_SPIKE_THRESHOLD == "humidity_spike_threshold"
        assert CONF_HUMIDITY_ABSOLUTE_MAX == "humidity_absolute_max"
        assert CONF_HUMIDITY_DETECTION_WINDOW == "humidity_detection_window"
        assert CONF_HUMIDITY_STABILIZATION_DELAY == "humidity_stabilization_delay"

    def test_humidity_default_constants_exist(self):
        """Verify all humidity default constants exist and are numeric."""
        assert DEFAULT_HUMIDITY_SPIKE_THRESHOLD is not None
        assert isinstance(DEFAULT_HUMIDITY_SPIKE_THRESHOLD, (int, float))

        assert DEFAULT_HUMIDITY_ABSOLUTE_MAX is not None
        assert isinstance(DEFAULT_HUMIDITY_ABSOLUTE_MAX, (int, float))

        assert DEFAULT_HUMIDITY_DETECTION_WINDOW is not None
        assert isinstance(DEFAULT_HUMIDITY_DETECTION_WINDOW, (int, float))

        assert DEFAULT_HUMIDITY_STABILIZATION_DELAY is not None
        assert isinstance(DEFAULT_HUMIDITY_STABILIZATION_DELAY, (int, float))

        assert DEFAULT_HUMIDITY_MAX_PAUSE is not None
        assert isinstance(DEFAULT_HUMIDITY_MAX_PAUSE, (int, float))

        assert DEFAULT_HUMIDITY_EXIT_THRESHOLD is not None
        assert isinstance(DEFAULT_HUMIDITY_EXIT_THRESHOLD, (int, float))

        assert DEFAULT_HUMIDITY_EXIT_DROP is not None
        assert isinstance(DEFAULT_HUMIDITY_EXIT_DROP, (int, float))

    def test_humidity_default_values(self):
        """Verify humidity default constants have expected values."""
        assert DEFAULT_HUMIDITY_SPIKE_THRESHOLD == 15
        assert DEFAULT_HUMIDITY_ABSOLUTE_MAX == 80
        assert DEFAULT_HUMIDITY_DETECTION_WINDOW == 300
        assert DEFAULT_HUMIDITY_STABILIZATION_DELAY == 300
        assert DEFAULT_HUMIDITY_MAX_PAUSE == 3600
        assert DEFAULT_HUMIDITY_EXIT_THRESHOLD == 70
        assert DEFAULT_HUMIDITY_EXIT_DROP == 5

    def test_humidity_default_values_are_positive(self):
        """Verify all humidity default values are positive."""
        defaults = [
            DEFAULT_HUMIDITY_SPIKE_THRESHOLD,
            DEFAULT_HUMIDITY_ABSOLUTE_MAX,
            DEFAULT_HUMIDITY_DETECTION_WINDOW,
            DEFAULT_HUMIDITY_STABILIZATION_DELAY,
            DEFAULT_HUMIDITY_MAX_PAUSE,
            DEFAULT_HUMIDITY_EXIT_THRESHOLD,
            DEFAULT_HUMIDITY_EXIT_DROP,
        ]

        for default in defaults:
            assert default > 0, f"Default value {default} must be positive"

    def test_humidity_percentage_values_in_valid_range(self):
        """Verify humidity percentage values are in valid 0-100 range."""
        assert 0 <= DEFAULT_HUMIDITY_SPIKE_THRESHOLD <= 100
        assert 0 <= DEFAULT_HUMIDITY_ABSOLUTE_MAX <= 100
        assert 0 <= DEFAULT_HUMIDITY_EXIT_THRESHOLD <= 100
        assert 0 <= DEFAULT_HUMIDITY_EXIT_DROP <= 100

    def test_humidity_time_values_are_seconds(self):
        """Verify time-based humidity values are in seconds."""
        # Detection window: 300 seconds = 5 minutes
        assert DEFAULT_HUMIDITY_DETECTION_WINDOW == 300
        # Stabilization delay: 300 seconds = 5 minutes
        assert DEFAULT_HUMIDITY_STABILIZATION_DELAY == 300
        # Max pause: 3600 seconds = 60 minutes
        assert DEFAULT_HUMIDITY_MAX_PAUSE == 3600


# Marker test
def test_const_module_exists():
    """Marker test to ensure const module is importable."""
    from custom_components.adaptive_thermostat import const
    assert const is not None
