"""
Self-journal (memory.md) tools.

Agent-aware: each agent gets its own memory file. The path is resolved at
runtime based on the ``_agent_name`` key injected by ``_inject_agent_context``
in ``mcp_tools/__init__.py``.

- Primary agent (ren / no _agent_name): ``.claude/memory.md``
- Other agents: ``.claude/agents/{name}/memory.md``
"""

import logging
import os
import sys
import datetime
import re
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.memory.journal")

# Project root — two levels up from .claude/scripts
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Base paths
_CLAUDE_DIR = os.path.dirname(SCRIPTS_DIR)  # .claude/
_PRIMARY_MEMORY = os.path.join(_CLAUDE_DIR, "memory.md")


def _resolve_memory_path(args: Dict[str, Any]) -> str:
    """Return the memory.md path for the calling agent.

    If ``_agent_name`` was injected (non-primary agent), use the per-agent
    file.  Otherwise fall back to the primary agent's memory.md.
    """
    agent_name = args.get("_agent_name")
    if agent_name:
        return os.path.join(_CLAUDE_DIR, "agents", agent_name, "memory.md")
    return _PRIMARY_MEMORY


@register_tool("journal")
@tool(
    name="memory_append",
    description="""Append a note to your persistent memory file. This file is loaded into your context at the start of every conversation, so anything you save here you'll remember next time.

Each agent has its own isolated memory — you only see yours.

SAVE things like:
- Lessons learned ("Running build before lint catches more issues")
- User preferences you've discovered ("the user prefers forms over chat Q&A")
- Codebase conventions ("This project uses snake_case for Python, camelCase for TS")
- Working theories and opinions ("I think the flaky test is a race condition in...")
- Patterns you've noticed ("When the user says 'clean it up' he means extract helpers")
- Anything you'd want to remember across sessions

DON'T SAVE:
- Temporary context for this session only (use working_memory_add)
- Raw facts the user stated (LTM captures those from chat automatically)

Keep entries concise — bullet points, not paragraphs. This goes into your context window every session.""",
    input_schema={
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "Section heading to append under (e.g., 'Lessons Learned', 'Codebase Conventions', 'User Preferences'). Creates the section if it doesn't exist."
            },
            "content": {
                "type": "string",
                "description": "The note to save. Keep it concise — one bullet point."
            }
        },
        "required": ["content"]
    }
)
async def memory_append(args: Dict[str, Any]) -> Dict[str, Any]:
    """Append to the calling agent's memory.md."""
    try:
        section = args.get("section", "")
        content = args.get("content", "")

        if not content:
            return {"content": [{"type": "text", "text": "content is required"}], "is_error": True}

        memory_path = _resolve_memory_path(args)
        agent_label = args.get("_agent_name", "primary")

        # Auto-create if the file doesn't exist yet (agent memory files start empty)
        if not os.path.exists(memory_path):
            os.makedirs(os.path.dirname(memory_path), exist_ok=True)
            with open(memory_path, 'w') as f:
                f.write("")
            logger.info(f"Created memory file for agent '{agent_label}': {memory_path}")

        with open(memory_path, 'r') as f:
            memory_content = f.read()

        # Find section and append
        if section:
            section_marker = f"## {section}"
            if section_marker in memory_content:
                # Find the end of this section (next ## or end of file)
                start_idx = memory_content.index(section_marker) + len(section_marker)
                next_section = memory_content.find("\n## ", start_idx)
                if next_section == -1:
                    next_section = memory_content.find("\n---", start_idx)

                if next_section != -1:
                    # Insert before next section
                    memory_content = (
                        memory_content[:next_section].rstrip() +
                        f"\n- {content}\n" +
                        memory_content[next_section:]
                    )
                else:
                    # Append at end of file
                    memory_content = memory_content.rstrip() + f"\n- {content}\n"
            else:
                # Section not found, add it
                memory_content = memory_content.rstrip() + f"\n\n## {section}\n\n- {content}\n"
        else:
            # Just append at end
            memory_content = memory_content.rstrip() + f"\n- {content}\n"

        # Update timestamp
        today = datetime.date.today().isoformat()
        if "*Last updated:" in memory_content:
            memory_content = re.sub(
                r'\*Last updated: .*\*',
                f'*Last updated: {today}*',
                memory_content
            )

        with open(memory_path, 'w') as f:
            f.write(memory_content)

        logger.info(f"[{agent_label}] memory_append: {content[:60]}")
        return {"content": [{"type": "text", "text": f"Saved to memory ({agent_label}): {content[:80]}..."}]}

    except Exception as e:
        logger.error(f"memory_append error: {e}")
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("journal")
@tool(
    name="memory_read",
    description="""Read your persistent memory file. This is the same content that gets loaded into your context at conversation start, but you can re-read it mid-conversation if needed (e.g., after appending to verify).""",
    input_schema={"type": "object", "properties": {}}
)
async def memory_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read the calling agent's memory.md."""
    try:
        memory_path = _resolve_memory_path(args)
        agent_label = args.get("_agent_name", "primary")

        if not os.path.exists(memory_path):
            return {"content": [{"type": "text", "text": f"No memory file found for '{agent_label}'. Use memory_append to create one."}]}

        with open(memory_path, 'r') as f:
            content = f.read()

        if not content.strip():
            return {"content": [{"type": "text", "text": f"Memory file for '{agent_label}' is empty. Use memory_append to start saving notes."}]}

        return {"content": [{"type": "text", "text": content}]}

    except Exception as e:
        logger.error(f"memory_read error: {e}")
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
