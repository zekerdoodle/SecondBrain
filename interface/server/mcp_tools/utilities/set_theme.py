"""
Set Theme tool - allows agents to change the UI theme color, mode, and palette.

Updates the server-side preferences file and broadcasts directly to all
connected clients via the broadcast broker (with retry for reliability).

Supports:
- Theme presets (curated color palettes like "midnight", "latte", "abyss")
- Accent color presets or custom hex colors
- Light/dark/system mode switching
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
    "rose": {"value": "#F43F5E", "hover": "#E11D48"},
    "pink": {"value": "#EC4899", "hover": "#DB2777"},
    "crimson": {"value": "#DC2626", "hover": "#B91C1C"},
    "orange": {"value": "#F97316", "hover": "#EA580C"},
    "amber": {"value": "#F59E0B", "hover": "#D97706"},
    "green": {"value": "#10B981", "hover": "#059669"},
    "teal": {"value": "#14B8A6", "hover": "#0D9488"},
    "cyan": {"value": "#06B6D4", "hover": "#0891B2"},
    "blue": {"value": "#3B82F6", "hover": "#2563EB"},
    "indigo": {"value": "#6366F1", "hover": "#4F46E5"},
    "purple": {"value": "#8B5CF6", "hover": "#7C3AED"},
}

# Theme presets (mirrors frontend THEME_PRESETS)
THEME_PRESETS = {
    # Light themes
    "parchment": {"mode": "light", "accent": {"color": "#D97757", "hover": "#C4684A"}},
    "paper": {"mode": "light", "accent": {"color": "#3B82F6", "hover": "#2563EB"}},
    "latte": {"mode": "light", "accent": {"color": "#D97757", "hover": "#C4684A"}},
    "frost": {"mode": "light", "accent": {"color": "#6366F1", "hover": "#4F46E5"}},
    # Dark themes
    "charcoal": {"mode": "dark", "accent": {"color": "#D97757", "hover": "#C4684A"}},
    "midnight": {"mode": "dark", "accent": {"color": "#3B82F6", "hover": "#2563EB"}},
    "ember": {"mode": "dark", "accent": {"color": "#F97316", "hover": "#EA580C"}},
    "abyss": {"mode": "dark", "accent": {"color": "#8B5CF6", "hover": "#7C3AED"}},
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
    description="""Set the UI theme palette, accent color, and/or mode.

Use this to change the look and feel of the Second Brain UI. Changes apply instantly
to all connected clients.

**Theme presets** — curated color palettes:
- Light: "parchment" (warm cream), "paper" (clean white), "latte" (coffee tones), "frost" (cool blue)
- Dark: "charcoal" (neutral dark), "midnight" (deep navy), "ember" (warm dark), "abyss" (pure black)

**Accent colors** — UI highlight color:
- Presets: "terracotta", "rose", "pink", "crimson", "orange", "amber", "green", "teal", "cyan", "blue", "indigo", "purple"
- Custom hex: "#FF6B35" (any valid 6-digit hex color)

**Mode**: "light", "dark", or "system" (follows OS preference).

Selecting a theme_preset automatically sets the mode and suggests a matching accent color.""",
    input_schema={
        "type": "object",
        "properties": {
            "theme_preset": {
                "type": "string",
                "description": 'Theme palette preset name (e.g. "midnight", "latte", "abyss")'
            },
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
    """Set the UI theme palette, accent color, and/or mode."""
    try:
        theme_preset_input = args.get("theme_preset")
        accent_color_input = args.get("accent_color")
        mode = args.get("mode")
        accent_hover_input = args.get("accent_hover")

        if not theme_preset_input and not accent_color_input and not mode:
            theme_names = ", ".join(THEME_PRESETS.keys())
            accent_names = ", ".join(ACCENT_PRESETS.keys())
            return {
                "content": [{"type": "text", "text": f"Please provide at least one of: theme_preset, accent_color, or mode.\n\nTheme presets: {theme_names}\nAccent presets: {accent_names}"}],
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

        # Handle theme preset
        if theme_preset_input:
            preset_key = theme_preset_input.lower()
            if preset_key not in THEME_PRESETS:
                available = ", ".join(THEME_PRESETS.keys())
                return {
                    "content": [{"type": "text", "text": f"Unknown theme preset '{theme_preset_input}'. Available: {available}"}],
                    "is_error": True
                }
            preset_info = THEME_PRESETS[preset_key]
            preset_mode = preset_info["mode"]

            # Set the preset for the appropriate mode
            if preset_mode == "light":
                theme["lightPreset"] = preset_key
            else:
                theme["darkPreset"] = preset_key

            # Set mode to match the preset (unless explicit mode provided)
            if not mode:
                theme["mode"] = preset_mode

            # Apply preset's default accent color (unless explicit accent provided)
            if not accent_color_input:
                theme["accentColor"] = preset_info["accent"]["color"]
                theme["accentHover"] = preset_info["accent"]["hover"]

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

        # Broadcast directly to all connected clients via broker (with retry)
        broadcast_ok = False
        try:
            from desk_broker import broadcast_theme_event
            # Send full theme state so clients can apply everything
            theme_payload = {}
            if accent_color_input or theme_preset_input:
                theme_payload["accentColor"] = theme.get("accentColor")
                theme_payload["accentHover"] = theme.get("accentHover")
            if mode or theme_preset_input:
                theme_payload["mode"] = theme.get("mode")
            if theme_preset_input:
                preset_mode = THEME_PRESETS[theme_preset_input.lower()]["mode"]
                if preset_mode == "light":
                    theme_payload["lightPreset"] = theme.get("lightPreset")
                else:
                    theme_payload["darkPreset"] = theme.get("darkPreset")
            if theme_payload:
                broadcast_ok = await broadcast_theme_event(theme_payload)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("SET_THEME: Broker broadcast failed", exc_info=True)

        # Build description for the response
        parts = []
        if theme_preset_input:
            parts.append(f"theme to {theme_preset_input.title()}")
        if accent_color_input:
            preset = ACCENT_PRESETS.get(accent_color_input.lower())
            if preset:
                parts.append(f"accent to {accent_color_input.title()} ({preset['value']})")
            else:
                parts.append(f"accent to {theme['accentColor']}")
        if mode:
            parts.append(f"mode to {mode}")

        broadcast_note = "" if broadcast_ok else " (Broadcast may not have reached all clients.)"
        return {
            "content": [{"type": "text", "text": f"Theme updated: {', '.join(parts)}. Applied to all connected clients.{broadcast_note}"}]
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
