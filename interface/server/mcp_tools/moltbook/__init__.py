"""Moltbook tools for AI social platform integration."""

# Import to trigger registration
from . import moltbook

# Re-export for direct access
from .moltbook import (
    moltbook_feed,
    moltbook_post,
    moltbook_comment,
    moltbook_get_post,
    moltbook_notifications,
)

__all__ = [
    "moltbook_feed",
    "moltbook_post",
    "moltbook_comment",
    "moltbook_get_post",
    "moltbook_notifications",
]
