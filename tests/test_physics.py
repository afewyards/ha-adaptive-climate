"""Tests for adaptive/physics.py module."""

import pytest
from custom_components.adaptive_thermostat.adaptive.physics import (
    calculate_thermal_time_constant,
    calculate_initial_pid,
    calculate_initial_pwm_period,
    GLAZING_U_VALUES,
)


class TestThermalTimeConstant:
    """Tests for calculate_thermal_time_constant function."""

    def test_thermal_time_constant_from_volume(self):
        """Test tau calculation from zone volume."""
        # Small zone (100 m3) should have lower tau
        tau_small = calculate_thermal_time_constant(volume_m3=100)
        assert tau_small == pytest.approx(2.0, abs=0.01)

        # Large zone (250 m3) should have higher tau
        tau_large = calculate_thermal_time_constant(volume_m3=250)
        assert tau_large == pytest.approx(5.0, abs=0.01)

        # Larger volume = higher tau (slower response)
        assert tau_large > tau_small

    def test_thermal_time_constant_from_energy_rating(self):
        """Test tau calculation from energy efficiency rating."""
        # A+++ rating (best insulation) should have highest tau
        tau_best = calculate_thermal_time_constant(energy_rating="A+++")
        assert tau_best == 8.0

        # D rating (poor insulation) should have lowest tau
        tau_poor = calculate_thermal_time_constant(energy_rating="D")
        assert tau_poor == 2.0

        # A rating (standard good insulation)
        tau_standard = calculate_thermal_time_constant(energy_rating="A")
        assert tau_standard == 4.0

        # Better insulation = higher tau
        assert tau_best > tau_standard > tau_poor

        # Case insensitive
        tau_lower = calculate_thermal_time_constant(energy_rating="a++")
        assert tau_lower == 6.0

    def test_thermal_time_constant_missing_params(self):
        """Test that ValueError is raised when no parameters provided."""
        with pytest.raises(ValueError, match="Either volume_m3 or energy_rating must be provided"):
            calculate_thermal_time_constant()

    def test_thermal_time_constant_unknown_rating(self):
        """Test fallback for unknown energy rating."""
        # Unknown rating should default to 4.0 (standard)
        tau_unknown = calculate_thermal_time_constant(energy_rating="X")
        assert tau_unknown == 4.0

    def test_thermal_time_constant_no_window_params(self):
        """Test that tau is unchanged when window params not provided."""
        # Base tau without windows
        tau_base = calculate_thermal_time_constant(volume_m3=200)
        assert tau_base == pytest.approx(4.0, abs=0.01)

        # With window_area but no floor_area - should be unchanged
        tau_no_floor = calculate_thermal_time_constant(volume_m3=200, window_area_m2=5.0)
        assert tau_no_floor == tau_base

        # With floor_area but no window_area - should be unchanged
        tau_no_window = calculate_thermal_time_constant(volume_m3=200, floor_area_m2=25.0)
        assert tau_no_window == tau_base

    def test_thermal_time_constant_with_hr_plus_plus_baseline(self):
        """Test tau adjustment with HR++ glazing at 20% window ratio (baseline)."""
        # 25 m2 floor, 5 m2 window = 20% ratio, HR++ (default)
        # At baseline, expect 15% reduction
        tau_base = 200 / 50.0  # 4.0
        tau_with_windows = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="hr++",
        )
        expected = tau_base * (1 - 0.15)  # 15% reduction at baseline
        assert tau_with_windows == pytest.approx(expected, abs=0.01)

    def test_thermal_time_constant_with_single_glazing(self):
        """Test tau adjustment with single pane glass - significant reduction."""
        # Single pane has U=5.8, much worse than HR++ (U=1.1)
        tau_base = 200 / 50.0  # 4.0
        tau_single = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="single",
        )
        tau_hr_plus_plus = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="hr++",
        )
        # Single glazing should result in lower tau (faster response/more heat loss)
        assert tau_single < tau_hr_plus_plus
        # Heat loss factor = (5.8/1.1) * 1.0 = 5.27, reduction = min(5.27 * 0.15, 0.4) = 0.4 (capped)
        expected = tau_base * (1 - 0.4)
        assert tau_single == pytest.approx(expected, abs=0.01)

    def test_thermal_time_constant_with_triple_glazing(self):
        """Test tau adjustment with triple glazing - minimal reduction."""
        tau_base = 200 / 50.0  # 4.0
        tau_triple = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="hr+++",
        )
        tau_hr_plus_plus = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="hr++",
        )
        # Triple glazing (U=0.6) should result in higher tau (less heat loss)
        assert tau_triple > tau_hr_plus_plus
        # Heat loss factor = (0.6/1.1) * 1.0 = 0.545, reduction = 0.545 * 0.15 = 0.082
        expected = tau_base * (1 - 0.0818)
        assert tau_triple == pytest.approx(expected, abs=0.01)

    def test_thermal_time_constant_high_window_ratio(self):
        """Test tau adjustment capped at 40% for high glass area."""
        tau_base = 200 / 50.0  # 4.0
        # 25 m2 floor, 15 m2 window = 60% ratio (very high!)
        tau_high_glass = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=15.0,
            floor_area_m2=25.0,
            window_rating="hr++",
        )
        # Heat loss factor = 1.0 * (0.6/0.2) = 3.0, reduction = min(3.0 * 0.15, 0.4) = 0.4 (capped)
        expected = tau_base * (1 - 0.4)
        assert tau_high_glass == pytest.approx(expected, abs=0.01)

    def test_thermal_time_constant_unknown_window_rating(self):
        """Test fallback to HR++ for unknown window rating."""
        tau_unknown = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="unknown_type",
        )
        tau_hr_plus_plus = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="hr++",
        )
        assert tau_unknown == tau_hr_plus_plus

    def test_thermal_time_constant_window_rating_case_insensitive(self):
        """Test that window rating is case insensitive."""
        tau_lower = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="hr++",
        )
        tau_upper = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="HR++",
        )
        tau_mixed = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="Hr++",
        )
        assert tau_lower == tau_upper == tau_mixed


class TestGlazingUValues:
    """Tests for GLAZING_U_VALUES constants."""

    def test_glazing_u_values_exist(self):
        """Test that all expected glazing types have U-values."""
        expected_types = ["single", "double", "hr", "hr+", "hr++", "hr+++", "triple"]
        for glazing_type in expected_types:
            assert glazing_type in GLAZING_U_VALUES

    def test_glazing_u_values_ordering(self):
        """Test that better glazing has lower U-values."""
        # Worse insulation = higher U-value
        assert GLAZING_U_VALUES["single"] > GLAZING_U_VALUES["double"]
        assert GLAZING_U_VALUES["double"] > GLAZING_U_VALUES["hr+"]
        assert GLAZING_U_VALUES["hr+"] > GLAZING_U_VALUES["hr++"]
        assert GLAZING_U_VALUES["hr++"] > GLAZING_U_VALUES["hr+++"]

    def test_glazing_aliases(self):
        """Test that aliases have same U-value."""
        assert GLAZING_U_VALUES["hr"] == GLAZING_U_VALUES["double"]
        assert GLAZING_U_VALUES["triple"] == GLAZING_U_VALUES["hr+++"]


class TestInitialPID:
    """Tests for calculate_initial_pid function."""

    def test_floor_heating_pid_conservative(self):
        """Test that floor heating gets conservative PID values."""
        tau = 4.0  # Standard thermal time constant
        Kp, Ki, Kd = calculate_initial_pid(tau, "floor_hydronic")

        # Floor heating should use conservative values (0.5 modifier)
        # Expected: Kp = 0.6/4 * 0.5 = 0.075
        assert Kp == pytest.approx(0.075, abs=0.001)

        # Ki should be small for slow integration
        # Expected: Ki = 2*0.075/4 * 0.5 = 0.01875
        assert Ki == pytest.approx(0.01875, abs=0.001)

        # Kd should provide damping
        # Expected: Kd = 0.075*4/8 * 0.5 = 0.01875
        assert Kd == pytest.approx(0.019, abs=0.001)

    def test_convector_pid_aggressive(self):
        """Test that convector heating gets more aggressive PID values."""
        tau = 4.0  # Standard thermal time constant
        Kp_conv, Ki_conv, Kd_conv = calculate_initial_pid(tau, "convector")

        # Convector uses standard modifier (1.0)
        # Expected: Kp = 0.6/4 * 1.0 = 0.15
        assert Kp_conv == pytest.approx(0.15, abs=0.001)

        # Ki should be larger than floor heating
        # Expected: Ki = 2*0.15/4 * 1.0 = 0.075
        assert Ki_conv == pytest.approx(0.075, abs=0.001)

        # Kd should be larger than floor heating
        # Expected: Kd = 0.15*4/8 * 1.0 = 0.075
        assert Kd_conv == pytest.approx(0.075, abs=0.001)

        # Compare with floor heating - convector should be more aggressive
        Kp_floor, Ki_floor, Kd_floor = calculate_initial_pid(tau, "floor_hydronic")
        assert Kp_conv > Kp_floor
        assert Ki_conv > Ki_floor
        assert Kd_conv > Kd_floor

    def test_forced_air_pid_most_aggressive(self):
        """Test that forced air heating gets most aggressive PID values."""
        tau = 4.0  # Standard thermal time constant
        Kp_air, Ki_air, Kd_air = calculate_initial_pid(tau, "forced_air")

        # Forced air uses aggressive modifier (1.3)
        # Expected: Kp = 0.6/4 * 1.3 = 0.195
        assert Kp_air == pytest.approx(0.195, abs=0.001)

        # Should be most aggressive of all heating types
        Kp_conv, Ki_conv, Kd_conv = calculate_initial_pid(tau, "convector")
        assert Kp_air > Kp_conv
        assert Ki_air > Ki_conv
        assert Kd_air > Kd_conv

    def test_radiator_pid_moderate(self):
        """Test that radiator heating gets moderate PID values."""
        tau = 4.0  # Standard thermal time constant
        Kp_rad, Ki_rad, Kd_rad = calculate_initial_pid(tau, "radiator")

        # Radiator uses moderate modifier (0.7)
        assert Kp_rad == pytest.approx(0.105, abs=0.001)

        # Should be between floor and convector
        Kp_floor, _, _ = calculate_initial_pid(tau, "floor_hydronic")
        Kp_conv, _, _ = calculate_initial_pid(tau, "convector")
        assert Kp_floor < Kp_rad < Kp_conv

    def test_pid_unknown_heating_type(self):
        """Test fallback for unknown heating type."""
        tau = 4.0
        Kp, Ki, Kd = calculate_initial_pid(tau, "unknown_type")

        # Should default to radiator modifier (0.7)
        Kp_rad, Ki_rad, Kd_rad = calculate_initial_pid(tau, "radiator")
        assert Kp == Kp_rad
        assert Ki == Ki_rad
        assert Kd == Kd_rad


class TestInitialPWMPeriod:
    """Tests for calculate_initial_pwm_period function."""

    def test_pwm_period_from_heating_type(self):
        """Test PWM period varies by heating type."""
        # Floor heating should have longest period (15 min = 900 sec)
        period_floor = calculate_initial_pwm_period("floor_hydronic")
        assert period_floor == 900

        # Radiator should have moderate period (10 min = 600 sec)
        period_rad = calculate_initial_pwm_period("radiator")
        assert period_rad == 600

        # Convector should have shorter period (5 min = 300 sec)
        period_conv = calculate_initial_pwm_period("convector")
        assert period_conv == 300

        # Forced air should have shortest period (3 min = 180 sec)
        period_air = calculate_initial_pwm_period("forced_air")
        assert period_air == 180

        # Longer period for slower heating systems
        assert period_floor > period_rad > period_conv > period_air

    def test_pwm_period_unknown_type(self):
        """Test fallback for unknown heating type."""
        period_unknown = calculate_initial_pwm_period("unknown_type")
        # Should default to 600 seconds (10 minutes)
        assert period_unknown == 600

    def test_pwm_period_no_args(self):
        """Test default PWM period when no args provided."""
        period_default = calculate_initial_pwm_period()
        # Should default to floor_hydronic (900 seconds)
        assert period_default == 900
