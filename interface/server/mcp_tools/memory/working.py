"""
Working Memory tools.

Ephemeral notes that persist across exchanges but auto-expire based on TTL.
Each agent gets its own private working memory store, isolated by agent name.
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

# Base .claude directory (for resolving per-agent memory.md paths)
_CLAUDE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude"))


def _get_agent_store(args: Dict[str, Any]):
    """Extract agent name from args and return the appropriate store."""
    sys.path.insert(0, SCRIPTS_DIR)
    from working_memory import get_store
    agent_name = args.pop("_agent_name", None) or "ren"
    return get_store(agent_name=agent_name), agent_name


@register_tool("memory")
@tool(
    name="working_memory_add",
    description="""Add a note to working memory. Working memory items are ephemeral notes that:
- Persist across exchanges but auto-expire based on TTL (time-to-live)
- Can be pinned to prevent expiration
- Support deadlines with countdown display
- Are injected into every prompt for context

Use this for:
- Observations you want to track temporarily
- Reminders about ongoing context
- Things to check back on later

TTL is measured in "exchanges" (user message + assistant response = 1 exchange).
Default TTL is 5 exchanges. Max is 10. Pinned items never expire.""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The note content"},
            "tag": {"type": "string", "description": "Optional category tag (e.g., 'reminder', 'observation', 'todo')"},
            "ttl": {"type": "integer", "description": "Time-to-live in exchanges (default: 5, max: 10)"},
            "pinned": {"type": "boolean", "description": "If true, item never auto-expires (max 3 pinned items)"},
            "deadline": {"type": "string", "description": "Optional deadline as ISO timestamp (e.g., '2026-01-25T14:00:00')"},
            "remind_before": {"type": "string", "description": "When to show 'due soon' warning (e.g., '2h', '24h')"}
        },
        "required": ["content"]
    }
)
async def working_memory_add(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a working memory item."""
    try:
        from working_memory import WorkingMemoryError
        from datetime import datetime

        store, agent_name = _get_agent_store(args)

        content = args.get("content", "").strip()
        if not content:
            return {"content": [{"type": "text", "text": "content is required"}], "is_error": True}

        tag = args.get("tag")
        ttl = args.get("ttl")
        pinned = args.get("pinned", False)
        deadline_str = args.get("deadline")
        remind_before = args.get("remind_before")

        # Parse deadline if provided
        deadline_at = None
        if deadline_str:
            try:
                deadline_at = datetime.fromisoformat(deadline_str)
                if deadline_at.tzinfo is None:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo("America/Chicago")
                    deadline_at = deadline_at.replace(tzinfo=tz)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid deadline format: {e}"}], "is_error": True}

        item = store.add_item(
            content=content,
            tag=tag,
            ttl=ttl,
            pinned=pinned,
            deadline_at=deadline_at,
            remind_before=remind_before,
        )

        status = "pinned" if item.pinned else f"TTL={item.ttl_initial}"
        return {"content": [{"type": "text", "text": f"Added to working memory [{status}]: {content[:80]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_update",
    description="""Update an existing working memory item by its display index (1-based).

You can update content, TTL, tag, pinned status, or deadline.""",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to update (1-based, as shown in prompt)"},
            "content": {"type": "string", "description": "New content (replaces existing)"},
            "append": {"type": "string", "description": "Text to append to existing content"},
            "tag": {"type": "string", "description": "New tag (empty string to clear)"},
            "ttl": {"type": "integer", "description": "Reset TTL to this value"},
            "pinned": {"type": "boolean", "description": "Set pinned status"},
            "deadline": {"type": "string", "description": "New deadline as ISO timestamp"},
            "remind_before": {"type": "string", "description": "When to show 'due soon' warning"}
        },
        "required": ["index"]
    }
)
async def working_memory_update(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update a working memory item."""
    try:
        from working_memory import WorkingMemoryError
        from datetime import datetime

        store, agent_name = _get_agent_store(args)

        index = args.get("index")
        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        # Parse deadline if provided
        deadline_at = None
        deadline_str = args.get("deadline")
        if deadline_str:
            try:
                deadline_at = datetime.fromisoformat(deadline_str)
                if deadline_at.tzinfo is None:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo("America/Chicago")
                    deadline_at = deadline_at.replace(tzinfo=tz)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid deadline format: {e}"}], "is_error": True}

        item = store.update_item(
            index=index,
            new_content=args.get("content"),
            append=args.get("append"),
            ttl=args.get("ttl"),
            tag=args.get("tag"),
            pinned=args.get("pinned"),
            deadline_at=deadline_at,
            remind_before=args.get("remind_before"),
        )

        return {"content": [{"type": "text", "text": f"Updated item {index}: {item.content[:80]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_remove",
    description="Remove a working memory item by its display index (1-based).",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to remove (1-based)"}
        },
        "required": ["index"]
    }
)
async def working_memory_remove(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a working memory item."""
    try:
        from working_memory import WorkingMemoryError

        store, agent_name = _get_agent_store(args)

        index = args.get("index")
        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        removed = store.remove_item(index)

        return {"content": [{"type": "text", "text": f"Removed: {removed.content[:80]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_list",
    description="List all current working memory items with their status.",
    input_schema={"type": "object", "properties": {}}
)
async def working_memory_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List working memory items."""
    try:
        store, agent_name = _get_agent_store(args)
        items = store.list_items()

        if not items:
            return {"content": [{"type": "text", "text": "Working memory is empty."}]}

        lines = []
        for i, item in enumerate(items, 1):
            status = "[PINNED]" if item.pinned else f"[TTL {item.ttl_remaining}/{item.ttl_initial}]"
            tag = f"[{item.tag}]" if item.tag else ""
            lines.append(f"{i}. {status} {tag} {item.content[:100]}...")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_snapshot",
    description="""Promote a working memory item to permanent storage (memories.json with always_load=true).

This "snapshots" an ephemeral working memory note into your permanent memory store.
Use this when a temporary observation or note becomes important enough to persist.

The item is saved as an always_load memory entry. By default, the
original working memory item is removed after promotion (set keep=true to retain it).""",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to promote (1-based)"},
            "section": {
                "type": "string",
                "description": "Section label for organization (e.g., 'Lessons Learned', 'User Preferences')",
                "default": "Promoted from Working Memory"
            },
            "keep": {
                "type": "boolean",
                "description": "If true, keep the item in working memory after promotion (default: false)",
                "default": False
            },
            "note": {
                "type": "string",
                "description": "Optional note to append to the content when saving"
            }
        },
        "required": ["index"]
    }
)
async def working_memory_snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """Promote working memory item to memories.json as always_load."""
    try:
        from working_memory import WorkingMemoryError
        import datetime
        import json

        store, agent_name = _get_agent_store(args)

        index = args.get("index")
        section = args.get("section", "Promoted from Working Memory")
        keep = args.get("keep", False)
        note = args.get("note", "")

        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        # Get the working memory item
        items = store.list_items()

        if not items:
            return {"content": [{"type": "text", "text": "Working memory is empty."}], "is_error": True}

        if index > len(items):
            return {"content": [{"type": "text", "text": f"No item at index {index}. Valid: 1-{len(items)}"}], "is_error": True}

        item = items[index - 1]
        content = item.content

        # Add note if provided
        if note:
            content = f"{content} — {note}"

        # Add tag context if present
        if item.tag:
            content = f"[{item.tag}] {content}"

        # Save to memories.json as always_load
        agent_name = agent_name or "ren"
        memories_path = os.path.join(_CLAUDE_DIR, "agents", agent_name, "memories.json")

        # Load existing memories
        memories = []
        if os.path.exists(memories_path):
            try:
                memories = json.loads(open(memories_path).read())
                if not isinstance(memories, list):
                    memories = []
            except Exception:
                memories = []

        # Create new memory entry
        next_id = max((m.get("id", 0) for m in memories), default=0) + 1
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        new_memory = {
            "id": next_id,
            "triggers": [section, content[:60]],
            "content": content,
            "always_load": True,
            "private": False,
            "created": now,
            "updated": now,
            "type": "observation",
        }

        memories.append(new_memory)

        # Save
        os.makedirs(os.path.dirname(memories_path), exist_ok=True)
        with open(memories_path, 'w') as f:
            json.dump(memories, f, indent=2, ensure_ascii=False)

        result = f"Promoted to permanent memory #{next_id} [always_load]: {content[:80]}..."

        # Remove from working memory unless keep=true
        if not keep:
            store.remove_item(index)
            result += "\nRemoved from working memory."
        else:
            result += "\nKept in working memory."

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
