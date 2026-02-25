"""
MCP Tools Module

Modular MCP tool definitions for Second Brain.

Usage:
    from mcp_tools import create_mcp_server

    # Create server with all tools
    server = create_mcp_server()

    # Create server with specific categories
    server = create_mcp_server(include_categories=["google", "scheduler"])

    # Create server with specific tools
    server = create_mcp_server(include_tools=["google_list", "schedule_self"])
"""

import logging
from typing import List, Optional

from claude_agent_sdk import create_sdk_mcp_server

from .registry import (
    get_all_tools,
    get_tools_by_category,
    get_tools_by_names,
    get_tool_names_by_category,
    list_categories,
    list_tools,
    get_tool_count,
)
from .constants import (
    MCP_SERVER_NAME,
    MCP_PREFIX,
    TOOL_CATEGORIES,
    ALL_TOOL_NAMES,
    ALL_MCP_TOOLS,
    get_mcp_tools_for_categories,
    is_valid_mcp_tool,
)

logger = logging.getLogger("mcp_tools")


def _inject_chat_context(tools, chat_id: str):
    """Wrap MCP tool handlers that need chat context with closures capturing the chat_id.

    This enables concurrent chat support: each ClaudeWrapper gets its own MCP server
    with tool handlers that know which chat they belong to, eliminating the need for
    a global CURRENT_CHAT_ID env var.
    """
    from claude_agent_sdk import SdkMcpTool

    # Tools that need to know their source chat_id
    CONTEXT_TOOLS = {"invoke_agent", "invoke_agent_chain", "invoke_agent_parallel"}

    wrapped = []
    for t in tools:
        tool_name = getattr(t, 'name', getattr(t, '__name__', ''))
        if tool_name in CONTEXT_TOOLS:
            original_handler = t.handler

            # Create closure that captures both handler and chat_id
            def _make_wrapper(handler, cid):
                async def wrapper(args):
                    args["_source_chat_id"] = cid
                    return await handler(args)
                return wrapper

            wrapped.append(SdkMcpTool(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                handler=_make_wrapper(original_handler, chat_id),
                annotations=getattr(t, 'annotations', None),
            ))
        else:
            wrapped.append(t)
    return wrapped


def _inject_agent_context(tools, agent_name: str):
    """Wrap agent-context-sensitive tool handlers to inject the calling agent's name.

    Injects ``_agent_name`` into the args dict so that:
    - ``memory_create/update/delete/search`` target ``.claude/agents/{name}/memories.json``.
    - ``schedule_self`` creates an agent-type scheduled task dispatched via the agent runner.
    """
    from claude_agent_sdk import SdkMcpTool

    AGENT_CONTEXT_TOOLS = {
        "memory_create", "memory_update", "memory_delete",
        "memory_search", "memory_search_agent",
        "schedule_self",
        "working_memory_add", "working_memory_update",
        "working_memory_remove", "working_memory_list",
        "working_memory_snapshot",
    }

    wrapped = []
    for t in tools:
        tool_name = getattr(t, 'name', getattr(t, '__name__', ''))
        if tool_name in AGENT_CONTEXT_TOOLS:
            original_handler = t.handler

            def _make_wrapper(handler, name):
                async def wrapper(args):
                    args["_agent_name"] = name
                    return await handler(args)
                return wrapper

            wrapped.append(SdkMcpTool(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                handler=_make_wrapper(original_handler, agent_name),
                annotations=getattr(t, 'annotations', None),
            ))
        else:
            wrapped.append(t)
    return wrapped



def _inject_skill_context(tools, allowed_skills):
    """Wrap fetch_skill handler with a closure that injects the agent's allowed skills.

    Same pattern as ``_inject_agent_context`` — the closure captures the per-agent
    ``allowed_skills`` list and injects it as ``_allowed_skills`` in the args dict.

    Args:
        tools: List of SdkMcpTool objects
        allowed_skills: None (all skills) or list of skill names this agent can access
    """
    from claude_agent_sdk import SdkMcpTool

    SKILL_TOOLS = {"fetch_skill"}

    wrapped = []
    for t in tools:
        tool_name = getattr(t, 'name', getattr(t, '__name__', ''))
        if tool_name in SKILL_TOOLS:
            original_handler = t.handler

            def _make_wrapper(handler, skills):
                async def wrapper(args):
                    args["_allowed_skills"] = skills
                    return await handler(args)
                return wrapper

            wrapped.append(SdkMcpTool(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                handler=_make_wrapper(original_handler, allowed_skills),
                annotations=getattr(t, 'annotations', None),
            ))
        else:
            wrapped.append(t)
    return wrapped


def create_mcp_server(
    name: str = "second_brain",
    version: str = "1.0.0",
    include_categories: Optional[List[str]] = None,
    include_tools: Optional[List[str]] = None,
    exclude_tools: Optional[List[str]] = None,
    chat_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    allowed_skills=None,
):
    """
    Create MCP server with specified tools.

    Args:
        name: Server name (default: "second_brain")
        version: Server version (default: "1.0.0")
        include_categories: List of category names to include (e.g., ["google", "scheduler"])
                           If None and include_tools is None, includes all tools
        include_tools: List of specific tool names to include
                      Takes precedence over include_categories
        exclude_tools: List of tool names to exclude from the final set
        chat_id: Source chat ID for concurrent session support. When provided,
                tool handlers that need chat context (invoke_agent, invoke_agent_chain)
                get wrapped with closures that inject this ID, eliminating the need
                for the global CURRENT_CHAT_ID env var.
        agent_name: Agent name for memory isolation. When provided, memory_create/
                   update/delete/search target .claude/agents/{name}/memories.json.
        allowed_skills: Per-agent skill filter for fetch_skill tool.
                       None = all skills, list = only these skills.
                       Sentinel "NO_SKILLS" string skips skill context injection entirely.

    Returns:
        MCP server instance
    """
    # Determine which tools to include
    if include_tools is not None:
        tools = get_tools_by_names(include_tools)
    elif include_categories is not None:
        tools = get_tools_by_category(include_categories)
    else:
        tools = get_all_tools()

    # Apply exclusions
    if exclude_tools:
        exclude_set = set(exclude_tools)
        tools = [t for t in tools if getattr(t, 'name', getattr(t, '__name__', '')) not in exclude_set]

    # Inject chat context for concurrent session support
    if chat_id:
        tools = _inject_chat_context(tools, chat_id)

    # Inject agent context for memory isolation
    if agent_name:
        tools = _inject_agent_context(tools, agent_name)

    # Inject skill context (allowed_skills filtering) for fetch_skill tool
    # Skip if allowed_skills is the sentinel "NO_SKILLS" (agent has skills: [])
    if allowed_skills != "NO_SKILLS":
        tools = _inject_skill_context(tools, allowed_skills)

    logger.info(f"Creating MCP server '{name}' with {len(tools)} tools (chat_id={chat_id}, agent={agent_name})")

    return create_sdk_mcp_server(
        name=name,
        version=version,
        tools=tools
    )


# Import all tool modules to trigger registration
# These imports must come after the registry is defined
def _load_all_tools():
    """Load all tool modules to register them with the registry."""
    from . import google
    from . import gmail
    from . import youtube
    from . import spotify
    from . import finance
    from . import scheduler
    from . import memory
    from . import utilities
    from . import agents
    from . import bash
    from . import forms
    from . import moltbook
    from . import llm
    from . import chess
    from . import image
    from . import skills


# Auto-load tools when module is imported
try:
    _load_all_tools()
except ImportError as e:
    logger.warning(f"Some tool modules failed to load: {e}")


# Backward compatibility: original function name
def create_second_brain_tools():
    """
    Create the MCP server with all Second Brain tools.

    This is the original function name, kept for backward compatibility.
    New code should use create_mcp_server() instead.
    """
    return create_mcp_server()


# Public API
__all__ = [
    # Server creation
    "create_mcp_server",
    "create_second_brain_tools",  # Backward compatibility
    # Registry functions
    "get_all_tools",
    "get_tools_by_category",
    "get_tools_by_names",
    "get_tool_names_by_category",
    "list_categories",
    "list_tools",
    "get_tool_count",
    # Constants
    "MCP_SERVER_NAME",
    "MCP_PREFIX",
    "TOOL_CATEGORIES",
    "ALL_TOOL_NAMES",
    "ALL_MCP_TOOLS",
    "get_mcp_tools_for_categories",
    "is_valid_mcp_tool",
]
