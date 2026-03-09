"""
Messaging tools package.

Tools for proactive agent-to-user communication:
- message_user: Send a message to the user (new room or existing)
- scan_rooms: Search and list existing conversation rooms
- message_react: React to a message with an emoji
"""

from .tools import message_user, scan_rooms
from .reactions import message_react

__all__ = ["message_user", "scan_rooms", "message_react"]
