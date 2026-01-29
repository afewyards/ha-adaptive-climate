"""Tests for Adaptive Thermostat performance sensors."""
import pytest
import asyncio
from unittest.mock import Mock, MagicMock


def test_duty_cycle_calculation():
    """Test duty cycle calculation from heater state.

    Note: The DutyCycleSensor implementation has been updated to use
    state change tracking with timestamps. This test verifies the
    control_output fallback behavior when no state changes are tracked.
    For comprehensive duty cycle tests, see tests/test_sensor.py.
    """
    # Import with minimal mocking
    import sys
    from unittest.mock import Mock
    from abc import ABC

    # Create mock base classes that work together
    class MockSensorEntity:
        pass

    class MockRestoreEntity(ABC):
        pass

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = MockSensorEntity
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

    mock_const = Mock()
    mock_const.PERCENTAGE = "%"
    mock_const.STATE_ON = "on"
    mock_const.UnitOfPower = Mock()
    mock_const.UnitOfPower.WATT = "W"
    mock_const.UnitOfTime = Mock()
    mock_const.UnitOfTime.MINUTES = "min"
    mock_const.UnitOfTemperature = Mock()
    mock_const.UnitOfTemperature.CELSIUS = "°C"

    mock_event = Mock()
    mock_event.async_track_time_interval = Mock()
    mock_event.async_track_state_change_event = Mock()

    # Event needs to support subscripting for type hints like Event[EventStateChangedData]
    class MockEvent:
        """Mock Event class that supports generic subscripting."""
        def __class_getitem__(cls, item):
            return cls

    mock_core = Mock()
    mock_core.HomeAssistant = Mock
    mock_core.callback = lambda f: f
    mock_core.Event = MockEvent

    mock_restore_state = Mock()
    mock_restore_state.RestoreEntity = MockRestoreEntity

    sys.modules['homeassistant'] = Mock()
    sys.modules['homeassistant.core'] = mock_core
    sys.modules['homeassistant.components'] = Mock()
    sys.modules['homeassistant.components.sensor'] = mock_sensor_module
    sys.modules['homeassistant.const'] = mock_const
    sys.modules['homeassistant.helpers'] = Mock()
    sys.modules['homeassistant.helpers.entity_platform'] = Mock()
    sys.modules['homeassistant.helpers.typing'] = Mock()
    sys.modules['homeassistant.helpers.event'] = mock_event
    sys.modules['homeassistant.helpers.restore_state'] = mock_restore_state

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

    # The new implementation uses control_output as a fallback when no state
    # changes are tracked. Let's test that behavior.

    # Mock climate entity with control_output at 100% (heater fully on)
    climate_state = Mock()
    climate_state.attributes = {"control_output": 100.0}
    mock_hass.states.get.return_value = climate_state

    # Test control_output = 100 - should report 100% duty cycle
    result = sensor._calculate_duty_cycle()
    assert result == 100.0

    # Mock climate entity with control_output at 0% (heater off)
    climate_state.attributes = {"control_output": 0.0}

    # Test control_output = 0 - should report 0% duty cycle
    result = sensor._calculate_duty_cycle()
    assert result == 0.0

    # Test with 50% control_output
    climate_state.attributes = {"control_output": 50.0}
    result = sensor._calculate_duty_cycle()
    assert result == 50.0


def test_power_m2_estimation():
    """Test power per m2 estimation from duty cycle and zone area."""
    import sys
    from unittest.mock import Mock
    from abc import ABC

    # Create mock base classes that work together
    class MockSensorEntity:
        pass

    class MockRestoreEntity(ABC):
        pass

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = MockSensorEntity
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.POWER = "power"
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

    # Event needs to support subscripting for type hints like Event[EventStateChangedData]
    class MockEvent:
        """Mock Event class that supports generic subscripting."""
        def __class_getitem__(cls, item):
            return cls

    mock_core = Mock()
    mock_core.Event = MockEvent

    mock_restore_state = Mock()
    mock_restore_state.RestoreEntity = MockRestoreEntity

    sys.modules['homeassistant'] = Mock()
    sys.modules['homeassistant.core'] = mock_core
    sys.modules['homeassistant.components'] = Mock()
    sys.modules['homeassistant.components.sensor'] = mock_sensor_module
    sys.modules['homeassistant.const'] = Mock()
    sys.modules['homeassistant.helpers'] = Mock()
    sys.modules['homeassistant.helpers.entity_platform'] = Mock()
    sys.modules['homeassistant.helpers.typing'] = Mock()
    sys.modules['homeassistant.helpers.event'] = Mock()
    sys.modules['homeassistant.helpers.restore_state'] = mock_restore_state

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
    """Test average cycle time calculation.

    Note: The CycleTimeSensor implementation has been updated to track
    actual heater on/off cycles and calculate rolling averages.
    For comprehensive cycle time tests, see tests/test_sensor.py.
    """
    import sys
    from unittest.mock import Mock
    from collections import deque
    from abc import ABC

    # Create mock base classes that work together
    class MockSensorEntity:
        pass

    class MockRestoreEntity(ABC):
        pass

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = MockSensorEntity
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.DURATION = "duration"
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

    # Event needs to support subscripting for type hints like Event[EventStateChangedData]
    class MockEvent:
        """Mock Event class that supports generic subscripting."""
        def __class_getitem__(cls, item):
            return cls

    mock_core = Mock()
    mock_core.HomeAssistant = Mock
    mock_core.callback = lambda f: f
    mock_core.Event = MockEvent

    mock_restore_state = Mock()
    mock_restore_state.RestoreEntity = MockRestoreEntity

    sys.modules['homeassistant'] = Mock()
    sys.modules['homeassistant.core'] = mock_core
    sys.modules['homeassistant.components'] = Mock()
    sys.modules['homeassistant.components.sensor'] = mock_sensor_module
    sys.modules['homeassistant.const'] = Mock()
    sys.modules['homeassistant.const'].STATE_ON = "on"
    sys.modules['homeassistant.helpers'] = Mock()
    sys.modules['homeassistant.helpers.entity_platform'] = Mock()
    sys.modules['homeassistant.helpers.typing'] = Mock()
    sys.modules['homeassistant.helpers.event'] = Mock()
    sys.modules['homeassistant.helpers.restore_state'] = mock_restore_state

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

    # Test with no cycles - should return None
    result = sensor._calculate_average_cycle_time()
    assert result is None

    # Test with recorded cycles
    sensor._cycle_times = deque([15.0, 20.0, 25.0])  # Average: 20 minutes
    result = sensor._calculate_average_cycle_time()
    assert result == 20.0

    # Test with single cycle
    sensor._cycle_times = deque([30.0])
    result = sensor._calculate_average_cycle_time()
    assert result == 30.0
