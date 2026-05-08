"""
Salon MCP tools — group-chat creation and management for agents.

Tools:
- create_salon: spin up a new group chat, optionally including the user
- add_to_salon: add a participant to an existing salon
- list_salons: list salons the calling agent is in
- read_salon: read the full message history of a salon
- post_to_salon: post a message to a salon you're a participant in
"""

from .tools import (
    create_salon,
    add_to_salon,
    list_salons,
    read_salon,
    post_to_salon,
)

__all__ = [
    "create_salon",
    "add_to_salon",
    "list_salons",
    "read_salon",
    "post_to_salon",
]
