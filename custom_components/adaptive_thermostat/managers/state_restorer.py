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
        - PID gains: kp, ki, kd, ke
        - Dual gain sets: _heating_gains and _cooling_gains (from pid_history)
        - PID mode (auto/off)

        Args:
            old_state: The restored state object from async_get_last_state().
                      Must not be None.
        """
        thermostat = self._thermostat

        if old_state is None or thermostat._pid_controller is None:
            return

        # Restore dual gain sets from pid_history FIRST (before legacy gains)
        # This ensures pid_history takes precedence over legacy kp/ki/kd attributes
        self._restore_dual_gain_sets(old_state)

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

        # Restore Kp
        if old_state.attributes.get('kp') is not None:
            thermostat._kp = float(old_state.attributes.get('kp'))
            thermostat._pid_controller.set_pid_param(kp=thermostat._kp)

        # Restore Ki
        if old_state.attributes.get('ki') is not None:
            thermostat._ki = float(old_state.attributes.get('ki'))
            thermostat._pid_controller.set_pid_param(ki=thermostat._ki)

        # Restore Kd
        if old_state.attributes.get('kd') is not None:
            thermostat._kd = float(old_state.attributes.get('kd'))
            thermostat._pid_controller.set_pid_param(kd=thermostat._kd)

        # Restore Ke
        if old_state.attributes.get('ke') is not None:
            thermostat._ke = float(old_state.attributes.get('ke'))
            thermostat._pid_controller.set_pid_param(ke=thermostat._ke)

        # Restore outdoor temperature lag state
        if old_state.attributes.get('outdoor_temp_lagged') is not None:
            outdoor_temp_lagged = float(old_state.attributes.get('outdoor_temp_lagged'))
            thermostat._pid_controller.outdoor_temp_lagged = outdoor_temp_lagged
            _LOGGER.info("%s: Restored outdoor_temp_lagged=%.2f°C",
                        thermostat.entity_id, outdoor_temp_lagged)

        _LOGGER.info("%s: Restored PID values - Kp=%.4f, Ki=%.5f, Kd=%.3f, Ke=%s",
                     thermostat.entity_id, thermostat._kp, thermostat._ki, thermostat._kd, thermostat._ke or 0)

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

        # Restore PID history for rollback support
        self._restore_pid_history(old_state)

    def _restore_dual_gain_sets(self, old_state: State) -> None:
        """Restore dual PIDGains sets (heating and cooling) from pid_history.

        Supports only the current mode-keyed format:
        pid_history = {"heating": [...], "cooling": [...]}

        Fallback: If no pid_history exists, initialize heating gains from physics baseline.

        Args:
            old_state: The restored state object from async_get_last_state().
        """
        thermostat = self._thermostat
        pid_history = old_state.attributes.get('pid_history')

        # Initialize gain sets to None (will be set below)
        thermostat._heating_gains = None
        thermostat._cooling_gains = None

        # Mode-keyed pid_history format
        if pid_history and isinstance(pid_history, dict):
            if 'heating' in pid_history or 'cooling' in pid_history:
                # Restore heating gains from last entry
                heating_history = pid_history.get('heating', [])
                if heating_history and len(heating_history) > 0:
                    last_heating = heating_history[-1]
                    thermostat._heating_gains = PIDGains(
                        kp=float(last_heating.get('kp', thermostat._kp)),
                        ki=float(last_heating.get('ki', thermostat._ki)),
                        kd=float(last_heating.get('kd', thermostat._kd))
                    )
                    _LOGGER.info(
                        "%s: Restored heating gains from pid_history: Kp=%.4f, Ki=%.5f, Kd=%.3f",
                        thermostat.entity_id,
                        thermostat._heating_gains.kp,
                        thermostat._heating_gains.ki,
                        thermostat._heating_gains.kd
                    )

                # Restore cooling gains from last entry (if exists)
                cooling_history = pid_history.get('cooling', [])
                if cooling_history and len(cooling_history) > 0:
                    last_cooling = cooling_history[-1]
                    thermostat._cooling_gains = PIDGains(
                        kp=float(last_cooling.get('kp', thermostat._kp)),
                        ki=float(last_cooling.get('ki', thermostat._ki)),
                        kd=float(last_cooling.get('kd', thermostat._kd))
                    )
                    _LOGGER.info(
                        "%s: Restored cooling gains from pid_history: Kp=%.4f, Ki=%.5f, Kd=%.3f",
                        thermostat.entity_id,
                        thermostat._cooling_gains.kp,
                        thermostat._cooling_gains.ki,
                        thermostat._cooling_gains.kd
                    )
                else:
                    # Cooling gains not present - lazy init
                    _LOGGER.debug("%s: Cooling gains not in pid_history - will lazy init on first COOL mode",
                                thermostat.entity_id)

                return  # Successfully restored from mode-keyed format

        # Fallback: No history - initialize heating gains from physics-based baseline
        # The thermostat._kp/_ki/_kd values are already set from calculate_initial_pid() or config
        if thermostat._kp and thermostat._ki and thermostat._kd:
            thermostat._heating_gains = PIDGains(
                kp=thermostat._kp,
                ki=thermostat._ki,
                kd=thermostat._kd
            )
            _LOGGER.info(
                "%s: Initialized heating gains from physics baseline: Kp=%.4f, Ki=%.5f, Kd=%.3f",
                thermostat.entity_id,
                thermostat._heating_gains.kp,
                thermostat._heating_gains.ki,
                thermostat._heating_gains.kd
            )
        # Cooling gains remain None (lazy init)

    def _restore_pid_history(self, old_state: State) -> None:
        """Restore PID history from Home Assistant's state restoration.

        This enables rollback to previous PID configurations across restarts.

        Args:
            old_state: The restored state object from async_get_last_state().
        """
        from ..const import DOMAIN, ATTR_PID_HISTORY

        thermostat = self._thermostat

        pid_history = old_state.attributes.get(ATTR_PID_HISTORY)
        if not pid_history:
            return

        # Get the adaptive learner from coordinator
        coordinator = thermostat.hass.data.get(DOMAIN, {}).get("coordinator")
        if not coordinator:
            _LOGGER.debug("%s: No coordinator available for PID history restoration",
                         thermostat.entity_id)
            return

        zone_id = getattr(thermostat, "_zone_id", None)
        if not zone_id:
            _LOGGER.debug("%s: No zone_id available for PID history restoration",
                         thermostat.entity_id)
            return

        zone_data = coordinator.get_zone_data(zone_id)
        if not zone_data:
            _LOGGER.debug("%s: No zone_data available for PID history restoration",
                         thermostat.entity_id)
            return

        adaptive_learner = zone_data.get("adaptive_learner")
        if not adaptive_learner:
            _LOGGER.debug("%s: No adaptive_learner available for PID history restoration",
                         thermostat.entity_id)
            return

        # Restore the history
        adaptive_learner.restore_pid_history(pid_history)
        _LOGGER.info("%s: Restored PID history with %d entries",
                    thermostat.entity_id, len(pid_history))
