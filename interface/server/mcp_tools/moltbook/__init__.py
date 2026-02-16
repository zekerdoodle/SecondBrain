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
    moltbook_account_status,
    moltbook_check_dms,
    moltbook_respond_challenge,
    moltbook_challenge_log,
)

__all__ = [
    "moltbook_feed",
    "moltbook_post",
    "moltbook_comment",
    "moltbook_get_post",
    "moltbook_notifications",
    "moltbook_account_status",
    "moltbook_check_dms",
    "moltbook_respond_challenge",
    "moltbook_challenge_log",
]
