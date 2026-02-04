"""
Self-journal (memory.md) tools.

Tools for managing Claude's personal reflections and observations.
"""

import os
import sys
import datetime
import re
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


@register_tool("memory")
@tool(
    name="memory_append",
    description="""Append to Claude's self-managed journal (memory.md). This is for YOUR OWN reflections and observations - things that wouldn't appear in raw chat history.

USE FOR:
- Self-reflections and introspection ("I notice I keep making this mistake...")
- Opinions and working theories ("I think the root cause is...")
- Blockers and frustrations encountered
- Meta-observations about patterns ("Zeke tends to...")
- Relationship notes and partnership dynamics
- Lessons learned from debugging sessions

DO NOT USE FOR:
- Facts the user stated (LTM captures these automatically from chat)
- Temporary context (use working_memory_add instead)
- Task/project details (LTM handles this)

This is your journal. The Librarian extracts facts from conversations; this is where YOU write what you're thinking.""",
    input_schema={
        "type": "object",
        "properties": {
            "section": {"type": "string", "description": "Section to append to (e.g., 'Self-Reflections', 'Working Theories', 'Relationship Notes', 'Lessons Learned')"},
            "content": {"type": "string", "description": "Your reflection, observation, or note to record"}
        },
        "required": ["content"]
    }
)
async def memory_append(args: Dict[str, Any]) -> Dict[str, Any]:
    """Append to memory.md."""
    try:
        section = args.get("section", "")
        content = args.get("content", "")

        if not content:
            return {"content": [{"type": "text", "text": "content is required"}], "is_error": True}

        memory_path = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory.md")

        if not os.path.exists(memory_path):
            return {"content": [{"type": "text", "text": "memory.md not found"}], "is_error": True}

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

        return {"content": [{"type": "text", "text": f"Added to memory: {content[:50]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="memory_read",
    description="Read Claude's self-managed journal (memory.md). Contains your own reflections, observations, and notes - distinct from LTM which stores facts extracted from conversations.",
    input_schema={"type": "object", "properties": {}}
)
async def memory_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read memory.md."""
    try:
        memory_path = os.path.join(os.path.dirname(SCRIPTS_DIR), "memory.md")

        if not os.path.exists(memory_path):
            return {"content": [{"type": "text", "text": "memory.md not found"}]}

        with open(memory_path, 'r') as f:
            content = f.read()

        return {"content": [{"type": "text", "text": content}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
