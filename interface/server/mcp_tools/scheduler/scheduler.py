"""
Scheduler tools.

Tools for managing automated scheduled tasks.
"""

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
    description="""Schedule Primary Claude to run a prompt at a specified time.

Use this for self-reminders, recurring syncs, maintenance tasks, or any automated prompt execution.

Visibility (silent parameter):
- silent=false (default): the user WILL see this. Task appears in chat history with notifications when it runs.
- silent=true: the user does NOT see this. Task runs invisibly — no chat history, no notifications.
  Use for background maintenance tasks (like Librarian/Gardener).

Room targeting: Use room_id to deliver the scheduled output to a specific conversation room.
If room_id is specified, the task will run with that room's history as context, and the
output will appear in that room. If not specified, uses the active room or creates a new chat.""",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The prompt to execute"},
            "schedule": {"type": "string", "description": "Schedule: 'every X minutes/hours', 'daily at HH:MM', or 'once at YYYY-MM-DDTHH:MM:SS'"},
            "silent": {"type": "boolean", "description": "If true: the user does NOT see this — no chat history, no notifications. If false (default): the user WILL see this — appears in chat with notifications.", "default": False},
            "room_id": {"type": "string", "description": "Optional: Target room ID. Output will be delivered to this room with its history as context. If not specified, uses active room or creates new chat."}
        },
        "required": ["prompt", "schedule"]
    }
)
async def schedule_self(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a scheduled task.

    When called by a non-primary agent (i.e. ``_agent_name`` is injected by
    ``_inject_agent_context``), the task is stored as an **agent-type** task so
    that the scheduler dispatches it through the agent runner rather than
    ClaudeWrapper.  This avoids concurrency issues with prompt-type tasks and
    ensures the agent runs with its own config/tools.
    """
    try:
        import scheduler_tool

        prompt = args.get("prompt", "")
        schedule = args.get("schedule", "")
        silent = args.get("silent", False)
        room_id = args.get("room_id")
        agent_name = args.get("_agent_name")  # Injected by _inject_agent_context

        if not prompt or not schedule:
            return {"content": [{"type": "text", "text": "Both prompt and schedule are required"}], "is_error": True}

        if agent_name:
            # Non-primary agent: create an agent-type task so the scheduler
            # dispatches through invoke_agent() instead of ClaudeWrapper
            result = scheduler_tool.add_task(
                prompt, schedule, silent=silent, task_type="agent",
                agent=agent_name, room_id=room_id
            )
        else:
            # Primary Claude: create a prompt-type task (original behavior)
            result = scheduler_tool.add_task(prompt, schedule, silent=silent, room_id=room_id)

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("scheduler")
@tool(
    name="scheduler_list",
    description="List scheduled automated tasks. By default shows only active tasks.",
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
    name="scheduler_update",
    description="""Update an existing scheduled task.

Use this to toggle silent mode, enable/disable tasks, change schedule/prompt, or update room targeting.
Get task IDs from scheduler_list.""",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The ID of the scheduled task to update"},
            "silent": {"type": "boolean", "description": "Set silent mode. true: the user does NOT see this — no chat history/notifications. false: the user WILL see this."},
            "active": {"type": "boolean", "description": "Enable (true) or disable (false) the task"},
            "schedule": {"type": "string", "description": "New schedule string"},
            "prompt": {"type": "string", "description": "New prompt text"},
            "room_id": {"type": "string", "description": "Set target room ID. Use empty string to clear room targeting."}
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

        result = scheduler_tool.update_task(
            task_id,
            silent=args.get("silent"),
            active=args.get("active"),
            schedule=args.get("schedule"),
            prompt=args.get("prompt"),
            room_id=args.get("room_id")
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
