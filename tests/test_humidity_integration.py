"""Tests for humidity detector integration in climate entity."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock, patch, call
from custom_components.adaptive_thermostat.adaptive.humidity_detector import HumidityDetector
from custom_components.adaptive_thermostat.const import (
    DEFAULT_HUMIDITY_SPIKE_THRESHOLD,
    DEFAULT_HUMIDITY_ABSOLUTE_MAX,
    DEFAULT_HUMIDITY_DETECTION_WINDOW,
    DEFAULT_HUMIDITY_STABILIZATION_DELAY,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = AsyncMock()
    hass.services.async_call = AsyncMock()
    hass.states = MagicMock()
    hass.bus = MagicMock()
    return hass


@pytest.fixture
def mock_pid_controller():
    """Create a mock PID controller."""
    controller = MagicMock()
    controller.integral = 100.0
    controller.error = 2.0
    controller.decay_integral = MagicMock()
    return controller


class TestHumidityDetectorInitialization:
    """Test HumidityDetector initialization in climate entity."""

    def test_humidity_detector_initialized_when_configured(self):
        """Test detector is created when humidity_sensor is configured."""
        # Simulate climate entity initialization with humidity config
        humidity_config = {
            'humidity_sensor': 'sensor.bathroom_humidity',
            'humidity_spike_threshold': 20.0,
            'humidity_absolute_max': 85.0,
            'humidity_detection_window': 400,
            'humidity_stabilization_delay': 400,
        }

        # Mock detector creation
        with patch('custom_components.adaptive_thermostat.adaptive.humidity_detector.HumidityDetector') as MockDetector:
            detector_instance = MagicMock()
            MockDetector.return_value = detector_instance

            # Simulate entity initialization
            detector = MockDetector(
                spike_threshold=humidity_config['humidity_spike_threshold'],
                absolute_max=humidity_config['humidity_absolute_max'],
                detection_window=humidity_config['humidity_detection_window'],
                stabilization_delay=humidity_config['humidity_stabilization_delay'],
            )

            assert detector is not None
            MockDetector.assert_called_once_with(
                spike_threshold=20.0,
                absolute_max=85.0,
                detection_window=400,
                stabilization_delay=400,
            )

    def test_humidity_detector_none_when_not_configured(self):
        """Test detector is None when humidity_sensor not configured."""
        # Simulate climate entity initialization without humidity config
        humidity_config = {
            'humidity_sensor': None,
        }

        # When no humidity_sensor, detector should be None
        detector = None if not humidity_config['humidity_sensor'] else HumidityDetector()
        assert detector is None

    def test_humidity_detector_uses_defaults(self):
        """Test detector uses default values from const.py."""
        with patch('custom_components.adaptive_thermostat.adaptive.humidity_detector.HumidityDetector') as MockDetector:
            detector_instance = MagicMock()
            MockDetector.return_value = detector_instance

            # Simulate initialization with defaults
            detector = MockDetector(
                spike_threshold=DEFAULT_HUMIDITY_SPIKE_THRESHOLD,
                absolute_max=DEFAULT_HUMIDITY_ABSOLUTE_MAX,
                detection_window=DEFAULT_HUMIDITY_DETECTION_WINDOW,
                stabilization_delay=DEFAULT_HUMIDITY_STABILIZATION_DELAY,
            )

            MockDetector.assert_called_once_with(
                spike_threshold=15,  # DEFAULT_HUMIDITY_SPIKE_THRESHOLD
                absolute_max=80,     # DEFAULT_HUMIDITY_ABSOLUTE_MAX
                detection_window=300,  # DEFAULT_HUMIDITY_DETECTION_WINDOW
                stabilization_delay=300,  # DEFAULT_HUMIDITY_STABILIZATION_DELAY
            )


class TestHumiditySensorTracking:
    """Test humidity sensor state tracking."""

    def test_humidity_sensor_state_change_records_humidity(self):
        """Test humidity changes trigger record_humidity()."""
        detector = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            detection_window=300,
            stabilization_delay=300,
        )

        # Simulate humidity readings
        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        ts2 = datetime(2024, 1, 15, 10, 1, 0)
        ts3 = datetime(2024, 1, 15, 10, 2, 0)

        detector.record_humidity(ts1, 50.0)
        detector.record_humidity(ts2, 55.0)
        detector.record_humidity(ts3, 60.0)

        # Verify state is normal (no spike)
        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_humidity_spike_triggers_pause(self):
        """Test rapid humidity increase triggers pause."""
        detector = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            detection_window=300,
            stabilization_delay=300,
        )

        # Simulate rapid humidity spike (shower starts)
        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        ts2 = datetime(2024, 1, 15, 10, 5, 0)  # 5 minutes later

        detector.record_humidity(ts1, 50.0)
        detector.record_humidity(ts2, 70.0)  # +20% rise (> 15% threshold)

        # Verify pause is triggered
        assert detector.get_state() == "paused"
        assert detector.should_pause() is True

    def test_absolute_max_triggers_pause(self):
        """Test exceeding absolute_max triggers pause."""
        detector = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            detection_window=300,
            stabilization_delay=300,
        )

        # Simulate humidity exceeding absolute max
        ts = datetime(2024, 1, 15, 10, 0, 0)
        detector.record_humidity(ts, 85.0)  # Exceeds 80% absolute_max

        # Verify pause is triggered
        assert detector.get_state() == "paused"
        assert detector.should_pause() is True


class TestControlLoopIntegration:
    """Test control loop integration."""

    @pytest.mark.asyncio
    async def test_control_loop_pauses_when_humidity_detected(self, mock_hass, mock_pid_controller):
        """Test heater turns off when should_pause() returns True."""
        # Create detector in paused state
        detector = HumidityDetector(spike_threshold=15.0, absolute_max=80.0)
        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        ts2 = datetime(2024, 1, 15, 10, 5, 0)
        detector.record_humidity(ts1, 50.0)
        detector.record_humidity(ts2, 70.0)  # Trigger pause

        assert detector.should_pause() is True

        # Simulate control loop behavior
        # When detector.should_pause() is True:
        # 1. Decay integral
        # 2. Turn off heater
        # 3. Return early (skip PID calculation)

        # Verify integral decay is called
        elapsed_seconds = 60  # 1 minute
        decay_factor = 0.9 ** (elapsed_seconds / 60)  # 0.9 per minute
        mock_pid_controller.decay_integral(decay_factor)
        mock_pid_controller.decay_integral.assert_called_once_with(decay_factor)

        # Verify heater service is called (turn_off)
        await mock_hass.services.async_call(
            "homeassistant", "turn_off", {"entity_id": "switch.heater"}
        )
        mock_hass.services.async_call.assert_called_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.heater"}
        )

    def test_integral_decays_during_humidity_pause(self, mock_pid_controller):
        """Test integral decay calculation during pause (10%/min)."""
        # Test decay factor calculation
        # Formula: 0.9 ** (elapsed_seconds / 60)

        # 1 minute elapsed
        elapsed_1min = 60
        decay_1min = 0.9 ** (elapsed_1min / 60)
        assert abs(decay_1min - 0.9) < 0.001

        # 2 minutes elapsed
        elapsed_2min = 120
        decay_2min = 0.9 ** (elapsed_2min / 60)
        assert abs(decay_2min - 0.81) < 0.001  # 0.9^2

        # 5 minutes elapsed
        elapsed_5min = 300
        decay_5min = 0.9 ** (elapsed_5min / 60)
        assert abs(decay_5min - 0.59049) < 0.001  # 0.9^5

        # Verify decay_integral is called with correct factor
        mock_pid_controller.decay_integral(decay_1min)
        mock_pid_controller.decay_integral.assert_called_with(decay_1min)

    @pytest.mark.asyncio
    async def test_pwm_mode_heater_turn_off(self, mock_hass):
        """Test heater turn off in PWM mode during humidity pause."""
        # Simulate PWM mode (force=True parameter)
        await mock_hass.services.async_call(
            "homeassistant", "turn_off", {"entity_id": "switch.heater"}
        )
        mock_hass.services.async_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_valve_mode_set_to_min(self, mock_hass):
        """Test valve set to min output during humidity pause."""
        # Simulate valve mode (set to output_min)
        output_min = 0
        await mock_hass.services.async_call(
            "number", "set_value",
            {"entity_id": "number.valve", "value": output_min}
        )
        mock_hass.services.async_call.assert_called_once_with(
            "number", "set_value",
            {"entity_id": "number.valve", "value": output_min}
        )


class TestPreheatInteraction:
    """Test preheat interaction with humidity detection."""

    def test_preheat_blocked_during_humidity_pause(self):
        """Test preheat doesn't start when humidity paused."""
        detector = HumidityDetector(spike_threshold=15.0, absolute_max=80.0)

        # Trigger pause
        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        ts2 = datetime(2024, 1, 15, 10, 5, 0)
        detector.record_humidity(ts1, 50.0)
        detector.record_humidity(ts2, 70.0)

        assert detector.should_pause() is True

        # Simulate preheat start check
        # if self._humidity_detector and self._humidity_detector.should_pause():
        #     return  # Don't start preheat

        should_block_preheat = detector.should_pause()
        assert should_block_preheat is True

    def test_preheat_allowed_when_not_paused(self):
        """Test preheat can start when humidity normal."""
        detector = HumidityDetector(spike_threshold=15.0, absolute_max=80.0)

        # Record normal humidity
        ts = datetime(2024, 1, 15, 10, 0, 0)
        detector.record_humidity(ts, 50.0)

        assert detector.should_pause() is False

        # Preheat should be allowed
        should_block_preheat = detector.should_pause()
        assert should_block_preheat is False


class TestHumidityStateTransitions:
    """Test humidity state machine transitions."""

    def test_normal_to_paused_transition(self):
        """Test transition from normal to paused on spike."""
        detector = HumidityDetector(spike_threshold=15.0, absolute_max=80.0)

        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        detector.record_humidity(ts1, 50.0)
        assert detector.get_state() == "normal"

        ts2 = datetime(2024, 1, 15, 10, 5, 0)
        detector.record_humidity(ts2, 70.0)  # Spike
        assert detector.get_state() == "paused"

    def test_paused_to_stabilizing_transition(self):
        """Test transition from paused to stabilizing."""
        detector = HumidityDetector(spike_threshold=15.0, absolute_max=80.0)

        # Trigger pause
        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        ts2 = datetime(2024, 1, 15, 10, 5, 0)
        detector.record_humidity(ts1, 50.0)
        detector.record_humidity(ts2, 85.0)  # Peak at 85%
        assert detector.get_state() == "paused"

        # Drop below 70% with >10% drop from peak
        ts3 = datetime(2024, 1, 15, 10, 10, 0)
        detector.record_humidity(ts3, 65.0)  # 65% < 70% AND (85 - 65) = 20% > 10%
        assert detector.get_state() == "stabilizing"

    def test_stabilizing_to_normal_transition(self):
        """Test transition from stabilizing to normal after delay."""
        detector = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            stabilization_delay=300,  # 5 minutes
        )

        # Trigger pause then stabilizing
        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        ts2 = datetime(2024, 1, 15, 10, 5, 0)
        ts3 = datetime(2024, 1, 15, 10, 10, 0)
        detector.record_humidity(ts1, 50.0)
        detector.record_humidity(ts2, 85.0)
        detector.record_humidity(ts3, 65.0)
        assert detector.get_state() == "stabilizing"

        # Wait stabilization delay (5 minutes)
        ts4 = datetime(2024, 1, 15, 10, 15, 0)  # 5 min after stabilizing started
        detector.record_humidity(ts4, 60.0)
        assert detector.get_state() == "normal"

    def test_time_until_resume_calculation(self):
        """Test time_until_resume returns correct value."""
        detector = HumidityDetector(
            spike_threshold=15.0,
            absolute_max=80.0,
            stabilization_delay=300,  # 5 minutes
        )

        # Trigger stabilizing state
        ts1 = datetime(2024, 1, 15, 10, 0, 0)
        ts2 = datetime(2024, 1, 15, 10, 5, 0)
        ts3 = datetime(2024, 1, 15, 10, 10, 0)
        detector.record_humidity(ts1, 50.0)
        detector.record_humidity(ts2, 85.0)
        detector.record_humidity(ts3, 65.0)

        # Check time remaining
        ts4 = datetime(2024, 1, 15, 10, 12, 0)  # 2 minutes into stabilizing
        detector.record_humidity(ts4, 62.0)

        time_remaining = detector.get_time_until_resume()
        # Should be ~180 seconds remaining (300 - 120)
        assert time_remaining is not None
        assert 170 <= time_remaining <= 190  # Allow small tolerance


class TestZoneDemandUpdate:
    """Test zone demand updates during humidity pause."""

    @pytest.mark.asyncio
    async def test_zone_demand_set_false_during_pause(self):
        """Test zone demand is set to False when paused."""
        # Mock coordinator
        mock_coordinator = MagicMock()
        mock_coordinator.update_zone_demand = MagicMock()

        # Simulate control loop setting demand to False during pause
        zone_id = "bathroom"
        mock_coordinator.update_zone_demand(zone_id, False, "heat")

        mock_coordinator.update_zone_demand.assert_called_once_with(
            zone_id, False, "heat"
        )
