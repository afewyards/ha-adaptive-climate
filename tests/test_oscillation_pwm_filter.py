"""Tests for PWM mode oscillation filtering in adaptive learning."""

from datetime import datetime, timedelta
import pytest

from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_thermostat.adaptive.pid_rules import PIDRule


class TestOscillationCountingPWMFilter:
    """Tests for oscillation counting and PWM mode filtering."""

    def test_oscillation_counting_pwm_mode_filters_rules(self):
        """Test that oscillation rules are filtered out in PWM mode."""
        learner = AdaptiveLearner()

        # Create cycles with high oscillation count (would normally trigger rules)
        for _ in range(6):
            cycle = CycleMetrics(
                overshoot=0.1,  # Low overshoot
                undershoot=0.1,  # Low undershoot
                settling_time=30.0,  # Fast settling
                oscillations=5,  # High oscillations (would trigger rule)
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # In PWM mode (pwm_seconds > 0), oscillation rules should be filtered
        recommendation = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            pwm_seconds=300,  # PWM mode active (5 minutes)
        )

        # Should return None because only oscillation rules triggered,
        # and they were filtered out in PWM mode
        assert recommendation is None

    def test_oscillation_counting_valve_mode_triggers_rules(self):
        """Test that oscillation rules still fire in valve mode."""
        learner = AdaptiveLearner()

        # Create cycles with high oscillation count
        for _ in range(6):
            cycle = CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30.0,
                oscillations=5,  # High oscillations
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # In valve mode (pwm_seconds = 0), oscillation rules should fire
        recommendation = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            pwm_seconds=0,  # Valve mode
        )

        # Should return a recommendation with reduced Kp (oscillation rule)
        assert recommendation is not None
        assert recommendation["kp"] < 100.0  # Kp reduced
        assert recommendation["kd"] > 2.0  # Kd increased

    def test_heater_cycles_separate_from_oscillations(self):
        """Test that heater_cycles is tracked separately from oscillations."""
        # Create cycle with high heater cycles but low temperature oscillations
        cycle = CycleMetrics(
            overshoot=0.1,
            undershoot=0.1,
            settling_time=30.0,
            oscillations=1,  # Low temp oscillations
            rise_time=15.0,
            heater_cycles=10,  # Many heater on/off cycles (PWM cycling)
        )

        # Verify metrics are separate
        assert cycle.oscillations == 1
        assert cycle.heater_cycles == 10

        # heater_cycles is informational only - doesn't affect learning
        learner = AdaptiveLearner()
        for _ in range(6):
            learner.add_cycle_metrics(cycle)

        # Should not trigger oscillation rules despite high heater_cycles
        recommendation = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            pwm_seconds=0,  # Even in valve mode
        )

        # No recommendation because temp oscillations are low
        assert recommendation is None

    def test_pwm_mode_allows_non_oscillation_rules(self):
        """Test that PWM mode only filters oscillation rules, not other rules."""
        learner = AdaptiveLearner()

        # Create cycles with high overshoot (should trigger overshoot rule)
        for _ in range(6):
            cycle = CycleMetrics(
                overshoot=1.5,  # High overshoot - should trigger rule
                undershoot=0.1,
                settling_time=30.0,
                oscillations=5,  # High oscillations (would trigger rule in valve mode)
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # In PWM mode, oscillation rules filtered but overshoot rules still fire
        recommendation = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            pwm_seconds=300,  # PWM mode
        )

        # Should return recommendation from overshoot rule (not oscillation rule)
        assert recommendation is not None
        assert recommendation["kp"] < 100.0  # Kp reduced due to overshoot

    def test_pwm_zero_treated_as_valve_mode(self):
        """Test that pwm_seconds=0 is treated as valve mode."""
        learner = AdaptiveLearner()

        # Create cycles with oscillations
        for _ in range(6):
            cycle = CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30.0,
                oscillations=5,
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # pwm_seconds=0 means valve mode - no filtering
        recommendation = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            pwm_seconds=0,
        )

        # Oscillation rules should fire
        assert recommendation is not None
        assert recommendation["kp"] < 100.0

    def test_pwm_mode_default_parameter(self):
        """Test that pwm_seconds defaults to 0 (valve mode) if not provided."""
        learner = AdaptiveLearner()

        # Create cycles with oscillations
        for _ in range(6):
            cycle = CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30.0,
                oscillations=5,
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # Default behavior (no pwm_seconds parameter) should be valve mode
        recommendation = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            # pwm_seconds not specified - defaults to 0
        )

        # Oscillation rules should fire (default is valve mode)
        assert recommendation is not None
        assert recommendation["kp"] < 100.0

    def test_pwm_small_period_still_filters(self):
        """Test that even small PWM periods trigger filtering."""
        learner = AdaptiveLearner()

        # Create cycles with oscillations
        for _ in range(6):
            cycle = CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30.0,
                oscillations=5,
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # Even 1 second PWM period should filter oscillation rules
        recommendation = learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            pwm_seconds=1,  # Minimal PWM period
        )

        # Oscillation rules should be filtered
        assert recommendation is None

    def test_cycle_metrics_heater_cycles_default(self):
        """Test that heater_cycles defaults to 0."""
        cycle = CycleMetrics(
            overshoot=0.5,
            undershoot=0.3,
            settling_time=45.0,
            oscillations=2,
            rise_time=20.0,
        )

        assert cycle.heater_cycles == 0

    def test_cycle_metrics_heater_cycles_explicit(self):
        """Test that heater_cycles can be set explicitly."""
        cycle = CycleMetrics(
            overshoot=0.5,
            undershoot=0.3,
            settling_time=45.0,
            oscillations=2,
            rise_time=20.0,
            heater_cycles=15,
        )

        assert cycle.heater_cycles == 15

    def test_pwm_mode_logging_message(self, caplog):
        """Test that PWM mode filtering logs appropriate message."""
        import logging
        caplog.set_level(logging.DEBUG)

        learner = AdaptiveLearner()

        # Create cycles with oscillations
        for _ in range(6):
            cycle = CycleMetrics(
                overshoot=0.1,
                undershoot=0.1,
                settling_time=30.0,
                oscillations=5,
                rise_time=15.0,
            )
            learner.add_cycle_metrics(cycle)

        # Call with PWM mode
        learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=1.0,
            current_kd=2.0,
            pwm_seconds=300,
        )

        # Check for PWM filtering log message
        assert any("PWM mode active" in record.message for record in caplog.records)
        assert any("filtered out" in record.message for record in caplog.records)
        assert any("expected behavior" in record.message for record in caplog.records)


class TestCountOscillationsDocumentation:
    """Tests verifying count_oscillations behavior matches its documentation."""

    def test_counts_temperature_crossings_not_heater_cycles(self):
        """Test that count_oscillations counts temp crossings, not heater cycles."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            count_oscillations,
        )

        # Temperature oscillates around target (crosses 3 times)
        history = [
            (datetime(2024, 1, 1, 10, 0), 19.5),  # Below target
            (datetime(2024, 1, 1, 10, 15), 20.5),  # Above target (crossing 1)
            (datetime(2024, 1, 1, 10, 30), 19.8),  # Below target (crossing 2)
            (datetime(2024, 1, 1, 10, 45), 20.3),  # Above target (crossing 3)
        ]

        oscillations = count_oscillations(history, target_temp=20.0, threshold=0.1)

        # Should count 3 temperature crossings
        assert oscillations == 3

    def test_docstring_mentions_temperature_oscillations(self):
        """Test that count_oscillations docstring clarifies it counts temp oscillations."""
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
            count_oscillations,
        )

        docstring = count_oscillations.__doc__

        # Check docstring mentions key clarifications
        assert "temperature oscillations" in docstring.lower()
        assert "not heater" in docstring.lower() or "not heater cycles" in docstring.lower()
        assert "pwm" in docstring.lower()
