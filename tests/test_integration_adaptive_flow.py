"""Integration tests for the adaptive learning flow.

Tests the complete flow: Cycle analysis -> PID recommendations -> application
"""
import pytest

from custom_components.adaptive_thermostat.adaptive.learning import (
    AdaptiveLearner,
    CycleMetrics,
    ThermalRateLearner,
)
from custom_components.adaptive_thermostat.const import PID_LIMITS, MIN_CYCLES_FOR_LEARNING
from custom_components.adaptive_thermostat.pid_controller import PID


@pytest.fixture
def adaptive_learner():
    """Create an adaptive learner instance."""
    return AdaptiveLearner()


@pytest.fixture
def thermal_learner():
    """Create a thermal rate learner instance."""
    return ThermalRateLearner()


@pytest.fixture
def pid():
    """Create a PID controller instance."""
    return PID(
        kp=100.0,
        ki=20.0,
        kd=50.0,
        ke=0,
        out_min=0,
        out_max=100,
        sampling_period=0,
    )


# =============================================================================
# Test: Cycle Metrics to PID Adjustment
# =============================================================================


def test_cycle_metrics_to_pid_adjustment(adaptive_learner):
    """
    Integration test: Observed cycle metrics flow through to PID recommendations.

    Flow: Heating cycle completes -> metrics extracted -> PID adjustment calculated
    """
    # Simulate 3 heating cycles with moderate overshoot and some oscillations
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.4,  # Moderate overshoot (>0.2°C)
            undershoot=0.1,
            settling_time=50.0,
            oscillations=2,  # Some oscillations (>1 triggers Kd increase)
            rise_time=35.0,
        )
        adaptive_learner.add_cycle_metrics(metrics)

    # Initial PID values
    kp, ki, kd = 100.0, 20.0, 2.0

    # Calculate adjustment
    result = adaptive_learner.calculate_pid_adjustment(kp, ki, kd)

    # Should have result (MIN_CYCLES_FOR_LEARNING cycles = minimum)
    assert result is not None, f"Expected PID adjustment with {MIN_CYCLES_FOR_LEARNING} cycles"

    # v0.7.0: Moderate overshoot (0.2-1.0°C) only increases Kd (thermal lag addressed by derivative)
    # Oscillations (>1) also increase Kd
    # Both rules increase Kd, so Kp should be unchanged
    assert result["kp"] == kp, f"Expected Kp unchanged, got {result['kp']} vs original {kp}"

    # Both moderate overshoot and oscillations should increase Kd
    assert result["kd"] > kd, f"Expected Kd increase, got {result['kd']} vs original {kd}"


def test_insufficient_cycles_returns_none(adaptive_learner):
    """
    Integration test: Adaptive learning refuses to make recommendations
    with insufficient data.
    """
    # Add only 2 cycles (below MIN_CYCLES_FOR_LEARNING = 3)
    for _ in range(2):
        metrics = CycleMetrics(
            overshoot=0.3,
            undershoot=0.1,
            settling_time=45.0,
            oscillations=0,
            rise_time=30.0,
        )
        adaptive_learner.add_cycle_metrics(metrics)

    result = adaptive_learner.calculate_pid_adjustment(100.0, 20.0, 10.0)
    assert result is None, "Expected None with insufficient cycles"


def test_high_overshoot_reduces_kp():
    """
    Integration test: Extreme overshoot (>1.0°C) reduces Kp and increases Kd (v0.7.0).
    """
    learner = AdaptiveLearner()

    # Extreme overshoot cycles (>1.0°C triggers HIGH_OVERSHOOT rule)
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=1.2,  # Extreme overshoot (>1.0°C)
            undershoot=0.0,
            settling_time=40.0,
            oscillations=0,
            rise_time=25.0,
        )
        learner.add_cycle_metrics(metrics)

    kp, ki, kd = 150.0, 25.0, 2.0
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # v0.7.0: Extreme overshoot (>1.0°C): reduce Kp by 10% and Ki by 10%, increase Kd by 20%
    assert result["kp"] < kp, f"Expected Kp reduction for extreme overshoot"
    # Extreme overshoot also reduces Ki
    assert result["ki"] < ki, f"Expected Ki reduction for extreme overshoot"
    # Extreme overshoot increases Kd to handle thermal lag
    assert result["kd"] > kd, f"Expected Kd increase for extreme overshoot"


def test_undershoot_increases_ki():
    """
    Integration test: Undershoot (>0.3°C) increases Ki.
    """
    learner = AdaptiveLearner()

    # Undershoot cycles (system not reaching setpoint)
    # Note: rise_time > 45 to avoid triggering convergence check
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.0,
            undershoot=0.5,  # Significant undershoot
            settling_time=50.0,
            oscillations=0,
            rise_time=50.0,  # Above convergence threshold
        )
        learner.add_cycle_metrics(metrics)

    kp, ki, kd = 100.0, 15.0, 3.0
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Undershoot: increase Ki by up to 20%
    assert result["ki"] > ki, f"Expected Ki increase for undershoot"


def test_many_oscillations_adjusts_kp_and_kd():
    """
    Integration test: Many oscillations (>3) reduces Kp and increases Kd.
    """
    learner = AdaptiveLearner()

    # Oscillating cycles
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.2,
            undershoot=0.15,
            settling_time=70.0,
            oscillations=5,  # Many oscillations
            rise_time=30.0,
        )
        learner.add_cycle_metrics(metrics)

    kp, ki, kd = 120.0, 20.0, 2.5
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Many oscillations: reduce Kp by 10%, increase Kd by 20%
    assert result["kp"] < kp, f"Expected Kp reduction for oscillations"
    assert result["kd"] > kd, f"Expected Kd increase for oscillations"


def test_slow_settling_increases_kd():
    """
    Integration test: Slow settling (>90 min) increases Kd.
    """
    learner = AdaptiveLearner()

    # Slow settling cycles
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.1,
            undershoot=0.1,
            settling_time=120.0,  # Very slow settling
            oscillations=0,
            rise_time=60.0,
        )
        learner.add_cycle_metrics(metrics)

    kp, ki, kd = 100.0, 20.0, 3.0
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Slow settling: increase Kd by 15%
    assert result["kd"] > kd, f"Expected Kd increase for slow settling"


def test_slow_rise_time_increases_kp():
    """
    Integration test: Slow rise time (>60 min) increases Kp.
    """
    learner = AdaptiveLearner()

    # Slow rise time cycles
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.1,
            undershoot=0.0,
            settling_time=80.0,
            oscillations=0,
            rise_time=75.0,  # Slow rise time
        )
        learner.add_cycle_metrics(metrics)

    kp, ki, kd = 80.0, 15.0, 4.0
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    # Slow rise time: increase Kp by 10%
    assert result["kp"] > kp, f"Expected Kp increase for slow rise time"


# =============================================================================
# Test: Progressive Learning Improves Tuning
# =============================================================================


def test_progressive_learning_improves_tuning():
    """
    Integration test: Multiple rounds of learning progressively tune PID (v0.7.0).

    Scenario: System starts with extreme overshoot, learning reduces Kp over rounds.
    """
    learner = AdaptiveLearner()
    kp, ki, kd = 150.0, 25.0, 2.0  # Start with aggressive tuning

    # Round 1: Extreme overshoot (>1.0°C triggers HIGH_OVERSHOOT rule which reduces Kp)
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=1.1,  # Extreme overshoot (>1.0°C) due to aggressive Kp
            undershoot=0.0,
            settling_time=40.0,
            oscillations=2,
            rise_time=25.0,
        )
        learner.add_cycle_metrics(metrics)

    result1 = learner.calculate_pid_adjustment(kp, ki, kd)
    assert result1 is not None
    assert result1["kp"] < kp  # Kp should be reduced (v0.7.0: HIGH_OVERSHOOT >1.0°C)

    # Apply first round adjustments
    kp, ki, kd = result1["kp"], result1["ki"], result1["kd"]

    # Round 2: Reduced overshoot due to lower Kp
    learner.clear_history()
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.3,  # Less overshoot now
            undershoot=0.1,
            settling_time=55.0,
            oscillations=0,
            rise_time=35.0,
        )
        learner.add_cycle_metrics(metrics)

    result2 = learner.calculate_pid_adjustment(kp, ki, kd)
    assert result2 is not None

    # Kp reduction should be smaller this round (moderate overshoot = 5% vs 15%)
    # The adjusted Kp should still be tuned appropriately
    assert PID_LIMITS["kp_min"] <= result2["kp"] <= PID_LIMITS["kp_max"]


# =============================================================================
# Test: PID Limits Are Respected
# =============================================================================


def test_pid_limits_enforced_on_high_values():
    """
    Integration test: PID adjustments are clamped to limits.
    """
    learner = AdaptiveLearner()

    # Cycles that would push for high Kp
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.0,
            undershoot=0.1,
            settling_time=60.0,
            oscillations=0,
            rise_time=80.0,  # Slow rise - would increase Kp
        )
        learner.add_cycle_metrics(metrics)

    # Start with values near limits
    kp, ki, kd = 480.0, 95.0, 4.5
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    assert result["kp"] <= PID_LIMITS["kp_max"], f"Kp {result['kp']} exceeds max"
    assert result["ki"] <= PID_LIMITS["ki_max"], f"Ki {result['ki']} exceeds max"
    assert result["kd"] <= PID_LIMITS["kd_max"], f"Kd {result['kd']} exceeds max"


def test_pid_limits_enforced_on_low_values():
    """
    Integration test: PID adjustments don't go below minimum limits.
    """
    learner = AdaptiveLearner()

    # Cycles that would push for low Kp (high overshoot)
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=1.0,  # Very high overshoot
            undershoot=0.0,
            settling_time=30.0,
            oscillations=5,
            rise_time=20.0,
        )
        learner.add_cycle_metrics(metrics)

    # Start with values near minimum
    kp, ki, kd = 15.0, 1.0, 5.0
    result = learner.calculate_pid_adjustment(kp, ki, kd)

    assert result is not None
    assert result["kp"] >= PID_LIMITS["kp_min"], f"Kp {result['kp']} below min"
    assert result["ki"] >= PID_LIMITS["ki_min"], f"Ki {result['ki']} below min"
    assert result["kd"] >= PID_LIMITS["kd_min"], f"Kd {result['kd']} below min"


# =============================================================================
# Test: Thermal Rate Learning
# =============================================================================


def test_thermal_rate_learning_cooling():
    """
    Integration test: Thermal rate learner tracks cooling rates.
    """
    learner = ThermalRateLearner()

    # Add cooling rate measurements (°C/hour)
    learner.add_cooling_measurement(1.2)
    learner.add_cooling_measurement(1.1)
    learner.add_cooling_measurement(1.3)

    avg_cooling = learner.get_average_cooling_rate()
    assert avg_cooling is not None
    assert 1.0 < avg_cooling < 1.5, f"Expected cooling rate around 1.2, got {avg_cooling}"


def test_thermal_rate_learning_heating():
    """
    Integration test: Thermal rate learner tracks heating rates.
    """
    learner = ThermalRateLearner()

    # Add heating rate measurements (°C/hour)
    learner.add_heating_measurement(2.5)
    learner.add_heating_measurement(2.3)
    learner.add_heating_measurement(2.7)

    avg_heating = learner.get_average_heating_rate()
    assert avg_heating is not None
    assert 2.0 < avg_heating < 3.0, f"Expected heating rate around 2.5, got {avg_heating}"


def test_thermal_rate_with_no_measurements():
    """
    Integration test: Thermal rate learner returns None with no data.
    """
    learner = ThermalRateLearner()

    assert learner.get_average_cooling_rate() is None
    assert learner.get_average_heating_rate() is None


# =============================================================================
# Test: End-to-End Adaptive Flow
# =============================================================================


def test_end_to_end_adaptive_learning_flow():
    """
    Integration test: Complete flow from cycle metrics to PID application.

    Flow:
    1. Simulate multiple heating cycles with specific characteristics
    2. Feed to adaptive learner
    3. Get PID recommendation
    4. Verify recommendation respects limits and addresses issues
    """
    learner = AdaptiveLearner()

    # Simulate MIN_CYCLES_FOR_LEARNING heating cycles with moderate overshoot and some oscillations
    for cycle in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.35 + cycle * 0.05,  # Increasing overshoot each cycle
            undershoot=0.1,
            settling_time=55.0 + cycle * 5,
            oscillations=1 + cycle,  # Increasing oscillations
            rise_time=40.0,
        )
        learner.add_cycle_metrics(metrics)

    # Initial PID values (v0.7.0: kd_max = 5.0)
    initial_kp, initial_ki, initial_kd = 100.0, 20.0, 3.0

    # Get recommendation
    result = learner.calculate_pid_adjustment(initial_kp, initial_ki, initial_kd)

    assert result is not None, "Expected PID adjustment"

    # Verify limits are respected
    assert PID_LIMITS["kp_min"] <= result["kp"] <= PID_LIMITS["kp_max"]
    assert PID_LIMITS["ki_min"] <= result["ki"] <= PID_LIMITS["ki_max"]
    assert PID_LIMITS["kd_min"] <= result["kd"] <= PID_LIMITS["kd_max"]

    # With average overshoot > 0.2 and oscillations > 1, expect:
    # - Kp reduced (for overshoot)
    # - Kd increased (for oscillations)
    assert result["kp"] < initial_kp, "Expected Kp reduction for overshoot"
    assert result["kd"] > initial_kd, "Expected Kd increase for oscillations"


def test_adaptive_pid_applied_to_controller(adaptive_learner, pid):
    """
    Integration test: Adaptive PID adjustments can be applied to controller.
    """
    # Add sufficient cycles
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.4,
            undershoot=0.1,
            settling_time=50.0,
            oscillations=2,
            rise_time=35.0,
        )
        adaptive_learner.add_cycle_metrics(metrics)

    # Get current PID values from controller
    current_kp = pid._Kp
    current_ki = pid._Ki
    current_kd = pid._Kd

    # Get recommendations
    result = adaptive_learner.calculate_pid_adjustment(current_kp, current_ki, current_kd)
    assert result is not None

    # Apply to PID controller
    pid.set_pid_param(kp=result["kp"], ki=result["ki"], kd=result["kd"])

    # Verify controller was updated
    assert pid._Kp == result["kp"]
    assert pid._Ki == result["ki"]
    assert pid._Kd == result["kd"]

    # Verify PID still works after update
    output, updated = pid.calc(19.0, 21.0)
    assert updated is True
    assert output > 0  # Should have heating demand


# =============================================================================
# Test: Combined Thermal Learning and Adaptive PID
# =============================================================================


def test_combined_thermal_and_adaptive_learning():
    """
    Integration test: Both thermal rate learning and adaptive PID work together.

    Simulates a complete learning cycle where:
    1. Thermal rates are observed
    2. Cycle performance is measured
    3. PID is adjusted based on both
    """
    thermal_learner = ThermalRateLearner()
    adaptive_learner = AdaptiveLearner()

    # Phase 1: Learn thermal rates
    thermal_learner.add_cooling_measurement(0.8)
    thermal_learner.add_cooling_measurement(0.9)
    thermal_learner.add_heating_measurement(2.2)
    thermal_learner.add_heating_measurement(2.0)

    cooling_rate = thermal_learner.get_average_cooling_rate()
    heating_rate = thermal_learner.get_average_heating_rate()

    assert cooling_rate is not None
    assert heating_rate is not None

    # Phase 2: Observe cycle performance
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=0.25,
            undershoot=0.2,
            settling_time=65.0,
            oscillations=1,
            rise_time=45.0,
        )
        adaptive_learner.add_cycle_metrics(metrics)

    # Phase 3: Get PID recommendations
    result = adaptive_learner.calculate_pid_adjustment(100.0, 20.0, 30.0)

    assert result is not None
    assert "kp" in result
    assert "ki" in result
    assert "kd" in result


def test_cycle_count_tracking(adaptive_learner):
    """
    Integration test: Cycle count is correctly tracked.
    """
    assert adaptive_learner.get_cycle_count() == 0

    # Add cycles
    for i in range(5):
        metrics = CycleMetrics(overshoot=0.2, oscillations=0)
        adaptive_learner.add_cycle_metrics(metrics)
        assert adaptive_learner.get_cycle_count() == i + 1

    # Clear history
    adaptive_learner.clear_history()
    assert adaptive_learner.get_cycle_count() == 0


def test_none_metrics_handled_gracefully():
    """
    Integration test: None values in metrics are handled gracefully.
    """
    learner = AdaptiveLearner()

    # Add cycles with some None values
    # Note: oscillations > 1 and undershoot > 0.3 to avoid convergence and trigger rules
    for _ in range(MIN_CYCLES_FOR_LEARNING):
        metrics = CycleMetrics(
            overshoot=None,  # None overshoot
            undershoot=0.4,  # Above rule threshold
            settling_time=None,  # None settling time
            oscillations=2,  # Above convergence threshold
            rise_time=None,  # None rise time
        )
        learner.add_cycle_metrics(metrics)

    # Should still produce a result (using available data)
    result = learner.calculate_pid_adjustment(100.0, 20.0, 30.0)
    assert result is not None
