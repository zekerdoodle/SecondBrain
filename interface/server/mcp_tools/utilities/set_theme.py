"""
Set Theme tool - allows agents to change the UI theme color, mode, and palette.

Updates the server-side preferences file and broadcasts directly to all
connected clients via the broadcast broker (with retry for reliability).

Supports:
- Theme presets (curated color palettes like "midnight", "latte", "abyss")
- Custom user-saved themes (by id or name)
- Creating a new custom theme inline (full palette + accent)
- Accent color presets or custom hex colors
- Light/dark/system mode switching
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

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


# ── Custom theme palette fields ────────────────────────────────────────────
# Mirrors the ThemePresetColors interface in the frontend (camelCase).
PALETTE_FIELDS = [
    "bgPrimary", "bgSecondary", "bgTertiary",
    "textPrimary", "textSecondary", "textMuted",
    "borderColor", "borderHover",
    "codeBg", "preBg", "preText",
    "scrollbarThumb", "scrollbarThumbHover",
    "userText",
]

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _normalize_hex(value: str, field: str) -> str:
    """Validate + normalize a hex color string. Raises ValueError on bad input."""
    if not isinstance(value, str):
        raise ValueError(f"Color for '{field}' must be a string, got {type(value).__name__}")
    v = value if value.startswith("#") else f"#{value}"
    if not _HEX_RE.match(v):
        raise ValueError(f"Invalid hex color for '{field}': {value!r}. Use 6-digit hex like '#3B82F6'.")
    return v.upper()


def _slugify(name: str) -> str:
    """Make a URL-safe slug from a theme name."""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s[:32] or "custom"


def _find_theme_in_customs(
    customs: List[Dict[str, Any]],
    needle: str,
) -> Optional[Dict[str, Any]]:
    """Match a custom theme by id (exact) or name (case-insensitive)."""
    n_lower = needle.lower()
    for t in customs:
        if t.get("id") == needle:
            return t
        if (t.get("name") or "").lower() == n_lower:
            return t
    return None


def build_custom_theme(spec: Dict[str, Any], existing_customs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a custom-theme dict from a partial spec.

    Required: spec["name"], spec["mode"] ("light"|"dark"), spec["colors"] (full palette).
    Optional: spec["accent"] = {"color": "#hex", "hover": "#hex"} — defaults to a sensible value.

    If a custom theme with the same name already exists, its id is reused
    (treated as an in-place update).
    """
    name = (spec.get("name") or "").strip()
    if not name:
        raise ValueError("custom_theme.name is required")

    mode = spec.get("mode")
    if mode not in ("light", "dark"):
        raise ValueError("custom_theme.mode must be 'light' or 'dark'")

    colors_in = spec.get("colors") or {}
    if not isinstance(colors_in, dict):
        raise ValueError("custom_theme.colors must be an object")

    missing = [f for f in PALETTE_FIELDS if f not in colors_in]
    if missing:
        raise ValueError(
            f"custom_theme.colors is missing required fields: {', '.join(missing)}. "
            f"All {len(PALETTE_FIELDS)} palette fields required: {', '.join(PALETTE_FIELDS)}"
        )

    colors_out = {f: _normalize_hex(colors_in[f], f) for f in PALETTE_FIELDS}

    accent_in = spec.get("accent") or {}
    accent_color = accent_in.get("color") or ("#D97757" if mode == "light" else "#3B82F6")
    accent_color = _normalize_hex(accent_color, "accent.color")
    accent_hover = accent_in.get("hover") or _darken_hex(accent_color)
    accent_hover = _normalize_hex(accent_hover, "accent.hover")

    # Reuse id if a theme with this name already exists (update in place)
    existing = _find_theme_in_customs(existing_customs, name)
    theme_id = existing["id"] if existing and existing.get("id") else f"custom-{_slugify(name)}-{int(time.time() * 1000):x}"

    return {
        "id": theme_id,
        "name": name,
        "mode": mode,
        "colors": colors_out,
        "defaultAccent": {"color": accent_color, "hover": accent_hover},
    }


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
- User-saved custom themes can also be selected by id or name.

**Accent colors** — UI highlight color:
- Presets: "terracotta", "rose", "pink", "crimson", "orange", "amber", "green", "teal", "cyan", "blue", "indigo", "purple"
- Custom hex: "#FF6B35" (any valid 6-digit hex color)

**Mode**: "light", "dark", or "system" (follows OS preference).

**Creating a custom theme** — pass `custom_theme` with a full palette to save it
and apply it. If a theme with the same name already exists, it's updated in place.
The 14 palette fields are: bgPrimary, bgSecondary, bgTertiary, textPrimary,
textSecondary, textMuted, borderColor, borderHover, codeBg, preBg, preText,
scrollbarThumb, scrollbarThumbHover, userText (color of text on user message
bubbles — typically white, but pick something darker if your accent is pale).
All required. accent is optional.

Selecting a theme_preset automatically sets the mode and suggests a matching accent color.""",
    input_schema={
        "type": "object",
        "properties": {
            "theme_preset": {
                "type": "string",
                "description": 'Theme palette preset name (built-in like "midnight" or a saved custom theme id/name)'
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
            },
            "custom_theme": {
                "type": "object",
                "description": "Create (or update) a custom theme with a full palette, then apply it.",
                "properties": {
                    "name": {"type": "string", "description": "Display name (required). Updates existing theme with same name."},
                    "mode": {"type": "string", "enum": ["light", "dark"], "description": "Light or dark mode (required)."},
                    "colors": {
                        "type": "object",
                        "description": "All 13 palette colors as 6-digit hex strings.",
                        "properties": {f: {"type": "string"} for f in PALETTE_FIELDS},
                        "required": PALETTE_FIELDS,
                    },
                    "accent": {
                        "type": "object",
                        "description": "Default accent for this theme (optional).",
                        "properties": {
                            "color": {"type": "string"},
                            "hover": {"type": "string"},
                        },
                    },
                },
                "required": ["name", "mode", "colors"],
            },
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
        custom_theme_input = args.get("custom_theme")

        if not any([theme_preset_input, accent_color_input, mode, custom_theme_input]):
            theme_names = ", ".join(THEME_PRESETS.keys())
            accent_names = ", ".join(ACCENT_PRESETS.keys())
            return {
                "content": [{"type": "text", "text": f"Please provide at least one of: theme_preset, accent_color, mode, or custom_theme.\n\nTheme presets: {theme_names}\nAccent presets: {accent_names}"}],
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
        custom_themes = list(theme.get("customThemes") or [])

        # Track what was set for the response message + broadcast payload
        custom_created: Optional[Dict[str, Any]] = None
        preset_resolved_mode: Optional[str] = None  # "light" or "dark"
        preset_resolved_id: Optional[str] = None
        preset_resolved_label: Optional[str] = None  # display name for response

        # 1) Handle custom_theme creation (must run first so theme_preset can reference it)
        if custom_theme_input:
            try:
                built = build_custom_theme(custom_theme_input, custom_themes)
            except ValueError as e:
                return {
                    "content": [{"type": "text", "text": f"Invalid custom_theme: {e}"}],
                    "is_error": True,
                }
            # Upsert into customThemes
            idx = next((i for i, t in enumerate(custom_themes) if t.get("id") == built["id"]), -1)
            if idx >= 0:
                custom_themes[idx] = built
            else:
                custom_themes.append(built)
            custom_created = built
            # Auto-activate the newly-created theme (unless theme_preset was also passed explicitly)
            if not theme_preset_input:
                preset_resolved_mode = built["mode"]
                preset_resolved_id = built["id"]
                preset_resolved_label = built["name"]
                if built["mode"] == "light":
                    theme["lightPreset"] = built["id"]
                else:
                    theme["darkPreset"] = built["id"]
                if not mode:
                    theme["mode"] = built["mode"]
                if not accent_color_input:
                    theme["accentColor"] = built["defaultAccent"]["color"]
                    theme["accentHover"] = built["defaultAccent"]["hover"]

        # 2) Handle theme preset (built-in OR custom by id/name)
        if theme_preset_input:
            preset_key = theme_preset_input.lower()

            if preset_key in THEME_PRESETS:
                # Built-in preset
                preset_info = THEME_PRESETS[preset_key]
                preset_resolved_mode = preset_info["mode"]
                preset_resolved_id = preset_key
                preset_resolved_label = preset_key.title()
                if preset_info["mode"] == "light":
                    theme["lightPreset"] = preset_key
                else:
                    theme["darkPreset"] = preset_key
                if not mode:
                    theme["mode"] = preset_info["mode"]
                if not accent_color_input:
                    theme["accentColor"] = preset_info["accent"]["color"]
                    theme["accentHover"] = preset_info["accent"]["hover"]
            else:
                # Look up custom theme by id or name (case-insensitive)
                match = _find_theme_in_customs(custom_themes, theme_preset_input)
                if not match:
                    available_builtin = ", ".join(THEME_PRESETS.keys())
                    available_custom = ", ".join(t.get("name", t.get("id", "?")) for t in custom_themes) or "(none)"
                    return {
                        "content": [{"type": "text", "text": f"Unknown theme preset '{theme_preset_input}'.\n\nBuilt-in: {available_builtin}\nCustom: {available_custom}"}],
                        "is_error": True
                    }
                preset_resolved_mode = match["mode"]
                preset_resolved_id = match["id"]
                preset_resolved_label = match.get("name", match["id"])
                if match["mode"] == "light":
                    theme["lightPreset"] = match["id"]
                else:
                    theme["darkPreset"] = match["id"]
                if not mode:
                    theme["mode"] = match["mode"]
                if not accent_color_input:
                    accent_def = match.get("defaultAccent") or {}
                    if accent_def.get("color"):
                        theme["accentColor"] = accent_def["color"]
                    if accent_def.get("hover"):
                        theme["accentHover"] = accent_def["hover"]

        # 3) Resolve accent color (explicit overrides)
        if accent_color_input:
            color, hover = resolve_color(accent_color_input, accent_hover_input)
            theme["accentColor"] = color
            theme["accentHover"] = hover

        # 4) Set mode
        if mode:
            if mode not in ("light", "dark", "system"):
                return {
                    "content": [{"type": "text", "text": f"Invalid mode '{mode}'. Use 'light', 'dark', or 'system'."}],
                    "is_error": True
                }
            theme["mode"] = mode

        # Persist updated customThemes back into theme
        if custom_created is not None or custom_themes != (existing.get("theme", {}).get("customThemes") or []):
            theme["customThemes"] = custom_themes

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
            theme_payload: Dict[str, Any] = {}
            if accent_color_input or theme_preset_input or custom_created:
                theme_payload["accentColor"] = theme.get("accentColor")
                theme_payload["accentHover"] = theme.get("accentHover")
            if mode or theme_preset_input or custom_created:
                theme_payload["mode"] = theme.get("mode")
            if preset_resolved_mode == "light":
                theme_payload["lightPreset"] = theme.get("lightPreset")
            elif preset_resolved_mode == "dark":
                theme_payload["darkPreset"] = theme.get("darkPreset")
            # If a custom theme was created or updated, send the full customThemes list
            if custom_created is not None:
                theme_payload["customThemes"] = custom_themes
            if theme_payload:
                broadcast_ok = await broadcast_theme_event(theme_payload)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("SET_THEME: Broker broadcast failed", exc_info=True)

        # Build description for the response
        parts = []
        if custom_created:
            parts.append(f"created custom theme '{custom_created['name']}'")
        if theme_preset_input:
            parts.append(f"theme to {preset_resolved_label}")
        elif custom_created and preset_resolved_id:
            parts.append(f"activated '{preset_resolved_label}'")
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
