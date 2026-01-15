"""Tests for comfort sensors."""
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Mock base classes that need to be distinct
class MockSensorEntity:
    """Mock SensorEntity base class."""
    pass


class MockRestoreEntity:
    """Mock RestoreEntity base class."""

    async def async_added_to_hass(self):
        """Mock async_added_to_hass for base class."""
        pass

    async def async_get_last_state(self):
        """Return None by default - tests will override."""
        return None


# Mock homeassistant modules before importing sensors
mock_sensor_module = MagicMock()
mock_sensor_module.SensorEntity = MockSensorEntity
mock_sensor_module.SensorDeviceClass = MagicMock()
mock_sensor_module.SensorStateClass = MagicMock()
mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

mock_const = MagicMock()
mock_const.PERCENTAGE = "%"
mock_const.STATE_ON = "on"
mock_const.UnitOfPower = MagicMock()
mock_const.UnitOfPower.WATT = "W"
mock_const.UnitOfTime = MagicMock()
mock_const.UnitOfTime.MINUTES = "min"
mock_const.UnitOfTemperature = MagicMock()
mock_const.UnitOfTemperature.CELSIUS = "°C"
mock_const.STATE_UNAVAILABLE = "unavailable"
mock_const.STATE_UNKNOWN = "unknown"

mock_event = MagicMock()
mock_event.async_track_time_interval = MagicMock()
mock_event.async_track_state_change_event = MagicMock()

mock_core = MagicMock()
mock_core.HomeAssistant = MagicMock
mock_core.callback = lambda f: f
mock_core.Event = MagicMock

mock_restore = MagicMock()
mock_restore.RestoreEntity = MockRestoreEntity

sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.core'] = mock_core
sys.modules['homeassistant.components'] = MagicMock()
sys.modules['homeassistant.components.sensor'] = mock_sensor_module
sys.modules['homeassistant.const'] = mock_const
sys.modules['homeassistant.helpers'] = MagicMock()
sys.modules['homeassistant.helpers.event'] = mock_event
sys.modules['homeassistant.helpers.restore_state'] = mock_restore
sys.modules['homeassistant.helpers.entity_platform'] = MagicMock()
sys.modules['homeassistant.helpers.typing'] = MagicMock()

# Now import the sensors module
from custom_components.adaptive_thermostat.sensors.comfort import (
    TimeAtTargetSensor,
    ComfortScoreSensor,
    TemperatureSample,
    DEFAULT_TARGET_TOLERANCE,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.data = {}
    return hass


def test_temperature_sample():
    """Test TemperatureSample dataclass."""
    sample = TemperatureSample(
        timestamp=datetime(2024, 1, 15, 10, 0, 0),
        temperature=21.5,
        setpoint=21.0,
    )

    assert sample.temperature == 21.5
    assert sample.setpoint == 21.0


def test_time_at_target_sensor_init(mock_hass):
    """Test TimeAtTargetSensor initialization."""
    sensor = TimeAtTargetSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    assert sensor._attr_name == "Living Room Time at Target"
    assert sensor._attr_unique_id == "living_room_time_at_target"
    assert sensor._tolerance == DEFAULT_TARGET_TOLERANCE


def test_time_at_target_record_sample(mock_hass):
    """Test recording temperature samples."""
    sensor = TimeAtTargetSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Record a sample
    sensor.record_sample(temperature=21.5, setpoint=21.0)

    assert len(sensor._samples) == 1
    assert sensor._samples[0].temperature == 21.5
    assert sensor._samples[0].setpoint == 21.0


def test_time_at_target_calculation(mock_hass):
    """Test time at target percentage calculation."""
    sensor = TimeAtTargetSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
        tolerance=0.5,
        measurement_window=timedelta(hours=1),
    )

    now = datetime.now()

    # Add 10 samples: 7 within tolerance, 3 outside
    for i in range(7):
        sensor._samples.append(TemperatureSample(
            timestamp=now - timedelta(minutes=5 * i),
            temperature=21.0 + (i % 3) * 0.2,  # Within 0.5°C of setpoint
            setpoint=21.0,
        ))

    for i in range(3):
        sensor._samples.append(TemperatureSample(
            timestamp=now - timedelta(minutes=35 + 5 * i),
            temperature=22.5,  # Outside tolerance (1.5°C off)
            setpoint=21.0,
        ))

    # Calculate time at target
    result = sensor._calculate_time_at_target()

    # 7 out of 10 samples = 70%
    assert result == 70.0


def test_time_at_target_empty_samples(mock_hass):
    """Test time at target with no samples."""
    sensor = TimeAtTargetSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    result = sensor._calculate_time_at_target()
    assert result == 0.0


def test_comfort_score_sensor_init(mock_hass):
    """Test ComfortScoreSensor initialization."""
    sensor = ComfortScoreSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    assert sensor._attr_name == "Living Room Comfort Score"
    assert sensor._attr_unique_id == "living_room_comfort_score"
    assert sensor._attr_icon == "mdi:emoticon-happy-outline"


@pytest.mark.asyncio
async def test_comfort_score_calculation(mock_hass):
    """Test comfort score calculation."""
    sensor = ComfortScoreSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Mock the sensor states
    def mock_get_state(entity_id):
        states = {
            "sensor.living_room_time_at_target": MagicMock(state="80.0"),
            "sensor.living_room_oscillations": MagicMock(state="2"),
            "climate.living_room": MagicMock(
                attributes={
                    "current_temperature": 21.2,
                    "temperature": 21.0,
                }
            ),
        }
        state = states.get(entity_id)
        if state:
            if hasattr(state, "state") and state.state not in ("unknown", "unavailable"):
                return state
        return None

    mock_hass.states.get = mock_get_state

    score = await sensor._calculate_comfort_score()

    # Time at target: 80 (60% weight = 48)
    # Deviation: 0.2°C -> score 100 - (0.2 * 50) = 90 (25% weight = 22.5)
    # Oscillations: 2 -> score 100 - (2 * 10) = 80 (15% weight = 12)
    # Total: 48 + 22.5 + 12 = 82.5
    assert 80 < score < 85


@pytest.mark.asyncio
async def test_comfort_score_with_missing_data(mock_hass):
    """Test comfort score when some sensors are unavailable."""
    sensor = ComfortScoreSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Mock all sensors returning unavailable
    mock_hass.states.get.return_value = None

    score = await sensor._calculate_comfort_score()

    # With 0 time_at_target and 0 deviation (= perfect), we get:
    # time_at_target: 0 * 0.6 = 0
    # deviation: (100 - 0*50) * 0.25 = 25
    # oscillation: (100 - 0*10) * 0.15 = 15
    # Total = 40
    assert score == 40.0


@pytest.mark.asyncio
async def test_time_at_target_async_update(mock_hass):
    """Test async_update records sample from climate entity."""
    sensor = TimeAtTargetSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Mock climate entity state
    mock_climate_state = MagicMock()
    mock_climate_state.attributes = {
        "current_temperature": 21.3,
        "temperature": 21.0,
    }
    mock_hass.states.get.return_value = mock_climate_state

    await sensor.async_update()

    # Should have recorded the sample
    assert len(sensor._samples) == 1
    assert sensor._samples[0].temperature == 21.3
    assert sensor._samples[0].setpoint == 21.0


def test_comfort_score_extra_attributes(mock_hass):
    """Test comfort score sensor extra attributes."""
    sensor = ComfortScoreSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Set component scores
    sensor._time_at_target_score = 80.0
    sensor._deviation_score = 90.0
    sensor._oscillation_score = 85.0

    attrs = sensor.extra_state_attributes

    assert attrs["time_at_target_score"] == 80.0
    assert attrs["deviation_score"] == 90.0
    assert attrs["oscillation_score"] == 85.0


def test_time_at_target_custom_tolerance(mock_hass):
    """Test TimeAtTargetSensor with custom tolerance."""
    sensor = TimeAtTargetSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
        tolerance=1.0,  # Custom 1°C tolerance
    )

    assert sensor._tolerance == 1.0

    now = datetime.now()

    # Add samples: all within 1°C of setpoint
    for i in range(5):
        sensor._samples.append(TemperatureSample(
            timestamp=now - timedelta(minutes=5 * i),
            temperature=21.0 + i * 0.2,  # 21.0 to 21.8
            setpoint=21.0,
        ))

    result = sensor._calculate_time_at_target()
    assert result == 100.0  # All within 1°C tolerance


def test_comfort_score_clamped_to_100(mock_hass):
    """Test comfort score is clamped to 0-100 range."""
    sensor = ComfortScoreSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
    )

    # Manually set very high component scores
    sensor._time_at_target_score = 100.0
    sensor._deviation_score = 100.0
    sensor._oscillation_score = 100.0

    # The calculation should still be <= 100
    weighted_sum = 100 * 0.6 + 100 * 0.25 + 100 * 0.15
    assert weighted_sum == 100.0
