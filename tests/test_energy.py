"""Tests for WeeklyCostSensor weekly delta calculation.

These tests verify:
1. Weekly delta calculation: (current_reading - week_start_reading)
2. Persistence across HA restarts via RestoreEntity
3. Week boundary reset on Sunday midnight (ISO week)
4. Meter reset/replacement handling
"""
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, AsyncMock, patch


class MockSensorEntity:
    """Mock base class for SensorEntity."""
    pass


class MockRestoreEntity:
    """Mock base class for RestoreEntity with async_get_last_state."""

    async def async_added_to_hass(self):
        """Mock async_added_to_hass for base class."""
        pass

    async def async_get_last_state(self):
        """Return None by default - tests will override."""
        return None


def _setup_mocks():
    """Set up mock modules for Home Assistant dependencies."""
    mock_sensor_module = Mock()
    mock_sensor_module.SensorEntity = MockSensorEntity
    mock_sensor_module.SensorDeviceClass = Mock()
    mock_sensor_module.SensorDeviceClass.MONETARY = "monetary"
    mock_sensor_module.SensorDeviceClass.POWER = "power"
    mock_sensor_module.SensorDeviceClass.DURATION = "duration"
    mock_sensor_module.SensorDeviceClass.TEMPERATURE = "temperature"
    mock_sensor_module.SensorStateClass = Mock()
    mock_sensor_module.SensorStateClass.TOTAL = "total"
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

    mock_restore_state = Mock()
    mock_restore_state.RestoreEntity = MockRestoreEntity

    mock_device_registry = Mock()
    mock_device_registry.DeviceInfo = dict

    sys.modules["homeassistant"] = Mock()
    sys.modules["homeassistant.core"] = mock_core
    sys.modules["homeassistant.components"] = Mock()
    sys.modules["homeassistant.components.sensor"] = mock_sensor_module
    sys.modules["homeassistant.const"] = mock_const
    sys.modules["homeassistant.helpers"] = Mock()
    sys.modules["homeassistant.helpers.entity_platform"] = Mock()
    sys.modules["homeassistant.helpers.typing"] = Mock()
    sys.modules["homeassistant.helpers.event"] = mock_event
    sys.modules["homeassistant.helpers.restore_state"] = mock_restore_state
    sys.modules["homeassistant.helpers.device_registry"] = mock_device_registry


# Set up mocks before importing the module
_setup_mocks()

from custom_components.adaptive_thermostat.sensor import WeeklyCostSensor


class TestWeeklyDeltaCalculation:
    """Tests for weekly energy delta calculation."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def sensor(self, mock_hass):
        """Create a WeeklyCostSensor instance."""
        return WeeklyCostSensor(
            hass=mock_hass,
            energy_meter_entity="sensor.energy_meter",
            energy_cost_entity="sensor.energy_price",
        )

    def test_weekly_delta_basic(self, sensor, mock_hass):
        """Test basic weekly delta: current - week_start."""
        # Set up week start
        sensor._week_start_reading = 100.0
        sensor._week_start_timestamp = datetime.now()

        # Mock meter state at 150 kWh
        meter_state = Mock()
        meter_state.state = "150.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        # Patch datetime.now to return same week
        with patch(
            "custom_components.adaptive_thermostat.sensor.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value = datetime.now()
            mock_datetime.fromisoformat = datetime.fromisoformat

            import asyncio

            asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should calculate delta = 150 - 100 = 50 kWh
        assert sensor._weekly_energy_kwh == 50.0

    def test_weekly_delta_accumulates(self, sensor, mock_hass):
        """Test delta accumulates over multiple updates."""
        sensor._week_start_reading = 100.0
        sensor._week_start_timestamp = datetime.now()

        # First update at 120 kWh
        meter_state = Mock()
        meter_state.state = "120.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())
        assert sensor._weekly_energy_kwh == 20.0

        # Second update at 150 kWh
        meter_state.state = "150.0"
        asyncio.get_event_loop().run_until_complete(sensor.async_update())
        assert sensor._weekly_energy_kwh == 50.0

    def test_weekly_delta_with_unit_conversion(self, sensor, mock_hass):
        """Test delta calculation with different energy units."""
        sensor._week_start_reading = 100.0  # kWh
        sensor._week_start_timestamp = datetime.now()

        # Meter reports in GJ (1 GJ = 277.778 kWh)
        meter_state = Mock()
        meter_state.state = "0.5"  # 0.5 GJ = ~138.889 kWh
        meter_state.attributes = {"unit_of_measurement": "GJ"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # 0.5 GJ = 138.889 kWh, delta = 138.889 - 100 = 38.889
        expected_kwh = 0.5 * 277.778
        assert abs(sensor._weekly_energy_kwh - (expected_kwh - 100.0)) < 0.01

    def test_weekly_cost_calculation(self, sensor, mock_hass):
        """Test cost calculation based on delta and price."""
        sensor._week_start_reading = 100.0
        sensor._week_start_timestamp = datetime.now()

        meter_state = Mock()
        meter_state.state = "150.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}

        price_state = Mock()
        price_state.state = "0.25"
        price_state.attributes = {"unit_of_measurement": "EUR/kWh"}

        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else price_state
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Delta = 50 kWh, price = 0.25 EUR/kWh, cost = 12.50 EUR
        assert sensor._weekly_energy_kwh == 50.0
        assert sensor._value == 12.5


class TestPersistenceAcrossRestarts:
    """Tests for state persistence across HA restarts."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def sensor(self, mock_hass):
        """Create a WeeklyCostSensor instance."""
        return WeeklyCostSensor(
            hass=mock_hass,
            energy_meter_entity="sensor.energy_meter",
            energy_cost_entity="sensor.energy_price",
        )

    def test_restore_week_start_reading(self, sensor):
        """Test week_start_reading restored from previous state."""
        import asyncio

        old_state = Mock()
        old_state.attributes = {
            "week_start_reading": 100.0,
            "week_start_timestamp": "2025-01-06T00:00:00",
            "last_meter_reading": 120.0,
            "weekly_energy_kwh": 20.0,
        }

        async def mock_get_last_state():
            return old_state

        with patch.object(sensor, "async_get_last_state", mock_get_last_state):
            asyncio.get_event_loop().run_until_complete(sensor.async_added_to_hass())

        assert sensor._week_start_reading == 100.0

    def test_restore_week_start_timestamp(self, sensor):
        """Test week_start_timestamp restored from previous state."""
        import asyncio

        old_state = Mock()
        old_state.attributes = {
            "week_start_reading": 100.0,
            "week_start_timestamp": "2025-01-06T00:00:00",
            "last_meter_reading": 120.0,
            "weekly_energy_kwh": 20.0,
        }

        async def mock_get_last_state():
            return old_state

        with patch.object(sensor, "async_get_last_state", mock_get_last_state):
            asyncio.get_event_loop().run_until_complete(sensor.async_added_to_hass())

        assert sensor._week_start_timestamp == datetime(2025, 1, 6, 0, 0, 0)

    def test_restore_weekly_energy(self, sensor):
        """Test weekly_energy_kwh restored from previous state."""
        import asyncio

        old_state = Mock()
        old_state.attributes = {
            "week_start_reading": 100.0,
            "week_start_timestamp": "2025-01-06T00:00:00",
            "last_meter_reading": 120.0,
            "weekly_energy_kwh": 20.0,
        }

        async def mock_get_last_state():
            return old_state

        with patch.object(sensor, "async_get_last_state", mock_get_last_state):
            asyncio.get_event_loop().run_until_complete(sensor.async_added_to_hass())

        assert sensor._weekly_energy_kwh == 20.0

    def test_calculation_continues_after_restart(self, sensor, mock_hass):
        """Test weekly calculation continues correctly after restart."""
        import asyncio

        # Restore state from before restart
        old_state = Mock()
        old_state.attributes = {
            "week_start_reading": 100.0,
            "week_start_timestamp": datetime.now().isoformat(),  # Same week
            "last_meter_reading": 120.0,
            "weekly_energy_kwh": 20.0,
        }

        async def mock_get_last_state():
            return old_state

        with patch.object(sensor, "async_get_last_state", mock_get_last_state):
            asyncio.get_event_loop().run_until_complete(sensor.async_added_to_hass())

        # Now update with new reading
        meter_state = Mock()
        meter_state.state = "150.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should calculate from original week_start (100), not from restart
        assert sensor._weekly_energy_kwh == 50.0

    def test_no_previous_state(self, sensor):
        """Test initialization when no previous state exists."""
        import asyncio

        async def mock_get_last_state():
            return None

        with patch.object(sensor, "async_get_last_state", mock_get_last_state):
            asyncio.get_event_loop().run_until_complete(sensor.async_added_to_hass())

        assert sensor._week_start_reading is None
        assert sensor._week_start_timestamp is None


class TestWeekBoundaryReset:
    """Tests for week boundary (Sunday midnight) reset."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def sensor(self, mock_hass):
        """Create a WeeklyCostSensor instance."""
        return WeeklyCostSensor(
            hass=mock_hass,
            energy_meter_entity="sensor.energy_meter",
            energy_cost_entity=None,
        )

    def test_reset_on_week_boundary(self, sensor, mock_hass):
        """Test week_start_reading resets when crossing into new week."""
        # Start in week 1
        week1_date = datetime(2025, 1, 6, 12, 0, 0)  # Monday of week 2
        sensor._week_start_reading = 100.0
        sensor._week_start_timestamp = week1_date

        meter_state = Mock()
        meter_state.state = "200.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        # Simulate update in week 3 (two weeks later)
        week3_date = datetime(2025, 1, 20, 12, 0, 0)  # Monday of week 4

        with patch(
            "custom_components.adaptive_thermostat.sensor.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value = week3_date
            mock_datetime.fromisoformat = datetime.fromisoformat

            import asyncio

            asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should have reset week_start to current reading
        assert sensor._week_start_reading == 200.0
        assert sensor._weekly_energy_kwh == 0.0

    def test_no_reset_same_week(self, sensor, mock_hass):
        """Test no reset when still in same week."""
        # Start on Monday of week 2
        monday = datetime(2025, 1, 6, 8, 0, 0)
        sensor._week_start_reading = 100.0
        sensor._week_start_timestamp = monday

        meter_state = Mock()
        meter_state.state = "150.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        # Update on Friday of same week
        friday = datetime(2025, 1, 10, 18, 0, 0)

        with patch(
            "custom_components.adaptive_thermostat.sensor.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value = friday
            mock_datetime.fromisoformat = datetime.fromisoformat

            import asyncio

            asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should NOT reset, should calculate delta
        assert sensor._week_start_reading == 100.0
        assert sensor._weekly_energy_kwh == 50.0

    def test_reset_captures_correct_reading(self, sensor, mock_hass):
        """Test reset captures current meter reading as new week start."""
        # Start in week 1
        sensor._week_start_reading = 100.0
        sensor._week_start_timestamp = datetime(2025, 1, 6, 0, 0, 0)

        meter_state = Mock()
        meter_state.state = "175.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        # Update in new week
        new_week = datetime(2025, 1, 13, 10, 0, 0)

        with patch(
            "custom_components.adaptive_thermostat.sensor.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value = new_week
            mock_datetime.fromisoformat = datetime.fromisoformat

            import asyncio

            asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Week start should be current reading (175)
        assert sensor._week_start_reading == 175.0
        assert sensor._weekly_energy_kwh == 0.0

    def test_year_boundary_crossing(self, sensor, mock_hass):
        """Test week reset works across year boundaries."""
        # Last week of 2024
        sensor._week_start_reading = 500.0
        sensor._week_start_timestamp = datetime(2024, 12, 30, 0, 0, 0)  # Week 1 of 2025 (ISO)

        meter_state = Mock()
        meter_state.state = "550.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        # Update in week 2 of 2025
        new_year = datetime(2025, 1, 6, 10, 0, 0)

        with patch(
            "custom_components.adaptive_thermostat.sensor.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value = new_year
            mock_datetime.fromisoformat = datetime.fromisoformat

            import asyncio

            asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should have reset for new week
        assert sensor._week_start_reading == 550.0


class TestMeterResetHandling:
    """Tests for meter reset/replacement scenarios."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def sensor(self, mock_hass):
        """Create a WeeklyCostSensor instance."""
        return WeeklyCostSensor(
            hass=mock_hass,
            energy_meter_entity="sensor.energy_meter",
            energy_cost_entity=None,
        )

    def test_meter_reset_detection(self, sensor, mock_hass):
        """Test detection when current reading < week_start_reading."""
        # Week started at 1000 kWh
        sensor._week_start_reading = 1000.0
        sensor._week_start_timestamp = datetime.now()

        # Meter reset to 50 kWh (replacement or rollover)
        meter_state = Mock()
        meter_state.state = "50.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should reset week_start to current reading
        assert sensor._week_start_reading == 50.0
        assert sensor._weekly_energy_kwh == 0.0

    def test_meter_reset_recovery(self, sensor, mock_hass):
        """Test sensor recovers gracefully from meter reset."""
        # Initial state
        sensor._week_start_reading = 1000.0
        sensor._week_start_timestamp = datetime.now()

        meter_state = Mock()
        meter_state.attributes = {"unit_of_measurement": "kWh"}

        # First: meter resets to 0
        meter_state.state = "0.0"
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        assert sensor._week_start_reading == 0.0
        assert sensor._weekly_energy_kwh == 0.0

        # Second: meter continues accumulating
        meter_state.state = "25.0"
        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should now track from new baseline
        assert sensor._weekly_energy_kwh == 25.0

    def test_meter_replacement_scenario(self, sensor, mock_hass):
        """Test handling of complete meter replacement (large value to small)."""
        sensor._week_start_reading = 50000.0  # Old meter had high reading
        sensor._week_start_timestamp = datetime.now()

        # New meter starts at 100
        meter_state = Mock()
        meter_state.state = "100.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should detect reset and use new reading as baseline
        assert sensor._week_start_reading == 100.0
        assert sensor._weekly_energy_kwh == 0.0


class TestExtraStateAttributes:
    """Tests for extra state attributes exposed by the sensor."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def sensor(self, mock_hass):
        """Create a WeeklyCostSensor instance."""
        return WeeklyCostSensor(
            hass=mock_hass,
            energy_meter_entity="sensor.energy_meter",
            energy_cost_entity="sensor.energy_price",
        )

    def test_attributes_contain_persistence_data(self, sensor):
        """Test that extra attributes include persistence data."""
        sensor._week_start_reading = 100.0
        sensor._week_start_timestamp = datetime(2025, 1, 6, 0, 0, 0)
        sensor._last_meter_reading = 150.0
        sensor._weekly_energy_kwh = 50.0
        sensor._price_per_kwh = 0.25

        attrs = sensor.extra_state_attributes

        assert attrs["week_start_reading"] == 100.0
        assert attrs["week_start_timestamp"] == "2025-01-06T00:00:00"
        assert attrs["last_meter_reading"] == 150.0
        assert attrs["weekly_energy_kwh"] == 50.0
        assert attrs["price_per_kwh"] == 0.25

    def test_attributes_with_no_week_start(self, sensor):
        """Test attributes when week hasn't started yet."""
        attrs = sensor.extra_state_attributes

        assert attrs["week_start_reading"] is None
        assert attrs["week_start_timestamp"] is None
        assert attrs["last_meter_reading"] is None


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.data = {}
        return hass

    @pytest.fixture
    def sensor(self, mock_hass):
        """Create a WeeklyCostSensor instance."""
        return WeeklyCostSensor(
            hass=mock_hass,
            energy_meter_entity="sensor.energy_meter",
            energy_cost_entity=None,
        )

    def test_unavailable_meter(self, sensor, mock_hass):
        """Test handling of unavailable meter entity."""
        meter_state = Mock()
        meter_state.state = "unavailable"
        mock_hass.states.get = Mock(return_value=meter_state)

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        assert sensor._attr_available is False

    def test_unknown_meter_state(self, sensor, mock_hass):
        """Test handling of unknown meter state."""
        meter_state = Mock()
        meter_state.state = "unknown"
        mock_hass.states.get = Mock(return_value=meter_state)

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        assert sensor._attr_available is False

    def test_invalid_meter_value(self, sensor, mock_hass):
        """Test handling of non-numeric meter value."""
        meter_state = Mock()
        meter_state.state = "not_a_number"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should handle gracefully
        assert sensor._value == 0.0

    def test_no_energy_meter_configured(self, mock_hass):
        """Test sensor with no energy meter configured."""
        sensor = WeeklyCostSensor(
            hass=mock_hass,
            energy_meter_entity=None,
            energy_cost_entity=None,
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        assert sensor._attr_available is False

    def test_first_reading_initializes_week(self, sensor, mock_hass):
        """Test that first reading initializes week tracking."""
        assert sensor._week_start_reading is None
        assert sensor._week_start_timestamp is None

        meter_state = Mock()
        meter_state.state = "100.0"
        meter_state.attributes = {"unit_of_measurement": "kWh"}
        mock_hass.states.get = Mock(
            side_effect=lambda entity_id: meter_state
            if entity_id == "sensor.energy_meter"
            else None
        )

        import asyncio

        asyncio.get_event_loop().run_until_complete(sensor.async_update())

        # Should initialize week with first reading
        assert sensor._week_start_reading == 100.0
        assert sensor._week_start_timestamp is not None
        assert sensor._weekly_energy_kwh == 0.0
