"""
Agent scheduling tool.

Tool for scheduling agents to run at specific times or intervals.
"""

import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Add agents directory to path for registry access
AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/agents"))
if AGENTS_DIR not in sys.path:
    sys.path.insert(0, AGENTS_DIR)


def _build_schedule_tool_schema():
    """Build tool schema dynamically from registry."""
    from registry import get_registry

    registry = get_registry()
    all_agents = registry.get_all_configs()
    all_background = registry.get_all_background_configs()
    combined = {**all_agents, **all_background}

    # Build description
    agent_lines = []
    for name, config in sorted(combined.items()):
        desc = config.description or "No description"
        agent_lines.append(f"- {name}: {desc}")

    agent_list = "\n".join(agent_lines)
    agent_names = list(combined.keys())

    description = f"""Schedule an agent to run at a specific time or interval.

Available agents:
{agent_list}

Schedule formats:
- "every X minutes" - Run every X minutes
- "every X hours" - Run every X hours
- "daily at HH:MM" - Run daily at specific time (24h format)
- "daily at HH:MMam/pm" - Run daily at specific time (12h format)
- "once at YYYY-MM-DDTHH:MM:SS" - Run once at specific datetime
- Cron syntax: "minute hour day-of-month month day-of-week" (e.g., "30 2 * * *" for daily at 2:30am)

Visibility: By default (silent=true), scheduled agents run in the background. Their output is written
to 00_Inbox/agent_outputs/ for async review during syncs. Set silent=false to create a visible chat
with notifications when the agent completes — useful for user-facing tasks like news briefings.

Output routing: If room_id is specified, agent output is delivered directly to that room."""

    schema = {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "enum": agent_names,
                "description": "Agent to schedule"
            },
            "prompt": {
                "type": "string",
                "description": "Task description for the agent"
            },
            "schedule": {
                "type": "string",
                "description": "When to run: 'every X minutes', 'daily at HH:MM', 'once at DATETIME', or cron syntax"
            },
            "room_id": {
                "type": "string",
                "description": "Optional: Target room ID. If specified, agent output will be delivered to this room instead of 00_Inbox/agent_outputs/"
            },
            "silent": {
                "type": "boolean",
                "description": "If true (default), agent runs in background without visible chat or notifications. If false, creates a visible chat and sends notifications when done."
            },
            "project": {
                "description": "Optional: Target project for output routing. When specified, agent output is tagged with YAML frontmatter for automatic routing to the project's _status.md during morning sync.",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}}
                ]
            }
        },
        "required": ["agent", "prompt", "schedule"]
    }

    return description, schema


_SCHEDULE_DESCRIPTION, _SCHEDULE_SCHEMA = _build_schedule_tool_schema()


@register_tool("agents")
@tool(name="schedule_agent", description=_SCHEDULE_DESCRIPTION, input_schema=_SCHEDULE_SCHEMA)
async def schedule_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    """Schedule an agent to run at specified times."""
    try:
        import scheduler_tool

        agent_name = args.get("agent", "")
        prompt = args.get("prompt", "")
        schedule = args.get("schedule", "")
        room_id = args.get("room_id")
        silent = args.get("silent", True)
        project = args.get("project")

        if not agent_name:
            return {"content": [{"type": "text", "text": "Error: agent is required"}], "is_error": True}

        if not prompt:
            return {"content": [{"type": "text", "text": "Error: prompt is required"}], "is_error": True}

        if not schedule:
            return {"content": [{"type": "text", "text": "Error: schedule is required"}], "is_error": True}

        result = scheduler_tool.add_agent_task(
            agent=agent_name,
            prompt=prompt,
            schedule_text=schedule,
            room_id=room_id,
            silent=silent,
            project=project
        )

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error scheduling agent: {str(e)}"}], "is_error": True}
