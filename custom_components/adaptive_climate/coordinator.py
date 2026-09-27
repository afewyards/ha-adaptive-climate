"""Data Update Coordinator for Adaptive Climate."""

from __future__ import annotations

from datetime import timedelta
import logging
import math
import time
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant, Event, callback, CALLBACK_TYPE
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.components.climate import HVACMode
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

# Relative imports are used in production (HA loads as a package); the absolute fallback
# is for running tests without a full HA installation (e.g. pytest with stub modules).
try:
    from .const import (
        DOMAIN,
        CONF_SUPPLY_TEMPERATURE,
        CONF_WATER_TEMP_CONTROL,
        CONF_WATER_TEMP_COOLING,
        CONF_WATER_TEMP_HEATING,
    )
    from .adaptive.sun_position import SunPositionCalculator, ORIENTATION_AZIMUTH
    from .managers.auto_mode_switching import AutoModeSwitchingManager
    from .managers.events import CycleEventDispatcher, CycleEventType, ZoneRegisteredEvent, ZoneUnregisteredEvent
    from .managers.water_temp_controller import WaterTempController
except ImportError:
    from const import (  # type: ignore[no-redef]
        DOMAIN,
        CONF_SUPPLY_TEMPERATURE,
        CONF_WATER_TEMP_CONTROL,
        CONF_WATER_TEMP_COOLING,
        CONF_WATER_TEMP_HEATING,
    )
    from adaptive.sun_position import SunPositionCalculator, ORIENTATION_AZIMUTH  # type: ignore[no-redef]
    from managers.auto_mode_switching import AutoModeSwitchingManager  # type: ignore[no-redef]
    from managers.events import CycleEventDispatcher, CycleEventType, ZoneRegisteredEvent, ZoneUnregisteredEvent  # type: ignore[no-redef]
    from managers.water_temp_controller import WaterTempController  # type: ignore[no-redef]

if TYPE_CHECKING:
    from datetime import datetime
    from .adaptive.manifold_registry import ManifoldRegistry
    from .central_controller import CentralController

_LOGGER = logging.getLogger(__name__)


class AdaptiveThermostatCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from thermostats and coordinating cross-zone state."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any] | None = None) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self._zones: dict[str, dict[str, Any]] = {}
        self._demand_states: dict[str, bool] = {}
        self._central_controller: CentralController | None = None
        self._sun_position_calculator = SunPositionCalculator.from_hass(hass)
        self._thermal_group_manager: Any = None  # ThermalGroupManager or None
        self._manifold_registry: ManifoldRegistry | None = None
        self._update_pending: bool = False
        self._rerun_pending: bool = False  # C04: re-run guard for demand changes during in-flight update
        self._config = config or {}
        self._outdoor_temp_unsub: CALLBACK_TYPE | None = None

        # Zone lifecycle pub/sub dispatcher (A02)
        self._zone_dispatcher = CycleEventDispatcher()

        # Shared outdoor temperature EMA filter
        self._outdoor_temp_lagged: float | None = None
        self._outdoor_temp_tau = self._resolve_outdoor_temp_tau()
        self._last_outdoor_temp_update: float | None = None

        # Auto mode switching (if configured)
        auto_mode_config = self._config.get("auto_mode_switching")
        if auto_mode_config and auto_mode_config.get("enabled", False):
            self._auto_mode_switching = AutoModeSwitchingManager(hass, auto_mode_config, self)
        else:
            self._auto_mode_switching: AutoModeSwitchingManager | None = None

        # Water temperature control (if configured)
        self._water_temp_controller: WaterTempController | None = None
        self._setup_water_temp_control()

        # Always set up outdoor temp listener when weather entity exists
        # (used for shared EMA filter; also triggers auto mode switching if enabled)
        self._setup_outdoor_temp_listener()

        # Initialize lagged temp from current weather state
        initial_temp = self.outdoor_temp
        if initial_temp is not None:
            self._outdoor_temp_lagged = initial_temp
            self._last_outdoor_temp_update = time.monotonic()

        # Schedule startup evaluation for auto mode switching (30s delay for zones to register)
        if self._auto_mode_switching:
            self._startup_eval_unsub = async_call_later(hass, 30, self._async_startup_auto_mode_eval)
        else:
            self._startup_eval_unsub = None

    async def _async_startup_auto_mode_eval(self, _now: Any) -> None:
        """Run initial auto mode evaluation after startup delay."""
        _LOGGER.debug("Running startup auto mode evaluation")
        await self._async_evaluate_auto_mode()

    @property
    def zone_dispatcher(self) -> CycleEventDispatcher:
        """Return the zone lifecycle event dispatcher (A02)."""
        return self._zone_dispatcher

    def set_central_controller(self, controller: CentralController) -> None:
        """Set the central controller reference for push-based updates."""
        self._central_controller = controller
        # Subscribe CentralController to zone lifecycle events (A02)
        self._zone_dispatcher.subscribe(
            CycleEventType.ZONE_REGISTERED,
            lambda e: _LOGGER.debug("CentralController: zone registered: %s", e.zone_id),
        )
        self._zone_dispatcher.subscribe(
            CycleEventType.ZONE_UNREGISTERED,
            lambda _e: self.hass.async_create_task(controller.update()),
        )

    def set_thermal_group_manager(self, manager: Any) -> None:
        """Set the thermal group manager reference.

        Args:
            manager: ThermalGroupManager instance or None
        """
        self._thermal_group_manager = manager
        # Subscribe ThermalGroupManager to zone lifecycle events (A02)
        self._zone_dispatcher.subscribe(
            CycleEventType.ZONE_REGISTERED,
            lambda e: _LOGGER.debug("ThermalGroupManager: zone registered: %s", e.zone_id),
        )
        self._zone_dispatcher.subscribe(
            CycleEventType.ZONE_UNREGISTERED,
            lambda e: manager.remove_zone(e.zone_id),
        )

    @property
    def thermal_group_manager(self) -> Any:
        """Get the thermal group manager.

        Returns:
            ThermalGroupManager instance or None
        """
        return self._thermal_group_manager

    def has_manifold_registry(self) -> bool:
        """Check if a manifold registry has been set.

        Returns:
            True if a manifold registry is configured, False otherwise.
        """
        return self._manifold_registry is not None

    def set_manifold_registry(self, registry: ManifoldRegistry) -> None:
        """Set the manifold registry reference for transport delay calculations.

        Args:
            registry: ManifoldRegistry instance
        """
        self._manifold_registry = registry

    def get_transport_delay_for_zone(self, zone_id: str) -> float:
        """Get the transport delay for a zone in minutes.

        Calculates the time for hot water to reach the zone based on active
        zones. Only considers zones with heating demand.

        Args:
            zone_id: Unique identifier for the zone

        Returns:
            Transport delay in minutes. Returns 0.0 if no registry is set.
        """
        if self._manifold_registry is None:
            return 0.0

        # demand_states is keyed by zone slug; the registry is keyed by climate entity_id
        active_zones = {
            f"climate.{zone_slug}"
            for zone_slug, demand_state in self._demand_states.items()
            if demand_state.get("demand") and demand_state.get("mode") == "heat"
        }
        return self._manifold_registry.get_transport_delay(zone_id, active_zones)

    def get_worst_case_transport_delay_for_zone(self, zone_id: str) -> float:
        """Get worst-case transport delay for preheat scheduling.

        Args:
            zone_id: The zone entity_id

        Returns:
            Transport delay in minutes, or 0.0 if no manifold.
        """
        if not self._manifold_registry:
            return 0.0
        return self._manifold_registry.get_worst_case_transport_delay(zone_id)

    @property
    def weather_entity(self) -> str | None:
        """Get the configured weather entity ID.

        Returns:
            Weather entity ID or None if not configured.
        """
        domain_data = self.hass.data.get(DOMAIN, {})
        return domain_data.get("weather_entity")

    @property
    def outdoor_temp(self) -> float | None:
        """Get the current outdoor temperature from the weather entity.

        Returns:
            Outdoor temperature in °C, or None if unavailable.
        """
        # Get weather entity from hass.data
        weather_entity_id = self.weather_entity

        if not weather_entity_id:
            return None

        # Get weather entity state
        state = self.hass.states.get(weather_entity_id)
        if state is None:
            return None

        # Extract temperature from attributes
        temp = state.attributes.get("temperature")
        if temp is None:
            return None

        try:
            return float(temp)
        except (ValueError, TypeError):
            return None

    @property
    def outdoor_temp_lagged(self) -> float | None:
        """Get the shared EMA-filtered outdoor temperature."""
        return self._outdoor_temp_lagged

    @property
    def outdoor_temp_tau(self) -> float:
        """Get the outdoor temp EMA time constant in hours."""
        return self._outdoor_temp_tau

    def update_outdoor_temp_lagged(self, temp: float, dt_seconds: float) -> None:
        """Update the shared outdoor temp EMA filter.

        H02: First-ever call seeds the EMA without filtering so we never overwrite a
        previously established history when HA replays two events with the same
        monotonic tick (dt=0).

        H03: Uses exponential discretisation ``alpha = 1 - exp(-dt / tau)`` instead of
        the Euler approximation ``alpha = dt / tau``.  The Euler form is only stable when
        ``dt << tau``; a 6-hour gap with the default 4-hour tau gives alpha≈1.5 which
        overwrites the EMA almost entirely.  The exponential form is always stable and
        matches the analytical first-order step response for any sampling interval.

        Args:
            temp: Current outdoor temperature in °C.
            dt_seconds: Time since last update in seconds.
        """
        if self._outdoor_temp_lagged is None:
            # H02: Seed on the very first update; no filtering needed yet.
            self._outdoor_temp_lagged = temp
        elif dt_seconds <= 0:
            # H02: Zero / negative dt (e.g., two events on the same monotonic tick after
            # an HA replay) – skip to preserve existing EMA history.
            pass
        else:
            # H03: Exponential alpha is numerically stable for any dt/tau ratio.
            # tau is in hours; convert to seconds for the exponent.
            alpha = 1.0 - math.exp(-dt_seconds / (self._outdoor_temp_tau * 3600.0))
            alpha = min(1.0, alpha)  # Defensive clamp (already <1 for any finite dt)
            self._outdoor_temp_lagged = alpha * temp + (1.0 - alpha) * self._outdoor_temp_lagged

    def _resolve_outdoor_temp_tau(self) -> float:
        """Get outdoor temp EMA tau from house_energy_rating, default 4.0h."""
        rating = self.hass.data.get(DOMAIN, {}).get("house_energy_rating")
        if not rating:
            return 4.0
        rating_map = {
            "A++++": 10.0,
            "A+++": 8.0,
            "A++": 6.0,
            "A+": 5.0,
            "A": 4.0,
            "B": 3.0,
            "C": 2.5,
            "D": 2.0,
        }
        return rating_map.get(rating.upper(), 4.0)

    @property
    def auto_mode_switching_enabled(self) -> bool:
        """Return whether auto mode switching is enabled."""
        return self._auto_mode_switching is not None

    @property
    def auto_mode_switching(self) -> AutoModeSwitchingManager | None:
        """Return the auto mode switching manager."""
        return self._auto_mode_switching

    @property
    def cooling_supply_temp(self) -> float | None:
        """Return the cooling supply temperature if configured.

        Looks in auto_mode_switching config first (typical location),
        then falls back to top-level domain config.
        """
        auto_mode_config = self._config.get("auto_mode_switching", {})
        return auto_mode_config.get("cooling_supply_temp") or self._config.get("cooling_supply_temp")

    @property
    def cooling_supply_margin(self) -> float:
        """Return the margin above cooling supply temp for minimum target.

        Looks in auto_mode_switching config first, then falls back to top-level.
        """
        auto_mode_config = self._config.get("auto_mode_switching", {})
        margin = auto_mode_config.get("cooling_supply_margin")
        if margin is not None:
            return margin
        return self._config.get("cooling_supply_margin", 1.5)

    @property
    def effective_cooling_supply_temp(self) -> float | None:
        """Return the supply temperature that currently limits cooling setpoints.

        Prefers the water temperature controller's live effective value; falls
        back to the static ``cooling_supply_temp`` before the controller's first
        computation, or when water temperature control is not configured.

        With a dynamic supply temperature, a static clamp lets zones chase
        setpoints the water cannot deliver — integral windup plus spurious
        undershoot Ki boosts.
        """
        if self._water_temp_controller is not None:
            dynamic = self._water_temp_controller.effective_cooling_supply_temp
            if dynamic is not None:
                return dynamic
        return self.cooling_supply_temp

    @property
    def min_cooling_target(self) -> float | None:
        """Return the minimum cooling target (effective supply temp + margin), or None."""
        supply_temp = self.effective_cooling_supply_temp
        if supply_temp is None:
            return None
        return supply_temp + self.cooling_supply_margin

    def _setup_water_temp_control(self) -> None:
        """Construct the water temperature controller when configured.

        Reads from ``self._config`` (the domain config passed to __init__) rather
        than ``hass.data[DOMAIN]`` because ``supply_temperature`` is written to
        hass.data only *after* the coordinator has been constructed.
        """
        water_temp_config = self._config.get(CONF_WATER_TEMP_CONTROL)
        if not water_temp_config:
            return
        if not water_temp_config.get(CONF_WATER_TEMP_COOLING) and not water_temp_config.get(CONF_WATER_TEMP_HEATING):
            return

        self._water_temp_controller = WaterTempController(
            self.hass,
            self,
            water_temp_config,
            supply_temperature=self._config.get(CONF_SUPPLY_TEMPERATURE),
        )
        self._water_temp_controller.async_start()
        _LOGGER.info("Water temperature control enabled")

    @property
    def water_temp_controller(self) -> WaterTempController | None:
        """Return the water temperature controller, or None if not configured."""
        return self._water_temp_controller

    def water_temp_learning_gate(self, mode: str | None) -> bool:
        """Return True while water-temp changes should suppress zone learning.

        Args:
            mode: The zone's HVAC mode ("heat"/"cool"), or None.

        Returns:
            True when the controller reports an active ramp or a recent large
            write for that mode.
        """
        if self._water_temp_controller is None or mode is None:
            return False
        return self._water_temp_controller.learning_gate(mode)

    def register_zone(self, zone_id: str, zone_data: dict[str, Any]) -> None:
        """Register a zone with the coordinator.

        Args:
            zone_id: Unique identifier for the zone
            zone_data: Dictionary containing zone information
        """
        if zone_id in self._zones:
            _LOGGER.warning(
                "Zone %s is already registered, overwriting existing registration",
                zone_id,
            )
        self._zones[zone_id] = zone_data
        # H09: Preserve demand state across re-registration so config reloads during
        # active heating don't wipe demand and shut off the boiler mid-cycle.
        if zone_id not in self._demand_states:
            self._demand_states[zone_id] = {"demand": False, "mode": None}
        else:
            _LOGGER.debug("Preserved demand state for re-registered zone: %s", zone_id)
        _LOGGER.debug("Registered zone: %s", zone_id)
        # A02: Notify all subscribers of zone registration
        entity_id = zone_data.get("climate_entity_id", zone_id)
        self._zone_dispatcher.emit(ZoneRegisteredEvent(zone_id=zone_id, entity_id=entity_id))

    def unregister_zone(self, zone_id: str) -> None:
        """Unregister a zone from the coordinator.

        Removes the zone from all tracking dicts (zones, demand states).
        Notifies subscribers (ModeSync, CentralController, ThermalGroupManager) via
        ZoneUnregisteredEvent instead of ad-hoc direct calls.
        This should be called when a climate entity is being removed.

        Args:
            zone_id: Unique identifier for the zone to unregister
        """
        if zone_id not in self._zones:
            _LOGGER.debug(
                "Zone %s not found in coordinator, nothing to unregister",
                zone_id,
            )
            return

        # Remove from zones dict
        del self._zones[zone_id]

        # Remove from demand states dict
        if zone_id in self._demand_states:
            del self._demand_states[zone_id]

        # A02: Notify all subscribers (ModeSync, CentralController, ThermalGroupManager)
        # via pub/sub instead of ad-hoc direct calls
        self._zone_dispatcher.emit(ZoneUnregisteredEvent(zone_id=zone_id))

        _LOGGER.info("Unregistered zone: %s", zone_id)

    def update_zone_demand(self, zone_id: str, has_demand: bool, hvac_mode: str | None = None) -> None:
        """Update the demand state for a zone.

        Args:
            zone_id: Unique identifier for the zone
            has_demand: Whether the zone currently has heating/cooling demand
            hvac_mode: The zone's current HVAC mode ("heat", "cool", or "off")
        """
        if zone_id in self._demand_states:
            old_state = self._demand_states[zone_id]
            new_state = {"demand": has_demand, "mode": hvac_mode}
            self._demand_states[zone_id] = new_state

            # Only trigger updates if demand or mode actually changed
            if old_state != new_state:
                _LOGGER.info(
                    "Demand changed for zone %s: %s/%s -> %s/%s",
                    zone_id,
                    old_state.get("demand"),
                    old_state.get("mode"),
                    has_demand,
                    hvac_mode,
                )

                # Trigger central controller with single-flight guard (C04, H01).
                if self._central_controller:
                    if not self._update_pending:
                        _LOGGER.info("Triggering CentralController update")
                        self._update_pending = True
                        try:
                            self.hass.async_create_task(self._update_with_guard())
                        except Exception:
                            # H01: Reset flag so future demand changes can still trigger updates.
                            _LOGGER.warning("Failed to schedule CentralController update; resetting guard")
                            self._update_pending = False
                    else:
                        # C04: Another update is already in flight; request a re-run after it
                        # completes so demand changes during the task body are not silently dropped.
                        _LOGGER.debug("Update in flight – marking rerun for zone %s", zone_id)
                        self._rerun_pending = True

    def _is_high_solar_gain(self, check_time: datetime | None = None) -> bool:
        """Check if high solar gain is currently detected.

        High solar gain is defined as:
        - Sun elevation > 15 degrees AND
        - At least one zone has a window with effective sun exposure

        Args:
            check_time: Time to check (defaults to now)

        Returns:
            True if high solar gain detected, False otherwise
        """
        # Return False if sun position calculator unavailable
        if self._sun_position_calculator is None:
            return False

        # Use current time if not specified
        if check_time is None:
            check_time = dt_util.utcnow()

        # Get sun position at check time
        sun_pos = self._sun_position_calculator.get_position_at_time(check_time)

        # If sun is below 15 degrees, no high solar gain
        if sun_pos.elevation < 15.0:
            return False

        # Check if any zone has a window with effective sun exposure
        for _zone_id, zone_data in self._zones.items():
            window_orientation = zone_data.get("window_orientation")
            if not window_orientation or window_orientation.lower() == "none":
                continue

            # Check if sun is effective for this window
            # Use same logic as SunPositionCalculator._is_sun_effective
            if window_orientation.lower() == "roof":
                # Skylights are effective when sun is high enough
                return True

            # Get window azimuth
            window_azimuth = ORIENTATION_AZIMUTH.get(window_orientation.lower())
            if window_azimuth is None:
                continue

            # Calculate angular difference
            diff = abs(sun_pos.azimuth - window_azimuth)
            if diff > 180:
                diff = 360 - diff

            # Sun is effective if within 45 degrees of window normal
            if diff <= 45:
                return True

        # No zones with effective sun exposure
        return False

    def get_aggregate_demand(self) -> dict[str, bool]:
        """Get aggregated demand across all zones.

        Returns:
            Dictionary with 'heating' and 'cooling' boolean values indicating
            if any zone has demand for that mode.
        """
        has_heating_demand = any(
            state.get("demand") and state.get("mode") == "heat" for state in self._demand_states.values()
        )
        has_cooling_demand = any(
            state.get("demand") and state.get("mode") == "cool" for state in self._demand_states.values()
        )
        return {
            "heating": has_heating_demand,
            "cooling": has_cooling_demand,
        }

    def get_all_zones(self) -> dict[str, dict[str, Any]]:
        """Get all registered zones.

        Returns:
            Dictionary of all registered zones with their data.
        """
        return self._zones.copy()

    def get_zone_data(self, zone_id: str) -> dict[str, Any] | None:
        """Get data for a specific zone.

        Args:
            zone_id: Unique identifier for the zone

        Returns:
            Zone data dictionary or None if zone not found.
        """
        return self._zones.get(zone_id)

    def get_active_zones(self, hvac_mode: str | None = None) -> dict[str, dict[str, Any]]:
        """Get zones that currently have demand (are actively heating/cooling).

        Args:
            hvac_mode: Optional filter by HVAC mode ("heat" or "cool").
                       If None, returns all zones with demand regardless of mode.

        Returns:
            Dictionary of zone_id -> zone_data for zones with active demand.
        """
        active_zones: dict[str, dict[str, Any]] = {}

        for zone_id, demand_state in self._demand_states.items():
            # Check if zone has demand
            if not demand_state.get("demand"):
                continue

            # If mode filter specified, check if it matches
            if hvac_mode is not None and demand_state.get("mode") != hvac_mode:
                continue

            # Include zone in active zones
            zone_data = self._zones.get(zone_id)
            if zone_data is not None:
                active_zones[zone_id] = zone_data

        return active_zones

    def get_zones_in_mode(self, hvac_mode: str) -> dict[str, dict[str, Any]]:
        """Get zones whose climate entity is currently in the given HVAC mode.

        Unlike :meth:`get_active_zones` this does **not** filter on live demand,
        so a COOL zone that is momentarily satisfied is still returned.  Reading
        the entity state (rather than ``_demand_states``) is also valid
        immediately after a restart, before any demand update has arrived.

        Args:
            hvac_mode: HVAC mode string to match ("heat", "cool", "off").

        Returns:
            Dictionary of zone_id -> zone_data for matching zones.
        """
        zones_in_mode: dict[str, dict[str, Any]] = {}
        for zone_id, zone_data in self._zones.items():
            climate_entity_id = zone_data.get("climate_entity_id")
            if not climate_entity_id:
                continue
            state = self.hass.states.get(climate_entity_id)
            if state is None or state.state != hvac_mode:
                continue
            zones_in_mode[zone_id] = zone_data
        return zones_in_mode

    def get_zone_current_temp(self, zone_id: str) -> float | None:
        """Get a zone's current temperature from its climate entity attribute.

        ``update_zone_temp`` has no production callers, so the registry's
        ``current_temp`` map is empty outside tests.  The entity's
        ``current_temperature`` attribute is the authoritative source.

        Args:
            zone_id: Unique identifier for the zone.

        Returns:
            Current temperature in °C, or None when unavailable/non-numeric.
        """
        zone_data = self._zones.get(zone_id)
        if zone_data is None:
            return None
        climate_entity_id = zone_data.get("climate_entity_id")
        if not climate_entity_id:
            return None
        state = self.hass.states.get(climate_entity_id)
        if state is None:
            return None
        temp = state.attributes.get("current_temperature")
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            return None
        value = float(temp)
        if not math.isfinite(value):
            return None
        return value

    def get_zone_temps(self) -> dict[str, float]:
        """Get current temperatures for all zones.

        Returns:
            Dictionary of zone_id -> current_temp for zones that have temperature data.
            Zones without a current_temp in their zone_data are excluded.
        """
        temps: dict[str, float] = {}

        for zone_id, zone_data in self._zones.items():
            temp = zone_data.get("current_temp")
            if temp is not None:
                temps[zone_id] = temp

        return temps

    def get_active_zone_setpoints(self) -> list[float]:
        """Return user setpoints of all non-OFF zones.

        Reads ``zone_data["user_target_temp"]`` (pre-setback, set by the climate
        entity on every setpoint change) so auto-mode switching uses the daytime
        target rather than the night-setback-reduced effective target.  Falls back
        to the entity's ``temperature`` state attribute for zones that have not yet
        written ``user_target_temp``.

        Returns:
            List of target temperatures from zones not in OFF mode.
        """
        setpoints = []
        for zone_id, zone in self._zones.items():
            demand_state = self._demand_states.get(zone_id, {})
            mode = demand_state.get("mode")
            if mode is not None and mode != HVACMode.OFF:
                # H04: Prefer user_target_temp (pre-setback) stored by climate entity
                user_target = zone.get("user_target_temp")
                if user_target is not None:
                    setpoints.append(user_target)
                else:
                    # Fallback for zones that haven't written user_target_temp yet
                    climate_entity_id = zone.get("climate_entity_id")
                    if climate_entity_id:
                        state = self.hass.states.get(climate_entity_id)
                        if state and state.attributes.get("temperature") is not None:
                            setpoints.append(state.attributes["temperature"])
        return setpoints

    def get_zone_by_climate_entity(self, climate_entity_id: str) -> tuple[str, dict[str, Any]] | None:
        """Find a zone by its climate entity ID.

        Args:
            climate_entity_id: Climate entity ID to search for

        Returns:
            Tuple of (zone_id, zone_data) if found, None otherwise.
        """
        for zone_id, zone_data in self._zones.items():
            if zone_data.get("climate_entity_id") == climate_entity_id:
                return zone_id, zone_data
        return None

    def get_adaptive_learner(self, climate_entity_id: str) -> Any | None:
        """Get the adaptive learner for a climate entity.

        Args:
            climate_entity_id: Climate entity ID to get learner for

        Returns:
            AdaptiveLearner instance or None if not found.
        """
        zone_info = self.get_zone_by_climate_entity(climate_entity_id)
        if zone_info is None:
            return None
        _, zone_data = zone_info
        return zone_data.get("adaptive_learner")

    def get_zone_count(self) -> int:
        """Get the number of registered zones.

        Returns:
            Number of zones registered with the coordinator.
        """
        return len(self._zones)

    def update_zone_temp(self, zone_id: str, temperature: float) -> None:
        """Update the current temperature for a zone.

        Args:
            zone_id: Unique identifier for the zone
            temperature: Current temperature in °C
        """
        if zone_id in self._zones:
            self._zones[zone_id]["current_temp"] = temperature

    async def _update_with_guard(self) -> None:
        """Update central controller with guard to prevent duplicate tasks.

        C04: After the update completes, check ``_rerun_pending``.  Demand changes
        that arrived *during* the update body set this flag so they are not silently
        dropped; we schedule one further update to pick them up.
        """
        try:
            if self._central_controller:
                await self._central_controller.update()
        finally:
            self._update_pending = False
            if self._rerun_pending and self._central_controller:
                self._rerun_pending = False
                self._update_pending = True
                try:
                    self.hass.async_create_task(self._update_with_guard())
                except Exception:
                    _LOGGER.warning("Failed to reschedule CentralController update after rerun; resetting guard")
                    self._update_pending = False

    def _setup_outdoor_temp_listener(self) -> None:
        """Set up listener for outdoor temperature changes."""
        weather_entity_id = self.weather_entity
        if not weather_entity_id:
            _LOGGER.debug("No weather entity configured")
            return

        @callback
        def _async_outdoor_temp_changed(event: Event) -> None:
            """Handle outdoor temperature change."""
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            temp_attr = new_state.attributes.get("temperature")
            if temp_attr is None:
                return
            try:
                temp = float(temp_attr)
            except (ValueError, TypeError):
                return

            now = time.monotonic()
            dt_seconds = (now - self._last_outdoor_temp_update) if self._last_outdoor_temp_update else 0
            self.update_outdoor_temp_lagged(temp, dt_seconds)
            self._last_outdoor_temp_update = now

            # Auto mode switching (existing behavior)
            if self._auto_mode_switching:
                self.hass.async_create_task(self._async_evaluate_auto_mode())

        self._outdoor_temp_unsub = async_track_state_change_event(
            self.hass,
            weather_entity_id,
            _async_outdoor_temp_changed,
        )
        _LOGGER.debug(
            "Tracking outdoor temp from %s (tau=%.1fh)",
            weather_entity_id,
            self._outdoor_temp_tau,
        )

    async def _async_evaluate_auto_mode(self) -> None:
        """Evaluate and apply auto mode switching if needed."""
        if not self._auto_mode_switching:
            return

        new_mode = await self._auto_mode_switching.async_evaluate()
        if new_mode:
            # H06: Only mark as switched when ≥1 zone actually received the command,
            # so a no-op (all zones OFF, service errors) doesn't consume the
            # rate-limit interval.
            zones_switched = await self._apply_house_mode(new_mode)
            if zones_switched > 0:
                self._auto_mode_switching.mark_switched(new_mode)

    async def _apply_house_mode(self, mode: str) -> int:
        """Apply HVAC mode to all non-OFF zones.

        C05: Temporarily sets ModeSync._sync_in_progress=True for the duration of the
        loop so that the first zone's state-change callback doesn't trigger a redundant
        ModeSync fan-out to the remaining N-1 zones before we've reached them ourselves.

        Args:
            mode: HVACMode to apply (HEAT or COOL)

        Returns:
            Number of zones that successfully received the mode command.
        """
        domain_data = self.hass.data.get("adaptive_climate", {})
        mode_sync = domain_data.get("mode_sync")

        # C05: Suppress ModeSync re-entrancy while we're already iterating zones.
        if mode_sync is not None:
            mode_sync._sync_in_progress = True

        zones_switched = 0
        try:
            for zone_id, zone in self._zones.items():
                climate_entity_id = zone.get("climate_entity_id")
                if not climate_entity_id:
                    _LOGGER.warning("No climate_entity_id for zone %s", zone_id)
                    continue
                # Read actual hvac_mode from the climate entity state, not zone data
                state = self.hass.states.get(climate_entity_id)
                if state is None:
                    _LOGGER.debug("Zone %s entity not yet available, skipping", zone_id)
                    continue
                if state.state == HVACMode.OFF:
                    continue
                try:
                    await self.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": climate_entity_id, "hvac_mode": mode},
                        blocking=False,
                    )
                    zones_switched += 1
                except Exception:
                    _LOGGER.exception("Failed to set mode %s for zone %s", mode, zone_id)
        finally:
            if mode_sync is not None:
                mode_sync._sync_in_progress = False

        return zones_switched

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from all zones.

        This method is called automatically by the coordinator at the
        configured update interval.

        Returns:
            Dictionary containing current state of all zones.
        """
        # For now, we just return the current state
        # In the future, this could fetch additional data or perform calculations
        return {
            "zones": self._zones,
            "demand": self._demand_states,
            "aggregate_demand": self.get_aggregate_demand(),
        }

    async def async_cleanup(self) -> None:
        """Clean up coordinator resources."""
        # Cancel startup evaluation timer if still pending
        if self._startup_eval_unsub is not None:
            self._startup_eval_unsub()
            self._startup_eval_unsub = None

        # Cancel outdoor temperature listener
        if self._outdoor_temp_unsub is not None:
            self._outdoor_temp_unsub()
            self._outdoor_temp_unsub = None
            _LOGGER.debug("Cancelled outdoor temperature listener")

        # Cancel water temperature control timers
        if self._water_temp_controller is not None:
            self._water_temp_controller.async_cleanup()
            self._water_temp_controller = None
            _LOGGER.debug("Cleaned up water temperature controller")


class ModeSync:
    """Mode synchronization across zones.

    Features:
    - Sync HEAT mode to all zones when one switches to HEAT
    - Sync COOL mode to all zones when one switches to COOL
    - Keep OFF mode independent per zone
    - Support disabling sync per zone
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AdaptiveThermostatCoordinator,
    ) -> None:
        """Initialize mode synchronization.

        Args:
            hass: Home Assistant instance
            coordinator: AdaptiveThermostatCoordinator instance
        """
        self.hass = hass
        self.coordinator = coordinator
        self._zone_modes: dict[str, str] = {}
        self._sync_disabled_zones: set[str] = set()
        self._sync_in_progress: bool = False

        # A02: Subscribe to zone lifecycle events from the coordinator dispatcher
        coordinator.zone_dispatcher.subscribe(CycleEventType.ZONE_REGISTERED, self._on_zone_registered)
        coordinator.zone_dispatcher.subscribe(CycleEventType.ZONE_UNREGISTERED, self._on_zone_unregistered)

        _LOGGER.debug("ModeSync initialized")

    def disable_sync_for_zone(self, zone_id: str) -> None:
        """Disable mode synchronization for a specific zone.

        Args:
            zone_id: Zone identifier to disable sync for
        """
        self._sync_disabled_zones.add(zone_id)
        _LOGGER.debug("Mode sync disabled for zone: %s", zone_id)

    def enable_sync_for_zone(self, zone_id: str) -> None:
        """Enable mode synchronization for a specific zone.

        Args:
            zone_id: Zone identifier to enable sync for
        """
        self._sync_disabled_zones.discard(zone_id)
        _LOGGER.debug("Mode sync enabled for zone: %s", zone_id)

    def is_sync_disabled(self, zone_id: str) -> bool:
        """Check if sync is disabled for a zone.

        Args:
            zone_id: Zone identifier to check

        Returns:
            True if sync is disabled for this zone
        """
        return zone_id in self._sync_disabled_zones

    def _prevailing_house_mode(self, all_zones: dict, exclude_zone_id: str) -> str | None:
        """Return the first active (heat/cool) mode among sync-enabled zones.

        Scans all registered zones except the one identified by exclude_zone_id,
        skipping zones with sync disabled, zones with no climate_entity_id, and
        zones whose HA state is OFF or unavailable.

        Args:
            all_zones: Mapping of zone_id → zone_data from coordinator.get_all_zones()
            exclude_zone_id: Zone ID to skip (the originating zone)

        Returns:
            "heat" or "cool" if a prevailing mode is found, else None
        """
        for other_zone_id, zone_data in all_zones.items():
            if other_zone_id == exclude_zone_id:
                continue
            if self.is_sync_disabled(other_zone_id):
                continue
            other_entity_id = zone_data.get("climate_entity_id")
            if not other_entity_id:
                continue
            state = self.hass.states.get(other_entity_id)
            if state is None or state.state not in ("heat", "cool"):
                continue
            return state.state
        return None

    async def on_mode_change(
        self,
        zone_id: str,
        old_mode: str,
        new_mode: str,
        climate_entity_id: str,
    ) -> None:
        """Handle mode change for a zone.

        When a zone changes to HEAT or COOL mode, synchronize all other zones
        (except those with sync disabled) to the same mode.
        OFF mode is kept independent per zone.

        Args:
            zone_id: Zone that changed mode
            old_mode: Previous mode (heat, cool, off, etc.)
            new_mode: New mode (heat, cool, off, etc.)
            climate_entity_id: Entity ID of the climate entity that changed
        """
        # Update stored mode
        self._zone_modes[zone_id] = new_mode.lower()

        # Only sync HEAT and COOL modes (not OFF)
        if new_mode.lower() not in ("heat", "cool"):
            _LOGGER.debug(
                "Zone %s changed to %s mode - no sync needed (not heat/cool)",
                zone_id,
                new_mode,
            )
            return

        # A sync-disabled zone is independent: it neither imposes its mode on
        # other zones nor conforms to theirs. Track its mode but do not sync.
        if self.is_sync_disabled(zone_id):
            _LOGGER.debug(
                "Zone %s changed to %s mode - sync disabled, not propagating",
                zone_id,
                new_mode,
            )
            return

        # Prevent feedback loop: skip if already syncing
        if self._sync_in_progress:
            _LOGGER.debug(
                "Zone %s mode change to %s - skipping sync (already in progress)",
                zone_id,
                new_mode,
            )
            return

        _LOGGER.info(
            "Zone %s changed to %s mode - synchronizing other zones",
            zone_id,
            new_mode,
        )

        # Set flag before syncing to prevent feedback loop
        self._sync_in_progress = True
        try:
            # Get all zones from coordinator
            all_zones = self.coordinator.get_all_zones()

            # Turn-on path: zone coming from OFF adopts prevailing house mode
            # instead of imposing its own, to prevent flipping the house mode.
            if old_mode.lower() == "off":
                prevailing = self._prevailing_house_mode(all_zones, exclude_zone_id=zone_id)
                if prevailing is not None and prevailing != new_mode.lower():
                    _LOGGER.info(
                        "Zone %s turning on in %s mode but house is running %s - conforming zone to house mode",
                        zone_id,
                        new_mode,
                        prevailing,
                    )
                    await self._set_zone_mode(zone_id, climate_entity_id, prevailing)
                    return

            # Sync mode to all other zones (except sync-disabled ones)
            for other_zone_id, zone_data in all_zones.items():
                # Skip the originating zone
                if other_zone_id == zone_id:
                    continue

                # Skip if sync is disabled for this zone
                if self.is_sync_disabled(other_zone_id):
                    _LOGGER.debug(
                        "Skipping zone %s - sync disabled",
                        other_zone_id,
                    )
                    continue

                # Get climate entity ID for this zone
                other_climate_entity_id = zone_data.get("climate_entity_id")
                if not other_climate_entity_id:
                    _LOGGER.warning(
                        "No climate entity ID found for zone %s",
                        other_zone_id,
                    )
                    continue

                # Skip zones that are currently OFF (OFF stays independent)
                other_state = self.hass.states.get(other_climate_entity_id)
                if other_state and other_state.state == "off":
                    _LOGGER.debug(
                        "Skipping zone %s - currently OFF (independent)",
                        other_zone_id,
                    )
                    continue

                # Set the mode for this zone
                await self._set_zone_mode(
                    other_zone_id,
                    other_climate_entity_id,
                    new_mode.lower(),
                )
        finally:
            # Always clear flag after sync completes (or on error)
            self._sync_in_progress = False

    async def _set_zone_mode(
        self,
        zone_id: str,
        climate_entity_id: str,
        mode: str,
    ) -> None:
        """Set mode for a specific zone.

        Args:
            zone_id: Zone identifier
            climate_entity_id: Climate entity ID
            mode: Mode to set (heat, cool, off, etc.)
        """
        try:
            # Get current state
            state = self.hass.states.get(climate_entity_id)
            if state is None:
                _LOGGER.warning(
                    "Climate entity %s not found for zone %s",
                    climate_entity_id,
                    zone_id,
                )
                return

            current_mode = state.state

            # Only change if different
            if current_mode == mode:
                _LOGGER.debug(
                    "Zone %s already in %s mode",
                    zone_id,
                    mode,
                )
                return

            # Set the mode
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {
                    "entity_id": climate_entity_id,
                    "hvac_mode": mode,
                },
                blocking=True,
            )

            # Update stored mode
            self._zone_modes[zone_id] = mode

            _LOGGER.info(
                "Synced zone %s (%s) to %s mode",
                zone_id,
                climate_entity_id,
                mode,
            )
        except Exception as e:
            _LOGGER.error(
                "Error setting mode for zone %s: %s",
                zone_id,
                e,
            )

    def get_zone_mode(self, zone_id: str) -> str | None:
        """Get the current mode for a zone.

        Args:
            zone_id: Zone identifier

        Returns:
            Current mode or None if not tracked
        """
        return self._zone_modes.get(zone_id)

    def is_sync_in_progress(self) -> bool:
        """Check if a sync operation is currently in progress.

        Returns:
            True if sync is in progress
        """
        return self._sync_in_progress

    def _on_zone_registered(self, event: ZoneRegisteredEvent) -> None:
        """Handle zone registered event (A02).

        Args:
            event: ZoneRegisteredEvent from the coordinator dispatcher
        """
        _LOGGER.debug("ModeSync: zone registered: %s", event.zone_id)

    def _on_zone_unregistered(self, event: ZoneUnregisteredEvent) -> None:
        """Handle zone unregistered event — delegates to unregister_zone (A02).

        Args:
            event: ZoneUnregisteredEvent from the coordinator dispatcher
        """
        self.unregister_zone(event.zone_id)

    def unregister_zone(self, zone_id: str) -> None:
        """Unregister a zone from mode synchronization.

        Removes the zone from all tracking dicts (zone modes, sync disabled zones).
        This should be called when a climate entity is being removed.

        Args:
            zone_id: Zone identifier to unregister
        """
        # Remove from zone modes dict
        if zone_id in self._zone_modes:
            del self._zone_modes[zone_id]

        # Remove from sync disabled zones set
        self._sync_disabled_zones.discard(zone_id)

        _LOGGER.debug("Unregistered zone from ModeSync: %s", zone_id)
