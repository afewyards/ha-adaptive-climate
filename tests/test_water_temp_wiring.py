"""Tests for water temperature controller wiring into coordinator + lifecycle."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# coordinator.py subclasses homeassistant.helpers.update_coordinator.DataUpdateCoordinator.
# conftest.py mocks most homeassistant.helpers.* submodules but not this one (only
# tests/test_coordinator.py registers it, locally, for its own legacy flat-import style).
# Register it here too (same pattern, guarded so a full-suite run that already primed
# this via test_coordinator.py is a no-op) so this file's package-style import of
# coordinator.py also works when this test file is run standalone.
if "homeassistant.helpers.update_coordinator" not in sys.modules:

    class _MockDataUpdateCoordinator:
        """Minimal stand-in base class - just needs to be a real, subclassable type."""

        def __init__(self, hass, logger, name, update_interval):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval

    _mock_update_coordinator_module = MagicMock()
    _mock_update_coordinator_module.DataUpdateCoordinator = _MockDataUpdateCoordinator
    sys.modules["homeassistant.helpers.update_coordinator"] = _mock_update_coordinator_module

from custom_components.adaptive_climate.const import (
    CONF_WATER_TEMP_CONTROL,
    CONF_WATER_TEMP_COOLING,
    CONF_WATER_TEMP_TARGET_ENTITY,
)
from custom_components.adaptive_climate.coordinator import AdaptiveThermostatCoordinator


def make_coordinator(config):
    """Build a coordinator without running DataUpdateCoordinator.__init__."""
    coordinator = AdaptiveThermostatCoordinator.__new__(AdaptiveThermostatCoordinator)
    coordinator.hass = MagicMock()
    coordinator._zones = {}
    coordinator._demand_states = {}
    coordinator._config = config
    coordinator._startup_eval_unsub = None
    coordinator._outdoor_temp_unsub = None
    coordinator._water_temp_controller = None
    coordinator._pump_flow_controller = None
    return coordinator


COOLING_CONFIG = {CONF_WATER_TEMP_CONTROL: {CONF_WATER_TEMP_COOLING: {CONF_WATER_TEMP_TARGET_ENTITY: "number.hp_cool"}}}


class TestCoordinatorWiring:
    def test_controller_is_built_from_the_passed_domain_config(self):
        """supply_temperature lands in hass.data only AFTER the coordinator exists."""
        with patch("custom_components.adaptive_climate.coordinator.WaterTempController") as controller_cls:
            coordinator = make_coordinator({**COOLING_CONFIG, "supply_temperature": 38.0})
            coordinator._setup_water_temp_control()

        controller_cls.assert_called_once()
        kwargs = controller_cls.call_args.kwargs
        assert kwargs["supply_temperature"] == 38.0
        controller_cls.return_value.async_start.assert_called_once()

    def test_no_controller_when_water_temp_control_is_absent(self):
        coordinator = make_coordinator({})
        coordinator._setup_water_temp_control()

        assert coordinator.water_temp_controller is None

    def test_no_controller_when_neither_half_is_configured(self):
        coordinator = make_coordinator({CONF_WATER_TEMP_CONTROL: {"idle_days": 7}})
        coordinator._setup_water_temp_control()

        assert coordinator.water_temp_controller is None

    @pytest.mark.asyncio
    async def test_cleanup_tears_the_controller_down(self):
        coordinator = make_coordinator(COOLING_CONFIG)
        controller = MagicMock()
        coordinator._water_temp_controller = controller

        await coordinator.async_cleanup()

        controller.async_cleanup.assert_called_once()
        assert coordinator.water_temp_controller is None

    @pytest.mark.asyncio
    async def test_cleanup_is_safe_without_a_controller(self):
        coordinator = make_coordinator({})

        await coordinator.async_cleanup()  # must not raise

        assert coordinator.water_temp_controller is None

    def test_learning_gate_delegates_to_the_controller(self):
        coordinator = make_coordinator(COOLING_CONFIG)
        controller = MagicMock()
        controller.learning_gate = MagicMock(return_value=True)
        coordinator._water_temp_controller = controller

        assert coordinator.water_temp_learning_gate("cool") is True
        controller.learning_gate.assert_called_once_with("cool")

    def test_learning_gate_is_false_without_a_controller_or_mode(self):
        coordinator = make_coordinator({})

        assert coordinator.water_temp_learning_gate("cool") is False
        assert coordinator.water_temp_learning_gate(None) is False


class TestRestoreAndSave:
    @pytest.mark.asyncio
    async def test_restore_applies_persisted_state_and_marks_restored(self):
        from custom_components.adaptive_climate.climate_setup import (
            async_restore_water_temp_state,
        )

        controller = MagicMock()
        store = MagicMock()
        store.async_load_water_temp_state = AsyncMock(return_value={"cooling": {}})

        await async_restore_water_temp_state(store, controller)

        controller.restore_state.assert_called_once_with({"cooling": {}})

    @pytest.mark.asyncio
    async def test_restore_still_marks_restored_when_nothing_is_persisted(self):
        from custom_components.adaptive_climate.climate_setup import (
            async_restore_water_temp_state,
        )

        controller = MagicMock()
        store = MagicMock()
        store.async_load_water_temp_state = AsyncMock(return_value=None)

        await async_restore_water_temp_state(store, controller)

        controller.restore_state.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_restore_is_a_noop_without_a_controller(self):
        from custom_components.adaptive_climate.climate_setup import (
            async_restore_water_temp_state,
        )

        store = MagicMock()
        store.async_load_water_temp_state = AsyncMock(return_value={"cooling": {}})

        await async_restore_water_temp_state(store, None)

        store.async_load_water_temp_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_helper_persists_the_controller_snapshot(self):
        from custom_components.adaptive_climate import async_save_water_temp_state_now

        hass = MagicMock()
        controller = MagicMock()
        controller.get_state_for_persistence = MagicMock(return_value={"cooling": {}})
        coordinator = MagicMock()
        coordinator.water_temp_controller = controller
        store = MagicMock()
        store.async_save_water_temp_state = AsyncMock(return_value=None)
        hass.data = {"adaptive_climate": {"coordinator": coordinator, "learning_store": store}}

        await async_save_water_temp_state_now(hass)

        store.async_save_water_temp_state.assert_awaited_once_with({"cooling": {}})

    @pytest.mark.asyncio
    async def test_save_helper_is_a_noop_without_a_controller_or_store(self):
        from custom_components.adaptive_climate import async_save_water_temp_state_now

        hass = MagicMock()
        hass.data = {"adaptive_climate": {}}

        await async_save_water_temp_state_now(hass)  # must not raise

    @pytest.mark.asyncio
    async def test_save_helper_swallows_store_errors(self):
        from custom_components.adaptive_climate import async_save_water_temp_state_now

        hass = MagicMock()
        controller = MagicMock()
        controller.get_state_for_persistence = MagicMock(return_value={})
        coordinator = MagicMock()
        coordinator.water_temp_controller = controller
        store = MagicMock()
        store.async_save_water_temp_state = AsyncMock(side_effect=OSError("disk full"))
        hass.data = {"adaptive_climate": {"coordinator": coordinator, "learning_store": store}}

        await async_save_water_temp_state_now(hass)  # must not raise
