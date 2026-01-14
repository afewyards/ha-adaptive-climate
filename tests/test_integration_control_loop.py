"""Integration tests for the main control loop.

Tests the complete flow: Climate entity -> PID controller -> demand -> central controller -> heater
"""
import asyncio
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock
from datetime import timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "adaptive_thermostat"))

# Mock homeassistant modules before importing
sys.modules['homeassistant'] = Mock()
sys.modules['homeassistant.core'] = Mock()
sys.modules['homeassistant.helpers'] = Mock()
sys.modules['homeassistant.helpers.update_coordinator'] = Mock()


# Create mock base class
class MockDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval


sys.modules['homeassistant.helpers.update_coordinator'].DataUpdateCoordinator = MockDataUpdateCoordinator

# Import modules under test
import coordinator
import central_controller as central_controller_module
import pid_controller


class StateRegistry:
    """Registry to simulate Home Assistant entity states."""

    def __init__(self):
        self._states = {}

    def set_state(self, entity_id: str, state: str, attributes: dict = None):
        """Set state for an entity."""
        mock_state = MagicMock()
        mock_state.state = state
        mock_state.attributes = attributes or {}
        self._states[entity_id] = mock_state

    def get_state(self, entity_id: str):
        """Get state for an entity."""
        return self._states.get(entity_id)

    def is_state(self, entity_id: str, expected_state: str) -> bool:
        """Check if entity is in expected state."""
        state = self._states.get(entity_id)
        return state is not None and state.state == expected_state


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance with service call tracking."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.config = MagicMock()
    hass.config.units = MagicMock()
    hass.config.units.temperature_unit = "°C"
    hass.data = {}
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock()

    # Track service calls for verification
    hass._service_call_history = []

    async def track_service_call(domain, service, data, **kwargs):
        hass._service_call_history.append({
            "domain": domain,
            "service": service,
            "data": data,
            "kwargs": kwargs,
        })

    hass.services.async_call = AsyncMock(side_effect=track_service_call)

    return hass


@pytest.fixture
def state_registry():
    """Create a state registry for simulating HA entity states."""
    return StateRegistry()


@pytest.fixture
def coord(mock_hass):
    """Create a coordinator instance."""
    return coordinator.AdaptiveThermostatCoordinator(mock_hass)


@pytest.fixture
def central_controller(mock_hass, coord):
    """Create a central controller with zero startup delay."""
    return coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.main_boiler"],
        startup_delay_seconds=0,
    )


@pytest.fixture
def zone_linker(mock_hass, coord):
    """Create a zone linker instance."""
    return coordinator.ZoneLinker(mock_hass, coord)


@pytest.fixture
def pid():
    """Create a PID controller instance with conservative tuning."""
    return pid_controller.PID(
        kp=50.0,
        ki=0.01,
        kd=100.0,
        ke=0,
        out_min=0,
        out_max=100,
        sampling_period=0,
        cold_tolerance=0.3,
        hot_tolerance=0.3,
    )


# =============================================================================
# Test: Full Control Loop - Temperature Triggers Heating
# =============================================================================


@pytest.mark.asyncio
async def test_full_control_loop_temperature_triggers_heating(
    mock_hass, state_registry, coord, central_controller, pid
):
    """
    Integration test: Temperature below setpoint triggers PID calculation,
    which generates demand, central controller turns heater ON.

    Flow: sensor change -> PID calc -> demand update -> central controller -> heater ON
    """
    # Setup: Heater is OFF, temp is below setpoint
    state_registry.set_state("switch.main_boiler", "off")
    state_registry.set_state("sensor.living_room_temp", "18.0")
    mock_hass.states.get = state_registry.get_state
    mock_hass.states.is_state = state_registry.is_state

    # Register zone
    coord.register_zone("living_room", {
        "climate_entity_id": "climate.living_room",
        "zone_name": "Living Room",
        "area_m2": 20.0,
    })

    # Simulate PID calculation with temp below setpoint
    current_temp = 18.0
    target_temp = 21.0
    output, _ = pid.calc(current_temp, target_temp)

    # Output should be positive (heating demand)
    assert output > 0, f"Expected positive PID output, got {output}"

    # Simulate demand switch update based on PID output
    # Demand threshold is typically 5% of output range
    demand_threshold = 5.0
    has_demand = output > demand_threshold
    assert has_demand, f"Expected demand with output {output}"

    coord.update_zone_demand("living_room", has_demand, hvac_mode="heat")

    # Central controller updates based on aggregate demand
    await central_controller.update()

    # Verify heater was turned ON
    turn_on_calls = [
        c for c in mock_hass._service_call_history
        if c["service"] == "turn_on"
    ]
    assert len(turn_on_calls) == 1
    assert turn_on_calls[0]["data"]["entity_id"] == "switch.main_boiler"


@pytest.mark.asyncio
async def test_control_loop_satisfied_turns_off_heater(
    mock_hass, state_registry, coord, central_controller, pid, monkeypatch
):
    """
    Integration test: When temperature reaches setpoint, PID output drops,
    demand is removed, central controller turns off heater.

    Flow: temp at setpoint -> PID calc (low output) -> no demand -> heater OFF
    """
    # Use short debounce for test
    monkeypatch.setattr(central_controller_module, "TURN_OFF_DEBOUNCE_SECONDS", 0.1)

    # Setup: Heater is ON, temp has reached setpoint
    state_registry.set_state("switch.main_boiler", "on")
    mock_hass.states.get = state_registry.get_state
    mock_hass.states.is_state = state_registry.is_state

    # Register zone with initial demand
    coord.register_zone("living_room", {"zone_name": "Living Room"})
    coord.update_zone_demand("living_room", True, hvac_mode="heat")

    # Simulate that heater was previously activated by controller
    central_controller._heater_activated_by_us = True

    # Simulate PID calculation with temp at setpoint
    current_temp = 21.0
    target_temp = 21.0
    output, _ = pid.calc(current_temp, target_temp)

    # Output should be near zero
    assert output < 5.0, f"Expected low PID output at setpoint, got {output}"

    # Update demand based on output
    has_demand = output > 5.0
    coord.update_zone_demand("living_room", has_demand, hvac_mode="heat")

    # Central controller updates
    await central_controller.update()

    # Wait for debounce
    await asyncio.sleep(0.2)

    # Verify heater was turned OFF
    turn_off_calls = [
        c for c in mock_hass._service_call_history
        if c["service"] == "turn_off"
    ]
    assert len(turn_off_calls) == 1
    assert turn_off_calls[0]["data"]["entity_id"] == "switch.main_boiler"


# =============================================================================
# Test: Multi-Zone Demand Aggregation
# =============================================================================


@pytest.mark.asyncio
async def test_multi_zone_demand_aggregation(
    mock_hass, state_registry, coord, central_controller, monkeypatch
):
    """
    Integration test: Multiple zones with different demands correctly
    aggregate to control central heater.

    Scenario:
    1. Zone A has demand -> heater ON
    2. Zone B also gets demand -> heater stays ON
    3. Zone A satisfied -> heater stays ON (B still needs heat)
    4. Zone B satisfied -> heater OFF
    """
    # Use short debounce for test
    monkeypatch.setattr(central_controller_module, "TURN_OFF_DEBOUNCE_SECONDS", 0.1)

    state_registry.set_state("switch.main_boiler", "off")
    mock_hass.states.get = state_registry.get_state
    mock_hass.states.is_state = state_registry.is_state

    # Register two zones
    coord.register_zone("zone_a", {"zone_name": "Zone A"})
    coord.register_zone("zone_b", {"zone_name": "Zone B"})

    # Step 1: Zone A has demand
    coord.update_zone_demand("zone_a", True, hvac_mode="heat")
    await central_controller.update()

    # Heater should be ON
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True

    # Verify heater was turned on
    assert any(c["service"] == "turn_on" for c in mock_hass._service_call_history)

    # Step 2: Zone B also has demand
    coord.update_zone_demand("zone_b", True, hvac_mode="heat")
    await central_controller.update()
    assert coord.get_aggregate_demand()["heating"] is True

    # Step 3: Zone A satisfied
    state_registry.set_state("switch.main_boiler", "on")  # Now it's on
    # Simulate that heater was previously activated by controller
    central_controller._heater_activated_by_us = True
    coord.update_zone_demand("zone_a", False, hvac_mode="heat")
    await central_controller.update()
    assert coord.get_aggregate_demand()["heating"] is True  # B still needs heat

    # Step 4: Zone B satisfied
    coord.update_zone_demand("zone_b", False, hvac_mode="heat")
    await central_controller.update()
    assert coord.get_aggregate_demand()["heating"] is False

    # Wait for debounce
    await asyncio.sleep(0.2)

    # Heater should be OFF now
    turn_off_calls = [c for c in mock_hass._service_call_history if c["service"] == "turn_off"]
    assert len(turn_off_calls) > 0


# =============================================================================
# Test: Zone Linking Delays Heating
# =============================================================================


@pytest.mark.asyncio
async def test_zone_linking_delays_heating(mock_hass, coord, zone_linker):
    """
    Integration test: Zone linking delays heating in thermally connected zones.

    Scenario: Kitchen heats, adjacent living room heating is delayed.
    """
    coord.register_zone("kitchen", {"zone_name": "Kitchen"})
    coord.register_zone("living_room", {"zone_name": "Living Room"})

    # Configure linking: kitchen heats -> living_room delayed
    zone_linker.configure_linked_zones("kitchen", ["living_room"])

    # Kitchen starts heating
    await zone_linker.on_zone_heating_started("kitchen", delay_minutes=20)

    # Living room should be delayed
    assert zone_linker.is_zone_delayed("living_room") is True

    # Kitchen should NOT be delayed
    assert zone_linker.is_zone_delayed("kitchen") is False

    # Check remaining time
    remaining = zone_linker.get_delay_remaining_minutes("living_room")
    assert remaining is not None
    assert remaining > 19  # Should be close to 20 minutes


@pytest.mark.asyncio
async def test_zone_linking_bidirectional(mock_hass, coord, zone_linker):
    """
    Integration test: Bidirectional zone linking works correctly.

    Both zones are thermally connected - when either heats, the other is delayed.
    """
    coord.register_zone("zone_a", {"zone_name": "Zone A"})
    coord.register_zone("zone_b", {"zone_name": "Zone B"})

    # Configure bidirectional linking
    zone_linker.configure_linked_zones("zone_a", ["zone_b"])
    zone_linker.configure_linked_zones("zone_b", ["zone_a"])

    # Zone A starts heating
    await zone_linker.on_zone_heating_started("zone_a", delay_minutes=15)

    # Zone B should be delayed
    assert zone_linker.is_zone_delayed("zone_b") is True
    assert zone_linker.is_zone_delayed("zone_a") is False

    # Clear delay and test reverse
    zone_linker.clear_delay("zone_b")

    # Zone B starts heating
    await zone_linker.on_zone_heating_started("zone_b", delay_minutes=15)

    # Zone A should now be delayed
    assert zone_linker.is_zone_delayed("zone_a") is True
    assert zone_linker.is_zone_delayed("zone_b") is False


# =============================================================================
# Test: PID Output Drives PWM Cycle
# =============================================================================


def test_pid_output_drives_pwm_cycle(pid):
    """
    Integration test: PID output determines PWM duty cycle timing.

    Verifies that PID output translates to sensible on/off times.
    """
    # Simulate temperature slightly below setpoint
    current_temp = 20.5
    target_temp = 21.0

    output, _ = pid.calc(current_temp, target_temp)

    # For floor heating with conservative tuning, output should be modest
    assert 0 < output < 100, f"Output {output} should be between 0 and 100"

    # Calculate PWM timing (15 min = 900 seconds default)
    pwm_period = 900
    output_range = 100  # max - min output
    time_on = pwm_period * abs(output) / output_range
    time_off = pwm_period - time_on

    # Verify timing makes sense
    assert time_on > 0
    assert time_off > 0
    assert time_on + time_off == pwm_period


def test_pid_output_scales_with_error(pid):
    """
    Integration test: Larger temperature errors produce higher PID output.
    """
    target_temp = 21.0

    # Small error
    output_small, _ = pid.calc(20.5, target_temp)

    # Large error
    output_large, _ = pid.calc(18.0, target_temp)

    # Larger error should produce higher output
    assert output_large > output_small, (
        f"Large error output {output_large} should exceed small error output {output_small}"
    )


# =============================================================================
# Test: Startup Delay with Demand Changes
# =============================================================================


@pytest.mark.asyncio
async def test_startup_delay_with_demand_changes(mock_hass, state_registry, coord):
    """
    Integration test: Startup delay correctly handles demand appearing,
    disappearing, and reappearing during delay period.
    """
    state_registry.set_state("switch.main_boiler", "off")
    mock_hass.states.get = state_registry.get_state
    mock_hass.states.is_state = state_registry.is_state

    # Create controller with 1 second delay for faster testing
    controller = coordinator.CentralController(
        mock_hass,
        coord,
        main_heater_switch=["switch.main_boiler"],
        startup_delay_seconds=1,
    )

    coord.register_zone("test_zone", {"zone_name": "Test"})

    # Add demand - starts delay timer
    coord.update_zone_demand("test_zone", True, hvac_mode="heat")
    await controller.update()
    assert controller.is_heater_waiting_for_startup()

    # Remove demand before delay expires - should cancel
    coord.update_zone_demand("test_zone", False, hvac_mode="heat")
    await controller.update()
    assert not controller.is_heater_waiting_for_startup()

    # Clear history for clean verification
    mock_hass._service_call_history.clear()

    # Re-add demand - starts new delay timer
    coord.update_zone_demand("test_zone", True, hvac_mode="heat")
    await controller.update()
    assert controller.is_heater_waiting_for_startup()

    # Wait for delay to complete
    await asyncio.sleep(1.5)

    # Heater should now be ON
    turn_on_calls = [c for c in mock_hass._service_call_history if c["service"] == "turn_on"]
    assert len(turn_on_calls) > 0


# =============================================================================
# Test: Component Interaction Order
# =============================================================================


@pytest.mark.asyncio
async def test_component_interaction_order(mock_hass, state_registry, coord, central_controller):
    """
    Verify components interact in correct order:
    PID calc -> demand update -> coordinator notification -> central controller action
    """
    state_registry.set_state("switch.main_boiler", "off")
    mock_hass.states.get = state_registry.get_state
    mock_hass.states.is_state = state_registry.is_state

    # Track order of operations
    operation_log = []

    # Wrap coordinator methods to track calls
    original_update_demand = coord.update_zone_demand

    def tracked_update_demand(zone_id, has_demand, hvac_mode=None):
        operation_log.append(("demand_update", zone_id, has_demand))
        return original_update_demand(zone_id, has_demand, hvac_mode=hvac_mode)

    coord.update_zone_demand = tracked_update_demand

    original_update = central_controller.update

    async def tracked_controller_update():
        operation_log.append(("controller_update", None, None))
        return await original_update()

    central_controller.update = tracked_controller_update

    # Execute flow
    coord.register_zone("test", {"name": "Test"})
    coord.update_zone_demand("test", True, hvac_mode="heat")  # Should log
    await central_controller.update()  # Should log

    # Verify order
    assert len(operation_log) == 2
    assert operation_log[0][0] == "demand_update"
    assert operation_log[1][0] == "controller_update"


# =============================================================================
# Test: Multiple Zones With PID
# =============================================================================


@pytest.mark.asyncio
async def test_multiple_zones_with_independent_pid(
    mock_hass, state_registry, coord, central_controller
):
    """
    Integration test: Multiple zones each with their own PID controllers
    correctly aggregate demand.
    """
    state_registry.set_state("switch.main_boiler", "off")
    mock_hass.states.get = state_registry.get_state
    mock_hass.states.is_state = state_registry.is_state

    # Create PIDs for each zone with different tuning
    pid_living = pid_controller.PID(kp=50.0, ki=0.01, kd=100.0, out_min=0, out_max=100)
    pid_bedroom = pid_controller.PID(kp=40.0, ki=0.008, kd=80.0, out_min=0, out_max=100)

    # Register zones
    coord.register_zone("living_room", {"zone_name": "Living Room"})
    coord.register_zone("bedroom", {"zone_name": "Bedroom"})

    # Living room: needs heat (temp below setpoint)
    output_living, _ = pid_living.calc(18.0, 21.0)
    has_demand_living = output_living > 5.0

    # Bedroom: satisfied (temp at setpoint)
    output_bedroom, _ = pid_bedroom.calc(21.0, 21.0)
    has_demand_bedroom = output_bedroom > 5.0

    # Update demands
    coord.update_zone_demand("living_room", has_demand_living, hvac_mode="heat")
    coord.update_zone_demand("bedroom", has_demand_bedroom, hvac_mode="heat")

    # Verify aggregate demand
    demand = coord.get_aggregate_demand()
    assert demand["heating"] is True  # Living room has demand

    await central_controller.update()

    # Heater should be ON
    turn_on_calls = [c for c in mock_hass._service_call_history if c["service"] == "turn_on"]
    assert len(turn_on_calls) == 1
