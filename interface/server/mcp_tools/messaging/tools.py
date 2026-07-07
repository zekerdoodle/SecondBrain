"""
Messaging tools — proactive agent-to-user communication.

Tools:
- message_user: Send a message to the user (creates new room or appends to existing)
- scan_rooms: Search and list existing conversation rooms
"""

import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.messaging")

ROOT_DIR = Path(__file__).resolve().parents[4]
INTERNAL_AGENT_INVOKE_TOKEN_FILE = ROOT_DIR / ".claude" / ".secrets" / "internal_agent_invoke_token"


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


def _running_under_codex_stdio_bridge() -> bool:
    argv = " ".join(sys.argv)
    return "mcp_tools/stdio_server.py" in argv or argv.endswith("stdio_server.py")


def _running_in_backend_process() -> bool:
    """Return True only when this tool is executing inside the live backend."""
    if _running_under_codex_stdio_bridge():
        return False

    argv = " ".join(sys.argv)
    if "uvicorn" not in argv or "main:app" not in argv:
        return False

    backend_pid = os.environ.get("SECOND_BRAIN_BACKEND_PID")
    if not backend_pid:
        return False

    try:
        return int(backend_pid) == os.getpid()
    except ValueError:
        return False


def _backend_main_module():
    if not _running_in_backend_process():
        return None

    main_mod = sys.modules.get("main") or sys.modules.get("__main__")
    if not main_mod or not hasattr(main_mod, "deliver_message_user"):
        return None
    return main_mod


def _get_internal_agent_invoke_token() -> Optional[str]:
    try:
        token = INTERNAL_AGENT_INVOKE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        token = None
    return token or os.environ.get("SECOND_BRAIN_INTERNAL_AGENT_TOKEN") or None


def _post_internal_message_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    token = _get_internal_agent_invoke_token()
    if not token:
        return {"error": "internal message_user relay token unavailable"}

    base_url = os.environ.get("SECOND_BRAIN_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
    url = base_url.rstrip("/") + "/api/internal/message-user"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Second-Brain-Internal-Token": token,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"status": "ok"}
    except urllib.error.HTTPError as e:
        detail_raw = e.read().decode("utf-8", errors="replace")
        relay_error = detail_raw
        result: Dict[str, Any] = {}
        try:
            data = json.loads(detail_raw)
            detail = data.get("detail", detail_raw)
            if isinstance(detail, dict):
                relay_error = detail.get("error") or json.dumps(detail, sort_keys=True)
                for key in ("room_id", "title", "is_new_room", "message_id", "saved"):
                    if key in detail:
                        result[key] = detail[key]
            else:
                relay_error = str(detail)
        except Exception:
            pass
        result["error"] = f"internal message_user relay failed ({e.code}): {relay_error}"
        return result
    except Exception as e:
        return {"error": f"internal message_user relay failed: {e}"}


async def _relay_message_user_to_backend(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_post_internal_message_user, payload)


def _error_result_from_message_user_failure(result: Dict[str, Any]) -> Dict[str, Any]:
    lines = [f"Error: {result.get('error', 'message_user failed')}"]
    for key in ("room_id", "title", "is_new_room", "message_id"):
        if key in result and result[key] is not None:
            lines.append(f"{key}: {result[key]}")
    if result.get("saved"):
        lines.append("The message was durably saved before live delivery failed; Patch can recover manually from the IDs above.")

    return {
        "content": [{"type": "text", "text": "\n".join(lines)}],
        "is_error": True,
    }


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
        room_id = args.get("room_id")
        contents = args.get("contents", "").strip()
        title = args.get("title")
        agent_name = args.get("_agent_name", "character")  # Injected by context wrapper

        if not contents:
            return {"content": [{"type": "text", "text": "Error: contents is required"}], "is_error": True}

        payload = {
            "room_id": room_id.strip() if isinstance(room_id, str) and room_id.strip() else None,
            "contents": contents,
            "title": title.strip() if isinstance(title, str) and title.strip() else None,
            "agent_name": agent_name,
        }

        main_mod = _backend_main_module()
        if main_mod:
            try:
                result = await main_mod.deliver_message_user(**payload)
            except ValueError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}
        else:
            result = await _relay_message_user_to_backend(payload)

        if result.get("error"):
            return _error_result_from_message_user_failure(result)

        response = {
            "room_id": result.get("room_id"),
            "title": result.get("title"),
            "is_new_room": result.get("is_new_room"),
            "message_id": result.get("message_id"),
        }

        logger.info(
            "message_user: %s room %s (agent=%s)",
            "created" if response["is_new_room"] else "appended to",
            response["room_id"],
            agent_name,
        )

        return {"content": [{"type": "text", "text": json.dumps(response)}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error sending message: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
