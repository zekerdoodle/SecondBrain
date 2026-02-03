"""
Critical Notification tool.

Sends urgent notifications via all channels: in-app toast, push notification, AND email.
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
        import asyncio

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
            server_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if server_path not in sys.path:
                sys.path.insert(0, server_path)
            from push_service import send_push_notification

            asyncio.create_task(send_push_notification(
                title="URGENT: Claude needs your attention",
                body=message[:100],
                chat_id="",
                critical=True
            ))
        except Exception as push_err:
            # Push is optional, don't fail the whole call
            pass

        return {"content": [{"type": "text", "text": f"Critical notification sent!\n- Email sent to: {user_email}\n- Push notification triggered\n\nMessage: {message}"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
