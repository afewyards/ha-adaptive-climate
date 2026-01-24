"""Tests for NightSetback integration with PreheatLearner."""
import pytest
from datetime import datetime
from custom_components.adaptive_thermostat.adaptive.night_setback import NightSetback
from custom_components.adaptive_thermostat.adaptive.preheat import PreheatLearner


class TestNightSetbackPreheatIntegration:
    """Test NightSetback.should_start_recovery() with PreheatLearner."""

    def test_should_start_recovery_uses_preheat_learner(self):
        """Test should_start_recovery uses PreheatLearner when provided."""
        # Create learner with known heating rate data
        learner = PreheatLearner(heating_type="radiator")
        # Add observations: 4°C rise in 120 minutes = 2.0°C/hour
        learner.add_observation(
            start_temp=16.0,
            end_temp=20.0,
            outdoor_temp=5.0,
            duration_minutes=120.0
        )

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=3.0,
            recovery_deadline="07:00",
            preheat_learner=learner,
            heating_type="radiator"
        )

        # Current: 05:00, 2 hours until deadline
        # Temp deficit: 4°C (16°C current, 20°C target)
        # Outdoor: 5°C
        # PreheatLearner should estimate time based on learned data
        # With 1 observation, falls back to fallback_rate (1.2 C/h for radiator)
        # margin = (1.0 + 4.0/10*0.3) * 1.3 ~= 1.456
        # time = (4.0 / 1.2) * 60 * 1.456 ~= 291 min = 4.85 hours
        # Should start recovery since 4.85h > 2h

        current = datetime(2024, 1, 15, 5, 0)
        base_setpoint = 20.0
        current_temp = 16.0
        outdoor_temp = 5.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        assert should_recover is True

    def test_should_start_recovery_preheat_learner_with_data(self):
        """Test with enough observations to use learned rate."""
        learner = PreheatLearner(heating_type="radiator")
        # Add 3 observations with consistent rate: 2.0°C/hour
        for _ in range(3):
            learner.add_observation(
                start_temp=18.0,
                end_temp=20.0,
                outdoor_temp=8.0,
                duration_minutes=60.0  # 2°C in 60 min = 2.0°C/h
            )

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            preheat_learner=learner,
            heating_type="radiator"
        )

        # Current: 05:30, 1.5 hours until deadline
        # Temp deficit: 2°C (18°C current, 20°C target)
        # Outdoor: 8°C
        # Learned rate: 2.0°C/h (median)
        # margin = (1.0 + 2.0/10*0.3) * 1.3 ~= 1.378
        # time = (2.0 / 2.0) * 60 * 1.378 ~= 82.7 min = 1.38 hours
        # Should NOT start recovery since 1.38h < 1.5h

        current = datetime(2024, 1, 15, 5, 30)
        base_setpoint = 20.0
        current_temp = 18.0
        outdoor_temp = 8.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        assert should_recover is False

    def test_should_start_recovery_fallback_to_heating_type(self):
        """Test fallback to heating type estimate when no learner."""
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=3.0,
            recovery_deadline="07:00",
            preheat_learner=None,  # No learner
            heating_type="forced_air"
        )

        # Current: 05:00, 2 hours until deadline
        # Temp deficit: 4°C (16°C current, 20°C target)
        # Forced air estimate: 4.0°C/h
        # Cold-soak margin: 1.1x for forced_air
        # Estimated recovery: (4 / 4.0) * 1.1 = 1.1 hours
        # Should NOT start recovery since 1.1h < 2h

        current = datetime(2024, 1, 15, 5, 0)
        base_setpoint = 20.0
        current_temp = 16.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint
        )
        assert should_recover is False

    def test_should_start_recovery_returns_true_when_time_is_up(self):
        """Test returns True when now >= calculated preheat start time."""
        learner = PreheatLearner(heating_type="radiator")
        # Fast heating: 4°C in 60 minutes = 4.0°C/hour
        for _ in range(3):
            learner.add_observation(
                start_temp=16.0,
                end_temp=20.0,
                outdoor_temp=10.0,
                duration_minutes=60.0
            )

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=3.0,
            recovery_deadline="07:00",
            preheat_learner=learner,
            heating_type="radiator"
        )

        # Current: 06:30, 0.5 hours until deadline
        # Temp deficit: 4°C (16°C current, 20°C target)
        # Learned rate: 4.0°C/h
        # margin = (1.0 + 4.0/10*0.3) * 1.3 ~= 1.456
        # time = (4.0 / 4.0) * 60 * 1.456 ~= 87.4 min = 1.46 hours
        # Should start recovery since 1.46h > 0.5h

        current = datetime(2024, 1, 15, 6, 30)
        base_setpoint = 20.0
        current_temp = 16.0
        outdoor_temp = 10.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        assert should_recover is True

    def test_should_start_recovery_returns_false_when_plenty_of_time(self):
        """Test returns False when now < calculated preheat start time."""
        learner = PreheatLearner(heating_type="forced_air")
        # Very fast heating: 4°C in 30 minutes = 8.0°C/hour
        for _ in range(3):
            learner.add_observation(
                start_temp=16.0,
                end_temp=20.0,
                outdoor_temp=10.0,
                duration_minutes=30.0
            )

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            preheat_learner=learner,
            heating_type="forced_air"
        )

        # Current: 03:00, 4 hours until deadline
        # Temp deficit: 2°C (18°C current, 20°C target)
        # Learned rate: 8.0°C/h
        # margin = (1.0 + 2.0/10*0.3) * 1.1 ~= 1.166
        # time = (2.0 / 8.0) * 60 * 1.166 ~= 17.5 min = 0.29 hours
        # Should NOT start recovery since 0.29h << 4h

        current = datetime(2024, 1, 15, 3, 0)
        base_setpoint = 20.0
        current_temp = 18.0
        outdoor_temp = 10.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        assert should_recover is False

    def test_should_start_recovery_respects_max_preheat_hours(self):
        """Test respects max_preheat_hours cap from learner config."""
        # Learner with max_hours = 2.0
        learner = PreheatLearner(heating_type="floor_hydronic", max_hours=2.0)
        # Very slow heating: 2°C in 360 minutes = 0.33°C/hour
        learner.add_observation(
            start_temp=10.0,
            end_temp=12.0,
            outdoor_temp=0.0,
            duration_minutes=360.0
        )

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=5.0,
            recovery_deadline="07:00",
            preheat_learner=learner,
            heating_type="floor_hydronic"
        )

        # Current: 04:00, 3 hours until deadline
        # Temp deficit: 8°C (12°C current, 20°C target)
        # Outdoor: 0°C (cold)
        # With fallback rate (0.5 C/h for floor) and high delta:
        # margin = (1.0 + 8.0/10*0.3) * 1.5 ~= 1.86
        # time = (8.0 / 0.5) * 60 * 1.86 ~= 1785 min = 29.75 hours
        # But clamped to max_hours * 60 = 120 minutes = 2.0 hours
        # Should start recovery since 2.0h < 3h (but still need to start early)

        current = datetime(2024, 1, 15, 4, 0)
        base_setpoint = 20.0
        current_temp = 12.0
        outdoor_temp = 0.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        # With 2 hour max and 3 hours available, should NOT start yet
        assert should_recover is False

        # But at 05:30 (1.5 hours until deadline), should start
        current = datetime(2024, 1, 15, 5, 30)
        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        assert should_recover is True

    def test_should_start_recovery_without_outdoor_temp_backward_compat(self):
        """Test backward compatibility when outdoor_temp not provided."""
        learner = PreheatLearner(heating_type="radiator")
        # Can't use learner without outdoor temp, should fall back to old method

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            preheat_learner=learner,
            heating_type="radiator"
        )

        # Current: 05:00, 2 hours until deadline
        # Temp deficit: 3°C (17°C current, 20°C target)
        # Should fall back to heating type estimate: 1.2°C/h, 1.3x margin
        # Estimated recovery: (3 / 1.2) * 1.3 = 3.25 hours
        # Should start recovery since 3.25h > 2h

        current = datetime(2024, 1, 15, 5, 0)
        base_setpoint = 20.0
        current_temp = 17.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint
        )
        assert should_recover is True

    def test_should_start_recovery_no_recovery_deadline(self):
        """Test returns False when no recovery deadline set."""
        learner = PreheatLearner(heating_type="radiator")
        learner.add_observation(18.0, 20.0, 5.0, 60.0)

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline=None,  # No deadline
            preheat_learner=learner,
            heating_type="radiator"
        )

        current = datetime(2024, 1, 15, 5, 0)
        base_setpoint = 20.0
        current_temp = 16.0
        outdoor_temp = 5.0

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        assert should_recover is False

    def test_should_start_recovery_learner_no_outdoor_bin_data(self):
        """Test when learner has no data for current outdoor conditions."""
        learner = PreheatLearner(heating_type="radiator")
        # Add observation for mild outdoor temps (5-12C)
        learner.add_observation(
            start_temp=18.0,
            end_temp=20.0,
            outdoor_temp=8.0,  # mild bin
            duration_minutes=60.0
        )

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            recovery_deadline="07:00",
            preheat_learner=learner,
            heating_type="radiator"
        )

        # Query with cold outdoor temp (different bin)
        # Should use fallback rate since no data for cold bin
        current = datetime(2024, 1, 15, 5, 0)
        base_setpoint = 20.0
        current_temp = 18.0
        outdoor_temp = 0.0  # cold bin - no observations

        should_recover = setback.should_start_recovery(
            current, current_temp, base_setpoint, outdoor_temp
        )
        # With fallback, should calculate appropriately
        # This is mostly checking it doesn't crash
        assert isinstance(should_recover, bool)


class TestNightSetbackPreheatLearnerParameter:
    """Test NightSetback accepts preheat_learner parameter."""

    def test_night_setback_accepts_preheat_learner(self):
        """Test NightSetback can be initialized with preheat_learner."""
        learner = PreheatLearner(heating_type="radiator")

        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0,
            preheat_learner=learner
        )

        assert hasattr(setback, 'preheat_learner')
        assert setback.preheat_learner is learner

    def test_night_setback_preheat_learner_optional(self):
        """Test preheat_learner is optional."""
        setback = NightSetback(
            start_time="22:00",
            end_time="06:00",
            setback_delta=2.0
        )

        assert hasattr(setback, 'preheat_learner')
        assert setback.preheat_learner is None
