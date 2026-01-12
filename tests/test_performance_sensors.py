"""Tests for Adaptive Thermostat performance sensors."""
import pytest
import asyncio
from unittest.mock import Mock, MagicMock


def test_duty_cycle_calculation():
    """Test duty cycle calculation from heater state."""
    # Import with minimal mocking
    import sys
    from unittest.mock import Mock

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = object
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

    sys.modules['homeassistant'] = Mock()
    sys.modules['homeassistant.core'] = Mock()
    sys.modules['homeassistant.components'] = Mock()
    sys.modules['homeassistant.components.sensor'] = mock_sensor_module
    sys.modules['homeassistant.const'] = Mock()
    sys.modules['homeassistant.helpers'] = Mock()
    sys.modules['homeassistant.helpers.entity_platform'] = Mock()
    sys.modules['homeassistant.helpers.typing'] = Mock()
    sys.modules['homeassistant.helpers.event'] = Mock()

    from custom_components.adaptive_thermostat.sensor import DutyCycleSensor

    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()
    mock_hass.data = {"history": Mock()}  # Add history module

    # Create sensor
    sensor = DutyCycleSensor(
        mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Mock climate entity state
    climate_state = Mock()
    climate_state.attributes = {"heater_entity_id": "switch.heater_living_room"}

    # Mock heater state - ON
    heater_state_on = Mock()
    heater_state_on.state = "on"

    mock_hass.states.get.side_effect = lambda entity_id: (
        climate_state if entity_id == "climate.living_room"
        else heater_state_on if entity_id == "switch.heater_living_room"
        else None
    )

    # Test heater ON - should report 100% duty cycle
    result = asyncio.run(sensor._calculate_duty_cycle())
    assert result == 100.0

    # Mock heater state - OFF
    heater_state_off = Mock()
    heater_state_off.state = "off"

    mock_hass.states.get.side_effect = lambda entity_id: (
        climate_state if entity_id == "climate.living_room"
        else heater_state_off if entity_id == "switch.heater_living_room"
        else None
    )

    # Test heater OFF - should report 0% duty cycle
    result = asyncio.run(sensor._calculate_duty_cycle())
    assert result == 0.0


def test_power_m2_estimation():
    """Test power per m2 estimation from duty cycle and zone area."""
    import sys
    from unittest.mock import Mock

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = object
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.POWER = "power"
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

    sys.modules['homeassistant'] = Mock()
    sys.modules['homeassistant.core'] = Mock()
    sys.modules['homeassistant.components'] = Mock()
    sys.modules['homeassistant.components.sensor'] = mock_sensor_module
    sys.modules['homeassistant.const'] = Mock()
    sys.modules['homeassistant.helpers'] = Mock()
    sys.modules['homeassistant.helpers.entity_platform'] = Mock()
    sys.modules['homeassistant.helpers.typing'] = Mock()
    sys.modules['homeassistant.helpers.event'] = Mock()

    from custom_components.adaptive_thermostat.sensor import PowerPerM2Sensor

    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()

    # Set up coordinator with zone data
    coordinator = Mock()
    coordinator.get_zone_data.return_value = {
        "area_m2": 20.0,
        "max_power_w_m2": 100.0,
    }

    mock_hass.data = {
        "adaptive_thermostat": {"coordinator": coordinator}
    }

    # Create sensor
    sensor = PowerPerM2Sensor(
        mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Mock duty cycle sensor at 50%
    duty_cycle_state = Mock()
    duty_cycle_state.state = "50.0"
    mock_hass.states.get.return_value = duty_cycle_state

    # Calculate power/m2
    result = asyncio.run(sensor._calculate_power_m2())

    # Expected: 50% duty cycle * 100 W/m² = 50 W/m²
    assert result == pytest.approx(50.0, rel=1e-2)

    # Test with 25% duty cycle
    duty_cycle_state.state = "25.0"
    result = asyncio.run(sensor._calculate_power_m2())
    assert result == pytest.approx(25.0, rel=1e-2)

    # Test with 0% duty cycle
    duty_cycle_state.state = "0.0"
    result = asyncio.run(sensor._calculate_power_m2())
    assert result == pytest.approx(0.0, rel=1e-2)


def test_average_cycle_time():
    """Test average cycle time calculation."""
    import sys
    from unittest.mock import Mock

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = object
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.DURATION = "duration"
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

    sys.modules['homeassistant'] = Mock()
    sys.modules['homeassistant.core'] = Mock()
    sys.modules['homeassistant.components'] = Mock()
    sys.modules['homeassistant.components.sensor'] = mock_sensor_module
    sys.modules['homeassistant.const'] = Mock()
    sys.modules['homeassistant.helpers'] = Mock()
    sys.modules['homeassistant.helpers.entity_platform'] = Mock()
    sys.modules['homeassistant.helpers.typing'] = Mock()
    sys.modules['homeassistant.helpers.event'] = Mock()

    from custom_components.adaptive_thermostat.sensor import CycleTimeSensor

    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()
    mock_hass.data = {}

    # Create sensor
    sensor = CycleTimeSensor(
        mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Mock climate entity state
    climate_state = Mock()
    climate_state.attributes = {}
    mock_hass.states.get.return_value = climate_state

    # Calculate average cycle time
    result = asyncio.run(sensor._calculate_average_cycle_time())

    # Should return a default value for now (implementation placeholder)
    # In production, this would be calculated from actual cycle history
    assert result == 20.0

    # Test with missing climate entity
    mock_hass.states.get.return_value = None
    result = asyncio.run(sensor._calculate_average_cycle_time())
    assert result is None
