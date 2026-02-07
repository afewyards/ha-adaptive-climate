# Reporting & Notifications Facelift

## Goal

Replace the plain-text weekly report with a markdown-formatted, operationally useful report focused on learning progress and problem zones. Add event-driven notifications for learning milestones and comfort degradation.

## Changes

### 1. Weekly Report Overhaul (`analytics/reports.py`)

Rewrite `WeeklyReport` class. Drop cost/energy totals and chart attachment.

**New persistent notification format (markdown):**

```markdown
## Weekly Heating Report
Jan 27 – Feb 2

### Learning Progress
**Living Room** — `tuned` (63%) · +8% this week · 2 recovery cycles
**Kitchen** — `collecting` (28%) · needs ~4 more recovery cycles
**Bathroom** — `stable` (45%) · no recovery cycles (humidity pauses)

### Needs Attention
**Office** — comfort dropped to 62% (was 81%) · 3 contact sensor pauses
**Bathroom** — 5 humidity pauses this week
```

Rules:
- Learning Progress: all zones, sorted by status (optimized → tuned → stable → collecting → idle)
- Needs Attention: only zones with issues, omitted entirely if none
- Each zone = one line: name, status, confidence%, WoW delta, short qualifier
- Qualifier sources: recovery cycle count, humidity pause count, contact pause count, confidence change

**New iOS summary format:**
```
Living Room reached "tuned" · Office comfort dropped to 62%
4 zones active · System healthy
```

Highlight reel of notable events. Falls back to "All zones progressing normally" when nothing notable.

**Removed from report:**
- Cost/energy totals and WoW cost comparison
- "Best zone" callout
- Chart PNG attachment
- Plain text `format_report()` — replaced by `format_markdown_report()`

### 2. History Store Extension (`analytics/history_store.py`)

Add to `ZoneSnapshot`:
- `confidence: float | None` — convergence confidence %
- `learning_status: str | None` — idle/collecting/stable/tuned/optimized
- `recovery_cycles: int | None` — recovery cycles collected this week
- `comfort_score_avg: float | None` — average comfort over the week (rename from `comfort_score` for clarity)
- `humidity_pauses: int | None` — count of humidity pauses
- `contact_pauses: int | None` — count of contact sensor pauses

Bump `STORAGE_VERSION` to 2. Migration: existing snapshots get `None` for new fields.

Weekly report uses previous snapshot confidence to calculate `+8% this week` delta.

### 3. Delete Charts Module

Delete `analytics/charts.py` entirely. Remove all references in `services/scheduled.py`.

### 4. Notification Manager (`managers/notification_manager.py`)

New centralized module for all notification dispatch.

```python
class NotificationManager:
    """Centralized notification dispatch with cooldowns."""

    def __init__(self, hass, notify_service, persistent_notification):
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

        Returns False if suppressed by cooldown.
        """

    def _check_cooldown(self, notification_id: str, cooldown_hours: float) -> bool:
        """Return True if notification is allowed (not in cooldown)."""
```

Used by: weekly report, learning milestone, comfort degradation.

### 5. Event-Driven: Learning Milestones

**Trigger:** confidence crosses tier threshold (collecting→stable, stable→tuned, tuned→optimized, or downgrade on rollback).

**Source:** `adaptive/learning.py` — `AdaptiveLearner` already tracks learning status. Add callback/event when status changes.

**Implementation:** Add `on_status_change` callback to `AdaptiveLearner`. Thermostat registers callback during init, forwards to `NotificationManager`.

**Notification:**
- iOS: `Living Room is now "tuned" (63% confidence)`
- Persistent: `Living Room reached "tuned" status (63% confidence). Night setback fully enabled. Next milestone: "optimized" at 95%.`
- Notification ID: `learning_milestone_{zone_id}` — no cooldown (tier changes are rare)

**Context strings by tier:**
| Tier | Context |
|------|---------|
| stable | Basic convergence reached. Partial night setback enabled. |
| tuned | System well-tuned. Night setback fully enabled. Auto-apply eligible. |
| optimized | High confidence. System operating at best performance. |
| collecting (downgrade) | Confidence decreased after rollback. Resuming data collection. |

### 6. Event-Driven: Comfort Degradation

**Trigger:** zone comfort score drops below 65% OR drops >15 points from its rolling average.

**Source:** `sensors/comfort.py` — `ComfortScoreSensor` updates every 5 min. Add degradation detection to update cycle.

**Implementation:**
- Track rolling average (last 24h of 5-min samples = 288 samples)
- On each update, check: `score < 65` or `rolling_avg - score > 15`
- If triggered, collect context from zone state attributes (contact pauses, humidity pauses, override history)
- Forward to `NotificationManager` with 24h cooldown per zone

**Notification:**
- iOS: `Office comfort dropped to 62% (avg was 81%)`
- Persistent: `Office comfort score dropped to 62% (24h average: 81%). Possible causes: 3 contact sensor pauses detected.`
- Notification ID: `comfort_degradation_{zone_id}` — 24h cooldown

**Context detection (best-effort):**
- Check `status.overrides` for recent contact/humidity/open_window events
- Check if settling MAE increased (from debug attrs if available)
- Fallback: just report the numbers without cause attribution

### 7. Scheduled Service Updates (`services/scheduled.py`)

`_run_weekly_report_core` changes:
- Collect learning data per zone: confidence, status, recovery cycle count from coordinator
- Collect event counts per zone: humidity pauses, contact pauses (need counter in coordinator or climate entity)
- Drop chart generation code and Pillow imports
- Pass all data to new `WeeklyReport` methods
- Use `NotificationManager` for dispatch

### 8. Pause/Event Counters

Need weekly counters for humidity pauses and contact pauses per zone.

**Implementation:** Add simple counters to climate entity, reset on weekly report generation.
- `_humidity_pause_count: int` — incremented in humidity detector callback
- `_contact_pause_count: int` — incremented in contact sensor callback
- Exposed via coordinator `get_zone_data()` for report collection
- Reset after snapshot is taken

### 9. Service & Config Updates

**`services.yaml`:**
- Remove `cost_report` service
- Update `weekly_report` description

**`const.py`:**
- `COMFORT_DEGRADATION_THRESHOLD = 65` — absolute floor
- `COMFORT_DROP_THRESHOLD = 15` — drop from rolling avg
- `COMFORT_ALERT_COOLDOWN_HOURS = 24`

### 10. CLAUDE.md Updates

Update state attributes section to document new notification behavior. Add NotificationManager to managers table.

## Tests

### Unit Tests

**`test_reports.py`** (new or extend existing):
- `test_markdown_report_all_zones` — verify markdown structure with learning + problem sections
- `test_markdown_report_no_problems` — Needs Attention section omitted
- `test_markdown_report_confidence_delta` — WoW confidence change shown correctly
- `test_ios_summary_with_highlights` — notable events in summary
- `test_ios_summary_no_highlights` — fallback message
- `test_zone_sorting_by_status` — optimized first, idle last

**`test_notification_manager.py`** (new):
- `test_send_ios_and_persistent` — both dispatched
- `test_send_ios_only` — persistent_notification=False
- `test_cooldown_suppresses` — second call within cooldown returns False
- `test_cooldown_expires` — call after cooldown succeeds
- `test_different_ids_independent` — cooldowns are per notification_id

**`test_history_store.py`** (extend):
- `test_v2_snapshot_fields` — new fields serialize/deserialize
- `test_v1_to_v2_migration` — old snapshots load with None for new fields
- `test_confidence_delta_calculation` — current vs previous confidence

**`test_comfort_degradation.py`** (new):
- `test_triggers_below_absolute_threshold` — score < 65 fires
- `test_triggers_on_large_drop` — >15 point drop fires
- `test_no_trigger_normal_fluctuation` — small drops ignored
- `test_cooldown_prevents_spam` — 24h cooldown respected
- `test_context_includes_contact_pauses` — cause attribution works
- `test_context_includes_humidity_pauses`

**`test_learning_milestone.py`** (new):
- `test_notification_on_tier_upgrade` — collecting→stable fires
- `test_notification_on_tier_downgrade` — rollback fires
- `test_no_notification_same_tier` — confidence change within tier silent
- `test_context_string_per_tier` — correct context for each tier

### Integration Tests

**`test_integration_weekly_report.py`** (new):
- `test_weekly_report_end_to_end` — set up coordinator with multiple zones, mock sensor states (duty, comfort, learning status), trigger report, verify markdown output + iOS summary + history snapshot saved
- `test_weekly_report_with_history` — save two snapshots, verify WoW confidence delta appears in report
- `test_weekly_report_pause_counters_reset` — verify humidity/contact counters reset after report generation

**`test_integration_event_notifications.py`** (new):
- `test_learning_milestone_fires_notification` — set up zone, push confidence past tier threshold, verify notification dispatched with correct title/body
- `test_comfort_drop_fires_notification` — set up comfort sensor, feed samples to build average, then drop score, verify notification with context
- `test_comfort_and_learning_independent` — both can fire for same zone without interfering
- `test_notification_manager_cooldown_across_events` — comfort alert cooldown doesn't affect learning milestone

## Resolved Questions

1. **Pause counters location** — climate entity. Simpler, direct access to event callbacks.
2. **Comfort rolling average** — separate buffer in the degradation detector. Don't bloat the existing 2h sensor buffer.
3. **Notification service discovery** — stored on coordinator for global access. NotificationManager initialized with coordinator reference.
