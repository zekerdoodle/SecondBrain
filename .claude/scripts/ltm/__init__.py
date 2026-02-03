"""
Long-Term Memory System for Second Brain

Implements a Librarian/Gardener pattern for semantic memory management:
- Librarian: Extracts and organizes memories from conversations
- Gardener: Maintains and deduplicates the memory store

Components:
- embeddings: FAISS + sentence-transformers for vector search
- atomic_memory: Individual fact storage
- thread_memory: Organized collections of facts
- memory_throttle: Rate limiting for background processing
- librarian_agent: Memory extraction via Claude Haiku
- gardener_agent: Maintenance via Claude Haiku
- memory_retrieval: Thread-first retrieval strategy
"""

from .embeddings import EmbeddingManager, get_embedding_manager
from .atomic_memory import AtomicMemoryManager, get_atomic_manager, AtomicMemory
from .thread_memory import ThreadMemoryManager, get_thread_manager, Thread
from .memory_throttle import (
    add_exchange_to_buffer,
    should_run_librarian,
    consume_exchange_buffer,
    get_throttle_state,
)
from .memory_retrieval import get_memory_context, MemoryContext

__all__ = [
    "EmbeddingManager",
    "get_embedding_manager",
    "AtomicMemoryManager",
    "get_atomic_manager",
    "AtomicMemory",
    "ThreadMemoryManager",
    "get_thread_manager",
    "Thread",
    "add_exchange_to_buffer",
    "should_run_librarian",
    "consume_exchange_buffer",
    "get_throttle_state",
    "get_memory_context",
    "MemoryContext",
]
