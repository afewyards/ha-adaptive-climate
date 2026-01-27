"""Data Update Coordinator for Adaptive Thermostat."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

try:
    from .const import DOMAIN
    from .adaptive.sun_position import SunPositionCalculator, ORIENTATION_AZIMUTH
except ImportError:
    from const import DOMAIN
    from adaptive.sun_position import SunPositionCalculator, ORIENTATION_AZIMUTH

if TYPE_CHECKING:
    from .adaptive.manifold_registry import ManifoldRegistry
    from .central_controller import CentralController as CentralControllerType

_LOGGER = logging.getLogger(__name__)


class AdaptiveThermostatCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from thermostats and coordinating cross-zone state."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self._zones: dict[str, dict[str, Any]] = {}
        self._demand_states: dict[str, bool] = {}
        self._central_controller: "CentralController | None" = None
        self._sun_position_calculator = SunPositionCalculator.from_hass(hass)
        self._thermal_group_manager: Any = None  # ThermalGroupManager or None
        self._manifold_registry: "ManifoldRegistry | None" = None
        self._zone_loops: dict[str, int] = {}

    def set_central_controller(self, controller: "CentralController") -> None:
        """Set the central controller reference for push-based updates."""
        self._central_controller = controller

    def set_thermal_group_manager(self, manager: Any) -> None:
        """Set the thermal group manager reference.

        Args:
            manager: ThermalGroupManager instance or None
        """
        self._thermal_group_manager = manager

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

    def set_manifold_registry(self, registry: "ManifoldRegistry") -> None:
        """Set the manifold registry reference for transport delay calculations.

        Args:
            registry: ManifoldRegistry instance
        """
        self._manifold_registry = registry

    def update_zone_loops(self, zone_id: str, loops: int) -> None:
        """Update the number of heating loops for a zone.

        Args:
            zone_id: Unique identifier for the zone
            loops: Number of heating loops in this zone
        """
        self._zone_loops[zone_id] = loops

    def get_transport_delay_for_zone(self, zone_id: str) -> float:
        """Get the transport delay for a zone in minutes.

        Calculates the time for hot water to reach the zone based on active
        zones and their loop counts. Only considers zones with heating demand.

        Args:
            zone_id: Unique identifier for the zone

        Returns:
            Transport delay in minutes. Returns 0.0 if no registry is set.
        """
        if self._manifold_registry is None:
            return 0.0

        # Build active_zones dict: {zone_id: loops} for zones with heating demand
        active_zones: dict[str, int] = {}
        for zone_slug, demand_state in self._demand_states.items():
            # Only include zones with heating demand
            if demand_state.get("demand") and demand_state.get("mode") == "heat":
                # Convert slug to entity_id (demand_states uses slugs, but zone_loops and registry use entity_ids)
                zone_entity_id = f"climate.{zone_slug}"
                # Get loop count from _zone_loops, default to 1 if not set
                loop_count = self._zone_loops.get(zone_entity_id, 1)
                active_zones[zone_entity_id] = loop_count

        # Call registry to calculate transport delay
        return self._manifold_registry.get_transport_delay(zone_id, active_zones)

    @property
    def outdoor_temp(self) -> float | None:
        """Get the current outdoor temperature from the weather entity.

        Returns:
            Outdoor temperature in °C, or None if unavailable.
        """
        # Get weather entity from hass.data
        domain_data = self.hass.data.get(DOMAIN, {})
        weather_entity_id = domain_data.get("weather_entity")

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
        self._demand_states[zone_id] = {"demand": False, "mode": None}
        _LOGGER.debug("Registered zone: %s", zone_id)

    def unregister_zone(self, zone_id: str) -> None:
        """Unregister a zone from the coordinator.

        Removes the zone from both the zones dict and demand states dict.
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
                    zone_id, old_state.get("demand"), old_state.get("mode"),
                    has_demand, hvac_mode
                )

                # Trigger central controller
                if self._central_controller:
                    _LOGGER.info("Triggering CentralController update")
                    self.hass.async_create_task(self._central_controller.update())

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
        for zone_id, zone_data in self._zones.items():
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
            state.get("demand") and state.get("mode") == "heat"
            for state in self._demand_states.values()
        )
        has_cooling_demand = any(
            state.get("demand") and state.get("mode") == "cool"
            for state in self._demand_states.values()
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

    def update_zone_temp(self, zone_id: str, temperature: float) -> None:
        """Update the current temperature for a zone.

        Args:
            zone_id: Unique identifier for the zone
            temperature: Current temperature in °C
        """
        if zone_id in self._zones:
            self._zones[zone_id]["current_temp"] = temperature

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


