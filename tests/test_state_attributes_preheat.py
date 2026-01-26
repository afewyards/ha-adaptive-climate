"""Tests for preheat state attributes."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from custom_components.adaptive_thermostat.managers.state_attributes import (
    build_state_attributes,
)


class TestPreheatStateAttributes:
    """Test preheat-related state attributes."""

    def _create_base_thermostat(self) -> MagicMock:
        """Create a mock thermostat with base attributes."""
        thermostat = MagicMock()
        thermostat._away_temp = 18.0
        thermostat._eco_temp = 19.0
        thermostat._boost_temp = 24.0
        thermostat._comfort_temp = 21.0
        thermostat._home_temp = 20.0
        thermostat._sleep_temp = 18.0
        thermostat._activity_temp = 20.0
        thermostat._control_output = 50.0
        thermostat._kp = 20.0
        thermostat._ki = 0.01
        thermostat._kd = 100.0
        thermostat._ke = 0.5
        thermostat.pid_mode = "auto"
        thermostat.pid_control_i = 5.0
        thermostat._pid_controller = MagicMock()
        thermostat._pid_controller.outdoor_temp_lagged = 5.0
        thermostat._pid_controller.outdoor_temp_lag_tau = 3600.0
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 100
        thermostat._heater_controller.cooler_cycle_count = 50
        thermostat._heater_controller.duty_accumulator_seconds = 120.5
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._night_setback_calculator = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        # Enable debug mode for preheat attributes to be visible
        thermostat.hass.data = {"adaptive_thermostat": {"debug": True}}
        # Add temperature getters for preheat
        thermostat._get_current_temp = MagicMock(return_value=18.0)
        thermostat._get_target_temp = MagicMock(return_value=21.0)
        thermostat._outdoor_sensor_temp = 5.0
        # Mock night setback adjustment calculation
        thermostat._calculate_night_setback_adjustment = MagicMock(
            return_value=(21.0, False, {})
        )
        return thermostat

    def test_preheat_disabled_attributes(self):
        """Test preheat attributes when preheat is disabled (not shown)."""
        thermostat = self._create_base_thermostat()
        thermostat._preheat_learner = None

        attrs = build_state_attributes(thermostat)

        # Preheat attributes should NOT be present when preheat is disabled
        assert "preheat_enabled" not in attrs
        assert "preheat_active" not in attrs
        assert "preheat_scheduled_start" not in attrs
        assert "preheat_estimated_duration_min" not in attrs
        assert "preheat_learning_confidence" not in attrs
        assert "preheat_heating_rate_learned" not in attrs
        assert "preheat_observation_count" not in attrs

    def test_preheat_enabled_no_data(self):
        """Test preheat attributes when enabled but no learning data yet."""
        thermostat = self._create_base_thermostat()

        # Mock preheat learner with no observations
        preheat_learner = MagicMock()
        preheat_learner.get_confidence.return_value = 0.0
        preheat_learner.get_observation_count.return_value = 0
        preheat_learner.get_learned_rate.return_value = None
        thermostat._preheat_learner = preheat_learner

        # Mock night setback calculator with preheat info (no preheat scheduled)
        calculator = MagicMock()
        calculator.get_preheat_info.return_value = {
            "scheduled_start": None,
            "estimated_duration": 0,
            "active": False,
        }
        controller = MagicMock()
        controller.calculator = calculator
        thermostat._night_setback_controller = controller

        attrs = build_state_attributes(thermostat)

        # preheat_enabled no longer exposed - attributes only present when enabled
        assert attrs["preheat_learning_confidence"] == 0.0
        assert "preheat_heating_rate_learned" not in attrs  # Not set when no learned rate
        assert attrs["preheat_observation_count"] == 0

    def test_preheat_scheduled_not_active(self):
        """Test preheat attributes when preheat is scheduled but not yet active."""
        thermostat = self._create_base_thermostat()

        # Mock preheat learner with some learning data
        preheat_learner = MagicMock()
        preheat_learner.get_confidence.return_value = 0.6
        preheat_learner.get_observation_count.return_value = 6
        preheat_learner.get_learned_rate.return_value = 2.5  # 2.5 C/hour
        thermostat._preheat_learner = preheat_learner

        # Set night setback config with recovery deadline
        thermostat._night_setback_config = {
            "recovery_deadline": "07:00",
            "delta": 3.0,
        }

        # Mock night setback calculator with scheduled preheat (future)
        scheduled_start = datetime.now() + timedelta(hours=2)
        calculator = MagicMock()
        calculator.get_preheat_info.return_value = {
            "scheduled_start": scheduled_start,
            "estimated_duration": 45,  # 45 minutes
            "active": False,
        }
        controller = MagicMock()
        controller.calculator = calculator
        thermostat._night_setback_controller = controller

        attrs = build_state_attributes(thermostat)

        assert attrs["preheat_active"] is False
        assert attrs["preheat_scheduled_start"] == scheduled_start.isoformat()
        assert attrs["preheat_estimated_duration_min"] == 45
        assert attrs["preheat_learning_confidence"] == 0.6
        assert attrs["preheat_heating_rate_learned"] == 2.5
        assert attrs["preheat_observation_count"] == 6

    def test_preheat_active(self):
        """Test preheat attributes when preheat is currently active."""
        thermostat = self._create_base_thermostat()

        # Mock preheat learner with good learning data
        preheat_learner = MagicMock()
        preheat_learner.get_confidence.return_value = 0.9
        preheat_learner.get_observation_count.return_value = 12
        preheat_learner.get_learned_rate.return_value = 3.2  # 3.2 C/hour
        thermostat._preheat_learner = preheat_learner

        # Set night setback config with recovery deadline
        thermostat._night_setback_config = {
            "recovery_deadline": "07:00",
            "delta": 3.0,
        }

        # Mock night setback calculator with active preheat
        scheduled_start = datetime.now() - timedelta(minutes=10)  # Started 10 min ago
        calculator = MagicMock()
        calculator.get_preheat_info.return_value = {
            "scheduled_start": scheduled_start,
            "estimated_duration": 60,
            "active": True,
        }
        controller = MagicMock()
        controller.calculator = calculator
        thermostat._night_setback_controller = controller

        attrs = build_state_attributes(thermostat)

        assert attrs["preheat_active"] is True
        assert attrs["preheat_scheduled_start"] == scheduled_start.isoformat()
        assert attrs["preheat_estimated_duration_min"] == 60
        assert attrs["preheat_learning_confidence"] == 0.9
        assert attrs["preheat_heating_rate_learned"] == 3.2
        assert attrs["preheat_observation_count"] == 12

    def test_preheat_confidence_zero_to_one(self):
        """Test that confidence is in 0.0-1.0 range."""
        thermostat = self._create_base_thermostat()

        preheat_learner = MagicMock()
        preheat_learner.get_observation_count.return_value = 5
        preheat_learner.get_learned_rate.return_value = 2.0
        thermostat._preheat_learner = preheat_learner

        calculator = MagicMock()
        calculator.get_preheat_info.return_value = {
            "scheduled_start": None,
            "estimated_duration": 0,
            "active": False,
        }
        controller = MagicMock()
        controller.calculator = calculator
        thermostat._night_setback_controller = controller

        # Test various confidence values
        for confidence in [0.0, 0.25, 0.5, 0.75, 1.0]:
            preheat_learner.get_confidence.return_value = confidence
            attrs = build_state_attributes(thermostat)
            assert attrs["preheat_learning_confidence"] == confidence

    def test_preheat_no_learned_rate_yet(self):
        """Test preheat with observations but no learned rate for current conditions."""
        thermostat = self._create_base_thermostat()

        # Has observations but no data for specific bin
        preheat_learner = MagicMock()
        preheat_learner.get_confidence.return_value = 0.3
        preheat_learner.get_observation_count.return_value = 3
        preheat_learner.get_learned_rate.return_value = None  # No data for this bin
        thermostat._preheat_learner = preheat_learner

        calculator = MagicMock()
        calculator.get_preheat_info.return_value = {
            "scheduled_start": None,
            "estimated_duration": 0,
            "active": False,
        }
        controller = MagicMock()
        controller.calculator = calculator
        thermostat._night_setback_controller = controller

        attrs = build_state_attributes(thermostat)

        assert attrs["preheat_learning_confidence"] == 0.3
        # preheat_heating_rate_learned not set when None
        assert "preheat_heating_rate_learned" not in attrs
        assert attrs["preheat_observation_count"] == 3

    def test_preheat_without_calculator(self):
        """Test preheat attributes when calculator is None but learner exists."""
        thermostat = self._create_base_thermostat()

        preheat_learner = MagicMock()
        preheat_learner.get_confidence.return_value = 0.4
        preheat_learner.get_observation_count.return_value = 4
        preheat_learner.get_learned_rate.return_value = 2.1
        thermostat._preheat_learner = preheat_learner
        thermostat._night_setback_calculator = None  # Calculator not initialized

        attrs = build_state_attributes(thermostat)

        # Should still show learner data but no schedule info
        assert attrs["preheat_learning_confidence"] == 0.4
        assert attrs["preheat_heating_rate_learned"] == 2.1
        assert attrs["preheat_observation_count"] == 4
        # No schedule info when controller is None
        assert "preheat_active" not in attrs
        assert "preheat_scheduled_start" not in attrs
        assert "preheat_estimated_duration_min" not in attrs

    def test_preheat_timestamp_formatting(self):
        """Test that scheduled_start is formatted as ISO 8601."""
        thermostat = self._create_base_thermostat()

        preheat_learner = MagicMock()
        preheat_learner.get_confidence.return_value = 0.8
        preheat_learner.get_observation_count.return_value = 8
        preheat_learner.get_learned_rate.return_value = 3.0
        thermostat._preheat_learner = preheat_learner

        # Set night setback config with recovery deadline
        thermostat._night_setback_config = {
            "recovery_deadline": "07:00",
            "delta": 3.0,
        }

        # Test with specific timestamp
        test_time = datetime(2024, 6, 15, 5, 30, 0)
        calculator = MagicMock()
        calculator.get_preheat_info.return_value = {
            "scheduled_start": test_time,
            "estimated_duration": 90,
            "active": False,
        }
        controller = MagicMock()
        controller.calculator = calculator
        thermostat._night_setback_controller = controller

        attrs = build_state_attributes(thermostat)

        # Should be in ISO format
        assert attrs["preheat_scheduled_start"] == test_time.isoformat()
        assert "2024-06-15" in attrs["preheat_scheduled_start"]
        assert "05:30:00" in attrs["preheat_scheduled_start"]

    def test_preheat_high_observation_count(self):
        """Test preheat with many observations (confidence should cap at 1.0)."""
        thermostat = self._create_base_thermostat()

        preheat_learner = MagicMock()
        preheat_learner.get_confidence.return_value = 1.0  # Capped at 1.0
        preheat_learner.get_observation_count.return_value = 25
        preheat_learner.get_learned_rate.return_value = 2.8
        thermostat._preheat_learner = preheat_learner

        calculator = MagicMock()
        calculator.get_preheat_info.return_value = {
            "scheduled_start": None,
            "estimated_duration": 0,
            "active": False,
        }
        controller = MagicMock()
        controller.calculator = calculator
        thermostat._night_setback_controller = controller

        attrs = build_state_attributes(thermostat)

        assert attrs["preheat_learning_confidence"] == 1.0
        assert attrs["preheat_observation_count"] == 25
