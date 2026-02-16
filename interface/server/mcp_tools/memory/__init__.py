"""Memory tools - journal, working memory, and long-term memory."""

# Import to trigger registration
from . import journal
from . import working
from . import ltm
from . import search
from . import gardener_tools

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
from .ltm import (
    ltm_search,
    ltm_get_context,
    ltm_add_memory,
    ltm_create_thread,
    ltm_stats,
    ltm_process_now,
    ltm_run_gardener,
    ltm_buffer_exchange,
    ltm_backfill,
    ltm_backfill_threads,
)
from .search import memory_search

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
    # Long-Term Memory
    "ltm_search",
    "ltm_get_context",
    "ltm_add_memory",
    "ltm_create_thread",
    "ltm_stats",
    "ltm_process_now",
    "ltm_run_gardener",
    "ltm_buffer_exchange",
    "ltm_backfill",
    "ltm_backfill_threads",
    # Active Memory Search
    "memory_search",
]
