"""
Rooms Meta Utilities

Provides metadata layer for fast room listing without reading all messages.
Adapted from Theo's rooms_meta.py for Second Brain.

Functions:
- rooms_meta_path() -> str
- load() -> dict
- save(meta: dict) -> None
- bump(room_id: str) -> None
- set_title(room_id: str, title: str) -> None
- get_room_meta(room_id: str) -> dict

Storage location: .claude/chats_meta.json
"""

from __future__ import annotations

import json
import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, Optional

# Import atomic file ops
try:
    from .atomic_file_ops import load_json, save_json
except ImportError:
    from atomic_file_ops import load_json, save_json

logger = logging.getLogger(__name__)

# Base paths - resolve from this script's location
_SCRIPTS_DIR = Path(__file__).parent
_CLAUDE_DIR = _SCRIPTS_DIR.parent
_ROOT_DIR = _CLAUDE_DIR.parent


def rooms_meta_path() -> str:
    """Return the absolute path to chats_meta.json."""
    return str(_CLAUDE_DIR / "chats_meta.json")


def load() -> Dict[str, Any]:
    """Load room metadata as a dict mapping room_id -> {title, updated_at, room_type}.

    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    path = Path(rooms_meta_path())
    if not path.exists():
        return {}
    return load_json(path, default={})


def save(meta: Dict[str, Any]) -> None:
    """Persist room metadata."""
    path = Path(rooms_meta_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, meta)


def bump(room_id: str) -> None:
    """Update updated_at for a room to now, creating entry if missing."""
    if not room_id:
        return
    try:
        meta = load()
        meta.setdefault(room_id, {})
        meta[room_id]["updated_at"] = time.time()
        save(meta)
    except Exception as e:
        logger.warning(f"rooms_meta.bump failed: {e}")


def set_title(room_id: str, title: str) -> None:
    """Set title for a room."""
    if not room_id:
        return
    try:
        meta = load()
        meta.setdefault(room_id, {})
        meta[room_id]["title"] = title
        meta[room_id]["updated_at"] = time.time()
        save(meta)
    except Exception as e:
        logger.warning(f"rooms_meta.set_title failed: {e}")


def get_room_meta(room_id: str) -> Dict[str, Any]:
    """Get metadata for a specific room."""
    if not room_id:
        return {"title": None, "updated_at": None, "room_type": "standard"}
    try:
        meta = load()
        return meta.get(room_id, {"title": None, "updated_at": None, "room_type": "standard"})
    except Exception:
        return {"title": None, "updated_at": None, "room_type": "standard"}


def create_room_meta(room_id: str, title: Optional[str] = None, room_type: str = "standard") -> Dict[str, Any]:
    """Create metadata entry for a new room."""
    if not room_id:
        return {}
    try:
        meta = load()
        now = time.time()
        meta[room_id] = {
            "title": title or "New Chat",
            "updated_at": now,
            "created_at": now,
            "room_type": room_type
        }
        save(meta)
        return meta[room_id]
    except Exception as e:
        logger.warning(f"rooms_meta.create_room_meta failed: {e}")
        return {}


def delete_room_meta(room_id: str) -> bool:
    """Remove metadata entry for a room."""
    if not room_id:
        return False
    try:
        meta = load()
        if room_id in meta:
            del meta[room_id]
            save(meta)
            return True
        return False
    except Exception as e:
        logger.warning(f"rooms_meta.delete_room_meta failed: {e}")
        return False


def list_rooms_from_meta() -> list[Dict[str, Any]]:
    """List all rooms with metadata, sorted by most recent update.

    Returns list of dicts with: id, title, updated_at, room_type
    """
    try:
        meta = load()
        rooms = []
        for room_id, data in meta.items():
            rooms.append({
                "id": room_id,
                "title": data.get("title", "Untitled Chat"),
                "updated_at": data.get("updated_at", 0),
                "room_type": data.get("room_type", "standard")
            })
        # Sort by most recently updated
        return sorted(rooms, key=lambda x: x["updated_at"], reverse=True)
    except Exception as e:
        logger.warning(f"rooms_meta.list_rooms_from_meta failed: {e}")
        return []


__all__ = [
    "rooms_meta_path",
    "load",
    "save",
    "bump",
    "set_title",
    "get_room_meta",
    "create_room_meta",
    "delete_room_meta",
    "list_rooms_from_meta",
]
