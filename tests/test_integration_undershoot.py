"""Integration tests for undershoot detection in climate control loop.

This module tests the integration of UndershootDetector with the climate control
loop, verifying that Ki adjustments are properly triggered and applied when
temperature remains below setpoint for extended periods.
"""

from __future__ import annotations

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from custom_components.adaptive_thermostat.const import HeatingType
from custom_components.adaptive_thermostat.adaptive.learning import AdaptiveLearner
from custom_components.adaptive_thermostat.pid_controller import PID
from homeassistant.components.climate import HVACMode


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {
        "adaptive_thermostat": {
            "coordinator": MagicMock(),
            "debug": True,  # Enable debug mode for full attribute visibility
        }
    }
    return hass


@pytest.fixture
def adaptive_learner():
    """Create an AdaptiveLearner instance."""
    return AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)


@pytest.fixture
def pid_controller():
    """Create a PID controller instance."""
    return PID(
        kp=100.0,
        ki=10.0,
        kd=5.0,
        ke=0.0,
        out_min=0.0,
        out_max=100.0,
        sampling_period=60,
        cold_tolerance=0.5,
        hot_tolerance=0.5,
        heating_type=HeatingType.FLOOR_HYDRONIC,
    )


@pytest.fixture
def mock_thermostat(mock_hass, adaptive_learner, pid_controller):
    """Create a mock thermostat with undershoot detection enabled."""
    thermostat = MagicMock()
    thermostat.hass = mock_hass
    thermostat.entity_id = "climate.bedroom"
    thermostat._zone_id = "bedroom"
    thermostat._hvac_mode = HVACMode.HEAT
    thermostat._current_temp = 18.0
    thermostat._target_temp = 20.0
    thermostat._cold_tolerance = 0.5
    thermostat._last_control_time = time.monotonic()
    thermostat._pid_controller = pid_controller
    thermostat._ki = pid_controller.ki
    thermostat.async_write_ha_state = MagicMock()

    # Set up coordinator with zone data
    zone_data = {"adaptive_learner": adaptive_learner}
    mock_hass.data["adaptive_thermostat"]["coordinator"].get_zone_data.return_value = zone_data

    return thermostat


class TestUndershootDetectionIntegration:
    """Test undershoot detector integration with climate control loop."""

    def test_updates_detector_on_each_control_loop(self, mock_thermostat, adaptive_learner):
        """Test that detector is updated on each control loop iteration."""
        from custom_components.adaptive_thermostat.climate_control import ClimateControlMixin

        # Simulate control loop calling update_undershoot_detector
        current_time = time.monotonic()
        dt_seconds = current_time - mock_thermostat._last_control_time

        # Initial state
        detector = adaptive_learner.undershoot_detector
        assert detector.time_below_target == 0.0
        assert detector.thermal_debt == 0.0

        # Update detector (simulating what happens in _async_control_heating)
        adaptive_learner.update_undershoot_detector(
            temp=mock_thermostat._current_temp,
            setpoint=mock_thermostat._target_temp,
            dt_seconds=dt_seconds,
            cold_tolerance=mock_thermostat._cold_tolerance,
        )

        # Verify detector was updated
        assert detector.time_below_target > 0.0
        assert detector.thermal_debt > 0.0

    def test_ki_adjustment_scales_integral(self, mock_thermostat, adaptive_learner, pid_controller):
        """Test that Ki adjustment properly scales integral to prevent output spike."""
        # Set up conditions for Ki adjustment
        detector = adaptive_learner.undershoot_detector

        # Accumulate significant debt and time (4+ hours, 2+ °C·h)
        # Floor hydronic threshold: 4.0 hours, 2.0 °C·h
        # Use 4 hours with 0.6°C error to trigger time threshold
        detector.time_below_target = 4.0 * 3600.0  # 4 hours in seconds
        detector.thermal_debt = 2.4  # 0.6 * 4 hours

        # Set integral to non-zero value
        pid_controller.integral = 50.0
        old_ki = pid_controller.ki
        old_integral = pid_controller.integral

        # Check for adjustment (no cycles completed yet)
        new_ki = adaptive_learner.check_undershoot_adjustment(cycles_completed=0, current_ki=old_ki)

        # Verify adjustment was recommended
        assert new_ki is not None
        assert new_ki > old_ki

        # Simulate what climate_control.py does
        scale_factor = old_ki / new_ki
        pid_controller.scale_integral(scale_factor)
        pid_controller.ki = new_ki

        # Verify integral was scaled to maintain same output contribution
        expected_integral = old_integral * scale_factor
        assert abs(pid_controller.integral - expected_integral) < 0.01

        # Verify Ki was updated
        assert pid_controller.ki == new_ki

    def test_no_adjustment_when_cycles_completed(self, mock_thermostat, adaptive_learner):
        """Test that detector does not trigger adjustments after first cycle completes."""
        # Set up conditions for Ki adjustment
        detector = adaptive_learner.undershoot_detector
        detector.time_below_target = 4.0 * 3600.0  # 4 hours
        detector.thermal_debt = 2.4  # Above threshold

        # Check with no completed cycles - should adjust
        new_ki = adaptive_learner.check_undershoot_adjustment(cycles_completed=0, current_ki=10.0)
        assert new_ki is not None

        # Apply the adjustment
        adaptive_learner.check_undershoot_adjustment(cycles_completed=0, current_ki=10.0)

        # Accumulate more debt
        detector.time_below_target = 8.0 * 3600.0  # 8 hours
        detector.thermal_debt = 4.8  # Well above threshold

        # Check with completed cycles - should not adjust (normal learning handles it)
        new_ki = adaptive_learner.check_undershoot_adjustment(cycles_completed=1, current_ki=10.0)
        assert new_ki is None

    def test_adjustment_respects_cumulative_cap(self, mock_thermostat, adaptive_learner):
        """Test that cumulative Ki multiplier respects safety cap."""
        detector = adaptive_learner.undershoot_detector

        # Set cumulative multiplier near cap (2.0)
        detector.cumulative_ki_multiplier = 1.9

        # Accumulate debt
        detector.time_below_target = 4.0 * 3600.0
        detector.thermal_debt = 2.4

        # Check adjustment
        current_ki = 10.0
        new_ki = adaptive_learner.check_undershoot_adjustment(cycles_completed=0, current_ki=current_ki)

        if new_ki is not None:
            # If adjustment was allowed, verify it didn't exceed cap
            multiplier = new_ki / current_ki
            # Cumulative multiplier after this adjustment
            new_cumulative = detector.cumulative_ki_multiplier
            assert new_cumulative <= 2.0  # MAX_UNDERSHOOT_KI_MULTIPLIER

    def test_resets_on_temperature_recovery(self, mock_thermostat, adaptive_learner):
        """Test that detector resets when temperature rises above setpoint."""
        detector = adaptive_learner.undershoot_detector

        # Accumulate debt
        adaptive_learner.update_undershoot_detector(
            temp=18.0,
            setpoint=20.0,
            dt_seconds=3600.0,  # 1 hour
            cold_tolerance=0.5,
        )

        assert detector.time_below_target > 0.0
        assert detector.thermal_debt > 0.0

        # Temperature rises above setpoint
        adaptive_learner.update_undershoot_detector(
            temp=20.5,
            setpoint=20.0,
            dt_seconds=60.0,
            cold_tolerance=0.5,
        )

        # Verify reset
        assert detector.time_below_target == 0.0
        assert detector.thermal_debt == 0.0

    def test_holds_state_within_tolerance_band(self, mock_thermostat, adaptive_learner):
        """Test that detector holds state when within tolerance band."""
        detector = adaptive_learner.undershoot_detector

        # Accumulate some debt
        adaptive_learner.update_undershoot_detector(
            temp=18.0,
            setpoint=20.0,
            dt_seconds=1800.0,  # 30 minutes
            cold_tolerance=0.5,
        )

        time_after_first = detector.time_below_target
        debt_after_first = detector.thermal_debt

        # Move into tolerance band (within cold_tolerance)
        adaptive_learner.update_undershoot_detector(
            temp=19.7,  # 20.0 - 0.3, within tolerance (error = 0.3 < 0.5)
            setpoint=20.0,
            dt_seconds=60.0,
            cold_tolerance=0.5,
        )

        # Verify state was held (not accumulated, not reset)
        assert detector.time_below_target == time_after_first
        assert detector.thermal_debt == debt_after_first


class TestUndershootDetectionDifferentHeatingTypes:
    """Test undershoot detection behavior across different heating types."""

    @pytest.mark.parametrize("heating_type,time_threshold", [
        (HeatingType.FLOOR_HYDRONIC, 4.0),
        (HeatingType.RADIATOR, 2.0),
        (HeatingType.CONVECTOR, 1.5),
        (HeatingType.FORCED_AIR, 0.75),
    ])
    def test_heating_type_specific_thresholds(self, heating_type, time_threshold):
        """Test that different heating types use correct thresholds."""
        learner = AdaptiveLearner(heating_type=heating_type)
        detector = learner.undershoot_detector

        # Accumulate time just below threshold
        seconds_below_threshold = (time_threshold - 0.1) * 3600.0
        # Use small error to avoid triggering debt threshold
        # error = 0.51 for all types (just above cold_tolerance of 0.5)
        temp = 20.0 - 0.51
        detector.update(temp=temp, setpoint=20.0, dt_seconds=seconds_below_threshold, cold_tolerance=0.5)

        # Should not trigger yet
        assert not detector.should_adjust_ki(cycles_completed=0)

        # Add enough time to exceed threshold
        detector.update(temp=temp, setpoint=20.0, dt_seconds=360.0, cold_tolerance=0.5)  # +6 minutes

        # Should trigger now
        assert detector.should_adjust_ki(cycles_completed=0)


class TestUndershootDetectionCooldown:
    """Test cooldown behavior between adjustments."""

    def test_enforces_cooldown_between_adjustments(self):
        """Test that detector enforces cooldown period between adjustments."""
        learner = AdaptiveLearner(heating_type=HeatingType.FLOOR_HYDRONIC)
        detector = learner.undershoot_detector

        # First adjustment
        detector.time_below_target = 4.0 * 3600.0
        detector.thermal_debt = 2.4

        # Apply adjustment
        assert detector.should_adjust_ki(cycles_completed=0)
        detector.apply_adjustment()

        # Immediately try to adjust again (still in cooldown)
        detector.time_below_target = 8.0 * 3600.0
        detector.thermal_debt = 4.8

        # Should be blocked by cooldown
        assert not detector.should_adjust_ki(cycles_completed=0)

        # Fast-forward time past cooldown (floor_hydronic: 24 hours)
        detector.last_adjustment_time = time.monotonic() - (24.5 * 3600.0)

        # Should be allowed now
        assert detector.should_adjust_ki(cycles_completed=0)
