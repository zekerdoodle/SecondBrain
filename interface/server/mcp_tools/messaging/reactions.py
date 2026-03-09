"""
Messaging tool — agent emoji reactions on messages.

Allows agents to react to messages in their current conversation with emoji.
"""

import json
import logging
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.messaging.reactions")


def _get_visible_text(msg: dict) -> str:
    """Extract visible text from a message, skipping thinking blocks."""
    # Try direct content first
    content = msg.get("content", "")
    if isinstance(content, str) and content.strip():
        return content.strip()
    # Try blocks (display_messages format) — skip thinking blocks
    blocks = msg.get("blocks", [])
    for block in blocks:
        btype = block.get("type", "")
        if btype in ("thinking", "tool_use", "tool_result"):
            continue
        text = block.get("content", "") or block.get("text", "")
        if isinstance(text, str) and text.strip():
            return text.strip()
    # Try content as list of blocks (messages format)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("thinking", "tool_use", "tool_result"):
                    continue
                text = block.get("text", "")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return ""


def _resolve_message_by_content(chat_data: dict, starts_with: str) -> str | None:
    """Find a message ID by matching the start of its visible text.

    Searches display_messages first (newest→oldest), then falls back to messages.
    Returns the message ID or None.
    """
    prefix = starts_with.strip().lower()
    # Search display_messages in reverse (newest first)
    for source_key in ("display_messages", "messages"):
        msgs = chat_data.get(source_key, [])
        for msg in reversed(msgs):
            visible = _get_visible_text(msg)
            if visible.lower().startswith(prefix):
                mid = msg.get("id")
                if mid:
                    return mid
    return None


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
        import main

        cm = main.chat_manager

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

        # Load chat
        chat_data = cm.load_chat(chat_id)
        if not chat_data:
            return {"content": [{"type": "text", "text": f"Error: chat '{chat_id}' not found"}], "is_error": True}

        # Resolve message_starts_with → message_id
        if not message_id and message_starts_with:
            resolved_id = _resolve_message_by_content(chat_data, message_starts_with)
            if not resolved_id:
                return {"content": [{"type": "text", "text": f"Error: no message found starting with '{message_starts_with[:50]}...'"}], "is_error": True}
            message_id = resolved_id

        def _toggle_reaction(msg: dict) -> bool:
            if msg.get("id") != message_id:
                return False
            reactions = msg.setdefault("reactions", {})
            if remove:
                reactors = reactions.get(emoji, [])
                if agent_name in reactors:
                    reactors.remove(agent_name)
                if not reactors:
                    reactions.pop(emoji, None)
            else:
                reactors = reactions.setdefault(emoji, [])
                if agent_name not in reactors:
                    reactors.append(agent_name)
            if not reactions:
                msg.pop("reactions", None)
            return True

        found = False
        for msg in chat_data.get("messages", []):
            if _toggle_reaction(msg):
                found = True
                break
        for msg in chat_data.get("display_messages", []):
            if _toggle_reaction(msg):
                found = True
                break

        if not found:
            return {"content": [{"type": "text", "text": f"Error: message '{message_id}' not found"}], "is_error": True}

        # Persist
        cm.save_chat(chat_id, chat_data)

        # Update in-memory streaming state if active
        ss = main.session_streaming_states.get(chat_id)
        if ss:
            for msg in ss.messages:
                _toggle_reaction(msg)

        # Get final reactions
        final_reactions = None
        for msg in chat_data.get("display_messages", chat_data.get("messages", [])):
            if msg.get("id") == message_id:
                final_reactions = msg.get("reactions")
                break

        # Broadcast
        await main.broadcast_to_session(chat_id, {
            "type": "reaction_update",
            "sessionId": chat_id,
            "messageId": message_id,
            "reactions": final_reactions or {},
        })

        action = "Removed" if remove else "Added"
        logger.info(f"message_react: {action} {emoji} by {agent_name} on {message_id} in {chat_id}")

        return {"content": [{"type": "text", "text": json.dumps({
            "status": "ok",
            "action": "removed" if remove else "added",
            "emoji": emoji,
            "message_id": message_id,
        })}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error reacting to message: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
