"""Temperature and preset mode manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

# These imports are only needed when running in Home Assistant
try:
    from homeassistant.components.climate import (
        PRESET_AWAY,
        PRESET_NONE,
        PRESET_ECO,
        PRESET_BOOST,
        PRESET_COMFORT,
        PRESET_HOME,
        PRESET_SLEEP,
        PRESET_ACTIVITY,
    )
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False
    PRESET_AWAY = "away"
    PRESET_NONE = "none"
    PRESET_ECO = "eco"
    PRESET_BOOST = "boost"
    PRESET_COMFORT = "comfort"
    PRESET_HOME = "home"
    PRESET_SLEEP = "sleep"
    PRESET_ACTIVITY = "activity"

if TYPE_CHECKING:
    from ..climate import AdaptiveThermostat

_LOGGER = logging.getLogger(__name__)


class TemperatureManager:
    """Manager for temperature and preset mode handling.

    Manages preset modes, preset temperatures, and temperature setpoint
    operations for the thermostat. This includes:
    - Preset mode management (away, eco, boost, comfort, home, sleep, activity)
    - Preset temperature mappings
    - Target temperature setting with preset synchronization
    - Saved target temperature handling for preset mode transitions
    """

    def __init__(
        self,
        thermostat: AdaptiveThermostat,
        away_temp: Optional[float],
        eco_temp: Optional[float],
        boost_temp: Optional[float],
        comfort_temp: Optional[float],
        home_temp: Optional[float],
        sleep_temp: Optional[float],
        activity_temp: Optional[float],
        preset_sync_mode: Optional[str],
        min_temp: float,
        max_temp: float,
        boost_pid_off: bool,
        get_target_temp: Callable[[], Optional[float]],
        set_target_temp: Callable[[float], None],
        get_current_temp: Callable[[], Optional[float]],
        set_force_on: Callable[[bool], None],
        set_force_off: Callable[[bool], None],
        async_set_pid_mode: Callable[[str], Any],
        async_control_heating: Callable[[bool], Any],
    ):
        """Initialize the TemperatureManager.

        Args:
            thermostat: Reference to the parent thermostat entity
            away_temp: Temperature for away preset
            eco_temp: Temperature for eco preset
            boost_temp: Temperature for boost preset
            comfort_temp: Temperature for comfort preset
            home_temp: Temperature for home preset
            sleep_temp: Temperature for sleep preset
            activity_temp: Temperature for activity preset
            preset_sync_mode: Preset synchronization mode ('sync' or 'none')
            min_temp: Minimum allowed temperature
            max_temp: Maximum allowed temperature
            boost_pid_off: Whether to turn PID off during boost mode
            get_target_temp: Callback to get current target temperature
            set_target_temp: Callback to set target temperature
            get_current_temp: Callback to get current temperature
            set_force_on: Callback to set force on flag
            set_force_off: Callback to set force off flag
            async_set_pid_mode: Async callback to set PID mode
            async_control_heating: Async callback to trigger heating control
        """
        self._thermostat = thermostat
        self._away_temp = away_temp
        self._eco_temp = eco_temp
        self._boost_temp = boost_temp
        self._comfort_temp = comfort_temp
        self._home_temp = home_temp
        self._sleep_temp = sleep_temp
        self._activity_temp = activity_temp
        self._preset_sync_mode = preset_sync_mode
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._boost_pid_off = boost_pid_off

        # Callbacks
        self._get_target_temp = get_target_temp
        self._set_target_temp = set_target_temp
        self._get_current_temp = get_current_temp
        self._set_force_on = set_force_on
        self._set_force_off = set_force_off
        self._async_set_pid_mode = async_set_pid_mode
        self._async_control_heating = async_control_heating

        # State
        self._attr_preset_mode = PRESET_NONE
        self._saved_target_temp: Optional[float] = None

    @property
    def preset_mode(self) -> str:
        """Return the current preset mode."""
        return self._attr_preset_mode

    @property
    def preset_modes(self) -> List[str]:
        """Return a list of available preset modes."""
        preset_modes = [PRESET_NONE]
        for mode, preset_mode_temp in self._preset_modes_temp.items():
            if preset_mode_temp is not None:
                preset_modes.append(mode)
        return preset_modes

    @property
    def _preset_modes_temp(self) -> Dict[str, Optional[float]]:
        """Return a dict of preset modes and their temperatures."""
        return {
            PRESET_AWAY: self._away_temp,
            PRESET_ECO: self._eco_temp,
            PRESET_BOOST: self._boost_temp,
            PRESET_COMFORT: self._comfort_temp,
            PRESET_HOME: self._home_temp,
            PRESET_SLEEP: self._sleep_temp,
            PRESET_ACTIVITY: self._activity_temp,
        }

    @property
    def _preset_temp_modes(self) -> Dict[Optional[float], str]:
        """Return a dict of preset temperatures and their modes."""
        return {
            self._away_temp: PRESET_AWAY,
            self._eco_temp: PRESET_ECO,
            self._boost_temp: PRESET_BOOST,
            self._comfort_temp: PRESET_COMFORT,
            self._home_temp: PRESET_HOME,
            self._sleep_temp: PRESET_SLEEP,
            self._activity_temp: PRESET_ACTIVITY,
        }

    @property
    def presets(self) -> Dict[str, float]:
        """Return a dict of available presets and their temperatures."""
        presets = {}
        for mode, preset_mode_temp in self._preset_modes_temp.items():
            if preset_mode_temp is not None:
                presets[mode] = preset_mode_temp
        return presets

    @property
    def saved_target_temp(self) -> Optional[float]:
        """Return the saved target temperature (before preset was applied)."""
        return self._saved_target_temp

    def has_preset_support(self) -> bool:
        """Return True if any preset temperatures are configured."""
        return any(
            temp is not None for temp in [
                self._away_temp,
                self._eco_temp,
                self._boost_temp,
                self._comfort_temp,
                self._home_temp,
                self._sleep_temp,
                self._activity_temp,
            ]
        )

    def get_preset_temperature(self, preset_mode: str) -> Optional[float]:
        """Get the temperature for a specific preset mode.

        Args:
            preset_mode: The preset mode to get temperature for

        Returns:
            The temperature for the preset, or None if not configured
        """
        return self._preset_modes_temp.get(preset_mode)

    def get_preset_for_temperature(self, temperature: float) -> Optional[str]:
        """Get the preset mode that matches a specific temperature.

        Args:
            temperature: The temperature to find a matching preset for

        Returns:
            The preset mode that matches, or None if no match
        """
        return self._preset_temp_modes.get(temperature)

    def restore_state(
        self,
        preset_mode: Optional[str] = None,
        saved_target_temp: Optional[float] = None,
    ) -> None:
        """Restore state from saved data.

        Args:
            preset_mode: The preset mode to restore
            saved_target_temp: The saved target temperature to restore
        """
        if preset_mode is not None:
            self._attr_preset_mode = preset_mode
        if saved_target_temp is not None:
            self._saved_target_temp = saved_target_temp

    async def async_set_temperature(self, temperature: float) -> None:
        """Set new target temperature.

        Handles preset synchronization if configured.

        Args:
            temperature: The new target temperature
        """
        current_temp = self._get_current_temp()

        # Set force flags based on temperature direction
        if current_temp is not None and temperature > current_temp:
            self._set_force_on(True)
        elif current_temp is not None and temperature < current_temp:
            self._set_force_off(True)

        # Check if temperature matches a preset and sync mode is enabled
        if temperature in self._preset_temp_modes and self._preset_sync_mode == 'sync':
            await self.async_set_preset_mode(self._preset_temp_modes[temperature])
        else:
            await self.async_set_preset_mode(PRESET_NONE)
            self._set_target_temp(temperature)

        await self._async_control_heating(True)

    async def async_set_preset_mode(self, preset_mode: str) -> Optional[None]:
        """Set new preset mode.

        Manages target temperature transitions when changing presets.

        Args:
            preset_mode: The new preset mode to set

        Returns:
            None if mode change is rejected (invalid mode or no change needed)
        """
        if preset_mode not in self.preset_modes:
            return None

        current_preset = self._attr_preset_mode
        target_temp = self._get_target_temp()

        if preset_mode != PRESET_NONE and current_preset == PRESET_NONE:
            # Switching from NONE to a preset - save current temperature
            self._saved_target_temp = target_temp
            self._set_target_temp(self.presets[preset_mode])
        elif preset_mode == PRESET_NONE and current_preset != PRESET_NONE:
            # Switching from a preset back to NONE - restore saved temperature
            if self._saved_target_temp is not None:
                self._set_target_temp(self._saved_target_temp)
        elif preset_mode == PRESET_NONE and current_preset == PRESET_NONE:
            # Already at NONE, nothing to do
            return None
        else:
            # Switching between presets - just use new preset's temperature
            self._set_target_temp(self.presets[preset_mode])

        self._attr_preset_mode = preset_mode

        # Handle PID mode changes for boost preset
        if self._boost_pid_off and self._attr_preset_mode == PRESET_BOOST:
            # Force PID OFF if requested and boost mode is active
            await self._async_set_pid_mode('off')
        elif self._boost_pid_off and self._attr_preset_mode != PRESET_BOOST:
            # Force PID Auto if managed by boost_pid_off and not in boost mode
            await self._async_set_pid_mode('auto')
        else:
            # if boost_pid_off is false, don't change the PID mode
            await self._async_control_heating(True)

        return None

    async def async_set_preset_temp(self, **kwargs) -> None:
        """Set the preset mode temperatures.

        Allows dynamic modification of preset temperatures.

        Args:
            **kwargs: Preset name to temperature mappings.
                      Supports both direct names (away_temp) and
                      disable variants (away_temp_disable).
        """
        for preset_name, preset_temp in kwargs.items():
            # Handle disable variants
            if 'disable' in preset_name and preset_temp:
                value = None
            else:
                value = max(min(float(preset_temp), self._max_temp), self._min_temp)

            # Map preset name to internal attribute
            attr_name = preset_name.replace('_disable', '')
            if attr_name == 'away_temp':
                self._away_temp = value
            elif attr_name == 'eco_temp':
                self._eco_temp = value
            elif attr_name == 'boost_temp':
                self._boost_temp = value
            elif attr_name == 'comfort_temp':
                self._comfort_temp = value
            elif attr_name == 'home_temp':
                self._home_temp = value
            elif attr_name == 'sleep_temp':
                self._sleep_temp = value
            elif attr_name == 'activity_temp':
                self._activity_temp = value

        await self._async_control_heating(True)

    def update_min_max_temp(self, min_temp: float, max_temp: float) -> None:
        """Update the min and max temperature limits.

        Args:
            min_temp: New minimum temperature
            max_temp: New maximum temperature
        """
        self._min_temp = min_temp
        self._max_temp = max_temp
