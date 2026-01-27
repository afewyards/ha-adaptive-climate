"""Tests for Adaptive Thermostat DutyCycleSensor.

These tests verify the real duty cycle calculation based on:
1. Tracking heater on/off state changes with timestamps
2. Calculating duty cycle as (on_time / total_time) over measurement window
3. Configurable measurement window (default 1 hour)
4. Edge cases: no state changes, always on, always off
5. Alternative: use control_output from PID controller as duty cycle
"""
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch
from collections import deque


def _setup_mocks():
    """Set up mock modules for Home Assistant dependencies."""
    # Mock voluptuous FIRST to avoid issues when __init__.py is loaded
    mock_vol = Mock()
    mock_vol.Schema = Mock(return_value=Mock())
    mock_vol.Optional = Mock(side_effect=lambda x, **kwargs: x)
    mock_vol.Required = Mock(side_effect=lambda x, **kwargs: x)
    mock_vol.Coerce = Mock(return_value=Mock())
    mock_vol.Range = Mock(return_value=Mock())
    mock_vol.In = Mock(return_value=Mock())
    mock_vol.ALLOW_EXTRA = "ALLOW_EXTRA"
    mock_vol.Invalid = Exception
    sys.modules['voluptuous'] = mock_vol

    # Create distinct base classes to avoid MRO conflicts
    class MockSensorEntity:
        pass

    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = MockSensorEntity
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.POWER = "power"
    mock_sensor_module.SensorDeviceClass.DURATION = "duration"
    mock_sensor_module.SensorDeviceClass.TEMPERATURE = "temperature"
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.MEASUREMENT = "measurement"

    mock_const = Mock()
    mock_const.PERCENTAGE = "%"
    mock_const.STATE_ON = "on"
    mock_const.STATE_UNAVAILABLE = "unavailable"
    mock_const.STATE_UNKNOWN = "unknown"
    mock_const.UnitOfPower = Mock()
    mock_const.UnitOfPower.WATT = "W"
    mock_const.UnitOfPower.KILO_WATT = "kW"
    mock_const.UnitOfTime = Mock()
    mock_const.UnitOfTime.MINUTES = "min"
    mock_const.UnitOfTemperature = Mock()
    mock_const.UnitOfTemperature.CELSIUS = "°C"

    mock_event = Mock()
    mock_event.async_track_time_interval = Mock()
    mock_event.async_track_state_change_event = Mock()

    mock_core = Mock()
    mock_core.HomeAssistant = Mock
    mock_core.callback = lambda f: f
    mock_core.Event = Mock

    # Create a distinct class for RestoreEntity to avoid duplicate base class error
    class MockRestoreEntity:
        async def async_added_to_hass(self):
            """Mock async_added_to_hass for base class."""
            pass

        async def async_get_last_state(self):
            """Return None by default - tests will override."""
            return None

    mock_restore_state = Mock()
    mock_restore_state.RestoreEntity = MockRestoreEntity

    # Mock device_registry for DeviceInfo
    mock_device_registry = Mock()
    mock_device_registry.DeviceInfo = dict  # DeviceInfo is essentially a TypedDict

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
    sys.modules['homeassistant.helpers.device_registry'] = mock_device_registry


# Set up mocks before importing the module
_setup_mocks()


# Now we can safely import the module under test
from custom_components.adaptive_thermostat.sensor import (
    DutyCycleSensor,
    CycleTimeSensor,
    HeatOutputSensor,
    HeaterStateChange,
    DEFAULT_DUTY_CYCLE_WINDOW,
    DEFAULT_ROLLING_AVERAGE_SIZE,
    AdaptiveThermostatSensor,
)
from custom_components.adaptive_thermostat.analytics.heat_output import (
    HeatOutputCalculator,
    calculate_heat_output_kw,
    SPECIFIC_HEAT_WATER,
)

# Define DOMAIN inline to avoid importing __init__.py (which needs voluptuous mocks)
DOMAIN = "adaptive_thermostat"


class TestHeaterStateChange:
    """Tests for HeaterStateChange dataclass."""

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_heater_state_change_creation(self, mock_dt_util):
        """Test creating HeaterStateChange instances."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        change = HeaterStateChange(timestamp=now, is_on=True)

        assert change.timestamp == now
        assert change.is_on is True

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_heater_state_change_off(self, mock_dt_util):
        """Test HeaterStateChange with off state."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        change = HeaterStateChange(timestamp=now, is_on=False)

        assert change.is_on is False


class TestDutyCycleCalculation:
    """Tests for DutyCycleSensor duty cycle calculation."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def duty_cycle_sensor(self, mock_hass):
        """Create a DutyCycleSensor instance for testing."""
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="living_room",
            zone_name="Living Room",
            climate_entity_id="climate.living_room",
            measurement_window=timedelta(hours=1),
        )
        return sensor

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_50_percent(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle calculation with 50% on/off pattern."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Create state changes: 30 minutes on, 30 minutes off
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=30), is_on=False),
        ])

        # Calculate duty cycle
        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 50%
        assert duty_cycle == pytest.approx(50.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_25_percent(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle calculation with 25% on time."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Create state changes: 15 minutes on, 45 minutes off
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=15), is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 25%
        assert duty_cycle == pytest.approx(25.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_75_percent(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle calculation with 75% on time."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Create state changes: 45 minutes on, 15 minutes off
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=45), is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 75%
        assert duty_cycle == pytest.approx(75.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_multiple_cycles(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle with multiple on/off cycles."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Create multiple cycles: 10 min on, 10 min off, 10 min on, 10 min off...
        # Total 30 minutes on out of 60 = 50%
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=10), is_on=False),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=20), is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=30), is_on=False),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=40), is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=50), is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 50%
        assert duty_cycle == pytest.approx(50.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_varied_cycle_lengths(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle with varied on/off cycle lengths."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Varied cycles: 5 min on, 15 min off, 20 min on, 10 min off, 5 min on, 5 min off
        # Total on: 5 + 20 + 5 = 30 min out of 60 = 50%
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=5), is_on=False),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=20), is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=40), is_on=False),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=50), is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=55), is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 50%
        assert duty_cycle == pytest.approx(50.0, rel=0.01)


class TestDutyCycleEdgeCases:
    """Tests for edge cases in DutyCycleSensor."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def duty_cycle_sensor(self, mock_hass):
        """Create a DutyCycleSensor instance for testing."""
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="living_room",
            zone_name="Living Room",
            climate_entity_id="climate.living_room",
            measurement_window=timedelta(hours=1),
        )
        return sensor

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_always_on(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle when heater is always on (100%)."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Heater turned on at window start and stayed on
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be 100%
        assert duty_cycle == pytest.approx(100.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_always_off(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle when heater is always off (0%)."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Heater turned off at window start and stayed off
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be 0%
        assert duty_cycle == pytest.approx(0.0, rel=0.01)

    def test_duty_cycle_no_state_changes_with_control_output(self, duty_cycle_sensor, mock_hass):
        """Test fallback to control_output when no state changes."""
        duty_cycle_sensor._state_changes = deque()

        # Mock climate entity with control_output attribute
        climate_state = Mock()
        climate_state.attributes = {"control_output": 65.0}
        mock_hass.states.get.return_value = climate_state

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should use control_output value
        assert duty_cycle == pytest.approx(65.0, rel=0.01)

    def test_duty_cycle_no_state_changes_no_control_output(self, duty_cycle_sensor, mock_hass):
        """Test fallback when no state changes and no control_output."""
        duty_cycle_sensor._state_changes = deque()

        # Mock climate entity without control_output
        climate_state = Mock()
        climate_state.attributes = {}
        mock_hass.states.get.return_value = climate_state

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should return 0%
        assert duty_cycle == pytest.approx(0.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_heater_on_at_end_of_window(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle when heater turns on near end of window."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Heater off for 50 minutes, then on for 10 minutes at end
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=False),
            HeaterStateChange(timestamp=now - timedelta(minutes=10), is_on=True),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 16.67% (10/60)
        assert duty_cycle == pytest.approx(16.67, rel=0.05)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_state_change_before_window(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle with state change before measurement window."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # State change happened 2 hours ago (before window), heater was on
        # Then turned off 30 minutes ago
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=now - timedelta(hours=2), is_on=True),
            HeaterStateChange(timestamp=now - timedelta(minutes=30), is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # First 30 minutes of window heater was on (from previous state)
        # Last 30 minutes heater was off
        # Should be approximately 50%
        assert duty_cycle == pytest.approx(50.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_duty_cycle_very_short_cycles(self, mock_dt_util, duty_cycle_sensor):
        """Test duty cycle with very short on/off cycles."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Many short 1-minute cycles
        changes = []
        for i in range(60):
            # Alternating on/off each minute
            changes.append(
                HeaterStateChange(
                    timestamp=window_start + timedelta(minutes=i),
                    is_on=(i % 2 == 0)
                )
            )

        duty_cycle_sensor._state_changes = deque(changes)

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 50%
        assert duty_cycle == pytest.approx(50.0, rel=0.05)


class TestControlOutputFallback:
    """Tests for control_output fallback functionality."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def duty_cycle_sensor(self, mock_hass):
        """Create a DutyCycleSensor instance for testing."""
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="living_room",
            zone_name="Living Room",
            climate_entity_id="climate.living_room",
        )
        return sensor

    def test_control_output_as_duty_cycle(self, duty_cycle_sensor, mock_hass):
        """Test using control_output as duty cycle."""
        climate_state = Mock()
        climate_state.attributes = {"control_output": 75.5}
        mock_hass.states.get.return_value = climate_state

        duty_cycle = duty_cycle_sensor._get_control_output_duty_cycle()

        assert duty_cycle == pytest.approx(75.5, rel=0.01)

    def test_control_output_clamped_to_max(self, duty_cycle_sensor, mock_hass):
        """Test control_output is clamped to 100% max."""
        climate_state = Mock()
        climate_state.attributes = {"control_output": 150.0}
        mock_hass.states.get.return_value = climate_state

        duty_cycle = duty_cycle_sensor._get_control_output_duty_cycle()

        assert duty_cycle == 100.0

    def test_control_output_clamped_to_min(self, duty_cycle_sensor, mock_hass):
        """Test control_output is clamped to 0% min."""
        climate_state = Mock()
        climate_state.attributes = {"control_output": -25.0}
        mock_hass.states.get.return_value = climate_state

        duty_cycle = duty_cycle_sensor._get_control_output_duty_cycle()

        assert duty_cycle == 0.0

    def test_control_output_invalid_value(self, duty_cycle_sensor, mock_hass):
        """Test handling invalid control_output value."""
        climate_state = Mock()
        climate_state.attributes = {"control_output": "invalid"}
        mock_hass.states.get.return_value = climate_state

        duty_cycle = duty_cycle_sensor._get_control_output_duty_cycle()

        assert duty_cycle == 0.0

    def test_no_climate_entity(self, duty_cycle_sensor, mock_hass):
        """Test when climate entity doesn't exist."""
        mock_hass.states.get.return_value = None

        duty_cycle = duty_cycle_sensor._get_control_output_duty_cycle()

        assert duty_cycle == 0.0


class TestMeasurementWindow:
    """Tests for configurable measurement window."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    def test_default_measurement_window(self, mock_hass):
        """Test default measurement window is 1 hour."""
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
        )

        assert sensor._measurement_window == DEFAULT_DUTY_CYCLE_WINDOW
        assert sensor._measurement_window == timedelta(hours=1)

    def test_custom_measurement_window(self, mock_hass):
        """Test custom measurement window."""
        custom_window = timedelta(minutes=30)
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            measurement_window=custom_window,
        )

        assert sensor._measurement_window == custom_window

    def test_measurement_window_in_attributes(self, mock_hass):
        """Test measurement window is exposed in extra_state_attributes."""
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            measurement_window=timedelta(minutes=45),
        )

        attrs = sensor.extra_state_attributes

        assert attrs["measurement_window_minutes"] == 45.0


class TestOnTimeCalculation:
    """Tests for the _calculate_on_time method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def duty_cycle_sensor(self, mock_hass):
        """Create a DutyCycleSensor instance for testing."""
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            measurement_window=timedelta(hours=1),
        )
        return sensor

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_calculate_on_time_simple(self, mock_dt_util, duty_cycle_sensor):
        """Test simple on-time calculation."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=30), is_on=False),
        ])

        on_time = duty_cycle_sensor._calculate_on_time(window_start, now)

        # Should be 30 minutes = 1800 seconds
        assert on_time == pytest.approx(1800.0, rel=0.01)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_calculate_on_time_with_final_on_period(self, mock_dt_util, duty_cycle_sensor):
        """Test on-time calculation when heater is on at window end."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Heater turns on 10 minutes before now and stays on
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=False),
            HeaterStateChange(timestamp=now - timedelta(minutes=10), is_on=True),
        ])

        on_time = duty_cycle_sensor._calculate_on_time(window_start, now)

        # Should be 10 minutes = 600 seconds
        assert on_time == pytest.approx(600.0, rel=0.01)


class TestStateChangePruning:
    """Tests for state change pruning functionality."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def duty_cycle_sensor(self, mock_hass):
        """Create a DutyCycleSensor instance for testing."""
        sensor = DutyCycleSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            measurement_window=timedelta(hours=1),
        )
        return sensor

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_prune_keeps_recent_before_window(self, mock_dt_util, duty_cycle_sensor):
        """Test pruning keeps most recent state before window."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # Add states: some before window, some within
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=now - timedelta(hours=3), is_on=False),
            HeaterStateChange(timestamp=now - timedelta(hours=2), is_on=True),
            HeaterStateChange(timestamp=now - timedelta(minutes=30), is_on=False),
        ])

        duty_cycle_sensor._prune_old_state_changes(window_start)

        # Should keep: last one before window (2 hours ago) and one within window
        assert len(duty_cycle_sensor._state_changes) == 2

        # First should be the one from 2 hours ago (most recent before window)
        changes = list(duty_cycle_sensor._state_changes)
        assert changes[0].is_on is True  # The one from 2 hours ago

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_prune_removes_all_old(self, mock_dt_util, duty_cycle_sensor):
        """Test pruning when all states are before window but keeps one."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        window_start = now - timedelta(hours=1)

        # All states before window
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=now - timedelta(hours=5), is_on=False),
            HeaterStateChange(timestamp=now - timedelta(hours=4), is_on=True),
            HeaterStateChange(timestamp=now - timedelta(hours=3), is_on=False),
        ])

        duty_cycle_sensor._prune_old_state_changes(window_start)

        # Should keep only the most recent one before window
        assert len(duty_cycle_sensor._state_changes) == 1
        assert duty_cycle_sensor._state_changes[0].is_on is False


@patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
def test_duty_cycle(mock_dt_util):
    """Integration test for duty cycle calculation.

    This is the main test that verifies the full duty cycle calculation
    works correctly end-to-end.
    """
    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()
    mock_hass.data = {}

    # Create sensor with 1 hour window
    sensor = DutyCycleSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
        measurement_window=timedelta(hours=1),
    )

    now = datetime.now()
    mock_dt_util.utcnow.return_value = now
    window_start = now - timedelta(hours=1)

    # Test 1: 50% duty cycle
    sensor._state_changes = deque([
        HeaterStateChange(timestamp=window_start, is_on=True),
        HeaterStateChange(timestamp=window_start + timedelta(minutes=30), is_on=False),
    ])
    duty_cycle = sensor._calculate_duty_cycle()
    assert duty_cycle == pytest.approx(50.0, rel=0.01), "50% duty cycle failed"

    # Test 2: 100% duty cycle (always on)
    sensor._state_changes = deque([
        HeaterStateChange(timestamp=window_start, is_on=True),
    ])
    duty_cycle = sensor._calculate_duty_cycle()
    assert duty_cycle == pytest.approx(100.0, rel=0.01), "100% duty cycle failed"

    # Test 3: 0% duty cycle (always off)
    sensor._state_changes = deque([
        HeaterStateChange(timestamp=window_start, is_on=False),
    ])
    duty_cycle = sensor._calculate_duty_cycle()
    assert duty_cycle == pytest.approx(0.0, rel=0.01), "0% duty cycle failed"

    # Test 4: Control output fallback
    sensor._state_changes = deque()
    climate_state = Mock()
    climate_state.attributes = {"control_output": 42.0}
    mock_hass.states.get.return_value = climate_state
    duty_cycle = sensor._calculate_duty_cycle()
    assert duty_cycle == pytest.approx(42.0, rel=0.01), "Control output fallback failed"

    print("All duty cycle tests passed!")


# ============================================================================
# CycleTimeSensor Tests
# ============================================================================


class TestCycleTimeCalculation:
    """Tests for CycleTimeSensor cycle time calculation."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def cycle_time_sensor(self, mock_hass):
        """Create a CycleTimeSensor instance for testing."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="living_room",
            zone_name="Living Room",
            climate_entity_id="climate.living_room",
            rolling_average_size=10,
        )
        return sensor

    def test_cycle_time_single_cycle(self, cycle_time_sensor):
        """Test cycle time with one complete cycle (ON->OFF->ON)."""
        # Simulate one complete cycle of 20 minutes
        cycle_time_sensor._cycle_times = deque([20.0])

        avg_time = cycle_time_sensor._calculate_average_cycle_time()

        assert avg_time == pytest.approx(20.0, rel=0.01)

    def test_cycle_time_no_complete_cycles(self, cycle_time_sensor):
        """Test returns None when no complete cycles yet."""
        # No cycles recorded
        cycle_time_sensor._cycle_times = deque()

        avg_time = cycle_time_sensor._calculate_average_cycle_time()

        assert avg_time is None

    def test_cycle_time_multiple_cycles(self, cycle_time_sensor):
        """Test average calculation with multiple cycles."""
        # Multiple cycles: 15, 20, 25 minutes = average 20 minutes
        cycle_time_sensor._cycle_times = deque([15.0, 20.0, 25.0])

        avg_time = cycle_time_sensor._calculate_average_cycle_time()

        assert avg_time == pytest.approx(20.0, rel=0.01)

    def test_cycle_time_varied_cycles(self, cycle_time_sensor):
        """Test with varied cycle times."""
        # Cycles: 10, 15, 20, 25, 30 minutes = average 20 minutes
        cycle_time_sensor._cycle_times = deque([10.0, 15.0, 20.0, 25.0, 30.0])

        avg_time = cycle_time_sensor._calculate_average_cycle_time()

        assert avg_time == pytest.approx(20.0, rel=0.01)


class TestCycleTimeRollingAverage:
    """Tests for CycleTimeSensor rolling average behavior."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    def test_rolling_average_maxlen(self, mock_hass):
        """Test old cycles are evicted when maxlen exceeded."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            rolling_average_size=5,
        )

        # Add more cycles than maxlen
        for i in range(10):
            sensor._cycle_times.append(float(i * 10))

        # Should only keep last 5: 50, 60, 70, 80, 90
        assert len(sensor._cycle_times) == 5
        assert list(sensor._cycle_times) == [50.0, 60.0, 70.0, 80.0, 90.0]

    def test_rolling_average_updates_correctly(self, mock_hass):
        """Test rolling average updates correctly with new cycles."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            rolling_average_size=3,
        )

        # First 3 cycles
        sensor._cycle_times.append(10.0)
        sensor._cycle_times.append(20.0)
        sensor._cycle_times.append(30.0)
        assert sensor._calculate_average_cycle_time() == pytest.approx(20.0, rel=0.01)

        # Add one more, first gets evicted
        sensor._cycle_times.append(40.0)
        # Now: 20, 30, 40 = average 30
        assert sensor._calculate_average_cycle_time() == pytest.approx(30.0, rel=0.01)

    def test_rolling_average_varied_cycle_times(self, mock_hass):
        """Test average with varied cycle times."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            rolling_average_size=10,
        )

        # Varied cycle times representing realistic floor heating
        cycle_times = [18.5, 22.3, 19.8, 21.0, 20.5, 23.1, 17.9, 20.2, 21.5, 19.2]
        for ct in cycle_times:
            sensor._cycle_times.append(ct)

        avg = sensor._calculate_average_cycle_time()
        expected = sum(cycle_times) / len(cycle_times)
        assert avg == pytest.approx(expected, rel=0.01)


class TestCycleTimeStateTracking:
    """Tests for CycleTimeSensor state change tracking."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def cycle_time_sensor(self, mock_hass):
        """Create a CycleTimeSensor instance for testing."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
        )
        return sensor

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_heater_state_change_on_to_off(self, mock_dt_util, cycle_time_sensor):
        """Test heater state change from ON to OFF."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        cycle_time_sensor._current_heater_state = True
        cycle_time_sensor._last_on_timestamp = now - timedelta(minutes=10)

        # Simulate OFF event
        event = Mock()
        event.data = {"new_state": Mock(state="off")}

        cycle_time_sensor._async_heater_state_changed(event)

        # State should be OFF now
        assert cycle_time_sensor._current_heater_state is False
        # No cycle recorded yet (need ON->OFF->ON)
        assert len(cycle_time_sensor._cycle_times) == 0

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_heater_state_change_off_to_on_first_time(self, mock_dt_util, cycle_time_sensor):
        """Test first heater state change from OFF to ON (no previous ON timestamp)."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        cycle_time_sensor._current_heater_state = False
        cycle_time_sensor._last_on_timestamp = None

        # Simulate ON event
        event = Mock()
        event.data = {"new_state": Mock(state="on")}

        cycle_time_sensor._async_heater_state_changed(event)

        # State should be ON now
        assert cycle_time_sensor._current_heater_state is True
        # last_on_timestamp should be set
        assert cycle_time_sensor._last_on_timestamp is not None
        # No cycle recorded (this is the first ON)
        assert len(cycle_time_sensor._cycle_times) == 0

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_heater_state_change_records_cycle(self, mock_dt_util, cycle_time_sensor):
        """Test complete cycle recording (ON->OFF->ON)."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        # Set up: heater was ON 20 minutes ago, then turned OFF, now turning ON again
        cycle_time_sensor._current_heater_state = False
        cycle_time_sensor._last_on_timestamp = now - timedelta(minutes=20)

        # Simulate ON event (completing a cycle)
        event = Mock()
        event.data = {"new_state": Mock(state="on")}

        cycle_time_sensor._async_heater_state_changed(event)

        # Should have recorded 1 cycle of ~20 minutes
        assert len(cycle_time_sensor._cycle_times) == 1
        # Allow some tolerance for test execution time
        assert cycle_time_sensor._cycle_times[0] == pytest.approx(20.0, rel=0.1)

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_heater_state_change_short_cycle_filtered(self, mock_dt_util, cycle_time_sensor):
        """Test short cycles (< 1 min) are filtered out."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        # Set up: heater was ON just 30 seconds ago
        cycle_time_sensor._current_heater_state = False
        cycle_time_sensor._last_on_timestamp = now - timedelta(seconds=30)

        # Simulate ON event
        event = Mock()
        event.data = {"new_state": Mock(state="on")}

        cycle_time_sensor._async_heater_state_changed(event)

        # Should NOT record this cycle (too short)
        assert len(cycle_time_sensor._cycle_times) == 0

    @patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
    def test_heater_state_no_change_ignored(self, mock_dt_util, cycle_time_sensor):
        """Test same state events are ignored."""
        now = datetime.now()
        mock_dt_util.utcnow.return_value = now
        cycle_time_sensor._current_heater_state = True
        cycle_time_sensor._last_on_timestamp = now - timedelta(minutes=10)

        # Simulate ON event when already ON
        event = Mock()
        event.data = {"new_state": Mock(state="on")}

        cycle_time_sensor._async_heater_state_changed(event)

        # State should still be ON, no cycle recorded
        assert cycle_time_sensor._current_heater_state is True
        assert len(cycle_time_sensor._cycle_times) == 0

    def test_heater_state_new_state_none_ignored(self, cycle_time_sensor):
        """Test events with None new_state are ignored."""
        cycle_time_sensor._current_heater_state = True

        # Simulate event with no new_state
        event = Mock()
        event.data = {"new_state": None}

        cycle_time_sensor._async_heater_state_changed(event)

        # State should be unchanged
        assert cycle_time_sensor._current_heater_state is True


class TestCycleTimeExtraAttributes:
    """Tests for CycleTimeSensor extra state attributes."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    def test_extra_attributes_empty(self, mock_hass):
        """Test extra_state_attributes when no cycles recorded."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
        )

        attrs = sensor.extra_state_attributes

        assert attrs["cycle_count"] == 0
        assert attrs["rolling_average_size"] == DEFAULT_ROLLING_AVERAGE_SIZE
        assert attrs["last_cycle_time_minutes"] is None

    def test_extra_attributes_with_cycles(self, mock_hass):
        """Test extra_state_attributes with cycles recorded."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            rolling_average_size=5,
        )
        sensor._heater_entity_id = "switch.test_heater"
        sensor._cycle_times = deque([15.0, 20.0, 25.0])

        attrs = sensor.extra_state_attributes

        assert attrs["cycle_count"] == 3
        assert attrs["rolling_average_size"] == 5
        assert attrs["heater_entity_id"] == "switch.test_heater"
        assert attrs["last_cycle_time_minutes"] == 25.0


class TestCycleTimeDefaults:
    """Tests for CycleTimeSensor default values."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    def test_default_rolling_average_size(self, mock_hass):
        """Test default rolling average size is 10."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
        )

        assert sensor._rolling_average_size == DEFAULT_ROLLING_AVERAGE_SIZE
        assert sensor._rolling_average_size == 10

    def test_custom_rolling_average_size(self, mock_hass):
        """Test custom rolling average size."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            rolling_average_size=20,
        )

        assert sensor._rolling_average_size == 20

    def test_initial_state_is_none(self, mock_hass):
        """Test initial native_value is None."""
        sensor = CycleTimeSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
        )

        assert sensor.native_value is None


@patch('custom_components.adaptive_thermostat.sensors.performance.dt_util')
def test_cycle_time(mock_dt_util):
    """Integration test for cycle time calculation.

    This is the main test that verifies the full cycle time calculation
    works correctly end-to-end.
    """
    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()
    mock_hass.data = {}

    # Create sensor
    sensor = CycleTimeSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
        rolling_average_size=5,
    )

    now = datetime.now()
    mock_dt_util.utcnow.return_value = now

    # Test 1: No cycles - should return None
    avg = sensor._calculate_average_cycle_time()
    assert avg is None, "No cycles should return None"

    # Test 2: Single cycle
    sensor._cycle_times.append(20.0)
    avg = sensor._calculate_average_cycle_time()
    assert avg == pytest.approx(20.0, rel=0.01), "Single cycle average failed"

    # Test 3: Multiple cycles - average
    sensor._cycle_times.clear()
    sensor._cycle_times.extend([15.0, 20.0, 25.0])
    avg = sensor._calculate_average_cycle_time()
    assert avg == pytest.approx(20.0, rel=0.01), "Multiple cycle average failed"

    # Test 4: Rolling average eviction
    sensor._cycle_times.clear()
    # Add 7 cycles to a size-5 deque
    for ct in [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0]:
        sensor._cycle_times.append(ct)
    # Should only have last 5: 20, 25, 30, 35, 40 = avg 30
    assert len(sensor._cycle_times) == 5, "Rolling window size failed"
    avg = sensor._calculate_average_cycle_time()
    assert avg == pytest.approx(30.0, rel=0.01), "Rolling average failed"

    # Test 5: State transition recording
    sensor._cycle_times.clear()
    sensor._current_heater_state = False
    sensor._last_on_timestamp = now - timedelta(minutes=25)

    event = Mock()
    event.data = {"new_state": Mock(state="on")}
    sensor._async_heater_state_changed(event)

    assert len(sensor._cycle_times) == 1, "Cycle should be recorded"
    assert sensor._cycle_times[0] == pytest.approx(25.0, rel=0.1), "Recorded cycle time incorrect"

    print("All cycle time tests passed!")


# ============================================================================
# HeatOutputSensor Tests
# ============================================================================


class TestHeatOutputCalculation:
    """Tests for HeatOutputSensor heat output calculation."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def heat_output_sensor(self, mock_hass):
        """Create a HeatOutputSensor instance for testing."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="living_room",
            zone_name="Living Room",
            climate_entity_id="climate.living_room",
            supply_temp_sensor="sensor.supply_temp",
            return_temp_sensor="sensor.return_temp",
            flow_rate_sensor="sensor.flow_rate",
            fallback_flow_rate=0.5,
        )
        return sensor

    def test_heat_output_basic_calculation(self, heat_output_sensor, mock_hass):
        """Test basic heat output calculation with typical values."""
        # Set up mock states: supply=40C, return=30C, flow=0.5 L/min
        def get_state(entity_id):
            states = {
                "sensor.supply_temp": Mock(state="40.0"),
                "sensor.return_temp": Mock(state="30.0"),
                "sensor.flow_rate": Mock(state="0.5"),
            }
            return states.get(entity_id)

        mock_hass.states.get = get_state

        # Get sensor value using internal method
        supply_temp = heat_output_sensor._get_sensor_value("sensor.supply_temp")
        return_temp = heat_output_sensor._get_sensor_value("sensor.return_temp")
        flow_rate = heat_output_sensor._get_sensor_value("sensor.flow_rate")

        assert supply_temp == 40.0
        assert return_temp == 30.0
        assert flow_rate == 0.5

        # Calculate expected heat output: Q = m * cp * delta_T
        # m = 0.5 L/min * (1 kg/L) / 60 = 0.00833 kg/s
        # delta_T = 10 C
        # Q = 0.00833 * 4.186 * 10 = 0.349 kW
        expected_kw = calculate_heat_output_kw(40.0, 30.0, 0.5)
        assert expected_kw == pytest.approx(0.349, rel=0.01)

    def test_heat_output_with_fallback_flow_rate(self, mock_hass):
        """Test heat output calculation using fallback flow rate."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            supply_temp_sensor="sensor.supply_temp",
            return_temp_sensor="sensor.return_temp",
            flow_rate_sensor=None,  # No flow rate sensor
            fallback_flow_rate=1.0,  # 1 L/min fallback
        )

        # Set up mock states without flow rate sensor
        def get_state(entity_id):
            states = {
                "sensor.supply_temp": Mock(state="45.0"),
                "sensor.return_temp": Mock(state="35.0"),
            }
            return states.get(entity_id)

        mock_hass.states.get = get_state

        # Calculate using calculator with fallback
        heat_output = sensor._calculator.calculate_with_fallback(
            supply_temp_c=45.0,
            return_temp_c=35.0,
            measured_flow_rate_lpm=None,  # Will use fallback
        )

        # Expected: m = 1.0/60 kg/s, delta_T = 10, Q = (1/60) * 4.186 * 10 = 0.698 kW
        expected = calculate_heat_output_kw(45.0, 35.0, 1.0)
        assert heat_output == pytest.approx(expected, rel=0.01)

    def test_heat_output_missing_supply_temp(self, heat_output_sensor, mock_hass):
        """Test returns None when supply temperature is unavailable."""
        def get_state(entity_id):
            if entity_id == "sensor.supply_temp":
                return None  # Unavailable
            return Mock(state="30.0")

        mock_hass.states.get = get_state

        supply_temp = heat_output_sensor._get_sensor_value("sensor.supply_temp")
        assert supply_temp is None

    def test_heat_output_missing_return_temp(self, heat_output_sensor, mock_hass):
        """Test returns None when return temperature is unavailable."""
        def get_state(entity_id):
            if entity_id == "sensor.return_temp":
                return Mock(state="unavailable")
            return Mock(state="40.0")

        mock_hass.states.get = get_state

        return_temp = heat_output_sensor._get_sensor_value("sensor.return_temp")
        # "unavailable" should return None
        assert return_temp is None

    def test_heat_output_invalid_temperatures(self, mock_hass):
        """Test returns None when supply <= return (invalid)."""
        calculator = HeatOutputCalculator(fallback_flow_rate_lpm=0.5)

        # Supply temp lower than return - invalid
        result = calculator.calculate_with_fallback(
            supply_temp_c=30.0,
            return_temp_c=40.0,  # Higher than supply - invalid
            measured_flow_rate_lpm=0.5,
        )

        assert result is None


class TestHeatOutputEdgeCases:
    """Tests for edge cases in HeatOutputSensor."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    def test_heat_output_sensor_unavailable_state(self, mock_hass):
        """Test handling of unavailable sensor state."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            supply_temp_sensor="sensor.supply_temp",
            return_temp_sensor="sensor.return_temp",
        )

        mock_hass.states.get.return_value = Mock(state="unavailable")

        value = sensor._get_sensor_value("sensor.supply_temp")
        assert value is None

    def test_heat_output_sensor_unknown_state(self, mock_hass):
        """Test handling of unknown sensor state."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            supply_temp_sensor="sensor.supply_temp",
            return_temp_sensor="sensor.return_temp",
        )

        mock_hass.states.get.return_value = Mock(state="unknown")

        value = sensor._get_sensor_value("sensor.supply_temp")
        assert value is None

    def test_heat_output_sensor_invalid_value(self, mock_hass):
        """Test handling of non-numeric sensor value."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            supply_temp_sensor="sensor.supply_temp",
            return_temp_sensor="sensor.return_temp",
        )

        mock_hass.states.get.return_value = Mock(state="not_a_number")

        value = sensor._get_sensor_value("sensor.supply_temp")
        assert value is None

    def test_heat_output_sensor_no_entity_id(self, mock_hass):
        """Test handling of None entity ID."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            supply_temp_sensor=None,
            return_temp_sensor=None,
        )

        value = sensor._get_sensor_value(None)
        assert value is None

    def test_heat_output_small_delta_t(self, mock_hass):
        """Test heat output with small temperature difference."""
        calculator = HeatOutputCalculator(fallback_flow_rate_lpm=0.5)

        # Small delta-T (1 degree)
        result = calculator.calculate_with_fallback(
            supply_temp_c=35.0,
            return_temp_c=34.0,
            measured_flow_rate_lpm=0.5,
        )

        # Q = (0.5/60) * 4.186 * 1 = 0.0349 kW
        expected = calculate_heat_output_kw(35.0, 34.0, 0.5)
        assert result == pytest.approx(expected, rel=0.01)

    def test_heat_output_high_flow_rate(self, mock_hass):
        """Test heat output with high flow rate."""
        calculator = HeatOutputCalculator(fallback_flow_rate_lpm=5.0)

        # High flow rate (5 L/min)
        result = calculator.calculate_with_fallback(
            supply_temp_c=45.0,
            return_temp_c=35.0,
            measured_flow_rate_lpm=5.0,
        )

        # Q = (5/60) * 4.186 * 10 = 3.49 kW
        expected = calculate_heat_output_kw(45.0, 35.0, 5.0)
        assert result == pytest.approx(expected, rel=0.01)


class TestHeatOutputExtraAttributes:
    """Tests for HeatOutputSensor extra state attributes."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    def test_extra_attributes_initial(self, mock_hass):
        """Test extra_state_attributes when sensor is initialized."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            supply_temp_sensor="sensor.supply_temp",
            return_temp_sensor="sensor.return_temp",
            flow_rate_sensor="sensor.flow_rate",
            fallback_flow_rate=0.5,
        )

        attrs = sensor.extra_state_attributes

        assert attrs["supply_temp_sensor"] == "sensor.supply_temp"
        assert attrs["return_temp_sensor"] == "sensor.return_temp"
        assert attrs["flow_rate_sensor"] == "sensor.flow_rate"
        assert attrs["fallback_flow_rate_lpm"] == 0.5
        assert attrs["supply_temp_c"] is None
        assert attrs["return_temp_c"] is None
        assert attrs["flow_rate_lpm"] is None
        assert attrs["delta_t_c"] is None

    def test_extra_attributes_without_flow_sensor(self, mock_hass):
        """Test extra_state_attributes without flow rate sensor."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="test",
            zone_name="Test",
            climate_entity_id="climate.test",
            supply_temp_sensor="sensor.supply_temp",
            return_temp_sensor="sensor.return_temp",
            flow_rate_sensor=None,
            fallback_flow_rate=1.0,
        )

        attrs = sensor.extra_state_attributes

        assert attrs["flow_rate_sensor"] is None
        assert attrs["fallback_flow_rate_lpm"] == 1.0


class TestHeatOutputSensorDefaults:
    """Tests for HeatOutputSensor default values."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    def test_sensor_attributes(self, mock_hass):
        """Test sensor has correct attributes."""
        sensor = HeatOutputSensor(
            hass=mock_hass,
            zone_id="living_room",
            zone_name="Living Room",
            climate_entity_id="climate.living_room",
            supply_temp_sensor="sensor.supply",
            return_temp_sensor="sensor.return",
        )

        assert sensor._attr_name == "Living Room Heat Output"
        assert sensor._attr_unique_id == "living_room_heat_output"
        assert sensor._attr_icon == "mdi:radiator"
        assert sensor.native_value is None


def test_heat_output():
    """Integration test for heat output calculation.

    This is the main test that verifies the full heat output calculation
    works correctly end-to-end.
    """
    # Create mock hass
    mock_hass = Mock()
    mock_hass.states = Mock()
    mock_hass.data = {}

    # Create sensor
    sensor = HeatOutputSensor(
        hass=mock_hass,
        zone_id="living_room",
        zone_name="Living Room",
        climate_entity_id="climate.living_room",
        supply_temp_sensor="sensor.supply_temp",
        return_temp_sensor="sensor.return_temp",
        flow_rate_sensor="sensor.flow_rate",
        fallback_flow_rate=0.5,
    )

    # Test 1: Basic heat output calculation using the calculator
    calculator = HeatOutputCalculator(fallback_flow_rate_lpm=0.5)
    heat_output = calculator.calculate_with_fallback(
        supply_temp_c=40.0,
        return_temp_c=30.0,
        measured_flow_rate_lpm=0.5,
    )
    expected = calculate_heat_output_kw(40.0, 30.0, 0.5)
    assert heat_output == pytest.approx(expected, rel=0.01), "Basic calculation failed"

    # Test 2: Using fallback flow rate
    heat_output = calculator.calculate_with_fallback(
        supply_temp_c=45.0,
        return_temp_c=35.0,
        measured_flow_rate_lpm=None,  # Use fallback
    )
    expected = calculate_heat_output_kw(45.0, 35.0, 0.5)
    assert heat_output == pytest.approx(expected, rel=0.01), "Fallback flow rate failed"

    # Test 3: Invalid temperatures (supply <= return) should return None
    heat_output = calculator.calculate_with_fallback(
        supply_temp_c=30.0,
        return_temp_c=40.0,  # Invalid: return > supply
        measured_flow_rate_lpm=0.5,
    )
    assert heat_output is None, "Invalid temps should return None"

    # Test 4: High delta-T scenario
    heat_output = calculator.calculate_with_fallback(
        supply_temp_c=60.0,
        return_temp_c=30.0,  # 30 degree delta
        measured_flow_rate_lpm=1.0,
    )
    expected = calculate_heat_output_kw(60.0, 30.0, 1.0)
    assert heat_output == pytest.approx(expected, rel=0.01), "High delta-T failed"

    # Test 5: Sensor value retrieval
    def mock_get_state(entity_id):
        states = {
            "sensor.supply_temp": Mock(state="42.5"),
            "sensor.return_temp": Mock(state="32.0"),
            "sensor.flow_rate": Mock(state="0.75"),
        }
        return states.get(entity_id)

    mock_hass.states.get = mock_get_state

    supply = sensor._get_sensor_value("sensor.supply_temp")
    assert supply == 42.5, "Supply temp retrieval failed"

    return_t = sensor._get_sensor_value("sensor.return_temp")
    assert return_t == 32.0, "Return temp retrieval failed"

    flow = sensor._get_sensor_value("sensor.flow_rate")
    assert flow == 0.75, "Flow rate retrieval failed"

    print("All heat output tests passed!")
