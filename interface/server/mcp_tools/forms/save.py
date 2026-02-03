"""
Forms Save Tool - Persist a form submission.

Saves user answers to the submissions log.
"""

import os
import sys
import time
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path for forms_store
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Import forms_store
try:
    from theo_ports.utils.forms_store import append_submission, get_form
except ImportError:
    append_submission = None
    get_form = None


@register_tool("forms")
@tool(
    name="forms_save",
    description="""Save a form submission to persistent storage.

This is typically called after receiving form answers from the user,
either from a forms_show interaction or from a direct text response.

The answers should map field IDs to their values.

Example:
  forms_save(
    form_id="daily_checkin",
    answers={"mood": "good", "energy": 7, "notes": "Productive day!"}
  )
""",
    input_schema={
        "type": "object",
        "properties": {
            "form_id": {
                "type": "string",
                "description": "The ID of the form being submitted"
            },
            "answers": {
                "type": "object",
                "description": "The form answers as field_id -> value mapping",
                "additionalProperties": True
            }
        },
        "required": ["form_id", "answers"]
    }
)
async def forms_save(args: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a form submission."""
    try:
        if not append_submission:
            return {
                "content": [{"type": "text", "text": "Forms store not available: forms_store module not found"}],
                "is_error": True
            }

        form_id = args.get("form_id")
        answers = args.get("answers", {})

        # Optionally validate form exists
        version = 1
        if get_form:
            form = get_form(form_id)
            if form:
                version = form.get("version", 1)

        # Build submission record
        record = {
            "ts": time.time(),
            "form_id": form_id,
            "version": version,
            "answers": answers,
        }

        success, message = append_submission(record)

        if success:
            answer_count = len(answers)
            return {
                "content": [{
                    "type": "text",
                    "text": f"Saved form submission for '{form_id}' with {answer_count} answers."
                }]
            }
        else:
            return {
                "content": [{"type": "text", "text": f"Failed to save submission: {message}"}],
                "is_error": True
            }

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error saving form: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
