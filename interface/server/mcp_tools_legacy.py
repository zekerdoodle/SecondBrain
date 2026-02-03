"""
Custom MCP Tools for Second Brain

Exposes the Python scripts as SDK MCP tools for seamless integration.
"""

import os
import sys
import json
import asyncio
import uuid
from typing import Any, Dict
from claude_agent_sdk import tool, create_sdk_mcp_server

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Import web search tool (Perplexity-based, replaces native WebSearch)
try:
    import web_search_tool
except ImportError:
    web_search_tool = None


# ===== Google Workspace Tools =====

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


# ===== Gmail Tools =====

@tool(
    name="gmail_list_messages",
    description="""List and search Gmail messages.

Use the query parameter for Gmail search syntax:
- is:unread - unread messages
- from:someone@example.com - from a specific sender
- to:me - sent to you
- subject:keyword - subject contains keyword
- has:attachment - has attachments
- after:2026/01/01 - after a date
- label:INBOX - in a specific label

Returns message summaries with id, subject, from, date, snippet, and labels.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Gmail search query (e.g., 'is:unread from:someone@example.com')",
                "default": ""
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum messages to return (default: 20)",
                "default": 20
            },
            "label_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by label IDs (e.g., ['INBOX', 'UNREAD'])"
            }
        }
    }
)
async def gmail_list_messages_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """List Gmail messages."""
    try:
        import google_tools

        query = args.get("query", "")
        max_results = args.get("max_results", 20)
        label_ids = args.get("label_ids")

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        messages = google_tools.gmail_list_messages(
            service,
            query=query,
            max_results=max_results,
            label_ids=label_ids
        )

        if not messages:
            return {"content": [{"type": "text", "text": f"No messages found for query: '{query}'"}]}

        output = [f"## Gmail Messages ({len(messages)} results)\n"]
        for msg in messages:
            labels = ', '.join(msg.get('labelIds', [])[:3])
            output.append(
                f"**ID:** `{msg['id']}`\n"
                f"**From:** {msg['from']}\n"
                f"**Subject:** {msg['subject']}\n"
                f"**Date:** {msg['date']}\n"
                f"**Labels:** {labels}\n"
                f"**Snippet:** {msg['snippet'][:150]}...\n"
            )

        return {"content": [{"type": "text", "text": "\n---\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="gmail_get_message",
    description="""Get the full content of a Gmail message by ID.

Returns complete message with subject, from, to, cc, date, body, and labels.
Use gmail_list_messages first to get message IDs.""",
    input_schema={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "Message ID (from gmail_list_messages)"
            }
        },
        "required": ["message_id"]
    }
)
async def gmail_get_message_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get full message content."""
    try:
        import google_tools

        message_id = args.get("message_id", "").strip()
        if not message_id:
            return {"content": [{"type": "text", "text": "Error: message_id is required"}], "is_error": True}

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        msg = google_tools.gmail_get_message(service, message_id)

        output = f"""## Email Message

**ID:** `{msg['id']}`
**Thread ID:** `{msg['threadId']}`
**From:** {msg['from']}
**To:** {msg['to']}
**CC:** {msg.get('cc', 'None')}
**Subject:** {msg['subject']}
**Date:** {msg['date']}
**Labels:** {', '.join(msg.get('labelIds', []))}

---

{msg['body'][:10000]}{'...[truncated]' if len(msg['body']) > 10000 else ''}
"""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="gmail_send",
    description="""Send an email.

Composes and sends an email immediately. For emails you want to review first, use gmail_draft_create instead.""",
    input_schema={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address(es), comma-separated"
            },
            "subject": {
                "type": "string",
                "description": "Email subject"
            },
            "body": {
                "type": "string",
                "description": "Email body (plain text)"
            },
            "cc": {
                "type": "string",
                "description": "CC recipients, comma-separated (optional)"
            },
            "bcc": {
                "type": "string",
                "description": "BCC recipients, comma-separated (optional)"
            }
        },
        "required": ["to", "subject", "body"]
    }
)
async def gmail_send_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Send an email."""
    try:
        import google_tools

        to = args.get("to", "").strip()
        subject = args.get("subject", "").strip()
        body = args.get("body", "").strip()
        cc = args.get("cc")
        bcc = args.get("bcc")

        if not to or not subject or not body:
            return {"content": [{"type": "text", "text": "Error: to, subject, and body are required"}], "is_error": True}

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        result = google_tools.gmail_send(service, to, subject, body, cc=cc, bcc=bcc)

        return {"content": [{"type": "text", "text": f"Email sent successfully!\nMessage ID: `{result.get('id')}`\nThread ID: `{result.get('threadId')}`"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="gmail_reply",
    description="""Reply to an existing email thread.

Sends a reply to the specified message, keeping it in the same thread.
Use reply_all=true to reply to all recipients.""",
    input_schema={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "ID of the message to reply to"
            },
            "body": {
                "type": "string",
                "description": "Reply body (plain text)"
            },
            "reply_all": {
                "type": "boolean",
                "description": "Reply to all recipients (default: false)",
                "default": False
            }
        },
        "required": ["message_id", "body"]
    }
)
async def gmail_reply_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Reply to an email."""
    try:
        import google_tools

        message_id = args.get("message_id", "").strip()
        body = args.get("body", "").strip()
        reply_all = args.get("reply_all", False)

        if not message_id or not body:
            return {"content": [{"type": "text", "text": "Error: message_id and body are required"}], "is_error": True}

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        result = google_tools.gmail_reply(service, message_id, body, reply_all=reply_all)

        return {"content": [{"type": "text", "text": f"Reply sent successfully!\nMessage ID: `{result.get('id')}`\nThread ID: `{result.get('threadId')}`"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="gmail_list_labels",
    description="""List all Gmail labels.

Returns system labels (INBOX, SENT, TRASH, etc.) and user-created labels.
Label IDs are used with gmail_modify_labels to organize messages.""",
    input_schema={"type": "object", "properties": {}}
)
async def gmail_list_labels_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all labels."""
    try:
        import google_tools

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        labels = google_tools.gmail_list_labels(service)

        output = ["## Gmail Labels\n"]

        # Separate system and user labels
        system = [l for l in labels if l['type'] == 'system']
        user = [l for l in labels if l['type'] == 'user']

        output.append("### System Labels")
        for label in system:
            output.append(f"- **{label['name']}** (ID: `{label['id']}`)")

        if user:
            output.append("\n### User Labels")
            for label in user:
                output.append(f"- **{label['name']}** (ID: `{label['id']}`)")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="gmail_modify_labels",
    description="""Add or remove labels from Gmail messages.

Use this to organize messages by adding/removing labels. Common uses:
- Mark as read: remove_labels=['UNREAD']
- Archive: remove_labels=['INBOX']
- Star: add_labels=['STARRED']
- Move to label: add_labels=['Label_X'], remove_labels=['INBOX']

Get label IDs from gmail_list_labels.""",
    input_schema={
        "type": "object",
        "properties": {
            "message_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Message ID(s) to modify"
            },
            "add_labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Label IDs to add"
            },
            "remove_labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Label IDs to remove"
            }
        },
        "required": ["message_ids"]
    }
)
async def gmail_modify_labels_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Modify labels on messages."""
    try:
        import google_tools

        message_ids = args.get("message_ids", [])
        add_labels = args.get("add_labels", [])
        remove_labels = args.get("remove_labels", [])

        if not message_ids:
            return {"content": [{"type": "text", "text": "Error: message_ids is required"}], "is_error": True}

        if not add_labels and not remove_labels:
            return {"content": [{"type": "text", "text": "Error: Must specify add_labels or remove_labels"}], "is_error": True}

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        result = google_tools.gmail_modify_labels(
            service,
            message_ids,
            add_labels=add_labels,
            remove_labels=remove_labels
        )

        return {"content": [{"type": "text", "text": f"Modified {result['modified']} message(s).\nAdded: {add_labels or 'None'}\nRemoved: {remove_labels or 'None'}"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="gmail_trash",
    description="""Move a message to Gmail trash.

The message can be recovered from trash for 30 days. For permanent deletion, the message would need to be deleted from trash (not supported by this tool for safety).""",
    input_schema={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "Message ID to trash"
            }
        },
        "required": ["message_id"]
    }
)
async def gmail_trash_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Move message to trash."""
    try:
        import google_tools

        message_id = args.get("message_id", "").strip()
        if not message_id:
            return {"content": [{"type": "text", "text": "Error: message_id is required"}], "is_error": True}

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        result = google_tools.gmail_trash(service, message_id)

        return {"content": [{"type": "text", "text": f"Message moved to trash.\nMessage ID: `{result.get('id')}`"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="gmail_draft_create",
    description="""Create a Gmail draft for review before sending.

Creates a draft that appears in Gmail's Drafts folder. The user can review and edit before sending.
Use this when you want human review before the email is sent.""",
    input_schema={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address(es), comma-separated"
            },
            "subject": {
                "type": "string",
                "description": "Email subject"
            },
            "body": {
                "type": "string",
                "description": "Email body (plain text)"
            },
            "cc": {
                "type": "string",
                "description": "CC recipients, comma-separated (optional)"
            },
            "bcc": {
                "type": "string",
                "description": "BCC recipients, comma-separated (optional)"
            }
        },
        "required": ["to", "subject", "body"]
    }
)
async def gmail_draft_create_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a draft email."""
    try:
        import google_tools

        to = args.get("to", "").strip()
        subject = args.get("subject", "").strip()
        body = args.get("body", "").strip()
        cc = args.get("cc")
        bcc = args.get("bcc")

        if not to or not subject or not body:
            return {"content": [{"type": "text", "text": "Error: to, subject, and body are required"}], "is_error": True}

        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        result = google_tools.gmail_create_draft(service, to, subject, body, cc=cc, bcc=bcc)

        return {"content": [{"type": "text", "text": f"Draft created successfully!\nDraft ID: `{result.get('id')}`\n\nThe draft is now in the Gmail Drafts folder for review."}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


# ===== YouTube Music Tools =====

@tool(
    name="ytmusic_get_playlists",
    description="Get user's YouTube Music playlists.",
    input_schema={
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "Maximum playlists to return (default 25)",
                "default": 25
            }
        }
    }
)
async def ytmusic_get_playlists_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get YouTube Music playlists."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        playlists = youtube_tools.get_playlists(service, args.get("max_results", 25))

        return {"content": [{"type": "text", "text": youtube_tools.format_playlist_list(playlists)}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ytmusic_get_playlist_items",
    description="Get songs in a specific playlist.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "Playlist ID"
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum items to return (default 50)",
                "default": 50
            }
        },
        "required": ["playlist_id"]
    }
)
async def ytmusic_get_playlist_items_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get items in a YouTube Music playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        items = youtube_tools.get_playlist_items(service, args["playlist_id"], args.get("max_results", 50))

        return {"content": [{"type": "text", "text": youtube_tools.format_track_list(items)}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ytmusic_get_liked",
    description="Get user's liked songs on YouTube Music.",
    input_schema={
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "Maximum songs to return (default 50)",
                "default": 50
            }
        }
    }
)
async def ytmusic_get_liked_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get liked songs."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        items = youtube_tools.get_liked_music(service, args.get("max_results", 50))

        return {"content": [{"type": "text", "text": youtube_tools.format_track_list(items, "Liked Songs")}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ytmusic_search",
    description="Search for music on YouTube Music.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (song name, artist, etc.)"
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default 10)",
                "default": 10
            }
        },
        "required": ["query"]
    }
)
async def ytmusic_search_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search YouTube Music."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        results = youtube_tools.search_music(service, args["query"], args.get("max_results", 10))

        return {"content": [{"type": "text", "text": youtube_tools.format_search_results(results)}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ytmusic_create_playlist",
    description="Create a new YouTube Music playlist.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Playlist title"
            },
            "description": {
                "type": "string",
                "description": "Playlist description (optional)",
                "default": ""
            },
            "privacy": {
                "type": "string",
                "description": "Privacy: 'private', 'public', or 'unlisted' (default: private)",
                "default": "private"
            }
        },
        "required": ["title"]
    }
)
async def ytmusic_create_playlist_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a YouTube Music playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.create_playlist(
            service,
            args["title"],
            args.get("description", ""),
            args.get("privacy", "private")
        )

        return {"content": [{"type": "text", "text": f"✅ Playlist created!\nTitle: {result['title']}\nID: `{result['id']}`\nURL: {result['url']}"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ytmusic_add_to_playlist",
    description="Add a song to a playlist. Use ytmusic_search to find video_id first.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "Playlist ID to add to"
            },
            "video_id": {
                "type": "string",
                "description": "Video ID of the song to add"
            }
        },
        "required": ["playlist_id", "video_id"]
    }
)
async def ytmusic_add_to_playlist_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a song to a playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.add_to_playlist(service, args["playlist_id"], args["video_id"])

        return {"content": [{"type": "text", "text": f"✅ Song added to playlist!"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ytmusic_remove_from_playlist",
    description="Remove a song from a playlist. Requires the playlist_item_id (not video_id) which you can get from ytmusic_get_playlist_items.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_item_id": {
                "type": "string",
                "description": "The playlist item ID (from ytmusic_get_playlist_items, not the video_id)"
            }
        },
        "required": ["playlist_item_id"]
    }
)
async def ytmusic_remove_from_playlist_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a song from a playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.remove_from_playlist(service, args["playlist_item_id"])

        return {"content": [{"type": "text", "text": f"✅ Song removed from playlist!"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ytmusic_delete_playlist",
    description="Delete a playlist permanently. Use with caution - this cannot be undone.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "ID of the playlist to delete"
            }
        },
        "required": ["playlist_id"]
    }
)
async def ytmusic_delete_playlist_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.delete_playlist(service, args["playlist_id"])

        return {"content": [{"type": "text", "text": f"✅ Playlist deleted!"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


# ===== Scheduler Tools =====

@tool(
    name="schedule_self",
    description="""Schedule Primary Claude to run a prompt at a specified time.

Use this for self-reminders, recurring syncs, maintenance tasks, or any automated prompt execution.

By default, scheduled tasks appear in chat history and notify the user when they run.
Set silent=true for background maintenance tasks (like Librarian/Gardener) that shouldn't
appear in the main chat list or trigger notifications.""",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The prompt to execute"},
            "schedule": {"type": "string", "description": "Schedule: 'every X minutes/hours', 'daily at HH:MM', or 'once at YYYY-MM-DDTHH:MM:SS'"},
            "silent": {"type": "boolean", "description": "If true, task runs silently (no chat history, no notifications). Use for maintenance tasks. Default: false", "default": False}
        },
        "required": ["prompt", "schedule"]
    }
)
async def schedule_self(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a scheduled task."""
    try:
        import scheduler_tool

        prompt = args.get("prompt", "")
        schedule = args.get("schedule", "")
        silent = args.get("silent", False)

        if not prompt or not schedule:
            return {"content": [{"type": "text", "text": "Both prompt and schedule are required"}], "is_error": True}

        result = scheduler_tool.add_task(prompt, schedule, silent=silent)
        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


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


@tool(
    name="scheduler_update",
    description="""Update an existing scheduled task.

Use this to toggle silent mode, enable/disable tasks, or change schedule/prompt.
Get task IDs from scheduler_list.""",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The ID of the scheduled task to update"},
            "silent": {"type": "boolean", "description": "Set silent mode (true = no chat history/notifications)"},
            "active": {"type": "boolean", "description": "Enable (true) or disable (false) the task"},
            "schedule": {"type": "string", "description": "New schedule string"},
            "prompt": {"type": "string", "description": "New prompt text"}
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
            prompt=args.get("prompt")
        )
        return {"content": [{"type": "text", "text": result}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


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


# ===== Critical Notification =====

@tool(
    name="send_critical_notification",
    description="""Send a critical notification that triggers ALL channels: in-app toast, push notification, AND email.

Use this ONLY for genuinely urgent situations:
- Time-sensitive deadlines (interview in 30 min, urgent blocker)
- Critical errors that need immediate attention
- Situations where missing the notification would have real consequences

The bar is HIGH. Normal notifications go through automatically. This is for escalation.

The email will have subject "URGENT: Claude needs your attention" and will be sent to the authenticated Gmail account.""",
    input_schema={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The urgent message content"
            },
            "context": {
                "type": "string",
                "description": "Additional context about why this is urgent (optional)"
            }
        },
        "required": ["message"]
    }
)
async def send_critical_notification(args: Dict[str, Any]) -> Dict[str, Any]:
    """Send a critical notification via all channels including email."""
    try:
        import google_tools
        import sys
        import os

        message = args.get("message", "").strip()
        context = args.get("context", "").strip()

        if not message:
            return {"content": [{"type": "text", "text": "Error: message is required"}], "is_error": True}

        # Get authenticated user's email
        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        # Get user's email from profile
        profile = service.users().getProfile(userId='me').execute()
        user_email = profile.get('emailAddress')

        if not user_email:
            return {"content": [{"type": "text", "text": "Error: Could not determine user email"}], "is_error": True}

        # Compose email body
        full_message = message
        if context:
            full_message += f"\n\nContext: {context}"

        # Send email
        email_result = google_tools.gmail_send(
            service,
            to=user_email,
            subject="URGENT: Claude needs your attention",
            body=f"""This is an urgent notification from Claude.

{full_message}

---
This email was sent because Claude marked this message as critical.
Open Second Brain to respond."""
        )

        # Also send push notification
        try:
            # Import push service from the interface server
            server_path = os.path.dirname(os.path.abspath(__file__))
            if server_path not in sys.path:
                sys.path.insert(0, server_path)
            from push_service import send_push_notification
            import asyncio

            # Run push notification async
            asyncio.create_task(send_push_notification(
                title="URGENT: Claude needs your attention",
                body=message[:100],
                chat_id="",  # Current chat - will be empty for MCP calls
                critical=True
            ))
        except Exception as push_err:
            # Push is optional, don't fail the whole call
            pass

        return {"content": [{"type": "text", "text": f"Critical notification sent!\n- Email sent to: {user_email}\n- Push notification triggered\n\nMessage: {message}"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


# ===== Memory Tools =====

@tool(
    name="memory_append",
    description="""Append to Claude's self-managed journal (memory.md). This is for YOUR OWN reflections and observations - things that wouldn't appear in raw chat history.

USE FOR:
- Self-reflections and introspection ("I notice I keep making this mistake...")
- Opinions and working theories ("I think the root cause is...")
- Blockers and frustrations encountered
- Meta-observations about patterns ("Zeke tends to...")
- Relationship notes and partnership dynamics
- Lessons learned from debugging sessions

DO NOT USE FOR:
- Facts the user stated (LTM captures these automatically from chat)
- Temporary context (use working_memory_add instead)
- Task/project details (LTM handles this)

This is your journal. The Librarian extracts facts from conversations; this is where YOU write what you're thinking.""",
    input_schema={
        "type": "object",
        "properties": {
            "section": {"type": "string", "description": "Section to append to (e.g., 'Self-Reflections', 'Working Theories', 'Relationship Notes', 'Lessons Learned')"},
            "content": {"type": "string", "description": "Your reflection, observation, or note to record"}
        },
        "required": ["content"]
    }
)
async def memory_append(args: Dict[str, Any]) -> Dict[str, Any]:
    """Append to memory.md."""
    try:
        section = args.get("section", "")
        content = args.get("content", "")

        if not content:
            return {"content": [{"type": "text", "text": "content is required"}], "is_error": True}

        memory_path = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory.md")

        if not os.path.exists(memory_path):
            return {"content": [{"type": "text", "text": "memory.md not found"}], "is_error": True}

        with open(memory_path, 'r') as f:
            memory_content = f.read()

        # Find section and append
        if section:
            section_marker = f"## {section}"
            if section_marker in memory_content:
                # Find the end of this section (next ## or end of file)
                start_idx = memory_content.index(section_marker) + len(section_marker)
                next_section = memory_content.find("\n## ", start_idx)
                if next_section == -1:
                    next_section = memory_content.find("\n---", start_idx)

                if next_section != -1:
                    # Insert before next section
                    memory_content = (
                        memory_content[:next_section].rstrip() +
                        f"\n- {content}\n" +
                        memory_content[next_section:]
                    )
                else:
                    # Append at end of file
                    memory_content = memory_content.rstrip() + f"\n- {content}\n"
            else:
                # Section not found, add it
                memory_content = memory_content.rstrip() + f"\n\n## {section}\n\n- {content}\n"
        else:
            # Just append at end
            memory_content = memory_content.rstrip() + f"\n- {content}\n"

        # Update timestamp
        import datetime
        today = datetime.date.today().isoformat()
        if "*Last updated:" in memory_content:
            import re
            memory_content = re.sub(
                r'\*Last updated: .*\*',
                f'*Last updated: {today}*',
                memory_content
            )

        with open(memory_path, 'w') as f:
            f.write(memory_content)

        return {"content": [{"type": "text", "text": f"Added to memory: {content[:50]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="memory_read",
    description="Read Claude's self-managed journal (memory.md). Contains your own reflections, observations, and notes - distinct from LTM which stores facts extracted from conversations.",
    input_schema={"type": "object", "properties": {}}
)
async def memory_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read memory.md."""
    try:
        memory_path = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory.md")

        if not os.path.exists(memory_path):
            return {"content": [{"type": "text", "text": "memory.md not found"}]}

        with open(memory_path, 'r') as f:
            content = f.read()

        return {"content": [{"type": "text", "text": content}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


# ===== Working Memory Tools =====

@tool(
    name="working_memory_add",
    description="""Add a note to working memory. Working memory items are ephemeral notes that:
- Persist across exchanges but auto-expire based on TTL (time-to-live)
- Can be pinned to prevent expiration
- Support deadlines with countdown display
- Are injected into every prompt for context

Use this for:
- Observations you want to track temporarily
- Reminders about ongoing context
- Things to check back on later

TTL is measured in "exchanges" (user message + assistant response = 1 exchange).
Default TTL is 5 exchanges. Max is 10. Pinned items never expire.""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The note content"},
            "tag": {"type": "string", "description": "Optional category tag (e.g., 'reminder', 'observation', 'todo')"},
            "ttl": {"type": "integer", "description": "Time-to-live in exchanges (default: 5, max: 10)"},
            "pinned": {"type": "boolean", "description": "If true, item never auto-expires (max 3 pinned items)"},
            "deadline": {"type": "string", "description": "Optional deadline as ISO timestamp (e.g., '2026-01-25T14:00:00')"},
            "remind_before": {"type": "string", "description": "When to show 'due soon' warning (e.g., '2h', '24h')"}
        },
        "required": ["content"]
    }
)
async def working_memory_add(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a working memory item."""
    try:
        # Import here to avoid circular imports
        sys.path.insert(0, SCRIPTS_DIR)
        from working_memory import get_store, WorkingMemoryError
        from datetime import datetime, timezone

        content = args.get("content", "").strip()
        if not content:
            return {"content": [{"type": "text", "text": "content is required"}], "is_error": True}

        tag = args.get("tag")
        ttl = args.get("ttl")
        pinned = args.get("pinned", False)
        deadline_str = args.get("deadline")
        remind_before = args.get("remind_before")

        # Parse deadline if provided
        deadline_at = None
        if deadline_str:
            try:
                deadline_at = datetime.fromisoformat(deadline_str)
                if deadline_at.tzinfo is None:
                    # Assume local timezone (America/Chicago)
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo("America/Chicago")
                    deadline_at = deadline_at.replace(tzinfo=tz)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid deadline format: {e}"}], "is_error": True}

        store = get_store()
        item = store.add_item(
            content=content,
            tag=tag,
            ttl=ttl,
            pinned=pinned,
            deadline_at=deadline_at,
            remind_before=remind_before,
        )

        status = "pinned" if item.pinned else f"TTL={item.ttl_initial}"
        return {"content": [{"type": "text", "text": f"Added to working memory [{status}]: {content[:80]}..."}]}

    except WorkingMemoryError as e:
        return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="working_memory_update",
    description="""Update an existing working memory item by its display index (1-based).

You can update content, TTL, tag, pinned status, or deadline.""",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to update (1-based, as shown in prompt)"},
            "content": {"type": "string", "description": "New content (replaces existing)"},
            "append": {"type": "string", "description": "Text to append to existing content"},
            "tag": {"type": "string", "description": "New tag (empty string to clear)"},
            "ttl": {"type": "integer", "description": "Reset TTL to this value"},
            "pinned": {"type": "boolean", "description": "Set pinned status"},
            "deadline": {"type": "string", "description": "New deadline as ISO timestamp"},
            "remind_before": {"type": "string", "description": "When to show 'due soon' warning"}
        },
        "required": ["index"]
    }
)
async def working_memory_update(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update a working memory item."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from working_memory import get_store, WorkingMemoryError
        from datetime import datetime

        index = args.get("index")
        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        # Parse deadline if provided
        deadline_at = None
        deadline_str = args.get("deadline")
        if deadline_str:
            try:
                deadline_at = datetime.fromisoformat(deadline_str)
                if deadline_at.tzinfo is None:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo("America/Chicago")
                    deadline_at = deadline_at.replace(tzinfo=tz)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid deadline format: {e}"}], "is_error": True}

        store = get_store()
        item = store.update_item(
            index=index,
            new_content=args.get("content"),
            append=args.get("append"),
            ttl=args.get("ttl"),
            tag=args.get("tag"),
            pinned=args.get("pinned"),
            deadline_at=deadline_at,
            remind_before=args.get("remind_before"),
        )

        return {"content": [{"type": "text", "text": f"Updated item {index}: {item.content[:80]}..."}]}

    except WorkingMemoryError as e:
        return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="working_memory_remove",
    description="Remove a working memory item by its display index (1-based).",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to remove (1-based)"}
        },
        "required": ["index"]
    }
)
async def working_memory_remove(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a working memory item."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from working_memory import get_store, WorkingMemoryError

        index = args.get("index")
        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        store = get_store()
        removed = store.remove_item(index)

        return {"content": [{"type": "text", "text": f"Removed: {removed.content[:80]}..."}]}

    except WorkingMemoryError as e:
        return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="working_memory_list",
    description="List all current working memory items with their status.",
    input_schema={"type": "object", "properties": {}}
)
async def working_memory_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List working memory items."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from working_memory import get_store

        store = get_store()
        items = store.list_items()

        if not items:
            return {"content": [{"type": "text", "text": "Working memory is empty."}]}

        lines = []
        for i, item in enumerate(items, 1):
            status = "[PINNED]" if item.pinned else f"[TTL {item.ttl_remaining}/{item.ttl_initial}]"
            tag = f"[{item.tag}]" if item.tag else ""
            lines.append(f"{i}. {status} {tag} {item.content[:100]}...")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="working_memory_snapshot",
    description="""Promote a working memory item to permanent storage (memory.md).

This "snapshots" an ephemeral working memory note into the permanent self-journal.
Use this when a temporary observation or note becomes important enough to persist.

The item will be added to the specified section in memory.md. By default, the
original working memory item is removed after promotion (set keep=true to retain it).""",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to promote (1-based)"},
            "section": {
                "type": "string",
                "description": "Section in memory.md (e.g., 'Self-Reflections', 'Working Theories', 'Lessons Learned')",
                "default": "Promoted from Working Memory"
            },
            "keep": {
                "type": "boolean",
                "description": "If true, keep the item in working memory after promotion (default: false)",
                "default": False
            },
            "note": {
                "type": "string",
                "description": "Optional note to append to the content when saving"
            }
        },
        "required": ["index"]
    }
)
async def working_memory_snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """Promote working memory item to memory.md."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from working_memory import get_store, WorkingMemoryError
        import datetime
        import re

        index = args.get("index")
        section = args.get("section", "Promoted from Working Memory")
        keep = args.get("keep", False)
        note = args.get("note", "")

        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        # Get the working memory item
        store = get_store()
        items = store.list_items()

        if not items:
            return {"content": [{"type": "text", "text": "Working memory is empty."}], "is_error": True}

        if index > len(items):
            return {"content": [{"type": "text", "text": f"No item at index {index}. Valid: 1-{len(items)}"}], "is_error": True}

        item = items[index - 1]
        content = item.content

        # Add note if provided
        if note:
            content = f"{content} — {note}"

        # Add tag context if present
        if item.tag:
            content = f"[{item.tag}] {content}"

        # Now append to memory.md
        memory_path = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory.md")

        if not os.path.exists(memory_path):
            return {"content": [{"type": "text", "text": "memory.md not found"}], "is_error": True}

        with open(memory_path, 'r') as f:
            memory_content = f.read()

        # Find section and append (same logic as memory_append)
        section_marker = f"## {section}"
        if section_marker in memory_content:
            # Find the end of this section (next ## or end of file)
            start_idx = memory_content.index(section_marker) + len(section_marker)
            next_section = memory_content.find("\n## ", start_idx)
            if next_section == -1:
                next_section = memory_content.find("\n---", start_idx)

            if next_section != -1:
                # Insert before next section
                memory_content = (
                    memory_content[:next_section].rstrip() +
                    f"\n- {content}\n" +
                    memory_content[next_section:]
                )
            else:
                # Append at end of file
                memory_content = memory_content.rstrip() + f"\n- {content}\n"
        else:
            # Section not found, add it
            memory_content = memory_content.rstrip() + f"\n\n## {section}\n\n- {content}\n"

        # Update timestamp
        today = datetime.date.today().isoformat()
        if "*Last updated:" in memory_content:
            memory_content = re.sub(
                r'\*Last updated: .*\*',
                f'*Last updated: {today}*',
                memory_content
            )

        with open(memory_path, 'w') as f:
            f.write(memory_content)

        result = f"Promoted to memory.md [{section}]: {content[:80]}..."

        # Remove from working memory unless keep=true
        if not keep:
            store.remove_item(index)
            result += "\nRemoved from working memory."
        else:
            result += "\nKept in working memory."

        return {"content": [{"type": "text", "text": result}]}

    except WorkingMemoryError as e:
        return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


# ===== Page Parser Tool =====

@tool(
    name="page_parser",
    description="""Fetch and parse web pages into clean Markdown.

Use this to extract readable content from URLs. Supports:
- HTML pages (uses Readability + markdownify for clean extraction)
- PDF documents (extracts text)
- Multiple URLs in one call

The tool:
1. Fetches the URL with proper headers and retries
2. Extracts main content (removes nav, ads, boilerplate)
3. Converts to clean Markdown with metadata header
4. Optionally saves to docs/webresults for future reference

Content is truncated at ~50k chars by default. Truncated/saved pages are stored for full retrieval later.""",
    input_schema={
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "URL(s) to fetch and parse"
            },
            "save": {
                "type": "boolean",
                "description": "Save parsed content to docs/webresults (default: false, but always saves if truncated)",
                "default": False
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters per page (default: 50000)",
                "default": 50000
            }
        },
        "required": ["urls"]
    }
)
async def page_parser_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Parse web pages into clean Markdown."""
    try:
        import page_parser as pp

        urls = args.get("urls", [])
        save = args.get("save", False)
        max_chars = args.get("max_chars", 50000)

        if not urls:
            return {"content": [{"type": "text", "text": "No URLs provided"}], "is_error": True}

        # Parse all URLs
        results = []
        for url in urls:
            result = pp.page_parser_single(url, save=save, max_chars=max_chars)
            if result["success"]:
                results.append(result["content"])
            else:
                results.append(f"Error parsing {url}: {result.get('error', 'Unknown error')}")

        combined = "\n\n---\n\n".join(results)
        return {"content": [{"type": "text", "text": combined}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


# ===== Restart Tool =====

@tool(
    name="restart_server",
    description="""Restart the Second Brain server to apply changes. Use this when you've made changes that require a server restart (e.g., modified server code, updated MCP tools, changed configurations).

Two modes available:
- **Quick restart** (default): Only restarts the Python server. Fast (~5 seconds).
- **Full restart with rebuild**: Rebuilds the frontend first, then restarts. Use when frontend code changed.

IMPORTANT: This tool will:
1. Save the current conversation state
2. Stop the server gracefully
3. Optionally rebuild the frontend (if rebuild=true)
4. Restart the server with your changes applied
5. Automatically continue this conversation after restart

You will receive a system message after restart confirming it worked. Use this to verify your changes.""",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Current session ID to continue after restart (auto-detected if not provided)"},
            "reason": {"type": "string", "description": "Why you're restarting (for logs)"},
            "rebuild": {"type": "boolean", "description": "If true, rebuild frontend before restart. Use when frontend code changed. Default: false (quick restart).", "default": False},
            "pending_messages": {"type": "array", "description": "Messages not yet saved (will be preserved)", "items": {"type": "object"}}
        }
    }
)
async def restart_server(args: Dict[str, Any]) -> Dict[str, Any]:
    """Restart the server with conversation continuity."""
    try:
        session_id = args.get("session_id")
        reason = args.get("reason", "Server restart requested")
        rebuild = args.get("rebuild", False)

        # Import tools
        import restart_tool as rt
        import subprocess
        import sys

        # Access the active conversations from the main server module
        main_module = sys.modules.get('main') or sys.modules.get('__main__')
        active_convs = {}
        chat_manager = None
        current_session = None
        if main_module:
            active_convs = getattr(main_module, 'active_conversations', {})
            chat_manager = getattr(main_module, 'chat_manager', None)
            current_session = getattr(main_module, 'current_processing_session', None)

        # Use the currently processing session (set by handle_message)
        # This is the most reliable way to know which chat we're in
        if current_session:
            session_id = current_session
            print(f"[restart_server] Using current processing session: {session_id}")

        if not session_id:
            return {
                "content": [{"type": "text", "text": "Error: Could not determine session_id. No active conversations found."}],
                "is_error": True
            }

        # IMPORTANT: Save the current conversation state from memory BEFORE scheduling restart
        # This ensures we don't lose messages that haven't been saved to disk yet
        try:
            if session_id in active_convs and chat_manager:
                conv = active_convs[session_id]
                messages_to_save = conv.messages.copy()

                # Include any pending (in-progress) assistant response
                # This captures what Claude was saying before calling the restart tool
                if hasattr(conv, 'pending_response') and conv.pending_response:
                    for segment in conv.pending_response:
                        if segment and segment.strip():
                            messages_to_save.append({
                                "id": str(uuid.uuid4()),
                                "role": "assistant",
                                "content": segment.strip()
                            })
                    print(f"[restart_server] Including {len(conv.pending_response)} pending response segments")

                if messages_to_save:
                    # Save conversation to disk NOW
                    existing = chat_manager.load_chat(session_id)
                    title = existing.get("title", "Untitled") if existing else "Untitled"
                    chat_manager.save_chat(session_id, {
                        "title": title,
                        "sessionId": session_id,
                        "messages": messages_to_save
                    })
                    print(f"[restart_server] Saved {len(messages_to_save)} messages before restart")
        except Exception as e:
            print(f"[restart_server] Warning: Could not save conversation state: {e}")

        # Save the continuation marker
        continuation = rt.save_continuation_state(session_id, reason)

        # Choose restart script based on rebuild flag
        if rebuild:
            # Full restart with frontend rebuild
            restart_script = rt.SECOND_BRAIN_ROOT / "interface" / "restart-server-full.sh"
            restart_type = "full (with frontend rebuild)"
            wait_time = 30  # Frontend build takes longer
        else:
            # Quick restart - server only
            restart_script = rt.QUICK_RESTART_SCRIPT
            restart_type = "quick (server only)"
            wait_time = 5

        log_file = rt.CLAUDE_DIR / "server_restart.log"

        # Schedule the restart using a DETACHED subprocess
        # A thread won't work because stop_server() kills the process the thread runs in
        # Using bash with sleep ensures the restart happens even after this process dies
        # This subprocess will:
        # 1. Sleep briefly (enough time for Claude to finish and handle_message to save)
        # 2. Run the restart script (which kills old server and starts new one)
        subprocess.Popen(
            f"sleep 3 && bash {restart_script} > {log_file} 2>&1",
            shell=True,
            start_new_session=True,  # Detach from parent process
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Restart initiated for session {session_id}.\n"
                    f"Reason: {reason}\n"
                    f"Mode: {restart_type}\n"
                    f"The server will restart in ~{wait_time} seconds.\n"
                    f"After restart, you'll receive a continuation message."
                )
            }]
        }

    except Exception as e:
        import traceback
        return {
            "content": [{
                "type": "text",
                "text": f"Error initiating restart: {str(e)}\n{traceback.format_exc()}"
            }],
            "is_error": True
        }


# ===== Computer Use Tools (RETIRED 2026-01-24) =====
# CUA code has been archived to: .99_Archive/cua_retired_2026-01-24/
# To restore, check git history or the archive directory.
# Reason: CUA functionality retired from active use.

# [ARCHIVED CODE REMOVED - ~760 lines]
# The following tools were removed:
#   - ComputerTool class (xdotool/scrot wrapper)
#   - screen_capture, mouse_click, type_text, key_press
#   - mouse_move, get_windows, focus_window, scroll
#   - cua_subagent (CUA loop runner)
#   - _execute_cua (internal CUA execution)


# === END ARCHIVED CUA SECTION ===


# ===== Claude Code Subagent Tool =====

@tool(
    name="claude_code",
    description="""Delegate coding tasks to Claude Code, a specialized coding agent.

IMPORTANT USAGE GUIDELINES FOR CLAUDE (the calling agent):

1. **Natural Language Only**: Provide descriptive, plain-language prompts explaining:
   - What you want to achieve (the goal)
   - Relevant context and constraints
   - Documentation or examples if helpful

   DO NOT provide:
   - Specific file paths to edit (let Claude Code discover them)
   - Exact code changes or diffs
   - Step-by-step implementation instructions

2. **Good Prompt Examples**:
   - "Add a new MCP tool called 'weather_check' that fetches weather data from OpenWeatherMap API"
   - "Fix the bug where scheduler tasks aren't persisting after server restart"
   - "Refactor the memory system to use async/await consistently"

3. **Bad Prompt Examples** (too prescriptive):
   - "Edit mcp_tools.py line 450, change X to Y"
   - "Add this exact function: def foo(): ..."

4. **Verification**: After Claude Code completes, you (the main agent) can:
   - Call `restart_server` to apply changes
   - Read modified files to verify the implementation
   - Run tests or commands to validate behavior

5. **Your Capabilities**: You have `restart_server` tool to restart the server after code changes.
   Claude Code will make the changes; you handle verification and server lifecycle.

This tool runs Claude Code with full permissions in your environment.""",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Natural language description of the coding task. Be descriptive about goals, not prescriptive about implementation."
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Maximum execution time in seconds (default: 600 = 10 minutes)",
                "default": 600
            },
            "model": {
                "type": "string",
                "description": "Model to use: 'opus' (default, more capable) or 'sonnet' (faster)",
                "enum": ["sonnet", "opus"],
                "default": "opus"
            }
        },
        "required": ["prompt"]
    }
)
async def claude_code_exec(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a coding task using Claude Code CLI as a subagent.

    Runs Claude Code in headless mode with full permissions to implement
    code changes autonomously.
    """
    prompt = args.get("prompt", "")
    timeout_seconds = args.get("timeout_seconds", 600)
    model = args.get("model", "opus")

    if not prompt:
        return {"content": [{"type": "text", "text": "Error: prompt is required"}], "is_error": True}

    # System prompt explaining Claude Code's role as a subagent
    # NOTE: We use --setting-sources user to skip project CLAUDE.md, so Claude Code
    # doesn't get confused by the primary agent's instructions
    system_prompt = """IMPORTANT: This message was sent by the primary Claude agent (using AgentSDK), not a human user.

You are Claude Code, operating as a coding subagent. Sessions are not persistent - each invocation is independent.

CONTEXT:
- You have full read/write access to the Second Brain codebase at /home/debian/second_brain
- The primary Claude agent delegated this coding task to you
- After you complete your work, the primary agent will verify and may restart the server
- You can be technical in your responses - the primary agent understands code

YOUR ROLE:
- Focus on implementing the requested changes cleanly
- Read existing code to understand patterns before making changes
- Make minimal, focused changes that accomplish the goal
- Report what you changed and any important notes

The primary agent handles:
- Server restarts (via restart_server tool)
- Verification of your changes
- Follow-up adjustments if needed

Do your best work. Be thorough but concise in your final summary."""

    # Build command - use --print for final result only, --output-format text for readable output
    # Note: No --cwd flag exists; working directory is set via subprocess cwd parameter
    # Use the bundled claude binary from claude-agent-sdk (not in PATH)
    # IMPORTANT: --setting-sources user skips project CLAUDE.md to prevent the primary
    # agent's instructions from being injected into Claude Code sessions
    claude_bin = "/home/debian/second_brain/interface/server/venv/lib/python3.11/site-packages/claude_agent_sdk/_bundled/claude"
    cmd = [
        claude_bin,
        "--print",
        "--dangerously-skip-permissions",
        "--model", model,
        "--setting-sources", "user",  # Skip project CLAUDE.md - it's for the primary agent
        "--append-system-prompt", system_prompt,  # Append to keep Claude Code's defaults
        "--output-format", "text",  # Use text format for cleaner output
        prompt
    ]

    # Working directory for the subprocess
    working_dir = "/home/debian/second_brain"

    try:
        print(f"[claude_code] Starting Claude Code with prompt: {prompt[:100]}...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,  # Set working directory here, not via CLI flag
            env={**os.environ}
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds
        )

        stdout_text = stdout.decode().strip()
        stderr_text = stderr.decode().strip()
        return_code = proc.returncode

        print(f"[claude_code] Completed with return code {return_code}")
        print(f"[claude_code] stdout length: {len(stdout_text)}, stderr length: {len(stderr_text)}")

        # Build result based on what we got
        result_parts = []

        # Check for errors first
        if return_code != 0:
            result_parts.append(f"**Claude Code exited with error (code {return_code})**\n")
            if stderr_text:
                result_parts.append(f"**stderr:**\n{stderr_text}\n")
            if stdout_text:
                result_parts.append(f"**stdout:**\n{stdout_text}")

            final_result = "\n".join(result_parts) if result_parts else f"Claude Code failed with exit code {return_code} but produced no output."
            return {"content": [{"type": "text", "text": final_result}], "is_error": True}

        # Success case - return stdout (the final result from --print mode)
        if stdout_text:
            return {"content": [{"type": "text", "text": stdout_text}]}

        # No stdout but process succeeded - report this clearly
        if stderr_text:
            # Sometimes useful output goes to stderr
            return {"content": [{"type": "text", "text": f"Claude Code completed but wrote to stderr:\n{stderr_text}"}]}

        # Truly empty output
        return {"content": [{"type": "text", "text": "Claude Code completed successfully but produced no output. The task may have been a no-op or Claude may have determined no changes were needed."}]}

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except:
            pass
        return {
            "content": [{"type": "text", "text": f"Claude Code timed out after {timeout_seconds} seconds. The task may be too complex - try breaking it into smaller steps."}],
            "is_error": True
        }
    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Claude Code error: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


# ===== Long-Term Memory Tools =====

# Add LTM scripts to path
LTM_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, "ltm"))
if LTM_DIR not in sys.path:
    sys.path.insert(0, LTM_DIR)


@tool(
    name="ltm_search",
    description="""Search long-term memory for relevant facts and context.

This searches the semantic memory store (atomic memories and threads) using embeddings.
Use this to find information that was previously learned about the user, their preferences,
past conversations, or any other stored knowledge.

Returns both individual facts and organized threads of related information.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query - what you're looking for"},
            "k": {"type": "integer", "description": "Number of results (default: 10)", "default": 10},
            "include_threads": {"type": "boolean", "description": "Include thread results (default: true)", "default": True}
        },
        "required": ["query"]
    }
)
async def ltm_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search long-term memory."""
    try:
        from ltm.memory_retrieval import search_memories

        query = args.get("query", "")
        k = args.get("k", 10)
        include_threads = args.get("include_threads", True)

        if not query:
            return {"content": [{"type": "text", "text": "Query is required"}], "is_error": True}

        results = search_memories(query, k=k, include_threads=include_threads)

        if not results:
            return {"content": [{"type": "text", "text": f"No memories found for: {query}"}]}

        output = [f"## Memory Search Results for: {query}\n"]
        for r in results:
            if r["type"] == "atomic":
                output.append(
                    f"**[Memory]** (importance: {r['importance']}, score: {r['score']:.2f})\n"
                    f"{r['content']}\n"
                )
            else:
                output.append(
                    f"**[Thread: {r['name']}]** ({r['memory_count']} memories, score: {r['score']:.2f})\n"
                    f"{r['description']}\n"
                )

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ltm_get_context",
    description="""Get relevant memory context for a query, formatted for prompt injection.

This is the main way to retrieve memory context for a conversation. It uses a thread-first
strategy: finds relevant threads, includes whole threads (to preserve context), and fills
remaining budget with individual relevant facts.

Guaranteed memories (importance=100) are always included regardless of budget.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Context query (usually the user's message)"},
            "token_budget": {"type": "integer", "description": "Max tokens for memory context (default: 4000)", "default": 4000}
        },
        "required": ["query"]
    }
)
async def ltm_get_context(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get memory context for prompt injection."""
    try:
        from ltm.memory_retrieval import get_memory_context

        query = args.get("query", "")
        token_budget = args.get("token_budget", 4000)

        if not query:
            return {"content": [{"type": "text", "text": "Query is required"}], "is_error": True}

        context = get_memory_context(query, token_budget=token_budget)
        formatted = context.format_for_prompt()

        if not formatted:
            return {"content": [{"type": "text", "text": "No relevant memory context found."}]}

        stats = (
            f"\n\n---\n*Memory stats: {len(context.threads)} threads, "
            f"{len(context.atomic_memories)} facts, {len(context.guaranteed_memories)} guaranteed, "
            f"~{context.total_tokens} tokens*"
        )

        return {"content": [{"type": "text", "text": formatted + stats}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ltm_add_memory",
    description="""Add a new fact or insight to long-term memory.

Use this to explicitly save something important that should be remembered long-term.
The memory will be embedded for semantic search and can be organized into threads.

Importance scale:
- 100: Core context, ALWAYS included (use sparingly)
- 80-99: Very important
- 50-79: Useful information
- 20-49: Minor details""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact or insight to remember"},
            "importance": {"type": "integer", "description": "Importance 0-100 (default: 50)", "default": 50},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for categorization"
            },
            "thread_name": {"type": "string", "description": "Optional: add to this thread (creates if doesn't exist)"}
        },
        "required": ["content"]
    }
)
async def ltm_add_memory(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a memory to long-term storage."""
    try:
        from ltm.atomic_memory import get_atomic_manager
        from ltm.thread_memory import get_thread_manager

        content = args.get("content", "").strip()
        importance = args.get("importance", 50)
        tags = args.get("tags", [])
        thread_name = args.get("thread_name")

        if not content:
            return {"content": [{"type": "text", "text": "Content is required"}], "is_error": True}

        atom_mgr = get_atomic_manager()

        # Check for duplicates
        existing = atom_mgr.find_similar(content, threshold=0.88)
        if existing:
            return {"content": [{"type": "text", "text": f"Similar memory already exists (ID: {existing.id}): {existing.content[:100]}..."}]}

        # Create the memory
        atom = atom_mgr.create(
            content=content,
            importance=importance,
            tags=tags
        )

        result = f"Created memory (ID: {atom.id}, importance: {importance})"

        # Add to thread if specified
        if thread_name:
            thread_mgr = get_thread_manager()
            thread = thread_mgr.find_or_create_thread(thread_name, f"Thread for {thread_name}")
            thread_mgr.add_memory_to_thread(thread.id, atom.id)
            result += f"\nAdded to thread: {thread_name}"

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ltm_create_thread",
    description="""Create a new thread to organize related memories.

Threads are like playlists - they group related facts together for better context retrieval.
When a thread is retrieved, ALL its memories come together.""",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Thread name"},
            "description": {"type": "string", "description": "What this thread is about"},
            "memory_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: memory IDs to add initially"
            }
        },
        "required": ["name", "description"]
    }
)
async def ltm_create_thread(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new thread."""
    try:
        from ltm.thread_memory import get_thread_manager

        name = args.get("name", "").strip()
        description = args.get("description", "").strip()
        memory_ids = args.get("memory_ids", [])

        if not name or not description:
            return {"content": [{"type": "text", "text": "Name and description are required"}], "is_error": True}

        thread_mgr = get_thread_manager()

        # Check if exists
        existing = thread_mgr.get_by_name(name)
        if existing:
            return {"content": [{"type": "text", "text": f"Thread '{name}' already exists (ID: {existing.id})"}]}

        thread = thread_mgr.create(name=name, description=description, memory_ids=memory_ids)

        return {"content": [{"type": "text", "text": f"Created thread '{name}' (ID: {thread.id}) with {len(memory_ids)} memories"}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="ltm_stats",
    description="""Get statistics about the long-term memory system.

Shows counts, distributions, and health metrics for the memory store.""",
    input_schema={"type": "object", "properties": {}}
)
async def ltm_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get memory system statistics."""
    try:
        from ltm.atomic_memory import get_atomic_manager
        from ltm.thread_memory import get_thread_manager
        from ltm.embeddings import get_embedding_manager
        from ltm.memory_throttle import get_buffer_stats

        atom_mgr = get_atomic_manager()
        thread_mgr = get_thread_manager()
        emb_mgr = get_embedding_manager()

        atom_stats = atom_mgr.stats()
        thread_stats = thread_mgr.stats()
        emb_stats = emb_mgr.stats()
        buffer_stats = get_buffer_stats()

        output = f"""## Long-Term Memory Statistics

### Atomic Memories
- Total: {atom_stats['total_memories']}
- Guaranteed (importance=100): {atom_stats['guaranteed_count']}
- Tags: {', '.join(atom_stats['tags'][:10]) or 'None'}

### Threads
- Total: {thread_stats['total_threads']}
- Empty: {thread_stats['empty_threads']}
- Avg memories per thread: {thread_stats['avg_memories_per_thread']:.1f}

### Embeddings
- Total vectors: {emb_stats['total_embeddings']}
- Cache size: {emb_stats['cache_size']}

### Processing Buffer
- Pending exchanges: {buffer_stats['buffer_size']}
- Can run Librarian now: {buffer_stats['can_run_now']}
- Minutes until next run: {buffer_stats['minutes_until_next_run']}
- Total Librarian runs: {buffer_stats['total_librarian_runs']}
- Total exchanges processed: {buffer_stats['total_exchanges_processed']}
"""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ltm_process_now",
    description="""Manually trigger the Librarian to process pending exchanges.

Normally the Librarian runs automatically every 20 minutes if there are pending exchanges.
Use this to force immediate processing.""",
    input_schema={"type": "object", "properties": {}}
)
async def ltm_process_now(args: Dict[str, Any]) -> Dict[str, Any]:
    """Force Librarian to run now."""
    try:
        from ltm.memory_throttle import force_librarian_ready, get_buffer_stats
        from ltm.librarian_agent import run_librarian_cycle

        # Check buffer first
        stats = get_buffer_stats()
        if stats["buffer_size"] == 0:
            return {"content": [{"type": "text", "text": "No exchanges in buffer to process."}]}

        # Force ready and run
        force_librarian_ready()
        result = await run_librarian_cycle()

        output = f"""## Librarian Run Complete

- Status: {result.get('status')}
- Exchanges processed: {result.get('exchanges_processed', 0)}
- Memories created: {result.get('memories_created', 0)}
- Duplicates skipped: {result.get('memories_skipped_duplicate', 0)}
- Threads created: {result.get('threads_created', 0)}
- Threads updated: {result.get('threads_updated', 0)}
"""
        if result.get('errors'):
            output += f"\nErrors: {result['errors']}"

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ltm_run_gardener",
    description="""Run the Gardener for memory maintenance.

The Gardener analyzes the memory store and suggests:
- Duplicate memories to merge
- Threads to consolidate
- Importance adjustments
- Stale content to review

By default, only non-destructive changes (importance adjustments) are auto-applied.
Destructive operations are logged for manual review.""",
    input_schema={
        "type": "object",
        "properties": {
            "auto_apply": {"type": "boolean", "description": "Auto-apply safe changes (default: false)", "default": False}
        }
    }
)
async def ltm_run_gardener(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run Gardener maintenance."""
    try:
        from ltm.gardener_agent import run_gardener_cycle

        auto_apply = args.get("auto_apply", False)
        result = await run_gardener_cycle(auto_apply=auto_apply)

        output = f"""## Gardener Run Complete

- Status: {result.get('status')}
- Memories analyzed: {result.get('memories_analyzed', 0)}
- Threads analyzed: {result.get('threads_analyzed', 0)}
- Importance adjusted: {result.get('importance_adjusted', 0)}
- Marked stale: {result.get('marked_stale', 0)}
- Skipped (manual review needed): {result.get('skipped', 0)}

### Summary
{result.get('summary', 'No summary')}
"""
        if result.get('errors'):
            output += f"\nErrors: {result['errors']}"

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@tool(
    name="ltm_buffer_exchange",
    description="""Add a conversation exchange to the processing buffer.

This is called automatically after each conversation turn. The exchange will be
processed by the Librarian within 20 minutes.

You normally don't need to call this manually - it's for the system integration.""",
    input_schema={
        "type": "object",
        "properties": {
            "user_message": {"type": "string", "description": "The user's message"},
            "assistant_message": {"type": "string", "description": "The assistant's response"},
            "session_id": {"type": "string", "description": "Session identifier"}
        },
        "required": ["user_message", "assistant_message"]
    }
)
async def ltm_buffer_exchange(args: Dict[str, Any]) -> Dict[str, Any]:
    """Buffer an exchange for later processing."""
    try:
        from ltm.memory_throttle import add_exchange_to_buffer, get_buffer_stats
        from datetime import datetime

        user_msg = args.get("user_message", "")
        assistant_msg = args.get("assistant_message", "")
        session_id = args.get("session_id", "unknown")

        if not user_msg or not assistant_msg:
            return {"content": [{"type": "text", "text": "Both user_message and assistant_message are required"}], "is_error": True}

        exchange = {
            "user_message": user_msg,
            "assistant_message": assistant_msg,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat()
        }

        should_run = add_exchange_to_buffer(exchange)
        stats = get_buffer_stats()

        result = f"Exchange buffered. Buffer size: {stats['buffer_size']}"
        if should_run:
            result += " (Librarian ready to run)"

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="ltm_backfill",
    description="""Process existing chat history through the Librarian to extract memories.

This runs the Librarian against all previous conversations to build up the memory store
from historical data. Useful when first setting up the LTM system or after clearing memories.

The backfill tracks which chats have been processed, so it's safe to run multiple times.
Only unprocessed chats will be analyzed.""",
    input_schema={
        "type": "object",
        "properties": {
            "dry_run": {"type": "boolean", "description": "Preview what would be processed without creating memories", "default": False},
            "batch_size": {"type": "integer", "description": "Exchanges per Librarian batch (default: 10)", "default": 10},
            "limit": {"type": "integer", "description": "Maximum chats to process (default: all)"},
            "reprocess": {"type": "boolean", "description": "Reprocess already-processed chats", "default": False}
        }
    }
)
async def ltm_backfill(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run backfill of chat history."""
    try:
        from ltm.backfill_chats import run_backfill, get_all_chats, load_backfill_state, extract_exchanges, load_chat

        dry_run = args.get("dry_run", False)
        batch_size = args.get("batch_size", 10)
        limit = args.get("limit")
        reprocess = args.get("reprocess", False)

        # Get overview first
        all_chats = get_all_chats()
        state = load_backfill_state()
        processed = set(state.get("processed_chats", []))

        total_exchanges = 0
        for chat_id, path in all_chats:
            if not reprocess and chat_id in processed:
                continue
            chat = load_chat(path)
            if chat:
                total_exchanges += len(extract_exchanges(chat))

        if total_exchanges == 0:
            return {"content": [{"type": "text", "text": "No unprocessed chats found. Use reprocess=true to reprocess all."}]}

        # Run backfill
        await run_backfill(
            dry_run=dry_run,
            batch_size=batch_size,
            limit=limit,
            skip_processed=not reprocess
        )

        # Get updated state
        new_state = load_backfill_state()

        output = f"""## Backfill {'Preview' if dry_run else 'Complete'}

- Total chats: {len(all_chats)}
- Previously processed: {len(processed)}
- Exchanges found: {total_exchanges}
- Total memories created (all time): {new_state.get('total_memories_created', 0)}
- Dry run: {dry_run}
"""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


# ===== Web Search Tool (Perplexity) =====

@tool(
    name="web_search",
    description="""Search the web using Perplexity API.

Use this to find current information, news, documentation, or any web content.
Returns formatted search results with titles, URLs, snippets, and dates.

Supports filtering by:
- recency: "day", "week", "month", "year"
- country: ISO 2-letter code (e.g., "US", "GB")
- domains: List of domains to include/exclude (prefix "-" to exclude)

Example queries:
- "latest Python 3.12 features"
- "Claude AI documentation 2026"
- "current weather in Austin"

For multiple parallel searches, call this tool multiple times.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query"
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results (1-20, default 10)",
                "default": 10
            },
            "recency": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
                "description": "Filter results by recency"
            },
            "country": {
                "type": "string",
                "description": "ISO 2-letter country code for regional filtering"
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Domains to include/exclude (prefix '-' to exclude)"
            }
        },
        "required": ["query"]
    }
)
async def web_search_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Perform a web search using Perplexity."""
    try:
        if not web_search_tool:
            return {
                "content": [{"type": "text", "text": "Web search not available: perplexityai package not installed"}],
                "is_error": True
            }

        query = args.get("query", "")
        max_results = args.get("max_results", 10)
        recency = args.get("recency")
        country = args.get("country")
        domains = args.get("domains")

        result = await web_search_tool.web_search(
            query=query,
            max_results=max_results,
            recency=recency,
            country=country,
            domains=domains,
        )

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Web search error: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }

# ===== Spotify Tools =====

@tool(
    name="spotify_auth_start",
    description="""Start Spotify OAuth flow. Returns an authorization URL.

Visit the URL in a browser, authorize the app, then copy the 'code' parameter
from the redirect URL and pass it to spotify_auth_callback.

Environment variables must be set:
- SPOTIFY_CLIENT_ID
- SPOTIFY_CLIENT_SECRET
- SPOTIFY_REDIRECT_URI (optional, defaults to http://localhost:8888/callback)""",
    input_schema={"type": "object", "properties": {}}
)
async def spotify_auth_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """Start Spotify OAuth flow."""
    try:
        import spotify_tools

        result = spotify_tools.auth_start()
        output = f"""## Spotify Authorization

1. Visit this URL in your browser:
   {result['auth_url']}

2. Log in and authorize the app

3. You'll be redirected to: {result['redirect_uri']}?code=XXX...

4. Copy the 'code' parameter value and call:
   `spotify_auth_callback` with the code

State (for verification): {result['state']}
"""
        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_auth_callback",
    description="""Complete Spotify OAuth by exchanging the authorization code for tokens.

After visiting the auth URL from spotify_auth_start and authorizing, you'll be
redirected to a URL like: http://localhost:8888/callback?code=ABC123...

Extract the code parameter value and pass it here.""",
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Authorization code from the redirect URL"}
        },
        "required": ["code"]
    }
)
async def spotify_auth_callback(args: Dict[str, Any]) -> Dict[str, Any]:
    """Exchange auth code for tokens."""
    try:
        import spotify_tools

        code = args.get("code", "").strip()
        if not code:
            return {"content": [{"type": "text", "text": "Error: code is required"}], "is_error": True}

        result = spotify_tools.auth_callback(code)
        return {"content": [{"type": "text", "text": f"Successfully authenticated with Spotify! Token expires in {result.get('expires_in', 3600)} seconds."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_recently_played",
    description="Get the user's recently played tracks on Spotify.",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of tracks to return (default: 20, max: 50)", "default": 20}
        }
    }
)
async def spotify_recently_played(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get recently played tracks."""
    try:
        import spotify_tools

        limit = args.get("limit", 20)
        tracks = spotify_tools.get_recently_played(limit=limit)
        output = spotify_tools.format_recently_played(tracks)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_top_items",
    description="""Get the user's top artists or tracks.

time_range options:
- short_term: Last 4 weeks
- medium_term: Last 6 months (default)
- long_term: All time""",
    input_schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["artists", "tracks"],
                "description": "Type of items to get",
                "default": "tracks"
            },
            "time_range": {
                "type": "string",
                "enum": ["short_term", "medium_term", "long_term"],
                "description": "Time range for top items",
                "default": "medium_term"
            },
            "limit": {"type": "integer", "description": "Number of items (default: 20, max: 50)", "default": 20}
        }
    }
)
async def spotify_top_items(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get top artists or tracks."""
    try:
        import spotify_tools

        item_type = args.get("type", "tracks")
        time_range = args.get("time_range", "medium_term")
        limit = args.get("limit", 20)

        items = spotify_tools.get_top_items(item_type=item_type, time_range=time_range, limit=limit)
        output = spotify_tools.format_top_items(items, item_type)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_search",
    description="""Search Spotify for tracks, artists, albums, or playlists.

Returns matching items with their Spotify URIs (needed for adding to playlists or playback).""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "type": {
                "type": "string",
                "enum": ["track", "artist", "album", "playlist"],
                "description": "Type of items to search for",
                "default": "track"
            },
            "limit": {"type": "integer", "description": "Number of results (default: 10, max: 50)", "default": 10}
        },
        "required": ["query"]
    }
)
async def spotify_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search Spotify."""
    try:
        import spotify_tools

        query = args.get("query", "").strip()
        if not query:
            return {"content": [{"type": "text", "text": "Error: query is required"}], "is_error": True}

        search_type = args.get("type", "track")
        limit = args.get("limit", 10)

        items = spotify_tools.search(query=query, search_type=search_type, limit=limit)
        output = spotify_tools.format_search_results(items, search_type)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_get_playlists",
    description="Get the user's Spotify playlists.",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of playlists (default: 20, max: 50)", "default": 20}
        }
    }
)
async def spotify_get_playlists(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get user's playlists."""
    try:
        import spotify_tools

        limit = args.get("limit", 20)
        playlists = spotify_tools.get_playlists(limit=limit)
        output = spotify_tools.format_playlists(playlists)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_create_playlist",
    description="Create a new Spotify playlist.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Playlist name"},
            "description": {"type": "string", "description": "Playlist description", "default": ""},
            "public": {"type": "boolean", "description": "Make playlist public (default: true)", "default": True}
        },
        "required": ["name"]
    }
)
async def spotify_create_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new playlist."""
    try:
        import spotify_tools

        name = args.get("name", "").strip()
        if not name:
            return {"content": [{"type": "text", "text": "Error: name is required"}], "is_error": True}

        description = args.get("description", "")
        public = args.get("public", True)

        result = spotify_tools.create_playlist(name=name, description=description, public=public)

        output = f"""## Playlist Created

**{result.get('name')}**
ID: `{result.get('id')}`
URL: {result.get('spotify_url')}

Use `spotify_add_to_playlist` with this ID to add tracks."""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_add_to_playlist",
    description="""Add tracks to a Spotify playlist.

Use spotify_search to find track URIs first. Track URIs look like: spotify:track:4iV5W9uYEdYUVa79Axb7Rh""",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {"type": "string", "description": "Playlist ID (from spotify_get_playlists or spotify_create_playlist)"},
            "track_uris": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of Spotify track URIs to add"
            }
        },
        "required": ["playlist_id", "track_uris"]
    }
)
async def spotify_add_to_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add tracks to a playlist."""
    try:
        import spotify_tools

        playlist_id = args.get("playlist_id", "").strip()
        track_uris = args.get("track_uris", [])

        if not playlist_id:
            return {"content": [{"type": "text", "text": "Error: playlist_id is required"}], "is_error": True}
        if not track_uris:
            return {"content": [{"type": "text", "text": "Error: track_uris is required"}], "is_error": True}

        result = spotify_tools.add_to_playlist(playlist_id=playlist_id, track_uris=track_uris)

        return {"content": [{"type": "text", "text": f"Added {result.get('tracks_added')} tracks to playlist."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_now_playing",
    description="Get the currently playing track on Spotify.",
    input_schema={"type": "object", "properties": {}}
)
async def spotify_now_playing(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get currently playing track."""
    try:
        import spotify_tools

        data = spotify_tools.get_now_playing()
        output = spotify_tools.format_now_playing(data)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@tool(
    name="spotify_playback_control",
    description="""Control Spotify playback.

**Requires Spotify Premium.**

Actions:
- play: Resume playback
- pause: Pause playback
- next: Skip to next track
- previous: Go to previous track""",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "pause", "next", "previous"],
                "description": "Playback action"
            }
        },
        "required": ["action"]
    }
)
async def spotify_playback_control(args: Dict[str, Any]) -> Dict[str, Any]:
    """Control playback."""
    try:
        import spotify_tools

        action = args.get("action", "")
        if not action:
            return {"content": [{"type": "text", "text": "Error: action is required"}], "is_error": True}

        result = spotify_tools.playback_control(action=action)

        action_msgs = {
            "play": "Playback resumed",
            "pause": "Playback paused",
            "next": "Skipped to next track",
            "previous": "Went to previous track",
        }

        return {"content": [{"type": "text", "text": action_msgs.get(action, f"Action '{action}' completed")}]}

    except Exception as e:
        error_msg = str(e)
        if "PREMIUM_REQUIRED" in error_msg or "403" in error_msg:
            error_msg = "This feature requires Spotify Premium."
        return {"content": [{"type": "text", "text": f"Error: {error_msg}"}], "is_error": True}


# ===== Create the MCP Server =====

def create_second_brain_tools():
    """Create the MCP server with all Second Brain tools."""
    return create_sdk_mcp_server(
        name="second_brain",
        version="1.0.0",
        tools=[
            google_create,
            google_list,
            google_delete_task,
            google_update_task,
            # Gmail tools
            gmail_list_messages_mcp,
            gmail_get_message_mcp,
            gmail_send_mcp,
            gmail_reply_mcp,
            gmail_list_labels_mcp,
            gmail_modify_labels_mcp,
            gmail_trash_mcp,
            gmail_draft_create_mcp,
            # YouTube Music tools
            ytmusic_get_playlists_mcp,
            ytmusic_get_playlist_items_mcp,
            ytmusic_get_liked_mcp,
            ytmusic_search_mcp,
            ytmusic_create_playlist_mcp,
            ytmusic_add_to_playlist_mcp,
            ytmusic_remove_from_playlist_mcp,
            ytmusic_delete_playlist_mcp,
            schedule_self,
            scheduler_list,
            scheduler_update,
            scheduler_remove,
            send_critical_notification,
            memory_append,
            memory_read,
            # Working Memory tools
            working_memory_add,
            working_memory_update,
            working_memory_remove,
            working_memory_list,
            working_memory_snapshot,
            # Long-Term Memory tools
            ltm_search,
            ltm_get_context,
            ltm_add_memory,
            ltm_create_thread,
            ltm_stats,
            ltm_process_now,
            ltm_run_gardener,
            ltm_buffer_exchange,
            ltm_backfill,
            # Page Parser
            page_parser_tool,
            # Restart
            restart_server,
            # Claude Code Subagent
            claude_code_exec,
            # Web Search (Perplexity)
            web_search_mcp,
            # Spotify Tools
            spotify_auth_start,
            spotify_auth_callback,
            spotify_recently_played,
            spotify_top_items,
            spotify_search,
            spotify_get_playlists,
            spotify_create_playlist,
            spotify_add_to_playlist,
            spotify_now_playing,
            spotify_playback_control,
        ]
    )
