"""
Critical Notification tool.

Sends urgent notifications via email with high-priority formatting.
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


@register_tool("utilities")
@tool(
    name="send_critical_notification",
    description="""Send a critical notification via email.

Use this ONLY for genuinely urgent situations:
- Time-sensitive deadlines (interview in 30 min, urgent blocker)
- Critical errors that need immediate attention
- Situations where missing the notification would have real consequences

The bar is HIGH. Normal notifications go through automatically. This is for escalation.

The email will be sent to zekethurston@gmail.com with urgent formatting.""",
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
    """Send a critical notification via email."""
    try:
        import asyncio

        message = args.get("message", "").strip()
        context = args.get("context", "").strip()

        if not message:
            return {"content": [{"type": "text", "text": "Error: message is required"}], "is_error": True}

        # Compose full message
        full_message = message
        if context:
            full_message += f"\n\nContext: {context}"

        # Use the email notification service (which replaced push_service)
        server_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if server_path not in sys.path:
            sys.path.insert(0, server_path)
        from push_service import send_push_notification

        # Send email notification (send_push_notification now sends email)
        result = await send_push_notification(
            title="Claude needs your attention",
            body=full_message,
            chat_id="",
            critical=True
        )

        if result:
            return {"content": [{"type": "text", "text": f"Critical notification sent via email!\n\nMessage: {message}"}]}
        else:
            return {"content": [{"type": "text", "text": "Failed to send notification email. Check logs for details."}], "is_error": True}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
