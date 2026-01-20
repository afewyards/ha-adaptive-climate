"""Tests for ControlOutputManager coupling compensation."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from custom_components.adaptive_thermostat.managers.control_output import (
    ControlOutputManager,
)
from custom_components.adaptive_thermostat.adaptive.thermal_coupling import (
    CouplingCoefficient,
    graduated_confidence,
)
from custom_components.adaptive_thermostat.const import (
    HEATING_TYPE_FLOOR_HYDRONIC,
    HEATING_TYPE_RADIATOR,
    HEATING_TYPE_CONVECTOR,
    HEATING_TYPE_FORCED_AIR,
    MAX_COUPLING_COMPENSATION,
)


class MockHVACMode:
    """Mock HVACMode for testing."""
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"
    HEAT_COOL = "heat_cool"


# Alias for convenience
HVACMode = MockHVACMode


# ============================================================================
# Coupling Compensation Tests
# ============================================================================


class TestCouplingCompensation:
    """Tests for thermal coupling compensation calculation."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock thermostat
        self.thermostat = Mock()
        self.thermostat.entity_id = "climate.living_room"
        self.thermostat.hass = Mock()
        self.thermostat._heating_type = HEATING_TYPE_CONVECTOR
        self.thermostat._hvac_mode = HVACMode.HEAT

        # Create mock PID controller
        self.pid_controller = Mock()

        # Create mock heater controller
        self.heater_controller = Mock()

        # Create mock coordinator
        self.coordinator = Mock()
        self.thermostat.hass.data = {
            "adaptive_thermostat": {
                "coordinator": self.coordinator
            }
        }

        # Setup thermal coupling learner
        self.coupling_learner = Mock()
        self.coordinator.thermal_coupling_learner = self.coupling_learner

        # Create callbacks
        self.callbacks = {
            "get_current_temp": Mock(return_value=20.0),
            "get_ext_temp": Mock(return_value=5.0),
            "get_wind_speed": Mock(return_value=0.0),
            "get_previous_temp_time": Mock(return_value=None),
            "set_previous_temp_time": Mock(),
            "get_cur_temp_time": Mock(return_value=None),
            "set_cur_temp_time": Mock(),
            "get_output_precision": Mock(return_value=1),
            "calculate_night_setback_adjustment": Mock(return_value=(21.0, None, None)),
            "set_control_output": Mock(),
            "set_p": Mock(),
            "set_i": Mock(),
            "set_d": Mock(),
            "set_e": Mock(),
            "set_dt": Mock(),
            "get_kp": Mock(return_value=100.0),
            "get_ki": Mock(return_value=0.1),
            "get_kd": Mock(return_value=50.0),
            "get_ke": Mock(return_value=0.3),
        }

        # Create control output manager
        self.manager = ControlOutputManager(
            thermostat=self.thermostat,
            pid_controller=self.pid_controller,
            heater_controller=self.heater_controller,
            **self.callbacks
        )

    def test_coupling_compensation_single_neighbor(self):
        """TEST: Calculate compensation from one heating neighbor."""
        # Setup: One neighbor heating, with learned coefficient
        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.25,  # 0.25°C rise in target per 1°C rise in source
            confidence=0.6,  # Above threshold, full effect
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        # Mock get_coefficient to return our test coefficient
        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        # Mock active zones - kitchen is heating
        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 18.0,  # Started heating at 18°C
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        # Mock zone temps - kitchen now at 20°C (2°C rise)
        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 20.0,
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Calculate compensation
        compensation = self.manager._calculate_coupling_compensation()

        # Expected: 0.25 (coefficient) * 1.0 (graduated_confidence(0.6)) * 2.0 (temp rise)
        # = 0.5°C
        # Then convert to power: 0.5 * 100.0 (Kp) = 50.0%
        assert compensation == pytest.approx(50.0, abs=0.1)

    def test_coupling_compensation_multiple_neighbors(self):
        """TEST: Sum compensation from multiple heating sources."""
        # Setup: Two neighbors heating
        kitchen_id = "climate.kitchen"
        bedroom_id = "climate.bedroom"

        # Different coefficients for each neighbor
        kitchen_coef = CouplingCoefficient(
            source_zone=kitchen_id,
            target_zone="climate.living_room",
            coefficient=0.25,
            confidence=0.6,
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )
        bedroom_coef = CouplingCoefficient(
            source_zone=bedroom_id,
            target_zone="climate.living_room",
            coefficient=0.15,
            confidence=0.5,
            observation_count=8,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        def mock_get_coefficient(source: str, target: str):
            if source == kitchen_id:
                return kitchen_coef
            elif source == bedroom_id:
                return bedroom_coef
            return None

        self.coupling_learner.get_coefficient = Mock(side_effect=mock_get_coefficient)

        # Both zones actively heating
        active_zones = {
            kitchen_id: {
                "entity_id": kitchen_id,
                "heating_start_temp": 18.0,
            },
            bedroom_id: {
                "entity_id": bedroom_id,
                "heating_start_temp": 19.5,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        # Current temps show rises
        zone_temps = {
            "climate.living_room": 20.0,
            kitchen_id: 20.0,  # 2.0°C rise
            bedroom_id: 21.0,  # 1.5°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Calculate compensation
        compensation = self.manager._calculate_coupling_compensation()

        # Expected:
        # Kitchen: 0.25 * 1.0 (full confidence) * 2.0 = 0.5°C
        # Bedroom: 0.15 * 1.0 (full confidence) * 1.5 = 0.225°C
        # Total: 0.725°C
        # Power: 0.725 * 100.0 = 72.5%
        assert compensation == pytest.approx(72.5, abs=0.1)

    def test_coupling_compensation_capped(self):
        """TEST: Compensation is capped at MAX_COMPENSATION for heating type."""
        # Use floor hydronic for testing (max = 1.0°C)
        self.thermostat._heating_type = HEATING_TYPE_FLOOR_HYDRONIC

        # Setup: Large coefficient and large temp rise to exceed cap
        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.8,  # Very high coupling
            confidence=0.6,
            observation_count=20,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 18.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        # Neighbor rose by 3°C
        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 21.0,  # 3°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Calculate compensation
        compensation = self.manager._calculate_coupling_compensation()

        # Uncapped would be: 0.8 * 1.0 * 3.0 = 2.4°C
        # But capped at MAX_COUPLING_COMPENSATION[floor_hydronic] = 1.0°C
        # Power: 1.0 * 100.0 = 100.0%
        assert compensation == pytest.approx(100.0, abs=0.1)

    def test_coupling_compensation_converts_to_power(self):
        """TEST: Compensation uses Kp to convert degC to power%."""
        # Setup: Simple scenario to test power conversion
        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.5,
            confidence=0.6,
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 19.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 20.0,  # 1°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Test with different Kp values
        test_cases = [
            (50.0, 25.0),   # Kp=50 → 0.5°C * 50 = 25%
            (100.0, 50.0),  # Kp=100 → 0.5°C * 100 = 50%
            (200.0, 100.0), # Kp=200 → 0.5°C * 200 = 100%
        ]

        for kp, expected_power in test_cases:
            self.callbacks["get_kp"].return_value = kp
            compensation = self.manager._calculate_coupling_compensation()
            assert compensation == pytest.approx(expected_power, abs=0.1), \
                f"Failed for Kp={kp}: expected {expected_power}, got {compensation}"

    def test_coupling_compensation_disabled_cooling(self):
        """TEST: Return 0 when hvac_mode is cool."""
        # Setup: Even with active neighbors, cooling mode disables compensation
        self.thermostat._hvac_mode = HVACMode.COOL

        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.5,
            confidence=0.6,
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 18.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 21.0,  # 3°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Calculate compensation - should be 0 in cooling mode
        compensation = self.manager._calculate_coupling_compensation()
        assert compensation == 0.0

    def test_coupling_compensation_no_coordinator(self):
        """TEST: Return 0 when coordinator is not available."""
        # Remove coordinator
        self.thermostat.hass.data = {}

        compensation = self.manager._calculate_coupling_compensation()
        assert compensation == 0.0

    def test_coupling_compensation_no_active_zones(self):
        """TEST: Return 0 when no neighbors are actively heating."""
        # No active zones
        self.coordinator.get_active_zones = Mock(return_value={})

        compensation = self.manager._calculate_coupling_compensation()
        assert compensation == 0.0

    def test_coupling_compensation_no_coefficient(self):
        """TEST: Skip neighbor when no learned coefficient exists."""
        neighbor_id = "climate.kitchen"

        # No coefficient learned yet
        self.coupling_learner.get_coefficient = Mock(return_value=None)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 18.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 20.0,
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # No coefficient → no compensation
        compensation = self.manager._calculate_coupling_compensation()
        assert compensation == 0.0

    def test_coupling_compensation_low_confidence(self):
        """TEST: Apply graduated_confidence scaling for low confidence."""
        neighbor_id = "climate.kitchen"

        # Confidence below threshold → no effect
        low_coef = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.5,
            confidence=0.2,  # Below 0.3 threshold
            observation_count=2,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=low_coef)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 18.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 20.0,  # 2°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Low confidence → graduated_confidence returns 0
        compensation = self.manager._calculate_coupling_compensation()
        assert compensation == 0.0

        # Test mid-range confidence (0.4 → 0.5 scaling)
        mid_coef = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.5,
            confidence=0.4,  # Between 0.3 and 0.5
            observation_count=5,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=mid_coef)
        compensation = self.manager._calculate_coupling_compensation()

        # Expected: 0.5 (coef) * 0.5 (graduated_confidence(0.4)) * 2.0 (rise) * 100 (Kp)
        # graduated_confidence(0.4) = (0.4 - 0.3) / (0.5 - 0.3) = 0.5
        # = 0.5 * 0.5 * 2.0 * 100 = 50.0%
        assert compensation == pytest.approx(50.0, abs=0.1)

    def test_coupling_compensation_negative_temp_rise(self):
        """TEST: Handle negative temp rise (neighbor cooling) gracefully."""
        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.5,
            confidence=0.6,
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 21.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        # Neighbor temp dropped (shouldn't happen, but test robustness)
        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 19.0,  # -2°C "rise"
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Negative rise should be treated as 0 (no heating effect)
        compensation = self.manager._calculate_coupling_compensation()
        assert compensation == 0.0

    def test_coupling_compensation_max_values_per_heating_type(self):
        """TEST: Verify max compensation values for all heating types."""
        neighbor_id = "climate.kitchen"

        # Large coefficient and temp rise to trigger cap
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=1.0,
            confidence=0.6,
            observation_count=20,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 15.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        # Large temp rise
        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 25.0,  # 10°C rise (extreme)
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Test each heating type
        test_cases = [
            (HEATING_TYPE_FLOOR_HYDRONIC, 1.0),
            (HEATING_TYPE_RADIATOR, 1.2),
            (HEATING_TYPE_CONVECTOR, 1.5),
            (HEATING_TYPE_FORCED_AIR, 2.0),
        ]

        for heating_type, max_temp in test_cases:
            self.thermostat._heating_type = heating_type
            compensation = self.manager._calculate_coupling_compensation()
            expected_power = max_temp * 100.0  # Convert to power with Kp=100
            assert compensation == pytest.approx(expected_power, abs=0.1), \
                f"Failed for {heating_type}: expected {expected_power}, got {compensation}"


# ============================================================================
# Feedforward Integration Tests (Story 6.2)
# ============================================================================


class TestControlLoopFeedforward:
    """Tests for feedforward integration in the control loop."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock thermostat
        self.thermostat = Mock()
        self.thermostat.entity_id = "climate.living_room"
        self.thermostat.hass = Mock()
        self.thermostat._heating_type = HEATING_TYPE_CONVECTOR
        self.thermostat._hvac_mode = HVACMode.HEAT

        # Create mock PID controller with real tracking
        self.pid_controller = Mock()
        self.pid_controller.sampling_period = 0
        self.pid_controller.calc = Mock(return_value=(80.0, True))
        self.pid_controller.proportional = 50.0
        self.pid_controller.integral = 25.0
        self.pid_controller.derivative = 5.0
        self.pid_controller.external = 0.0
        self.pid_controller.feedforward = 0.0
        self.pid_controller.error = 1.0
        self.pid_controller.dt = 60.0

        # Track set_feedforward calls
        self._feedforward_calls = []

        def track_feedforward(value):
            self._feedforward_calls.append(value)

        self.pid_controller.set_feedforward = Mock(side_effect=track_feedforward)

        # Create mock heater controller
        self.heater_controller = Mock()

        # Create mock coordinator
        self.coordinator = Mock()
        self.thermostat.hass.data = {
            "adaptive_thermostat": {
                "coordinator": self.coordinator
            }
        }

        # Setup thermal coupling learner
        self.coupling_learner = Mock()
        self.coordinator.thermal_coupling_learner = self.coupling_learner

        # Create callbacks
        self.callbacks = {
            "get_current_temp": Mock(return_value=20.0),
            "get_ext_temp": Mock(return_value=5.0),
            "get_wind_speed": Mock(return_value=0.0),
            "get_previous_temp_time": Mock(return_value=1000.0),
            "set_previous_temp_time": Mock(),
            "get_cur_temp_time": Mock(return_value=1060.0),
            "set_cur_temp_time": Mock(),
            "get_output_precision": Mock(return_value=1),
            "calculate_night_setback_adjustment": Mock(return_value=(21.0, None, None)),
            "set_control_output": Mock(),
            "set_p": Mock(),
            "set_i": Mock(),
            "set_d": Mock(),
            "set_e": Mock(),
            "set_dt": Mock(),
            "get_kp": Mock(return_value=100.0),
            "get_ki": Mock(return_value=0.1),
            "get_kd": Mock(return_value=50.0),
            "get_ke": Mock(return_value=0.3),
        }

        # Create control output manager
        self.manager = ControlOutputManager(
            thermostat=self.thermostat,
            pid_controller=self.pid_controller,
            heater_controller=self.heater_controller,
            **self.callbacks
        )

    @pytest.mark.asyncio
    async def test_control_loop_sets_feedforward(self):
        """TEST: PID receives coupling compensation as feedforward before calc."""
        # Setup: One neighbor heating with learned coefficient
        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.25,
            confidence=0.6,
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 18.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 20.0,  # 2°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Calculate output (this should set feedforward before calling PID.calc)
        await self.manager.calc_output()

        # Verify set_feedforward was called before calc
        assert len(self._feedforward_calls) == 1

        # Expected compensation: 0.25 * 1.0 * 2.0 * 100.0 = 50.0
        assert self._feedforward_calls[0] == pytest.approx(50.0, abs=0.1)

        # Verify calc was called after set_feedforward
        self.pid_controller.calc.assert_called_once()

    @pytest.mark.asyncio
    async def test_control_loop_reduces_output(self):
        """TEST: Output reduced when neighbors heating (integration test)."""
        # Setup: Neighbor heating with significant coupling
        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.3,
            confidence=0.6,
            observation_count=15,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 17.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 21.0,  # 4°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        # Expected feedforward: 0.3 * 1.0 * 4.0 * 100.0 = 120.0%
        # Capped at max for convector: 1.5°C * 100 Kp = 150.0%

        await self.manager.calc_output()

        # Verify feedforward was set with the correct value
        # compensation = 0.3 * 1.0 * 4.0 = 1.2°C < 1.5°C cap
        # power = 1.2 * 100 = 120.0%
        assert self._feedforward_calls[0] == pytest.approx(120.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_control_loop_zero_feedforward_no_neighbors(self):
        """TEST: Feedforward is 0 when no neighbors heating."""
        # No active zones
        self.coordinator.get_active_zones = Mock(return_value={})

        await self.manager.calc_output()

        # Should still call set_feedforward (with 0)
        assert len(self._feedforward_calls) == 1
        assert self._feedforward_calls[0] == 0.0

    @pytest.mark.asyncio
    async def test_control_loop_feedforward_cooling_mode(self):
        """TEST: Feedforward is 0 in cooling mode."""
        self.thermostat._hvac_mode = HVACMode.COOL

        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.5,
            confidence=0.6,
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 18.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 20.0,
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        await self.manager.calc_output()

        # Feedforward disabled in cooling mode
        assert len(self._feedforward_calls) == 1
        assert self._feedforward_calls[0] == 0.0

    @pytest.mark.asyncio
    async def test_control_loop_feedforward_sampling_mode(self):
        """TEST: Feedforward works in sampling mode (non-zero sampling_period)."""
        # Switch to sampling mode
        self.pid_controller.sampling_period = 60

        neighbor_id = "climate.kitchen"
        coefficient = CouplingCoefficient(
            source_zone=neighbor_id,
            target_zone="climate.living_room",
            coefficient=0.2,
            confidence=0.6,
            observation_count=10,
            baseline_overshoot=None,
            last_updated=datetime.now()
        )

        self.coupling_learner.get_coefficient = Mock(return_value=coefficient)

        active_zones = {
            neighbor_id: {
                "entity_id": neighbor_id,
                "heating_start_temp": 19.0,
            }
        }
        self.coordinator.get_active_zones = Mock(return_value=active_zones)

        zone_temps = {
            "climate.living_room": 20.0,
            neighbor_id: 21.0,  # 2°C rise
        }
        self.coordinator.get_zone_temps = Mock(return_value=zone_temps)

        await self.manager.calc_output()

        # Feedforward should be set: 0.2 * 1.0 * 2.0 * 100.0 = 40.0
        assert len(self._feedforward_calls) == 1
        assert self._feedforward_calls[0] == pytest.approx(40.0, abs=0.1)
