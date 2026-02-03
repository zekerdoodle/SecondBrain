"""
MCP Tools - Backward Compatibility Shim

This file provides backward compatibility for imports from the old monolithic mcp_tools.py.
All tools have been migrated to the mcp_tools/ module.

New code should import from mcp_tools/ directly:
    from mcp_tools import create_mcp_server, create_second_brain_tools
"""

import warnings

# Re-export everything from the new modular package
from mcp_tools import (
    # Server creation
    create_mcp_server,
    create_second_brain_tools,
    # Registry functions
    get_all_tools,
    get_tools_by_category,
    get_tools_by_names,
    get_tool_names_by_category,
    list_categories,
    list_tools,
    get_tool_count,
    # Constants
    MCP_SERVER_NAME,
    MCP_PREFIX,
    TOOL_CATEGORIES,
    ALL_TOOL_NAMES,
    ALL_MCP_TOOLS,
    get_mcp_tools_for_categories,
    is_valid_mcp_tool,
)

# Emit deprecation warning for direct imports of this file
warnings.warn(
    "Importing from mcp_tools.py is deprecated. "
    "Import from 'mcp_tools' package instead: from mcp_tools import create_second_brain_tools",
    DeprecationWarning,
    stacklevel=2
)

__all__ = [
    "create_mcp_server",
    "create_second_brain_tools",
    "get_all_tools",
    "get_tools_by_category",
    "get_tools_by_names",
    "get_tool_names_by_category",
    "list_categories",
    "list_tools",
    "get_tool_count",
    "MCP_SERVER_NAME",
    "MCP_PREFIX",
    "TOOL_CATEGORIES",
    "ALL_TOOL_NAMES",
    "ALL_MCP_TOOLS",
    "get_mcp_tools_for_categories",
    "is_valid_mcp_tool",
]
