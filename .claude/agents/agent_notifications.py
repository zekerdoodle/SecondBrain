"""
Notification Queue for Ping Mode.

Manages pending notifications from completed agents that need to be
injected into the next Claude <3 prompt.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from models import PendingNotification

logger = logging.getLogger("agents.notifications")

# Singleton instance
_notification_queue: Optional["NotificationQueue"] = None


class NotificationQueue:
    """
    Queue for managing ping mode notifications.

    Notifications are stored in pending.json and injected into
    Claude <3's system prompt when they're pending.

    Storage: .claude/agents/notifications/pending.json
    """

    def __init__(self, storage_dir: Path):
        """
        Initialize the notification queue.

        Args:
            storage_dir: Directory for notification storage
        """
        self.storage_dir = Path(storage_dir)
        self.storage_file = self.storage_dir / "pending.json"
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Ensure storage directory and file exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_file.exists():
            self._save([])

    def _load(self) -> List[PendingNotification]:
        """Load notifications from storage."""
        try:
            if not self.storage_file.exists():
                return []
            with open(self.storage_file, "r") as f:
                data = json.load(f)
            return [PendingNotification.from_dict(n) for n in data.get("notifications", [])]
        except Exception as e:
            logger.error(f"Failed to load notifications: {e}")
            return []

    def _save(self, notifications: List[PendingNotification]) -> None:
        """Save notifications to storage."""
        try:
            data = {
                "notifications": [n.to_dict() for n in notifications],
                "last_updated": datetime.utcnow().isoformat()
            }
            with open(self.storage_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save notifications: {e}")

    def add(
        self,
        agent: str,
        agent_response: str,
        source_chat_id: str,
        invoked_at: datetime,
        completed_at: Optional[datetime] = None
    ) -> PendingNotification:
        """
        Add a notification to the queue.

        Args:
            agent: Name of the agent that completed
            agent_response: The agent's final response
            source_chat_id: Chat to inject notification into
            invoked_at: When the agent was invoked
            completed_at: When the agent completed (default: now)

        Returns:
            The created notification
        """
        notification = PendingNotification(
            id=str(uuid.uuid4()),
            agent=agent,
            invoked_at=invoked_at,
            completed_at=completed_at or datetime.utcnow(),
            source_chat_id=source_chat_id,
            agent_response=agent_response,
            status="pending"
        )

        notifications = self._load()
        notifications.append(notification)
        self._save(notifications)

        logger.info(f"Added notification for agent '{agent}' (chat: {source_chat_id})")
        return notification

    def get_pending(self, chat_id: Optional[str] = None) -> List[PendingNotification]:
        """
        Get pending notifications, optionally filtered by chat ID.

        Args:
            chat_id: If provided, only return notifications for this chat

        Returns:
            List of pending notifications
        """
        notifications = self._load()
        pending = [n for n in notifications if n.status == "pending"]

        if chat_id:
            pending = [n for n in pending if n.source_chat_id == chat_id]

        return pending

    def get_stale(self, threshold_minutes: int = 15) -> List[PendingNotification]:
        """
        Get stale notifications (pending and older than threshold).

        Args:
            threshold_minutes: How old before considered stale

        Returns:
            List of stale notifications
        """
        notifications = self._load()
        return [n for n in notifications if n.is_stale(threshold_minutes)]

    def mark_injected(self, notification_ids: List[str]) -> int:
        """
        Mark notifications as injected.

        Args:
            notification_ids: IDs of notifications to mark

        Returns:
            Number of notifications marked
        """
        if not notification_ids:
            return 0

        notifications = self._load()
        marked = 0

        for n in notifications:
            if n.id in notification_ids and n.status == "pending":
                n.status = "injected"
                marked += 1

        self._save(notifications)
        logger.info(f"Marked {marked} notifications as injected")
        return marked

    def mark_expired(self, notification_ids: List[str]) -> int:
        """
        Mark notifications as expired.

        Args:
            notification_ids: IDs of notifications to mark

        Returns:
            Number of notifications marked
        """
        if not notification_ids:
            return 0

        notifications = self._load()
        marked = 0

        for n in notifications:
            if n.id in notification_ids and n.status == "pending":
                n.status = "expired"
                marked += 1

        self._save(notifications)
        logger.info(f"Marked {marked} notifications as expired")
        return marked

    def cleanup(self, max_age_hours: int = 24) -> int:
        """
        Remove old injected/expired notifications.

        Args:
            max_age_hours: Remove notifications older than this

        Returns:
            Number of notifications removed
        """
        notifications = self._load()
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

        original_count = len(notifications)
        notifications = [
            n for n in notifications
            if n.status == "pending" or n.completed_at > cutoff
        ]

        self._save(notifications)
        removed = original_count - len(notifications)

        if removed:
            logger.info(f"Cleaned up {removed} old notifications")

        return removed

    def format_for_injection(self, notifications: List[PendingNotification]) -> str:
        """
        Format notifications for injection into system prompt.

        Args:
            notifications: Notifications to format

        Returns:
            Formatted XML block for injection
        """
        if not notifications:
            return ""

        parts = ["""<agent-completions>
IMPORTANT: The following agent(s) have completed their tasks since your last turn.
You MUST acknowledge these completions to the user in your response.
- Summarize the agent's response in a natural, conversational way
- If the agent reported errors, explain what went wrong
- If the agent's response requires follow-up action, suggest next steps
"""]

        for n in notifications:
            parts.append(f"""
<agent-notification id="{n.id}">
<agent>{n.agent}</agent>
<invoked-at>{n.invoked_at.strftime('%Y-%m-%d %H:%M:%S')}</invoked-at>
<completed-at>{n.completed_at.strftime('%Y-%m-%d %H:%M:%S')}</completed-at>
<response>
{n.agent_response}
</response>
</agent-notification>
""")

        parts.append("</agent-completions>")
        return "\n".join(parts)


def get_notification_queue(storage_dir: Optional[Path] = None) -> NotificationQueue:
    """
    Get the singleton notification queue.

    Args:
        storage_dir: Override storage directory (only used on first call)

    Returns:
        NotificationQueue instance
    """
    global _notification_queue

    if _notification_queue is None:
        if storage_dir is None:
            storage_dir = Path(__file__).parent / "notifications"
        _notification_queue = NotificationQueue(storage_dir)

    return _notification_queue


def reset_notification_queue() -> None:
    """Reset the notification queue singleton."""
    global _notification_queue
    _notification_queue = None
