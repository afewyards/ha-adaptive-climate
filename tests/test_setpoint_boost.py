"""Tests for SetpointBoostManager class."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
from homeassistant.core import HomeAssistant

from custom_components.adaptive_thermostat.managers.setpoint_boost import (
    SetpointBoostManager,
)
from custom_components.adaptive_thermostat.pid_controller import PID
from custom_components.adaptive_thermostat.const import HeatingType


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = Mock(spec=HomeAssistant)
    return hass


@pytest.fixture
def mock_pid():
    """Create a mock PID controller."""
    pid = Mock(spec=PID)
    pid.integral = 50.0
    return pid


@pytest.fixture
def is_night_period_callback():
    """Create a callback that returns False (not in night period)."""
    return Mock(return_value=False)


class TestSetpointBoostManagerInitialization:
    """Tests for SetpointBoostManager initialization."""

    def test_initialization_defaults(self, mock_hass, mock_pid, is_night_period_callback):
        """Test manager initializes with default parameters."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        assert manager._enabled is True
        assert manager._debounce_seconds == 5
        assert manager._pending_delta == 0.0
        assert manager._debounce_timer is None
        assert manager._heating_type == HeatingType.RADIATOR

    def test_initialization_disabled(self, mock_hass, mock_pid, is_night_period_callback):
        """Test manager can be initialized as disabled."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
            enabled=False,
        )

        assert manager._enabled is False

    def test_initialization_custom_debounce(self, mock_hass, mock_pid, is_night_period_callback):
        """Test manager initializes with custom debounce seconds."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
            debounce_seconds=10,
        )

        assert manager._debounce_seconds == 10

    def test_initialization_custom_boost_factor(self, mock_hass, mock_pid, is_night_period_callback):
        """Test manager initializes with custom boost factor override."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
            boost_factor=50.0,
        )

        # Access private attribute to verify override
        assert manager._boost_factor == 50.0


class TestSetpointBoostManagerDebounce:
    """Tests for debounce behavior."""

    @patch('custom_components.adaptive_thermostat.managers.setpoint_boost.async_call_later')
    def test_single_setpoint_change_schedules_timer(
        self, mock_call_later, mock_hass, mock_pid, is_night_period_callback
    ):
        """Test single setpoint change schedules debounce timer."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
            debounce_seconds=5,
        )

        manager.on_setpoint_change(old_temp=20.0, new_temp=22.0)

        # Timer should be scheduled with 5s delay
        mock_call_later.assert_called_once()
        args = mock_call_later.call_args
        assert args[0][1] == 5  # debounce_seconds (second positional arg)

        # Pending delta should accumulate
        assert manager._pending_delta == 2.0

    @patch('custom_components.adaptive_thermostat.managers.setpoint_boost.async_call_later')
    def test_multiple_rapid_changes_accumulate_delta(
        self, mock_call_later, mock_hass, mock_pid, is_night_period_callback
    ):
        """Test multiple rapid setpoint changes accumulate delta and reset timer."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
            debounce_seconds=5,
        )

        # First change: 20 -> 21 (+1.0)
        manager.on_setpoint_change(old_temp=20.0, new_temp=21.0)
        assert manager._pending_delta == 1.0

        # Second change: 21 -> 22.5 (+1.5)
        manager.on_setpoint_change(old_temp=21.0, new_temp=22.5)
        assert manager._pending_delta == 2.5  # 1.0 + 1.5

        # Third change: 22.5 -> 23 (+0.5)
        manager.on_setpoint_change(old_temp=22.5, new_temp=23.0)
        assert manager._pending_delta == 3.0  # 2.5 + 0.5

        # Timer should be called 3 times (once per change)
        assert mock_call_later.call_count == 3

    @patch('custom_components.adaptive_thermostat.managers.setpoint_boost.async_call_later')
    def test_timer_cancellation_on_new_change(
        self, mock_call_later, mock_hass, mock_pid, is_night_period_callback
    ):
        """Test pending timer is cancelled when new setpoint change occurs."""
        mock_cancel = Mock()
        mock_call_later.return_value = mock_cancel

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # First change schedules timer
        manager.on_setpoint_change(old_temp=20.0, new_temp=21.0)
        first_timer = manager._debounce_timer
        assert first_timer is not None

        # Second change should cancel first timer
        manager.on_setpoint_change(old_temp=21.0, new_temp=22.0)

        # First timer should be cancelled
        first_timer.assert_called_once()

    @patch('custom_components.adaptive_thermostat.managers.setpoint_boost.async_call_later')
    def test_setpoint_decrease_accumulates_negative_delta(
        self, mock_call_later, mock_hass, mock_pid, is_night_period_callback
    ):
        """Test setpoint decrease accumulates negative delta."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Decrease: 22 -> 20 (-2.0)
        manager.on_setpoint_change(old_temp=22.0, new_temp=20.0)
        assert manager._pending_delta == -2.0

    @patch('custom_components.adaptive_thermostat.managers.setpoint_boost.async_call_later')
    def test_mixed_up_down_changes_accumulate_net_delta(
        self, mock_call_later, mock_hass, mock_pid, is_night_period_callback
    ):
        """Test mixed increase/decrease changes accumulate net delta."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # +2.0
        manager.on_setpoint_change(old_temp=20.0, new_temp=22.0)
        assert manager._pending_delta == 2.0

        # -1.0 (net +1.0)
        manager.on_setpoint_change(old_temp=22.0, new_temp=21.0)
        assert manager._pending_delta == 1.0

        # +0.5 (net +1.5)
        manager.on_setpoint_change(old_temp=21.0, new_temp=21.5)
        assert manager._pending_delta == 1.5


class TestSetpointBoostCalculation:
    """Tests for boost calculation (setpoint INCREASE)."""

    @pytest.mark.asyncio
    async def test_boost_calculation_floor_hydronic(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost calculation for floor_hydronic heating type."""
        mock_pid.integral = 40.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FLOOR_HYDRONIC,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 2.0°C
        # Boost = 2.0 * 25.0 = 50.0
        # Cap = max(abs(40.0) * 0.5, 15.0) = max(20.0, 15.0) = 20.0
        # Final boost = min(50.0, 20.0) = 20.0
        manager._pending_delta = 2.0
        await manager._apply_boost(datetime.now())

        # Integral should be 40.0 + 20.0 = 60.0
        assert mock_pid.integral == 60.0

    @pytest.mark.asyncio
    async def test_boost_calculation_radiator(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost calculation for radiator heating type."""
        mock_pid.integral = 30.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 1.5°C
        # Boost = 1.5 * 18.0 = 27.0
        # Cap = max(abs(30.0) * 0.5, 15.0) = max(15.0, 15.0) = 15.0
        # Final boost = min(27.0, 15.0) = 15.0
        manager._pending_delta = 1.5
        await manager._apply_boost(datetime.now())

        # Integral should be 30.0 + 15.0 = 45.0
        assert mock_pid.integral == 45.0

    @pytest.mark.asyncio
    async def test_boost_calculation_convector(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost calculation for convector heating type."""
        mock_pid.integral = 50.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.CONVECTOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 2.0°C
        # Boost = 2.0 * 12.0 = 24.0
        # Cap = max(abs(50.0) * 0.5, 15.0) = max(25.0, 15.0) = 25.0
        # Final boost = min(24.0, 25.0) = 24.0
        manager._pending_delta = 2.0
        await manager._apply_boost(datetime.now())

        # Integral should be 50.0 + 24.0 = 74.0
        assert mock_pid.integral == 74.0

    @pytest.mark.asyncio
    async def test_boost_calculation_forced_air(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost calculation for forced_air heating type."""
        mock_pid.integral = 20.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FORCED_AIR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 3.0°C
        # Boost = 3.0 * 8.0 = 24.0
        # Cap = max(abs(20.0) * 0.5, 15.0) = max(10.0, 15.0) = 15.0
        # Final boost = min(24.0, 15.0) = 15.0
        manager._pending_delta = 3.0
        await manager._apply_boost(datetime.now())

        # Integral should be 20.0 + 15.0 = 35.0
        assert mock_pid.integral == 35.0

    @pytest.mark.asyncio
    async def test_boost_cap_minimum_15(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost cap has minimum of 15.0 even with low integral."""
        mock_pid.integral = 10.0  # Small integral

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 0.5°C
        # Boost = 0.5 * 18.0 = 9.0
        # Cap = max(abs(10.0) * 0.5, 15.0) = max(5.0, 15.0) = 15.0
        # Final boost = min(9.0, 15.0) = 9.0
        manager._pending_delta = 0.5
        await manager._apply_boost(datetime.now())

        # Integral should be 10.0 + 9.0 = 19.0
        assert mock_pid.integral == 19.0

    @pytest.mark.asyncio
    async def test_boost_with_negative_integral(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost calculation with negative integral (cooling mode)."""
        mock_pid.integral = -40.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 2.0°C (increase)
        # Boost = 2.0 * 18.0 = 36.0
        # Cap = max(abs(-40.0) * 0.5, 15.0) = max(20.0, 15.0) = 20.0
        # Final boost = min(36.0, 20.0) = 20.0
        manager._pending_delta = 2.0
        await manager._apply_boost(datetime.now())

        # Integral should be -40.0 + 20.0 = -20.0
        assert mock_pid.integral == -20.0

    @pytest.mark.asyncio
    async def test_boost_with_custom_boost_factor(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost calculation with custom boost factor override."""
        mock_pid.integral = 50.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
            boost_factor=30.0,  # Override default 18.0
        )

        # Delta = 1.0°C
        # Boost = 1.0 * 30.0 = 30.0
        # Cap = max(abs(50.0) * 0.5, 15.0) = max(25.0, 15.0) = 25.0
        # Final boost = min(30.0, 25.0) = 25.0
        manager._pending_delta = 1.0
        await manager._apply_boost(datetime.now())

        # Integral should be 50.0 + 25.0 = 75.0
        assert mock_pid.integral == 75.0


class TestSetpointDecayCalculation:
    """Tests for decay calculation (setpoint DECREASE)."""

    @pytest.mark.asyncio
    async def test_decay_calculation_floor_hydronic(self, mock_hass, mock_pid, is_night_period_callback):
        """Test decay calculation for floor_hydronic heating type."""
        mock_pid.integral = 60.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FLOOR_HYDRONIC,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = -2.0°C (decrease)
        # Decay = max(0.3, 1.0 - abs(-2.0) * 0.15) = max(0.3, 1.0 - 0.3) = max(0.3, 0.7) = 0.7
        # Integral = 60.0 * 0.7 = 42.0
        manager._pending_delta = -2.0
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == 42.0

    @pytest.mark.asyncio
    async def test_decay_calculation_radiator(self, mock_hass, mock_pid, is_night_period_callback):
        """Test decay calculation for radiator heating type."""
        mock_pid.integral = 50.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = -1.5°C
        # Decay = max(0.3, 1.0 - abs(-1.5) * 0.20) = max(0.3, 1.0 - 0.3) = max(0.3, 0.7) = 0.7
        # Integral = 50.0 * 0.7 = 35.0
        manager._pending_delta = -1.5
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == 35.0

    @pytest.mark.asyncio
    async def test_decay_calculation_convector(self, mock_hass, mock_pid, is_night_period_callback):
        """Test decay calculation for convector heating type."""
        mock_pid.integral = 40.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.CONVECTOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = -2.0°C
        # Decay = max(0.3, 1.0 - abs(-2.0) * 0.25) = max(0.3, 1.0 - 0.5) = max(0.3, 0.5) = 0.5
        # Integral = 40.0 * 0.5 = 20.0
        manager._pending_delta = -2.0
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == 20.0

    @pytest.mark.asyncio
    async def test_decay_calculation_forced_air(self, mock_hass, mock_pid, is_night_period_callback):
        """Test decay calculation for forced_air heating type."""
        mock_pid.integral = 50.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FORCED_AIR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = -2.0°C
        # Decay = max(0.3, 1.0 - abs(-2.0) * 0.30) = max(0.3, 1.0 - 0.6) = max(0.3, 0.4) = 0.4
        # Integral = 50.0 * 0.4 = 20.0
        manager._pending_delta = -2.0
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == 20.0

    @pytest.mark.asyncio
    async def test_decay_floor_at_0_3(self, mock_hass, mock_pid, is_night_period_callback):
        """Test decay has minimum floor of 0.3 for large decreases."""
        mock_pid.integral = 60.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FORCED_AIR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = -5.0°C (large decrease)
        # Decay = max(0.3, 1.0 - abs(-5.0) * 0.30) = max(0.3, 1.0 - 1.5) = max(0.3, -0.5) = 0.3
        # Integral = 60.0 * 0.3 = 18.0
        manager._pending_delta = -5.0
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == 18.0

    @pytest.mark.asyncio
    async def test_decay_with_negative_integral(self, mock_hass, mock_pid, is_night_period_callback):
        """Test decay calculation with negative integral (cooling mode)."""
        mock_pid.integral = -40.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = -1.0°C (decrease)
        # Decay = max(0.3, 1.0 - abs(-1.0) * 0.20) = max(0.3, 1.0 - 0.2) = max(0.3, 0.8) = 0.8
        # Integral = -40.0 * 0.8 = -32.0
        manager._pending_delta = -1.0
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == -32.0


class TestSetpointBoostSkipConditions:
    """Tests for skip conditions."""

    @pytest.mark.asyncio
    async def test_skip_small_delta_positive(self, mock_hass, mock_pid, is_night_period_callback):
        """Test skip when delta < 0.3°C (positive)."""
        mock_pid.integral = 50.0
        original_integral = mock_pid.integral

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = 0.2
        await manager._apply_boost(datetime.now())

        # Integral should be unchanged
        assert mock_pid.integral == original_integral

    @pytest.mark.asyncio
    async def test_skip_small_delta_negative(self, mock_hass, mock_pid, is_night_period_callback):
        """Test skip when abs(delta) < 0.3°C (negative)."""
        mock_pid.integral = 50.0
        original_integral = mock_pid.integral

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = -0.25
        await manager._apply_boost(datetime.now())

        # Integral should be unchanged
        assert mock_pid.integral == original_integral

    @pytest.mark.asyncio
    async def test_skip_night_setback_active(self, mock_hass, mock_pid):
        """Test skip when night setback is active."""
        mock_pid.integral = 50.0
        original_integral = mock_pid.integral

        # Night period callback returns True
        is_night_period_cb = Mock(return_value=True)

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_cb,
        )

        manager._pending_delta = 2.0  # Large enough delta
        await manager._apply_boost(datetime.now())

        # Integral should be unchanged
        assert mock_pid.integral == original_integral
        # Callback should have been called
        is_night_period_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_skip_when_not_night_period(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost applies when not in night period."""
        mock_pid.integral = 50.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = 2.0
        await manager._apply_boost(datetime.now())

        # Integral should change
        assert mock_pid.integral != 50.0
        # Callback should have been called
        is_night_period_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_exactly_0_3_delta_applies_boost(self, mock_hass, mock_pid, is_night_period_callback):
        """Test that exactly 0.3°C delta applies boost (not skipped)."""
        mock_pid.integral = 50.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = 0.3
        await manager._apply_boost(datetime.now())

        # Integral should change (0.3 * 18.0 = 5.4, cap = 25.0, boost = 5.4)
        assert mock_pid.integral == pytest.approx(55.4)

    @pytest.mark.asyncio
    async def test_skip_when_disabled(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost is skipped when manager is disabled."""
        mock_pid.integral = 50.0
        original_integral = mock_pid.integral

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
            enabled=False,
        )

        manager._pending_delta = 2.0
        await manager._apply_boost(datetime.now())

        # Integral should be unchanged
        assert mock_pid.integral == original_integral


class TestSetpointBoostPendingDeltaReset:
    """Tests for pending delta reset after boost."""

    @pytest.mark.asyncio
    async def test_pending_delta_reset_after_boost(self, mock_hass, mock_pid, is_night_period_callback):
        """Test pending delta is reset to 0 after boost applied."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = 2.0
        await manager._apply_boost(datetime.now())

        # Pending delta should be reset
        assert manager._pending_delta == 0.0

    @pytest.mark.asyncio
    async def test_pending_delta_reset_after_skip(self, mock_hass, mock_pid, is_night_period_callback):
        """Test pending delta is reset even when boost is skipped."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = 0.2  # Too small, will be skipped
        await manager._apply_boost(datetime.now())

        # Pending delta should still be reset
        assert manager._pending_delta == 0.0


class TestSetpointBoostTimerCancellation:
    """Tests for timer cancellation."""

    @patch('custom_components.adaptive_thermostat.managers.setpoint_boost.async_call_later')
    def test_cancel_pending_timer(self, mock_call_later, mock_hass, mock_pid, is_night_period_callback):
        """Test cancel() cancels pending timer."""
        mock_cancel = Mock()
        mock_call_later.return_value = mock_cancel

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Schedule a timer
        manager.on_setpoint_change(old_temp=20.0, new_temp=22.0)
        assert manager._debounce_timer is not None

        # Cancel
        manager.cancel()

        # Timer should be cancelled
        mock_cancel.assert_called_once()
        assert manager._debounce_timer is None

    def test_cancel_with_no_pending_timer(self, mock_hass, mock_pid, is_night_period_callback):
        """Test cancel() is safe when no timer is pending."""
        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Should not raise exception
        manager.cancel()
        assert manager._debounce_timer is None


class TestSetpointBoostHeatingTypeFactors:
    """Tests to verify heating type factors are correct."""

    @pytest.mark.asyncio
    async def test_floor_hydronic_factors(self, mock_hass, mock_pid, is_night_period_callback):
        """Test floor_hydronic uses boost_factor=25.0, decay_rate=0.15."""
        mock_pid.integral = 100.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FLOOR_HYDRONIC,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Test boost: delta=1.0, boost=1.0*25.0=25.0, cap=50.0, final=25.0
        manager._pending_delta = 1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == 125.0

        # Reset and test decay: delta=-1.0, decay=max(0.3, 1.0-1.0*0.15)=0.85
        mock_pid.integral = 100.0
        manager._pending_delta = -1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == pytest.approx(85.0)

    @pytest.mark.asyncio
    async def test_radiator_factors(self, mock_hass, mock_pid, is_night_period_callback):
        """Test radiator uses boost_factor=18.0, decay_rate=0.20."""
        mock_pid.integral = 100.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Test boost: delta=1.0, boost=1.0*18.0=18.0, cap=50.0, final=18.0
        manager._pending_delta = 1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == 118.0

        # Reset and test decay: delta=-1.0, decay=max(0.3, 1.0-1.0*0.20)=0.80
        mock_pid.integral = 100.0
        manager._pending_delta = -1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == pytest.approx(80.0)

    @pytest.mark.asyncio
    async def test_convector_factors(self, mock_hass, mock_pid, is_night_period_callback):
        """Test convector uses boost_factor=12.0, decay_rate=0.25."""
        mock_pid.integral = 100.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.CONVECTOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Test boost: delta=1.0, boost=1.0*12.0=12.0, cap=50.0, final=12.0
        manager._pending_delta = 1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == 112.0

        # Reset and test decay: delta=-1.0, decay=max(0.3, 1.0-1.0*0.25)=0.75
        mock_pid.integral = 100.0
        manager._pending_delta = -1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == pytest.approx(75.0)

    @pytest.mark.asyncio
    async def test_forced_air_factors(self, mock_hass, mock_pid, is_night_period_callback):
        """Test forced_air uses boost_factor=8.0, decay_rate=0.30."""
        mock_pid.integral = 100.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FORCED_AIR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Test boost: delta=1.0, boost=1.0*8.0=8.0, cap=50.0, final=8.0
        manager._pending_delta = 1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == 108.0

        # Reset and test decay: delta=-1.0, decay=max(0.3, 1.0-1.0*0.30)=0.70
        mock_pid.integral = 100.0
        manager._pending_delta = -1.0
        await manager._apply_boost(datetime.now())
        assert mock_pid.integral == pytest.approx(70.0)


class TestSetpointBoostEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_zero_integral_boost_applied(self, mock_hass, mock_pid, is_night_period_callback):
        """Test boost applies correctly when integral is zero."""
        mock_pid.integral = 0.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 2.0°C
        # Boost = 2.0 * 18.0 = 36.0
        # Cap = max(abs(0.0) * 0.5, 15.0) = max(0.0, 15.0) = 15.0
        # Final boost = min(36.0, 15.0) = 15.0
        manager._pending_delta = 2.0
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == 15.0

    @pytest.mark.asyncio
    async def test_zero_integral_decay_applied(self, mock_hass, mock_pid, is_night_period_callback):
        """Test decay applies correctly when integral is zero."""
        mock_pid.integral = 0.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = -2.0
        await manager._apply_boost(datetime.now())

        # 0.0 * decay_factor = 0.0
        assert mock_pid.integral == 0.0

    @pytest.mark.asyncio
    async def test_very_large_boost_capped(self, mock_hass, mock_pid, is_night_period_callback):
        """Test very large boost is capped correctly."""
        mock_pid.integral = 200.0

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.FLOOR_HYDRONIC,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Delta = 10.0°C (very large)
        # Boost = 10.0 * 25.0 = 250.0
        # Cap = max(abs(200.0) * 0.5, 15.0) = max(100.0, 15.0) = 100.0
        # Final boost = min(250.0, 100.0) = 100.0
        manager._pending_delta = 10.0
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == 300.0  # 200.0 + 100.0

    @pytest.mark.asyncio
    async def test_very_small_boost_below_threshold_skipped(self, mock_hass, mock_pid, is_night_period_callback):
        """Test very small boost below threshold is skipped."""
        mock_pid.integral = 50.0
        original_integral = mock_pid.integral

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        manager._pending_delta = 0.1  # Below 0.3 threshold
        await manager._apply_boost(datetime.now())

        assert mock_pid.integral == original_integral

    @pytest.mark.asyncio
    async def test_integral_precision_maintained(self, mock_hass, mock_pid, is_night_period_callback):
        """Test integral precision is maintained through boost/decay."""
        mock_pid.integral = 47.3456

        manager = SetpointBoostManager(
            hass=mock_hass,
            heating_type=HeatingType.RADIATOR,
            pid_controller=mock_pid,
            is_night_period_cb=is_night_period_callback,
        )

        # Boost
        manager._pending_delta = 1.5
        await manager._apply_boost(datetime.now())

        # Should maintain floating point precision
        # 1.5 * 18.0 = 27.0, cap = max(23.6728, 15.0) = 23.6728, boost = min(27.0, 23.6728) = 23.6728
        # integral = 47.3456 + 23.6728 = 71.0184
        # But cap is calculated as abs(integral) * 0.5 = abs(47.3456) * 0.5 = 23.6728
        expected = 47.3456 + min(27.0, max(abs(47.3456) * 0.5, 15.0))
        assert mock_pid.integral == pytest.approx(expected)
