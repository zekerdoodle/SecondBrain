"""
Thread Memory Manager

Organizes atomic memories into semantic threads (playlists).
Threads provide context by grouping related facts together.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from filelock import FileLock

logger = logging.getLogger("ltm.thread_memory")

# Paths
MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
THREADS_FILE = MEMORY_DIR / "threads.json"
LOCK_FILE = MEMORY_DIR / "threads.lock"


@dataclass
class Thread:
    """A thread organizing related atomic memories."""
    id: str
    name: str
    description: str
    memory_ids: List[str] = field(default_factory=list)
    created_at: str = ""
    last_updated: str = ""
    embedding_id: Optional[str] = None
    # v2 fields: scope and split lineage
    scope: str = ""  # Describes what content belongs in this thread
    split_from: Optional[str] = None  # Parent thread ID if created by split
    split_into: Optional[List[str]] = None  # Child thread IDs if this was split
    # v3 field: thread type
    thread_type: str = "topical"  # "topical" (default) or "conversation" (immutable snapshot)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Thread":
        # Only pass fields that the dataclass knows about (handles old data gracefully)
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def _generate_id() -> str:
    """Generate a unique thread ID."""
    import uuid
    return f"thread_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


class ThreadMemoryManager:
    """
    Manages memory threads (organized collections).

    Features:
    - Create/update/delete threads
    - Add/remove memories from threads
    - Semantic search over threads
    - Thread consolidation suggestions
    """

    def __init__(self, threads_file: Optional[Path] = None):
        self.threads_file = Path(threads_file) if threads_file else THREADS_FILE
        self.lock_file = self.threads_file.parent / "threads.lock"
        self.threads_file.parent.mkdir(parents=True, exist_ok=True)
        self.threads: List[Thread] = []
        self._load()

    def _load(self):
        """Load threads from disk."""
        if self.threads_file.exists():
            try:
                with open(self.threads_file, 'r') as f:
                    data = json.load(f)
                self.threads = [
                    Thread.from_dict(t) for t in data.get("threads", [])
                ]
                logger.info(f"Loaded {len(self.threads)} threads")
            except Exception as e:
                logger.error(f"Failed to load threads: {e}")
                self.threads = []
        else:
            self.threads = []

    def _save(self):
        """Save threads to disk (thread-safe)."""
        with FileLock(self.lock_file):
            self.threads_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.threads_file, 'w') as f:
                json.dump({
                    "threads": [t.to_dict() for t in self.threads],
                    "version": 4,
                    "last_modified": datetime.now().isoformat()
                }, f, indent=2)

    def create(
        self,
        name: str,
        description: str,
        memory_ids: Optional[List[str]] = None,
        embed: bool = True,
        scope: str = "",
        split_from: Optional[str] = None,
        thread_type: str = "topical"
    ) -> Thread:
        """
        Create a new thread.

        Args:
            name: Thread name
            description: Thread description
            memory_ids: Initial memory IDs to include
            embed: Whether to create an embedding
            scope: What kind of content belongs in this thread
            split_from: Parent thread ID if created by a split
            thread_type: Thread type - "topical" (default) or "conversation" (immutable snapshot)

        Returns:
            The created Thread
        """
        now = datetime.now().isoformat()
        thread = Thread(
            id=_generate_id(),
            name=name,
            description=description,
            memory_ids=memory_ids or [],
            created_at=now,
            last_updated=now,
            scope=scope or description,  # Default scope to description
            split_from=split_from,
            thread_type=thread_type
        )

        # Create embedding for thread
        if embed:
            try:
                try:
                    from .embeddings import get_embedding_manager, ContentType
                except ImportError:
                    from embeddings import get_embedding_manager, ContentType
                emb_mgr = get_embedding_manager()
                thread.embedding_id = emb_mgr.embed(
                    f"{name}: {description}",
                    metadata={
                        "type": "thread",
                        "thread_id": thread.id
                    },
                    content_type=ContentType.THREAD
                )
            except Exception as e:
                logger.warning(f"Failed to create thread embedding: {e}")

        self.threads.append(thread)
        self._save()
        logger.info(f"Created thread: {thread.id} ({name})")
        return thread

    def update(
        self,
        thread_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        memory_ids: Optional[List[str]] = None,
        action: str = "overwrite"  # overwrite, append, remove
    ) -> Optional[Thread]:
        """
        Update an existing thread.

        Args:
            thread_id: Thread ID
            name: New name (optional)
            description: New description (optional)
            memory_ids: Memory IDs to modify
            action: How to handle memory_ids: overwrite, append, remove

        Returns:
            Updated Thread or None if not found
        """
        thread = self.get(thread_id)
        if not thread:
            return None

        if name is not None:
            thread.name = name
        if description is not None:
            thread.description = description

        if memory_ids is not None:
            if action == "overwrite":
                thread.memory_ids = memory_ids
            elif action == "append":
                for mid in memory_ids:
                    if mid not in thread.memory_ids:
                        thread.memory_ids.append(mid)
            elif action == "remove":
                thread.memory_ids = [m for m in thread.memory_ids if m not in memory_ids]

        thread.last_updated = datetime.now().isoformat()

        # Update embedding if name/description changed
        if name is not None or description is not None:
            try:
                try:
                    from .embeddings import get_embedding_manager, ContentType
                except ImportError:
                    from embeddings import get_embedding_manager, ContentType
                emb_mgr = get_embedding_manager()

                if thread.embedding_id:
                    emb_mgr.delete_by_id(thread.embedding_id)

                thread.embedding_id = emb_mgr.embed(
                    f"{thread.name}: {thread.description}",
                    metadata={
                        "type": "thread",
                        "thread_id": thread.id
                    },
                    content_type=ContentType.THREAD
                )
            except Exception as e:
                logger.warning(f"Failed to update thread embedding: {e}")

        self._save()
        return thread

    def delete(self, thread_id: str) -> bool:
        """Delete a thread (does not delete its memories)."""
        thread = self.get(thread_id)
        if not thread:
            return False

        # Delete embedding
        if thread.embedding_id:
            try:
                try:
                    from .embeddings import get_embedding_manager
                except ImportError:
                    from embeddings import get_embedding_manager
                emb_mgr = get_embedding_manager()
                emb_mgr.delete_by_id(thread.embedding_id)
            except Exception as e:
                logger.warning(f"Failed to delete thread embedding: {e}")

        self.threads = [t for t in self.threads if t.id != thread_id]
        self._save()
        return True

    def get(self, thread_id: str) -> Optional[Thread]:
        """Get a thread by ID."""
        for t in self.threads:
            if t.id == thread_id:
                return t
        return None

    def get_by_name(self, name: str) -> Optional[Thread]:
        """Get a thread by name (case-insensitive)."""
        name_lower = name.lower()
        for t in self.threads:
            if t.name.lower() == name_lower:
                return t
        return None

    def list_all(self) -> List[Thread]:
        """List all threads."""
        return self.threads.copy()

    def search(
        self,
        query: str,
        k: int = 10
    ) -> List[Tuple[Thread, float]]:
        """
        Search threads by semantic similarity.

        Args:
            query: Search query
            k: Number of results

        Returns:
            List of (thread, score) tuples
        """
        try:
            try:
                from .embeddings import get_embedding_manager, ContentType
            except ImportError:
                from embeddings import get_embedding_manager, ContentType
            emb_mgr = get_embedding_manager()

            results = emb_mgr.retrieve(
                query,
                k=k * 2,
                threshold=0.2,
                content_type_filter=ContentType.THREAD
            )

            thread_results = []
            for meta, score in results:
                thread_id = meta.get("thread_id")
                if thread_id:
                    thread = self.get(thread_id)
                    if thread:
                        thread_results.append((thread, score))

                if len(thread_results) >= k:
                    break

            return thread_results
        except Exception as e:
            logger.error(f"Thread search failed: {e}")
            return []

    def get_conversation_thread_for_room(self, room_id: str) -> Optional[Thread]:
        """Get the conversation thread associated with a room/chat ID.

        Conversation threads store the room ID in their scope field
        as 'room:{room_id}'. Returns None if no conversation thread
        exists for this room.
        """
        scope_key = f"room:{room_id}"
        for t in self.threads:
            if t.thread_type == "conversation" and t.scope == scope_key:
                return t
        return None

    def get_threads_for_memory(self, memory_id: str) -> List[Thread]:
        """Get all threads containing a specific memory."""
        return [t for t in self.threads if memory_id in t.memory_ids]

    def remove_memory_from_all(self, memory_id: str):
        """Remove a memory ID from all threads (cleanup helper)."""
        modified = False
        for thread in self.threads:
            if memory_id in thread.memory_ids:
                thread.memory_ids.remove(memory_id)
                thread.last_updated = datetime.now().isoformat()
                modified = True

        if modified:
            self._save()

    def add_memory_to_thread(
        self,
        thread_id: str,
        memory_id: str
    ) -> bool:
        """Add a memory to a thread."""
        thread = self.get(thread_id)
        if not thread:
            return False

        if memory_id not in thread.memory_ids:
            thread.memory_ids.append(memory_id)
            thread.last_updated = datetime.now().isoformat()
            self._save()

        return True

    def find_or_create_thread(
        self,
        name: str,
        description: str
    ) -> Thread:
        """Find a thread by name or create it."""
        existing = self.get_by_name(name)
        if existing:
            return existing
        return self.create(name, description)

    def stats(self) -> Dict[str, Any]:
        """Get thread statistics."""
        memory_counts = [len(t.memory_ids) for t in self.threads]
        return {
            "total_threads": len(self.threads),
            "empty_threads": sum(1 for t in self.threads if not t.memory_ids),
            "avg_memories_per_thread": sum(memory_counts) / len(memory_counts) if memory_counts else 0,
            "max_memories_in_thread": max(memory_counts) if memory_counts else 0
        }

    def split_thread(
        self,
        source_thread_id: str,
        new_threads: List[Dict[str, Any]],
        delete_source_if_empty: bool = True
    ) -> Dict[str, Any]:
        """
        Split a thread by creating new threads and reassigning atoms.

        Atoms can belong to multiple threads, so this operation:
        1. Creates new threads with the specified atoms
        2. Removes those atoms from the source thread
        3. Optionally deletes the source thread if it becomes empty

        Args:
            source_thread_id: ID of the thread to split
            new_threads: List of dicts with 'name', 'description', 'atom_ids' keys
            delete_source_if_empty: Whether to delete source if no atoms remain

        Returns:
            Dict with 'success', 'new_thread_ids', 'source_deleted', 'errors'
        """
        result = {
            "success": False,
            "new_thread_ids": [],
            "atoms_reassigned": 0,
            "source_deleted": False,
            "errors": []
        }

        # Validate source thread exists
        source_thread = self.get(source_thread_id)
        if not source_thread:
            result["errors"].append(f"Source thread not found: {source_thread_id}")
            return result

        # Validate new_threads structure
        if not new_threads:
            result["errors"].append("No new threads specified")
            return result

        for i, nt in enumerate(new_threads):
            if not nt.get("name"):
                result["errors"].append(f"New thread {i} missing 'name'")
            if not nt.get("description"):
                result["errors"].append(f"New thread {i} missing 'description'")
            if not nt.get("atom_ids"):
                result["errors"].append(f"New thread {i} missing 'atom_ids'")

        if result["errors"]:
            return result

        # Validate all atom_ids exist in source thread
        source_atom_set = set(source_thread.memory_ids)
        all_atoms_to_move = set()
        for nt in new_threads:
            for atom_id in nt.get("atom_ids", []):
                if atom_id not in source_atom_set:
                    result["errors"].append(
                        f"Atom '{atom_id}' not in source thread '{source_thread.name}'"
                    )
                all_atoms_to_move.add(atom_id)

        if result["errors"]:
            return result

        # Validate atom IDs actually exist in the memory system
        try:
            try:
                from .atomic_memory import get_atomic_manager
            except ImportError:
                from atomic_memory import get_atomic_manager
            atom_mgr = get_atomic_manager()
            for atom_id in all_atoms_to_move:
                if atom_mgr.get(atom_id) is None:
                    result["errors"].append(f"Atom '{atom_id}' does not exist in memory store")
        except Exception as e:
            result["errors"].append(f"Failed to validate atoms: {e}")

        if result["errors"]:
            return result

        # Create new threads with split lineage
        created_threads = []
        try:
            for nt in new_threads:
                new_thread = self.create(
                    name=nt["name"],
                    description=nt.get("description", ""),
                    memory_ids=nt["atom_ids"],
                    scope=nt.get("scope", nt.get("description", "")),
                    split_from=source_thread_id
                )
                created_threads.append(new_thread)
                result["new_thread_ids"].append(new_thread.id)
                result["atoms_reassigned"] += len(nt["atom_ids"])
                logger.info(
                    f"Created thread '{new_thread.name}' with {len(nt['atom_ids'])} atoms "
                    f"(split from '{source_thread.name}')"
                )
        except Exception as e:
            # Rollback: delete any threads we created
            for t in created_threads:
                try:
                    self.delete(t.id)
                except Exception:
                    pass
            result["errors"].append(f"Failed to create new threads: {e}")
            result["new_thread_ids"] = []
            return result

        # Record split_into on source thread
        if source_thread.split_into is None:
            source_thread.split_into = []
        source_thread.split_into.extend(result["new_thread_ids"])

        # Remove atoms from source thread
        self.update(
            source_thread_id,
            memory_ids=list(all_atoms_to_move),
            action="remove"
        )

        # Check if source thread should be deleted
        source_thread = self.get(source_thread_id)  # Refresh
        if delete_source_if_empty and source_thread and not source_thread.memory_ids:
            self.delete(source_thread_id)
            result["source_deleted"] = True
            logger.info(f"Deleted empty source thread '{source_thread.name}'")

        result["success"] = True
        return result


# Singleton instance
_thread_manager: Optional[ThreadMemoryManager] = None


def get_thread_manager() -> ThreadMemoryManager:
    """Get the singleton thread manager."""
    global _thread_manager
    if _thread_manager is None:
        _thread_manager = ThreadMemoryManager()
    return _thread_manager
