"""
Scheduler tools.

Tools for managing automated scheduled tasks.
"""

import json
import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


@register_tool("scheduler")
@tool(
    name="schedule_self",
    description="""Schedule the current agent to run a prompt at a specified time.

Use this for self-reminders, recurring syncs, maintenance tasks, or any automated prompt execution.

Visibility (silent parameter):
- silent=false (default): the user WILL see this. Task appears in chat history with notifications when it runs.
- silent=true: the user does NOT see this. Task runs invisibly — no chat history, no notifications.
  Use for background maintenance tasks (like Librarian/Gardener).

Room targeting: Use room_id to deliver the scheduled output to a specific conversation room.
If room_id is specified, the task will run with that room's history as context, and the
output will appear in that room. If not specified, uses the active room or creates a new chat.

Pass room_id="current" to target the CURRENT room you're running in (most common case for
self-reminders and follow-ups — e.g. "remind me in this chat in 10 min"). The server will
substitute the actual room ID automatically.""",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The prompt to execute"},
            "schedule": {"type": "string", "description": "Schedule: 'every X minutes/hours', 'daily at HH:MM', or 'once at YYYY-MM-DDTHH:MM:SS'. Offsetless one-time values are America/Chicago wall time; explicit ISO numeric offsets and uppercase Z are exact instants."},
            "silent": {"type": "boolean", "description": "If true: the user does NOT see this — no chat history, no notifications. If false (default): the user WILL see this — appears in chat with notifications.", "default": False},
            "room_id": {"type": "string", "description": "Optional: Target room ID. Pass \"current\" to target the room you're currently running in (auto-resolved to the actual ID). Pass a specific room ID to target that room. Omit to use active room or create new chat."}
        },
        "required": ["prompt", "schedule"]
    }
)
async def schedule_self(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a scheduled task.

    All agents store tasks as **agent-type** tasks so the scheduler dispatches
    them through the agent runner.  The ``_agent_name`` (injected by
    ``_inject_agent_context``) determines which agent config to use;
    defaults to ``character`` if not specified.
    """
    try:
        import scheduler_tool

        prompt = args.get("prompt", "")
        schedule = args.get("schedule", "")
        silent = args.get("silent", False)
        room_id = args.get("room_id")
        agent_name = args.get("_agent_name")  # Injected by _inject_agent_context

        # Resolve "current" sentinel to the actual source chat_id (injected by _inject_chat_context).
        # Allows agents to target the room they're currently running in without knowing its ID.
        if room_id == "current":
            source_chat_id = args.get("_source_chat_id")
            if source_chat_id:
                room_id = source_chat_id
            else:
                # No source chat — fall back to letting the scheduler use active/new room.
                room_id = None

        if not prompt or not schedule:
            return {"content": [{"type": "text", "text": "Both prompt and schedule are required"}], "is_error": True}

        # All agents (including character) use agent-type tasks
        effective_agent = agent_name or "character"
        result = scheduler_tool.add_task(
            prompt, schedule, silent=silent, task_type="agent",
            agent=effective_agent, room_id=room_id
        )

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("scheduler")
@tool(
    name="scheduler_list",
    description="List scheduled task definitions plus recent content-free execution receipts. By default definitions are limited to active tasks, while recent/nonterminal receipts remain visible for inactive one-time firings.",
    input_schema={
        "type": "object",
        "properties": {
            "include_all": {"type": "boolean", "description": "Include inactive/dead tasks (default: false)", "default": False}
        }
    }
)
async def scheduler_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List scheduled tasks."""
    try:
        import scheduler_tool
        include_all = args.get("include_all", False)
        result = scheduler_tool.list_tasks(include_inactive=include_all)
        return {"content": [{"type": "text", "text": result}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("scheduler")
@tool(
    name="scheduler_status",
    description="""Read bounded content-free status for one already-known scheduler identity.

Use task_id for effective definition state plus fixed receipt counts and one latest valid
attempt. Use attempt_id for one exact receipt. Supply both IDs to prove ownership. This
tool never lists unrelated definitions; use scheduler_list only for intentional catalog
browsing.""",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}$",
                "description": "One exact bounded scheduler task ID.",
            },
            "attempt_id": {
                "type": "string",
                "minLength": 36,
                "maxLength": 36,
                "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                "description": "One exact canonical lowercase hyphenated UUID attempt ID.",
            },
        },
        "anyOf": [
            {"required": ["task_id"]},
            {"required": ["attempt_id"]},
        ],
    },
)
async def scheduler_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return canonical JSON from the dedicated exact status projector."""
    fixed_unavailable = {
        "schema": "second_brain.scheduler_status.v1",
        "ok": False,
        "code": "store_unavailable",
        "query": {},
        "task": None,
        "attempts": None,
        "attempt": None,
    }
    try:
        import scheduler_tool

        if not isinstance(args, dict):
            result = scheduler_tool.invalid_exact_status_request()
        elif set(args) - {"task_id", "attempt_id"}:
            result = scheduler_tool.invalid_exact_status_request(
                task_id=args.get("task_id"),
                attempt_id=args.get("attempt_id"),
            )
        else:
            result = scheduler_tool.get_exact_status(
                task_id=args.get("task_id"),
                attempt_id=args.get("attempt_id"),
            )
    except Exception:
        result = fixed_unavailable

    rendered = json.dumps(
        result,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    response = {"content": [{"type": "text", "text": rendered}]}
    if not result.get("ok", False):
        response["is_error"] = True
    return response


@register_tool("scheduler")
@tool(
    name="scheduler_update",
    description="""Update an existing scheduled task.

Use this to toggle silent mode, enable/disable tasks, change schedule/prompt, or update room targeting.
Get task IDs from scheduler_list.

Pass room_id="current" to retarget the task to the room you're currently running in
(auto-resolved to the actual ID).""",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The ID of the scheduled task to update"},
            "silent": {"type": "boolean", "description": "Set silent mode. true: the user does NOT see this — no chat history/notifications. false: the user WILL see this."},
            "active": {"type": "boolean", "description": "Enable (true) or disable (false) the task"},
            "schedule": {"type": "string", "description": "New schedule string. Offsetless 'once at' values are America/Chicago wall time; explicit ISO numeric offsets and uppercase Z are exact instants."},
            "prompt": {"type": "string", "description": "New prompt text"},
            "room_id": {"type": "string", "description": "Set target room ID. Pass \"current\" to target the room you're currently running in (auto-resolved). Use empty string to clear room targeting."}
        },
        "required": ["task_id"]
    }
)
async def scheduler_update(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update a scheduled task."""
    try:
        import scheduler_tool
        task_id = args.get("task_id", "")
        if not task_id:
            return {"content": [{"type": "text", "text": "task_id is required"}], "is_error": True}

        # Resolve "current" sentinel to the actual source chat_id.
        room_id = args.get("room_id")
        if room_id == "current":
            source_chat_id = args.get("_source_chat_id")
            if source_chat_id:
                room_id = source_chat_id
            # If no source chat, leave as "current" — update_task will treat it as a literal
            # room ID string, which is wrong. Better to drop it.
            else:
                room_id = None

        result = scheduler_tool.update_task(
            task_id,
            silent=args.get("silent"),
            active=args.get("active"),
            schedule=args.get("schedule"),
            prompt=args.get("prompt"),
            room_id=room_id
        )
        return {"content": [{"type": "text", "text": result}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("scheduler")
@tool(
    name="scheduler_remove",
    description="Remove a scheduled task by ID.",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The ID of the scheduled task to remove"}
        },
        "required": ["task_id"]
    }
)
async def scheduler_remove(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a scheduled task."""
    try:
        import scheduler_tool
        task_id = args.get("task_id", "")
        if not task_id:
            return {"content": [{"type": "text", "text": "task_id is required"}], "is_error": True}
        result = scheduler_tool.remove_task(task_id)
        return {"content": [{"type": "text", "text": result}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
