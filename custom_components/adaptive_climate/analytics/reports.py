"""Weekly performance reports for adaptive thermostat."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


class WeeklyReport:
    """
    Generate weekly performance reports for the heating system.

    Includes energy consumption, costs, comfort metrics, and week-over-week comparison.
    Gracefully handles missing data.
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

        # Comfort metrics
        self.comfort_scores: dict[str, float] = {}
        self.time_at_target: dict[str, float] = {}

        # Zone cost breakdown (estimated)
        self.zone_costs: dict[str, float] = {}

        # Week-over-week comparison
        self.cost_change_pct: Optional[float] = None
        self.energy_change_pct: Optional[float] = None

        # Health summary
        self.health_status: str = "healthy"
        self.active_zones: int = 0

    def add_zone_data(
        self,
        zone_id: str,
        duty_cycle: float,
        energy_kwh: Optional[float] = None,
        cost: Optional[float] = None,
        comfort_score: Optional[float] = None,
        time_at_target: Optional[float] = None,
        area_m2: Optional[float] = None,
    ) -> None:
        """
        Add performance data for a zone.

        Args:
            zone_id: Zone identifier
            duty_cycle: Average duty cycle as percentage (0-100)
            energy_kwh: Energy consumption in kWh (None if not available)
            cost: Cost in currency units (None if not available)
            comfort_score: Comfort score 0-100 (None if not available)
            time_at_target: Time at target percentage (None if not available)
            area_m2: Zone area in m² (None if not available)
        """
        self.zones[zone_id] = {
            "duty_cycle": duty_cycle,
            "energy_kwh": energy_kwh,
            "cost": cost,
            "area_m2": area_m2,
        }

        if comfort_score is not None:
            self.comfort_scores[zone_id] = comfort_score

        if time_at_target is not None:
            self.time_at_target[zone_id] = time_at_target

        # Count active zones (duty cycle > 5%)
        if duty_cycle > 5:
            self.active_zones += 1

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

    def get_average_duty_cycle(self) -> Optional[float]:
        """
        Calculate the average duty cycle across all zones.

        Returns:
            Average duty cycle as percentage, or None if no zones
        """
        if not self.zones:
            return None
        total = sum(zone["duty_cycle"] for zone in self.zones.values())
        return total / len(self.zones)

    def get_average_comfort(self) -> Optional[float]:
        """
        Calculate the average comfort score across all zones.

        Returns:
            Average comfort score, or None if no data
        """
        if not self.comfort_scores:
            return None
        return sum(self.comfort_scores.values()) / len(self.comfort_scores)

    def get_best_zone(self) -> Optional[tuple[str, float]]:
        """
        Get the zone with the highest comfort score.

        Returns:
            Tuple of (zone_id, score), or None if no data
        """
        if not self.comfort_scores:
            return None
        best_zone = max(self.comfort_scores.items(), key=lambda x: x[1])
        return best_zone

    def set_week_over_week(
        self,
        cost_change_pct: Optional[float] = None,
        energy_change_pct: Optional[float] = None,
    ) -> None:
        """
        Set week-over-week comparison data.

        Args:
            cost_change_pct: Cost change percentage (negative = decrease)
            energy_change_pct: Energy change percentage
        """
        self.cost_change_pct = cost_change_pct
        self.energy_change_pct = energy_change_pct

    def calculate_zone_costs(self) -> None:
        """
        Estimate cost breakdown per zone based on duty cycle and area.

        Distributes total_cost proportionally to each zone's
        weighted contribution (duty_cycle × area).
        """
        if self.total_cost is None or self.total_cost <= 0:
            return

        # Calculate weighted contribution for each zone
        weights = {}
        total_weight = 0

        for zone_id, zone_data in self.zones.items():
            duty = zone_data.get("duty_cycle", 0)
            area = zone_data.get("area_m2", 1) or 1  # Default to 1 if not set
            weight = duty * area
            weights[zone_id] = weight
            total_weight += weight

        if total_weight <= 0:
            return

        # Distribute cost proportionally
        for zone_id, weight in weights.items():
            self.zone_costs[zone_id] = (weight / total_weight) * self.total_cost

    def format_summary(self, currency_symbol: str = "€") -> str:
        """
        Format a short, digestible summary for mobile notification.

        Args:
            currency_symbol: Currency symbol to use

        Returns:
            3-line summary string
        """
        lines = []

        # Line 1: Cost with week-over-week change
        if self.total_cost is not None:
            cost_str = f"{currency_symbol}{self.total_cost:.2f} spent"
            if self.cost_change_pct is not None:
                arrow = "↓" if self.cost_change_pct < 0 else "↑"
                cost_str += f" ({arrow}{abs(self.cost_change_pct):.0f}%)"
            lines.append(cost_str)
        elif self.total_energy_kwh is not None:
            lines.append(f"{self.total_energy_kwh:.1f} kWh used")
        else:
            avg_duty = self.get_average_duty_cycle()
            if avg_duty is not None:
                lines.append(f"Avg {avg_duty:.0f}% duty cycle")

        # Line 2: Comfort summary
        avg_comfort = self.get_average_comfort()
        best_zone = self.get_best_zone()
        if avg_comfort is not None:
            comfort_str = f"{avg_comfort:.0f}% comfort avg"
            if best_zone:
                # Format zone name nicely
                zone_name = best_zone[0].replace("_", " ").title()
                comfort_str += f" · Best: {zone_name}"
            lines.append(comfort_str)

        # Line 3: Health and zone count
        zone_str = f"{self.active_zones} zone{'s' if self.active_zones != 1 else ''} active"
        if self.health_status == "healthy":
            lines.append(f"{zone_str} · System healthy")
        else:
            lines.append(f"{zone_str} · {self.health_status}")

        return "\n".join(lines)

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
            energy_str = f"  Energy: {self.total_energy_kwh:.1f} kWh"
            if self.energy_change_pct is not None:
                arrow = "↓" if self.energy_change_pct < 0 else "↑"
                energy_str += f" ({arrow}{abs(self.energy_change_pct):.0f}% vs last week)"
            lines.append(energy_str)
        else:
            lines.append("  Energy: N/A (no meter data)")

        if self.total_cost is not None:
            cost_str = f"  Cost: {currency_symbol}{self.total_cost:.2f}"
            if self.cost_change_pct is not None:
                arrow = "↓" if self.cost_change_pct < 0 else "↑"
                cost_str += f" ({arrow}{abs(self.cost_change_pct):.0f}% vs last week)"
            lines.append(cost_str)
        else:
            lines.append("  Cost: N/A (no cost data)")

        # Average comfort
        avg_comfort = self.get_average_comfort()
        if avg_comfort is not None:
            lines.append(f"  Avg Comfort: {avg_comfort:.0f}%")

        lines.append("")

        # Zone breakdown
        lines.append("Zone Performance:")
        for zone_id in sorted(self.zones.keys()):
            zone_data = self.zones[zone_id]
            # Format zone name nicely
            zone_name = zone_id.replace("_", " ").title()
            lines.append(f"  {zone_name}:")
            lines.append(f"    Duty Cycle: {zone_data['duty_cycle']:.1f}%")

            # Comfort score
            if zone_id in self.comfort_scores:
                score = self.comfort_scores[zone_id]
                # Add indicator
                indicator = "✓" if score >= 80 else ("○" if score >= 60 else "!")
                lines.append(f"    Comfort: {score:.0f}% {indicator}")

            # Estimated zone cost
            if zone_id in self.zone_costs:
                lines.append(f"    Est. Cost: {currency_symbol}{self.zone_costs[zone_id]:.2f}")

            if zone_data.get('energy_kwh') is not None:
                lines.append(f"    Energy: {zone_data['energy_kwh']:.1f} kWh")

        # Health status
        if self.health_status != "healthy":
            lines.append("")
            lines.append(f"Health: {self.health_status}")

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
            "comfort_scores": self.comfort_scores.copy(),
            "time_at_target": self.time_at_target.copy(),
            "zone_costs": self.zone_costs.copy(),
            "cost_change_pct": self.cost_change_pct,
            "energy_change_pct": self.energy_change_pct,
            "health_status": self.health_status,
            "active_zones": self.active_zones,
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
