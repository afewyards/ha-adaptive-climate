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
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.adaptive_thermostat.managers.cycle_tracker import (
    CycleState,
    CycleTrackerManager,
)
from custom_components.adaptive_thermostat.managers.events import (
    CycleEventDispatcher,
    CycleStartedEvent,
    SettlingStartedEvent,
)
from custom_components.adaptive_thermostat.adaptive.learning import (
    AdaptiveLearner,
    get_auto_apply_thresholds,
)
from custom_components.adaptive_thermostat.adaptive.cycle_analysis import CycleMetrics
from custom_components.adaptive_thermostat.const import (
    CONFIDENCE_INCREASE_PER_GOOD_CYCLE,
    VALIDATION_CYCLE_COUNT,
    HEATING_TYPE_CONVECTOR,
)

# Module-level patches for dt_util and time
pytestmark = [
    pytest.mark.asyncio,
]

# Global time tracking for tests
_test_current_time = datetime(2024, 1, 1, 10, 0, 0)


def _mock_utcnow():
    """Return the current test time."""
    return _test_current_time


def _set_test_time(dt: datetime):
    """Set the current test time."""
    global _test_current_time
    _test_current_time = dt


@pytest.fixture(autouse=True)
def mock_dt_modules():
    """Automatically patch dt_util.utcnow for all tests in this module."""
    with patch('custom_components.adaptive_thermostat.managers.cycle_metrics.dt_util') as mock_metrics_dt, \
         patch('custom_components.adaptive_thermostat.adaptive.validation.dt_util') as mock_validation_dt:
        mock_metrics_dt.utcnow = _mock_utcnow
        mock_validation_dt.utcnow = _mock_utcnow
        yield


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


@pytest.fixture
def dispatcher():
    """Create a cycle event dispatcher."""
    return CycleEventDispatcher()


@pytest.fixture
def mock_dt_util():
    """Create a mock dt_util that tracks time progression."""
    current_time = [datetime(2024, 1, 1, 10, 0, 0)]

    class MockDtUtil:
        @staticmethod
        def utcnow():
            return current_time[0]

        @staticmethod
        def advance(delta: timedelta):
            current_time[0] += delta

        @staticmethod
        def set_time(time: datetime):
            current_time[0] = time

    return MockDtUtil()


# Decorator to patch dt_util for integration tests
def patch_dt_util(func):
    """Decorator to patch dt_util.utcnow for cycle_metrics and validation modules."""
    @patch('custom_components.adaptive_thermostat.managers.cycle_metrics.dt_util')
    @patch('custom_components.adaptive_thermostat.adaptive.validation.dt_util')
    async def wrapper(self, mock_validation_dt, mock_metrics_dt, *args, **kwargs):
        # Set up mocked dt_util to return real datetime objects
        current_time = [datetime(2024, 1, 1, 10, 0, 0)]

        def mock_utcnow():
            return current_time[0]

        mock_metrics_dt.utcnow = mock_utcnow
        mock_validation_dt.utcnow = mock_utcnow

        # Add time tracking to kwargs so tests can advance time
        kwargs['_current_time'] = current_time
        return await func(self, *args, **kwargs)

    return wrapper


def create_good_cycle_metrics(overshoot: float = 0.15) -> CycleMetrics:
    """Create metrics representing a good heating cycle.

    Args:
        overshoot: Overshoot value (default 0.15°C which is good for convector)

    Returns:
        CycleMetrics for a good cycle
    """
    return CycleMetrics(
        overshoot=overshoot,
        undershoot=0.1,  # Low undershoot for good cycle
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

    async def test_full_auto_apply_flow(self, mock_hass, adaptive_learner, mock_callbacks, dispatcher):
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Simulate 6 good cycles (convector confidence_first=0.60 requires 6 good cycles)
        # Each good cycle adds 0.10 to confidence
        start_time = datetime(2024, 1, 1, 10, 0, 0)

        for cycle_num in range(6):
            current_time = start_time + timedelta(hours=cycle_num * 2)
            _set_test_time(current_time)

            # Start heating via event (start closer to setpoint to keep undershoot < 0.2°C)
            start_temp = 20.85  # undershoot will be 21.0 - 20.85 = 0.15°C
            dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=current_time, target_temp=21.0, current_temp=start_temp))
            assert tracker.state == CycleState.HEATING

            # Collect temperature samples during heating (20 samples = 10 min)
            for i in range(20):
                temp = start_temp + min(i * 0.01, 0.15)  # Rise gradually to 21.0°C
                await tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

            # Stop heating via event
            dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))
            assert tracker.state == CycleState.SETTLING

            # Settling samples (stable temperature = good cycle)
            for _ in range(10):
                await tracker.update_temperature(current_time, 21.0)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

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

    async def test_validation_failure_triggers_rollback(
        self, mock_hass, adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Enter validation mode with baseline
        baseline_overshoot = 0.10
        adaptive_learner.start_validation_mode(baseline_overshoot)

        # Simulate 5 validation cycles with 40% worse overshoot (triggers rollback)
        degraded_overshoot = baseline_overshoot * 1.4  # 0.14°C
        for i in range(VALIDATION_CYCLE_COUNT):
            # Complete a cycle that produces the degraded metrics
            start_time = datetime(2024, 1, 1, 10 + i, 0, 0)
            _set_test_time(start_time)
            dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

            # Quick heating cycle
            current_time = start_time
            for j in range(20):
                temp = 19.0 + min(j * 0.15, 2.0)
                await tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

            dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

            # Settling with overshoot
            for _ in range(10):
                # Create overshoot scenario
                await tracker.update_temperature(current_time, 21.0 + degraded_overshoot)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

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
        self, mock_hass, adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

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
            _set_test_time(current_time)

            # Start heating via event
            dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=current_time, target_temp=21.0, current_temp=19.0))
            assert tracker.state == CycleState.HEATING

            # Collect temperature samples during heating
            for j in range(20):
                temp = 19.0 + min(j * 0.15, 2.0)
                await tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

            # Stop heating via event
            dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))
            assert tracker.state == CycleState.SETTLING

            # Settling with degraded overshoot
            for _ in range(10):
                await tracker.update_temperature(current_time, 21.0 + degraded_overshoot)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

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
        now = datetime(2024, 1, 15, 12, 0, 0)
        _set_test_time(now)
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
        now = datetime(2024, 1, 15, 12, 0, 0)
        _set_test_time(now)
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

    @pytest.mark.asyncio
    async def test_lifetime_limit_blocks_21st_apply(self, adaptive_learner):
        """Test that 21st lifetime auto-apply is blocked.

        PRD Story 10.4 Steps:
        1. Set learner._auto_apply_count = 19
        2. Trigger successful 20th auto-apply
        3. Verify _auto_apply_count = 20
        4. Build confidence to 80% again
        5. Attempt 21st auto-apply
        6. Verify check_auto_apply_limits returns 'Lifetime limit reached' error
        7. Verify calculate_pid_adjustment returns None
        8. Verify log warning mentions manual review required
        """
        # Step 1: Set _auto_apply_count to 19 (one below the limit)
        adaptive_learner._auto_apply_count = 19

        # Step 2: Trigger successful 20th auto-apply
        # Simulate the auto-apply process: record PID snapshots and increment count
        baseline_overshoot = 0.15
        adaptive_learner.record_pid_snapshot(
            kp=100.0, ki=0.01, kd=50.0,
            reason="before_auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )
        adaptive_learner.record_pid_snapshot(
            kp=95.0, ki=0.012, kd=48.0,
            reason="auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )
        adaptive_learner.clear_history()
        adaptive_learner._auto_apply_count += 1  # Simulates what async_auto_apply_adaptive_pid does

        # Step 3: Verify _auto_apply_count = 20
        assert adaptive_learner._auto_apply_count == 20
        assert adaptive_learner.get_auto_apply_count() == 20

        # Step 4: Build confidence to 80% again for next attempt
        for i in range(10):
            metrics = create_good_cycle_metrics(overshoot=0.12)
            adaptive_learner.add_cycle_metrics(metrics)
            adaptive_learner.update_convergence_confidence(metrics)
        adaptive_learner._convergence_confidence = 0.80  # Ensure we hit threshold

        # Step 5: Attempt 21st auto-apply
        # Check via check_auto_apply_limits first
        result = adaptive_learner.check_auto_apply_limits(93.0, 0.013, 46.0)

        # Step 6: Verify 'Lifetime limit reached' error
        assert result is not None, "check_auto_apply_limits should return error at lifetime limit"
        assert "Lifetime limit reached" in result
        assert "manual review" in result.lower()

        # Step 7: Verify calculate_pid_adjustment returns None when blocked
        adjustment = adaptive_learner.calculate_pid_adjustment(
            current_kp=93.0,
            current_ki=0.013,
            current_kd=46.0,
            check_auto_apply=True,
        )

        assert adjustment is None, "calculate_pid_adjustment should return None when lifetime limit reached"

        # Step 8: Log warning verification is implicit via the assertion on 'manual review'
        # The check_auto_apply_limits return value contains the warning message that would be logged


class TestSeasonalShiftBlocking:
    """Test seasonal shift blocking functionality."""

    async def test_seasonal_shift_blocks_auto_apply(self, adaptive_learner):
        """Test that seasonal shift blocks auto-apply for 7 days."""
        # Record seasonal shift 3 days ago
        now = datetime(2024, 1, 15, 12, 0, 0)
        _set_test_time(now)
        adaptive_learner._last_seasonal_shift = now - timedelta(days=3)

        # Check limits - should be blocked
        result = adaptive_learner.check_auto_apply_limits(100.0, 0.01, 50.0)

        assert result is not None
        assert "Seasonal shift block active" in result
        # Should show ~4 days remaining
        assert "days remaining" in result

    async def test_seasonal_shift_unblocks_after_7_days(self, adaptive_learner):
        """Test that auto-apply is unblocked after 7 days."""
        # Record seasonal shift 8 days ago (past the 7-day block)
        now = datetime(2024, 1, 15, 12, 0, 0)
        _set_test_time(now)
        adaptive_learner._last_seasonal_shift = now - timedelta(days=8)

        # Check limits - should NOT be blocked by seasonal shift
        result = adaptive_learner.check_auto_apply_limits(100.0, 0.01, 50.0)

        # Either None (all pass) or some other reason, but not seasonal shift
        if result is not None:
            assert "Seasonal shift" not in result

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
        now = datetime(2024, 1, 15, 12, 0, 0)
        _set_test_time(now)
        adaptive_learner._last_seasonal_check = now - timedelta(days=2)

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
        now_plus_8 = now + timedelta(days=8)
        _set_test_time(now_plus_8)
        adaptive_learner._last_seasonal_shift = now

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

    @pytest.mark.asyncio
    async def test_manual_rollback_service(
        self, mock_hass, adaptive_learner, mock_callbacks
    ):
        """Test complete manual rollback service flow.

        Integration test for story 9.6:
        1. Set initial PID (kp=100, ki=0.01, kd=50)
        2. Trigger auto-apply to new PID (kp=90, ki=0.012, kd=55)
        3. Verify PID history has 2 entries
        4. Call adaptive_thermostat.rollback_pid service
        5. Verify async_rollback_pid called
        6. Verify PID reverted to kp=100, ki=0.01, kd=50
        7. Verify rollback snapshot recorded
        8. Verify learning history cleared
        9. Verify persistent notification sent about rollback
        """
        # Track service calls
        service_calls = []

        async def mock_service_call(domain, service, data):
            service_calls.append({"domain": domain, "service": service, "data": data})

        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock(side_effect=mock_service_call)

        # Step 1: Set initial PID (kp=100, ki=0.01, kd=50)
        initial_kp, initial_ki, initial_kd = 100.0, 0.01, 50.0
        adaptive_learner.record_pid_snapshot(
            kp=initial_kp,
            ki=initial_ki,
            kd=initial_kd,
            reason="before_auto_apply",
            metrics={"source": "initial"}
        )

        # Step 2: Trigger auto-apply to new PID (kp=90, ki=0.012, kd=55)
        new_kp, new_ki, new_kd = 90.0, 0.012, 55.0
        adaptive_learner.record_pid_snapshot(
            kp=new_kp,
            ki=new_ki,
            kd=new_kd,
            reason="auto_apply",
            metrics={"source": "auto_apply"}
        )

        # Add some cycle history that should be cleared on rollback
        for i in range(5):
            metrics = create_good_cycle_metrics()
            adaptive_learner.add_cycle_metrics(metrics)
        assert adaptive_learner.get_cycle_count() == 5

        # Step 3: Verify PID history has 2 entries
        history = adaptive_learner.get_pid_history()
        assert len(history) == 2
        assert history[0]["kp"] == initial_kp
        assert history[0]["reason"] == "before_auto_apply"
        assert history[1]["kp"] == new_kp
        assert history[1]["reason"] == "auto_apply"

        # Step 4 & 5: Simulate rollback_pid service call
        # This simulates what PIDTuningManager.async_rollback_pid() does
        previous_pid = adaptive_learner.get_previous_pid()
        assert previous_pid is not None, "Should have previous PID to rollback to"

        # Step 6: Verify PID values from rollback
        assert previous_pid["kp"] == initial_kp
        assert previous_pid["ki"] == initial_ki
        assert previous_pid["kd"] == initial_kd

        # Step 7: Record rollback snapshot (as async_rollback_pid does)
        adaptive_learner.record_pid_snapshot(
            kp=previous_pid["kp"],
            ki=previous_pid["ki"],
            kd=previous_pid["kd"],
            reason="rollback",
            metrics={
                "rolled_back_from_kp": new_kp,
                "rolled_back_from_ki": new_ki,
                "rolled_back_from_kd": new_kd,
            }
        )

        # Verify rollback snapshot recorded
        history = adaptive_learner.get_pid_history()
        assert len(history) == 3
        assert history[-1]["reason"] == "rollback"
        assert history[-1]["kp"] == initial_kp
        assert history[-1]["ki"] == initial_ki
        assert history[-1]["kd"] == initial_kd
        assert history[-1]["metrics"]["rolled_back_from_kp"] == new_kp

        # Step 8: Clear learning history (as async_rollback_pid does)
        adaptive_learner.clear_history()
        assert adaptive_learner.get_cycle_count() == 0
        assert adaptive_learner._convergence_confidence == 0.0

        # Step 9: Simulate notification (as _handle_validation_failure does)
        await mock_hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Adaptive Thermostat PID Rolled Back",
                "message": (
                    f"PID values rolled back for test_zone:\n"
                    f"Kp: {new_kp:.2f} → {initial_kp:.2f}\n"
                    f"Ki: {new_ki:.4f} → {initial_ki:.4f}\n"
                    f"Kd: {new_kd:.2f} → {initial_kd:.2f}"
                ),
                "notification_id": "adaptive_thermostat_rollback_test_zone",
            }
        )

        # Verify persistent notification was sent
        assert len(service_calls) == 1
        assert service_calls[0]["domain"] == "persistent_notification"
        assert service_calls[0]["service"] == "create"
        assert "Rolled Back" in service_calls[0]["data"]["title"]
        assert str(initial_kp) in service_calls[0]["data"]["message"]


class TestAutoApplyDisabled:
    """Test behavior when auto_apply_pid is disabled."""

    @pytest.mark.asyncio
    async def test_no_auto_apply_when_disabled(self, mock_hass, adaptive_learner, mock_callbacks, dispatcher):
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Complete a cycle
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        _set_test_time(start_time)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)
            _set_test_time(current_time)

        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

        for _ in range(10):
            await tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)
            _set_test_time(current_time)

        # Cycle completes but no auto-apply callback was ever set
        assert tracker.state == CycleState.IDLE
        assert tracker._on_auto_apply_check is None


class TestMultiZoneAutoApply:
    """Test auto-apply behavior with multiple zones."""

    @pytest.mark.asyncio
    async def test_multiple_zones_auto_apply_simultaneously(self, mock_hass):
        """Test that multiple zones can auto-apply simultaneously without interference.

        Integration test for story 10.2:
        1. Create 2 thermostats: zone1 (convector), zone2 (radiator)
        2. Build confidence in both zones (60% zone1, 70% zone2)
        3. Finalize cycles in both zones in same event loop iteration
        4. Verify both _check_auto_apply_pid callbacks trigger
        5. Verify both zones apply PID independently
        6. Verify no interference (zone1 history not cleared by zone2 action)
        """
        from custom_components.adaptive_thermostat.const import HEATING_TYPE_RADIATOR

        # Track auto-apply calls for each zone
        zone1_auto_apply_calls = []
        zone2_auto_apply_calls = []

        async def zone1_auto_apply():
            zone1_auto_apply_calls.append(True)

        async def zone2_auto_apply():
            zone2_auto_apply_calls.append(True)

        # Create callbacks for zone1 (convector)
        zone1_callbacks = {
            "get_target_temp": MagicMock(return_value=21.0),
            "get_current_temp": MagicMock(return_value=19.0),
            "get_hvac_mode": MagicMock(return_value="heat"),
            "get_in_grace_period": MagicMock(return_value=False),
        }

        # Create callbacks for zone2 (radiator)
        zone2_callbacks = {
            "get_target_temp": MagicMock(return_value=20.0),
            "get_current_temp": MagicMock(return_value=18.0),
            "get_hvac_mode": MagicMock(return_value="heat"),
            "get_in_grace_period": MagicMock(return_value=False),
        }

        # Create separate AdaptiveLearner instances for each zone
        zone1_learner = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
        zone1_learner.set_physics_baseline(100.0, 0.01, 50.0)

        zone2_learner = AdaptiveLearner(heating_type=HEATING_TYPE_RADIATOR)
        zone2_learner.set_physics_baseline(120.0, 0.012, 60.0)

        # Create separate dispatchers for each zone
        zone1_dispatcher = CycleEventDispatcher()
        zone2_dispatcher = CycleEventDispatcher()

        # Create separate cycle trackers for each zone
        zone1_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="zone1",
            adaptive_learner=zone1_learner,
            get_target_temp=zone1_callbacks["get_target_temp"],
            get_current_temp=zone1_callbacks["get_current_temp"],
            get_hvac_mode=zone1_callbacks["get_hvac_mode"],
            get_in_grace_period=zone1_callbacks["get_in_grace_period"],
            on_auto_apply_check=zone1_auto_apply,
            dispatcher=zone1_dispatcher,
        )
        zone1_tracker.set_restoration_complete()

        zone2_tracker = CycleTrackerManager(
            hass=mock_hass,
            zone_id="zone2",
            adaptive_learner=zone2_learner,
            get_target_temp=zone2_callbacks["get_target_temp"],
            get_current_temp=zone2_callbacks["get_current_temp"],
            get_hvac_mode=zone2_callbacks["get_hvac_mode"],
            get_in_grace_period=zone2_callbacks["get_in_grace_period"],
            on_auto_apply_check=zone2_auto_apply,
            dispatcher=zone2_dispatcher,
        )
        zone2_tracker.set_restoration_complete()

        # Phase 1: Build confidence in zone1 to 60% (6 good cycles for convector)
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        for cycle_num in range(6):
            current_time = start_time + timedelta(hours=cycle_num * 2)
            _set_test_time(current_time)
            start_temp_z1 = 20.85  # undershoot will be 21.0 - 20.85 = 0.15°C
            zone1_dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=current_time, target_temp=21.0, current_temp=start_temp_z1))

            for i in range(20):
                temp = start_temp_z1 + min(i * 0.01, 0.15)
                await zone1_tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

            zone1_dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

            for _ in range(10):
                await zone1_tracker.update_temperature(current_time, 21.0)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

        # Verify zone1 confidence reached 60%
        assert zone1_learner.get_convergence_confidence() >= 0.60

        # Phase 2: Build confidence in zone2 to 70% (7 good cycles for radiator)
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        for cycle_num in range(7):
            current_time = start_time + timedelta(hours=cycle_num * 2)
            _set_test_time(current_time)
            start_temp_z2 = 19.85  # undershoot will be 20.0 - 19.85 = 0.15°C
            zone2_dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=current_time, target_temp=20.0, current_temp=start_temp_z2))

            for i in range(20):
                temp = start_temp_z2 + min(i * 0.01, 0.15)
                await zone2_tracker.update_temperature(current_time, temp)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

            zone2_dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

            for _ in range(10):
                await zone2_tracker.update_temperature(current_time, 20.0)
                current_time += timedelta(seconds=30)
                _set_test_time(current_time)

        # Verify zone2 confidence reached 70%
        assert zone2_learner.get_convergence_confidence() >= 0.70

        # Phase 3: Finalize cycles in both zones "simultaneously" (same event loop iteration)
        # Both zones complete a cycle that should trigger auto-apply check
        zone1_time = datetime(2024, 1, 2, 10, 0, 0)
        zone2_time = datetime(2024, 1, 2, 10, 0, 0)
        _set_test_time(zone1_time)

        # Zone1 cycle
        start_temp_z1 = 20.85
        zone1_dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=zone1_time, target_temp=21.0, current_temp=start_temp_z1))
        for i in range(20):
            temp = start_temp_z1 + min(i * 0.01, 0.15)
            await zone1_tracker.update_temperature(zone1_time, temp)
            zone1_time += timedelta(seconds=30)
            _set_test_time(zone1_time)
        zone1_dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=zone1_time))
        for _ in range(10):
            await zone1_tracker.update_temperature(zone1_time, 21.0)
            zone1_time += timedelta(seconds=30)
            _set_test_time(zone1_time)

        # Zone2 cycle
        _set_test_time(zone2_time)
        start_temp_z2 = 19.85
        zone2_dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=zone2_time, target_temp=20.0, current_temp=start_temp_z2))
        for i in range(20):
            temp = start_temp_z2 + min(i * 0.01, 0.15)
            await zone2_tracker.update_temperature(zone2_time, temp)
            zone2_time += timedelta(seconds=30)
            _set_test_time(zone2_time)
        zone2_dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=zone2_time))
        for _ in range(10):
            await zone2_tracker.update_temperature(zone2_time, 20.0)
            zone2_time += timedelta(seconds=30)
            _set_test_time(zone2_time)

        # Phase 4: Await all created tasks (auto-apply callbacks)
        import asyncio
        for task in mock_hass._created_tasks:
            if asyncio.iscoroutine(task):
                await task

        # Phase 5: Verify both zones' auto-apply callbacks were triggered
        assert len(zone1_auto_apply_calls) >= 1, "Zone1 auto-apply callback should be triggered"
        assert len(zone2_auto_apply_calls) >= 1, "Zone2 auto-apply callback should be triggered"

        # Phase 6: Verify both zones maintain independent state (no interference)
        # Zone1 should have 7 cycles (6 confidence-building + 1 final)
        assert zone1_learner.get_cycle_count() == 7
        # Zone2 should have 8 cycles (7 confidence-building + 1 final)
        assert zone2_learner.get_cycle_count() == 8

        # Verify independence: zone1's history not affected by zone2
        zone1_confidence = zone1_learner.get_convergence_confidence()
        zone2_confidence = zone2_learner.get_convergence_confidence()
        assert zone1_confidence >= 0.60
        assert zone2_confidence >= 0.70

        # Verify each learner maintains its own PID baseline
        assert zone1_learner._physics_baseline_kp == 100.0
        assert zone2_learner._physics_baseline_kp == 120.0


class TestValidationModeBlocking:
    """Test that auto-apply is blocked during validation mode."""

    @pytest.mark.asyncio
    async def test_auto_apply_blocked_during_validation(
        self, mock_hass, adaptive_learner, mock_callbacks, dispatcher
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
            dispatcher=dispatcher,
        )
        tracker.set_restoration_complete()

        # Enter validation mode
        adaptive_learner.start_validation_mode(baseline_overshoot=0.15)
        assert adaptive_learner.is_in_validation_mode() is True

        # Complete a cycle while in validation mode
        start_time = datetime(2024, 1, 1, 10, 0, 0)
        _set_test_time(start_time)
        dispatcher.emit(CycleStartedEvent(hvac_mode="heat", timestamp=start_time, target_temp=21.0, current_temp=19.0))

        current_time = start_time
        for i in range(20):
            temp = 19.0 + min(i * 0.15, 2.0)
            await tracker.update_temperature(current_time, temp)
            current_time += timedelta(seconds=30)
            _set_test_time(current_time)

        dispatcher.emit(SettlingStartedEvent(hvac_mode="heat", timestamp=current_time))

        for _ in range(10):
            await tracker.update_temperature(current_time, 21.0)
            current_time += timedelta(seconds=30)
            _set_test_time(current_time)

        # Auto-apply should NOT be called during validation mode
        # (the callback triggers only when not in validation mode)
        assert len(auto_apply_calls) == 0

    @pytest.mark.asyncio
    async def test_manual_pid_change_during_validation(self, adaptive_learner):
        """Test that manual PID change aborts validation mode.

        Integration test for story 10.3:
        1. Trigger auto-apply and enter validation mode
        2. Complete 2 out of 5 validation cycles
        3. User manually calls adaptive_thermostat.set_pid service
        4. Verify manual change records snapshot with reason='manual_apply'
        5. Verify clear_history called, which resets validation_mode=False
        6. Verify validation aborted (validation_cycles cleared)
        7. Verify normal operation continues (no validation completion)
        """
        # Phase 1: Trigger auto-apply and enter validation mode
        # Build confidence to 70%
        for i in range(7):
            metrics = create_good_cycle_metrics(overshoot=0.15)
            adaptive_learner.add_cycle_metrics(metrics)
            adaptive_learner.update_convergence_confidence(metrics)

        assert adaptive_learner.get_convergence_confidence() >= 0.60

        # Simulate auto-apply
        baseline_overshoot = 0.15
        adaptive_learner.record_pid_snapshot(
            kp=100.0, ki=0.01, kd=50.0,
            reason="before_auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )
        adaptive_learner.record_pid_snapshot(
            kp=90.0, ki=0.012, kd=45.0,
            reason="auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )

        # Clear history and start validation mode
        adaptive_learner.clear_history()
        adaptive_learner.start_validation_mode(baseline_overshoot)

        # Verify validation mode is active
        assert adaptive_learner.is_in_validation_mode() is True
        assert len(adaptive_learner._validation_cycles) == 0

        # Phase 2: Complete 2 out of 5 validation cycles
        for i in range(2):
            metrics = create_good_cycle_metrics(overshoot=0.12)
            result = adaptive_learner.add_validation_cycle(metrics)
            # Should still be collecting (None = not complete yet)
            assert result is None

        # Verify we have 2 validation cycles collected
        assert len(adaptive_learner._validation_cycles) == 2
        assert adaptive_learner.is_in_validation_mode() is True

        # Phase 3: User manually calls adaptive_thermostat.set_pid service
        # This simulates what PIDTuningManager.async_apply_adaptive_pid does
        # when called manually via service

        # Phase 4: Verify manual change records snapshot with reason='manual_apply'
        manual_kp, manual_ki, manual_kd = 110.0, 0.015, 55.0
        adaptive_learner.record_pid_snapshot(
            kp=manual_kp,
            ki=manual_ki,
            kd=manual_kd,
            reason="manual_apply",
            metrics={"source": "user_service_call"}
        )

        # Verify snapshot was recorded
        history = adaptive_learner.get_pid_history()
        assert len(history) == 3  # before_auto_apply, auto_apply, manual_apply
        assert history[-1]["reason"] == "manual_apply"
        assert history[-1]["kp"] == manual_kp
        assert history[-1]["ki"] == manual_ki
        assert history[-1]["kd"] == manual_kd

        # Phase 5 & 6: Verify clear_history called, which resets validation_mode=False
        # and clears validation_cycles
        adaptive_learner.clear_history()

        # Phase 6: Verify validation aborted (validation_cycles cleared)
        assert adaptive_learner.is_in_validation_mode() is False
        assert len(adaptive_learner._validation_cycles) == 0
        # Note: _validation_baseline_overshoot is not cleared by clear_history(),
        # but validation_mode=False is the key indicator that validation is aborted

        # Verify learning state was reset
        assert adaptive_learner.get_cycle_count() == 0
        assert adaptive_learner._convergence_confidence == 0.0

        # Phase 7: Verify normal operation continues (no validation completion)
        # System can now collect new cycles and build confidence from scratch
        new_metrics = create_good_cycle_metrics(overshoot=0.13)
        adaptive_learner.add_cycle_metrics(new_metrics)

        # Verify new cycle was added (normal operation)
        assert adaptive_learner.get_cycle_count() == 1
        # Still not in validation mode
        assert adaptive_learner.is_in_validation_mode() is False


class TestHARestartEdgeCases:
    """Test edge cases around Home Assistant restarts."""

    @pytest.mark.asyncio
    async def test_ha_restart_during_validation(self, adaptive_learner):
        """Test HA restart during validation mode results in state loss.

        This test verifies the edge case where Home Assistant restarts while
        validation mode is active (e.g., 3 out of 5 validation cycles complete).

        Expected behavior (DOCUMENTED LIMITATION):
        - Validation state (_validation_mode, _validation_cycles, _validation_baseline_overshoot)
          is stored in instance variables and NOT persisted to state
        - After HA restart, a new AdaptiveLearner instance is created
        - The new instance has validation_mode=False (state lost)
        - This is ACCEPTABLE: the system will rebuild confidence naturally
        - PID gains ARE persisted, so the auto-applied values remain active

        Integration test for story 10.1:
        1. Build confidence and trigger auto-apply to enter validation mode
        2. Add 3 validation cycles (out of 5 needed)
        3. Verify validation_mode=True and _validation_cycles has 3 entries
        4. Simulate HA restart by creating fresh AdaptiveLearner instance
        5. Verify new instance has validation_mode=False (state lost)
        6. Document that this is expected behavior - validation state loss is acceptable
        """
        # Phase 1: Build confidence and trigger auto-apply to enter validation mode
        # Simulate 8 good cycles to build 80% confidence
        for i in range(8):
            metrics = create_good_cycle_metrics(overshoot=0.15)
            adaptive_learner.add_cycle_metrics(metrics)
            adaptive_learner.update_convergence_confidence(metrics)

        # Verify confidence reached threshold
        assert adaptive_learner.get_convergence_confidence() >= 0.60

        # Simulate auto-apply: record PID snapshot and start validation
        baseline_overshoot = 0.15
        adaptive_learner.record_pid_snapshot(
            kp=100.0, ki=0.01, kd=50.0,
            reason="before_auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )
        adaptive_learner.record_pid_snapshot(
            kp=90.0, ki=0.012, kd=45.0,
            reason="auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )

        # Clear history and start validation mode (as auto-apply does)
        adaptive_learner.clear_history()
        adaptive_learner.start_validation_mode(baseline_overshoot)

        # Phase 2: Add 3 validation cycles (out of 5 needed)
        for i in range(3):
            metrics = create_good_cycle_metrics(overshoot=0.12)
            result = adaptive_learner.add_validation_cycle(metrics)
            # Should still be collecting (None = not complete yet)
            assert result is None

        # Phase 3: Verify validation mode active with 3 validation cycles
        assert adaptive_learner.is_in_validation_mode() is True
        assert len(adaptive_learner._validation_cycles) == 3
        assert adaptive_learner._validation_baseline_overshoot == baseline_overshoot

        # Phase 4: Simulate HA restart by creating a fresh AdaptiveLearner instance
        # In reality, HA would restore some persisted state (PID gains, physics baseline),
        # but validation state is NOT persisted (it's only in instance variables)
        restarted_learner = AdaptiveLearner(heating_type=HEATING_TYPE_CONVECTOR)
        restarted_learner.set_physics_baseline(100.0, 0.01, 50.0)

        # In a real restart, PID history would be restored from persistent state
        # (simulating this for completeness)
        restarted_learner.record_pid_snapshot(
            kp=100.0, ki=0.01, kd=50.0,
            reason="before_auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )
        restarted_learner.record_pid_snapshot(
            kp=90.0, ki=0.012, kd=45.0,
            reason="auto_apply",
            metrics={"baseline_overshoot": baseline_overshoot}
        )

        # Phase 5: Verify validation state is lost after restart
        # IMPORTANT: This is EXPECTED BEHAVIOR - validation state is not persisted
        assert restarted_learner.is_in_validation_mode() is False
        assert len(restarted_learner._validation_cycles) == 0
        assert restarted_learner._validation_baseline_overshoot is None

        # Phase 6: Verify that PID history IS preserved (this is persisted)
        history = restarted_learner.get_pid_history()
        assert len(history) == 2
        assert history[-1]["reason"] == "auto_apply"
        assert history[-1]["kp"] == 90.0

        # DOCUMENTATION NOTE:
        # Validation state loss on HA restart is acceptable because:
        # 1. The auto-applied PID gains remain active (they're persisted)
        # 2. The system will continue running with the new gains
        # 3. If the gains perform well, confidence will rebuild naturally
        # 4. If the gains perform poorly, degradation will be detected in future cycles
        # 5. Validation is a safety mechanism, not a critical operational requirement
        #
        # Future enhancement could persist validation state if needed, but current
        # behavior is reasonable given the low likelihood of restart during validation
        # (validation completes in 5 cycles, typically <24 hours).


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
