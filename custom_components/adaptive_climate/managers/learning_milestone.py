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

        if prev is None:
            self._last_status = new_status
            return False

        if new_status == prev:
            return False

        self._last_status = new_status

        if new_status == "idle" or prev == "idle":
            return False

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
