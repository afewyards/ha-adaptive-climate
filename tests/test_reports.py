"""Tests for weekly performance reports."""
from datetime import datetime
import pytest

from custom_components.adaptive_thermostat.analytics.reports import (
    WeeklyReport,
    generate_weekly_report,
)


def test_report_content():
    """Test that weekly report includes all required data."""
    start_date = datetime(2024, 1, 1, 0, 0, 0)
    end_date = datetime(2024, 1, 7, 23, 59, 59)

    report = WeeklyReport(start_date, end_date)

    # Add zone data with full information
    report.add_zone_data("living_room", duty_cycle=45.5, energy_kwh=123.4, cost=12.34)
    report.add_zone_data("bedroom", duty_cycle=30.2, energy_kwh=89.7, cost=8.97)
    report.add_zone_data("kitchen", duty_cycle=55.8, energy_kwh=145.2, cost=14.52)

    # Set system totals
    report.set_totals(total_energy_kwh=358.3, total_cost=35.83)

    # Verify data is stored correctly
    assert report.start_date == start_date
    assert report.end_date == end_date
    assert len(report.zones) == 3

    # Check living room data
    assert report.zones["living_room"]["duty_cycle"] == 45.5
    assert report.zones["living_room"]["energy_kwh"] == 123.4
    assert report.zones["living_room"]["cost"] == 12.34

    # Check bedroom data
    assert report.zones["bedroom"]["duty_cycle"] == 30.2
    assert report.zones["bedroom"]["energy_kwh"] == 89.7
    assert report.zones["bedroom"]["cost"] == 8.97

    # Check kitchen data
    assert report.zones["kitchen"]["duty_cycle"] == 55.8
    assert report.zones["kitchen"]["energy_kwh"] == 145.2
    assert report.zones["kitchen"]["cost"] == 14.52

    # Check totals
    assert report.total_energy_kwh == 358.3
    assert report.total_cost == 35.83

    # Verify formatted report contains key information
    formatted = report.format_report(currency_symbol="€")
    assert "Weekly Heating Performance Report" in formatted
    assert "2024-01-01" in formatted
    assert "2024-01-07" in formatted
    assert "Total Energy: 358.3 kWh" in formatted
    assert "Total Cost: €35.83" in formatted
    assert "living_room" in formatted
    assert "45.5%" in formatted
    assert "123.4 kWh" in formatted
    assert "€12.34" in formatted


def test_report_without_cost_data():
    """Test that report gracefully handles missing cost data."""
    start_date = datetime(2024, 1, 1, 0, 0, 0)
    end_date = datetime(2024, 1, 7, 23, 59, 59)

    report = WeeklyReport(start_date, end_date)

    # Add zone data without cost information
    report.add_zone_data("living_room", duty_cycle=45.5, energy_kwh=123.4, cost=None)
    report.add_zone_data("bedroom", duty_cycle=30.2, energy_kwh=None, cost=None)

    # Set system totals without cost
    report.set_totals(total_energy_kwh=123.4, total_cost=None)

    # Verify data is stored correctly with None values
    assert report.zones["living_room"]["duty_cycle"] == 45.5
    assert report.zones["living_room"]["energy_kwh"] == 123.4
    assert report.zones["living_room"]["cost"] is None

    assert report.zones["bedroom"]["duty_cycle"] == 30.2
    assert report.zones["bedroom"]["energy_kwh"] is None
    assert report.zones["bedroom"]["cost"] is None

    assert report.total_energy_kwh == 123.4
    assert report.total_cost is None

    # Verify formatted report handles missing data gracefully
    formatted = report.format_report(currency_symbol="€")
    assert "Weekly Heating Performance Report" in formatted
    assert "Total Energy: 123.4 kWh" in formatted
    assert "Total Cost: N/A (no cost data)" in formatted
    assert "living_room" in formatted
    assert "45.5%" in formatted
    assert "bedroom" in formatted
    assert "30.2%" in formatted

    # Ensure missing cost info doesn't break formatting
    assert "€12.34" not in formatted  # No cost should appear


def test_report_without_energy_meter():
    """Test that report works when no energy meter is available."""
    start_date = datetime(2024, 1, 1, 0, 0, 0)
    end_date = datetime(2024, 1, 7, 23, 59, 59)

    report = WeeklyReport(start_date, end_date)

    # Add zone data with only duty cycle (no energy or cost)
    report.add_zone_data("living_room", duty_cycle=45.5)
    report.add_zone_data("bedroom", duty_cycle=30.2)

    # No system totals
    report.set_totals(total_energy_kwh=None, total_cost=None)

    # Verify data is stored correctly
    assert report.zones["living_room"]["duty_cycle"] == 45.5
    assert report.zones["living_room"]["energy_kwh"] is None
    assert report.zones["living_room"]["cost"] is None

    assert report.total_energy_kwh is None
    assert report.total_cost is None

    # Verify formatted report handles missing data
    formatted = report.format_report(currency_symbol="€")
    assert "Weekly Heating Performance Report" in formatted
    assert "Total Energy: N/A (no meter data)" in formatted
    assert "Total Cost: N/A (no cost data)" in formatted
    assert "living_room" in formatted
    assert "45.5%" in formatted


def test_generate_weekly_report_function():
    """Test the generate_weekly_report convenience function."""
    start_date = datetime(2024, 1, 1, 0, 0, 0)
    end_date = datetime(2024, 1, 7, 23, 59, 59)

    zones_data = {
        "living_room": {
            "duty_cycle": 45.5,
            "energy_kwh": 123.4,
            "cost": 12.34,
        },
        "bedroom": {
            "duty_cycle": 30.2,
            "energy_kwh": 89.7,
            "cost": 8.97,
        },
    }

    report = generate_weekly_report(
        zones_data=zones_data,
        start_date=start_date,
        end_date=end_date,
        total_energy_kwh=213.1,
        total_cost=21.31,
    )

    assert report.start_date == start_date
    assert report.end_date == end_date
    assert len(report.zones) == 2
    assert report.zones["living_room"]["duty_cycle"] == 45.5
    assert report.zones["bedroom"]["duty_cycle"] == 30.2
    assert report.total_energy_kwh == 213.1
    assert report.total_cost == 21.31


def test_report_to_dict():
    """Test converting report to dictionary for storage."""
    start_date = datetime(2024, 1, 1, 0, 0, 0)
    end_date = datetime(2024, 1, 7, 23, 59, 59)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data("living_room", duty_cycle=45.5, energy_kwh=123.4, cost=12.34)
    report.set_totals(total_energy_kwh=123.4, total_cost=12.34)

    report_dict = report.to_dict()

    assert report_dict["start_date"] == "2024-01-01T00:00:00"
    assert report_dict["end_date"] == "2024-01-07T23:59:59"
    assert report_dict["total_energy_kwh"] == 123.4
    assert report_dict["total_cost"] == 12.34
    assert report_dict["zones"]["living_room"]["duty_cycle"] == 45.5
    assert report_dict["zones"]["living_room"]["energy_kwh"] == 123.4
    assert report_dict["zones"]["living_room"]["cost"] == 12.34


def test_report_custom_currency():
    """Test report formatting with custom currency symbol."""
    start_date = datetime(2024, 1, 1, 0, 0, 0)
    end_date = datetime(2024, 1, 7, 23, 59, 59)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data("living_room", duty_cycle=45.5, energy_kwh=123.4, cost=12.34)
    report.set_totals(total_energy_kwh=123.4, total_cost=12.34)

    # Test with USD
    formatted_usd = report.format_report(currency_symbol="$")
    assert "$12.34" in formatted_usd
    assert "€" not in formatted_usd

    # Test with GBP
    formatted_gbp = report.format_report(currency_symbol="£")
    assert "£12.34" in formatted_gbp
    assert "€" not in formatted_gbp


def test_generate_report_with_missing_zone_keys():
    """Test that generate_weekly_report handles missing dictionary keys gracefully."""
    start_date = datetime(2024, 1, 1, 0, 0, 0)
    end_date = datetime(2024, 1, 7, 23, 59, 59)

    # Zone data with missing keys
    zones_data = {
        "living_room": {
            "duty_cycle": 45.5,
            # Missing energy_kwh and cost
        },
        "bedroom": {
            # Missing duty_cycle, defaults to 0.0
            "energy_kwh": 89.7,
        },
    }

    report = generate_weekly_report(
        zones_data=zones_data,
        start_date=start_date,
        end_date=end_date,
    )

    # Living room should have duty cycle but no energy/cost
    assert report.zones["living_room"]["duty_cycle"] == 45.5
    assert report.zones["living_room"]["energy_kwh"] is None
    assert report.zones["living_room"]["cost"] is None

    # Bedroom should default to 0.0 duty cycle
    assert report.zones["bedroom"]["duty_cycle"] == 0.0
    assert report.zones["bedroom"]["energy_kwh"] == 89.7
    assert report.zones["bedroom"]["cost"] is None
