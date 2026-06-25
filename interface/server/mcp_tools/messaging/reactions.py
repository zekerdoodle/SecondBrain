"""
Messaging tool — agent emoji reactions on messages.

Allows agents to react to messages in their current conversation with emoji.
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
import asyncio
from pathlib import Path
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.messaging.reactions")

ROOT_DIR = Path(__file__).resolve().parents[4]
INTERNAL_AGENT_INVOKE_TOKEN_FILE = ROOT_DIR / ".claude" / ".secrets" / "internal_agent_invoke_token"


def _running_under_codex_stdio_bridge() -> bool:
    argv = " ".join(sys.argv)
    return "mcp_tools/stdio_server.py" in argv or argv.endswith("stdio_server.py")


def _backend_main_module():
    """Return the live backend main module when this tool is running in-process."""
    if _running_under_codex_stdio_bridge():
        return None

    main_mod = sys.modules.get("main") or sys.modules.get("__main__")
    if not main_mod or not hasattr(main_mod, "apply_message_reaction"):
        return None

    backend_pid = os.environ.get("SECOND_BRAIN_BACKEND_PID")
    if backend_pid:
        try:
            if int(backend_pid) != os.getpid():
                return None
        except ValueError:
            return None

    return main_mod


def _get_internal_agent_invoke_token() -> str | None:
    try:
        token = INTERNAL_AGENT_INVOKE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        token = None
    return token or os.environ.get("SECOND_BRAIN_INTERNAL_AGENT_TOKEN") or None


def _post_internal_message_reaction(payload: Dict[str, Any]) -> Dict[str, Any]:
    token = _get_internal_agent_invoke_token()
    if not token:
        return {"error": "internal message reaction relay token unavailable"}

    base_url = os.environ.get("SECOND_BRAIN_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
    url = base_url.rstrip("/") + "/api/internal/message-reaction"
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
        detail = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(detail)
            detail = data.get("detail", detail)
        except Exception:
            pass
        return {"error": f"internal message reaction relay failed ({e.code}): {detail}"}
    except Exception as e:
        return {"error": f"internal message reaction relay failed: {e}"}


async def _relay_message_reaction_to_backend(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_post_internal_message_reaction, payload)


_MESSAGE_REACT_DESCRIPTION = """React to a message with an emoji.

Use this to express acknowledgment, agreement, humor, or emotion about a specific message
in the current conversation. Reactions are lightweight and visible to the user.

You can identify the target message in two ways:
- message_id: The exact message ID (if you know it)
- message_starts_with: The verbatim beginning of the message text (at least 20 chars).
  Searches recent messages in reverse order (newest first). Matches against visible text only.

Examples:
- React with 👍 to acknowledge: message_starts_with="Great, I'll get that done"
- React with 🎉 to celebrate
- React with 🤔 to express uncertainty
- Use remove=true to undo a previous reaction"""

_MESSAGE_REACT_SCHEMA = {
    "type": "object",
    "properties": {
        "message_id": {
            "type": "string",
            "description": "The ID of the message to react to (use this OR message_starts_with)"
        },
        "message_starts_with": {
            "type": "string",
            "description": "Verbatim text the target message starts with (at least 20 chars). Searches newest messages first."
        },
        "emoji": {
            "type": "string",
            "description": "The emoji character to react with (e.g. '👍', '❤️', '🔥')"
        },
        "remove": {
            "type": "boolean",
            "description": "If true, remove the reaction instead of adding it",
            "default": False
        }
    },
    "required": ["emoji"]
}


@register_tool("messaging")
@tool(name="message_react", description=_MESSAGE_REACT_DESCRIPTION, input_schema=_MESSAGE_REACT_SCHEMA)
async def message_react(args: Dict[str, Any]) -> Dict[str, Any]:
    """React to a message with an emoji."""
    try:
        message_id = args.get("message_id", "").strip()
        message_starts_with = args.get("message_starts_with", "").strip()
        emoji = args.get("emoji", "").strip()
        remove = args.get("remove", False)
        agent_name = args.get("_agent_name", "assistant")
        chat_id = args.get("_source_chat_id")

        if not message_id and not message_starts_with:
            return {"content": [{"type": "text", "text": "Error: provide either message_id or message_starts_with"}], "is_error": True}
        if not emoji:
            return {"content": [{"type": "text", "text": "Error: emoji is required"}], "is_error": True}
        if not chat_id:
            return {"content": [{"type": "text", "text": "Error: no source chat context available"}], "is_error": True}

        payload = {
            "chat_id": chat_id,
            "message_id": message_id or None,
            "message_starts_with": message_starts_with or None,
            "emoji": emoji,
            "remove": bool(remove),
            "reactor": agent_name,
        }

        main_mod = _backend_main_module()
        try:
            if main_mod:
                result = await main_mod.apply_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id or None,
                    message_starts_with=message_starts_with or None,
                    emoji=emoji,
                    reactor=agent_name,
                    remove=remove,
                )
            else:
                result = await _relay_message_reaction_to_backend(payload)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}

        if result.get("error"):
            return {"content": [{"type": "text", "text": f"Error: {result['error']}"}], "is_error": True}

        resolved_message_id = result.get("message_id") or message_id

        action = "Removed" if remove else "Added"
        logger.info(f"message_react: {action} {emoji} by {agent_name} on {resolved_message_id} in {chat_id}")

        return {"content": [{"type": "text", "text": json.dumps({
            "status": "ok",
            "action": "removed" if remove else "added",
            "emoji": emoji,
            "message_id": resolved_message_id,
        })}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error reacting to message: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
