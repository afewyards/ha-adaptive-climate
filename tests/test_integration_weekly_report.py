"""Integration tests for weekly report end-to-end flow."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.adaptive_climate.analytics.reports import WeeklyReport
from custom_components.adaptive_climate.analytics.history_store import (
    HistoryStore, WeeklySnapshot, ZoneSnapshot,
)
from custom_components.adaptive_climate.managers.notification_manager import (
    NotificationManager,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)
    hass.services.async_call = AsyncMock()
    return hass


def test_weekly_report_end_to_end():
    """Full report generation with multiple zones produces valid markdown."""
    start = datetime(2024, 1, 27)
    end = datetime(2024, 2, 2)

    report = WeeklyReport(start, end)
    report.add_zone_data(
        "living_room", duty_cycle=45.0, comfort_score=85.0,
        learning_status="tuned", confidence=63, confidence_prev=55,
        recovery_cycles=2,
    )
    report.add_zone_data(
        "kitchen", duty_cycle=30.0, comfort_score=72.0,
        learning_status="collecting", confidence=28,
    )
    report.add_zone_data(
        "office", duty_cycle=50.0, comfort_score=58.0,
        learning_status="stable", confidence=45, comfort_score_prev=81.0,
        contact_pauses=5,
    )
    report.health_status = "healthy"

    # Verify markdown report
    md = report.format_markdown_report()
    assert "## Weekly Heating Report" in md
    assert "### Learning Progress" in md
    assert "### Needs Attention" in md
    assert "**Office**" in md
    assert "58%" in md
    assert "5 contact" in md

    # Verify iOS summary
    ios = report.format_ios_summary()
    assert "healthy" in ios.lower()
    assert len(ios.split("\n")) <= 3

    # Verify to_dict roundtrip
    d = report.to_dict()
    assert d["zones"]["living_room"]["confidence"] == 63
    assert d["zones"]["kitchen"]["learning_status"] == "collecting"


def test_weekly_report_with_history():
    """WoW confidence delta calculated from history snapshots."""
    prev_snap = ZoneSnapshot(
        zone_id="living_room", duty_cycle=40.0, comfort_score=80.0,
        time_at_target=75.0, area_m2=20.0, confidence=0.55,
        learning_status="stable",
    )
    curr_snap = ZoneSnapshot(
        zone_id="living_room", duty_cycle=42.0, comfort_score=85.0,
        time_at_target=78.0, area_m2=20.0, confidence=0.63,
        learning_status="tuned",
    )

    # Compute confidence delta
    delta = None
    if curr_snap.confidence is not None and prev_snap.confidence is not None:
        delta = round((curr_snap.confidence - prev_snap.confidence) * 100)
    assert delta == 8

    # Build report using this delta
    report = WeeklyReport(datetime(2024, 2, 3), datetime(2024, 2, 9))
    report.add_zone_data(
        "living_room", duty_cycle=42.0, comfort_score=85.0,
        learning_status="tuned", confidence=63, confidence_prev=55,
    )

    md = report.format_markdown_report()
    assert "+8%" in md


@pytest.mark.asyncio
async def test_notification_manager_sends_report(mock_hass):
    """NotificationManager sends both iOS and persistent for report."""
    mgr = NotificationManager(
        hass=mock_hass,
        notify_service="mobile_app_phone",
        persistent_notification=True,
    )

    report = WeeklyReport(datetime(2024, 1, 27), datetime(2024, 2, 2))
    report.add_zone_data(
        "living_room", duty_cycle=45.0, learning_status="tuned", confidence=63,
    )

    result = await mgr.async_send(
        notification_id="adaptive_climate_weekly",
        title="Weekly Heating Report",
        ios_message=report.format_ios_summary(),
        persistent_message=report.format_markdown_report(),
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 2


def test_weekly_report_zone_sorting():
    """Zones are sorted by learning status (best first)."""
    report = WeeklyReport(datetime(2024, 1, 27), datetime(2024, 2, 2))
    report.add_zone_data("zone_a", duty_cycle=10.0, learning_status="collecting")
    report.add_zone_data("zone_b", duty_cycle=15.0, learning_status="optimized")
    report.add_zone_data("zone_c", duty_cycle=12.0, learning_status="tuned")
    report.add_zone_data("zone_d", duty_cycle=8.0, learning_status="stable")

    md = report.format_markdown_report()
    lines = md.split("\n")

    # Find learning progress section
    progress_idx = next(i for i, line in enumerate(lines) if "Learning Progress" in line)

    # Check order: optimized, tuned, stable, collecting
    zone_lines = [line for line in lines[progress_idx + 1:] if line.startswith("**")]
    assert "Zone B" in zone_lines[0]  # optimized
    assert "Zone C" in zone_lines[1]  # tuned
    assert "Zone D" in zone_lines[2]  # stable
    assert "Zone A" in zone_lines[3]  # collecting


def test_weekly_report_problem_detection():
    """Problem zones are correctly identified and reported."""
    report = WeeklyReport(datetime(2024, 1, 27), datetime(2024, 2, 2))

    # Zone with comfort drop
    report.add_zone_data(
        "bedroom", duty_cycle=20.0, comfort_score=60.0,
        comfort_score_prev=78.0, learning_status="stable",
    )

    # Zone with contact pauses
    report.add_zone_data(
        "kitchen", duty_cycle=25.0, comfort_score=75.0,
        contact_pauses=5, learning_status="collecting",
    )

    # Zone with no problems
    report.add_zone_data(
        "living_room", duty_cycle=30.0, comfort_score=85.0,
        learning_status="tuned",
    )

    md = report.format_markdown_report()

    # Should have needs attention section
    assert "### Needs Attention" in md

    # Should list problem zones
    assert "**Bedroom**" in md
    assert "60%" in md
    assert "**Kitchen**" in md
    assert "5 contact" in md

    # Living room should not be in problem section
    lines = md.split("\n")
    needs_idx = next(i for i, line in enumerate(lines) if "Needs Attention" in line)
    problem_section = "\n".join(lines[needs_idx:])
    learning_idx = next(i for i, line in enumerate(lines) if "Learning Progress" in line)
    learning_section = "\n".join(lines[learning_idx:needs_idx])

    assert "**Living Room**" in learning_section
    assert "**Living Room**" not in problem_section


def test_weekly_report_no_problems():
    """Report without problems doesn't show needs attention section."""
    report = WeeklyReport(datetime(2024, 1, 27), datetime(2024, 2, 2))
    report.add_zone_data(
        "living_room", duty_cycle=30.0, comfort_score=85.0,
        learning_status="tuned", confidence=65,
    )
    report.add_zone_data(
        "kitchen", duty_cycle=25.0, comfort_score=80.0,
        learning_status="stable", confidence=42,
    )

    md = report.format_markdown_report()

    assert "### Needs Attention" not in md
    assert "### Learning Progress" in md


def test_weekly_report_ios_summary_highlights():
    """iOS summary shows tier changes and comfort drops."""
    report = WeeklyReport(datetime(2024, 1, 27), datetime(2024, 2, 2))

    # Zone with tier change
    report.add_zone_data(
        "living_room", duty_cycle=30.0, comfort_score=85.0,
        learning_status="tuned", learning_status_prev="stable",
        confidence=65,
    )

    # Zone with comfort drop
    report.add_zone_data(
        "bedroom", duty_cycle=20.0, comfort_score=58.0,
        learning_status="stable",
    )

    report.health_status = "healthy"

    ios = report.format_ios_summary()

    # Should mention tier change
    assert "Living Room" in ios
    assert "tuned" in ios

    # Should mention comfort drop
    assert "Bedroom" in ios
    assert "58%" in ios

    # Should include system health
    assert "healthy" in ios


def test_zone_confidence_delta():
    """ZoneReportData correctly calculates confidence delta."""
    from custom_components.adaptive_climate.analytics.reports import ZoneReportData

    # With previous confidence
    zone = ZoneReportData(
        zone_id="test", duty_cycle=30.0,
        confidence=65, confidence_prev=58,
    )
    assert zone.confidence_delta == 7

    # Negative delta
    zone2 = ZoneReportData(
        zone_id="test", duty_cycle=30.0,
        confidence=50, confidence_prev=60,
    )
    assert zone2.confidence_delta == -10

    # Missing previous
    zone3 = ZoneReportData(
        zone_id="test", duty_cycle=30.0,
        confidence=65, confidence_prev=None,
    )
    assert zone3.confidence_delta is None

    # Missing current
    zone4 = ZoneReportData(
        zone_id="test", duty_cycle=30.0,
        confidence=None, confidence_prev=58,
    )
    assert zone4.confidence_delta is None
