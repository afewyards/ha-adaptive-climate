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
    # Create mock modules
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = object
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.POWER = "power"
    mock_sensor_module.SensorDeviceClass.DURATION = "duration"
    mock_sensor_module.SensorDeviceClass.TEMPERATURE = "temperature"
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

    mock_core = Mock()
    mock_core.HomeAssistant = Mock
    mock_core.callback = lambda f: f
    mock_core.Event = Mock

    sys.modules['homeassistant'] = Mock()
    sys.modules['homeassistant.core'] = mock_core
    sys.modules['homeassistant.components'] = Mock()
    sys.modules['homeassistant.components.sensor'] = mock_sensor_module
    sys.modules['homeassistant.const'] = mock_const
    sys.modules['homeassistant.helpers'] = Mock()
    sys.modules['homeassistant.helpers.entity_platform'] = Mock()
    sys.modules['homeassistant.helpers.typing'] = Mock()
    sys.modules['homeassistant.helpers.event'] = mock_event


# Set up mocks before importing the module
_setup_mocks()


# Now we can safely import the module under test
from custom_components.adaptive_thermostat.sensor import (
    DutyCycleSensor,
    HeaterStateChange,
    DEFAULT_DUTY_CYCLE_WINDOW,
)


class TestHeaterStateChange:
    """Tests for HeaterStateChange dataclass."""

    def test_heater_state_change_creation(self):
        """Test creating HeaterStateChange instances."""
        now = datetime.now()
        change = HeaterStateChange(timestamp=now, is_on=True)

        assert change.timestamp == now
        assert change.is_on is True

    def test_heater_state_change_off(self):
        """Test HeaterStateChange with off state."""
        now = datetime.now()
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

    def test_duty_cycle_50_percent(self, duty_cycle_sensor):
        """Test duty cycle calculation with 50% on/off pattern."""
        now = datetime.now()
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

    def test_duty_cycle_25_percent(self, duty_cycle_sensor):
        """Test duty cycle calculation with 25% on time."""
        now = datetime.now()
        window_start = now - timedelta(hours=1)

        # Create state changes: 15 minutes on, 45 minutes off
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=15), is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 25%
        assert duty_cycle == pytest.approx(25.0, rel=0.01)

    def test_duty_cycle_75_percent(self, duty_cycle_sensor):
        """Test duty cycle calculation with 75% on time."""
        now = datetime.now()
        window_start = now - timedelta(hours=1)

        # Create state changes: 45 minutes on, 15 minutes off
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=45), is_on=False),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 75%
        assert duty_cycle == pytest.approx(75.0, rel=0.01)

    def test_duty_cycle_multiple_cycles(self, duty_cycle_sensor):
        """Test duty cycle with multiple on/off cycles."""
        now = datetime.now()
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

    def test_duty_cycle_varied_cycle_lengths(self, duty_cycle_sensor):
        """Test duty cycle with varied on/off cycle lengths."""
        now = datetime.now()
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

    def test_duty_cycle_always_on(self, duty_cycle_sensor):
        """Test duty cycle when heater is always on (100%)."""
        now = datetime.now()
        window_start = now - timedelta(hours=1)

        # Heater turned on at window start and stayed on
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be 100%
        assert duty_cycle == pytest.approx(100.0, rel=0.01)

    def test_duty_cycle_always_off(self, duty_cycle_sensor):
        """Test duty cycle when heater is always off (0%)."""
        now = datetime.now()
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

    def test_duty_cycle_heater_on_at_end_of_window(self, duty_cycle_sensor):
        """Test duty cycle when heater turns on near end of window."""
        now = datetime.now()
        window_start = now - timedelta(hours=1)

        # Heater off for 50 minutes, then on for 10 minutes at end
        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=False),
            HeaterStateChange(timestamp=now - timedelta(minutes=10), is_on=True),
        ])

        duty_cycle = duty_cycle_sensor._calculate_duty_cycle()

        # Should be approximately 16.67% (10/60)
        assert duty_cycle == pytest.approx(16.67, rel=0.05)

    def test_duty_cycle_state_change_before_window(self, duty_cycle_sensor):
        """Test duty cycle with state change before measurement window."""
        now = datetime.now()
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

    def test_duty_cycle_very_short_cycles(self, duty_cycle_sensor):
        """Test duty cycle with very short on/off cycles."""
        now = datetime.now()
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

    def test_calculate_on_time_simple(self, duty_cycle_sensor):
        """Test simple on-time calculation."""
        now = datetime.now()
        window_start = now - timedelta(hours=1)

        duty_cycle_sensor._state_changes = deque([
            HeaterStateChange(timestamp=window_start, is_on=True),
            HeaterStateChange(timestamp=window_start + timedelta(minutes=30), is_on=False),
        ])

        on_time = duty_cycle_sensor._calculate_on_time(window_start, now)

        # Should be 30 minutes = 1800 seconds
        assert on_time == pytest.approx(1800.0, rel=0.01)

    def test_calculate_on_time_with_final_on_period(self, duty_cycle_sensor):
        """Test on-time calculation when heater is on at window end."""
        now = datetime.now()
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

    def test_prune_keeps_recent_before_window(self, duty_cycle_sensor):
        """Test pruning keeps most recent state before window."""
        now = datetime.now()
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

    def test_prune_removes_all_old(self, duty_cycle_sensor):
        """Test pruning when all states are before window but keeps one."""
        now = datetime.now()
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


def test_duty_cycle():
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
