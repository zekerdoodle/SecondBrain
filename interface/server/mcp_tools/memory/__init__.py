"""Memory tools - unified memory, working memory, and conversation search."""

# Import to trigger registration
from . import unified
from . import working
from . import chat_search

# Re-export for direct access
from .unified import (
    memory_create,
    memory_search,
    memory_update,
    memory_delete,
    memory_search_agent,
)
from .working import (
    working_memory_add,
    working_memory_update,
    working_memory_remove,
    working_memory_list,
    working_memory_snapshot,
)
from .chat_search import (
    search_conversation_history,
)

__all__ = [
    # Unified Memory
    "memory_create",
    "memory_search",
    "memory_update",
    "memory_delete",
    "memory_search_agent",
    # Working Memory
    "working_memory_add",
    "working_memory_update",
    "working_memory_remove",
    "working_memory_list",
    "working_memory_snapshot",
    # Chat History Search
    "search_conversation_history",
]
