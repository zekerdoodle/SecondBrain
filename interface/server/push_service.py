"""
Email Notification Service

Sends notifications via email using Gmail API instead of web push.
Replaces the previous web push implementation.
"""

import json
import os
import sys
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Paths
SECRETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.claude/secrets"))
CLAUDE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.claude"))
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.claude/scripts"))

# Notification recipient
NOTIFICATION_EMAIL = "username@gmail.com"
BASE_URL = "https://brain.username.org"

# Add scripts directory to path for google_tools
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


# ===== Legacy push subscription types (kept for API compatibility) =====

@dataclass
class PushSubscription:
    """Represents a push subscription from a client (legacy, kept for API compatibility)."""
    endpoint: str
    keys: Dict[str, str]
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "keys": self.keys,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PushSubscription":
        return cls(
            endpoint=data["endpoint"],
            keys=data["keys"],
            created_at=data.get("created_at")
        )


def get_vapid_public_key() -> Optional[str]:
    """Legacy: Returns None since we're using email now."""
    return None


def load_subscriptions() -> List[PushSubscription]:
    """Legacy: Returns empty list since we're using email now."""
    return []


def save_subscriptions(subscriptions: List[PushSubscription]):
    """Legacy: No-op since we're using email now."""
    pass


def add_subscription(subscription: PushSubscription) -> bool:
    """Legacy: No-op since we're using email now."""
    return True


def remove_subscription(endpoint: str) -> bool:
    """Legacy: No-op since we're using email now."""
    return True


# ===== Email notification implementation =====

def _build_email_html(title: str, body: str, chat_id: str, critical: bool = False) -> str:
    """Build a pretty HTML email for notifications."""
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # Build chat link
    chat_link = BASE_URL
    if chat_id:
        chat_link = f"{BASE_URL}/?chat={chat_id}"

    # Color scheme based on critical status
    accent_color = "#dc2626" if critical else "#6366f1"  # Red for critical, indigo for normal
    badge_text = "URGENT" if critical else "New Message"
    badge_bg = "#fef2f2" if critical else "#eef2ff"
    badge_border = "#fecaca" if critical else "#c7d2fe"

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f3f4f6;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f3f4f6; padding: 40px 20px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 480px; background-color: #ffffff; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">

                    <!-- Header -->
                    <tr>
                        <td style="padding: 32px 32px 24px 32px; text-align: center; border-bottom: 1px solid #e5e7eb;">
                            <div style="display: inline-block; padding: 6px 16px; background-color: {badge_bg}; border: 1px solid {badge_border}; border-radius: 9999px; font-size: 12px; font-weight: 600; color: {accent_color}; text-transform: uppercase; letter-spacing: 0.5px;">
                                {badge_text}
                            </div>
                            <h1 style="margin: 16px 0 0 0; font-size: 22px; font-weight: 700; color: #111827; line-height: 1.3;">
                                {title}
                            </h1>
                        </td>
                    </tr>

                    <!-- Body -->
                    <tr>
                        <td style="padding: 24px 32px;">
                            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #374151;">
                                {body}
                            </p>
                            <p style="margin: 0; font-size: 13px; color: #9ca3af;">
                                {timestamp}
                            </p>
                        </td>
                    </tr>

                    <!-- CTA Button -->
                    <tr>
                        <td style="padding: 8px 32px 32px 32px;">
                            <a href="{chat_link}" style="display: block; width: 100%; padding: 16px 24px; background-color: {accent_color}; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600; text-align: center; border-radius: 8px; box-sizing: border-box;">
                                Open Chat
                            </a>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="padding: 20px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; border-radius: 0 0 12px 12px;">
                            <p style="margin: 0; font-size: 12px; color: #9ca3af; text-align: center;">
                                Second Brain &bull; <a href="{BASE_URL}" style="color: #6b7280;">brain.username.org</a>
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''


async def send_push_notification(
    title: str,
    body: str,
    chat_id: str,
    critical: bool = False
) -> int:
    """
    Send notification via email (replaces web push).

    Same signature as the original push notification function for compatibility.
    Returns 1 on success, 0 on failure.
    """
    try:
        import google_tools
        import base64
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        # Build email subject with context emoji
        if critical:
            subject = f"🚨 URGENT: {title}"
        elif "completed" in title.lower() or "finished" in title.lower():
            subject = f"✅ {title}"
        elif "response" in title.lower() or "message" in title.lower():
            subject = f"💬 {title}"
        else:
            subject = f"🧠 {title}"

        # Build HTML body
        html_body = _build_email_html(title, body, chat_id, critical)

        # Authenticate and get Gmail service
        creds = google_tools.authenticate()
        service = google_tools._get_gmail_service(creds)

        # Build the email
        message = MIMEMultipart('alternative')
        message['To'] = NOTIFICATION_EMAIL
        message['Subject'] = subject

        # Plain text fallback
        chat_link = f"{BASE_URL}/?chat={chat_id}" if chat_id else BASE_URL
        plain_text = f"{title}\n\n{body}\n\nOpen chat: {chat_link}"

        # Attach both plain text and HTML versions
        message.attach(MIMEText(plain_text, 'plain'))
        message.attach(MIMEText(html_body, 'html'))

        # Encode and send
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        result = service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        logger.info(f"Email notification sent: {subject} (message_id: {result.get('id')})")
        return 1

    except Exception as e:
        logger.error(f"Email notification failed: {e}")
        return 0
