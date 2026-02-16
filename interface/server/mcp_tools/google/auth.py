"""
Google OAuth status/re-auth tool.

Allows Claude to check Google auth status and provide a re-auth link.
"""

import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add server directory to path for google_auth_web
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

AUTH_URL = "https://brain.zekethurston.org/api/auth/google/login"


@register_tool("google")
@tool(
    name="google_auth",
    description="""Check Google OAuth status and get a re-auth link if needed.

Call this when:
- A Google/Gmail/YouTube tool fails with an auth error
- The user asks about Google authentication status
- You want to proactively verify Google services are working

Returns auth status. If expired, returns a clickable link the user can open to re-authenticate.""",
    input_schema={"type": "object", "properties": {}}
)
async def google_auth(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check Google auth status and provide re-auth link if needed."""
    try:
        from google_auth_web import get_auth_status, has_web_credentials

        status = get_auth_status()

        if status.get("authenticated"):
            expiry = status.get("expiry", "unknown")
            refreshed = " (just refreshed)" if status.get("refreshed") else ""
            output = f"Google authentication is valid{refreshed}. Token expires: {expiry}"
            return {"content": [{"type": "text", "text": output}]}

        # Not authenticated — build a helpful message
        reason = status.get("reason", "unknown")
        error = status.get("error", "")

        reason_msgs = {
            "no_token": "No Google token found.",
            "refresh_failed": f"Token refresh failed: {error}",
            "expired_no_refresh": "Token expired and no refresh token available.",
            "error": f"Token error: {error}",
        }
        reason_msg = reason_msgs.get(reason, f"Authentication issue: {reason}")

        if has_web_credentials():
            output = f"""## Google Re-Authentication Needed

{reason_msg}

**Click here to re-authenticate:** {AUTH_URL}

After completing the Google sign-in, your Google tools (Gmail, Calendar, Tasks, YouTube) will work again immediately."""
        else:
            output = f"""## Google Re-Authentication Needed

{reason_msg}

Web OAuth credentials are not configured yet. To enable browser-based re-auth:
1. Create a "Web Application" OAuth client in Google Cloud Console
2. Set redirect URI to: https://brain.zekethurston.org/api/auth/google/callback
3. Save the downloaded JSON to: .claude/secrets/credentials_web.json

Until then, re-auth requires SSH access to the server."""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error checking auth status: {str(e)}"}], "is_error": True}
