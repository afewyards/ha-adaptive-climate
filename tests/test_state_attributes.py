"""Tests for state attribute building."""
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch
from abc import ABC
from enum import IntFlag

# ClimateEntity must use ABC to be compatible with RestoreEntity's ABCMeta
class MockClimateEntity(ABC):
    """Mock ClimateEntity base class."""
    pass

class MockClimateEntityFeature(IntFlag):
    """Mock ClimateEntityFeature enum."""
    TARGET_TEMPERATURE = 1

# Mock homeassistant.components.climate for HVACMode
mock_ha_climate = MagicMock()
# Create mock HVACMode enum
mock_hvac_mode = MagicMock()
mock_hvac_mode.HEAT = "heat"
mock_hvac_mode.COOL = "cool"
mock_ha_climate.HVACMode = mock_hvac_mode
mock_ha_climate.ClimateEntity = MockClimateEntity
mock_ha_climate.ClimateEntityFeature = MockClimateEntityFeature
sys.modules['homeassistant.components'] = MagicMock()
sys.modules['homeassistant.components.climate'] = mock_ha_climate

from custom_components.adaptive_thermostat.managers.state_attributes import (
    _compute_learning_status,
    _add_learning_status_attributes,
    _build_status_attribute,
    ATTR_LEARNING_STATUS,
    ATTR_CYCLES_COLLECTED,
    ATTR_CONVERGENCE_CONFIDENCE,
)
from custom_components.adaptive_thermostat.const import (
    MIN_CYCLES_FOR_LEARNING,
    MIN_CONVERGENCE_CYCLES_FOR_KE,
    HeatingType,
    AUTO_APPLY_THRESHOLDS,
)


class TestComputeLearningStatus:
    """Test _compute_learning_status helper function."""

    def test_idle_status_when_paused(self):
        """Test idle status when is_paused=True, regardless of other metrics."""
        # Should return idle even with high confidence and many cycles
        status = _compute_learning_status(
            cycle_count=20,
            convergence_confidence=0.99,
            heating_type=HeatingType.CONVECTOR,
            is_paused=True,
        )
        assert status == "idle"

    def test_idle_status_with_low_cycles_when_paused(self):
        """Test idle status when paused with low cycle count."""
        status = _compute_learning_status(
            cycle_count=2,
            convergence_confidence=0.0,
            heating_type=HeatingType.CONVECTOR,
            is_paused=True,
        )
        assert status == "idle"

    def test_collecting_status_insufficient_cycles(self):
        """Test collecting status when cycle count < MIN_CYCLES_FOR_LEARNING."""
        status = _compute_learning_status(
            cycle_count=3,
            convergence_confidence=0.0,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "collecting"

    def test_collecting_status_at_boundary(self):
        """Test collecting status at MIN_CYCLES_FOR_LEARNING - 1."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING - 1,
            convergence_confidence=0.8,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "collecting"

    def test_collecting_status_low_confidence(self):
        """Test collecting status when cycles >= MIN but confidence < threshold."""
        # CONVECTOR threshold is 0.60
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.3,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "collecting"

    def test_collecting_status_zero_confidence(self):
        """Test collecting status with zero confidence."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING + 5,
            convergence_confidence=0.0,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "collecting"

    def test_stable_status(self):
        """Test stable status when confidence >= heating-type threshold but < 95%."""
        # CONVECTOR threshold is 0.60
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.7,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "stable"

    def test_stable_status_at_boundary(self):
        """Test stable status at confidence == heating-type threshold."""
        # CONVECTOR threshold is 0.60
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING + 1,
            convergence_confidence=0.6,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "stable"

    def test_stable_status_high_confidence(self):
        """Test stable status with high confidence but below optimized threshold."""
        status = _compute_learning_status(
            cycle_count=15,
            convergence_confidence=0.90,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "stable"

    def test_optimized_status(self):
        """Test optimized status when confidence >= 95%."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.95,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "optimized"

    def test_optimized_status_at_boundary(self):
        """Test optimized status at confidence == 95% threshold."""
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING + 1,
            convergence_confidence=0.95,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "optimized"

    def test_optimized_status_very_high_confidence(self):
        """Test optimized status with very high confidence (100%)."""
        status = _compute_learning_status(
            cycle_count=20,
            convergence_confidence=1.0,
            heating_type=HeatingType.CONVECTOR,
        )
        assert status == "optimized"

    def test_floor_hydronic_threshold(self):
        """Test that floor_hydronic uses higher threshold (0.80)."""
        # Below floor_hydronic threshold (0.80) but above convector threshold (0.60)
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.70,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )
        assert status == "collecting"

        # At floor_hydronic threshold (0.80) - should be stable (not optimized yet)
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.80,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )
        assert status == "stable"

        # At optimized threshold (0.95) - should be optimized
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.95,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )
        assert status == "optimized"

    def test_radiator_threshold(self):
        """Test that radiator uses threshold (0.70)."""
        # Below radiator threshold
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.65,
            heating_type=HeatingType.RADIATOR,
        )
        assert status == "collecting"

        # At radiator threshold - should be stable (not optimized yet)
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.70,
            heating_type=HeatingType.RADIATOR,
        )
        assert status == "stable"

        # At optimized threshold (0.95) - should be optimized
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.95,
            heating_type=HeatingType.RADIATOR,
        )
        assert status == "optimized"

    def test_forced_air_threshold(self):
        """Test that forced_air uses threshold (0.60)."""
        # Below forced_air threshold
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.55,
            heating_type=HeatingType.FORCED_AIR,
        )
        assert status == "collecting"

        # At forced_air threshold - should be stable (not optimized yet)
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.60,
            heating_type=HeatingType.FORCED_AIR,
        )
        assert status == "stable"

        # At optimized threshold (0.95) - should be optimized
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.95,
            heating_type=HeatingType.FORCED_AIR,
        )
        assert status == "optimized"

    def test_unknown_heating_type_uses_convector_default(self):
        """Test that unknown heating type defaults to convector threshold."""
        # Unknown heating type should use convector threshold (0.60)
        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.60,
            heating_type="unknown_type",
        )
        assert status == "stable"

        status = _compute_learning_status(
            cycle_count=MIN_CYCLES_FOR_LEARNING,
            convergence_confidence=0.55,
            heating_type="unknown_type",
        )
        assert status == "collecting"


class TestAddLearningStatusAttributes:
    """Test _add_learning_status_attributes function."""

    def test_no_coordinator(self):
        """Test that function handles missing coordinator gracefully."""
        thermostat = MagicMock()
        thermostat._coordinator = None
        thermostat.hass.data.get.return_value = {}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should not add any attributes
        assert len(attrs) == 0

    def test_no_adaptive_learner(self):
        """Test that function handles missing adaptive learner gracefully."""
        thermostat = MagicMock()
        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": None,
            "cycle_tracker": MagicMock(),
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        thermostat._coordinator = coordinator
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should not add any attributes
        assert len(attrs) == 0

    def test_no_cycle_tracker(self):
        """Test that function handles missing cycle tracker gracefully."""
        thermostat = MagicMock()
        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": MagicMock(),
            "cycle_tracker": None,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        thermostat._coordinator = coordinator
        thermostat.hass.data.get.return_value = {"coordinator": coordinator}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should not add any attributes
        assert len(attrs) == 0

    def test_collecting_state(self):
        """Test attributes in collecting state."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 2
        adaptive_learner.get_convergence_confidence.return_value = 0.0
        adaptive_learner.get_last_adjustment_time.return_value = None
        adaptive_learner.get_pid_history.return_value = []

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        # Set _coordinator directly on mock thermostat (bypassing the @property)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None
        # Set pause conditions to None (not paused)
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        # Enable debug mode to get cycles_collected and convergence_confidence_pct
        thermostat.hass.data = {"adaptive_thermostat": {"debug": True}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "collecting"
        assert attrs[ATTR_CYCLES_COLLECTED] == 2
        assert attrs[ATTR_CONVERGENCE_CONFIDENCE] == 0

    def test_stable_state_with_heating_cycle(self):
        """Test attributes in stable state with heating cycle."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 8
        adaptive_learner.get_convergence_confidence.return_value = 0.6
        adaptive_learner.get_last_adjustment_time.return_value = None
        adaptive_learner.get_pid_history.return_value = []

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "heating"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        # Set _coordinator directly on mock thermostat (bypassing the @property)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None
        # Set pause conditions to None (not paused)
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        # Enable debug mode to get cycles_collected and convergence_confidence_pct
        thermostat.hass.data = {"adaptive_thermostat": {"debug": True}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "stable"
        assert attrs[ATTR_CYCLES_COLLECTED] == 8
        assert attrs[ATTR_CONVERGENCE_CONFIDENCE] == 60  # 0.6 * 100

    def test_optimized_state_high_confidence(self):
        """Test attributes in optimized state with 95%+ confidence."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 15
        adaptive_learner.get_convergence_confidence.return_value = 0.95

        # Set last adjustment time
        last_adjustment = datetime(2024, 1, 15, 14, 30, 0)
        adaptive_learner.get_last_adjustment_time.return_value = last_adjustment
        adaptive_learner.get_pid_history.return_value = []

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        # Set _coordinator directly on mock thermostat (bypassing the @property)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None
        # Set pause conditions to None (not paused)
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        # Enable debug mode to get cycles_collected and convergence_confidence_pct
        thermostat.hass.data = {"adaptive_thermostat": {"debug": True}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "optimized"
        assert attrs[ATTR_CYCLES_COLLECTED] == 15
        assert attrs[ATTR_CONVERGENCE_CONFIDENCE] == 95  # 0.95 * 100

    def test_settling_state(self):
        """Test attributes with settling cycle state (removed - cycle state no longer exposed)."""
        pass

    def test_interrupted_cycle_setpoint_change(self):
        """Test attributes with interrupted cycle (removed - interruption reason no longer exposed)."""
        pass

    def test_interrupted_cycle_mode_change(self):
        """Test attributes with interrupted cycle (removed - interruption reason no longer exposed)."""
        pass

    def test_interrupted_cycle_contact_sensor(self):
        """Test attributes with interrupted cycle (removed - interruption reason no longer exposed)."""
        pass

    def test_convergence_confidence_percentage_conversion(self):
        """Test that convergence confidence is correctly converted to percentage."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = MIN_CYCLES_FOR_LEARNING
        adaptive_learner.get_consecutive_converged_cycles.return_value = 0
        adaptive_learner.get_last_adjustment_time.return_value = None
        adaptive_learner.get_pid_history.return_value = []

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"
        cycle_tracker.get_last_interruption_reason.return_value = None

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        # Set _coordinator directly on mock thermostat (bypassing the @property)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None
        # Set pause conditions to None (not paused)
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        # Enable debug mode to get convergence_confidence_pct
        thermostat.hass.data = {"adaptive_thermostat": {"debug": True}}

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
        """Test that last adjustment timestamp is formatted (removed - timestamp no longer exposed)."""
        pass

    def test_idle_status_with_learning_grace_period(self):
        """Test idle status when learning grace period is active."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 10
        adaptive_learner.get_convergence_confidence.return_value = 0.8

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None

        # Learning grace period active
        night_setback_controller = MagicMock()
        night_setback_controller.in_learning_grace_period = True
        thermostat._night_setback_controller = night_setback_controller
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None

        thermostat.hass.data = {"adaptive_thermostat": {"debug": False}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "idle"

    def test_idle_status_with_contact_sensor_open(self):
        """Test idle status when contact sensor is open."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 10
        adaptive_learner.get_convergence_confidence.return_value = 0.8

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None
        thermostat._night_setback_controller = None

        # Contact sensor open
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        thermostat._contact_sensor_handler = contact_handler
        thermostat._humidity_detector = None

        thermostat.hass.data = {"adaptive_thermostat": {"debug": False}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "idle"

    def test_idle_status_with_humidity_spike(self):
        """Test idle status when humidity spike is active."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 10
        adaptive_learner.get_convergence_confidence.return_value = 0.8

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None

        # Humidity detector paused
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        thermostat._humidity_detector = humidity_detector

        thermostat.hass.data = {"adaptive_thermostat": {"debug": False}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "idle"

    def test_stable_status_when_not_paused(self):
        """Test stable status when no pause conditions are active."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 10
        adaptive_learner.get_convergence_confidence.return_value = 0.7  # Above 0.6 threshold

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None

        # No pause conditions
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None

        thermostat.hass.data = {"adaptive_thermostat": {"debug": False}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        assert attrs[ATTR_LEARNING_STATUS] == "stable"

    def test_learning_grace_takes_priority(self):
        """Test that learning grace period is checked first (before contact/humidity)."""
        thermostat = MagicMock()
        thermostat._heating_type = HeatingType.CONVECTOR
        adaptive_learner = MagicMock()
        adaptive_learner.get_cycle_count.return_value = 10
        adaptive_learner.get_convergence_confidence.return_value = 0.8

        cycle_tracker = MagicMock()
        cycle_tracker.get_state_name.return_value = "idle"

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": thermostat.entity_id,
            "adaptive_learner": adaptive_learner,
            "cycle_tracker": cycle_tracker,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)
        thermostat._coordinator = coordinator
        thermostat._gains_manager = None

        # Learning grace active (should take priority)
        night_setback_controller = MagicMock()
        night_setback_controller.in_learning_grace_period = True
        thermostat._night_setback_controller = night_setback_controller

        # Contact sensor NOT open (shouldn't matter since learning grace is active)
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = False
        thermostat._contact_sensor_handler = contact_handler

        # Humidity detector NOT paused
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = False
        thermostat._humidity_detector = humidity_detector

        thermostat.hass.data = {"adaptive_thermostat": {"debug": False}}
        attrs = {}

        _add_learning_status_attributes(thermostat, attrs)

        # Should be idle due to learning grace
        assert attrs[ATTR_LEARNING_STATUS] == "idle"

        # Verify that contact and humidity checks were not called (short-circuit)
        contact_handler.is_any_contact_open.assert_not_called()
        humidity_detector.should_pause.assert_not_called()


class TestDutyAccumulatorAttributes:
    """Tests for duty accumulator state attributes."""

    def test_accumulator_not_in_state_attributes_without_debug(self):
        """Test duty_accumulator_pct is not present without debug mode."""
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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat._coordinator = None  # No coordinator, so learning status won't be added
        thermostat.hass = MagicMock()
        thermostat.hass.data = {"adaptive_thermostat": {"debug": False}}

        attrs = build_state_attributes(thermostat)

        # duty_accumulator_pct is debug-only
        assert "duty_accumulator" not in attrs
        assert "duty_accumulator_pct" not in attrs

    def test_accumulator_in_state_attributes_with_debug(self):
        """Test duty_accumulator_pct present in debug mode."""
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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat._coordinator = None  # No coordinator, so learning status won't be added
        thermostat.hass = MagicMock()
        thermostat.hass.data = {"adaptive_thermostat": {"debug": True}}

        attrs = build_state_attributes(thermostat)

        # duty_accumulator_pct present in debug mode
        assert "duty_accumulator" not in attrs
        assert "duty_accumulator_pct" in attrs

    def test_accumulator_pct_attribute(self):
        """Test duty_accumulator_pct shows percentage of threshold in debug mode."""
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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat._coordinator = None  # No coordinator, so learning status won't be added
        thermostat.hass.data = {"adaptive_thermostat": {"debug": True}}

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
        """Test that kp, ki, kd, ke, pid_mode are NOT in state attributes.

        These values are now stored authoritatively in pid_history and restored
        via gains_manager.restore_from_state(), so they don't need to be exposed
        as top-level state attributes.
        """
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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat._coordinator = None  # No coordinator
        thermostat.hass.data = {}
        thermostat._coordinator = None  # No coordinator, so learning status won't be added

        attrs = build_state_attributes(thermostat)

        # kp, ki, kd, ke, pid_mode should NOT be in state attributes
        # They are now stored in pid_history
        assert "kp" not in attrs
        assert "ki" not in attrs
        assert "kd" not in attrs
        assert "ke" not in attrs
        assert "pid_mode" not in attrs
        # pid_i renamed to integral and should be present
        assert "pid_i" not in attrs
        assert "integral" in attrs
        assert attrs["integral"] == 5.0

    def test_heating_convergence_confidence_attribute(self):
        """Test heating_convergence_confidence attribute removed."""
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
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 100
        thermostat._heater_controller.cooler_cycle_count = 50
        thermostat._heater_controller.duty_accumulator_seconds = 120.5
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._ke_learner = None
        thermostat._heater_control_failed = False
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat._coordinator = None  # No coordinator
        thermostat.hass.data = {}
        thermostat._coordinator = None  # No coordinator, so learning status won't be added

        attrs = build_state_attributes(thermostat)

        # Per-mode convergence confidence attributes removed
        assert "heating_convergence_confidence" not in attrs
        assert "cooling_convergence_confidence" not in attrs

    def test_cooling_convergence_confidence_attribute(self):
        """Test cooling_convergence_confidence attribute removed."""
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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        # Setup coordinator with adaptive learner
        adaptive_learner = MagicMock()
        adaptive_learner.get_convergence_confidence.return_value = 0.85

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": "climate.test_zone",
            "adaptive_learner": adaptive_learner,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)

        thermostat._coordinator = coordinator
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        # Per-mode convergence confidence attributes removed
        assert "cooling_convergence_confidence" not in attrs

    def test_both_mode_convergence_attributes(self):
        """Test both heating and cooling convergence attributes removed."""
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
        thermostat._humidity_detector = None
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
        zone_data = {
            "climate_entity_id": "climate.test_zone",
            "adaptive_learner": adaptive_learner,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)

        thermostat._coordinator = coordinator
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        # Per-mode convergence confidence attributes removed
        assert "heating_convergence_confidence" not in attrs
        assert "cooling_convergence_confidence" not in attrs

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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.hass = MagicMock()
        thermostat._coordinator = None  # No coordinator
        thermostat.hass.data = {}
        thermostat._coordinator = None  # No coordinator, so learning status won't be added

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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": "climate.test_zone",
            "adaptive_learner": None,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)

        thermostat._coordinator = coordinator
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
        thermostat._humidity_detector = None
        thermostat.in_learning_grace_period = False
        thermostat.entity_id = "climate.test_zone"

        # Setup coordinator with adaptive learner
        adaptive_learner = MagicMock()
        # Return value that needs rounding
        adaptive_learner.get_convergence_confidence.return_value = 0.7349

        coordinator = MagicMock()
        zone_data = {
            "climate_entity_id": "climate.test_zone",
            "adaptive_learner": adaptive_learner,
        }
        coordinator.get_zone_by_climate_entity.return_value = ("zone1", zone_data)

        thermostat._coordinator = coordinator
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        # Per-mode convergence confidence attributes removed
        assert "heating_convergence_confidence" not in attrs
        assert "cooling_convergence_confidence" not in attrs


class TestHumidityDetectionAttributes:
    """Tests for humidity detection state attributes."""

    def test_status_not_active_when_no_detector(self):
        """Test that status attribute shows inactive when detector is None."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat._night_setback_controller = None
        thermostat.hvac_mode = "off"

        status_attr = _build_status_attribute(thermostat)

        # Status should be idle with no conditions
        assert status_attr["state"] == "idle"
        assert status_attr["conditions"] == []
        # humidity_detection_state and humidity_resume_in should not be in status attribute
        assert "humidity_detection_state" not in status_attr
        assert "humidity_resume_in" not in status_attr

    def test_status_not_active_humidity_normal_state(self):
        """Test status attribute when humidity detector is in normal state."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None
        thermostat._night_setback_controller = None

        # Setup humidity detector in normal state
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = False
        humidity_detector.get_state.return_value = "normal"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector
        thermostat._heater_controller = None
        thermostat.hvac_mode = "off"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        # Status should be idle with no conditions
        assert status_attr["state"] == "idle"
        assert status_attr["conditions"] == []

    def test_status_active_humidity_paused_state(self):
        """Test status attribute when humidity detector is in paused state."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Setup humidity detector in paused state
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "paused"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        # Status should be paused with humidity_spike condition
        assert status_attr["state"] == "paused"
        assert "humidity_spike" in status_attr["conditions"]
        assert "resume_at" not in status_attr

    def test_status_active_humidity_stabilizing_with_countdown(self):
        """Test status attribute when humidity detector is in stabilizing state with countdown."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Setup humidity detector in stabilizing state with resume time
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 180  # 3 minutes
        thermostat._humidity_detector = humidity_detector
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        # Status should be paused with humidity_spike condition and resume_at
        assert status_attr["state"] == "paused"
        assert "humidity_spike" in status_attr["conditions"]
        assert "resume_at" in status_attr

    def test_status_active_humidity_stabilizing_with_zero_resume_time(self):
        """Test status attribute when humidity is stabilizing with 0 resume time (about to exit)."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Setup humidity detector in stabilizing state with 0 resume time (about to exit)
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 0
        thermostat._humidity_detector = humidity_detector
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        # Status should be paused with humidity_spike condition, but no resume_at since it's 0
        assert status_attr["state"] == "paused"
        assert "humidity_spike" in status_attr["conditions"]
        # 0 should not be included in resume_at
        assert "resume_at" not in status_attr

    def test_debug_attributes_still_present(self):
        """Test that humidity_detection_state and humidity_resume_in are still available for debugging."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _add_humidity_detection_attributes,
        )

        thermostat = MagicMock()

        # Setup humidity detector in stabilizing state with resume time
        humidity_detector = MagicMock()
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 180  # 3 minutes
        thermostat._humidity_detector = humidity_detector

        attrs = {}
        _add_humidity_detection_attributes(thermostat, attrs)

        # Debug attributes should be present
        assert attrs["humidity_detection_state"] == "stabilizing"
        assert attrs["humidity_resume_in"] == 180


class TestStatusAttribute:
    """Tests for consolidated status attribute."""

    def test_status_not_active_no_detectors(self):
        """Test status attribute when no detectors configured."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "off"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        assert status_attr == {"state": "idle", "conditions": []}

    def test_status_not_active_contact_closed(self):
        """Test status attribute when contact sensor exists but is closed."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()

        # Contact sensor closed and not paused
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = False
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = None
        thermostat._contact_sensor_handler = contact_handler
        thermostat._humidity_detector = None
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "off"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        assert status_attr == {"state": "idle", "conditions": []}

    def test_status_active_contact(self):
        """Test status attribute when contact sensor status is active."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )
        from custom_components.adaptive_thermostat.adaptive.contact_sensors import ContactAction

        thermostat = MagicMock()

        # Contact sensor open and paused
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE
        contact_handler.get_time_until_action.return_value = None
        thermostat._contact_sensor_handler = contact_handler
        thermostat._humidity_detector = None
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat._preheat_active = False
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        assert status_attr == {"state": "paused", "conditions": ["contact_open"]}

    def test_status_pending_contact(self):
        """Test status attribute when contact is open but not paused yet (in delay)."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()

        # Contact sensor open but not yet paused (in countdown)
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = False
        contact_handler.get_time_until_action.return_value = 120  # 2 minutes remaining
        thermostat._contact_sensor_handler = contact_handler
        thermostat._humidity_detector = None
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        # Contact is open but not paused yet - should show contact_open condition but idle state
        # Resume_at should be in ISO8601 format
        assert status_attr["state"] == "idle"
        assert "contact_open" in status_attr["conditions"]
        assert "resume_at" in status_attr

    def test_status_active_humidity(self):
        """Test status attribute when humidity status is active."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Humidity detector paused
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "paused"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        assert status_attr == {"state": "paused", "conditions": ["humidity_spike"]}

    def test_status_humidity_stabilizing_with_countdown(self):
        """Test status attribute when humidity is stabilizing with countdown."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Humidity detector stabilizing
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 180  # 3 minutes
        thermostat._humidity_detector = humidity_detector
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        assert status_attr["state"] == "paused"
        assert "humidity_spike" in status_attr["conditions"]
        assert "resume_at" in status_attr

    def test_status_contact_priority_over_humidity(self):
        """Test that contact sensor takes priority over humidity when both active."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )
        from custom_components.adaptive_thermostat.adaptive.contact_sensors import ContactAction

        thermostat = MagicMock()

        # Contact sensor paused
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE
        contact_handler.get_time_until_action.return_value = None
        thermostat._contact_sensor_handler = contact_handler

        # Humidity detector also paused
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "paused"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        # Contact should take priority - both conditions shown but contact first
        assert status_attr["state"] == "paused"
        assert status_attr["conditions"] == ["contact_open", "humidity_spike"]

    def test_status_humidity_only_when_no_contact(self):
        """Test humidity detector works when no contact sensor configured."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            _build_status_attribute,
        )

        thermostat = MagicMock()
        thermostat._contact_sensor_handler = None

        # Humidity detector stabilizing with countdown
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 240  # 4 minutes
        thermostat._humidity_detector = humidity_detector
        thermostat._night_setback_controller = None
        thermostat._heater_controller = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        status_attr = _build_status_attribute(thermostat)

        assert status_attr["state"] == "paused"
        assert "humidity_spike" in status_attr["conditions"]
        assert "resume_at" in status_attr


class TestStatusAttributeIntegration:
    """Integration tests for status attribute with full thermostat scenarios."""

    def test_idle_thermostat_status(self):
        """Test status when thermostat is idle (at setpoint, not heating)."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        thermostat = MagicMock()
        # Setup minimal thermostat in idle state
        thermostat._away_temp = 18.0
        thermostat._eco_temp = 19.0
        thermostat._boost_temp = 24.0
        thermostat._comfort_temp = 21.0
        thermostat._home_temp = 20.0
        thermostat._sleep_temp = 18.0
        thermostat._activity_temp = 20.0
        thermostat._control_output = 0.0  # Not calling for heat
        thermostat._kp = 20.0
        thermostat._ki = 0.01
        thermostat._kd = 100.0
        thermostat._ke = 0.5
        thermostat.pid_mode = "auto"
        thermostat.pid_control_i = 0.0
        thermostat._pid_controller = MagicMock()
        thermostat._pid_controller.outdoor_temp_lagged = 5.0
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 10
        thermostat._heater_controller.cooler_cycle_count = 0
        thermostat._heater_controller.duty_accumulator_seconds = 0.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._heater_controller.heater_on = False
        thermostat._heater_controller.cooler_on = False
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat._coordinator = None
        thermostat.hvac_mode = "heat"  # Mode is heat but not actively heating
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "status" in attrs
        assert attrs["status"]["state"] == "idle"
        assert attrs["status"]["conditions"] == []

    def test_heating_thermostat_status(self):
        """Test status when thermostat is actively heating."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        thermostat = MagicMock()
        # Setup thermostat actively heating
        thermostat._away_temp = 18.0
        thermostat._eco_temp = 19.0
        thermostat._boost_temp = 24.0
        thermostat._comfort_temp = 21.0
        thermostat._home_temp = 20.0
        thermostat._sleep_temp = 18.0
        thermostat._activity_temp = 20.0
        thermostat._control_output = 75.0  # Calling for heat
        thermostat._kp = 20.0
        thermostat._ki = 0.01
        thermostat._kd = 100.0
        thermostat._ke = 0.5
        thermostat.pid_mode = "auto"
        thermostat.pid_control_i = 5.0
        thermostat._pid_controller = MagicMock()
        thermostat._pid_controller.outdoor_temp_lagged = 5.0
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 10
        thermostat._heater_controller.cooler_cycle_count = 0
        thermostat._heater_controller.duty_accumulator_seconds = 150.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._heater_controller.heater_on = True  # Heater actively on
        thermostat._heater_controller.cooler_on = False
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat._coordinator = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "status" in attrs
        assert attrs["status"]["state"] == "heating"
        assert attrs["status"]["conditions"] == []

    def test_paused_by_contact_sensor_status(self):
        """Test status when paused by open contact sensor."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )
        from custom_components.adaptive_thermostat.adaptive.contact_sensors import ContactAction

        thermostat = MagicMock()
        # Setup thermostat paused by contact sensor
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
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 10
        thermostat._heater_controller.cooler_cycle_count = 0
        thermostat._heater_controller.duty_accumulator_seconds = 0.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._heater_controller.heater_on = False  # Paused, so not heating
        thermostat._heater_controller.cooler_on = False

        # Contact sensor open and pausing
        contact_handler = MagicMock()
        contact_handler.is_any_contact_open.return_value = True
        contact_handler.should_take_action.return_value = True
        contact_handler.get_action.return_value = ContactAction.PAUSE
        contact_handler.get_time_until_action.return_value = None
        thermostat._contact_sensor_handler = contact_handler

        thermostat._humidity_detector = None
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._coordinator = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "status" in attrs
        assert attrs["status"]["state"] == "paused"
        assert "contact_open" in attrs["status"]["conditions"]
        # No resume_at since contact sensors don't have timed resume
        assert "resume_at" not in attrs["status"]

    def test_paused_by_humidity_status(self):
        """Test status when paused by humidity spike."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        thermostat = MagicMock()
        # Setup thermostat paused by humidity
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
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 10
        thermostat._heater_controller.cooler_cycle_count = 0
        thermostat._heater_controller.duty_accumulator_seconds = 0.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._heater_controller.heater_on = False
        thermostat._heater_controller.cooler_on = False

        # Humidity detector paused
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "paused"
        humidity_detector.get_time_until_resume.return_value = None
        thermostat._humidity_detector = humidity_detector

        thermostat._contact_sensor_handler = None
        thermostat._night_setback = None
        thermostat._night_setback_config = None
        thermostat._night_setback_controller = None
        thermostat._coordinator = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "status" in attrs
        assert attrs["status"]["state"] == "paused"
        assert "humidity_spike" in attrs["status"]["conditions"]

    def test_night_setback_status(self):
        """Test status during night setback period."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        # Mock dt_util.now() to return a real datetime
        mock_now = datetime(2024, 1, 15, 6, 30, 0)
        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.now', return_value=mock_now):
            thermostat = MagicMock()
            # Setup thermostat in night setback
            thermostat._away_temp = 18.0
            thermostat._eco_temp = 19.0
            thermostat._boost_temp = 24.0
            thermostat._comfort_temp = 21.0
            thermostat._home_temp = 20.0
            thermostat._sleep_temp = 18.0
            thermostat._activity_temp = 20.0
            thermostat._control_output = 20.0
            thermostat._kp = 20.0
            thermostat._ki = 0.01
            thermostat._kd = 100.0
            thermostat._ke = 0.5
            thermostat.pid_mode = "auto"
            thermostat.pid_control_i = 2.0
            thermostat._pid_controller = MagicMock()
            thermostat._pid_controller.outdoor_temp_lagged = 5.0
            thermostat._heater_controller = MagicMock()
            thermostat._heater_controller.heater_cycle_count = 10
            thermostat._heater_controller.cooler_cycle_count = 0
            thermostat._heater_controller.duty_accumulator_seconds = 50.0
            thermostat._heater_controller.min_on_cycle_duration = 300.0
            thermostat._heater_controller.heater_on = False
            thermostat._heater_controller.cooler_on = False

            # Night setback controller active
            night_setback_controller = MagicMock()
            night_setback_controller.calculate_night_setback_adjustment.return_value = (
                -3.0,  # adjustment
                True,   # in_night_period
                {"night_setback_delta": 3.0, "night_setback_end": "07:00"}
            )
            night_setback_controller.in_learning_grace_period = False
            thermostat._night_setback_controller = night_setback_controller

            thermostat._contact_sensor_handler = None
            thermostat._humidity_detector = None
            thermostat._coordinator = None
            thermostat.hvac_mode = "heat"
            thermostat.hass = MagicMock()
            thermostat.hass.data = {}

            attrs = build_state_attributes(thermostat)

            assert "status" in attrs
            assert "night_setback" in attrs["status"]["conditions"]
            assert attrs["status"]["setback_delta"] == 3.0
            # setback_end should be ISO8601 format
            assert "setback_end" in attrs["status"]
            assert "T" in attrs["status"]["setback_end"]  # ISO8601 has T separator

    def test_multiple_conditions_status(self):
        """Test status with multiple active conditions."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        # Mock dt_util.now() to return a real datetime
        mock_now = datetime(2024, 1, 15, 6, 30, 0)
        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.now', return_value=mock_now):
            thermostat = MagicMock()
            # Setup thermostat with multiple conditions
            thermostat._away_temp = 18.0
            thermostat._eco_temp = 19.0
            thermostat._boost_temp = 24.0
            thermostat._comfort_temp = 21.0
            thermostat._home_temp = 20.0
            thermostat._sleep_temp = 18.0
            thermostat._activity_temp = 20.0
            thermostat._control_output = 20.0
            thermostat._kp = 20.0
            thermostat._ki = 0.01
            thermostat._kd = 100.0
            thermostat._ke = 0.5
            thermostat.pid_mode = "auto"
            thermostat.pid_control_i = 2.0
            thermostat._pid_controller = MagicMock()
            thermostat._pid_controller.outdoor_temp_lagged = 5.0
            thermostat._heater_controller = MagicMock()
            thermostat._heater_controller.heater_cycle_count = 10
            thermostat._heater_controller.cooler_cycle_count = 0
            thermostat._heater_controller.duty_accumulator_seconds = 0.0
            thermostat._heater_controller.min_on_cycle_duration = 300.0
            thermostat._heater_controller.heater_on = False
            thermostat._heater_controller.cooler_on = False

            # Night setback AND learning grace period active
            night_setback_controller = MagicMock()
            night_setback_controller.calculate_night_setback_adjustment.return_value = (
                -3.0,
                True,
                {"night_setback_delta": 3.0, "night_setback_end": "07:00"}
            )
            night_setback_controller.in_learning_grace_period = True
            thermostat._night_setback_controller = night_setback_controller

            thermostat._contact_sensor_handler = None
            thermostat._humidity_detector = None
            thermostat._coordinator = None
            thermostat.hvac_mode = "heat"
            thermostat.hass = MagicMock()
            thermostat.hass.data = {}

            attrs = build_state_attributes(thermostat)

            assert "status" in attrs
            assert "night_setback" in attrs["status"]["conditions"]
            assert "learning_grace" in attrs["status"]["conditions"]
            # Both conditions should be present
            assert len(attrs["status"]["conditions"]) == 2

    def test_preheating_status(self):
        """Test status when preheating before night setback ends."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        # Mock dt_util.now() to return a real datetime
        mock_now = datetime(2024, 1, 15, 6, 30, 0)
        with patch('custom_components.adaptive_thermostat.managers.status_manager.dt_util.now', return_value=mock_now):
            thermostat = MagicMock()
            # Setup thermostat in preheat mode
            thermostat._away_temp = 18.0
            thermostat._eco_temp = 19.0
            thermostat._boost_temp = 24.0
            thermostat._comfort_temp = 21.0
            thermostat._home_temp = 20.0
            thermostat._sleep_temp = 18.0
            thermostat._activity_temp = 20.0
            thermostat._control_output = 60.0
            thermostat._kp = 20.0
            thermostat._ki = 0.01
            thermostat._kd = 100.0
            thermostat._ke = 0.5
            thermostat.pid_mode = "auto"
            thermostat.pid_control_i = 3.0
            thermostat._pid_controller = MagicMock()
            thermostat._pid_controller.outdoor_temp_lagged = 5.0
            thermostat._heater_controller = MagicMock()
            thermostat._heater_controller.heater_cycle_count = 10
            thermostat._heater_controller.cooler_cycle_count = 0
            thermostat._heater_controller.duty_accumulator_seconds = 100.0
            thermostat._heater_controller.min_on_cycle_duration = 300.0
            thermostat._heater_controller.heater_on = True
            thermostat._heater_controller.cooler_on = False

            # Preheat active
            thermostat._preheat_active = True

            # Night setback controller with preheat
            night_setback_controller = MagicMock()
            night_setback_controller.calculate_night_setback_adjustment.return_value = (
                -2.0,  # Still in setback but preheating
                True,
                {"night_setback_delta": 2.0, "night_setback_end": "07:00"}
            )
            night_setback_controller.in_learning_grace_period = False
            thermostat._night_setback_controller = night_setback_controller

            thermostat._contact_sensor_handler = None
            thermostat._humidity_detector = None
            thermostat._coordinator = None
            thermostat.hvac_mode = "heat"
            thermostat.hass = MagicMock()
            thermostat.hass.data = {}

            attrs = build_state_attributes(thermostat)

            assert "status" in attrs
            assert attrs["status"]["state"] == "preheating"
            # Night setback condition should still be present
            assert "night_setback" in attrs["status"]["conditions"]

    def test_settling_status(self):
        """Test status during settling phase after heating cycle."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        thermostat = MagicMock()
        # Setup thermostat in settling phase
        thermostat._away_temp = 18.0
        thermostat._eco_temp = 19.0
        thermostat._boost_temp = 24.0
        thermostat._comfort_temp = 21.0
        thermostat._home_temp = 20.0
        thermostat._sleep_temp = 18.0
        thermostat._activity_temp = 20.0
        thermostat._control_output = 0.0  # No longer calling for heat
        thermostat._kp = 20.0
        thermostat._ki = 0.01
        thermostat._kd = 100.0
        thermostat._ke = 0.5
        thermostat.pid_mode = "auto"
        thermostat.pid_control_i = 0.0
        thermostat._pid_controller = MagicMock()
        thermostat._pid_controller.outdoor_temp_lagged = 5.0
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 10
        thermostat._heater_controller.cooler_cycle_count = 0
        thermostat._heater_controller.duty_accumulator_seconds = 0.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._heater_controller.heater_on = False
        thermostat._heater_controller.cooler_on = False

        # Cycle tracker in settling state
        thermostat._cycle_tracker = MagicMock()
        thermostat._cycle_tracker.get_state_name.return_value = "settling"

        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat._night_setback_controller = None
        thermostat._coordinator = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "status" in attrs
        assert attrs["status"]["state"] == "settling"
        assert attrs["status"]["conditions"] == []

    def test_status_in_extra_state_attributes(self):
        """Test that status appears correctly in build_state_attributes."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        thermostat = MagicMock()
        # Setup minimal thermostat
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
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 10
        thermostat._heater_controller.cooler_cycle_count = 0
        thermostat._heater_controller.duty_accumulator_seconds = 0.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._heater_controller.heater_on = True
        thermostat._heater_controller.cooler_on = False
        thermostat._contact_sensor_handler = None
        thermostat._humidity_detector = None
        thermostat._night_setback_controller = None
        thermostat._coordinator = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        # Verify status key exists and has correct structure
        assert "status" in attrs
        assert isinstance(attrs["status"], dict)
        assert "state" in attrs["status"]
        assert "conditions" in attrs["status"]
        assert isinstance(attrs["status"]["conditions"], list)

    def test_resume_at_is_iso8601_format(self):
        """Test that resume_at timestamps are in ISO8601 format."""
        from custom_components.adaptive_thermostat.managers.state_attributes import (
            build_state_attributes,
        )

        thermostat = MagicMock()
        # Setup thermostat with humidity pause and resume time
        thermostat._away_temp = 18.0
        thermostat._eco_temp = 19.0
        thermostat._boost_temp = 24.0
        thermostat._comfort_temp = 21.0
        thermostat._home_temp = 20.0
        thermostat._sleep_temp = 18.0
        thermostat._activity_temp = 20.0
        thermostat._control_output = 0.0
        thermostat._kp = 20.0
        thermostat._ki = 0.01
        thermostat._kd = 100.0
        thermostat._ke = 0.5
        thermostat.pid_mode = "auto"
        thermostat.pid_control_i = 0.0
        thermostat._pid_controller = MagicMock()
        thermostat._pid_controller.outdoor_temp_lagged = 5.0
        thermostat._heater_controller = MagicMock()
        thermostat._heater_controller.heater_cycle_count = 10
        thermostat._heater_controller.cooler_cycle_count = 0
        thermostat._heater_controller.duty_accumulator_seconds = 0.0
        thermostat._heater_controller.min_on_cycle_duration = 300.0
        thermostat._heater_controller.heater_on = False
        thermostat._heater_controller.cooler_on = False

        # Humidity detector with resume time
        humidity_detector = MagicMock()
        humidity_detector.should_pause.return_value = True
        humidity_detector.get_state.return_value = "stabilizing"
        humidity_detector.get_time_until_resume.return_value = 300  # 5 minutes
        thermostat._humidity_detector = humidity_detector

        thermostat._contact_sensor_handler = None
        thermostat._night_setback_controller = None
        thermostat._coordinator = None
        thermostat.hvac_mode = "heat"
        thermostat.hass = MagicMock()
        thermostat.hass.data = {}

        attrs = build_state_attributes(thermostat)

        assert "status" in attrs
        assert "resume_at" in attrs["status"]
        # Verify ISO8601 format (contains T separator)
        resume_at = attrs["status"]["resume_at"]
        assert "T" in resume_at
        # Verify it's a valid timestamp format (YYYY-MM-DDTHH:MM:SS)
        from datetime import datetime
        try:
            # Try parsing as ISO8601
            datetime.fromisoformat(resume_at)
        except ValueError:
            pytest.fail(f"resume_at is not valid ISO8601: {resume_at}")
