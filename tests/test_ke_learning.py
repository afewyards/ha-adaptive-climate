"""Tests for Ke (outdoor temperature compensation) adaptive learning."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from custom_components.adaptive_thermostat.adaptive.ke_learning import (
    KeLearner,
    KeObservation,
)
from custom_components.adaptive_thermostat.adaptive.physics import (
    calculate_initial_ke,
    ENERGY_RATING_TO_INSULATION,
)
from custom_components.adaptive_thermostat.adaptive.learning import (
    AdaptiveLearner,
    CycleMetrics,
)
from custom_components.adaptive_thermostat.const import (
    KE_MIN_OBSERVATIONS,
    KE_MIN_TEMP_RANGE,
    KE_CORRELATION_THRESHOLD,
    KE_ADJUSTMENT_STEP,
    KE_MAX_OBSERVATIONS,
    MIN_CONVERGENCE_CYCLES_FOR_KE,
    PID_LIMITS,
)


# ============================================================================
# KeObservation Tests
# ============================================================================


class TestKeObservation:
    """Tests for the KeObservation dataclass."""

    def test_observation_creation(self):
        """Test basic observation creation."""
        now = datetime.now()
        obs = KeObservation(
            timestamp=now,
            outdoor_temp=5.0,
            pid_output=60.0,
            indoor_temp=20.5,
            target_temp=21.0,
        )

        assert obs.timestamp == now
        assert obs.outdoor_temp == 5.0
        assert obs.pid_output == 60.0
        assert obs.indoor_temp == 20.5
        assert obs.target_temp == 21.0

    def test_observation_to_dict(self):
        """Test observation serialization to dictionary."""
        now = datetime(2024, 1, 15, 12, 0, 0)
        obs = KeObservation(
            timestamp=now,
            outdoor_temp=5.0,
            pid_output=60.0,
            indoor_temp=20.5,
            target_temp=21.0,
        )

        data = obs.to_dict()

        assert data["timestamp"] == "2024-01-15T12:00:00"
        assert data["outdoor_temp"] == 5.0
        assert data["pid_output"] == 60.0
        assert data["indoor_temp"] == 20.5
        assert data["target_temp"] == 21.0

    def test_observation_from_dict(self):
        """Test observation deserialization from dictionary."""
        data = {
            "timestamp": "2024-01-15T12:00:00",
            "outdoor_temp": 5.0,
            "pid_output": 60.0,
            "indoor_temp": 20.5,
            "target_temp": 21.0,
        }

        obs = KeObservation.from_dict(data)

        assert obs.timestamp == datetime(2024, 1, 15, 12, 0, 0)
        assert obs.outdoor_temp == 5.0
        assert obs.pid_output == 60.0
        assert obs.indoor_temp == 20.5
        assert obs.target_temp == 21.0

    def test_observation_roundtrip(self):
        """Test observation serialization roundtrip."""
        now = datetime.now()
        original = KeObservation(
            timestamp=now,
            outdoor_temp=-2.5,
            pid_output=85.0,
            indoor_temp=19.8,
            target_temp=20.0,
        )

        data = original.to_dict()
        restored = KeObservation.from_dict(data)

        # Note: microseconds may be lost in ISO format
        assert restored.timestamp.replace(microsecond=0) == now.replace(microsecond=0)
        assert restored.outdoor_temp == original.outdoor_temp
        assert restored.pid_output == original.pid_output
        assert restored.indoor_temp == original.indoor_temp
        assert restored.target_temp == original.target_temp


# ============================================================================
# KeLearner Tests
# ============================================================================


class TestKeLearnerBasic:
    """Basic tests for the KeLearner class."""

    def test_initial_state(self):
        """Test initial state of KeLearner."""
        learner = KeLearner(initial_ke=0.3)

        assert learner.current_ke == 0.3
        assert learner.enabled is False
        assert learner.observation_count == 0

    def test_enable_disable(self):
        """Test enabling and disabling Ke learning."""
        learner = KeLearner(initial_ke=0.3)

        assert learner.enabled is False

        learner.enable()
        assert learner.enabled is True

        learner.disable()
        assert learner.enabled is False

    def test_add_observation_when_disabled(self):
        """Test that observations are rejected when learning is disabled."""
        learner = KeLearner(initial_ke=0.3)

        result = learner.add_observation(
            outdoor_temp=5.0,
            pid_output=60.0,
            indoor_temp=20.5,
            target_temp=21.0,
        )

        assert result is False
        assert learner.observation_count == 0

    def test_add_observation_when_enabled(self):
        """Test that observations are accepted when learning is enabled."""
        learner = KeLearner(initial_ke=0.3)
        learner.enable()

        result = learner.add_observation(
            outdoor_temp=5.0,
            pid_output=60.0,
            indoor_temp=20.5,
            target_temp=21.0,
        )

        assert result is True
        assert learner.observation_count == 1


class TestKeLearnerFIFO:
    """Tests for FIFO eviction in KeLearner."""

    def test_fifo_eviction(self):
        """Test that old observations are evicted when max is exceeded."""
        learner = KeLearner(initial_ke=0.3, max_observations=5)
        learner.enable()

        # Add 7 observations
        for i in range(7):
            learner.add_observation(
                outdoor_temp=5.0 + i,
                pid_output=50.0 + i,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        # Should only have 5 observations
        assert learner.observation_count == 5

    def test_fifo_keeps_newest(self):
        """Test that FIFO keeps the newest observations."""
        learner = KeLearner(initial_ke=0.3, max_observations=3)
        learner.enable()

        # Add observations with distinctive outdoor temps
        for i in range(5):
            learner.add_observation(
                outdoor_temp=float(i),  # 0, 1, 2, 3, 4
                pid_output=50.0,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        # Check summary - should have temps 2, 3, 4 (newest 3)
        summary = learner.get_observations_summary()
        assert summary["outdoor_temp_min"] == 2.0
        assert summary["outdoor_temp_max"] == 4.0


class TestKeLearnerCorrelation:
    """Tests for Ke correlation calculation and adjustment."""

    def test_pearson_correlation_perfect_negative(self):
        """Test Pearson correlation with perfectly negative correlation."""
        learner = KeLearner(initial_ke=0.3)

        # Perfect negative correlation: as X increases, Y decreases
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [10.0, 8.0, 6.0, 4.0, 2.0]

        correlation = learner._calculate_pearson_correlation(x, y)

        assert correlation is not None
        assert correlation == pytest.approx(-1.0, abs=0.01)

    def test_pearson_correlation_perfect_positive(self):
        """Test Pearson correlation with perfectly positive correlation."""
        learner = KeLearner(initial_ke=0.3)

        # Perfect positive correlation
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]

        correlation = learner._calculate_pearson_correlation(x, y)

        assert correlation is not None
        assert correlation == pytest.approx(1.0, abs=0.01)

    def test_pearson_correlation_no_correlation(self):
        """Test Pearson correlation with no correlation."""
        learner = KeLearner(initial_ke=0.3)

        # No correlation
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [5.0, 1.0, 3.0, 2.0, 4.0]

        correlation = learner._calculate_pearson_correlation(x, y)

        # Should be close to 0 (may not be exactly 0)
        assert correlation is not None
        assert abs(correlation) < 0.5

    def test_pearson_correlation_insufficient_data(self):
        """Test Pearson correlation with insufficient data."""
        learner = KeLearner(initial_ke=0.3)

        # Only 1 data point
        x = [1.0]
        y = [5.0]

        correlation = learner._calculate_pearson_correlation(x, y)

        assert correlation is None

    def test_adjustment_insufficient_observations(self):
        """Test that adjustment is None with insufficient observations."""
        learner = KeLearner(initial_ke=0.3)
        learner.enable()

        # Add fewer than KE_MIN_OBSERVATIONS
        for i in range(KE_MIN_OBSERVATIONS - 1):
            learner.add_observation(
                outdoor_temp=5.0 + i,
                pid_output=50.0 + i * 5,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result = learner.calculate_ke_adjustment()
        assert result is None

    def test_adjustment_insufficient_temp_range(self):
        """Test that adjustment is None with insufficient temperature range."""
        learner = KeLearner(initial_ke=0.3)
        learner.enable()

        # Add observations with small temp range (< KE_MIN_TEMP_RANGE)
        for i in range(KE_MIN_OBSERVATIONS + 2):
            learner.add_observation(
                outdoor_temp=10.0 + i * 0.5,  # Only 0.5C variation
                pid_output=50.0 + i * 5,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result = learner.calculate_ke_adjustment()
        assert result is None

    def test_adjustment_negative_correlation_increases_ke(self):
        """Test that strong negative correlation recommends Ke increase (v0.7.0: 100x smaller scale)."""
        learner = KeLearner(initial_ke=0.003)
        learner.enable()

        # Create strong negative correlation:
        # Lower outdoor temp -> higher PID output (expected behavior)
        outdoor_temps = [0.0, 2.0, 5.0, 8.0, 10.0, 12.0, 15.0]
        pid_outputs = [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0]

        for outdoor, pid in zip(outdoor_temps, pid_outputs):
            learner.add_observation(
                outdoor_temp=outdoor,
                pid_output=pid,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result = learner.calculate_ke_adjustment()

        # Should recommend increasing Ke
        assert result is not None
        assert result > learner.current_ke

    def test_adjustment_positive_correlation_decreases_ke(self):
        """Test that strong positive correlation recommends Ke decrease."""
        learner = KeLearner(initial_ke=0.5)
        learner.enable()

        # Create strong positive correlation (overcompensating):
        # Higher outdoor temp -> higher PID output (wrong - Ke is too high)
        outdoor_temps = [0.0, 2.0, 5.0, 8.0, 10.0, 12.0, 15.0]
        pid_outputs = [30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]

        for outdoor, pid in zip(outdoor_temps, pid_outputs):
            learner.add_observation(
                outdoor_temp=outdoor,
                pid_output=pid,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result = learner.calculate_ke_adjustment()

        # Should recommend decreasing Ke
        assert result is not None
        assert result < learner.current_ke

    def test_adjustment_weak_correlation_no_change(self):
        """Test that weak correlation results in no adjustment."""
        learner = KeLearner(initial_ke=0.3)
        learner.enable()

        # Create weak/no correlation - Ke is well tuned
        # PID output is constant regardless of outdoor temp
        outdoor_temps = [0.0, 5.0, 10.0, 15.0, 20.0, 7.0, 12.0]
        pid_outputs = [50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0]  # Constant output

        for outdoor, pid in zip(outdoor_temps, pid_outputs):
            learner.add_observation(
                outdoor_temp=outdoor,
                pid_output=pid,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result = learner.calculate_ke_adjustment()

        # Should be None (no change needed - std_y is 0 so correlation is None)
        assert result is None


class TestKeLearnerRateLimiting:
    """Tests for rate limiting in KeLearner."""

    def test_rate_limiting_blocks_adjustment(self):
        """Test that rate limiting blocks frequent adjustments."""
        learner = KeLearner(initial_ke=0.3)
        learner.enable()

        # Add enough observations for adjustment
        for i in range(KE_MIN_OBSERVATIONS + 2):
            learner.add_observation(
                outdoor_temp=float(i) * 2,  # Wide range
                pid_output=90.0 - i * 10,  # Negative correlation
                indoor_temp=20.0,
                target_temp=20.0,
            )

        # First adjustment should succeed
        result1 = learner.calculate_ke_adjustment()
        assert result1 is not None

        # Apply the adjustment (sets rate limit)
        learner.apply_ke_adjustment(result1)

        # Add more observations
        for i in range(KE_MIN_OBSERVATIONS + 2):
            learner.add_observation(
                outdoor_temp=float(i) * 2,
                pid_output=90.0 - i * 10,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        # Second adjustment should be rate limited
        result2 = learner.calculate_ke_adjustment()
        assert result2 is None  # Rate limited

    def test_rate_limiting_expires(self):
        """Test that rate limiting expires after interval (v0.7.0: 100x smaller scale)."""
        learner = KeLearner(initial_ke=0.003)
        learner.enable()

        # Add observations and apply adjustment
        for i in range(KE_MIN_OBSERVATIONS + 2):
            learner.add_observation(
                outdoor_temp=float(i) * 2,
                pid_output=90.0 - i * 10,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result1 = learner.calculate_ke_adjustment()
        learner.apply_ke_adjustment(result1)

        # Mock time to be after rate limit interval
        past_time = datetime.now() - timedelta(hours=50)  # > KE_ADJUSTMENT_INTERVAL
        learner._last_adjustment_time = past_time

        # Add more observations
        for i in range(KE_MIN_OBSERVATIONS + 2):
            learner.add_observation(
                outdoor_temp=float(i) * 2,
                pid_output=90.0 - i * 10,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        # Adjustment should now succeed
        result2 = learner.calculate_ke_adjustment()
        assert result2 is not None


class TestKeLearnerKeLimits:
    """Tests for Ke value limits."""

    def test_ke_respects_upper_limit(self):
        """Test that Ke adjustments respect upper limit."""
        learner = KeLearner(initial_ke=PID_LIMITS["ke_max"] - 0.05)
        learner.enable()

        # Create strong negative correlation to push Ke up
        for i in range(KE_MIN_OBSERVATIONS + 2):
            learner.add_observation(
                outdoor_temp=float(i) * 2,
                pid_output=90.0 - i * 10,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result = learner.calculate_ke_adjustment()

        if result is not None:
            assert result <= PID_LIMITS["ke_max"]

    def test_ke_respects_lower_limit(self):
        """Test that Ke adjustments respect lower limit."""
        learner = KeLearner(initial_ke=PID_LIMITS["ke_min"] + 0.05)
        learner.enable()

        # Create strong positive correlation to push Ke down
        for i in range(KE_MIN_OBSERVATIONS + 2):
            learner.add_observation(
                outdoor_temp=float(i) * 2,
                pid_output=30.0 + i * 10,  # Positive correlation
                indoor_temp=20.0,
                target_temp=20.0,
            )

        result = learner.calculate_ke_adjustment()

        if result is not None:
            assert result >= PID_LIMITS["ke_min"]


class TestKeLearnerPersistence:
    """Tests for KeLearner persistence."""

    def test_to_dict(self):
        """Test serialization to dictionary."""
        learner = KeLearner(initial_ke=0.4)
        learner.enable()

        # Add some observations
        learner.add_observation(
            outdoor_temp=5.0,
            pid_output=60.0,
            indoor_temp=20.0,
            target_temp=20.0,
        )

        data = learner.to_dict()

        assert data["current_ke"] == 0.4
        assert data["enabled"] is True
        assert len(data["observations"]) == 1

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "current_ke": 0.5,
            "enabled": True,
            "max_observations": 50,
            "last_adjustment_time": "2024-01-15T12:00:00",
            "observations": [
                {
                    "timestamp": "2024-01-15T11:00:00",
                    "outdoor_temp": 5.0,
                    "pid_output": 60.0,
                    "indoor_temp": 20.0,
                    "target_temp": 20.0,
                }
            ],
        }

        learner = KeLearner.from_dict(data)

        assert learner.current_ke == 0.5
        assert learner.enabled is True
        assert learner.observation_count == 1
        assert learner.get_last_adjustment_time() == datetime(2024, 1, 15, 12, 0, 0)

    def test_roundtrip_persistence(self):
        """Test full persistence roundtrip."""
        original = KeLearner(initial_ke=0.35, max_observations=20)
        original.enable()

        # Add observations
        for i in range(5):
            original.add_observation(
                outdoor_temp=float(i),
                pid_output=50.0 + i * 5,
                indoor_temp=20.0,
                target_temp=20.0,
            )

        # Serialize and restore
        data = original.to_dict()
        restored = KeLearner.from_dict(data)

        assert restored.current_ke == original.current_ke
        assert restored.enabled == original.enabled
        assert restored.observation_count == original.observation_count


# ============================================================================
# calculate_initial_ke Tests
# ============================================================================


class TestCalculateInitialKe:
    """Tests for physics-based initial Ke calculation."""

    def test_default_values(self):
        """Test calculation with default values (v0.7.0: 100x smaller scale)."""
        ke = calculate_initial_ke()

        # Default should be moderate (A-rated building baseline, v0.7.0 scale)
        assert 0.003 <= ke <= 0.007

    def test_energy_rating_effect(self):
        """Test that better energy rating results in lower Ke."""
        ke_a_plus = calculate_initial_ke(energy_rating="A+++")
        ke_d = calculate_initial_ke(energy_rating="D")

        # Better insulation = less outdoor impact = lower Ke
        assert ke_a_plus < ke_d

    def test_all_energy_ratings(self):
        """Test all energy ratings produce valid values."""
        for rating in ENERGY_RATING_TO_INSULATION.keys():
            ke = calculate_initial_ke(energy_rating=rating)
            assert 0.0 <= ke <= 2.0

    def test_window_area_effect(self):
        """Test that larger window area increases Ke."""
        ke_small_windows = calculate_initial_ke(
            window_area_m2=2.0,
            floor_area_m2=20.0,
        )
        ke_large_windows = calculate_initial_ke(
            window_area_m2=8.0,
            floor_area_m2=20.0,
        )

        # More windows = more outdoor impact = higher Ke
        assert ke_large_windows > ke_small_windows

    def test_window_rating_effect(self):
        """Test that better window rating decreases Ke."""
        ke_single_pane = calculate_initial_ke(
            window_area_m2=4.0,
            floor_area_m2=20.0,
            window_rating="single",
        )
        ke_triple = calculate_initial_ke(
            window_area_m2=4.0,
            floor_area_m2=20.0,
            window_rating="hr+++",
        )

        # Better glazing = less outdoor impact = lower Ke
        assert ke_triple < ke_single_pane

    def test_heating_type_effect(self):
        """Test that heating type affects Ke."""
        ke_floor = calculate_initial_ke(heating_type="floor_hydronic")
        ke_forced_air = calculate_initial_ke(heating_type="forced_air")

        # Floor heating benefits more from outdoor compensation
        assert ke_floor > ke_forced_air


# ============================================================================
# AdaptiveLearner Convergence Tracking Tests
# ============================================================================


class TestAdaptiveLearnerConvergenceTracking:
    """Tests for convergence tracking in AdaptiveLearner for Ke learning."""

    def test_initial_convergence_state(self):
        """Test initial convergence tracking state."""
        learner = AdaptiveLearner()

        assert learner.is_pid_converged_for_ke() is False
        assert learner.get_consecutive_converged_cycles() == 0

    def test_convergence_tracking_with_good_cycles(self):
        """Test that consecutive good cycles leads to convergence."""
        learner = AdaptiveLearner()

        # Add converged cycles (within all thresholds)
        for _ in range(MIN_CONVERGENCE_CYCLES_FOR_KE):
            metrics = CycleMetrics(
                overshoot=0.1,       # Within 0.2 threshold
                undershoot=0.05,
                settling_time=30,    # Within 60 min threshold
                oscillations=0,      # Within 1 threshold
                rise_time=20,        # Within 45 min threshold
            )
            learner.update_convergence_tracking(metrics)

        assert learner.is_pid_converged_for_ke() is True
        assert learner.get_consecutive_converged_cycles() >= MIN_CONVERGENCE_CYCLES_FOR_KE

    def test_convergence_reset_on_bad_cycle(self):
        """Test that a bad cycle resets convergence counter."""
        learner = AdaptiveLearner()

        # Add some good cycles
        for _ in range(MIN_CONVERGENCE_CYCLES_FOR_KE - 1):
            good_metrics = CycleMetrics(
                overshoot=0.1,
                undershoot=0.05,
                settling_time=30,
                oscillations=0,
                rise_time=20,
            )
            learner.update_convergence_tracking(good_metrics)

        # Now add a bad cycle (high overshoot)
        bad_metrics = CycleMetrics(
            overshoot=0.5,  # Above 0.2 threshold
            undershoot=0.05,
            settling_time=30,
            oscillations=0,
            rise_time=20,
        )
        learner.update_convergence_tracking(bad_metrics)

        assert learner.is_pid_converged_for_ke() is False
        assert learner.get_consecutive_converged_cycles() == 0

    def test_convergence_maintains_after_achieved(self):
        """Test that convergence state maintains with continued good cycles."""
        learner = AdaptiveLearner()

        # Achieve convergence
        for _ in range(MIN_CONVERGENCE_CYCLES_FOR_KE + 2):
            metrics = CycleMetrics(
                overshoot=0.1,
                undershoot=0.05,
                settling_time=30,
                oscillations=0,
                rise_time=20,
            )
            learner.update_convergence_tracking(metrics)

        initial_count = learner.get_consecutive_converged_cycles()
        assert learner.is_pid_converged_for_ke() is True

        # Add more good cycles
        for _ in range(3):
            metrics = CycleMetrics(
                overshoot=0.15,
                undershoot=0.1,
                settling_time=40,
                oscillations=1,
                rise_time=30,
            )
            learner.update_convergence_tracking(metrics)

        # Should still be converged with higher count
        assert learner.is_pid_converged_for_ke() is True
        assert learner.get_consecutive_converged_cycles() > initial_count

    def test_reset_ke_convergence(self):
        """Test manual reset of Ke convergence tracking."""
        learner = AdaptiveLearner()

        # Achieve convergence
        for _ in range(MIN_CONVERGENCE_CYCLES_FOR_KE):
            metrics = CycleMetrics(
                overshoot=0.1,
                undershoot=0.05,
                settling_time=30,
                oscillations=0,
                rise_time=20,
            )
            learner.update_convergence_tracking(metrics)

        assert learner.is_pid_converged_for_ke() is True

        # Reset convergence
        learner.reset_ke_convergence()

        assert learner.is_pid_converged_for_ke() is False
        assert learner.get_consecutive_converged_cycles() == 0

    def test_convergence_with_high_oscillations(self):
        """Test that high oscillations prevent convergence."""
        learner = AdaptiveLearner()

        # Add cycles with high oscillations
        for _ in range(MIN_CONVERGENCE_CYCLES_FOR_KE + 2):
            metrics = CycleMetrics(
                overshoot=0.1,
                undershoot=0.05,
                settling_time=30,
                oscillations=3,  # Above 1 threshold
                rise_time=20,
            )
            learner.update_convergence_tracking(metrics)

        assert learner.is_pid_converged_for_ke() is False

    def test_convergence_with_slow_settling(self):
        """Test that slow settling prevents convergence."""
        learner = AdaptiveLearner()

        # Add cycles with slow settling
        for _ in range(MIN_CONVERGENCE_CYCLES_FOR_KE + 2):
            metrics = CycleMetrics(
                overshoot=0.1,
                undershoot=0.05,
                settling_time=100,  # Above 60 min threshold
                oscillations=0,
                rise_time=20,
            )
            learner.update_convergence_tracking(metrics)

        assert learner.is_pid_converged_for_ke() is False

    def test_convergence_handles_none_values(self):
        """Test that None values in metrics are handled correctly."""
        learner = AdaptiveLearner()

        # Add cycles with some None values
        for _ in range(MIN_CONVERGENCE_CYCLES_FOR_KE):
            metrics = CycleMetrics(
                overshoot=None,  # Will default to 0.0
                undershoot=None,
                settling_time=None,  # Will default to 0.0
                oscillations=0,
                rise_time=None,  # Will default to 0.0
            )
            learner.update_convergence_tracking(metrics)

        # Should converge since None defaults to 0.0 which is within thresholds
        assert learner.is_pid_converged_for_ke() is True
