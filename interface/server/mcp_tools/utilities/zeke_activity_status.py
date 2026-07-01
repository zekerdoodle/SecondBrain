"""Read-only the user activity status tool for Character and Patch."""

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

import zeke_activity


ALLOWED_AGENTS = {"patch", "character"}

_TOOL_DESCRIPTION = """Return the user's minimized Second Brain activity status.

Only Character and Patch may use this tool. It reports active/idle/offline state,
age of the most recent human activity, last action category, connection status,
and a bounded list of redacted recent activity events. It never returns chat
message text, composer drafts, app data contents, file contents, or AI
prompt/response bodies.
"""

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "recent_limit": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": "Number of recent redacted activity events to include. Default 5, max 10.",
        }
    },
}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _format_age(value: Optional[str], now: datetime) -> str:
    parsed = _parse_iso(value)
    if parsed is None:
        return "unknown"
    seconds = max(0, int((now - parsed).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s ago"
    hours, mins = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h{mins:02d}m ago"
    days, rem_hours = divmod(hours, 24)
    return f"{days}d{rem_hours:02d}h ago"


def _metadata_suffix(metadata: Mapping[str, Any]) -> str:
    bits = []
    for key in ("agent", "chat_id", "app_name", "app_entry", "app_path", "path"):
        value = metadata.get(key)
        if value:
            label = "chat" if key == "chat_id" else key.replace("_", " ")
            bits.append(f"{label}: `{value}`")
    return f" ({', '.join(bits)})" if bits else ""


def _render_status(status: Mapping[str, Any]) -> str:
    now = _parse_iso(status.get("generated_at")) or datetime.now(timezone.utc)
    state = status.get("state") or "offline"
    state_label = {
        "active": "active",
        "idle": "idle/away",
        "offline": "offline",
    }.get(str(state), str(state))
    last_activity = status.get("last_user_activity_at")
    last_seen = (status.get("presence") or {}).get("last_seen_at") if isinstance(status.get("presence"), dict) else None
    presence = status.get("presence") if isinstance(status.get("presence"), dict) else {}

    lines = [
        f"the user is **{state_label}**.",
        f"Last human activity: {_format_age(last_activity, now)}.",
        f"Last action: `{status.get('last_action_category') or 'unknown'}`.",
        f"Summary: {status.get('last_activity_summary') or 'No recent activity recorded.'}",
        (
            "Connection: "
            f"{presence.get('session_count', 0)} connected session(s), "
            f"last seen {_format_age(last_seen, now)}."
        ),
    ]

    events = status.get("recent_events") if isinstance(status.get("recent_events"), list) else []
    if events:
        lines.extend(["", "Recent redacted events:"])
        for event in events:
            if not isinstance(event, dict):
                continue
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            lines.append(
                f"- {_format_age(event.get('at'), now)} "
                f"`{event.get('category') or 'unknown'}`: "
                f"{event.get('summary') or 'Activity'}"
                f"{_metadata_suffix(metadata)}"
            )
    return "\n".join(lines)


@register_tool("utilities")
@tool(name="zeke_activity_status", description=_TOOL_DESCRIPTION, input_schema=_TOOL_SCHEMA)
async def zeke_activity_status(args: Dict[str, Any]) -> Dict[str, Any]:
    caller = str(args.pop("_agent_name", "") or "").lower()
    args.pop("_source_chat_id", None)
    if caller not in ALLOWED_AGENTS:
        return {
            "content": [{"type": "text", "text": "Access denied: zeke_activity_status is only available to Character and Patch."}],
            "is_error": True,
        }

    try:
        recent_limit = int(args.get("recent_limit", 5))
    except (TypeError, ValueError):
        recent_limit = 5
    recent_limit = max(0, min(recent_limit, 10))

    status = zeke_activity.read_status(recent_limit=recent_limit)
    return {"content": [{"type": "text", "text": _render_status(status)}]}
