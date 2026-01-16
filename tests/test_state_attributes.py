"""Tests for state attribute building."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock

from custom_components.adaptive_thermostat.managers.state_attributes import (
    _compute_learning_status,
    _add_learning_status_attributes,
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
        assert attrs[ATTR_LAST_PID_ADJUSTMENT] is None

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
