"""Protocol definitions for Adaptive Thermostat.

This module defines protocols (structural types) that describe the interfaces
managers and other components expect from the thermostat entity.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode
    from .coordinator import AdaptiveThermostatCoordinator
    from .pid_controller import PIDController
    from .managers.heater_controller import HeaterController


@runtime_checkable
class ThermostatState(Protocol):
    """Protocol defining the state interface that managers access from the thermostat.

    This protocol describes the properties and methods that managers need to read
    from the thermostat entity without requiring tight coupling to the full
    AdaptiveThermostat implementation.

    Usage:
        Instead of passing individual callbacks, managers can accept a ThermostatState
        instance and access properties directly, maintaining type safety and reducing
        boilerplate.
    """

    # Core identification
    @property
    def entity_id(self) -> str:
        """Return the entity ID of the thermostat."""
        ...

    @property
    def _zone_id(self) -> str | None:
        """Return the zone ID for multi-zone coordination."""
        ...

    # Temperature state
    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        ...

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        ...

    @property
    def _target_temp(self) -> float | None:
        """Internal target temperature storage."""
        ...

    @property
    def _cur_temp(self) -> float | None:
        """Internal current temperature storage."""
        ...

    @property
    def _outdoor_temp(self) -> float | None:
        """Return the outdoor temperature."""
        ...

    @property
    def _wind_speed(self) -> float | None:
        """Return the wind speed."""
        ...

    # HVAC state
    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        ...

    @property
    def _hvac_mode(self) -> str:
        """Internal HVAC mode storage."""
        ...

    @property
    def hvac_action(self) -> str | None:
        """Return the current HVAC action."""
        ...

    @property
    def is_heating(self) -> bool:
        """Return True if currently heating."""
        ...

    @property
    def _is_heating(self) -> bool:
        """Internal heating state."""
        ...

    # Heating system configuration
    @property
    def heating_type(self) -> str:
        """Return the heating system type (floor_hydronic, radiator, etc.)."""
        ...

    @property
    def _cold_tolerance(self) -> float:
        """Return the cold tolerance."""
        ...

    @property
    def _hot_tolerance(self) -> float:
        """Return the hot tolerance."""
        ...

    # PID state and components
    @property
    def _control_output(self) -> float:
        """Return the current PID control output."""
        ...

    @property
    def _kp(self) -> float:
        """Return the proportional gain."""
        ...

    @property
    def _ki(self) -> float:
        """Return the integral gain."""
        ...

    @property
    def _kd(self) -> float:
        """Return the derivative gain."""
        ...

    @property
    def _ke(self) -> float:
        """Return the outdoor temperature compensation gain."""
        ...

    @property
    def pid_control_p(self) -> float:
        """Return the proportional component."""
        ...

    @property
    def pid_control_i(self) -> float:
        """Return the integral component."""
        ...

    @property
    def pid_control_d(self) -> float:
        """Return the derivative component."""
        ...

    @property
    def pid_control_e(self) -> float:
        """Return the external/outdoor component."""
        ...

    @property
    def pid_mode(self) -> str:
        """Return the PID mode (off, pid, valve)."""
        ...

    # Controllers and managers
    @property
    def _pid_controller(self) -> PIDController:
        """Return the PID controller instance."""
        ...

    @property
    def _heater_controller(self) -> HeaterController | None:
        """Return the heater controller instance."""
        ...

    @property
    def _coordinator(self) -> AdaptiveThermostatCoordinator | None:
        """Return the coordinator for multi-zone operations."""
        ...

    # Preset and configuration
    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        ...

    @property
    def _away_temp(self) -> float | None:
        """Return the away preset temperature."""
        ...

    @property
    def _eco_temp(self) -> float | None:
        """Return the eco preset temperature."""
        ...

    @property
    def _boost_temp(self) -> float | None:
        """Return the boost preset temperature."""
        ...

    @property
    def _comfort_temp(self) -> float | None:
        """Return the comfort preset temperature."""
        ...

    @property
    def _home_temp(self) -> float | None:
        """Return the home preset temperature."""
        ...

    @property
    def _sleep_temp(self) -> float | None:
        """Return the sleep preset temperature."""
        ...

    @property
    def _activity_temp(self) -> float | None:
        """Return the activity preset temperature."""
        ...

    # Output precision for control value rounding
    @property
    def _output_precision(self) -> int:
        """Return the output precision for control values."""
        ...

    # Timing state for PID calculation
    @property
    def _prev_temp_time(self) -> float | None:
        """Return the previous temperature timestamp."""
        ...

    @property
    def _cur_temp_time(self) -> float | None:
        """Return the current temperature timestamp."""
        ...

    # Night setback and special modes
    @property
    def _night_setback(self) -> object | None:
        """Return the night setback configuration object."""
        ...

    @property
    def _night_setback_config(self) -> dict | None:
        """Return the night setback configuration dictionary."""
        ...

    @property
    def _night_setback_controller(self) -> object | None:
        """Return the night setback controller."""
        ...

    @property
    def _preheat_learner(self) -> object | None:
        """Return the preheat learner instance."""
        ...

    @property
    def _contact_sensor_handler(self) -> object | None:
        """Return the contact sensor handler."""
        ...

    @property
    def _humidity_detector(self) -> object | None:
        """Return the humidity detector instance."""
        ...

    # Methods that managers may need to call
    def _calculate_night_setback_adjustment(self) -> tuple:
        """Calculate night setback adjustment.

        Returns:
            Tuple of (effective_target, in_night_period, night_info)
        """
        ...

    def _get_current_temp(self) -> float | None:
        """Get the current temperature (method form)."""
        ...

    def _get_target_temp(self) -> float | None:
        """Get the target temperature (method form)."""
        ...
