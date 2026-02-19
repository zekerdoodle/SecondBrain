"""
Notification Queue for Ping Mode.

Manages pending notifications from completed agents that need to be
injected into the next Claude <3 prompt.

Uses file locking (fcntl.flock) for safe concurrent access.
"""

import fcntl
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

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
        self.lock_file = self.storage_dir / "pending.lock"
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Ensure storage directory and file exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_file.exists():
            self._save_unlocked([])

    def _load_unlocked(self) -> List[PendingNotification]:
        """Load notifications from storage (caller must hold lock or accept stale reads)."""
        try:
            if not self.storage_file.exists():
                return []
            with open(self.storage_file, "r") as f:
                data = json.load(f)
            return [PendingNotification.from_dict(n) for n in data.get("notifications", [])]
        except Exception as e:
            logger.error(f"Failed to load notifications: {e}")
            return []

    def _save_unlocked(self, notifications: List[PendingNotification]) -> None:
        """Save notifications to storage. Raises on failure."""
        data = {
            "notifications": [n.to_dict() for n in notifications],
            "last_updated": datetime.utcnow().isoformat()
        }
        # Write to temp file then rename for atomicity
        tmp_file = self.storage_file.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=2)
        tmp_file.rename(self.storage_file)

    def _locked_update(self, fn: Callable[[List[PendingNotification]], List[PendingNotification]]) -> None:
        """
        Execute fn(notifications) -> notifications under an exclusive file lock.

        fn receives current notifications and must return the updated list.
        """
        with open(self.lock_file, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                notifications = self._load_unlocked()
                notifications = fn(notifications)
                self._save_unlocked(notifications)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

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

        def _do_add(notifications):
            notifications.append(notification)
            return notifications

        self._locked_update(_do_add)
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
        notifications = self._load_unlocked()
        pending = [n for n in notifications if n.status == "pending"]

        if chat_id:
            pending = [n for n in pending if n.source_chat_id == chat_id]

        return pending

    def get_stale(self, threshold_minutes: int = 5, threshold_seconds: Optional[int] = None) -> List[PendingNotification]:
        """
        Get stale notifications (pending and older than threshold).

        Args:
            threshold_minutes: How old before considered stale (used if threshold_seconds is None)
            threshold_seconds: Seconds threshold (takes precedence over threshold_minutes)

        Returns:
            List of stale notifications
        """
        notifications = self._load_unlocked()
        return [n for n in notifications if n.is_stale(
            threshold_minutes=threshold_minutes,
            threshold_seconds=threshold_seconds
        )]

    def claim_pending(self, chat_id: Optional[str] = None, threshold_seconds: Optional[int] = None) -> List[PendingNotification]:
        """
        Atomically claim pending notifications by transitioning them from "pending" to "injected".

        This is the safe way to grab notifications — it reads AND marks under a single
        file lock, preventing two paths (inline injection vs wake-up loop) from both
        grabbing the same notification.

        Args:
            chat_id: If provided, only claim notifications for this chat
            threshold_seconds: If provided, only claim notifications older than this (for stale/wake-up path)

        Returns:
            List of claimed notifications (status already set to "injected")
        """
        claimed: List[PendingNotification] = []

        def _do_claim(notifications):
            for n in notifications:
                if n.status != "pending":
                    continue
                if chat_id and n.source_chat_id != chat_id:
                    continue
                if threshold_seconds is not None and not n.is_stale(threshold_seconds=threshold_seconds):
                    continue
                n.status = "injected"
                claimed.append(n)
            return notifications

        self._locked_update(_do_claim)
        if claimed:
            logger.info(f"Claimed {len(claimed)} notifications (chat_id={chat_id}, threshold_seconds={threshold_seconds})")
        return claimed

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

        id_set = set(notification_ids)
        marked = 0

        def _do_mark(notifications):
            nonlocal marked
            for n in notifications:
                if n.id in id_set and n.status == "pending":
                    n.status = "injected"
                    marked += 1
            return notifications

        self._locked_update(_do_mark)
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

        id_set = set(notification_ids)
        marked = 0

        def _do_mark(notifications):
            nonlocal marked
            for n in notifications:
                if n.id in id_set and n.status == "pending":
                    n.status = "expired"
                    marked += 1
            return notifications

        self._locked_update(_do_mark)
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
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        removed = 0

        def _do_cleanup(notifications):
            nonlocal removed
            original_count = len(notifications)
            kept = [
                n for n in notifications
                if n.status == "pending" or n.completed_at > cutoff
            ]
            removed = original_count - len(kept)
            return kept

        self._locked_update(_do_cleanup)

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
