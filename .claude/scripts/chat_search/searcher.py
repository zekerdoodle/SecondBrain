"""
Chat Search Engine

Provides keyword, semantic, and hybrid search over chat history.
"""

import re
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import numpy as np

from .indexer import (
    ChatSearchIndex, ChatMessageIndex,
    get_index, refresh_index, tokenize,
    INDEX_DIR
)

logger = logging.getLogger("chat_search.searcher")

# Paths for embeddings (separate from LTM)
CHAT_EMBEDDINGS_DIR = INDEX_DIR
CHAT_FAISS_FILE = CHAT_EMBEDDINGS_DIR / "faiss_index.bin"
CHAT_METADATA_FILE = CHAT_EMBEDDINGS_DIR / "embeddings_metadata.json"

# Lazy-loaded embedding infrastructure
_embedding_manager = None


@dataclass
class SearchFilters:
    """Filters for search results."""
    date_from: Optional[float] = None  # Unix timestamp
    date_to: Optional[float] = None    # Unix timestamp
    roles: Optional[List[str]] = None  # ["user", "assistant"]
    exclude_system: bool = True        # Exclude system/scheduled chats
    chat_ids: Optional[List[str]] = None  # Filter to specific chats


@dataclass
class SearchResult:
    """A single search result."""
    message_id: str
    chat_id: str
    chat_title: str
    role: str
    content_preview: str
    timestamp: float
    score: float
    match_type: str  # "keyword", "semantic", "both"


def _get_embedding_manager():
    """Get or create the embedding manager for chat search."""
    global _embedding_manager
    if _embedding_manager is None:
        # Import here to avoid circular dependencies and lazy load ML libs
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from ltm.embeddings import EmbeddingManager

        CHAT_EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
        _embedding_manager = EmbeddingManager(embeddings_dir=CHAT_EMBEDDINGS_DIR)
        logger.info("Initialized chat embedding manager")
    return _embedding_manager


def highlight_matches(text: str, query: str, max_length: int = 200) -> str:
    """
    Create a preview with highlighted search terms.
    Returns HTML-safe text with <mark> tags around matches.
    """
    # Tokenize query
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return text[:max_length] + ("..." if len(text) > max_length else "")

    # Find the best snippet containing query terms
    words = text.split()
    best_start = 0
    best_score = 0

    for i in range(len(words)):
        # Score this window
        window = words[i:i + 30]
        window_text = " ".join(window).lower()
        score = sum(1 for t in query_tokens if t in window_text)
        if score > best_score:
            best_score = score
            best_start = i

    # Extract snippet
    snippet_words = words[best_start:best_start + 30]
    snippet = " ".join(snippet_words)

    # Truncate if needed
    if len(snippet) > max_length:
        snippet = snippet[:max_length].rsplit(" ", 1)[0] + "..."

    # Highlight matches (case-insensitive)
    for token in query_tokens:
        pattern = re.compile(f'({re.escape(token)})', re.IGNORECASE)
        snippet = pattern.sub(r'<mark>\1</mark>', snippet)

    return snippet


def apply_filters(msg: ChatMessageIndex, filters: SearchFilters) -> bool:
    """Check if a message passes the filters."""
    if filters.exclude_system and msg.is_system_chat:
        return False

    if filters.roles and msg.role not in filters.roles:
        return False

    if filters.date_from and msg.timestamp < filters.date_from:
        return False

    if filters.date_to and msg.timestamp > filters.date_to:
        return False

    if filters.chat_ids and msg.chat_id not in filters.chat_ids:
        return False

    return True


def recency_score(timestamp: float, max_days: float = 30.0) -> float:
    """Calculate recency boost (1.0 for today, decaying over max_days)."""
    now = datetime.now().timestamp()
    days_old = (now - timestamp) / 86400  # Seconds per day
    return max(0.0, 1.0 - (days_old / max_days))


class ChatSearcher:
    """Hybrid search engine for chat history."""

    def __init__(self):
        self.index: Optional[ChatSearchIndex] = None

    def _ensure_index(self):
        """Ensure the search index is loaded."""
        if self.index is None:
            self.index = get_index()

    def keyword_search(
        self,
        query: str,
        filters: Optional[SearchFilters] = None,
        limit: int = 50
    ) -> List[SearchResult]:
        """
        Fast keyword search using inverted index.

        Uses AND semantics - all query tokens must match.
        Scores by term frequency and recency.
        """
        self._ensure_index()
        filters = filters or SearchFilters()

        tokens = tokenize(query)
        if not tokens:
            return []

        # Find messages containing all tokens (AND semantics)
        candidate_indices: Optional[set] = None
        for token in tokens:
            matches = set(self.index.keyword_index.get(token, []))
            if candidate_indices is None:
                candidate_indices = matches
            else:
                candidate_indices &= matches

        if not candidate_indices:
            return []

        # Score candidates
        results: List[Tuple[ChatMessageIndex, float]] = []
        for idx in candidate_indices:
            msg = self.index.messages[idx]

            # Apply filters
            if not apply_filters(msg, filters):
                continue

            # Calculate score
            content_lower = msg.content.lower()

            # Term frequency (log-scaled)
            tf_score = sum(
                1 + math.log(content_lower.count(t) + 1)
                for t in tokens
            )

            # Recency boost
            rec_score = recency_score(msg.timestamp)

            # Final score: 70% TF, 30% recency
            score = 0.7 * (tf_score / len(tokens)) + 0.3 * rec_score

            results.append((msg, score))

        # Sort by score descending
        results.sort(key=lambda x: -x[1])

        # Convert to SearchResult
        return [
            SearchResult(
                message_id=msg.message_id,
                chat_id=msg.chat_id,
                chat_title=msg.chat_title,
                role=msg.role,
                content_preview=highlight_matches(msg.content, query),
                timestamp=msg.timestamp,
                score=score,
                match_type="keyword"
            )
            for msg, score in results[:limit]
        ]

    def semantic_search(
        self,
        query: str,
        filters: Optional[SearchFilters] = None,
        k: int = 30,
        threshold: float = 0.3
    ) -> List[SearchResult]:
        """
        Semantic similarity search using embeddings.

        Returns results sorted by cosine similarity.
        """
        self._ensure_index()
        filters = filters or SearchFilters()

        # Check if we have embeddings
        if not self.index.embedding_ids:
            logger.info("No embeddings found, building...")
            self._build_embeddings()

        if not self.index.embedding_ids:
            logger.warning("Failed to build embeddings")
            return []

        try:
            emb_manager = _get_embedding_manager()

            # Search with query prefix for e5 model
            hits = emb_manager.retrieve(
                f"query: {query}",
                k=k * 2,  # Get more to filter
                threshold=threshold
            )

            results: List[SearchResult] = []
            seen_msg_ids = set()

            for metadata, score in hits:
                msg_id = metadata.get("message_id")
                if not msg_id or msg_id in seen_msg_ids:
                    continue
                seen_msg_ids.add(msg_id)

                # Find the message in our index
                msg = self._get_message_by_id(msg_id)
                if not msg:
                    continue

                # Apply filters
                if not apply_filters(msg, filters):
                    continue

                results.append(SearchResult(
                    message_id=msg.message_id,
                    chat_id=msg.chat_id,
                    chat_title=msg.chat_title,
                    role=msg.role,
                    content_preview=msg.content_preview,
                    timestamp=msg.timestamp,
                    score=float(score),
                    match_type="semantic"
                ))

                if len(results) >= k:
                    break

            return results

        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []

    def hybrid_search(
        self,
        query: str,
        filters: Optional[SearchFilters] = None,
        limit: int = 20
    ) -> List[SearchResult]:
        """
        Combined keyword + semantic search.

        Returns merged results with combined scoring.
        """
        # Get keyword results
        keyword_results = self.keyword_search(query, filters, limit=limit * 2)

        # Get semantic results
        semantic_results = self.semantic_search(query, filters, k=limit * 2)

        # Merge results
        merged: Dict[str, SearchResult] = {}

        # Normalize keyword scores (0-1 range)
        if keyword_results:
            max_kw = max(r.score for r in keyword_results)
            for r in keyword_results:
                r.score = r.score / max_kw if max_kw > 0 else 0
                merged[r.message_id] = r

        # Merge semantic results
        for r in semantic_results:
            if r.message_id in merged:
                # Both match - combine scores
                existing = merged[r.message_id]
                combined_score = (
                    0.4 * existing.score +  # Keyword
                    0.3 * r.score +          # Semantic
                    0.2 * recency_score(r.timestamp) +  # Recency
                    0.1                      # Bonus for both
                )
                existing.score = combined_score
                existing.match_type = "both"
            else:
                # Only semantic match
                r.score = (
                    0.3 * r.score +
                    0.2 * recency_score(r.timestamp)
                )
                merged[r.message_id] = r

        # Sort by final score
        results = list(merged.values())
        results.sort(key=lambda r: -r.score)

        return results[:limit]

    def _get_message_by_id(self, msg_id: str) -> Optional[ChatMessageIndex]:
        """Find a message by its ID."""
        for msg in self.index.messages:
            if msg.message_id == msg_id:
                return msg
        return None

    def _build_embeddings(self, batch_size: int = 32):
        """Build embeddings for all messages."""
        self._ensure_index()

        if not self.index.messages:
            logger.info("No messages to embed")
            return

        logger.info(f"Building embeddings for {len(self.index.messages)} messages...")

        try:
            emb_manager = _get_embedding_manager()

            # Prepare batch items
            items: List[Tuple[str, Dict[str, Any]]] = []
            for msg in self.index.messages:
                if msg.message_id in self.index.embedding_ids:
                    continue  # Already embedded

                # Use passage prefix for e5 model
                text = f"passage: {msg.content[:2000]}"  # Limit length
                metadata = {
                    "message_id": msg.message_id,
                    "chat_id": msg.chat_id,
                    "role": msg.role,
                    "type": "chat_message"
                }
                items.append((text, metadata))

            if not items:
                logger.info("All messages already embedded")
                return

            # Batch embed
            emb_ids = emb_manager.embed_batch(items)

            # Update index with embedding IDs
            for (text, metadata), emb_id in zip(items, emb_ids):
                msg_id = metadata["message_id"]
                self.index.embedding_ids[msg_id] = emb_id

            # Save updated index
            from .indexer import save_index
            save_index(self.index)

            logger.info(f"Created {len(emb_ids)} embeddings")

        except Exception as e:
            logger.error(f"Failed to build embeddings: {e}")
            raise

    def refresh(self):
        """Refresh the index and add embeddings for new messages."""
        self.index = refresh_index()
        self._build_embeddings()


# Singleton searcher instance
_searcher: Optional[ChatSearcher] = None


def get_searcher() -> ChatSearcher:
    """Get the singleton searcher instance."""
    global _searcher
    if _searcher is None:
        _searcher = ChatSearcher()
    return _searcher
