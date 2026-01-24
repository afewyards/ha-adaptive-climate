"""Tests for NightSetbackCalculator preheat functionality."""
import pytest
from datetime import datetime, time as dt_time, timedelta
from unittest.mock import Mock
from custom_components.adaptive_thermostat.managers.night_setback_calculator import NightSetbackCalculator
from custom_components.adaptive_thermostat.adaptive.preheat import PreheatLearner


class TestNightSetbackCalculatorPreheat:
    """Test preheat start calculation in NightSetbackCalculator."""

    def create_calculator(
        self,
        preheat_learner=None,
        preheat_enabled=False,
        night_setback_config=None
    ):
        """Helper to create NightSetbackCalculator with preheat support."""
        hass = Mock()
        entity_id = "climate.test"
        get_target_temp = Mock(return_value=20.0)
        get_current_temp = Mock(return_value=18.0)

        calculator = NightSetbackCalculator(
            hass=hass,
            entity_id=entity_id,
            night_setback=None,
            night_setback_config=night_setback_config or {
                "start": "22:00",
                "delta": 3.0,
                "recovery_deadline": "07:00"
            },
            window_orientation=None,
            get_target_temp=get_target_temp,
            get_current_temp=get_current_temp,
            preheat_learner=preheat_learner,
            preheat_enabled=preheat_enabled,
        )

        return calculator

    def test_calculate_preheat_start_basic(self):
        """Test basic preheat start calculation with learned data."""
        # Create learner with known data
        learner = PreheatLearner(heating_type="radiator")
        # Add observations: 4°C rise in 120 minutes = 2.0°C/hour
        learner.add_observation(
            start_temp=16.0,
            end_temp=20.0,
            outdoor_temp=5.0,
            duration_minutes=120.0
        )

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True
        )

        # Test: 18°C current, 20°C target, 5°C outdoor
        # Delta = 2°C, fallback rate ~1.2°C/h for radiator
        # Time estimate from learner.estimate_time_to_target()
        deadline = datetime(2024, 1, 15, 7, 0)
        current_temp = 18.0
        target_temp = 20.0
        outdoor_temp = 5.0

        preheat_start = calculator.calculate_preheat_start(
            deadline, current_temp, target_temp, outdoor_temp
        )

        # Should return a datetime before the deadline
        assert preheat_start is not None
        assert preheat_start < deadline
        assert preheat_start > deadline - timedelta(hours=4)

    def test_calculate_preheat_start_with_buffer(self):
        """Test that 10% buffer (minimum 15 min) is added."""
        learner = PreheatLearner(heating_type="forced_air")
        # Add observation suggesting 30 minutes needed
        learner.add_observation(
            start_temp=18.0,
            end_temp=20.0,
            outdoor_temp=8.0,
            duration_minutes=30.0
        )

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True
        )

        deadline = datetime(2024, 1, 15, 7, 0)
        preheat_start = calculator.calculate_preheat_start(
            deadline, 18.0, 20.0, 8.0
        )

        # 30 min estimated + 10% buffer (3 min) -> 15 min minimum buffer = 45 min total
        # Should be approximately 45 minutes before deadline
        expected_start = deadline - timedelta(minutes=45)
        assert preheat_start is not None
        # Allow some tolerance for calculation differences
        assert abs((preheat_start - expected_start).total_seconds()) < 300  # 5 min tolerance

    def test_calculate_preheat_start_clamped_to_max_hours(self):
        """Test that total time is clamped to max_preheat_hours."""
        learner = PreheatLearner(heating_type="floor_hydronic", max_hours=3.0)
        # Simulate very slow heating
        learner.add_observation(
            start_temp=10.0,
            end_temp=12.0,
            outdoor_temp=0.0,
            duration_minutes=360.0  # 2°C in 6 hours = 0.33°C/h
        )

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True,
            night_setback_config={
                "start": "22:00",
                "delta": 5.0,
                "recovery_deadline": "07:00"
            }
        )

        deadline = datetime(2024, 1, 15, 7, 0)
        # Large delta would normally require many hours
        preheat_start = calculator.calculate_preheat_start(
            deadline, 12.0, 20.0, 0.0
        )

        # Should be clamped to max_preheat_hours (3.0 hours = 180 minutes)
        max_start = deadline - timedelta(minutes=180)
        assert preheat_start is not None
        assert preheat_start >= max_start

    def test_calculate_preheat_start_disabled(self):
        """Test that preheat start returns None when disabled."""
        learner = PreheatLearner(heating_type="radiator")
        learner.add_observation(18.0, 20.0, 5.0, 60.0)

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=False  # Disabled
        )

        deadline = datetime(2024, 1, 15, 7, 0)
        preheat_start = calculator.calculate_preheat_start(
            deadline, 18.0, 20.0, 5.0
        )

        assert preheat_start is None

    def test_calculate_preheat_start_no_recovery_deadline(self):
        """Test that preheat start returns None when no recovery_deadline."""
        learner = PreheatLearner(heating_type="radiator")
        learner.add_observation(18.0, 20.0, 5.0, 60.0)

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True,
            night_setback_config={
                "start": "22:00",
                "delta": 3.0,
                # No recovery_deadline
            }
        )

        deadline = datetime(2024, 1, 15, 7, 0)
        preheat_start = calculator.calculate_preheat_start(
            deadline, 18.0, 20.0, 5.0
        )

        assert preheat_start is None

    def test_calculate_preheat_start_already_at_target(self):
        """Test when already at or above target temperature."""
        learner = PreheatLearner(heating_type="radiator")
        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True
        )

        deadline = datetime(2024, 1, 15, 7, 0)

        # At target
        preheat_start = calculator.calculate_preheat_start(
            deadline, 20.0, 20.0, 5.0
        )
        assert preheat_start == deadline  # No preheat needed

        # Above target
        preheat_start = calculator.calculate_preheat_start(
            deadline, 21.0, 20.0, 5.0
        )
        assert preheat_start == deadline  # No preheat needed

    def test_get_preheat_info_with_scheduled_start(self):
        """Test get_preheat_info returns correct info dict."""
        learner = PreheatLearner(heating_type="radiator")
        learner.add_observation(18.0, 20.0, 5.0, 60.0)

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True
        )

        now = datetime(2024, 1, 15, 5, 0)
        deadline = datetime(2024, 1, 15, 7, 0)

        info = calculator.get_preheat_info(
            now, 18.0, 20.0, 5.0, deadline
        )

        assert isinstance(info, dict)
        assert "scheduled_start" in info
        assert "estimated_duration" in info
        assert "active" in info

        assert info["scheduled_start"] is not None
        assert info["estimated_duration"] > 0
        # Active if now >= scheduled_start
        assert isinstance(info["active"], bool)

    def test_get_preheat_info_active_status(self):
        """Test active status is True when past scheduled start."""
        learner = PreheatLearner(heating_type="forced_air")
        # Quick heating: 2°C in 15 minutes
        learner.add_observation(18.0, 20.0, 10.0, 15.0)

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True
        )

        deadline = datetime(2024, 1, 15, 7, 0)

        # Current time is before scheduled start
        now_before = datetime(2024, 1, 15, 5, 0)
        info_before = calculator.get_preheat_info(
            now_before, 18.0, 20.0, 10.0, deadline
        )
        assert info_before["active"] is False

        # Current time is after scheduled start
        now_after = datetime(2024, 1, 15, 6, 50)
        info_after = calculator.get_preheat_info(
            now_after, 18.0, 20.0, 10.0, deadline
        )
        assert info_after["active"] is True

    def test_get_preheat_info_disabled(self):
        """Test get_preheat_info when preheat is disabled."""
        learner = PreheatLearner(heating_type="radiator")
        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=False
        )

        now = datetime(2024, 1, 15, 5, 0)
        deadline = datetime(2024, 1, 15, 7, 0)

        info = calculator.get_preheat_info(
            now, 18.0, 20.0, 5.0, deadline
        )

        assert info["scheduled_start"] is None
        assert info["estimated_duration"] == 0
        assert info["active"] is False

    def test_minimum_buffer_15_minutes(self):
        """Test that minimum buffer is 15 minutes even when 10% is less."""
        learner = PreheatLearner(heating_type="forced_air")
        # Quick heating: 2°C in 10 minutes, but with only 1 observation
        # it will use fallback rate of 4.0 C/h
        learner.add_observation(18.0, 20.0, 10.0, 10.0)

        calculator = self.create_calculator(
            preheat_learner=learner,
            preheat_enabled=True
        )

        deadline = datetime(2024, 1, 15, 7, 0)
        preheat_start = calculator.calculate_preheat_start(
            deadline, 18.0, 20.0, 10.0
        )

        # With fallback rate (4.0 C/h) and margins:
        # margin = (1.0 + 2.0/10*0.3) * 1.1 = 1.166
        # time = (2.0/4.0) * 60 * 1.166 ~= 35 min
        # 10% buffer = 3.5 min -> 15 min minimum
        # Total: 35 + 15 = 50 min
        expected_start = deadline - timedelta(minutes=50)
        assert preheat_start is not None
        # Allow tolerance for calculation differences
        assert abs((preheat_start - expected_start).total_seconds()) < 300
