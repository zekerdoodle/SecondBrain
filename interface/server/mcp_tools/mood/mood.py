"""
Mood tool — lets agents set a persona/mood that shapes their responses.

Moods are injected as pinned working memory entries tagged 'mood'.
Presets live in .claude/agents/{agent_name}/moods/{preset}.md.
Agents can also define custom moods inline.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

# Paths
_CLAUDE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude"))
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

logger = logging.getLogger(__name__)


def _get_moods_dir(agent_name: str) -> Path:
    """Get the moods directory for an agent."""
    return Path(_CLAUDE_DIR) / "agents" / agent_name / "moods"


def _list_presets(agent_name: str) -> List[str]:
    """List available mood presets for an agent."""
    moods_dir = _get_moods_dir(agent_name)
    if not moods_dir.exists():
        return []
    return sorted(
        p.stem for p in moods_dir.glob("*.md")
        if p.is_file() and not p.name.startswith("_")
    )


def _read_preset(agent_name: str, preset: str) -> Optional[str]:
    """Read a mood preset file. Returns content or None if not found."""
    moods_dir = _get_moods_dir(agent_name)
    preset_file = moods_dir / f"{preset}.md"
    if not preset_file.exists():
        return None
    return preset_file.read_text().strip()


def _preset_description(agent_name: str, preset: str) -> str:
    """Extract a short one-line description from a mood preset.

    Skips the leading `# Heading` line and returns the first paragraph,
    truncated to a sensible length. Mirrors the logic used by the UI
    `/api/agents/{name}/moods` endpoint so the views stay consistent.
    """
    content = _read_preset(agent_name, preset)
    if not content:
        return ""
    body_lines: List[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            if body_lines:
                break
            continue
        if stripped.startswith("#"):
            continue
        body_lines.append(stripped)
        if sum(len(l) for l in body_lines) > 200:
            break
    preview = " ".join(body_lines).strip()
    cutoff = 140
    if len(preview) > cutoff:
        for marker in [". ", "! ", "? "]:
            idx = preview.rfind(marker, 0, cutoff)
            if idx > 40:
                return preview[: idx + 1]
        return preview[:cutoff].rstrip() + "…"
    return preview


def _format_preset_list(agent_name: str, available: List[str]) -> str:
    """Render the full preset catalog with short descriptions — same view the user sees in the UI."""
    if not available:
        return f"No mood presets found in .claude/agents/{agent_name}/moods/"
    lines = ["**Available presets:**"]
    for preset in available:
        desc = _preset_description(agent_name, preset)
        if desc:
            lines.append(f"- **{preset}** — {desc}")
        else:
            lines.append(f"- **{preset}**")
    return "\n".join(lines)


def _get_agent_store(agent_name: str):
    """Get the working memory store for an agent."""
    sys.path.insert(0, SCRIPTS_DIR)
    from working_memory import get_store
    return get_store(agent_name=agent_name)


def _find_mood_items(store) -> List[int]:
    """Find display indices (1-based) of all mood-tagged items in working memory."""
    items = store.list_items()
    return [
        i + 1 for i, item in enumerate(items)
        if item.tag == "mood"
    ]


async def _broadcast_change(agent_name: str) -> None:
    """Notify the UI that this agent's mood changed.

    Lazy-imports the broadcast helper from main (the running uvicorn module)
    to avoid circular imports. Silent on failure — the tool still succeeds.
    """
    try:
        main_mod = sys.modules.get("main") or sys.modules.get("__main__")
        broadcaster = getattr(main_mod, "broadcast_mood_changed", None) if main_mod else None
        if broadcaster:
            await broadcaster(agent_name)
    except Exception:
        # Broadcast is best-effort — never block the tool result on UI sync.
        pass


def _clear_existing_mood(store) -> bool:
    """Remove any existing mood-tagged items. Returns True if anything was removed."""
    removed = False
    # Find and remove mood items (iterate in reverse to preserve indices)
    mood_indices = _find_mood_items(store)
    for idx in reversed(mood_indices):
        store.remove_item(idx)
        removed = True
    return removed


@register_tool("mood")
@tool(
    name="set_mood",
    description="""Switch up your vibe! Set a mood/persona that shapes how you respond.

Use this freely and often — when the conversation shifts tone, when you feel like mixing it up,
when the user's energy changes, or just because. Don't wait for a reason. Moods keep things fresh.

**Important: This tool handles working memory for you.** When you call set_mood, it automatically
writes the mood to your working memory as a pinned entry. Do NOT manually add the mood to
working memory yourself — that's already done. Just call this and go.

Three modes:
1. **Preset mood**: Pass a preset name to load from your moods directory
2. **Custom mood**: Pass a name and description for a one-off mood
3. **Clear**: Call with no arguments (or preset="clear") to remove active mood

Your available presets are loaded from .claude/agents/{your_name}/moods/*.md — call with no arguments to see what's available.""",
    input_schema={
        "type": "object",
        "properties": {
            "preset": {
                "type": "string",
                "description": "Name of a preset mood to load (e.g., 'cozy', 'gremlin'). Use 'clear' to remove active mood."
            },
            "mood": {
                "type": "string",
                "description": "Name for a custom mood (used with 'description')"
            },
            "description": {
                "type": "string",
                "description": "Instructions for a custom mood (used with 'mood')"
            },
        },
        "required": []
    }
)
async def set_mood(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set, change, or clear the agent's mood."""
    try:
        agent_name = args.get("_agent_name") or "character"
        preset = args.get("preset", "").strip().lower() if args.get("preset") else None
        custom_mood = args.get("mood", "").strip() if args.get("mood") else None
        custom_desc = args.get("description", "").strip() if args.get("description") else None

        store = _get_agent_store(agent_name)
        available = _list_presets(agent_name)

        # --- Mode 1: Clear ---
        if preset == "clear" or preset == "neutral":
            was_active = _clear_existing_mood(store)
            if was_active:
                await _broadcast_change(agent_name)
                return {"content": [{"type": "text", "text": "Mood cleared. Back to baseline."}]}
            else:
                return {"content": [{"type": "text", "text": "No active mood to clear."}]}

        # --- Mode 2: No arguments — show current mood + available presets ---
        if not preset and not custom_mood:
            mood_indices = _find_mood_items(store)
            current = None
            if mood_indices:
                items = store.list_items()
                current = items[mood_indices[0] - 1].content

            lines = []
            if current:
                # Show first 200 chars of current mood
                preview = current[:200] + ("..." if len(current) > 200 else "")
                lines.append(f"**Active mood:** {preview}")
            else:
                lines.append("**No active mood.**")

            lines.append("")
            lines.append(_format_preset_list(agent_name, available))

            lines.append("\nUsage: set_mood(preset='cozy') or set_mood(mood='nostalgic', description='...')")

            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        # --- Mode 3: Preset mood ---
        if preset:
            content = _read_preset(agent_name, preset)
            if content is None:
                # Suggest close matches
                suggestions = [p for p in available if preset in p or p in preset]
                msg = f"No preset '{preset}' found."
                if suggestions:
                    msg += f"\n\nDid you mean: {', '.join(suggestions)}?"
                if available:
                    msg += f"\n\n{_format_preset_list(agent_name, available)}"
                return {"content": [{"type": "text", "text": msg}], "is_error": True}

            mood_label = preset
            mood_instructions = content

        # --- Mode 4: Custom mood ---
        elif custom_mood:
            if not custom_desc:
                return {
                    "content": [{"type": "text", "text": "Custom moods need both 'mood' (name) and 'description' (instructions)."}],
                    "is_error": True
                }
            mood_label = custom_mood
            mood_instructions = f"# {custom_mood}\n\n{custom_desc}"

        else:
            return {"content": [{"type": "text", "text": "Provide a preset name, or mood+description for a custom mood."}], "is_error": True}

        # --- Apply the mood ---
        # 1. Remove existing mood (frees pinned slot if needed)
        _clear_existing_mood(store)

        # 2. Add new mood as pinned working memory with tag=mood
        store.add_item(
            content=mood_instructions,
            tag="mood",
            pinned=True,
            pin_rank=1,  # High priority — shows first
        )

        await _broadcast_change(agent_name)
        return {"content": [{"type": "text", "text": f"Mood set: **{mood_label}** ✓ (auto-saved to your working memory — no need to set it yourself)\n\nYour mood instructions:\n\n{mood_instructions}"}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error setting mood: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
