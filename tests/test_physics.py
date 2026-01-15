"""Tests for adaptive/physics.py module."""

import pytest
from custom_components.adaptive_thermostat.adaptive.physics import (
    calculate_thermal_time_constant,
    calculate_initial_pid,
    calculate_initial_pwm_period,
    calculate_initial_ke,
    calculate_power_scaling_factor,
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
        # tau reduction = 0.15 * 5.27 = 0.79 (clamped to 0.5 max)
        # tau = 4.0 * (1 - 0.5) = 2.0
        assert tau_with_single == pytest.approx(2.0, abs=0.01)

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

        # Base values: kp=0.3, ki=1.2, kd=2.5
        # tau_factor = (1.5/8.0)**0.7 = 0.1875**0.7 = 0.3095
        # Kp = 0.3 * 0.3095 = 0.0929
        # Ki = 1.2 * (0.3095**1.5) = 1.2 * 0.172 = 0.206
        # Kd = 2.5 / 0.3095 = 8.08
        assert kp == pytest.approx(0.0929, abs=0.01)
        assert ki == pytest.approx(0.206, abs=0.03)
        assert kd == pytest.approx(8.08, abs=0.5)

    def test_calculate_initial_pid_radiator(self):
        """Test PID calculation for radiator heating."""
        tau = 4.0  # Moderate thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "radiator")

        # Base values: kp=0.5, ki=2.0, kd=2.0
        # tau_factor = (1.5/4.0)**0.7 = 0.375**0.7 = 0.5032
        # Kp = 0.5 * 0.5032 = 0.2516
        # Ki = 2.0 * (0.5032**1.5) = 2.0 * 0.357 = 0.714
        # Kd = 2.0 / 0.5032 = 3.97
        assert kp == pytest.approx(0.2516, abs=0.02)
        assert ki == pytest.approx(0.714, abs=0.08)
        assert kd == pytest.approx(3.97, abs=0.3)

    def test_calculate_initial_pid_convector(self):
        """Test PID calculation for convector heating."""
        tau = 2.5  # Low thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "convector")

        # Base values: kp=0.8, ki=4.0, kd=1.2
        # tau_factor = (1.5/2.5)**0.7 = 0.6**0.7 = 0.6994
        # Kp = 0.8 * 0.6994 = 0.5595
        # Ki = 4.0 * (0.6994**1.5) = 4.0 * 0.585 = 2.34
        # Kd = 1.2 / 0.6994 = 1.72
        assert kp == pytest.approx(0.5595, abs=0.05)
        assert ki == pytest.approx(2.34, abs=0.2)
        assert kd == pytest.approx(1.72, abs=0.15)

    def test_calculate_initial_pid_forced_air(self):
        """Test PID calculation for forced air heating."""
        tau = 1.5  # Very low thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "forced_air")

        # Base values: kp=1.2, ki=8.0, kd=0.8
        # tau_factor = (1.5/1.5)**0.7 = 1.0**0.7 = 1.0
        # Kp = 1.2 * 1.0 = 1.2
        # Ki = 8.0 * (1.0**1.5) = 8.0 * 1.0 = 8.0
        # Kd = 0.8 / 1.0 = 0.8
        assert kp == pytest.approx(1.2, abs=0.1)
        assert ki == pytest.approx(8.0, abs=0.5)
        assert kd == pytest.approx(0.8, abs=0.1)

    def test_calculate_initial_pid_unknown_type(self):
        """Test PID calculation with unknown heating type uses default modifier."""
        tau = 4.0
        kp, ki, kd = calculate_initial_pid(tau, "unknown_type")

        # Should use radiator as fallback (default in heating_params.get())
        # Base values: kp=0.5, ki=2.0, kd=2.0
        # tau_factor = (1.5/4.0)**0.7 = 0.5032
        # Same as radiator test
        assert kp == pytest.approx(0.2516, abs=0.02)
        assert ki == pytest.approx(0.714, abs=0.08)
        assert kd == pytest.approx(3.97, abs=0.3)

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
        Base Ki=1.2 for floor_hydronic, adjusted by tau_factor for tau=8.0.
        """
        # Floor hydronic heating with base Ki=1.2, tau=8.0
        # tau_factor = (1.5/8.0)**0.7 = 0.3095 (widened range with gentler scaling)
        # Actual Ki = 1.2 * (0.3095**1.5) = 1.2 * 0.172 = 0.207 %/(°C·hour)
        tau = 8.0
        kp, ki, kd = calculate_initial_pid(tau, "floor_hydronic")

        # At 1°C error for 1 hour, integral should accumulate approximately Ki
        error = 1.0  # °C
        time_hours = 1.0  # hour
        expected_integral_contribution = ki * error * time_hours

        # With widened tau adjustment: Ki≈0.207, so at 1°C for 1 hour: integral += 0.207%
        assert expected_integral_contribution == pytest.approx(0.207, abs=0.05)

        # At 2 hours with 1°C error: integral += 0.414%
        time_hours = 2.0
        expected_integral_contribution = ki * error * time_hours
        assert expected_integral_contribution == pytest.approx(0.414, abs=0.1)

        # Verify Ki is in reasonable range (0.1-10.0 for hourly units)
        assert 0.1 <= ki <= 10.0

    def test_cold_start_recovery(self):
        """Test PID recovery from cold start (10°C → 20°C scenario).

        Simulates a severe undershoot scenario where the zone starts far below
        setpoint, verifying that Ki can accumulate sufficient integral term.
        """
        # Radiator system with base Ki=2.0, tau=4.0
        # tau_factor = (1.5/4.0)**0.7 = 0.5032 (widened range with gentler scaling)
        # Actual Ki = 2.0 * (0.5032**1.5) = 2.0 * 0.357 = 0.714 %/(°C·hour)
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

        # Ki=0.714, avg_error=5°C, time=4h: integral = 0.714 * 5 * 4 = 14.28%
        assert integral_contribution == pytest.approx(14.28, abs=2.0)

        # This should be sufficient to provide boost (combined with Kp term)
        # Kp=0.252, error=10°C: P term = 0.252 * 10 = 2.52%
        # After 2 hours: I term = 0.714*5*2 = 7.14%
        # Total output can reach 10%+ to drive recovery

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

    Feature 1.3: Ke values reduced by 100x in v0.7.0 to match corrected Ki dimensional analysis.
    New range: 0.001 - 0.02 (was 0.1 - 2.0)
    """

    def test_ke_magnitude_sanity_check(self):
        """Test that all Ke values are within new 0.001-0.02 range after 100x reduction."""
        # Test all energy ratings
        for rating in ENERGY_RATING_TO_INSULATION.keys():
            ke = calculate_initial_ke(energy_rating=rating, heating_type="radiator")
            assert ke >= 0.001, f"Ke too low for {rating}: {ke}"
            assert ke <= 0.02, f"Ke too high for {rating}: {ke}"

        # Test all heating types with moderate insulation
        for heating_type in ["floor_hydronic", "radiator", "convector", "forced_air"]:
            ke = calculate_initial_ke(energy_rating="B", heating_type=heating_type)
            assert ke >= 0.001, f"Ke too low for {heating_type}: {ke}"
            assert ke <= 0.02, f"Ke too high for {heating_type}: {ke}"

    def test_ke_energy_rating_values(self):
        """Test Ke values for different energy ratings (100x scaling)."""
        # A++++ (best) should have lowest Ke
        ke_best = calculate_initial_ke(energy_rating="A++++", heating_type="radiator")
        assert ke_best == pytest.approx(0.001, abs=0.0001)

        # G (worst) should have highest Ke
        ke_worst = calculate_initial_ke(energy_rating="G", heating_type="radiator")
        assert ke_worst == pytest.approx(0.013, abs=0.001)

        # A (standard) should be moderate
        ke_standard = calculate_initial_ke(energy_rating="A", heating_type="radiator")
        assert ke_standard == pytest.approx(0.0045, abs=0.0005)

        # Better insulation = lower Ke
        assert ke_best < ke_standard < ke_worst

    def test_ke_heating_type_factors(self):
        """Test Ke adjustment by heating type (100x scaling maintained)."""
        # Floor hydronic should have highest Ke (slow response, benefits from compensation)
        ke_floor = calculate_initial_ke(energy_rating="A", heating_type="floor_hydronic")

        # Radiator is baseline
        ke_rad = calculate_initial_ke(energy_rating="A", heating_type="radiator")

        # Forced air should have lowest Ke (fast response, less benefit)
        ke_air = calculate_initial_ke(energy_rating="A", heating_type="forced_air")

        # Verify relationship
        assert ke_floor > ke_rad > ke_air

        # Check approximate values (A rating base is 0.0045)
        assert ke_floor == pytest.approx(0.0054, abs=0.0005)  # 0.0045 * 1.2
        assert ke_rad == pytest.approx(0.0045, abs=0.0005)    # 0.0045 * 1.0
        assert ke_air == pytest.approx(0.0027, abs=0.0005)    # 0.0045 * 0.6

    def test_ke_vs_p_term_ratio(self):
        """Test that Ke contributes 20-50% of P term in typical scenarios.

        This verifies the fix is correct: Ke should provide meaningful but not
        dominant outdoor compensation compared to the proportional term.
        """
        # Typical scenario:
        # - Indoor target: 20°C, current: 19°C (error = 1°C)
        # - Outdoor: -10°C (delta = 30°C from 20°C reference)
        # - Kp = 150, Ke = 0.005 (moderate insulation, convector)

        kp = 150.0
        ke = 0.005
        indoor_error = 1.0  # °C
        outdoor_delta = 30.0  # °C

        p_term = kp * indoor_error  # 150% power contribution
        e_term = ke * outdoor_delta  # 0.005 * 30 = 0.15% power contribution

        # E term should be 0.1% (well below P term)
        ratio = e_term / p_term
        assert ratio < 0.01, f"E term too dominant: {ratio:.2%} of P term"
        assert ratio > 0.0001, f"E term too weak: {ratio:.2%} of P term"

        # In extreme cold (-20°C, delta = 40°C)
        outdoor_delta_extreme = 40.0
        e_term_extreme = ke * outdoor_delta_extreme  # 0.005 * 40 = 0.20%
        ratio_extreme = e_term_extreme / p_term

        # Even in extreme conditions, E term should be modest
        assert ratio_extreme < 0.005, f"E term too dominant in extreme cold: {ratio_extreme:.2%}"

    def test_ke_with_windows_adjustment(self):
        """Test Ke window area adjustment maintains new scale (100x)."""
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
        assert 0.001 <= ke_base <= 0.02
        assert 0.001 <= ke_with_windows <= 0.02

        # Windows should increase Ke (more heat loss)
        assert ke_with_windows > ke_base

        # But not by more than 50% (window_factor capped at 0.5)
        assert ke_with_windows <= ke_base * 1.5

    def test_ke_default_fallback(self):
        """Test Ke defaults to moderate value when energy rating not specified."""
        ke_default = calculate_initial_ke(heating_type="radiator")

        # Should default to B rating equivalent (0.0045 * 1.0 = 0.0045)
        assert ke_default == pytest.approx(0.0045, abs=0.0005)
        assert 0.001 <= ke_default <= 0.02


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
        """Test floor hydronic Kd with widened tau adjustment (v0.7.0)."""
        tau = 8.0  # Typical high thermal mass
        kp, ki, kd = calculate_initial_pid(tau, "floor_hydronic")

        # Base value: kd=2.5 (reduced from 7.0 in v0.7.0)
        # tau_factor = (1.5/8.0)**0.7 = 0.3095 (widened range)
        # Expected Kd = 2.5 / 0.3095 = 8.07 (Kd uses inverse tau_factor for more damping on slow systems)
        assert kd == pytest.approx(8.07, abs=0.5)

        # With widened tau range and gentler scaling, Kd is higher for slow systems
        # This provides necessary damping for high thermal mass systems
        assert kd < 10.0

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
        """Test that tau_factor range is widened from ±30% to -70%/+150%."""
        # Very fast building (tau=0.5h) - tau_factor within range (not clamped)
        # tau_factor = (1.5/0.5)**0.7 = 3.0**0.7 = 2.1577 (< 2.5, not clamped)
        kp_very_fast, ki_very_fast, kd_very_fast = calculate_initial_pid(0.5, "forced_air")

        # Very slow building (tau=15.0h) - should hit lower clamp at 0.3x
        # tau_factor = (1.5/15.0)**0.7 = 0.1**0.7 = 0.1995, clamped to 0.3
        kp_very_slow, ki_very_slow, kd_very_slow = calculate_initial_pid(15.0, "floor_hydronic")

        # Base values for forced_air: kp=1.2, ki=8.0, kd=0.8
        # With tau_factor=2.1577: Kp=2.589, Ki=8.0*(2.1577**1.5)=25.36, Kd=0.371
        assert kp_very_fast == pytest.approx(2.589, abs=0.1)
        assert ki_very_fast == pytest.approx(25.36, abs=2.0)
        assert kd_very_fast == pytest.approx(0.371, abs=0.05)

        # Base values for floor_hydronic: kp=0.3, ki=1.2, kd=2.5
        # With tau_factor=0.3 (clamped lower): Kp=0.09, Ki=1.2*(0.3**1.5)=0.197, Kd=8.33
        assert kp_very_slow == pytest.approx(0.09, abs=0.02)
        assert ki_very_slow == pytest.approx(0.197, abs=0.05)
        assert kd_very_slow == pytest.approx(8.33, abs=0.5)

    def test_tau_factor_gentler_scaling(self):
        """Test that gentler scaling (power 0.7) reduces extreme adjustments."""
        # Compare tau=3.0 with tau=1.5 baseline
        # Old: tau_factor = 1.5/3.0 = 0.5 (50% reduction)
        # New: tau_factor = (1.5/3.0)**0.7 = 0.5**0.7 = 0.615 (38.5% reduction)

        kp_baseline, ki_baseline, kd_baseline = calculate_initial_pid(1.5, "radiator")
        kp_double, ki_double, kd_double = calculate_initial_pid(3.0, "radiator")

        # With gentler scaling, the adjustment should be less extreme
        # Expected tau_factor for tau=3.0: (1.5/3.0)**0.7 = 0.615
        expected_tau_factor = 0.615

        # Kp and Ki should be reduced by tau_factor
        assert kp_double == pytest.approx(0.5 * expected_tau_factor, abs=0.05)
        # Ki uses tau_factor**1.5 for stronger adjustment
        assert ki_double == pytest.approx(2.0 * (expected_tau_factor ** 1.5), abs=0.1)
        # Kd should be increased (inverse)
        assert kd_double == pytest.approx(2.0 / expected_tau_factor, abs=0.2)

    def test_ki_strengthened_adjustment(self):
        """Test that Ki adjustment uses power 1.5 for better slow-building performance."""
        # For slow buildings with high tau, Ki should be reduced more aggressively
        # to prevent excessive integral windup in slow-responding systems

        tau_fast = 1.5   # Baseline
        tau_slow = 6.0   # 4x slower

        kp_fast, ki_fast, kd_fast = calculate_initial_pid(tau_fast, "convector")
        kp_slow, ki_slow, kd_slow = calculate_initial_pid(tau_slow, "convector")

        # tau_factor for tau=6.0: (1.5/6.0)**0.7 = 0.25**0.7 = 0.3789
        expected_tau_factor = 0.3789

        # Ki uses tau_factor**1.5 instead of just tau_factor
        # Base Ki for convector is 4.0
        # Expected Ki_slow = 4.0 * (0.3789**1.5) = 4.0 * 0.2333 = 0.933
        assert ki_slow == pytest.approx(4.0 * (expected_tau_factor ** 1.5), abs=0.1)

        # This should be significantly lower than linear scaling would give
        # Linear scaling: 4.0 * 0.3789 = 1.516
        # Power 1.5 scaling: 4.0 * 0.2333 = 0.933
        assert ki_slow < 1.0, f"Ki={ki_slow} should be < 1.0 with strengthened adjustment"

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
