"""
running_agents MCP tool — read-only view of currently in-flight agent
invocations.

The tool returns a snapshot from the backend-owned registry in
``interface/server/running_agents.py``. Codex stdio MCP bridge processes do not
read their private module copy; they call the backend internal endpoint through
the source-of-truth helper. Filterable by agent and kind.
"""

import os
import sys
import time
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Make ``running_agents`` importable.
_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)


_KINDS = [
    "chat",
    "invoke_foreground",
    "invoke_ping",
    "invoke_trust",
    "scheduled",
    "salon_convener",
    "salon_agent",
    "background_processing",
    "agent_conversation_join",
]


_TOOL_DESCRIPTION = """Returns the list of currently-running agent invocations on this server.

Use this when you need to know whether anyone (or you yourself, or a specific agent) is currently in flight — for example, before requesting a server restart, before dispatching a coder that might conflict with another in-flight coder, or to answer "is anyone running right now?"

Returns one entry per live invocation. Filter by agent or kind. Kinds:
- chat: a WebSocket chat session (the user or Character talking to an agent).
- invoke_foreground / invoke_ping / invoke_trust: another agent invoked this one via invoke_agent.
- scheduled: a scheduled task wrapper is running. Receipt-aware scheduled rows carry scheduled_task_id plus immutable scheduled_attempt_id; inner invoke_trust / invoke_foreground rows carry the same pair and caller_agent="scheduler". The scheduler receipt, not this live row, owns terminal truth.
- salon_convener: the salon dispatcher is choosing next salon speaker(s); live convener rows are expected to carry salon_id and caller_agent="salon_dispatcher".
- salon_agent: an agent is running inside a salon (group chat) dispatch.
- background_processing: an agent's idle-time background-processing hook fired.
- agent_conversation_join: an invocation that joined an existing agent-to-agent thread.

Each entry has: id, agent, kind, started_at (UNIX seconds), elapsed_seconds, task_summary, source_chat_id, conversation_id, salon_id, scheduled_task_id, scheduled_attempt_id, caller_agent. Receipt-aware scheduled rows suppress prompt-derived task_summary and expose only content-free task/attempt/thread/live provenance. Worktree-routed coder invocations also include worktree_branch, worktree_slug, and worktree_path. Fields that don't apply are null or omitted.

For restart decisions, treat this as an authoritative snapshot of the invocation paths currently wired into running_agents, not a universal proof that every possible process in the system is idle. If the source-of-truth read errors, fail closed.
"""


_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "description": "Optional — only return entries for this agent name (e.g., 'patch', 'plumb', 'character').",
        },
        "kind": {
            "type": "string",
            "enum": _KINDS,
            "description": "Optional — only return entries of this kind.",
        },
    },
}


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h{mins:02d}m"


def _render_markdown(entries):
    if not entries:
        return "No agent invocations are currently in flight."
    lines = [f"**{len(entries)} invocation(s) in flight:**", ""]
    for e in entries:
        parts = [f"- **{e['agent']}** ({e['kind']}, {_format_elapsed(e['elapsed_seconds'])})"]
        if e.get("caller_agent"):
            parts.append(f"← from `{e['caller_agent']}`")
        if e.get("conversation_id"):
            parts.append(f"[thread `{e['conversation_id'][:8]}`]")
        if e.get("salon_id"):
            parts.append(f"[salon `{e['salon_id'][:8]}`]")
        if e.get("scheduled_task_id"):
            parts.append(f"[task `{e['scheduled_task_id'][:8]}`]")
        if e.get("scheduled_attempt_id"):
            parts.append(f"[attempt `{e['scheduled_attempt_id'][:12]}`]")
        if e.get("worktree_slug") or e.get("worktree_branch"):
            worktree_bits = []
            if e.get("worktree_slug"):
                worktree_bits.append(f"slug `{e['worktree_slug']}`")
            if e.get("worktree_branch"):
                worktree_bits.append(f"branch `{e['worktree_branch']}`")
            parts.append("[worktree " + ", ".join(worktree_bits) + "]")
        line = " ".join(parts)
        summary = "" if e.get("scheduled_attempt_id") else (e.get("task_summary") or "")
        if summary:
            line += f"\n  _{summary}_"
        if e.get("worktree_path"):
            line += f"\n  worktree: `{e['worktree_path']}`"
        lines.append(line)
    return "\n".join(lines)


@register_tool("utilities")
@tool(name="running_agents", description=_TOOL_DESCRIPTION, input_schema=_TOOL_SCHEMA)
async def running_agents_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return the snapshot of currently-running invocations."""
    try:
        import running_agents as ra_module
    except ImportError as e:
        return {
            "content": [{"type": "text", "text": f"running_agents module unavailable: {e}"}],
            "is_error": True,
        }

    # Pop MCP wrapper context fields so they don't reach list_all().
    args.pop("_agent_name", None)
    args.pop("_source_chat_id", None)

    filter_agent = args.get("agent")
    filter_kind = args.get("kind")

    try:
        raw = await ra_module.list_source_of_truth(
            filter_agent=filter_agent,
            filter_kind=filter_kind,
        )
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"running_agents source-of-truth unavailable: {e}"}],
            "is_error": True,
        }

    now = time.time()
    entries = []
    for e in raw:
        started = e.get("started_at") or now
        entries.append({
            **e,
            "elapsed_seconds": max(0.0, now - started),
        })

    text = _render_markdown(entries)
    return {"content": [{"type": "text", "text": text}]}
