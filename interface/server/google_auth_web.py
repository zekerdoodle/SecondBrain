"""
Google OAuth Web Flow for browser-based re-authentication.

Uses a "Web Application" OAuth client (credentials_web.json) to allow
re-authentication from any browser, without SSH access to the server.

The resulting token is saved to the same token.json used by google_tools.py.
"""

import os
import time
import logging

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
SECRETS_DIR = os.path.join(ROOT_DIR, ".claude", "secrets")
WEB_CREDENTIALS_FILE = os.path.join(SECRETS_DIR, "credentials_web.json")
TOKEN_FILE = os.path.join(SECRETS_DIR, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]

REDIRECT_URI = "https://brain.zekethurston.org/api/auth/google/callback"

# In-memory store for pending auth sessions: {state: {flow, created_at}}
_pending_flows: dict = {}
_FLOW_TTL_SECONDS = 600  # 10 minutes


def _cleanup_stale_flows():
    """Remove expired pending flows."""
    now = time.time()
    expired = [s for s, v in _pending_flows.items() if now - v["created_at"] > _FLOW_TTL_SECONDS]
    for s in expired:
        del _pending_flows[s]


def get_auth_status() -> dict:
    """Check current Google token status."""
    if not os.path.exists(TOKEN_FILE):
        return {"authenticated": False, "reason": "no_token"}
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds.valid:
            return {"authenticated": True, "expiry": creds.expiry.isoformat() if creds.expiry else None}
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
                return {"authenticated": True, "expiry": creds.expiry.isoformat() if creds.expiry else None, "refreshed": True}
            except Exception as e:
                return {"authenticated": False, "reason": "refresh_failed", "error": str(e)}
        return {"authenticated": False, "reason": "expired_no_refresh"}
    except Exception as e:
        return {"authenticated": False, "reason": "error", "error": str(e)}


def has_web_credentials() -> bool:
    """Check if the web OAuth credentials file exists."""
    return os.path.exists(WEB_CREDENTIALS_FILE)


def create_authorization_url() -> tuple:
    """
    Create an OAuth authorization URL for the user to visit.

    Returns: (authorization_url, state)
    Raises: FileNotFoundError if credentials_web.json is missing.
    """
    _cleanup_stale_flows()

    if not os.path.exists(WEB_CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"Web OAuth credentials not found at {WEB_CREDENTIALS_FILE}. "
            "Create a 'Web Application' OAuth client in Google Cloud Console "
            "and save the JSON here."
        )

    flow = Flow.from_client_secrets_file(
        WEB_CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # Force consent to get new refresh_token
    )

    _pending_flows[state] = {
        "flow": flow,
        "created_at": time.time(),
    }

    logger.info(f"Created Google OAuth authorization URL (state={state[:8]}...)")
    return authorization_url, state


def handle_callback(state: str, code: str) -> dict:
    """
    Exchange the authorization code for credentials and save token.json.

    Args:
        state: The OAuth state parameter from the callback.
        code: The authorization code from the callback.

    Returns: {"success": True} or raises an exception.
    """
    _cleanup_stale_flows()

    if state not in _pending_flows:
        raise ValueError("Invalid or expired OAuth state. Please start the auth flow again.")

    flow_data = _pending_flows.pop(state)
    flow = flow_data["flow"]

    flow.fetch_token(code=code)
    creds = flow.credentials

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    logger.info("Google OAuth re-authentication successful, token.json updated")
    return {"success": True}
