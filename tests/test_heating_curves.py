"""Tests for heating curves (outdoor temperature compensation)."""

import pytest
from custom_components.adaptive_thermostat.adaptive.heating_curves import (
    calculate_weather_compensation,
    calculate_recommended_ke,
    apply_outdoor_compensation_to_pid_output,
)


class TestWeatherCompensation:
    """Tests for weather compensation calculation."""

    def test_weather_compensation_calculation(self):
        """Test basic weather compensation calculation."""
        # Indoor setpoint 20°C, outdoor 0°C, ke=1.0
        # Compensation should be 1.0 * (20 - 0) = 20.0
        compensation = calculate_weather_compensation(20.0, 0.0, ke=1.0)
        assert compensation == pytest.approx(20.0)

    def test_no_compensation_when_ke_zero(self):
        """Test no compensation when ke=0.0."""
        compensation = calculate_weather_compensation(20.0, 0.0, ke=0.0)
        assert compensation == 0.0

    def test_compensation_decreases_with_warmer_outdoor(self):
        """Test compensation decreases as outdoor temp increases."""
        # Indoor setpoint 20°C, ke=1.0
        comp_cold = calculate_weather_compensation(20.0, 0.0, ke=1.0)
        comp_mild = calculate_weather_compensation(20.0, 10.0, ke=1.0)
        comp_warm = calculate_weather_compensation(20.0, 15.0, ke=1.0)

        assert comp_cold > comp_mild > comp_warm
        assert comp_cold == pytest.approx(20.0)
        assert comp_mild == pytest.approx(10.0)
        assert comp_warm == pytest.approx(5.0)

    def test_zero_compensation_when_outdoor_equals_setpoint(self):
        """Test zero compensation when outdoor temp equals setpoint."""
        compensation = calculate_weather_compensation(20.0, 20.0, ke=1.0)
        assert compensation == pytest.approx(0.0)

    def test_different_ke_values(self):
        """Test different ke coefficients."""
        # Indoor 20°C, outdoor 0°C
        comp_ke_05 = calculate_weather_compensation(20.0, 0.0, ke=0.5)
        comp_ke_10 = calculate_weather_compensation(20.0, 0.0, ke=1.0)
        comp_ke_20 = calculate_weather_compensation(20.0, 0.0, ke=2.0)

        assert comp_ke_05 == pytest.approx(10.0)  # 0.5 * 20
        assert comp_ke_10 == pytest.approx(20.0)  # 1.0 * 20
        assert comp_ke_20 == pytest.approx(40.0)  # 2.0 * 20


class TestRecommendedKe:
    """Tests for recommended ke calculation."""

    def test_excellent_insulation_low_ke(self):
        """Test excellent insulation gives low ke (v0.7.1: restored scaling)."""
        ke = calculate_recommended_ke("excellent", "radiator")
        assert ke == pytest.approx(0.3)  # 0.3 * 1.0

    def test_poor_insulation_high_ke(self):
        """Test poor insulation gives high ke (v0.7.1: restored scaling)."""
        ke = calculate_recommended_ke("poor", "radiator")
        assert ke == pytest.approx(1.5)  # 1.5 * 1.0

    def test_heating_type_adjustments(self):
        """Test heating type affects ke (v0.7.1: restored scaling)."""
        # Good insulation (0.5 base) with different heating types
        ke_floor = calculate_recommended_ke("good", "floor_hydronic")
        ke_radiator = calculate_recommended_ke("good", "radiator")
        ke_forced_air = calculate_recommended_ke("good", "forced_air")

        assert ke_floor == pytest.approx(0.4)   # 0.5 * 0.8
        assert ke_radiator == pytest.approx(0.5)  # 0.5 * 1.0
        assert ke_forced_air == pytest.approx(0.6)  # 0.5 * 1.2

    def test_combined_poor_insulation_fast_heating(self):
        """Test poor insulation with fast heating gives high ke (v0.7.1: restored scaling)."""
        ke = calculate_recommended_ke("poor", "forced_air")
        assert ke == pytest.approx(1.8)  # 1.5 * 1.2

    def test_unknown_values_use_defaults(self):
        """Test unknown insulation/heating types use defaults (v0.7.1: restored scaling)."""
        ke = calculate_recommended_ke("unknown", "unknown")
        assert ke == pytest.approx(0.5)  # good insulation (0.5) * radiator (1.0)

    def test_ke_clamped_to_valid_range(self):
        """Test ke is clamped to 0.0-2.0 range (v0.7.1: restored scaling)."""
        # This shouldn't happen with current values, but test the clamping logic
        ke = calculate_recommended_ke("poor", "forced_air")
        assert 0.0 <= ke <= 2.0


class TestOutdoorCompensationToPIDOutput:
    """Tests for applying outdoor compensation to PID output."""

    def test_outdoor_temp_effect_on_output(self):
        """Test outdoor temperature affects PID output."""
        # PID output 30, setpoint 20°C, ke=1.0
        # Cold outdoor (0°C): 30 + 20 = 50
        # Mild outdoor (10°C): 30 + 10 = 40
        # Warm outdoor (15°C): 30 + 5 = 35
        output_cold = apply_outdoor_compensation_to_pid_output(
            30.0, 20.0, 0.0, ke=1.0
        )
        output_mild = apply_outdoor_compensation_to_pid_output(
            30.0, 20.0, 10.0, ke=1.0
        )
        output_warm = apply_outdoor_compensation_to_pid_output(
            30.0, 20.0, 15.0, ke=1.0
        )

        assert output_cold == pytest.approx(50.0)
        assert output_mild == pytest.approx(40.0)
        assert output_warm == pytest.approx(35.0)

    def test_output_clamped_to_max(self):
        """Test output is clamped to maximum value."""
        # PID output 30, outdoor 0°C, ke=1.0, setpoint 20°C
        # Would be 30 + 20 = 50, but max is 45
        output = apply_outdoor_compensation_to_pid_output(
            30.0, 20.0, 0.0, ke=1.0, max_output=45.0
        )
        assert output == pytest.approx(45.0)

    def test_output_clamped_to_min(self):
        """Test output is clamped to minimum value."""
        # PID output 5, outdoor 20°C (equals setpoint), ke=1.0
        # Would be 5 + 0 = 5, but min is 10
        output = apply_outdoor_compensation_to_pid_output(
            5.0, 20.0, 20.0, ke=1.0, min_output=10.0
        )
        assert output == pytest.approx(10.0)

    def test_no_compensation_when_outdoor_temp_none(self):
        """Test no compensation when outdoor temp unavailable."""
        # Should return original output (clamped)
        output = apply_outdoor_compensation_to_pid_output(
            30.0, 20.0, None, ke=1.0
        )
        assert output == pytest.approx(30.0)

    def test_no_compensation_when_ke_zero(self):
        """Test no compensation when ke=0.0."""
        output = apply_outdoor_compensation_to_pid_output(
            30.0, 20.0, 0.0, ke=0.0
        )
        assert output == pytest.approx(30.0)

    def test_realistic_scenario(self):
        """Test realistic heating scenario."""
        # PID output 25%, outdoor -5°C, indoor setpoint 21°C, ke=0.5
        # Compensation: 0.5 * (21 - (-5)) = 0.5 * 26 = 13
        # Total: 25 + 13 = 38
        output = apply_outdoor_compensation_to_pid_output(
            25.0, 21.0, -5.0, ke=0.5, min_output=0.0, max_output=100.0
        )
        assert output == pytest.approx(38.0)
