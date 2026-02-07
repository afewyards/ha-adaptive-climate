"""Tests for weekly performance reports."""

from datetime import datetime
import pytest

from custom_components.adaptive_climate.analytics.reports import (
    WeeklyReport,
    ZoneReportData,
)


def test_markdown_report_all_zones():
    """Verify markdown structure with learning + problem sections."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data(
        "living_room",
        duty_cycle=45.0,
        comfort_score=85.0,
        learning_status="tuned",
        confidence=63,
        confidence_prev=55,
        recovery_cycles=2,
    )
    report.add_zone_data(
        "kitchen",
        duty_cycle=30.0,
        comfort_score=72.0,
        learning_status="collecting",
        confidence=28,
    )
    report.add_zone_data(
        "office",
        duty_cycle=50.0,
        comfort_score=62.0,
        learning_status="stable",
        confidence=45,
        comfort_score_prev=81.0,
        contact_pauses=3,
    )

    md = report.format_markdown_report()
    assert "## Weekly Heating Report" in md
    assert "Jan 27" in md
    assert "### Learning Progress" in md
    assert "**Living Room**" in md
    assert "`tuned`" in md
    assert "(63%)" in md
    assert "+8%" in md
    assert "### Needs Attention" in md
    assert "**Office**" in md
    assert "62%" in md
    assert "3 contact" in md


def test_markdown_report_no_problems():
    """Needs Attention section omitted when no issues."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data(
        "living_room",
        duty_cycle=45.0,
        comfort_score=85.0,
        learning_status="tuned",
        confidence=63,
    )

    md = report.format_markdown_report()
    assert "### Learning Progress" in md
    assert "### Needs Attention" not in md


def test_markdown_report_confidence_delta():
    """WoW confidence change shown correctly."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data(
        "living_room",
        duty_cycle=45.0,
        comfort_score=85.0,
        learning_status="stable",
        confidence=45,
        confidence_prev=37,
    )

    md = report.format_markdown_report()
    assert "+8%" in md


def test_ios_summary_with_highlights():
    """Notable events appear in iOS summary."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data(
        "living_room",
        duty_cycle=45.0,
        comfort_score=85.0,
        learning_status="tuned",
        confidence=63,
        learning_status_prev="stable",
    )
    report.add_zone_data(
        "office",
        duty_cycle=50.0,
        comfort_score=62.0,
        learning_status="collecting",
        confidence=20,
        comfort_score_prev=81.0,
    )
    report.health_status = "healthy"

    summary = report.format_ios_summary()
    assert "tuned" in summary.lower() or "Living Room" in summary
    assert "Office" in summary
    assert "healthy" in summary.lower()


def test_ios_summary_no_highlights():
    """Fallback message when nothing notable."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data(
        "living_room",
        duty_cycle=45.0,
        comfort_score=85.0,
        learning_status="stable",
        confidence=45,
    )
    report.health_status = "healthy"

    summary = report.format_ios_summary()
    assert "progressing" in summary.lower() or "normal" in summary.lower()


def test_zone_sorting_by_status():
    """Zones sorted: optimized > tuned > stable > collecting > idle."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data("z_collecting", duty_cycle=30.0, learning_status="collecting", confidence=20)
    report.add_zone_data("z_optimized", duty_cycle=30.0, learning_status="optimized", confidence=96)
    report.add_zone_data("z_stable", duty_cycle=30.0, learning_status="stable", confidence=45)
    report.add_zone_data("z_tuned", duty_cycle=30.0, learning_status="tuned", confidence=70)

    md = report.format_markdown_report()
    pos_opt = md.index("Z Optimized")
    pos_tuned = md.index("Z Tuned")
    pos_stable = md.index("Z Stable")
    pos_coll = md.index("Z Collecting")
    assert pos_opt < pos_tuned < pos_stable < pos_coll


def test_zone_report_data_properties():
    """Test ZoneReportData computed properties."""
    z = ZoneReportData(
        zone_id="living_room",
        duty_cycle=45.0,
        comfort_score=60.0,
        comfort_score_prev=80.0,
        learning_status="tuned",
        learning_status_prev="stable",
        confidence=63,
        confidence_prev=55,
    )
    assert z.display_name == "Living Room"
    assert z.confidence_delta == 8
    assert z.has_comfort_problem is True  # 80-60=20 >= 15
    assert z.has_tier_change is True


def test_zone_no_problems():
    """Zone with good metrics has no problems."""
    z = ZoneReportData(
        zone_id="bedroom",
        duty_cycle=30.0,
        comfort_score=85.0,
        humidity_pauses=1,
        contact_pauses=0,
    )
    assert z.has_comfort_problem is False
    assert z.has_pause_problems is False
    assert z.has_problems is False


def test_report_to_dict():
    """Test to_dict serialization."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data(
        "living_room",
        duty_cycle=45.0,
        comfort_score=85.0,
        learning_status="tuned",
        confidence=63,
    )

    d = report.to_dict()
    assert d["start_date"] == "2024-01-27T00:00:00"
    assert d["zones"]["living_room"]["confidence"] == 63
    assert d["zones"]["living_room"]["learning_status"] == "tuned"
    assert d["health_status"] == "healthy"


def test_active_zones_count():
    """Zones with >5% duty cycle counted as active."""
    report = WeeklyReport(datetime(2024, 1, 27), datetime(2024, 2, 2))
    report.add_zone_data("active", duty_cycle=45.0)
    report.add_zone_data("inactive", duty_cycle=3.0)
    report.add_zone_data("borderline", duty_cycle=5.0)  # not > 5, so inactive
    assert report.active_zones == 1


def test_humidity_pause_problems():
    """Zone with >=3 humidity pauses flagged."""
    z = ZoneReportData(zone_id="bathroom", duty_cycle=30.0, humidity_pauses=5)
    assert z.has_pause_problems is True
    assert z.has_problems is True


def test_problem_line_format():
    """Problem line includes comfort and pause details."""
    report = WeeklyReport(datetime(2024, 1, 27), datetime(2024, 2, 2))
    report.add_zone_data(
        "bathroom",
        duty_cycle=30.0,
        comfort_score=50.0,
        humidity_pauses=5,
        contact_pauses=4,
    )
    md = report.format_markdown_report()
    assert "5 humidity" in md
    assert "4 contact" in md
    assert "50%" in md
