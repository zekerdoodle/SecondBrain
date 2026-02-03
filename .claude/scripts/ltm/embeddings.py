"""
Embedding Manager for Long-Term Memory

Uses sentence-transformers (e5-base-v2) for embeddings and FAISS for vector search.
Provides caching, batch processing, and content-type aware retrieval.
"""

import json
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, asdict
from datetime import datetime
import numpy as np

# Lazy imports for heavy ML libs
_model = None
_faiss = None

logger = logging.getLogger("ltm.embeddings")

# Paths
MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
EMBEDDINGS_DIR = MEMORY_DIR / "embeddings"
INDEX_FILE = EMBEDDINGS_DIR / "faiss_index.bin"
METADATA_FILE = EMBEDDINGS_DIR / "metadata.json"
CACHE_DIR = EMBEDDINGS_DIR / "cache"


class ContentType(Enum):
    """Content types for embedding optimization."""
    CODE = "code"
    TEXT = "text"
    CONFIG = "config"
    MEMORY = "memory"
    THREAD = "thread"
    GENERAL = "general"


@dataclass
class EmbeddingMetadata:
    """Metadata stored alongside each embedding."""
    id: str
    text: str
    content_type: str
    created_at: str
    metadata: Dict[str, Any]


def _get_model():
    """Lazy load the sentence transformer model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading e5-base-v2 embedding model...")
        _model = SentenceTransformer('intfloat/e5-base-v2')
        logger.info("Embedding model loaded")
    return _model


def _get_faiss():
    """Lazy load FAISS."""
    global _faiss
    if _faiss is None:
        import faiss
        _faiss = faiss
    return _faiss


def _generate_id() -> str:
    """Generate a unique embedding ID."""
    import uuid
    return f"emb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _hash_text(text: str) -> str:
    """Generate a hash for caching."""
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]


def detect_content_type(text: str, metadata: Optional[Dict] = None) -> ContentType:
    """Detect content type for embedding optimization."""
    if metadata:
        mtype = metadata.get("type", "")
        if mtype in ("atomic_memory", "long_term_memory"):
            return ContentType.MEMORY
        if mtype == "thread":
            return ContentType.THREAD

    # Code detection
    code_indicators = ['import ', 'def ', 'class ', 'function ', 'const ', 'let ', 'var ', '=> {', '};']
    if any(ind in text for ind in code_indicators):
        return ContentType.CODE

    # Config detection
    if text.strip().startswith('{') or text.strip().startswith('---') or text.strip().startswith('['):
        return ContentType.CONFIG

    return ContentType.TEXT


class EmbeddingManager:
    """
    Manages embeddings with FAISS index and sentence-transformers.

    Features:
    - Automatic model loading (lazy)
    - Per-text caching
    - Batch processing
    - Content-type aware retrieval
    """

    def __init__(self, embeddings_dir: Optional[Path] = None):
        self.embeddings_dir = Path(embeddings_dir) if embeddings_dir else EMBEDDINGS_DIR
        self.index_file = self.embeddings_dir / "faiss_index.bin"
        self.metadata_file = self.embeddings_dir / "metadata.json"
        self.cache_dir = self.embeddings_dir / "cache"

        # Ensure directories exist
        self.embeddings_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # State
        self.index = None
        self.metadata: List[EmbeddingMetadata] = []
        self._load_or_create_index()

    def _load_or_create_index(self):
        """Load existing index or create new one."""
        faiss = _get_faiss()

        if self.index_file.exists() and self.metadata_file.exists():
            try:
                self.index = faiss.read_index(str(self.index_file))
                with open(self.metadata_file, 'r') as f:
                    raw_metadata = json.load(f)
                self.metadata = [
                    EmbeddingMetadata(**m) if isinstance(m, dict) else m
                    for m in raw_metadata
                ]
                logger.info(f"Loaded {len(self.metadata)} embeddings from disk")
            except Exception as e:
                logger.warning(f"Failed to load index: {e}, creating new")
                self._create_new_index()
        else:
            self._create_new_index()

    def _create_new_index(self):
        """Create a new FAISS index."""
        faiss = _get_faiss()
        # e5-base-v2 produces 768-dim vectors
        self.index = faiss.IndexFlatIP(768)  # Inner product (cosine on normalized vectors)
        self.metadata = []
        logger.info("Created new FAISS index")

    def _save_index(self):
        """Save index and metadata to disk."""
        faiss = _get_faiss()
        faiss.write_index(self.index, str(self.index_file))
        with open(self.metadata_file, 'w') as f:
            json.dump([asdict(m) for m in self.metadata], f, indent=2)

    def _get_cached_embedding(self, text_hash: str) -> Optional[np.ndarray]:
        """Check cache for existing embedding."""
        cache_file = self.cache_dir / f"{text_hash}.npy"
        if cache_file.exists():
            return np.load(cache_file)
        return None

    def _cache_embedding(self, text_hash: str, embedding: np.ndarray):
        """Cache an embedding."""
        cache_file = self.cache_dir / f"{text_hash}.npy"
        np.save(cache_file, embedding)

    def embed(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[ContentType] = None
    ) -> str:
        """
        Embed text and add to index.

        Args:
            text: Text to embed
            metadata: Additional metadata to store
            content_type: Content type (auto-detected if not provided)

        Returns:
            Embedding ID
        """
        model = _get_model()
        metadata = metadata or {}

        # Auto-detect content type
        if content_type is None:
            content_type = detect_content_type(text, metadata)

        # Check cache
        text_hash = _hash_text(text)
        embedding = self._get_cached_embedding(text_hash)

        if embedding is None:
            # Generate embedding with e5 prefix
            prefix = "passage: " if content_type != ContentType.CODE else "query: "
            embedding = model.encode(
                prefix + text,
                normalize_embeddings=True,
                show_progress_bar=False
            )
            embedding = np.array(embedding, dtype=np.float32)
            self._cache_embedding(text_hash, embedding)

        # Generate ID and add to index
        emb_id = _generate_id()
        self.index.add(np.array([embedding]))

        # Store metadata
        self.metadata.append(EmbeddingMetadata(
            id=emb_id,
            text=text[:1000],  # Truncate for storage
            content_type=content_type.value,
            created_at=datetime.now().isoformat(),
            metadata=metadata
        ))

        self._save_index()
        return emb_id

    def embed_batch(
        self,
        items: List[Tuple[str, Dict[str, Any]]]
    ) -> List[str]:
        """
        Embed multiple items efficiently.

        Args:
            items: List of (text, metadata) tuples

        Returns:
            List of embedding IDs
        """
        model = _get_model()
        ids = []

        # Separate cached vs uncached
        to_embed = []
        to_embed_indices = []
        embeddings_cache = {}

        for i, (text, metadata) in enumerate(items):
            text_hash = _hash_text(text)
            cached = self._get_cached_embedding(text_hash)
            if cached is not None:
                embeddings_cache[i] = cached
            else:
                content_type = detect_content_type(text, metadata)
                prefix = "passage: " if content_type != ContentType.CODE else "query: "
                to_embed.append(prefix + text)
                to_embed_indices.append((i, text_hash))

        # Batch encode uncached
        if to_embed:
            batch_embeddings = model.encode(
                to_embed,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=32
            )
            for (idx, text_hash), emb in zip(to_embed_indices, batch_embeddings):
                emb = np.array(emb, dtype=np.float32)
                self._cache_embedding(text_hash, emb)
                embeddings_cache[idx] = emb

        # Add all to index
        all_embeddings = []
        for i, (text, metadata) in enumerate(items):
            emb = embeddings_cache[i]
            all_embeddings.append(emb)

            emb_id = _generate_id()
            ids.append(emb_id)

            content_type = detect_content_type(text, metadata)
            self.metadata.append(EmbeddingMetadata(
                id=emb_id,
                text=text[:1000],
                content_type=content_type.value,
                created_at=datetime.now().isoformat(),
                metadata=metadata
            ))

        if all_embeddings:
            self.index.add(np.array(all_embeddings, dtype=np.float32))

        self._save_index()
        return ids

    def retrieve(
        self,
        query: str,
        k: int = 10,
        threshold: float = 0.3,
        content_type_filter: Optional[ContentType] = None
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        Retrieve top-k similar items.

        Args:
            query: Query text
            k: Number of results
            threshold: Minimum similarity threshold
            content_type_filter: Filter by content type

        Returns:
            List of (metadata, score) tuples
        """
        if self.index.ntotal == 0:
            return []

        model = _get_model()

        # Encode query with e5 prefix
        query_emb = model.encode(
            "query: " + query,
            normalize_embeddings=True,
            show_progress_bar=False
        )
        query_emb = np.array([query_emb], dtype=np.float32)

        # Search more than k to allow for filtering
        search_k = min(k * 3, self.index.ntotal)
        scores, indices = self.index.search(query_emb, search_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            if score < threshold:
                continue

            meta = self.metadata[idx]

            # Apply content type filter
            if content_type_filter and meta.content_type != content_type_filter.value:
                continue

            results.append(({
                "id": meta.id,
                "text": meta.text,
                "content_type": meta.content_type,
                "created_at": meta.created_at,
                **meta.metadata
            }, float(score)))

            if len(results) >= k:
                break

        return results

    def delete_by_id(self, emb_id: str) -> bool:
        """
        Delete an embedding by ID.

        Note: FAISS IndexFlatIP doesn't support deletion, so we rebuild.
        """
        idx_to_remove = None
        for i, meta in enumerate(self.metadata):
            if meta.id == emb_id:
                idx_to_remove = i
                break

        if idx_to_remove is None:
            return False

        # Remove from metadata
        self.metadata.pop(idx_to_remove)

        # Rebuild index (expensive but necessary for IndexFlatIP)
        self._rebuild_index()
        return True

    def _rebuild_index(self):
        """Rebuild index from metadata (for deletions)."""
        faiss = _get_faiss()
        model = _get_model()

        # Create new index
        self.index = faiss.IndexFlatIP(768)

        if not self.metadata:
            self._save_index()
            return

        # Re-embed all (from cache ideally)
        embeddings = []
        for meta in self.metadata:
            text_hash = _hash_text(meta.text)
            emb = self._get_cached_embedding(text_hash)
            if emb is None:
                content_type = ContentType(meta.content_type)
                prefix = "passage: " if content_type != ContentType.CODE else "query: "
                emb = model.encode(prefix + meta.text, normalize_embeddings=True)
                emb = np.array(emb, dtype=np.float32)
            embeddings.append(emb)

        self.index.add(np.array(embeddings, dtype=np.float32))
        self._save_index()

    def clear(self):
        """Clear all embeddings."""
        self._create_new_index()
        self._save_index()

        # Clear cache
        for f in self.cache_dir.glob("*.npy"):
            f.unlink()

    def stats(self) -> Dict[str, Any]:
        """Get embedding statistics."""
        type_counts = {}
        for meta in self.metadata:
            ctype = meta.content_type
            type_counts[ctype] = type_counts.get(ctype, 0) + 1

        return {
            "total_embeddings": len(self.metadata),
            "index_size": self.index.ntotal if self.index else 0,
            "by_content_type": type_counts,
            "cache_size": len(list(self.cache_dir.glob("*.npy")))
        }


# Singleton instance
_embedding_manager: Optional[EmbeddingManager] = None


def get_embedding_manager() -> EmbeddingManager:
    """Get the singleton embedding manager."""
    global _embedding_manager
    if _embedding_manager is None:
        _embedding_manager = EmbeddingManager()
    return _embedding_manager
