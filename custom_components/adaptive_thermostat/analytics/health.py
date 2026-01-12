"""Health monitoring with alerts for adaptive thermostat system."""
from enum import Enum
from typing import Optional, Dict, List, Any


class HealthStatus(Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


class HealthIssue:
    """Represents a health issue detected in a zone."""

    def __init__(self, zone: str, severity: HealthStatus, issue_type: str, message: str):
        """Initialize health issue.

        Args:
            zone: Zone name
            severity: Issue severity (HEALTHY, WARNING, CRITICAL)
            issue_type: Type of issue (short_cycle, high_power, sensor_unavailable)
            message: Human-readable description
        """
        self.zone = zone
        self.severity = severity
        self.issue_type = issue_type
        self.message = message


class HealthMonitor:
    """Monitors health of individual zones."""

    # Thresholds
    CRITICAL_CYCLE_TIME_MIN = 10  # Minutes
    WARNING_CYCLE_TIME_MIN = 15   # Minutes
    HIGH_POWER_W_M2 = 20.0        # W/m²

    def __init__(self, zone_name: str, exception_zones: Optional[List[str]] = None):
        """Initialize health monitor for a zone.

        Args:
            zone_name: Name of the zone to monitor
            exception_zones: List of zone names that are exceptions to high power rule
        """
        self.zone_name = zone_name
        self.exception_zones = exception_zones or []

    def check_cycle_time(self, cycle_time_min: Optional[float]) -> Optional[HealthIssue]:
        """Check if cycle time is too short.

        Args:
            cycle_time_min: Average cycle time in minutes

        Returns:
            HealthIssue if problem detected, None otherwise
        """
        if cycle_time_min is None:
            return None

        if cycle_time_min < self.CRITICAL_CYCLE_TIME_MIN:
            return HealthIssue(
                zone=self.zone_name,
                severity=HealthStatus.CRITICAL,
                issue_type="short_cycle",
                message=f"Critical: Very short cycling detected ({cycle_time_min:.1f} min). "
                       f"This causes excessive valve wear."
            )

        if cycle_time_min < self.WARNING_CYCLE_TIME_MIN:
            return HealthIssue(
                zone=self.zone_name,
                severity=HealthStatus.WARNING,
                issue_type="short_cycle",
                message=f"Warning: Short cycling detected ({cycle_time_min:.1f} min). "
                       f"Consider increasing PWM period."
            )

        return None

    def check_power_consumption(self, power_w_m2: Optional[float]) -> Optional[HealthIssue]:
        """Check if power consumption is too high.

        Args:
            power_w_m2: Power consumption in W/m²

        Returns:
            HealthIssue if problem detected, None otherwise
        """
        if power_w_m2 is None:
            return None

        # Exception zones (like bathroom) are allowed high power
        if self.zone_name in self.exception_zones:
            return None

        if power_w_m2 > self.HIGH_POWER_W_M2:
            return HealthIssue(
                zone=self.zone_name,
                severity=HealthStatus.WARNING,
                issue_type="high_power",
                message=f"Warning: High power consumption ({power_w_m2:.1f} W/m²). "
                       f"Check for heat loss or poor insulation."
            )

        return None

    def check_sensor_availability(self, sensor_available: bool) -> Optional[HealthIssue]:
        """Check if temperature sensor is available.

        Args:
            sensor_available: Whether the sensor is available

        Returns:
            HealthIssue if sensor unavailable, None otherwise
        """
        if not sensor_available:
            return HealthIssue(
                zone=self.zone_name,
                severity=HealthStatus.CRITICAL,
                issue_type="sensor_unavailable",
                message=f"Critical: Temperature sensor unavailable for {self.zone_name}. "
                       f"Cannot control heating."
            )

        return None

    def check_all(
        self,
        cycle_time_min: Optional[float],
        power_w_m2: Optional[float],
        sensor_available: bool
    ) -> List[HealthIssue]:
        """Run all health checks for the zone.

        Args:
            cycle_time_min: Average cycle time in minutes
            power_w_m2: Power consumption in W/m²
            sensor_available: Whether temperature sensor is available

        Returns:
            List of health issues (empty if all healthy)
        """
        issues = []

        # Check sensor first (most critical)
        sensor_issue = self.check_sensor_availability(sensor_available)
        if sensor_issue:
            issues.append(sensor_issue)

        # Check cycle time
        cycle_issue = self.check_cycle_time(cycle_time_min)
        if cycle_issue:
            issues.append(cycle_issue)

        # Check power consumption
        power_issue = self.check_power_consumption(power_w_m2)
        if power_issue:
            issues.append(power_issue)

        return issues


class SystemHealthMonitor:
    """Monitors aggregate health across all zones."""

    def __init__(self, exception_zones: Optional[List[str]] = None):
        """Initialize system health monitor.

        Args:
            exception_zones: List of zone names that are exceptions to high power rule
        """
        self.exception_zones = exception_zones or []

    def aggregate_health(self, zone_issues: Dict[str, List[HealthIssue]]) -> HealthStatus:
        """Determine overall system health from zone issues.

        Args:
            zone_issues: Dictionary mapping zone names to their health issues

        Returns:
            Overall system health status
        """
        has_critical = False
        has_warning = False

        for issues in zone_issues.values():
            for issue in issues:
                if issue.severity == HealthStatus.CRITICAL:
                    has_critical = True
                elif issue.severity == HealthStatus.WARNING:
                    has_warning = True

        if has_critical:
            return HealthStatus.CRITICAL
        elif has_warning:
            return HealthStatus.WARNING
        else:
            return HealthStatus.HEALTHY

    def check_all_zones(
        self,
        zones_data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Check health of all zones.

        Args:
            zones_data: Dictionary mapping zone names to their data:
                {
                    "zone_name": {
                        "cycle_time_min": float or None,
                        "power_w_m2": float or None,
                        "sensor_available": bool
                    }
                }

        Returns:
            Dictionary with health status and issues:
                {
                    "status": HealthStatus,
                    "zone_issues": Dict[str, List[HealthIssue]],
                    "summary": str
                }
        """
        zone_issues = {}

        for zone_name, data in zones_data.items():
            monitor = HealthMonitor(zone_name, self.exception_zones)
            issues = monitor.check_all(
                cycle_time_min=data.get("cycle_time_min"),
                power_w_m2=data.get("power_w_m2"),
                sensor_available=data.get("sensor_available", True)
            )
            zone_issues[zone_name] = issues

        overall_status = self.aggregate_health(zone_issues)

        # Generate summary
        total_issues = sum(len(issues) for issues in zone_issues.values())
        critical_count = sum(
            1 for issues in zone_issues.values()
            for issue in issues
            if issue.severity == HealthStatus.CRITICAL
        )
        warning_count = sum(
            1 for issues in zone_issues.values()
            for issue in issues
            if issue.severity == HealthStatus.WARNING
        )

        if overall_status == HealthStatus.HEALTHY:
            summary = "All zones healthy"
        elif overall_status == HealthStatus.WARNING:
            summary = f"{warning_count} warning(s) detected"
        else:
            summary = f"{critical_count} critical issue(s), {warning_count} warning(s)"

        return {
            "status": overall_status,
            "zone_issues": zone_issues,
            "summary": summary,
            "total_issues": total_issues,
            "critical_count": critical_count,
            "warning_count": warning_count
        }
