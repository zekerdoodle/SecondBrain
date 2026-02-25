"""
Agent tools package.

Tools for invoking and scheduling agents.

The agent list is built once by ``build_agent_list_block()`` and injected
into the **system prompt** (not tool descriptions) by the caller — see
``claude_wrapper.py`` and ``runner.py``.  This keeps the list in one place,
appearing exactly once per agent session, and *only* for agents that have
access to at least one agent-calling tool.
"""

import os
import sys

# Add agents directory to path (needed for registry import)
AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/agents"))
if AGENTS_DIR not in sys.path:
    sys.path.insert(0, AGENTS_DIR)


# Agent tool names that signal "this agent has access to other agents"
AGENT_TOOL_NAMES = {"invoke_agent", "invoke_agent_chain", "invoke_agent_parallel", "schedule_agent"}

# MCP-prefixed versions (mcp__brain__invoke_agent etc.)
AGENT_MCP_TOOL_NAMES = {f"mcp__brain__{t}" for t in AGENT_TOOL_NAMES}


def build_agent_list_block() -> tuple[str, list[str]]:
    """Build the shared 'Available agents' block and name list.

    Returns:
        (description_block, all_agent_names)
        - description_block: Human-readable block for system prompt injection
        - all_agent_names: Full list of agent names (including hidden) for schema enums
    """
    from registry import get_registry

    registry = get_registry()
    all_agents = registry.get_all_configs()
    all_background = registry.get_all_background_configs()
    combined = {**all_agents, **all_background}

    # Visible agents for the description
    visible = {k: v for k, v in combined.items() if not v.hidden}
    agent_lines = []
    for name, config in sorted(visible.items()):
        desc = config.description or "No description"
        display = config.display_name or name
        agent_lines.append(f"- {display}: {desc}")
    agent_list = "\n".join(agent_lines)

    # All names (including hidden) for schema enum validation
    agent_names = list(combined.keys())

    description_block = f"""Available agents (applies to invoke_agent, invoke_agent_chain, invoke_agent_parallel, and schedule_agent):
{agent_list}"""

    return description_block, agent_names


def get_agent_list_for_prompt(tool_names: list[str]) -> str:
    """Return the agent list block if tool_names includes any agent-calling tools.

    Call this when building a system prompt. Pass the agent's full tool list
    (either internal names like "invoke_agent" or MCP names like "mcp__brain__invoke_agent").
    Returns the block to append, or empty string if the agent has no agent access.

    Args:
        tool_names: List of tool names the agent has access to.

    Returns:
        Agent list block string to append to system prompt, or "".
    """
    tool_set = set(tool_names)
    has_agent_tools = bool(tool_set & AGENT_TOOL_NAMES) or bool(tool_set & AGENT_MCP_TOOL_NAMES)
    if not has_agent_tools:
        return ""

    block, _ = build_agent_list_block()
    return f"\n\n{block}"


from .invoke import invoke_agent, invoke_agent_chain, invoke_agent_parallel
from .scheduler import schedule_agent

__all__ = [
    "invoke_agent",
    "invoke_agent_chain",
    "invoke_agent_parallel",
    "schedule_agent",
    "build_agent_list_block",
    "get_agent_list_for_prompt",
    "AGENT_TOOL_NAMES",
    "AGENT_MCP_TOOL_NAMES",
]
