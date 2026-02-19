"""
Fetch skill instructions on demand.

Pull-based skill system: Claude sees a lightweight menu of available skills
in its system prompt and calls fetch_skill when it decides a skill is needed.
Per-agent filtering is handled via _allowed_skills injected by closure.
"""

import logging
import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.skills.fetch")

# Ensure skill_injector is importable
_AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/agents"))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)


def _get_available_skill_names(allowed_skills) -> list[str]:
    """Get the list of skill names this agent can access."""
    from skill_injector import get_registry

    registry = get_registry()
    if allowed_skills is None:
        # None = all skills
        return sorted(registry.keys())
    else:
        return sorted(name for name in allowed_skills if name in registry)


@register_tool("skills")
@tool(
    name="fetch_skill",
    description=(
        "Load a skill's full instructions. Call this when the user references a "
        "skill (e.g., /sync, /red-team) or when you decide a skill would help "
        "with the current task. Returns the complete skill instructions wrapped "
        "in <skill-instructions> tags."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name to load (e.g., 'sync', 'red-team', 'compact')"
            }
        },
        "required": ["skill"]
    }
)
async def fetch_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch a skill's full instructions for the calling agent."""
    try:
        from skill_injector import get_registry, SKILL_ALIASES

        skill_name = args.get("skill", "").strip().lower()
        allowed_skills = args.get("_allowed_skills")  # Injected by closure

        if not skill_name:
            available = _get_available_skill_names(allowed_skills)
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: skill name is required.\n\n"
                        f"Available skills: {', '.join(available)}"
                    )
                }],
                "is_error": True
            }

        registry = get_registry()

        # Resolve aliases (e.g., "redteam" -> "red-team")
        canonical = skill_name
        if canonical not in registry and canonical in SKILL_ALIASES:
            canonical = SKILL_ALIASES[canonical]

        # Check existence
        if canonical not in registry:
            available = _get_available_skill_names(allowed_skills)
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Unknown skill: '{skill_name}'.\n\n"
                        f"Available skills: {', '.join(available)}"
                    )
                }],
                "is_error": True
            }

        # Check permission
        if allowed_skills is not None and canonical not in allowed_skills:
            available = _get_available_skill_names(allowed_skills)
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Permission denied: skill '{canonical}' is not available to this agent.\n\n"
                        f"Available skills: {', '.join(available)}"
                    )
                }],
                "is_error": True
            }

        # Success — return the full skill body
        entry = registry[canonical]
        logger.info(f"Fetched skill '{canonical}' ({len(entry.body)} chars)")

        return {
            "content": [{
                "type": "text",
                "text": (
                    f'<skill-instructions name="{canonical}">\n'
                    f"{entry.body}\n"
                    f"</skill-instructions>"
                )
            }]
        }

    except Exception as e:
        logger.error(f"fetch_skill error: {e}")
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "is_error": True
        }
