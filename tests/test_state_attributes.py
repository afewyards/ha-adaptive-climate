"""Tests for state attribute building."""
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock

# Mock homeassistant.components.climate for HVACMode
mock_ha_climate = MagicMock()
# Create mock HVACMode enum
mock_hvac_mode = MagicMock()
mock_hvac_mode.HEAT = "heat"
mock_hvac_mode.COOL = "cool"
mock_ha_climate.HVACMode = mock_hvac_mode
sys.modules['homeassistant.components'] = MagicMock()
sys.modules['homeassistant.components.climate'] = mock_ha_climate

from custom_components.adaptive_thermostat.managers.state_attributes import (
    _compute_learning_status,
    _add_learning_status_attributes,
    _build_pause_attribute,
    ATTR_LEARNING_STATUS,
    ATTR_CYCLES_COLLECTED,
    ATTR_CYCLES_REQUIRED,
    ATTR_CONVERGENCE_CONFIDENCE,
    ATTR_CURRENT_CYCLE_STATE,
    ATTR_LAST_CYCLE_INTERRUPTED,
    ATTR_LAST_PID_ADJUSTMENT,
)
from custom_components.adaptive_thermostat.const import (
    MIN_CYCLES_FOR_LEARNING,
    MIN_CONVERGENCE_CYCLES_FOR_KE,
)


class TestComputeLearningStatus:
    """Test _compute_learning_status helper function."""

    def test_collecting_status(self):
        """Test collecting status when cycle count < MIN_CYCLES_FOR_LEARNING."""
        status = _compute_learning_status(
            cycle_count=3,
            convergence_confidence=0.0,
            consecutive_converged=0,
        )
        assert status == "collecting"

    def test_collecting_status_at_boundary(self):
        """Test collecting status at MIN_CYCLES_FOR_LEARNING - 1."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING - 1,
            convergence_confidence=0.8,
            consecutive_converged=0,
        )
        assert status == "collecting"

    def test_converged_status(self):
        """Test converged status when consecutive_converged >= MIN_CONVERGENCE_CYCLES_FOR_KE."""
        status = _compute_learning_status(
            cycle_count=10,
            convergence_confidence=0.9,
            consecutive_converged=MIN_CONVERGENCE_CYCLES_FOR_KE,
        )
        assert status == "converged"

    def test_converged_status_high_consecutive(self):
        """Test converged status with more consecutive cycles."""
        status = _compute_learning_status(
            cycle_count=20,
            convergence_confidence=0.95,
            consecutive_converged=10,
        )
        assert status == "converged"

    def test_active_status(self):
        """Test active status when confidence >= 0.5 but not converged."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.7,
            consecutive_converged=1,
        )
        assert status == "active"

    def test_active_status_at_boundary(self):
        """Test active status at confidence == 0.5."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING + 1,
            convergence_confidence=0.5,
            consecutive_converged=0,
        )
        assert status == "active"

    def test_ready_status(self):
        """Test ready status when cycles >= MIN but confidence < 0.5."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.3,
            consecutive_converged=0,
        )
        assert status == "ready"

    def test_ready_status_zero_confidence(self):
        """Test ready status with zero confidence."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING + 5,
            convergence_confidence=0.0,
            consecutive_converged=0,
        )
        assert status == "ready"

    def test_converged_priority_over_active(self):
        """Test that converged status takes priority over active."""
        status = _compute_learning_status(
            cycle_count=15,
            convergence_confidence=0.9,  # Would be "active"
            consecutive_converged=MIN_CONVERGENCE_CYCLES_FOR_KE,  # But converged
        )
        assert status == "converged"


class TestAddLearningStatusAttributes:
    """Test _add_learning_status_attributes function."""

    def test_no_coordinator(self):
        """Test that function handles missing coordinator gracefully."""
        thermostat = MagicMock()
        thermostat.hass.data.get.return_value = {}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should not add any attributes
        assert len(attrs) == 0

    def test_no_adaptive_learner(self):
        """Test that function handles missing adaptive learner gracefully."""
        thermostat = MagicMock()
        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": None,
                "cycle_tracker": MagicMock(),
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should not add any attributes
        assert len(attrs) == 0

    def test_no_cycle_tracker(self):
        """Test that function handles missing cycle tracker gracefully."""
        thermostat = MagicMock()
        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": MagicMock(),
                "cycle_tracker": None,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should not add any attributes
        assert len(attrs) == 0

    def test_collecting_state(self):
        """Test attributes in collecting state."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 2
        adaptive_learner.get_convergence_confidence.return_value = 0.0
        adaptive_learner.get_consecutive_converged_cycles.return_value = 0
        adaptive_learner.get_last_adjustment_time.return_value = None

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "collecting"
        assert attrs[ATTR_CYCLES_COLLECTED] == 2
        assert attrs[ATTR_CYCLES_REQUIRED] == MIN_CYCLES_FOR_LEARNING
        assert attrs[ATTR_CONVERGENCE_CONFIDENCE] == 0
        assert attrs[ATTR_CURRENT_CYCLE_STATE] == "idle"
        assert attrs[ATTR_LAST_CYCLE_INTERRUPTED] is None
        # last_pid_adjustment not set when None (cleaned up)

    def test_active_state_with_heating_cycle(self):
        """Test attributes in active state with heating cycle."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 8
        adaptive_learner.get_convergence_confidence.return_value = 0.6
        adaptive_learner.get_consecutive_converged_cycles.return_value = 1
        adaptive_learner.get_last_adjustment_time.return_value = None

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "heating"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "active"
        assert attrs[ATTR_CYCLES_COLLECTED] == 8
        assert attrs[ATTR_CONVERGENCE_CONFIDENCE] == 60  # 0.6 * 100
        assert attrs[ATTR_CURRENT_CYCLE_STATE] == "heating"

    def test_converged_state(self):
        """Test attributes in converged state."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 15
        adaptive_learner.get_convergence_confidence.return_value = 0.95
        adaptive_learner.get_consecutive_converged_cycles.return_value = MIN_CONVERGENCE_CYCLES_FOR_KE

        # Set last adjustment time
        last_adjustment = datetime(2024, 1, 15, 14, 30, 0)
        adaptive_learner.get_last_adjustment_time.return_value = last_adjustment

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "converged"
        assert attrs[ATTR_CYCLES_COLLECTED] == 15
        assert attrs[ATTR_CONVERGENCE_CONFIDENCE] == 95  # 0.95 * 100
        assert attrs[ATTR_LAST_PID_ADJUSTMENT] == last_adjustment.isoformat()

    def test_settling_state(self):
        """Test attributes with settling cycle state."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 10
        adaptive_learner.get_convergence_confidence.return_value = 0.7
        adaptive_learner.get_consecutive_converged_cycles.return_value = 2
        adaptive_learner.get_last_adjustment_time.return_value = None

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "settling"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_CURRENT_CYCLE_STATE] == "settling"

    def test_interrupted_cycle_setpoint_change(self):
        """Test attributes with interrupted cycle (setpoint change)."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 5
        adaptive_learner.get_convergence_confidence.return_value = 0.4
        adaptive_learner.get_consecutive_converged_cycles.return_value = 0
        adaptive_learner.get_last_adjustment_time.return_value = None

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = "setpoint_change"

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LAST_CYCLE_INTERRUPTED] == "setpoint_change"

    def test_interrupted_cycle_mode_change(self):
        """Test attributes with interrupted cycle (mode change)."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 7
        adaptive_learner.get_convergence_confidence.return_value = 0.5
        adaptive_learner.get_consecutive_converged_cycles.return_value = 0
        adaptive_learner.get_last_adjustment_time.return_value = None

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = "mode_change"

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LAST_CYCLE_INTERRUPTED] == "mode_change"

    def test_interrupted_cycle_contact_sensor(self):
        """Test attributes with interrupted cycle (contact sensor)."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 4
        adaptive_learner.get_convergence_confidence.return_value = 0.3
        adaptive_learner.get_consecutive_converged_cycles.return_value = 0
        adaptive_learner.get_last_adjustment_time.return_value = None

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = "contact_sensor"

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LAST_CYCLE_INTERRUPTED] == "contact_sensor"

    def test_convergence_confidence_percentage_conversion(self):
        """Test that convergence confidence is correctly converted to percentage."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = MIN_CYCLES_FOR_LEARNING
        adaptive_learner.get_consecutive_converged_cycles.return_value = 0
        adaptive_learner.get_last_adjustment_time.return_value = None

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}

        # Test various confidence values
        test_cases = [
            (0.0, 0),
            (0.25, 25),
            (0.5, 50),
            (0.75, 75),
            (0.99, 99),
            (1.0, 100),
        ]

        for confidence, expected_pct in test_cases:
            adaptive_learner.get_convergence_confidence.return_value = confidence
            attrs = {}
            _add_learning_status_attributes(thermostat, attrs)
            assert attrs[ATTR_CONVERGENCE_CONFIDENCE] == expected_pct, \
                f"Failed for confidence={confidence}, expected {expected_pct}, got {attrs[ATTR_CONVERGENCE_CONFIDENCE]}"

    def test_last_adjustment_timestamp_formatting(self):
        """Test that last adjustment timestamp is formatted as ISO 8601."""
        thermostat = MagicMock()
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = MIN_CYCLES_FOR_LEARNING
        adaptive_learner.get_convergence_confidence.return_value = 0.8
        adaptive_learner.get_consecutive_converged_cycles.return_value = 2

        # Test with a specific timestamp
        test_time = datetime(2024, 3, 15, 10, 30, 45)
        adaptive_learner.get_last_adjustment_time.return_value = test_time

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": thermostat.entity_id,
                "adaptive_learner": adaptive_learner,
                "cycle_tracker": cycle_tracker,
            }
        }
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should be in ISO format
        assert attrs[ATTR_LAST_PID_ADJUSTMENT] == test_time.isoformat()
        assert "2024-03-15" in attrs[ATTR_LAST_PID_ADJUSTMENT]
        assert "10:30:45" in attrs[ATTR_LAST_PID_ADJUSTMENT]


class TestDutyAccumulatorAttributes:
    """Tests for duty accumulator state attributes."""

    def test_accumulator_in_state_attributes(self):
        """Test duty_accumulator exposed in extra_state_attributes."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "duty_accumulator" in attrs
        assert attrs["duty_accumulator"] == 120.5

    def test_accumulator_zero_without_heater_controller(self):
        """Test duty_accumulator is 0 when heater_controller is None."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._heater_controller = None
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "duty_accumulator" in attrs
        assert attrs["duty_accumulator"] == 0.0

    def test_accumulator_pct_attribute(self):
        """Test duty_accumulator_pct shows percentage of threshold."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._heater_controller.duty_accumulator_seconds = 150.0  # 50% of 300s
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "duty_accumulator_pct" in attrs
        assert attrs["duty_accumulator_pct"] == 50.0

    def test_accumulator_pct_at_threshold(self):
        """Test duty_accumulator_pct shows 100% when at threshold."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _compute_duty_accumulator_pct,
        )

        thermostat = MagicMock()
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.duty_accumulator_seconds = 300.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0

        pct = _compute_duty_accumulator_pct(thermostat)

        assert pct == 100.0

    def test_accumulator_pct_above_threshold(self):
        """Test duty_accumulator_pct can exceed 100% (max is 200%)."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _compute_duty_accumulator_pct,
        )

        thermostat = MagicMock()
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.duty_accumulator_seconds = 500.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0

        pct = _compute_duty_accumulator_pct(thermostat)

        assert pct == pytest.approx(166.7, rel=0.01)

    def test_accumulator_pct_zero_without_controller(self):
        """Test duty_accumulator_pct is 0 when heater_controller is None."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _compute_duty_accumulator_pct,
        )

        thermostat = MagicMock()
        thermostat._heater_controller = None

        pct = _compute_duty_accumulator_pct(thermostat)

        assert pct == 0.0

    def test_accumulator_pct_zero_with_zero_min_on(self):
        """Test duty_accumulator_pct handles zero min_on_cycle_duration."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _compute_duty_accumulator_pct,
        )

        thermostat = MagicMock()
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.duty_accumulator_seconds = 100.0
        thermostat._heater_controller.min_on_cycle_duration = 0.0

        pct = _compute_duty_accumulator_pct(thermostat)

        assert pct == 0.0


class TestPerModeConvergenceConfidence:
    """Tests for per-mode convergence confidence attributes."""

    def test_kp_ki_kd_removed_from_state_attributes(self):
        """Test that kp, ki, kd are no longer in state attributes (moved to persistence)."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        # kp, ki, kd should NOT be in state attributes
        assert "kp" not in attrs
        assert "ki" not in attrs
        assert "kd" not in attrs
        # Other attributes should still be present
        assert "ke" in attrs
        assert "pid_mode" in attrs
        # pid_i renamed to integral and only shown in debug mode
        assert "pid_i" not in attrs

    def test_heating_convergence_confidence_attribute(self):
        """Test heating_convergence_confidence attribute is added."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )
        from homeassistant.components.climate import HVACMode

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        # Setup coordinator with adaptive learner
        adaptive_learner = MagicMock()
        adaptive_learner.get_convergence_confidence.return_value = 0.75

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": "climate.test_zone",
                "adaptive_learner": adaptive_learner,
            }
        }

        thermostat.hass = MagicMock()
        thermostat.hass.data = {"adaptive_thermostat": {"coordinator": coordinator}}

        attrs = build_state_attributes(thermostat)

        # Should have heating_convergence_confidence attribute
        assert "heating_convergence_confidence" in attrs
        # Verify it's called with HVACMode.HEAT
        adaptive_learner.get_convergence_confidence.assert_any_call(HVACMode.HEAT)
        # Value should be percentage (0.75 -> 75)
        assert attrs["heating_convergence_confidence"] == 75

    def test_cooling_convergence_confidence_attribute(self):
        """Test cooling_convergence_confidence attribute is added."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )
        from homeassistant.components.climate import HVACMode

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        # Setup coordinator with adaptive learner
        adaptive_learner = MagicMock()
        adaptive_learner.get_convergence_confidence.return_value = 0.85

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": "climate.test_zone",
                "adaptive_learner": adaptive_learner,
            }
        }

        thermostat.hass = MagicMock()
        thermostat.hass.data = {"adaptive_thermostat": {"coordinator": coordinator}}

        attrs = build_state_attributes(thermostat)

        # Should have cooling_convergence_confidence attribute
        assert "cooling_convergence_confidence" in attrs
        # Verify it's called with HVACMode.COOL
        adaptive_learner.get_convergence_confidence.assert_called_with(HVACMode.COOL)
        # Value should be percentage (0.85 -> 85)
        assert attrs["cooling_convergence_confidence"] == 85

    def test_both_mode_convergence_attributes(self):
        """Test both heating and cooling convergence attributes are present."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )
        from homeassistant.components.climate import HVACMode

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        # Setup coordinator with adaptive learner
        adaptive_learner = MagicMock()
        # Return different values for different modes
        def get_confidence_by_mode(mode):
            if mode == HVACMode.HEAT:
                return 0.65
            elif mode == HVACMode.COOL:
                return 0.90
            return 0.0

        adaptive_learner.get_convergence_confidence.side_effect = get_confidence_by_mode

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": "climate.test_zone",
                "adaptive_learner": adaptive_learner,
            }
        }

        thermostat.hass = MagicMock()
        thermostat.hass.data = {"adaptive_thermostat": {"coordinator": coordinator}}

        attrs = build_state_attributes(thermostat)

        # Both attributes should be present
        assert "heating_convergence_confidence" in attrs
        assert "cooling_convergence_confidence" in attrs
        # Values should be different
        assert attrs["heating_convergence_confidence"] == 65
        assert attrs["cooling_convergence_confidence"] == 90

    def test_convergence_confidence_no_coordinator(self):
        """Test that missing coordinator doesn't add per-mode convergence attributes."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        # Per-mode convergence attributes should not be present
        assert "heating_convergence_confidence" not in attrs
        assert "cooling_convergence_confidence" not in attrs

    def test_convergence_confidence_no_adaptive_learner(self):
        """Test that missing adaptive learner doesn't add per-mode convergence attributes."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": "climate.test_zone",
                "adaptive_learner": None,
            }
        }

        thermostat.hass = MagicMock()
        thermostat.hass.data = {"adaptive_thermostat": {"coordinator": coordinator}}

        attrs = build_state_attributes(thermostat)

        # Per-mode convergence attributes should not be present
        assert "heating_convergence_confidence" not in attrs
        assert "cooling_convergence_confidence" not in attrs

    def test_convergence_confidence_rounding(self):
        """Test that convergence confidence is properly rounded to integer percentage."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )
        from homeassistant.components.climate import HVACMode

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        # Setup coordinator with adaptive learner
        adaptive_learner = MagicMock()
        # Return value that needs rounding
        adaptive_learner.get_convergence_confidence.return_value = 0.7349

        coordinator = MagicMock()
        coordinator.get_all_zones.return_value = {
            "zone1": {
                "climate_entity_id": "climate.test_zone",
                "adaptive_learner": adaptive_learner,
            }
        }

        thermostat.hass = MagicMock()
        thermostat.hass.data = {"adaptive_thermostat": {"coordinator": coordinator}}

        attrs = build_state_attributes(thermostat)

        # Should be rounded to integer (0.7349 * 100 = 73.49 -> 73)
        assert attrs["heating_convergence_confidence"] == 73
        assert attrs["cooling_convergence_confidence"] == 73


class TestHumidityDetectionAttributes:
    """Tests for humidity detection state attributes."""

    def test_humidity_detection_attributes_absent_when_no_detector(self):
        """Test that humidity detection attributes are not added when detector is None."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat._humidity_detector = None
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        # Humidity detection attributes should not be present
        assert "humidity_detection_state" not in attrs
        assert "humidity_resume_in" not in attrs

    def test_humidity_detection_normal_state(self):
        """Test humidity detection attributes when detector is in normal state."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        # Setup humidity detector in normal state
        humidity_detector = MagicMock()
        humidity_detector.get_state.return_value = "normal"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector

        attrs = build_state_attributes(thermostat)

        # Humidity detection attributes should be present
        assert attrs["humidity_detection_state"] == "normal"
        assert attrs["humidity_resume_in"] is None

    def test_humidity_detection_paused_state(self):
        """Test humidity detection attributes when detector is in paused state."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        # Setup humidity detector in paused state
        humidity_detector = MagicMock()
        humidity_detector.get_state.return_value = "paused"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector

        attrs = build_state_attributes(thermostat)

        # Humidity detection attributes should be present
        assert attrs["humidity_detection_state"] == "paused"
        assert attrs["humidity_resume_in"] is None

    def test_humidity_detection_stabilizing_state(self):
        """Test humidity detection attributes when detector is in stabilizing state."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        # Setup humidity detector in stabilizing state with resume time
        humidity_detector = MagicMock()
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 180  # 3 minutes
        thermostat._humidity_detector = humidity_detector

        attrs = build_state_attributes(thermostat)

        # Humidity detection attributes should be present
        assert attrs["humidity_detection_state"] == "stabilizing"
        assert attrs["humidity_resume_in"] == 180

    def test_humidity_detection_stabilizing_with_zero_resume_time(self):
        """Test humidity detection attributes when stabilizing with 0 resume time."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

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
        thermostat._transport_delay = 0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._control_output_manager = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        # Setup humidity detector in stabilizing state with 0 resume time (about to exit)
        humidity_detector = MagicMock()
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 0
        thermostat._humidity_detector = humidity_detector

        attrs = build_state_attributes(thermostat)

        # Humidity detection attributes should be present
        assert attrs["humidity_detection_state"] == "stabilizing"
        assert attrs["humidity_resume_in"] == 0


class TestPauseAttribute:
    """Tests for consolidated pause attribute."""

    def test_pause_not_active_no_detectors(self):
        """Test pause attribute when no detectors configured."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None

        pause_attr = _build_pause_attribute(thermostat)

        assert pause_attr == {"active": False, "reason": None}

    def test_pause_not_active_contact_closed(self):
        """Test pause attribute when contact sensor exists but is closed."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()

        # Contact sensor closed and not paused
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = False
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = None
        thermostat._contact_sensor_handler = contact_handler
        thermostat._humidity_detector = None

        pause_attr = _build_pause_attribute(thermostat)

        assert pause_attr == {"active": False, "reason": None}

    def test_pause_active_contact(self):
        """Test pause attribute when contact sensor pause is active."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()

        # Contact sensor open and paused
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = True
        contact_handler.get_time_until_action.return_value = None
        thermostat._contact_sensor_handler = contact_handler
        thermostat._humidity_detector = None

        pause_attr = _build_pause_attribute(thermostat)

        assert pause_attr == {"active": True, "reason": "contact"}

    def test_pause_pending_contact(self):
        """Test pause attribute when contact is open but not paused yet (in delay)."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()

        # Contact sensor open but not yet paused (in countdown)
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = 120  # 2 minutes remaining
        thermostat._contact_sensor_handler = contact_handler
        thermostat._humidity_detector = None

        pause_attr = _build_pause_attribute(thermostat)

        assert pause_attr == {"active": False, "reason": None, "resume_in": 120}

    def test_pause_active_humidity(self):
        """Test pause attribute when humidity pause is active."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Humidity detector paused
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "paused"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector

        pause_attr = _build_pause_attribute(thermostat)

        assert pause_attr == {"active": True, "reason": "humidity"}

    def test_pause_humidity_stabilizing_with_countdown(self):
        """Test pause attribute when humidity is stabilizing with countdown."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Humidity detector stabilizing
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 180  # 3 minutes
        thermostat._humidity_detector = humidity_detector

        pause_attr = _build_pause_attribute(thermostat)

        assert pause_attr == {"active": True, "reason": "humidity", "resume_in": 180}

    def test_pause_contact_priority_over_humidity(self):
        """Test that contact sensor takes priority over humidity when both active."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()

        # Contact sensor paused
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = True
        contact_handler.get_time_until_action.return_value = None
        thermostat._contact_sensor_handler = contact_handler

        # Humidity detector also paused
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "paused"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector

        pause_attr = _build_pause_attribute(thermostat)

        # Contact should take priority
        assert pause_attr == {"active": True, "reason": "contact"}

    def test_pause_humidity_only_when_no_contact(self):
        """Test humidity detector works when no contact sensor configured."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_pause_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Humidity detector stabilizing with countdown
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 240  # 4 minutes
        thermostat._humidity_detector = humidity_detector

        pause_attr = _build_pause_attribute(thermostat)

        assert pause_attr == {"active": True, "reason": "humidity", "resume_in": 240}
