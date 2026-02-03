"""
Tool Registry - Collects and manages MCP tools.

Supports:
- Auto-discovery of tools from submodules
- Category-based grouping
- Config-driven tool selection
"""

import logging
from typing import List, Dict, Set, Callable, Any, Optional

logger = logging.getLogger("mcp_tools.registry")

# Global registry
_tool_registry: Dict[str, Dict[str, Any]] = {}  # name -> {func, category, metadata}
_category_tools: Dict[str, List[str]] = {}  # category -> [tool_names]


def register_tool(category: str, metadata: dict = None):
    """
    Decorator to register a tool in the registry.

    Usage:
        @register_tool("google")
        @tool(name="google_list", ...)
        async def google_list(args): ...

    Note: This decorator should be placed BEFORE @tool decorator, so it receives
    the SdkMcpTool object (not the raw function).

    Args:
        category: Category name for grouping (e.g., "google", "gmail", "scheduler")
        metadata: Optional metadata dict for the tool

    Returns:
        Decorator function
    """
    def decorator(obj):
        # Handle SdkMcpTool objects from @tool decorator
        # The @tool decorator returns an SdkMcpTool with 'name' attribute
        if hasattr(obj, 'name') and not callable(getattr(obj, 'name', None)):
            # SdkMcpTool has a 'name' attribute (not method)
            name = obj.name
        elif hasattr(obj, '__name__'):
            # Raw function (not yet decorated with @tool)
            name = obj.__name__
            logger.debug(f"Tool {name} registered before @tool decorator")
        else:
            # Unknown type - try to get name somehow
            name = str(obj)
            logger.warning(f"Unknown tool type registered: {type(obj)}")

        _tool_registry[name] = {
            'func': obj,
            'category': category,
            'metadata': metadata or {}
        }

        if category not in _category_tools:
            _category_tools[category] = []
        if name not in _category_tools[category]:
            _category_tools[category].append(name)

        logger.debug(f"Registered tool '{name}' in category '{category}'")
        return obj
    return decorator


def get_all_tools() -> List[Callable]:
    """Get all registered tool functions."""
    return [info['func'] for info in _tool_registry.values()]


def get_tools_by_category(categories: List[str]) -> List[Callable]:
    """
    Get tools by category names.

    Args:
        categories: List of category names

    Returns:
        List of tool functions
    """
    tools = []
    for cat in categories:
        if cat in _category_tools:
            for name in _category_tools[cat]:
                if name in _tool_registry:
                    tools.append(_tool_registry[name]['func'])
        else:
            logger.warning(f"Unknown category: {cat}")
    return tools


def get_tools_by_names(names: List[str]) -> List[Callable]:
    """
    Get specific tools by name.

    Args:
        names: List of tool names

    Returns:
        List of tool functions
    """
    tools = []
    for name in names:
        if name in _tool_registry:
            tools.append(_tool_registry[name]['func'])
        else:
            logger.warning(f"Unknown tool: {name}")
    return tools


def get_tool_names_by_category(categories: List[str]) -> List[str]:
    """
    Get tool names by category.

    Args:
        categories: List of category names

    Returns:
        List of tool names (for allowed_tools config)
    """
    names = []
    for cat in categories:
        if cat in _category_tools:
            names.extend(_category_tools[cat])
    return names


def list_categories() -> Dict[str, List[str]]:
    """List all categories and their tool names."""
    return dict(_category_tools)


def list_tools() -> Dict[str, str]:
    """List all tools with their categories."""
    return {name: info['category'] for name, info in _tool_registry.items()}


def get_tool_count() -> int:
    """Get total number of registered tools."""
    return len(_tool_registry)


def clear_registry():
    """Clear the registry. Useful for testing."""
    global _tool_registry, _category_tools
    _tool_registry = {}
    _category_tools = {}
