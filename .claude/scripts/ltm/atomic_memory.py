"""
Atomic Memory Manager

Stores individual facts/memories as atomic units.
Each atom has a unique ID, content, and optional metadata.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from filelock import FileLock

logger = logging.getLogger("ltm.atomic_memory")

# Paths
MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
ATOMIC_FILE = MEMORY_DIR / "atomic_memories.json"
LOCK_FILE = MEMORY_DIR / "atomic.lock"

# Fields from old schema that should be silently dropped when loading
_DEPRECATED_FIELDS = {"importance"}


@dataclass
class AtomicMemory:
    """A single atomic memory/fact."""
    id: str
    content: str
    created_at: str
    last_modified: str
    source_exchange_id: Optional[str] = None
    source_session_id: Optional[str] = None  # Chat session that generated this atom
    embedding_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    # v2 fields: versioning and confidence tracking
    previous_versions: List[Dict[str, Any]] = field(default_factory=list)
    assignment_confidence: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AtomicMemory":
        # Filter out deprecated fields (e.g. importance from old data)
        filtered = {k: v for k, v in data.items() if k not in _DEPRECATED_FIELDS}
        # Only pass fields that the dataclass knows about
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in filtered.items() if k in valid_fields}
        return cls(**filtered)


def _generate_id() -> str:
    """Generate a unique atomic memory ID."""
    import uuid
    return f"atom_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


class AtomicMemoryManager:
    """
    Manages atomic memories (individual facts).

    Features:
    - CRUD operations with version tracking
    - Integration with embedding manager
    - Thread-safe file operations
    """

    def __init__(self, memory_file: Optional[Path] = None):
        self.memory_file = Path(memory_file) if memory_file else ATOMIC_FILE
        self.lock_file = self.memory_file.parent / "atomic.lock"
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.memories: List[AtomicMemory] = []
        self._load()

    def _load(self):
        """Load memories from disk."""
        if self.memory_file.exists():
            try:
                with open(self.memory_file, 'r') as f:
                    data = json.load(f)
                self.memories = [
                    AtomicMemory.from_dict(m) for m in data.get("memories", [])
                ]
                logger.info(f"Loaded {len(self.memories)} atomic memories")
            except Exception as e:
                logger.error(f"Failed to load atomic memories: {e}")
                self.memories = []
        else:
            self.memories = []

    def _save(self):
        """Save memories to disk (thread-safe)."""
        with FileLock(self.lock_file):
            self.memory_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_file, 'w') as f:
                json.dump({
                    "memories": [m.to_dict() for m in self.memories],
                    "version": 3,
                    "last_modified": datetime.now().isoformat()
                }, f, indent=2)

    def create(
        self,
        content: str,
        source_exchange_id: Optional[str] = None,
        source_session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        embed: bool = True,
        created_at: Optional[str] = None
    ) -> AtomicMemory:
        """
        Create a new atomic memory.

        Args:
            content: The memory content
            source_exchange_id: ID of the exchange that generated this memory
            source_session_id: Chat session ID that generated this memory
            tags: Optional tags for categorization
            embed: Whether to create an embedding
            created_at: Optional ISO timestamp for when the memory was created (defaults to now)

        Returns:
            The created AtomicMemory
        """
        now = datetime.now().isoformat()
        atom = AtomicMemory(
            id=_generate_id(),
            content=content,
            created_at=created_at or now,
            last_modified=now,
            source_exchange_id=source_exchange_id,
            source_session_id=source_session_id,
            tags=tags or []
        )

        # Create embedding
        if embed:
            try:
                try:
                    from .embeddings import get_embedding_manager
                except ImportError:
                    from embeddings import get_embedding_manager
                emb_mgr = get_embedding_manager()
                atom.embedding_id = emb_mgr.embed(
                    content,
                    metadata={
                        "type": "atomic_memory",
                        "memory_id": atom.id,
                        "tags": atom.tags
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to create embedding: {e}")

        self.memories.append(atom)
        self._save()
        logger.info(f"Created atomic memory: {atom.id}")
        return atom

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_exchange_id: Optional[str] = None,
        superseded_reason: Optional[str] = None
    ) -> Optional[AtomicMemory]:
        """
        Update an existing atomic memory.

        When content changes, the old version is preserved in previous_versions.

        Args:
            memory_id: ID of the memory to update
            content: New content (triggers versioning if changed)
            tags: New tags
            source_exchange_id: Source exchange for tracking
            superseded_reason: Why the content was superseded (e.g. "Status changed - got the job")
        """
        atom = self.get(memory_id)
        if not atom:
            return None

        # If content is changing, version the old content
        if content is not None and content != atom.content:
            atom.previous_versions.append({
                "content": atom.content,
                "timestamp": atom.last_modified,
                "superseded_reason": superseded_reason or "Content updated"
            })
            atom.content = content

        # Also keep legacy history for backward compat
        atom.history.append({
            "content": atom.content,
            "replaced_at": datetime.now().isoformat(),
            "source_exchange_id": source_exchange_id
        })

        if tags is not None:
            atom.tags = tags

        atom.last_modified = datetime.now().isoformat()

        # Update embedding if content changed
        if content is not None:
            try:
                try:
                    from .embeddings import get_embedding_manager
                except ImportError:
                    from embeddings import get_embedding_manager
                emb_mgr = get_embedding_manager()

                # Delete old embedding
                if atom.embedding_id:
                    emb_mgr.delete_by_id(atom.embedding_id)

                # Create new embedding
                atom.embedding_id = emb_mgr.embed(
                    atom.content,
                    metadata={
                        "type": "atomic_memory",
                        "memory_id": atom.id,
                        "tags": atom.tags
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to update embedding: {e}")

        self._save()
        return atom

    def delete(self, memory_id: str) -> bool:
        """Delete an atomic memory."""
        atom = self.get(memory_id)
        if not atom:
            return False

        # Delete embedding
        if atom.embedding_id:
            try:
                try:
                    from .embeddings import get_embedding_manager
                except ImportError:
                    from embeddings import get_embedding_manager
                emb_mgr = get_embedding_manager()
                emb_mgr.delete_by_id(atom.embedding_id)
            except Exception as e:
                logger.warning(f"Failed to delete embedding: {e}")

        self.memories = [m for m in self.memories if m.id != memory_id]
        self._save()
        return True

    def get(self, memory_id: str) -> Optional[AtomicMemory]:
        """Get a memory by ID."""
        for m in self.memories:
            if m.id == memory_id:
                return m
        return None

    def list_all(self) -> List[AtomicMemory]:
        """List all memories."""
        return self.memories.copy()

    def search(
        self,
        query: str,
        k: int = 10
    ) -> List[Tuple[AtomicMemory, float]]:
        """
        Search memories by semantic similarity.

        Args:
            query: Search query
            k: Number of results

        Returns:
            List of (memory, score) tuples
        """
        try:
            try:
                from .embeddings import get_embedding_manager, ContentType
            except ImportError:
                from embeddings import get_embedding_manager, ContentType
            emb_mgr = get_embedding_manager()

            results = emb_mgr.retrieve(
                query,
                k=k * 2,  # Over-fetch for filtering
                threshold=0.2,
                content_type_filter=ContentType.MEMORY
            )

            # Map back to memories
            memory_results = []
            for meta, score in results:
                memory_id = meta.get("memory_id")
                if memory_id:
                    atom = self.get(memory_id)
                    if atom:
                        memory_results.append((atom, score))

                if len(memory_results) >= k:
                    break

            return memory_results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def find_similar(
        self,
        content: str,
        threshold: float = 0.92
    ) -> Optional[AtomicMemory]:
        """
        Find a memory similar to the given content.

        Used for deduplication before creating new memories.
        """
        try:
            try:
                from .embeddings import get_embedding_manager, ContentType
            except ImportError:
                from embeddings import get_embedding_manager, ContentType
            emb_mgr = get_embedding_manager()

            results = emb_mgr.retrieve(
                content,
                k=5,
                threshold=threshold,
                content_type_filter=ContentType.MEMORY
            )

            for meta, score in results:
                memory_id = meta.get("memory_id")
                if memory_id:
                    atom = self.get(memory_id)
                    if atom:
                        return atom

            return None
        except Exception as e:
            logger.warning(f"Similarity search failed: {e}")
            return None

    def get_low_confidence_atoms(self) -> List[AtomicMemory]:
        """Get atoms with any low-confidence thread assignments (triage queue)."""
        return [
            m for m in self.memories
            if any(c == "low" for c in m.assignment_confidence.values())
        ]

    def stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        confidence_dist = {"high": 0, "medium": 0, "low": 0, "unassigned": 0}
        for m in self.memories:
            if not m.assignment_confidence:
                confidence_dist["unassigned"] += 1
            else:
                for conf in m.assignment_confidence.values():
                    confidence_dist[conf] = confidence_dist.get(conf, 0) + 1

        return {
            "total_memories": len(self.memories),
            "with_versions": sum(1 for m in self.memories if m.previous_versions),
            "confidence_distribution": confidence_dist,
            "tags": list(set(t for m in self.memories for t in m.tags))
        }


# Singleton instance
_atomic_manager: Optional[AtomicMemoryManager] = None


def get_atomic_manager() -> AtomicMemoryManager:
    """Get the singleton atomic memory manager."""
    global _atomic_manager
    if _atomic_manager is None:
        _atomic_manager = AtomicMemoryManager()
    return _atomic_manager


def wipe_memory() -> Dict[str, Any]:
    """
    Wipe all memory state - atomic memories, threads, embeddings, and buffers.

    Clears:
    - .claude/memory/atomic_memories.json
    - .claude/memory/threads.json
    - .claude/memory/embeddings/ directory (faiss_index.bin, metadata.json, cache/)
    - .claude/memory/backfill_state.json
    - .claude/memory/exchange_buffer.json
    - .claude/memory/throttle_state.json

    Returns:
        Dict with status and details of what was wiped
    """
    import shutil

    global _atomic_manager

    results = {
        "files_deleted": [],
        "directories_deleted": [],
        "errors": []
    }

    # Files to delete
    files_to_delete = [
        MEMORY_DIR / "atomic_memories.json",
        MEMORY_DIR / "threads.json",
        MEMORY_DIR / "backfill_state.json",
        MEMORY_DIR / "exchange_buffer.json",
        MEMORY_DIR / "throttle_state.json",
    ]

    # Directories to delete
    dirs_to_delete = [
        MEMORY_DIR / "embeddings",
    ]

    # Delete files
    for file_path in files_to_delete:
        try:
            if file_path.exists():
                file_path.unlink()
                results["files_deleted"].append(str(file_path))
        except Exception as e:
            results["errors"].append(f"Failed to delete {file_path}: {e}")

    # Delete directories
    for dir_path in dirs_to_delete:
        try:
            if dir_path.exists():
                shutil.rmtree(dir_path)
                results["directories_deleted"].append(str(dir_path))
        except Exception as e:
            results["errors"].append(f"Failed to delete {dir_path}: {e}")

    # Reset singleton instances
    _atomic_manager = None

    # Try to reset thread manager singleton
    try:
        from thread_memory import _thread_manager
        import thread_memory
        thread_memory._thread_manager = None
    except Exception:
        pass

    # Try to reset embedding manager singleton
    try:
        import embeddings
        embeddings._embedding_manager = None
    except Exception:
        pass

    results["status"] = "success" if not results["errors"] else "partial"
    return results
