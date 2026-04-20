"""
Messaging tools — proactive agent-to-user communication.

Tools:
- message_user: Send a message to the user (creates new room or appends to existing)
- scan_rooms: Search and list existing conversation rooms
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.messaging")


# =============================================================================
# scan_rooms — List/search conversation rooms
# =============================================================================

_SCAN_ROOMS_DESCRIPTION = """Search and list existing conversation rooms.

Use this to find conversations by title, agent, or recency before sending a message.
Returns rooms sorted by most recent activity.

Examples:
- Find Character's recent conversations: agent="character"
- Find a specific topic: query="portfolio"
- List the 5 most recent rooms: limit=5"""

_SCAN_ROOMS_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "description": "Max results to return (default: 20, max: 50)",
            "default": 20,
            "minimum": 1,
            "maximum": 50
        },
        "query": {
            "type": "string",
            "description": "Filter by title substring (case-insensitive)"
        },
        "agent": {
            "type": "string",
            "description": "Filter by agent name (e.g., 'character', 'patch')"
        }
    },
    "required": []
}


@register_tool("messaging")
@tool(name="scan_rooms", description=_SCAN_ROOMS_DESCRIPTION, input_schema=_SCAN_ROOMS_SCHEMA)
async def scan_rooms(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search and list conversation rooms."""
    try:
        # Lazy import to avoid circular imports
        import main
        cm = main.chat_manager

        limit = min(args.get("limit", 20), 50)
        query = args.get("query", "").strip().lower()
        agent_filter = args.get("agent", "").strip().lower()

        # Get all chats (already sorted by recency)
        all_chats = cm.list_chats()

        # Apply filters
        filtered = []
        for chat in all_chats:
            # Agent filter
            if agent_filter:
                chat_agent = (chat.get("agent") or "").lower()
                if chat_agent != agent_filter:
                    continue

            # Title query filter (case-insensitive substring)
            if query:
                title = (chat.get("title") or "").lower()
                if query not in title:
                    continue

            filtered.append({
                "room_id": chat["id"],
                "title": chat.get("title", "Untitled"),
                "agent": chat.get("agent"),
                "last_activity": chat.get("updated"),
                "is_system": chat.get("is_system", False),
                "scheduled": chat.get("scheduled", False),
            })

            if len(filtered) >= limit:
                break

        result_text = json.dumps(filtered, indent=2)
        summary = f"Found {len(filtered)} room(s)"
        if query:
            summary += f" matching '{query}'"
        if agent_filter:
            summary += f" for agent '{agent_filter}'"

        return {"content": [{"type": "text", "text": f"{summary}\n\n{result_text}"}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error scanning rooms: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


# =============================================================================
# message_user — Proactive agent-to-user messaging
# =============================================================================

_MESSAGE_USER_DESCRIPTION = """Send a message directly to the user.

This bypasses the inference loop — the message is delivered as-is, no tokens burned.
Use this for proactive communication: thoughts, updates, reminders, or continuing a conversation.

Behavior:
- room_id=None: Creates a NEW room with the message. Provide a title.
- room_id="abc-123": Appends the message to an existing room.
- Always sends a notification (toast + push) so the user sees it.
- The message appears as an assistant message in the chat.

Use scan_rooms first to find the right room if you want to continue a conversation."""

_MESSAGE_USER_SCHEMA = {
    "type": "object",
    "properties": {
        "room_id": {
            "type": "string",
            "description": "Target room ID. Omit or null to create a new room."
        },
        "contents": {
            "type": "string",
            "description": "The message body (markdown supported)"
        },
        "title": {
            "type": "string",
            "description": "Title for new rooms. Ignored if room_id is provided. Auto-generated if omitted."
        }
    },
    "required": ["contents"]
}


@register_tool("messaging")
@tool(name="message_user", description=_MESSAGE_USER_DESCRIPTION, input_schema=_MESSAGE_USER_SCHEMA)
async def message_user(args: Dict[str, Any]) -> Dict[str, Any]:
    """Send a message to the user, creating a new room or appending to an existing one."""
    try:
        # Lazy imports to avoid circular dependencies
        import main
        from notifications import should_notify, send_notification

        cm = main.chat_manager

        room_id = args.get("room_id")
        contents = args.get("contents", "").strip()
        title = args.get("title")
        agent_name = args.get("_agent_name", "character")  # Injected by context wrapper

        if not contents:
            return {"content": [{"type": "text", "text": "Error: contents is required"}], "is_error": True}

        now = time.time()
        msg_id = f"msg-{int(now * 1000)}-{uuid.uuid4().hex[:8]}"
        is_new_room = False

        if room_id:
            # --- Append to existing room ---
            existing = cm.load_chat(room_id)
            if not existing:
                return {
                    "content": [{"type": "text", "text": f"Error: room '{room_id}' not found"}],
                    "is_error": True
                }

            # Append the assistant message
            existing.setdefault("messages", []).append({
                "id": msg_id,
                "role": "assistant",
                "content": contents,
                "created_at": now,
            })
            existing["last_message_at"] = now
            cm.save_chat(room_id, existing)
            title = existing.get("title", "Untitled")

        else:
            # --- Create new room ---
            room_id = f"msg-{uuid.uuid4().hex[:12]}"
            is_new_room = True

            if not title:
                # Auto-generate from first 50 chars of content
                title = contents[:47].replace("\n", " ").strip()
                if len(contents) > 47:
                    title += "..."

            chat_data = {
                "title": title,
                "agent": agent_name,
                "messages": [
                    {
                        "id": msg_id,
                        "role": "assistant",
                        "content": contents,
                        "created_at": now,
                    }
                ],
                "last_message_at": now,
                "is_system": False,
                "scheduled": False,
            }
            cm.save_chat(room_id, chat_data)

        # --- Broadcast to connected clients ---

        # If it's a new room, tell all clients about it (for sidebar update)
        if is_new_room:
            await main.broadcast_chat_created(
                chat_id=room_id,
                title=title,
                agent=agent_name,
            )

        # Broadcast the message to anyone viewing this room
        # Uses "message_accepted" type which the frontend already handles
        await main.broadcast_to_session(room_id, {
            "type": "message_accepted",
            "sessionId": room_id,
            "message": {
                "id": msg_id,
                "role": "assistant",
                "content": contents,
                "created_at": now,
            },
        })

        # --- Send notification ---
        # Always notify for message_user (override silent flag)
        decision = should_notify(
            chat_id=room_id,
            is_silent=False,  # Never silent — whole point is to reach the user
            client_sessions=main.client_sessions,
        )

        if decision.notify:
            preview = contents[:100] if contents else "New message"
            await send_notification(
                client_sessions=main.client_sessions,
                chat_id=room_id,
                preview=preview,
                play_sound=decision.play_sound,
                title=title or "",
            )

        logger.info(f"message_user: {'created' if is_new_room else 'appended to'} room {room_id} (agent={agent_name})")

        return {"content": [{"type": "text", "text": json.dumps({
            "room_id": room_id,
            "title": title,
            "is_new_room": is_new_room,
            "message_id": msg_id,
        })}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error sending message: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
