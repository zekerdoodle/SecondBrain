"""
Messaging tools package.

Tools for proactive agent-to-user communication:
- message_user: Send a message to the user (new room or existing)
- scan_rooms: Search and list existing conversation rooms
"""

from .tools import message_user, scan_rooms

__all__ = ["message_user", "scan_rooms"]
