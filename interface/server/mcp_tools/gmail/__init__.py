"""Gmail tools."""

# Import to trigger registration
from . import messages

# Re-export for direct access
from .messages import (
    gmail_list_messages,
    gmail_get_message,
    gmail_send,
    gmail_reply,
    gmail_list_labels,
    gmail_modify_labels,
    gmail_trash,
    gmail_draft_create,
)

__all__ = [
    "gmail_list_messages",
    "gmail_get_message",
    "gmail_send",
    "gmail_reply",
    "gmail_list_labels",
    "gmail_modify_labels",
    "gmail_trash",
    "gmail_draft_create",
]
