"""
Notification Decision Logic

Determines when and how to notify users based on:
- Chat silent flag
- User visibility state
- Message criticality
"""

from dataclasses import dataclass
from typing import Dict, Optional, Any
import time


@dataclass
class NotificationDecision:
    """Result of notification decision logic."""
    notify: bool = False
    use_toast: bool = False      # In-app toast notification
    use_push: bool = False       # Mobile push notification
    use_email: bool = False      # Email fallback (critical only)
    play_sound: bool = False     # Play notification sound
    reason: str = ""             # Why this decision was made


def should_notify(
    chat_id: str,
    is_silent: bool,
    client_sessions: Dict[Any, Any],
    critical: bool = False,
    stale_timeout: float = 90
) -> NotificationDecision:
    """
    Determine whether to send a notification for a message.

    Args:
        chat_id: The chat session ID
        is_silent: Whether this is a silent/background task
        client_sessions: Dict of WebSocket -> ClientSession
        critical: Whether this message is marked critical
        stale_timeout: Seconds before a connection is considered stale

    Returns:
        NotificationDecision with channels to use
    """
    # Silent tasks never notify (unless critical overrides)
    if is_silent and not critical:
        return NotificationDecision(notify=False, reason="silent_chat")

    # Check if any connected client is actively viewing this chat
    current_time = time.time()
    user_is_viewing = False
    has_active_connection = False

    for ws, session in client_sessions.items():
        # Skip stale connections
        if current_time - session.last_heartbeat > stale_timeout:
            continue

        has_active_connection = True

        # Check if this client is actively viewing the specific chat
        if session.is_active and session.current_chat_id == chat_id:
            user_is_viewing = True
            break

    # If user is actively viewing this chat, no notification needed
    # (unless critical - critical always notifies via email)
    if user_is_viewing and not critical:
        return NotificationDecision(notify=False, reason="user_viewing")

    # Determine notification channels based on connection state
    if critical:
        # Critical messages: all channels
        return NotificationDecision(
            notify=True,
            use_toast=has_active_connection,
            use_push=True,  # Always push for critical
            use_email=True,  # Always email for critical
            play_sound=has_active_connection,
            reason="critical_message"
        )

    if has_active_connection:
        # User has active connection but not viewing this chat
        # Toast + sound + push (they may be on mobile, not at the browser)
        return NotificationDecision(
            notify=True,
            use_toast=True,
            use_push=True,  # Always push - browser tab open doesn't mean user is there
            use_email=False,
            play_sound=True,
            reason="user_online_not_viewing"
        )

    # No active connections - user is away
    # Push notification only (no one to show toast to)
    return NotificationDecision(
        notify=True,
        use_toast=False,
        use_push=True,
        use_email=False,
        play_sound=False,
        reason="user_offline"
    )


async def send_notification(
    client_sessions: Dict[Any, Any],
    chat_id: str,
    preview: str,
    critical: bool = False,
    play_sound: bool = True
):
    """
    Send notification to all connected clients.

    Args:
        client_sessions: Dict of WebSocket -> ClientSession
        chat_id: The chat to notify about
        preview: Message preview text
        critical: Whether this is a critical notification
        play_sound: Whether clients should play sound
    """
    notification_data = {
        "type": "new_message_notification",
        "chatId": chat_id,
        "preview": preview[:200] if preview else "",
        "critical": critical,
        "playSound": play_sound
    }

    for ws in client_sessions:
        try:
            await ws.send_json(notification_data)
        except Exception:
            pass  # Connection may be closed
