"""
Forms Define Tool - Register form schemas.

Creates or updates form definitions that can be displayed to users.
"""

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
    from theo_ports.utils.forms_store import define_form as _define_form
except ImportError:
    _define_form = None


@register_tool("forms")
@tool(
    name="forms_define",
    description="""Define a form schema for collecting structured information from the user.

USE THIS PROACTIVELY when you need to collect multiple pieces of information from the user,
especially for:
- Check-ins, reviews, or surveys (mood, energy, goals, reflections)
- Gathering project details or requirements
- Collecting preferences or settings
- Any situation where a structured UI form is better than back-and-forth chat

After defining a form, IMMEDIATELY call forms_show to display it to the user.

Field types:
- text: Single-line input
- textarea: Multi-line input
- select: Dropdown (requires options array)
- checkbox: Yes/no toggle
- number: Numeric input
- date: Date picker

Example - creating and showing a check-in form:
1. Call forms_define with the schema
2. Call forms_show with the form_id
3. User fills it out in a nice UI modal
4. Their answers come back as a message""",
    input_schema={
        "type": "object",
        "properties": {
            "form_id": {
                "type": "string",
                "description": "Unique identifier for the form (e.g., 'daily_checkin', 'project_review')"
            },
            "title": {
                "type": "string",
                "description": "Display title for the form"
            },
            "description": {
                "type": "string",
                "description": "Optional description shown to user"
            },
            "fields": {
                "type": "array",
                "description": "List of form field definitions",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Field identifier"},
                        "type": {
                            "type": "string",
                            "enum": ["text", "textarea", "select", "checkbox", "number", "date"],
                            "description": "Field type"
                        },
                        "label": {"type": "string", "description": "Field label shown to user"},
                        "placeholder": {"type": "string", "description": "Placeholder text"},
                        "required": {"type": "boolean", "description": "Whether field is required"},
                        "options": {
                            "type": "array",
                            "description": "Options for select fields",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "value": {"type": "string"}
                                }
                            }
                        },
                        "defaultValue": {"description": "Default value for the field"}
                    },
                    "required": ["id", "type", "label"]
                }
            },
            "version": {
                "type": "integer",
                "description": "Optional version number (defaults to 1)"
            }
        },
        "required": ["form_id", "title", "fields"]
    }
)
async def forms_define(args: Dict[str, Any]) -> Dict[str, Any]:
    """Register or update a form definition."""
    try:
        if not _define_form:
            return {
                "content": [{"type": "text", "text": "Forms store not available: forms_store module not found"}],
                "is_error": True
            }

        form_data = {
            "form_id": args.get("form_id"),
            "title": args.get("title"),
            "fields": args.get("fields", []),
            "description": args.get("description", ""),
            "version": args.get("version", 1),
        }

        success, message = _define_form(form_data)

        if success:
            field_count = len(form_data.get("fields", []))
            return {
                "content": [{
                    "type": "text",
                    "text": f"Form '{form_data['form_id']}' defined successfully with {field_count} fields."
                }]
            }
        else:
            return {
                "content": [{"type": "text", "text": f"Failed to define form: {message}"}],
                "is_error": True
            }

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error defining form: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
