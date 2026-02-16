"""
Active Room Tracker

Provides persistence of the currently active room for the web UI,
so that scheduled tasks and background operations can target a sensible place.

Adapted from Theo's active_room.py for Second Brain.

Storage location: .claude/active_room.json

Functions:
- get_active_room() -> Optional[str]
- set_active_room(room_id: str, context: str = None) -> Tuple[bool, str]
- get_active_payload() -> dict
- set_active_context(context: str) -> Tuple[bool, str]
"""

from __future__ import annotations

import json
import os
import re
import time
import logging
from pathlib import Path
from typing import Optional, Tuple

# Import atomic file ops
try:
    from .atomic_file_ops import load_json, save_json
except ImportError:
    from atomic_file_ops import load_json, save_json

logger = logging.getLogger(__name__)

# Base paths - resolve from this script's location
_SCRIPTS_DIR = Path(__file__).parent
_CLAUDE_DIR = _SCRIPTS_DIR.parent
_CHATS_DIR = _CLAUDE_DIR / "chats"

# Room ID validation pattern - alphanumeric, dashes, underscores
_ROOM_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _active_path() -> Path:
    """Return path to active_room.json."""
    return _CLAUDE_DIR / "active_room.json"


def _room_file(room_id: str) -> Path:
    """Return path to a room's chat file."""
    return _CHATS_DIR / f"{room_id}.json"


def _is_valid_room(room_id: str) -> bool:
    """Check if room_id is valid and exists on disk."""
    if not isinstance(room_id, str) or not room_id:
        return False
    if not _ROOM_RE.match(room_id):
        return False
    # Only return valid if the room exists on disk
    return _room_file(room_id).exists()


def set_active_room(room_id: str, context: Optional[str] = None) -> Tuple[bool, str]:
    """Persist the active room id with timestamp.

    Args:
        room_id: The room ID to set as active
        context: Optional context string (e.g., "idle", "chat", "tasks")

    Returns:
        Tuple of (success, message)
    """
    try:
        # Ensure directories exist
        _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

        if not _is_valid_room(room_id):
            return False, f"invalid or unknown room: {room_id}"

        payload = {
            "room": room_id,
            "updated_at": time.time()
        }

        if context:
            payload["context"] = str(context)
        else:
            # Preserve existing context if not overriding
            try:
                existing = get_active_payload()
                if existing.get("context"):
                    payload["context"] = existing["context"]
            except Exception:
                pass

        save_json(_active_path(), payload)
        logger.debug(f"Active room set to {room_id} (context={payload.get('context')})")
        return True, "ok"

    except Exception as e:
        logger.warning(f"set_active_room failed: {e}")
        return False, str(e)


def set_active_context(context: str, room_id: Optional[str] = None) -> Tuple[bool, str]:
    """Store the active UI context (e.g., tasks views) with optional room hint.

    Args:
        context: The context to set (e.g., "idle", "chat", "tasks")
        room_id: Optional room ID to associate with the context

    Returns:
        Tuple of (success, message)
    """
    try:
        _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

        payload = get_active_payload()
        payload["context"] = str(context) if context else None

        if room_id and _is_valid_room(room_id):
            payload["room"] = room_id

        payload["updated_at"] = time.time()
        save_json(_active_path(), payload)
        return True, "ok"

    except Exception as e:
        logger.warning(f"set_active_context failed: {e}")
        return False, str(e)


def get_active_room() -> Optional[str]:
    """Return the currently active room id if valid and present.

    Returns:
        The room id or None if not set/valid.
    """
    try:
        path = _active_path()
        if not path.exists():
            return None

        data = load_json(path, default={})
        room = str(data.get("room") or "").strip()

        if _is_valid_room(room):
            return room
        return None

    except Exception as e:
        logger.debug(f"get_active_room failed: {e}")
        return None


def get_active_payload() -> dict:
    """Return the raw active-room payload for API responses.

    Ensures keys are present even if file missing.
    Automatically cleans up stale room references.
    """
    try:
        path = _active_path()
        if not path.exists():
            return {"room": None, "updated_at": None, "context": None}

        data = load_json(path, default={})

        room = data.get("room")
        updated_at = data.get("updated_at")
        context = data.get("context")

        # Check if room is valid; if not, clean it up
        if room is not None:
            if not isinstance(room, str) or not _is_valid_room(room):
                logger.debug(f"Cleaning up stale active room reference: {room}")
                room = None
                # Clean up the file to remove the stale reference
                try:
                    cleaned_data = {"room": None, "updated_at": time.time(), "context": context}
                    save_json(path, cleaned_data)
                except Exception as cleanup_exc:
                    logger.debug(f"Failed to clean up stale room reference: {cleanup_exc}")

        if updated_at is not None:
            try:
                updated_at = float(updated_at)
            except Exception:
                updated_at = None

        if context is not None:
            context = str(context)

        return {"room": room, "updated_at": updated_at, "context": context}

    except Exception as e:
        logger.debug(f"get_active_payload failed: {e}")
        return {"room": None, "updated_at": None, "context": None}


def clear_active_room() -> bool:
    """Clear the active room (but preserve context)."""
    try:
        path = _active_path()
        payload = get_active_payload()
        payload["room"] = None
        payload["updated_at"] = time.time()
        save_json(path, payload)
        return True
    except Exception as e:
        logger.warning(f"clear_active_room failed: {e}")
        return False


__all__ = [
    "get_active_room",
    "set_active_room",
    "get_active_payload",
    "set_active_context",
    "clear_active_room",
]
