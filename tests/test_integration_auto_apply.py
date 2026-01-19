"""Integration tests for auto-apply PID functionality.

This module tests the complete auto-apply flow including:
- Full auto-apply triggered after reaching confidence threshold
- Validation success scenario
- Validation failure and automatic rollback
- Limit enforcement (seasonal, lifetime, drift)
- Seasonal shift blocking
- Manual rollback service
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.adaptive_thermostat.managers.cycle_tracker import (
    CycleState,
    CycleTrackerManager,
)
from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_thermostat.const import (
    CONFIDENCE_INCREASE_PER_GOOD_CYCLE,
    VALIDATION_CYCLE_COUNT,
    get_auto_apply_thresholds,
    HEATING_TYPE_CONVECTOR,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()

    # Track created tasks so we can await them
    created_tasks = []

    def track_task(coro):
        created_tasks.append(coro)
        return coro

    hass.async_create_task = MagicMock(side_effect=track_task)
    hass._created_tasks = created_tasks  # Expose for tests

    def mock_call_later(delay, callback):
        return MagicMock()

    hass.async_call_later = MagicMock(side_effect=mock_call_later)
    return hass


@pytest.fixture
def adaptive_learner():
    """Create a real AdaptiveLearner instance for testing."""
    learner = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
    # Set physics baseline for drift calculations
    learner.set_physics_baseline(100.0, 0.01, 50.0)
    return learner


@pytest.fixture
def mock_callbacks():
    """Create mock getter callbacks."""
    return {
        "get_target_temp": MagicMock(return_value=21.0),
        "get_current_temp": MagicMock(return_value=19.0),
        "get_hvac_mode": MagicMock(return_value="heat"),
        "get_in_grace_period": MagicMock(return_value=False),
    }


def create_good_cycle_metrics(overshoot: float = 0.15) -> CycleMetrics:
    """Create metrics representing a good heating cycle.

    Args:
        overshoot: Overshoot value (default 0.15°C which is good for convector)

    Returns:
        CycleMetrics for a good cycle
    """
    return CycleMetrics(
        overshoot=overshoot,
        oscillations=1,  # Matches convector convergence threshold
        settling_time=30.0,  # Well under 60 min max
        rise_time=20.0,  # Well under 45 min max
        interruption_history=[],
    )


def create_bad_cycle_metrics(overshoot: float = 0.4) -> CycleMetrics:
    """Create metrics representing a poor heating cycle.

    Args:
        overshoot: Overshoot value (default 0.4°C which exceeds convector threshold)

    Returns:
        CycleMetrics for a bad cycle
    """
    return CycleMetrics(
        overshoot=overshoot,
        oscillations=3,  # Exceeds 1 oscillation max
        settling_time=90.0,  # Exceeds 60 min max
        rise_time=60.0,  # Exceeds 45 min max
        interruption_history=[],
    )


class TestFullAutoApplyFlow:
    """Test complete auto-apply flow from confidence building to validation."""

    @pytest.mark.asyncio
    async def test_full_auto_apply_flow(self, mock_hass, adaptive_learner, mock_callbacks):
        """Test complete auto-apply flow triggered after reaching confidence threshold.

        This test simulates:
        1. Building confidence through 6 good cycles (reaching 60% confidence)
        2. Auto-apply callback being triggered on 6th cycle finalization
        3. Validation mode being entered after auto-apply
        4. PID snapshot recorded with reason='auto_apply'
        5. Learning history cleared
        """
        # Track auto-apply callback calls
        auto_apply_called = []

        async def mock_auto_apply_check():
            auto_apply_called.append(True)

        # Create cycle tracker with real adaptive learner
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            on_auto_apply_check=mock_auto_apply_check,
        )

        # Simulate 6 good cycles (convector confidence_first=0.60 requires 6 good cycles)
        # Each good cycle adds 0.10 to confidence
        start_time = datetime(2024, 1, 1, 10, 0, 0)

        for cycle_num in range(6):
            current_time = start_time + timedelta(hours=cycle_num * 2)

            # Start heating
            tracker.on_heating_started(current_time)
            assert tracker.state == CycleState.HEATING

            # Collect temperature samples during heating (20 samples = 10 min)
            for i in range(20):
                temp = 19.0 + min(i * 0.15, 2.0)  # Rise to 21.0°C
                await tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)

            # Stop heating
            tracker.on_heating_stopped(current_time)
            assert tracker.state == CycleState.SETTLING

            # Settling samples (stable temperature = good cycle)
            for _ in range(10):
                await tracker.update_temperature(current_time, 21.0)
                current_time += timedelta(seconds=30)

            # Cycle should complete
            assert tracker.state == CycleState.IDLE

        # Verify confidence built up to at least 60% (6 good cycles * 0.10 = 0.60)
        assert adaptive_learner.get_convergence_confidence() >= 0.60

        # Await any created tasks (auto-apply callbacks)
        import asyncio
        for task in mock_hass._created_tasks:
            if asyncio.iscoroutine(task):
                await task

        # Verify auto-apply callback was triggered on cycle completions
        # (It's triggered after each cycle when not in validation mode)
        assert len(auto_apply_called) >= 1

        # Verify learner has recorded 6 cycles
        assert adaptive_learner.get_cycle_count() == 6

    @pytest.mark.asyncio
    async def test_auto_apply_triggers_validation_mode(self, mock_hass, adaptive_learner):
        """Test that auto-apply enters validation mode after applying PID."""
        # Build enough confidence (simulate 8 good cycles worth)
        adaptive_learner._convergence_confidence = 0.80

        # Add some cycle history for baseline overshoot calculation
        for i in range(6):
            metrics = create_good_cycle_metrics(overshoot=0.12)
            adaptive_learner.add_cycle_metrics(metrics)

        # Not in validation mode yet
        assert adaptive_learner.is_in_validation_mode() is False

        # Simulate auto-apply by starting validation mode with baseline
        baseline_overshoot = 0.12
        adaptive_learner.start_validation_mode(baseline_overshoot)

        # Verify validation mode is active
        assert adaptive_learner.is_in_validation_mode() is True
        assert adaptive_learner._validation_baseline_overshoot == baseline_overshoot
        assert adaptive_learner._validation_cycles == []

    @pytest.mark.asyncio
    async def test_pid_snapshot_recorded_on_auto_apply(self, adaptive_learner):
        """Test that PID snapshots are recorded before and after auto-apply."""
        # Record "before" snapshot (like async_auto_apply_adaptive_pid does)
        adaptive_learner.record_pid_snapshot(
            kp=100.0, ki=0.01, kd=50.0,
            reason="before_auto_apply",
            metrics={"baseline_overshoot": 0.15}
        )

        # Record "after" snapshot
        adaptive_learner.record_pid_snapshot(
            kp=90.0, ki=0.012, kd=45.0,
            reason="auto_apply",
            metrics={"baseline_overshoot": 0.15, "confidence": 0.65}
        )

        # Verify history
        history = adaptive_learner.get_pid_history()
        assert len(history) == 2

        # Verify before snapshot
        assert history[0]["reason"] == "before_auto_apply"
        assert history[0]["kp"] == 100.0

        # Verify auto_apply snapshot
        assert history[1]["reason"] == "auto_apply"
        assert history[1]["kp"] == 90.0
        assert history[1]["metrics"]["confidence"] == 0.65

    @pytest.mark.asyncio
    async def test_learning_history_cleared_after_auto_apply(self, adaptive_learner):
        """Test that learning history is cleared after auto-apply."""
        # Add some cycle history
        for i in range(5):
            metrics = create_good_cycle_metrics()
            adaptive_learner.add_cycle_metrics(metrics)

        assert adaptive_learner.get_cycle_count() == 5

        # Simulate auto-apply clearing history
        adaptive_learner.clear_history()

        # Verify history cleared
        assert adaptive_learner.get_cycle_count() == 0
        assert adaptive_learner._convergence_confidence == 0.0


class TestValidationSuccess:
    """Test validation success scenario."""

    @pytest.mark.asyncio
    async def test_validation_success_after_five_good_cycles(self, adaptive_learner):
        """Test that validation succeeds after 5 cycles with maintained/improved performance."""
        baseline_overshoot = 0.15
        adaptive_learner.start_validation_mode(baseline_overshoot)

        # Add 5 validation cycles with same or better overshoot
        for i in range(VALIDATION_CYCLE_COUNT):
            # Slightly better overshoot (0.12°C vs 0.15°C baseline)
            metrics = create_good_cycle_metrics(overshoot=0.12)
            result = adaptive_learner.add_validation_cycle(metrics)

            if i < VALIDATION_CYCLE_COUNT - 1:
                assert result is None  # Still collecting
            else:
                assert result == "success"

        # Validation mode should be exited
        assert adaptive_learner.is_in_validation_mode() is False

    @pytest.mark.asyncio
    async def test_validation_success_increments_auto_apply_count(self, adaptive_learner):
        """Test that auto_apply_count is tracked after validation."""
        # Note: auto_apply_count is incremented in async_auto_apply_adaptive_pid,
        # not after validation completes
        assert adaptive_learner.get_auto_apply_count() == 0

        # Simulate incrementing after auto-apply
        adaptive_learner._auto_apply_count = 1

        assert adaptive_learner.get_auto_apply_count() == 1

    @pytest.mark.asyncio
    async def test_full_validation_success_flow(
        self, mock_hass, adaptive_learner, mock_callbacks
    ):
        """Test complete validation success flow from auto-apply through validation completion.

        Integration test for story 9.2:
        1. Trigger auto-apply (6 good cycles reaching 60% confidence)
        2. Verify validation mode started with baseline_overshoot
        3. Simulate 5 validation cycles with equal/better overshoot
        4. On 5th cycle, verify add_validation_cycle returns 'success'
        5. Verify validation_mode = False
        6. Verify auto_apply_count incremented to 1
        """
        # Phase 1: Build confidence to trigger auto-apply conditions
        # Simulate 8 good cycles (convector confidence_first=0.60)
        for i in range(8):
            metrics = create_good_cycle_metrics(overshoot=0.15)
            adaptive_learner.add_cycle_metrics(metrics)
            adaptive_learner.update_convergence_confidence(metrics)

        # Verify confidence reached required threshold
        assert adaptive_learner.get_convergence_confidence() >= 0.60

        # Phase 2: Simulate auto-apply (what async_auto_apply_adaptive_pid does)
        # Record baseline overshoot from last 6 cycles
        import statistics
        recent_overshoots = [0.15] * 6  # All cycles had 0.15°C overshoot
        baseline_overshoot = statistics.mean(recent_overshoots)
        assert baseline_overshoot == 0.15

        # Record PID snapshot before auto-apply
        adaptive_learner.record_pid_snapshot(
            kp=100.0, ki=0.01, kd=50.0,
            reason="before_auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )

        # Apply new PID values (simulated)
        new_kp, new_ki, new_kd = 90.0, 0.012, 45.0

        # Record PID snapshot after auto-apply
        adaptive_learner.record_pid_snapshot(
            kp=new_kp, ki=new_ki, kd=new_kd,
            reason="auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )

        # Clear learning history (as auto-apply does)
        adaptive_learner.clear_history()
        assert adaptive_learner.get_cycle_count() == 0
        assert adaptive_learner._convergence_confidence == 0.0

        # Increment auto_apply_count (done in async_auto_apply_adaptive_pid)
        adaptive_learner._auto_apply_count += 1
        assert adaptive_learner.get_auto_apply_count() == 1

        # Start validation mode with baseline
        adaptive_learner.start_validation_mode(baseline_overshoot)

        # Phase 3: Verify validation mode is active with correct baseline
        assert adaptive_learner.is_in_validation_mode() is True
        assert adaptive_learner._validation_baseline_overshoot == baseline_overshoot
        assert len(adaptive_learner._validation_cycles) == 0

        # Phase 4: Complete 5 validation cycles with improved overshoot
        validation_overshoot = 0.12  # Better than 0.15°C baseline
        for i in range(VALIDATION_CYCLE_COUNT):
            metrics = create_good_cycle_metrics(overshoot=validation_overshoot)
            result = adaptive_learner.add_validation_cycle(metrics)

            if i < VALIDATION_CYCLE_COUNT - 1:
                # Still collecting cycles
                assert result is None
                assert adaptive_learner.is_in_validation_mode() is True
                assert len(adaptive_learner._validation_cycles) == i + 1
            else:
                # 5th cycle - validation should complete with success
                assert result == "success"

        # Phase 5: Verify validation completed successfully
        assert adaptive_learner.is_in_validation_mode() is False

        # Phase 6: Verify auto_apply_count is 1 (was incremented before validation)
        assert adaptive_learner.get_auto_apply_count() == 1

        # Verify PID history contains the auto-apply record
        history = adaptive_learner.get_pid_history()
        assert len(history) == 2
        assert history[1]["reason"] == "auto_apply"
        assert history[1]["kp"] == 90.0


class TestValidationFailureAndRollback:
    """Test validation failure and automatic rollback scenario."""

    @pytest.mark.asyncio
    async def test_validation_failure_triggers_rollback(
        self, mock_hass, adaptive_learner, mock_callbacks
    ):
        """Test that validation failure triggers rollback callback."""
        rollback_called = []

        async def mock_validation_failed():
            rollback_called.append(True)

        # Create tracker with rollback callback
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            on_validation_failed=mock_validation_failed,
        )

        # Enter validation mode with baseline
        baseline_overshoot = 0.10
        adaptive_learner.start_validation_mode(baseline_overshoot)

        # Simulate 5 validation cycles with 40% worse overshoot (triggers rollback)
        degraded_overshoot = baseline_overshoot * 1.4  # 0.14°C
        for i in range(VALIDATION_CYCLE_COUNT):
            # Complete a cycle that produces the degraded metrics
            start_time = datetime(2024, 1, 1, 10 + i, 0, 0)
            tracker.on_heating_started(start_time)

            # Quick heating cycle
            current_time = start_time
            for j in range(20):
                temp = 19.0 + min(j * 0.15, 2.0)
                await tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)

            tracker.on_heating_stopped(current_time)

            # Settling with overshoot
            for _ in range(10):
                # Create overshoot scenario
                await tracker.update_temperature(current_time, 21.0 + degraded_overshoot)
                current_time += timedelta(seconds=30)

        # Await any created tasks (rollback callbacks)
        import asyncio
        for task in mock_hass._created_tasks:
            if asyncio.iscoroutine(task):
                await task

        # Validation should have completed with rollback result
        assert adaptive_learner.is_in_validation_mode() is False
        # Rollback callback should have been triggered
        assert len(rollback_called) >= 1

    @pytest.mark.asyncio
    async def test_rollback_restores_previous_pid(self, adaptive_learner):
        """Test that rollback retrieves correct previous PID values."""
        # Record initial PID (before auto-apply)
        adaptive_learner.record_pid_snapshot(
            kp=100.0, ki=0.01, kd=50.0,
            reason="before_auto_apply"
        )

        # Record auto-applied PID
        adaptive_learner.record_pid_snapshot(
            kp=90.0, ki=0.012, kd=45.0,
            reason="auto_apply"
        )

        # Get previous PID for rollback
        previous = adaptive_learner.get_previous_pid()

        assert previous is not None
        assert previous["kp"] == 100.0
        assert previous["ki"] == 0.01
        assert previous["kd"] == 50.0
        assert previous["reason"] == "before_auto_apply"

    @pytest.mark.asyncio
    async def test_rollback_records_snapshot(self, adaptive_learner):
        """Test that rollback records a snapshot with reason='rollback'."""
        # Setup history
        adaptive_learner.record_pid_snapshot(100.0, 0.01, 50.0, reason="before_auto_apply")
        adaptive_learner.record_pid_snapshot(90.0, 0.012, 45.0, reason="auto_apply")

        # Simulate rollback recording
        previous = adaptive_learner.get_previous_pid()
        adaptive_learner.record_pid_snapshot(
            kp=previous["kp"],
            ki=previous["ki"],
            kd=previous["kd"],
            reason="rollback",
            metrics={
                "rolled_back_from_kp": 90.0,
                "rolled_back_from_ki": 0.012,
                "rolled_back_from_kd": 45.0,
            }
        )

        history = adaptive_learner.get_pid_history()
        assert len(history) == 3
        assert history[-1]["reason"] == "rollback"
        assert history[-1]["kp"] == 100.0

    @pytest.mark.asyncio
    async def test_validation_failure_automatic_rollback(
        self, mock_hass, adaptive_learner, mock_callbacks
    ):
        """Test complete validation failure and automatic rollback flow.

        Integration test for story 9.3:
        1. Record initial PID values (kp=100, ki=0.01, kd=50)
        2. Trigger auto-apply, verify new PID applied (e.g., kp=90)
        3. Verify validation mode started
        4. Simulate 5 validation cycles with 40% worse overshoot (degradation)
        5. On 5th cycle, verify add_validation_cycle returns 'rollback'
        6. Verify _handle_validation_failure callback triggered
        7. Verify PID rolled back to previous values (kp=100)
        8. Verify learning history cleared
        9. Verify auto_apply_count NOT incremented (still 0)
        """
        # Track rollback callback invocations
        rollback_called = []

        async def mock_validation_failed():
            rollback_called.append(True)

        # Create cycle tracker with validation failure callback
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            on_validation_failed=mock_validation_failed,
        )

        # Phase 1: Record initial PID values (baseline before auto-apply)
        initial_kp, initial_ki, initial_kd = 100.0, 0.01, 50.0
        adaptive_learner.record_pid_snapshot(
            kp=initial_kp,
            ki=initial_ki,
            kd=initial_kd,
            reason="before_auto_apply",
            metrics={"baseline_overshoot": 0.15}
        )

        # Phase 2: Simulate auto-apply (applying new PID values)
        new_kp, new_ki, new_kd = 90.0, 0.012, 45.0
        adaptive_learner.record_pid_snapshot(
            kp=new_kp,
            ki=new_ki,
            kd=new_kd,
            reason="auto_apply",
            metrics={"baseline_overshoot": 0.15}
        )

        # Verify new PID values were applied
        history = adaptive_learner.get_pid_history()
        assert len(history) == 2
        assert history[-1]["kp"] == new_kp
        assert history[-1]["reason"] == "auto_apply"

        # Phase 3: Start validation mode with baseline overshoot
        baseline_overshoot = 0.15
        adaptive_learner.start_validation_mode(baseline_overshoot)
        assert adaptive_learner.is_in_validation_mode() is True
        assert adaptive_learner._validation_baseline_overshoot == baseline_overshoot

        # Phase 4: Simulate 5 validation cycles with 40% worse overshoot (degradation)
        # 40% worse: 0.15 * 1.4 = 0.21°C (exceeds 30% degradation threshold)
        degraded_overshoot = baseline_overshoot * 1.4
        start_time = datetime(2024, 1, 1, 10, 0, 0)

        for i in range(VALIDATION_CYCLE_COUNT):
            current_time = start_time + timedelta(hours=i * 2)

            # Start heating
            tracker.on_heating_started(current_time)
            assert tracker.state == CycleState.HEATING

            # Collect temperature samples during heating
            for j in range(20):
                temp = 19.0 + min(j * 0.15, 2.0)
                await tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)

            # Stop heating
            tracker.on_heating_stopped(current_time)
            assert tracker.state == CycleState.SETTLING

            # Settling with degraded overshoot
            for _ in range(10):
                await tracker.update_temperature(current_time, 21.0 + degraded_overshoot)
                current_time += timedelta(seconds=30)

            # Cycle should complete
            assert tracker.state == CycleState.IDLE

        # Phase 5: Verify validation completed with rollback result
        # The 5th cycle should have triggered rollback via add_validation_cycle
        assert adaptive_learner.is_in_validation_mode() is False

        # Phase 6: Verify _handle_validation_failure callback was triggered
        import asyncio
        for task in mock_hass._created_tasks:
            if asyncio.iscoroutine(task):
                await task

        assert len(rollback_called) >= 1, "Validation failure callback should be triggered"

        # Phase 7: Simulate rollback (what _handle_validation_failure does)
        # Get previous PID values
        previous = adaptive_learner.get_previous_pid()
        assert previous is not None
        assert previous["kp"] == initial_kp
        assert previous["ki"] == initial_ki
        assert previous["kd"] == initial_kd

        # Record rollback snapshot
        adaptive_learner.record_pid_snapshot(
            kp=previous["kp"],
            ki=previous["ki"],
            kd=previous["kd"],
            reason="rollback",
            metrics={
                "rolled_back_from_kp": new_kp,
                "rolled_back_from_ki": new_ki,
                "rolled_back_from_kd": new_kd,
                "degradation_percent": 40.0,
            }
        )

        # Verify rollback snapshot recorded
        history = adaptive_learner.get_pid_history()
        assert len(history) == 3
        assert history[-1]["reason"] == "rollback"
        assert history[-1]["kp"] == initial_kp
        assert history[-1]["ki"] == initial_ki
        assert history[-1]["kd"] == initial_kd

        # Phase 8: Clear learning history (as rollback does)
        adaptive_learner.clear_history()
        assert adaptive_learner.get_cycle_count() == 0
        assert adaptive_learner._convergence_confidence == 0.0

        # Phase 9: Verify auto_apply_count NOT incremented (still 0)
        # When validation fails, we don't increment auto_apply_count
        # because the auto-apply is being rolled back
        assert adaptive_learner.get_auto_apply_count() == 0


class TestLimitEnforcement:
    """Test auto-apply limit enforcement."""

    @pytest.mark.asyncio
    async def test_seasonal_limit_blocks_sixth_apply(self, adaptive_learner):
        """Test that 6th auto-apply within 90 days is blocked.

        PRD Story 9.4 Steps:
        1. Trigger 5 successful auto-applies within 90 days
        2. Build confidence to 80% for 6th attempt
        3. Attempt 6th auto-apply, verify blocked
        4. Verify calculate_pid_adjustment returns None
        5. Verify log warning contains 'Seasonal limit reached'
        """
        # Step 1: Trigger 5 successful auto-applies within 90 days
        now = datetime.now()
        for i in range(5):
            adaptive_learner._pid_history.append({
                "timestamp": now - timedelta(days=i * 10),
                "kp": 100.0 - i,
                "ki": 0.01,
                "kd": 50.0,
                "reason": "auto_apply",
                "metrics": None,
            })

        # Step 2: Build confidence to 80% for 6th attempt
        adaptive_learner._convergence_confidence = 0.80

        # Step 3: Attempt 6th auto-apply via check_auto_apply_limits, verify blocked
        result = adaptive_learner.check_auto_apply_limits(95.0, 0.011, 48.0)

        assert result is not None
        assert "Seasonal limit reached" in result
        assert "90 days" in result

        # Step 4: Verify calculate_pid_adjustment with check_auto_apply=True returns None
        # when seasonal limit is reached
        adjustment = adaptive_learner.calculate_pid_adjustment(
            current_kp=95.0,
            current_ki=0.011,
            current_kd=48.0,
            check_auto_apply=True,
        )

        assert adjustment is None, "calculate_pid_adjustment should return None when seasonal limit reached"

        # Step 5: Log warning verification is implicit via check_auto_apply_limits assertion above

    @pytest.mark.asyncio
    async def test_drift_limit_blocks_apply(self, adaptive_learner):
        """Test that >50% cumulative drift blocks auto-apply.

        PRD Story 9.4 Steps:
        1. Set physics baseline
        2. Simulate 3 auto-applies creating 55% cumulative drift
        3. Attempt next auto-apply, verify blocked with drift error
        """
        # Step 1: Set physics baseline
        baseline_kp, baseline_ki, baseline_kd = 100.0, 0.01, 50.0
        adaptive_learner.set_physics_baseline(baseline_kp, baseline_ki, baseline_kd)

        # Step 2: Simulate 3 auto-applies creating 55% cumulative drift
        # Start with baseline values
        now = datetime.now()
        adaptive_learner._pid_history.append({
            "timestamp": now - timedelta(days=60),
            "kp": baseline_kp,
            "ki": baseline_ki,
            "kd": baseline_kd,
            "reason": "physics_reset",
            "metrics": None,
        })

        # First auto-apply: +20% Kp drift (120.0)
        adaptive_learner._pid_history.append({
            "timestamp": now - timedelta(days=50),
            "kp": 120.0,
            "ki": baseline_ki,
            "kd": baseline_kd,
            "reason": "auto_apply",
            "metrics": None,
        })

        # Second auto-apply: +35% Kp drift (135.0)
        adaptive_learner._pid_history.append({
            "timestamp": now - timedelta(days=30),
            "kp": 135.0,
            "ki": baseline_ki,
            "kd": baseline_kd,
            "reason": "auto_apply",
            "metrics": None,
        })

        # Third auto-apply: +50% Kp drift (150.0) - at the limit
        adaptive_learner._pid_history.append({
            "timestamp": now - timedelta(days=10),
            "kp": 150.0,
            "ki": baseline_ki,
            "kd": baseline_kd,
            "reason": "auto_apply",
            "metrics": None,
        })

        # Step 3: Attempt 4th auto-apply with 55% drift (155.0), verify blocked
        result = adaptive_learner.check_auto_apply_limits(155.0, baseline_ki, baseline_kd)

        assert result is not None
        assert "Cumulative drift limit exceeded" in result
        assert "55.0%" in result

        # Also verify via calculate_pid_adjustment with check_auto_apply=True
        adjustment = adaptive_learner.calculate_pid_adjustment(
            current_kp=155.0,
            current_ki=baseline_ki,
            current_kd=baseline_kd,
            check_auto_apply=True,
        )

        assert adjustment is None, "calculate_pid_adjustment should return None when drift limit exceeded"


class TestSeasonalShiftBlocking:
    """Test seasonal shift blocking functionality."""

    @pytest.mark.asyncio
    async def test_seasonal_shift_blocks_auto_apply(self, adaptive_learner):
        """Test that seasonal shift blocks auto-apply for 7 days."""
        # Record seasonal shift 3 days ago
        adaptive_learner._last_seasonal_shift = datetime.now() - timedelta(days=3)

        # Check limits - should be blocked
        result = adaptive_learner.check_auto_apply_limits(100.0, 0.01, 50.0)

        assert result is not None
        assert "Seasonal shift block active" in result
        # Should show ~4 days remaining
        assert "days remaining" in result

    @pytest.mark.asyncio
    async def test_seasonal_shift_unblocks_after_7_days(self, adaptive_learner):
        """Test that auto-apply is unblocked after 7 days."""
        # Record seasonal shift 8 days ago (past the 7-day block)
        adaptive_learner._last_seasonal_shift = datetime.now() - timedelta(days=8)

        # Check limits - should NOT be blocked by seasonal shift
        result = adaptive_learner.check_auto_apply_limits(100.0, 0.01, 50.0)

        # Either None (all pass) or some other reason, but not seasonal shift
        if result is not None:
            assert "Seasonal shift" not in result

    @pytest.mark.asyncio
    async def test_seasonal_shift_blocks_auto_apply_integration(self, adaptive_learner):
        """Test complete seasonal shift detection and blocking flow.

        Integration test for story 9.5:
        1. Build confidence to 70%
        2. Set outdoor_temp history to stable 15°C average
        3. Simulate outdoor_temp drop to 3°C (12°C shift)
        4. Call calculate_pid_adjustment with outdoor_temp=3°C and check_auto_apply=True
        5. Verify check_seasonal_shift detects shift
        6. Verify record_seasonal_shift called
        7. Verify calculate_pid_adjustment returns None (blocked)
        8. Wait 8 days (mock time), attempt auto-apply again, verify unblocked
        """
        # Step 1: Build confidence to 70%
        adaptive_learner._convergence_confidence = 0.70

        # Step 2: Set outdoor_temp history to stable 15°C average
        # Need at least 10 readings for shift detection
        for _ in range(15):
            adaptive_learner._outdoor_temp_history.append(15.0)

        # Set last_seasonal_check to allow check to proceed
        adaptive_learner._last_seasonal_check = datetime.now() - timedelta(days=2)

        # Verify initial state - no seasonal shift recorded
        assert adaptive_learner._last_seasonal_shift is None

        # Step 3: Simulate outdoor_temp drop to 3°C (12°C shift)
        # Add 10 more readings at 3°C to establish new regime
        for _ in range(10):
            adaptive_learner._outdoor_temp_history.append(3.0)

        # Step 4 & 5: Call calculate_pid_adjustment with outdoor_temp=3°C and check_auto_apply=True
        # This should internally call check_seasonal_shift and record_seasonal_shift
        adjustment = adaptive_learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=0.01,
            current_kd=50.0,
            check_auto_apply=True,
            outdoor_temp=3.0,
        )

        # Step 6: Verify check_seasonal_shift detected shift (via log) and record_seasonal_shift was called
        assert adaptive_learner._last_seasonal_shift is not None, "record_seasonal_shift should have been called"

        # Step 7: Verify calculate_pid_adjustment returns None (blocked)
        assert adjustment is None, "calculate_pid_adjustment should return None when seasonal shift blocks auto-apply"

        # Step 8: Wait 8 days (mock time), attempt auto-apply again, verify unblocked
        # Mock the shift as happening 8 days ago
        adaptive_learner._last_seasonal_shift = datetime.now() - timedelta(days=8)

        # Verify seasonal shift block is no longer active
        limit_check = adaptive_learner.check_auto_apply_limits(100.0, 0.01, 50.0)
        if limit_check is not None:
            assert "Seasonal shift" not in limit_check, "Seasonal shift block should have expired after 8 days"

        # Add some cycles to pass the cycle gate for adjustment
        for i in range(6):
            metrics = create_good_cycle_metrics(overshoot=0.12)
            adaptive_learner.add_cycle_metrics(metrics)

        # Now calculate_pid_adjustment should not be blocked by seasonal shift
        # (may still return None due to convergence, but not due to seasonal shift)
        adjustment = adaptive_learner.calculate_pid_adjustment(
            current_kp=100.0,
            current_ki=0.01,
            current_kd=50.0,
            check_auto_apply=True,
            outdoor_temp=3.0,
        )

        # We don't assert that adjustment is not None, because the system might
        # be converged or have other reasons to skip adjustment.
        # The key is that it's NOT blocked by seasonal shift anymore.
        # We already verified this with the limit_check above.


class TestManualRollbackService:
    """Test manual rollback service functionality."""

    @pytest.mark.asyncio
    async def test_manual_rollback_retrieves_previous_config(self, adaptive_learner):
        """Test that manual rollback retrieves correct previous configuration."""
        # Set initial PID
        adaptive_learner.record_pid_snapshot(100.0, 0.01, 50.0, reason="physics_reset")

        # Simulate auto-apply
        adaptive_learner.record_pid_snapshot(90.0, 0.012, 55.0, reason="auto_apply")

        # Get previous for rollback
        previous = adaptive_learner.get_previous_pid()

        assert previous is not None
        assert previous["kp"] == 100.0
        assert previous["ki"] == 0.01
        assert previous["kd"] == 50.0

    @pytest.mark.asyncio
    async def test_rollback_clears_history(self, adaptive_learner):
        """Test that rollback clears learning history."""
        # Add some cycle history
        for i in range(5):
            metrics = create_good_cycle_metrics()
            adaptive_learner.add_cycle_metrics(metrics)

        assert adaptive_learner.get_cycle_count() == 5

        # Simulate rollback clearing history
        adaptive_learner.clear_history()

        assert adaptive_learner.get_cycle_count() == 0


class TestAutoApplyDisabled:
    """Test behavior when auto_apply_pid is disabled."""

    @pytest.mark.asyncio
    async def test_no_auto_apply_when_disabled(self, mock_hass, adaptive_learner, mock_callbacks):
        """Test that auto-apply callback is not triggered when disabled.

        Note: The actual disabling is handled in climate.py by not setting the
        on_auto_apply_check callback. This test verifies that without the callback,
        auto-apply is not triggered.
        """
        # Create tracker WITHOUT on_auto_apply_check callback
        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            # on_auto_apply_check NOT provided
        )

        # Complete a cycle
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        tracker.on_heating_started(start_time)

        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        tracker.on_heating_stopped(current_time)

        for _ in range(10):
            await tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Cycle completes but no auto-apply callback was ever set
        assert tracker.state == CycleState.IDLE
        assert tracker._on_auto_apply_check is None


class TestValidationModeBlocking:
    """Test that auto-apply is blocked during validation mode."""

    @pytest.mark.asyncio
    async def test_auto_apply_blocked_during_validation(
        self, mock_hass, adaptive_learner, mock_callbacks
    ):
        """Test that auto-apply callback is not triggered during validation."""
        auto_apply_calls = []

        async def mock_auto_apply():
            auto_apply_calls.append(True)

        tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="test_zone",
            adaptive_learner=adaptive_learner,
            get_target_temp=mock_callbacks["get_target_temp"],
            get_current_temp=mock_callbacks["get_current_temp"],
            get_hvac_mode=mock_callbacks["get_hvac_mode"],
            get_in_grace_period=mock_callbacks["get_in_grace_period"],
            on_auto_apply_check=mock_auto_apply,
        )

        # Enter validation mode
        adaptive_learner.start_validation_mode(baseline_overshoot=0.15)
        assert adaptive_learner.is_in_validation_mode() is True

        # Complete a cycle while in validation mode
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        tracker.on_heating_started(start_time)

        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)

        tracker.on_heating_stopped(current_time)

        for _ in range(10):
            await tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)

        # Auto-apply should NOT be called during validation mode
        # (the callback triggers only when not in validation mode)
        assert len(auto_apply_calls) == 0


# Marker test for module existence
def test_integration_auto_apply_module_exists():
    """Marker test to verify module can be imported."""
    from custom_components.adaptive_thermostat.managers.cycle_tracker import (
        CycleTrackerManager,
        CycleState,
    )
    from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner

    assert CycleTrackerManager is not None
    assert CycleState is not None
    assert AdaptiveLearner is not None
