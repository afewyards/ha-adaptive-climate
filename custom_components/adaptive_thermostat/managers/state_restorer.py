"""State restoration manager for Adaptive Thermostat integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from homeassistant.core import State
    from ..climate import AdaptiveThermostat

from ..const import PIDGains

_LOGGER = logging.getLogger(__name__)


class StateRestorer:
    """Manager for restoring thermostat state from Home Assistant's state restoration.

    Handles restoration of:
    - Target temperature setpoint
    - Active preset mode
    - HVAC mode
    - PID controller values (integral, gains, mode)
    - PID history for rollback support
    """

    def __init__(self, thermostat: AdaptiveThermostat) -> None:
        """Initialize the StateRestorer.

        Args:
            thermostat: Reference to the parent thermostat entity
        """
        self._thermostat = thermostat

    def restore(self, old_state: Optional[State]) -> None:
        """Restore all state from a previous session.

        This is the main entry point that orchestrates the full restoration
        process by calling both _restore_state and _restore_pid_values.

        Args:
            old_state: The restored state object from async_get_last_state(),
                      or None if no previous state exists.
        """
        self._restore_state(old_state)
        if old_state is not None:
            self._restore_pid_values(old_state)

    def _restore_state(self, old_state: Optional[State]) -> None:
        """Restore climate entity state from Home Assistant's state restoration.

        This method restores:
        - Target temperature setpoint
        - Active preset mode
        - HVAC mode

        Note: Preset temperatures are not restored as they now come from controller config.

        Args:
            old_state: The restored state object from async_get_last_state(),
                      or None if no previous state exists.
        """
        # Import here to avoid circular imports
        from homeassistant.const import ATTR_TEMPERATURE
        from homeassistant.components.climate import ATTR_PRESET_MODE

        thermostat = self._thermostat

        if old_state is not None:
            # Restore target temperature
            if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                if thermostat._target_temp is None:
                    if thermostat._ac_mode:
                        thermostat._target_temp = thermostat.max_temp
                    else:
                        thermostat._target_temp = thermostat.min_temp
                _LOGGER.warning("%s: No setpoint available in old state, falling back to %s",
                                thermostat.entity_id, thermostat._target_temp)
            else:
                thermostat._target_temp = float(old_state.attributes.get(ATTR_TEMPERATURE))

            # Restore preset mode
            if old_state.attributes.get(ATTR_PRESET_MODE) is not None:
                thermostat._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
                # Sync to temperature manager if initialized
                if thermostat._temperature_manager:
                    thermostat._temperature_manager.restore_state(
                        preset_mode=thermostat._attr_preset_mode,
                        saved_target_temp=thermostat._saved_target_temp,
                    )

            # Restore HVAC mode
            if not thermostat._hvac_mode and old_state.state:
                thermostat.set_hvac_mode(old_state.state)
        else:
            # No previous state, set defaults
            if thermostat._target_temp is None:
                if thermostat._ac_mode:
                    thermostat._target_temp = thermostat.max_temp
                else:
                    thermostat._target_temp = thermostat.min_temp
            _LOGGER.warning("%s: No setpoint to restore, setting to %s", thermostat.entity_id,
                            thermostat._target_temp)

    def _restore_pid_values(self, old_state: State) -> None:
        """Restore PID controller values from Home Assistant's state restoration.

        This method restores:
        - PID integral value (pid_i)
        - PID gains: kp, ki, kd, ke (via gains_manager)
        - PID mode (auto/off)

        Args:
            old_state: The restored state object from async_get_last_state().
                      Must not be None.
        """
        thermostat = self._thermostat

        if old_state is None or thermostat._pid_controller is None:
            return

        # Restore PID integral value (check new name first, then legacy)
        integral_value = old_state.attributes.get('integral')
        if integral_value is None:
            integral_value = old_state.attributes.get('pid_i')  # Legacy name
        if isinstance(integral_value, (float, int)):
            thermostat._i = float(integral_value)
            thermostat._pid_controller.integral = thermostat._i
            _LOGGER.info("%s: Restored integral=%.2f", thermostat.entity_id, thermostat._i)
        else:
            _LOGGER.warning(
                "%s: No integral in old_state (integral=%s, pid_i=%s). Available attrs: %s",
                thermostat.entity_id,
                old_state.attributes.get('integral'),
                old_state.attributes.get('pid_i'),
                list(old_state.attributes.keys())
            )

        # Restore PID gains (kp, ki, kd, ke) via gains_manager
        if thermostat._gains_manager:
            # Use gains_manager's restore_from_state() which handles:
            # - Restoring kp, ki, kd, ke from attributes (defaults ke to 0.0)
            # - Recording a snapshot with reason='restore'
            # - Migrating old history format
            thermostat._gains_manager.restore_from_state(old_state)

            # Log restored values (access via properties which read from gains_manager)
            _LOGGER.info("%s: Restored PID values via gains_manager - Kp=%.4f, Ki=%.5f, Kd=%.3f, Ke=%s",
                        thermostat.entity_id, thermostat._kp, thermostat._ki, thermostat._kd, thermostat._ke or 0)
        else:
            # This should never happen since gains_manager is initialized in async_setup_managers()
            # before restore() is called. Log error and skip gain restoration.
            _LOGGER.error("%s: gains_manager not available during restoration - this indicates an initialization order bug",
                         thermostat.entity_id)

        # Restore outdoor temperature lag state
        if old_state.attributes.get('outdoor_temp_lagged') is not None:
            outdoor_temp_lagged = float(old_state.attributes.get('outdoor_temp_lagged'))
            thermostat._pid_controller.outdoor_temp_lagged = outdoor_temp_lagged
            _LOGGER.info("%s: Restored outdoor_temp_lagged=%.2f°C",
                        thermostat.entity_id, outdoor_temp_lagged)

        # Restore PID mode
        if old_state.attributes.get('pid_mode') is not None:
            thermostat._pid_controller.mode = old_state.attributes.get('pid_mode')

        # Restore actuator cycle counts for wear tracking
        if thermostat._heater_controller:
            heater_cycles = old_state.attributes.get('heater_cycle_count')
            if heater_cycles is not None:
                thermostat._heater_controller.set_heater_cycle_count(int(heater_cycles))
                _LOGGER.info("%s: Restored heater_cycle_count=%d",
                            thermostat.entity_id, int(heater_cycles))

            cooler_cycles = old_state.attributes.get('cooler_cycle_count')
            if cooler_cycles is not None:
                thermostat._heater_controller.set_cooler_cycle_count(int(cooler_cycles))
                _LOGGER.info("%s: Restored cooler_cycle_count=%d",
                            thermostat.entity_id, int(cooler_cycles))

            # NOTE: duty_accumulator is intentionally NOT restored across restarts.
            # The accumulator handles sub-threshold duty within a single session, but
            # restoring it can cause spurious heating when combined with a restored
            # PID integral that keeps control_output positive even when temp > setpoint.

        # Note: PID history restoration is now handled by gains_manager.restore_from_state()
        # called earlier in this method (line 150)
