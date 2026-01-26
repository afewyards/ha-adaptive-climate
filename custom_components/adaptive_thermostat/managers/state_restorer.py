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
        - PID gains: Kp, Ki, Kd, Ke (supports both lowercase and uppercase attribute names)
        - Dual gain sets: _heating_gains and _cooling_gains (from pid_history)
        - Legacy migration: kp/ki/kd attributes and flat pid_history arrays
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

        # Restore PID integral value with migration for v0.7.0 dimensional fix
        # In v0.6.x and earlier, integral was accumulated with dt in seconds
        # In v0.7.0+, integral uses dt in hours, so we need to multiply by 3600
        if isinstance(old_state.attributes.get('pid_i'), (float, int)):
            old_integral = float(old_state.attributes.get('pid_i'))
            # Check if migration is needed by looking for version marker
            # If no marker exists, assume old version and migrate
            needs_migration = old_state.attributes.get('pid_integral_migrated') != True

            if needs_migration and old_integral != 0:
                # Migrate: old integral was in %·seconds, new integral is in %·hours
                # So multiply by 3600 to convert seconds to hours
                thermostat._i = old_integral * 3600.0
                _LOGGER.info(
                    "%s: Migrated PID integral from v0.6.x (seconds) to v0.7.0+ (hours): "
                    "%.4f -> %.4f",
                    thermostat.entity_id, old_integral, thermostat._i
                )
            else:
                thermostat._i = old_integral

            thermostat._pid_controller.integral = thermostat._i

        # Restore Kp (supports both 'kp' and 'Kp')
        if old_state.attributes.get('kp') is not None:
            thermostat._kp = float(old_state.attributes.get('kp'))
            thermostat._pid_controller.set_pid_param(kp=thermostat._kp)
        elif old_state.attributes.get('Kp') is not None:
            thermostat._kp = float(old_state.attributes.get('Kp'))
            thermostat._pid_controller.set_pid_param(kp=thermostat._kp)

        # Restore Ki (supports both 'ki' and 'Ki')
        if old_state.attributes.get('ki') is not None:
            thermostat._ki = float(old_state.attributes.get('ki'))
            thermostat._pid_controller.set_pid_param(ki=thermostat._ki)
        elif old_state.attributes.get('Ki') is not None:
            thermostat._ki = float(old_state.attributes.get('Ki'))
            thermostat._pid_controller.set_pid_param(ki=thermostat._ki)

        # Restore Kd (supports both 'kd' and 'Kd')
        if old_state.attributes.get('kd') is not None:
            thermostat._kd = float(old_state.attributes.get('kd'))
            thermostat._pid_controller.set_pid_param(kd=thermostat._kd)
        elif old_state.attributes.get('Kd') is not None:
            thermostat._kd = float(old_state.attributes.get('Kd'))
            thermostat._pid_controller.set_pid_param(kd=thermostat._kd)

        # Restore Ke (supports both 'ke' and 'Ke') with migration for v0.7.1 scaling restoration
        # In v0.7.0, Ke values were incorrectly scaled down by 100x
        # In v0.7.1+, Ke restored to proper range by multiplying by 100x
        ke_value = None
        if old_state.attributes.get('ke') is not None:
            ke_value = float(old_state.attributes.get('ke'))
        elif old_state.attributes.get('Ke') is not None:
            ke_value = float(old_state.attributes.get('Ke'))

        if ke_value is not None:
            # Check if migration is needed by looking for version marker
            # If no marker exists, assume v0.7.0 and migrate if Ke < 0.05
            ke_v071_migrated = old_state.attributes.get('ke_v071_migrated') == True

            if not ke_v071_migrated and ke_value < 0.05:
                # Migrate: v0.7.0 Ke was 100x too small, multiply by 100
                thermostat._ke = ke_value * 100.0
                _LOGGER.info(
                    "%s: Ke v0.7.1 migration: %.4f -> %.4f",
                    thermostat.entity_id, ke_value, thermostat._ke
                )
            else:
                thermostat._ke = ke_value

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

        Handles multiple formats:
        1. New format: pid_history = {"heating": [...], "cooling": [...]}
        2. Legacy flat format: pid_history = [...]
        3. Legacy single gains: kp, ki, kd attributes (no pid_history)

        Priority order:
        1. If pid_history exists with mode keys (heating/cooling), use it
        2. Else if pid_history is a flat array, migrate to heating gains
        3. Else if kp/ki/kd attributes exist, use them for heating gains
        4. Else fall back to physics-based initialization (thermostat._kp/_ki/_kd)

        Args:
            old_state: The restored state object from async_get_last_state().
        """
        from ..adaptive.physics import calculate_initial_pid

        thermostat = self._thermostat
        pid_history = old_state.attributes.get('pid_history')

        # Initialize gain sets to None (will be set below)
        thermostat._heating_gains = None
        thermostat._cooling_gains = None

        # Case 1: New format - mode-keyed pid_history
        if pid_history and isinstance(pid_history, dict):
            # Check if it's the new nested format (has 'heating' or 'cooling' keys)
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

                return  # Successfully restored from new format

        # Case 2: Legacy flat array format - migrate to heating gains
        if pid_history and isinstance(pid_history, list) and len(pid_history) > 0:
            _LOGGER.info("%s: Migrating legacy flat pid_history to heating gains", thermostat.entity_id)
            last_entry = pid_history[-1]
            thermostat._heating_gains = PIDGains(
                kp=float(last_entry.get('kp', thermostat._kp)),
                ki=float(last_entry.get('ki', thermostat._ki)),
                kd=float(last_entry.get('kd', thermostat._kd))
            )
            _LOGGER.info(
                "%s: Migrated heating gains from flat pid_history: Kp=%.4f, Ki=%.5f, Kd=%.3f",
                thermostat.entity_id,
                thermostat._heating_gains.kp,
                thermostat._heating_gains.ki,
                thermostat._heating_gains.kd
            )
            # Cooling gains remain None (lazy init)
            return

        # Case 3: Legacy single gain attributes (kp, ki, kd) - migrate to heating gains
        kp = old_state.attributes.get('kp') or old_state.attributes.get('Kp')
        ki = old_state.attributes.get('ki') or old_state.attributes.get('Ki')
        kd = old_state.attributes.get('kd') or old_state.attributes.get('Kd')

        if kp is not None and ki is not None and kd is not None:
            _LOGGER.info("%s: Migrating legacy kp/ki/kd attributes to heating gains", thermostat.entity_id)
            thermostat._heating_gains = PIDGains(
                kp=float(kp),
                ki=float(ki),
                kd=float(kd)
            )
            _LOGGER.info(
                "%s: Migrated heating gains from legacy attributes: Kp=%.4f, Ki=%.5f, Kd=%.3f",
                thermostat.entity_id,
                thermostat._heating_gains.kp,
                thermostat._heating_gains.ki,
                thermostat._heating_gains.kd
            )
            # Cooling gains remain None (lazy init)
            return

        # Case 4: No history or legacy gains - fall back to physics-based initialization
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
