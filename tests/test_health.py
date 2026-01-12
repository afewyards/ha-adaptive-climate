"""Tests for health monitoring system."""
import pytest
from custom_components.adaptive_thermostat.analytics.health import (
    HealthMonitor,
    HealthStatus,
    SystemHealthMonitor,
)


class TestHealthMonitor:
    """Tests for HealthMonitor class."""

    def test_critical_short_cycles(self):
        """Test critical short cycle detection (<10 min)."""
        monitor = HealthMonitor("living_room")

        # Test critical short cycle (5 minutes)
        issue = monitor.check_cycle_time(5.0)

        assert issue is not None
        assert issue.severity == HealthStatus.CRITICAL
        assert issue.issue_type == "short_cycle"
        assert issue.zone == "living_room"
        assert "5.0 min" in issue.message
        assert "valve wear" in issue.message.lower()

    def test_warning_short_cycles(self):
        """Test warning short cycle detection (10-15 min)."""
        monitor = HealthMonitor("bedroom")

        # Test warning short cycle (12 minutes)
        issue = monitor.check_cycle_time(12.0)

        assert issue is not None
        assert issue.severity == HealthStatus.WARNING
        assert issue.issue_type == "short_cycle"
        assert issue.zone == "bedroom"
        assert "12.0 min" in issue.message
        assert "PWM period" in issue.message

        # Test boundary: exactly 10 minutes should be warning
        issue_boundary = monitor.check_cycle_time(10.0)
        assert issue_boundary is not None
        assert issue_boundary.severity == HealthStatus.WARNING

    def test_high_power_warning(self):
        """Test high power consumption warning (>20 W/m²)."""
        monitor = HealthMonitor("study")

        # Test high power consumption (25 W/m²)
        issue = monitor.check_power_consumption(25.0)

        assert issue is not None
        assert issue.severity == HealthStatus.WARNING
        assert issue.issue_type == "high_power"
        assert issue.zone == "study"
        assert "25.0 W/m²" in issue.message
        assert "heat loss" in issue.message.lower() or "insulation" in issue.message.lower()

        # Test boundary: exactly 20 W/m² should NOT trigger warning
        issue_boundary = monitor.check_power_consumption(20.0)
        assert issue_boundary is None

    def test_exception_zones(self):
        """Test exception zones (bathroom allowed high power)."""
        # Bathroom is in exception list
        monitor = HealthMonitor("bathroom", exception_zones=["bathroom"])

        # High power should NOT trigger warning for bathroom
        issue = monitor.check_power_consumption(30.0)
        assert issue is None

        # But other zones should still trigger warnings
        monitor_other = HealthMonitor("kitchen", exception_zones=["bathroom"])
        issue_other = monitor_other.check_power_consumption(30.0)
        assert issue_other is not None
        assert issue_other.severity == HealthStatus.WARNING

    def test_sensor_unavailable(self):
        """Test sensor unavailable detection."""
        monitor = HealthMonitor("hallway")

        # Test sensor unavailable
        issue = monitor.check_sensor_availability(False)

        assert issue is not None
        assert issue.severity == HealthStatus.CRITICAL
        assert issue.issue_type == "sensor_unavailable"
        assert issue.zone == "hallway"
        assert "unavailable" in issue.message.lower()
        assert "Cannot control heating" in issue.message

        # Test sensor available - no issue
        issue_ok = monitor.check_sensor_availability(True)
        assert issue_ok is None

    def test_system_wide_aggregation(self):
        """Test system-wide health aggregation."""
        system_monitor = SystemHealthMonitor(exception_zones=["bathroom"])

        # Test all zones healthy
        zones_data_healthy = {
            "living_room": {
                "cycle_time_min": 20.0,
                "power_w_m2": 15.0,
                "sensor_available": True,
            },
            "bedroom": {
                "cycle_time_min": 25.0,
                "power_w_m2": 12.0,
                "sensor_available": True,
            },
        }

        result = system_monitor.check_all_zones(zones_data_healthy)
        assert result["status"] == HealthStatus.HEALTHY
        assert result["summary"] == "All zones healthy"
        assert result["total_issues"] == 0
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0

        # Test with warnings
        zones_data_warnings = {
            "living_room": {
                "cycle_time_min": 12.0,  # Warning: short cycle
                "power_w_m2": 15.0,
                "sensor_available": True,
            },
            "bedroom": {
                "cycle_time_min": 25.0,
                "power_w_m2": 25.0,  # Warning: high power
                "sensor_available": True,
            },
        }

        result = system_monitor.check_all_zones(zones_data_warnings)
        assert result["status"] == HealthStatus.WARNING
        assert result["warning_count"] == 2
        assert result["critical_count"] == 0
        assert "2 warning(s)" in result["summary"]

        # Test with critical issues
        zones_data_critical = {
            "living_room": {
                "cycle_time_min": 5.0,  # Critical: very short cycle
                "power_w_m2": 15.0,
                "sensor_available": True,
            },
            "bedroom": {
                "cycle_time_min": 25.0,
                "power_w_m2": 12.0,
                "sensor_available": False,  # Critical: sensor unavailable
            },
        }

        result = system_monitor.check_all_zones(zones_data_critical)
        assert result["status"] == HealthStatus.CRITICAL
        assert result["critical_count"] == 2
        assert "2 critical issue(s)" in result["summary"]

    def test_healthy_cycles_no_issues(self):
        """Test that healthy cycle times don't trigger warnings."""
        monitor = HealthMonitor("living_room")

        # Test healthy cycle times (15+ minutes)
        assert monitor.check_cycle_time(15.0) is None
        assert monitor.check_cycle_time(20.0) is None
        assert monitor.check_cycle_time(30.0) is None

    def test_healthy_power_no_issues(self):
        """Test that healthy power consumption doesn't trigger warnings."""
        monitor = HealthMonitor("bedroom")

        # Test healthy power consumption (<20 W/m²)
        assert monitor.check_power_consumption(10.0) is None
        assert monitor.check_power_consumption(15.0) is None
        assert monitor.check_power_consumption(19.9) is None

    def test_none_values_no_issues(self):
        """Test that None values don't trigger warnings."""
        monitor = HealthMonitor("study")

        # None values should not trigger issues (insufficient data)
        assert monitor.check_cycle_time(None) is None
        assert monitor.check_power_consumption(None) is None

    def test_check_all_combined(self):
        """Test check_all method with multiple issues."""
        monitor = HealthMonitor("kitchen")

        # Test with multiple issues
        issues = monitor.check_all(
            cycle_time_min=8.0,  # Critical: short cycle
            power_w_m2=25.0,  # Warning: high power
            sensor_available=True,
        )

        assert len(issues) == 2
        assert any(i.severity == HealthStatus.CRITICAL for i in issues)
        assert any(i.severity == HealthStatus.WARNING for i in issues)

        # Test sensor unavailable takes priority (most critical)
        issues_sensor = monitor.check_all(
            cycle_time_min=8.0,
            power_w_m2=25.0,
            sensor_available=False,
        )

        assert len(issues_sensor) == 3
        assert issues_sensor[0].issue_type == "sensor_unavailable"

    def test_multiple_exception_zones(self):
        """Test multiple exception zones."""
        monitor_bath = HealthMonitor("bathroom", exception_zones=["bathroom", "kitchen"])
        monitor_kitchen = HealthMonitor("kitchen", exception_zones=["bathroom", "kitchen"])
        monitor_bedroom = HealthMonitor("bedroom", exception_zones=["bathroom", "kitchen"])

        # Both bathroom and kitchen should be exempt
        assert monitor_bath.check_power_consumption(30.0) is None
        assert monitor_kitchen.check_power_consumption(30.0) is None

        # Bedroom should still trigger warning
        assert monitor_bedroom.check_power_consumption(30.0) is not None
