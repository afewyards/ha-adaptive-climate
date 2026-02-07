"""Weekly performance reports for adaptive thermostat."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# Status sort order: higher = listed first
_STATUS_ORDER = {
    "optimized": 0,
    "tuned": 1,
    "stable": 2,
    "collecting": 3,
    "idle": 4,
}

# Comfort thresholds for problem detection
COMFORT_PROBLEM_THRESHOLD = 65
COMFORT_DROP_THRESHOLD = 15


@dataclass
class ZoneReportData:
    """Per-zone data for the weekly report."""

    zone_id: str
    duty_cycle: float
    comfort_score: float | None = None
    comfort_score_prev: float | None = None
    learning_status: str | None = None
    learning_status_prev: str | None = None
    confidence: int | None = None
    confidence_prev: int | None = None
    recovery_cycles: int | None = None
    humidity_pauses: int | None = None
    contact_pauses: int | None = None
    area_m2: float | None = None

    @property
    def display_name(self) -> str:
        """Human-readable zone name."""
        return self.zone_id.replace("_", " ").title()

    @property
    def confidence_delta(self) -> int | None:
        """Week-over-week confidence change in percentage points."""
        if self.confidence is not None and self.confidence_prev is not None:
            return self.confidence - self.confidence_prev
        return None

    @property
    def has_comfort_problem(self) -> bool:
        """Zone has a comfort issue worth reporting."""
        if self.comfort_score is None:
            return False
        if self.comfort_score < COMFORT_PROBLEM_THRESHOLD:
            return True
        if (
            self.comfort_score_prev is not None
            and self.comfort_score_prev - self.comfort_score >= COMFORT_DROP_THRESHOLD
        ):
            return True
        return False

    @property
    def has_pause_problems(self) -> bool:
        """Zone had notable pause events."""
        return (self.humidity_pauses or 0) >= 3 or (self.contact_pauses or 0) >= 3

    @property
    def has_problems(self) -> bool:
        """Zone has any reportable problem."""
        return self.has_comfort_problem or self.has_pause_problems

    @property
    def has_tier_change(self) -> bool:
        """Zone changed learning tier this week."""
        return (
            self.learning_status_prev is not None
            and self.learning_status is not None
            and self.learning_status != self.learning_status_prev
        )


class WeeklyReport:
    """Generate weekly performance reports focused on learning and problems."""

    def __init__(self, start_date: datetime, end_date: datetime) -> None:
        self.start_date = start_date
        self.end_date = end_date
        self.zones: dict[str, ZoneReportData] = {}
        self.health_status: str = "healthy"
        self.active_zones: int = 0

    def add_zone_data(
        self,
        zone_id: str,
        duty_cycle: float,
        comfort_score: float | None = None,
        comfort_score_prev: float | None = None,
        learning_status: str | None = None,
        learning_status_prev: str | None = None,
        confidence: int | None = None,
        confidence_prev: int | None = None,
        recovery_cycles: int | None = None,
        humidity_pauses: int | None = None,
        contact_pauses: int | None = None,
        area_m2: float | None = None,
    ) -> None:
        """Add performance data for a zone."""
        self.zones[zone_id] = ZoneReportData(
            zone_id=zone_id,
            duty_cycle=duty_cycle,
            comfort_score=comfort_score,
            comfort_score_prev=comfort_score_prev,
            learning_status=learning_status,
            learning_status_prev=learning_status_prev,
            confidence=confidence,
            confidence_prev=confidence_prev,
            recovery_cycles=recovery_cycles,
            humidity_pauses=humidity_pauses,
            contact_pauses=contact_pauses,
            area_m2=area_m2,
        )
        if duty_cycle > 5:
            self.active_zones += 1

    def _sorted_zones(self) -> list[ZoneReportData]:
        """Zones sorted by learning status (best first)."""
        return sorted(
            self.zones.values(),
            key=lambda z: _STATUS_ORDER.get(z.learning_status or "idle", 99),
        )

    def _problem_zones(self) -> list[ZoneReportData]:
        """Zones with reportable problems."""
        return [z for z in self.zones.values() if z.has_problems]

    def _format_learning_line(self, z: ZoneReportData) -> str:
        """Format one zone's learning progress line."""
        parts = [f"**{z.display_name}**"]

        status = z.learning_status or "idle"
        parts.append(f"— `{status}`")
        if z.confidence is not None:
            parts.append(f"({z.confidence}%)")

        delta = z.confidence_delta
        if delta is not None and delta != 0:
            sign = "+" if delta > 0 else ""
            parts.append(f"· {sign}{delta}% this week")

        if z.recovery_cycles is not None and z.recovery_cycles > 0:
            parts.append(f"· {z.recovery_cycles} recovery cycle{'s' if z.recovery_cycles != 1 else ''}")
        elif z.humidity_pauses and z.humidity_pauses > 0:
            parts.append("· no recovery cycles (humidity pauses)")
        elif z.contact_pauses and z.contact_pauses > 0:
            parts.append("· no recovery cycles (contact pauses)")

        return " ".join(parts)

    def _format_problem_line(self, z: ZoneReportData) -> str:
        """Format one zone's problem line."""
        parts = [f"**{z.display_name}**"]
        details = []

        if z.has_comfort_problem and z.comfort_score is not None:
            comfort_str = f"comfort dropped to {z.comfort_score:.0f}%"
            if z.comfort_score_prev is not None:
                comfort_str += f" (was {z.comfort_score_prev:.0f}%)"
            details.append(comfort_str)

        if z.contact_pauses and z.contact_pauses >= 3:
            details.append(f"{z.contact_pauses} contact sensor pause{'s' if z.contact_pauses != 1 else ''}")

        if z.humidity_pauses and z.humidity_pauses >= 3:
            details.append(f"{z.humidity_pauses} humidity pause{'s' if z.humidity_pauses != 1 else ''}")

        parts.append("— " + " · ".join(details))
        return " ".join(parts)

    def _notable_highlights(self) -> list[str]:
        """Notable events for iOS summary."""
        highlights = []
        for z in self.zones.values():
            if z.has_tier_change:
                highlights.append(f'{z.display_name} reached "{z.learning_status}"')
            if z.has_comfort_problem and z.comfort_score is not None:
                highlights.append(f"{z.display_name} comfort dropped to {z.comfort_score:.0f}%")
        return highlights

    def format_markdown_report(self) -> str:
        """Format the report as markdown for persistent notification."""
        lines = []

        start_str = self.start_date.strftime("%b %-d")
        end_str = self.end_date.strftime("%b %-d")
        lines.append("## Weekly Heating Report")
        lines.append(f"{start_str} – {end_str}")
        lines.append("")

        lines.append("### Learning Progress")
        for z in self._sorted_zones():
            lines.append(self._format_learning_line(z))
        lines.append("")

        problems = self._problem_zones()
        if problems:
            lines.append("### Needs Attention")
            for z in problems:
                lines.append(self._format_problem_line(z))
            lines.append("")

        return "\n".join(lines)

    def format_ios_summary(self) -> str:
        """Format a short summary for iOS notification."""
        highlights = self._notable_highlights()

        lines = []
        if highlights:
            lines.append(" · ".join(highlights[:3]))
        else:
            lines.append("All zones progressing normally")

        zone_str = f"{self.active_zones} zone{'s' if self.active_zones != 1 else ''} active"
        lines.append(f"{zone_str} · System {self.health_status}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert report to dictionary format for storage."""
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "zones": {
                zid: {
                    "duty_cycle": z.duty_cycle,
                    "comfort_score": z.comfort_score,
                    "learning_status": z.learning_status,
                    "confidence": z.confidence,
                }
                for zid, z in self.zones.items()
            },
            "health_status": self.health_status,
            "active_zones": self.active_zones,
        }
