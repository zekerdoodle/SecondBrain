"""
Working Memory subsystem for Second Brain.

Working memory provides ephemeral, TTL-based notes that:
- Persist across exchanges but auto-expire
- Can be pinned to prevent expiration
- Support deadlines with countdown display
- Are injected into every prompt for context

Usage:
    from working_memory import get_store

    store = get_store()
    store.add_item("Remember to check X", tag="reminder", ttl=5)
    store.advance_exchange()  # Call after each exchange
"""

from .models import WorkingMemoryItem
from .manager import (
    WorkingMemoryStore,
    WorkingMemoryConfig,
    WorkingMemoryError,
    WorkingMemoryIndexError,
    WorkingMemoryDuplicateError,
    get_store,
    reset_store,
)
from .formatter import (
    format_working_memory_section,
    format_time_until,
    parse_duration,
    is_due_soon,
)

__all__ = [
    # Models
    "WorkingMemoryItem",
    # Manager
    "WorkingMemoryStore",
    "WorkingMemoryConfig",
    "WorkingMemoryError",
    "WorkingMemoryIndexError",
    "WorkingMemoryDuplicateError",
    "get_store",
    "reset_store",
    # Formatter
    "format_working_memory_section",
    "format_time_until",
    "parse_duration",
    "is_due_soon",
]
