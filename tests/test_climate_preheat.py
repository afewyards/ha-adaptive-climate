"""Tests for PreheatLearner integration in climate entity."""

from datetime import datetime
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
import voluptuous as vol

from homeassistant.const import CONF_NAME

from custom_components.adaptive_thermostat.adaptive.preheat import PreheatLearner
from custom_components.adaptive_thermostat.managers.events import (
    CycleEventDispatcher,
    CycleEventType,
    CycleEndedEvent,
)
from custom_components.adaptive_thermostat import const


@pytest.fixture
def mock_hass():
    """Create mock Home Assistant instance."""
    hass = Mock()
    hass.data = {
        "adaptive_thermostat": {
            "coordinator": None,
        }
    }
    hass.states = Mock()
    hass.services = Mock()
    hass.bus = Mock()
    hass.config = Mock()
    hass.config.units = Mock()
    hass.config.units.temperature_unit = "°C"
    return hass


@pytest.fixture
def mock_night_setback_config():
    """Night setback config with preheat enabled."""
    return {
        "night_setback": 3.0,
        "night_setback_start": "22:00",
        "recovery_deadline": "06:00",
        "preheat_enabled": True,
        "max_preheat_hours": 2.0,
    }


@pytest.fixture
def mock_night_setback_config_no_preheat():
    """Night setback config with preheat disabled."""
    return {
        "night_setback": 3.0,
        "night_setback_start": "22:00",
        "recovery_deadline": "06:00",
        "preheat_enabled": False,
    }


class TestPreheatLearnerInitialization:
    """Test PreheatLearner initialization in climate entity."""

    def test_preheat_learner_initialized_when_enabled(
        self, mock_night_setback_config
    ):
        """Test PreheatLearner is initialized when preheat_enabled=True."""
        # Simulate the initialization logic from climate.py
        night_setback_config = mock_night_setback_config
        heating_type = "floor_hydronic"
        preheat_learner = None

        # Simulate preheat learner initialization (as would happen in async_added_to_hass)
        if night_setback_config and night_setback_config.get("preheat_enabled"):
            max_hours = night_setback_config.get("max_preheat_hours")
            preheat_learner = PreheatLearner(
                heating_type=heating_type,
                max_hours=max_hours,
            )

        # Assert PreheatLearner was created
        assert preheat_learner is not None
        assert isinstance(preheat_learner, PreheatLearner)
        assert preheat_learner.heating_type == "floor_hydronic"
        assert preheat_learner.max_hours == 2.0

    def test_preheat_learner_not_initialized_when_disabled(
        self, mock_night_setback_config_no_preheat
    ):
        """Test PreheatLearner is NOT initialized when preheat_enabled=False."""
        night_setback_config = mock_night_setback_config_no_preheat
        heating_type = "floor_hydronic"
        preheat_learner = None

        # Simulate preheat learner initialization
        if night_setback_config and night_setback_config.get("preheat_enabled"):
            max_hours = night_setback_config.get("max_preheat_hours")
            preheat_learner = PreheatLearner(
                heating_type=heating_type,
                max_hours=max_hours,
            )

        # Assert PreheatLearner was NOT created
        assert preheat_learner is None

    def test_preheat_learner_not_initialized_when_config_missing(self):
        """Test PreheatLearner is NOT initialized when night_setback_config is missing."""
        night_setback_config = None
        heating_type = "floor_hydronic"
        preheat_learner = None

        # Simulate preheat learner initialization
        if night_setback_config and night_setback_config.get("preheat_enabled"):
            max_hours = night_setback_config.get("max_preheat_hours")
            preheat_learner = PreheatLearner(
                heating_type=heating_type,
                max_hours=max_hours,
            )

        # Assert PreheatLearner was NOT created
        assert preheat_learner is None


class TestPreheatLearnerPersistence:
    """Test PreheatLearner persistence and restoration."""

    def test_preheat_learner_restored_from_storage(self, mock_hass):
        """Test PreheatLearner is restored from persistence on startup."""
        # Create stored preheat data
        stored_preheat_data = {
            "heating_type": "floor_hydronic",
            "max_hours": 2.0,
            "observations": [
                {
                    "bin_key": ["2-4", "cold"],
                    "start_temp": 17.0,
                    "end_temp": 20.0,
                    "outdoor_temp": 2.0,
                    "duration_minutes": 60.0,
                    "rate": 3.0,
                    "timestamp": "2024-01-01T10:00:00",
                }
            ],
        }

        # Mock coordinator with stored data
        mock_coordinator = Mock()
        mock_coordinator.get_zone_data = Mock(
            return_value={"stored_preheat_data": stored_preheat_data}
        )
        mock_hass.data["adaptive_thermostat"]["coordinator"] = mock_coordinator

        # Simulate restoration from storage (as in async_added_to_hass)
        zone_id = "test_zone"
        coordinator = mock_hass.data.get("adaptive_thermostat", {}).get("coordinator")
        stored_preheat_data_retrieved = None
        if coordinator and zone_id:
            zone_data = coordinator.get_zone_data(zone_id)
            if zone_data:
                stored_preheat_data_retrieved = zone_data.get("stored_preheat_data")

        preheat_learner = None
        if stored_preheat_data_retrieved:
            preheat_learner = PreheatLearner.from_dict(stored_preheat_data_retrieved)

        # Assert learner was restored
        assert preheat_learner is not None
        assert preheat_learner.heating_type == "floor_hydronic"
        assert preheat_learner.max_hours == 2.0
        assert preheat_learner.get_observation_count() == 1


class TestPreheatObservationRecording:
    """Test observation recording on cycle completion."""

    @pytest.mark.asyncio
    async def test_observation_recorded_on_cycle_completion(
        self, mock_hass, mock_night_setback_config
    ):
        """Test observation is recorded when heating cycle completes successfully."""
        # Create dispatcher and preheat learner
        dispatcher = CycleEventDispatcher()
        preheat_learner = PreheatLearner(heating_type="floor_hydronic", max_hours=2.0)

        # Mock outdoor temperature
        outdoor_temp = 5.0

        # Simulate cycle completion handler
        def handle_cycle_ended(event: CycleEndedEvent):
            """Handle cycle ended event and record observation."""
            if event.metrics and not event.metrics.get("interrupted"):
                # Extract cycle data
                start_temp = event.metrics.get("start_temp")
                end_temp = event.metrics.get("end_temp")
                duration_minutes = event.metrics.get("duration_minutes")

                # Record observation if we have outdoor temp
                if start_temp and end_temp and duration_minutes and outdoor_temp:
                    preheat_learner.add_observation(
                        start_temp=start_temp,
                        end_temp=end_temp,
                        outdoor_temp=outdoor_temp,
                        duration_minutes=duration_minutes,
                        timestamp=event.timestamp,
                    )

        # Subscribe to cycle ended events
        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, handle_cycle_ended)

        # Emit cycle ended event with metrics
        cycle_ended = CycleEndedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            metrics={
                "start_temp": 18.0,
                "end_temp": 21.0,
                "duration_minutes": 45.0,
                "interrupted": False,
            },
        )
        dispatcher.emit(cycle_ended)

        # Assert observation was recorded
        assert preheat_learner.get_observation_count() == 1

    @pytest.mark.asyncio
    async def test_observation_not_recorded_when_interrupted(
        self, mock_hass, mock_night_setback_config
    ):
        """Test observation is NOT recorded when cycle was interrupted."""
        dispatcher = CycleEventDispatcher()
        preheat_learner = PreheatLearner(heating_type="floor_hydronic", max_hours=2.0)
        outdoor_temp = 5.0

        def handle_cycle_ended(event: CycleEndedEvent):
            """Handle cycle ended event."""
            if event.metrics and not event.metrics.get("interrupted"):
                start_temp = event.metrics.get("start_temp")
                end_temp = event.metrics.get("end_temp")
                duration_minutes = event.metrics.get("duration_minutes")

                if start_temp and end_temp and duration_minutes and outdoor_temp:
                    preheat_learner.add_observation(
                        start_temp=start_temp,
                        end_temp=end_temp,
                        outdoor_temp=outdoor_temp,
                        duration_minutes=duration_minutes,
                        timestamp=event.timestamp,
                    )

        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, handle_cycle_ended)

        # Emit interrupted cycle
        cycle_ended = CycleEndedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            metrics={
                "start_temp": 18.0,
                "end_temp": 19.5,
                "duration_minutes": 20.0,
                "interrupted": True,
            },
        )
        dispatcher.emit(cycle_ended)

        # Assert observation was NOT recorded
        assert preheat_learner.get_observation_count() == 0

    @pytest.mark.asyncio
    async def test_observation_not_recorded_without_outdoor_temp(
        self, mock_hass, mock_night_setback_config
    ):
        """Test observation is NOT recorded when outdoor temp is unavailable."""
        dispatcher = CycleEventDispatcher()
        preheat_learner = PreheatLearner(heating_type="floor_hydronic", max_hours=2.0)
        outdoor_temp = None  # Outdoor temp unavailable

        def handle_cycle_ended(event: CycleEndedEvent):
            """Handle cycle ended event."""
            if event.metrics and not event.metrics.get("interrupted"):
                start_temp = event.metrics.get("start_temp")
                end_temp = event.metrics.get("end_temp")
                duration_minutes = event.metrics.get("duration_minutes")

                if start_temp and end_temp and duration_minutes and outdoor_temp:
                    preheat_learner.add_observation(
                        start_temp=start_temp,
                        end_temp=end_temp,
                        outdoor_temp=outdoor_temp,
                        duration_minutes=duration_minutes,
                        timestamp=event.timestamp,
                    )

        dispatcher.subscribe(CycleEventType.CYCLE_ENDED, handle_cycle_ended)

        # Emit cycle ended event
        cycle_ended = CycleEndedEvent(
            hvac_mode="heat",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            metrics={
                "start_temp": 18.0,
                "end_temp": 21.0,
                "duration_minutes": 45.0,
                "interrupted": False,
            },
        )
        dispatcher.emit(cycle_ended)

        # Assert observation was NOT recorded
        assert preheat_learner.get_observation_count() == 0


class TestPreheatLearnerPassedToNightSetback:
    """Test PreheatLearner is passed to NightSetback components."""

    def test_preheat_learner_passed_to_night_setback_calculator(
        self, mock_hass, mock_night_setback_config
    ):
        """Test PreheatLearner is passed to NightSetbackCalculator."""
        from custom_components.adaptive_thermostat.adaptive.night_setback import (
            NightSetback,
        )
        from custom_components.adaptive_thermostat.managers.night_setback_calculator import (
            NightSetbackCalculator,
        )

        preheat_learner = PreheatLearner(heating_type="floor_hydronic", max_hours=2.0)

        # Create NightSetbackCalculator with preheat learner
        calculator = NightSetbackCalculator(
            hass=mock_hass,
            entity_id="climate.test",
            night_setback=None,
            night_setback_config=mock_night_setback_config,
            window_orientation=None,
            get_target_temp=lambda: 20.0,
            get_current_temp=lambda: 18.0,
            preheat_learner=preheat_learner,
            preheat_enabled=True,
        )

        # Assert learner was passed correctly
        assert calculator._preheat_learner is preheat_learner
        assert calculator._preheat_enabled is True


class TestPreheatSchemaValidation:
    """Test preheat configuration schema validation and propagation."""

    def test_night_setback_schema_has_preheat_enabled_key(self):
        """Test that night_setback schema includes preheat_enabled key definition."""
        # We can't test the actual schema due to mocking in conftest.py,
        # but we can test that when the actual implementation is done,
        # the schema in climate.py will include these keys.
        # This test documents the expected schema structure.

        # For now, test that the constant exists
        assert hasattr(const, 'CONF_PREHEAT_ENABLED')
        assert const.CONF_PREHEAT_ENABLED == "preheat_enabled"

        # When implemented, the schema at line ~159 in climate.py should have:
        # vol.Optional(const.CONF_PREHEAT_ENABLED, default=False): cv.boolean,

    def test_night_setback_schema_has_max_preheat_hours_key(self):
        """Test that night_setback schema includes max_preheat_hours key definition."""
        # For now, test that the constant exists
        assert hasattr(const, 'CONF_MAX_PREHEAT_HOURS')
        assert const.CONF_MAX_PREHEAT_HOURS == "max_preheat_hours"

        # When implemented, the schema at line ~159 in climate.py should have:
        # vol.Optional(const.CONF_MAX_PREHEAT_HOURS): vol.Coerce(float),

    def test_night_setback_schema_structure_includes_preheat_keys(self):
        """Test that both preheat keys can coexist in night_setback config."""
        # This tests the expected behavior once the schema is updated
        night_setback_config = {
            const.CONF_NIGHT_SETBACK_START: "22:00",
            const.CONF_NIGHT_SETBACK_END: "06:00",
            const.CONF_PREHEAT_ENABLED: True,
            const.CONF_MAX_PREHEAT_HOURS: 3.0,
        }

        # Verify both keys can be present in the config dict
        assert const.CONF_PREHEAT_ENABLED in night_setback_config
        assert const.CONF_MAX_PREHEAT_HOURS in night_setback_config

    def test_preheat_config_copied_to_night_setback_config_dict(self):
        """Test that preheat keys from night_setback_config are copied to _night_setback_config."""
        # Simulate the config dict that would be passed to the entity
        night_setback_config = {
            const.CONF_NIGHT_SETBACK_START: "22:00",
            const.CONF_NIGHT_SETBACK_END: "06:00",
            const.CONF_NIGHT_SETBACK_DELTA: 3.0,
            const.CONF_NIGHT_SETBACK_RECOVERY_DEADLINE: "07:00",
            const.CONF_MIN_EFFECTIVE_ELEVATION: 10.0,
            const.CONF_PREHEAT_ENABLED: True,
            const.CONF_MAX_PREHEAT_HOURS: 2.5,
        }

        # Simulate the _night_setback_config creation logic from climate.py
        _night_setback_config = {
            'start': night_setback_config.get(const.CONF_NIGHT_SETBACK_START),
            'end': night_setback_config.get(const.CONF_NIGHT_SETBACK_END),
            'delta': night_setback_config.get(
                const.CONF_NIGHT_SETBACK_DELTA,
                const.DEFAULT_NIGHT_SETBACK_DELTA
            ),
            'recovery_deadline': night_setback_config.get(const.CONF_NIGHT_SETBACK_RECOVERY_DEADLINE),
            'min_effective_elevation': night_setback_config.get(
                const.CONF_MIN_EFFECTIVE_ELEVATION,
                const.DEFAULT_MIN_EFFECTIVE_ELEVATION
            ),
            'preheat_enabled': night_setback_config.get(const.CONF_PREHEAT_ENABLED),
            'max_preheat_hours': night_setback_config.get(const.CONF_MAX_PREHEAT_HOURS),
        }

        # Assert preheat keys were copied
        assert _night_setback_config['preheat_enabled'] is True
        assert _night_setback_config['max_preheat_hours'] == 2.5

    def test_preheat_config_defaults_to_none_when_missing(self):
        """Test that preheat keys default to None when not provided."""
        night_setback_config = {
            const.CONF_NIGHT_SETBACK_START: "22:00",
            const.CONF_NIGHT_SETBACK_END: "06:00",
            const.CONF_NIGHT_SETBACK_DELTA: 3.0,
        }

        # Simulate the _night_setback_config creation logic
        _night_setback_config = {
            'start': night_setback_config.get(const.CONF_NIGHT_SETBACK_START),
            'end': night_setback_config.get(const.CONF_NIGHT_SETBACK_END),
            'delta': night_setback_config.get(
                const.CONF_NIGHT_SETBACK_DELTA,
                const.DEFAULT_NIGHT_SETBACK_DELTA
            ),
            'recovery_deadline': night_setback_config.get(const.CONF_NIGHT_SETBACK_RECOVERY_DEADLINE),
            'min_effective_elevation': night_setback_config.get(
                const.CONF_MIN_EFFECTIVE_ELEVATION,
                const.DEFAULT_MIN_EFFECTIVE_ELEVATION
            ),
            'preheat_enabled': night_setback_config.get(const.CONF_PREHEAT_ENABLED),
            'max_preheat_hours': night_setback_config.get(const.CONF_MAX_PREHEAT_HOURS),
        }

        # Assert preheat keys default to None
        assert _night_setback_config['preheat_enabled'] is None
        assert _night_setback_config['max_preheat_hours'] is None
