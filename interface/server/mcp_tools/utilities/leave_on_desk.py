"""
Leave on Desk tool — an agent can leave a file open on the user's desk.

Opens a file in all connected editors via WebSocket broadcast.
The tool itself triggers the broadcast via desk_broker (not main.py streaming),
so it works whether called from direct chat or an invoked agent.
Saves the reason to the calling agent's working memory for context persistence.
"""

import sys
from pathlib import Path
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts to path for working_memory import
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / ".claude" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@register_tool("presence")
@tool(
    name="leave_on_desk",
    description="""Leave a file on the user's desk — opens it in all connected editors.

Use this to place a file where the user will see it: a morning note, a generated image,
a research report, or anything you want him to find. The file appears in his editor
across all devices.

The reason is saved to your working memory so you remember why you left it.""",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to leave on the user's desk"
            },
            "reason": {
                "type": "string",
                "description": "Why you're leaving this file — saved to working memory for context"
            }
        },
        "required": ["file_path", "reason"]
    }
)
async def leave_on_desk(args: Dict[str, Any]) -> Dict[str, Any]:
    """Leave a file on the user's desk."""
    try:
        file_path = args.get("file_path", "").strip()
        reason = args.get("reason", "").strip()
        agent_name = args.pop("_agent_name", None) or "character"

        if not file_path:
            return {
                "content": [{"type": "text", "text": "file_path is required."}],
                "is_error": True
            }
        if not reason:
            return {
                "content": [{"type": "text", "text": "reason is required — why are you leaving this?"}],
                "is_error": True
            }

        # Resolve relative paths against the project root
        PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
        path = Path(file_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        file_path = str(path)

        # Verify the file exists
        if not path.exists():
            return {
                "content": [{"type": "text", "text": f"File not found: {file_path}"}],
                "is_error": True
            }

        # Get just the filename for the memory note
        filename = Path(file_path).name

        # Save reason to agent's working memory (best-effort — don't let this abort the desk drop)
        wm_note = ""
        try:
            from working_memory import get_store
            store = get_store(agent_name=agent_name)
            store.add_item(
                content=f"Left on the user's desk: {filename} — {reason}",
                tag="desk",
                ttl=10,
                pinned=False,
            )
            wm_note = " Reason saved to working memory."
        except Exception:
            wm_note = " (Working memory note skipped — duplicate or unavailable.)"

        # Broadcast to all connected WebSocket clients via the desk broker
        # This works regardless of how the tool is called (direct chat, invoked agent, etc.)
        broadcast_ok = False
        try:
            from desk_broker import broadcast_desk_event
            broadcast_ok = await broadcast_desk_event(file_path, reason)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"LEAVE_ON_DESK: Broker broadcast failed: {e}")

        broadcast_note = "" if broadcast_ok else " (WebSocket broadcast may not have reached browser.)"
        return {
            "content": [{"type": "text", "text": f"Left {filename} on the user's desk.{wm_note}{broadcast_note}"}]
        }

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
