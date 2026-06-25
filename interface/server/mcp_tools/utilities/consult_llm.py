"""Compatibility shim for the canonical LLM consultation tool.

The registered ``consult_llm`` MCP implementation lives in
``mcp_tools.llm.consultation``. This module intentionally has no
``@register_tool`` decorator so the utility package cannot overwrite the
canonical registry entry.
"""

from ..llm.consultation import consult_llm

__all__ = ["consult_llm"]
