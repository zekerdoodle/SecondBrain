"""Working Memory manager - simplified for Second Brain."""

from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import WorkingMemoryItem
from .formatter import format_working_memory_section

# Import atomic file ops with fallback for different run contexts
try:
    from ..atomic_file_ops import load_json, save_json
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from atomic_file_ops import load_json, save_json

logger = logging.getLogger(__name__)


class WorkingMemoryError(Exception):
    """Base exception for working memory errors."""


class WorkingMemoryIndexError(WorkingMemoryError):
    """Invalid display index."""


class WorkingMemoryDuplicateError(WorkingMemoryError):
    """Duplicate content."""


@dataclass(frozen=True, slots=True)
class WorkingMemoryConfig:
    """Configuration for working memory."""
    default_ttl: int = 5  # Default exchanges before expiry
    max_ttl: int = 10  # Maximum TTL allowed
    max_pinned_items: int = 3  # Maximum pinned items
    default_timezone: str = "America/Chicago"


class WorkingMemoryStore:
    """
    Single working memory store for the Second Brain.

    Simplified from Theo's multi-conversation model - we just have one global store
    since Second Brain conversations are managed differently.
    """

    def __init__(self, persist_path: Path, config: Optional[WorkingMemoryConfig] = None):
        self.persist_path = persist_path
        self.config = config or WorkingMemoryConfig()
        self._items: List[WorkingMemoryItem] = []
        self._version: int = 0
        self._load()

    # ---- Persistence ----

    def _load(self) -> None:
        """Load items from disk using atomic file operations."""
        if not self.persist_path.exists():
            return
        try:
            data = load_json(self.persist_path, default={})
            if isinstance(data, dict) and "items" in data:
                for item_data in data["items"]:
                    try:
                        item = WorkingMemoryItem.from_dict(item_data)
                        self._items.append(item)
                    except Exception as e:
                        logger.debug(f"Skipping invalid item: {e}")
                self._version = data.get("version", 0)
        except Exception as e:
            logger.warning(f"Could not load working memory: {e}")

    def _save(self) -> None:
        """Save items to disk using atomic file operations."""
        try:
            data = {
                "version": self._version,
                "items": [item.to_dict() for item in self._items]
            }
            if not save_json(self.persist_path, data):
                logger.error("Atomic save returned False for working memory")
        except Exception as e:
            logger.error(f"Could not save working memory: {e}")

    # ---- Internal helpers ----

    def _clamp_ttl(self, ttl: Optional[int]) -> int:
        if ttl is None:
            return self.config.default_ttl
        if ttl < 1:
            return 1
        if ttl > self.config.max_ttl:
            return self.config.max_ttl
        return ttl

    def _next_version(self) -> None:
        self._version += 1

    def _sort_items(self) -> None:
        """Sort: pinned first (by rank desc), then deadline proximity, then recency."""
        def sort_key(item: WorkingMemoryItem):
            if item.pinned:
                return (0, -item.pin_rank, -item.updated_at.timestamp())
            if item.deadline_at:
                time_until = item.time_until_deadline or float('inf')
                return (1, time_until, -item.updated_at.timestamp())
            return (2, -item.updated_at.timestamp(), 0)

        self._items.sort(key=sort_key)

    def _find_by_index(self, index: int) -> Tuple[int, WorkingMemoryItem]:
        """Find item by 1-based display index."""
        if index < 1:
            raise WorkingMemoryIndexError(f"No item numbered {index}.")
        self._sort_items()
        if not self._items:
            raise WorkingMemoryIndexError("Working memory is empty.")
        if index > len(self._items):
            raise WorkingMemoryIndexError(
                f"No item numbered {index}. Valid indices: 1-{len(self._items)}."
            )
        return index - 1, self._items[index - 1]

    def _dedupe_check(self, content: str) -> None:
        """Raise if duplicate content exists."""
        for item in self._items:
            if item.content == content:
                raise WorkingMemoryDuplicateError("Working memory already contains that note.")

    # ---- Public API ----

    @property
    def version(self) -> str:
        return str(self._version)

    def list_items(self) -> Sequence[WorkingMemoryItem]:
        """Get sorted list of all items."""
        self._sort_items()
        return tuple(self._items)

    def add_item(
        self,
        content: str,
        tag: Optional[str] = None,
        ttl: Optional[int] = None,
        pinned: bool = False,
        pin_rank: int = 1,
        deadline_at: Optional[datetime] = None,
        remind_before: Optional[str] = None,
        deadline_type: str = "soft",
    ) -> WorkingMemoryItem:
        """Add a new working memory item."""
        normalized = (content or "").strip()
        if not normalized:
            raise WorkingMemoryError("Cannot add empty content.")

        # Check pinned limit
        if pinned:
            pinned_count = sum(1 for item in self._items if item.pinned)
            if pinned_count >= self.config.max_pinned_items:
                raise WorkingMemoryError(
                    f"Maximum of {self.config.max_pinned_items} pinned items. Unpin one first."
                )

        pin_rank = max(1, min(3, pin_rank))
        ttl_clamped = self._clamp_ttl(ttl)
        self._dedupe_check(normalized)

        item = WorkingMemoryItem(
            item_id=str(uuid.uuid4()),
            content=normalized,
            tag=tag.strip() if tag and tag.strip() else None,
            ttl_remaining=ttl_clamped,
            ttl_initial=ttl_clamped,
            pinned=pinned,
            pin_rank=pin_rank,
            deadline_at=deadline_at,
            remind_before=remind_before,
            deadline_type=deadline_type if deadline_type in ("soft", "hard") else "soft",
        )

        self._items.insert(0, item)
        self._next_version()
        self._save()

        logger.info(f"WM: Added item {item.item_id[:8]} (ttl={ttl_clamped}, pinned={pinned})")
        return item

    def remove_item(self, index: int) -> WorkingMemoryItem:
        """Remove item by 1-based display index."""
        pos, item = self._find_by_index(index)
        removed = self._items.pop(pos)
        self._next_version()
        self._save()
        logger.info(f"WM: Removed item {removed.item_id[:8]}")
        return removed

    def update_item(
        self,
        index: int,
        *,
        new_content: Optional[str] = None,
        append: Optional[str] = None,
        ttl: Optional[int] = None,
        tag: Optional[str] = None,
        pinned: Optional[bool] = None,
        pin_rank: Optional[int] = None,
        deadline_at: Optional[datetime] = None,
        remind_before: Optional[str] = None,
        deadline_type: Optional[str] = None,
    ) -> WorkingMemoryItem:
        """Update an existing item."""
        _, item = self._find_by_index(index)
        changed = False

        # Update content
        if new_content is not None and new_content.strip():
            updated = new_content.strip()
            if updated != item.content:
                self._dedupe_check(updated)
                item.content = updated
                changed = True

        # Append to content
        if append and append.strip():
            suffix = append.strip()
            combined = f"{item.content} {suffix}".strip()
            if combined != item.content:
                self._dedupe_check(combined)
                item.content = combined
                changed = True

        # Update TTL
        if ttl is not None:
            ttl_clamped = self._clamp_ttl(ttl)
            if item.ttl_initial != ttl_clamped or item.ttl_remaining != ttl_clamped:
                item.ttl_initial = ttl_clamped
                item.ttl_remaining = ttl_clamped
                changed = True

        # Update tag
        if tag is not None:
            normalized_tag = tag.strip() if tag and tag.strip() else None
            if normalized_tag != item.tag:
                item.tag = normalized_tag
                changed = True

        # Update pinned status
        if pinned is not None:
            if pinned and not item.pinned:
                pinned_count = sum(1 for i in self._items if i.pinned and i.item_id != item.item_id)
                if pinned_count >= self.config.max_pinned_items:
                    raise WorkingMemoryError(
                        f"Maximum of {self.config.max_pinned_items} pinned items. Unpin one first."
                    )
            if item.pinned != pinned:
                item.pinned = pinned
                changed = True

        # Update pin rank
        if pin_rank is not None:
            clamped_rank = max(1, min(3, pin_rank))
            if item.pin_rank != clamped_rank:
                item.pin_rank = clamped_rank
                changed = True

        # Update deadline
        if deadline_at is not None:
            if item.deadline_at != deadline_at:
                item.deadline_at = deadline_at
                changed = True

        if remind_before is not None:
            if item.remind_before != remind_before:
                item.remind_before = remind_before
                changed = True

        if deadline_type is not None:
            validated_type = deadline_type if deadline_type in ("soft", "hard") else "soft"
            if item.deadline_type != validated_type:
                item.deadline_type = validated_type
                changed = True

        if changed:
            item.touch()
            self._next_version()
            self._save()
            logger.info(f"WM: Updated item {item.item_id[:8]}")

        return item

    def advance_exchange(self) -> bool:
        """
        Decrement TTL after a completed exchange and purge expired items.

        Rules:
        - Pinned items never expire
        - Items with active deadlines ignore TTL until deadline passes
        - After deadline passes, TTL countdown begins
        """
        if not self._items:
            return False

        changed = False
        to_remove: List[WorkingMemoryItem] = []

        for item in self._items:
            # Pinned items immune to expiration
            if item.pinned:
                continue

            # Items with active deadlines ignore TTL
            if item.deadline_at and not item.deadline_passed:
                continue

            # Normal TTL countdown
            if item.ttl_remaining > 0:
                item.ttl_remaining -= 1
                item.touch()
                changed = True

            if item.ttl_remaining <= 0:
                to_remove.append(item)

        for item in to_remove:
            try:
                self._items.remove(item)
                logger.info(f"WM: Expired item {item.item_id[:8]}")
            except ValueError:
                continue
            changed = True

        if changed:
            self._next_version()
            self._save()

        return changed

    def purge_all(self) -> None:
        """Remove all items."""
        if self._items:
            self._items.clear()
            self._next_version()
            self._save()
            logger.info("WM: Purged all items")

    def format_prompt_block(self) -> Optional[str]:
        """Format items for prompt injection."""
        items = self.list_items()
        if not items:
            return None
        return format_working_memory_section(items)


# ---- Per-agent store registry ----

_stores: Dict[str, WorkingMemoryStore] = {}

# Base directory for agent stores
_AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"


def get_store(agent_name: Optional[str] = None) -> WorkingMemoryStore:
    """Get the working memory store for a specific agent.

    All agents (including Character) use .claude/agents/{name}/working_memory.json.
    If no agent name is provided, defaults to 'character'.
    """
    agent_name = agent_name or "character"
    if agent_name not in _stores:
        persist_path = _AGENTS_DIR / agent_name / "working_memory.json"
        # Ensure agent directory exists
        persist_path.parent.mkdir(parents=True, exist_ok=True)
        _stores[agent_name] = WorkingMemoryStore(persist_path)
    return _stores[agent_name]


def reset_store(agent_name: Optional[str] = None) -> None:
    """Reset a specific store or all stores (for testing).

    Args:
        agent_name: Specific agent to reset, or None for character.
                    Pass sentinel "__all__" to reset all stores.
    """
    if agent_name == "__all__":
        _stores.clear()
    else:
        _stores.pop(agent_name or "character", None)
