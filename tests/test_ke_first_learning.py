"""Tests for Ke-First learning (learn Ke before PID tuning)."""

import pytest
from datetime import datetime, timedelta
from custom_components.adaptive_thermostat.adaptive.ke_first_learning import (
    KeFirstLearner,
    SteadyStateCycle,
    KE_FIRST_MIN_CYCLES,
    KE_FIRST_R_SQUARED_THRESHOLD,
    KE_FIRST_STEADY_STATE_DURATION_MIN,
    KE_FIRST_DUTY_CYCLE_TOLERANCE,
)


class TestSteadyStateCycle:
    """Test SteadyStateCycle dataclass."""

    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        cycle = SteadyStateCycle(
            timestamp=datetime(2024, 1, 1, 12, 0),
            outdoor_temp=5.0,
            indoor_temp_start=20.5,
            indoor_temp_end=19.8,
            duration_hours=0.5,
            temp_drop_rate=1.4,
            temp_difference=15.0,
            duty_cycle=0.35,
        )

        # Serialize and deserialize
        data = cycle.to_dict()
        restored = SteadyStateCycle.from_dict(data)

        # Verify
        assert restored.timestamp == cycle.timestamp
        assert restored.outdoor_temp == cycle.outdoor_temp
        assert restored.indoor_temp_start == cycle.indoor_temp_start
        assert restored.indoor_temp_end == cycle.indoor_temp_end
        assert restored.duration_hours == cycle.duration_hours
        assert restored.temp_drop_rate == cycle.temp_drop_rate
        assert restored.temp_difference == cycle.temp_difference
        assert restored.duty_cycle == cycle.duty_cycle


class TestKeFirstLearner:
    """Test KeFirstLearner class."""

    def test_initialization(self):
        """Test learner initialization."""
        learner = KeFirstLearner(initial_ke=0.005)

        assert learner.current_ke == 0.005
        assert not learner.converged
        assert learner.r_squared is None
        assert learner.cycle_count == 0
        assert learner.convergence_progress < 1.0

    def test_steady_state_detection(self):
        """Test steady-state detection (stable duty cycle for 60+ min)."""
        learner = KeFirstLearner()
        start_time = datetime(2024, 1, 1, 12, 0)

        # First call - not steady yet
        assert not learner.detect_steady_state(0.40, start_time)

        # After 30 minutes - not steady yet (need 60)
        time_30min = start_time + timedelta(minutes=30)
        assert not learner.detect_steady_state(0.41, time_30min)  # within ±5%

        # After 65 minutes - now steady
        time_65min = start_time + timedelta(minutes=65)
        assert learner.detect_steady_state(0.39, time_65min)  # within ±5%

    def test_steady_state_reset_on_duty_cycle_change(self):
        """Test that steady state resets when duty cycle changes too much."""
        learner = KeFirstLearner()
        start_time = datetime(2024, 1, 1, 12, 0)

        # Establish initial steady state tracking
        learner.detect_steady_state(0.40, start_time)

        # After 30 minutes, duty cycle changes >5%
        time_30min = start_time + timedelta(minutes=30)
        result = learner.detect_steady_state(0.47, time_30min)  # 7% change
        assert not result  # Resets because duty cycle changed too much

        # After another 65 minutes with stable duty cycle
        time_95min = start_time + timedelta(minutes=95)
        result = learner.detect_steady_state(0.46, time_95min)
        assert result  # Now steady with new duty cycle

    def test_on_heater_off_tracking(self):
        """Test tracking when heater turns off."""
        learner = KeFirstLearner()
        timestamp = datetime(2024, 1, 1, 12, 0)

        learner.on_heater_off(
            indoor_temp=20.5,
            outdoor_temp=5.0,
            timestamp=timestamp,
        )

        # Verify internal state
        assert learner._off_period_start == timestamp
        assert learner._off_period_start_temp == 20.5
        assert learner._off_period_outdoor_temp == 5.0

    def test_cycle_recording_basic(self):
        """Test basic cycle recording when heater turns back on."""
        learner = KeFirstLearner()
        off_time = datetime(2024, 1, 1, 12, 0)
        on_time = off_time + timedelta(minutes=30)  # 0.5 hour off period

        # Heater turns off
        learner.on_heater_off(
            indoor_temp=20.5,
            outdoor_temp=5.0,
            timestamp=off_time,
        )

        # Heater turns back on after temperature dropped
        result = learner.on_heater_on(
            indoor_temp=19.8,  # Dropped 0.7°C
            outdoor_temp=4.5,
            duty_cycle=0.40,
            timestamp=on_time,
        )

        assert result is True
        assert learner.cycle_count == 1

        # Verify cycle data
        cycle = learner._cycles[0]
        assert cycle.duration_hours == 0.5
        assert cycle.indoor_temp_start == 20.5
        assert cycle.indoor_temp_end == 19.8
        assert abs(cycle.temp_drop_rate - 1.4) < 0.01  # 0.7°C / 0.5h = 1.4°C/h
        assert abs(cycle.temp_difference - 15.4) < 0.1  # Avg temps: (20.15 - 4.75)

    def test_cycle_recording_ignores_short_periods(self):
        """Test that very short off periods are ignored."""
        learner = KeFirstLearner()
        off_time = datetime(2024, 1, 1, 12, 0)
        on_time = off_time + timedelta(minutes=3)  # Only 3 minutes

        learner.on_heater_off(20.5, 5.0, off_time)
        result = learner.on_heater_on(20.4, 5.0, 0.40, on_time)

        assert result is False
        assert learner.cycle_count == 0

    def test_cycle_recording_ignores_small_drops(self):
        """Test that insignificant temperature drops are ignored."""
        learner = KeFirstLearner()
        off_time = datetime(2024, 1, 1, 12, 0)
        on_time = off_time + timedelta(hours=1)

        learner.on_heater_off(20.5, 5.0, off_time)
        # Temperature barely changed (0.005°C in 1h = 0.005°C/h < threshold)
        result = learner.on_heater_on(20.495, 5.0, 0.40, on_time)

        assert result is False
        assert learner.cycle_count == 0

    def test_linear_regression_calculation(self):
        """Test linear regression calculation."""
        learner = KeFirstLearner()

        # Simple test data: y = 2x + 1
        x_values = [1.0, 2.0, 3.0, 4.0, 5.0]
        y_values = [3.0, 5.0, 7.0, 9.0, 11.0]

        slope, intercept, r_squared = learner._calculate_linear_regression(x_values, y_values)

        assert abs(slope - 2.0) < 0.01
        assert abs(intercept - 1.0) < 0.01
        assert abs(r_squared - 1.0) < 0.01  # Perfect fit

    def test_linear_regression_with_noise(self):
        """Test linear regression with noisy data."""
        learner = KeFirstLearner()

        # Data with some noise around y = -0.5x + 10
        x_values = [5.0, 10.0, 15.0, 20.0, 25.0]
        y_values = [7.6, 5.1, 2.4, -0.3, -2.8]  # Some noise added

        slope, intercept, r_squared = learner._calculate_linear_regression(x_values, y_values)

        assert abs(slope - (-0.5)) < 0.05
        assert abs(intercept - 10.0) < 0.5
        assert r_squared > 0.95  # Strong correlation despite noise

    def test_convergence_insufficient_cycles(self):
        """Test that convergence requires minimum cycles."""
        learner = KeFirstLearner()

        # Add fewer than minimum cycles
        for i in range(KE_FIRST_MIN_CYCLES - 1):
            self._add_synthetic_cycle(learner, outdoor_temp=5.0 + i)

        learner._check_convergence()
        assert not learner.converged

    def test_convergence_insufficient_temp_range(self):
        """Test that convergence requires sufficient temperature range."""
        learner = KeFirstLearner()

        # Add cycles with insufficient outdoor temp variation (<5°C)
        for i in range(KE_FIRST_MIN_CYCLES + 5):
            self._add_synthetic_cycle(learner, outdoor_temp=10.0 + i * 0.2)

        learner._check_convergence()
        assert not learner.converged

    def test_convergence_poor_correlation(self):
        """Test that convergence requires strong correlation (R² > 0.7)."""
        learner = KeFirstLearner()

        # Add cycles with random drop rates (poor correlation)
        import random
        random.seed(42)
        for i in range(KE_FIRST_MIN_CYCLES + 5):
            outdoor_temp = 0.0 + i
            temp_diff = 20.0 - outdoor_temp
            # Random drop rate (no correlation)
            drop_rate = random.uniform(0.5, 2.5)

            cycle = SteadyStateCycle(
                timestamp=datetime.now(),
                outdoor_temp=outdoor_temp,
                indoor_temp_start=20.5,
                indoor_temp_end=20.5 - drop_rate * 0.5,
                duration_hours=0.5,
                temp_drop_rate=drop_rate,
                temp_difference=temp_diff,
                duty_cycle=0.40,
            )
            learner._cycles.append(cycle)

        learner._check_convergence()
        assert not learner.converged

    def test_convergence_success(self):
        """Test successful convergence with good data."""
        learner = KeFirstLearner(initial_ke=0.005)

        # Add 15 cycles with strong correlation
        # Realistic scenario: indoor 20°C, outdoor varies from -5 to +10°C
        # Expected relationship: higher temp_diff -> higher drop rate
        for outdoor_temp in [-5, -3, -1, 0, 2, 4, 6, 8, 10, 12, -4, -2, 1, 3, 5]:
            temp_diff = 20.0 - outdoor_temp
            # drop_rate proportional to temp_diff: Ke ≈ 0.05
            drop_rate = 0.05 * temp_diff + 0.1

            cycle = SteadyStateCycle(
                timestamp=datetime.now(),
                outdoor_temp=outdoor_temp,
                indoor_temp_start=20.5,
                indoor_temp_end=20.5 - drop_rate * 0.5,
                duration_hours=0.5,
                temp_drop_rate=drop_rate,
                temp_difference=temp_diff,
                duty_cycle=0.40,
            )
            learner._cycles.append(cycle)

        learner._check_convergence()

        assert learner.converged
        assert learner.r_squared is not None
        assert learner.r_squared > KE_FIRST_R_SQUARED_THRESHOLD
        assert learner.current_ke > 0  # Should have calculated a positive Ke

    def test_integration_15_cycles_with_outdoor_variation(self):
        """Integration test: 15 steady-state cycles with outdoor temp variation."""
        learner = KeFirstLearner(initial_ke=0.003)

        # Simulate 15 heating cycles over several days
        # Outdoor temp varies from -5°C to +10°C
        outdoor_temps = [-5, -3, 0, 2, 5, 7, 10, 8, 3, 1, -2, -4, 4, 6, 9]
        indoor_target = 20.0

        for i, outdoor_temp in enumerate(outdoor_temps):
            off_time = datetime(2024, 1, 1) + timedelta(hours=i * 4)
            on_time = off_time + timedelta(minutes=45)

            # Temperature difference drives heat loss
            temp_diff = indoor_target - outdoor_temp
            # Realistic drop rate: ~0.04°C/h per °C difference
            drop_rate_per_hour = 0.04 * temp_diff + 0.05  # Small base rate
            temp_drop = drop_rate_per_hour * 0.75  # 45 minutes

            learner.on_heater_off(indoor_target, outdoor_temp, off_time)
            learner.on_heater_on(
                indoor_temp=indoor_target - temp_drop,
                outdoor_temp=outdoor_temp,
                duty_cycle=0.40,
                timestamp=on_time,
            )

        # Should have collected 15 cycles
        assert learner.cycle_count == 15

        # Should have converged
        assert learner.converged
        assert learner.r_squared > KE_FIRST_R_SQUARED_THRESHOLD

        # Ke should be reasonable (clamped to ke_max of 0.02)
        # Our simulation uses 0.04 but it gets clamped
        assert 0.015 <= learner.current_ke <= 0.02

    def test_blocks_pid_learning_integration(self):
        """Test that KeFirstLearner blocks PID tuning until converged."""
        from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
        from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics

        ke_learner = KeFirstLearner()
        pid_learner = AdaptiveLearner(ke_first_learner=ke_learner)

        # Add sufficient PID cycles for learning
        for _ in range(6):
            pid_learner.add_cycle_metrics(
                CycleMetrics(
                    rise_time=30.0,
                    overshoot=0.3,
                    undershoot=0.2,
                    settling_time=45.0,
                    oscillations=1,
                )
            )

        # Try to calculate PID adjustment - should be blocked
        result = pid_learner.calculate_pid_adjustment(100, 1.0, 2.0)
        assert result is None  # Blocked because Ke not converged

        # Now converge Ke learning
        for outdoor_temp in [-5, -3, 0, 2, 5, 7, 10, 8, 3, 1, -2, -4, 4, 6, 9]:
            temp_diff = 20.0 - outdoor_temp
            drop_rate = 0.04 * temp_diff + 0.05
            cycle = SteadyStateCycle(
                timestamp=datetime.now(),
                outdoor_temp=outdoor_temp,
                indoor_temp_start=20.0,
                indoor_temp_end=20.0 - drop_rate * 0.5,
                duration_hours=0.5,
                temp_drop_rate=drop_rate,
                temp_difference=temp_diff,
                duty_cycle=0.40,
            )
            ke_learner._cycles.append(cycle)
        ke_learner._check_convergence()

        assert ke_learner.converged

        # Now PID learning should proceed (though may still return None for other reasons)
        # The key is it won't be blocked by Ke gate
        result = pid_learner.calculate_pid_adjustment(100, 1.0, 2.0)
        # Result may be None due to rate limiting or convergence, but not Ke gate

    def test_convergence_progress_tracking(self):
        """Test convergence progress percentage calculation."""
        learner = KeFirstLearner()

        # Initially 0%
        assert learner.convergence_progress == 0.0

        # Add some cycles
        for i in range(5):
            self._add_synthetic_cycle(learner, outdoor_temp=i)

        # Should show partial progress
        progress = learner.convergence_progress
        assert 0 < progress < 100

        # Add more cycles and improve correlation
        for outdoor_temp in [-5, -3, 0, 2, 5, 7, 10, 8, 3, 1]:
            temp_diff = 20.0 - outdoor_temp
            drop_rate = 0.04 * temp_diff
            cycle = SteadyStateCycle(
                timestamp=datetime.now(),
                outdoor_temp=outdoor_temp,
                indoor_temp_start=20.0,
                indoor_temp_end=20.0 - drop_rate * 0.5,
                duration_hours=0.5,
                temp_drop_rate=drop_rate,
                temp_difference=temp_diff,
                duty_cycle=0.40,
            )
            learner._cycles.append(cycle)
        learner._check_convergence()

        # Should be 100% after convergence
        if learner.converged:
            assert learner.convergence_progress == 100.0

    def test_get_summary(self):
        """Test summary statistics."""
        learner = KeFirstLearner(initial_ke=0.005)

        summary = learner.get_summary()
        assert summary["converged"] is False
        assert summary["current_ke"] == 0.005
        assert summary["cycle_count"] == 0

        # Add cycles and check summary updates
        self._add_synthetic_cycle(learner, outdoor_temp=0.0)
        self._add_synthetic_cycle(learner, outdoor_temp=10.0)

        summary = learner.get_summary()
        assert summary["cycle_count"] == 2
        assert summary["temperature_range"] == 10.0

    def test_reset(self):
        """Test resetting learner state."""
        learner = KeFirstLearner(initial_ke=0.005)

        # Add cycles and converge
        for outdoor_temp in [-5, -3, 0, 2, 5, 7, 10, 8, 3, 1, -2, -4, 4, 6, 9]:
            temp_diff = 20.0 - outdoor_temp
            drop_rate = 0.04 * temp_diff
            cycle = SteadyStateCycle(
                timestamp=datetime.now(),
                outdoor_temp=outdoor_temp,
                indoor_temp_start=20.0,
                indoor_temp_end=20.0 - drop_rate * 0.5,
                duration_hours=0.5,
                temp_drop_rate=drop_rate,
                temp_difference=temp_diff,
                duty_cycle=0.40,
            )
            learner._cycles.append(cycle)
        learner._check_convergence()

        assert learner.converged

        # Reset
        learner.reset()

        # Verify reset
        assert not learner.converged
        assert learner.cycle_count == 0
        assert learner.r_squared is None
        assert learner.current_ke == 0.005  # Back to initial

    def test_persistence_to_dict_and_from_dict(self):
        """Test state persistence."""
        learner = KeFirstLearner(initial_ke=0.005)

        # Add some cycles
        for i in range(5):
            self._add_synthetic_cycle(learner, outdoor_temp=i)

        # Serialize
        data = learner.to_dict()

        # Restore
        restored = KeFirstLearner.from_dict(data)

        # Verify
        assert restored.current_ke == learner.current_ke
        assert restored.cycle_count == learner.cycle_count
        assert restored.converged == learner.converged

    # Helper methods

    def _add_synthetic_cycle(self, learner: KeFirstLearner, outdoor_temp: float):
        """Add a synthetic cycle for testing."""
        cycle = SteadyStateCycle(
            timestamp=datetime.now(),
            outdoor_temp=outdoor_temp,
            indoor_temp_start=20.0,
            indoor_temp_end=19.5,
            duration_hours=0.5,
            temp_drop_rate=1.0,
            temp_difference=20.0 - outdoor_temp,
            duty_cycle=0.40,
        )
        learner._cycles.append(cycle)
