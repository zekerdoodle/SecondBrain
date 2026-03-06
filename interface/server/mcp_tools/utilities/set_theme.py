"""
Set Theme tool - allows agents to change the UI theme color and mode.

Updates the server-side preferences file. The server-side streaming handler
detects this tool and broadcasts a theme_update to all connected clients.
"""

import json
import os
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../"))
PREFERENCES_FILE = os.path.join(ROOT_DIR, ".claude", "user_preferences.json")

# Preset accent colors (mirrors frontend ACCENT_COLORS)
ACCENT_PRESETS = {
    "terracotta": {"value": "#D97757", "hover": "#C4684A"},
    "blue": {"value": "#3B82F6", "hover": "#2563EB"},
    "green": {"value": "#10B981", "hover": "#059669"},
    "purple": {"value": "#8B5CF6", "hover": "#7C3AED"},
    "pink": {"value": "#EC4899", "hover": "#DB2777"},
    "orange": {"value": "#F97316", "hover": "#EA580C"},
    "teal": {"value": "#14B8A6", "hover": "#0D9488"},
    "rose": {"value": "#F43F5E", "hover": "#E11D48"},
}


def _darken_hex(hex_color: str, factor: float = 0.85) -> str:
    """Darken a hex color by a factor (0-1). Returns hex string."""
    hex_color = hex_color.lstrip("#")
    r = max(0, int(int(hex_color[0:2], 16) * factor))
    g = max(0, int(int(hex_color[2:4], 16) * factor))
    b = max(0, int(int(hex_color[4:6], 16) * factor))
    return f"#{r:02X}{g:02X}{b:02X}"


def resolve_color(accent_color: str, accent_hover: str | None = None) -> tuple[str, str]:
    """Resolve accent color (preset name or hex) to (color, hover) hex pair."""
    # Check if it's a preset name
    preset = ACCENT_PRESETS.get(accent_color.lower())
    if preset:
        return preset["value"], preset["hover"]

    # Treat as hex color
    color = accent_color if accent_color.startswith("#") else f"#{accent_color}"
    if len(color) != 7:
        raise ValueError(f"Invalid hex color: {accent_color}. Use a 6-digit hex like '#3B82F6' or a preset name.")

    hover = accent_hover or _darken_hex(color)
    if not hover.startswith("#"):
        hover = f"#{hover}"

    return color, hover


@register_tool("utilities")
@tool(
    name="set_theme",
    description="""Set the UI theme color and/or mode (light/dark).

Use this to change the look and feel of the Second Brain UI. Changes apply instantly
to all connected clients.

For accent_color, use a preset name or a hex color:
- Presets: "terracotta", "blue", "green", "purple", "pink", "orange", "teal", "rose"
- Custom hex: "#FF6B35" (any valid 6-digit hex color)

For mode, use "light", "dark", or "system" (follows OS preference).""",
    input_schema={
        "type": "object",
        "properties": {
            "accent_color": {
                "type": "string",
                "description": 'Accent color — preset name (e.g. "blue", "purple") or hex code (e.g. "#3B82F6")'
            },
            "mode": {
                "type": "string",
                "enum": ["light", "dark", "system"],
                "description": "Theme mode: light, dark, or system"
            },
            "accent_hover": {
                "type": "string",
                "description": "Optional hover color hex. Auto-generated (darkened) if not provided."
            }
        },
    }
)
async def set_theme(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set the UI theme color and/or mode."""
    try:
        accent_color_input = args.get("accent_color")
        mode = args.get("mode")
        accent_hover_input = args.get("accent_hover")

        if not accent_color_input and not mode:
            presets = ", ".join(ACCENT_PRESETS.keys())
            return {
                "content": [{"type": "text", "text": f"Please provide at least accent_color or mode.\n\nAvailable presets: {presets}"}],
                "is_error": True
            }

        # Load existing preferences
        existing = {}
        if os.path.exists(PREFERENCES_FILE):
            try:
                with open(PREFERENCES_FILE, "r") as f:
                    existing = json.load(f)
            except Exception:
                pass

        theme = existing.get("theme", {})

        # Resolve accent color
        if accent_color_input:
            color, hover = resolve_color(accent_color_input, accent_hover_input)
            theme["accentColor"] = color
            theme["accentHover"] = hover

        # Set mode
        if mode:
            if mode not in ("light", "dark", "system"):
                return {
                    "content": [{"type": "text", "text": f"Invalid mode '{mode}'. Use 'light', 'dark', or 'system'."}],
                    "is_error": True
                }
            theme["mode"] = mode

        # Save to preferences file
        existing["theme"] = theme
        os.makedirs(os.path.dirname(PREFERENCES_FILE), exist_ok=True)
        with open(PREFERENCES_FILE, "w") as f:
            json.dump(existing, f, indent=2)

        # Build description for the response
        parts = []
        if accent_color_input:
            preset = ACCENT_PRESETS.get(accent_color_input.lower())
            if preset:
                parts.append(f"accent color to {accent_color_input.title()} ({preset['value']})")
            else:
                parts.append(f"accent color to {theme['accentColor']}")
        if mode:
            parts.append(f"mode to {mode}")

        return {
            "content": [{"type": "text", "text": f"Theme updated: {', '.join(parts)}. Applied to all connected clients."}]
        }

    except ValueError as e:
        return {
            "content": [{"type": "text", "text": str(e)}],
            "is_error": True
        }
    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error setting theme: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
