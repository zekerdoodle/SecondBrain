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


def create_mcp_server(
    name: str = "second_brain",
    version: str = "1.0.0",
    include_categories: Optional[List[str]] = None,
    include_tools: Optional[List[str]] = None,
    exclude_tools: Optional[List[str]] = None,
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

    logger.info(f"Creating MCP server '{name}' with {len(tools)} tools")

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
    from . import scheduler
    from . import memory
    from . import utilities
    from . import agents
    from . import bash
    from . import forms
    from . import moltbook
    from . import llm


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
