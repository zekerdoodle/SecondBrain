"""
Process List tool.

Lists all running Claude processes (agents + primary) from the process registry.
"""

from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool


@register_tool("utilities")
@tool(
    name="process_list",
    description="""List all currently running Claude processes (agents and primary).

Shows each process's name, PID, task description, and start time.
Dead processes are automatically pruned. Use this to see what agents are active.""",
    input_schema={
        "type": "object",
        "properties": {},
    }
)
async def process_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return the current process registry."""
    import sys
    import os

    # Import from server directory
    server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)

    from process_registry import get_process_list

    entries = get_process_list()

    if not entries:
        return {
            "content": [{"type": "text", "text": "No running processes found."}]
        }

    lines = []
    for entry in entries:
        pid = entry.get('pid')
        pid_display = f"PID {pid}" if pid is not None else "managed"
        lines.append(
            f"- **{entry['agent']}** ({pid_display}) — {entry['task']}  "
            f"[started {entry['started']}]"
        )

    text = f"**Running processes ({len(entries)}):**\n" + "\n".join(lines)

    return {
        "content": [{"type": "text", "text": text}]
    }
