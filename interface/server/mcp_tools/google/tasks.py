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
                        "end": {"type": "string", "description": "End time (YYYY-MM-DD for all-day, or ISO datetime)"},
                        "location": {"type": "string", "description": "Event location (optional)"},
                        "recurrence": {
                            "type": "array",
                            "description": "List of RRULE strings for recurring events (e.g. ['RRULE:FREQ=WEEKLY;COUNT=10'])",
                            "items": {"type": "string"}
                        }
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
            created = len(task_results)
            failed = len(tasks) - created
            msg = f"Created {created} task(s)"
            if failed:
                msg += f" ({failed} failed — check due date format: use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)"
            results.append(msg)

        # Create events
        if events:
            from googleapiclient.discovery import build
            cal_service = build("calendar", "v3", credentials=creds)
            event_results = google_tools.create_events(cal_service, events)
            created = len(event_results)
            failed = len(events) - created
            msg = f"Created {created} event(s)"
            if failed:
                msg += f" ({failed} failed — check date format)"
            results.append(msg)

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
            parts = [f"EVENT: {event.get('summary', '(No title)')} @ {event['start']}"]
            if event.get('end'):
                parts.append(f" → {event['end']}")
            if event.get('location'):
                parts.append(f" 📍 {event['location']}")
            parts.append(f" [ID: {event.get('id', 'N/A')}]")
            output.append("".join(parts))

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


@register_tool("google")
@tool(
    name="google_get_event",
    description="""Get a Google Calendar event by its ID.

Use google_list first to get event IDs. Returns full event details including description, location, attendees, and recurrence.""",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The calendar event ID (from google_list)"}
        },
        "required": ["event_id"]
    }
)
async def google_get_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get a single calendar event by ID."""
    try:
        import google_tools
        from googleapiclient.discovery import build

        event_id = args.get("event_id")
        if not event_id:
            return {"content": [{"type": "text", "text": "event_id is required"}], "is_error": True}

        creds = google_tools.authenticate()
        cal_service = build("calendar", "v3", credentials=creds)
        result = google_tools.get_event(cal_service, event_id)

        if result["success"]:
            event = result["event"]
            lines = [
                f"📅 {event['summary']}",
                f"   Start: {event['start']}",
                f"   End: {event['end']}",
            ]
            if event.get('location'):
                lines.append(f"   Location: {event['location']}")
            if event.get('description'):
                lines.append(f"   Description: {event['description']}")
            if event.get('recurrence'):
                lines.append(f"   Recurrence: {', '.join(event['recurrence'])}")
            if event.get('attendees'):
                attendee_list = ', '.join(a.get('email', '?') for a in event['attendees'])
                lines.append(f"   Attendees: {attendee_list}")
            if event.get('htmlLink'):
                lines.append(f"   Link: {event['htmlLink']}")
            lines.append(f"   ID: {event['id']}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        else:
            return {"content": [{"type": "text", "text": f"Failed to get event: {result.get('error')}"}], "is_error": True}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("google")
@tool(
    name="google_update_event",
    description="""Update a Google Calendar event's fields.

Use google_list first to get event IDs. Only provided fields will be updated.""",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The calendar event ID to update (from google_list)"},
            "summary": {"type": "string", "description": "New event title (optional)"},
            "description": {"type": "string", "description": "New event description (optional)"},
            "start": {"type": "string", "description": "New start time - YYYY-MM-DD for all-day or ISO datetime (optional)"},
            "end": {"type": "string", "description": "New end time - YYYY-MM-DD for all-day or ISO datetime (optional)"},
            "location": {"type": "string", "description": "New event location (optional)"},
            "recurrence": {
                "type": "array",
                "description": "New recurrence rules as RRULE strings (optional)",
                "items": {"type": "string"}
            }
        },
        "required": ["event_id"]
    }
)
async def google_update_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update a calendar event in Google Calendar."""
    try:
        import google_tools
        from googleapiclient.discovery import build

        event_id = args.get("event_id")
        if not event_id:
            return {"content": [{"type": "text", "text": "event_id is required"}], "is_error": True}

        creds = google_tools.authenticate()
        cal_service = build("calendar", "v3", credentials=creds)

        result = google_tools.update_event(
            cal_service,
            event_id,
            summary=args.get("summary"),
            description=args.get("description"),
            start=args.get("start"),
            end=args.get("end"),
            location=args.get("location"),
            recurrence=args.get("recurrence")
        )

        if result["success"]:
            event = result["event"]
            return {"content": [{"type": "text", "text": f"Updated event: {event.get('summary')} (ID: {event_id})"}]}
        else:
            return {"content": [{"type": "text", "text": f"Failed to update event: {result.get('error')}"}], "is_error": True}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("google")
@tool(
    name="google_delete_event",
    description="""Delete a Google Calendar event by its ID.

Use google_list first to get event IDs.""",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The calendar event ID to delete (from google_list)"}
        },
        "required": ["event_id"]
    }
)
async def google_delete_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a calendar event from Google Calendar."""
    try:
        import google_tools
        from googleapiclient.discovery import build

        event_id = args.get("event_id")
        if not event_id:
            return {"content": [{"type": "text", "text": "event_id is required"}], "is_error": True}

        creds = google_tools.authenticate()
        cal_service = build("calendar", "v3", credentials=creds)
        result = google_tools.delete_event(cal_service, event_id)

        if result["success"]:
            return {"content": [{"type": "text", "text": f"Deleted event: {event_id}"}]}
        else:
            return {"content": [{"type": "text", "text": f"Failed to delete event: {result.get('error')}"}], "is_error": True}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
