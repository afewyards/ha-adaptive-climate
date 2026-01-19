"""Thermal coupling learning for multi-zone heat transfer prediction.

This module provides automatic learning of thermal coupling coefficients between
zones, enabling feedforward compensation to reduce overshoot when neighboring
zones are heating.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any


@dataclass
class CouplingObservation:
    """A single observation of heat transfer between two zones.

    Records the temperature changes in source and target zones during a
    heating event, along with environmental conditions.
    """

    timestamp: datetime
    source_zone: str          # Entity ID of the zone that was heating
    target_zone: str          # Entity ID of the zone being observed
    source_temp_start: float  # Source zone temp at observation start (°C)
    source_temp_end: float    # Source zone temp at observation end (°C)
    target_temp_start: float  # Target zone temp at observation start (°C)
    target_temp_end: float    # Target zone temp at observation end (°C)
    outdoor_temp_start: float # Outdoor temp at observation start (°C)
    outdoor_temp_end: float   # Outdoor temp at observation end (°C)
    duration_minutes: float   # Duration of observation in minutes

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to dictionary for persistence."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "source_zone": self.source_zone,
            "target_zone": self.target_zone,
            "source_temp_start": self.source_temp_start,
            "source_temp_end": self.source_temp_end,
            "target_temp_start": self.target_temp_start,
            "target_temp_end": self.target_temp_end,
            "outdoor_temp_start": self.outdoor_temp_start,
            "outdoor_temp_end": self.outdoor_temp_end,
            "duration_minutes": self.duration_minutes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CouplingObservation":
        """Create observation from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source_zone=data["source_zone"],
            target_zone=data["target_zone"],
            source_temp_start=data["source_temp_start"],
            source_temp_end=data["source_temp_end"],
            target_temp_start=data["target_temp_start"],
            target_temp_end=data["target_temp_end"],
            outdoor_temp_start=data["outdoor_temp_start"],
            outdoor_temp_end=data["outdoor_temp_end"],
            duration_minutes=data["duration_minutes"],
        )
