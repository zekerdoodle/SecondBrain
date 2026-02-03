"""
Forms Show Tool - Display a form to the user.

Embeds form schema in the message for UI rendering.
"""

import json
import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path for forms_store
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Import forms_store
try:
    from theo_ports.utils.forms_store import get_form, get_form_registry
except ImportError:
    get_form = None
    get_form_registry = None


# Special marker for form embedding - UI will detect and render
# Using unique delimiters that won't conflict with normal content
FORM_MARKER_START = ":::FORM_REQUEST:::"
FORM_MARKER_END = ":::END_FORM:::"


@register_tool("forms")
@tool(
    name="forms_show",
    description="""Display a form modal to the user for structured data collection.

ALWAYS call this immediately after forms_define to show the form you just created.
The form appears as a beautiful popup modal - much better UX than asking questions in chat.

The user fills out the form and clicks Submit. Their answers come back as a structured
message that you can then process.

Workflow:
1. forms_define(...) - create the form schema
2. forms_show(form_id="...") - display it to user
3. User fills it out and submits
4. You receive their answers and can respond accordingly

Use prefill to pre-populate fields with known values.""",
    input_schema={
        "type": "object",
        "properties": {
            "form_id": {
                "type": "string",
                "description": "The ID of the form to display (must be previously defined)"
            },
            "prefill": {
                "type": "object",
                "description": "Optional values to prefill in the form (field_id -> value)",
                "additionalProperties": True
            }
        },
        "required": ["form_id"]
    }
)
async def forms_show(args: Dict[str, Any]) -> Dict[str, Any]:
    """Request the UI to render a form modal."""
    try:
        if not get_form:
            return {
                "content": [{"type": "text", "text": "Forms store not available: forms_store module not found"}],
                "is_error": True
            }

        form_id = args.get("form_id")
        prefill = args.get("prefill", {})

        # Fetch form definition
        form = get_form(form_id)
        if not form:
            # List available forms to help user
            available = []
            if get_form_registry:
                registry = get_form_registry()
                available = sorted(list(registry.keys()))

            available_msg = f" Available forms: {', '.join(available)}" if available else " No forms defined yet."
            return {
                "content": [{
                    "type": "text",
                    "text": f"Form '{form_id}' not found.{available_msg}"
                }],
                "is_error": True
            }

        # Build form payload for UI
        form_payload = {
            "formId": form_id,
            "title": form.get("title", form_id),
            "description": form.get("description", ""),
            "fields": form.get("fields", []),
            "prefill": prefill,
            "version": form.get("version", 1),
        }

        # Return message for Claude and form data for UI broadcast
        # The _form_request key signals to main.py to broadcast to the UI
        return {
            "content": [{
                "type": "text",
                "text": f"I've opened the '{form.get('title', form_id)}' form for you to fill out."
            }],
            "_form_request": form_payload
        }

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error showing form: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
