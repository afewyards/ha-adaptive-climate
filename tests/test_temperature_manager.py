"""Tests for TemperatureManager.

This test suite verifies that TemperatureManager works correctly using only
callback-based access, without requiring direct thermostat reference.
"""
import pytest
from unittest.mock import AsyncMock, Mock
from custom_components.adaptive_climate.managers.temperature_manager import (
    TemperatureManager,
    PRESET_NONE,
    PRESET_AWAY,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_HOME,
    PRESET_SLEEP,
    PRESET_ACTIVITY,
)


class TestTemperatureManagerCallbackOnly:
    """Test that TemperatureManager works with only callbacks (no direct thermostat reference)."""

    @pytest.fixture
    def mock_callbacks(self):
        """Create mock callbacks for TemperatureManager."""
        return {
            'target_temp': 20.0,
            'current_temp': 18.0,
            'force_on': False,
            'force_off': False,
        }

    @pytest.fixture
    def callbacks(self, mock_callbacks):
        """Create callback functions that use mock_callbacks state."""
        return {
            'get_target_temp': lambda: mock_callbacks['target_temp'],
            'set_target_temp': lambda temp: mock_callbacks.update({'target_temp': temp}),
            'get_current_temp': lambda: mock_callbacks['current_temp'],
            'set_force_on': lambda val: mock_callbacks.update({'force_on': val}),
            'set_force_off': lambda val: mock_callbacks.update({'force_off': val}),
            'async_set_pid_mode': AsyncMock(),
            'async_control_heating': AsyncMock(),
        }

    @pytest.fixture
    def manager(self, callbacks):
        """Create a TemperatureManager instance with callbacks only."""
        # Note: We pass None for thermostat to verify it's not needed
        return TemperatureManager(
            thermostat=None,  # Explicitly None to prove it's not used
            away_temp=15.0,
            eco_temp=17.0,
            boost_temp=24.0,
            comfort_temp=21.0,
            home_temp=20.0,
            sleep_temp=18.0,
            activity_temp=22.0,
            preset_sync_mode='sync',
            min_temp=10.0,
            max_temp=30.0,
            boost_pid_off=True,
            **callbacks
        )

    def test_initialization_without_thermostat(self, manager):
        """Test that manager initializes correctly without thermostat reference."""
        assert manager is not None
        assert manager.preset_mode == PRESET_NONE
        assert manager.saved_target_temp is None

    def test_preset_modes_listing(self, manager):
        """Test that preset modes list correctly via property."""
        modes = manager.preset_modes
        assert PRESET_NONE in modes
        assert PRESET_AWAY in modes
        assert PRESET_ECO in modes
        assert PRESET_BOOST in modes
        assert PRESET_COMFORT in modes
        assert PRESET_HOME in modes
        assert PRESET_SLEEP in modes
        assert PRESET_ACTIVITY in modes

    def test_get_preset_temperature(self, manager):
        """Test getting preset temperatures via callbacks."""
        assert manager.get_preset_temperature(PRESET_AWAY) == 15.0
        assert manager.get_preset_temperature(PRESET_ECO) == 17.0
        assert manager.get_preset_temperature(PRESET_BOOST) == 24.0
        assert manager.get_preset_temperature(PRESET_COMFORT) == 21.0
        assert manager.get_preset_temperature(PRESET_HOME) == 20.0
        assert manager.get_preset_temperature(PRESET_SLEEP) == 18.0
        assert manager.get_preset_temperature(PRESET_ACTIVITY) == 22.0

    def test_get_preset_for_temperature(self, manager):
        """Test reverse lookup of preset from temperature."""
        assert manager.get_preset_for_temperature(15.0) == PRESET_AWAY
        assert manager.get_preset_for_temperature(17.0) == PRESET_ECO
        assert manager.get_preset_for_temperature(24.0) == PRESET_BOOST
        assert manager.get_preset_for_temperature(21.0) == PRESET_COMFORT

    def test_has_preset_support(self, manager):
        """Test preset support detection."""
        assert manager.has_preset_support() is True

    def test_has_preset_support_no_presets(self, callbacks):
        """Test preset support when no presets configured."""
        manager = TemperatureManager(
            thermostat=None,
            away_temp=None,
            eco_temp=None,
            boost_temp=None,
            comfort_temp=None,
            home_temp=None,
            sleep_temp=None,
            activity_temp=None,
            preset_sync_mode='sync',
            min_temp=10.0,
            max_temp=30.0,
            boost_pid_off=False,
            **callbacks
        )
        assert manager.has_preset_support() is False

    @pytest.mark.asyncio
    async def test_set_temperature_increasing(self, manager, mock_callbacks, callbacks):
        """Test setting temperature higher than current (force_on flag)."""
        # Current temp is 18.0, set to 22.0
        await manager.async_set_temperature(22.0)

        # Should set force_on flag
        assert mock_callbacks['force_on'] is True
        assert mock_callbacks['force_off'] is False

        # Should update target temp
        assert mock_callbacks['target_temp'] == 22.0

        # Should trigger heating control
        callbacks['async_control_heating'].assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_set_temperature_decreasing(self, manager, mock_callbacks, callbacks):
        """Test setting temperature lower than current (force_off flag)."""
        # Set current temp higher
        mock_callbacks['current_temp'] = 25.0

        # Set target lower
        await manager.async_set_temperature(20.0)

        # Should set force_off flag
        assert mock_callbacks['force_on'] is False
        assert mock_callbacks['force_off'] is True

        # Should update target temp
        assert mock_callbacks['target_temp'] == 20.0

        # Should trigger heating control
        callbacks['async_control_heating'].assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_set_temperature_matches_preset_with_sync(self, manager, mock_callbacks, callbacks):
        """Test that setting temp matching a preset switches to that preset (sync mode)."""
        await manager.async_set_temperature(15.0)  # Matches AWAY preset

        # Should switch to AWAY preset
        assert manager.preset_mode == PRESET_AWAY

        # Should set target to preset temp
        assert mock_callbacks['target_temp'] == 15.0

    @pytest.mark.asyncio
    async def test_set_temperature_no_sync(self, callbacks, mock_callbacks):
        """Test that preset sync doesn't happen when mode is 'none'."""
        manager = TemperatureManager(
            thermostat=None,
            away_temp=15.0,
            eco_temp=17.0,
            boost_temp=24.0,
            comfort_temp=21.0,
            home_temp=20.0,
            sleep_temp=18.0,
            activity_temp=22.0,
            preset_sync_mode='none',  # Sync disabled
            min_temp=10.0,
            max_temp=30.0,
            boost_pid_off=False,
            **callbacks
        )

        await manager.async_set_temperature(15.0)  # Matches AWAY preset

        # Should stay in NONE preset
        assert manager.preset_mode == PRESET_NONE

        # Should still set target temp
        assert mock_callbacks['target_temp'] == 15.0

    @pytest.mark.asyncio
    async def test_set_preset_mode_from_none(self, manager, mock_callbacks, callbacks):
        """Test switching from NONE to a preset saves current temperature."""
        # Set a specific target temp
        mock_callbacks['target_temp'] = 19.5

        await manager.async_set_preset_mode(PRESET_AWAY)

        # Should save the current target temp
        assert manager.saved_target_temp == 19.5

        # Should switch to preset temp
        assert mock_callbacks['target_temp'] == 15.0

        # Should update preset mode
        assert manager.preset_mode == PRESET_AWAY

    @pytest.mark.asyncio
    async def test_set_preset_mode_back_to_none(self, manager, mock_callbacks, callbacks):
        """Test switching from preset back to NONE restores saved temperature."""
        # First switch to a preset
        mock_callbacks['target_temp'] = 19.5
        await manager.async_set_preset_mode(PRESET_AWAY)

        # Then switch back to NONE
        await manager.async_set_preset_mode(PRESET_NONE)

        # Should restore saved temp
        assert mock_callbacks['target_temp'] == 19.5

        # Should update preset mode
        assert manager.preset_mode == PRESET_NONE

    @pytest.mark.asyncio
    async def test_set_preset_mode_between_presets(self, manager, mock_callbacks, callbacks):
        """Test switching between different presets."""
        # Switch to ECO first
        await manager.async_set_preset_mode(PRESET_ECO)
        assert mock_callbacks['target_temp'] == 17.0

        # Switch to COMFORT
        await manager.async_set_preset_mode(PRESET_COMFORT)
        assert mock_callbacks['target_temp'] == 21.0

        # Should update preset mode
        assert manager.preset_mode == PRESET_COMFORT

    @pytest.mark.asyncio
    async def test_boost_mode_with_pid_off(self, manager, callbacks):
        """Test that boost mode turns PID off when boost_pid_off=True."""
        await manager.async_set_preset_mode(PRESET_BOOST)

        # Should turn PID off
        callbacks['async_set_pid_mode'].assert_called_once_with('off')

        assert manager.preset_mode == PRESET_BOOST

    @pytest.mark.asyncio
    async def test_exit_boost_mode_with_pid_auto(self, manager, callbacks):
        """Test that exiting boost mode restores PID to auto when boost_pid_off=True."""
        # Enter boost
        await manager.async_set_preset_mode(PRESET_BOOST)
        callbacks['async_set_pid_mode'].reset_mock()

        # Exit boost
        await manager.async_set_preset_mode(PRESET_ECO)

        # Should turn PID back to auto
        callbacks['async_set_pid_mode'].assert_called_once_with('auto')

    @pytest.mark.asyncio
    async def test_boost_pid_off_false(self, callbacks, mock_callbacks):
        """Test that PID mode is not changed when boost_pid_off=False."""
        manager = TemperatureManager(
            thermostat=None,
            away_temp=15.0,
            eco_temp=17.0,
            boost_temp=24.0,
            comfort_temp=21.0,
            home_temp=20.0,
            sleep_temp=18.0,
            activity_temp=22.0,
            preset_sync_mode='sync',
            min_temp=10.0,
            max_temp=30.0,
            boost_pid_off=False,  # Don't control PID
            **callbacks
        )

        await manager.async_set_preset_mode(PRESET_BOOST)

        # Should NOT call async_set_pid_mode
        callbacks['async_set_pid_mode'].assert_not_called()

        # Should trigger heating control instead
        callbacks['async_control_heating'].assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_invalid_preset_mode(self, manager):
        """Test that setting invalid preset mode returns None and doesn't change state."""
        original_mode = manager.preset_mode
        result = await manager.async_set_preset_mode("invalid_preset")

        assert result is None
        assert manager.preset_mode == original_mode

    @pytest.mark.asyncio
    async def test_set_preset_mode_already_none(self, manager):
        """Test that setting NONE when already NONE returns None."""
        assert manager.preset_mode == PRESET_NONE
        result = await manager.async_set_preset_mode(PRESET_NONE)

        assert result is None

    def test_restore_state(self, manager):
        """Test restoring saved state."""
        manager.restore_state(
            preset_mode=PRESET_ECO,
            saved_target_temp=19.5
        )

        assert manager.preset_mode == PRESET_ECO
        assert manager.saved_target_temp == 19.5

    def test_restore_state_partial(self, manager):
        """Test restoring only some state values."""
        manager.restore_state(preset_mode=PRESET_AWAY)
        assert manager.preset_mode == PRESET_AWAY
        assert manager.saved_target_temp is None  # Unchanged

        manager.restore_state(saved_target_temp=22.0)
        assert manager.saved_target_temp == 22.0
        assert manager.preset_mode == PRESET_AWAY  # Unchanged

    @pytest.mark.asyncio
    async def test_set_preset_temp_update(self, manager, callbacks, mock_callbacks):
        """Test updating preset temperatures dynamically."""
        await manager.async_set_preset_temp(away_temp=12.0)

        # Should update the away temperature
        assert manager.get_preset_temperature(PRESET_AWAY) == 12.0

        # Should trigger heating control
        callbacks['async_control_heating'].assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_set_preset_temp_disable(self, manager, callbacks):
        """Test disabling a preset temperature."""
        await manager.async_set_preset_temp(away_temp_disable=True)

        # Should disable the away preset
        assert manager.get_preset_temperature(PRESET_AWAY) is None
        assert PRESET_AWAY not in manager.preset_modes

    @pytest.mark.asyncio
    async def test_set_preset_temp_clamps_to_limits(self, manager, callbacks):
        """Test that preset temps are clamped to min/max."""
        # Manager has min_temp=10.0, max_temp=30.0
        await manager.async_set_preset_temp(away_temp=5.0)  # Below min
        assert manager.get_preset_temperature(PRESET_AWAY) == 10.0

        await manager.async_set_preset_temp(boost_temp=35.0)  # Above max
        assert manager.get_preset_temperature(PRESET_BOOST) == 30.0

    @pytest.mark.asyncio
    async def test_set_preset_temp_multiple(self, manager, callbacks):
        """Test updating multiple preset temps at once."""
        await manager.async_set_preset_temp(
            away_temp=14.0,
            eco_temp=16.0,
            boost_temp=26.0
        )

        assert manager.get_preset_temperature(PRESET_AWAY) == 14.0
        assert manager.get_preset_temperature(PRESET_ECO) == 16.0
        assert manager.get_preset_temperature(PRESET_BOOST) == 26.0

    def test_update_min_max_temp(self, manager):
        """Test updating min/max temperature limits."""
        manager.update_min_max_temp(15.0, 25.0)

        assert manager._min_temp == 15.0
        assert manager._max_temp == 25.0

    def test_presets_property(self, manager):
        """Test presets property returns only configured presets."""
        presets = manager.presets

        assert presets[PRESET_AWAY] == 15.0
        assert presets[PRESET_ECO] == 17.0
        assert presets[PRESET_BOOST] == 24.0
        assert presets[PRESET_COMFORT] == 21.0
        assert presets[PRESET_HOME] == 20.0
        assert presets[PRESET_SLEEP] == 18.0
        assert presets[PRESET_ACTIVITY] == 22.0

        # NONE should not be in presets
        assert PRESET_NONE not in presets


class TestTemperatureManagerEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.fixture
    def callbacks(self):
        """Create minimal callbacks."""
        state = {'target_temp': 20.0, 'current_temp': None}
        return {
            'get_target_temp': lambda: state['target_temp'],
            'set_target_temp': lambda temp: state.update({'target_temp': temp}),
            'get_current_temp': lambda: state['current_temp'],
            'set_force_on': lambda val: None,
            'set_force_off': lambda val: None,
            'async_set_pid_mode': AsyncMock(),
            'async_control_heating': AsyncMock(),
        }

    @pytest.mark.asyncio
    async def test_set_temperature_no_current_temp(self, callbacks):
        """Test setting temperature when current temp is unavailable."""
        manager = TemperatureManager(
            thermostat=None,
            away_temp=15.0,
            eco_temp=None,
            boost_temp=None,
            comfort_temp=None,
            home_temp=None,
            sleep_temp=None,
            activity_temp=None,
            preset_sync_mode='sync',
            min_temp=10.0,
            max_temp=30.0,
            boost_pid_off=False,
            **callbacks
        )

        # Should not crash when current_temp is None
        await manager.async_set_temperature(20.0)

        # Should still call control_heating
        callbacks['async_control_heating'].assert_called_once()

    def test_preset_modes_partial_configuration(self, callbacks):
        """Test preset modes when only some presets are configured."""
        manager = TemperatureManager(
            thermostat=None,
            away_temp=15.0,
            eco_temp=None,  # Not configured
            boost_temp=24.0,
            comfort_temp=None,  # Not configured
            home_temp=None,  # Not configured
            sleep_temp=18.0,
            activity_temp=None,  # Not configured
            preset_sync_mode='sync',
            min_temp=10.0,
            max_temp=30.0,
            boost_pid_off=False,
            **callbacks
        )

        modes = manager.preset_modes
        assert PRESET_NONE in modes
        assert PRESET_AWAY in modes
        assert PRESET_BOOST in modes
        assert PRESET_SLEEP in modes

        # Not configured presets should not be available
        assert PRESET_ECO not in modes
        assert PRESET_COMFORT not in modes
        assert PRESET_HOME not in modes
        assert PRESET_ACTIVITY not in modes

    @pytest.mark.asyncio
    async def test_all_preset_types(self, callbacks):
        """Test that all preset types can be set and retrieved."""
        manager = TemperatureManager(
            thermostat=None,
            away_temp=15.0,
            eco_temp=17.0,
            boost_temp=24.0,
            comfort_temp=21.0,
            home_temp=20.0,
            sleep_temp=18.0,
            activity_temp=22.0,
            preset_sync_mode='sync',
            min_temp=10.0,
            max_temp=30.0,
            boost_pid_off=False,
            **callbacks
        )

        presets_to_test = [
            PRESET_AWAY,
            PRESET_ECO,
            PRESET_BOOST,
            PRESET_COMFORT,
            PRESET_HOME,
            PRESET_SLEEP,
            PRESET_ACTIVITY,
        ]

        for preset in presets_to_test:
            await manager.async_set_preset_mode(preset)
            assert manager.preset_mode == preset
            assert manager.get_preset_temperature(preset) is not None
