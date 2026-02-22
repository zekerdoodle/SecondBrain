"""Memory tools - journal, working memory, and contextual memory."""

# Import to trigger registration
from . import journal
from . import working
from . import contextual
from . import chat_search

# Re-export for direct access
from .journal import (
    memory_append,
    memory_read,
)
from .working import (
    working_memory_add,
    working_memory_update,
    working_memory_remove,
    working_memory_list,
    working_memory_snapshot,
)
from .contextual import (
    memory_save,
    memory_search,
    memory_search_agent,
)
from .chat_search import (
    search_conversation_history,
)

__all__ = [
    # Journal
    "memory_append",
    "memory_read",
    # Working Memory
    "working_memory_add",
    "working_memory_update",
    "working_memory_remove",
    "working_memory_list",
    "working_memory_snapshot",
    # Contextual Memory
    "memory_save",
    "memory_search",
    "memory_search_agent",
    # Chat History Search
    "search_conversation_history",
]
