"""
Gmail tools.

Tools for managing Gmail messages, labels, and drafts.
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


@register_tool("gmail")
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
async def gmail_list_messages(args: Dict[str, Any]) -> Dict[str, Any]:
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


@register_tool("gmail")
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
async def gmail_get_message(args: Dict[str, Any]) -> Dict[str, Any]:
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


@register_tool("gmail")
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
async def gmail_send(args: Dict[str, Any]) -> Dict[str, Any]:
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


@register_tool("gmail")
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
async def gmail_reply(args: Dict[str, Any]) -> Dict[str, Any]:
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


@register_tool("gmail")
@tool(
    name="gmail_list_labels",
    description="""List all Gmail labels.

Returns system labels (INBOX, SENT, TRASH, etc.) and user-created labels.
Label IDs are used with gmail_modify_labels to organize messages.""",
    input_schema={"type": "object", "properties": {}}
)
async def gmail_list_labels(args: Dict[str, Any]) -> Dict[str, Any]:
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


@register_tool("gmail")
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
async def gmail_modify_labels(args: Dict[str, Any]) -> Dict[str, Any]:
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


@register_tool("gmail")
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
async def gmail_trash(args: Dict[str, Any]) -> Dict[str, Any]:
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


@register_tool("gmail")
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
async def gmail_draft_create(args: Dict[str, Any]) -> Dict[str, Any]:
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
