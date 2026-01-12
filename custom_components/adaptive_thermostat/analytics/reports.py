"""Weekly performance reports for adaptive thermostat."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


class WeeklyReport:
    """
    Generate weekly performance reports for the heating system.

    Includes energy consumption, costs, and duty cycle metrics per zone.
    Gracefully handles missing energy meter data.
    """

    def __init__(
        self,
        start_date: datetime,
        end_date: datetime,
    ):
        """
        Initialize weekly report.

        Args:
            start_date: Start of reporting period
            end_date: End of reporting period
        """
        self.start_date = start_date
        self.end_date = end_date
        self.zones: dict[str, dict] = {}
        self.total_energy_kwh: Optional[float] = None
        self.total_cost: Optional[float] = None

    def add_zone_data(
        self,
        zone_id: str,
        duty_cycle: float,
        energy_kwh: Optional[float] = None,
        cost: Optional[float] = None,
    ) -> None:
        """
        Add performance data for a zone.

        Args:
            zone_id: Zone identifier
            duty_cycle: Average duty cycle as percentage (0-100)
            energy_kwh: Energy consumption in kWh (None if not available)
            cost: Cost in currency units (None if not available)
        """
        self.zones[zone_id] = {
            "duty_cycle": duty_cycle,
            "energy_kwh": energy_kwh,
            "cost": cost,
        }

    def set_totals(
        self,
        total_energy_kwh: Optional[float] = None,
        total_cost: Optional[float] = None,
    ) -> None:
        """
        Set system-wide totals.

        Args:
            total_energy_kwh: Total energy consumption in kWh
            total_cost: Total cost in currency units
        """
        self.total_energy_kwh = total_energy_kwh
        self.total_cost = total_cost

    def format_report(self, currency_symbol: str = "€") -> str:
        """
        Format the report as a human-readable string.

        Args:
            currency_symbol: Currency symbol to use (default €)

        Returns:
            Formatted report string
        """
        lines = []

        # Header
        lines.append("Weekly Heating Performance Report")
        lines.append("=" * 40)
        lines.append(f"Period: {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}")
        lines.append("")

        # System totals
        lines.append("System Totals:")
        if self.total_energy_kwh is not None:
            lines.append(f"  Total Energy: {self.total_energy_kwh:.1f} kWh")
        else:
            lines.append("  Total Energy: N/A (no meter data)")

        if self.total_cost is not None:
            lines.append(f"  Total Cost: {currency_symbol}{self.total_cost:.2f}")
        else:
            lines.append(f"  Total Cost: N/A (no cost data)")

        lines.append("")

        # Zone breakdown
        lines.append("Zone Performance:")
        for zone_id in sorted(self.zones.keys()):
            zone_data = self.zones[zone_id]
            lines.append(f"  {zone_id}:")
            lines.append(f"    Avg Duty Cycle: {zone_data['duty_cycle']:.1f}%")

            if zone_data['energy_kwh'] is not None:
                lines.append(f"    Energy: {zone_data['energy_kwh']:.1f} kWh")

            if zone_data['cost'] is not None:
                lines.append(f"    Cost: {currency_symbol}{zone_data['cost']:.2f}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """
        Convert report to dictionary format for storage.

        Returns:
            Dictionary representation of the report
        """
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_energy_kwh": self.total_energy_kwh,
            "total_cost": self.total_cost,
            "zones": self.zones.copy(),
        }


def generate_weekly_report(
    zones_data: dict[str, dict],
    start_date: datetime,
    end_date: datetime,
    total_energy_kwh: Optional[float] = None,
    total_cost: Optional[float] = None,
) -> WeeklyReport:
    """
    Generate a weekly performance report from zone data.

    Args:
        zones_data: Dictionary of zone performance data
            Format: {
                "zone_id": {
                    "duty_cycle": float,
                    "energy_kwh": Optional[float],
                    "cost": Optional[float],
                }
            }
        start_date: Start of reporting period
        end_date: End of reporting period
        total_energy_kwh: Total system energy consumption (optional)
        total_cost: Total system cost (optional)

    Returns:
        WeeklyReport instance with all data populated
    """
    report = WeeklyReport(start_date, end_date)

    # Add zone data
    for zone_id, zone_data in zones_data.items():
        report.add_zone_data(
            zone_id=zone_id,
            duty_cycle=zone_data.get("duty_cycle", 0.0),
            energy_kwh=zone_data.get("energy_kwh"),
            cost=zone_data.get("cost"),
        )

    # Set totals
    report.set_totals(total_energy_kwh, total_cost)

    return report
