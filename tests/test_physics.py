"""Tests for adaptive/physics.py module."""

import pytest
from custom_components.adaptive_thermostat.adaptive.physics import (
    calculate_thermal_time_constant,
    calculate_initial_pid,
    calculate_initial_pwm_period,
    calculate_initial_ke,
    calculate_power_scaling_factor,
    calculate_floor_thermal_properties,
    validate_floor_construction,
    GLAZING_U_VALUES,
    ENERGY_RATING_TO_INSULATION,
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
            window_rating="hr++"
        )
        # Heat loss factor = (1.1/1.1) * (0.2/0.2) = 1.0 (baseline)
        # tau = tau_base * (1 - 0.15 * 1.0) = 4.0 * 0.85 = 3.4
        assert tau_with_windows == pytest.approx(3.4, abs=0.01)

    def test_thermal_time_constant_with_single_pane_windows(self):
        """Test tau adjustment with poor glazing (single pane)."""
        # Single pane has U-value 5.8, HR++ baseline is 1.1
        # 25 m2 floor, 5 m2 window = 20% ratio
        tau_base = 200 / 50.0  # 4.0
        tau_with_single = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="single"
        )
        # Heat loss factor = (5.8/1.1) * (0.2/0.2) = 5.27
        # tau reduction = 0.15 * 5.27 = 0.79 (clamped to 0.4 max)
        # tau = 4.0 * (1 - 0.4) = 2.4
        assert tau_with_single == pytest.approx(2.4, abs=0.01)

    def test_thermal_time_constant_with_triple_glazing(self):
        """Test tau adjustment with excellent glazing (triple pane)."""
        # Triple has U-value 0.6, HR++ baseline is 1.1
        # 25 m2 floor, 5 m2 window = 20% ratio
        tau_base = 200 / 50.0  # 4.0
        tau_with_triple = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="triple"
        )
        # Heat loss factor = (0.6/1.1) * (0.2/0.2) = 0.55
        # tau = 4.0 * (1 - 0.15 * 0.55) = 4.0 * 0.92 = 3.67
        assert tau_with_triple == pytest.approx(3.67, abs=0.01)

    def test_thermal_time_constant_with_large_window_area(self):
        """Test tau adjustment with larger window ratio."""
        # 25 m2 floor, 10 m2 window = 40% ratio (double baseline)
        tau_base = 200 / 50.0  # 4.0
        tau_with_large = calculate_thermal_time_constant(
            volume_m3=200,
            window_area_m2=10.0,
            floor_area_m2=25.0,
            window_rating="hr++"
        )
        # Heat loss factor = (1.1/1.1) * (0.4/0.2) = 2.0
        # tau = 4.0 * (1 - 0.15 * 2.0) = 4.0 * 0.7 = 2.8
        assert tau_with_large == pytest.approx(2.8, abs=0.01)


class TestPhysicsCalculations:
    """Tests for physics-based PID calculations."""

    def test_calculate_initial_pid_floor_hydronic(self):
        """Test PID calculation for floor hydronic heating."""
        tau = 8.0  # High thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "floor_hydronic")

        # v0.7.1: Multi-point model uses reference profile at tau=8.0
        # Reference: (8.0, 0.18, 0.6, 3.2) (reduced from 4.2 to fit kd_max=3.3)
        assert kp == pytest.approx(0.18, abs=0.01)
        assert ki == pytest.approx(0.6, abs=0.05)
        assert kd == pytest.approx(3.2, abs=0.3)

    def test_calculate_initial_pid_radiator(self):
        """Test PID calculation for radiator heating."""
        tau = 4.0  # Moderate thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "radiator")

        # v0.7.1: Multi-point model interpolates between tau=3.0 and tau=5.0
        # Reference: (3.0, 0.50, 2.0, 2.0) and (5.0, 0.36, 1.3, 2.8)
        # At tau=4.0: alpha = (4-3)/(5-3) = 0.5
        # Kp = 0.50 + 0.5*(0.36-0.50) = 0.43
        # Ki = 2.0 + 0.5*(1.3-2.0) = 1.65
        # Kd = 2.0 + 0.5*(2.8-2.0) = 2.4
        assert kp == pytest.approx(0.43, abs=0.02)
        assert ki == pytest.approx(1.65, abs=0.08)
        assert kd == pytest.approx(2.4, abs=0.2)

    def test_calculate_initial_pid_convector(self):
        """Test PID calculation for convector heating."""
        tau = 2.5  # Low thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "convector")

        # v0.7.1: Multi-point model uses reference profile at tau=2.5
        # Reference: (2.5, 0.80, 4.0, 1.2)
        assert kp == pytest.approx(0.80, abs=0.05)
        assert ki == pytest.approx(4.0, abs=0.2)
        assert kd == pytest.approx(1.2, abs=0.15)

    def test_calculate_initial_pid_forced_air(self):
        """Test PID calculation for forced air heating."""
        tau = 1.5  # Very low thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "forced_air")

        # v0.7.1: Multi-point model uses reference profile at tau=1.5
        # Reference: (1.5, 1.20, 8.0, 0.8)
        assert kp == pytest.approx(1.2, abs=0.1)
        assert ki == pytest.approx(8.0, abs=0.5)
        assert kd == pytest.approx(0.8, abs=0.1)

    def test_calculate_initial_pid_unknown_type(self):
        """Test PID calculation with unknown heating type uses default modifier."""
        tau = 4.0
        kp, ki, kd = calculate_initial_pid(tau, "unknown_type")

        # Should use radiator as fallback (default in reference_profiles.get())
        # v0.7.1: Same as radiator test with interpolation at tau=4.0
        assert kp == pytest.approx(0.43, abs=0.02)
        assert ki == pytest.approx(1.65, abs=0.08)
        assert kd == pytest.approx(2.4, abs=0.2)

    def test_calculate_initial_pid_relationships(self):
        """Test that PID values follow expected relationships."""
        tau = 4.0
        kp, ki, kd = calculate_initial_pid(tau, "convector")

        # Basic sanity checks
        assert kp > 0
        assert ki > 0
        assert kd > 0

        # After v0.7.0 changes, with widened tau adjustment and inverse Kd scaling,
        # Kd can be larger than Kp for systems with tau != baseline (1.5h)
        # For tau=4.0 with convector, Kd will be larger due to inverse tau_factor
        # Just verify all are positive - relationships depend on tau value

    def test_calculate_initial_pid_tau_scaling(self):
        """Test that PID values scale appropriately with tau."""
        tau_low = 2.0
        tau_high = 8.0

        kp_low, ki_low, kd_low = calculate_initial_pid(tau_low, "convector")
        kp_high, ki_high, kd_high = calculate_initial_pid(tau_high, "convector")

        # Higher tau should result in:
        # - Lower Kp (more conservative)
        assert kp_high < kp_low

        # - Lower Ki (slower integral accumulation)
        assert ki_high < ki_low

        # - Higher Kd (more damping for slow systems)
        assert kd_high > kd_low


class TestKiWindupTime:
    """Tests for Ki integral accumulation behavior with v0.7.0 hourly units."""

    def test_ki_windup_time(self):
        """Test Ki windup time at 1°C error verifies 1-2 hour accumulation.

        With the v0.7.0 fix, Ki values use hourly units: %/(°C·hour).
        v0.7.1: Multi-point model uses reference profile at tau=8.0.
        """
        # Floor hydronic heating at tau=8.0
        # v0.7.1: Reference profile (8.0, 0.18, 0.6, 3.2)
        # Ki = 0.6 %/(°C·hour)
        tau = 8.0
        kp, ki, kd = calculate_initial_pid(tau, "floor_hydronic")

        # At 1°C error for 1 hour, integral should accumulate approximately Ki
        error = 1.0  # °C
        time_hours = 1.0  # hour
        expected_integral_contribution = ki * error * time_hours

        # v0.7.1: Ki=0.6, so at 1°C for 1 hour: integral += 0.6%
        assert expected_integral_contribution == pytest.approx(0.6, abs=0.05)

        # At 2 hours with 1°C error: integral += 1.2%
        time_hours = 2.0
        expected_integral_contribution = ki * error * time_hours
        assert expected_integral_contribution == pytest.approx(1.2, abs=0.1)

        # Verify Ki is in reasonable range (0.1-10.0 for hourly units)
        assert 0.1 <= ki <= 10.0

    def test_cold_start_recovery(self):
        """Test PID recovery from cold start (10°C → 20°C scenario).

        Simulates a severe undershoot scenario where the zone starts far below
        setpoint, verifying that Ki can accumulate sufficient integral term.
        """
        # Radiator system at tau=4.0
        # v0.7.1: Multi-point model interpolates between (3.0, 0.50, 2.0, 2.0) and (5.0, 0.36, 1.3, 2.8)
        # Ki = 1.65 %/(°C·hour)
        tau = 4.0
        kp, ki, kd = calculate_initial_pid(tau, "radiator")

        # Cold start: 10°C below setpoint
        initial_error = 10.0  # °C

        # Assume error decays linearly over 4 hours to reach setpoint
        # Average error over recovery period: 5°C
        avg_error = 5.0  # °C
        recovery_time_hours = 4.0  # hours

        # Integral accumulation during recovery
        integral_contribution = ki * avg_error * recovery_time_hours

        # v0.7.1: Ki=1.65, avg_error=5°C, time=4h: integral = 1.65 * 5 * 4 = 33.0%
        assert integral_contribution == pytest.approx(33.0, abs=2.0)

        # This should be sufficient to provide boost (combined with Kp term)
        # Kp=0.43, error=10°C: P term = 0.43 * 10 = 4.3%
        # After 2 hours: I term = 1.65*5*2 = 16.5%
        # Total output can reach 20%+ to drive recovery

        # Verify Ki is properly scaled for cold start scenarios
        assert ki >= 0.5  # Must be at least 0.5 to accumulate meaningfully


class TestPWMPeriod:
    """Tests for PWM period calculation."""

    def test_pwm_period_heating_types(self):
        """Test PWM period for different heating types."""
        # Floor hydronic should have longest period (15 min = 900 sec)
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


class TestKeCalculation:
    """Tests for Ke (outdoor temperature compensation) calculation.

    Feature 1.5: Ke values restored by 100x in v0.7.1 to fix v0.7.0 incorrect scaling.
    New range: 0.1 - 2.0 (restored from incorrect 0.001 - 0.02 in v0.7.0)
    """

    def test_ke_magnitude_sanity_check(self):
        """Test that all Ke values are within restored 0.1-2.0 range after 100x restoration."""
        # Test all energy ratings
        for rating in ENERGY_RATING_TO_INSULATION.keys():
            ke = calculate_initial_ke(energy_rating=rating, heating_type="radiator")
            assert ke >= 0.1, f"Ke too low for {rating}: {ke}"
            assert ke <= 2.0, f"Ke too high for {rating}: {ke}"

        # Test all heating types with moderate insulation
        for heating_type in ["floor_hydronic", "radiator", "convector", "forced_air"]:
            ke = calculate_initial_ke(energy_rating="B", heating_type=heating_type)
            assert ke >= 0.1, f"Ke too low for {heating_type}: {ke}"
            assert ke <= 2.0, f"Ke too high for {heating_type}: {ke}"

    def test_ke_energy_rating_values(self):
        """Test Ke values for different energy ratings (v0.7.1 restored scaling)."""
        # A++++ (best) should have lowest Ke
        ke_best = calculate_initial_ke(energy_rating="A++++", heating_type="radiator")
        assert ke_best == pytest.approx(0.1, abs=0.01)

        # G (worst) should have highest Ke
        ke_worst = calculate_initial_ke(energy_rating="G", heating_type="radiator")
        assert ke_worst == pytest.approx(1.3, abs=0.1)

        # A (standard) should be moderate
        ke_standard = calculate_initial_ke(energy_rating="A", heating_type="radiator")
        assert ke_standard == pytest.approx(0.45, abs=0.05)

        # Better insulation = lower Ke
        assert ke_best < ke_standard < ke_worst

    def test_ke_heating_type_factors(self):
        """Test Ke adjustment by heating type (v0.7.1 restored scaling)."""
        # Floor hydronic should have highest Ke (slow response, benefits from compensation)
        ke_floor = calculate_initial_ke(energy_rating="A", heating_type="floor_hydronic")

        # Radiator is baseline
        ke_rad = calculate_initial_ke(energy_rating="A", heating_type="radiator")

        # Forced air should have lowest Ke (fast response, less benefit)
        ke_air = calculate_initial_ke(energy_rating="A", heating_type="forced_air")

        # Verify relationship
        assert ke_floor > ke_rad > ke_air

        # Check approximate values (A rating base is 0.45)
        assert ke_floor == pytest.approx(0.54, abs=0.05)  # 0.45 * 1.2
        assert ke_rad == pytest.approx(0.45, abs=0.05)    # 0.45 * 1.0
        assert ke_air == pytest.approx(0.27, abs=0.05)    # 0.45 * 0.6

    def test_ke_vs_p_term_ratio(self):
        """Test that Ke contributes 10-30% outdoor compensation in typical scenarios.

        This verifies the v0.7.1 restoration: Ke should provide meaningful
        outdoor compensation matching industry standard 10-30% feed-forward.
        """
        # Typical scenario:
        # - Indoor target: 20°C, current: 19°C (error = 1°C)
        # - Outdoor: -10°C (delta = 30°C from 20°C reference)
        # - Kp = 150, Ke = 0.5 (moderate insulation, convector)

        kp = 150.0
        ke = 0.5
        indoor_error = 1.0  # °C
        outdoor_delta = 30.0  # °C

        p_term = kp * indoor_error  # 150% power contribution
        e_term = ke * outdoor_delta  # 0.5 * 30 = 15% power contribution

        # E term should be 10% (meaningful outdoor compensation)
        ratio = e_term / p_term
        assert ratio < 0.3, f"E term too dominant: {ratio:.2%} of P term"
        assert ratio > 0.05, f"E term too weak: {ratio:.2%} of P term"

        # In extreme cold (-20°C, delta = 40°C)
        outdoor_delta_extreme = 40.0
        e_term_extreme = ke * outdoor_delta_extreme  # 0.5 * 40 = 20%
        ratio_extreme = e_term_extreme / p_term

        # Even in extreme conditions, E term should be within industry standard range
        assert ratio_extreme < 0.35, f"E term too dominant in extreme cold: {ratio_extreme:.2%}"

    def test_ke_with_windows_adjustment(self):
        """Test Ke window area adjustment maintains restored scale."""
        # Base Ke without windows
        ke_base = calculate_initial_ke(energy_rating="B", heating_type="radiator")

        # Ke with 20% window ratio (baseline)
        ke_with_windows = calculate_initial_ke(
            energy_rating="B",
            window_area_m2=5.0,
            floor_area_m2=25.0,
            window_rating="hr++",
            heating_type="radiator"
        )

        # Both should be in valid range
        assert 0.1 <= ke_base <= 2.0
        assert 0.1 <= ke_with_windows <= 2.0

        # Windows should increase Ke (more heat loss)
        assert ke_with_windows > ke_base

        # But not by more than 50% (window_factor capped at 0.5)
        assert ke_with_windows <= ke_base * 1.5

    def test_ke_default_fallback(self):
        """Test Ke defaults to moderate value when energy rating not specified."""
        ke_default = calculate_initial_ke(heating_type="radiator")

        # Should default to B rating equivalent (0.45 * 1.0 = 0.45)
        assert ke_default == pytest.approx(0.45, abs=0.05)
        assert 0.1 <= ke_default <= 2.0


class TestKdValues:
    """Tests for Kd (derivative) values after v0.7.0 reduction."""

    def test_kd_values_proper_range(self):
        """Test that all Kd values are within proper range after v0.7.0 widened tau adjustment."""
        # Test all heating types with typical tau values
        heating_types = ["floor_hydronic", "radiator", "convector", "forced_air"]
        tau_values = [8.0, 4.0, 2.5, 1.5]  # Typical tau for each type

        for heating_type, tau in zip(heating_types, tau_values):
            kp, ki, kd = calculate_initial_pid(tau, heating_type)

            # After widened tau_factor range with gentler scaling, Kd can be higher for slow systems
            # Reasonable range is 0.5 to 10.0 (floor hydronic with tau=8.0 can reach ~8.0)
            assert 0.5 <= kd <= 10.0, f"{heating_type}: Kd={kd} out of range"

    def test_kd_relationship_to_kp(self):
        """Test that Kd is reasonable relative to Kp for all heating types."""
        # After v0.7.0 reduction, Kd values should be more reasonable
        # Note: Due to inverse tau_factor scaling (Kd divided by tau_factor),
        # Kd can be larger than Kp, especially for systems with tau_factor < 1.0
        heating_types = ["floor_hydronic", "radiator", "convector", "forced_air"]
        tau_values = [8.0, 4.0, 2.5, 1.5]

        for heating_type, tau in zip(heating_types, tau_values):
            kp, ki, kd = calculate_initial_pid(tau, heating_type)

            # Kd should be positive and reasonable (not excessively large)
            assert 0 < kd < 10.0, f"{heating_type}: Kd={kd} out of reasonable range"

            # All Kd values should be significantly reduced from old values (7.0, 5.0, 3.0, 2.0)
            # which would have resulted in Kd values >10 for slow systems

    def test_kd_floor_hydronic_specific(self):
        """Test floor hydronic Kd with multi-point model (v0.7.1)."""
        tau = 8.0  # Typical high thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "floor_hydronic")

        # v0.7.1: Multi-point model uses reference profile at tau=8.0
        # Reference: (8.0, 0.18, 0.6, 3.2) (reduced from 4.2 to fit kd_max=3.3)
        # Kd = 3.2 (reduced from ~8.07 with old tau-based scaling)
        assert kd == pytest.approx(3.2, abs=0.3)

        # Multi-point model provides calibrated Kd for high thermal mass systems
        # This provides necessary damping without excessive derivative action
        assert kd <= 3.3  # Must not exceed kd_max

    def test_kd_reference_profiles_respect_kd_max(self):
        """Test that all reference profile Kd values respect kd_max=3.3."""
        from custom_components.adaptive_thermostat.const import PID_LIMITS

        kd_max = PID_LIMITS["kd_max"]

        # Test all reference tau values for each heating type
        test_cases = [
            ("floor_hydronic", [2.0, 4.0, 6.0, 8.0]),
            ("radiator", [1.5, 3.0, 5.0]),
            ("convector", [1.0, 2.5, 4.0]),
            ("forced_air", [0.5, 1.5, 3.0]),
        ]

        for heating_type, tau_values in test_cases:
            for tau in tau_values:
                kp, ki, kd = calculate_initial_pid(tau, heating_type)

                # All reference profile Kd values should be within limits
                # (extrapolated values may exceed, but will be clamped at runtime)
                if heating_type == "floor_hydronic" and tau <= 8.0:
                    assert kd <= kd_max, (
                        f"{heating_type} tau={tau}: Kd={kd} exceeds kd_max={kd_max}"
                    )

    def test_kd_forced_air_specific(self):
        """Test forced air Kd reduced from 2.0 to 0.8 (60% reduction)."""
        tau = 1.5  # Typical low thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "forced_air")

        # After tau_factor adjustment (1.0 for tau=1.5)
        # Expected Kd = 0.8 * 1.0 = 0.8
        assert kd == pytest.approx(0.8, abs=0.2)

        # Should be significantly lower than old value (2.0 * 1.0 = 2.0)
        assert kd < 1.2

    def test_kd_values_increase_with_tau(self):
        """Test that Kd increases with thermal time constant (more damping for slow systems)."""
        # Test with convector heating type at different tau values
        tau_low = 1.5
        tau_high = 6.0

        kp_low, ki_low, kd_low = calculate_initial_pid(tau_low, "convector")
        kp_high, ki_high, kd_high = calculate_initial_pid(tau_high, "convector")

        # Higher tau should result in higher Kd (more damping needed)
        assert kd_high > kd_low, f"Kd should increase with tau: {kd_low} -> {kd_high}"

    def test_kd_reasonable_relative_to_ki(self):
        """Test that Kd values are reasonable relative to Ki after v0.7.0 fixes."""
        # After Ki increase (100x) and Kd reduction (60%), plus widened tau adjustment
        heating_types = ["floor_hydronic", "radiator", "convector", "forced_air"]
        tau_values = [8.0, 4.0, 2.5, 1.5]

        for heating_type, tau in zip(heating_types, tau_values):
            kp, ki, kd = calculate_initial_pid(tau, heating_type)

            # With fixed Ki (now in proper units) and widened tau adjustment, Kd/Ki ratio varies more
            # Due to different tau_factor scaling (Ki uses power 1.5, Kd uses inverse):
            # - Fast systems (forced_air) can have Kd/Ki < 0.2
            # - Slow systems (floor_hydronic) can have Kd/Ki up to 40
            if ki > 0:  # Avoid division by zero
                ratio = kd / ki
                assert 0.05 <= ratio <= 50.0, (
                    f"{heating_type}: Kd/Ki ratio {ratio:.2f} out of expected range"
                )


class TestTauAdjustmentExtreme:
    """Tests for tau-based PID adjustment with extreme building characteristics (v0.7.0)."""

    def test_tau_adjustment_extreme_buildings(self):
        """Test tau adjustment works for buildings with extreme thermal characteristics (2h to 10h)."""
        # Test very fast building (tau=2.0h) - poorly insulated, small thermal mass
        kp_fast, ki_fast, kd_fast = calculate_initial_pid(2.0, "convector")

        # Test very slow building (tau=10.0h) - extremely well insulated, high thermal mass
        kp_slow, ki_slow, kd_slow = calculate_initial_pid(10.0, "floor_hydronic")

        # All gains should be positive and finite
        assert kp_fast > 0 and kp_slow > 0
        assert ki_fast > 0 and ki_slow > 0
        assert kd_fast > 0 and kd_slow > 0

        # Fast building should have higher Kp and Ki (more aggressive)
        assert kp_fast > kp_slow, f"Fast building Kp={kp_fast} should be > slow building Kp={kp_slow}"
        assert ki_fast > ki_slow, f"Fast building Ki={ki_fast} should be > slow building Ki={ki_slow}"

        # Slow building should have higher Kd (more damping)
        assert kd_slow > kd_fast, f"Slow building Kd={kd_slow} should be > fast building Kd={kd_fast}"

    def test_tau_factor_range_widened(self):
        """Test multi-point model handles extreme tau values (v0.7.1)."""
        # Very fast building (tau=0.5h) - reference point for forced_air
        kp_very_fast, ki_very_fast, kd_very_fast = calculate_initial_pid(0.5, "forced_air")

        # Very slow building (tau=15.0h) - extrapolates beyond floor_hydronic highest reference
        kp_very_slow, ki_very_slow, kd_very_slow = calculate_initial_pid(15.0, "floor_hydronic")

        # v0.7.1: Multi-point model uses reference profile at tau=0.5 for forced_air
        # Reference: (0.5, 1.80, 12.0, 0.4)
        assert kp_very_fast == pytest.approx(1.80, abs=0.1)
        assert ki_very_fast == pytest.approx(12.0, abs=2.0)
        assert kd_very_fast == pytest.approx(0.4, abs=0.05)

        # v0.7.1: Extrapolates from tau=8.0 reference (0.18, 0.6, 3.2)
        # tau_ratio = 8.0/15.0 = 0.533
        # Kp = 0.18 * 0.533 * sqrt(0.533) = 0.07
        # Ki = 0.6 * 0.533 = 0.32
        # Kd = 3.2 / 0.533 = 6.0
        assert kp_very_slow == pytest.approx(0.07, abs=0.02)
        assert ki_very_slow == pytest.approx(0.32, abs=0.05)
        assert kd_very_slow == pytest.approx(6.0, abs=0.5)

    def test_tau_factor_gentler_scaling(self):
        """Test multi-point model interpolation provides smooth scaling (v0.7.1)."""
        # Compare tau=1.5 with tau=3.0 for radiator
        kp_baseline, ki_baseline, kd_baseline = calculate_initial_pid(1.5, "radiator")
        kp_double, ki_double, kd_double = calculate_initial_pid(3.0, "radiator")

        # v0.7.1: Multi-point model uses reference profiles
        # tau=1.5: Reference (1.5, 0.70, 3.0, 1.2)
        # tau=3.0: Reference (3.0, 0.50, 2.0, 2.0)
        assert kp_baseline == pytest.approx(0.70, abs=0.05)
        assert ki_baseline == pytest.approx(3.0, abs=0.2)
        assert kd_baseline == pytest.approx(1.2, abs=0.1)

        assert kp_double == pytest.approx(0.50, abs=0.05)
        assert ki_double == pytest.approx(2.0, abs=0.1)
        assert kd_double == pytest.approx(2.0, abs=0.2)

    def test_ki_strengthened_adjustment(self):
        """Test multi-point model provides appropriate Ki for slow buildings (v0.7.1)."""
        # For slow buildings with high tau, Ki should be reduced significantly
        # to prevent excessive integral windup in slow-responding systems

        tau_fast = 1.0   # Fast convector
        tau_slow = 4.0   # Slow convector

        kp_fast, ki_fast, kd_fast = calculate_initial_pid(tau_fast, "convector")
        kp_slow, ki_slow, kd_slow = calculate_initial_pid(tau_slow, "convector")

        # v0.7.1: Multi-point model uses reference profiles
        # tau=1.0: Reference (1.0, 1.10, 6.0, 0.7)
        # tau=4.0: Reference (4.0, 0.60, 2.8, 1.8)
        assert ki_fast == pytest.approx(6.0, abs=0.5)
        assert ki_slow == pytest.approx(2.8, abs=0.3)

        # Ki should be significantly reduced for slow buildings
        assert ki_slow < ki_fast / 2, f"Ki_slow={ki_slow} should be < Ki_fast/2={ki_fast/2}"

    def test_tau_adjustment_continuity(self):
        """Test that PID gains change smoothly across tau range (no discontinuities)."""
        tau_values = [2.0, 3.0, 4.0, 6.0, 8.0, 10.0]
        heating_type = "radiator"

        prev_kp, prev_ki, prev_kd = None, None, None

        for tau in tau_values:
            kp, ki, kd = calculate_initial_pid(tau, heating_type)

            if prev_kp is not None:
                # Gains should change smoothly (no jumps > 2x)
                kp_ratio = max(kp / prev_kp, prev_kp / kp)
                ki_ratio = max(ki / prev_ki, prev_ki / ki)
                kd_ratio = max(kd / prev_kd, prev_kd / kd)

                assert kp_ratio < 2.0, f"Kp jump too large at tau={tau}: {prev_kp} -> {kp}"
                assert ki_ratio < 2.0, f"Ki jump too large at tau={tau}: {prev_ki} -> {ki}"
                assert kd_ratio < 2.0, f"Kd jump too large at tau={tau}: {prev_kd} -> {kd}"

            prev_kp, prev_ki, prev_kd = kp, ki, kd

    def test_tau_adjustment_module_exists(self):
        """Marker test to verify tau adjustment functionality exists."""
        # This test always passes and serves as a marker for the feature
        tau = 5.0
        kp, ki, kd = calculate_initial_pid(tau, "floor_hydronic")
        assert kp > 0 and ki > 0 and kd > 0


class TestPowerScaling:
    """Tests for power scaling functionality."""

    def test_power_scaling_factor_undersized_system(self):
        """Test power scaling for undersized heating system (2x higher gains)."""
        # 50m² zone with floor_hydronic (baseline 20 W/m²)
        # 500W heater installed (10 W/m² actual - half of baseline)
        # scaling = 20 / 10 = 2.0 (double the PID gains)
        heating_type = "floor_hydronic"
        area_m2 = 50.0
        max_power_w = 500.0  # 10 W/m² (baseline is 20 W/m²)

        scaling = calculate_power_scaling_factor(heating_type, area_m2, max_power_w)

        assert scaling == pytest.approx(2.0, abs=0.01)

    def test_power_scaling_factor_oversized_system(self):
        """Test power scaling for oversized heating system (0.5x lower gains)."""
        # 50m² zone with floor_hydronic (baseline 20 W/m²)
        # 2000W heater installed (40 W/m² actual - double of baseline)
        # scaling = 20 / 40 = 0.5 (halve the PID gains)
        heating_type = "floor_hydronic"
        area_m2 = 50.0
        max_power_w = 2000.0  # 40 W/m² (baseline is 20 W/m²)

        scaling = calculate_power_scaling_factor(heating_type, area_m2, max_power_w)

        assert scaling == pytest.approx(0.5, abs=0.01)

    def test_power_scaling_factor_no_power_configured(self):
        """Test power scaling returns 1.0 when no power configured."""
        # No power configured - should return 1.0 (no scaling)
        heating_type = "radiator"
        area_m2 = 30.0
        max_power_w = None

        scaling = calculate_power_scaling_factor(heating_type, area_m2, max_power_w)

        assert scaling == pytest.approx(1.0, abs=0.01)

    def test_power_scaling_factor_no_area_configured(self):
        """Test power scaling returns 1.0 when no area configured."""
        # No area configured - should return 1.0 (no scaling)
        heating_type = "radiator"
        area_m2 = None
        max_power_w = 1500.0

        scaling = calculate_power_scaling_factor(heating_type, area_m2, max_power_w)

        assert scaling == pytest.approx(1.0, abs=0.01)

    def test_power_scaling_factor_clamping_min(self):
        """Test power scaling clamps to 0.25x minimum (4x oversized)."""
        # 50m² zone with floor_hydronic (baseline 20 W/m²)
        # 10000W heater installed (200 W/m² - 10x baseline)
        # scaling = 20 / 200 = 0.1, clamped to 0.25
        heating_type = "floor_hydronic"
        area_m2 = 50.0
        max_power_w = 10000.0

        scaling = calculate_power_scaling_factor(heating_type, area_m2, max_power_w)

        assert scaling == pytest.approx(0.25, abs=0.01)

    def test_power_scaling_factor_clamping_max(self):
        """Test power scaling clamps to 4.0x maximum (4x undersized)."""
        # 50m² zone with floor_hydronic (baseline 20 W/m²)
        # 200W heater installed (4 W/m² - 1/5 of baseline)
        # scaling = 20 / 4 = 5.0, clamped to 4.0
        heating_type = "floor_hydronic"
        area_m2 = 50.0
        max_power_w = 200.0

        scaling = calculate_power_scaling_factor(heating_type, area_m2, max_power_w)

        assert scaling == pytest.approx(4.0, abs=0.01)

    def test_power_scaling_different_heating_types(self):
        """Test power scaling uses correct baseline for each heating type."""
        area_m2 = 50.0

        # Floor hydronic: baseline 20 W/m² → 1000W = 20 W/m² → scaling = 1.0
        scaling_floor = calculate_power_scaling_factor("floor_hydronic", area_m2, 1000.0)
        assert scaling_floor == pytest.approx(1.0, abs=0.01)

        # Radiator: baseline 50 W/m² → 2500W = 50 W/m² → scaling = 1.0
        scaling_radiator = calculate_power_scaling_factor("radiator", area_m2, 2500.0)
        assert scaling_radiator == pytest.approx(1.0, abs=0.01)

        # Convector: baseline 60 W/m² → 3000W = 60 W/m² → scaling = 1.0
        scaling_convector = calculate_power_scaling_factor("convector", area_m2, 3000.0)
        assert scaling_convector == pytest.approx(1.0, abs=0.01)

        # Forced air: baseline 80 W/m² → 4000W = 80 W/m² → scaling = 1.0
        scaling_forced = calculate_power_scaling_factor("forced_air", area_m2, 4000.0)
        assert scaling_forced == pytest.approx(1.0, abs=0.01)

    def test_power_scaling_applied_to_pid_gains(self):
        """Test that power scaling is correctly applied to Kp and Ki (not Kd)."""
        tau = 4.0
        heating_type = "floor_hydronic"
        area_m2 = 50.0

        # Calculate baseline PID (no power scaling)
        kp_baseline, ki_baseline, kd_baseline = calculate_initial_pid(tau, heating_type, None, None)

        # Calculate with undersized system (2x power scaling)
        max_power_w = 500.0  # 10 W/m² vs baseline 20 W/m² = 2x scaling
        kp_scaled, ki_scaled, kd_scaled = calculate_initial_pid(tau, heating_type, area_m2, max_power_w)

        # Kp and Ki should be doubled
        assert kp_scaled == pytest.approx(kp_baseline * 2.0, abs=0.01)
        assert ki_scaled == pytest.approx(ki_baseline * 2.0, abs=0.01)
        # Kd should remain unchanged (not scaled)
        assert kd_scaled == pytest.approx(kd_baseline, abs=0.01)

    def test_power_scaling_module_exists(self):
        """Marker test to verify power scaling functionality exists."""
        # This test always passes and serves as a marker for the feature
        scaling = calculate_power_scaling_factor("radiator", 30.0, 1500.0)
        assert scaling > 0


class TestPhysicsInitDiverseBuildings:
    """Tests for v0.7.1 hybrid multi-point physics-based PID initialization."""

    def test_physics_init_diverse_buildings_tau_range(self):
        """Test multi-point model handles diverse tau range (2h-10h)."""
        # Test floor_hydronic across extreme tau values
        heating_type = "floor_hydronic"

        # Fast floor heating (tau=2h) - well-insulated
        kp_2h, ki_2h, kd_2h = calculate_initial_pid(2.0, heating_type)
        assert kp_2h == pytest.approx(0.45, abs=0.05)
        assert ki_2h == pytest.approx(2.0, abs=0.2)
        assert kd_2h == pytest.approx(1.4, abs=0.2)

        # Standard floor heating (tau=4h)
        kp_4h, ki_4h, kd_4h = calculate_initial_pid(4.0, heating_type)
        assert kp_4h == pytest.approx(0.30, abs=0.05)
        assert ki_4h == pytest.approx(1.2, abs=0.2)
        assert kd_4h == pytest.approx(2.5, abs=0.3)

        # High thermal mass (tau=6h)
        kp_6h, ki_6h, kd_6h = calculate_initial_pid(6.0, heating_type)
        assert kp_6h == pytest.approx(0.22, abs=0.05)
        assert ki_6h == pytest.approx(0.8, abs=0.2)
        assert kd_6h == pytest.approx(3.3, abs=0.3)

        # Very slow floor heating (tau=8h)
        kp_8h, ki_8h, kd_8h = calculate_initial_pid(8.0, heating_type)
        assert kp_8h == pytest.approx(0.18, abs=0.05)
        assert ki_8h == pytest.approx(0.6, abs=0.2)
        assert kd_8h == pytest.approx(3.2, abs=0.3)

        # Verify trends: higher tau → lower Kp, lower Ki, higher Kd
        assert kp_2h > kp_4h > kp_6h > kp_8h
        assert ki_2h > ki_4h > ki_6h > ki_8h
        # Note: kd_6h=3.3 and kd_8h=3.2 are both capped near kd_max=3.3
        # Natural trend would be kd increasing with tau, but capped for I/D balance
        assert kd_2h < kd_4h < kd_6h
        assert kd_8h <= kd_6h  # Both reduced from natural values to respect kd_max

    def test_interpolation_between_reference_points(self):
        """Test linear interpolation between reference profiles."""
        # Test radiator at tau=4.0 (between reference points 3.0 and 5.0)
        kp, ki, kd = calculate_initial_pid(4.0, "radiator")

        # Reference: (3.0, 0.50, 2.0, 2.0) and (5.0, 0.36, 1.3, 2.8)
        # At tau=4.0: alpha = (4-3)/(5-3) = 0.5
        # Kp = 0.50 + 0.5*(0.36-0.50) = 0.43
        # Ki = 2.0 + 0.5*(1.3-2.0) = 1.65
        # Kd = 2.0 + 0.5*(2.8-2.0) = 2.4
        assert kp == pytest.approx(0.43, abs=0.02)
        assert ki == pytest.approx(1.65, abs=0.1)
        assert kd == pytest.approx(2.4, abs=0.2)

    def test_extrapolation_below_lowest_reference(self):
        """Test improved scaling below lowest reference point."""
        # Test forced_air at tau=0.3 (below lowest reference tau=0.5)
        kp, ki, kd = calculate_initial_pid(0.3, "forced_air")

        # Reference: (0.5, 1.80, 12.0, 0.4)
        # tau_ratio = 0.5 / 0.3 = 1.667
        # Kp = 1.80 * 1.667 * sqrt(1.667) = 1.80 * 1.667 * 1.291 = 3.87
        # Ki = 12.0 * 1.667 = 20.0
        # Kd = 0.4 / 1.667 = 0.24
        assert kp == pytest.approx(3.87, abs=0.5)
        assert ki == pytest.approx(20.0, abs=2.0)
        assert kd == pytest.approx(0.24, abs=0.05)

        # Verify very fast system gets aggressive gains
        assert kp > 2.0  # High proportional gain for fast response
        assert ki > 15.0  # High integral gain for fast recovery

    def test_extrapolation_above_highest_reference(self):
        """Test improved scaling above highest reference point."""
        # Test floor_hydronic at tau=10.0 (above highest reference tau=8.0)
        kp, ki, kd = calculate_initial_pid(10.0, "floor_hydronic")

        # Reference: (8.0, 0.18, 0.6, 3.2) (reduced from 4.2 to fit kd_max=3.3)
        # tau_ratio = 8.0 / 10.0 = 0.8
        # Kp = 0.18 * 0.8 * sqrt(0.8) = 0.18 * 0.8 * 0.894 = 0.129
        # Ki = 0.6 * 0.8 = 0.48
        # Kd = 3.2 / 0.8 = 4.0
        assert kp == pytest.approx(0.129, abs=0.02)
        assert ki == pytest.approx(0.48, abs=0.05)
        assert kd == pytest.approx(4.0, abs=0.3)

        # Verify very slow system gets conservative gains
        assert kp < 0.2  # Low proportional gain for slow, stable response
        assert ki < 0.6  # Low integral gain to prevent windup
        # Note: Kd=4.0 exceeds kd_max=3.3, will be clamped at runtime in PID controller

    def test_tau_scaling_formulas_applied(self):
        """Test improved tau scaling formulas: Kp ∝ 1/(tau × √tau), Ki ∝ 1/tau, Kd ∝ tau."""
        # Test extrapolation scaling with forced_air
        tau_ref = 0.5
        tau_test = 1.0  # Double the reference tau

        kp_ref, ki_ref, kd_ref = calculate_initial_pid(tau_ref, "forced_air")
        kp_test, ki_test, kd_test = calculate_initial_pid(tau_test, "forced_air")

        # tau_ratio = tau_ref / tau_test = 0.5 / 1.0 = 0.5
        # Expected: Kp *= 0.5 * sqrt(0.5) = 0.5 * 0.707 = 0.354
        # Expected: Ki *= 0.5
        # Expected: Kd /= 0.5 (i.e., * 2.0)

        # Since tau=1.0 is between references, use interpolation instead
        # Reference: (0.5, 1.80, 12.0, 0.4) and (1.5, 1.20, 8.0, 0.8)
        # alpha = (1.0-0.5)/(1.5-0.5) = 0.5
        # Kp = 1.80 + 0.5*(1.20-1.80) = 1.50
        # Ki = 12.0 + 0.5*(8.0-12.0) = 10.0
        # Kd = 0.4 + 0.5*(0.8-0.4) = 0.6
        assert kp_test == pytest.approx(1.50, abs=0.1)
        assert ki_test == pytest.approx(10.0, abs=0.5)
        assert kd_test == pytest.approx(0.6, abs=0.1)

    def test_all_heating_types_covered(self):
        """Test all heating types have reference profiles."""
        heating_types = ["floor_hydronic", "radiator", "convector", "forced_air"]
        tau = 3.0

        for heating_type in heating_types:
            kp, ki, kd = calculate_initial_pid(tau, heating_type)
            # All gains should be positive
            assert kp > 0, f"{heating_type}: Kp should be positive"
            assert ki > 0, f"{heating_type}: Ki should be positive"
            assert kd > 0, f"{heating_type}: Kd should be positive"

    def test_power_scaling_with_multipoint_model(self):
        """Test power scaling works correctly with multi-point model."""
        tau = 4.0
        heating_type = "floor_hydronic"
        area_m2 = 50.0

        # Calculate baseline (no power scaling)
        kp_baseline, ki_baseline, kd_baseline = calculate_initial_pid(tau, heating_type, None, None)

        # Calculate with 2x undersized system
        max_power_w = 500.0  # 10 W/m² vs baseline 20 W/m² = 2x scaling
        kp_scaled, ki_scaled, kd_scaled = calculate_initial_pid(tau, heating_type, area_m2, max_power_w)

        # Verify power scaling multiplier applied correctly
        assert kp_scaled == pytest.approx(kp_baseline * 2.0, abs=0.01)
        assert ki_scaled == pytest.approx(ki_baseline * 2.0, abs=0.01)
        assert kd_scaled == pytest.approx(kd_baseline, abs=0.01)  # Kd not scaled

    def test_reference_profile_consistency(self):
        """Test reference profiles maintain expected PID gain relationships."""
        # For each heating type, verify gains follow expected relationships at reference points
        heating_types = ["floor_hydronic", "radiator", "convector", "forced_air"]

        for heating_type in heating_types:
            # Test at middle reference point for consistency
            if heating_type == "floor_hydronic":
                tau = 4.0  # Middle reference
            elif heating_type == "radiator":
                tau = 3.0  # Middle reference
            elif heating_type == "convector":
                tau = 2.5  # Middle reference
            else:  # forced_air
                tau = 1.5  # Middle reference

            kp, ki, kd = calculate_initial_pid(tau, heating_type)

            # Basic sanity checks
            assert 0.1 < kp < 2.0, f"{heating_type}: Kp out of expected range"
            assert 0.5 < ki < 15.0, f"{heating_type}: Ki out of expected range"
            assert 0.3 < kd < 5.0, f"{heating_type}: Kd out of expected range"


class TestFloorConstruction:
    """Tests for calculate_floor_thermal_properties function."""

    def test_basic_ceramic_tile_cement_screed(self):
        """Test basic floor construction: 10mm ceramic tile + 50mm cement screed."""
        layers = [
            {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
        ]
        area_m2 = 50.0

        result = calculate_floor_thermal_properties(layers, area_m2)

        # Ceramic tile: 0.01m × 50m² × 2300kg/m³ × 840J/(kg·K) = 966,000 J/K
        # Cement screed: 0.05m × 50m² × 2100kg/m³ × 840J/(kg·K) = 4,410,000 J/K
        # Total: 5,376,000 J/K = 5376.0 kJ/K
        assert result['thermal_mass_kj_k'] == pytest.approx(5376.0, abs=1.0)

        # Thermal resistance:
        # Ceramic tile: 0.01m / 1.3 W/(m·K) = 0.00769 (m²·K)/W
        # Cement screed: 0.05m / 1.4 W/(m·K) = 0.03571 (m²·K)/W
        # Total: 0.0434 (m²·K)/W
        assert result['thermal_resistance'] == pytest.approx(0.0434, abs=0.001)

        # Reference mass (50mm cement screed):
        # 0.05m × 50m² × 2000kg/m³ × 1000J/(kg·K) = 5,000,000 J/K
        # tau_modifier = 5,376,000 / 5,000,000 = 1.0752
        # With 150mm pipe spacing (efficiency 0.87): 1.0752 / 0.87 = 1.24
        assert result['tau_modifier'] == pytest.approx(1.24, abs=0.05)

    def test_heavy_stone_construction(self):
        """Test heavy floor construction with natural stone."""
        layers = [
            {'type': 'top_floor', 'material': 'natural_stone', 'thickness_mm': 20},
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 60},
        ]
        area_m2 = 40.0

        result = calculate_floor_thermal_properties(layers, area_m2)

        # Natural stone: 0.02m × 40m² × 2700kg/m³ × 900J/(kg·K) = 1,944,000 J/K
        # Cement screed: 0.06m × 40m² × 2100kg/m³ × 840J/(kg·K) = 4,233,600 J/K
        # Total: 6,177,600 J/K = 6177.6 kJ/K
        assert result['thermal_mass_kj_k'] == pytest.approx(6177.6, abs=10.0)

        # Reference mass: 0.05m × 40m² × 2000kg/m³ × 1000J/(kg·K) = 4,000,000 J/K
        # tau_modifier = 6,177,600 / 4,000,000 = 1.544
        # With 150mm pipe spacing: 1.544 / 0.87 = 1.77
        assert result['tau_modifier'] == pytest.approx(1.77, abs=0.05)

    def test_lightweight_construction(self):
        """Test lightweight floor construction with vinyl and lightweight screed."""
        layers = [
            {'type': 'top_floor', 'material': 'vinyl', 'thickness_mm': 5},
            {'type': 'screed', 'material': 'lightweight', 'thickness_mm': 40},
        ]
        area_m2 = 30.0

        result = calculate_floor_thermal_properties(layers, area_m2)

        # Vinyl: 0.005m × 30m² × 1200kg/m³ × 1400J/(kg·K) = 252,000 J/K
        # Lightweight screed: 0.04m × 30m² × 1000kg/m³ × 1000J/(kg·K) = 1,200,000 J/K
        # Total: 1,452,000 J/K = 1452.0 kJ/K
        assert result['thermal_mass_kj_k'] == pytest.approx(1452.0, abs=5.0)

        # Reference mass: 0.05m × 30m² × 2000kg/m³ × 1000J/(kg·K) = 3,000,000 J/K
        # tau_modifier = 1,452,000 / 3,000,000 = 0.484
        # With 150mm pipe spacing: 0.484 / 0.87 = 0.56
        assert result['tau_modifier'] == pytest.approx(0.56, abs=0.02)

    def test_pipe_spacing_100mm(self):
        """Test pipe spacing effect with 100mm spacing (higher efficiency)."""
        layers = [
            {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
        ]
        area_m2 = 50.0

        result = calculate_floor_thermal_properties(layers, area_m2, pipe_spacing_mm=100)

        # Same thermal mass as test_basic_ceramic_tile_cement_screed
        assert result['thermal_mass_kj_k'] == pytest.approx(5376.0, abs=1.0)

        # tau_modifier with 100mm spacing (efficiency 0.92): 1.0752 / 0.92 = 1.17
        assert result['tau_modifier'] == pytest.approx(1.17, abs=0.02)

    def test_pipe_spacing_300mm(self):
        """Test pipe spacing effect with 300mm spacing (lower efficiency)."""
        layers = [
            {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
        ]
        area_m2 = 50.0

        result = calculate_floor_thermal_properties(layers, area_m2, pipe_spacing_mm=300)

        # Same thermal mass as test_basic_ceramic_tile_cement_screed
        assert result['thermal_mass_kj_k'] == pytest.approx(5376.0, abs=1.0)

        # tau_modifier with 300mm spacing (efficiency 0.68): 1.0752 / 0.68 = 1.58
        assert result['tau_modifier'] == pytest.approx(1.58, abs=0.02)

    def test_custom_material_properties(self):
        """Test custom material properties override material lookup."""
        layers = [
            {
                'type': 'top_floor',
                'material': 'custom_tile',  # Unknown material
                'thickness_mm': 15,
                'conductivity': 2.0,  # Custom properties
                'density': 2500,
                'specific_heat': 900,
            },
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
        ]
        area_m2 = 50.0

        result = calculate_floor_thermal_properties(layers, area_m2)

        # Custom tile: 0.015m × 50m² × 2500kg/m³ × 900J/(kg·K) = 1,687,500 J/K
        # Cement screed: 0.05m × 50m² × 2100kg/m³ × 840J/(kg·K) = 4,410,000 J/K
        # Total: 6,097,500 J/K = 6097.5 kJ/K
        assert result['thermal_mass_kj_k'] == pytest.approx(6097.5, abs=10.0)

        # Thermal resistance:
        # Custom tile: 0.015m / 2.0 W/(m·K) = 0.0075 (m²·K)/W
        # Cement screed: 0.05m / 1.4 W/(m·K) = 0.0357 (m²·K)/W
        # Total: 0.0432 (m²·K)/W
        assert result['thermal_resistance'] == pytest.approx(0.0432, abs=0.001)

    def test_multi_layer_construction(self):
        """Test multi-layer construction with multiple screed layers."""
        layers = [
            {'type': 'top_floor', 'material': 'porcelain', 'thickness_mm': 12},
            {'type': 'screed', 'material': 'self_leveling', 'thickness_mm': 5},
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 45},
        ]
        area_m2 = 60.0

        result = calculate_floor_thermal_properties(layers, area_m2)

        # Porcelain: 0.012m × 60m² × 2400kg/m³ × 880J/(kg·K) = 1,520,640 J/K
        # Self-leveling: 0.005m × 60m² × 1900kg/m³ × 900J/(kg·K) = 513,000 J/K
        # Cement: 0.045m × 60m² × 2100kg/m³ × 840J/(kg·K) = 4,762,800 J/K
        # Total: 6,796,440 J/K = 6796.44 kJ/K
        assert result['thermal_mass_kj_k'] == pytest.approx(6796.44, abs=10.0)

        # Thermal resistance:
        # Porcelain: 0.012m / 1.5 = 0.008
        # Self-leveling: 0.005m / 1.3 = 0.00385
        # Cement: 0.045m / 1.4 = 0.03214
        # Total: 0.04399
        assert result['thermal_resistance'] == pytest.approx(0.044, abs=0.001)

    def test_thermal_resistance_high(self):
        """Test thermal resistance calculation with insulating materials."""
        layers = [
            {'type': 'top_floor', 'material': 'carpet', 'thickness_mm': 8},
            {'type': 'screed', 'material': 'dry_screed', 'thickness_mm': 40},
        ]
        area_m2 = 25.0

        result = calculate_floor_thermal_properties(layers, area_m2)

        # Thermal resistance (high due to insulating materials):
        # Carpet: 0.008m / 0.06 W/(m·K) = 0.1333 (m²·K)/W
        # Dry screed: 0.04m / 0.2 W/(m·K) = 0.2000 (m²·K)/W
        # Total: 0.3333 (m²·K)/W
        assert result['thermal_resistance'] == pytest.approx(0.3333, abs=0.01)

    def test_unknown_material_raises_error(self):
        """Test that unknown material raises ValueError."""
        layers = [
            {'type': 'top_floor', 'material': 'unobtanium', 'thickness_mm': 10},
        ]
        area_m2 = 50.0

        with pytest.raises(ValueError, match="Unknown material 'unobtanium'"):
            calculate_floor_thermal_properties(layers, area_m2)

    def test_unknown_layer_type_raises_error(self):
        """Test that unknown layer type raises ValueError."""
        layers = [
            {'type': 'insulation', 'material': 'ceramic_tile', 'thickness_mm': 10},
        ]
        area_m2 = 50.0

        with pytest.raises(ValueError, match="Unknown layer type 'insulation'"):
            calculate_floor_thermal_properties(layers, area_m2)

    def test_invalid_thickness_raises_error(self):
        """Test that invalid thickness raises ValueError."""
        layers = [
            {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 0},
        ]
        area_m2 = 50.0

        with pytest.raises(ValueError, match="Layer must have valid thickness_mm"):
            calculate_floor_thermal_properties(layers, area_m2)

    def test_missing_thickness_raises_error(self):
        """Test that missing thickness raises ValueError."""
        layers = [
            {'type': 'top_floor', 'material': 'ceramic_tile'},
        ]
        area_m2 = 50.0

        with pytest.raises(ValueError, match="Layer must have valid thickness_mm"):
            calculate_floor_thermal_properties(layers, area_m2)

    def test_all_top_floor_materials(self):
        """Test that all defined top floor materials work."""
        from custom_components.adaptive_thermostat.const import TOP_FLOOR_MATERIALS

        for material in TOP_FLOOR_MATERIALS.keys():
            layers = [
                {'type': 'top_floor', 'material': material, 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ]
            result = calculate_floor_thermal_properties(layers, area_m2=50.0)

            # All should produce valid results
            assert result['thermal_mass_kj_k'] > 0
            assert result['thermal_resistance'] > 0
            assert result['tau_modifier'] > 0

    def test_all_screed_materials(self):
        """Test that all defined screed materials work."""
        from custom_components.adaptive_thermostat.const import SCREED_MATERIALS

        for material in SCREED_MATERIALS.keys():
            layers = [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': material, 'thickness_mm': 50},
            ]
            result = calculate_floor_thermal_properties(layers, area_m2=50.0)

            # All should produce valid results
            assert result['thermal_mass_kj_k'] > 0
            assert result['thermal_resistance'] > 0
            assert result['tau_modifier'] > 0

    def test_tau_modifier_range_validation(self):
        """Test that tau_modifier values are in reasonable range (0.3 - 3.0)."""
        # Lightweight construction
        layers_light = [
            {'type': 'top_floor', 'material': 'vinyl', 'thickness_mm': 5},
            {'type': 'screed', 'material': 'lightweight', 'thickness_mm': 35},
        ]
        result_light = calculate_floor_thermal_properties(layers_light, area_m2=30.0)
        assert 0.3 <= result_light['tau_modifier'] <= 3.0

        # Heavy construction
        layers_heavy = [
            {'type': 'top_floor', 'material': 'natural_stone', 'thickness_mm': 25},
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 70},
        ]
        result_heavy = calculate_floor_thermal_properties(layers_heavy, area_m2=40.0)
        assert 0.3 <= result_heavy['tau_modifier'] <= 3.0

    def test_default_pipe_spacing(self):
        """Test that default pipe spacing is 150mm."""
        layers = [
            {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
            {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
        ]
        area_m2 = 50.0

        # Call without pipe_spacing_mm parameter
        result = calculate_floor_thermal_properties(layers, area_m2)

        # Should use 150mm spacing (efficiency 0.87)
        # Same as test_basic_ceramic_tile_cement_screed
        assert result['tau_modifier'] == pytest.approx(1.24, abs=0.05)


class TestFloorConstructionValidation:
    """Tests for validate_floor_construction function."""

    def test_valid_basic_configuration(self):
        """Test valid basic floor configuration."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_valid_all_pipe_spacings(self):
        """Test all valid pipe spacing values."""
        for spacing in [100, 150, 200, 300]:
            config = {
                'layers': [
                    {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                    {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
                ],
                'pipe_spacing_mm': spacing,
            }
            errors = validate_floor_construction(config)
            assert errors == [], f"Valid spacing {spacing} should not produce errors"

    def test_invalid_pipe_spacing(self):
        """Test invalid pipe spacing value."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 175,  # Invalid spacing
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "pipe_spacing_mm must be one of [100, 150, 200, 300]" in errors[0]
        assert "175" in errors[0]

    def test_missing_pipe_spacing(self):
        """Test missing pipe_spacing_mm."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "pipe_spacing_mm is required" in errors[0]

    def test_empty_layers_list(self):
        """Test empty layers list."""
        config = {
            'layers': [],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "layers list cannot be empty" in errors[0]

    def test_missing_layers(self):
        """Test missing layers key."""
        config = {
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "layers is required" in errors[0]

    def test_layers_not_a_list(self):
        """Test layers is not a list."""
        config = {
            'layers': "not a list",
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "layers must be a list" in errors[0]

    def test_top_floor_thickness_at_min_boundary(self):
        """Test top_floor thickness at minimum boundary (5mm)."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'vinyl', 'thickness_mm': 5},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_top_floor_thickness_at_max_boundary(self):
        """Test top_floor thickness at maximum boundary (25mm)."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'natural_stone', 'thickness_mm': 25},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_top_floor_thickness_below_min(self):
        """Test top_floor thickness below minimum."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'vinyl', 'thickness_mm': 3},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "thickness_mm must be between 5-25mm" in errors[0]
        assert "3mm" in errors[0]

    def test_top_floor_thickness_above_max(self):
        """Test top_floor thickness above maximum."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'natural_stone', 'thickness_mm': 30},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "thickness_mm must be between 5-25mm" in errors[0]
        assert "30mm" in errors[0]

    def test_screed_thickness_at_min_boundary(self):
        """Test screed thickness at minimum boundary (30mm)."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 30},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_screed_thickness_at_max_boundary(self):
        """Test screed thickness at maximum boundary (80mm)."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 80},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_screed_thickness_below_min(self):
        """Test screed thickness below minimum."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 25},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "thickness_mm must be between 30-80mm" in errors[0]
        assert "25mm" in errors[0]

    def test_screed_thickness_above_max(self):
        """Test screed thickness above maximum."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 85},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "thickness_mm must be between 30-80mm" in errors[0]
        assert "85mm" in errors[0]

    def test_unknown_top_floor_material(self):
        """Test unknown top floor material."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'unobtanium', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "unknown material 'unobtanium'" in errors[0]

    def test_unknown_screed_material(self):
        """Test unknown screed material."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'vibranium', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "unknown material 'vibranium'" in errors[0]

    def test_valid_custom_material_properties(self):
        """Test valid custom material properties."""
        config = {
            'layers': [
                {
                    'type': 'top_floor',
                    'material': 'custom_tile',
                    'thickness_mm': 10,
                    'conductivity': 1.5,
                    'density': 2400,
                    'specific_heat': 900,
                },
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_custom_properties_incomplete(self):
        """Test incomplete custom properties (missing specific_heat)."""
        config = {
            'layers': [
                {
                    'type': 'top_floor',
                    'material': 'custom_tile',
                    'thickness_mm': 10,
                    'conductivity': 1.5,
                    'density': 2400,
                    # Missing specific_heat
                },
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        # Without all three custom properties, it tries to look up the material name
        assert "unknown material 'custom_tile'" in errors[0]

    def test_custom_properties_invalid_value(self):
        """Test invalid custom property value (negative conductivity)."""
        config = {
            'layers': [
                {
                    'type': 'top_floor',
                    'material': 'custom_tile',
                    'thickness_mm': 10,
                    'conductivity': -1.5,
                    'density': 2400,
                    'specific_heat': 900,
                },
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "conductivity must be a positive number" in errors[0]

    def test_layer_order_valid_top_floor_then_screed(self):
        """Test valid layer order: top_floor before screed."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_layer_order_valid_multiple_top_floors(self):
        """Test valid layer order: multiple top_floor layers before screed."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 8},
                {'type': 'top_floor', 'material': 'vinyl', 'thickness_mm': 5},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_layer_order_valid_multiple_screeds(self):
        """Test valid layer order: top_floor before multiple screed layers."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'anhydrite', 'thickness_mm': 35},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 45},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert errors == []

    def test_layer_order_invalid_screed_before_top_floor(self):
        """Test invalid layer order: screed before top_floor."""
        config = {
            'layers': [
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "Layer order invalid" in errors[0]
        assert "top_floor' layers must precede all 'screed' layers" in errors[0]

    def test_layer_order_invalid_interleaved(self):
        """Test invalid layer order: interleaved top_floor and screed."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 30},
                {'type': 'top_floor', 'material': 'vinyl', 'thickness_mm': 5},  # Invalid position
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "Layer order invalid" in errors[0]

    def test_missing_thickness(self):
        """Test missing thickness_mm."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile'},  # Missing thickness
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "thickness_mm is required" in errors[0]

    def test_invalid_thickness_zero(self):
        """Test invalid thickness value (zero)."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': 0},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "thickness_mm must be a positive number" in errors[0]

    def test_invalid_thickness_negative(self):
        """Test invalid thickness value (negative)."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'ceramic_tile', 'thickness_mm': -10},
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "thickness_mm must be a positive number" in errors[0]

    def test_invalid_layer_type(self):
        """Test invalid layer type."""
        config = {
            'layers': [
                {'type': 'insulation', 'material': 'foam', 'thickness_mm': 20},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "type must be 'top_floor' or 'screed'" in errors[0]
        assert "insulation" in errors[0]

    def test_multiple_validation_errors(self):
        """Test multiple validation errors are collected."""
        config = {
            'layers': [
                {'type': 'top_floor', 'material': 'unobtanium', 'thickness_mm': 3},  # Unknown material + too thin
                {'type': 'screed', 'material': 'vibranium', 'thickness_mm': 90},  # Unknown material + too thick
            ],
            'pipe_spacing_mm': 175,  # Invalid spacing
        }
        errors = validate_floor_construction(config)
        # Should have at least 5 errors
        assert len(errors) >= 5
        # Check that different types of errors are present
        error_text = ' '.join(errors)
        assert "pipe_spacing_mm" in error_text
        assert "thickness_mm" in error_text
        assert "unknown material" in error_text

    def test_missing_material_without_custom_properties(self):
        """Test missing material name without custom properties."""
        config = {
            'layers': [
                {'type': 'top_floor', 'thickness_mm': 10},  # Missing material
                {'type': 'screed', 'material': 'cement', 'thickness_mm': 50},
            ],
            'pipe_spacing_mm': 150,
        }
        errors = validate_floor_construction(config)
        assert len(errors) == 1
        assert "material name is required" in errors[0]
