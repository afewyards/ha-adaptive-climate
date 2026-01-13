"""Tests for learning metrics sensors."""
import pytest
import asyncio
from unittest.mock import Mock


def test_overshoot_sensor_value():
    """Test overshoot sensor calculates average correctly."""
    import sys
    from unittest.mock import Mock

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = object
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.TEMPERATURE = "temperature"
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

    from custom_components.adaptive_thermostat.sensor import OvershootSensor
    from custom_components.adaptive_thermostat.adaptive.learning import (
        CycleMetrics,
        AdaptiveLearner,
    )

    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()

    # Create adaptive learner with cycle history
    learner = AdaptiveLearner()

    # Add some cycle metrics
    cycle1 = CycleMetrics(
        overshoot=0.5,
        undershoot=0.0,
        settling_time=45.0,
        oscillations=2,
        rise_time=30.0,
    )
    cycle2 = CycleMetrics(
        overshoot=0.3,
        undershoot=0.1,
        settling_time=50.0,
        oscillations=3,
        rise_time=35.0,
    )
    cycle3 = CycleMetrics(
        overshoot=0.4,
        undershoot=0.0,
        settling_time=40.0,
        oscillations=1,
        rise_time=32.0,
    )

    learner.cycle_history = [cycle1, cycle2, cycle3]

    # Set up coordinator with zone data
    coordinator = Mock()
    coordinator.get_zone_data.return_value = {"adaptive_learner": learner}

    mock_hass.data = {"adaptive_thermostat": {"coordinator": coordinator}}

    # Create sensor
    sensor = OvershootSensor(
        mock_hass, "test_zone", "Test Zone", "climate.test_zone"
    )

    # Run async update
    result = asyncio.run(sensor.async_update())

    # Should be average of 0.5, 0.3, 0.4 = 0.4
    assert sensor.native_value == pytest.approx(0.4, abs=0.01)
    assert sensor._attr_name == "Test Zone Overshoot"
    assert sensor._attr_unique_id == "test_zone_overshoot"


def test_settling_time_in_minutes_conversion():
    """Test settling time sensor returns value in minutes."""
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

    from custom_components.adaptive_thermostat.sensor import SettlingTimeSensor
    from custom_components.adaptive_thermostat.adaptive.learning import (
        CycleMetrics,
        AdaptiveLearner,
    )

    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()

    # Create adaptive learner with cycle history
    learner = AdaptiveLearner()

    # Add some cycle metrics
    cycle1 = CycleMetrics(
        overshoot=0.5,
        undershoot=0.0,
        settling_time=45.0,
        oscillations=2,
        rise_time=30.0,
    )
    cycle2 = CycleMetrics(
        overshoot=0.3,
        undershoot=0.1,
        settling_time=50.0,
        oscillations=3,
        rise_time=35.0,
    )
    cycle3 = CycleMetrics(
        overshoot=0.4,
        undershoot=0.0,
        settling_time=40.0,
        oscillations=1,
        rise_time=32.0,
    )

    learner.cycle_history = [cycle1, cycle2, cycle3]

    # Set up coordinator with zone data
    coordinator = Mock()
    coordinator.get_zone_data.return_value = {"adaptive_learner": learner}

    mock_hass.data = {"adaptive_thermostat": {"coordinator": coordinator}}

    # Create sensor
    sensor = SettlingTimeSensor(
        mock_hass, "test_zone", "Test Zone", "climate.test_zone"
    )

    # Run async update
    result = asyncio.run(sensor.async_update())

    # Should be average of 45.0, 50.0, 40.0 = 45.0 minutes
    assert sensor.native_value == pytest.approx(45.0, abs=0.1)
    assert sensor._attr_name == "Test Zone Settling Time"
    assert sensor._attr_unique_id == "test_zone_settling_time"
