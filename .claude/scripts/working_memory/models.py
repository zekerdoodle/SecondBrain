"""Data structures for the Working Memory subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any


@dataclass(slots=True)
class WorkingMemoryItem:
    """
    A working memory entry.

    Working memory items are ephemeral notes that persist across exchanges
    but auto-expire based on TTL (time-to-live in exchanges).

    Features:
    - TTL-based expiration (countdown per exchange)
    - Pinning (items that never auto-expire)
    - Tags for categorization
    - Deadlines with countdown display
    """

    item_id: str
    content: str
    tag: Optional[str]
    ttl_remaining: int
    ttl_initial: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Pinning: pinned items don't auto-expire
    pinned: bool = False
    pin_rank: int = 1  # 1-3, higher = more important

    # Deadline support
    deadline_at: Optional[datetime] = None  # ISO 8601 timestamp
    remind_before: Optional[str] = None  # Duration like "2h", "24h"
    deadline_type: str = "soft"  # "soft" or "hard"

    # Time-based expiration (independent from TTL exchange-count).
    # When set, the item is purged once `datetime.now() > expires_at`,
    # regardless of TTL. Pinned items still trump this.
    expires_at: Optional[datetime] = None

    def touch(self) -> None:
        """Refresh the updated_at timestamp."""
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "item_id": self.item_id,
            "content": self.content,
            "tag": self.tag,
            "ttl_remaining": self.ttl_remaining,
            "ttl_initial": self.ttl_initial,
            "created_at": _serialize_dt(self.created_at),
            "updated_at": _serialize_dt(self.updated_at),
            "pinned": self.pinned,
            "pin_rank": self.pin_rank,
            "deadline_at": _serialize_dt(self.deadline_at),
            "remind_before": self.remind_before,
            "deadline_type": self.deadline_type,
            "expires_at": _serialize_dt(self.expires_at),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkingMemoryItem":
        """Rehydrate from a dict."""
        if not isinstance(data, dict):
            raise ValueError("WorkingMemoryItem data must be a dict.")

        item_id = data.get("item_id")
        content = data.get("content")
        ttl_remaining = data.get("ttl_remaining")
        ttl_initial = data.get("ttl_initial")

        if not item_id or not isinstance(content, str):
            raise ValueError("WorkingMemoryItem requires item_id and content.")
        if ttl_remaining is None or ttl_initial is None:
            raise ValueError("WorkingMemoryItem requires ttl_remaining and ttl_initial.")

        deadline_type = data.get("deadline_type", "soft") or "soft"
        if deadline_type not in ("soft", "hard"):
            deadline_type = "soft"

        pin_rank = int(data.get("pin_rank", 1))
        pin_rank = max(1, min(3, pin_rank))

        return cls(
            item_id=str(item_id),
            content=content,
            tag=data.get("tag"),
            ttl_remaining=int(ttl_remaining),
            ttl_initial=int(ttl_initial),
            created_at=_deserialize_dt(data.get("created_at")) or datetime.now(timezone.utc),
            updated_at=_deserialize_dt(data.get("updated_at")) or datetime.now(timezone.utc),
            pinned=bool(data.get("pinned", False)),
            pin_rank=pin_rank,
            deadline_at=_deserialize_dt(data.get("deadline_at")),
            remind_before=data.get("remind_before"),
            deadline_type=deadline_type,
            expires_at=_deserialize_dt(data.get("expires_at")),
        )

    @property
    def age_exchanges(self) -> int:
        """Number of exchanges since creation."""
        if self.ttl_initial <= 0:
            return 0
        consumed = self.ttl_initial - max(self.ttl_remaining, 0)
        return max(consumed, 0)

    @property
    def is_overdue(self) -> bool:
        """Check if past deadline."""
        if not self.deadline_at:
            return False
        return datetime.now(timezone.utc) > self.deadline_at

    @property
    def deadline_passed(self) -> bool:
        """Alias for is_overdue (for TTL interaction logic)."""
        return self.is_overdue

    @property
    def time_until_deadline(self) -> Optional[float]:
        """Seconds until deadline (negative if overdue)."""
        if not self.deadline_at:
            return None
        delta = self.deadline_at - datetime.now(timezone.utc)
        return delta.total_seconds()

    @property
    def is_time_expired(self) -> bool:
        """True if expires_at is set and now is past it."""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def time_until_expires(self) -> Optional[float]:
        """Seconds until time-based expiration (negative if past)."""
        if not self.expires_at:
            return None
        delta = self.expires_at - datetime.now(timezone.utc)
        return delta.total_seconds()


def _serialize_dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _deserialize_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
