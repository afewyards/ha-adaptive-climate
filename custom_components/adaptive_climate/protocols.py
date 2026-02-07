"""Protocol definitions for Adaptive Climate.

This module defines protocols (structural types) that describe the interfaces
managers and other components expect from the thermostat entity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from homeassistant.components.climate import HVACMode
    from .const import HeatingType
    from .coordinator import AdaptiveThermostatCoordinator
    from .pid_controller import PIDController
    from .managers.heater_controller import HeaterController


@runtime_checkable
class TemperatureState(Protocol):
    """Protocol for managers that only need temperature-related state.

    This is a minimal sub-protocol for managers that only need access to current
    and target temperatures, external temperature, and tolerance settings. It
    provides a focused interface for temperature-dependent logic without exposing
    the full thermostat state.

    Usage:
        Managers that only need temperature data (e.g., TemperatureManager,
        KeManager) can accept a TemperatureState instance instead of the full
        ThermostatState, making dependencies explicit and reducing coupling.
    """

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        ...

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        ...

    @property
    def _ext_temp(self) -> float | None:
        """Return the external/outdoor temperature."""
        ...

    @property
    def _cold_tolerance(self) -> float:
        """Return the cold tolerance threshold."""
        ...

    @property
    def _hot_tolerance(self) -> float:
        """Return the hot tolerance threshold."""
        ...


@runtime_checkable
class PIDState(Protocol):
    """Protocol for managers that need PID-related state.

    This is a minimal sub-protocol that provides access to PID gains,
    control output, and component terms. Managers that only need PID state
    can accept this lighter protocol instead of the full ThermostatState.

    Usage:
        Managers that need to read PID gains or component terms (P, I, D, E)
        can depend on this protocol for better separation of concerns.
    """

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
    def _control_output(self) -> float:
        """Return the current PID control output (0-100%)."""
        ...

    @property
    def pid_control_p(self) -> float:
        """Return the proportional component of PID output."""
        ...

    @property
    def pid_control_i(self) -> float:
        """Return the integral component of PID output."""
        ...

    @property
    def pid_control_d(self) -> float:
        """Return the derivative component of PID output."""
        ...

    @property
    def pid_control_e(self) -> float:
        """Return the outdoor/external component of PID output."""
        ...


@runtime_checkable
class HVACState(Protocol):
    """Protocol for managers that need HVAC mode state.

    This minimal protocol provides access to HVAC mode, heating type, and
    device active state. It serves as a base protocol for managers that only
    need operational state without full thermostat access.

    Usage:
        Managers that only need HVAC mode information can accept an HVACState
        instance instead of the full ThermostatState, reducing coupling.
    """

    @property
    def _hvac_mode(self) -> HVACMode:
        """Return the internal HVAC mode."""
        ...

    @property
    def heating_type(self) -> HeatingType:
        """Return the heating system type (floor_hydronic, radiator, etc.)."""
        ...

    @property
    def _is_device_active(self) -> bool:
        """Return True if the heating/cooling device is currently active."""
        ...


@runtime_checkable
class KeManagerState(TemperatureState, PIDState, HVACState, Protocol):
    """Protocol for KeManager state access.

    This protocol combines temperature, PID, and HVAC state for outdoor
    temperature compensation (Ke) learning. KeManager needs to read:
    - Temperature readings (current, target, outdoor) and tolerances
    - PID gains (Ke) and control output for steady-state observations
    - HVAC mode to determine when steady-state tracking is valid

    By inheriting from the three sub-protocols, this provides all needed
    read-only state without requiring action callbacks.

    Usage:
        KeManager should accept a KeManagerState instance for all read-only
        state access. Action callbacks (set_ke, async_control_heating, etc.)
        remain as explicit callable parameters.
    """

    # All properties inherited from TemperatureState, PIDState, and HVACState
    # Additional property needed for logging
    @property
    def entity_id(self) -> str:
        """Return the entity ID of the thermostat."""
        ...


@runtime_checkable
class ThermostatState(TemperatureState, PIDState, HVACState, Protocol):
    """Combined protocol defining the full state interface for the thermostat.

    This protocol composes the three sub-protocols (TemperatureState, PIDState,
    HVACState) and adds additional properties needed by managers that require
    access to the complete thermostat state.

    Usage:
        Managers should depend on the most specific protocol they need:
        - TemperatureState for temperature-only needs
        - PIDState for PID-only needs
        - HVACState for HVAC mode-only needs
        - ThermostatState when full access is required

        This composition approach reduces coupling and makes dependencies explicit.
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

    # Additional temperature properties not in TemperatureState
    @property
    def _target_temp(self) -> float | None:
        """Internal target temperature storage."""
        ...

    @property
    def _current_temp(self) -> float | None:
        """Internal current temperature storage."""
        ...

    @property
    def _wind_speed(self) -> float | None:
        """Return the wind speed."""
        ...

    # Additional HVAC properties not in HVACState
    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
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

    # Additional PID properties not in PIDState
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
    def _previous_temp_time(self) -> float | None:
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


@runtime_checkable
class PIDTuningManagerState(ThermostatState, Protocol):
    """Protocol for PIDTuningManager - needs full thermostat state access.

    PIDTuningManager handles PID parameter tuning operations including:
    - Manual PID setting
    - Physics-based reset
    - Adaptive PID application
    - Auto-apply with validation
    - Rollback

    It requires access to physical properties for physics calculations,
    coordinator for learning state, and gains manager for history tracking.
    """

    # Additional properties needed for PID tuning
    @property
    def _area_m2(self) -> float | None:
        """Return the room area in square meters."""
        ...

    @property
    def _ceiling_height(self) -> float:
        """Return the ceiling height in meters."""
        ...

    @property
    def _window_area_m2(self) -> float:
        """Return the window area in square meters."""
        ...

    @property
    def _window_rating(self) -> str:
        """Return the window rating (single/double/triple)."""
        ...

    @property
    def _floor_construction(self) -> dict | None:
        """Return the floor construction configuration."""
        ...

    @property
    def _supply_temperature(self) -> float | None:
        """Return the supply temperature in degrees Celsius."""
        ...

    @property
    def _max_power_w(self) -> float | None:
        """Return the maximum power in watts."""
        ...

    @property
    def _pwm(self) -> int:
        """Return the PWM cycle duration in seconds."""
        ...

    @property
    def _gains_manager(self) -> object:
        """Return the PID gains manager instance."""
        ...
