"""
Chat Search Indexer

Builds and maintains the keyword index for fast chat search.
"""

import json
import re
import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime

logger = logging.getLogger("chat_search.indexer")

# Paths
SCRIPTS_DIR = Path(__file__).parent.parent.parent
CHATS_DIR = SCRIPTS_DIR / "chats"
INDEX_DIR = SCRIPTS_DIR.parent / "chat_search"
INDEX_FILE = INDEX_DIR / "index.json"

# Stopwords for keyword indexing (common words to skip)
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "is", "it", "be", "as", "was", "with", "that", "this", "by",
    "are", "from", "have", "has", "had", "not", "you", "your", "i", "me",
    "my", "we", "our", "they", "their", "he", "she", "his", "her", "its",
    "will", "would", "could", "should", "can", "do", "does", "did", "just",
    "so", "if", "then", "than", "when", "what", "which", "who", "how",
    "all", "each", "both", "more", "most", "other", "some", "any", "no",
    "there", "here", "where", "why", "very", "also", "only", "about",
    "up", "out", "into", "over", "after", "before", "between", "through"
}


@dataclass
class ChatMessageIndex:
    """Indexed message for search."""
    message_id: str
    chat_id: str
    chat_title: str
    role: str
    content: str
    content_preview: str
    timestamp: float
    is_system_chat: bool


@dataclass
class ChatSearchIndex:
    """Full search index for all chats."""
    messages: List[ChatMessageIndex] = field(default_factory=list)
    keyword_index: Dict[str, List[int]] = field(default_factory=dict)  # word -> message indices
    embedding_ids: Dict[str, str] = field(default_factory=dict)  # message_id -> embedding_id
    last_indexed: str = ""
    chat_mtimes: Dict[str, float] = field(default_factory=dict)  # chat_id -> mtime

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "messages": [asdict(m) for m in self.messages],
            "keyword_index": self.keyword_index,
            "embedding_ids": self.embedding_ids,
            "last_indexed": self.last_indexed,
            "chat_mtimes": self.chat_mtimes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatSearchIndex":
        """Create from dictionary."""
        return cls(
            messages=[ChatMessageIndex(**m) for m in data.get("messages", [])],
            keyword_index=data.get("keyword_index", {}),
            embedding_ids=data.get("embedding_ids", {}),
            last_indexed=data.get("last_indexed", ""),
            chat_mtimes=data.get("chat_mtimes", {}),
        )


def parse_message_timestamp(msg_id: str, chat_mtime: float) -> float:
    """Extract timestamp from message ID or fall back to chat mtime."""
    # Try Unix timestamp (ms) format - these are 13+ digit numbers
    if msg_id.isdigit() and len(msg_id) >= 13:
        return int(msg_id) / 1000.0
    # UUID format - use chat mtime as fallback
    return chat_mtime


def tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase words, filtering stopwords."""
    # Extract words (letters and numbers)
    words = re.findall(r'\b[a-zA-Z0-9]+\b', text.lower())
    # Filter stopwords and short tokens
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def create_content_preview(content: str, max_length: int = 200) -> str:
    """Create a preview of the content."""
    # Clean up whitespace
    preview = " ".join(content.split())
    if len(preview) > max_length:
        # Cut at word boundary
        preview = preview[:max_length].rsplit(" ", 1)[0] + "..."
    return preview


def load_index() -> Optional[ChatSearchIndex]:
    """Load existing index from disk."""
    if not INDEX_FILE.exists():
        return None
    try:
        with open(INDEX_FILE, "r") as f:
            data = json.load(f)
        return ChatSearchIndex.from_dict(data)
    except Exception as e:
        logger.warning(f"Failed to load index: {e}")
        return None


def save_index(index: ChatSearchIndex):
    """Save index to disk."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, "w") as f:
        json.dump(index.to_dict(), f)
    logger.info(f"Saved index with {len(index.messages)} messages")


def build_index(chats_dir: Optional[Path] = None) -> ChatSearchIndex:
    """
    Build a complete search index from all chat files.

    Args:
        chats_dir: Directory containing chat JSON files

    Returns:
        ChatSearchIndex with all messages indexed
    """
    chats_dir = chats_dir or CHATS_DIR
    index = ChatSearchIndex()

    if not chats_dir.exists():
        logger.warning(f"Chats directory not found: {chats_dir}")
        return index

    chat_files = list(chats_dir.glob("*.json"))
    logger.info(f"Indexing {len(chat_files)} chat files...")

    for chat_file in chat_files:
        try:
            mtime = os.path.getmtime(chat_file)
            with open(chat_file, "r") as f:
                chat_data = json.load(f)

            chat_id = chat_data.get("sessionId", chat_file.stem)
            chat_title = chat_data.get("title", "Untitled")
            is_system = chat_data.get("is_system", False)
            messages = chat_data.get("messages", [])

            # Index each message
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                msg_id = msg.get("id", "")

                # Skip system messages (prompts) and empty content
                if role == "system" or not content.strip():
                    continue

                timestamp = parse_message_timestamp(msg_id, mtime)

                msg_index = ChatMessageIndex(
                    message_id=msg_id,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    role=role,
                    content=content,
                    content_preview=create_content_preview(content),
                    timestamp=timestamp,
                    is_system_chat=is_system,
                )

                # Add to messages list
                msg_idx = len(index.messages)
                index.messages.append(msg_index)

                # Build keyword index
                tokens = tokenize(content)
                for token in set(tokens):  # Unique tokens per message
                    if token not in index.keyword_index:
                        index.keyword_index[token] = []
                    index.keyword_index[token].append(msg_idx)

            # Track mtime for incremental updates
            index.chat_mtimes[chat_id] = mtime

        except Exception as e:
            logger.warning(f"Failed to index {chat_file}: {e}")

    index.last_indexed = datetime.now().isoformat()
    save_index(index)

    logger.info(f"Built index: {len(index.messages)} messages, {len(index.keyword_index)} unique tokens")
    return index


def update_index(existing_index: Optional[ChatSearchIndex] = None,
                 chats_dir: Optional[Path] = None) -> ChatSearchIndex:
    """
    Incrementally update the index with new/modified chats.

    Args:
        existing_index: Current index to update (loads from disk if None)
        chats_dir: Directory containing chat JSON files

    Returns:
        Updated ChatSearchIndex
    """
    chats_dir = chats_dir or CHATS_DIR

    # Load existing index
    if existing_index is None:
        existing_index = load_index()

    # If no existing index, build from scratch
    if existing_index is None:
        return build_index(chats_dir)

    if not chats_dir.exists():
        return existing_index

    # Find new/modified files
    chat_files = list(chats_dir.glob("*.json"))
    updated_count = 0

    for chat_file in chat_files:
        chat_id = chat_file.stem
        current_mtime = os.path.getmtime(chat_file)

        # Check if file is new or modified
        if chat_id in existing_index.chat_mtimes:
            if existing_index.chat_mtimes[chat_id] >= current_mtime:
                continue  # No changes

        # Remove old messages for this chat
        old_msg_ids = set()
        for i, msg in enumerate(existing_index.messages):
            if msg.chat_id == chat_id:
                old_msg_ids.add(i)

        if old_msg_ids:
            # Remove from keyword index
            for token, indices in existing_index.keyword_index.items():
                existing_index.keyword_index[token] = [
                    i for i in indices if i not in old_msg_ids
                ]

            # Remove empty keyword entries
            existing_index.keyword_index = {
                k: v for k, v in existing_index.keyword_index.items() if v
            }

            # Remove old messages (rebuild list)
            existing_index.messages = [
                m for i, m in enumerate(existing_index.messages)
                if i not in old_msg_ids
            ]

            # Reindex keyword index (indices shifted)
            # Rebuild from scratch for this operation
            new_keyword_index: Dict[str, List[int]] = {}
            for i, msg in enumerate(existing_index.messages):
                for token in set(tokenize(msg.content)):
                    if token not in new_keyword_index:
                        new_keyword_index[token] = []
                    new_keyword_index[token].append(i)
            existing_index.keyword_index = new_keyword_index

        # Now add updated chat
        try:
            with open(chat_file, "r") as f:
                chat_data = json.load(f)

            chat_title = chat_data.get("title", "Untitled")
            is_system = chat_data.get("is_system", False)
            messages = chat_data.get("messages", [])

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                msg_id = msg.get("id", "")

                if role == "system" or not content.strip():
                    continue

                timestamp = parse_message_timestamp(msg_id, current_mtime)

                msg_index = ChatMessageIndex(
                    message_id=msg_id,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    role=role,
                    content=content,
                    content_preview=create_content_preview(content),
                    timestamp=timestamp,
                    is_system_chat=is_system,
                )

                msg_idx = len(existing_index.messages)
                existing_index.messages.append(msg_index)

                for token in set(tokenize(content)):
                    if token not in existing_index.keyword_index:
                        existing_index.keyword_index[token] = []
                    existing_index.keyword_index[token].append(msg_idx)

            existing_index.chat_mtimes[chat_id] = current_mtime
            updated_count += 1

        except Exception as e:
            logger.warning(f"Failed to update {chat_file}: {e}")

    if updated_count > 0:
        existing_index.last_indexed = datetime.now().isoformat()
        save_index(existing_index)
        logger.info(f"Updated {updated_count} chats, total {len(existing_index.messages)} messages")

    return existing_index


# Singleton index instance
_index: Optional[ChatSearchIndex] = None


def get_index() -> ChatSearchIndex:
    """Get the current search index, loading or building if needed."""
    global _index
    if _index is None:
        _index = load_index()
        if _index is None:
            _index = build_index()
    return _index


def refresh_index() -> ChatSearchIndex:
    """Refresh the index with any new/modified chats."""
    global _index
    _index = update_index(_index)
    return _index
