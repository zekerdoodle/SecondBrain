"""
Forms List Tool - Query past form submissions.

Retrieves submissions with optional filtering.
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path for forms_store
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Import forms_store
try:
    from theo_ports.utils.forms_store import list_submissions as _list_submissions, get_form_registry
except ImportError:
    _list_submissions = None
    get_form_registry = None


def _format_timestamp(ts: float) -> str:
    """Format timestamp for display."""
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


@register_tool("forms")
@tool(
    name="forms_list",
    description="""List past form submissions with optional filtering.

Returns recent submissions, optionally filtered by form_id.
Submissions are returned newest first.

Use this to:
- Review past check-ins or surveys
- Analyze trends in collected data
- Find specific submissions

Example:
  forms_list(form_id="daily_checkin", limit=7)  # Last week of check-ins
""",
    input_schema={
        "type": "object",
        "properties": {
            "form_id": {
                "type": "string",
                "description": "Filter by specific form ID (optional - omit to list all)"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of submissions to return (default 20)",
                "default": 20
            },
            "after_ts": {
                "type": "number",
                "description": "Only include submissions after this Unix timestamp"
            },
            "list_forms": {
                "type": "boolean",
                "description": "If true, list available form definitions instead of submissions"
            }
        }
    }
)
async def forms_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """Query past form submissions."""
    try:
        # Handle list_forms flag
        if args.get("list_forms"):
            if not get_form_registry:
                return {
                    "content": [{"type": "text", "text": "Forms store not available"}],
                    "is_error": True
                }

            registry = get_form_registry()
            if not registry:
                return {
                    "content": [{"type": "text", "text": "No forms defined yet. Use forms_define to create one."}]
                }

            lines = ["**Available Forms:**\n"]
            for form_id, form in sorted(registry.items()):
                field_count = len(form.get("fields", []))
                lines.append(f"- **{form_id}**: {form.get('title', form_id)} ({field_count} fields)")

            return {
                "content": [{"type": "text", "text": "\n".join(lines)}]
            }

        # List submissions
        if not _list_submissions:
            return {
                "content": [{"type": "text", "text": "Forms store not available: forms_store module not found"}],
                "is_error": True
            }

        form_id = args.get("form_id")
        limit = args.get("limit", 20)
        after_ts = args.get("after_ts")

        submissions = _list_submissions(
            form_id=form_id,
            limit=limit,
            after_ts=after_ts
        )

        if not submissions:
            filter_msg = f" for '{form_id}'" if form_id else ""
            return {
                "content": [{"type": "text", "text": f"No submissions found{filter_msg}."}]
            }

        # Format submissions for display
        lines = []
        for sub in submissions:
            ts = _format_timestamp(sub.get("ts", 0))
            fid = sub.get("form_id", "unknown")
            answers = sub.get("answers", {})

            # Format answers concisely
            answer_parts = []
            for key, value in answers.items():
                if isinstance(value, str) and len(value) > 50:
                    value = value[:47] + "..."
                answer_parts.append(f"{key}={value}")

            answers_str = ", ".join(answer_parts[:5])
            if len(answer_parts) > 5:
                answers_str += f" (+{len(answer_parts) - 5} more)"

            lines.append(f"- [{ts}] **{fid}**: {answers_str}")

        header = f"**{len(submissions)} submission(s)**"
        if form_id:
            header += f" for '{form_id}'"
        header += ":\n"

        return {
            "content": [{"type": "text", "text": header + "\n".join(lines)}]
        }

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error listing submissions: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
