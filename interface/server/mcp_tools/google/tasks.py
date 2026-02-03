"""
Google Tasks and Calendar tools.

Tools for managing Google Tasks and Calendar events.
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


@register_tool("google")
@tool(
    name="google_create_tasks_and_events",
    description="""Create Google Tasks and/or Calendar events.

For tasks, provide objects with: title (required), notes (optional), due (optional, format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
For events, provide objects with: summary (required), description (optional), start (required), end (required)
  - All-day events: use YYYY-MM-DD format for start/end
  - Timed events: use ISO format like 2026-01-24T10:00:00 for start/end (timezone: America/Chicago)""",
    input_schema={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "List of task objects",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Task title"},
                        "notes": {"type": "string", "description": "Task notes/details"},
                        "due": {"type": "string", "description": "Due date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)"}
                    },
                    "required": ["title"]
                }
            },
            "events": {
                "type": "array",
                "description": "List of calendar event objects",
                "items": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Event title"},
                        "description": {"type": "string", "description": "Event description"},
                        "start": {"type": "string", "description": "Start time (YYYY-MM-DD for all-day, or ISO datetime)"},
                        "end": {"type": "string", "description": "End time (YYYY-MM-DD for all-day, or ISO datetime)"}
                    },
                    "required": ["summary", "start", "end"]
                }
            }
        }
    }
)
async def google_create(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create tasks and events in Google."""
    try:
        import google_tools

        tasks = args.get("tasks", [])
        events = args.get("events", [])

        if not tasks and not events:
            return {"content": [{"type": "text", "text": "No tasks or events provided"}]}

        # Authenticate
        creds = google_tools.authenticate()

        results = []

        # Create tasks
        if tasks:
            from googleapiclient.discovery import build
            task_service = build("tasks", "v1", credentials=creds)
            task_results = google_tools.create_tasks(task_service, tasks)
            results.append(f"Created {len(task_results)} tasks")

        # Create events
        if events:
            from googleapiclient.discovery import build
            cal_service = build("calendar", "v3", credentials=creds)
            event_results = google_tools.create_events(cal_service, events)
            results.append(f"Created {len(event_results)} events")

        return {"content": [{"type": "text", "text": "\n".join(results) or "Done"}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("google")
@tool(
    name="google_list",
    description="List upcoming Google Tasks and Calendar events.",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum items to return (default 10)", "default": 10}
        }
    }
)
async def google_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List tasks and events from Google."""
    try:
        import google_tools

        limit = args.get("limit", 10)
        creds = google_tools.authenticate()
        data = google_tools.list_items(creds, limit)

        output = []
        for task in data.get("tasks", []):
            output.append(f"TASK: {task['title']} (due: {task.get('due', 'No date')}) [ID: {task['id']}]")
        for event in data.get("events", []):
            output.append(f"EVENT: {event['summary']} @ {event['start']}")

        return {"content": [{"type": "text", "text": "\n".join(output) or "No items found"}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("google")
@tool(
    name="google_delete_task",
    description="""Delete a Google Task by its ID.

Use google_list first to get task IDs.""",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task ID to delete (from google_list)"}
        },
        "required": ["task_id"]
    }
)
async def google_delete_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a task from Google Tasks."""
    try:
        import google_tools
        from googleapiclient.discovery import build

        task_id = args.get("task_id")
        if not task_id:
            return {"content": [{"type": "text", "text": "task_id is required"}], "is_error": True}

        creds = google_tools.authenticate()
        task_service = build("tasks", "v1", credentials=creds)
        result = google_tools.delete_task(task_service, task_id)

        if result["success"]:
            return {"content": [{"type": "text", "text": f"Deleted task: {task_id}"}]}
        else:
            return {"content": [{"type": "text", "text": f"Failed to delete task: {result.get('error')}"}], "is_error": True}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("google")
@tool(
    name="google_update_task",
    description="""Update a Google Task's fields.

Use google_list first to get task IDs. Only provided fields will be updated.""",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task ID to update (from google_list)"},
            "title": {"type": "string", "description": "New title (optional)"},
            "notes": {"type": "string", "description": "New notes/details (optional)"},
            "due": {"type": "string", "description": "New due date in YYYY-MM-DD or ISO format (optional)"},
            "status": {"type": "string", "description": "Task status: 'needsAction' or 'completed' (optional)", "enum": ["needsAction", "completed"]}
        },
        "required": ["task_id"]
    }
)
async def google_update_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update a task in Google Tasks."""
    try:
        import google_tools
        from googleapiclient.discovery import build

        task_id = args.get("task_id")
        if not task_id:
            return {"content": [{"type": "text", "text": "task_id is required"}], "is_error": True}

        creds = google_tools.authenticate()
        task_service = build("tasks", "v1", credentials=creds)

        result = google_tools.update_task(
            task_service,
            task_id,
            title=args.get("title"),
            notes=args.get("notes"),
            due=args.get("due"),
            status=args.get("status")
        )

        if result["success"]:
            task = result["task"]
            return {"content": [{"type": "text", "text": f"Updated task: {task.get('title')} (ID: {task_id})"}]}
        else:
            return {"content": [{"type": "text", "text": f"Failed to update task: {result.get('error')}"}], "is_error": True}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
