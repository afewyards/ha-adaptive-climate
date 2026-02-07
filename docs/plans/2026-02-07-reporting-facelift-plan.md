# Reporting & Notifications Facelift — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace plain-text weekly reports with markdown-formatted reports focused on learning progress and problem zones; add event-driven notifications for learning milestones and comfort degradation.

**Architecture:** NotificationManager (new) centralizes all notification dispatch with cooldown support. WeeklyReport rewritten for markdown output. ComfortDegradationDetector (new) tracks 24h rolling average and fires alerts. Learning milestone callbacks added to state_attributes computation path. Pause counters added to climate entity.

**Tech Stack:** Python 3.11+, Home Assistant core APIs (services, persistent_notification), pytest

**Design doc:** `docs/plans/2026-02-07-reporting-facelift-design.md`

---

### Task 1: NotificationManager

**Files:**
- Create: `custom_components/adaptive_climate/managers/notification_manager.py`
- Test: `tests/test_notification_manager.py`

**Step 1: Write the failing tests**

```python
# tests/test_notification_manager.py
"""Tests for NotificationManager."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

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


@pytest.fixture
def manager(mock_hass):
    return NotificationManager(
        hass=mock_hass,
        notify_service="mobile_app_phone",
        persistent_notification=True,
    )


@pytest.fixture
def manager_no_persistent(mock_hass):
    return NotificationManager(
        hass=mock_hass,
        notify_service="mobile_app_phone",
        persistent_notification=False,
    )


@pytest.mark.asyncio
async def test_send_ios_and_persistent(manager, mock_hass):
    """Both iOS and persistent notification dispatched."""
    result = await manager.async_send(
        notification_id="test_1",
        title="Test Title",
        ios_message="Short message",
        persistent_message="Detailed message",
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 2

    # Check iOS call
    ios_call = mock_hass.services.async_call.call_args_list[0]
    assert ios_call[0][0] == "notify"
    assert ios_call[0][1] == "mobile_app_phone"
    assert ios_call[0][2]["title"] == "Test Title"
    assert ios_call[0][2]["message"] == "Short message"

    # Check persistent call
    persistent_call = mock_hass.services.async_call.call_args_list[1]
    assert persistent_call[0][0] == "persistent_notification"
    assert persistent_call[0][2]["message"] == "Detailed message"


@pytest.mark.asyncio
async def test_send_ios_only(manager_no_persistent, mock_hass):
    """Only iOS notification when persistent_notification=False."""
    result = await manager_no_persistent.async_send(
        notification_id="test_1",
        title="Test Title",
        ios_message="Short message",
        persistent_message="Detailed message",
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 1
    ios_call = mock_hass.services.async_call.call_args_list[0]
    assert ios_call[0][0] == "notify"


@pytest.mark.asyncio
async def test_persistent_uses_ios_message_as_fallback(manager, mock_hass):
    """When no persistent_message given, uses ios_message for persistent too."""
    await manager.async_send(
        notification_id="test_1",
        title="Test Title",
        ios_message="Short message",
    )
    assert mock_hass.services.async_call.call_count == 2
    persistent_call = mock_hass.services.async_call.call_args_list[1]
    assert persistent_call[0][2]["message"] == "Short message"


@pytest.mark.asyncio
async def test_cooldown_suppresses(manager, mock_hass):
    """Second call within cooldown returns False."""
    await manager.async_send(
        notification_id="cd_test",
        title="T",
        ios_message="M",
        cooldown_hours=1.0,
    )
    assert mock_hass.services.async_call.call_count == 2

    # Second call — should be suppressed
    result = await manager.async_send(
        notification_id="cd_test",
        title="T",
        ios_message="M",
        cooldown_hours=1.0,
    )
    assert result is False
    assert mock_hass.services.async_call.call_count == 2  # no new calls


@pytest.mark.asyncio
async def test_cooldown_expires(manager, mock_hass):
    """Call after cooldown succeeds."""
    await manager.async_send(
        notification_id="cd_test",
        title="T",
        ios_message="M",
        cooldown_hours=1.0,
    )
    # Manually expire the cooldown
    manager._cooldowns["cd_test"] = datetime.now() - timedelta(hours=2)

    result = await manager.async_send(
        notification_id="cd_test",
        title="T",
        ios_message="M",
        cooldown_hours=1.0,
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 4  # 2 + 2


@pytest.mark.asyncio
async def test_different_ids_independent(manager, mock_hass):
    """Cooldowns are per notification_id."""
    await manager.async_send(
        notification_id="id_a",
        title="T",
        ios_message="M",
        cooldown_hours=1.0,
    )
    # Different ID should not be blocked
    result = await manager.async_send(
        notification_id="id_b",
        title="T",
        ios_message="M",
        cooldown_hours=1.0,
    )
    assert result is True
    assert mock_hass.services.async_call.call_count == 4


@pytest.mark.asyncio
async def test_no_notify_service(mock_hass):
    """Gracefully handles missing notify service."""
    manager = NotificationManager(
        hass=mock_hass,
        notify_service=None,
        persistent_notification=True,
    )
    result = await manager.async_send(
        notification_id="test",
        title="T",
        ios_message="M",
    )
    # Should still send persistent, but no iOS
    assert result is True
    assert mock_hass.services.async_call.call_count == 1  # only persistent


@pytest.mark.asyncio
async def test_zero_cooldown_never_suppresses(manager, mock_hass):
    """Zero cooldown (default) never suppresses."""
    for _ in range(3):
        result = await manager.async_send(
            notification_id="test",
            title="T",
            ios_message="M",
            cooldown_hours=0,
        )
        assert result is True
    assert mock_hass.services.async_call.call_count == 6  # 3 * 2
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notification_manager.py -v`
Expected: FAIL — module not found

**Step 3: Write implementation**

```python
# custom_components/adaptive_climate/managers/notification_manager.py
"""Centralized notification dispatch with cooldowns."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class NotificationManager:
    """Centralized notification dispatch with cooldown support.

    Sends iOS (mobile_app) and persistent notifications.
    Supports per-notification-id cooldowns to prevent spam.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        notify_service: str | None,
        persistent_notification: bool,
    ) -> None:
        self._hass = hass
        self._notify_service = notify_service
        self._persistent = persistent_notification
        self._cooldowns: dict[str, datetime] = {}

    async def async_send(
        self,
        notification_id: str,
        title: str,
        ios_message: str,
        persistent_message: str | None = None,
        cooldown_hours: float = 0,
    ) -> bool:
        """Send iOS + optional persistent notification with cooldown.

        Args:
            notification_id: Unique ID for cooldown tracking and persistent notification
            title: Notification title
            ios_message: Short message for iOS notification
            persistent_message: Longer markdown message for persistent notification (falls back to ios_message)
            cooldown_hours: Minimum hours between sends for this notification_id (0 = no cooldown)

        Returns:
            True if sent, False if suppressed by cooldown
        """
        if cooldown_hours > 0 and not self._check_cooldown(notification_id, cooldown_hours):
            _LOGGER.debug(
                "Notification %s suppressed by cooldown", notification_id
            )
            return False

        sent_any = False

        # Send iOS notification
        if self._notify_service:
            try:
                if "." in self._notify_service:
                    _, service_name = self._notify_service.split(".", 1)
                else:
                    service_name = self._notify_service

                await self._hass.services.async_call(
                    "notify",
                    service_name,
                    {"title": title, "message": ios_message},
                    blocking=True,
                )
                sent_any = True
            except Exception:
                _LOGGER.exception("Failed to send iOS notification")

        # Send persistent notification
        if self._persistent:
            try:
                await self._hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "notification_id": notification_id,
                        "title": title,
                        "message": persistent_message or ios_message,
                    },
                    blocking=True,
                )
                sent_any = True
            except Exception:
                _LOGGER.exception("Failed to send persistent notification")

        # Record cooldown
        if cooldown_hours > 0 and sent_any:
            self._cooldowns[notification_id] = datetime.now()

        return sent_any

    def _check_cooldown(
        self, notification_id: str, cooldown_hours: float
    ) -> bool:
        """Return True if notification is allowed (not in cooldown)."""
        last_sent = self._cooldowns.get(notification_id)
        if last_sent is None:
            return True
        elapsed = datetime.now() - last_sent
        return elapsed >= timedelta(hours=cooldown_hours)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notification_manager.py -v`
Expected: all PASS

**Step 5: Commit**

```
feat: add NotificationManager with cooldown support
```

---

### Task 2: History Store v2

**Files:**
- Modify: `custom_components/adaptive_climate/analytics/history_store.py`
- Test: `tests/test_history_store.py` (extend — create if doesn't exist)

**Step 1: Write the failing tests**

```python
# tests/test_history_store.py
"""Tests for history store v2 with learning fields."""
import pytest
from custom_components.adaptive_climate.analytics.history_store import (
    ZoneSnapshot,
    WeeklySnapshot,
)


def test_v2_snapshot_fields():
    """New fields serialize and deserialize correctly."""
    snap = ZoneSnapshot(
        zone_id="living_room",
        duty_cycle=42.0,
        comfort_score=85.0,
        time_at_target=78.0,
        area_m2=25.0,
        confidence=0.63,
        learning_status="tuned",
        recovery_cycles=5,
        humidity_pauses=2,
        contact_pauses=3,
    )
    d = snap.to_dict()
    assert d["confidence"] == 0.63
    assert d["learning_status"] == "tuned"
    assert d["recovery_cycles"] == 5
    assert d["humidity_pauses"] == 2
    assert d["contact_pauses"] == 3

    restored = ZoneSnapshot.from_dict(d)
    assert restored.confidence == 0.63
    assert restored.learning_status == "tuned"
    assert restored.recovery_cycles == 5
    assert restored.humidity_pauses == 2
    assert restored.contact_pauses == 3


def test_v1_to_v2_migration():
    """Old v1 snapshots load with None for new fields."""
    v1_data = {
        "zone_id": "bedroom",
        "duty_cycle": 30.0,
        "comfort_score": 75.0,
        "time_at_target": 70.0,
        "area_m2": 15.0,
        # No v2 fields
    }
    snap = ZoneSnapshot.from_dict(v1_data)
    assert snap.zone_id == "bedroom"
    assert snap.duty_cycle == 30.0
    assert snap.confidence is None
    assert snap.learning_status is None
    assert snap.recovery_cycles is None
    assert snap.humidity_pauses is None
    assert snap.contact_pauses is None


def test_confidence_delta_calculation():
    """Current vs previous confidence delta computed correctly."""
    current = ZoneSnapshot(
        zone_id="living_room", duty_cycle=40.0, comfort_score=80.0,
        time_at_target=75.0, area_m2=20.0, confidence=0.63,
        learning_status="tuned", recovery_cycles=3,
        humidity_pauses=0, contact_pauses=0,
    )
    previous = ZoneSnapshot(
        zone_id="living_room", duty_cycle=38.0, comfort_score=78.0,
        time_at_target=72.0, area_m2=20.0, confidence=0.55,
        learning_status="stable", recovery_cycles=2,
        humidity_pauses=1, contact_pauses=0,
    )
    # Delta = current - previous confidence
    delta = None
    if current.confidence is not None and previous.confidence is not None:
        delta = round((current.confidence - previous.confidence) * 100)
    assert delta == 8  # +8 percentage points
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_history_store.py -v`
Expected: FAIL — TypeError: unexpected keyword arguments

**Step 3: Modify `ZoneSnapshot`**

In `custom_components/adaptive_climate/analytics/history_store.py`:

Change `ZoneSnapshot` dataclass (lines 25-48) to add new fields with defaults:

```python
@dataclass
class ZoneSnapshot:
    """Snapshot of a single zone's weekly performance."""

    zone_id: str
    duty_cycle: float
    comfort_score: float | None
    time_at_target: float | None
    area_m2: float | None
    confidence: float | None = None
    learning_status: str | None = None
    recovery_cycles: int | None = None
    humidity_pauses: int | None = None
    contact_pauses: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ZoneSnapshot:
        """Create from dictionary."""
        return cls(
            zone_id=data["zone_id"],
            duty_cycle=data.get("duty_cycle", 0.0),
            comfort_score=data.get("comfort_score"),
            time_at_target=data.get("time_at_target"),
            area_m2=data.get("area_m2"),
            confidence=data.get("confidence"),
            learning_status=data.get("learning_status"),
            recovery_cycles=data.get("recovery_cycles"),
            humidity_pauses=data.get("humidity_pauses"),
            contact_pauses=data.get("contact_pauses"),
        )
```

Bump storage version: change `STORAGE_VERSION = 1` → `STORAGE_VERSION = 2` (line 21).

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_history_store.py -v`
Expected: all PASS

**Step 5: Commit**

```
feat: add learning/pause fields to ZoneSnapshot (v2)
```

---

### Task 3: Weekly Report Rewrite

**Files:**
- Modify: `custom_components/adaptive_climate/analytics/reports.py`
- Modify: `tests/test_reports.py`

**Step 1: Write the failing tests**

Add to `tests/test_reports.py` (replace existing tests that reference removed methods — `format_report`, `format_summary`, cost-related tests):

```python
# Add these new tests to tests/test_reports.py

def test_markdown_report_all_zones():
    """Verify markdown structure with learning + problem sections."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
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
        "office", duty_cycle=50.0, comfort_score=62.0,
        learning_status="stable", confidence=45, comfort_score_prev=81.0,
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
        "living_room", duty_cycle=45.0, comfort_score=85.0,
        learning_status="tuned", confidence=63,
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
        "living_room", duty_cycle=45.0, comfort_score=85.0,
        learning_status="stable", confidence=45, confidence_prev=37,
    )

    md = report.format_markdown_report()
    assert "+8%" in md


def test_ios_summary_with_highlights():
    """Notable events appear in iOS summary."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data(
        "living_room", duty_cycle=45.0, comfort_score=85.0,
        learning_status="tuned", confidence=63, learning_status_prev="stable",
    )
    report.add_zone_data(
        "office", duty_cycle=50.0, comfort_score=62.0,
        learning_status="collecting", confidence=20, comfort_score_prev=81.0,
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
        "living_room", duty_cycle=45.0, comfort_score=85.0,
        learning_status="stable", confidence=45,
    )
    report.health_status = "healthy"

    summary = report.format_ios_summary()
    assert "progressing" in summary.lower() or "normal" in summary.lower()


def test_zone_sorting_by_status():
    """Zones sorted: optimized → tuned → stable → collecting → idle."""
    start_date = datetime(2024, 1, 27)
    end_date = datetime(2024, 2, 2)

    report = WeeklyReport(start_date, end_date)
    report.add_zone_data("z_collecting", duty_cycle=30.0, learning_status="collecting", confidence=20)
    report.add_zone_data("z_optimized", duty_cycle=30.0, learning_status="optimized", confidence=96)
    report.add_zone_data("z_stable", duty_cycle=30.0, learning_status="stable", confidence=45)
    report.add_zone_data("z_tuned", duty_cycle=30.0, learning_status="tuned", confidence=70)

    md = report.format_markdown_report()
    # Find positions of each zone in output
    pos_opt = md.index("Z Optimized")
    pos_tuned = md.index("Z Tuned")
    pos_stable = md.index("Z Stable")
    pos_coll = md.index("Z Collecting")
    assert pos_opt < pos_tuned < pos_stable < pos_coll
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reports.py::test_markdown_report_all_zones -v`
Expected: FAIL — `add_zone_data` doesn't accept `learning_status`

**Step 3: Rewrite `WeeklyReport` in `analytics/reports.py`**

```python
"""Weekly performance reports for adaptive thermostat."""
from __future__ import annotations

from dataclasses import dataclass, field
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

        # Status and confidence
        status = z.learning_status or "idle"
        parts.append(f"— `{status}`")
        if z.confidence is not None:
            parts.append(f"({z.confidence}%)")

        # Confidence delta
        delta = z.confidence_delta
        if delta is not None and delta != 0:
            sign = "+" if delta > 0 else ""
            parts.append(f"· {sign}{delta}% this week")

        # Qualifier
        if z.recovery_cycles is not None and z.recovery_cycles > 0:
            parts.append(f"· {z.recovery_cycles} recovery cycle{'s' if z.recovery_cycles != 1 else ''}")
        elif z.humidity_pauses and z.humidity_pauses > 0:
            parts.append(f"· no recovery cycles (humidity pauses)")
        elif z.contact_pauses and z.contact_pauses > 0:
            parts.append(f"· no recovery cycles (contact pauses)")

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

        # Header
        start_str = self.start_date.strftime("%b %-d")
        end_str = self.end_date.strftime("%b %-d")
        lines.append("## Weekly Heating Report")
        lines.append(f"{start_str} – {end_str}")
        lines.append("")

        # Learning Progress
        lines.append("### Learning Progress")
        for z in self._sorted_zones():
            lines.append(self._format_learning_line(z))
        lines.append("")

        # Needs Attention (only if problems exist)
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
            lines.append(" · ".join(highlights[:3]))  # max 3 highlights
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
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reports.py -v`
Expected: New tests PASS. Old tests referencing `format_report`, `format_summary`, `set_totals`, `calculate_zone_costs`, `get_best_zone`, `get_average_comfort`, `get_average_duty_cycle`, `generate_weekly_report` will FAIL — remove or rewrite them in this step.

Remove/rewrite old tests that reference removed API. Keep `test_report_to_dict` but update for new structure.

**Step 5: Commit**

```
feat: rewrite WeeklyReport with markdown output and learning focus
```

---

### Task 4: Delete Charts Module

**Files:**
- Delete: `custom_components/adaptive_climate/analytics/charts.py`
- Modify: `custom_components/adaptive_climate/services/scheduled.py` (remove chart imports/usage)

**Step 1: Remove chart references from `services/scheduled.py`**

In `_run_weekly_report_core` (line 185): remove `from ..analytics.charts import ChartGenerator, save_chart_to_www, cleanup_old_charts`

Remove lines 299-323 (chart generation block) and `chart_url` from the return dict and notification data.

**Step 2: Delete `analytics/charts.py`**

**Step 3: Run tests to verify nothing breaks**

Run: `pytest tests/ -v --tb=short`
Expected: no test imports charts.py (it was never tested)

**Step 4: Commit**

```
chore: remove charts module (replaced by markdown reports)
```

---

### Task 5: Remove Cost Report Service

**Files:**
- Modify: `custom_components/adaptive_climate/services/__init__.py` — remove `async_handle_cost_report`, `SERVICE_COST_REPORT`, cost_report registration
- Modify: `custom_components/adaptive_climate/services.yaml` — remove `cost_report` block (lines 138-153)
- Modify: `custom_components/adaptive_climate/__init__.py` — remove `SERVICE_COST_REPORT` import, `COST_REPORT_SCHEMA`, `cost_report_schema` param

**Step 1: Remove from `services/__init__.py`**

- Delete `async_handle_cost_report` function (lines 207-330)
- Delete `SERVICE_COST_REPORT = "cost_report"` (line 38)
- Remove `_cost_report_handler` wrapper (lines 503-507)
- Remove `DOMAIN, SERVICE_COST_REPORT, _cost_report_handler` registration (lines 522-525)
- Remove `SERVICE_COST_REPORT` from `services_to_remove` list (line 554)
- Remove from `__all__` export (line 585)
- Remove `cost_report_schema` parameter from `async_register_services` signature (line 467)

**Step 2: Remove from `services.yaml`**

Delete lines 138-153 (cost_report service definition).

**Step 3: Remove from `__init__.py`**

- Remove `SERVICE_COST_REPORT` from import (line 114)
- Remove `COST_REPORT_SCHEMA` definition (line 475)
- Remove `cost_report_schema=COST_REPORT_SCHEMA` from service registration call (line 717)

**Step 4: Run tests**

Run: `pytest tests/ -v --tb=short`

**Step 5: Commit**

```
chore: remove cost_report service
```

---

### Task 6: Comfort Degradation Detector

**Files:**
- Create: `custom_components/adaptive_climate/managers/comfort_degradation.py`
- Test: `tests/test_comfort_degradation.py`

**Step 1: Write the failing tests**

```python
# tests/test_comfort_degradation.py
"""Tests for comfort degradation detection."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from custom_components.adaptive_climate.managers.comfort_degradation import (
    ComfortDegradationDetector,
)


@pytest.fixture
def mock_notification_manager():
    mgr = MagicMock()
    mgr.async_send = AsyncMock(return_value=True)
    return mgr


@pytest.fixture
def detector(mock_notification_manager):
    return ComfortDegradationDetector(
        zone_id="office",
        zone_name="Office",
        notification_manager=mock_notification_manager,
    )


def test_triggers_below_absolute_threshold(detector, mock_notification_manager):
    """Score < 65 fires degradation."""
    # Build up rolling average at 80
    for _ in range(50):
        detector.record_score(80.0)
    assert detector.check_degradation(60.0) is True


def test_triggers_on_large_drop(detector, mock_notification_manager):
    """Drop >15 points from rolling avg fires."""
    for _ in range(50):
        detector.record_score(85.0)
    # 85 - 68 = 17 > 15
    assert detector.check_degradation(68.0) is True


def test_no_trigger_normal_fluctuation(detector, mock_notification_manager):
    """Small drops are ignored."""
    for _ in range(50):
        detector.record_score(80.0)
    # 80 - 75 = 5, and 75 > 65
    assert detector.check_degradation(75.0) is False


def test_no_trigger_insufficient_data(detector):
    """No trigger when not enough samples for reliable average."""
    detector.record_score(80.0)
    assert detector.check_degradation(50.0) is False


def test_build_context_contact_pauses(detector):
    """Context includes contact pause count."""
    ctx = detector.build_context(contact_pauses=5, humidity_pauses=0)
    assert "5 contact" in ctx


def test_build_context_humidity_pauses(detector):
    """Context includes humidity pause count."""
    ctx = detector.build_context(contact_pauses=0, humidity_pauses=3)
    assert "3 humidity" in ctx


def test_build_context_no_causes(detector):
    """Context is empty string when no known causes."""
    ctx = detector.build_context(contact_pauses=0, humidity_pauses=0)
    assert ctx == ""


def test_rolling_average(detector):
    """Rolling average computed correctly."""
    for _ in range(10):
        detector.record_score(80.0)
    for _ in range(10):
        detector.record_score(90.0)
    avg = detector.rolling_average
    assert avg is not None
    assert 84.0 <= avg <= 86.0  # weighted toward middle
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_comfort_degradation.py -v`
Expected: FAIL — module not found

**Step 3: Write implementation**

```python
# custom_components/adaptive_climate/managers/comfort_degradation.py
"""Comfort degradation detection with rolling average."""
from __future__ import annotations

import logging
from collections import deque

_LOGGER = logging.getLogger(__name__)

# Constants
COMFORT_DEGRADATION_THRESHOLD = 65  # Absolute floor
COMFORT_DROP_THRESHOLD = 15  # Points drop from rolling avg
MIN_SAMPLES_FOR_DETECTION = 12  # ~1 hour of 5-min samples


class ComfortDegradationDetector:
    """Detects significant comfort score degradation.

    Tracks a 24h rolling average (288 samples at 5-min intervals).
    Fires when score < absolute threshold OR drops >15 from average.
    """

    def __init__(
        self,
        zone_id: str,
        zone_name: str,
        notification_manager: object | None = None,
        max_samples: int = 288,
    ) -> None:
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._notification_manager = notification_manager
        self._samples: deque[float] = deque(maxlen=max_samples)

    def record_score(self, score: float) -> None:
        """Record a comfort score sample."""
        self._samples.append(score)

    @property
    def rolling_average(self) -> float | None:
        """Get rolling average, or None if insufficient data."""
        if len(self._samples) < MIN_SAMPLES_FOR_DETECTION:
            return None
        return sum(self._samples) / len(self._samples)

    def check_degradation(self, current_score: float) -> bool:
        """Check if current score indicates degradation.

        Returns True if degradation detected.
        """
        avg = self.rolling_average
        if avg is None:
            return False

        if current_score < COMFORT_DEGRADATION_THRESHOLD:
            return True

        if avg - current_score >= COMFORT_DROP_THRESHOLD:
            return True

        return False

    def build_context(
        self,
        contact_pauses: int = 0,
        humidity_pauses: int = 0,
    ) -> str:
        """Build cause attribution string.

        Returns empty string if no known causes.
        """
        causes = []
        if contact_pauses > 0:
            causes.append(
                f"{contact_pauses} contact sensor pause{'s' if contact_pauses != 1 else ''}"
            )
        if humidity_pauses > 0:
            causes.append(
                f"{humidity_pauses} humidity pause{'s' if humidity_pauses != 1 else ''}"
            )
        return " · ".join(causes)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_comfort_degradation.py -v`
Expected: all PASS

**Step 5: Commit**

```
feat: add ComfortDegradationDetector with rolling average
```

---

### Task 7: Learning Milestone Notifications

**Files:**
- Create: `custom_components/adaptive_climate/managers/learning_milestone.py`
- Test: `tests/test_learning_milestone.py`

**Step 1: Write the failing tests**

```python
# tests/test_learning_milestone.py
"""Tests for learning milestone notifications."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.adaptive_climate.managers.learning_milestone import (
    LearningMilestoneTracker,
    TIER_CONTEXT,
)


@pytest.fixture
def mock_notification_manager():
    mgr = MagicMock()
    mgr.async_send = AsyncMock(return_value=True)
    return mgr


@pytest.fixture
def tracker(mock_notification_manager):
    return LearningMilestoneTracker(
        zone_id="living_room",
        zone_name="Living Room",
        notification_manager=mock_notification_manager,
    )


@pytest.mark.asyncio
async def test_notification_on_tier_upgrade(tracker, mock_notification_manager):
    """Collecting → stable fires notification."""
    result = await tracker.async_check_milestone(
        new_status="stable", confidence=40
    )
    assert result is True
    mock_notification_manager.async_send.assert_called_once()
    call_kwargs = mock_notification_manager.async_send.call_args[1]
    assert "stable" in call_kwargs["ios_message"].lower()
    assert "Living Room" in call_kwargs["ios_message"]
    assert "40%" in call_kwargs["ios_message"]


@pytest.mark.asyncio
async def test_notification_on_tier_downgrade(tracker, mock_notification_manager):
    """Rollback from tuned → collecting fires notification."""
    # First go to tuned
    await tracker.async_check_milestone(new_status="tuned", confidence=70)
    mock_notification_manager.async_send.reset_mock()

    # Then downgrade
    result = await tracker.async_check_milestone(
        new_status="collecting", confidence=25
    )
    assert result is True
    call_kwargs = mock_notification_manager.async_send.call_args[1]
    assert "collecting" in call_kwargs["ios_message"].lower()


@pytest.mark.asyncio
async def test_no_notification_same_tier(tracker, mock_notification_manager):
    """Confidence change within same tier is silent."""
    await tracker.async_check_milestone(new_status="stable", confidence=40)
    mock_notification_manager.async_send.reset_mock()

    # Same tier, different confidence
    result = await tracker.async_check_milestone(new_status="stable", confidence=45)
    assert result is False
    mock_notification_manager.async_send.assert_not_called()


def test_context_string_per_tier():
    """Correct context string for each tier."""
    assert "convergence" in TIER_CONTEXT["stable"].lower()
    assert "night setback" in TIER_CONTEXT["tuned"].lower() or "auto-apply" in TIER_CONTEXT["tuned"].lower()
    assert "best performance" in TIER_CONTEXT["optimized"].lower() or "high confidence" in TIER_CONTEXT["optimized"].lower()
    assert "collecting" in TIER_CONTEXT  # downgrade context exists


@pytest.mark.asyncio
async def test_initial_status_none(tracker, mock_notification_manager):
    """First check with idle status doesn't fire."""
    result = await tracker.async_check_milestone(new_status="idle", confidence=0)
    assert result is False
    mock_notification_manager.async_send.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_learning_milestone.py -v`
Expected: FAIL — module not found

**Step 3: Write implementation**

```python
# custom_components/adaptive_climate/managers/learning_milestone.py
"""Learning milestone notification tracker."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .notification_manager import NotificationManager

_LOGGER = logging.getLogger(__name__)

TIER_CONTEXT = {
    "stable": "Basic convergence reached. Partial night setback enabled.",
    "tuned": "System well-tuned. Night setback fully enabled. Auto-apply eligible.",
    "optimized": "High confidence. System operating at best performance.",
    "collecting": "Confidence decreased after rollback. Resuming data collection.",
}

# Tiers in ascending order for comparison
_TIER_RANK = {
    "idle": 0,
    "collecting": 1,
    "stable": 2,
    "tuned": 3,
    "optimized": 4,
}


class LearningMilestoneTracker:
    """Tracks learning status and fires notifications on tier changes."""

    def __init__(
        self,
        zone_id: str,
        zone_name: str,
        notification_manager: NotificationManager | None = None,
    ) -> None:
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._notification_manager = notification_manager
        self._last_status: str | None = None

    async def async_check_milestone(
        self, new_status: str, confidence: int
    ) -> bool:
        """Check for tier change and send notification if needed.

        Args:
            new_status: Current learning status
            confidence: Current confidence percentage (0-100)

        Returns:
            True if notification was sent
        """
        prev = self._last_status

        # First call — just record, don't notify
        if prev is None:
            self._last_status = new_status
            return False

        # No change
        if new_status == prev:
            return False

        self._last_status = new_status

        # Don't notify for idle transitions (pause/unpause)
        if new_status == "idle" or prev == "idle":
            return False

        # Determine direction
        new_rank = _TIER_RANK.get(new_status, 0)
        prev_rank = _TIER_RANK.get(prev, 0)

        if new_rank > prev_rank:
            verb = "reached"
        else:
            verb = "dropped to"

        context = TIER_CONTEXT.get(new_status, "")

        ios_message = (
            f'{self._zone_name} {verb} "{new_status}" ({confidence}% confidence)'
        )
        persistent_message = f"{ios_message}. {context}"

        if self._notification_manager:
            return await self._notification_manager.async_send(
                notification_id=f"learning_milestone_{self._zone_id}",
                title="Learning Milestone",
                ios_message=ios_message,
                persistent_message=persistent_message,
            )

        return False
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_learning_milestone.py -v`
Expected: all PASS

**Step 5: Commit**

```
feat: add LearningMilestoneTracker for tier change notifications
```

---

### Task 8: Constants and Config Updates

**Files:**
- Modify: `custom_components/adaptive_climate/const.py`
- Modify: `custom_components/adaptive_climate/services.yaml`

**Step 1: Add constants to `const.py`**

After the existing confidence tier constants (~line 550), add:

```python
# Comfort degradation notification thresholds
COMFORT_DEGRADATION_THRESHOLD = 65  # Absolute floor for alert
COMFORT_DROP_THRESHOLD = 15  # Points drop from rolling avg for alert
COMFORT_ALERT_COOLDOWN_HOURS = 24  # Hours between alerts per zone
```

**Step 2: Update `services.yaml` weekly_report description**

Change line 136 from:
```yaml
  description: Generate and send a weekly performance report via the configured notification service. Includes duty cycles, energy usage, and zone statistics.
```
to:
```yaml
  description: Generate and send a weekly performance report via the configured notification service. Shows learning progress per zone and highlights problem zones.
```

**Step 3: Run tests**

Run: `pytest tests/ -v --tb=short`
Expected: all PASS

**Step 4: Commit**

```
chore: add comfort degradation constants, update service descriptions
```

---

### Task 9: Pause Counters on Climate Entity

**Files:**
- Modify: `custom_components/adaptive_climate/climate.py` — add `_humidity_pause_count`, `_contact_pause_count` counters
- Test: verify via existing climate tests or add targeted test

**Step 1: Identify where to add counters**

In `climate.py`, find the `__init__` or `async_added_to_hass` where humidity detector and contact sensor handler are set up. Add:

```python
self._humidity_pause_count: int = 0
self._contact_pause_count: int = 0
```

**Step 2: Increment on pause events**

In the humidity detector callback (where `should_pause()` transitions to True), increment `self._humidity_pause_count += 1`.

In the contact sensor handler (where `ContactPauseEvent` is emitted), increment `self._contact_pause_count += 1`.

**Step 3: Expose via coordinator**

When registering with coordinator, include the counters in zone_data, or add a method `get_pause_counts() -> tuple[int, int]` that the report can call.

**Step 4: Add reset method**

```python
def reset_pause_counters(self) -> None:
    """Reset weekly pause counters (called after report generation)."""
    self._humidity_pause_count = 0
    self._contact_pause_count = 0
```

**Step 5: Run tests**

Run: `pytest tests/ -v --tb=short`

**Step 6: Commit**

```
feat: add humidity/contact pause counters to climate entity
```

---

### Task 10: Wire Up Weekly Report (`services/scheduled.py`)

**Files:**
- Modify: `custom_components/adaptive_climate/services/scheduled.py`

**Step 1: Rewrite `_run_weekly_report_core`**

Replace the function to:
1. Collect learning data per zone (confidence, status, recovery cycles) from coordinator
2. Collect pause counters from climate entities
3. Get previous week's snapshot for WoW confidence delta
4. Build `WeeklyReport` with new `add_zone_data` signature
5. Use `NotificationManager` (from coordinator) for dispatch
6. Save snapshot with new v2 fields
7. Reset pause counters after snapshot

Remove: chart generation, chart imports, cost/energy collection.

**Step 2: Run all tests**

Run: `pytest tests/ -v --tb=short`

**Step 3: Commit**

```
feat: wire weekly report to learning data and NotificationManager
```

---

### Task 11: Wire Up Event-Driven Notifications

**Files:**
- Modify: `custom_components/adaptive_climate/managers/state_attributes.py` — add milestone check after `_compute_learning_status`
- Modify: `custom_components/adaptive_climate/sensors/comfort.py` — add degradation check to `ComfortScoreSensor` update
- Modify: `custom_components/adaptive_climate/__init__.py` — initialize `NotificationManager` on coordinator

**Step 1: Initialize NotificationManager in `__init__.py`**

After coordinator creation (line 502-503), add:

```python
from .managers.notification_manager import NotificationManager

notification_manager = NotificationManager(
    hass=hass,
    notify_service=notify_service,
    persistent_notification=persistent_notification,
)
coordinator.notification_manager = notification_manager
hass.data[DOMAIN]["notification_manager"] = notification_manager
```

**Step 2: Add milestone tracker initialization**

In `climate.py` or `climate_init.py`, when zone is registered with coordinator, create `LearningMilestoneTracker` per zone and store on zone_data.

**Step 3: Hook milestone check into learning status computation**

In `_add_learning_object` (state_attributes.py, line 273), after `learning_status` is computed, call:

```python
milestone_tracker = zone_data.get("milestone_tracker")
if milestone_tracker:
    # Fire-and-forget — don't await in attribute builder
    hass.async_create_task(
        milestone_tracker.async_check_milestone(learning_status, round(convergence_confidence * 100))
    )
```

**Step 4: Hook degradation check into ComfortScoreSensor**

In `ComfortScoreSensor._calculate_comfort_score`, after computing score, call degradation detector if available.

**Step 5: Run all tests**

Run: `pytest tests/ -v --tb=short`

**Step 6: Commit**

```
feat: wire event-driven learning and comfort notifications
```

---

### Task 12: Integration Tests

**Files:**
- Create: `tests/test_integration_weekly_report.py`
- Create: `tests/test_integration_event_notifications.py`

**Step 1: Write integration tests for weekly report**

```python
# tests/test_integration_weekly_report.py
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
```

**Step 2: Write integration tests for event notifications**

```python
# tests/test_integration_event_notifications.py
"""Integration tests for event-driven notifications."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from custom_components.adaptive_climate.managers.notification_manager import (
    NotificationManager,
)
from custom_components.adaptive_climate.managers.learning_milestone import (
    LearningMilestoneTracker,
)
from custom_components.adaptive_climate.managers.comfort_degradation import (
    ComfortDegradationDetector,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def notification_manager(mock_hass):
    return NotificationManager(
        hass=mock_hass,
        notify_service="mobile_app_phone",
        persistent_notification=True,
    )


@pytest.mark.asyncio
async def test_learning_milestone_fires_notification(notification_manager, mock_hass):
    """Learning tier change triggers notification through full stack."""
    tracker = LearningMilestoneTracker(
        zone_id="living_room",
        zone_name="Living Room",
        notification_manager=notification_manager,
    )

    # Initialize with collecting
    await tracker.async_check_milestone("collecting", 25)
    mock_hass.services.async_call.reset_mock()

    # Upgrade to stable
    result = await tracker.async_check_milestone("stable", 40)
    assert result is True
    assert mock_hass.services.async_call.call_count == 2  # iOS + persistent

    # Verify notification content
    ios_call = mock_hass.services.async_call.call_args_list[0]
    assert "Living Room" in ios_call[0][2]["message"]
    assert "stable" in ios_call[0][2]["message"]

    persistent_call = mock_hass.services.async_call.call_args_list[1]
    assert "convergence" in persistent_call[0][2]["message"].lower()


@pytest.mark.asyncio
async def test_comfort_drop_fires_notification(notification_manager, mock_hass):
    """Comfort degradation triggers notification through full stack."""
    detector = ComfortDegradationDetector(
        zone_id="office",
        zone_name="Office",
        notification_manager=notification_manager,
    )

    # Build up 24h average at ~82
    for _ in range(50):
        detector.record_score(82.0)

    # Drop to 60
    triggered = detector.check_degradation(60.0)
    assert triggered is True

    # Verify context
    ctx = detector.build_context(contact_pauses=3, humidity_pauses=0)
    assert "3 contact" in ctx

    # Send via notification manager
    avg = detector.rolling_average
    result = await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort Alert",
        ios_message=f"Office comfort dropped to 60% (avg was {avg:.0f}%)",
        persistent_message=f"Office comfort dropped to 60% (avg was {avg:.0f}%). {ctx}",
        cooldown_hours=24,
    )
    assert result is True


@pytest.mark.asyncio
async def test_comfort_and_learning_independent(notification_manager, mock_hass):
    """Both notification types can fire for same zone without interfering."""
    tracker = LearningMilestoneTracker(
        zone_id="office", zone_name="Office",
        notification_manager=notification_manager,
    )
    detector = ComfortDegradationDetector(
        zone_id="office", zone_name="Office",
        notification_manager=notification_manager,
    )

    # Learning milestone
    await tracker.async_check_milestone("collecting", 20)
    result1 = await tracker.async_check_milestone("stable", 40)
    assert result1 is True

    # Comfort degradation (different notification_id)
    for _ in range(50):
        detector.record_score(85.0)
    triggered = detector.check_degradation(60.0)
    assert triggered is True

    result2 = await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort Alert",
        ios_message="Office comfort dropped",
        cooldown_hours=24,
    )
    assert result2 is True

    # Both sent (4 calls from milestone, 2 from comfort = 6)
    # milestone: init (0) + stable (2) = 2, comfort (2) = 4 total
    assert mock_hass.services.async_call.call_count == 4


@pytest.mark.asyncio
async def test_cooldown_across_events(notification_manager, mock_hass):
    """Comfort alert cooldown doesn't affect learning milestone."""
    # Send comfort alert with cooldown
    await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort", ios_message="M", cooldown_hours=24,
    )

    # Comfort alert again — should be blocked
    result = await notification_manager.async_send(
        notification_id="comfort_degradation_office",
        title="Comfort", ios_message="M", cooldown_hours=24,
    )
    assert result is False

    # Learning milestone — different ID, should work
    result = await notification_manager.async_send(
        notification_id="learning_milestone_office",
        title="Learning", ios_message="M",
    )
    assert result is True
```

**Step 3: Run all integration tests**

Run: `pytest tests/test_integration_weekly_report.py tests/test_integration_event_notifications.py -v`
Expected: all PASS

**Step 4: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: all PASS

**Step 5: Commit**

```
test: add integration tests for weekly report and event notifications
```

---

### Task 13: Final Verification and Cleanup

**Step 1: Full test suite with coverage**

Run: `pytest tests/ --cov=custom_components/adaptive_climate -v`

**Step 2: Verify no broken imports**

Run: `python -c "from custom_components.adaptive_climate.analytics.reports import WeeklyReport; print('OK')"`
Run: `python -c "from custom_components.adaptive_climate.managers.notification_manager import NotificationManager; print('OK')"`
Run: `python -c "from custom_components.adaptive_climate.managers.learning_milestone import LearningMilestoneTracker; print('OK')"`
Run: `python -c "from custom_components.adaptive_climate.managers.comfort_degradation import ComfortDegradationDetector; print('OK')"`

**Step 3: Verify charts.py is deleted**

Run: `test ! -f custom_components/adaptive_climate/analytics/charts.py && echo "OK"`

**Step 4: Commit any cleanup**

```
chore: final cleanup for reporting facelift
```
