"""Registry helper utilities for zone discovery."""

from typing import Dict, List, Optional

try:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import (
        entity_registry as er,
        area_registry as ar,
        floor_registry as fr,
    )
except ImportError:
    # For testing without full HA installation
    pass


def discover_zone_floors(
    hass: "HomeAssistant", zone_entity_ids: List[str]
) -> Dict[str, Optional[int]]:
    """Discover floor levels for zones using entity/area/floor registries.

    Args:
        hass: Home Assistant instance
        zone_entity_ids: List of climate entity IDs to discover floors for

    Returns:
        Dictionary mapping entity_id -> floor level (int) or None if not assigned.
        Returns None for a zone if:
        - Entity is not in entity registry
        - Entity has no area_id
        - Area has no floor_id
        - Floor is not in floor registry

    Example:
        >>> discover_zone_floors(hass, ["climate.living_room", "climate.bedroom"])
        {
            "climate.living_room": 0,
            "climate.bedroom": 1,
        }
    """
    result: Dict[str, Optional[int]] = {}

    # Get registry instances
    entity_registry = er.async_get(hass)
    area_registry = ar.async_get(hass)
    floor_registry = fr.async_get(hass)

    # Process each zone entity
    for entity_id in zone_entity_ids:
        # Step 1: Get entity entry
        entity_entry = entity_registry.async_get(entity_id)
        if entity_entry is None:
            result[entity_id] = None
            continue

        # Step 2: Check if entity has area_id (Story 1.1, Step 4)
        if entity_entry.area_id is None:
            result[entity_id] = None
            continue

        # Step 3: Get area entry
        area_entry = area_registry.async_get(entity_entry.area_id)
        if area_entry is None:
            result[entity_id] = None
            continue

        # Step 4: Check if area has floor_id (Story 1.1, Step 6)
        if area_entry.floor_id is None:
            result[entity_id] = None
            continue

        # Step 5: Get floor entry
        floor_entry = floor_registry.async_get(area_entry.floor_id)
        if floor_entry is None:
            result[entity_id] = None
            continue

        # Step 6: Extract floor level
        result[entity_id] = floor_entry.level

    # Story 1.1, Step 8: Loop continues processing remaining zones after None case
    return result
